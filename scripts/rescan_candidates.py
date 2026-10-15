"""S1 验证（无需 LLM）：扩展词表 + 确定性候选扫描 能否补回漏抽。

对 sample 文本扫描候选，与管线预测取并集，对 human gold 重算 P/R/F1。
用法: python scripts/rescan_candidates.py [--gold data/gold]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.harvest.candidates import scan_texts  # noqa: E402
from app.core.vocab import expand_accession as _expand  # noqa: E402


def canon(a: str) -> str:
    from app.core.vocab import canon_accession
    return canon_accession(a)


def accset(items) -> set:
    s: set = set()
    for a in items:
        s.update(_expand(a))
    return s


def jl(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def score(preds: dict, ref: dict) -> dict:
    tp = fp = fn = 0
    for pid in set(preds) | set(ref):
        p, r = preds.get(pid, set()), ref.get(pid, set())
        tp += len(p & r)
        fp += len(p - r)
        fn += len(r - p)
    prec = tp / max(1, tp + fp)
    rec = tp / max(1, tp + fn)
    f1 = 2 * prec * rec / max(1e-9, prec + rec)
    return {"tp": tp, "fp": fp, "fn": fn, "precision": round(prec, 4),
            "recall": round(rec, 4), "f1": round(f1, 4)}


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", default=str(root / "data" / "gold"))
    args = ap.parse_args()
    g = Path(args.gold)

    sample = {r["id"]: (r.get("text") or "") for r in jl(g / "sample.jsonl")}
    gold = {r["paper_id"]: accset(r.get("accessions", [])) for r in jl(g / "gold.jsonl")}
    pipe = {}
    for r in jl(g / "predictions.jsonl"):
        accs = [l["accession"] for d in r.get("datasets", []) for l in d.get("links", [])
                if l.get("accession")]
        pipe[r["paper_id"]] = accset(accs)

    scan: dict[str, set[str]] = {}
    for pid, text in sample.items():
        scan[pid] = accset([c["accession"] for c in scan_texts([("sample", text)])])

    union = {pid: pipe.get(pid, set()) | scan.get(pid, set()) for pid in set(pipe) | set(scan)}
    res = {"pipeline_only": score(pipe, gold),
           "scan_only": score(scan, gold),
           "union(pipe+scan)": score(union, gold)}
    out = {"reference": "human annotation (single annotator, provisional)", "results": res}
    (g / "s1-rescan-report.json").write_text(json.dumps(out, ensure_ascii=False, indent=2),
                                             encoding="utf-8")
    md = ["# S1 validation — vocabulary + deterministic candidate scan (no LLM)", "",
          "| variant | P | R | F1 | tp | fp | fn |", "|---|---|---|---|---|---|---|"]
    for k, v in res.items():
        md.append(f"| {k} | {v['precision']} | {v['recall']} | {v['f1']} | "
                  f"{v['tp']} | {v['fp']} | {v['fn']} |")
    # 回收的漏抽
    only_pipe = res["pipeline_only"]["fn"]
    only_union = res["union(pipe+scan)"]["fn"]
    md += ["", f"FN: pipeline {only_pipe} → union {only_union} "
               f"(recovered {only_pipe - only_union} of the missed accessions)"]
    (g / "s1-rescan-report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))


if __name__ == "__main__":
    main()
