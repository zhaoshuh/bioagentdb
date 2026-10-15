"""视觉对抗审查工具：截图 -> opencode go 视觉模型 -> 三名科研工作者批判 -> 报告。

用法:
  python scripts/ui_review.py               # 截当前页 + 一轮三视角审查
  python scripts/ui_review.py --round 2     # 第二轮（报告存 data/reviews/roundN.json）

需要设置：网页 Settings -> OpenCode Go API key（opencode.ai console 的 Go 订阅 key）
（环境变量 OPENCODE_GO_API_KEY 或数据库 settings.go_api_key）
"""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import cfg

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
BASE_URL_KEY = "go_base_url"
MODEL_KEY = "vision_model"

# 三名科研工作者的审查视角（合并为一次视觉调用，要求分别输出）
PERSONAS = """You are reviewing this webpage as THREE separate experts. For EACH expert, give
a numbered list of concrete issues, then a final verdict (pass/needs-fix) with the 3 most
important fixes. Be specific and visual (spacing, alignment, contrast, hierarchy, density,
empty states, motion, type), never generic.

Expert 1 - Clinical researcher (end user): can I immediately understand what this site
offers and trust it? Is the information useful and readable at a glance?

Expert 2 - Bioinformatics analyst (data searcher): can I quickly scan and compare records?
Are identifiers, evidence and filters prominent and legible?

Expert 3 - Design lead: is the page distinctive or generic? Typography scale, rhythm,
whitespace, alignment, color use, card layout, responsiveness, any cliches or slop.

Output ONLY JSON:
{"experts":[{"expert":1,"issues":["..."],"verdict":"pass|needs-fix","top_fixes":["..."]}, ...]}"""


def screenshot(url: str, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [EDGE, "--headless=new", "--disable-gpu", "--hide-scrollbars",
         "--window-size=1680,1000", f"--screenshot={out}", "--virtual-time-budget=15000", url],
        capture_output=True, timeout=90)
    return out


def vision_review(image_path: Path) -> dict:
    """调用 opencode go 视觉模型审查截图。"""
    from app.core.utils import client

    base = cfg.get(BASE_URL_KEY) or "https://api.opencode.ai/v1"
    key = cfg.get("go_api_key")
    model = cfg.get(MODEL_KEY) or "deepseek-v4-flash-vision-exp"
    if not key:
        return {"error": "go_api_key not configured (Settings page)"}
    b64 = base64.b64encode(image_path.read_bytes()).decode()
    messages = [{"role": "user", "content": [
        {"type": "text", "text": PERSONAS},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
    ]}]
    resp = client().post(
        f"{base.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"model": model, "messages": messages, "max_tokens": 2000},
        timeout=240,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"].get("content") or ""
    if isinstance(content, list):
        content = "".join(str(b.get("text", "")) for b in content if isinstance(b, dict))
    try:
        start = content.find("{")
        end = content.rfind("}")
        return json.loads(content[start:end + 1]) if start >= 0 else {"raw": content}
    except json.JSONDecodeError:
        return {"raw": content}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--round", type=int, default=1)
    parser.add_argument("--url", default="http://127.0.0.1:8000/")
    parser.add_argument("--no-shot", action="store_true", help="复用已有截图")
    args = parser.parse_args()

    reviews_dir = Path(cfg.data_dir) / "reviews"
    shot = reviews_dir / f"shot_round{args.round}.png"
    if not args.no_shot:
        shot = screenshot(args.url, shot)
        print(f"screenshot: {shot} ({shot.stat().st_size // 1024} KB)")

    result = vision_review(shot)
    report_path = reviews_dir / f"round{args.round}.json"
    report_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if "error" in result:
        print("ERROR:", result["error"])
    else:
        for e in result.get("experts", []):
            print(f"--- Expert {e.get('expert')}: {e.get('verdict')} ---")
            for i in e.get("issues", [])[:8]:
                print(f"  - {i}")
    print(f"report: {report_path}")


if __name__ == "__main__":
    main()