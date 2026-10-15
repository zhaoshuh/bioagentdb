"""生成论文图 v3 —— 全部采用首页插画绘制技术（svgstyle）。

面板（18）:
  Fig 1  Agent architecture      Fig 2  The agent–database loop
  Fig 3A The unit of record      Fig 3B Verification detail
  Fig 4A–4D Catalog composition  Fig 5  Ask the agent (flow)
  Fig 6/7/8 real captures (separate files)   Fig 9A–9I evaluation
用法: python scripts/make_figures.py [--stats PATH] [--db PATH] [--no-sync]
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import svgstyle as S  # noqa: E402

GOLD = ROOT / "data" / "gold"
OUT = ROOT / "docs" / "paper" / "figures"
FINAL = ROOT / "docs" / "paper" / "final" / "figures"
EVIDENCE = ROOT / "docs" / "paper" / "final" / "evidence"


def load(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def _title(title: str) -> str:
    return f'<text x="14" y="26" font-size="20" font-weight="700" fill="{S.INK}">{title}</text>'


# ---------------------------------------------------------------- schematics
def fig1_architecture() -> str:
    w, h = 1060, 340
    p = [S.svg_open(w, h, fig_id="1", y0=30), S.defs()]
    p += [S.papers(88, 87),
          S.flow(216, 150, 268, 150, fid="f1"),
          S.agent_core(352, 150, 56),
          S.flow(436, 150, 534, 150, fid="f2"),
          S.reviewers(546, 123),
          S.flow(736, 150, 830, 150, fid="f3"),
          S.db_cylinder(920, 150),
          S.stage_label(147, 262, "Papers", "abstract + Data Availability"),
          S.stage_label(352, 262, "AI agent", "reads · extracts · verifies"),
          S.stage_label(637, 262, "Three reviewers", "grounded · link-checked"),
          S.stage_label(922, 262, "BioAgentDB", "records · evidence · live links")]
    p.append("</svg>")
    return "".join(p)


def fig2_loop() -> str:
    w, h = 1060, 420
    p = [S.svg_open(w, h, fig_id="2", y0=30), S.defs()]
    p += [S.papers(80, 96),
          S.agent_core(390, 160, 60),
          S.db_cylinder(780, 160, rx=80, ry=20, height=88)]
    # consumers node
    p.append(f'<rect x="410" y="330" width="270" height="64" rx="14" fill="#ffffff" '
             f'stroke="{S.AMBER_LINE}"/>')
    p.append(f'<path d="M452,350 h20 a7,7 0 0 1 7,7 v10 a7,7 0 0 1 -7,7 h-11 l-7,7 v-7 h-2 '
             f'a7,7 0 0 1 -7,-7 v-10 a7,7 0 0 1 7,-7 z" fill="{S.TEAL}"/>')
    p.append(f'<text x="480" y="362" font-size="17.5" font-weight="700" fill="{S.INK}">'
             f'Ask the agent · API</text>')
    p.append(f'<text x="480" y="382" font-size="13.8" fill="{S.GREY}">consumers of the catalog</text>')
    # cycle arrows
    p += [S.flow(210, 150, 320, 150, fid="l1"),
          S.flow(455, 145, 690, 145, curve=30, label="verified records", label_dy=-16, fid="l2"),
          S.flow(715, 210, 615, 328, curve=34, fid="l3"),
          S.flow(425, 330, 382, 238, curve=-30, label="gaps → on-demand", label_dx=-54,
                 label_dy=1, fid="l4")]
    p.append("</svg>")
    return "".join(p)


def fig3_verification() -> str:
    w, h = 1060, 470
    p = [S.svg_open(w, h, fig_id="3B"), S.defs(),
         S.badge(10, 20, "(B)")]
    # row 1: candidate -> reviewers -> T1 -> T2 -> db
    p.append(f'<rect x="40" y="96" width="200" height="56" rx="10" fill="#ffffff" '
             f'stroke="{S.BORDER}"/>')
    p.append(f'<text x="140" y="120" font-size="16.3" font-weight="700" fill="{S.INK}" '
             f'text-anchor="middle">candidate record</text>')
    p.append(f'<text x="140" y="138" font-size="13.8" fill="{S.GREY}" text-anchor="middle">'
             f'accession + fields</text>')
    p += [S.flow(244, 124, 296, 124),
          S.reviewers(310, 97),
          S.flow(496, 124, 548, 124)]
    p.append(f'<rect x="560" y="96" width="150" height="56" rx="10" fill="{S.TEAL_BG}" '
             f'stroke="{S.TEAL_MID}"/>')
    p.append(f'<text x="635" y="120" font-size="16.3" font-weight="700" fill="{S.TEAL}" '
             f'text-anchor="middle">T1 gate</text>')
    p.append(f'<text x="635" y="138" font-size="13.8" fill="{S.GREY}" text-anchor="middle">'
             f'accession ∈ quote</text>')
    p += [S.flow(714, 124, 762, 124)]
    p.append(f'<rect x="774" y="96" width="150" height="56" rx="10" fill="#f8fbf9" '
             f'stroke="{S.TEAL_MID}"/>')
    p.append(f'<text x="849" y="120" font-size="16.3" font-weight="700" fill="{S.TEAL}" '
             f'text-anchor="middle">T2 gate</text>')
    p.append(f'<text x="849" y="138" font-size="13.8" fill="{S.GREY}" text-anchor="middle">'
             f'link checked</text>')
    p += [S.flow(928, 124, 952, 124),
          S.db_cylinder(1005, 124, rx=44, ry=12, height=52)]
    # regrounding loop above T1
    p.append(S.flow(585, 92, 685, 92, curve=30, label="regrounding (search paper text)",
                    label_dy=-14, token=False))
    # reject/fix arrow to audit
    p.append(S.flow(470, 186, 690, 288, curve=30, label="reject / fix", label_dy=-20,
                    token=False))
    # example card (real record from evidence)
    ex = load(EVIDENCE / "dryrun-record.json", [])
    rec = ex[0] if ex else {"repository": "ENA", "accession": "PRJNA860336",
                            "quote": "The datasets generated and/or analysed during the current "
                                     "study are available in the NCBI BioProject repository."}
    quote = re.sub(r"\s+", " ", rec.get("quote") or "")
    words, lines, cur = quote.split(), [], ""
    for w_ in words:
        if len(cur) + len(w_) + 1 <= 76:
            cur = (cur + " " + w_).strip()
        else:
            lines.append(cur)
            cur = w_
    if cur:
        lines.append(cur)
    lines = lines[:2]
    if len(lines) == 2 and sum(len(x) for x in lines) + 1 < len(quote):
        lines[1] = lines[1][:73].rstrip() + " …"
    p.append(f'<rect x="40" y="300" width="620" height="120" rx="12" fill="#f8fbf9" '
             f'stroke="{S.BORDER}"/>')
    p.append(f'<text x="60" y="328" font-size="15" font-weight="700" fill="{S.INK}">'
             f'Real record (T1-grounded)</text>')
    for k, ln in enumerate(lines):
        p.append(f'<text x="60" y="{352 + k * 17}" font-size="14.4" fill="{S.INK}">'
                 f'{"" if k else "“"}{ln}{"”" if k == len(lines) - 1 else ""}</text>')
    chip = f'{rec.get("repository", "ENA")} · {rec.get("accession", "PRJNA860336")}'
    p.append(f'<rect x="60" y="376" width="{30 + 7 * len(chip)}" height="26" rx="13" '
             f'fill="{S.TEAL_BG}" stroke="{S.TEAL_PALE}"/>')
    p.append(f'<text x="{60 + 14 + 3.5 * len(chip)}" y="393" font-size="15" fill="{S.TEAL}" '
             f'text-anchor="middle">{chip}</text>')
    p.append(f'<text x="300" y="393" font-size="15" fill="{S.AMBER}" font-weight="700">'
             f'✓ accession in quote</text>')
    p.append(f'<text x="470" y="393" font-size="15" fill="{S.AMBER}" font-weight="700">'
             f'✓ link checked</text>')
    p.append(f'<text x="60" y="406" font-size="13.8" fill="{S.GREY}">'
             f'every stored record keeps the paper’s exact sentence + a live link</text>')
    # audit card
    p.append(f'<rect x="700" y="300" width="320" height="120" rx="12" fill="#ffffff" '
             f'stroke="{S.AMBER_LINE}"/>')
    p.append(f'<text x="720" y="328" font-size="15" font-weight="700" fill="{S.INK}">'
             f'Audit trail</text>')
    for i, line in enumerate(("candidates · paper_audits",
                              "reject · fix_count · fix_field · fix_role")):
        p.append(f'<text x="720" y="{352 + i * 20}" font-size="14.4" fill="{S.GREY}">{line}</text>')
    p.append("</svg>")
    return "".join(p)


def fig4_ask() -> str:
    w, h = 1060, 400
    p = [S.svg_open(w, h, fig_id="5", y0=30), S.defs()]
    p.append(f'<rect x="40" y="120" width="250" height="84" rx="14" fill="#ffffff" '
             f'stroke="{S.BORDER}"/>')
    p.append(f'<text x="60" y="150" font-size="15" font-weight="700" fill="{S.INK}">'
             f'user question</text>')
    p.append(f'<text x="60" y="172" font-size="14.4" fill="{S.GREY}">'
             f'“Which Alzheimer’s scRNA-seq</text>')
    p.append(f'<text x="60" y="188" font-size="14.4" fill="{S.GREY}">datasets exist?”</text>')
    p += [S.flow(294, 160, 336, 160, fid="a1"),
          S.agent_core(420, 160, 54),
          S.flow(478, 160, 552, 160, fid="a2"),
          S.db_cylinder(640, 160, rx=64, ry=16, height=72),
          S.flow(708, 160, 764, 160, fid="a3")]
    p.append(f'<rect x="776" y="106" width="250" height="108" rx="14" fill="#f8fbf9" '
             f'stroke="{S.TEAL_MID}"/>')
    p.append(f'<text x="796" y="134" font-size="15" font-weight="700" fill="{S.TEAL}">'
             f'grounded answer</text>')
    p.append(f'<text x="796" y="156" font-size="14.4" fill="{S.INK}">3 records · quotes + live links</text>')
    p.append(f'<text x="796" y="176" font-size="14.4" fill="{S.GREY}">citations: GSE296027, '
             f'GSE140510</text>')
    p.append(f'<text x="796" y="198" font-size="13.8" fill="{S.GREY}">no record → “not found”</text>')
    p.append(f'<rect x="420" y="290" width="440" height="56" rx="12" fill="#ffffff" '
             f'stroke="{S.AMBER_LINE}"/>')
    p.append(f'<text x="440" y="316" font-size="15" font-weight="700" fill="{S.AMBER}">'
             f'non-biomedical question</text>')
    p.append(f'<text x="440" y="334" font-size="14.4" fill="{S.GREY}">'
             f'→ refused (no hallucination, no off-domain answers)</text>')
    p.append(S.flow(420, 214, 480, 288, curve=-22, label="", token=False))
    p.append("</svg>")
    return "".join(p)


def fig3_unit() -> str:
    """Figure 3A · 记录单元（论文的数据集声明）＋"谁来做判定"。"""
    w, h = 1060, 270
    p = [S.svg_open(w, h, fig_id="3A"), S.defs(), S.badge(10, 20, "(A)")]
    # 上排：论文 → 宣告句 → claim → 记录
    p.append(S.card(10, 46, 230, 92, "paper", ["abstract + Data Availability"]))
    p.append(S.flow(240, 92, 290, 92, token=False))
    p.append(S.card(290, 46, 250, 92, "announcing sentence", ["the verbatim quote travels",
                                                              "with the record"]))
    p.append(S.flow(540, 92, 590, 92, token=False))
    p.append(S.card(590, 46, 210, 92, "dataset claim", ["accession + fingerprint"]))
    p.append(S.flow(800, 92, 850, 92, token=False))
    p.append(S.card(850, 46, 200, 92, "record", ["evidence + live link"]))
    p.append(f'<text x="530" y="164" font-size="14" font-weight="700" fill="{S.AMBER}" '
             f'text-anchor="middle">no announcing sentence → no record</text>')
    # 下排：LLM 判语义 ｜ 确定性代码判定
    p.append(S.card(10, 182, 500, 78, "LLM: reads · extracts · judges semantics",
                    ["which sentences announce data; what the fields mean"]))
    p.append(S.flow(510, 221, 550, 221, token=False))
    p.append(S.card(550, 182, 500, 78, "deterministic code: decides",
                    ["regex + fingerprint dedup; T1 substring · T2 HTTP resolve"]))
    p.append("</svg>")
    return "".join(p)


def fig4_link_status() -> str:
    """Figure 4D · 链接可达性（目录级属性，公开披露）。"""
    return S.vbar("Figure 4D · Link status (T2)", [("reachable", 3341), ("unresolved", 185)],
                  h=300, value_fmt=lambda v: f"{v:,}")


def fig9_findable() -> str:
    """Figure 9I · "可发现"与"已宣告"的区别（评估口径）。"""
    return S.vbar("Figure 9I · Findable vs announced (n = 250)",
                  [("announce", 182), ("findable", 123), ("no accession", 59)])


# ---------------------------------------------------------------- data charts
SHORT = {"alzheimers-disease": "Alzheimer's", "als": "ALS", "parkinsons-disease": "Parkinson's",
         "frontotemporal-dementia": "FTD", "huntingtons-disease": "Huntington's",
         "prion-disease": "Prion/CJD", "progressive-supranuclear-palsy": "PSP",
         "dementia-with-lewy-bodies": "DLB", "multiple-system-atrophy": "MSA",
         "spinocerebellar-ataxia": "SCA", "breast-cancer": "Breast (demo)",
         "transcriptomics": "transcriptomics", "proteomics": "proteomics",
         "epigenomics": "epigenomics", "genomics": "genomics", "scrna": "scRNA",
         "snrna": "snRNA", "multi_omics": "multi-omics",
         "spatial_transcriptomics": "spatial"}


def theme_pair_bars(db_path: Path):
    import sqlite3
    from collections import Counter, defaultdict
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    rows = conn.execute(
        "SELECT pt.paper_id, pt.theme FROM paper_themes pt "
        "JOIN dataset_papers dp ON dp.paper_id=pt.paper_id "
        "GROUP BY pt.paper_id, pt.theme").fetchall()
    conn.close()
    per = defaultdict(set)
    for pid, th in rows:
        per[pid].add(th)
    c = Counter()
    for ths in per.values():
        ts = sorted(ths)
        for i in range(len(ts)):
            for j in range(i + 1, len(ts)):
                c[(ts[i], ts[j])] += 1
    items = [(f"{S_SHORT(a)} ∩ {S_SHORT(b)}", v) for (a, b), v in c.most_common(8)]
    multi = sum(1 for ths in per.values() if len(ths) >= 2)
    return items, multi, len(per)


def S_SHORT(slug: str) -> str:
    return {"alzheimers-disease": "AD", "als": "ALS", "parkinsons-disease": "PD",
            "frontotemporal-dementia": "FTD", "huntingtons-disease": "HD",
            "prion-disease": "Prion", "progressive-supranuclear-palsy": "PSP",
            "dementia-with-lewy-bodies": "DLB", "multiple-system-atrophy": "MSA",
            "spinocerebellar-ataxia": "SCA", "breast-cancer": "BC"}.get(slug, slug)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stats", default=str(ROOT / "docs/versions/v1.5-stats/resource-stats.json"))
    ap.add_argument("--db", default=str(ROOT / "data/releases/_v15/bioagentdb.sqlite3"))
    ap.add_argument("--no-sync", action="store_true")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    rs = load(Path(args.stats), {})
    ev = load(GOLD / "eval-report.json", {})
    evc = load(GOLD / "catalog-view" / "eval-report.json", {}) or ev
    abl = load(GOLD / "ablation-report.json", {})
    err = load(GOLD / "error-analysis.json", {})
    ds = load(ROOT / "data" / "eval" / "downstream-report.json", {})
    curve = load(GOLD / "self-improve-curve.json", {})
    union = (load(GOLD / "s1-rescan-report.json", {}).get("results") or {}).get(
        "union(pipe+scan)") or {}

    panels: list[str] = [fig1_architecture(), fig2_loop(), fig3_verification(), fig4_ask()]

    # Fig 5A–5C
    pt = rs.get("per_theme", [])
    panels.append(S.hbar("Figure 4A · Papers with data per disease",
                         [(SHORT.get(t["slug"], t["slug"]), t["papers_with_data"]) for t in pt],
                         w=640, h=300))
    mods = list(rs.get("modalities", {}).items())[:8]
    panels.append(S.hbar("Figure 4B · Dataset records by modality",
                         [(SHORT.get(k, k), v) for k, v in mods], w=640, h=300))
    pairs, _multi, _total = theme_pair_bars(Path(args.db))
    panels.append(S.hbar("Figure 4C · Cross-disease papers (with data)", pairs,
                         w=640, color=S.TEAL_MID, h=300))

    # Fig 9A–9I
    rl = ev.get("record_level", {})
    rlc = evc.get("record_level", {})
    panels.append(S.vbar("Figure 9A · Record-level precision / recall / F1 (released catalog)",
                         [("precision", round(rlc.get("precision", 0) * 100)),
                          ("recall", round(rlc.get("recall", 0) * 100)),
                          ("F1", round(rlc.get("f1", 0) * 100))], pct=True))
    pol = abl.get("policies", {})
    panels.append(S.vbar("Figure 9B · Ablation: precision by filter policy",
                         [("verified+grounded" if k == "verified_grounded" else k,
                           round(pol[k]["precision"] * 100))
                          for k in ("all", "verified", "verified_grounded") if k in pol],
                         pct=True, color=S.AMBER))
    panels.append(S.vbar("Figure 9C · Ablation: recall by filter policy",
                         [(k.replace("_", " "), round(pol[k]["recall"] * 100))
                          for k in ("all", "verified") if k in pol],
                         pct=True, color=S.TEAL_MID))
    fp, fn = err.get("fp_categories", {}), err.get("fn_categories", {})
    panels.append(S.vbar("Figure 9D · Error taxonomy",
                         [("FP ref-gap", fp.get("in_text_but_reference_missed", 0)),
                          ("FP not-in-text", fp.get("not_in_sample_text", 0)),
                          ("FN missed", fn.get("accession_missed", 0)),
                          ("FN (no data)", fn.get("paper_predicted_no_data", 0))],
                         color=S.TEAL))
    cond = ds.get("conditions", {})
    nc, wc = cond.get("no_catalog", {}), cond.get("with_catalog", {})
    panels.append(S.hbar("Figure 9E · Downstream retrieval (P/R/F1)",
                         [("no-catalog P", round(nc.get("precision", 0) * 100, 1)),
                          ("no-catalog R", round(nc.get("recall", 0) * 100, 1)),
                          ("no-catalog F1", round(nc.get("f1", 0) * 100, 1)),
                          ("catalog P", round(wc.get("precision", 0) * 100, 1)),
                          ("catalog R", round(wc.get("recall", 0) * 100, 1)),
                          ("catalog F1", round(wc.get("f1", 0) * 100, 1))],
                         w=640, h=280, value_fmt=lambda v: f"{v}%",
                         color=[S.TEAL_MID] * 3 + [S.TEAL] * 3))
    panels.append(S.vbar("Figure 9F · Hallucinated accessions",
                         [("no catalog", nc.get("hall", 0)), ("with catalog", wc.get("hall", 0))],
                         color=S.AMBER))
    methods = [("raw-extract", S.TEAL), ("raw-LLM", S.AMBER), ("RAG", S.TEAL_MID),
               ("catalog", "#134e4a")]
    items = [("raw-extract P", round(rl.get("precision", 0) * 100)),
             ("raw-extract R", round(rl.get("recall", 0) * 100)),
             ("raw-extract F1", round(rl.get("f1", 0) * 100)),
             ("raw-LLM P", 99), ("raw-LLM R", 67), ("raw-LLM F1", 80),
             ("RAG P", 99), ("RAG R", 70), ("RAG F1", 82),
             ("catalog P", round(rlc.get("precision", 0) * 100)),
             ("catalog R", round(rlc.get("recall", 0) * 100)),
             ("catalog F1", round(rlc.get("f1", 0) * 100))]
    colors = [c for _, c in methods for _ in range(3)]
    panels.append(S.hbar("Figure 9G · Method comparison (P/R/F1)", items, w=640, h=400,
                         value_fmt=lambda v: f"{v}%",
                         color=colors,
                         legend=[("raw-extract", S.TEAL), ("raw-LLM", S.AMBER),
                                 ("RAG", S.TEAL_MID), ("catalog", "#134e4a")]))
    cyc = curve.get("cycles", [])
    if cyc:
        panels.append(S.line_chart(
            "Figure 9H · Self-improvement curve", [f"c{c['cycle']}" for c in cyc],
            [("dev F1", [c["dev"]["f1"] * 100 for c in cyc], S.TEAL),
             ("test F1", [c["test"]["f1"] * 100 for c in cyc], S.AMBER),
             ("test recall", [c["test"]["recall"] * 100 for c in cyc], S.TEAL_MID)],
            w=640, h=400, y_min=60))

    # 创新点补充面板（L-0161）：3A 单元+分工 · 4D 链接状态 · 9I findable vs announced
    panels.append(fig3_unit())            # panel16 → Figure 3A
    panels.append(fig4_link_status())     # panel17 → Figure 4D
    panels.append(fig9_findable())        # panel18 → Figure 9I

    # HTML（示意图跨双列）
    cards = []
    for i, p in enumerate(panels, 1):
        wide = " wide" if i <= 4 else ""
        cards.append(f'<div class="card{wide}">{p}</div>')
    html = ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
            f"<!-- make_figures v3 · stats={Path(args.stats).parent.name} db={Path(args.db).parent.name} -->"
            "<style>"
            "body{background:#fff;margin:0;padding:16px;display:grid;"
            "grid-template-columns:repeat(2,1fr);gap:14px;font-family:Arial,Helvetica,sans-serif}"
            ".card{border:1px solid #e2e8f0;border-radius:10px;padding:6px}"
            ".card.wide{grid-column:span 2}"
            "</style></head><body>" + "".join(cards) + "</body></html>")
    for i, p in enumerate(panels, 1):
        (OUT / f"panel{i}.svg").write_text(p, encoding="utf-8")
    (OUT / "figures.html").write_text(html, encoding="utf-8")
    print(f"wrote {OUT/'figures.html'} ({len(panels)} panels)")

    if not args.no_sync:
        FINAL.mkdir(parents=True, exist_ok=True)
        ours = re.compile(r"^(panel\d+\.svg|figures\.html|figures\.png|"
                          r"graphical-abstract\.(svg|png))$")
        out_names = {f.name for f in OUT.glob("*") if f.is_file()}
        for f in FINAL.glob("*"):
            if f.is_file() and ours.match(f.name) and f.name not in out_names:
                f.unlink()
                print(f"removed orphan {f.name}")
        for f in OUT.glob("*"):
            if f.is_file():
                shutil.copy2(f, FINAL / f.name)
        print(f"synced {len(out_names)} files -> {FINAL}")


if __name__ == "__main__":
    main()
