"""目录查询：datasets / papers / runs / stats / export（只读）。"""

from __future__ import annotations

import csv
import io
import json
from typing import Any

from pathlib import Path
ROOT_DIR = Path(__file__).resolve().parents[2]
SNAPSHOT = ROOT_DIR / "docs" / "versions" / "v1.6-stats" / "resource-stats.json"

from app.core.db import db
from app.core.utils import norm_space, split_terms_like


def _like_terms(q: str) -> list[str]:
    return split_terms_like(q)


_LEX_CACHE: dict | None = None


def _lexicon() -> dict:
    global _LEX_CACHE
    if _LEX_CACHE is None:
        try:
            import json as _json
            from pathlib import Path as _P
            p = _P(__file__).resolve().parents[2] / "data" / "norm" / "lexicon.json"
            _LEX_CACHE = _json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        except Exception:  # noqa: BLE001
            _LEX_CACHE = {}
    return _LEX_CACHE


def _canon_for(field: str, text: str) -> list[str]:
    """过滤输入先过词表：'mouse' → ['Mus musculus']。"""
    lex = _lexicon().get(field) or {}
    t = (text or "").strip().lower()
    if not t:
        return []
    out = []
    for canon, spec in lex.items():
        names = [canon.lower()] + [str(a).lower() for a in (spec.get("aliases") or [])]
        if any(t == n or t in n for n in names):
            out.append(canon)
    return out[:5]


# L-0255：隐藏主题（themes.hidden=1）的内容不出现在目录列表。
# 多主题条目按"全部成员主题都被隐藏才隐藏"；无成员主题的条目保持可见。
_VISIBLE_PAPER = (
    "NOT (EXISTS (SELECT 1 FROM paper_themes _hp1 WHERE _hp1.paper_id = p.id) "
    "AND NOT EXISTS (SELECT 1 FROM paper_themes _hp2 JOIN themes _ht2 ON _ht2.slug = _hp2.theme "
    "WHERE _hp2.paper_id = p.id AND _ht2.hidden = 0))"
)
_VISIBLE_PAPER_BARE = (
    "NOT (EXISTS (SELECT 1 FROM paper_themes _hp1 WHERE _hp1.paper_id = papers.id) "
    "AND NOT EXISTS (SELECT 1 FROM paper_themes _hp2 JOIN themes _ht2 ON _ht2.slug = _hp2.theme "
    "WHERE _hp2.paper_id = papers.id AND _ht2.hidden = 0))"
)
_VISIBLE_DATASET = (
    "NOT (EXISTS (SELECT 1 FROM dataset_papers _hd1 WHERE _hd1.dataset_id = d.id) "
    "AND NOT EXISTS (SELECT 1 FROM dataset_papers _hd2 JOIN papers _dp2 ON _dp2.id = _hd2.paper_id "
    "WHERE _hd2.dataset_id = d.id AND ("
    "NOT EXISTS (SELECT 1 FROM paper_themes _hp3 WHERE _hp3.paper_id = _dp2.id) "
    "OR EXISTS (SELECT 1 FROM paper_themes _hp4 JOIN themes _ht4 ON _ht4.slug = _hp4.theme "
    "WHERE _hp4.paper_id = _dp2.id AND _ht4.hidden = 0))))"
)


def _dataset_filters(*, q: str = "", theme: str = "", modality: str = "", organism: str = "",
                     source: str = "", status: str = "", statuses: str = "",
                     year_min: int | None = None, year_max: int | None = None
                     ) -> tuple[list[str], list[Any]]:
    """数据集级过滤条件（`list_datasets` 与 `export_csv` 共用，保证"导出 = 当前筛选结果"）。"""
    where: list[str] = []
    args: list[Any] = []
    terms = _like_terms(q) if q else []
    if terms:
        clauses = []
        for t in terms:
            clauses.append(
                "(d.title LIKE ? OR d.description LIKE ? OR d.modalities LIKE ? "
                "OR d.organism LIKE ? OR d.tissue LIKE ? OR d.disease LIKE ? "
                "OR d.platform LIKE ?)")
            args.extend([f"%{t}%"] * 7)
        where.append("(" + " AND ".join(clauses) + ")")
    if theme:
        where.append("EXISTS (SELECT 1 FROM dataset_papers dtp JOIN paper_themes pt "
                     "ON pt.paper_id=dtp.paper_id "
                     "WHERE dtp.dataset_id=d.id AND pt.theme=?)")
        args.append(theme)
    if modality:
        where.append("(d.modality = ? OR EXISTS (SELECT 1 FROM json_each(d.modalities) je "
                     "WHERE je.value = ?) OR EXISTS (SELECT 1 FROM record_normalization rn "
                     "WHERE rn.record_id=d.id AND rn.field='modality' AND rn.canonical = ?))")
        args.extend([modality, modality, modality])
    if organism:
        canon = _canon_for("organism", organism)
        sub = "(rn.canonical LIKE ?" + (" OR rn.canonical IN (" +
                                        ",".join("?" for _ in canon) + ")" if canon else "") + ")"
        where.append("(d.organism LIKE ? OR d.organism LIKE ? OR EXISTS ("
                     "SELECT 1 FROM record_normalization rn WHERE rn.record_id=d.id "
                     "AND rn.field='organism' AND " + sub + "))")
        args.extend([f"%{organism}%", f"%{organism.title()}%", f"%{organism}%"] + canon)
    if status:
        where.append("d.status = ?")
        args.append(status)
    elif statuses:
        parts = [s.strip() for s in statuses.split(",") if s.strip()]
        if parts:
            where.append("d.status IN (" + ",".join("?" for _ in parts) + ")")
            args.extend(parts)
    if source:
        where.append("EXISTS (SELECT 1 FROM dataset_links l WHERE l.dataset_id=d.id AND l.repository=?)")
        args.append(source)
    if year_min:
        where.append("EXISTS (SELECT 1 FROM dataset_papers dp JOIN papers p ON p.id=dp.paper_id "
                     "WHERE dp.dataset_id=d.id AND p.year >= ?)")
        args.append(year_min)
    if year_max:
        where.append("EXISTS (SELECT 1 FROM dataset_papers dp JOIN papers p ON p.id=dp.paper_id "
                     "WHERE dp.dataset_id=d.id AND p.year <= ?)")
        args.append(year_max)
    where.append(_VISIBLE_DATASET)   # L-0255：隐藏主题的内容不进目录列表
    return where, args


def list_datasets(*, q: str = "", theme: str = "", modality: str = "", organism: str = "",
                  source: str = "", status: str = "", statuses: str = "",
                  year_min: int | None = None, year_max: int | None = None,
                  limit: int = 200, offset: int = 0, sort: str = "updated_at") -> dict:
    where, args = _dataset_filters(q=q, theme=theme, modality=modality, organism=organism,
                                   source=source, status=status, statuses=statuses,
                                   year_min=year_min, year_max=year_max)

    wsql = (" WHERE " + " AND ".join(where)) if where else ""
    sort_sql = {
        "updated_at": "d.updated_at DESC",
        "created_at": "d.created_at DESC",
        "confidence": "d.confidence DESC",
        "title": "d.title ASC",
    }.get(sort, "d.updated_at DESC")
    with db() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) FROM datasets d{wsql}", args).fetchone()[0]
        rows = conn.execute(
            f"SELECT d.* FROM datasets d{wsql} ORDER BY {sort_sql} LIMIT ? OFFSET ?",
            args + [limit, offset]).fetchall()
        items = [dict(r) for r in rows]
    return {"total": total, "items": _enrich_batch(items), "limit": limit, "offset": offset}


def _enrich_batch(items: list[dict]) -> list[dict]:
    if not items:
        return []
    import json as _json
    for it in items:
        # modalities/多值字段在库里是 JSON 字符串，转成数组
        mv = it.get("modalities")
        if isinstance(mv, str):
            try:
                it["modalities"] = _json.loads(mv) if mv else []
            except Exception:  # noqa: BLE001
                it["modalities"] = []
        if not isinstance(it.get("modalities"), list):
            it["modalities"] = []
    ids = [it["id"] for it in items]
    placeholders = ",".join("?" for _ in ids)
    links: dict[str, list[dict]] = {}
    papers: dict[str, list[dict]] = {}
    evs: dict[str, list[dict]] = {}
    with db() as conn:
        for row in conn.execute(
                f"SELECT * FROM dataset_links WHERE dataset_id IN ({placeholders}) ORDER BY is_primary DESC",
                ids):
            links.setdefault(row["dataset_id"], []).append(dict(row))
        for row in conn.execute(
                f"""SELECT dp.dataset_id, p.id, p.title, p.journal, p.year, p.doi, p.pmid,
                           p.is_preprint, dp.role FROM dataset_papers dp
                    JOIN papers p ON p.id = dp.paper_id
                    WHERE dp.dataset_id IN ({placeholders}) AND {_VISIBLE_PAPER}""", ids):
            d = dict(row)
            papers.setdefault(d.pop("dataset_id"), []).append(d)
        for row in conn.execute(
                f"SELECT * FROM evidence WHERE dataset_id IN ({placeholders}) ORDER BY zone", ids):
            evs.setdefault(row["dataset_id"], []).append({k: row[k] for k in
                                                          ("zone", "quote", "paper_id")})
        norms: dict[str, dict] = {}
        for row in conn.execute(
                f"SELECT record_id, field, canonical, quote FROM record_normalization "
                f"WHERE record_id IN ({placeholders}) AND canonical != ''", ids):
            norms.setdefault(row["record_id"], {}).setdefault(row["field"], []).append(
                {"canonical": row["canonical"], "quote": row["quote"]})
    out = []
    for it in items:
        it["links"] = links.get(it["id"], [])
        it["papers"] = papers.get(it["id"], [])
        it["evidence"] = evs.get(it["id"], [])
        it["normalized"] = norms.get(it["id"], {})
        out.append(it)
    return out


def get_dataset(dataset_id: str) -> dict | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM datasets WHERE id=?", (dataset_id,)).fetchone()
    if row is None:
        return None
    items = _enrich_batch([dict(row)])
    return items[0]


# ---------------------------------------------------------------- 论文粒度记录

def list_records(*, q: str = "", theme: str = "", modality: str = "",
                 organism: str = "", status: str = "", statuses: str = "",
                 limit: int = 60, offset: int = 0, sort: str = "year") -> dict:
    """以论文为主题的数据记录：每篇有数据的论文 = 一条记录，挂其下所有数据集。

    返回 {total, items:[{paper..., evidence:[...], datasets:[...]}]}
    """
    import json as _json
    where: list[str] = ["EXISTS (SELECT 1 FROM dataset_papers dp WHERE dp.paper_id = p.id)",
                        _VISIBLE_PAPER]
    args: list[Any] = []
    for t in split_terms_like(q):
        where.append("(p.title LIKE ? OR p.abstract LIKE ?)")
        args.extend([f"%{t}%", f"%{t}%"])
    if theme:
        where.append("EXISTS (SELECT 1 FROM paper_themes pt WHERE pt.paper_id = p.id "
                     "AND pt.theme = ?)")
        args.append(theme)
    if organism:
        canon = _canon_for("organism", organism)
        sub = ("(rn.canonical LIKE ?" + (" OR rn.canonical IN (" +
                                         ",".join("?" for _ in canon) + ")" if canon else "") + ")")
        where.append("EXISTS (SELECT 1 FROM dataset_papers dp JOIN datasets d ON d.id=dp.dataset_id "
                     "WHERE dp.paper_id=p.id AND (d.organism LIKE ? OR d.organism LIKE ? OR EXISTS ("
                     "SELECT 1 FROM record_normalization rn WHERE rn.record_id=d.id "
                     "AND rn.field='organism' AND " + sub + ")))")
        args.extend([f"%{organism}%", f"%{organism.title()}%", f"%{organism}%"] + canon)
    if modality:
        where.append("EXISTS (SELECT 1 FROM dataset_papers dp JOIN datasets d ON d.id=dp.dataset_id "
                     "WHERE dp.paper_id=p.id AND (d.modality = ? OR EXISTS ("
                     "SELECT 1 FROM json_each(d.modalities) je WHERE je.value = ?)))")
        args.extend([modality, modality])
    if status:
        where.append("EXISTS (SELECT 1 FROM dataset_papers dp JOIN datasets d ON d.id=dp.dataset_id "
                     "WHERE dp.paper_id=p.id AND d.status = ?)")
        args.append(status)
    elif statuses:
        parts = [s.strip() for s in statuses.split(",") if s.strip()]
        if parts:
            where.append("EXISTS (SELECT 1 FROM dataset_papers dp JOIN datasets d "
                         "ON d.id=dp.dataset_id WHERE dp.paper_id=p.id AND d.status IN ("
                         + ",".join("?" for _ in parts) + "))")
            args.extend(parts)
    wsql = " WHERE " + " AND ".join(where)
    sort_sql = {
        "year": "p.year DESC", "title": "p.title ASC",
        "datasets": "(SELECT COUNT(*) FROM dataset_papers dp WHERE dp.paper_id=p.id) DESC",
    }.get(sort, "p.year DESC")
    with db() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM papers p{wsql}", args).fetchone()[0]
        rows = conn.execute(
            f"""SELECT p.id, p.title, p.journal, p.year, p.pmid, p.doi, p.is_preprint,
                       p.abstract, p.theme
                FROM papers p{wsql} ORDER BY {sort_sql} LIMIT ? OFFSET ?""",
            args + [limit, offset]).fetchall()
        paper_ids = [r["id"] for r in rows]
        items = [dict(r) for r in rows]
        if not paper_ids:
            return {"total": total, "items": [], "limit": limit, "offset": offset}
        ph = ",".join("?" for _ in paper_ids)
        # 每篇论文的数据集 + 链接 + 证据
        datasets: dict[str, list[dict]] = {}
        for r in conn.execute(
                f"""SELECT dp.paper_id, d.id, d.title, d.description, d.modality, d.modalities,
                           d.organism, d.tissue, d.disease, d.platform, d.sample_count,
                           d.status, d.confidence
                    FROM dataset_papers dp JOIN datasets d ON d.id = dp.dataset_id
                    WHERE dp.paper_id IN ({ph}) ORDER BY d.updated_at DESC""", paper_ids):
            d = dict(r)
            d["modalities"] = _json.loads(d["modalities"]) if isinstance(d.get("modalities"), str) else (d.get("modalities") or [])
            d["links"] = []
            datasets.setdefault(d.pop("paper_id"), []).append(d)
        for r in conn.execute(
                f"SELECT dataset_id, repository, accession, url, link_type, is_primary, reachable, file_name "
                f"FROM dataset_links WHERE dataset_id IN "
                f"(SELECT d.id FROM dataset_papers dp JOIN datasets d ON d.id=dp.dataset_id "
                f"WHERE dp.paper_id IN ({ph}))", paper_ids):
            for lst in datasets.values():
                for d in lst:
                    if d["id"] == r["dataset_id"]:
                        d["links"].append(dict(r))
        evidence: dict[str, list[dict]] = {}
        for r in conn.execute(
                f"SELECT paper_id, zone, quote, dataset_id FROM evidence WHERE paper_id IN ({ph})",
                paper_ids):
            evidence.setdefault(r["paper_id"], []).append({"zone": r["zone"], "quote": r["quote"], "dataset_id": r["dataset_id"]})
        themes: dict[str, list[str]] = {}
        for r in conn.execute(
                f"SELECT pt.paper_id, pt.theme FROM paper_themes pt "
                f"JOIN themes th ON th.slug = pt.theme AND th.hidden = 0 "
                f"WHERE pt.paper_id IN ({ph}) ORDER BY pt.theme",
                paper_ids):
            themes.setdefault(r["paper_id"], []).append(r["theme"])
        for it in items:
            it["datasets"] = datasets.get(it["id"], [])
            it["evidence"] = evidence.get(it["id"], [])
            it["n_datasets"] = len(it["datasets"])
            it["themes"] = themes.get(it["id"], [it["theme"]] if it.get("theme") else [])
    return {"total": total, "items": items, "limit": limit, "offset": offset}


def get_record(record_id: str) -> dict | None:
    """按论文 id 取单条记录（与 list_records 同整形：datasets + links + evidence + themes）。"""
    import json as _json
    with db() as conn:
        r = conn.execute(
            """SELECT p.id, p.title, p.journal, p.year, p.pmid, p.doi, p.is_preprint,
                      p.abstract, p.theme
               FROM papers p WHERE p.id = ?""", (record_id,)).fetchone()
        if r is None:
            return None
        it = dict(r)
        datasets: list[dict] = []
        for dr in conn.execute(
                """SELECT d.id, d.title, d.description, d.modality, d.modalities, d.organism,
                          d.tissue, d.disease, d.platform, d.sample_count, d.status, d.confidence
                   FROM dataset_papers dp JOIN datasets d ON d.id = dp.dataset_id
                   WHERE dp.paper_id = ? ORDER BY d.updated_at DESC""", (record_id,)):
            d = dict(dr)
            d["modalities"] = (_json.loads(d["modalities"])
                               if isinstance(d.get("modalities"), str)
                               else (d.get("modalities") or []))
            d["links"] = []
            datasets.append(d)
        for lr in conn.execute(
                """SELECT dl.dataset_id, dl.repository, dl.accession, dl.url, dl.link_type,
                          dl.is_primary, dl.reachable, dl.file_name
                   FROM dataset_links dl WHERE dl.dataset_id IN
                   (SELECT d.id FROM dataset_papers dp JOIN datasets d ON d.id = dp.dataset_id
                    WHERE dp.paper_id = ?)""", (record_id,)):
            for d in datasets:
                if d["id"] == lr["dataset_id"]:
                    d["links"].append(dict(lr))
        it["datasets"] = datasets
        it["evidence"] = [dict(e) for e in conn.execute(
            "SELECT zone, quote, dataset_id FROM evidence WHERE paper_id = ?",
            (record_id,))]
        it["n_datasets"] = len(datasets)
        it["themes"] = [t["theme"] for t in conn.execute(
            "SELECT pt.theme FROM paper_themes pt JOIN themes th ON th.slug = pt.theme "
            "AND th.hidden = 0 WHERE pt.paper_id = ? ORDER BY pt.theme", (record_id,))]
        if (not it["themes"] and it.get("theme") and not conn.execute(
                "SELECT 1 FROM paper_themes WHERE paper_id = ? LIMIT 1",
                (record_id,)).fetchone()):
            it["themes"] = [it["theme"]]
    return it


def facets() -> dict:
    """筛选下拉框取值：**归一化优先**（norm_v1），归一化表为空时回退原始字段。"""
    with db() as conn:
        # 模态：按"多值模态指派"口径（与论文 Figure 4B / 发布快照一致）
        try:
            norm_mod = conn.execute(
                "SELECT j.value AS modality, COUNT(*) n FROM datasets d, json_each(d.modalities) j "
                "WHERE j.value IS NOT NULL AND j.value != '' AND " + _VISIBLE_DATASET +
                " GROUP BY j.value ORDER BY n DESC LIMIT 40").fetchall()
        except Exception:
            norm_mod = []
        if not norm_mod:
            norm_mod = conn.execute(
                "SELECT rn.canonical AS modality, COUNT(*) n FROM record_normalization rn "
                "WHERE rn.field='modality' AND rn.canonical != '' AND EXISTS ("
                "SELECT 1 FROM datasets d WHERE d.id = rn.record_id AND " + _VISIBLE_DATASET +
                ") GROUP BY rn.canonical ORDER BY n DESC LIMIT 40").fetchall()
        norm_org = conn.execute(
            "SELECT rn.canonical AS organism, COUNT(*) n FROM record_normalization rn "
            "WHERE rn.field='organism' AND rn.canonical != '' AND EXISTS ("
            "SELECT 1 FROM datasets d WHERE d.id = rn.record_id AND " + _VISIBLE_DATASET +
            ") GROUP BY rn.canonical ORDER BY n DESC LIMIT 40").fetchall()
        mods = norm_mod or conn.execute(
            "SELECT d.modality, COUNT(*) n FROM datasets d WHERE d.modality != '' AND "
            + _VISIBLE_DATASET + " GROUP BY d.modality ORDER BY n DESC LIMIT 40").fetchall()
        orgs = norm_org or conn.execute(
            "SELECT d.organism, COUNT(*) n FROM datasets d WHERE d.organism != '' AND "
            + _VISIBLE_DATASET + " GROUP BY d.organism ORDER BY n DESC LIMIT 40").fetchall()
        sts = conn.execute("SELECT d.status, COUNT(*) n FROM datasets d WHERE "
                           + _VISIBLE_DATASET + " GROUP BY d.status").fetchall()
        yrs = conn.execute(
            "SELECT p.year, COUNT(DISTINCT dp.dataset_id) n FROM dataset_papers dp "
            "JOIN papers p ON p.id=dp.paper_id JOIN datasets d ON d.id=dp.dataset_id "
            "WHERE p.year IS NOT NULL AND " + _VISIBLE_PAPER + " AND " + _VISIBLE_DATASET + " "
            "GROUP BY p.year ORDER BY p.year DESC LIMIT 30").fetchall()
    return {
        "modalities": [dict(r) for r in mods],
        "organisms": [dict(r) for r in orgs],
        "statuses": [dict(r) for r in sts],
        "years": [dict(r) for r in yrs],
    }


def throughput() -> dict:
    """吞吐：近 15 分钟的处理速度 / 产出率 / 平均耗时。

    L-0230 修复 C-2：发布模式下任务挖掘也是目录增长 → 计入；私有模式（publish=0）仍过滤任务行。
    """
    from datetime import datetime, timedelta, timezone
    from app.core.config import cfg as _cfg

    hide = not _cfg.bool("mining_task_publish_records", True)
    cond = "task_id='' AND " if hide else ""
    wsql = "WHERE task_id='' " if hide else ""

    now = datetime.now(timezone.utc)

    def iso(minutes: int) -> str:
        return (now - timedelta(minutes=minutes)).replace(microsecond=0).isoformat()

    with db() as conn:
        rows = conn.execute(
            f"SELECT verdict, duration_ms FROM paper_audits WHERE {cond}created_at >= ?",
            (iso(15),)).fetchall()
        hour = conn.execute(
            f"SELECT COUNT(*) FROM paper_audits WHERE {cond}created_at >= ?",
            (iso(60),)).fetchone()[0]
        recent = conn.execute(
            f"SELECT verdict, duration_ms FROM paper_audits {wsql}"
            "ORDER BY created_at DESC LIMIT 300"
        ).fetchall()

    durs = [r["duration_ms"] for r in rows if r["duration_ms"]]
    avg_s = round(sum(durs) / len(durs) / 1000, 1) if durs else 0.0
    mined = sum(1 for r in recent if r["verdict"] == "mined")
    nodata = sum(1 for r in recent if r["verdict"] in ("no_data", "no_accessions"))
    off = sum(1 for r in recent if r["verdict"] == "off_topic")
    return {
        "window_minutes": 15,
        "processed": len(rows),
        "per_hour": round(len(rows) * 4),
        "per_hour_60m": hour,
        "yield_pct": round(100 * mined / max(1, mined + nodata + off), 1),
        "avg_seconds": avg_s,
        "sample": len(recent),
    }


RELEASE = "v1.6"   # 当前发布版（论文 §4.5 引用）



def _release_breakdown() -> tuple[dict, dict]:
    """按"去重数据集"口径统计仓库/模态（与论文 Table 1/2、v1.5 发布快照一致）。"""
    repo: dict[str, int] = {}
    mod: dict[str, int] = {}
    with db() as conn:
        for r in conn.execute(
                "SELECT l.repository AS k, COUNT(DISTINCT l.dataset_id) AS n FROM dataset_links l "
                "WHERE l.is_primary = 1 AND l.repository <> '' GROUP BY l.repository"):
            repo[r["k"]] = r["n"]
        for r in conn.execute(
                "SELECT d.modality AS k, COUNT(*) AS n FROM datasets d "
                "WHERE d.modality IS NOT NULL AND d.modality <> '' GROUP BY d.modality"):
            mod[r["k"]] = r["n"]
    return repo, mod

def stats() -> dict:
    with db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM datasets").fetchone()[0]
        verified = conn.execute(
            "SELECT COUNT(*) FROM datasets WHERE status IN ('verified','paper_verified')"
        ).fetchone()[0]
        papers = conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
        mined = conn.execute(
            "SELECT COUNT(*) FROM papers WHERE mined_status IN "
            "('mined','no_accessions','off_topic')"
        ).fetchone()[0]
        pending = conn.execute(
            "SELECT COUNT(*) FROM papers WHERE mined_status IN "
            "('pending','fetched','claimed')"
        ).fetchone()[0]   # L-0217 C-4：首页 queued 用真待挖口径（不再用 total−mined 的错觉减法）
        runs = conn.execute("SELECT COUNT(*) FROM harvest_runs").fetchone()[0]
        by_status = {r["status"]: r["n"] for r in conn.execute(
            "SELECT status, COUNT(*) n FROM datasets GROUP BY status")}
        by_repo, by_modality = _release_breakdown()   # 兜底：去重数据集口径
        if SNAPSHOT.exists():
            _snap = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
            if _snap.get("repositories"):
                by_repo = dict(sorted(_snap["repositories"].items(), key=lambda kv: -kv[1]))
            if _snap.get("modalities"):
                by_modality = dict(sorted(_snap["modalities"].items(), key=lambda kv: -kv[1]))
        papers_with_data = conn.execute(
            "SELECT COUNT(DISTINCT paper_id) FROM dataset_papers").fetchone()[0]
    return {
        "release": RELEASE,
        "datasets_total": total,
        "datasets_verified": verified,
        "papers_total": papers,
        "papers_mined": mined,
        "papers_pending": pending,
        "runs_total": runs,
        "papers_with_data": papers_with_data,
        "by_status": by_status,
        "by_modality": by_modality,
        "by_repository": by_repo,
    }


def list_papers(*, q: str = "", year_min: int | None = None, year_max: int | None = None,
                limit: int = 100, offset: int = 0) -> dict:
    where, args = [_VISIBLE_PAPER_BARE], []
    for t in split_terms_like(q):
        where.append("(title LIKE ? OR abstract LIKE ?)")
        pat = f"%{t}%"
        args.extend([pat, pat])
    if year_min:
        where.append("year >= ?")
        args.append(year_min)
    if year_max:
        where.append("year <= ?")
        args.append(year_max)
    wsql = (" WHERE " + " AND ".join(where)) if where else ""
    with db() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM papers{wsql}", args).fetchone()[0]
        rows = conn.execute(
            f"SELECT id, pmid, pmcid, doi, title, journal, year, authors, is_preprint, "
            f"is_oa, fulltext_source, mined_status, mined_at FROM papers{wsql} "
            f"ORDER BY year DESC LIMIT ? OFFSET ?", args + [limit, offset]).fetchall()
    items = []
    for r in rows:
        d = dict(r)
        if str(d.get("mined_status") or "").startswith("task_"):
            # L-0217 C-3：公共词表不含任务内部状态；折叠为已定义的 private
            d["mined_status"] = "private"
        items.append(d)
    return {"total": total, "items": items}


def list_runs(limit: int = 50) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM harvest_runs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["counters"] = json.loads(d.get("counters") or "{}")
        out.append(d)
    return out


def list_candidates(run_id: str, limit: int = 300) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM candidates WHERE run_id=? ORDER BY created_at DESC LIMIT ?",
            (run_id, limit)).fetchall()
    return [dict(r) for r in rows]


def export_csv(*, q: str = "", theme: str = "", modality: str = "", organism: str = "",
               source: str = "", status: str = "", statuses: str = "",
               year_min: int | None = None, year_max: int | None = None) -> str:
    """导出当前筛选的数据集 CSV（与 `list_datasets` 共用过滤条件，保证"导出 = 当前筛选结果"）。"""
    where, args = _dataset_filters(q=q, theme=theme, modality=modality, organism=organism,
                                   source=source, status=status, statuses=statuses,
                                   year_min=year_min, year_max=year_max)
    wsql = (" WHERE " + " AND ".join(where)) if where else ""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["fingerprint", "title", "description", "modality", "organism", "tissue",
                "disease", "platform", "sample_count", "status", "confidence",
                "repository", "accession", "url", "evidence_zone", "evidence_quote",
                "paper_title", "paper_year", "paper_doi"])
    with db() as conn:
        rows = conn.execute(
            """SELECT d.*, l.repository, l.accession AS link_acc, l.url, l.link_type,
                      e.zone, e.quote, p.title AS paper_title, p.year AS paper_year, p.doi AS paper_doi
                 FROM datasets d
                 LEFT JOIN dataset_links l ON l.dataset_id = d.id AND l.is_primary = 1
                 LEFT JOIN dataset_papers dp ON dp.dataset_id = d.id
                 LEFT JOIN papers p ON p.id = dp.paper_id
                 LEFT JOIN evidence e ON e.dataset_id = d.id AND e.paper_id = p.id
                 """ + wsql +
            " ORDER BY d.updated_at DESC,"
            " CASE WHEN dp.role = 'primary' THEN 0 ELSE 1 END,"
            " (e.quote IS NULL), p.year DESC, e.zone", args).fetchall()
    seen: set[str] = set()
    for r in rows:
        # The paper/evidence joins are one-to-many: keep exactly one row per dataset
        # (prefer the primary paper that carries an evidence quote) so that
        # "export = current filtered result" holds row-for-row.
        if r["id"] in seen:
            continue
        seen.add(r["id"])
        w.writerow([r["fingerprint"], r["title"], r["description"], r["modality"],
                    r["organism"], r["tissue"], r["disease"], r["platform"],
                    r["sample_count"], r["status"], r["confidence"], r["repository"],
                    r["link_acc"], r["url"], r["zone"], r["quote"],
                    r["paper_title"], r["paper_year"], r["paper_doi"]])
    return buf.getvalue()
