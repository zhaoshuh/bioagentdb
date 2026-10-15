"""W1（路径 B'）· 模型辅助一致性（L-0288）：独立 LLM 标注者对 100 篇 IAA 子集重标，
与人类 gold 同口径比较（κ / 双向 micro P/R/F1 / 分歧表 + 幻觉检查）。

产出（data/gold/iaa/model-assist/）：
  annotation-a-subset.csv   人类 gold 在子集上的对应行
  annotation-b-model.csv    模型标注
  iaa-report.{json,md} + arbitration.csv（由 iaa_score.py 生成）
用法: python scripts/iaa_model_assist.py [--limit N]
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SUBSET = ROOT / "data" / "gold" / "iaa" / "subset.json"
SAMPLE = ROOT / "data" / "gold" / "sample.jsonl"
ANNOT = ROOT / "data" / "gold" / "annotation.csv"
OUTDIR = ROOT / "data" / "gold" / "iaa" / "model-assist"
COLS = ["paper_id", "title", "year", "themes", "text",
        "announces_data(Y/N)", "accessions(semicolon)", "evidence_quote", "notes"]

PROMPT = """You annotate ONE scientific paper for a gold dataset-annotation task. Read ONLY the text below (abstract + Data Availability). Rules, applied exactly:
- announces_data = "Y" if the text names a public dataset/accession (deposited OR reused), or says data are available upon request, or names supplementary files as the data source; otherwise "N".
- accessions: repository identifiers exactly as printed (e.g. GSE12345, PXD000000, phs000000, E-MTAB-1234, 10.5281/zenodo.12345). Only identifiers that appear literally in the text. Empty list if none.
Return JSON only: {"announces_data": "Y" or "N", "accessions": ["..."], "notes": "short"}.

TEXT:
__TEXT__"""


def build_prompt(text: str) -> str:
    return PROMPT.replace("__TEXT__", text)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    subset = json.loads(SUBSET.read_text(encoding="utf-8"))["papers"]
    ids = [r["paper_id"] for r in subset]
    if args.limit:
        ids = ids[:args.limit]
    samples = {}
    for l in SAMPLE.read_text(encoding="utf-8").splitlines():
        if l.strip():
            r = json.loads(l)
            samples[r["id"]] = r

    OUTDIR.mkdir(parents=True, exist_ok=True)
    raw_path = OUTDIR / "model-raw.jsonl"
    done = {}
    if raw_path.exists():
        for l in raw_path.read_text(encoding="utf-8").splitlines():
            if l.strip():
                r = json.loads(l)
                done[r["paper_id"]] = r

    from app.harvest.llm import chat_json
    fh = raw_path.open("a", encoding="utf-8")
    t0 = time.time()
    for i, pid in enumerate(ids, 1):
        if pid in done:
            continue
        row = samples.get(pid, {})
        try:
            data = chat_json([{"role": "user",
                               "content": build_prompt((row.get("text") or "")[:6000])}],
                             model=None, timeout=90)
        except Exception as exc:  # noqa: BLE001
            print(f"  {pid} EXC {type(exc).__name__}", flush=True)
            continue
        rec = {"paper_id": pid,
               "announces_data": str(data.get("announces_data", "N")).upper()[:1],
               "accessions": [str(a).strip() for a in (data.get("accessions") or []) if str(a).strip()],
               "notes": str(data.get("notes", ""))[:200]}
        done[pid] = rec
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        fh.flush()
        if i % 10 == 0:
            print(f"  annotated {i}/{len(ids)} ({time.time()-t0:.0f}s)", flush=True)
    fh.close()

    # human subset CSV
    humans = {}
    with ANNOT.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            humans[(row.get("paper_id") or "").strip()] = row
    with (OUTDIR / "annotation-a-subset.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(COLS)
        for pid in ids:
            h = humans.get(pid)
            if not h:
                continue
            w.writerow([h.get(c, "") for c in COLS])
    # model CSV
    with (OUTDIR / "annotation-b-model.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(COLS)
        hall = 0
        total_acc = 0
        for pid in ids:
            rec = done.get(pid)
            if not rec:
                continue
            text_up = (samples.get(pid, {}).get("text") or "").upper()
            for a in rec["accessions"]:
                total_acc += 1
                if a.upper() not in text_up:
                    hall += 1
            w.writerow([pid, samples.get(pid, {}).get("title", ""),
                        samples.get(pid, {}).get("year", ""),
                        ";".join(samples.get(pid, {}).get("themes") or []),
                        "", rec["announces_data"], ";".join(rec["accessions"]),
                        "", (rec["notes"] + f" [model; hallucinated={hall}]")[:200]])
    print(f"model accessions: {total_acc} | not-verbatim (hallucination check): {hall}")

    subprocess.run([sys.executable, str(ROOT / "scripts" / "iaa_score.py"),
                    str(OUTDIR / "annotation-a-subset.csv"),
                    str(OUTDIR / "annotation-b-model.csv"),
                    "--out", str(OUTDIR)])


if __name__ == "__main__":
    main()
