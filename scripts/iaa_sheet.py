"""W1 · IAA 抽样与标注表（L-0283）。

从 sample.jsonl 抽分层子集（默认 n=100：with_data 50 / no_data 50，固定种子），
生成**两份空白标注表**（首标注者 / 第二标注者）+ 子集清单；列与 annotation.csv 相同。
用法: python scripts/iaa_sheet.py [--n 100] [--seed 20261013]
"""
from __future__ import annotations

import argparse
import csv
import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "data" / "gold" / "sample.jsonl"
OUTDIR = ROOT / "data" / "gold" / "iaa"
COLS = ["paper_id", "title", "year", "themes", "text",
        "announces_data(Y/N)", "accessions(semicolon)", "evidence_quote", "notes"]


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(COLS)
        for r in rows:
            w.writerow([r["id"], r.get("title", ""), r.get("year", ""),
                        ";".join(r.get("themes", [])), r.get("text", ""), "", "", "", ""])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--seed", type=int, default=20261013)
    args = ap.parse_args()

    rows = [json.loads(l) for l in SAMPLE.read_text(encoding="utf-8").splitlines() if l.strip()]
    by_group = {"with_data": [], "no_data": []}
    for r in rows:
        by_group.setdefault(r.get("group", "with_data"), []).append(r)
    rng = random.Random(args.seed)
    half = args.n // 2
    pick = (rng.sample(by_group["with_data"], min(half, len(by_group["with_data"]))) +
            rng.sample(by_group["no_data"], min(args.n - half, len(by_group["no_data"]))))
    pick.sort(key=lambda r: r["id"])

    OUTDIR.mkdir(parents=True, exist_ok=True)
    write_csv(OUTDIR / "annotation-first.csv", pick)
    write_csv(OUTDIR / "annotation-second.csv", pick)
    (OUTDIR / "subset.json").write_text(json.dumps(
        {"seed": args.seed, "n": len(pick),
         "papers": [{"paper_id": r["id"], "group": r.get("group")} for r in pick]},
        ensure_ascii=False, indent=2), encoding="utf-8")
    (OUTDIR / "README.md").write_text(
        "# IAA subset (second-annotator workflow)\n\n"
        f"- `annotation-first.csv` / `annotation-second.csv`: blank copies for the two annotators "
        f"(same {len(pick)} papers; stratified {half}/{len(pick) - half}).\n"
        "- Fill the last four columns exactly as in `../annotation_README.md` "
        "(and the guide `docs/ANNOTATION_GUIDE.md`).\n"
        "- The two annotators work **independently**; do not consult each other or "
        "`predictions.jsonl` / `reference.jsonl`.\n"
        "- When both files are filled, run: `python scripts/iaa_score.py "
        "data/gold/iaa/annotation-first.csv data/gold/iaa/annotation-second.csv`\n"
        "- Disagreements are exported to `arbitration.csv`; adjudicate them with the guide as "
        "the rulebook and record the outcome per row.\n",
        encoding="utf-8")
    print(f"wrote {OUTDIR} ({len(pick)} papers; {half} with_data / {len(pick) - half} no_data)")


if __name__ == "__main__":
    main()
