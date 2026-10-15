"""学习产物注册表（L-0038 M0）。

版本化的、可审计的"学到的规则"：
- learned/patterns.json   自动/人工发现的登记号模式（仓库/正则/URL 模板/证据/来源）
- learned/policies.json   阈值（候选门、置信下限等）
运行时叠加到内置词表之上；产物版本化记录于 `learned/`。
"""

from __future__ import annotations

import json
from pathlib import Path

from app.core.db import utcnow

LEARNED_DIR = Path(__file__).resolve().parents[1] / "learned"
PATTERNS = LEARNED_DIR / "patterns.json"
POLICIES = LEARNED_DIR / "policies.json"


def _read(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def read_patterns() -> list[dict]:
    return _read(PATTERNS, [])


def write_patterns(items: list[dict]) -> None:
    LEARNED_DIR.mkdir(parents=True, exist_ok=True)
    PATTERNS.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def add_pattern(item: dict) -> dict:
    """追加/更新一条模式（按 repository+pattern 去重），带版本与时间。"""
    items = read_patterns()
    key = (item.get("repository", ""), item.get("pattern", ""))
    for existing in items:
        if (existing.get("repository", ""), existing.get("pattern", "")) == key:
            existing.update(item)
            write_patterns(items)
            return existing
    item = dict(item)
    item.setdefault("version", 1)
    item.setdefault("added_at", utcnow())
    items.append(item)
    write_patterns(items)
    return item


def read_policies() -> dict:
    return _read(POLICIES, {"precision_floor": 0.97, "recall_gain_min": 0.02, "max_accept": 3})


def write_policies(d: dict) -> None:
    LEARNED_DIR.mkdir(parents=True, exist_ok=True)
    POLICIES.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def summary() -> dict:
    pats = read_patterns()
    return {"patterns": len(pats), "repositories": sorted({p.get("repository") for p in pats}),
            "policies": read_policies(), "dir": str(LEARNED_DIR)}


def clear_patterns() -> None:
    write_patterns([])
