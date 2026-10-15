"""修复：主登记号未出现在任何证据引文里的数据集 → 从论文 DA/摘要里补一句自洽引文。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.db import db, utcnow  # noqa: E402
from app.core.utils import grounded_quote, new_id  # noqa: E402


def main() -> None:
    with db() as conn:
        rows = conn.execute("""
            SELECT d.id, l.accession AS acc, dp.paper_id
            FROM datasets d
            JOIN dataset_links l ON l.dataset_id=d.id AND l.is_primary=1
            JOIN dataset_papers dp ON dp.dataset_id=d.id
            WHERE l.accession != ''""").fetchall()

    fixed = 0
    unfixable = 0
    for r in rows:
        acc = r["acc"]
        with db() as conn:
            has = conn.execute(
                "SELECT 1 FROM evidence WHERE dataset_id=? AND UPPER(quote) LIKE ?",
                (r["id"], f"%{acc.upper()}%")).fetchone()
            if has:
                continue
            p = conn.execute("SELECT da_text, abstract FROM papers WHERE id=?",
                             (r["paper_id"],)).fetchone()
        g = grounded_quote(acc, ("data_availability", (p["da_text"] if p else "") or ""),
                           ("abstract", (p["abstract"] if p else "") or ""))
        if not g:
            unfixable += 1
            print(f"UNFIXABLE {acc} (dataset {r['id']})")
            continue
        with db() as conn:
            conn.execute(
                """INSERT INTO evidence(id, dataset_id, paper_id, zone, quote, char_offset)
                   VALUES(?,?,?,?,?,?)""",
                (new_id("ev"), r["id"], r["paper_id"], g[0], g[1], 0))
        fixed += 1
        print(f"regrounded {acc}: [{g[0]}] {g[1][:90]}")
    print(f"\nregrounded={fixed} unfixable={unfixable}")


if __name__ == "__main__":
    main()
