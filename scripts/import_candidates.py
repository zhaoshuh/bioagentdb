"""把过滤后的多源候选入库（主库）：papers + paper_sources + paper_themes（相关性守卫）。

用法:
  python scripts/import_candidates.py --limit 500 [--per-theme 120]

- 优先级：epmc_data > da_scan > terms；同优先级按（有摘要、年份新）排。
- 已存在（DOI/PMID/PMCID/标题+年）自动跳过。
- 写 data/pilot/imported.json 供定点挖矿使用。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.db import db, init_db, utcnow  # noqa: E402
from app.core.relevance import theme_relevant  # noqa: E402
from app.core.utils import new_id  # noqa: E402
from app.harvest.dedup import norm_doi, norm_pmid, norm_pmcid, norm_title  # noqa: E402

IN_JSONL = ROOT / "data" / "pilot" / "candidates-filtered.jsonl"
PILOT_DB = ROOT / "data" / "pilot" / "multisource-pilot.sqlite3"
OUT_IMPORTED = ROOT / "data" / "pilot" / "imported.json"

PRIORITY = {"epmc_data": 0, "da_scan": 1, "terms": 2}


def _prio(cand: dict) -> int:
    return min(PRIORITY.get(f, 9) for f in cand.get("filters") or ["terms"])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=str(IN_JSONL))
    ap.add_argument("--limit", type=int, default=500)
    ap.add_argument("--per-theme", type=int, default=120)
    args = ap.parse_args()

    init_db()
    cands = [json.loads(l) for l in Path(args.input).read_text(encoding="utf-8").splitlines()
             if l.strip()]
    cands.sort(key=lambda c: (_prio(c), 0 if c.get("abstract") else 1,
                              -(c.get("year") or 0)))

    # 主库现有键
    with db() as conn:
        dois = {norm_doi(r["doi"]) for r in conn.execute(
            "SELECT doi FROM papers WHERE doi != ''")}
        pmids = {norm_pmid(r["pmid"]) for r in conn.execute(
            "SELECT pmid FROM papers WHERE pmid != ''")}
        pmcids = {norm_pmcid(r["pmcid"]) for r in conn.execute(
            "SELECT pmcid FROM papers WHERE pmcid != ''")}
        titles = {norm_title(r["title"]) for r in conn.execute("SELECT title FROM papers")}

    # merge_log 溯源
    pl = sqlite3.connect(PILOT_DB)
    pl.row_factory = sqlite3.Row
    members_map: dict[str, list] = {}
    for r in pl.execute("SELECT gid, members FROM merge_log"):
        members_map[r["gid"]] = json.loads(r["members"])
    pl.close()

    imported = []
    skipped = Counter()
    per_theme = Counter()
    now = utcnow()
    for c in cands:
        if len(imported) >= args.limit:
            break
        doi, pmid = norm_doi(c.get("doi") or ""), norm_pmid(c.get("pmid") or "")
        pmcid = norm_pmcid(c.get("pmcid") or "")
        title = norm_title(c.get("title") or "")
        if (doi and doi in dois) or (pmid and pmid in pmids) or (pmcid and pmcid in pmcids) \
                or (title and title in titles):
            skipped["exists"] += 1
            continue
        themes = [t for t in (c.get("themes") or []) if t]
        home = themes[0] if themes else "alzheimers-disease"
        if per_theme[home] >= args.per_theme:
            skipped["theme_cap"] += 1
            continue
        pid = new_id("paper")
        has_xref = 1 if "epmc_data" in (c.get("filters") or []) else 0
        with db() as conn:
            conn.execute(
                """INSERT INTO papers(id, pmid, pmcid, doi, title, journal, year, authors,
                     abstract, is_preprint, is_oa, fulltext_source, fulltext, da_text,
                     supp_files, mined_status, mined_at, created_at, updated_at, theme,
                     has_db_xref, has_suppl)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (pid, pmid, pmcid, doi, c.get("title") or "", c.get("journal") or "",
                 c.get("year"), "", c.get("abstract") or "",
                 int(c.get("is_preprint") or 0), 0, "", "", "", "[]",
                 "pending", "", now, now, home, has_xref, 0))
            for t in themes:
                if theme_relevant(t, c.get("title") or "", c.get("abstract") or ""):
                    conn.execute(
                        "INSERT OR IGNORE INTO paper_themes(paper_id, theme, source, created_at) "
                        "VALUES(?,?,?,?)", (pid, t, "multisource", now))
            for m in members_map.get(c["gid"], []):
                conn.execute(
                    """INSERT OR IGNORE INTO paper_sources(paper_id, source, source_id,
                         url, license, retrieved_at) VALUES(?,?,?,?,?,?)""",
                    (pid, m.get("source") or "", m.get("source_id") or "", "", "", now))
        if doi:
            dois.add(doi)
        if pmid:
            pmids.add(pmid)
        if pmcid:
            pmcids.add(pmcid)
        if title:
            titles.add(title)
        per_theme[home] += 1
        imported.append({"paper_id": pid, "gid": c["gid"], "theme": home,
                         "filters": c.get("filters"), "sources": c.get("sources")})

    OUT_IMPORTED.write_text(json.dumps(imported, ensure_ascii=False, indent=2),
                            encoding="utf-8")
    print(f"imported: {len(imported)} · skipped: {dict(skipped)}")
    print(f"per theme: {dict(per_theme)}")
    print(f"wrote {OUT_IMPORTED}")


if __name__ == "__main__":
    main()
