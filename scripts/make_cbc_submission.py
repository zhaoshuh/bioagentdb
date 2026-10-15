"""Assemble the CBC submission package deterministically (C1 fix batch, L-0334).

Outputs (all under docs/paper/cbc/submission/):
- BioAgentDB-manuscript.pdf        <- cbc-paper.pdf
- BioAgentDB-supplementary.pdf      <- cbc-supplementary.pdf
- BioAgentDB-highlights.txt         <- cbc-highlights.md
- BioAgentDB-cover-letter.md        <- cover-letter.md
- figures/Figure1-9, FigureS1-S4, GraphicalAbstract
    * vector panels 1-5, 9, S1, GA copied from docs/paper/final/figures/
    * rasters 6-8, S3, S4 copied from docs/paper/cbc/figures-hires/ (FigureN.png)
    * FigureS2.pdf REGENERATED from panelS2A-D with the CBC render-layer label
      fix (GWASCatalog -> GWAS Catalog); master exports stay untouched.
- source-captures/<8 windows> from docs/paper/final/evidence/ (renamed)
    * fulfils the "full-resolution source captures ... as Supplementary Data"
      promise in section "Supplementary data" / Figure 6 caption.
- MANIFEST.tsv (file \t size \t sha256_16) over everything above + CHECKLIST.md

Usage: python scripts/make_cbc_submission.py          # rebuild package
       python scripts/make_cbc_submission.py --check   # verify MANIFEST only
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CBC = ROOT / "docs" / "paper" / "cbc"
SUB = CBC / "submission"
FIN = ROOT / "docs" / "paper" / "final" / "figures"
EVID = ROOT / "docs" / "paper" / "final" / "evidence"
HIRES = CBC / "figures-hires"
CAPS = ROOT / "docs" / "paper" / "final" / "captions.md"
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

TOP = [("cbc-paper.pdf", "BioAgentDB-manuscript.pdf", CBC),
       ("cbc-supplementary.pdf", "BioAgentDB-supplementary.pdf", CBC),
       ("cbc-highlights.md", "BioAgentDB-highlights.txt", CBC),
       ("cover-letter.md", "BioAgentDB-cover-letter.md", CBC)]

VEC = [("figure1.pdf", "Figure1.pdf"), ("figure2.pdf", "Figure2.pdf"),
       ("figure3.pdf", "Figure3.pdf"), ("figure4.pdf", "Figure4.pdf"),
       ("figure5.pdf", "Figure5.pdf"), ("figure9.pdf", "Figure9.pdf"),
       ("figureS1.pdf", "FigureS1.pdf"), ("graphical-abstract.pdf", "GraphicalAbstract.pdf")]

RAS = [("figure6-ledger.png", "Figure6.png"), ("figure7-record.png", "Figure7.png"),
       ("figure8-ask.png", "Figure8.png"),
       ("supplementary-figure-s3.png", "FigureS3.png"),
       ("supplementary-figure-s4.png", "FigureS4.png")]

CAPTURES = [("v4-ledger.png", "figure6-source-window.png"),
            ("v4-record.png", "figure7-source-window.png"),
            ("v4-ask.png", "figure8-source-window.png"),
            ("v4-export.png", "figureS3-source-window.png"),
            ("v4-ming-start.png", "figureS4-a-source-window.png"),
            ("v4-mining-live.png", "figureS4-b-source-window.png"),
            ("v4-mining-topics.png", "figureS4-c-source-window.png"),
            ("v4-ledger81.png", "figureS4-d-source-window.png")]


def build_figure_s2(out_pdf: Path) -> None:
    """Regenerate Supplementary Figure S2 as a standalone PDF with the panel
    label aligned to the manuscript spelling (CBC render layer only)."""
    panels = []
    for c in "ABCD":
        svg = (FIN / f"panelS2{c}.svg").read_text(encoding="utf-8")
        panels.append(svg.replace("GWASCatalog", "GWAS Catalog"))
    caps = CAPS.read_text(encoding="utf-8")
    m = re.search(r"^##\s+Supplementary Figure\s+S2\s*(?:·\s*([^\n]+))?\s*\n(.*?)(?=^##\s|\Z)",
                  caps, re.S | re.M)
    title = (m.group(1) or "").strip() if m else ""
    body = re.sub(r"\s+", " ", m.group(2).replace("**", "")).strip() if m else ""
    cap = ((f"{title}. " if title else "") + body).replace("GWASCatalog", "GWAS Catalog")
    if not cap or len(cap) > 800 or "## " in cap or "**" in cap:
        sys.exit(f"FigureS2 caption extraction looks wrong (len={len(cap)})")
    cells = "".join(f"<div class='cell'>{p}</div>" for p in panels)
    doc = ("<!doctype html><html><head><meta charset='utf-8'><style>"
           "@page{size:A4;margin:14mm}html,body{margin:0}"
           ".g{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6mm;align-items:start}"
           ".cell svg{width:100%;max-width:100%;height:auto;display:block}"
           ".cap{font:8.5pt/1.35 'Times New Roman',Times,serif;text-align:justify;"
           "margin-top:6mm;padding-right:1mm}</style></head><body>"
           "<div class='g'>" + cells + "</div>"
           f"<div class='cap'><b>Supplementary Figure S2.</b> {cap}</div></body></html>")
    hp = Path(os.environ.get("TEMP", ".")) / "cbc-figureS2-export.htm"
    hp.write_text(doc, encoding="utf-8")
    out_pdf.unlink(missing_ok=True)
    subprocess.run([EDGE, "--headless", "--disable-gpu", "--no-pdf-header-footer",
                    f"--print-to-pdf={out_pdf}", hp.as_uri()],
                   capture_output=True, timeout=240)
    hp.unlink(missing_ok=True)
    if not out_pdf.exists() or out_pdf.stat().st_size == 0:
        sys.exit("FigureS2.pdf export failed")
    print(f"  wrote figures/FigureS2.pdf (regenerated, {out_pdf.stat().st_size} B)")


def _write_bytes(src: Path, dst: Path) -> None:
    data = src.read_bytes()
    for attempt in range(12):
        try:
            dst.write_bytes(data)
            return
        except PermissionError:
            if attempt == 11:
                raise
            time.sleep(5)


def manifest() -> list[str]:
    rows = []
    for f in sorted(SUB.rglob("*")):
        if f.is_file() and f.name != "MANIFEST.tsv":
            h = hashlib.sha256(f.read_bytes()).hexdigest()[:16]
            rows.append(f"{f.relative_to(SUB).as_posix()}\t{f.stat().st_size}\t{h}")
    (SUB / "MANIFEST.tsv").write_text("file\tsize\tsha256_16\n" + "\n".join(rows) + "\n",
                                      encoding="utf-8")
    return rows


def check() -> None:
    rows = (SUB / "MANIFEST.tsv").read_text(encoding="utf-8").splitlines()[1:]
    listed = set()
    bad = 0
    for r in rows:
        name, size, h = r.split("\t")
        listed.add(name)
        p = SUB / name
        if not p.exists():
            print(f"  MISSING {name}"); bad += 1; continue
        if p.stat().st_size != int(size) or hashlib.sha256(p.read_bytes()).hexdigest()[:16] != h:
            print(f"  HASH-MISMATCH {name}"); bad += 1
    extras = [f.relative_to(SUB).as_posix() for f in sorted(SUB.rglob("*"))
              if f.is_file() and f.name != "MANIFEST.tsv"
              and f.relative_to(SUB).as_posix() not in listed]
    for e in extras:
        print(f"  EXTRA {e}"); bad += 1
    bad_listed = bad - len(extras)
    print(f"MANIFEST check: {len(rows) - bad_listed}/{len(rows)} listed files ok"
          + (f"; {len(extras)} unexpected extra file(s)" if extras else ""))
    sys.exit(1 if bad else 0)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check:
        check()
        return

    (SUB / "figures").mkdir(parents=True, exist_ok=True)
    (SUB / "source-captures").mkdir(parents=True, exist_ok=True)

    for sname, dname, base in TOP:
        src = base / sname
        if not src.exists():
            sys.exit(f"missing source: {src}")
        _write_bytes(src, SUB / dname)
        print(f"  wrote {dname}")
    for sname, dname in VEC:
        _write_bytes(FIN / sname, SUB / "figures" / dname)
    for sname, dname in RAS:
        _write_bytes(HIRES / sname, SUB / "figures" / dname)
    for sname, dname in CAPTURES:
        _write_bytes(EVID / sname, SUB / "source-captures" / dname)
    print(f"  wrote figures/ ({len(VEC) + len(RAS) + 1} files) and source-captures/ ({len(CAPTURES)} files)")

    build_figure_s2(SUB / "figures" / "FigureS2.pdf")

    rows = manifest()
    print(f"submission assembled: {len(rows)} files")
    for r in rows:
        print("  ", r.split("\t")[0])


if __name__ == "__main__":
    main()
