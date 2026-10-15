"""P5 · 全库确定性回填预演（不写入、不用 LLM）。

对每篇已入库论文做候选扫描，与已有数据集登记号比对，统计"会新增多少记录"。
用法: python scripts/backfill_preview.py [--limit 0]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.config import cfg  # noqa: E402
from app.core.db import db  # noqa: E402
from app.core.vocab import canon_accession  # noqa: E402
from app.harvest.candidates import candidates_for_paper  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    with db() as conn:
        rows = conn.execute(
            "SELECT id, abstract, da_text, supp_files FROM papers "
            "WHERE abstract != '' OR da_text != ''").fetchall()
        existing = {}
        for r in conn.execute(
                "SELECT dp.paper_id, l.accession FROM dataset_papers dp "
                "JOIN dataset_links l ON l.dataset_id = dp.dataset_id"):
            if r["accession"]:
                existing.setdefault(r["paper_id"], set()).add(canon_accession(r["accession"]))
    if args.limit:
        rows = rows[: args.limit]

    papers_gain = 0
    new_total = 0
    by_repo = Counter()
    t0 = time.time()
    for r in rows:
        try:
            supp = json.loads(r["supp_files"] or "[]")
        except Exception:  # noqa: BLE001
            supp = []
        cand = candidates_for_paper({"abstract": r["abstract"], "da_text": r["da_text"]}, supp)
        have = existing.get(r["id"], set())
        new = []
        for c in cand:
            a = canon_accession(c["accession"])
            if a and a not in have:
                new.append(c)
        if new:
            papers_gain += 1
            new_total += len(new)
            for c in new:
                by_repo[c["repository"]] += 1

    out = {"papers_scanned": len(rows), "papers_that_gain": papers_gain,
           "new_records": new_total, "by_repository": dict(by_repo.most_common()),
           "seconds": round(time.time() - t0, 1)}
    g = ROOT / "data" / "gold"
    (g / "backfill-preview.json").write_text(json.dumps(out, ensure_ascii=False, indent=2),
                                             encoding="utf-8")
    md = ["# S5 backfill preview (deterministic, no LLM)", "",
          f"- papers scanned: {out['papers_scanned']}",
          f"- papers that would gain records: **{papers_gain}**",
          f"- new records (accessions not yet in catalog): **{new_total}**",
          f"- elapsed: {out['seconds']}s", "", "## By repository", ""]
    md += [f"- {k}: {v}" for k, v in by_repo.most_common()]
    (g / "backfill-preview.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md[:8]))


if __name__ == "__main__":
    main()
