"""把 annotation.csv 转成 gold.jsonl（真人金标准），并给出基础一致性统计。

用法: python scripts/ingest_annotation.py [--csv data/gold/annotation.csv] [--out data/gold/gold.jsonl]
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPLIT = re.compile(r"[;,、，\s]+")
NONE = {"", "NONE", "NA", "N/A", "-", "NO", "NIL", "NULL"}


def parse_acc(s: str) -> list[str]:
    out = []
    for t in SPLIT.split(s or ""):
        t = t.strip().strip(".").strip()
        if t and t.upper() not in NONE:
            out.append(t)
    return list(dict.fromkeys(out))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=str(ROOT / "data" / "gold" / "annotation.csv"))
    ap.add_argument("--out", default=str(ROOT / "data" / "gold" / "gold.jsonl"))
    args = ap.parse_args()
    with Path(args.csv).open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    gold = []
    y = n = 0
    for r in rows:
        ann = (r.get("announces_data(Y/N)") or "").strip().upper().startswith("Y")
        accs = parse_acc(r.get("accessions(semicolon)") or "")
        if ann:
            y += 1
        else:
            n += 1
        gold.append({
            "paper_id": r["paper_id"],
            "title": r.get("title", ""),
            "announces_data": ann,
            "accessions": accs,
            "evidence_quote": (r.get("evidence_quote") or "").strip(),
            "notes": (r.get("notes") or "").strip(),
        })
    Path(args.out).write_text(
        "\n".join(json.dumps(g, ensure_ascii=False) for g in gold) + "\n", encoding="utf-8")

    with_acc = sum(1 for g in gold if g["accessions"])
    print(f"gold: {len(gold)} papers | announces_data Y={y} N={n} | with >=1 accession: {with_acc}")
    # 与管线预测对比（论文级）
    preds = {}
    pp = ROOT / "data" / "gold" / "predictions.jsonl"
    if pp.exists():
        for l in pp.read_text(encoding="utf-8").splitlines():
            if l.strip():
                rec = json.loads(l)
                preds[rec["paper_id"]] = bool(rec.get("datasets"))
        gy = {g["paper_id"] for g in gold if g["announces_data"]}
        py = {pid for pid, has in preds.items() if has}
        print(f"paper-level: gold_has_data={len(gy)} pred_has_data={len(py)} "
              f"| both={len(gy & py)} | gold-only(missed)={len(gy - py)} "
              f"| pred-only={len(py - gy)}")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
