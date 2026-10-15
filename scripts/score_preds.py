"""对任意预测文件 vs 参照算 P/R/F1（统一口径）。

支持两种 preds：
  1) {paper_id, pred:[acc,...]}            —— 基线
  2) {paper_id, datasets:[{links:[{accession}]}]} —— 管线 predictions.jsonl
参照：gold.jsonl（真人）或 reference.jsonl（正则）。

用法:
  python scripts/score_preds.py --preds data/gold/baseline-rag-preds.jsonl \
      --ref data/gold/gold.jsonl --name "RAG vs human gold"
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:          # 允许直接 python scripts/score_preds.py 运行
    sys.path.insert(0, str(ROOT))


def canon(a: str) -> str:
    from app.core.vocab import canon_accession
    return canon_accession(a)


def load_preds(path: Path) -> dict:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if "pred" in r:
            accs = {canon(a) for a in r["pred"] if a}
        else:
            accs = set()
            for d in r.get("datasets", []):
                for l in d.get("links", []):
                    a = canon(l.get("accession", ""))
                    if a:
                        accs.add(a)
        out[r["paper_id"]] = accs
    return out


def load_ref(path: Path) -> dict:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            out[r["paper_id"]] = {canon(a) for a in r.get("accessions", [])}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--preds", required=True)
    ap.add_argument("--ref", required=True)
    ap.add_argument("--name", default="")
    args = ap.parse_args()
    P = load_preds(Path(args.preds))
    R = load_ref(Path(args.ref))
    papers = sorted(set(P) | set(R))
    tp = fp = fn = 0
    for pid in papers:
        p, r = P.get(pid, set()), R.get(pid, set())
        tp += len(p & r)
        fp += len(p - r)
        fn += len(r - p)
    prec = tp / max(1, tp + fp)
    rec = tp / max(1, tp + fn)
    f1 = 2 * prec * rec / max(1e-9, prec + rec)
    print(f"{args.name or args.preds}: P/R/F1 = {prec:.3f} / {rec:.3f} / {f1:.3f} "
          f"(tp={tp}, fp={fp}, fn={fn}, n={len(papers)})")


if __name__ == "__main__":
    main()
