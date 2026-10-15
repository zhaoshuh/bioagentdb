"""合并器：三份对抗报告 → 去重/仲裁/排序 → 一份行动清单。"""

from __future__ import annotations

import json
import logging
import time

from app.harvest.llm import chat_json
from app.redteam.playbook import CONSOLIDATE_PROMPT

logger = logging.getLogger(__name__)

SEVERITY_ORDER = {"high": 0, "mid": 1, "low": 2}


def _present(record: dict, paper: dict) -> bool:
    """确定性存在性（L-0306b）：与门 T1 同口径——大小写归一 + 版本后缀宽容。"""
    from app.core.utils import acc_in_text

    hay = f"{paper.get('abstract') or ''}\n{paper.get('da_text') or ''}"
    return bool(acc_in_text(record.get("identifier") or "", hay))


def normalize_findings(reports: dict) -> dict:
    """把三份报告转成 {adversary: [finding,...]} 的规范结构。"""
    out: dict = {}
    for name, rep in (reports or {}).items():
        findings = []
        for f in (rep or {}).get("findings") or []:
            if isinstance(f, dict) and f.get("claim"):
                findings.append({
                    "target": str(f.get("target") or "paper"),
                    "severity": str(f.get("severity") or "low"),
                    "claim": str(f.get("claim") or ""),
                    "evidence": str(f.get("evidence") or ""),
                    "expected_action": str(f.get("expected_action") or "verify"),
                })
        out[name] = findings
    return out


def consolidate(paper: dict, records: list[dict], reports: dict,
                *, use_llm: bool = True) -> list[dict]:
    """返回决策清单 [{target, severity, action, detail}]，按严重度排序。"""
    norm = normalize_findings(reports)
    total = sum(len(v) for v in norm.values())
    if total == 0:
        return []
    # 发现很少（≤2）且无 reject 时无需 LLM 仲裁，直接规则合并 → 省一次调用；
    # 含 reject 时必须经 LLM 合并（REJECT SCOPE 守门，L-0302/V-2）。
    has_reject = any(f.get("expected_action") == "reject"
                     for findings in norm.values() for f in findings)
    if total <= 2 and not has_reject:
        return _deterministic_fallback(norm)
    if use_llm:
        try:
            data = chat_json([{
                "role": "system",
                "content": CONSOLIDATE_PROMPT,
            }, {
                "role": "user",
                "content": json.dumps({
                    "records": [
                        {**{k: r.get(k) for k in ("identifier", "repository", "title",
                                                  "sample_count", "role")},
                         "present_in_text": _present(r, paper)}
                        for r in records
                    ] or "none",
                    "veracity": norm.get("veracity") or [],
                    "coverage": norm.get("coverage") or [],
                    "metadata": norm.get("metadata") or [],
                }, ensure_ascii=False),
            }], model=None, timeout=90)
            decisions = []
            for d in data.get("decisions") or []:
                if isinstance(d, dict) and d.get("action"):
                    decisions.append({
                        "target": str(d.get("target") or "paper"),
                        "severity": str(d.get("severity") or "low"),
                        "action": str(d.get("action")),
                        "detail": str(d.get("detail") or ""),
                    })
            if decisions:
                return sorted(decisions, key=lambda d: SEVERITY_ORDER.get(d["severity"], 3))
        except Exception as exc:  # noqa: BLE001
            logger.warning("consolidate LLM failed: %s", exc)
    return _deterministic_fallback(norm)


def _deterministic_fallback(norm: dict) -> list[dict]:
    """LLM 不可用时的规则合并：降低误伤，只保留跨对抗者共识/直接证据。"""
    out: list[dict] = []
    seen: set[tuple] = set()
    for findings in norm.values():
        for f in findings:
            key = (f["target"], f["expected_action"])
            if key in seen:
                continue
            seen.add(key)
            out.append({"target": f["target"], "severity": f["severity"],
                        "action": f["expected_action"], "detail": f["claim"][:200]})
    return sorted(out, key=lambda d: SEVERITY_ORDER.get(d["severity"], 3))