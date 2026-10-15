"""Figure 6 / 7 / 8 —— 三张真机截图，**完整窗口 + 外框**（L-0198：统一 Guide 式取法）。

  Figure 6 = 台账（完整窗口：导航 → 窗口底；卡片行 + 登记号 chip + 证据引文）
  Figure 7 = 一条记录**就地打开**（完整窗口：页面 + 居中弹窗）
  Figure 8 = 真机会话（完整窗口：会话 + 报告 + 结果栏）
来源（L-0271）：evidence/v4-ledger.png / v4-record.png / v4-ask.png（2026-10-13 实拍）。

取法：源截屏**整幅采用**（不做事后裁剪；窗口底的截断属窗口自然边界），等比缩放到
1740 px 宽，外套**可见外框**（白边 18 px + 3 px 线 + 12 px 圆角）。
用法: python scripts/make_figure7.py
产出: docs/paper/figures/{figure6-ledger,figure7-record,figure8-ask}.png（同时复制到 final/figures/）
"""
from __future__ import annotations

import shutil
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
EVID = ROOT / "docs" / "paper" / "final" / "evidence"
OUT = ROOT / "docs" / "paper" / "figures"
FINAL = ROOT / "docs" / "paper" / "final" / "figures"

WIDTH = 1740                 # 174 mm 打印宽
CARD_PAD = 18                # 白边
CARD_RADIUS = 12             # 圆角
CARD_BORDER = 3              # 可见框线宽
LINE = (200, 212, 206)       # #C8D4CE


def _window(path: Path, width: int = WIDTH) -> Image.Image:
    """完整窗口：整幅等比缩放到指定宽度（不裁剪）。"""
    im = Image.open(path).convert("RGB")
    w, h = im.size
    return im.resize((width, round(h * width / w)), Image.LANCZOS)


def _card(img: Image.Image) -> Image.Image:
    """可见外框：圆角遮罩 + 白边 + 3 px 线。"""
    w, h = img.size
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1], radius=CARD_RADIUS, fill=255)
    out = Image.new("RGB", (w + 2 * CARD_PAD, h + 2 * CARD_PAD), (255, 255, 255))
    out.paste(img, (CARD_PAD, CARD_PAD), mask)
    ImageDraw.Draw(out).rounded_rectangle(
        [CARD_PAD, CARD_PAD, CARD_PAD + w - 1, CARD_PAD + h - 1],
        radius=CARD_RADIUS, outline=LINE, width=CARD_BORDER)
    return out


def _save(sheet: Image.Image, name: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    FINAL.mkdir(parents=True, exist_ok=True)
    p = OUT / name
    sheet.save(p)
    shutil.copy2(p, FINAL / name)
    print(f"wrote {p}  {sheet.size[0]}x{sheet.size[1]} → 满宽打印高 {174 * sheet.size[1] / sheet.size[0]:.0f} mm")


def main() -> None:
    _save(_card(_window(EVID / "v4-ledger.png")), "figure6-ledger.png")
    _save(_card(_window(EVID / "v4-record.png")), "figure7-record.png")
    _save(_card(_window(EVID / "v4-ask.png")), "figure8-ask.png")


if __name__ == "__main__":
    main()
