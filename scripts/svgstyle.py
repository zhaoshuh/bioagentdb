"""论文图共享样式库 —— 与首页插画同一绘制技术。

调色板 / 渐变 / 圆角节点 / 曲线流动箭头 / 脉冲 agent 核心 / 软底色，全部取自
`web/index.html` 的首页插画（2026-10 提取）。

组件:
  defs()            渐变 + 箭头 marker
  papers()          论文堆叠卡
  agent_core()      脉冲核心（orbit + 辐条 + 内核）
  reviewers()       三审查者（琥珀勾）
  db_cylinder()     数据库圆柱
  flow()            流动箭头（可选标签/token 动画）
  stage_label()     节点主/副标签
  hbar()            横向条形图（>5 类，禁止旋转标签）
  vbar()            纵向条形图（≤5 类）
  line_chart()      折线
  collision_check() 标签碰撞检查（供 QA 断言）
"""

from __future__ import annotations

# ---------------------------------------------------------------- palette
TEAL = "#0f766e"
TEAL_MID = "#5aa79a"
TEAL_SOFT = "#a8d3c9"
TEAL_PALE = "#cfe4dd"
TEAL_BG = "#eaf4f1"
TEAL_BG2 = "#d3e7e1"
AMBER = "#a16207"
AMBER_LINE = "#e3ded0"
INK = "#111827"
GREY = "#64748b"
PAPER = "#f2f6f4"
LINE = "#e6ece8"
BORDER = "#c8d4ce"
ARROW = "#7fb3a8"
GRID = "#e2e8f0"

FONT = "Arial, Helvetica, sans-serif"


def svg_open(w: int, h: int, fig_id: str = "", y0: int = 0) -> str:
    vh = h - y0
    tag = (f'<svg width="{w}" height="{vh}" viewBox="0 {y0} {w} {vh}" '
           f'xmlns="http://www.w3.org/2000/svg" font-family="{FONT}">')
    if fig_id:                      # 面板内不再写图号标题，图号以注释保留（工具/审查可读）
        tag += f"<!-- figure: {fig_id} -->"
    return tag


def defs() -> str:
    return (
        '<defs>'
        '<linearGradient id="gSheet" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0" stop-color="#ffffff"/><stop offset="1" stop-color="#f3f7f5"/>'
        '</linearGradient>'
        '<linearGradient id="gAgent" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0" stop-color="#eaf4f1"/><stop offset="1" stop-color="#d3e7e1"/>'
        '</linearGradient>'
        '<radialGradient id="gGlow" cx="50%" cy="50%" r="50%">'
        '<stop offset="0" stop-color="#0f766e" stop-opacity=".20"/>'
        '<stop offset="1" stop-color="#0f766e" stop-opacity="0"/>'
        '</radialGradient>'
        '<linearGradient id="gDb" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0" stop-color="#f8fbf9"/><stop offset="1" stop-color="#eef4f1"/>'
        '</linearGradient>'
        '<marker id="arrow" viewBox="0 0 10 10" refX="7.5" refY="5" markerWidth="7" '
        'markerHeight="7" orient="auto"><path d="M0,1 L9,5 L0,9 z" fill="#7fb3a8"/></marker>'
        '</defs>')


# ---------------------------------------------------------------- nodes
def papers(x: float, y: float, w: float = 118, h: float = 126) -> str:
    """论文堆叠卡（三张 + 文本行），(x,y) 为左下前卡左上角。"""
    parts = []
    for i, (dx, dy, fill, stroke) in enumerate((
            (-28, 24, PAPER, "#dfe7e3"), (-14, 12, "url(#gSheet)", "#d5dfda"),
            (0, 0, "#ffffff", BORDER))):
        parts.append(f'<rect x="{x+dx}" y="{y+dy}" width="{w}" height="{h}" rx="9" '
                     f'fill="{fill}" stroke="{stroke}"/>')
    bx = x + 16
    for i, (by, bw, bc) in enumerate((
            (24, 86, LINE), (42, 70, LINE), (60, 86, TEAL_PALE),
            (78, 78, LINE), (96, 58, LINE))):
        parts.append(f'<rect x="{bx}" y="{y+by}" width="{bw}" height="6" rx="3" fill="{bc}"/>')
    return "".join(parts)


def agent_core(cx: float, cy: float, r: float = 56, animate: bool = True) -> str:
    """脉冲 agent 核心：glow + 虚线 orbit + 辐条 + 内核。"""
    spokes = []
    import math
    for k in range(5):
        a = math.radians(-90 + k * 72)
        sx, sy = cx + r * 0.54 * math.cos(a), cy + r * 0.54 * math.sin(a)
        spokes.append((sx, sy))
    parts = [f'<circle cx="{cx}" cy="{cy}" r="{r*1.54:.0f}" fill="url(#gGlow)"/>']
    orbit = (f'<circle cx="{cx}" cy="{cy}" r="{r*1.36:.0f}" fill="none" stroke="{TEAL_SOFT}" '
             f'stroke-dasharray="4 7"/>')
    if animate:
        orbit = (f'<g><animateTransform attributeName="transform" type="rotate" '
                 f'from="0 {cx} {cy}" to="360 {cx} {cy}" dur="40s" repeatCount="indefinite"/>'
                 f'{orbit}</g>')
    parts.append(orbit)
    parts.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="url(#gAgent)" stroke="{TEAL_MID}" '
                 f'stroke-width="1.8"/>')
    lines = "".join(f'<line x1="{cx}" y1="{cy}" x2="{sx:.1f}" y2="{sy:.1f}"/>' for sx, sy in spokes)
    parts.append(f'<g stroke="{TEAL}" stroke-width="1.6" opacity=".6">{lines}</g>')
    dots = "".join(f'<circle cx="{sx:.1f}" cy="{sy:.1f}" r="5.5"/>' for sx, sy in spokes)
    parts.append(f'<g fill="#ffffff" stroke="{TEAL}" stroke-width="1.8">{dots}</g>')
    parts.append(f'<circle cx="{cx}" cy="{cy}" r="{r*0.14:.0f}" fill="{TEAL}">'
                 f'<animate attributeName="r" values="{r*0.12:.0f};{r*0.18:.0f};{r*0.12:.0f}" '
                 f'dur="2.6s" repeatCount="indefinite"/></circle>')
    return "".join(parts)


def reviewers(x: float, y: float, box: float = 54, gap: float = 10) -> str:
    """三审查者：三个圆角方块 + 琥珀勾。"""
    parts = []
    for i in range(3):
        bx = x + i * (box + gap)
        parts.append(f'<rect x="{bx}" y="{y}" width="{box}" height="{box}" rx="14" '
                     f'fill="#ffffff" stroke="{AMBER_LINE}"/>')
        cx, cy = bx + box / 2, y + box / 2
        parts.append(f'<path d="M{cx-9},{cy} l7,8 l13,-16" fill="none" stroke="{AMBER}" '
                     f'stroke-width="2.8" stroke-linecap="round" stroke-linejoin="round"/>')
    return "".join(parts)


def db_cylinder(cx: float, cy: float, rx: float = 86, ry: float = 22,
                height: float = 92) -> str:
    """数据库圆柱（首页同款）。"""
    top = cy - height / 2
    bottom = cy + height / 2
    left, right = cx - rx, cx + rx
    body = (f'M{left},{top} L{left},{bottom} A{rx},{ry} 0 0 0 {right},{bottom} '
            f'L{right},{top} Z')
    parts = [f'<path d="{body}" fill="url(#gDb)" stroke="#cfdad5"/>']
    for frac in (0.42, 0.72):
        yy = top + height * frac
        parts.append(f'<path d="M{left},{yy} A{rx},{ry} 0 0 0 {right},{yy}" fill="none" '
                     f'stroke="#dbe4e0"/>')
    parts.append(f'<ellipse cx="{cx}" cy="{top}" rx="{rx}" ry="{ry}" fill="#ffffff" '
                 f'stroke="{BORDER}"/>')
    return "".join(parts)


def _split_title(title: str) -> tuple[str, str, str]:
    """把 "Figure 9A · Record-level …" 解析为 ("", "(A)", "9A")。

    面板内不再重复图注标题；图号以徽标 + `<!-- figure: … -->` 注释承载。
    非该形式的标题原样返回（供无字母的自定义标题使用）。
    """
    import re as _re
    m = _re.match(r"^Figure\s+(S?\d+[A-I]?)\s*·\s*(.*)$", title or "")
    if not m:
        return title, "", ""
    fid, _rest = m.group(1), m.group(2)
    m2 = _re.search(r"([A-I])$", fid)
    return "", (f"({m2.group(1)})" if m2 else ""), fid


def badge(x: float, y: float, text: str, *, fs: float = 17, fill: str = TEAL_BG,
          stroke: str = TEAL_PALE, color: str = TEAL) -> str:
    """面板徽标（(A)/(A–C)）——取代面板内图号+标题，标题由图注承载。"""
    w = 14 + 0.62 * fs * len(text)
    return (f'<rect x="{x:.0f}" y="{y - fs:.0f}" width="{w:.0f}" height="{fs + 10:.0f}" rx="6" '
            f'fill="{fill}" stroke="{stroke}"/>'
            f'<text x="{x + 7:.0f}" y="{y + 2:.0f}" font-size="{fs}" font-weight="700" '
            f'fill="{color}">{text}</text>')


def card(x: float, y: float, w: float, h: float, title: str = "", lines=(), *,
         fill: str = "#ffffff", stroke: str = BORDER, rx: float = 12,
         title_fs: float = 17.5, body_fs: float = 14, title_fill: str = INK,
         body_fill: str = GREY, title_dy: float = 28, line_h: float = 20) -> str:
    """软底卡片（与首页插画同款圆角 + 描边），供示意图复用，保证风格一致。"""
    parts = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" '
             f'stroke="{stroke}"/>']
    if title:
        parts.append(f'<text x="{x + 16}" y="{y + title_dy}" font-size="{title_fs}" '
                     f'font-weight="700" fill="{title_fill}">{title}</text>')
    for i, ln in enumerate(lines):
        parts.append(f'<text x="{x + 16}" y="{y + title_dy + (i + 1) * line_h}" '
                     f'font-size="{body_fs}" fill="{body_fill}">{ln}</text>')
    return "".join(parts)


def chip(x: float, y: float, text: str, *, fs: float = 14, pad: float = 14,
         fill: str = TEAL_BG, stroke: str = TEAL_PALE, color: str = TEAL) -> str:
    """圆角标签（登记号 chip 同款）。"""
    w = 2 * pad + 0.58 * fs * len(text)
    return (f'<rect x="{x}" y="{y}" width="{w:.0f}" height="{fs + 12}" rx="{(fs + 12) / 2:.0f}" '
            f'fill="{fill}" stroke="{stroke}"/>'
            f'<text x="{x + pad:.0f}" y="{y + fs + 4}" font-size="{fs}" fill="{color}">{text}</text>')


def gate(cx: float, cy: float, label: str, *, w: float = 120, h: float = 46,
         fill: str = "#fdf6e3", stroke: str = AMBER_LINE, color: str = AMBER,
         fs: float = 15) -> str:
    """机械门（琥珀）——"由确定性代码判定"的视觉符号。"""
    return (f'<rect x="{cx - w / 2:.0f}" y="{cy - h / 2:.0f}" width="{w:.0f}" height="{h:.0f}" '
            f'rx="10" fill="{fill}" stroke="{stroke}"/>'
            f'<text x="{cx:.0f}" y="{cy + fs * 0.36:.0f}" font-size="{fs}" font-weight="700" '
            f'fill="{color}" text-anchor="middle">{label}</text>')


def flow(x1: float, y1: float, x2: float, y2: float, *, curve: float = 0,
         label: str = "", label_dy: float = -12, label_dx: float = 0, token: bool = True,
         fid: str = "") -> str:
    """流动箭头（可带标签与 animateMotion token）。"""
    if curve:
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2 - curve
        d = f"M{x1},{y1} Q{mx},{my} {x2},{y2}"
    else:
        d = f"M{x1},{y1} L{x2},{y2}"
    parts = [f'<path {"id=" + chr(34) + fid + chr(34) if fid else ""} class="flow" d="{d}" '
             f'fill="none" stroke="{ARROW}" stroke-width="2" marker-end="url(#arrow)"/>']
    if label:
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        # 曲线流箭头：标签落在**曲线**上（t=0.5 的二次贝塞尔点），再按 label_dy 垂直偏移，
        # 否则标签会压在鼓起的弧线上（Figure 2/3 曾如此）
        ly = my - curve / 2 if curve else my
        parts.append(f'<text x="{mx + label_dx}" y="{ly + label_dy}" font-size="16" '
                     f'fill="{GREY}" text-anchor="middle">{label}</text>')
    if token and fid:
        parts.append(f'<circle r="3.8" fill="{TEAL}" opacity=".85">'
                     f'<animateMotion dur="2.6s" repeatCount="indefinite">'
                     f'<mpath href="#{fid}"/></animateMotion></circle>')
    return "".join(parts)


def stage_label(cx: float, y: float, main: str, sub: str = "") -> str:
    parts = [f'<text x="{cx}" y="{y}" font-size="17.5" font-weight="700" fill="{INK}" '
             f'text-anchor="middle">{main}</text>']
    if sub:
        parts.append(f'<text x="{cx}" y="{y+18}" font-size="16" fill="{GREY}" '
                     f'text-anchor="middle">{sub}</text>')
    return "".join(parts)


# ---------------------------------------------------------------- charts
def _tw(text: str, fs: float) -> float:
    """粗略文本宽度估计（用于碰撞检查与标签列宽）。"""
    return 0.62 * fs * len(text)


def collision_check(rows: list[tuple[str, float]], plot_w: float, fs: float = 12,
                    min_gap: float = 6) -> list[str]:
    """横向条形标签碰撞检查：返回冲突描述（空 = 通过）。"""
    issues = []
    max_label = max((_tw(lbl, fs) for lbl, _ in rows), default=0)
    if max_label > plot_w * 0.38:
        issues.append(f"label column too wide: {max_label:.0f}px > {plot_w*0.38:.0f}px")
    return issues


def hbar(title: str, items: list[tuple[str, float]], *, w: int = 640, color=TEAL,
         value_fmt=str, fs_lab: float = 18, row_h: float = 26,
         legend: list[tuple[str, str]] | None = None, h: int | None = None,
         badge_text: str = "", fig_id: str = "") -> str:
    """横向条形图（>5 类专用，标签在左、值在右，零旋转）。

    给定 h 时**行距均分填满高度**（用于同一行内多面板等高）；无标题时自动去掉标题带。
    """
    title, _b, _fid = _split_title(title)
    badge_text = badge_text or _b
    fig_id = fig_id or _fid
    pad_l = 150 + max(0.0, fs_lab - 15) * 10
    pad_r, pad_b = 56, 18
    pad_t = 58 if legend else 44
    n = max(1, len(items))
    legend_h = 24 if legend else 0
    title_band = 30 if title else 0
    head = title_band if title else (26 if badge_text else 0)
    h_decl = int(h) if h else int(pad_t + n * row_h + pad_b + legend_h + 30)
    h_out = int(h_decl - 30 + head)
    row_h = (h_out - pad_t - pad_b - legend_h) / n          # 均分填满
    plot_w = w - pad_l - pad_r
    vmax = max([v for _, v in items] + [1]) * 1.06
    colors = color if isinstance(color, list) else [color] * n
    parts = [svg_open(w, h_out, fig_id), defs()]
    if title:
        parts.append(f'<text x="14" y="26" font-size="18" font-weight="700" fill="{INK}">{title}</text>')
    if badge_text:
        parts.append(badge(10, 20, badge_text))
    if legend:
        for j2, (lbl, col) in enumerate(legend):
            lx = 14 + j2 * 118
            parts.append(f'<rect x="{lx}" y="52" width="10" height="10" rx="2" fill="{col}"/>')
            parts.append(f'<text x="{lx+14}" y="61" font-size="18" fill="{INK}">{lbl}</text>')
    y0 = pad_t + legend_h
    for i2, (label, val) in enumerate(items):
        y = y0 + i2 * row_h
        bh = min(row_h * 0.62, 34)
        bw = plot_w * val / vmax
        parts.append(f'<text x="{pad_l-10}" y="{y+bh*0.78:.0f}" font-size="{fs_lab}" '
                     f'fill="{INK}" text-anchor="end">{label}</text>')
        parts.append(f'<rect x="{pad_l}" y="{y:.1f}" width="{plot_w}" height="{bh:.1f}" rx="4" '
                     f'fill="{LINE}"/>')
        parts.append(f'<rect x="{pad_l}" y="{y:.1f}" width="{bw:.1f}" height="{bh:.1f}" rx="4" '
                     f'fill="{colors[i2 % len(colors)]}"/>')
        parts.append(f'<text x="{pad_l+bw+8:.0f}" y="{y+bh*0.78:.0f}" font-size="{fs_lab}" '
                     f'fill="{INK}">{value_fmt(val)}</text>')
    parts.append("</svg>")
    return "".join(parts)


def vbar(title: str, items: list[tuple[str, float]], *, w: int = 640, h: int = 280,
         color=TEAL, pct: bool = False, fs_val: float = 20, value_fmt=str,
         badge_text: str = "", fig_id: str = "") -> str:
    """纵向条形图（≤5 类）。**值标签一律在柱上方**：轴上限取 nice×1.30 留出顶部空间。
    无标题时（title=""）自动去掉标题带并整体上移；badge_text 画左上角徽标。"""
    title, _b, _fid = _split_title(title)
    badge_text = badge_text or _b
    fig_id = fig_id or _fid
    title_band = 30 if title else 0
    head = title_band if title else (26 if badge_text else 0)
    h_out = int(h - 30 + head)
    pad_l, pad_b, pad_t = 54, 64, 64 - 30 + head
    plot_w, plot_h = w - pad_l - 24, h_out - pad_t - pad_b
    vmax = max([v for _, v in items] + [1])
    nice = 100.0 if (pct and vmax <= 100) else _nice(vmax * 1.15)
    axis = nice * 1.30                       # 顶部 30% 头高：柱值标签落在头高区（网格线只画 0%/50%）
    n = max(1, len(items))
    gap = plot_w / n
    bw = gap * 0.5
    parts = [svg_open(w, h_out, fig_id), defs()]
    if title:
        parts.append(f'<text x="14" y="26" font-size="18" font-weight="700" fill="{INK}">{title}</text>')
    if badge_text:
        parts.append(badge(10, 20, badge_text))
    for frac in (0.0, 0.5):          # 只画 0% 与 50%；柱值由标签给出，避免标签与顶线相压
        yy = pad_t + plot_h - plot_h * (nice * frac) / axis
        parts.append(f'<line x1="{pad_l}" y1="{yy:.1f}" x2="{w-24}" y2="{yy:.1f}" stroke="{GRID}"/>')
        parts.append(f'<text x="{pad_l-6}" y="{yy+4:.1f}" font-size="18" fill="{GREY}" '
                     f'text-anchor="end">{nice*frac:,.6g}{"% " if pct else ""}</text>')
    for i, (label, val) in enumerate(items):
        bh = plot_h * val / axis
        x = pad_l + gap * i + (gap - bw) / 2
        y = pad_t + plot_h - bh
        parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{bh:.1f}" rx="4" '
                     f'fill="{color}"/>')
        ly = y - 18
        line50 = pad_t + plot_h - plot_h * (nice * 0.5) / axis   # 50% 刻度线
        if ly + 0.22 * fs_val > line50 - 6 and ly - 0.78 * fs_val < line50 + 6:
            ly = line50 - 10.4                                    # 会压 50% 线 → 抬到线上方 ≥6 px
        parts.append(f'<text x="{x+bw/2:.1f}" y="{ly:.1f}" font-size="{fs_val}" '
                     f'fill="{INK}" text-anchor="middle">{value_fmt(val)}{"% " if pct else ""}</text>')
        parts.append(f'<text x="{x+bw/2:.1f}" y="{pad_t+plot_h+22:.1f}" font-size="18" '
                     f'fill="{GREY}" text-anchor="middle">{label}</text>')
    parts.append("</svg>")
    return "".join(parts)


def line_chart(title: str, xlabels: list[str], series, *, w: int = 640, h: int = 320,
               legend: bool = True, label_fs: float = 17, badge_text: str = "",
               fig_id: str = "", y_min: float = 0.0) -> str:
    title, _b, _fid = _split_title(title)
    badge_text = badge_text or _b
    fig_id = fig_id or _fid
    title_band = 30 if title else 0
    head = title_band if title else (26 if badge_text else 0)
    h_out = int(h - 30 + head)
    pad_l, pad_b, pad_t = 54, 76, 96 - 30 + head
    pw, ph = w - pad_l - 24, h_out - pad_t - pad_b
    n = max(1, len(xlabels))

    def X(i: int) -> float:
        return pad_l + 16 + ((pw - 16) * (i / (n - 1)) if n > 1 else (pw - 16) / 2)

    _mx = max([v for _, vals, _ in series for v in vals] + [1])
    # 高值系列留出顶部空间（标签基线在点上方 11 px、墨高约 13.3 px → 需 24.3 px 余量）
    if y_min > 0:
        _scale = 100.0                     # 抬高纵轴起点（截断轴，题注须注明）
    else:
        _scale = 100.0 if _mx <= 88 else min(150.0, max(115.0, _mx / (1.0 - 24.3 / max(ph, 1.0))))

    def Y(v: float) -> float:
        return pad_t + ph - ph * ((max(y_min, min(_scale, v)) - y_min) / max(_scale - y_min, 1e-6))

    parts = [svg_open(w, h_out, fig_id), defs()]
    if title:
        parts.append(f'<text x="14" y="26" font-size="18" font-weight="700" fill="{INK}">{title}</text>')
    if badge_text:
        parts.append(badge(10, 20, badge_text))
    ticks = ([0, 50, 100] if y_min <= 0 else list(range(int(y_min), 100, 20)))   # 抬高轴时不出 100% 线（端点标签自带量值）
    for tick in ticks:
        yy = Y(tick)
        parts.append(f'<line x1="{pad_l}" y1="{yy:.1f}" x2="{w-24}" y2="{yy:.1f}" stroke="{GRID}"/>')
        parts.append(f'<text x="{pad_l-6}" y="{yy+4:.1f}" font-size="17" fill="{GREY}" '
                     f'text-anchor="end">{tick}%</text>')
    for i, xl in enumerate(xlabels):
        parts.append(f'<text x="{X(i):.1f}" y="{pad_t+ph+20:.1f}" font-size="17" fill="{GREY}" '
                     f'text-anchor="middle">{xl}</text>')
    for si, (name, vals, color) in enumerate(series):
        pts = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(vals))
        parts.append(f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="2.4"/>')
        for i, v in enumerate(vals):
            parts.append(f'<circle cx="{X(i):.1f}" cy="{Y(v):.1f}" r="3.2" fill="{color}"/>')
        # 只在首末点标注数值，并按系列错开纵向偏移（端点标签与折线不重叠，无需描边）
        for k, i in enumerate((0, len(vals) - 1)):
            if not vals:
                continue
            v = vals[i]
            # 抬高纵轴（y_min>0）时系列间距已足够 → 标签统一置于点上方，避免压线/互叠
            dy = -13 if y_min > 0 else -11 + 13 * si    # 抬高轴时统一在点上方（顶网格线已不画）
            parts.append(f'<text x="{X(i):.1f}" y="{Y(v)+dy:.1f}" font-size="{label_fs}" fill="{color}" '
                         f'text-anchor="middle">{int(v + 0.5)}</text>')
    if legend:
        for j, (name, _, color) in enumerate(series):
            lx = pad_l + j * 128
            parts.append(f'<rect x="{lx}" y="{pad_t-56}" width="11" height="11" rx="2" fill="{color}"/>')
            parts.append(f'<text x="{lx+16}" y="{pad_t-44}" font-size="17" fill="{INK}">{name}</text>')
    parts.append("</svg>")
    return "".join(parts)


def _nice(x: float) -> float:
    import math
    if x <= 0:
        return 1.0
    m = 10 ** math.floor(math.log10(x))
    for k in (1, 1.5, 2, 2.5, 3, 4, 5, 6, 7, 8, 9, 10):
        if k * m >= x - 1e-9:
            return k * m
    return 10 * m
