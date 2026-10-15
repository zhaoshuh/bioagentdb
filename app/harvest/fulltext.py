"""全文与补充材料：Europe PMC fullTextXML（DA 优先抽取）+ supplementaryFiles 清单。

按用户要求：不解析补充材料内容，只登记 file_name + url。
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET

from app.core import utils
from app.core.db import db, utcnow

logger = logging.getLogger(__name__)

_EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest"
_SMALL_SUPP_ZIP = 2 * 1024 * 1024  # 2MB：小包下载枚举文件名；大包只记 bundle


def fetch_paper_assets(paper: dict) -> dict:
    """给一篇论文取 (fulltext_xml, da_text, supp_files)。

    fulltext 会用 zlib 压缩后存库（通常 100-700KB 原文 → ~30-60KB）。
    返回 {ok, fulltext, da_text, supp_files, fulltext_source, note}。
    """
    pmcid = paper.get("pmcid") or ""
    pmid = paper.get("pmid") or ""
    out = {"ok": False, "fulltext": "", "da_text": "", "supp_files": [],
           "fulltext_source": paper.get("fulltext_source", ""), "note": ""}
    xml_text = ""
    source = ""

    if pmcid:
        xml_text = utils.get_text(f"{_EPMC}/{pmcid}/fullTextXML", timeout=60, retries=4)
        if _valid_xml(xml_text):
            source = "europepmc_fulltext"
        else:
            # EBI fullTextXML 间歇 500 → NCBI efetch 兜底（OA 子集，JATS XML）
            xml_text = _ncbi_efetch(pmcid)
            if _valid_xml(xml_text):
                source = "ncbi_efetch"

    if not xml_text and paper.get("is_preprint") and paper.get("doi"):
        xml_text = _fetch_preprint_epmc(paper)
        if _valid_xml(xml_text):
            source = "preprint"

    if xml_text:
        da_text = _extract_data_availability(xml_text)
        supp = _list_supplementary(pmcid) if paper.get("has_suppl") else []
        out.update({
            "ok": True,
            "fulltext": xml_text,
            "da_text": da_text,
            "supp_files": supp,
            "fulltext_source": source,
        })
    # 无全文：da_text = ""，由 abstract 承担证据来源
    return out


def _valid_xml(xml_text: str) -> bool:
    return bool(xml_text and "<article" in xml_text[:3000])


def _ncbi_efetch(pmcid: str) -> str:
    """NCBI efetch PMC JATS XML（OA 文章；httpx 直连无 Schannel 问题）。"""
    return utils.get_text(
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
        params={"db": "pmc", "id": pmcid, "rettype": "xml", "retmode": "xml"},
        timeout=90, retries=3)


def _fetch_preprint_epmc(paper: dict) -> str:
    """bioRxiv/medRxiv 预印本在 Europe PMC 有 PPR 记录，尝试抓其全文 XML。"""
    return ""


def _extract_data_availability(xml_text: str) -> str:
    """从 JATS XML 抽出 Data Availability 段正文（含小标题，截断 8000 字）。"""
    da_title_re = re.compile(
        r"(data\s*availability|data\s*accessib|availability\s*of\s*(the\s+)?data|"
        r"data\s*deposition|accession\s*number|data\s*access\b|"
        r"availability\s*of\s*(the\s+)?data)",
        re.IGNORECASE)
    for m in re.finditer(r"<title[^>]*>(.*?)</title>", xml_text, re.IGNORECASE | re.DOTALL):
        title = re.sub(r"<[^>]+>", "", m.group(1)).strip()
        if not da_title_re.search(title):
            continue
        start = m.end()
        nxt = re.search(r"<title[^>]*>", xml_text[start:], re.IGNORECASE)
        end = start + (nxt.start() if nxt else 8000)
        body = re.sub(r"<[^>]+>", " ", xml_text[start:end])
        body = re.sub(r"\s+", " ", body).strip()
        if body:
            return body[:8000]
    return ""


def _list_supplementary(pmcid: str) -> list[dict]:
    """扫描补充材料：只登记 file_name / url，不解析内容。

    小 zip（≤2MB）下载后枚举文件名；大 zip 只登记打包文件本身（避免每个论文
    下载几十 MB 只为看名字）。
    """
    if not pmcid:
        return []
    url = f"{_EPMC}/{pmcid}/supplementaryFiles"
    size = _head_size(url)
    if size is not None and size <= _SMALL_SUPP_ZIP:
        data = _download_bytes(url)
        if data:
            names = _zipfile_names(data)
            if names:
                return [{"name": n, "url": url} for n in names[:200]]
    # 大包 / 不可探测 / 下载失败：登记打包入口
    if size is not None:
        note = "large bundle (not enumerated)" if size > _SMALL_SUPP_ZIP else "size unknown"
    else:
        note = "size unknown"
    return [{"name": f"{pmcid}_supplementaryFiles.zip" if size is not None
             else f"{pmcid}_supplementary.bundle",
             "url": url, "note": note}]


def _head_size(url: str) -> int | None:
    import httpx
    try:
        r = httpx.head(url, timeout=8, follow_redirects=True,
                       headers={"User-Agent": "BioAgentDB/0.1"})
        cl = r.headers.get("content-length")
        return int(cl) if cl else None
    except Exception:  # noqa: BLE001
        return None


def _download_bytes(url: str) -> bytes | None:
    import httpx
    try:
        with httpx.Client(timeout=120, follow_redirects=True) as c:
            r = c.get(url)
            r.raise_for_status()
            return r.content
    except Exception:  # noqa: BLE001
        return None


def _zipfile_names(data: bytes) -> list[str]:
    import io
    import zipfile
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            return [n for n in zf.namelist() if not n.endswith("/")]
    except Exception:  # noqa: BLE001
        return []


def compress_text(text: str) -> bytes:
    import zlib
    return zlib.compress(text.encode("utf-8", errors="replace"), 6)


def decompress_text(data: bytes) -> str:
    import zlib
    try:
        return zlib.decompress(data).decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return ""


def mark_paper_mined(paper_id: str, *, ok: bool, note: str = "") -> None:
    with db() as conn:
        conn.execute(
            "UPDATE papers SET mined_status=?, mined_at=?, updated_at=? WHERE id=?",
            ("fetched" if ok else "error", utcnow(), utcnow(), paper_id),
        )