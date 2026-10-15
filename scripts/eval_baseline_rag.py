"""基线：检索增强（RAG 风格）抽取 —— 先检索相关句，再让 LLM 抽登记号。

与「整篇喂给裸 LLM」（baseline-llm）和「管线」在同一 250 篇样本上比较。
用法: python scripts/eval_baseline_rag.py --n 250 [--k 8]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.harvest.llm import chat  # noqa: E402

ACC_RE = re.compile(
    r"\b(GSE\d{3,7}|PXD\d{5,7}|PRJNA\d{4,9}|PRJEB\d{4,9}|SRP\d{5,8}|MTBLS\d{3,6}|"
    r"phs\d{6}(?:\.v\d+\.p\d+\b)?|EGA[SDN]\d{8,14}|E-[A-Z]{4}-\d{3,5}|CRA\d{6}|JPST\d{6}|"
    r"10\.5281/zenodo\.\d{4,9}|10\.6084/m9\.figshare\.\d{5,9}|10\.5061/dryad\.[a-z0-9]+)",
    re.I)
SENT = re.compile(r"(?<=[.!?])\s+|\n+")
KEY = re.compile(r"deposit|availab|accession|repository|GEO|GSE|PRIDE|PXD|Zenodo|dbGaP|"
                 r"data (?:are|were|is|was)|raw data|supplementar|archive|BioProject|SRA|ENA",
                 re.I)
SYS = ("You are a biomedical data curator. From the excerpts, list every public dataset "
       "accession number mentioned. Output ONLY a comma-separated list, or NONE.")


def canon(a: str) -> str:
    a = (a or "").strip().lower()
    return re.sub(r"\.v\d+\.p\d+$", "", a)


def retrieve(text: str, k: int) -> list[str]:
    sents = [s.strip() for s in SENT.split(text or "") if s.strip()]
    scored = []
    for i, s in enumerate(sents):
        score = len(KEY.findall(s)) + (2 if ACC_RE.search(s) else 0)
        scored.append((score, i, s))
    scored.sort(key=lambda x: (-x[0], x[1]))
    top = sorted(scored[:k], key=lambda x: x[1])
    return [s for _, _, s in top]


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=250)
    ap.add_argument("--k", type=int, default=8)
    ap.add_argument("--gold", default=str(root / "data" / "gold"))
    args = ap.parse_args()
    g = Path(args.gold)
    sample = [json.loads(l) for l in (g / "sample.jsonl").read_text(
        encoding="utf-8").splitlines() if l.strip()][: args.n]
    ref = {json.loads(l)["paper_id"]: {canon(a) for a in json.loads(l)["accessions"]}
           for l in (g / "reference.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()}

    tp = fp = fn = 0
    pred_rows = []
    for s in sample:
        ctx = "\n".join(retrieve(s.get("text") or "", args.k))[:4000]
        try:
            out = chat([{"role": "system", "content": SYS},
                        {"role": "user", "content": ctx}], temperature=0.0, timeout=120)
            ans = out.get("content") or ""
        except Exception:  # noqa: BLE001
            ans = ""
        P = {canon(a) for a in ACC_RE.findall(ans)}
        R = ref.get(s["id"], set())
        tp += len(P & R)
        fp += len(P - R)
        fn += len(R - P)
        pred_rows.append({"paper_id": s["id"], "pred": sorted(P)})

    with (g / "baseline-rag-preds.jsonl").open("w", encoding="utf-8") as fh:
        for row in pred_rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    p = tp / max(1, tp + fp)
    r = tp / max(1, tp + fn)
    f = 2 * p * r / max(1e-9, p + r)
    report = {"provisional": True, "method": f"retrieval-augmented (top-{args.k} sentences)",
              "n": len(sample), "tp": tp, "fp": fp, "fn": fn,
              "precision": round(p, 4), "recall": round(r, 4), "f1": round(f, 4)}
    (g / "baseline-rag-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                                encoding="utf-8")
    md = [f"# Baseline: retrieval-augmented (provisional, top-{args.k})", "",
          f"- n = {len(sample)}", f"- P/R/F1 = {p:.3f} / {r:.3f} / {f:.3f} "
          f"(tp={tp}, fp={fp}, fn={fn})"]
    (g / "baseline-rag-report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))


if __name__ == "__main__":
    main()
