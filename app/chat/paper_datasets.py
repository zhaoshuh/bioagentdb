"""论文内数据集提取（v1.5 W3）：四路全文 + DA 段优先 + 引文佐证。

路由（按序尝试）：
  1. PMC/EPMC XML（`fetch_paper_assets`，免费）
  2. 预印本 PPR XML（同函数分支，免费）
  3. CORE：`GET /v3/works/{id}` fullText → 无则 downloadUrl PDF（需代理）
  4. OpenAlex：`content.openalex.org/works/{id}.pdf`（付费，缓存 + 会话上限）

只存引文与链接；全文/PDF 仅本地处理与缓存（data/workbench_cache/）。
"""

from __future__ import annotations

import io
import json
import logging
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from app.core import utils
from app.core.config import cfg
from app.harvest.extract import evidence_for, find_identifiers

logger = logging.getLogger(__name__)

CACHE_DIR = cfg.data_dir / "workbench_cache"

_DA_HEAD_RE = re.compile(
    r"(data\s*availability|data\s*accessib|availability\s*of\s*(the\s+)?data|"
    r"data\s*deposition|data\s*access\b|availability\s*of\s*data|"
    r"data\s*and\s*(code|materials)\s*availability)",
    re.IGNORECASE)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- extraction

def extract_da_from_text(text: str, cap: int = 6000) -> str:
    """纯文本（PDF 等）里的 DA 段：按标题行定位，取后文 cap 字符；找不到返回空。"""
    if not text:
        return ""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if _DA_HEAD_RE.search(line[:120]) and len(line.strip()) < 120:
            body = "\n".join(lines[i:i + 60])
            return re.sub(r"\s+", " ", body).strip()[:cap]
    # 段落级兜底（标题与正文同段）
    m = _DA_HEAD_RE.search(text)
    if m:
        return re.sub(r"\s+", " ", text[m.start():m.start() + cap]).strip()
    return ""


def datasets_from_text(text: str, *, is_xml: bool) -> tuple[list[dict], bool]:
    """从全文提取数据集公告。

    返回 (datasets, da_evidence)：datasets=[{accession, repository, quote}]；
    da_evidence=True 表示来自 DA 段（高精度），False 表示全文兜底扫描。
    """
    if not text:
        return [], False
    da_text = ""
    if is_xml:
        from app.harvest.fulltext import _extract_data_availability
        da_text = _extract_data_availability(text)
    else:
        da_text = extract_da_from_text(text)
    scan_text, from_da = (da_text, True) if da_text else (text, False)
    out: list[dict] = []
    seen: set[str] = set()
    for item in find_identifiers(scan_text):
        acc = item["acc"]
        key = acc.upper()
        if key in seen:
            continue
        seen.add(key)
        quote = evidence_for(scan_text, acc)
        out.append({"accession": acc, "repository": item.get("repository") or "",
                    "quote": quote, "da_evidence": from_da})
    return out, from_da


# ---------------------------------------------------------------- fulltext

def _cache_path(kind: str, key: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", key)[:80]
    return CACHE_DIR / kind / f"{safe}.json"


def _cache_get(kind: str, key: str) -> dict | None:
    p = _cache_path(kind, key)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return None
    return None


def _cache_put(kind: str, key: str, payload: dict) -> None:
    p = _cache_path(kind, key)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _pdf_text(data: bytes, max_pages: int = 40) -> str:
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        parts = []
        for page in reader.pages[:max_pages]:
            try:
                parts.append(page.extract_text() or "")
            except Exception:  # noqa: BLE001
                continue
        return "\n".join(parts)
    except Exception as exc:  # noqa: BLE001
        logger.warning("pdf extract failed: %s", exc)
        return ""


def fetch_fulltext(cand: dict, members: list[dict], *, pdf_budget_left: int) -> tuple[str, str]:
    """四路全文。返回 (text, kind)；kind ∈ pmc/preprint/core/core_pdf/openalex_pdf/none。"""
    pmcid = (cand.get("pmcid") or "").strip()
    doi = (cand.get("doi") or "").strip()
    is_preprint = bool(cand.get("is_preprint"))
    if pmcid:
        ck = _cache_get("pmc", pmcid)
        if ck is not None:
            return ck.get("text") or "", ck.get("kind") or "pmc"
        from app.harvest.fulltext import fetch_paper_assets
        assets = fetch_paper_assets({"pmcid": pmcid, "pmid": cand.get("pmid") or "",
                                     "doi": doi, "is_preprint": is_preprint})
        if assets.get("ok") and assets.get("fulltext"):
            kind = assets.get("fulltext_source") or "pmc"
            _cache_put("pmc", pmcid, {"text": assets["fulltext"], "kind": kind,
                                      "fetched_at": _utcnow()})
            return assets["fulltext"], kind
    if is_preprint and doi:
        ck = _cache_get("preprint", doi)
        if ck is not None:
            return ck.get("text") or "", ck.get("kind") or "preprint"
        from app.harvest.fulltext import fetch_paper_assets
        assets = fetch_paper_assets({"pmcid": "", "pmid": cand.get("pmid") or "",
                                     "doi": doi, "is_preprint": True})
        if assets.get("ok") and assets.get("fulltext"):
            _cache_put("preprint", doi, {"text": assets["fulltext"], "kind": "preprint",
                                         "fetched_at": _utcnow()})
            return assets["fulltext"], "preprint"

    for m in members or []:
        src = (m.get("source") or "").lower()
        sid = str(m.get("source_id") or "").strip()
        if not sid:
            continue
        if src == "core" and pdf_budget_left > 0:
            ck = _cache_get("core", sid)
            if ck is not None:
                return ck.get("text") or "", ck.get("kind") or "core"
            headers = {"Authorization": f"Bearer {cfg.get('core_api_key')}"}
            data = _core_work(sid, headers)
            text, kind = "", ""
            if isinstance(data, dict):
                ft = data.get("fullText") or data.get("fulltext") or ""
                if ft and len(str(ft)) > 500:
                    text, kind = str(ft), "core"
                else:
                    dl = str(data.get("downloadUrl") or "")
                    if dl:
                        resp = utils._request("GET", dl, retries=1, timeout=30)
                        if resp is not None and resp.status_code == 200 and \
                                resp.headers.get("content-type", "").lower().find("pdf") >= 0:
                            text = _pdf_text(resp.content)
                            kind = "core_pdf"
            if text:
                _cache_put("core", sid, {"text": text, "kind": kind,
                                         "fetched_at": _utcnow()})
                return text, kind
        if src == "openalex" and pdf_budget_left > 0:
            ck = _cache_get("openalex", sid)
            if ck is not None:
                return ck.get("text") or "", ck.get("kind") or "openalex_pdf"
            url = f"https://content.openalex.org/works/{sid}.pdf"
            params = {"api_key": cfg.get("openalex_api_key")}
            resp = utils._request("GET", url, params=params, retries=1, timeout=45)
            if resp is not None and resp.status_code == 200 and \
                    resp.headers.get("content-type", "").lower().find("pdf") >= 0:
                text = _pdf_text(resp.content)
                if text:
                    _cache_put("openalex", sid, {"text": text, "kind": "openalex_pdf",
                                                 "fetched_at": _utcnow()})
                    return text, "openalex_pdf"
    return "", "none"


def _core_work(core_id: str, headers: dict) -> dict | None:
    for url in (f"https://api.core.ac.uk/v3/works/{core_id}",
                f"https://api.core.ac.uk/v3/works/{core_id}/"):
        resp = utils._request("GET", url, headers=headers, retries=1, timeout=25)
        if resp is not None and resp.status_code == 200:
            try:
                return resp.json()
            except Exception:  # noqa: BLE001
                return None
    return None


def attach_datasets(candidates: list[dict], members_map: dict[str, list[dict]], *,
                    k: int = 8, pdf_cap: int = 20, pdf_used: int = 0,
                    on_progress=None) -> dict:
    """对 top-K 候选抓全文并提取论文内数据集（并发 3）。返回统计。"""
    from concurrent.futures import ThreadPoolExecutor

    subset = candidates[:k]
    stats = {"attempted": len(subset), "fulltext_ok": 0, "da_evidence": 0,
             "datasets": 0, "kinds": {}, "pdf_used": pdf_used}
    budget = max(0, pdf_cap - pdf_used)
    lock = threading.Lock()
    counter = {"n": 0}

    def _one(c: dict) -> None:
        members = members_map.get(c.get("gid") or "", [])
        text, kind = fetch_fulltext(c, members, pdf_budget_left=budget)
        c["fulltext_kind"] = kind
        ds: list[dict] = []
        if text:
            ds, from_da = datasets_from_text(text, is_xml=kind in (
                "europepmc_fulltext", "ncbi_efetch", "pmc_oa", "pmc", "preprint"))
            if from_da:
                stats["da_evidence"] += 1
        for d in ds:
            url, _dl, _label = "", "", ""
            try:
                from app.core import vocab
                _repo, url, _dl, _label = vocab.canonical_links(d["accession"])
            except Exception:  # noqa: BLE001
                pass
            d["url"] = url
        c["datasets"] = ds
        c.setdefault("t2", False)
        with lock:
            counter["n"] += 1
            i = counter["n"]
        if on_progress:
            kind_label = {"none": "no full text", "europepmc_fulltext": "EPMC XML",
                          "ncbi_efetch": "PMC (NCBI)", "pmc_oa": "PMC OA",
                          "preprint": "preprint XML", "core": "CORE full text",
                          "core_pdf": "CORE PDF",
                          "openalex_pdf": "OpenAlex PDF"}.get(kind, kind)
            on_progress(f"paper {i}/{len(subset)} · {kind_label} · "
                        f"{len(ds)} dataset(s) · {(c.get('title') or '')[:42]}")

    with ThreadPoolExecutor(max_workers=3) as pool:
        futs = [pool.submit(_one, c) for c in subset]
        for f in futs:
            try:
                f.result(timeout=90)
            except Exception as exc:  # noqa: BLE001
                logger.warning("fulltext worker failed: %s", exc)
    for c in subset:
        kind = c.get("fulltext_kind") or "none"
        stats["kinds"][kind] = stats["kinds"].get(kind, 0) + 1
        if kind != "none":
            stats["fulltext_ok"] += 1
        stats["datasets"] += len(c.get("datasets") or [])
    if "core_pdf" in stats["kinds"]:
        stats["pdf_used"] += stats["kinds"]["core_pdf"]
    if "openalex_pdf" in stats["kinds"]:
        stats["pdf_used"] += stats["kinds"]["openalex_pdf"]
    return stats
