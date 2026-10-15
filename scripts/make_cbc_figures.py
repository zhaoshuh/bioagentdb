"""CBC 高清图件（>=2244 px 宽，Elsevier 全页 300 dpi 阈值）——仅生成 CBC 专用副本。

来源：docs/paper/final/evidence/v4-*.png（3264x2432 @2x 实拍底片）。
与母版图件（1740 px）同款取法（整幅窗口 + 白边/圆角/线框；S3 带 Guide 式 callout），
按 SCALE = 2244/1740 等比放大全部细节常量；输出到 docs/paper/cbc/figures-hires/，
不触碰母版目录。

用法: python scripts/make_cbc_figures.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
EVID = ROOT / "docs" / "paper" / "final" / "evidence"
OUT = ROOT / "docs" / "paper" / "cbc" / "figures-hires"

BASE_W = 1740          # 母版成品宽（make_figure7.py 等）
OUT_W = 2244           # CBC 目标宽（>= 2244 px, 300 dpi @ 190 mm）
SCALE = OUT_W / BASE_W  # ≈ 1.2897


def _s(x: float) -> int:
    return max(1, int(round(x * SCALE)))


CARD_PAD = _s(18)
CARD_RADIUS = _s(12)
CARD_BORDER = max(3, _s(3))
LINE = (200, 212, 206)
TEAL = (15, 118, 110)
AMBER = (161, 98, 7)

FONTS = [r"C:\Windows\Fonts\arialbd.ttf", r"C:\Windows\Fonts\arial.ttf"]


def _font(size: int) -> ImageFont.FreeTypeFont:
    for p in FONTS:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def _window(path: Path, width: int = OUT_W) -> Image.Image:
    im = Image.open(path).convert("RGB")
    w, h = im.size
    return im.resize((width, round(h * width / w)), Image.LANCZOS)


def _card(img: Image.Image) -> Image.Image:
    w, h = img.size
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1],
                                           radius=CARD_RADIUS, fill=255)
    out = Image.new("RGB", (w + 2 * CARD_PAD, h + 2 * CARD_PAD), (255, 255, 255))
    out.paste(img, (CARD_PAD, CARD_PAD), mask)
    ImageDraw.Draw(out).rounded_rectangle(
        [CARD_PAD, CARD_PAD, CARD_PAD + w - 1, CARD_PAD + h - 1],
        radius=CARD_RADIUS, outline=LINE, width=CARD_BORDER)
    return out


def _save(img: Image.Image, name: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / name
    img.save(p, dpi=(300, 300))  # pHYs for Editorial Manager (>72 dpi warning guard)
    print(f"wrote {p.name}  {img.size[0]}x{img.size[1]}  "
          f"(full-width dpi @150mm = {img.size[0] / (150 / 25.4):.0f})")


# ---- main rasters: figures 6/7/8 -------------------------------------------

def figures_678() -> None:
    for src, name in (("v4-ledger.png", "figure6-ledger.png"),
                      ("v4-record.png", "figure7-record.png"),
                      ("v4-ask.png", "figure8-ask.png")):
        _save(_card(_window(EVID / src)), name)


# ---- supplementary figure S3 (callouts, scales with sx) --------------------

SRC_SIZE = (3264, 2432)
CALLOUTS = [((2440, 178, 3232, 880), "1", TEAL),
            ((2440, 890, 3232, 975), "2", TEAL),
            ((2440, 1136, 3232, 1965), "3", AMBER)]


def figure_s3() -> None:
    img = _window(EVID / "v4-export.png")
    d = ImageDraw.Draw(img)
    sx = img.width / SRC_SIZE[0]
    sy = img.height / SRC_SIZE[1]
    radius = max(8, int(round(12 * sx)))
    line_w = max(3, int(round(4 * sx)))
    br = int(round(17 * sx))
    fs = max(14, int(round(21 * sx)))
    font = _font(fs)
    for (x0, y0, x1, y1), num, color in CALLOUTS:
        box = [x0 * sx, y0 * sy, x1 * sx, y1 * sy]
        d.rounded_rectangle(box, radius=radius, outline=color, width=line_w)
        cx, cy = box[0] + br * 0.15, box[1] + br * 0.15
        d.ellipse([cx - br, cy - br, cx + br, cy + br], fill=color)
        d.text((cx, cy + 1), num, font=font, fill=(255, 255, 255), anchor="mm")
    _save(_card(img), "supplementary-figure-s3.png")


# ---- supplementary figure S4 (2x2 grid) ------------------------------------

CELL_W = _s(852)
GAP = _s(18)
LABEL_H = _s(34)
S4_CARD_PAD = _s(12)
S4_CARD_RADIUS = _s(10)
PANELS = [
    ("v4-ming-start.png", "(A) Ming — one sentence to a running task"),
    ("v4-mining-live.png", "(B) My mining — the task runs and records publish"),
    ("v4-mining-topics.png", "(C) Topics — rename, or hide reversibly"),
    ("v4-ledger81.png", "(D) the same deployment's ledger, grown from zero"),
]


def _card_s4(img: Image.Image) -> Image.Image:
    w, h = img.size
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1],
                                           radius=S4_CARD_RADIUS, fill=255)
    out = Image.new("RGB", (w + 2 * S4_CARD_PAD, h + 2 * S4_CARD_PAD), (255, 255, 255))
    out.paste(img, (S4_CARD_PAD, S4_CARD_PAD), mask)
    ImageDraw.Draw(out).rounded_rectangle(
        [S4_CARD_PAD, S4_CARD_PAD, S4_CARD_PAD + w - 1, S4_CARD_PAD + h - 1],
        radius=S4_CARD_RADIUS, outline=LINE, width=CARD_BORDER)
    return out


def figure_s4() -> None:
    cells = []
    for name, label in PANELS:
        im = Image.open(EVID / name).convert("RGB")
        w, h = im.size
        im = im.resize((CELL_W, round(h * CELL_W / w)), Image.LANCZOS)
        card = _card_s4(im)
        cell = Image.new("RGB", (card.width, card.height + LABEL_H), (255, 255, 255))
        cell.paste(card, (0, 0))
        ImageDraw.Draw(cell).text(
            (cell.width / 2, card.height + LABEL_H / 2), label,
            font=_font(_s(21)), fill=TEAL, anchor="mm")
        cells.append(cell)
    cols = 2
    cw = max(c.width for c in cells)
    row_h = [max(c.height for c in cells[i:i + cols]) for i in range(0, len(cells), cols)]
    W = cols * cw + (cols - 1) * GAP
    H = sum(row_h) + (len(row_h) - 1) * GAP
    sheet = Image.new("RGB", (W, H), (255, 255, 255))
    y = 0
    for r, hgt in enumerate(row_h):
        for cidx in range(cols):
            i = r * cols + cidx
            if i >= len(cells):
                break
            sheet.paste(cells[i], (cidx * (cw + GAP), y))
        y += hgt + GAP
    _save(sheet, "supplementary-figure-s4.png")


def main() -> None:
    figures_678()
    figure_s3()
    figure_s4()


if __name__ == "__main__":
    main()
