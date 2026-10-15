"""W3 · 评审员级一致性实验（L-0283）：对样本论文重跑三名对抗者，记录**逐票**并统计。

- 输入：`data/gold/sample.jsonl`（论文文本）+ release 库候选（重建 records）。
- 每篇：`app.redteam.adversaries.attack()` → 逐评审者 findings（target/severity/claim/
  expected_action）；记录 JSON 失败率。
- 统计：各者 finding 数/目标面/严重度；两两在"记录级标靶"上的 Jaccard 与 κ；
  共识（≥2 者）vs 独唱；原始输出存 data/eval/reviewer-raw/。
- 输出：data/eval/reviewer-agreement.json + .md
用法: python scripts/eval_reviewer_agreement.py [--limit N] [--only-with-records]
环境: BIOAGENTDB_EVAL_DB=<db> 切换源库（默认 _v15；E5/L-0306 传 _v15）。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import os  # noqa: E402

DB = Path(os.environ.get("BIOAGENTDB_EVAL_DB")
          or ROOT / "data" / "releases" / "_v15" / "bioagentdb.sqlite3")
SAMPLE = ROOT / "data" / "gold" / "sample.jsonl"
OUT_J = ROOT / "data" / "eval" / "reviewer-agreement.json"
OUT_M = ROOT / "data" / "eval" / "reviewer-agreement.md"
RAW = ROOT / "data" / "eval" / "reviewer-raw"
PAIRS = list(combinations(["veracity", "coverage", "metadata"], 2))


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


def kappa_from_flags(pairs: list[tuple[bool, bool]]) -> tuple[float, float]:
    n = len(pairs)
    if not n:
        return 0.0, 0.0
    po = sum(1 for a, b in pairs if a == b) / n
    pa = sum(1 for a, _ in pairs if a) / n
    pb = sum(1 for _, b in pairs if b) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return po, ((po - pe) / (1 - pe) if pe < 1 else 0.0)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    papers = [json.loads(l) for l in SAMPLE.read_text(encoding="utf-8").splitlines() if l.strip()]
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    cand_rows = defaultdict(list)
    for pid, acc, source, title, verdict in c.execute(
            "SELECT paper_id, accession, source, title, verdict FROM candidates"):
        cand_rows[pid].append({"identifier": acc, "repository": source, "title": title,
                               "role": "primary", "verdict": verdict})
    c.close()

    targets = [p for p in papers if cand_rows.get(p["id"])]
    if args.limit:
        targets = targets[:args.limit]
    print(f"papers with candidates: {len(targets)} (of {len(papers)} sample rows)")

    from app.redteam.adversaries import attack, ADVERSARIES
    RAW.mkdir(parents=True, exist_ok=True)

    per_adv = {a: Counter() for a in ADVERSARIES}
    target_flags = defaultdict(dict)          # (paper, target) -> {adv: bool}
    paper_flags = defaultdict(dict)           # paper -> {adv: bool}
    json_fail = Counter()
    done = 0
    for p in targets:
        paper = {"id": p["id"], "title": p.get("title", ""),
                 "abstract": (p.get("text") or "").split("[Data Availability]")[0][:2000],
                 "da_text": (p.get("text") or "")}
        records = cand_rows[p["id"]]
        rep = attack(paper, records)
        (RAW / f"{p['id']}.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1),
                                             encoding="utf-8")
        for a in ADVERSARIES:
            finds = (rep.get(a) or {}).get("findings") or []
            if not finds and rep.get(a, {}).get("ms", 0) == 0:
                json_fail[a] += 1
            for f in finds:
                tgt = str(f.get("target") or "paper")
                per_adv[a][tgt if tgt == "paper" else "record"] += 1
                per_adv[a][f"sev:{f.get('severity', 'low')}"] += 1
                per_adv[a][f"act:{f.get('expected_action', 'verify')}"] += 1
                key = (p["id"], tgt)
                target_flags[key][a] = True
            paper_flags[p["id"]][a] = bool(finds)
        done += 1
        if done % 10 == 0:
            print(f"  attacked {done}/{len(targets)}", flush=True)

    pair_stats = {}
    for a, b in PAIRS:
        pairs = [(target_flags[k].get(a, False), target_flags[k].get(b, False))
                 for k in target_flags]
        po, kap = kappa_from_flags(pairs)
        both = sum(1 for x, y in pairs if x and y)
        only_a = sum(1 for x, y in pairs if x and not y)
        only_b = sum(1 for x, y in pairs if y and not x)
        union = both + only_a + only_b
        pair_stats[f"{a}|{b}"] = {
            "both": both, "only_" + a: only_a, "only_" + b: only_b,
            "jaccard": round(both / union, 4) if union else None,
            "raw_agreement": round(po, 4), "kappa": round(kap, 4),
        }

    paper_pairs = {}
    for a, b in PAIRS:
        pp = [(paper_flags[k].get(a, False), paper_flags[k].get(b, False)) for k in paper_flags]
        po, kap = kappa_from_flags(pp)
        paper_pairs[f"{a}|{b}"] = {"raw_agreement": round(po, 4), "kappa": round(kap, 4)}

    consensus = Counter(0 for _ in [])
    for k, flags in target_flags.items():
        consensus[len(flags)] += 1

    out = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "prompt_revision": prompt_revision(),
        "db": str(DB),
        "papers_attacked": len(targets),
        "json_failures_by_adversary": dict(json_fail),
        "per_adversary": {a: dict(per_adv[a]) for a in ADVERSARIES},
        "pairwise_record_level": pair_stats,
        "pairwise_paper_level": paper_pairs,
        "flagged_targets_by_adversary_count": dict(consensus),
        "note": "re-run experiment on reconstructed records (from stored candidates); "
                "adversary agreement measured on (paper, target) units.",
    }
    OUT_J.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    md = ["# Reviewer (adversary) agreement — re-run on the sample", "",
          f"- papers attacked: {len(targets)} (of which flagged by ≥1 adversary: "
          f"{sum(1 for v in paper_flags.values() if any(v.values()))})",
          f"- json failures: {dict(json_fail)}",
          f"- flagged (paper,target) units by adversary count: {dict(consensus)}"]
    for name, st in pair_stats.items():
        md.append(f"- {name}: both {st['both']}, Jaccard {st['jaccard']}, κ {st['kappa']} "
                  f"(raw {st['raw_agreement']})")
    for name, st in paper_pairs.items():
        md.append(f"- paper-level {name}: κ {st['kappa']} (raw {st['raw_agreement']})")
    OUT_M.write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))
    print(f"wrote {OUT_J}")


if __name__ == "__main__":
    main()
