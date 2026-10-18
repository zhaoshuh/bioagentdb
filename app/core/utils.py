"""通用工具：HTTP（智能代理）、正则、JSON、时间。"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from html import unescape
from threading import RLock
from typing import Any

import httpx

from app.core.config import cfg

logger = logging.getLogger(__name__)

_client: httpx.Client | None = None
_client_lock = RLock()


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def new_id(prefix: str) -> str:
    import uuid
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def norm_space(text: Any) -> str:
    if text is None:
        return ""
    return re.sub(r"\s+", " ", str(text)).strip()


_TAG_RE = re.compile(r"<[^>]+>")


def clean_html(text: Any) -> str:
    """反复 unescape + 剥标签（处理 &lt;sup&gt; 这类多级编码），再归一空白。"""
    if not text:
        return ""
    s = str(text)
    for _ in range(4):
        prev = s
        s = _TAG_RE.sub(" ", unescape(s))
        if s == prev:
            break
    return norm_space(s)


def grounded_quote(identifier: str, *texts: tuple[str, str]) -> tuple[str, str] | None:
    """在 (zone, text) 中找包含 identifier 的那句话，返回 (zone, sentence)。

    用于保证"记录里的登记号"一定能在其证据引文里看到（引文自洽）。
    """
    needle = (identifier or "").strip().upper()
    if not needle:
        return None
    for zone, text in texts:
        t = text or ""
        i = t.upper().find(needle)
        if i < 0:
            base = re.sub(r"(\.V\d+(?:\.P\d+)?|/V\d+)$", "", needle)
            if base and base != needle:
                i = t.upper().find(base)
        if i < 0:
            continue
        start = max(t.rfind(". ", 0, i), t.rfind("\n", 0, i), t.rfind("; ", 0, i),
                    t.rfind("(", 0, i))
        start = start + 1 if start >= 0 else 0
        end = len(t)
        for sep in (". ", "! ", "? ", "\n"):
            j = t.find(sep, i)
            if j >= 0:
                end = min(end, j + len(sep))
        return zone, norm_space(t[start:end])[:600]
    return None


def acc_in_text(identifier: str, text: str) -> bool:
    """登记号是否出现在文本中（与 grounded_quote 同口径：大小写不敏感 + 版本后缀宽容）。

    L-0300 修复：此前多处写成 `acc in text.upper()`（只归一化文本、未归一化 acc），
    小写形态登记号（phs…、10.5281/zenodo…、ng…、syn…）被系统性误判为未命中。
    """
    needle = (identifier or "").strip().upper()
    if not needle:
        return False
    hay = (text or "").upper()
    if needle in hay:
        return True
    base = re.sub(r"(\.V\d+(?:\.P\d+)?|/V\d+)$", "", needle)
    return bool(base) and base != needle and base in hay


def norm_key(text: Any) -> str:
    s = norm_space(text).lower()
    s = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", s)
    return s


def json_loads(text: str | bytes | None, default: Any) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return default


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def parse_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    m = re.search(r"(\d[\d,._ ]*)", str(value))
    if not m:
        return None
    try:
        return int(m.group(1).replace(",", "").replace(" ", "").replace("_", ""))
    except ValueError:
        return None


def parse_year(value: Any) -> int | None:
    if value is None:
        return None
    m = re.search(r"(19|20)\d{2}", str(value))
    return int(m.group(0)) if m else None


def split_terms_like(text: str) -> list[str]:
    """把检索词切成用于 LIKE 的 token（≥2 字符），兼容中文连续词。"""
    if not text:
        return []
    import unicodedata
    raw = unicodedata.normalize("NFKC", str(text))
    parts = re.split(r"[\s,;/|()\[\]{}<>\"'+\-—–]+", raw)
    stop = {"and", "or", "not", "the", "of", "in", "for", "with", "on", "to",
            "a", "an", "at", "by", "is", "are", "was", "were",
            "data", "dataset", "datasets", "disease", "diseases"}
    out = []
    for p in parts:
        p = p.strip().strip("'\"")
        if len(p) >= 2 and p.lower() not in stop:
            out.append(p)
    return out[:12]


# ---------------------------------------------------------------- HTTP

def proxy_for_url(url: str) -> str | None:
    """同 omics-discovery 的智能代理规则：.cn/本地直连，其余走代理（若配置）。"""
    from omics_discovery.smart_proxy import proxy_for
    return proxy_for(url)


def client() -> httpx.Client:
    global _client
    with _client_lock:
        if _client is None:
            _client = httpx.Client(
                timeout=httpx.Timeout(cfg.float("request_timeout", 30.0), connect=10.0),
                follow_redirects=True,
                headers={"User-Agent": cfg.get("user_agent")},
                limits=httpx.Limits(max_connections=32, max_keepalive_connections=16),
                trust_env=False,  # 只走我们自己的代理逻辑（直连优先）
            )
        return _client


def _proxy_client() -> httpx.Client | None:
    proxy = cfg.get("http_proxy")
    if not proxy:
        return None
    return httpx.Client(
        timeout=httpx.Timeout(cfg.float("request_timeout", 30.0), connect=12.0),
        follow_redirects=True, proxy=proxy, trust_env=False,
        headers={"User-Agent": cfg.get("user_agent")},
    )


def _request(method: str, url: str, *, params=None, headers=None, json_body=None,
             timeout: float | None = None, retries: int = 2) -> httpx.Response | None:
    """直连优先；传输失败时自动切本地代理重试；429/5xx 退避重试。"""
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        resp = None
        # 1) 直连（注意：代理兜底里的 with 变量不能叫 client，否则本行 client 会被
        #    Python 视为局部变量而抛 UnboundLocalError——曾导致直连路径整条失效）
        try:
            resp = client().request(method, url, params=params, headers=headers,
                                    json=json_body, timeout=timeout or None)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            logger.debug("direct http attempt failed: %s (%s)", exc, url)
        # 2) 代理兜底
        if resp is None:
            try:
                pc = _proxy_client()
                if pc is not None:
                    with pc as proxy_client:
                        resp = proxy_client.request(method, url, params=params,
                                                    headers=headers,
                                                    json=json_body,
                                                    timeout=timeout or None)
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                logger.debug("proxy http attempt failed: %s (%s)", exc, url)
        if resp is None:
            if attempt < retries:
                import time
                time.sleep(0.8 + attempt * 1.2)
            continue
        if resp.status_code in (429, 500, 502, 503, 504) and attempt < retries:
            import time
            time.sleep(1.0 + attempt * 2.0)
            continue
        return resp
    if last_exc:
        return None
    return None


def get_json(url: str, params: dict | None = None, headers: dict | None = None,
             retries: int = 2, timeout: float | None = None) -> Any:
    resp = _request("GET", url, params=params, headers=headers, retries=retries,
                    timeout=timeout)
    if resp is None or resp.status_code != 200:
        return None
    try:
        return resp.json()
    except Exception:  # noqa: BLE001
        return None


def get_text(url: str, params: dict | None = None, headers: dict | None = None,
             retries: int = 2, timeout: float | None = None) -> str:
    resp = _request("GET", url, params=params, headers=headers, retries=retries,
                    timeout=timeout)
    if resp is None:
        return ""
    return resp.text


def http_status(url: str) -> tuple[bool, str]:
    """链接可达性（HEAD 优先，失败退回 GET；403/401 视为存在但拒绝自动访问）。"""
    if not url or not url.startswith("http"):
        return False, "invalid url"
    safe = (400, 401, 403, 405, 406, 429)
    for method in ("HEAD", "GET"):
        resp = _request(method, url, retries=1)
        if resp is not None:
            if resp.status_code < 400:
                return True, f"HTTP {resp.status_code}"
            if resp.status_code in safe:
                return True, f"HTTP {resp.status_code} (存在但拒绝自动访问)"
    return False, "unreachable"