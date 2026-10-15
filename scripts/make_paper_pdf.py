"""把 paper.md 排版为 NAR（Nucleic Acids Research）期刊版式 PDF。

流程: paper.md → NAR 样式 HTML → Edge print-to-pdf → reportlab 盖章（页眉 + D 页码）。
要点:
- 刊头 / 作者·单位·通讯占位（不填实际信息）/ ABSTRACT 区块 / 无编号粗体标题；
- 正文引用转上标；Fig. 1b → Figure 1B；图入正文（Figure 1、Figure 2）；Table 1 题注；
- 文末声明占位；参考文献去 PMID/PMCID；
- 页脚 "D1, D2…"（Database issue 页码），≥2 页加书眉。
用法: python scripts/make_paper_pdf.py [--no-pdf]
"""

from __future__ import annotations

import argparse
import io
import re
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FINAL = ROOT / "docs" / "paper" / "final"
FIGDIR = FINAL.parent / "figures"
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

CSS = """
@page { size: A4; margin: 20mm 18mm 22mm 18mm; }
body { font-family: "Times New Roman", Times, serif; font-size: 10pt;
       line-height: 1.35; color: #000; }
.narhead { display: flex; justify-content: space-between; font-size: 8.5pt;
           border-bottom: 1pt solid #000; padding-bottom: 3pt; margin-bottom: 12pt; }
h1 { font-size: 16pt; line-height: 1.25; margin: 4pt 0 8pt; }
.authors { font-size: 11pt; margin: 2pt 0 1pt; }
.affil { font-size: 9pt; font-style: italic; margin: 1pt 0; }
.corr { font-size: 8.5pt; margin: 4pt 0 12pt; }
.abstract { margin: 0 0 12pt; }
.abstract h2 { font-size: 10.5pt; margin: 0 0 4pt; }
.abstract p { font-size: 9.5pt; }
h2 { font-size: 11pt; margin: 14pt 0 5pt; page-break-after: avoid; }
h3 { font-size: 10pt; margin: 10pt 0 3pt; page-break-after: avoid; }
p { margin: 5pt 0; text-align: justify; }
sup { font-size: 7pt; }
blockquote { font-size: 8.5pt; color: #333; border-left: 2pt solid #999;
             padding: 4pt 8pt; margin: 8pt 0; background: #f8f8f8; }
table { border-collapse: collapse; width: 100%; font-size: 8.5pt; margin: 6pt 0;
        page-break-inside: avoid; }
th, td { border: 0.5pt solid #999; padding: 2.5pt 4pt; text-align: left;
         vertical-align: top; }
code { font-family: Consolas, "Courier New", monospace; font-size: 8.5pt; }
.tabcap { font-size: 8.5pt; margin: 8pt 0 2pt; }
.figblock { margin: 4pt 0 6pt; }   /* allow figure blocks to break between rows (avoids half-empty pages) */
.figblock.keep { break-inside: avoid; }
.figcap { break-before: avoid; }
figure { margin: 0; }
.tabcap { break-after: avoid; break-inside: avoid; }
table { break-inside: avoid; }
.tblock { break-inside: avoid; }
.figgrid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 3pt; }
.figblock svg, .figblock img { display: block; width: 100%; max-width: 100%; height: auto; }
.figgrid svg { width: 100%; max-width: 100%; height: auto; border: 0.5pt solid #ddd; }
.ga { width: 100%; }
.ga svg { width: 100%; height: auto; }
.figcap { font-size: 8.5pt; margin-top: 4pt; text-align: justify; break-inside: avoid; }
ol.references { font-size: 8pt; padding-left: 16pt; }
ol.references li { margin-bottom: 2.5pt; text-align: left; }
"""

def build_captions() -> tuple[dict, dict]:
    """从 `captions.md`（单一真源）构建图注；缺条目即报错（不再保留会漂移的兜底副本）。"""
    try:
        text = (FINAL / "captions.md").read_text(encoding="utf-8")
    except OSError:
        return {}, {}
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
    return caps, supp


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


def figblock(panels, num, caption: str, cols: int = 2, label: str | None = None,
             keep: bool = False) -> str:
    tag = label if label is not None else f"Figure {num}."
    if cols == 1:
        inner = "".join(f"<div>{p}</div>" for p in panels)
    else:
        # 每 cols 个面板一行、每行一个 grid：行间可分页（整块 grid 在 Chromium 中不可分页）
        rows = [panels[i:i + cols] for i in range(0, len(panels), cols)]
        inner = "".join(
            f'<div class="figgrid" style="grid-template-columns:repeat({cols},minmax(0,1fr))">'
            + "".join(f"<div>{p}</div>" for p in row) + "</div>" for row in rows)
    cls = "figblock keep" if keep else "figblock"
    return (f'<div class="{cls}">{inner}'
            f'<div class="figcap"><b>{tag}</b> {caption}</div></div>')


def stamp_pdf(pdf_path: Path, title: str = "") -> int:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(str(pdf_path))
    writer = PdfWriter()
    w, h = A4
    for i, page in enumerate(reader.pages):
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=A4)
        c.setFont("Times-Roman", 8)
        c.drawCentredString(w / 2, 30, f"D{i + 1}")  # Database issue 页码
        if i > 0:
            c.setFont("Times-Italic", 8)
            c.drawString(51, h - 42, "Nucleic Acids Research, 2026, Database issue")
        c.save()
        buf.seek(0)
        overlay = PdfReader(buf).pages[0]
        page.merge_page(overlay)
        writer.add_page(page)
    writer.add_metadata({
        "/Title": title or "BioAgentDB",
        "/Subject": "Nucleic Acids Research, 2026, Database issue",
        "/Creator": "BioAgentDB (scripts/make_paper_pdf.py)",
    })
    with tempfile.NamedTemporaryFile(dir=str(pdf_path.parent), suffix=".pdf.tmp",
                                     delete=False) as fh:
        tmp = Path(fh.name)
    try:
        with tmp.open("wb") as fh:
            writer.write(fh)
        for attempt in range(10):          # Windows 上目标可能被 Edge/并发进程短暂占用
            try:
                tmp.replace(pdf_path)      # 原子替换
                break
            except PermissionError:
                if attempt == 9:
                    with tmp.open("rb") as src, pdf_path.open("wb") as dst:
                        dst.write(src.read())
                else:
                    time.sleep(0.8)
    finally:
        tmp.unlink(missing_ok=True)
    return len(reader.pages)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-pdf", action="store_true")
    args = ap.parse_args()

    import markdown  # noqa: PLC0415

    paper = (FINAL / "paper.md").read_text(encoding="utf-8")
    # 1) 顶部注记 → 草稿说明
    paper = re.sub(
        r"> Numbers are bound[\s\S]*?(?=\n## Abstract)",
        "> *Draft manuscript formatted for the Nucleic Acids Research Database issue; author "
        "information to be added. All catalog numbers are traced in the project repository; "
        "evaluation uses a human, single-annotator annotation (provisional).*", paper)
    # 2) 去作者占位行（NAR 作者块由 HTML 注入）——按模式剥离，避免占位行措辞变化时漏删
    paper = re.sub(r"^\*\*[^\n]*\*\* \*\(to be completed\)\*\n", "", paper, flags=re.M)
    # 3) References 段替换为完整列表
    idx = paper.index("## References")
    paper = paper[:idx] + "## References\n\n" + build_references_block() + "\n"

    body = markdown.markdown(paper, extensions=["tables"])

    # 4) 引用保持**圆括号制**（NAR：`(1)`、`(1–5)`）——v2 正文已是括号制；此处仅把可能残留的 [n] 兜底转换
    body = re.sub(r"\[(\d+(?:\s*[,–-]\s*\d+)*)\]", r"(\1)", body)
    # 5) 图号统一为 "Figure N"（v2 正文已用此写法，兜底）
    body = re.sub(r"Fig\.\s*(\d)\s*([a-h])\b",
                  lambda mm: f"Figure {mm.group(1)}{mm.group(2).upper()}", body)

    # 6) figures into the body: panel1/2=图1/2，panel16+3=图3A/3B，panel5/6/7/17=图4A–4D，panel4=图5，
#    figure6-ledger/7-record/8-ask = 图 6/7/8（真机截图），panel8–15+18=图 9A–9I，
#    panelS1A-C=补充图 S1，panelS2A-D=补充图 S2，supplementary-figure-s3.png=补充图 S3，supplementary-figure-s4.png=补充图 S4
    def P(i) -> str:
        return (FIGDIR / f"panel{i}.svg").read_text(encoding="utf-8")

    caps, supp = build_captions()
    missing = [f"Figure {i}" for i in range(1, 10) if not caps.get(i)]
    missing += [f"Supplementary Figure S{i}" for i in (1, 2, 3, 4) if not supp.get(i)]
    if missing:
        raise SystemExit(f"captions.md is missing: {missing}")   # 单一真源：不再保留会漂移的兜底副本
    caps_s1, caps_s2, caps_s3, caps_s4 = supp[1], supp[2], supp[3], supp[4]

    def C(i: int) -> str:
        return caps[i]
    # 文档顺序 = 图号顺序：1(§3.1后) 2(§3.3后) 3(§3末) 4(§4末) 5(§5末·Ask流程) 6/7/8(§5末·三张真机截图) 9(§6末·评测)
    body = body.replace("<h3>3.2 ", figblock([P(1)], 1, C(1), cols=1) + "<h3>3.2 ")
    body = body.replace("<h3>3.4 ", figblock([P(2)], 2, C(2), cols=1) + "<h3>3.4 ")
    body = body.replace("<h2>4. ", figblock([P(16), P(3)], 3, C(3), cols=1) + "<h2>4. ")
    # 图 4：4A–4C + 4D（链接状态），双栏
    body = body.replace("<h2>5. ", figblock([P(5), P(6), P(7), P(17)], 4, C(4), cols=2) + "<h2>5. ")
    # Figure 6/7/8 = 三张真机截图（一图一注，L-0185）；Figure 9 = 评估（9A–9I）
    rasters = ((6, "figure6-ledger.png", "100%"),
               (7, "figure7-record.png", "100%"),
               (8, "figure8-ask.png", "100%"))
    blks = ""
    for rnum, rfile, rw in rasters:
        if (FINAL / "figures" / rfile).exists():
            blks += figblock([f'<img alt="Figure {rnum}" style="width:{rw};margin:0 auto" '
                              f'src="figures/{rfile}">'], rnum, C(rnum), cols=1, keep=True)
        else:
            print(f"WARNING: {rfile} not found; Figure {rnum} not embedded")
    body = body.replace("<h2>6. ", figblock([P(4)], 5, C(5), cols=1) + blks + "<h2>6. ")
    body = body.replace("<h2>7. ", figblock([P(i) for i in range(8, 16)] + [P(18)], 9, C(9),
                                            cols=2, keep=True) + "<h2>7. ")

    # 6b) 补充图 S1（机制：门控 / 活的资源 / 归一化；字母按正文引用序）
    s1 = [P(f"S1{c}") for c in ("A", "B", "C")] if all(
        (FIGDIR / f"panelS1{c}.svg").exists() for c in ("A", "B", "C")) else []
    if s1:
        blk = figblock(s1, 0, caps_s1, cols=1, label="Supplementary Figure S1.")
        if "<h2>Funding</h2>" in body:
            body = body.replace("<h2>Funding</h2>", blk + "<h2>Funding</h2>", 1)
        else:
            body += blk
        print("supplementary figure S1 embedded:", "Supplementary Figure S1." in body)
    else:
        print("WARNING: panels panelS1A/panelS1B/panelS1C not found; Supplementary Figure S1 not embedded")

    # 6b2) 补充图 S2（覆盖：论文级 P/R、扫描增益、归一化覆盖、仓库分布）
    s2 = [P(f"S2{c}") for c in ("A", "B", "C", "D")] if all(
        (FIGDIR / f"panelS2{c}.svg").exists() for c in ("A", "B", "C", "D")) else []
    if s2:
        blk2 = figblock(s2, 0, caps_s2, cols=2, label="Supplementary Figure S2.")
        if "<h2>Funding</h2>" in body:
            body = body.replace("<h2>Funding</h2>", blk2 + "<h2>Funding</h2>", 1)
        else:
            body += blk2
        print("supplementary figure S2 embedded:", "Supplementary Figure S2." in body)
    else:
        print("WARNING: panels panelS2A/panelS2B/panelS2C/panelS2D not found; Supplementary Figure S2 not embedded")

    # 6b3) 补充图 S3（§5 结果集与导出）+ S4（用户挖掘；L-0271，v4 实拍）
    for num, fname, cap, frac in ((3, "supplementary-figure-s3.png", caps_s3, "100%"),
                                  (4, "supplementary-figure-s4.png", caps_s4, "100%")):
        img = FINAL / "figures" / fname
        if not img.exists():
            print(f"WARNING: {fname} not found; Supplementary Figure S{num} not embedded")
            continue
        blk = figblock([f'<img alt="Supplementary Figure S{num}" style="width:{frac};margin:0 auto" '
                        f'src="figures/{fname}">'],
                       0, cap, cols=1, label=f"Supplementary Figure S{num}.")
        if "<h2>Funding</h2>" in body:
            body = body.replace("<h2>Funding</h2>", blk + "<h2>Funding</h2>", 1)
        else:
            body += blk
        print(f"supplementary figure S{num} embedded:", f"Supplementary Figure S{num}." in body)

    # 6c) 表题注加 class，使 .tabcap{break-after:avoid} 生效（题注不跨页）
    body = re.sub(r"<p>(<strong>Table \d+\.)", r'<p class="tabcap">\1', body)

    # 7) 表题注由正文提供（Table 1–5 已写在 paper.md），此处不再注入，避免重复

    # 8) 文末声明由正文自身提供（Supplementary data / Funding / Conflict of interest），不再注入重复块

    # 9) Abstract 区块 + Graphical Abstract（NAR 位置：摘要之后）
    m = re.search(r"<h2>Abstract</h2>\s*<p>(.*?)</p>", body, re.S)
    if m:
        ga_svg = (ROOT / "docs" / "paper" / "figures" / "graphical-abstract.svg").read_text(
            encoding="utf-8")
        ga = ('<div class="figtag">Graphical Abstract</div>'
              f'<figure class="ga">{ga_svg}</figure>')
        body = body.replace(m.group(0),
                            f'<div class="abstract"><h2>Abstract</h2><p>{m.group(1)}</p></div>' + ga)

    # 10) 标题：**保留编号**（正文含 §x.y 交叉引用，去编号会使其悬空）；保持句子式（不大写）
    body = re.sub(r"<h2>(\d+\.\s*)([^<]+)</h2>",
                  lambda mm: f"<h2>{mm.group(1).strip()} {mm.group(2)}</h2>", body)
    body = re.sub(r"<h3>(\d+\.\d+)\s*([^<]+)</h3>",
                  lambda mm: f"<h3>{mm.group(1)} {mm.group(2)}</h3>", body)

    # 11) 作者/单位/通讯占位块
    author_block = ('<div class="authors">[Author list to be added]<sup>1,*</sup></div>'
                    '<div class="affil"><sup>1</sup>[Affiliations to be added]</div>'
                    '<div class="corr">ORCID: [to be added] · Author contributions (CRediT): [to be added] · '
                    'Acknowledgements: [to be added]</div>'
                    '<div class="corr">*To whom correspondence should be addressed. '
                    'Email: [to be added]</div>')
    body = body.replace("</h1>", "</h1>" + author_block, 1)

    narhead = ('<div class="narhead"><span>Nucleic Acids Research, 2026, Database issue</span>'
               '<span>doi: [to be assigned]</span></div>')
    html = (f'<!DOCTYPE html><html><head><meta charset="utf-8">'
            f'<title>BioAgentDB — manuscript</title><style>{CSS}</style></head>'
            f'<body>{narhead}{body}</body></html>')
    out_html = FINAL / "paper.html"
    out_html.write_text(html, encoding="utf-8")
    print(f"wrote {out_html}")

    if args.no_pdf:
        return
    out_pdf = FINAL / "paper.pdf"
    staged = FINAL / "paper.pdf.staged"
    staged.unlink(missing_ok=True)
    r = subprocess.run(
        [EDGE, "--headless", "--disable-gpu", "--no-pdf-header-footer",
         f"--print-to-pdf={staged}", out_html.as_uri()],
        capture_output=True, timeout=240)
    size = staged.stat().st_size if staged.exists() else 0
    print(f"print-to-pdf rc={r.returncode} | size={size}")
    pages = stamp_pdf(staged, title=str((FINAL / "paper.md").read_text(
        encoding="utf-8").splitlines()[0].lstrip("# ").strip()))
    print(f"stamped NAR header/footer | pages={pages} | size={staged.stat().st_size}")
    # 目标文件可能被 Review 面板/预览器占用：带重试地换入，失败则保留 staged 文件并提示
    for attempt in range(12):
        try:
            staged.replace(out_pdf)
            break
        except PermissionError:
            if attempt == 11:
                print(f"WARNING: {out_pdf.name} is locked by another process; "
                      f"the corrected build is at {staged.name}")
            else:
                time.sleep(5)


if __name__ == "__main__":
    main()
