"""M3 · 提示词变体注册表（版本化，运行时叠加）。

stage 目前：`structure`（结构化抽取）。学到的变体写入 `learned/prompts/<stage>.json`。
"""

from __future__ import annotations

import json
from pathlib import Path

from app.core.db import utcnow

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "learned" / "prompts"

# v1：更强调"仅 JSON、字段尽量填、覆盖所有标识号、给出 disease"
STRUCTURE_V1 = """You are a biomedical data curator. Candidate dataset identifiers found
verbatim in a paper are listed below. For EACH identifier that is a dataset produced or
released by THIS paper, output one JSON record.

Identifiers: {identifiers}

Paper title: {title}
Abstract: {abstract}
Data Availability: {da_text}

Return ONLY a JSON object: {{"records": [ ... ]}} where each record has EXACTLY these keys:
"identifier" (verbatim), "title", "description" (text-grounded; "" if unsupported),
"modality" (one of {modalities}), "organism" ("" if unstated), "tissue" ("" if unstated),
"disease" ("" if unstated), "platform" ("" if unstated), "sample_count" (integer, 0 if unknown),
"role" ("primary"|"reuse"), "on_request_only" (true/false), "relevant" (true/false).

Hard rules: output valid JSON only (no prose, no markdown); never invent identifiers; fill as
many fields as the text supports; include EVERY listed identifier you can justify."""


def _path(stage: str) -> Path:
    return PROMPTS_DIR / f"{stage}.json"


def read(stage: str) -> dict | None:
    p = _path(stage)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def current(stage: str) -> str | None:
    d = read(stage)
    return (d or {}).get("template")


def write(stage: str, name: str, template: str, *, metrics: dict | None = None) -> None:
    PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
    _path(stage).write_text(json.dumps(
        {"name": name, "template": template, "metrics": metrics or {},
         "source": "auto", "added_at": utcnow()}, ensure_ascii=False, indent=2),
        encoding="utf-8")
