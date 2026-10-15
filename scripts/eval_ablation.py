"""消融（post-hoc 过滤版）：衡量"审稿人/核验过滤"对精度的贡献。

策略（同一份预测，不同过滤）：
  all               : 所有抽取到的记录（含 rejected）
  verified          : 仅 status in (verified, paper_verified) —— 审稿人+核验的过滤结果
  verified_grounded : 在 verified 基础上要求登记号出现在证据引文里 —— 再加 T1 过滤

对比参照（regex reference）算 P/R/F1，差值即各过滤环节的贡献。

注：这是"事后过滤"消融，不重跑管线；完整组件消融（去对抗者/T2/修复/补锚）见台账待办。

用法: python scripts/eval_ablation.py [--gold data/gold]
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def canon(a: str) -> str:
    a = (a or "").strip().lower()
    return re.sub(r"\.v\d+\.p\d+$", "", a)


def jl(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", default=str(root / "data" / "gold"))
    args = ap.parse_args()
    g = Path(args.gold)

    preds = {r["paper_id"]: r for r in jl(g / "predictions.jsonl")}
    ref = {r["paper_id"]: {canon(a) for a in r["accessions"]} for r in jl(g / "reference.jsonl")}

    def accs(rec: dict, policy: str) -> set[str]:
        out = set()
        for d in rec.get("datasets", []):
            st = d.get("status")
            if policy in ("verified", "verified_grounded") and st not in ("verified", "paper_verified"):
                continue
            ev = " ".join(d.get("evidence") or []).lower()
            for l in d.get("links", []):
                a = canon(l.get("accession", ""))
                if not a:
                    continue
                if policy == "verified_grounded" and a not in ev:
                    continue
                out.add(a)
        return out

    def score(policy: str) -> dict:
        tp = fp = fn = 0
        for pid, rec in preds.items():
            P = accs(rec, policy)
            R = ref.get(pid, set())
            tp += len(P & R)
            fp += len(P - R)
            fn += len(R - P)
        p = tp / max(1, tp + fp)
        r = tp / max(1, tp + fn)
        f = 2 * p * r / max(1e-9, p + r)
        return {"tp": tp, "fp": fp, "fn": fn, "precision": round(p, 4),
                "recall": round(r, 4), "f1": round(f, 4)}

    policies = ["all", "verified", "verified_grounded"]
    res = {k: score(k) for k in policies}
    report = {"provisional": True, "policies": res,
              "note": "post-hoc filter ablation; full component ablation pending"}
    (g / "ablation-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                            encoding="utf-8")
    md = ["# Ablation (post-hoc filter, provisional)", "",
          "| policy | P | R | F1 | tp | fp | fn |", "|---|---|---|---|---|---|---|"]
    for k in policies:
        v = res[k]
        md.append(f"| {k} | {v['precision']} | {v['recall']} | {v['f1']} | "
                  f"{v['tp']} | {v['fp']} | {v['fn']} |")
    md += ["", "Precision gain from `all` → `verified` = "
           f"{res['verified']['precision'] - res['all']['precision']:+.3f}; "
           "`verified` → `verified_grounded` = "
           f"{res['verified_grounded']['precision'] - res['verified']['precision']:+.3f}."]
    (g / "ablation-report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))


if __name__ == "__main__":
    main()
