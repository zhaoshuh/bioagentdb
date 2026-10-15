"""Supplementary Figure S4 —— 用户挖掘（Ming → My mining；L-0271，v4 实拍）。

四面板（2×2，均为**完整窗口**，来源 evidence/v4-*.png）：
  (A) Ming 对话 → 提议卡 → "Task started"          v4-ming-start.png
  (B) My mining：任务运行 + 记录（含证据/链接）      v4-mining-live.png
  (C) Topics：改名 / 隐藏（可逆）                    v4-mining-topics.png
  (D) 同一部署的 ledger（从零生长）                  v4-ledger81.png
外框与取法沿用图 6–8 / S3（白边 + 3 px 线 + 12 px 圆角）。
用法: python scripts/make_figure_s4.py
产出: docs/paper/figures/supplementary-figure-s4.png（同步 final/figures/）
"""
from __future__ import annotations

import shutil
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
EVID = ROOT / "docs" / "paper" / "final" / "evidence"
OUT = ROOT / "docs" / "paper" / "figures"
FINAL = ROOT / "docs" / "paper" / "final" / "figures"

CELL_W = 852                   # 每格内容宽（2 列 + 间距 ≈ 1740）
GAP = 18
LABEL_H = 34
CARD_PAD = 12
CARD_RADIUS = 10
CARD_BORDER = 3
LINE = (200, 212, 206)
TEAL = (15, 118, 110)

PANELS = [
    ("v4-ming-start.png", "(A) Ming — one sentence to a running task"),
    ("v4-mining-live.png", "(B) My mining — the task runs and records publish"),
    ("v4-mining-topics.png", "(C) Topics — rename, or hide reversibly"),
    ("v4-ledger81.png", "(D) the same deployment's ledger, grown from zero"),
]

FONTS = [r"C:\Windows\Fonts\arialbd.ttf", r"C:\Windows\Fonts\arial.ttf"]


def _font(size: int) -> ImageFont.FreeTypeFont:
    for p in FONTS:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def _card(img: Image.Image) -> Image.Image:
    w, h = img.size
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1], radius=CARD_RADIUS, fill=255)
    out = Image.new("RGB", (w + 2 * CARD_PAD, h + 2 * CARD_PAD), (255, 255, 255))
    out.paste(img, (CARD_PAD, CARD_PAD), mask)
    ImageDraw.Draw(out).rounded_rectangle(
        [CARD_PAD, CARD_PAD, CARD_PAD + w - 1, CARD_PAD + h - 1],
        radius=CARD_RADIUS, outline=LINE, width=CARD_BORDER)
    return out


def main() -> None:
    cells = []
    for name, label in PANELS:
        im = Image.open(EVID / name).convert("RGB")
        w, h = im.size
        im = im.resize((CELL_W, round(h * CELL_W / w)), Image.LANCZOS)
        card = _card(im)
        cell = Image.new("RGB", (card.width, card.height + LABEL_H), (255, 255, 255))
        cell.paste(card, (0, 0))
        d = ImageDraw.Draw(cell)
        d.text((cell.width / 2, card.height + LABEL_H / 2), label,
               font=_font(21), fill=TEAL, anchor="mm")
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

    OUT.mkdir(parents=True, exist_ok=True)
    FINAL.mkdir(parents=True, exist_ok=True)
    p = OUT / "supplementary-figure-s4.png"
    sheet.save(p)
    shutil.copy2(p, FINAL / p.name)
    print(f"wrote {p}  {sheet.size[0]}x{sheet.size[1]} → 满宽打印高 {174 * sheet.size[1] / sheet.size[0]:.0f} mm")


if __name__ == "__main__":
    main()
