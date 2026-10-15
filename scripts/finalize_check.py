"""定稿自动化检查（退出标准的一部分）。

检查：产物齐全 / 数字与溯源一致 / 引用编号有效 / 图表被引用 / 快照存在。
用法: python scripts/finalize_check.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAPERS = [ROOT / "docs" / "paper" / "final" / "paper.md"]
NUM_SRC = ROOT / "docs" / "paper" / "number-sources.md"
REFS = ROOT / "docs" / "paper" / "final" / "references.md"

REQUIRED = [
    "docs/paper/final/paper.md", "docs/paper/final/paper.pdf",
    "docs/paper/final/references.md", "docs/paper/refs/verified.json",
    "docs/paper/number-sources.md", "docs/paper/final/tables.md",
    "docs/paper/final/captions.md", "docs/paper/final/SUBMISSION-CHECKLIST.md",
    "docs/paper/final/SIGNOFF.md",
    "docs/paper/figures/figures.html", "docs/paper/figures/figures.png",
    "docs/paper/final/figures/figures.pdf",
    "docs/paper/final/figures/figureS1.pdf",
    "docs/paper/final/figures/figureS2.pdf",
    "docs/paper/final/figures/figureS3.pdf",
    "docs/paper/final/figures/figureS4.pdf",
    "docs/paper/final/figures/figure1.pdf",
    "docs/paper/final/figures/figure2.pdf",
    "docs/paper/final/figures/figure3.pdf",
    "docs/paper/final/figures/figure4.pdf",
    "docs/paper/final/figures/figure5.pdf",
    "docs/paper/final/figures/figure6.pdf",
    "docs/paper/final/figures/figure7.pdf",
    "docs/paper/final/figures/graphical-abstract.pdf",
    "docs/paper/final/figures/figure8.pdf",
    "docs/paper/final/figures/figure9.pdf",
    "docs/paper/final/figures/figure6-ledger.png",
    "docs/paper/final/figures/figure7-record.png",
    "docs/paper/final/figures/figure8-ask.png",
    "docs/paper/final/figures/supplementary-figure-s3.png",
    "docs/paper/final/figures/supplementary-figure-s4.png",
    "docs/versions/v1.5-stats/resource-stats.json",
    "docs/versions/v1.5-proteinopathies.md",
    "docs/paper/final/evidence/rejection-audit.csv",
]

# 关键数字（v1.5 口径；L-0314）
KEY_NUMBERS = ["21,014", "19,390", "1,307", "2,289", "2,204", "0.990", "3,555", "3,519",
               "3,618", "4,980", "2,296",
               "0.961", "0.928", "0.944", "0.967", "0.904", "0.934",
               "992", "49", "855", "536", "61",
               "205", "163", "0.988", "0.911", "0.927", "0.658", "0.645"]


def main() -> None:
    findings = []

    def add(name, ok, detail=""):
        findings.append({"check": name, "ok": bool(ok), "detail": str(detail)[:200]})

    for rel in REQUIRED:
        p = ROOT / rel
        add(f"artifact exists: {rel}", p.exists(), "" if p.exists() else "MISSING")

    text = "\n".join(p.read_text(encoding="utf-8") for p in PAPERS if p.exists())

    src = NUM_SRC.read_text(encoding="utf-8") if NUM_SRC.exists() else ""
    missing_nums = [n for n in KEY_NUMBERS if n not in text]
    add("key numbers present in papers", not missing_nums, missing_nums)
    untraced = [n for n in KEY_NUMBERS if n not in src]
    add("key numbers traced in number-sources", not untraced, untraced)
    ttext2 = (ROOT / "docs" / "paper" / "final" / "tables.md").read_text(encoding="utf-8")
    add("tables.md carries v1.5 totals", ("2,204" in ttext2) and ("0.967" in ttext2), "")
    add("S5 note consistent with its rows", "coincide" not in ttext2 and
        "97.5% vs 96.7%" in ttext2 and "96.6%" not in ttext2, "")
    add("S7 rejected count shipped", "| rejected | 85 |" in ttext2, "")
    add("no stale T1 counts", "1,187/1,189" not in text, "")
    add("no stale refusal metrics", "78 correct" not in text and "88 rows" not in text, "")
    add("no stale DOI-form count", "27 DOI-form" not in text, "")

    max_ref = 61
    if REFS.exists():
        nums = {int(m) for m in re.findall(r"^\s*(\d+)\.\s", REFS.read_text(encoding="utf-8"), re.M)}
        max_ref = max(nums) if nums else 0
    used = set()
    for m in re.finditer(r"[\[(]([0-9,\s\u2013\-]+)[\])]", text):
        for part in re.split(r",", m.group(1)):
            part = part.strip()
            if re.fullmatch(r"\d+", part):
                used.add(int(part))
            elif re.fullmatch(r"\d+\s*[\u2013-]\s*\d+", part):
                a, b = re.split(r"[\u2013-]", part)
                used.update(range(int(a), int(b) + 1))
    bad = sorted(n for n in used if n < 1 or n > max_ref)
    add("citations valid", not bad, f"out of range: {bad}" if bad else f"used 1–{max_ref}")

    tables_p = ROOT / "docs" / "paper" / "final" / "tables.md"
    ttext = tables_p.read_text(encoding="utf-8") if tables_p.exists() else ""
    leaks = sorted(set(re.findall(r"L-\d{4}|(?:scripts|docs|data|app)/", ttext)))
    add("tables.md free of internal identifiers", not leaks, leaks)   # L-0263/L-0264（P4 表组守卫）

    t2 = {}
    sec2 = re.search(r"\*\*Table 2\.(.*?)\*\*Table 3\.", text, re.S)
    if sec2:
        for m in re.finditer(r"^\|\s*(\d+)\s*\|\s*[^|]+\|\s*[^|]+\|\s*(\d+)\s*\|\s*(\d+)\s*\|",
                             sec2.group(1), re.M):
            t2[int(m.group(1))] = (int(m.group(2)), int(m.group(3)))
    s1 = {}
    sec1 = re.search(r"## Supplementary Table S1 .*?\n(.*?)## Supplementary Table S2", ttext, re.S)
    if sec1:
        for m in re.finditer(r"^\|\s*(\d+)\s*\|\s*[a-z0-9-]+\s*\|\s*[^|]+\|\s*(\d+)\s*\|\s*(\d+)\s*\|",
                             sec1.group(1), re.M):
            s1[int(m.group(1))] = (int(m.group(2)), int(m.group(3)))
    _diff = {k: (t2.get(k), s1.get(k)) for k in set(t2) | set(s1) if t2.get(k) != s1.get(k)}
    add("Table 2 values match Supplementary Table S1", bool(t2) and t2 == s1,
        f"t2={len(t2)} s1={len(s1)} diff={_diff}")   # L-0282：TABLES-NOTES 声称的等式闸门

    fig_refs = sorted({int(m) for m in re.findall(r"Fig(?:ure)?\s*(\d+)", text)})
    panels = sorted((ROOT / "docs" / "paper" / "figures").glob("panel*.svg"))
    add("figures referenced", len(fig_refs) >= 7, f"referenced: {fig_refs}")
    add("figure panels rendered", len(panels) >= 25, f"panels: {len(panels)}")

    ok = sum(1 for f in findings if f["ok"])
    out = ROOT / "docs" / "paper" / "final" / "reviews"
    out.mkdir(parents=True, exist_ok=True)
    (out / "finalize-check.json").write_text(json.dumps(findings, ensure_ascii=False, indent=2),
                                             encoding="utf-8")
    md = ["# Finalization automated check", "",
          f"- passed **{ok}/{len(findings)}**", ""]
    for f in findings:
        md.append(f"- {'PASS' if f['ok'] else '**FAIL**'} · {f['check']}"
                  + (f"  → {f['detail']}" if not f["ok"] else ""))
    (out / "finalize-check.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md[:14]))
    print(f"\npassed {ok}/{len(findings)}")


if __name__ == "__main__":
    main()
