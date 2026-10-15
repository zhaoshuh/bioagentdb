"""独立参照抽取：仅正则从论文文本抽登记号（不经 LLM 管线）。

作为"非循环"参照，用于 provisional 评测；真人标注到位后替换/对照。

用法: python scripts/rule_reference.py --sample data/gold/sample.jsonl --out data/gold/reference.jsonl
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

# (repository, pattern, group)  自包含、独立于 app 的抽取器
PATTERNS: list[tuple[str, re.Pattern]] = [
    ("GEO", re.compile(r"\b(GSE\d{3,7})\b", re.I), 1),
    ("GEO", re.compile(r"acc=(GSE\d+)", re.I), 1),
    ("SRA/BioProject", re.compile(r"\b(SRP\d{5,8}|PRJNA\d{4,9})\b", re.I), 1),
    ("ENA", re.compile(r"\b(ERP\d{5,8}|PRJEB\d{4,9})\b", re.I), 1),
    ("PRIDE", re.compile(r"\b(PXD\d{5,7})\b", re.I), 1),
    ("MassIVE", re.compile(r"\b(MSV\d{6,9})\b", re.I), 1),
    ("MetaboLights", re.compile(r"\b(MTBLS\d{3,6})\b", re.I), 1),
    ("dbGaP", re.compile(r"\b(phs\d{6}(?:\.v\d+\.p\d+)?)\b"), 1),
    ("EGA", re.compile(r"\b(EGA[SDN]\d{8,14})\b", re.I), 1),
    ("ArrayExpress", re.compile(r"\b(E-[A-Z]{4}-\d{3,5})\b", re.I), 1),
    ("Zenodo", re.compile(r"10\.5281/zenodo\.(\d{4,9})", re.I), 0),
    ("Figshare", re.compile(r"10\.6084/m9\.figshare\.(\d{5,9})", re.I), 0),
    ("jPOST", re.compile(r"\b(JPST\d{6})\b", re.I), 1),
    ("GSA", re.compile(r"\b(CRA\d{6})\b", re.I), 1),
    ("Dryad", re.compile(r"10\.5061/dryad\.([a-z0-9]+)", re.I), 0),
]

SENT = re.compile(r"(?<=[.!?])\s+")


def _norm(repo: str, acc: str) -> str:
    a = acc.strip()
    return a.lower() if a.lower().startswith("phs") else a.upper()


def _sentence(text: str, idx: int) -> str:
    start = max(text.rfind(". ", 0, idx), text.rfind("\n", 0, idx))
    start = start + 1 if start >= 0 else 0
    end = len(text)
    for sep in (". ", "! ", "? ", "\n"):
        j = text.find(sep, idx)
        if j >= 0:
            end = min(end, j + 1)
    return re.sub(r"\s+", " ", text[start:end]).strip()[:400]


def extract(text: str) -> list[dict]:
    out: dict[str, dict] = {}
    for repo, pat, grp in PATTERNS:
        for m in pat.finditer(text or ""):
            raw = m.group(grp)
            acc = _norm(repo, raw)
            if acc and acc not in out:
                out[acc] = {"repository": repo, "accession": acc,
                            "quote": _sentence(text, m.start())}
    return list(out.values())


def main() -> None:
    ap = argparse.ArgumentParser()
    root = Path(__file__).resolve().parents[1]
    ap.add_argument("--sample", default=str(root / "data" / "gold" / "sample.jsonl"))
    ap.add_argument("--out", default=str(root / "data" / "gold" / "reference.jsonl"))
    args = ap.parse_args()
    rows = [json.loads(l) for l in Path(args.sample).read_text(encoding="utf-8").splitlines() if l]
    out = []
    for r in rows:
        recs = extract(r.get("text") or "")
        out.append({"paper_id": r["id"], "accessions": [x["accession"] for x in recs],
                    "records": recs})
    Path(args.out).write_text(
        "\n".join(json.dumps(x, ensure_ascii=False) for x in out) + "\n", encoding="utf-8")
    n_rec = sum(len(x["accessions"]) for x in out)
    print(f"reference: {len(out)} papers, {n_rec} accessions -> {args.out}")


if __name__ == "__main__":
    main()
