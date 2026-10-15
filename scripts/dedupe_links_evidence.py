"""一次性清理重复行（L-0231/L-0232）。

- `dataset_links`：按 (dataset_id, link_type, lower(trim(repository)), upper(trim(accession)))
  分组；保留键 = **非空 url 优先 → last_checked 较新 → rowid 最小**（L-0232 修复 C-4：避免"删好留坏"）。
- `evidence`：按 (dataset_id, zone, quote) 保留 rowid 最小一条。

用法:
  python scripts/dedupe_links_evidence.py            # dry-run（严格只读）
  python scripts/dedupe_links_evidence.py --apply    # 执行删除
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.db import db, init_db  # noqa: E402


def _dup_links(conn) -> list[int]:
    rows = conn.execute(
        "SELECT rowid, dataset_id, link_type, repository, accession, url, last_checked "
        "FROM dataset_links").fetchall()
    groups: dict[tuple, list] = {}
    for r in rows:
        key = (r["dataset_id"], r["link_type"],
               (r["repository"] or "").strip().lower(),
               (r["accession"] or "").strip().upper())
        groups.setdefault(key, []).append(r)
    doomed: list[int] = []
    for g in groups.values():
        if len(g) < 2:
            continue
        g = sorted(g, key=lambda r: r["rowid"])                      # 稳定排序：三级优先级
        g = sorted(g, key=lambda r: r["last_checked"] or "", reverse=True)
        g = sorted(g, key=lambda r: 0 if (r["url"] or "") else 1)
        doomed.extend(r["rowid"] for r in g[1:])
    return doomed


def _dup_evidence(conn) -> list[int]:
    rows = conn.execute(
        "SELECT rowid, dataset_id, zone, quote FROM evidence").fetchall()
    groups: dict[tuple, list[int]] = {}
    for r in rows:
        groups.setdefault((r["dataset_id"], r["zone"] or "", r["quote"] or ""),
                          []).append(r["rowid"])
    doomed: list[int] = []
    for g in groups.values():
        if len(g) > 1:
            doomed.extend(sorted(g)[1:])
    return doomed


def _delete(conn, table: str, ids: list[int]) -> None:
    for chunk in range(0, len(ids), 400):
        batch = ids[chunk:chunk + 400]
        marks = ",".join("?" * len(batch))
        conn.execute(f"DELETE FROM {table} WHERE rowid IN ({marks})", batch)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="执行删除（默认 dry-run，严格只读）")
    args = ap.parse_args()

    if args.apply:
        init_db()   # 仅 apply 需要建表/迁移；dry-run 严格只读（L-0232）
    with db() as conn:
        dl = _dup_links(conn)
        de = _dup_evidence(conn)
        print(f"duplicate rows: dataset_links={len(dl)} evidence={len(de)} apply={args.apply}")
        if args.apply:
            _delete(conn, "dataset_links", dl)
            _delete(conn, "evidence", de)
            print(f"deleted: dataset_links={len(dl)} evidence={len(de)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
