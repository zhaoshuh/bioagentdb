"""合成图生成器：按**图号顺序**拼装 `figures-sheet.html`（含 S1/S2/S3/S4/GA）→ 矢量 `figures.pdf` + 预览位图 `figures.png`。

顺序：Figure 1, 2, 3A–3B, 4A–4D, 5（Ask 流程）, 6/7/8（真机截图栅格）, 9A–9I（评测）, S1A–S1C（机制）, S2A–S2D（覆盖）, S3（结果集，含标识框）, GA。
（此前合成图直接渲染 `figures.html`，其面板物理顺序为 1,2,3,5,4A–C,7A–H 且不含图 6/GA。）

**谁是真源**：论文正文只内联 `panel*.svg`（矢量）与三张真机截图（`figure6-ledger.png` / `figure7-record.png` / `figure8-ask.png`）；本脚本产出的
`figures.pdf` 是矢量联系表（提交/评审用），`figures.png` 仅是位图预览（headless 截图，
抗锯齿噪声导致逐次哈希不同，不可作为复现判据）。

用法: python scripts/make_figures_sheet.py [--width 1360] [--scale 2]
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import websocket

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "paper" / "figures"
FINAL = ROOT / "docs" / "paper" / "final" / "figures"
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PROFILE = r"C:\Users\Administrator\AppData\Local\Temp\edge-fig"
PORT = 9231

ORDER = ["panel1", "panel2", "panel16", "panel3",                     # 图 1,2,3A,3B
         "panel5", "panel6", "panel7", "panel17",                      # 图 4A,4B,4C,4D
         "panel4",                                                     # 图 5
         "__FIG6__", "__FIG7__", "__FIG8__",                             # 图 6/7/8 三张真机截图
         "panel8", "panel9", "panel10", "panel11", "panel12", "panel13",
         "panel14", "panel15", "panel18",                              # 图 9A–9H,9I
         "__SUPP__",                                                   # 补充图 S1（机制，3 面板）
         "__SUPP2__",                                                  # 补充图 S2（覆盖，4 面板）
         "__FIGS3__", "__FIGS4__"]                                     # 补充图 S3（结果集）+ S4（用户挖掘）


def build_html() -> Path:
    parts = []
    for name in ORDER:
        if name in ("__FIG6__", "__FIG7__", "__FIG8__"):
            fname = {"__FIG6__": "figure6-ledger.png", "__FIG7__": "figure7-record.png",
                     "__FIG8__": "figure8-ask.png"}[name]
            lab = {"__FIG6__": "Figure 6 · the ledger",
                   "__FIG7__": "Figure 7 · a record in place",
                   "__FIG8__": "Figure 8 · a real Ask session"}[name]
            src = FINAL / fname
            if src.exists() and not (OUT / fname).exists():
                shutil.copy2(src, OUT / fname)
            if (OUT / fname).exists():
                parts.append(f'<div class="cell"><div class="lab">{lab}</div>'
                             f'<img alt="{lab}" style="width:100%" src="{fname}"></div>')
            continue
        if name == "__SUPP__":
            for c in ("A", "B", "C"):
                p = OUT / f"panelS1{c}.svg"
                if p.exists():
                    parts.append(f'<div class="cell">{p.read_text(encoding="utf-8")}</div>')
            continue
        if name == "__SUPP2__":
            for c in ("A", "B", "C", "D"):
                p = OUT / f"panelS2{c}.svg"
                if p.exists():
                    parts.append(f'<div class="cell">{p.read_text(encoding="utf-8")}</div>')
            continue
        if name in ("__FIGS3__", "__FIGS4__"):
            fname = {"__FIGS3__": "supplementary-figure-s3.png",
                     "__FIGS4__": "supplementary-figure-s4.png"}[name]
            lab = {"__FIGS3__": "Supplementary Figure S3 · the result set and its export",
                   "__FIGS4__": "Supplementary Figure S4 · user-defined mining (Ming → My mining)"}[name]
            src = FINAL / fname
            if src.exists() and not (OUT / fname).exists():
                shutil.copy2(src, OUT / fname)
            if (OUT / fname).exists():
                parts.append(f'<div class="cell"><div class="lab">{lab}</div>'
                             f'<img alt="{lab}" style="width:100%" src="{fname}"></div>')
            continue
        svg = (OUT / f"{name}.svg").read_text(encoding="utf-8")
        parts.append(f'<div class="cell">{svg}</div>')
    ga = OUT / "graphical-abstract.svg"
    if ga.exists():
        parts.append('<div class="cell ga"><div class="lab">Graphical Abstract</div>' +
                     ga.read_text(encoding="utf-8") + "</div>")
    html = ("<!doctype html><html><head><meta charset='utf-8'><style>"
            "@page{size:A4;margin:10mm}"
            "body{margin:0;background:#fff;font-family:Arial,Helvetica,sans-serif}"
            ".grid{display:grid;grid-template-columns:1fr;gap:14px;padding:10px}"
            ".cell{padding:6px;border:1px solid #e3eae6;border-radius:10px;background:#fff;break-inside:avoid}"
            ".cell svg,.cell img{display:block;width:100%;height:auto}"
            ".lab{font:12px Arial,Helvetica,sans-serif;font-weight:700;color:#0f766e;padding:2px 0 6px}"
            "</style></head><body><div class='grid'>" + "".join(parts) + "</div></body></html>")
    (OUT / "figures.html").write_text(html, encoding="utf-8")
    p = OUT / "figures-sheet.html"
    p.write_text(html, encoding="utf-8")
    return p


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--width", type=int, default=1360)
    ap.add_argument("--scale", type=int, default=2)
    args = ap.parse_args()

    html = build_html()
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    "Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" | "
                    f"Where-Object {{ $_.CommandLine -match 'edge-fig' }} | "
                    "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"], capture_output=True)
    time.sleep(2)
    shutil.rmtree(PROFILE, ignore_errors=True)
    subprocess.Popen([EDGE, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                      f"--window-size={args.width},{args.width}", f"--remote-debugging-port={PORT}",
                      "--remote-allow-origins=*", f"--user-data-dir={PROFILE}", html.as_uri()])
    page = None
    for _ in range(45):
        try:
            tg = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json", timeout=3).read())
            pages = [t for t in tg if t.get("type") == "page"]
            page = next((t for t in pages if "figures-sheet" in (t.get("url") or "")), None) \
                or (pages[0] if pages else None)
            if page:
                break
        except Exception:  # noqa: BLE001
            pass
        time.sleep(1)
    if not page:
        sys.exit("CDP not ready")
    ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=300)

    def send(mid: int, method: str, params: dict | None = None) -> dict:
        ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            m = json.loads(ws.recv())
            if m.get("id") == mid:
                return m

    send(1, "Page.navigate", {"url": html.as_uri()})
    send(2, "Emulation.setDeviceMetricsOverride",
         {"width": args.width, "height": 900, "deviceScaleFactor": args.scale, "mobile": False})
    time.sleep(2)
    send(3, "Runtime.evaluate", {"expression": "document.fonts ? document.fonts.ready : 1",
                                 "awaitPromise": True})
    time.sleep(2)
    h = int(send(4, "Runtime.evaluate", {"expression": "document.documentElement.scrollHeight",
                                         "returnByValue": True})["result"]["result"]["value"])
    res = send(5, "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True,
                                             "clip": {"x": 0, "y": 0, "width": args.width,
                                                      "height": h, "scale": 1}})
    data = base64.b64decode(res["result"]["data"])
    (OUT / "figures.png").write_bytes(data)
    FINAL.mkdir(parents=True, exist_ok=True)
    shutil.copy2(OUT / "figures.png", FINAL / "figures.png")
    shutil.copy2(html, FINAL / "figures.html")
    shutil.copy2(html, FINAL / "figures-sheet.html")
    ws.close()
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    "Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" | "
                    f"Where-Object {{ $_.CommandLine -match 'edge-fig' }} | "
                    "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"], capture_output=True)
    txt = (OUT / "figures-sheet.html").read_text(encoding="utf-8")
    # 面板内不再写 "Figure 9A · 标题"（L-0181）：改为读 (A) 徽标旁的机器可读注释 + 栅格/GA 的 .lab
    panel_ids = re.findall(r"<!-- figure: ([0-9S][A-Z0-9]*) -->", txt)
    lab_txt = re.findall(r'<div class="lab">([^<]+)</div>', txt)
    labels = panel_ids + lab_txt
    print(f"wrote {OUT/'figures.png'} ({args.width}×{h} @{args.scale}x) → {FINAL}")
    # 矢量联系表：同一 HTML 打印为 PDF（面板为矢量；Figure 6 面板本身是栅格截屏）
    out_pdf = OUT / "figures.pdf"
    subprocess.run([EDGE, "--headless", "--disable-gpu", "--no-pdf-header-footer",
                    f"--print-to-pdf={out_pdf}", html.as_uri()], capture_output=True, timeout=300)
    if out_pdf.exists():
        shutil.copy2(out_pdf, FINAL / "figures.pdf")
        from pypdf import PdfReader
        print(f"wrote {out_pdf} (vector contact sheet, {len(PdfReader(str(out_pdf)).pages)} pages)")
    print("sheet 内图号顺序:", labels or "(未解析到)")


if __name__ == "__main__":
    main()
