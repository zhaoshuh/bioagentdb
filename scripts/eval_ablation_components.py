"""W3/W5 · 组件消融 harness（L-0283）：在样本上重放「抽取→候选扫描→对抗→合并→修正→核验」，
逐组件关闭，测 accession 级 P/R/F1 与 T1 率。

变体（同一篇共享同一次 attack 输出）：
  full          基线（现管线口径）
  no_scan       去掉确定性候选补录（_augment_with_candidates）
  no_reviewers  跳过对抗者与合并（无 reject/fix）
  no_fixes      保留 reject、去掉 fix_* 应用与漏网补录
  no_reground   去掉 _reground_evidence
  t2_blocking   T2 不可达 → 判 rejected（现口径"记录不阻断"）
输出: data/eval/ablation-components.json + .md
用法: python scripts/eval_ablation_components.py [--limit N]
环境: BIOAGENTDB_EVAL_DB=<db> 切换源库（默认 _v15；E5/L-0306 传 _v15）。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import os  # noqa: E402

DB = Path(os.environ.get("BIOAGENTDB_EVAL_DB")
          or ROOT / "data" / "releases" / "_v15" / "bioagentdb.sqlite3")
GOLD = ROOT / "data" / "gold" / "gold.jsonl"
SAMPLE = ROOT / "data" / "gold" / "sample.jsonl"
OUT_J = ROOT / "data" / "eval" / "ablation-components.json"
OUT_M = ROOT / "data" / "eval" / "ablation-components.md"


def prompt_revision() -> str:
    """复盘锚（L-0304/L-0308）：评审包内容指纹（playbook + adversaries + consolidator）。"""
    import hashlib
    h = hashlib.sha256()
    for rel in ("app/redteam/playbook.py", "app/redteam/adversaries.py",
                "app/redteam/consolidate.py"):
        try:
            h.update((ROOT / rel).read_bytes())
        except OSError:
            return ""
    return "sha256:" + h.hexdigest()[:16]


def norm(acc: str) -> str:
    from app.core.vocab import canon_accession
    return canon_accession(acc or "")


def paper_input(sample_row: dict, supp_files: list) -> dict:
    text = sample_row.get("text", "")
    if "[Data Availability]" in text:
        abstract, da = text.split("[Data Availability]", 1)
    else:
        abstract, da = text, ""
    return {"id": sample_row["id"], "title": sample_row.get("title", ""),
            "abstract": abstract.strip(), "da_text": da.strip(), "supp_files": supp_files,
            "themes": sample_row.get("themes") or []}


def run_one(paper: dict, gold_accs: set[str]) -> dict:
    from app.harvest.extract import extract_from_paper
    from app.harvest.llm import available as llm_available
    from app.miner.paperflow import (_add_missed_record, _apply_fix,
                                     _augment_with_candidates, _default_llm,
                                     _reground_evidence)
    from app.harvest import verify as verify_mod
    from app.redteam.adversaries import attack
    from app.redteam.consolidate import consolidate

    llm = _default_llm if llm_available() else None
    abstract, da = paper.get("abstract") or "", paper.get("da_text") or ""
    topic = ""
    try:
        from app.mining.tasks import theme_label
        if paper.get("themes"):
            topic = theme_label(paper["themes"][0]) or paper["themes"][0]
    except Exception:  # noqa: BLE001
        pass

    ext = extract_from_paper(paper, llm_chat=llm, topic=topic)
    base = [r for r in ext.get("records", []) if r.get("relevant", True)]
    scan = _augment_with_candidates(paper, list(base))
    reports = attack(paper, scan) if scan else {}
    decisions = consolidate(paper, scan, reports) if scan else []

    results = {}
    for variant in ("full", "no_scan", "no_reviewers", "no_fixes", "no_reground",
                    "t2_blocking"):
        records = [dict(r) for r in (base if variant == "no_scan" else scan)]
        dec = [] if variant == "no_reviewers" else decisions
        kept = []
        for rec in records:
            actions = [d.get("action") for d in dec
                       if d.get("target") == f"record-{rec.get('identifier')}"]
            if "reject" in actions:
                continue
            if variant != "no_fixes":
                for d in dec:
                    if d.get("target") != f"record-{rec.get('identifier')}":
                        continue
                    if d.get("action") in ("fix_count", "fix_field") and d.get("detail"):
                        _apply_fix(rec, d.get("detail"), abstract, da)
                    elif d.get("action") == "fix_role":
                        rec["role"] = "reuse"
            kept.append(rec)
        if variant != "no_fixes" and dec:
            kept.extend(_add_missed_record(paper, dec, abstract, da))
        outcomes = []
        for rec in kept:
            if variant != "no_reground":
                _reground_evidence(rec, abstract, da)
            result = verify_mod.verify_record(rec, abstract=abstract, da_text=da)
            rec["_status"] = result.get("status", "rejected")
            rec["_reachable"] = bool(result.get("link_reachable"))
            if variant == "t2_blocking" and not rec["_reachable"]:
                rec["_status"] = "rejected"
            if rec["_status"] != "rejected":
                outcomes.append(rec)
        pred = {norm(r.get("identifier")) for r in outcomes}
        pred.discard("")
        t1_num = t1_den = 0
        for r in outcomes:
            ev = json.dumps(r.get("evidence") or {}, ensure_ascii=False).lower()
            a = norm(r.get("identifier"))
            if a:
                t1_den += 1
                if a in ev:
                    t1_num += 1
        results[variant] = {"pred": sorted(pred), "t1_num": t1_num, "t1_den": t1_den,
                            "rejected": len(kept) - len(outcomes)}
    return {"paper_id": paper["id"], "gold": sorted(gold_accs), "variants": results}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=60)
    ap.add_argument("--workers", type=int, default=3)
    args = ap.parse_args()

    gold = {json.loads(l)["paper_id"]: {norm(x) for x in json.loads(l).get("accessions", [])} - {""}
            for l in GOLD.read_text(encoding="utf-8").splitlines() if l.strip()}
    samples = {json.loads(l)["id"]: json.loads(l)
               for l in SAMPLE.read_text(encoding="utf-8").splitlines() if l.strip()}

    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    cols = [r[1] for r in c.execute("PRAGMA table_info(papers)")]
    has_supp = "supp_files" in cols
    with_data = [pid for pid, in c.execute("SELECT DISTINCT paper_id FROM dataset_papers")]
    supp = {}
    if has_supp:
        for pid, sf in c.execute("SELECT id, supp_files FROM papers"):
            try:
                supp[pid] = json.loads(sf or "[]")
            except Exception:  # noqa: BLE001
                supp[pid] = []
    c.close()

    targets = [p for p in sorted(set(with_data) & set(samples))]
    if args.limit:
        targets = targets[:args.limit]
    print(f"ablating {len(targets)} papers with records")

    rows = []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(run_one, paper_input(samples[p], supp.get(p, [])),
                          gold.get(p, set())): p for p in targets}
        for i, fut in enumerate(as_completed(futs), 1):
            try:
                rows.append(fut.result())
            except Exception as exc:  # noqa: BLE001
                rows.append({"paper_id": futs[fut], "error": type(exc).__name__})
            if i % 10 == 0:
                print(f"  {i}/{len(targets)} ({time.time()-t0:.0f}s)", flush=True)

    variants = ("full", "no_scan", "no_reviewers", "no_fixes", "no_reground", "t2_blocking")
    agg = {v: Counter() for v in variants}
    for row in rows:
        if "variants" not in row:
            continue
        goldset = set(row["gold"])
        for v in variants:
            got = set(row["variants"][v]["pred"])
            agg[v]["tp"] += len(got & goldset)
            agg[v]["fp"] += len(got - goldset)
            agg[v]["fn"] += len(goldset - got)
            agg[v]["t1_num"] += row["variants"][v]["t1_num"]
            agg[v]["t1_den"] += row["variants"][v]["t1_den"]

    summary = {}
    for v in variants:
        a = agg[v]
        prec = a["tp"] / max(1, a["tp"] + a["fp"])
        rec = a["tp"] / max(1, a["tp"] + a["fn"])
        f1 = 2 * prec * rec / max(1e-9, prec + rec)
        summary[v] = {"tp": a["tp"], "fp": a["fp"], "fn": a["fn"],
                      "precision": round(prec, 4), "recall": round(rec, 4),
                      "f1": round(f1, 4),
                      "t1_rate": round(a["t1_num"] / max(1, a["t1_den"]), 4)}
    out = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
           "prompt_revision": prompt_revision(),
           "db": str(DB),
           "papers": len([r for r in rows if "variants" in r]),
           "errors": [r["paper_id"] for r in rows if "variants" not in r],
           "variants": summary, "per_paper": rows}
    OUT_J.parent.mkdir(parents=True, exist_ok=True)
    OUT_J.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    md = ["# Component ablations (the sample)", "",
          f"- papers: {out['papers']} (errors: {len(out['errors'])})"]
    for v in variants:
        s = summary[v]
        md.append(f"- {v}: P {s['precision']:.3f} / R {s['recall']:.3f} / F1 {s['f1']:.3f} "
                  f"(tp={s['tp']} fp={s['fp']} fn={s['fn']}; T1 {s['t1_rate']:.3f})")
    OUT_M.write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))
    print(f"wrote {OUT_J}")


if __name__ == "__main__":
    main()
