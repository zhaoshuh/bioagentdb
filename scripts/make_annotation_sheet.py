"""生成真人标注表（CSV，Excel 可直接打开）。

输出: data/gold/annotation.csv —— 每篇论文一行，最后几列留给标注者填。
同时输出 data/gold/annotation_README.md（操作说明）。
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "data" / "gold"

COLS = ["paper_id", "title", "year", "themes", "text",
        "announces_data(Y/N)", "accessions(semicolon)", "evidence_quote", "notes"]


def main() -> None:
    rows = [json.loads(l) for l in (GOLD / "sample.jsonl").read_text(
        encoding="utf-8").splitlines() if l.strip()]
    out = GOLD / "annotation.csv"
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(COLS)
        for r in rows:
            w.writerow([
                r["id"], r.get("title", ""), r.get("year", ""),
                ";".join(r.get("themes", [])), r.get("text", ""),
                "", "", "", "",
            ])
    (GOLD / "annotation_README.md").write_text(
        "# How to annotate\n\n"
        "Open `annotation.csv` (Excel). One row per paper. Fill the last four columns:\n\n"
        "1. **announces_data(Y/N)** — does the paper announce a public dataset?\n"
        "2. **accessions(semicolon)** — list accessions exactly as printed, semicolon-separated\n"
        "   (e.g. `GSE12345;PXD000000`). Use `NONE` if announces_data=N.\n"
        "3. **evidence_quote** — copy the sentence that announces them.\n"
        "4. **notes** — anything odd (available on request, supplementary only, ambiguous).\n\n"
        "Rules: only IDs that appear in the text; do NOT look at `predictions.jsonl` while\n"
        "labeling; 'available upon request' => announces_data=Y but note it in notes.\n"
        "See also `docs/ANNOTATION_GUIDE.md`.\n",
        encoding="utf-8")
    print(f"wrote {out} ({len(rows)} rows) + annotation_README.md")


if __name__ == "__main__":
    main()
