"""Supplementary Figure S3 —— §5 的**结果集与导出**（真实截屏；L-0271：换用 v4 实拍）。

基图 = `v4-export.png`（2026-10-13 05:38 会话展开态**完整窗口**；条目与展开集同框可见）。
图内标识框（Guide 式 callout：圆角矩形 + 数字徽标）：
  ① 结果集条目 + 每集 JSONL/CSV 导出（RESULTS 头 / 时间戳 / 计数 / 运行统计）
  ② By paper / By dataset 口径切换
  ③ 一条已验证记录（登记号 chip / 证据引文 / view record →）
外框：白边 18 px + 3 px 线 + 12 px 圆角（L-0198 口径）。
用法: python scripts/make_figure_s2.py
产出: docs/paper/figures/supplementary-figure-s3.png（同时复制到 final/figures/）
"""
from __future__ import annotations

import shutil
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
EVID = ROOT / "docs" / "paper" / "final" / "evidence"
OUT = ROOT / "docs" / "paper" / "figures"
FINAL = ROOT / "docs" / "paper" / "final" / "figures"

WIDTH = 1740
CARD_PAD = 18
CARD_RADIUS = 12
CARD_BORDER = 3
LINE = (200, 212, 206)

TEAL = (15, 118, 110)
AMBER = (161, 98, 7)

SRC = "v4-export.png"
SRC_SIZE = (3264, 2432)              # 源窗口像素尺寸（@2x；标识框坐标据此定义）

# (x0, y0, x1, y1) 于源坐标；数字徽标压在框左上角
CALLOUTS = [
    ((2440, 178, 3232, 880), "1", TEAL),     # 条目 + 导出 + 计数
    ((2440, 890, 3232, 975), "2", TEAL),     # 口径切换
    ((2440, 1136, 3232, 1965), "3", AMBER),  # 一条已验证记录
]

FONTS = [r"C:\Windows\Fonts\arialbd.ttf", r"C:\Windows\Fonts\arial.ttf"]


def _font(size: int) -> ImageFont.FreeTypeFont:
    for p in FONTS:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def _window(path: Path, width: int = WIDTH) -> Image.Image:
    im = Image.open(path).convert("RGB")
    w, h = im.size
    return im.resize((width, round(h * width / w)), Image.LANCZOS)


def _callouts(img: Image.Image) -> Image.Image:
    """Guide 式标识框：圆角矩形 + 数字徽标（色底白字圆）。"""
    d = ImageDraw.Draw(img)
    sx = img.width / SRC_SIZE[0]
    sy = img.height / SRC_SIZE[1]
    radius = max(8, int(round(12 * sx)))
    line_w = max(3, int(round(4 * sx)))
    br = int(round(17 * sx))                     # 徽标半径
    fs = max(14, int(round(21 * sx)))
    font = _font(fs)
    for (x0, y0, x1, y1), num, color in CALLOUTS:
        box = [x0 * sx, y0 * sy, x1 * sx, y1 * sy]
        d.rounded_rectangle(box, radius=radius, outline=color, width=line_w)
        cx, cy = box[0] + br * 0.15, box[1] + br * 0.15
        d.ellipse([cx - br, cy - br, cx + br, cy + br], fill=color)
        d.text((cx, cy + 1), num, font=font, fill=(255, 255, 255), anchor="mm")
    return img


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
    OUT.mkdir(parents=True, exist_ok=True)
    FINAL.mkdir(parents=True, exist_ok=True)
    img = _callouts(_window(EVID / SRC))
    sheet = _card(img)
    p = OUT / "supplementary-figure-s3.png"
    sheet.save(p)
    shutil.copy2(p, FINAL / p.name)
    print(f"wrote {p}  {sheet.size[0]}x{sheet.size[1]} → 满宽打印高 {174 * sheet.size[1] / sheet.size[0]:.0f} mm")
    # L-0271：S4 恢复为独立图（make_figure_s4.py）；本脚本不再清理。


if __name__ == "__main__":
    main()
