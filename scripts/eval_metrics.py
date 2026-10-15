"""评测：管线预测 vs 参照（默认真人金标准 gold.jsonl；也可用正则 reference.jsonl）。

指标：登记号级 P/R/F1（paper×accession）、论文级"可发现数据"P/R、
T1 自洽率、金标准中"announces but no accession"计数。输出 JSON + Markdown。

用法:
  python scripts/eval_metrics.py --ref data/gold/gold.jsonl --name "human gold" --out data/gold
  python scripts/eval_metrics.py --ref data/gold/reference.jsonl --name "regex reference" \
      --out data/gold/regex-ref
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def norm(acc: str) -> str:
    from app.core.vocab import canon_accession
    return canon_accession(acc)


def load(path: Path) -> dict:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            out[r["paper_id"]] = r
    return out


def pred_accessions(rec: dict) -> set[str]:
    accs = set()
    for d in rec.get("datasets", []):
        for l in d.get("links", []):
            a = norm(l.get("accession", ""))
            if a:
                accs.add(a)
    return accs


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    ap = argparse.ArgumentParser()
    ap.add_argument("--pred", default=str(root / "data" / "gold" / "predictions.jsonl"))
    ap.add_argument("--ref", default=str(root / "data" / "gold" / "gold.jsonl"))
    ap.add_argument("--name", default="human gold")
    ap.add_argument("--out", default=str(root / "data" / "gold"))
    args = ap.parse_args()

    pred = load(Path(args.pred))
    ref = load(Path(args.ref))
    papers = sorted(set(pred) | set(ref))

    tp = fp = fn = 0
    gold_findable = gold_announces = 0
    pred_pos = 0
    both = 0
    t1_num = t1_den = 0
    per_paper = []
    for pid in papers:
        P = pred_accessions(pred.get(pid, {}))
        R = {norm(x) for x in ref.get(pid, {}).get("accessions", [])}
        tp += len(P & R)
        fp += len(P - R)
        fn += len(R - P)
        pred_has = bool(P)
        if pred_has:
            pred_pos += 1
        if R:
            gold_findable += 1
            if pred_has:
                both += 1
        if ref.get(pid, {}).get("announces_data"):
            gold_announces += 1
        for d in pred.get(pid, {}).get("datasets", []):
            ev = " ".join(d.get("evidence") or []).lower()
            for l in d.get("links", []):
                a = norm(l.get("accession", ""))
                if a:
                    t1_den += 1
                    if a in ev:
                        t1_num += 1
        per_paper.append({"paper_id": pid, "pred": sorted(P), "ref": sorted(R),
                          "tp": sorted(P & R), "fp": sorted(P - R), "fn": sorted(R - P)})

    prec = tp / max(1, tp + fp)
    rec = tp / max(1, tp + fn)
    f1 = 2 * prec * rec / max(1e-9, prec + rec)
    p_prec = both / max(1, pred_pos)
    p_rec = both / max(1, gold_findable)

    report = {
        "provisional": args.name != "human gold",
        "reference": args.name,
        "papers": len(papers),
        "record_level": {"tp": tp, "fp": fp, "fn": fn,
                         "precision": round(prec, 4), "recall": round(rec, 4),
                         "f1": round(f1, 4)},
        "paper_level": {"pred_has_data": pred_pos, "gold_findable": gold_findable,
                        "gold_announces_data": gold_announces, "both": both,
                        "precision": round(p_prec, 4), "recall_on_findable": round(p_rec, 4),
                        "gold_announces_no_accession": gold_announces - gold_findable},
        "t1_rate_on_predictions": round(t1_num / max(1, t1_den), 4),
        "t1_counts": {"grounded": t1_num, "total": t1_den},
        "per_paper": per_paper,
    }
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "eval-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                          encoding="utf-8")
    md = [
        f"# Evaluation — pipeline vs {args.name}", "",
        f"- provisional: {report['provisional']}",
        f"- papers: {report['papers']}",
        f"- record-level P/R/F1: {prec:.3f} / {rec:.3f} / {f1:.3f} "
        f"(tp={tp}, fp={fp}, fn={fn})",
        f"- paper-level: pred_has_data={pred_pos}, gold_findable={gold_findable}, "
        f"both={both} (P={p_prec:.3f}, R={p_rec:.3f})",
        f"- gold announces data but no accession: {gold_announces - gold_findable}",
        f"- T1 rate (predictions): {report['t1_rate_on_predictions']:.3f} ({t1_num}/{t1_den})",
    ]
    (out / "eval-report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))


if __name__ == "__main__":
    main()
