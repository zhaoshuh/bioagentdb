"""M1c · 为自动发现的模式补 URL 模板并"探测验证"。

做法：取该模式的一个真实登记号样例 → 套用已知 URL 模板 → HTTP HEAD 验证 →
把 `url/url_verified/example` 写回 learned/patterns.json。
用法: python scripts/probe_patterns.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import learned  # noqa: E402
from app.core.db import db  # noqa: E402
from app.core.utils import http_status  # noqa: E402
from app.improve.propose import URL_TEMPLATES  # noqa: E402


def example_for(pattern: str) -> str:
    rx = re.compile(pattern, re.I)
    with db() as conn:
        for r in conn.execute("SELECT DISTINCT accession FROM dataset_links "
                              "WHERE accession != '' LIMIT 5000"):
            acc = r["accession"]
            if rx.fullmatch(acc):
                return acc
    return ""


def main() -> None:
    items = learned.read_patterns()
    if not items:
        print("no learned patterns")
        return
    changed = 0
    for p in items:
        repo, pattern = p.get("repository", ""), p.get("pattern", "")
        if p.get("url") and p.get("url_verified"):
            continue
        tmpl = URL_TEMPLATES.get(repo) or p.get("url") or ""
        if not tmpl:
            p["url_verified"] = False
            p["verify_status"] = "no template"
            continue
        ex = example_for(pattern)
        if not ex:
            p["url_verified"] = False
            p["verify_status"] = "no example accession in catalog"
            continue
        url = tmpl.format(acc=ex)
        ok, status = http_status(url)
        p["url"] = tmpl
        p["url_verified"] = bool(ok)
        p["example"] = ex
        p["verify_status"] = status
        changed += 1
        print(f"{repo} `{pattern}` -> {url} [{status}] verified={ok}")
    learned.write_patterns(items)
    ok_n = sum(1 for p in items if p.get("url_verified"))
    print(f"\npatterns: {len(items)} · url_verified: {ok_n} · updated: {changed}")


if __name__ == "__main__":
    main()
