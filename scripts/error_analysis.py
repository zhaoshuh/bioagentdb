"""误差分析：把 provisional 评测里的 FP/FN 归类（论文 B 的误差类型图）。

用法: python scripts/error_analysis.py [--gold data/gold]
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path


def norm(a: str) -> str:
    a = (a or "").strip().lower()
    return re.sub(r"\.v\d+\.p\d+$", "", a)


def jl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", default=str(root / "data" / "gold"))
    args = ap.parse_args()
    gold = Path(args.gold)

    sample = {r["id"]: r for r in jl(gold / "sample.jsonl")}
    report = json.loads((gold / "eval-report.json").read_text(encoding="utf-8"))

    fp_cat = Counter()
    fn_cat = Counter()
    examples: dict[str, list[str]] = {"fp": [], "fn": []}
    for pp in report["per_paper"]:
        pid = pp["paper_id"]
        text = (sample.get(pid, {}).get("text") or "").lower()
        for acc in pp["fp"]:
            if acc and acc in text:
                fp_cat["in_text_but_reference_missed"] += 1
                tag = "fp:in_text_ref_missed"
            else:
                fp_cat["not_in_sample_text"] += 1
                tag = "fp:not_in_sample_text"
            examples.setdefault(tag, [])
            if len(examples[tag]) < 8:
                examples[tag].append(f"{acc} @ {pid} ({sample.get(pid,{}).get('title','')[:60]})")
        for acc in pp["fn"]:
            if not pp["pred"]:
                fn_cat["paper_predicted_no_data"] += 1
                tag = "fn:predicted_no_data"
            else:
                fn_cat["accession_missed"] += 1
                tag = "fn:accession_missed"
            examples.setdefault(tag, [])
            if len(examples[tag]) < 8:
                examples[tag].append(f"{acc} @ {pid} ({sample.get(pid,{}).get('title','')[:60]})")

    out = {
        "provisional": True,
        "fp_total": sum(fp_cat.values()),
        "fn_total": sum(fn_cat.values()),
        "fp_categories": dict(fp_cat),
        "fn_categories": dict(fn_cat),
        "examples": examples,
    }
    (gold / "error-analysis.json").write_text(json.dumps(out, ensure_ascii=False, indent=2),
                                              encoding="utf-8")
    md = ["# Error analysis (provisional)", "",
          f"- FP total: {out['fp_total']} · {dict(fp_cat)}",
          f"- FN total: {out['fn_total']} · {dict(fn_cat)}", "", "## Examples"]
    for k, v in examples.items():
        md.append(f"\n**{k}**")
        md += [f"- {x}" for x in v]
    (gold / "error-analysis.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md[:12]))


if __name__ == "__main__":
    main()
