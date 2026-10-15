"""W7 · 自举 95% CI（L-0283）：从 predictions.jsonl × gold.jsonl 重抽论文（默认 10k 次），
为 record-level P/R/F1、paper-level P/R、T1 率给出 95% 置信区间。纯离线。

用法: python scripts/eval_ci.py [--n 10000] [--pred data/gold/predictions.jsonl]
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import eval_metrics as EM  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=10000)
    ap.add_argument("--pred", default=str(ROOT / "data" / "gold" / "predictions.jsonl"))
    ap.add_argument("--ref", default=str(ROOT / "data" / "gold" / "gold.jsonl"))
    ap.add_argument("--out", default=str(ROOT / "data" / "eval" / "metrics-ci.json"))
    args = ap.parse_args()

    pred = EM.load(Path(args.pred))
    ref = EM.load(Path(args.ref))
    papers = sorted(set(pred) | set(ref))

    rows = []
    for pid in papers:
        P = EM.pred_accessions(pred.get(pid, {}))
        R = {EM.norm(x) for x in ref.get(pid, {}).get("accessions", [])}
        t1_num = t1_den = 0
        for d in pred.get(pid, {}).get("datasets", []):
            ev = " ".join(d.get("evidence") or []).lower()
            for l in d.get("links", []):
                a = EM.norm(l.get("accession", ""))
                if a:
                    t1_den += 1
                    if a in ev:
                        t1_num += 1
        rows.append((P, R, bool(R), bool(ref.get(pid, {}).get("announces_data")), t1_num, t1_den))

    def metrics(idx):
        tp = fp = fn = 0
        pred_pos = gold_findable = both = gold_announces = 0
        t1n = t1d = 0
        for i in idx:
            P, R, r_has, announces, t1_num, t1_den = rows[i]
            tp += len(P & R)
            fp += len(P - R)
            fn += len(R - P)
            if P:
                pred_pos += 1
            if r_has:
                gold_findable += 1
                if P:
                    both += 1
            if announces:
                gold_announces += 1
            t1n += t1_num
            t1d += t1_den
        prec = tp / max(1, tp + fp)
        rec = tp / max(1, tp + fn)
        f1 = 2 * prec * rec / max(1e-9, prec + rec)
        return (prec, rec, f1,
                both / max(1, pred_pos), both / max(1, gold_findable),
                t1n / max(1, t1d))

    def pct(vals, q):
        vals = sorted(vals)
        k = int(round((len(vals) - 1) * q))
        return vals[k]

    point = metrics(range(len(rows)))
    rng = random.Random(20261013)
    N = len(rows)
    samples = [metrics([rng.randrange(N) for _ in range(N)]) for _ in range(args.n)]
    names = ["record_precision", "record_recall", "record_f1",
             "paper_precision", "paper_recall", "t1_rate"]
    ci = {}
    for j, name in enumerate(names):
        vals = [s[j] for s in samples]
        ci[name] = {
            "point": round(point[j], 4),
            "ci95": [round(pct(vals, 0.025), 4), round(pct(vals, 0.975), 4)],
        }
    out = {"n_papers": N, "n_bootstrap": args.n, "seed": 20261013, "metrics": ci,
           "provisional": True}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
