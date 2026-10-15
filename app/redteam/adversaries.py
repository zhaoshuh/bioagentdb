"""三名对抗者：并行审查单篇论文的预备记录，各出一份 JSON 报告。"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor

from app.harvest.llm import chat_json
from app.redteam.playbook import PLAYBOOKS

logger = logging.getLogger(__name__)

ADVERSARIES = ["veracity", "coverage", "metadata"]


def _payload(paper: dict, records: list[dict]) -> str:
    import json

    from app.core.utils import acc_in_text

    # （L-0306b）评审文本与门 T1 同量级全文：旧 2000/2500 截断曾让长 DA 列表尾部的
    # 登记号对评审层"不可见" → 被误判 absent → 误拒。
    abstract = paper.get("abstract") or ""
    da_text = paper.get("da_text") or ""
    hay = f"{abstract}\n{da_text}"
    return json.dumps({
        "title": paper.get("title", ""),
        "abstract": abstract[:9000],
        "data_availability": da_text[:8000],
        "records": [
            {**{k: r.get(k) for k in ("identifier", "repository", "title", "description",
                                      "modality", "organism", "tissue", "disease", "platform",
                                      "sample_count", "role", "evidence")},
             "present_in_text": bool(acc_in_text(r.get("identifier") or "", hay))}
            for r in records
        ] or "none",
    }, ensure_ascii=False)


def attack(paper: dict, records: list[dict], *, timeout_s: int = 90) -> dict:
    """三名对抗者并行攻击。返回 {adversary: {findings:[...], ms}}。"""
    payload = _payload(paper, records)
    out: dict = {}

    def _one(name: str):
        t0 = time.time()
        try:
            data = chat_json(
                [{"role": "system", "content": PLAYBOOKS[name]},
                 {"role": "user", "content": payload}],
                model=None, timeout=timeout_s)
            findings = data.get("findings") or []
            findings = [f for f in findings if isinstance(f, dict) and f.get("claim")]
        except Exception as exc:  # noqa: BLE001
            logger.warning("adversary %s failed: %s", name, exc)
            findings = []
        return name, {"findings": findings[:10], "ms": int((time.time() - t0) * 1000)}

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(_one, name) for name in ADVERSARIES]
        for fut in futures:
            try:
                name, result = fut.result(timeout=timeout_s + 15)
                out[name] = result
            except Exception as exc:  # noqa: BLE001
                logger.warning("adversary worker failed: %s", exc)
    for name in ADVERSARIES:
        out.setdefault(name, {"findings": [], "ms": 0})
    return out