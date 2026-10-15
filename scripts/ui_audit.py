"""Web 对抗审查：CDP 断言 + 逐状态截图（供目视）。

输出: docs/paper/final/reviews/round-{N}/web/{*.png,assertions.json}
用法: python scripts/ui_audit.py --round 1
"""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import time
import urllib.request
from pathlib import Path

import websocket

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
URL = "http://127.0.0.1:8000/?v=45"
ROOT = Path(__file__).resolve().parents[1]

ASSERT_JS = r"""
(async () => {
  const out = [];
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const add = (n, ok, d = '') => out.push({ n, ok: !!ok, d: String(d).slice(0, 160) });
  for (let i = 0; i < 60; i++) {
    if (document.querySelectorAll('#rec-list .record').length) break;
    await sleep(250);
  }
  add('no "The three diseases" text', !document.body.innerText.includes('The three diseases'));
  const lede = document.querySelector('#hero .lede');
  const heroText = document.querySelector('#hero').innerText;
  add('hero copy describes purpose (no disease list / no reviewer detail)',
      lede && /catalog/i.test(lede.textContent)
        && !/Alzheimer|amyotrophic|Parkinson|frontotemporal|Huntington/i.test(lede.textContent)
        && !/three reviewers/i.test(heroText),
      lede && lede.textContent.slice(0, 80));
  add('hero disease count > 0', parseInt(document.getElementById('hs-themes').textContent) > 0,
      document.getElementById('hs-themes').textContent);
  const sel = document.getElementById('rec-theme');
  add('theme filter lists on-demand theme', [...sel.options].map(o => o.value).includes('breast-cancer'));
  add('selects auto-apply', ['rec-theme','rec-modality','rec-organism']
      .every(id => (document.getElementById(id).getAttribute('onchange') || '').includes('ledgerSearch')));
  add('disease chips on paper cards', document.querySelectorAll('#rec-list .chip.dz').length > 0);
  add('live dot visible', document.getElementById('ledger-dot').getBoundingClientRect().width > 0);
  add('no operator settings UI', !document.body.innerText.match(/model settings/i));
  add('hero mined counter = 19,390 (v1.3)', document.getElementById('live-read').textContent.replace(/\D/g,'') === '19390',
      document.getElementById('live-read').textContent);
  add('hero papers counter = 1,307 (v1.3)', document.getElementById('hs-papers').textContent.replace(/\D/g,'') === '1307',
      document.getElementById('hs-papers').textContent);
  const mstat = await (await fetch('/api/miner/status')).json();
  const anyLive = (mstat.processes || []).some(p => p.status === 'running');
  const lline = document.getElementById('live-status').textContent;
  add('miner line truthful (idle when stopped)', lline.includes('mining') === anyLive, lline);
  const sstat = await (await fetch('/api/stats')).json();
  add('live totals match v1.3 release',
      sstat.papers_total === 21014 && sstat.papers_mined === 19390 &&
      sstat.papers_with_data === 1307 && sstat.datasets_total === 2289,
      [sstat.papers_total, sstat.papers_mined, sstat.papers_with_data, sstat.datasets_total].join('/'));
  sel.value = 'parkinsons-disease'; sel.dispatchEvent(new Event('change'));
  await sleep(3000);
  const cards = [...document.querySelectorAll('#rec-list .record')];
  add('theme filter applies', cards.length > 0 && cards.every(c => c.innerText.includes("Parkinson's disease")), cards.length + ' cards');
  document.querySelector('.seg-btn[data-mode="datasets"]').click();
  await sleep(3000);
  add('datasets mode renders', document.querySelectorAll('#rec-list .ds-card').length > 0);
  add('disease chip on dataset cards', document.querySelectorAll('#rec-list .ds-card .chip.dz').length > 0);
  document.querySelector('.seg-btn[data-mode="papers"]').click();
  await sleep(1500);
  add('records not editable/deletable', document.querySelectorAll('#rec-list button').length === 0);
  return JSON.stringify(out);
})()
"""

CHAT_JS = r"""
(async () => {
  const out = [];
  const add = (n, ok, d = '') => out.push({ n, ok: !!ok, d: String(d).slice(0, 160) });
  const log = document.getElementById('chat-log') ? document.getElementById('chat-log').innerText : '';
  add('chat refusal for out-of-domain question', log.includes('outside what I do'), log.slice(0, 90));
  add('chat asks which disease when ambiguous', log.includes('which disease'), '');
  add('chat offers catalog themes as choices', log.includes('Parkinson'), '');
  const st = await (await fetch('/api/miner/status')).json();
  const anyLive = (st.processes || []).some(p => p.status === 'running');
  const line = document.getElementById('live-status').textContent;
  add('miner line truthful (after chat)', line.includes('mining') === anyLive, line);
  return JSON.stringify(out);
})()
"""
SCREENS = [
    ("01_home", "", 800),
    ("02_diseases", "document.getElementById('diseases').scrollIntoView();", 600),
    ("03_ledger", "document.getElementById('ledger').scrollIntoView();", 600),
    ("04_ledger_datasets",
     "document.querySelector('.seg-btn[data-mode=\"datasets\"]').click();"
     "document.getElementById('ledger').scrollIntoView();", 3000),
    ("05_chat_refusal",
     "document.querySelector('.seg-btn[data-mode=\"papers\"]').click(); window.scrollTo(0,0);"
     "openChat(); document.getElementById('chat-input').value=\"what's the weather in Paris today?\";"
     "chatSend();", 15000),
    ("06_chat_clarify",
     "const i=document.getElementById('chat-input'); i.value='single cell RNA sequencing brain'; chatSend();",
     15000),
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--round", type=int, default=1)
    args = ap.parse_args()
    outdir = ROOT / "docs" / "paper" / "final" / "reviews" / f"round-{args.round}" / "web"
    outdir.mkdir(parents=True, exist_ok=True)

    subprocess.Popen([EDGE, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                      "--window-size=1680,1040", "--remote-debugging-port=9222",
                      "--remote-allow-origins=*",
                      r"--user-data-dir=C:\Users\Administrator\AppData\Local\Temp\edge-shot",
                      URL])
    page = None
    for _ in range(40):
        try:
            targets = json.loads(urllib.request.urlopen("http://127.0.0.1:9222/json", timeout=3).read())
            pages = [t for t in targets if t.get("type") == "page"]
            page = next((t for t in pages if "8000" in (t.get("url") or "")), None) or (pages[0] if pages else None)
            if page:
                break
        except Exception:
            pass
        time.sleep(1)
    ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=180)

    def send(mid, method, params=None):
        ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            m = json.loads(ws.recv())
            if m.get("id") == mid:
                return m

    time.sleep(3)
    res = send(1, "Runtime.evaluate", {"expression": ASSERT_JS, "awaitPromise": True,
                                       "returnByValue": True})
    val = res.get("result", {}).get("result", {}).get("value")
    rows = json.loads(val) if val else []
    fails = [r for r in rows if not r["ok"]]
    print(f"assertions: {len(rows) - len(fails)}/{len(rows)} passed")
    for r in fails:
        print("  FAIL:", r["n"], r["d"])

    mid = 100
    for name, js, wait in SCREENS:
        if js:
            send(mid, "Runtime.evaluate", {"expression": "(async()=>{%s})()" % js,
                                           "awaitPromise": True, "returnByValue": True})
            mid += 1
        time.sleep(max(0, wait) / 1000.0)
        shot = send(mid, "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
        mid += 1
        data = shot.get("result", {}).get("data")
        if data:
            (outdir / f"{name}.png").write_bytes(base64.b64decode(data))
            print("  saved", name)

    res2 = send(mid, "Runtime.evaluate", {"expression": CHAT_JS, "awaitPromise": True,
                                          "returnByValue": True})
    val2 = res2.get("result", {}).get("result", {}).get("value")
    rows2 = json.loads(val2) if val2 else []
    fails2 = [r for r in rows2 if not r["ok"]]
    print(f"chat assertions: {len(rows2) - len(fails2)}/{len(rows2)} passed")
    for r in fails2:
        print("  FAIL:", r["n"], r["d"])
    rows.extend(rows2)
    (outdir / "assertions.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2),
                                            encoding="utf-8")
    ws.close()


if __name__ == "__main__":
    main()
