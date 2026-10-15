"""入库：指纹去重、dataset/links/papers/evidence 落表、候选审计。"""

from __future__ import annotations

import json
import logging

from app.core.db import db, utcnow
from app.core.utils import new_id, norm_key

logger = logging.getLogger(__name__)


def fingerprint_for(record: dict) -> str:
    repo = norm_key(record.get("repository") or "")
    acc = norm_key(record.get("identifier") or "")
    return f"{repo}|{acc}"


def _clean(value) -> str:
    """入库前清除文本里的 HTML 残留（多级编码也处理）。"""
    import re
    from html import unescape
    s = value or ""
    if "<" not in s and "&" not in s:
        return s
    for _ in range(3):
        prev = s
        s = re.sub(r"<[^>]+>", " ", unescape(s))
        if s == prev:
            break
    return re.sub(r"\s+", " ", s).strip()


def ingest_record(record: dict, *, paper_id: str, run_id: str,
                  verdict: str, reason: str = "", checks: list | None = None) -> dict:
    """写一条数据记录（含链接/论文/证据）。返回 {dataset_id, created}。"""
    now = utcnow()
    fp = fingerprint_for(record)
    title = _clean(record.get("title"))
    description = _clean(record.get("description"))
    organism = _clean(record.get("organism"))
    tissue = _clean(record.get("tissue"))
    disease = _clean(record.get("disease"))
    platform = _clean(record.get("platform"))
    quote = _clean((record.get("evidence") or {}).get("quote"))
    dataset_id = ""
    created = False
    with db() as conn:
        row = conn.execute("SELECT id FROM datasets WHERE fingerprint = ?", (fp,)).fetchone()
        if row is None:
            dataset_id = new_id("ds")
            created = True
            modalities = json.dumps(record.get("modality") or [], ensure_ascii=False)
            sample_count = record.get("sample_count") or None
            conn.execute(
                """INSERT INTO datasets(id, fingerprint, title, description, modality,
                   modalities, organism, tissue, disease, platform, sample_count, status,
                   confidence, evidence_summary, derived_from_preprint, created_at, updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (dataset_id, fp, title[:400], description[:2000],
                 (record.get("modality") or [""])[0] if record.get("modality") else "",
                 modalities, organism, tissue, disease, platform[:200], sample_count,
                 verdict, float(record.get("confidence") or 0.0),
                 quote[:1600],
                 1 if str(record.get("is_preprint") or "").lower() in ("1", "true") else 0,
                 now, now))
        else:
            dataset_id = row["id"]
            # 已存在：合并补全（不覆盖已核验字段）；L-0230 修复 F3：状态按 rank 升级
            rank = {"rejected": 0, "paper_verified": 1, "verified": 2}
            prev = conn.execute("SELECT status FROM datasets WHERE id=?", (dataset_id,)).fetchone()
            prev_st = (prev["status"] if prev else "") or ""
            conf = float(record.get("confidence") or 0.0)
            if rank.get(verdict, 0) > rank.get(prev_st, 0):
                conn.execute(
                    "UPDATE datasets SET status=?, updated_at=?, confidence=MAX(confidence, ?) "
                    "WHERE id=?", (verdict, now, conf, dataset_id))
            else:
                conn.execute(
                    "UPDATE datasets SET updated_at=?, confidence=MAX(confidence, ?) WHERE id=?",
                    (now, conf, dataset_id))

        # 链接（主链接 + 下载入口；L-0230/L-0231：按 SQL 归一键幂等，重跑不追加）
        repo_raw = record.get("repository", "")
        acc_raw = record.get("identifier", "")
        ltype = record.get("link_type", "portal")
        link_id = new_id("lnk")
        conn.execute(
            """INSERT INTO dataset_links(id, dataset_id, repository, accession, url, link_type,
               is_primary, reachable, http_status, last_checked, note)
               SELECT ?,?,?,?,?,?,?,?,?,?,?
               WHERE NOT EXISTS (SELECT 1 FROM dataset_links WHERE dataset_id=? AND link_type=?
                                 AND lower(trim(repository)) = lower(trim(?))
                                 AND upper(trim(accession)) = upper(trim(?)))""",
            (link_id, dataset_id, repo_raw, acc_raw,
             record.get("url", ""), ltype,
             1 if record.get("is_primary", True) else 0,
             1 if record.get("link_reachable") else None,
             record.get("http_status", "")[:120], now,
             record.get("repository_label", ""),
             dataset_id, ltype, repo_raw, acc_raw))
        dl = record.get("download_url")
        if dl and dl != record.get("url"):
            conn.execute(
                """INSERT INTO dataset_links(id, dataset_id, repository, accession, url, link_type,
                   is_primary, last_checked)
                   SELECT ?,?,?,?,?,?,?,?
                   WHERE NOT EXISTS (SELECT 1 FROM dataset_links WHERE dataset_id=? AND link_type='download'
                                     AND lower(trim(repository)) = lower(trim(?))
                                     AND upper(trim(accession)) = upper(trim(?)))""",
                (new_id("lnk"), dataset_id, repo_raw, acc_raw, dl, "download", 0, now,
                 dataset_id, repo_raw, acc_raw))

        # 论文关联 + 证据
        conn.execute(
            "INSERT OR IGNORE INTO dataset_papers(dataset_id, paper_id, role) VALUES(?,?,?)",
            (dataset_id, paper_id, record.get("role", "primary")))
        ev = record.get("evidence") or {}
        if ev.get("quote"):
            q = _clean(ev.get("quote", ""))[:1600]
            conn.execute(
                """INSERT INTO evidence(id, dataset_id, paper_id, zone, quote, char_offset)
                   SELECT ?,?,?,?,?,? WHERE NOT EXISTS (
                     SELECT 1 FROM evidence WHERE dataset_id=? AND zone=? AND quote=?)""",
                (new_id("ev"), dataset_id, paper_id, ev.get("zone", ""), q, 0,
                 dataset_id, ev.get("zone", ""), q))

        # 候选审计
        conn.execute(
            """INSERT INTO candidates(id, run_id, paper_id, fingerprint, source, accession,
               title, verdict, reason, payload, created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (new_id("cand"), run_id, paper_id, fp, record.get("repository", ""),
             record.get("identifier", ""), record.get("title", "")[:300], verdict,
             reason[:400], json.dumps({"checks": checks or []}, ensure_ascii=False), now))
    return {"dataset_id": dataset_id, "created": created}


def update_theme_stats(theme_slug: str) -> None:
    """按主题统计（多主题归属）：有数据的论文数 / 已读论文数。"""
    with db() as conn:
        theme = conn.execute("SELECT id FROM themes WHERE slug=?", (theme_slug,)).fetchone()
        if theme is None:
            return
        papers_with_data = conn.execute(
            """SELECT COUNT(DISTINCT dp.paper_id)
               FROM dataset_papers dp
               JOIN paper_themes pt ON pt.paper_id = dp.paper_id
               JOIN datasets d ON d.id = dp.dataset_id
               WHERE pt.theme=? AND d.status IN ('verified','paper_verified')""",
            (theme_slug,)).fetchone()[0]
        scanned = conn.execute(
            """SELECT COUNT(*) FROM papers p
               JOIN paper_themes pt ON pt.paper_id = p.id
               WHERE pt.theme=? AND p.mined_status IN ('mined','no_accessions','off_topic')""",
            (theme_slug,)).fetchone()[0]
        conn.execute(
            "UPDATE themes SET records_count=?, papers_scanned=? WHERE id=?",
            (papers_with_data, scanned, theme["id"]))