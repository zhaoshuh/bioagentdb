"""补充图面板：S1 = 机制（panelS1A–C，3 面板）· S2 = 覆盖（panelS2A–D，4 面板）· S3 = 结果集（栅格，另见 make_figure_s2.py）。
用法: python scripts/make_figures_supp.py"""
from __future__ import annotations
import json, sqlite3, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import svgstyle as S
OUT = ROOT / "docs" / "paper" / "figures"
FINAL = ROOT / "docs" / "paper" / "final" / "figures"
import os  # noqa: E402

DB = Path(os.environ.get("BIOAGENTDB_EVAL_DB")
          or ROOT / "data" / "releases" / "_v15" / "bioagentdb.sqlite3")


def _title(text: str) -> str:
    return f'<text x="14" y="26" font-size="20" font-weight="700" fill="{S.INK}">{text}</text>'


def panel_s1a() -> str:
    """Figure S1A · 回归门控的自我改进（机制示意）。"""
    w, h = 1060, 175
    p = [S.svg_open(w, h, fig_id="S1A"), S.defs(),
         S.badge(10, 20, "(A)")]
    p += [S.card(10, 70, 175, 96, "errors", ["agent mines its", "own mistakes"]),
          S.flow(185, 118, 230, 118, token=False),
          S.card(230, 70, 175, 96, "proposals", ["repository patterns"]),
          S.flow(405, 118, 450, 118, token=False),
          S.card(450, 70, 175, 96, "dev split", ["the gate acts here"]),
          S.flow(625, 118, 655, 118, token=False),
          S.gate(715, 118, "gate"),
          S.flow(775, 118, 805, 118, token=False),
          S.card(805, 70, 175, 96, "held-out test", ["reported after", "the dev gate"])]
    p.append("</svg>")
    return "".join(p)


def panel_s1c() -> str:
    """Figure S1C · 论文锚定的归一化（raw–evidence–canonical 三元组）。"""
    w, h = 1060, 175
    p = [S.svg_open(w, h, fig_id="S1C"), S.defs(),
         S.badge(10, 20, "(C)")]
    p += [S.card(10, 70, 300, 100, "raw value", ["“Homo sapiens (human)”"]),
          S.flow(310, 120, 370, 120, label="map", label_dy=-12, token=False),
          S.card(370, 70, 330, 100, "evidence quote",
                 ["the sentence the mapping", "is anchored to"]),
          S.flow(700, 120, 800, 120, label="canonical", label_dy=-12, token=False),
          S.card(800, 70, 250, 100, "canonical value", ["“Homo sapiens”", "norm_v2"])]
    p.append("</svg>")
    return "".join(p)


def panel_s1b() -> str:
    """Figure S1B · 活的、绑定发布的资源（版本带 + 按需扩展）。"""
    w, h = 1060, 165
    p = [S.svg_open(w, h, fig_id="S1B"), S.defs(),
         S.badge(10, 20, "(B)")]
    xs = 30.0
    for i, v in enumerate(("1", "2", "3", "4", "5")):
        p.append(S.chip(xs, 52, v, fs=15, fill=S.TEAL_BG if i < 4 else "#d1fae5"))
        xs += 150
    p += [S.card(10, 88, 250, 66, "new theme", ["name + aliases + modality"]),
          S.flow(260, 121, 310, 121, token=False),
          S.card(310, 88, 250, 66, "query facets", ["generated on demand"]),
          S.flow(560, 121, 610, 121, token=False),
          S.card(610, 88, 250, 66, "mined", ["same pipeline + gates"]),
          S.flow(860, 121, 910, 121, token=False),
          S.card(910, 88, 140, 66, "catalog", ["keeps growing"])]
    p.append("</svg>")
    return "".join(p)


def main() -> None:
    ev = json.loads((ROOT / "data" / "gold" / "eval-report.json").read_text(encoding="utf-8"))
    evc_path = ROOT / "data" / "gold" / "catalog-view" / "eval-report.json"
    evc = (json.loads(evc_path.read_text(encoding="utf-8")) if evc_path.exists() else ev)
    resc = json.loads((ROOT / "data" / "gold" / "s1-rescan-report.json").read_text(encoding="utf-8"))
    rem = json.loads((ROOT / "data" / "gold" / "s1-remine-report.json").read_text(encoding="utf-8"))
    stats = json.loads((ROOT / "docs" / "versions" / "v1.5-stats" / "resource-stats.json").read_text(encoding="utf-8"))
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    n_total = conn.execute("SELECT COUNT(*) FROM record_normalization").fetchone()[0]
    n_quote = conn.execute("SELECT COUNT(*) FROM record_normalization WHERE quote IS NOT NULL AND quote != ''").fetchone()[0]
    n_multi = conn.execute("SELECT COUNT(*) FROM (SELECT record_id, field FROM record_normalization GROUP BY record_id, field HAVING COUNT(*)>1)").fetchone()[0]
    conn.close()
    pl = evc.get("paper_level") or {}
    panels = {
        "panelS2C": S.vbar("Figure S2C · Paper-level P/R (findable data)",
                          [("precision", round(pl.get("precision", 0) * 100)),
                           ("recall", round(pl.get("recall_on_findable", 0) * 100))], pct=True, h=302),
        "panelS2D": S.vbar("Figure S2D · Recall gain vs precision cost",
                          [("pipeline R", round(100 * (resc["results"]["pipeline_only"].get("recall") or 0))),
                           ("union R", round(100 * (resc["results"]["union(pipe+scan)"].get("recall") or 0))),
                           ("full-text P", round(100 * (rem.get("precision") or 0)))],
                          pct=True, color=S.AMBER, h=302),
        "panelS2B": S.vbar("Figure S2B · Normalization coverage",
                          [("mapped", 100), ("quoted", round(100 * n_quote / max(n_total, 1))),
                           ("multi-value", round(100 * n_multi / max(n_total, 1)))],
                          pct=True, color=S.TEAL_MID, h=302),
        "panelS2A": S.hbar("Figure S2A · Repository distribution (top 10)",
                          [(k, v) for k, v in sorted((stats.get("repositories") or {}).items(),
                                                     key=lambda kv: -kv[1])[:10]], h=302),
        # Supplementary Figure S1（L-0161：机制类创新；字母按正文引用序）
        "panelS1A": panel_s1a(),
        "panelS1C": panel_s1c(),
        "panelS1B": panel_s1b(),
    }
    for name, svg in panels.items():
        (OUT / f"{name}.svg").write_text(svg, encoding="utf-8")
        (FINAL / f"{name}.svg").write_text(svg, encoding="utf-8")
        print("wrote", name)
if __name__ == "__main__":
    main()
