"""FN 分类：金标准里被管线漏掉的登记号，按"是否出现在论文文本 / 仓库"归类。

用法: python scripts/fn_triage.py [--gold data/gold]
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

REPO = [
    ("GEO", re.compile(r"^gse\d+$")), ("PRIDE", re.compile(r"^pxd\d+$")),
    ("SRA/BioProject", re.compile(r"^(srp|prjna)\d+$")), ("ENA", re.compile(r"^(erp|prjeb)\d+$")),
    ("dbGaP", re.compile(r"^phs\d+")), ("EGA", re.compile(r"^ega[sdn]\d+")),
    ("ArrayExpress", re.compile(r"^e-[a-z]{4}-\d+$")), ("Zenodo", re.compile(r"zenodo")),
    ("Figshare", re.compile(r"figshare")), ("Dryad", re.compile(r"dryad")),
    ("jPOST", re.compile(r"^jpst\d+$")), ("GSA", re.compile(r"^cra\d+$")),
    ("MetaboLights", re.compile(r"^mtbls\d+$")), ("MassIVE", re.compile(r"^msv\d+$")),
]


def canon(a: str) -> str:
    a = (a or "").strip().lower()
    return re.sub(r"\.v\d+\.p\d+$", "", a)


def repo_of(acc: str) -> str:
    for name, pat in REPO:
        if pat.search(acc):
            return name
    return "other"


def jl(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", default=str(root / "data" / "gold"))
    args = ap.parse_args()
    g = Path(args.gold)

    sample = {r["id"]: (r.get("text") or "").lower() for r in jl(g / "sample.jsonl")}
    gold = {r["paper_id"]: r for r in jl(g / "gold.jsonl")}
    preds = {}
    for r in jl(g / "predictions.jsonl"):
        accs = set()
        for d in r.get("datasets", []):
            for l in d.get("links", []):
                if l.get("accession"):
                    accs.add(canon(l["accession"]))
        preds[r["paper_id"]] = accs

    in_text = Counter()
    by_repo = Counter()
    examples = {"in_text": [], "not_in_text": []}
    total = 0
    for pid, grec in gold.items():
        pred = preds.get(pid, set())
        text = sample.get(pid, "")
        for acc in grec.get("accessions", []):
            c = canon(acc)
            if c in pred:
                continue
            total += 1
            by_repo[repo_of(c)] += 1
            if c and c in text:
                in_text["in_text"] += 1
                if len(examples["in_text"]) < 10:
                    examples["in_text"].append(f"{acc} @ {pid}")
            else:
                in_text["not_in_text"] += 1
                if len(examples["not_in_text"]) < 10:
                    examples["not_in_text"].append(f"{acc} @ {pid}")

    out = {"gold_accessions_missed": total, "in_text": in_text["in_text"],
           "not_in_text": in_text["not_in_text"],
           "by_repo": dict(by_repo), "examples": examples}
    (g / "fn-triage.json").write_text(json.dumps(out, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
    md = ["# FN triage (human gold misses)", "",
          f"- total missed gold accessions: **{total}**",
          f"- present in paper text (true pipeline misses): **{in_text['in_text']}**",
          f"- NOT in paper text (annotator external / truncation): **{in_text['not_in_text']}**",
          "", "## By repository", ""]
    md += [f"- {k}: {v}" for k, v in by_repo.most_common()]
    md += ["", "## Examples present in text (real misses)"]
    md += [f"- {x}" for x in examples["in_text"]]
    md += ["", "## Examples not in text"]
    md += [f"- {x}" for x in examples["not_in_text"]]
    (g / "fn-triage.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md[:8]))


if __name__ == "__main__":
    main()
