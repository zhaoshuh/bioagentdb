"""基线：裸 LLM 抽取（无对抗者、无核验），对同一正则参照评测。

与管线结果（0.978/0.946/0.962）可比。
用法: python scripts/eval_baseline_llm.py --n 60 [--gold data/gold]
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
    r"phs\d{6}(?:\.v\d+\.p\d+)?|EGA[SDN]\d{8,14}|E-[A-Z]{4}-\d{3,5}|CRA\d{6}|JPST\d{6}|"
    r"10\.5281/zenodo\.\d{4,9}|10\.6084/m9\.figshare\.\d{5,9}|10\.5061/dryad\.[a-z0-9]+)\b",
    re.I)

SYS = ("You are a biomedical data curator. From the paper text, list every public dataset "
       "accession number explicitly mentioned. Output ONLY a comma-separated list of "
       "accessions. If none, output NONE.")


def canon(a: str) -> str:
    a = (a or "").strip().lower()
    return re.sub(r"\.v\d+\.p\d+$", "", a)


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=60)
    ap.add_argument("--gold", default=str(root / "data" / "gold"))
    args = ap.parse_args()
    g = Path(args.gold)
    sample = [json.loads(l) for l in (g / "sample.jsonl").read_text(
        encoding="utf-8").splitlines() if l.strip()][: args.n]
    ref = {json.loads(l)["paper_id"]: {canon(a) for a in json.loads(l)["accessions"]}
           for l in (g / "reference.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()}

    tp = fp = fn = 0
    rows = []
    for s in sample:
        text = (s.get("text") or "")[:4000]
        try:
            out = chat([{"role": "system", "content": SYS},
                        {"role": "user", "content": text}], temperature=0.0, timeout=120)
            ans = out.get("content") or ""
        except Exception as exc:  # noqa: BLE001
            ans = ""
            rows.append({"paper_id": s["id"], "error": str(exc)[:120]})
        P = {canon(a) for a in ACC_RE.findall(ans)}
        R = ref.get(s["id"], set())
        tp += len(P & R)
        fp += len(P - R)
        fn += len(R - P)
        rows.append({"paper_id": s["id"], "pred": sorted(P), "ref": sorted(R)})

    p = tp / max(1, tp + fp)
    r = tp / max(1, tp + fn)
    f = 2 * p * r / max(1e-9, p + r)
    # 保存逐篇预测，供对任意参照（gold / regex）重算
    with (g / "baseline-llm-preds.jsonl").open("w", encoding="utf-8") as fh:
        for row in rows:
            if "pred" in row:
                fh.write(json.dumps({"paper_id": row["paper_id"], "pred": row["pred"]},
                                    ensure_ascii=False) + "\n")
    report = {"provisional": True, "method": "raw LLM extraction (no reviewers/verify)",
              "n": len(sample), "tp": tp, "fp": fp, "fn": fn,
              "precision": round(p, 4), "recall": round(r, 4), "f1": round(f, 4)}
    g.mkdir(parents=True, exist_ok=True)
    (g / "baseline-llm-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                                encoding="utf-8")
    md = ["# Baseline: raw LLM extraction (provisional)", "",
          f"- n = {len(sample)}", f"- P/R/F1 = {p:.3f} / {r:.3f} / {f:.3f} "
          f"(tp={tp}, fp={fp}, fn={fn})",
          f"- compare pipeline: P/R/F1 = 0.978 / 0.946 / 0.962"]
    (g / "baseline-llm-report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))


if __name__ == "__main__":
    main()
