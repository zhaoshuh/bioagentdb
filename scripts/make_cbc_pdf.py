"""Build the CBC submission PDF from docs/paper/cbc/cbc-paper.md.

Manuscript-style rendering (Elsevier "Your Paper Your Way"):
  A4, 30 mm margins, Times New Roman 12 pt, double-spaced, left-aligned,
  figures embedded at the same anchors as the master build, no journal stamping.

Figure sources: panel SVGs from docs/paper/figures (unchanged, vector); the raster
captures (Figures 6-8, Supplementary S3/S4) prefer docs/paper/cbc/figures-hires/
(>=2244 px, >=300 dpi) and fall back to docs/paper/final/figures.

Reads : docs/paper/cbc/cbc-paper.md  (master paper.md + overrides; frozen numbers)
        docs/paper/final/captions.md, docs/paper/final/references.md
Writes: docs/paper/cbc/cbc-paper.html + cbc-paper.pdf

Usage: python scripts/make_cbc_pdf.py [--html-only]
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
FIGDIR = ROOT / "docs" / "paper" / "figures"
HIRES = CBC / "figures-hires"
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

CSS = """
@page { size: A4; margin: 30mm; }
body { font-family: "Times New Roman", Times, serif; font-size: 12pt;
       line-height: 2.0; color: #000; }
h1 { font-size: 16pt; line-height: 1.3; margin: 0 0 12pt; }
h2 { font-size: 13pt; line-height: 1.4; margin: 18pt 0 8pt; page-break-after: avoid; }
h3 { font-size: 12pt; line-height: 1.4; margin: 14pt 0 6pt; page-break-after: avoid; }
p { margin: 0 0 10pt; text-align: left; }
sup { font-size: 8pt; }
blockquote { font-size: 10pt; line-height: 1.4; color: #222; border-left: 2pt solid #999;
             padding: 4pt 8pt; margin: 10pt 0; background: #f8f8f8; }
table { border-collapse: collapse; width: 100%; font-size: 9pt; line-height: 1.4;
        margin: 8pt 0; page-break-inside: avoid; }
th, td { border: 0.5pt solid #999; padding: 2.5pt 4pt; text-align: left; vertical-align: top; }
code { font-family: Consolas, "Courier New", monospace; font-size: 10pt; }
pre { font-family: Consolas, "Courier New", monospace; font-size: 9pt; line-height: 1.35;
      background: #f7f7f7; border: 0.5pt solid #ccc; padding: 6pt; margin: 8pt 0;
      white-space: pre-wrap; break-inside: avoid; }
.figblock { margin: 6pt 0 10pt; }
.figblock.keep { break-inside: avoid; }
.figcap { font-size: 10pt; line-height: 1.5; margin-top: 5pt; text-align: left;
          break-inside: avoid; }
figure { margin: 0; }
.figgrid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 4pt; }
.figblock svg, .figblock img { display: block; width: 100%; max-width: 100%; height: auto; }
.figgrid svg { width: 100%; max-width: 100%; height: auto; border: 0.5pt solid #ddd; }
.ga svg { width: 100%; height: auto; }
.figtag { font-size: 11pt; font-weight: bold; margin: 12pt 0 4pt; }
.tabcap { font-size: 10pt; break-after: avoid; break-inside: avoid; }
ol.references { font-size: 10pt; line-height: 1.5; padding-left: 18pt; }
ol.references li { margin-bottom: 3pt; text-align: left; }
"""


def build_captions() -> tuple[dict, dict]:
    caps_v16 = CBC / "v2" / "captions-v1.6.md"
    text = (caps_v16 if caps_v16.exists() else FINAL / "captions.md").read_text(encoding="utf-8")
    caps: dict = {}
    supp: dict = {}
    for m in re.finditer(r"^##\s+(.+?)\s*$\n(.*?)(?=^##\s|\Z)", text, re.S | re.M):
        head = m.group(1).strip()
        body = re.sub(r"\s+", " ", m.group(2).replace("**", "")).strip()
        title = head.split("·", 1)[1].strip() if "·" in head else ""
        merged = f"{title}. {body}".strip() if (title and body) else (body or title)
        fm = re.match(r"Figure\s+(\d)\b", head)
        fs = re.match(r"Supplementary Figure\s+S(\d)\b", head)
        if fm and merged:
            caps[int(fm.group(1))] = merged
        elif fs and merged:
            supp[int(fs.group(1))] = merged
    # L-0344/L-0346: render-layer correction of the retired provenance wording
    # (frozen master captions.md cannot be edited; Figure 9's title carries it)
    fix = lambda s: s.replace("single annotator", "five annotators, 50 papers each")
    return ({k: fix(v) for k, v in caps.items()},
            {k: fix(v) for k, v in supp.items()})


def build_references_block() -> str:
    text = (FINAL / "references.md").read_text(encoding="utf-8")
    entries = []
    for line in text.splitlines():
        m = re.match(r"^(\d+)\.\s+(.*)$", line.strip())
        if not m:
            continue
        body = re.sub(r"\s+PMID:\S+", "", m.group(2))
        body = re.sub(r"\s+PMCID:\S+", "", body)
        entries.append(f"{m.group(1)}. {body}")
    return "\n".join(entries)


def figblock(panels, num, caption: str, cols: int = 2,
             label: str | None = None, keep: bool = False) -> str:
    tag = label if label is not None else f"Figure {num}."
    if cols == 1:
        inner = "".join(f"<div>{p}</div>" for p in panels)
    else:
        rows = [panels[i:i + cols] for i in range(0, len(panels), cols)]
        inner = "".join(
            f'<div class="figgrid" style="grid-template-columns:repeat({cols},minmax(0,1fr))">'
            + "".join(f"<div>{p}</div>" for p in row) + "</div>" for row in rows)
    cls = "figblock keep" if keep else "figblock"
    return (f'<div class="{cls}">{inner}'
            f'<div class="figcap"><b>{tag}</b> {caption}</div></div>')


def raster(name: str, alt: str) -> str:
    """Prefer the CBC hi-res copy; fall back to the master PNG. Returns an <img>."""
    hires = HIRES / name
    if hires.exists():
        src = f"figures-hires/{name}"
    else:
        src = f"../final/figures/{name}"
    return f'<img alt="{alt}" style="width:100%;margin:0 auto" src="{src}">'


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--html-only", action="store_true")
    args = ap.parse_args()

    import markdown

    paper = (CBC / "cbc-paper.md").read_text(encoding="utf-8")

    # References: replace the section with the verified list (same as the master build)
    idx = paper.index("## References")
    paper = paper[:idx] + "## References\n\n" + build_references_block() + "\n"

    body = markdown.markdown(paper, extensions=["tables", "fenced_code"])

    # Citations stay in the master's consistent style (CBC: any consistent style at
    # submission; the journal style is applied at proof stage). No conversion.

    def P(i) -> str:
        svg = (FIGDIR / f"panel{i}.svg").read_text(encoding="utf-8")
        # CBC render layer: align panel labels with the manuscript spelling
        return svg.replace("GWASCatalog", "GWAS Catalog")

    caps, supp = build_captions()
    missing = [f"Figure {i}" for i in range(1, 10) if not caps.get(i)]
    missing += [f"Supplementary Figure S{i}" for i in (1, 2, 3, 4) if not supp.get(i)]
    if missing:
        raise SystemExit(f"captions.md is missing: {missing}")
    caps_s1, caps_s2, caps_s3, caps_s4 = supp[1], supp[2], supp[3], supp[4]

    def C(i: int) -> str:
        return caps[i]

    # Figure anchors identical to the master build
    body = body.replace("<h3>3.2 ", figblock([P(1)], 1, C(1), cols=1) + "<h3>3.2 ")
    body = body.replace("<h3>3.4 ", figblock([P(2)], 2, C(2), cols=1) + "<h3>3.4 ")
    body = body.replace("<h2>4. ", figblock([P(16), P(3)], 3, C(3), cols=1, keep=True) + "<h2>4. ")
    body = body.replace("<h2>5. ", figblock([P(5), P(6), P(7), P(17)], 4, C(4), cols=2, keep=True) + "<h2>5. ")

    blks = ""
    for rnum, rfile in ((6, "figure6-ledger.png"), (7, "figure7-record.png"),
                        (8, "figure8-ask.png")):
        found = (HIRES / rfile).exists() or (FINAL / "figures" / rfile).exists()
        if found:
            blks += figblock([raster(rfile, f"Figure {rnum}")], rnum, C(rnum),
                             cols=1, keep=True)
        else:
            print(f"WARNING: {rfile} not found; Figure {rnum} not embedded")
    body = body.replace("<h2>6. ", figblock([P(4)], 5, C(5), cols=1) + blks + "<h2>6. ")
    body = body.replace("<h2>7. ", figblock([P(i) for i in range(8, 16)] + [P(18)], 9, C(9),
                                            cols=2, keep=True) + "<h2>7. ")

    # Supplementary figures S1 (3 panels, col=1), S2 (4 panels, col=2), S3/S4 (rasters)
    s1 = [P(f"S1{c}") for c in ("A", "B", "C")] if all(
        (FIGDIR / f"panelS1{c}.svg").exists() for c in ("A", "B", "C")) else []
    s2 = [P(f"S2{c}") for c in ("A", "B", "C", "D")] if all(
        (FIGDIR / f"panelS2{c}.svg").exists() for c in ("A", "B", "C", "D")) else []
    supp_blocks = ""
    if s1:
        supp_blocks += figblock(s1, 0, caps_s1, cols=1, keep=True, label="Supplementary Figure S1.")
    else:
        print("WARNING: panels panelS1A/panelS1B/panelS1C not found")
    if s2:
        supp_blocks += figblock(s2, 0, caps_s2, cols=2, keep=True, label="Supplementary Figure S2.")
    else:
        print("WARNING: panels panelS2A-D not found")
    for num, fname, cap in ((3, "supplementary-figure-s3.png", caps_s3),
                            (4, "supplementary-figure-s4.png", caps_s4)):
        if (HIRES / fname).exists() or (FINAL / "figures" / fname).exists():
            supp_blocks += figblock([raster(fname, f"Supplementary Figure S{num}")],
                                    0, cap, cols=1, label=f"Supplementary Figure S{num}.")
        else:
            print(f"WARNING: {fname} not found")
    if "<h2>Funding</h2>" in body:
        body = body.replace("<h2>Funding</h2>", supp_blocks + "<h2>Funding</h2>", 1)
    else:
        body += supp_blocks

    # Table captions get the tabcap class (keep with table)
    body = re.sub(r"<p>(<strong>Table \d+\.)", r'<p class="tabcap">\1', body)

    # Abstract wrapper + Graphical Abstract right after it
    m = re.search(r"<h2>Abstract</h2>\s*<p>(.*?)</p>", body, re.S)
    if m:
        ga_svg = (FIGDIR / "graphical-abstract.svg").read_text(encoding="utf-8")
        ga = ('<div class="figtag">Graphical Abstract</div>'
              f'<figure class="ga">{ga_svg}</figure>')
        body = body.replace(m.group(0),
                            f'<div class="abstract"><h2>Abstract</h2><p>{m.group(1)}</p></div>' + ga)

    # Section numbering stays (cross-references rely on it)
    body = re.sub(r"<h2>(\d+\.\s*)([^<]+)</h2>",
                  lambda mm: f"<h2>{mm.group(1).strip()} {mm.group(2)}</h2>", body)
    body = re.sub(r"<h3>(\d+\.\d+)\s*([^<]+)</h3>",
                  lambda mm: f"<h3>{mm.group(1)} {mm.group(2)}</h3>", body)

    html = (f'<!DOCTYPE html><html><head><meta charset="utf-8">'
            f'<title>BioAgentDB — manuscript (CBC submission)</title>'
            f'<style>{CSS}</style></head><body>{body}</body></html>')
    out_html = CBC / "cbc-paper.html"
    out_html.write_text(html, encoding="utf-8")
    print(f"wrote {out_html.relative_to(ROOT)}")

    # sanity: no NAR strings
    for bad in ("Nucleic Acids Research", "Database issue", "narhead"):
        assert bad not in html, f"stray NAR string in CBC html: {bad}"

    if args.html_only:
        return
    out_pdf = CBC / "cbc-paper.pdf"
    staged = CBC / "cbc-paper.pdf.staged"
    staged.unlink(missing_ok=True)
    r = subprocess.run(
        [EDGE, "--headless", "--disable-gpu", "--no-pdf-header-footer",
         f"--print-to-pdf={staged}", out_html.as_uri()],
        capture_output=True, timeout=420)
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
