"""确定性候选扫描：用扩展词表在论文文本里找登记号，补 LLM 抽取的漏。

不解析补充材料内容：只对 摘要 / Data Availability / 全文 / 补充清单(名称+URL) 做正则扫描。
"""

from __future__ import annotations

import re

from app.core import vocab
from app.core.utils import clean_html

_SENT = re.compile(r"(?<=[.!?。！？])\s+|\n+")
# 弱仓库（易误报）：句子里需出现数据语境才接受
_WEAK_CTX = re.compile(
    r"deposit|availab|accession|repository|archiv|dataset|supplement|publicly|"
    r"under (?:the )?(?:id|identifier|accession)|brows|download|portal|study|data\b", re.I)
_STRONG = {"GEO", "SRA", "ENA", "PRIDE", "GWASCatalog", "Synapse", "BioSample", "dbGaP",
           "EGA", "MassIVE", "MetaboLights", "BioStudies", "ArrayExpress",
           "SingleCellPortal", "CNGB", "NGDC-GSA", "jPOST", "PRIDE-DOI", "NIAGADS"}


def _compiled() -> list[tuple[str, re.Pattern]]:
    """每次扫描时取最新模式（含 learned/），保证闭环改进即时生效。"""
    return vocab.iter_accession_patterns()


def _sentence(text: str, idx: int) -> str:
    start = max(text.rfind(". ", 0, idx), text.rfind("\n", 0, idx))
    start = start + 1 if start >= 0 else 0
    end = len(text)
    for sep in (". ", "! ", "? ", "\n"):
        j = text.find(sep, idx)
        if j >= 0:
            end = min(end, j + len(sep))
    return re.sub(r"\s+", " ", text[start:end]).strip()[:400]


def scan_texts(texts: list[tuple[str, str]],
               extra_patterns: list[tuple[str, re.Pattern]] | None = None) -> list[dict]:
    """texts = [(zone, text)] → [{repository, accession, zone, quote}]（按首次出现去重）。

    extra_patterns：额外 (repo, compiled)（用于沙盒评测未提交的提议）。
    """
    found: dict[str, dict] = {}
    pats = _compiled() + list(extra_patterns or [])
    for zone, text in texts:
        t = clean_html(text or "")
        if not t:
            continue
        for name, pat in pats:
            for m in pat.finditer(t):
                acc = m.group(0).strip()
                key = acc.lower()
                if key in found:
                    continue
                quote = _sentence(t, m.start())
                if name not in _STRONG and not _WEAK_CTX.search(quote):
                    continue   # 弱仓库 + 无数据语境 → 丢弃（防参考文献/误报）
                found[key] = {"repository": name, "accession": acc, "zone": zone,
                              "quote": quote}
    return list(found.values())


def candidates_for_paper(paper: dict, supp_files: list | None = None) -> list[dict]:
    """从论文对象（abstract / da_text / supp_files）抽候选。

    注意：**不扫 fulltext**——全文中大量登记号来自参考文献/方法，非"该论文公告的数据"，
    实测会把 FP 从个位数抬到上百（见台账 L-0036）。只扫摘要 + Data Availability + 补充清单。
    """
    texts: list[tuple[str, str]] = [
        ("abstract", paper.get("abstract") or ""),
        ("data_availability", paper.get("da_text") or ""),
    ]
    for sf in (supp_files or []):
        name = sf.get("name") or ""
        url = sf.get("url") or ""
        texts.append(("supplementary", f"{name} {url}"))
    return scan_texts(texts)
