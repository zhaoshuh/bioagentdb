"""BioAgentDB 自己的 LLM 客户端（OpenAI 兼容，走 .env 的 DeepSeek 配置）。

所有请求/响应都落**内容寻址缓存**（data/llm_cache/<sha256>.json）：
命中即返回，未命中才调用 API 并写入。
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from app.core.config import cfg
from app.core.utils import client

logger = logging.getLogger(__name__)


def _cache_dir() -> Path:
    p = Path(cfg.get("data_dir")) / "llm_cache"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _cache_key(model: str, messages: list[dict], json_mode: bool, tools: Any,
               temperature: float) -> str:
    payload = json.dumps({"model": model, "messages": messages, "json_mode": json_mode,
                          "tools": tools, "temperature": temperature},
                         sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _cache_get(key: str) -> dict | None:
    f = _cache_dir() / f"{key}.json"
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return None
    return None


def _cache_put(key: str, result: dict) -> None:
    try:
        (_cache_dir() / f"{key}.json").write_text(
            json.dumps(result, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.warning("llm cache write failed: %s", exc)


def chat(messages: list[dict], *, model: str | None = None, json_mode: bool = False,
         temperature: float = 0.0, timeout: float = 180.0, tools: list | None = None,
         tool_choice: Any = None) -> dict:
    """一次对话补全 → {content, tool_calls, finish_reason}。命中缓存则不发请求。"""
    model = model or cfg.get("deepseek_model")
    use_cache = cfg.bool("llm_cache", True)
    key = _cache_key(model, messages, json_mode, tools, temperature) if use_cache else ""

    if use_cache and key:
        hit = _cache_get(key)
        if hit is not None:
            return hit

    url = f"{(cfg.get('deepseek_base_url') or 'https://api.deepseek.com').rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {cfg.get('deepseek_api_key')}",
        "Content-Type": "application/json",
    }
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    if tools:
        body["tools"] = tools
        if tool_choice:
            body["tool_choice"] = tool_choice
    resp = client().post(url, headers=headers, json=body, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    content = msg.get("content")
    if isinstance(content, list):  # 多段内容
        content = "".join(str(b.get("text", "")) for b in content if isinstance(b, dict))
    result = {
        "content": content or "",
        "tool_calls": msg.get("tool_calls") or [],
        "finish_reason": choice.get("finish_reason") or "",
        "usage": data.get("usage") or {},
    }
    if use_cache and key:
        _cache_put(key, result)
    return result


def chat_json(messages: list[dict], **kwargs) -> dict:
    """JSON 模式 → dict（失败返回 {}）。"""
    try:
        out = chat(messages, json_mode=True, **kwargs)
    except Exception as exc:  # noqa: BLE001
        logger.warning("chat_json failed: %s", exc)
        return {}
    text = (out.get("content") or "").strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def available() -> bool:
    return bool(cfg.get("deepseek_api_key"))


def cache_stats() -> dict:
    files = list(_cache_dir().glob("*.json"))
    size = sum(f.stat().st_size for f in files)
    return {"entries": len(files), "bytes": size, "dir": str(_cache_dir())}
