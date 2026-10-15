"""清理域外主题归属（source='link' 检索命中的、'backfill' 主题回填的）。

规则：仅当论文标题命中疾病关键词，或摘要在 ≥2 次命中，才保留该归属；
否则删除（不动 keyword 来源，不动论文与数据集）。
用法: python scripts/clean_memberships.py [--apply]
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.db import db  # noqa: E402
from app.core.relevance import theme_relevant  # noqa: E402

CLEAN_SOURCES = ("link", "backfill")


def main() -> None:
    apply = "--apply" in sys.argv
    removed = Counter()
    kept = Counter()
    with db() as conn:
        rows = conn.execute("""
            SELECT pt.paper_id, pt.theme, pt.source, p.title, p.abstract
            FROM paper_themes pt JOIN papers p ON p.id = pt.paper_id
            WHERE pt.source IN ('link','backfill')""").fetchall()
        for r in rows:
            if theme_relevant(r["theme"], r["title"], r["abstract"]):
                kept[r["theme"]] += 1
            else:
                removed[r["theme"]] += 1
                if apply:
                    conn.execute(
                        "DELETE FROM paper_themes WHERE paper_id=? AND theme=? AND source=?",
                        (r["paper_id"], r["theme"], r["source"]))
    print("kept:", dict(kept))
    print("removed:", dict(removed), " total:", sum(removed.values()))
    print("mode:", "APPLIED" if apply else "dry-run (use --apply)")


if __name__ == "__main__":
    main()
