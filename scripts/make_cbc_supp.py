"""Build the CBC supplementary-material PDF from docs/paper/final/tables.md.

Reads : docs/paper/final/tables.md      (S1-S11 + Tables notes; frozen content)
Writes: docs/paper/cbc/cbc-supplementary.html + cbc-supplementary.pdf

Manuscript-style, single column, compact. No journal stamping.

Usage: python scripts/make_cbc_supp.py [--html-only]
"""
from __future__ import annotations

import argparse
import re
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CBC = ROOT / "docs" / "paper" / "cbc"
FINAL = ROOT / "docs" / "paper" / "final"
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

CSS = """
@page { size: A4; margin: 25mm 20mm; }
body { font-family: "Times New Roman", Times, serif; font-size: 10.5pt;
       line-height: 1.45; color: #000; }
h1 { font-size: 15pt; margin: 0 0 10pt; }
h2 { font-size: 12pt; margin: 16pt 0 6pt; page-break-after: avoid; }
blockquote { font-size: 9.5pt; color: #333; border-left: 2pt solid #999;
             padding: 4pt 8pt; margin: 8pt 0; background: #f8f8f8; }
table { border-collapse: collapse; width: 100%; font-size: 9pt; line-height: 1.35;
        margin: 6pt 0; page-break-inside: avoid; }
.keep { break-inside: avoid; page-break-inside: avoid; }
th, td { border: 0.5pt solid #999; padding: 2.5pt 4pt; text-align: left; vertical-align: top; }
p { margin: 4pt 0; }
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--html-only", action="store_true")
    args = ap.parse_args()

    import markdown

    tables_v16 = CBC / "v2" / "tables-v1.6.md"
    tables_md = (tables_v16 if tables_v16.exists() else FINAL / "tables.md").read_text(encoding="utf-8")
    # journal-facing sanitisation: no maintainer notes / process words in the
    # supplementary PDF (the on-disk tables.md keeps them for the project record)
    tables_md = re.sub(r"\s*\(S1 is hand-ported;\s*see `?TABLES-NOTES\.md`?\)", "", tables_md)
    tables_md = re.sub(r"> Release: \*\*v1\.5\*\* \(October 2026\)[^\n]*\n>[^\n]*",
                       "> Release: **v1.5** (October 2026).", tables_md)
    # demote the file's own H1 (the page keeps a single <h1> masthead)
    tables_md = re.sub(r"^# ", "## ", tables_md, count=1, flags=re.M)
    # rendering-layer naming consistency with the manuscript
    tables_md = tables_md.replace("GWASCatalog", "GWAS Catalog")
    tables_md = tables_md.replace("CELLxGENE", "CZ CELLxGENE")
    tables_md = tables_md.replace("SingleCellPortal", "Single Cell Portal")
    tables_md = tables_md.replace("ProtocolsIO", "protocols.io")
    body = markdown.markdown(tables_md, extensions=["tables", "fenced_code"])
    # keep each table's heading (+lead note) and body together on one page where it
    # fits; tempered so a heading block never crosses into the next section's <h2>
    body = re.sub(
        r"(<h2>[^<]*</h2>(?:(?!<h2>).)*?<table>.*?</table>)",
        r'<section class="keep">\1</section>', body, flags=re.S)
    html = (f'<!DOCTYPE html><html><head><meta charset="utf-8">'
            f'<title>BioAgentDB - Supplementary material</title>'
            f'<style>{CSS}</style></head><body>'
            f'<h1>BioAgentDB - Supplementary material</h1>'
            f'{body}</body></html>')
    # layout gate (TM-13): single masthead + one keep-block per supplementary table
    n_h1 = html.count("<h1>")
    n_keep = html.count('class="keep"')
    if n_h1 != 1 or n_keep != 11:
        raise SystemExit(f"supplementary layout gate failed: h1={n_h1}, keep={n_keep}")
    print(f"layout gate ok: h1={n_h1}, keep={n_keep}")
    out_html = CBC / "cbc-supplementary.html"
    out_html.write_text(html, encoding="utf-8")
    print(f"wrote {out_html.relative_to(ROOT)}")
    if args.html_only:
        return

    out_pdf = CBC / "cbc-supplementary.pdf"
    staged = CBC / "cbc-supplementary.pdf.staged"
    staged.unlink(missing_ok=True)
    r = subprocess.run(
        [EDGE, "--headless", "--disable-gpu", "--no-pdf-header-footer",
         f"--print-to-pdf={staged}", out_html.as_uri()],
        capture_output=True, timeout=300)
    size = staged.stat().st_size if staged.exists() else 0
    print(f"print-to-pdf rc={r.returncode} | size={size}")
    for attempt in range(12):
        try:
            staged.replace(out_pdf)
            break
        except PermissionError:
            if attempt == 11:
                print(f"WARNING: {out_pdf.name} locked; build kept at {staged.name}")
            else:
                time.sleep(5)
    print(f"wrote {out_pdf.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
