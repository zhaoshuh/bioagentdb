"""W3 · 否决精确率审计表（L-0283；L-0320 改为读出货态清单）：从随文拒绝清单分层抽样，
生成审计 CSV（审计两列留空；模型辅助审计由 `eval_rejection_audit_llm.py` 完成）。

输入：docs/paper/final/evidence/rejection-audit.csv（出货态清单，单一事实来源）
输出：data/eval/rejection-audit.csv + data/eval/rejection-audit-README.md
用法: python scripts/eval_rejection_audit.py [--n 80] [--sheet PATH]
"""
from __future__ import annotations

import argparse
import csv
import json
import random
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHEET = ROOT / "docs" / "paper" / "final" / "evidence" / "rejection-audit.csv"
OUT = ROOT / "data" / "eval" / "rejection-audit.csv"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=80)
    ap.add_argument("--seed", type=int, default=20261013)
    ap.add_argument("--sheet", default=str(SHEET))
    args = ap.parse_args()

    rows = list(csv.DictReader(open(args.sheet, encoding="utf-8-sig")))

    def bucket(reason: str) -> str:
        r = (reason or "").lower()
        if "request" in r:
            return "on_request"
        if "case" in r:
            return "case_mismatch"
        if "field" in r or "mistake" in r:
            return "field_mistake"
        return "other"

    by_b = {}
    for row in rows:
        by_b.setdefault(bucket(row.get("rejection_reason") or ""), []).append(row)
    rng = random.Random(args.seed)
    per = max(1, args.n // max(1, len(by_b)))
    pick = []
    for b, items in sorted(by_b.items()):
        pick += rng.sample(items, min(per, len(items)))
    if len(pick) < args.n:
        rest = [r for r in rows if r not in pick]
        pick += rng.sample(rest, min(args.n - len(pick), len(rest)))
    pick.sort(key=lambda r: (bucket(r.get("rejection_reason") or ""), r.get("paper_id", "")))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) +
                           ["audit_verdict(correct/wrong/uncertain)", "audit_note"])
        w.writeheader()
        for r in pick:
            r = dict(r)
            r["audit_verdict(correct/wrong/uncertain)"] = ""
            r["audit_note"] = ""
            w.writerow(r)
    readme = OUT.with_name("rejection-audit-README.md")
    readme.write_text(
        "# Rejection-precision audit\n\n"
        f"- {len(pick)} rejected dataset records sampled from the shipped refusal sheet "
        f"({len(rows)} rows; stratified by reason bucket: "
        f"{json.dumps({b: len(v) for b, v in sorted(by_b.items())}, ensure_ascii=False)}).\n"
        "- For each row, open the paper (pmid/doi) and decide whether the red-team rejection was "
        "justified:\n  `audit_verdict` = correct / wrong / uncertain, plus a one-line note.\n"
        "- A rejection is **correct** if the record should not be in the catalog "
        "(e.g., on-request-only data, identifier not literally present, wrong field).\n"
        "- verdicts feed the rejection-precision number: correct / (correct + wrong).\n",
        encoding="utf-8")
    print(f"wrote {OUT} ({len(pick)} rows) + {readme.name}")


if __name__ == "__main__":
    main()
