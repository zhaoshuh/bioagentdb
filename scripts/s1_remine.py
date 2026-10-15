"""S1 集成验证：在草稿副本上重挖样本论文（含候选扫描），对人工标注重算指标。

用独立 DB（data/scratch/s1.sqlite3）跑，避免污染正式库。
用法: python scripts/s1_remine.py [--n 250] [--workers 6]
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRATCH = ROOT / "data" / "scratch"
SCRATCH_DB = SCRATCH / "s1.sqlite3"
os.environ["BIOAGENTDB_DATA_DIR"] = str(SCRATCH)
os.environ["BIOAGENTDB_DB_PATH"] = str(SCRATCH_DB)
sys.path.insert(0, str(ROOT))


def backup_live() -> None:
    SCRATCH.mkdir(parents=True, exist_ok=True)
    live = ROOT / "data" / "bioagentdb.sqlite3"
    if SCRATCH_DB.exists():
        SCRATCH_DB.unlink()
    src = sqlite3.connect(f"file:{live}?mode=ro", uri=True)
    dst = sqlite3.connect(SCRATCH_DB)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def main() -> None:
    from app.core.vocab import expand_accession
    from app.miner.paperflow import run_paper
    from app.harvest.llm import available

    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=250)
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()

    g = ROOT / "data" / "gold"
    sample = [json.loads(l) for l in (g / "sample.jsonl").read_text(
        encoding="utf-8").splitlines() if l.strip()][: args.n]
    gold = {r["paper_id"]: {t for a in r.get("accessions", []) for t in expand_accession(a)}
            for r in (json.loads(l) for l in (g / "gold.jsonl").read_text(
                encoding="utf-8").splitlines() if l.strip())}

    backup_live()
    print(f"scratch db: {SCRATCH_DB} | llm available: {available()}")

    conn = sqlite3.connect(f"file:{SCRATCH_DB}?mode=ro", uri=True)
    themes = {}
    for r in conn.execute("SELECT paper_id, theme FROM paper_themes"):
        themes.setdefault(r[0], r[1])
    conn.close()

    ids = [s["id"] for s in sample]
    done = 0

    def work(pid: str) -> None:
        th = themes.get(pid, "alzheimers-disease")
        try:
            run_paper(pid, th)
        except Exception as exc:  # noqa: BLE001
            print("ERR", pid, str(exc)[:100])

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(work, pid) for pid in ids]
        for _ in as_completed(futs):
            done += 1
            if done % 25 == 0:
                print(f"  remined {done}/{len(ids)}")

    # 组装重挖后的预测并评分
    conn = sqlite3.connect(f"file:{SCRATCH_DB}?mode=ro", uri=True)
    pred = {}
    for pid in ids:
        accs = set()
        for (acc,) in conn.execute(
                "SELECT l.accession FROM dataset_papers dp JOIN dataset_links l "
                "ON l.dataset_id=dp.dataset_id WHERE dp.paper_id=?", (pid,)):
            if acc:
                accs.update(expand_accession(acc))
        pred[pid] = accs
    conn.close()

    tp = fp = fn = 0
    for pid in set(pred) | set(gold):
        p, r = pred.get(pid, set()), gold.get(pid, set())
        tp += len(p & r)
        fp += len(p - r)
        fn += len(r - p)
    prec = tp / max(1, tp + fp)
    rec = tp / max(1, tp + fn)
    f1 = 2 * prec * rec / max(1e-9, prec + rec)
    report = {"n": len(ids), "tp": tp, "fp": fp, "fn": fn, "precision": round(prec, 4),
              "recall": round(rec, 4), "f1": round(f1, 4),
              "reference": "human annotation (single annotator, provisional)"}
    (g / "s1-remine-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                             encoding="utf-8")
    md = ["# S1 remine — integrated pipeline (candidate scan in the pipeline)", "",
          f"- papers remined: {len(ids)}", f"- P/R/F1 = {prec:.3f} / {rec:.3f} / {f1:.3f} "
          f"(tp={tp}, fp={fp}, fn={fn})",
          "", "compare: pipeline-only 0.974/0.773/0.862 · union(pipe+scan) 0.971/0.928/0.949"]
    (g / "s1-remine-report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))


if __name__ == "__main__":
    main()
