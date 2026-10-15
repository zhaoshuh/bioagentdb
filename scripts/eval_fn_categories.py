"""W4 · 66 个漏抽（FN）细分（L-0283）：逐登记号定位可修复面。

分为四类（互斥，按优先级）：
 1. `extended_vocab`   —— 基础词表未找到、扩展词表（扫描路径）能找到 → 扫描可修复；
 2. `base_hit_stage_loss` —— 基础词表在文本中能直接找到、但未进预测 → 管线后续阶段丢失；
 3. `pattern_exists_variant` —— 登记号本体匹配已知仓库模式、但两种正则都没在文本命中
    → 形态/边界变体（大小写、换行、粘连等）；
 4. `no_pattern` —— 登记号本体不匹配任何已知模式 → 词表缺口（如裸数字 ID、非常规形态）。
输出：data/eval/fn-categories.json + .md
用法: python scripts/eval_fn_categories.py
环境: BIOAGENTDB_EVAL_DB=<db> 切换源库（默认 _v15；E5/L-0306 传 _v15）。
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
GOLD = ROOT / "data" / "gold" / "gold.jsonl"
PRED = ROOT / "data" / "gold" / "predictions.jsonl"
SAMPLE = ROOT / "data" / "gold" / "sample.jsonl"
OUT_J = ROOT / "data" / "eval" / "fn-categories.json"
OUT_M = ROOT / "data" / "eval" / "fn-categories.md"
import os  # noqa: E402

EVAL_DB = Path(os.environ.get("BIOAGENTDB_EVAL_DB")
               or ROOT / "data" / "releases" / "_v15" / "bioagentdb.sqlite3")


def norm(acc: str) -> str:
    from app.core.vocab import canon_accession
    return canon_accession(acc)


def main() -> None:
    import re
    from app.harvest.extract import _ACCESSION_PATTERNS, _extended_regex, find_identifiers

    gold = {json.loads(l)["paper_id"]: json.loads(l)
            for l in GOLD.read_text(encoding="utf-8").splitlines() if l.strip()}
    pred = {}
    for l in PRED.read_text(encoding="utf-8").splitlines():
        if l.strip():
            r = json.loads(l)
            accs = set()
            for d in r.get("datasets", []):
                for lnk in d.get("links", []):
                    a = norm(lnk.get("accession", ""))
                    if a:
                        accs.add(a)
            pred[r["paper_id"]] = accs
    texts = {}
    for l in SAMPLE.read_text(encoding="utf-8").splitlines():
        if l.strip():
            r = json.loads(l)
            texts[r["id"]] = r.get("text", "")

    ext = _extended_regex()

    import sqlite3
    cdb = sqlite3.connect(f"file:{EVAL_DB}?mode=ro", uri=True)
    cand_by_paper: dict[str, dict[str, str]] = {}
    for pid2, acc2, verdict2 in cdb.execute(
            "SELECT paper_id, accession, verdict FROM candidates"):
        cand_by_paper.setdefault(pid2, {})[norm(acc2 or "")] = verdict2 or ""
    cdb.close()

    def repo_of(acc: str) -> str | None:
        for repo, pat in _ACCESSION_PATTERNS:
            if pat.fullmatch(acc):
                return repo
        return None

    rows = []
    for pid, g in gold.items():
        text = texts.get(pid, "")
        found_base = {norm(i["acc"]) for i in find_identifiers(text)} if text else set()
        found_ext = set()
        if text:
            try:
                for m in ext.finditer(text):
                    s = m.group(0)
                    found_ext.add(norm(s))
            except Exception:  # noqa: BLE001
                pass
        for acc in g.get("accessions", []):
            a = norm(acc)
            if not a or a in pred.get(pid, set()):
                continue
            repo = repo_of(a)
            if a in found_ext and a not in found_base:
                cat = "extended_vocab"
            elif a in found_base:
                cv = cand_by_paper.get(pid, {}).get(a, None)
                if cv is None:
                    cat = "base_hit_no_candidate_row"
                elif cv == "rejected":
                    cat = "base_hit_candidate_rejected"
                else:
                    cat = "base_hit_candidate_other"
            elif a in found_ext:
                cat = "extended_vocab"
            elif repo:
                cat = "pattern_exists_variant"
            else:
                cat = "no_pattern"
            rows.append({"paper_id": pid, "accession": a, "repo": repo or "(unmatched)",
                         "category": cat})

    counts = Counter(r["category"] for r in rows)
    by_repo = Counter(r["repo"] for r in rows if r["category"] == "no_pattern")
    out = {
        "fn_total": len(rows),
        "categories": dict(counts),
        "no_pattern_repos": dict(by_repo.most_common(12)),
        "examples": {c: [r for r in rows if r["category"] == c][:8] for c in counts},
    }
    OUT_J.parent.mkdir(parents=True, exist_ok=True)
    OUT_J.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    md = ["# FN sub-categorisation (66 in-text misses)", "",
          f"- total: {len(rows)}",
          f"- categories: {json.dumps(dict(counts), ensure_ascii=False)}",
          f"- no_pattern repo mix: {json.dumps(dict(by_repo.most_common(12)), ensure_ascii=False)}"]
    OUT_M.write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))
    print(f"wrote {OUT_J}")


if __name__ == "__main__":
    main()
