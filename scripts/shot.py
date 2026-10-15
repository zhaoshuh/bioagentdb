"""截图工具（CDP）：强制完成动画后截图，供视觉审查使用。

用法:
  python scripts/shot.py OUT.png [URL] [WIDTH] [HEIGHT] [ANCHOR]
      [--js-file PATH] [--wait MS] [--viewport]
"""

import argparse
import base64
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import websocket

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("url", nargs="?", default="http://127.0.0.1:8000/")
    ap.add_argument("width", nargs="?", type=int, default=1680)
    ap.add_argument("height", nargs="?", type=int, default=1200)
    ap.add_argument("anchor", nargs="?", default="")
    ap.add_argument("--js-file", default="")
    ap.add_argument("--wait", type=int, default=1200)
    ap.add_argument("--viewport", action="store_true")
    ap.add_argument("--exact", action="store_true",
                    help="用 Emulation 固定视口为 WIDTHxHEIGHT（不受窗口/记忆尺寸影响）")
    args = ap.parse_args()

    out = Path(args.out)
    width, height = args.width, args.height
    anchor = args.anchor
    pre_js = Path(args.js_file).read_text(encoding="utf-8") if args.js_file else ""
    wait_ms = args.wait

    subprocess.Popen([EDGE, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                      f"--window-size={width},{height}", "--remote-debugging-port=9222",
                      "--remote-allow-origins=*",
                      r"--user-data-dir=C:\Users\Administrator\AppData\Local\Temp\edge-shot",
                      args.url])
    page = None
    for _ in range(40):
        try:
            targets = json.loads(urllib.request.urlopen("http://127.0.0.1:9222/json", timeout=3).read())
            pages = [t for t in targets if t.get("type") == "page"]
            page = next((t for t in pages if "8000" in (t.get("url") or "")), None) \
                or (pages[0] if pages else None)
            if page:
                break
        except Exception:
            pass
        time.sleep(1)
    if not page:
        print("CDP not ready")
        sys.exit(1)
    ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=240)

    def send(mid, method, params=None):
        ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            m = json.loads(ws.recv())
            if m.get("id") == mid:
                return m

    # 强制导航到目标 URL（防"残留实例复用旧标签页"：9222 被旧进程占用时仍拍到旧页面）
    send(11, "Page.navigate", {"url": args.url})
    for _ in range(30):
        r = send(12, "Runtime.evaluate", {"expression": "document.readyState",
                                          "returnByValue": True})
        if r.get("result", {}).get("result", {}).get("value") == "complete":
            break
        time.sleep(0.5)
    time.sleep(0.6)

    # 等数据加载 + 强制显示所有 reveal + 冻结动画
    res = send(1, "Runtime.evaluate", {"expression": f"""
      (async () => {{
        for (let i=0;i<40;i++){{ const L=document.querySelector('#rec-list'); if(!L) break; if (L.querySelectorAll('.record').length>0) break; await new Promise(r=>setTimeout(r,250)); }}
        document.querySelectorAll('.reveal').forEach(e=>e.classList.add('in'));
        document.querySelectorAll('.mining .tokens, .mining .orbit, .mining .core').forEach(e=>{{ e.style.animation='none'; }});
        const s=document.createElement('style'); s.textContent='*{{animation:none!important;transition:none!important}}'; document.head.appendChild(s);
        const a = {json.dumps(anchor)};
        let y = 0;
        if (a) {{ const el = document.querySelector(a); if (el) y = Math.max(0, el.getBoundingClientRect().top + window.scrollY - 8); }}
        await new Promise(r=>setTimeout(r,400));
        return y;
      }})()""", "awaitPromise": True, "returnByValue": True})
    clip_y = int(res.get("result", {}).get("result", {}).get("value") or 0)
    if args.exact:
        send(9, "Emulation.setDeviceMetricsOverride",
             {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": False})
        time.sleep(0.9)
    if pre_js:
        send(3, "Runtime.evaluate", {"expression": pre_js, "awaitPromise": True,
                                      "returnByValue": True})
        time.sleep(max(0, wait_ms) / 1000.0)
    time.sleep(0.6)
    if args.viewport or args.exact:
        res = send(2, "Page.captureScreenshot", {"format": "png",
                                                 "captureBeyondViewport": False})
    else:
        res = send(2, "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True,
                                                 "clip": {"x": 0, "y": clip_y, "width": width,
                                                          "height": height, "scale": 1}})
    data = res.get("result", {}).get("data")
    if not data:
        print("capture failed", json.dumps(res)[:300])
        sys.exit(1)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(base64.b64decode(data))
    print(f"saved {out} ({out.stat().st_size // 1024} KB)")
    ws.close()


if __name__ == "__main__":
    main()
