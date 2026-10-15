"""C · 存量回填 paper_sources（europepmc 溯源）。

对每篇有 pmid/doi 且尚无 europepmc 溯源行的论文，补一行
(paper_id, 'europepmc', pmid|doi, landing, '', now)。
用法: python scripts/backfill_paper_sources.py [--apply]
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN_DB = ROOT / "data" / "bioagentdb.sqlite3"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    c = sqlite3.connect(MAIN_DB)
    missing = c.execute("""
        SELECT COUNT(*) FROM papers p
        WHERE (p.pmid != '' OR p.doi != '')
          AND NOT EXISTS (SELECT 1 FROM paper_sources s
                          WHERE s.paper_id = p.id AND s.source = 'europepmc')""").fetchone()[0]
    print(f"papers missing europepmc provenance: {missing}")
    if args.apply:
        cur = c.execute("""
            INSERT OR IGNORE INTO paper_sources(paper_id, source, source_id, url,
                                                license, retrieved_at)
            SELECT p.id, 'europepmc',
                   CASE WHEN p.pmid != '' THEN p.pmid ELSE p.doi END,
                   CASE WHEN p.pmid != ''
                        THEN (CASE WHEN p.is_preprint = 1
                                   THEN 'https://europepmc.org/article/PPR/'
                                   ELSE 'https://europepmc.org/article/MED/' END) || p.pmid
                        ELSE 'https://doi.org/' || p.doi END,
                   '', ?
            FROM papers p
            WHERE (p.pmid != '' OR p.doi != '')
              AND NOT EXISTS (SELECT 1 FROM paper_sources s
                              WHERE s.paper_id = p.id AND s.source = 'europepmc')""",
            (now,))
        c.commit()
        total = c.execute("SELECT COUNT(*) FROM paper_sources").fetchone()[0]
        print(f"applied: +{cur.rowcount} rows · paper_sources total = {total}")
    else:
        print("dry-run (use --apply)")
    c.close()


if __name__ == "__main__":
    main()
