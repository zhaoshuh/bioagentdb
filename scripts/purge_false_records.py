"""清理历史误报：PMC 引用型"记录"（非数据集）+ 相关痕迹。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.db import db


def main() -> None:
    with db() as conn:
        ids = [r["id"] for r in conn.execute(
            """SELECT DISTINCT d.id FROM datasets d
               JOIN dataset_links l ON l.dataset_id = d.id
               WHERE l.repository = 'PubMed Central' OR l.accession LIKE 'PMC%'
               OR d.fingerprint LIKE 'pubmed central|%'""").fetchall()]
        ids = list(dict.fromkeys(ids))
        placeholders = ",".join("?" for _ in ids) if ids else ""
        if placeholders:
            for tbl in ("evidence", "dataset_papers", "dataset_links"):
                conn.execute(f"DELETE FROM {tbl} WHERE dataset_id IN ({placeholders})", ids)
            conn.execute(f"DELETE FROM datasets WHERE id IN ({placeholders})", ids)
        conn.execute("DELETE FROM candidates WHERE accession LIKE 'PMC%'")
    print("purged PMC-type dataset records:", len(ids))


if __name__ == "__main__":
    main()