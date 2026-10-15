"""随文拒绝清单（L-0308；L-0320 出货态修正）：从 release 库导出**被拒数据集**各一行（含拒因）。

语义：凡 `datasets.status='rejected'` 的数据集各一行（与出货目录严格一致；清除了旧版按候选行
"最新判决"可能出现的清单↔库态错位）。拒因取该 (paper, accession) 最近一次 `verdict='rejected'`
候选行的 reason；candidate_id 为该行 id（供审计脚本取检查载荷）。

用法: python scripts/make_rejection_sheet.py --db data/releases/_v15/bioagentdb.sqlite3 \
        --out docs/paper/final/evidence/rejection-audit.csv
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
from pathlib import Path

_ACC = ("(SELECT l.accession FROM dataset_links l WHERE l.dataset_id=d.id "
        "ORDER BY COALESCE(l.is_primary,0) DESC, l.id LIMIT 1)")
_REPO = ("(SELECT l.repository FROM dataset_links l WHERE l.dataset_id=d.id "
         "ORDER BY COALESCE(l.is_primary,0) DESC, l.id LIMIT 1)")

QUERY = f"""
SELECT d.id AS dataset_id, dp.paper_id,
       {_ACC} AS accession, {_REPO} AS repository,
       d.title AS record_title,
       (SELECT c.reason FROM candidates c
        WHERE c.paper_id=dp.paper_id AND c.verdict='rejected'
          AND lower(COALESCE(c.accession,''))=lower(COALESCE({_ACC}, ''))
        ORDER BY c.rowid DESC LIMIT 1) AS reason,
       (SELECT c.id FROM candidates c
        WHERE c.paper_id=dp.paper_id AND c.verdict='rejected'
          AND lower(COALESCE(c.accession,''))=lower(COALESCE({_ACC}, ''))
        ORDER BY c.rowid DESC LIMIT 1) AS candidate_id,
       p.title AS paper_title, p.pmid, p.doi
FROM datasets d
JOIN dataset_papers dp ON dp.dataset_id = d.id
JOIN papers p ON p.id = dp.paper_id
WHERE d.status = 'rejected'
GROUP BY d.id
ORDER BY dp.paper_id, accession
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    c = sqlite3.connect(f"file:{Path(args.db)}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    rows = c.execute(QUERY).fetchall()
    c.close()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["candidate_id", "dataset_id", "paper_id", "accession", "repository",
                    "record_title", "rejection_reason", "paper_title", "pmid", "doi"])
        for r in rows:
            w.writerow([r["candidate_id"] or "", r["dataset_id"] or "", r["paper_id"] or "",
                        r["accession"] or "", r["repository"] or "",
                        r["record_title"] or "", (r["reason"] or "").replace("\n", " "),
                        r["paper_title"] or "", r["pmid"] or "", r["doi"] or ""])
    print(f"wrote {out} ({len(rows)} refused dataset records)")


if __name__ == "__main__":
    main()
