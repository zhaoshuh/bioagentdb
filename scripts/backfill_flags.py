"""回填 papers.has_db_xref / has_suppl（欧洲 PMC EXT_ID 批量查询）。"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.db import db, init_db
from app.core.utils import get_json

SEARCH = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"


def chunk(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def main() -> None:
    init_db()
    from app.core.config import cfg
    # L-0217 C-9：`task_done` 论文在 recatalog=1 下可被目录复挖，标志也应回填
    statuses = "('pending','fetched','task_done')" if cfg.bool("mining_task_recatalog", True) \
        else "('pending','fetched')"
    with db() as conn:
        rows = conn.execute(
            f"""SELECT id, pmid FROM papers
               WHERE pmid != '' AND mined_status IN {statuses}
               ORDER BY created_at DESC""").fetchall()
    pmids = [(r["id"], r["pmid"]) for r in rows]
    print(f"{len(pmids)} 篇待挖论文待回填")
    updated = 0
    for batch in chunk(pmids, 40):
        q = " OR ".join(f"EXT_ID:{p}" for _, p in batch)
        data = get_json(SEARCH, params={"query": q, "format": "json",
                                        "pageSize": 100, "resultType": "core"}, retries=2)
        if not data:
            time.sleep(1.0)
            continue
        flags = {}
        for it in ((data.get("resultList") or {}).get("result") or []):
            pid = str(it.get("id") or "")
            flags[pid] = (
                1 if str(it.get("hasDbCrossReferences") or "").lower() in ("y", "true", "1") else 0,
                1 if str(it.get("hasSuppl") or "").lower() in ("y", "true", "1") else 0,
            )
        with db() as conn:
            for row_id, pmid in batch:
                f = flags.get(pmid)
                if f is None:
                    continue
                conn.execute("UPDATE papers SET has_db_xref=?, has_suppl=? WHERE id=?",
                             (f[0], f[1], row_id))
                updated += 1
        time.sleep(0.35)
        if updated and updated % 400 == 0:
            print(f"  ...{updated} 已更新")
    with db() as conn:
        x = conn.execute(f"SELECT COUNT(*) FROM papers WHERE mined_status IN {statuses} AND has_db_xref=1").fetchone()[0]
        s = conn.execute(f"SELECT COUNT(*) FROM papers WHERE mined_status IN {statuses} AND has_suppl=1").fetchone()[0]
    print(f"完成：更新 {updated} 篇 | 池中带数据链接标记 {x} 篇 | 带补充材料 {s} 篇")


if __name__ == "__main__":
    main()