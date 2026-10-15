"""逐图导出：figures/figure1–9.pdf + figureS1–S4.pdf + graphical-abstract.pdf。

规则：
- 单面板按 SVG viewBox 紧裁（页面高度 = 图高 + 题注高度）；多面板默认双栏 + A4，
  但**全宽示意图（viewBox 宽 ≥ 900）一律单栏**（否则缩到 <6 pt）；
- 每张导出图**带主题注**（图注单一真源 = `docs/paper/final/captions.md`，与本稿 §Figure captions 一致）；
- 若题注使单面板超出 1 页，自动回退为 A4 重渲染；GA 无题注。

用法: python scripts/make_figure_exports.py
"""
from __future__ import annotations

import html
import re
import subprocess
import tempfile
from pathlib import Path

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
FIGD = ROOT / "docs" / "paper" / "figures"
FT = ROOT / "docs" / "paper" / "final" / "figures"
CAPS_MD = ROOT / "docs" / "paper" / "final" / "captions.md"
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
FIG_MM = 170.0   # 逐图导出页宽（L-0203：与成稿内实际打印宽 ≈169.86 mm 归一；不再用 174）

# 图号 -> 面板（图 3 = 3A 单元/分工 + 3B 机制；图 4 = 4A–4C + 4D 链接状态；图 6/7/8 = 真机截图栅格；
# 图 9 = 9A–9I findable vs announced）
GROUPS = {1: ["panel1"], 2: ["panel2"], 3: ["panel16", "panel3"],
          4: ["panel5", "panel6", "panel7", "panel17"],
          5: ["panel4"], 9: ["panel8", "panel9", "panel10", "panel11", "panel12",
                             "panel13", "panel14", "panel15", "panel18"]}
RASTERS = {6: "figure6-ledger.png", 7: "figure7-record.png", 8: "figure8-ask.png"}
S1 = ["panelS1A", "panelS1B", "panelS1C"]          # 机制（引用序在前）
S2 = ["panelS2A", "panelS2B", "panelS2C", "panelS2D"]  # 覆盖
S_RASTERS = {3: "supplementary-figure-s3.png", 4: "supplementary-figure-s4.png"}  # 结果集 + 用户挖掘（栅格）


def load_captions() -> dict:
    """从 captions.md 读取图注（与 PDF builder 同一真源），含补充图 S1–S4。"""
    try:
        text = CAPS_MD.read_text(encoding="utf-8")
    except OSError:
        return {}
    caps: dict = {}
    for m in re.finditer(r"^##\s+(.+?)\s*$\n(.*?)(?=^##\s|\Z)", text, re.S | re.M):
        head = m.group(1).strip()
        body = re.sub(r"\s+", " ", m.group(2).replace("**", "")).strip()
        title = head.split("·", 1)[1].strip() if "·" in head else ""
        merged = f"{title}. {body}".strip() if (title and body) else (body or title)
        fm = re.match(r"Figure\s+(\d)\b", head)
        fs = re.match(r"Supplementary Figure\s+S(\d)\b", head)
        if fm and merged:
            caps[f"Figure {fm.group(1)}"] = merged
        elif fs and merged:
            caps[f"Supplementary Figure S{fs.group(1)}"] = merged
    return caps


def _css(page_size: str, margin: str, grid: str, wcss: str) -> str:
    return (f"@page{{size:{page_size};margin:{margin}}}html,body{{margin:0}}{grid}"
            ".cap{font:8.5pt/1.35 'Times New Roman',Times,serif;text-align:justify;"
            "margin-top:4mm;padding-right:1mm}"
            f"svg,img{{width:{wcss};max-width:100%;height:auto;display:block}}")


def _write_pdf(cells: list[str], out_pdf: Path, css: str, wrap: bool) -> int:
    body = "".join(cells)
    if wrap:
        body = "<div class='g'>" + body + "</div>"
    hp = Path(tempfile.gettempdir()) / (out_pdf.stem + "-export.htm")
    hp.write_text("<!doctype html><html><head><meta charset='utf-8'><style>" + css +
                  "</style></head><body>" + body + "</body></html>", encoding="utf-8")
    # 先删旧件：若被其它进程占用会在此 **大声失败**（防"打印静默沿用旧件"，L-0203）
    out_pdf.unlink(missing_ok=True)
    subprocess.run([EDGE, "--headless", "--disable-gpu", "--no-pdf-header-footer",
                    f"--print-to-pdf={out_pdf}", hp.as_uri()], capture_output=True, timeout=240)
    hp.unlink(missing_ok=True)
    if not out_pdf.exists() or out_pdf.stat().st_size == 0:
        raise SystemExit(f"print failed for {out_pdf} (locked or edge error)")
    return len(PdfReader(str(out_pdf)).pages)


def pick_cols(cells: list[str], default: int) -> int:
    """全宽示意图（viewBox 宽 ≥900）必须单栏，否则缩到 <6 pt。"""
    widths = [float(b[0]) for b in re.findall(
        r'viewBox="[-\d.]+ [-\d.]+ ([\d.]+) ([\d.]+)"', "".join(cells))]
    return 1 if (widths and max(widths) >= 900) else default


def _img_size(cells: list[str]) -> tuple[float, float] | None:
    """从 <img src=...png> 读取像素尺寸（用于给栅格算紧凑页高）。"""
    import struct
    m = re.search(r"src='([^']+\.png)'", "".join(cells))
    if not m:
        return None
    try:
        from urllib.parse import unquote
        p = Path(unquote(m.group(1)).lstrip("/"))
        if p.drive == "":
            p = Path(unquote(m.group(1).replace("file:///", "")))
        with open(p, "rb") as fh:
            head = fh.read(24)
        w, h = struct.unpack(">II", head[16:24])
        return float(w), float(h)
    except Exception:
        return None


def caption_for(caps: dict, key: str) -> str:
    """题注必须来自 captions.md（单一真源）；缺失或退化为标题即报错（与论文侧对称）。"""
    cap = (caps.get(key) or "").strip()
    if len(cap) < 40:
        raise SystemExit(f"captions.md is missing a usable caption for {key} (got {cap!r})")
    return cap


def render(cells: list[str], out_pdf: Path, cols: int = 1,
           label: str = "", caption: str = "") -> None:
    boxes = [(b[0], b[1]) for b in re.findall(
        r'viewBox="[-\d.]+ [-\d.]+ ([\d.]+) ([\d.]+)"', "".join(cells))
        if float(b[0]) > 50]
    multi = cols > 1 or len(boxes) > 1
    cap = (f'<div class="cap"><b>{html.escape(label)}.</b> {html.escape(caption)}</div>'
           if caption else "")
    if multi:
        m0, wcss = "0", f"{FIG_MM:.0f}mm"
        grid = (".g{display:grid;grid-template-columns:repeat("
                f"{max(cols, 1)},minmax(0,1fr));gap:4mm}}")
        gap_mm, colw = 4.0, (FIG_MM - 4.0 * (max(cols, 1) - 1)) / max(cols, 1)
        rows = [boxes[i:i + max(cols, 1)] for i in range(0, len(boxes), max(cols, 1))]
        if boxes:
            hs = [max(colw * float(b[1]) / float(b[0]) for b in row) for row in rows]
            fig_mm = sum(hs) + gap_mm * (len(rows) - 1)
            cap_mm = (max(1, -(-len(caption) // 112)) * 8.5 * 1.35 * 0.3528 + 10.0) if caption else 0.0
            if fig_mm + cap_mm <= 250:
                size = f"{FIG_MM:.0f}mm {fig_mm + cap_mm:.1f}mm"
            else:
                size, m0, wcss = "A4", "6mm", "100%"
        else:
            size = "A4"                                  # 栅格（无 viewBox）→ A4
    elif boxes or _img_size(cells):
        if boxes:
            w, h = float(boxes[0][0]), float(boxes[0][1])
        else:
            w, h = _img_size(cells)                      # 栅格（无 viewBox）：按 PNG 像素
        fig_mm = FIG_MM * h / w
        cap_mm = 0.0
        if caption:
            lines = max(1, -(-len(caption) // 112))          # ~112 chars/line at 8.5 pt
            cap_mm = lines * 8.5 * 1.35 * 0.3528 + 10.0      # + margin headroom（含 2 pt 余量，消除行末 +0.53 pt 溢出）
        size, m0, wcss, grid = f"{FIG_MM:.0f}mm {fig_mm + cap_mm:.1f}mm", "0", f"{FIG_MM:.0f}mm", ""
    else:
        size, m0, wcss, grid = "A4", "6mm", "100%", ""
    def _render(page_size: str, margin: str, width: str) -> int:
        return _write_pdf(cells + ([cap] if cap else []), out_pdf,
                          _css(page_size, margin, grid, width), bool(grid))

    pages = _render(size, m0, wcss)
    if pages > 1 and size != "A4":                           # 估算偏小 → 逐步放宽，再不行回退 A4
        h_mm = float(size.split()[1][:-2])
        for factor in (1.12, 1.30):
            pages = _render(f"{FIG_MM:.0f}mm {h_mm * factor:.1f}mm", m0, wcss)
            if pages == 1:
                break
        if pages > 1:
            pages = _render("A4", "6mm", "100%")
    print(f"wrote {out_pdf.name} ({pages} page{'s' if pages != 1 else ''})")


def main() -> None:
    caps = load_captions()
    for n, panels in GROUPS.items():
        cells = [(FIGD / f"{p}.svg").read_text(encoding="utf-8") for p in panels]
        label = f"Figure {n}"
        render(cells, FT / f"figure{n}.pdf",
               cols=pick_cols(cells, 1 if len(panels) == 1 else 2),
               label=label, caption=caption_for(caps, label))
    for rnum, rname in RASTERS.items():
        rp = FIGD / rname
        if rp.exists():
            render([f"<img src='{rp.as_uri()}' style='width:100%'>"], FT / f"figure{rnum}.pdf",
                   label=f"Figure {rnum}", caption=caption_for(caps, f"Figure {rnum}"))
    for key, names, default_cols in (("Supplementary Figure S1", S1, 1),
                                     ("Supplementary Figure S2", S2, 2)):
        cells = [FIGD / f"{p}.svg" for p in names]
        if all(p.exists() for p in cells):
            svgs = [p.read_text(encoding="utf-8") for p in cells]
            render(svgs, FT / f"figure{key.split()[-1]}.pdf",
                   cols=pick_cols(svgs, default_cols),
                   label=key, caption=caption_for(caps, key))
    for snum, sname in S_RASTERS.items():
        sr = FIGD / sname
        if sr.exists():
            render([f"<img src='{sr.as_uri()}' style='width:100%'>"], FT / f"figureS{snum}.pdf",
                   label=f"Supplementary Figure S{snum}",
                   caption=caption_for(caps, f"Supplementary Figure S{snum}"))
    ga = FIGD / "graphical-abstract.svg"
    if ga.exists():
        render([ga.read_text(encoding="utf-8")], FT / "graphical-abstract.pdf")


if __name__ == "__main__":
    main()
