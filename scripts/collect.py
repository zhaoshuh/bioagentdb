"""BioAgentDB 用户版（命令行）。

一边采集一边把记录写到「同级目录」的文件夹；挖到"空了"就出总结报告，否则一直跑。

用法:
  python scripts/collect.py --disease "spinocerebellar ataxia"
  python scripts/collect.py --disease huntingtons-disease --limit 100 --workers 3
  python scripts/collect.py --disease "frontotemporal dementia,multiple system atrophy"

默认输出到项目上级目录: ../bioagentdb-records/<disease-slug>/
  records.jsonl   每篇论文及其数据集/证据/链接
  datasets.jsonl  每条数据集记录（扁平）
  REPORT.md       该疾病的总结报告
  miner.log       本轮挖掘日志

用户版不启用 LLM 缓存。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

os.environ["BIOAGENTDB_LLM_CACHE"] = "0"   # 用户版：不搞缓存

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.config import cfg  # noqa: E402


def resolve_theme(disease: str) -> tuple[str, str]:
    """疾病名/slug → (slug, 展示名)，不存在则动态建主题。"""
    from app.harvest import themes as T

    slug = T.slugify(disease)
    th = T.get_theme(slug)
    if th:
        return th["slug"], th["name"]
    for t in T.list_themes():
        if T.slugify(t["name"]) == slug:
            return t["slug"], t["name"]
    th = T.ensure_disease_theme(slug, disease, queries=T.default_queries(disease))
    return th["slug"], th.get("name", disease)


def run_miner(slug: str, workers: int, limit: int, log: Path) -> int:
    env = dict(os.environ)
    env["BIOAGENTDB_LLM_CACHE"] = "0"
    cmd = [sys.executable, str(ROOT / "scripts" / "miner.py"), "--theme", slug,
           "--workers", str(workers), "--limit", str(limit), "--log", str(log)]
    return subprocess.call(cmd, env=env, cwd=str(ROOT))


def export_records(slug: str, outdir: Path) -> tuple[int, int]:
    from app.catalog import store

    data = store.list_records(theme=slug, q="", limit=100000, offset=0, sort="year")
    items = data["items"]
    n_ds = 0
    with (outdir / "records.jsonl").open("w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")
    with (outdir / "datasets.jsonl").open("w", encoding="utf-8") as f:
        for it in items:
            for ds in it.get("datasets", []):
                n_ds += 1
                row = dict(ds)
                row["paper_id"] = it["id"]
                row["paper_title"] = it.get("title")
                row["paper_year"] = it.get("year")
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(items), n_ds


def write_report(slug: str, name: str, outdir: Path, rounds: int, elapsed: int,
                 exhausted: bool, note: str = "") -> None:
    items = [json.loads(l) for l in
             (outdir / "records.jsonl").read_text(encoding="utf-8").splitlines() if l]
    datasets = [json.loads(l) for l in
                (outdir / "datasets.jsonl").read_text(encoding="utf-8").splitlines() if l]
    mods = Counter(m for d in datasets
                   for m in (d.get("modalities") or ([d["modality"]] if d.get("modality") else [])))
    repos = Counter((l.get("repository") or "") for d in datasets for l in (d.get("links") or []))
    orgs = Counter(d.get("organism") or "" for d in datasets)
    lines = [
        f"# {name} — BioAgentDB summary",
        "",
        f"- disease: `{slug}`",
        f"- status: {'mined out (no new records left)' if exhausted else 'stopped (round cap)'}",
        f"- rounds run: {rounds} · elapsed: {elapsed // 60}m{elapsed % 60}s",
        f"- papers with data: {len(items)}",
        f"- dataset records: {len(datasets)}",
        *([f"- note: {note}"] if note else []),
        "",
        "## Modalities",
        *[f"- {k}: {v}" for k, v in mods.most_common()],
        "",
        "## Repositories",
        *[f"- {k or 'n/a'}: {v}" for k, v in repos.most_common()],
        "",
        "## Organisms",
        *[f"- {k or 'n/a'}: {v}" for k, v in orgs.most_common()],
        "",
        "## Sample records",
    ]
    for it in items[:15]:
        accs = [((d.get("links") or [{}])[0] or {}).get("accession") for d in it.get("datasets", [])]
        accs = [a for a in accs if a]
        lines.append(f"- {it.get('title','')[:110]}  ·  {', '.join(accs[:4])}")
    (outdir / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def collect_one(disease: str, out_root: Path, workers: int, limit: int, max_rounds: int,
                empty_rounds: int) -> dict:
    from app.chat import mining
    from app.core.db import init_db
    from app.harvest.themes import seed_themes
    from app.miner.claim import count_open, count_pending, refill

    init_db()
    seed_themes(force=True)
    slug, name = resolve_theme(disease)
    outdir = out_root / slug
    outdir.mkdir(parents=True, exist_ok=True)
    log = outdir / "miner.log"

    print(f"\n=== {name} ({slug}) → {outdir} ===")
    t0 = time.time()
    empty = 0
    rounds = 0
    source_errors = 0
    try:
        while rounds < max_rounds:
            rounds += 1
            before = mining._counts(slug)[1]
            rc = run_miner(slug, workers, limit, log)
            if rc == 3:
                # miner 报告"源站异常"（L-0212）：不计空轮、退避后继续
                source_errors += 1
                print("! miner reported source-unwell; backing off "
                      f"({source_errors} time(s))")
                time.sleep(min(600, 60 * source_errors))
                continue
            after = mining._counts(slug)[1]
            pending = count_pending(slug)
            if pending == 0:
                try:
                    refill(slug, 200)     # 补给；没有新论文就真到头了
                    pending = count_pending(slug)
                except Exception as exc:  # noqa: BLE001
                    # 补货失败：不能当"挖尽"（L-0212 修复）
                    source_errors += 1
                    print(f"! refill failed (source unwell?): {str(exc)[:140]}")
                    pending = -1
            open_n = count_open(slug) if pending >= 0 else -1   # 含在飞 claim（L-0212 修复 V-2）
            added = after - before
            n_papers, n_ds = export_records(slug, outdir)
            print(f"[round {rounds}] +{added} records · total {n_ds} · "
                  f"{n_papers} papers with data · open {open_n}")
            if added == 0 and open_n == 0:
                empty += 1
            else:
                empty = 0
            if empty >= empty_rounds:
                print("→ mined out: no new records and nothing left to mine.")
                break
    except KeyboardInterrupt:
        print("\n(interrupted)")

    exhausted = rounds < max_rounds and empty >= empty_rounds
    note = f"source-unwell events: {source_errors}" if source_errors else ""
    n_papers, n_ds = export_records(slug, outdir)
    write_report(slug, name, outdir, rounds, int(time.time() - t0), exhausted, note=note)
    return {"disease": name, "slug": slug, "papers": n_papers, "datasets": n_ds,
            "rounds": rounds, "exhausted": exhausted, "source_errors": source_errors,
            "outdir": str(outdir)}


def main() -> None:
    ap = argparse.ArgumentParser(description="BioAgentDB user CLI — collect data records")
    ap.add_argument("--disease", required=True,
                    help="疾病名或 slug（逗号分隔可多个）")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[2] / "bioagentdb-records"),
                    help="输出根目录（默认：项目同级目录 bioagentdb-records/）")
    ap.add_argument("--workers", type=int, default=cfg.int("collect_workers", 3))
    ap.add_argument("--limit", type=int, default=cfg.int("collect_limit", 100),
                    help="每轮最多处理的论文数")
    ap.add_argument("--rounds", type=int, default=cfg.int("collect_rounds", 50), help="最多轮数")
    ap.add_argument("--empty-rounds", type=int, default=cfg.int("collect_empty_rounds", 2),
                    help="连续多少轮无新记录且无待挖即判定挖空")
    args = ap.parse_args()

    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)
    diseases = [d.strip() for d in args.disease.split(",") if d.strip()]
    results = [collect_one(d, out_root, args.workers, args.limit, args.rounds,
                           args.empty_rounds) for d in diseases]

    lines = ["# BioAgentDB — collection summary", "",
             f"output root: `{out_root}`", "",
             "| disease | papers with data | dataset records | rounds | status |",
             "|---|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r['disease']} | {r['papers']} | {r['datasets']} | "
                     f"{r['rounds']} | {'mined out' if r['exhausted'] else 'round cap'} |")
    (out_root / "SUMMARY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nsummary: {out_root / 'SUMMARY.md'}")
    for r in results:
        print(f"  {r['disease']}: {r['papers']} papers, {r['datasets']} datasets -> {r['outdir']}")


if __name__ == "__main__":
    main()
