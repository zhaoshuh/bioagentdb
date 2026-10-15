"""M4 · 多轮自改进轨迹（"自改进曲线"）。

从"退化的词表"（屏蔽若干内置仓库）出发，跑 N 轮自改进；每轮：错误挖掘→提议→dev 门控接受；
记录 dev/test 指标 → 曲线（论文 B 图）。
用法: python scripts/improve_run.py [--cycles 4]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 反事实：把这些内置仓库临时屏蔽，作为"初始退化词表"
BASE_EXCLUDE = ["GWASCatalog", "Synapse", "BioSample", "NIAGADS", "SingleCellPortal",
                "PRIDE-DOI", "EGA", "ProtocolsIO", "HCA"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cycles", type=int, default=4)
    ap.add_argument("--gold", default=str(ROOT / "data" / "gold"))
    args = ap.parse_args()
    g = Path(args.gold)

    from app import learned
    from app.improve import errors, evaluate, propose

    learned.clear_patterns()
    os.environ["BIOAGENTDB_EXCLUDE_REPOS"] = ",".join(BASE_EXCLUDE)

    sample = {r["id"]: (r.get("text") or "") for r in
              (json.loads(l) for l in (g / "sample.jsonl").read_text(
                  encoding="utf-8").splitlines() if l.strip())}
    gold = evaluate.load_gold(g / "gold.jsonl")
    pipe = evaluate.load_preds(g / "predictions.jsonl")
    ids = sorted(sample)
    dev, test = evaluate.split(ids, 0.6)
    pol = learned.read_policies()

    traj = []
    for cycle in range(args.cycles):
        # 本轮：从 dev 的未识别簇发现并接受
        accepted_this = []
        if cycle > 0:
            err = errors.report(g / "gold.jsonl", g / "predictions.jsonl",
                                {pid: sample[pid] for pid in dev})
            props = propose.propose_patterns(err["unmatched_token_clusters"])
            cur = evaluate.union_metrics([], sample, pipe, gold, dev)
            # 已在 learned 里的模式由 scan 使用；这里逐个提议试
            for prop in props:
                extra = evaluate.compile_extra([prop])
                new = evaluate.union_metrics(extra, sample, pipe, gold, dev)
                ok, why = evaluate.gate(cur, new, precision_floor=pol.get("precision_floor", 0.97),
                                        recall_gain=pol.get("recall_gain_min", 0.02))
                if ok and len(accepted_this) < pol.get("max_accept", 3):
                    learned.add_pattern({"repository": prop["repository"], "label": prop["label"],
                                         "pattern": prop["pattern"], "url": prop.get("url", ""),
                                         "source": "auto", "evidence": prop.get("evidence", ""),
                                         "status": "active"})
                    accepted_this.append(prop)
                    cur = new
        d = evaluate.union_metrics([], sample, pipe, gold, dev)
        t = evaluate.union_metrics([], sample, pipe, gold, test)
        traj.append({"cycle": cycle, "learned": learned.summary()["patterns"],
                     "accepted": [p["repository"] for p in accepted_this],
                     "dev": d, "test": t})
        print(f"[cycle {cycle}] learned={len(learned.summary()['repositories'])} "
              f"accepted={[p['repository'] for p in accepted_this]} | "
              f"dev F1={d['f1']} R={d['recall']} | test F1={t['f1']} R={t['recall']}")

    os.environ.pop("BIOAGENTDB_EXCLUDE_REPOS", None)
    out = {"start_excluded": BASE_EXCLUDE, "cycles": traj}
    (g / "self-improve-curve.json").write_text(json.dumps(out, ensure_ascii=False, indent=2),
                                               encoding="utf-8")
    md = ["# Self-improvement trajectory (L8)", "",
          f"initial excluded built-ins: {', '.join(BASE_EXCLUDE)}", "",
          "| cycle | learned | accepted | dev F1 | test F1 | test R |",
          "|---|---|---|---|---|---|"]
    for r in traj:
        md.append(f"| {r['cycle']} | {r['learned']} | {','.join(r['accepted']) or '-'} | "
                  f"{r['dev']['f1']} | {r['test']['f1']} | {r['test']['recall']} |")
    (g / "self-improve-curve.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))


if __name__ == "__main__":
    main()
