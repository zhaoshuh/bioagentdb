"""任务记录区：`task_records` 读写（L-0225：默认同时发布进公共目录；publish=0 时即私有区）。

- 一条记录 = 一行（按 task_id + fingerprint 去重；fingerprint 沿用 ingest 的
  `norm_key(repository)|norm_key(accession)`）；
- 状态取"最高档"（verified > paper_verified > rejected），证据/链接缺失时补齐；
- 核验门槛与公共目录完全一致（T1/T2 在 paperflow 中执行后才写入）。
"""

from __future__ import annotations

from app.core.db import db, utcnow
from app.core.utils import new_id
from app.harvest.ingest import _clean, fingerprint_for

_STATUS_RANK = {"pending": 0, "rejected": 1, "paper_verified": 2, "verified": 3}


def store_task_record(record: dict, *, task_id: str, theme: str, paper_id: str,
                      verdict: str) -> dict:
    """写入/合并一条任务记录。返回 {created, record_id}。"""
    fp = fingerprint_for(record)
    if not fp.strip("|"):
        return {"created": False, "record_id": ""}
    now = utcnow()
    ev = record.get("evidence") or {}
    modality = record.get("modality") or []
    if isinstance(modality, list):
        modality = modality[0] if modality else ""
    with db() as conn:
        row = conn.execute(
            "SELECT id, status FROM task_records WHERE task_id=? AND fingerprint=?",
            (task_id, fp)).fetchone()
        if row is None:
            rid = new_id("trec")
            conn.execute(
                """INSERT INTO task_records(id, task_id, fingerprint, repository, accession,
                   url, download_url, link_type, title, description, modality, organism,
                   tissue, disease, platform, sample_count, status, confidence,
                   evidence_zone, evidence_quote, paper_id, theme, created_at, updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (rid, task_id, fp, record.get("repository", ""), record.get("identifier", ""),
                 record.get("url", ""), record.get("download_url", ""),
                 record.get("link_type", "portal"), _clean(record.get("title"))[:400],
                 _clean(record.get("description"))[:2000], modality,
                 _clean(record.get("organism")), _clean(record.get("tissue")),
                 _clean(record.get("disease")), _clean(record.get("platform"))[:200],
                 record.get("sample_count") or None, verdict,
                 float(record.get("confidence") or 0.0), ev.get("zone", ""),
                 _clean(ev.get("quote", ""))[:1600], paper_id, theme, now, now))
            return {"created": True, "record_id": rid}
        old_rank = _STATUS_RANK.get(row["status"], 0)
        new_rank = _STATUS_RANK.get(verdict, 0)
        new_status = verdict if new_rank > old_rank else row["status"]
        conn.execute(
            """UPDATE task_records SET status=?, confidence=MAX(confidence, ?),
               evidence_quote=CASE WHEN evidence_quote='' THEN ? ELSE evidence_quote END,
               url=CASE WHEN url='' THEN ? ELSE url END,
               download_url=CASE WHEN download_url='' THEN ? ELSE download_url END,
               updated_at=? WHERE id=?""",
            (new_status, float(record.get("confidence") or 0.0),
             _clean(ev.get("quote", ""))[:1600], record.get("url", ""),
             record.get("download_url", ""), now, row["id"]))
        return {"created": False, "record_id": row["id"]}


def task_record_counts(task_id: str) -> dict:
    with db() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) n FROM task_records WHERE task_id=? GROUP BY status",
            (task_id,)).fetchall()
    by = {r["status"]: r["n"] for r in rows}
    kept = by.get("verified", 0) + by.get("paper_verified", 0)
    return {"total": sum(by.values()), "kept": kept, "by_status": by}
