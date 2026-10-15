"""金标准抽样（确定性、可复现）。

按「有数据 / 无数据」分层，各取 N/2，用 md5(salt+paper_id) 排序保证可复现。

用法:
  python scripts/gold_sample.py [--db PATH] [--n 250] [--out data/gold]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SALT = "bioagentdb-gold-v0.9"
MAX_TEXT = 6000


def _key(pid: str) -> str:
    return hashlib.md5(f"{SALT}:{pid}".encode()).hexdigest()


def run(db_path: Path, n: int, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        with_data = [r["paper_id"] for r in conn.execute(
            "SELECT DISTINCT paper_id FROM dataset_papers")]
        with_set = set(with_data)
        mined_no_data = [r["id"] for r in conn.execute(
            "SELECT id FROM papers WHERE mined_status IN ('mined','no_accessions')")]
        mined_no_data = [p for p in mined_no_data if p not in with_set]

        half = n // 2
        grp_a = sorted(with_data, key=_key)[:half]
        grp_b = sorted(mined_no_data, key=_key)[:max(0, n - len(grp_a))]
        chosen = grp_a + grp_b

        def paper(pid: str) -> dict:
            p = conn.execute(
                "SELECT id, pmid, pmcid, doi, title, journal, year, abstract, da_text, "
                "has_db_xref, has_suppl FROM papers WHERE id=?", (pid,)).fetchone()
            d = dict(p)
            themes = [r["theme"] for r in conn.execute(
                "SELECT theme FROM paper_themes WHERE paper_id=?", (pid,))]
            d["themes"] = themes
            text = f"{d.get('abstract') or ''}\n\n[Data Availability]\n{d.get('da_text') or ''}"
            d["text"] = text[:MAX_TEXT]
            d.pop("abstract", None)
            d.pop("da_text", None)
            d["group"] = "with_data" if pid in with_set else "no_data"
            return d

        sample = [paper(p) for p in chosen]
        (out / "sample.jsonl").write_text(
            "\n".join(json.dumps(s, ensure_ascii=False) for s in sample) + "\n", encoding="utf-8")

        # 管线预测（供标注者核对；也是评测的 pred）
        preds = []
        for pid in chosen:
            ds = [dict(r) for r in conn.execute(
                "SELECT d.id, d.title, d.status, d.modality, d.organism, d.tissue, d.disease, "
                "d.sample_count FROM dataset_papers dp JOIN datasets d ON d.id=dp.dataset_id "
                "WHERE dp.paper_id=?", (pid,))]
            for d in ds:
                d["links"] = [dict(r) for r in conn.execute(
                    "SELECT repository, accession, url, is_primary FROM dataset_links "
                    "WHERE dataset_id=?", (d["id"],))]
                d["evidence"] = [r["quote"] for r in conn.execute(
                    "SELECT quote FROM evidence WHERE dataset_id=?", (d["id"],))]
            preds.append({"paper_id": pid, "datasets": ds})
        (out / "predictions.jsonl").write_text(
            "\n".join(json.dumps(p, ensure_ascii=False) for p in preds) + "\n", encoding="utf-8")

        print(f"sampled {len(sample)} papers "
              f"(with_data={len(grp_a)}, no_data={len(grp_b)}) -> {out/'sample.jsonl'}")
    finally:
        conn.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="")
    ap.add_argument("--n", type=int, default=250)
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    if args.db:
        db_path = Path(args.db)
    else:
        from app.core.config import cfg
        db_path = Path(cfg.db_path)
    root = Path(__file__).resolve().parents[1]
    out = Path(args.out) if args.out else root / "data" / "gold"
    run(db_path, args.n, out)


if __name__ == "__main__":
    main()
