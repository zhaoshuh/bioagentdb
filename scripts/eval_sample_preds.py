"""E5/L-0306 · 冻结 250 样本的 predictions 重建（目录口径）。

对 `data/gold/sample.jsonl` 的 250 篇论文，从指定 release 库重建
`predictions.jsonl`（与 gold_sample.py 同查询结构；口径 = `datasets.status IN
('verified','paper_verified')`，即"目录在册"，rejected 行不计入预测）。

用法: python scripts/eval_sample_preds.py --db data/releases/_v15/bioagentdb.sqlite3 \
        --out data/gold/predictions.jsonl
口径: --status 默认 "verified,paper_verified"（目录在册）；传 all 则不滤状态
      （= 冻结 §6.3/§6.5 方法：含 rejected 行，供 all-vs-verified 消融对照）。
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--status", default="verified,paper_verified")
    args = ap.parse_args()

    if args.status.strip().lower() == "all":
        statuses, where, params = (), "", ()
    else:
        statuses = tuple(s.strip() for s in args.status.split(",") if s.strip())
        where = " AND d.status IN (" + ",".join("?" * len(statuses)) + ")"
        params = statuses

    ids = [json.loads(line)["id"] for line in
           (ROOT / "data" / "gold" / "sample.jsonl").read_text(encoding="utf-8").splitlines()
           if line.strip()]

    conn = sqlite3.connect(f"file:{Path(args.db)}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    preds = []
    for pid in ids:
        ds = [dict(r) for r in conn.execute(
            "SELECT d.id, d.title, d.status, d.modality, d.organism, d.tissue, d.disease, "
            "d.sample_count FROM dataset_papers dp JOIN datasets d ON d.id=dp.dataset_id "
            f"WHERE dp.paper_id=?{where}", (pid, *params))]
        for d in ds:
            d["links"] = [dict(r) for r in conn.execute(
                "SELECT repository, accession, url, is_primary FROM dataset_links "
                "WHERE dataset_id=?", (d["id"],))]
            d["evidence"] = [r[0] for r in conn.execute(
                "SELECT quote FROM evidence WHERE dataset_id=?", (d["id"],))]
        preds.append({"paper_id": pid, "datasets": ds})
    conn.close()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(json.dumps(p, ensure_ascii=False) for p in preds) + "\n",
                   encoding="utf-8")
    print(f"wrote {out} ({len(preds)} papers, {sum(len(p['datasets']) for p in preds)} datasets; "
          f"status={args.status})")


if __name__ == "__main__":
    main()
