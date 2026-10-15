"""Graphical Abstract（NAR Database Issue 强制）——**纯图标 + 五处部位短标签**（L-0195 / L-0196）。

五部分（每部分一条短标签，除标签外零文字；无 SMIL/动画）：
  ① Papers            论文堆叠页（输入）
  ② Agent             辐条核（LLM 抽取）
  ③ Adversarial reviewers  三名审者同一竖直线（eye=正确性 / lens=完备性 / list=元数据）
  ④ Record            记录卡（登记号标签 / 证据行（琥珀）/ 链接 + 对勾徽标）
  ⑤ Read-only catalog DB 圆柱（样表 + 高亮行）＋锁（只读）；底部虚线回流＝按需再挖

画布 1000×372（与旧版同盒，避免成稿/联系表/导出分页漂移）。
产出：docs/paper/figures/graphical-abstract.svg + .png（3× 渲染）并复制到 final/figures。
用法: python scripts/make_graphical_abstract.py
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import svgstyle as S  # noqa: E402

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
OUT = ROOT / "docs" / "paper" / "figures"
FINAL = ROOT / "docs" / "paper" / "final" / "figures"

LABELS = ["Papers", "Agent", "Adversarial reviewers", "Record", "Read-only catalog"]
AX = 170.0                                   # 主流程轴线 y
CARD_L, CARD_T, CARD_W, CARD_H = 560, 116, 150, 108
DB = (866.0, 170.0, 66.0, 17.0, 108.0)       # cx, cy, rx, ry, height


def check(cx: float, cy: float, *, s: float = 1.0, color: str = "#ffffff",
          sw: float = 3.2) -> str:
    """对勾（徽标/表网格共用）。"""
    return (f'<path d="M{cx-7*s:.1f},{cy:.1f} l{5*s:.1f},{6*s:.1f} l{11*s:.1f},{-13*s:.1f}" '
            f'fill="none" stroke="{color}" stroke-width="{sw}" stroke-linecap="round" '
            f'stroke-linejoin="round"/>')


def glyph(kind: str, cx: float, cy: float, color: str) -> str:
    """审者字形 + 卡片内字形（纯路径，无文字）。"""
    if kind == "eye":                       # 正确性：眼睛
        return (f'<path d="M{cx-12},{cy} C{cx-6},{cy-9} {cx+6},{cy-9} {cx+12},{cy} '
                f'C{cx+6},{cy+9} {cx-6},{cy+9} {cx-12},{cy} Z" fill="none" '
                f'stroke="{color}" stroke-width="2.4" stroke-linejoin="round"/>'
                f'<circle cx="{cx}" cy="{cy}" r="4" fill="{color}"/>')
    if kind == "lens":                      # 完备性：放大镜
        return (f'<circle cx="{cx-2}" cy="{cy-2}" r="8" fill="none" stroke="{color}" '
                f'stroke-width="2.6"/><path d="M{cx+4.4},{cy+4.4} L{cx+11},{cy+11}" '
                f'stroke="{color}" stroke-width="3" stroke-linecap="round"/>')
    if kind == "list":                      # 元数据：字段列表
        out = []
        for dy in (-6, 0, 6):
            out.append(f'<circle cx="{cx-8}" cy="{cy+dy}" r="1.9" fill="{color}"/>')
            out.append(f'<path d="M{cx-4.5},{cy+dy} L{cx+9},{cy+dy}" stroke="{color}" '
                       f'stroke-width="2.3" stroke-linecap="round"/>')
        return "".join(out)
    if kind == "tag":                       # 登记号标签（卡片内）
        return (f'<g transform="rotate(-38 {cx} {cy})">'
                f'<rect x="{cx-8}" y="{cy-6.5}" width="16" height="13" rx="3.5" fill="none" '
                f'stroke="{color}" stroke-width="2.2"/>'
                f'<circle cx="{cx-4.6}" cy="{cy-3.4}" r="1.8" fill="{color}"/></g>')
    if kind == "sheet":                     # 证据行（中行琥珀高亮＝原文引文）
        return (f'<rect x="{cx-7.5}" y="{cy-10}" width="15" height="20" rx="2.5" fill="none" '
                f'stroke="{color}" stroke-width="2.2"/>'
                f'<rect x="{cx-4.5}" y="{cy-5.5}" width="9" height="2.2" rx="1.1" fill="#c8d4ce"/>'
                f'<rect x="{cx-4.5}" y="{cy-1.1}" width="9" height="2.2" rx="1.1" fill="{S.AMBER}"/>'
                f'<rect x="{cx-4.5}" y="{cy+3.3}" width="9" height="2.2" rx="1.1" fill="#c8d4ce"/>')
    if kind == "link":                      # 链接（互扣双环链节）
        return (f'<rect x="{cx-11}" y="{cy-4}" width="12" height="8" rx="4" fill="none" '
                f'stroke="{color}" stroke-width="2.2"/>'
                f'<rect x="{cx-1}" y="{cy-4}" width="12" height="8" rx="4" fill="#f8fbfa" '
                f'stroke="{color}" stroke-width="2.2"/>')
    if kind == "lock":                      # 只读
        return (f'<path d="M{cx-4.5},{cy+0.5} v-4 a4.5,4.5 0 0 1 9,0 v4" fill="none" '
                f'stroke="{color}" stroke-width="2.6" stroke-linecap="round"/>'
                f'<rect x="{cx-7.5}" y="{cy+0.5}" width="15" height="11.5" rx="3" fill="{color}"/>')
    raise ValueError(kind)


def agent_core(cx: float, cy: float, r: float = 46) -> str:
    """agent 核心：glow + 虚线轨道 + 辐条 + 内核（静态，无 SMIL）。"""
    import math
    parts = [f'<circle cx="{cx}" cy="{cy}" r="{r*1.54:.0f}" fill="url(#gGlow)"/>',
             f'<circle cx="{cx}" cy="{cy}" r="{r*1.36:.0f}" fill="none" stroke="{S.TEAL_SOFT}" '
             f'stroke-dasharray="4 7"/>',
             f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="url(#gAgent)" stroke="{S.TEAL_MID}" '
             f'stroke-width="1.8"/>']
    lines, dots = [], []
    for k in range(5):
        a = math.radians(-90 + k * 72)
        sx, sy = cx + r * 0.54 * math.cos(a), cy + r * 0.54 * math.sin(a)
        lines.append(f'<line x1="{cx}" y1="{cy}" x2="{sx:.1f}" y2="{sy:.1f}"/>')
        dots.append(f'<circle cx="{sx:.1f}" cy="{sy:.1f}" r="5.5"/>')
    parts.append(f'<g stroke="{S.TEAL}" stroke-width="1.6" opacity=".6">{"".join(lines)}</g>')
    parts.append(f'<g fill="#ffffff" stroke="{S.TEAL}" stroke-width="1.8">{"".join(dots)}</g>')
    parts.append(f'<circle cx="{cx}" cy="{cy}" r="6.5" fill="{S.TEAL}"/>')
    return "".join(parts)


def reviewer_box(cx: float, cy: float, kind: str, box: float = 58) -> str:
    """审者：白底圆角方块 + 琥珀字形。"""
    x, y = cx - box / 2, cy - box / 2
    return (f'<rect x="{x:.1f}" y="{y:.1f}" width="{box}" height="{box}" rx="15" fill="#ffffff" '
            f'stroke="{S.AMBER_LINE}" stroke-width="1.7"/>' + glyph(kind, cx, cy, S.AMBER))


def record_card(x: float, y: float, w: float, h: float) -> str:
    """记录卡：标题条 + 三枚字形（标签 / 证据 / 链接）+ 对勾徽标。"""
    cx = x + w / 2
    parts = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="#ffffff" '
             f'stroke="{S.BORDER}" stroke-width="1.7"/>',
             f'<rect x="{cx-34}" y="{y+23}" width="68" height="9" rx="4.5" fill="{S.LINE}"/>']
    tile_y = y + 44
    for i, kind in enumerate(("tag", "sheet", "link")):
        tx = cx - 61 + i * 44
        parts.append(f'<rect x="{tx}" y="{tile_y}" width="34" height="34" rx="8" fill="#f8fbfa" '
                     f'stroke="{S.LINE}"/>')
        parts.append(glyph(kind, tx + 17, tile_y + 17, S.TEAL))
    bx, by = x + w - 6, y + 1
    parts.append(f'<circle cx="{bx}" cy="{by}" r="15" fill="{S.TEAL}" stroke="#ffffff" '
                 f'stroke-width="2.5"/>')
    parts.append(check(bx, by, s=0.62, color="#ffffff", sw=3.0))
    return "".join(parts)


def db_cylinder(cx: float, cy: float, rx: float, ry: float, height: float) -> str:
    """DB 圆柱 + 样表网格（末行高亮带勾）+ 锁徽标（只读）。"""
    top, bot = cy - height / 2, cy + height / 2
    parts = [
        f'<path d="M{cx-rx:.0f},{top:.0f} L{cx-rx:.0f},{bot:.0f} A{rx:.0f},{ry:.0f} 0 0 0 '
        f'{cx+rx:.0f},{bot:.0f} L{cx+rx:.0f},{top:.0f} Z" fill="url(#gDb)" stroke="#cfdad5"/>',
        f'<path d="M{cx-rx:.0f},{bot-12:.0f} A{rx:.0f},{ry:.0f} 0 0 0 {cx+rx:.0f},{bot-12:.0f}" '
        f'fill="none" stroke="#dbe4e0"/>',
        f'<ellipse cx="{cx:.0f}" cy="{top:.0f}" rx="{rx:.0f}" ry="{ry:.0f}" fill="#ffffff" '
        f'stroke="{S.BORDER}"/>',
    ]
    gw, gh, gap, cw, ch = 82.0, 52.0, 5.0, 24.0, 14.0
    gx, gy = cx - gw / 2, top + 22
    for r in range(3):
        for c in range(3):
            xx, yy = gx + c * (cw + gap), gy + r * (ch + gap)
            last = r == 2
            parts.append(f'<rect x="{xx:.1f}" y="{yy:.1f}" width="{cw}" height="{ch}" rx="3" '
                         f'fill="{S.TEAL_BG2 if last else "#ffffff"}" '
                         f'stroke="{S.TEAL_PALE if last else S.LINE}"/>')
    parts.append(check(gx + 2 * (cw + gap) + cw / 2, gy + 2 * (ch + gap) + ch / 2,
                       s=0.55, color=S.TEAL, sw=2.4))
    lx, ly = cx + rx - 8, bot - 4
    parts.append(f'<circle cx="{lx:.0f}" cy="{ly:.0f}" r="16" fill="#ffffff" '
                 f'stroke="{S.BORDER}"/>')
    parts.append(glyph("lock", lx, ly - 1.5, S.TEAL))
    return "".join(parts)


def build_svg() -> str:
    w, h = 1000, 372
    p = [S.svg_open(w, h), S.defs()]

    # ① 论文堆叠页
    p.append(S.papers(66, 84))

    # ② 论文 → agent
    p.append(S.flow(196, AX, 232, AX, fid="ga1", token=False))

    # ③ agent 核心
    p.append(agent_core(292, AX, 46))

    # ④ agent →(扇出)→ 三名审者（同一竖直线 cx=466）
    p.append(S.flow(360, 140, 431, 110, fid="ga2", token=False))     # 正确性（眼）
    p.append(S.flow(364, AX, 431, AX, fid="ga3", token=False))       # 完备性（放大镜）
    p.append(S.flow(360, 200, 431, 230, fid="ga4", token=False))     # 元数据（列表）
    p.append(reviewer_box(466, 94, "eye"))
    p.append(reviewer_box(466, AX, "lens"))
    p.append(reviewer_box(466, 246, "list"))

    # ⑤ 三路 →(收敛)→ 记录卡
    p.append(S.flow(501, 102, 556, 144, fid="ga5", token=False))
    p.append(S.flow(501, AX, 556, AX, fid="ga6", token=False))
    p.append(S.flow(501, 238, 556, 196, fid="ga7", token=False))
    p.append(record_card(CARD_L, CARD_T, CARD_W, CARD_H))

    # ⑥ 记录 → DB
    p.append(S.flow(716, AX, 754, AX, fid="ga8", token=False))
    p.append(db_cylinder(*DB))

    # ⑦ 回流（按需再挖）——下移至标签行之下
    p.append(f'<path d="M866,246 C700,352 400,358 138,268" fill="none" stroke="{S.ARROW}" '
             f'stroke-width="2.2" stroke-dasharray="7 7" marker-end="url(#arrow)"/>')

    # ⑧ 五处部位短标签（唯一允许的文字）
    for lx, text in ((111, LABELS[0]), (292, LABELS[1]), (466, LABELS[2]),
                     (635, LABELS[3]), (866, LABELS[4])):
        p.append(f'<text x="{lx}" y="298" font-size="17" font-weight="600" '
                 f'fill="{S.TEAL}" text-anchor="middle">{text}</text>')

    p.append("</svg>")
    return "".join(p)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    FINAL.mkdir(parents=True, exist_ok=True)
    svg = build_svg()
    # 自检：只有五处部位标签、零 SMIL（L-0196 口径）
    labels = re.findall(r"<text[^>]*>([^<]*)</text>", svg)
    assert labels == LABELS, f"unexpected labels: {labels}"
    assert svg.count("<text") == 5, "only the five part labels are allowed"
    assert "<animate" not in svg, "GA must contain no SMIL/animation"
    out_svg = OUT / "graphical-abstract.svg"
    out_svg.write_text(svg, encoding="utf-8")
    html = ("<!DOCTYPE html><html><head><meta charset='utf-8'><style>"
            "body{margin:0;padding:0;background:#fff}</style></head><body>"
            + svg + "</body></html>")
    tmp = Path(str(out_svg)[:-4] + ".html")
    tmp.write_text(html, encoding="utf-8")
    out_png = OUT / "graphical-abstract.png"
    rc = subprocess.run([EDGE, "--headless", "--disable-gpu", "--hide-scrollbars",
                         "--force-device-scale-factor=3", "--window-size=1000,372",
                         f"--screenshot={out_png}", tmp.as_uri()], capture_output=True).returncode
    print(f"wrote {out_svg}")
    print(f"wrote {out_png} rc={rc} size={out_png.stat().st_size if out_png.exists() else 0}")
    print(f"self-check: labels={len(labels)}, smil=0")
    for f in ("graphical-abstract.svg", "graphical-abstract.png"):
        shutil.copy2(OUT / f, FINAL / f)


if __name__ == "__main__":
    main()
