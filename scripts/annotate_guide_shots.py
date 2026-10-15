"""给 Guide 截图加标注（①②③ 编号气泡 + 框选 + 图例），Edge 1.5× 合成。

输入:  web/guide/img/<name>.png（原始真实截图）
输出:  web/guide/img/<name>.annotated.png（标注版）
用法:  python scripts/annotate_guide_shots.py
"""

from __future__ import annotations

import math
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMG = ROOT / "web" / "guide" / "img"
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
TEAL, AMBER = "#0f766e", "#a16207"

# (num, x, y, w, h, color, legend)
ANN: dict[str, list[tuple]] = {
    "home-hero": [
        (1, 180, 16, 405, 30, TEAL, "Navigation — Topics, Records, Ask the agent, My mining, Guide."),
        (2, 80, 145, 990, 330, TEAL, "What the site is, in one screen."),
        (3, 105, 570, 960, 34, AMBER, "Live counters — the agent at work."),
        (4, 95, 625, 1430, 295, TEAL, "papers → AI agent → checks → BioAgentDB."),
    ],
    "search-results": [
        (1, 93, 2, 402, 44, TEAL, "Type a topic, gene, tissue or accession."),
        (2, 507, 2, 1006, 44, TEAL, "Filter by topic, modality, organism, status."),
        (3, 1512, 2, 112, 44, AMBER, "Press Search."),
        (4, 92, 46, 100, 40, AMBER, "Export the current results (CSV)."),
        (5, 105, 300, 715, 140, AMBER, "Accessions and the verbatim evidence quote."),
    ],
    "record-detail": [
        (1, 452, 112, 780, 92, TEAL, "The record in context — title, repository, status."),
        (2, 452, 268, 780, 30, TEAL, "Normalized fields — every value is anchored to the paper."),
        (3, 452, 296, 780, 172, AMBER, "Disease / organism / modalities, each with its quote."),
        (4, 452, 480, 780, 180, AMBER, "Evidence — the exact sentence, with its zone."),
    ],
    "ask-session": [
        (1, 310, 1198, 920, 96, TEAL, "Ask in plain language — the input box."),
        (2, 315, 214, 900, 44, TEAL, "Every turn keeps its agent steps — expand to watch."),
        (3, 315, 265, 775, 665, AMBER, "The report: verified catalog records with evidence, plus papers to inspect."),
        (4, 1246, 100, 400, 190, TEAL, "Timestamped result sets — newest first; tick which to export (JSONL / CSV)."),
    ],
    "ming-panel": [
        (1, 306, 82, 150, 32, TEAL, "Research | Ming — two independent modes."),
        (2, 14, 94, 262, 110, TEAL, "Ming threads — the session list is per-mode (research sessions stay in Research)."),
        (3, 310, 114, 905, 374, TEAL, "A conversation like Research — plain-text questions; answer freely (say \u201cany\u201d to skip)."),
        (4, 310, 488, 902, 346, AMBER, "The proposal: theme, editable queries and budget — press Start Ming."),
        (5, 306, 1082, 910, 104, TEAL, "The same composer as Research (Enter to send; Stop to cancel)."),
    ],
    "mining-progress": [
        (1, 275, 218, 140, 34, TEAL, "Tasks | Topics — switch between your tasks and topic management."),
        (2, 262, 285, 348, 758, TEAL, "Each task: status, records found, papers read."),
        (3, 632, 293, 762, 165, TEAL, "The selected task — status, intent, budget and rounds."),
        (4, 638, 463, 312, 46, AMBER, "Restart a stopped or failed task; export its records as JSONL / CSV."),
        (5, 638, 513, 758, 520, AMBER, "Verified records with their evidence quotes — published to the catalog."),
    ],
    "export-toolbar": [
        (1, 93, 2, 402, 44, TEAL, "Search first."),
        (2, 507, 2, 1006, 44, TEAL, "Filter by topic / modality / organism / status."),
        (3, 92, 46, 100, 40, AMBER, "Export the results as CSV."),
        (4, 105, 298, 380, 46, TEAL, "Accessions travel with the export."),
    ],
}


def build_html(img_path: Path, items: list[tuple], w: int, h: int) -> tuple[str, int]:
    boxes, legend = [], []
    for num, x, y, bw, bh, color, text in items:
        boxes.append(f'<div class="box" style="left:{x}px;top:{y}px;width:{bw}px;'
                     f'height:{bh}px;border-color:{color}"></div>')
        bx, by = max(2, x - 14), max(2, y - 14)
        boxes.append(f'<div class="bub" style="left:{bx}px;top:{by}px;'
                     f'background:{color}">{num}</div>')
        legend.append(f'<div class="li"><span class="ln" style="background:{color}">'
                      f'{num}</span>{text}</div>')
    rows = math.ceil(len(items) / 2)
    legend_h = 24 + rows * 30
    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
body{{margin:0;background:#ffffff;font-family:Arial,Helvetica,sans-serif}}
.wrap{{position:relative;width:{w}px}}
.wrap img{{display:block;width:{w}px}}
.box{{position:absolute;border:2.5px solid;border-radius:8px;
  box-shadow:0 0 0 1.5px rgba(255,255,255,.75)}}
.bub{{position:absolute;width:26px;height:26px;border-radius:50%;color:#fff;
  font:700 14px/26px Arial,Helvetica,sans-serif;text-align:center;
  box-shadow:0 2px 6px rgba(0,0,0,.3)}}
.legend{{display:grid;grid-template-columns:1fr 1fr;gap:10px 26px;
  padding:16px 14px 8px;width:{w - 28}px}}
.li{{font-size:13.5px;color:#161f2c;display:flex;gap:9px;align-items:baseline}}
.ln{{flex:none;display:inline-block;width:21px;height:21px;border-radius:50%;
  color:#fff;font:700 12px/21px Arial,Helvetica,sans-serif;text-align:center}}
</style></head><body>
<div class="wrap"><img src="{img_path.as_uri()}">{''.join(boxes)}</div>
<div class="legend">{''.join(legend)}</div>
</body></html>"""
    return html, legend_h


def main() -> None:
    import struct
    for stem, items in ANN.items():
        raw = IMG / f"{stem}.png"
        if not raw.exists():
            print(f"skip (missing): {raw}")
            continue
        d = raw.read_bytes()[:33]
        w, h = struct.unpack(">II", d[16:24])
        html, legend_h = build_html(raw, items, w, h)
        tmp = IMG / f"_{stem}.annot.html"
        tmp.write_text(html, encoding="utf-8")
        out = IMG / f"{stem}.annotated.png"
        r = subprocess.run(
            [EDGE, "--headless", "--disable-gpu", "--hide-scrollbars",
             "--force-device-scale-factor=1.5",
             r"--user-data-dir=C:\Users\Administrator\AppData\Local\Temp\opencode\edge-profile",
             f"--window-size={w},{h + legend_h}", f"--screenshot={out}", tmp.as_uri()],
            capture_output=True, text=True, timeout=120)
        tmp.unlink(missing_ok=True)
        print(f"wrote {out.name} rc={r.returncode} "
              f"size={out.stat().st_size // 1024 if out.exists() else 0} KB")


if __name__ == "__main__":
    main()
