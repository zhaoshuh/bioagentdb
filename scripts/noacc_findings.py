"""列出 no_accessions 二次复核的发现（extract_misses）——按仓库/域名聚合，供补正则（L-0246）。

用法:
    python scripts/noacc_findings.py [--limit 30]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.db import db  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=30)
    args = ap.parse_args()
    with db() as conn:
        rows = conn.execute(
            "SELECT repository, known, COUNT(*) AS n FROM extract_misses "
            "GROUP BY repository, known ORDER BY n DESC").fetchall()
        print("== grouped (repository | known_repo | count) ==")
        if not rows:
            print("  (empty)")
        for r in rows:
            print(f"  {r['repository'] or '(none)':30s} known={r['known']}  n={r['n']}")
        recent = conn.execute(
            "SELECT accession, url, repository, known, paper_id, quote, created_at "
            "FROM extract_misses ORDER BY created_at DESC LIMIT ?", (args.limit,)).fetchall()
        print(f"\n== latest {len(recent)} ==")
        for r in recent:
            acc = r["accession"] or "(url-only)"
            tag = "K" if r["known"] else "?"
            print(f"  [{tag}] {acc[:44]:46s} {(r['url'] or '')[:60]}")
            print(f"       paper={r['paper_id']}  quote={(r['quote'] or '')[:90]!r}")


if __name__ == "__main__":
    main()
