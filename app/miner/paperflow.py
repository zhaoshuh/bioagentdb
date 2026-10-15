"""单篇论文的完整流水线：挖掘 Agent → 3 名对抗者 → 合并 → 修正 → 核验 → 入库 → 审计。

论文级生命周期（每线程处理一篇）：
  load → extract（预备记录） → adversaries（并行） → consolidate → apply
       → final verify（T1/T2） → ingest → audit（paper_audits）→ mined
"""

from __future__ import annotations

import json
import logging
import re
import time

from app.core.config import cfg
from app.core.db import db, utcnow
from app.core.utils import acc_in_text, new_id
from app.harvest import ingest, verify as verify_mod
from app.harvest.extract import evidence_for, extract_from_paper
from app.harvest.fulltext import decompress_text
from app.mining import store as task_store
from app.redteam.adversaries import attack
from app.redteam.consolidate import consolidate

logger = logging.getLogger(__name__)

_FIX_PROMPT = """You correct one dataset record based on the paper text and a reviewer claim.

Paper abstract: {abstract}
Data Availability: {da_text}
Record: {record}
Reviewer claim: {claim}

Return JSON with ONLY corrected fields; leave unsupported fields as "":
{{"sample_count": 0, "organism": "", "tissue": "", "disease": "", "platform": "",
  "title": "", "description": "", "role": ""}}
sample_count: integer only if the paper states a count for THIS dataset; else 0.
role: "primary" (dataset produced by this paper) or "reuse"."""


# 门拒理由生成已归口 verify.py（L-0302/V-4：runner 路径复用同一实现）
_gate_reject_reason = verify_mod.gate_reject_reason


def _redteam_reject_reason(rec_decisions: list, reports: dict) -> str:
    """评审层拒绝理由（L-0304/P2-2）：只取 reject 决策、且必须带内容。

    detail 为空时回退到该目标 reject finding 的 claim/evidence；压平换行、限长 300，
    避免退化成裸前缀 "red-team rejected: "。
    """
    fallback: dict = {}
    for rep in (reports or {}).values():
        for f in (rep.get("findings") or []):
            if f.get("expected_action") == "reject":
                txt = str(f.get("claim") or f.get("evidence") or "")
                fallback.setdefault(str(f.get("target") or ""), txt)
    parts = []
    for d in rec_decisions:
        if d.get("action") != "reject":
            continue
        txt = str(d.get("detail") or "").strip() or fallback.get(str(d.get("target") or ""), "")
        txt = " ".join(txt.split())
        if txt:
            parts.append(txt)
    return ("red-team rejected: " + "; ".join(parts))[:300] if parts else "red-team rejected"


def _apply_fix(record: dict, claim: str, abstract: str, da_text: str) -> dict:
    """针对 fix_count/fix_field 的定向修正（LLM）；失败则原样返回。"""
    from app.harvest.llm import chat_json
    try:
        data = chat_json([{
            "role": "user",
            "content": _FIX_PROMPT.format(
                abstract=(abstract or "")[:1500],
                da_text=(da_text or "")[:2000],
                record=json.dumps({k: record.get(k) for k in
                                   ("identifier", "title", "description", "organism",
                                    "tissue", "disease", "platform", "sample_count",
                                    "role")}, ensure_ascii=False),
                claim=str(claim)[:400]),
        }], model=None, timeout=60)
    except Exception as exc:  # noqa: BLE001
        logger.warning("fix LLM failed: %s", exc)
        return record
    changed = False
    if isinstance(data, dict):
        sc = data.get("sample_count")
        if isinstance(sc, int) and sc > 0 and sc != record.get("sample_count"):
            record["sample_count"] = sc
            changed = True
        for k in ("organism", "tissue", "disease", "platform", "title",
                  "description", "role"):
            v = (data.get(k) or "").strip()
            if v and v != record.get(k):
                record[k] = v
                changed = True
    if changed:
        record["fixed_by_redteam"] = True
    return record


def _augment_with_candidates(paper: dict, records: list[dict]) -> list[dict]:
    """用确定性候选扫描补齐 LLM 漏抽的登记号（L2，见台账 L-0032）。"""
    from app.core.vocab import canon_accession
    from app.harvest.candidates import candidates_for_paper

    have = {canon_accession(r.get("identifier", "")) for r in records}
    added = []
    for c in candidates_for_paper(paper, paper.get("supp_files") or []):
        key = canon_accession(c["accession"])
        if not key or key in have:
            continue
        have.add(key)
        added.append({
            "identifier": c["accession"], "repository": c["repository"],
            "title": f"{c['repository']} {c['accession']}", "description": "",
            "organism": "", "tissue": "", "disease": "", "platform": "",
            "sample_count": None, "role": "primary",
            "evidence": {"zone": c["zone"], "quote": c["quote"]},
            "relevant": True, "source": "candidate_scan",
        })
    if added:
        logger.info("candidate_scan added %d record(s) (paper=%s)", len(added), paper.get("id"))
    return records + added


def _reground_evidence(record: dict, abstract: str, da_text: str) -> None:
    """确保记录的证据引文里能看到它的登记号（引文自洽）；否则从全文里补一句。"""
    from app.core.utils import grounded_quote

    acc = record.get("identifier") or ""
    if not acc:
        return
    ev = record.get("evidence") or {}
    if acc_in_text(acc, ev.get("quote") or ""):
        return
    g = grounded_quote(acc, ("data_availability", da_text), ("abstract", abstract))
    if g:
        record["evidence"] = {"zone": g[0], "quote": g[1]}


def _add_missed_record(paper: dict, decisions: list[dict], abstract: str,
                       da_text: str) -> list[dict]:
    """覆盖度对抗者发现的漏网标识 → 补建记录（不臆造：必须逐字在原文中）。"""
    from app.harvest.extract import find_identifiers

    added: list[dict] = []
    known = set()
    for d in decisions:
        if d.get("action") != "add":
            continue
        text = str(d.get("detail", "")) + " " + str(d.get("claim", ""))
        for item in find_identifiers(text):
            acc = item["acc"]
            if acc in known:
                continue
            known.add(acc)
            hay = f"{abstract}\n{da_text}"
            if not acc_in_text(acc, hay):
                continue  # 对抗者可能捏造 → 以原文为准
            zone = "data_availability" if (da_text and acc_in_text(acc, da_text)) else "abstract"
            src = da_text if zone == "data_availability" else abstract
            from app.core import vocab
            repo, url, download, label = vocab.canonical_links(acc)
            if not repo:
                continue
            quote = evidence_for(src, acc)
            added.append({
                "identifier": acc, "repository": repo, "repository_label": label,
                "url": url, "download_url": download, "link_type": "portal",
                "title": f"{paper.get('title', '')[:100]} — {acc}",
                "description": quote[:300],
                "modality": vocab.normalize_modality(abstract, quote),
                "organism": "", "tissue": "", "disease": "", "platform": "",
                "sample_count": None, "role": "primary", "relevant": True,
                "evidence": {"zone": zone, "quote": quote},
            })
    return added


def run_paper(paper_id: str, theme: str, *, llm=True,
              task_id: str | None = None) -> dict:
    """处理一篇论文（L-0225 起默认**发布进公共目录**）。

    `task_id` 非空时为任务挖掘：`mining_task_publish_records=1`（默认）→ 记录发布进公共目录
    （datasets/evidence/dataset_links/dataset_papers）＋保留任务日志（task_records）；
    设 0 则回退旧私有模式（目录记录表零写入，L-0206a）。`topic` 注入抽取提示词。
    """
    t_total = time.time()
    ms: dict[str, int] = {}
    publish = (not task_id) or bool(cfg.bool("mining_task_publish_records", True))

    t0 = time.time()
    with db() as conn:
        row = conn.execute("SELECT * FROM papers WHERE id=?", (paper_id,)).fetchone()
    if row is None:
        return {"error": "paper not found"}
    paper = dict(row)
    if paper.get("fulltext"):
        paper["fulltext"] = decompress_text(paper["fulltext"])
    import json as _json
    try:
        paper["supp_files"] = _json.loads(paper.get("supp_files") or "[]")
    except Exception:  # noqa: BLE001
        paper["supp_files"] = []
    ms["load"] = int((time.time() - t0) * 1000)

    # 0) 若从未抓过全文：先取 全文/DA/补充清单
    if not paper.get("da_text") and not paper.get("fulltext"):
        from app.harvest.fulltext import compress_text, fetch_paper_assets
        assets = fetch_paper_assets(paper)
        if assets.get("ok"):
            paper["da_text"] = assets.get("da_text", "")
            paper["supp_files"] = assets.get("supp_files", [])
            with db() as conn:
                conn.execute(
                    """UPDATE papers SET da_text=?, fulltext=?, supp_files=?,
                       fulltext_source=?, mined_status='fetched', updated_at=? WHERE id=?""",
                    (paper["da_text"], compress_text(assets.get("fulltext", "")),
                     _json.dumps(paper["supp_files"], ensure_ascii=False),
                     assets.get("fulltext_source", ""), utcnow(), paper["id"]))
        else:
            # 无全文可用：DA 段缺失，仍可尝试从摘要抽取
            paper["da_text"] = ""

    # 1) 抽取预备记录（abstract + DA 两区）+ 确定性候选扫描（补漏）
    t0 = time.time()
    from app.harvest.llm import available as llm_available
    topic = theme
    try:
        from app.mining.tasks import theme_label
        topic = theme_label(theme) or theme
    except Exception:  # noqa: BLE001
        pass
    extracted = extract_from_paper(
        paper, llm_chat=((_llm or _default_llm) if (llm and llm_available()) else None),
        topic=topic)
    ms["extract"] = int((time.time() - t0) * 1000)

    records = [r for r in extracted.get("records", []) if r.get("relevant", True)]
    records = _augment_with_candidates(paper, records)
    if not records and extracted.get("no_accessions"):
        _finish_paper(paper, theme, [], {}, findings={}, verdict="no_data",
                      ms=ms, total_ms=int((time.time() - t_total) * 1000),
                      mined_status=("no_accessions" if (not task_id or publish)
                                    else "task_no_accessions"),
                      task_id=(task_id or ""))
        return {"phase_ms": ms, "records": 0, "verdict": "no_data"}
    if not records:
        _finish_paper(paper, theme, [], {}, findings={}, verdict="off_topic",
                      ms=ms, total_ms=int((time.time() - t_total) * 1000),
                      mined_status=("off_topic" if (not task_id or publish)
                                    else "task_off_topic"),
                      task_id=(task_id or ""))
        return {"phase_ms": ms, "records": 0, "verdict": "off_topic"}

    # 2) 三名对抗者
    t0 = time.time()
    reports = attack(paper, records)
    ms["adversaries"] = int((time.time() - t0) * 1000)

    # 3) 合并
    t0 = time.time()
    decisions = consolidate(paper, records, reports)
    ms["consolidate"] = int((time.time() - t0) * 1000)

    # 4) 应用决策（含漏网补录）
    t0 = time.time()
    abstract = paper.get("abstract") or ""
    da_text = paper.get("da_text") or ""
    kept: list[dict] = []
    corrections = 0
    for rec in records:
        rec_decisions = [d for d in decisions
                         if d.get("target") == f"record-{rec['identifier']}"]
        actions = [d.get("action") for d in rec_decisions]
        if "reject" in actions:
            rec["rejected_by_redteam"] = True
            if task_id:
                task_store.store_task_record(
                    rec, task_id=task_id, theme=theme, paper_id=paper_id, verdict="rejected")
            if publish:
                ingest.ingest_record(rec, paper_id=paper_id,
                                     run_id=("task" if task_id else "redteam"),
                                     verdict="rejected",
                                     reason=_redteam_reject_reason(rec_decisions, reports))
            continue
        for d in rec_decisions:
            if d.get("action") in ("fix_count", "fix_field") and d.get("detail"):
                _apply_fix(rec, d.get("detail"), abstract, da_text)
                corrections += 1
            elif d.get("action") == "fix_role":
                rec["role"] = "reuse"
                corrections += 1
        kept.append(rec)
    missed = _add_missed_record(paper, decisions, abstract, da_text)
    kept.extend(missed)
    if missed:
        corrections += len(missed)
    ms["apply"] = int((time.time() - t0) * 1000)

    # 5) 最终核验 + 入库
    t0 = time.time()
    verified = paper_ok = pending = rejected = 0
    for rec in kept:
        _reground_evidence(rec, abstract, da_text)
        result = verify_mod.verify_record(rec, abstract=abstract, da_text=da_text)
        rec.update({
            "link_reachable": result.get("link_reachable", False),
            "http_status": result.get("http_status", ""),
            "confidence": result.get("confidence", 0.0),
            "is_preprint": paper.get("is_preprint", 0),
            "theme": theme,
        })
        if result["status"] == "rejected":
            rejected += 1
            if task_id:
                task_store.store_task_record(rec, task_id=task_id, theme=theme,
                                             paper_id=paper_id, verdict="rejected")
            if publish:
                ingest.ingest_record(rec, paper_id=paper_id,
                                     run_id=("task" if task_id else "redteam"),
                                     verdict="rejected", reason=_gate_reject_reason(result),
                                     checks=result.get("checks"))
        else:
            journal = None
            if task_id:
                journal = task_store.store_task_record(rec, task_id=task_id, theme=theme,
                                                       paper_id=paper_id,
                                                       verdict=result["status"])
            info = {"created": False}
            if publish:
                info = ingest.ingest_record(rec, paper_id=paper_id,
                                            run_id=("task" if task_id else "redteam"),
                                            verdict=result["status"],
                                            checks=result.get("checks"))
            elif journal is not None:
                info = journal   # 私有模式：计数回落任务日志（L-0230 修复 legacy 计数）
            if info["created"]:
                if result["status"] == "verified":
                    verified += 1
                else:
                    paper_ok += 1
    ms["verify_ingest"] = int((time.time() - t0) * 1000)

    # 6) 审计 + 完结
    total_ms = int((time.time() - t_total) * 1000)
    _finish_paper(paper, theme, kept, decisions, findings={
        adv: rep["findings"] for adv, rep in reports.items()},
        verdict="mined", ms=ms, total_ms=total_ms,
        mined_status=("mined" if (not task_id or publish) else "task_done"), records_summary={
            "verified": verified, "paper_verified": paper_ok,
            "rejected": rejected, "corrections": corrections},
        task_id=(task_id or ""),
    )
    return {"phase_ms": ms, "records": len(kept), "verified": verified,
            "paper_verified": paper_ok, "rejected": rejected,
            "verdict": "mined"}


def _finish_paper(paper: dict, theme: str, records: list[dict], decisions: list[dict],
                  findings: dict, verdict: str, ms: dict, total_ms: int,
                  mined_status: str, records_summary: dict | None = None,
                  task_id: str = "") -> None:
    now = utcnow()
    with db() as conn:
        conn.execute(
            "UPDATE papers SET mined_status=?, mined_at=?, mine_attempts=0, updated_at=? "
            "WHERE id=?",
            (mined_status, now, now, paper["id"]))
        conn.execute(
            """INSERT INTO paper_audits(id, paper_id, theme, task_id, findings, verdict, records,
               timings_ms, created_at, duration_ms)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (new_id("aud"), paper["id"], theme, task_id or "",
             json.dumps({"findings": findings, "decisions": decisions}, ensure_ascii=False),
             verdict,
             json.dumps(records_summary or {
                 "records": [r.get("identifier") for r in records]},
                 ensure_ascii=False),
             json.dumps(ms, ensure_ascii=False), now, total_ms))
        if task_id:
            # 任务×论文覆盖日志（L-0215 修复 C-2）：本任务不再重领此篇；他任务不受影响。
            conn.execute(
                "INSERT OR REPLACE INTO task_papers(task_id, paper_id, outcome, created_at) "
                "VALUES(?,?,?,?)",
                (task_id, paper["id"], mined_status, now))


_llm = None


def set_llm(fn) -> None:
    global _llm
    _llm = fn


def _default_llm(messages, **kw):
    from app.harvest.llm import chat
    return chat(messages, **kw)