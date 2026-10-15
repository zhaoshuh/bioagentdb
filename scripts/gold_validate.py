"""W1 · 确定性金标校验（L-0288）：每条 gold 登记号是否**字面/变体**出现在对应论文文本中。

判级：
  exact     —— 规范化后大小写不敏感、直接包含；
  variant   —— 去空格/连字符等变体后包含；
  missing   —— 两者都不中（逐条列出，供人工/仲裁复核）。
输出：data/eval/gold-validation.json + .md
用法: python scripts/gold_validate.py
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
GOLD = ROOT / "data" / "gold" / "gold.jsonl"
SAMPLE = ROOT / "data" / "gold" / "sample.jsonl"
OUT_J = ROOT / "data" / "eval" / "gold-validation.json"
OUT_M = ROOT / "data" / "eval" / "gold-validation.md"


def _squash(s: str) -> str:
    return re.sub(r"[\s\-_/.:]+", "", (s or "").upper())


def main() -> None:
    texts = {}
    for l in SAMPLE.read_text(encoding="utf-8").splitlines():
        if l.strip():
            r = json.loads(l)
            texts[r["id"]] = r.get("text", "") or ""
    rows = []
    for l in GOLD.read_text(encoding="utf-8").splitlines():
        if not l.strip():
            continue
        g = json.loads(l)
        text = texts.get(g["paper_id"], "")
        up = text.upper()
        sq = _squash(text)
        for acc in g.get("accessions", []):
            a = (acc or "").strip()
            if not a:
                continue
            if a.upper() in up:
                status = "exact"
            elif _squash(a) and _squash(a) in sq:
                status = "variant"
            else:
                status = "missing"
            rows.append({"paper_id": g["paper_id"], "accession": a, "status": status})

    counts = Counter(r["status"] for r in rows)
    missing = [r for r in rows if r["status"] == "missing"]
    out = {"total": len(rows), "counts": dict(counts),
           "missing": missing[:50],
           "note": "Exact = case-insensitive containment in the paper text as stored; "
                   "variant = containment after squashing whitespace/dashes/separators."}
    OUT_J.parent.mkdir(parents=True, exist_ok=True)
    OUT_J.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    md = ["# Deterministic gold validation", "",
          f"- accessions: {len(rows)}",
          f"- exact: {counts.get('exact', 0)} · variant: {counts.get('variant', 0)} · "
          f"missing: {counts.get('missing', 0)}"]
    for m in missing[:20]:
        md.append(f"- MISSING: {m['paper_id']} {m['accession']}")
    OUT_M.write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))
    print(f"wrote {OUT_J}")


if __name__ == "__main__":
    main()
