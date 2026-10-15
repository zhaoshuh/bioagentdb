"""挖掘流水线编排：扫描 → 全文/DA/补充 → 抽取 → 核验 → 入库。

后台线程执行；harvest_runs 表记录每阶段进度与游标，支持断点续跑。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from app.core import utils
from app.core.config import cfg
from app.core.db import db, utcnow
from app.harvest import ingest, verify as verify_mod
from app.harvest.extract import extract_from_paper
from app.harvest.fulltext import compress_text, fetch_paper_assets
from app.harvest.llm import available as llm_available
from app.harvest.sweep import expand_queries, sweep_papers
from app.harvest.themes import get_theme

logger = logging.getLogger(__name__)

_STAGES = ["plan", "sweep", "fulltext", "extract", "verify", "ingest", "done"]
_active_runs: dict[str, threading.Thread] = {}


def start_harvest(theme_slug: str, *, max_papers: int | None = None,
                  queries: list[str] | None = None) -> str:
    theme = get_theme(theme_slug)
    if theme is None:
        raise ValueError(f"unknown theme: {theme_slug}")
    run_id = utils.new_id("run")
    with db() as conn:
        # 陈旧运行（进程重启/中断）标记为 interrupted
        conn.execute(
            "UPDATE harvest_runs SET status='interrupted' "
            "WHERE status IN ('queued','running')")
        conn.execute(
            "INSERT INTO harvest_runs(id, theme_id, status, created_at) VALUES(?,?,?,?)",
            (run_id, theme["id"], "queued", utcnow()))
    opts = {
        "max_papers": max_papers or cfg.int("harvest_max_papers_per_run", 500),
        "queries": queries,
    }
    t = threading.Thread(target=_run_harvest, args=(run_id, theme_slug, opts),
                         daemon=True, name=f"harvest-{run_id}")
    _active_runs[run_id] = t
    t.start()
    return run_id


def _set_run(run_id: str, **fields) -> None:
    with db() as conn:
        set_parts = ", ".join(f"{k}=?" for k in fields)
        conn.execute(f"UPDATE harvest_runs SET {set_parts} WHERE id=?",
                     (*fields.values(), run_id))


def _counters(run_id: str) -> dict:
    with db() as conn:
        row = conn.execute("SELECT counters FROM harvest_runs WHERE id=?", (run_id,)).fetchone()
    if not row:
        return {}
    raw = row["counters"]
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}
    return raw or {}


def _bump(run_id: str, key: str, delta: int = 1) -> None:
    c = _counters(run_id)
    c[key] = int(c.get(key, 0)) + delta
    _set_run(run_id, counters=json.dumps(c, ensure_ascii=False))


def _run_harvest(run_id: str, theme_slug: str, opts: dict) -> None:
    theme = get_theme(theme_slug)
    if theme is None:
        _set_run(run_id, status="error", error="theme missing")
        return
    max_papers = int(opts.get("max_papers") or cfg.int("harvest_max_papers_per_run", 500))
    started = utcnow()
    _set_run(run_id, status="running", started_at=started)
    llm = llm_available()

    try:
        # 1) plan：查询列表（静态 facets；LLM 扩展留作后续）
        queries = [q for q in (opts.get("queries") or []) if q] or expand_queries(theme)
        _set_run(run_id, stage="sweep", progress=0.05)
        # 2) sweep：逐查询分页拉到预算（论文按 pmid/doi 去重，重复运行幂等）
        total_budget = max_papers
        sweep_errors = 0
        for qi, q in enumerate(queries):
            if total_budget <= 0:
                break
            outcome = sweep_papers(
                q, max_papers=total_budget,
                progress=lambda msg: _set_run(run_id, stage="sweep",
                                              progress=round(0.05 + 0.30 * qi / max(len(queries), 1), 3)))
            if outcome.get("error") and not outcome.get("added"):
                sweep_errors += 1          # 源站失败≠空结果（L-0212 修复；sweep 新 error 标志）
            total_budget -= outcome["added"]
            _bump(run_id, "papers_scanned", outcome["added"])
        if sweep_errors and _counters(run_id).get("papers_scanned", 0) == 0:
            _set_run(run_id, status="error", stage="sweep", progress=1.0,
                     error=f"Europe PMC request failed for {sweep_errors} query(ies); "
                           "run aborted (source unreachable?)",
                     finished_at=utcnow())
            return
        _set_run(run_id, stage="fulltext")
        _refresh_run(run_id)

        # 3) fulltext+extract+verify+ingest：逐批处理论文
        processed = 0
        while processed < max_papers:
            papers = _pick_papers(cfg.int("harvest_batch_size", 100))
            if not papers:
                break
            _process_batch(run_id, papers, llm)
            processed += len(papers)
            _bump(run_id, "papers_processed", len(papers))
            frac = min(0.95, 0.35 + 0.60 * processed / max(max_papers, 1))
            _set_run(run_id, stage="extract", progress=round(frac, 3))
            _refresh_run(run_id)
            time.sleep(0.2)
    except Exception as exc:  # noqa: BLE001
        logger.exception("harvest run failed")
        import traceback
        _set_run(run_id, status="error", error=traceback.format_exc()[-2500:])
        return
    finally:
        try:
            ingest.update_theme_stats(theme["id"])
        except Exception:  # noqa: BLE001
            pass

    counters = _counters(run_id)
    _set_run(run_id, status="done", stage="done", progress=1.0,
             finished_at=utcnow(), counters=json.dumps(counters, ensure_ascii=False))


def _refresh_run(run_id: str) -> None:
    """把本次 run 的计数写回游标（跨阶段续跑）。"""
    c = _counters(run_id)
    with db() as conn:
        conn.execute("UPDATE harvest_runs SET cursor=? WHERE id=?",
                     (json.dumps({"counters": c}, ensure_ascii=False), run_id))


def _pick_papers(limit: int) -> list[dict]:
    statuses = ["pending", "fetched"]
    if cfg.bool("mining_task_recatalog", True):
        statuses.append("task_done")   # 任务挖过的论文，目录可再挖（L-0213 池分离）
    marks = ",".join(f"'{s}'" for s in statuses)
    with db() as conn:
        rows = conn.execute(
            f"SELECT * FROM papers WHERE mined_status IN ({marks}) "
            "ORDER BY (fulltext_source != '') DESC, year DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def _process_batch(run_id: str, papers: list[dict], llm: bool) -> None:
    # a) 抓全文/DA/补充（只对 pending）
    to_fetch = [p for p in papers if p["mined_status"] == "pending"]
    if to_fetch:
        with ThreadPoolExecutor(max_workers=cfg.int("harvest_concurrency", 6)) as pool:
            futures = {pool.submit(fetch_paper_assets, p): p for p in to_fetch}
            for fut in as_completed(futures):
                p = futures[fut]
                try:
                    res = fut.result()
                except Exception as exc:  # noqa: BLE001
                    _bump(run_id, "fulltext_errors")
                    _set_paper(p["id"], mined_status="error")
                    continue
                if not res.get("ok"):
                    _set_paper(p["id"], mined_status="fetched")  # 无全文：仍可挖 abstract
                    continue
                _bump(run_id, "fulltext_ok")
                _set_paper(p["id"], fulltext=compress_text(res["fulltext"]),
                           da_text=res.get("da_text", ""),
                           supp_files=json.dumps(res.get("supp_files", []), ensure_ascii=False),
                           fulltext_source=res.get("fulltext_source", ""),
                           mined_status="fetched")

    # b) 抽取 + 核验 + 入库
    for p in papers:
        current = _paper(p["id"])
        if current is None:
            continue
        try:
            paper = _paper_with_text(current)
            topic = current.get("theme") or ""
            try:
                from app.harvest.themes import get_theme as _get_theme
                topic = (_get_theme(topic) or {}).get("name") or topic   # L-0230：topic 透传
            except Exception:  # noqa: BLE001
                pass
            extracted = extract_from_paper(
                paper, llm_chat=((_harvest_llm or _default_llm) if llm else None),
                topic=topic)
        except Exception as exc:  # noqa: BLE001
            logger.warning("extract failed %s: %s", p["id"], exc)
            _set_paper(p["id"], mined_status="error")
            _bump(run_id, "extract_errors")
            continue
        if extracted.get("no_accessions"):
            _set_paper(p["id"], mined_status="no_accessions")
            continue
        records = extracted.get("records", [])
        for rec in records:
            result = verify_mod.verify_record(rec, abstract=paper.get("abstract", ""),
                                              da_text=paper.get("da_text", ""))
            rec.update({
                "link_reachable": result.get("link_reachable", False),
                "http_status": result.get("http_status", ""),
                "confidence": result.get("confidence", 0.0),
                "is_preprint": paper.get("is_preprint", 0),
            })
            if result["status"] == "rejected":
                _bump(run_id, "rejected")
                ingest.ingest_record(rec, paper_id=paper["id"], run_id=run_id,
                                     verdict="rejected",
                                     reason=result.get("reason") or verify_mod.gate_reject_reason(result),
                                     checks=result.get("checks"))
                continue
            info = ingest.ingest_record(rec, paper_id=paper["id"], run_id=run_id,
                                        verdict=result["status"],
                                        checks=result.get("checks"))
            if result["status"] == "verified":
                _bump(run_id, "verified")
            else:
                _bump(run_id, "paper_verified")
            if not info["created"]:
                _bump(run_id, "duplicates")
        _set_paper(p["id"], mined_status="mined")
        _bump(run_id, "papers_mined")


_harvest_llm = None


def set_harvest_llm(fn) -> None:
    """允许外部注入 LLM 客户端（默认用 app.harvest.llm.chat）。"""
    global _harvest_llm
    _harvest_llm = fn


def _default_llm(messages, **kw):
    from app.harvest.llm import chat
    return chat(messages, **kw)


def _paper(paper_id: str) -> dict | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM papers WHERE id=?", (paper_id,)).fetchone()
    return dict(row) if row else None


def _paper_with_text(paper: dict) -> dict:
    if paper.get("fulltext"):
        from app.harvest.fulltext import decompress_text
        paper["fulltext"] = decompress_text(paper["fulltext"])
    import json as _json
    try:
        paper["supp_files"] = _json.loads(paper.get("supp_files") or "[]")
    except Exception:  # noqa: BLE001
        paper["supp_files"] = []
    return paper


def _set_paper(paper_id: str, **fields) -> None:
    with db() as conn:
        fields["updated_at"] = utcnow()
        if "mined_status" in fields:
            fields["mined_at"] = utcnow()
        set_parts = ", ".join(f"{k}=?" for k in fields)
        conn.execute(f"UPDATE papers SET {set_parts} WHERE id=?",
                     (*fields.values(), paper_id))