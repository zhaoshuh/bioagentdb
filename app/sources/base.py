"""多源论文发现：统一记录模型 + 限速/缓存 HTTP 工具。

试点隔离：缓存与产物写 data/pilot/，不触碰线上库与快照。
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core import utils

PILOT_ROOT = Path(__file__).resolve().parents[2] / "data" / "pilot"
CACHE_DIR = PILOT_ROOT / "http_cache"


@dataclass
class RawPaper:
    source: str
    source_id: str
    doi: str = ""
    pmid: str = ""
    pmcid: str = ""
    title: str = ""
    abstract: str = ""
    year: int | None = None
    journal: str = ""
    authors: list[str] = field(default_factory=list)
    is_preprint: bool = False
    is_oa: bool = False
    oa_url: str = ""
    landing_url: str = ""
    license: str = ""
    extra: dict = field(default_factory=dict)

    def to_row(self) -> dict[str, Any]:
        row = dict(self.__dict__)
        row["authors"] = json.dumps(self.authors, ensure_ascii=False)
        row.pop("extra", None)
        return row


class SourceAdapter:
    """发现源适配器基类：search(theme, queries, want, seen_ids) -> [RawPaper]。"""

    name = "base"
    rate_s = 0.3

    def search(self, theme: str, queries: list[str], want: int,
               seen_ids: set[str]) -> list[RawPaper]:
        raise NotImplementedError

    def _throttle(self) -> None:
        time.sleep(self.rate_s)


def cache_path(source: str, key: str) -> Path:
    h = hashlib.sha1(key.encode("utf-8")).hexdigest()
    return CACHE_DIR / source / f"{h}.json"


def cached_request(source: str, method: str, url: str, *, params=None, headers=None,
                   json_body=None, use_cache: bool = True) -> Any:
    """HTTP（直连优先→代理兜底）+ 磁盘缓存（只缓存 200 的 JSON）。"""
    key = json.dumps({"m": method, "u": url, "p": params, "j": json_body},
                     sort_keys=True, ensure_ascii=False)
    p = cache_path(source, key)
    if use_cache and p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))["payload"]
        except Exception:  # noqa: BLE001
            pass
    resp = utils._request(method, url, params=params, headers=headers,
                          json_body=json_body, retries=2)
    if resp is None or resp.status_code != 200:
        return None
    try:
        payload = resp.json()
    except Exception:  # noqa: BLE001
        return None
    if use_cache:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"url": url, "payload": payload},
                                ensure_ascii=False), encoding="utf-8")
    return payload


def cached_text(source: str, method: str, url: str, *, params=None, headers=None,
                use_cache: bool = True) -> str:
    """同上，但返回文本（PubMed efetch XML 用）。"""
    key = json.dumps({"m": method, "u": url, "p": params, "text": True},
                     sort_keys=True, ensure_ascii=False)
    p = cache_path(source, key)
    if use_cache and p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))["text"]
        except Exception:  # noqa: BLE001
            pass
    resp = utils._request(method, url, params=params, headers=headers, retries=2)
    if resp is None or resp.status_code != 200:
        return ""
    text = resp.text
    if use_cache:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"url": url, "text": text}, ensure_ascii=False),
                     encoding="utf-8")
    return text
