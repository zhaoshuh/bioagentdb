"""L8-MVP 一轮自改进闭环（M1+M2，确定性，几乎不花 API）。

错误挖掘 → 模式提议 → dev 门控评测 → 接受/回滚 → test 验证 → 写 learned/ + 报告草稿。
用法: python scripts/improve_cycle.py [--gold data/gold]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import learned  # noqa: E402
from app.improve import errors, evaluate, propose  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", default=str(ROOT / "data" / "gold"))
    args = ap.parse_args()
    g = Path(args.gold)

    sample = {r["id"]: (r.get("text") or "") for r in
              (json.loads(l) for l in (g / "sample.jsonl").read_text(
                  encoding="utf-8").splitlines() if l.strip())}
    gold = evaluate.load_gold(g / "gold.jsonl")
    pipe = evaluate.load_preds(g / "predictions.jsonl")
    ids = sorted(sample)

    dev, test = evaluate.split(ids, 0.6)
    pol = learned.read_policies()

    # M1 · 错误挖掘（在 dev 上）
    err = errors.report(g / "gold.jsonl", g / "predictions.jsonl",
                        {pid: sample[pid] for pid in dev})
    clusters = err["unmatched_token_clusters"]
    proposals = propose.propose_patterns(clusters)

    # M2 · 基线 + 门控
    base_dev = evaluate.union_metrics([], sample, pipe, gold, dev)
    accepted, rejected = [], []
    cur_extra = []
    cur = base_dev
    for prop in proposals[:10]:
        ex = evaluate.compile_extra([prop])
        new = evaluate.union_metrics(cur_extra + ex, sample, pipe, gold, dev)
        ok, why = evaluate.gate(cur, new, precision_floor=pol.get("precision_floor", 0.97),
                                recall_gain=pol.get("recall_gain_min", 0.02))
        prop["dev_metrics"] = new
        prop["gate"] = why
        if ok and len(accepted) < pol.get("max_accept", 3):
            accepted.append(prop)
            cur_extra = cur_extra + ex
            cur = new
        else:
            rejected.append(prop)

    for prop in accepted:
        learned.add_pattern({"repository": prop["repository"], "label": prop["label"],
                             "pattern": prop["pattern"], "url": prop.get("url", ""),
                             "source": "auto", "evidence": prop.get("evidence", ""),
                             "status": "active"})

    test_base = evaluate.union_metrics([], sample, pipe, gold, test)
    test_new = evaluate.union_metrics(cur_extra, sample, pipe, gold, test)

    report = {
        "cycle": "L8-MVP deterministic",
        "dev_baseline": base_dev, "dev_after": cur,
        "test_baseline": test_base, "test_after": test_new,
        "accepted": [{"repository": p["repository"], "pattern": p["pattern"],
                      "evidence": p["evidence"], "dev": p["dev_metrics"]} for p in accepted],
        "rejected": [{"repository": p["repository"], "pattern": p["pattern"],
                      "gate": p["gate"]} for p in rejected],
        "unmatched_clusters": clusters,
    }
    (g / "improve-cycle-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                                 encoding="utf-8")
    md = ["# L8-MVP self-improvement cycle (deterministic)", "",
          f"- dev papers: {len(dev)} · test papers: {len(test)}",
          f"- dev baseline: P {base_dev['precision']} / R {base_dev['recall']} / F1 {base_dev['f1']}",
          f"- dev after   : P {cur['precision']} / R {cur['recall']} / F1 {cur['f1']}",
          f"- test baseline: P {test_base['precision']} / R {test_base['recall']} / F1 {test_base['f1']}",
          f"- test after   : P {test_new['precision']} / R {test_new['recall']} / F1 {test_new['f1']}",
          "", f"## Accepted ({len(accepted)})"]
    for p in accepted:
        md.append(f"- {p['repository']}: `{p['pattern']}`  ← {p['evidence'][:80]}")
    md += ["", f"## Rejected ({len(rejected)})"]
    for p in rejected:
        md.append(f"- {p['repository']}: `{p['pattern']}`  gate={p['gate']}")
    md += ["", "## Unmatched token clusters (top)"]
    for c in clusters[:12]:
        md.append(f"- {c['shape']} n={c['count']} eg={c['examples'][:4]}")
    (g / "improve-cycle-report.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    # 台账草稿（待人确认后并入台账）
    draft = ROOT / "docs" / "reviews" / "L8-cycle-draft.md"
    draft.write_text("# L8 cycle draft (pending ledger entry)\n\n" + "\n".join(md) + "\n",
                     encoding="utf-8")
    print("\n".join(md[:10]))
    print(f"\nlearned patterns now: {learned.summary()['patterns']}")


if __name__ == "__main__":
    main()
