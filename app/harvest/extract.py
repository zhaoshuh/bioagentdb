"""记录抽取：只在 abstract 与 Data Availability 两区挖数据标识，LLM 结构化 + 证据引用。

补充材料按用户要求不解析，只登记 link+filename（fulltext.py 完成）。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.core import vocab
from app.core.utils import acc_in_text, norm_space, parse_int
from app.core.config import cfg

logger = logging.getLogger(__name__)

# 额外登记号正则（复用 omics-discovery 论文挖掘的正则）
_extended_acc_re = None


def _extended_regex():
    global _extended_acc_re
    if _extended_acc_re is None:
        try:
            from omics_discovery.sources.extended import _PAPER_ACC_RE
            _extended_acc_re = _PAPER_ACC_RE
        except Exception:  # noqa: BLE001
            _extended_acc_re = re.compile(r"(?!)")
    return _extended_acc_re


_ACCESSION_PATTERNS: list[tuple[str, re.Pattern]] = []
for _repo, _spec in vocab.REPOSITORIES.items():
    for _p in _spec["patterns"]:
        _ACCESSION_PATTERNS.append((_repo, re.compile(_p, re.I)))

_URL_HINTS = [
    (re.compile(r"https?://(?:www\.)?zenodo\.org/(?:record|records)/(\d+)", re.I), "Zenodo"),
    (re.compile(r"https?://(?:www\.)?figshare\.com/s/([0-9a-f]{16,})", re.I), "Figshare"),
    (re.compile(r"https?://(?:www\.)?ebi\.ac\.uk/(?:pride/archive/projects|pride/ws/archive/v2/projects)/([A-Za-z0-9]+)", re.I), "PRIDE"),
    (re.compile(r"https?://(?:www\.)?ncbi\.nlm\.nih\.gov/geo/query/acc\.cgi\?acc=(GSE\d+)", re.I), "GEO"),
    (re.compile(r"https?://cellxgene\.cziscience\.com/(?:collections|datasets)/([0-9a-fA-F-]{36})", re.I), "CELLxGENE"),
    (re.compile(r"https?://(?:www\.)?ebi\.ac\.uk/(ena/browser/view|metabolights/editor)/([A-Za-z0-9]+)", re.I), None),  # 用第二组
]

_SENTENCE_RE = re.compile(r"(?<=[.!?。！？])\s+")


def _acc_case(acc: str) -> str:
    """dbGaP 的 phs 登记号保持小写（规范形式）；其余统一大写。"""
    a = (acc or "").strip()
    return a.lower() if a.lower().startswith("phs") else a.upper()


def find_identifiers(text: str) -> list[dict]:
    """在文本里找数据标识，返回 [{acc, repository}]（去重保序）。"""
    if not text:
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for repo, pattern in _ACCESSION_PATTERNS:
        for m in pattern.finditer(text):
            acc = _acc_case(m.group(0))
            key = f"{repo}|{acc}"
            if key in seen:
                continue
            seen.add(key)
            out.append({"acc": acc, "repository": repo})
    # URL 提示
    for pattern, repo in _URL_HINTS:
        for m in pattern.finditer(text):
            groups = m.groups()
            acc = groups[-1] if groups else ""
            if not acc:
                continue
            acc = _acc_case(acc)
            if len(acc) < 3:
                continue
            repo_name = repo or acc.split("-")[0]
            key = f"{repo_name}|{acc}"
            if key in seen:
                continue
            seen.add(key)
            out.append({"acc": acc, "repository": repo_name})
    # 扩展正则
    for m in _extended_regex().finditer(text):
        acc = _acc_case(m.group(0))
        repo = vocab.repository_for(acc)
        if not repo:
            continue
        key = f"{repo}|{acc}"
        if key in seen:
            continue
        seen.add(key)
        out.append({"acc": acc, "repository": repo})
    return out


def evidence_for(text: str, identifier: str) -> str:
    """定位包含标识的原句（+相邻一句），作为论文内证引用。"""
    if not text:
        return ""
    sentences = _SENTENCE_RE.split(text)
    for i, s in enumerate(sentences):
        if acc_in_text(identifier, s):
            head = sentences[i - 1] if i > 0 and len(sentences[i - 1]) < 200 else ""
            tail = sentences[i + 1] if i + 1 < len(sentences) and len(sentences[i + 1]) < 200 else ""
            quote = norm_space(" ".join(x for x in (head, s, tail) if x))
            return quote[:700]
    return ""


# ---------------------------------------------------------------- no_accessions 二次复核（L-0246）

_NOACC_PROMPT = """You are a biomedical data-mining assistant. A paper was processed, but our
standard identifier patterns found NO dataset accession in it. Re-read the text below and check
whether the paper actually announces data (a dataset, database deposit, or archive record).

Rules:
- Only report identifiers or URLs that appear VERBATIM in the text below (copy them exactly).
- Do NOT report data "available from the authors on request", or data that appears only inside
  supplementary file names.
- Do NOT guess or invent anything. If nothing concrete is announced, return an empty list.
- "quote" must be the exact sentence from the text containing that identifier/URL.

Return ONLY JSON:
{{"items": [{{"accession": "", "url": "", "quote": "", "repository": ""}}]}}

## Paper
Title: {title}
Abstract: {abstract}
Data Availability: {da_text}"""

_DOMAIN_REPO = {
    "zenodo.org": "Zenodo", "figshare.com": "Figshare", "datadryad.org": "Dryad",
    "dryad.org": "Dryad", "osf.io": "OSF", "ncbi.nlm.nih.gov": "NCBI",
    "ebi.ac.uk": "EBI", "ngdc.cncb.ac.cn": "NGDC", "bigd.big.ac.cn": "NGDC",
    "openneuro.org": "OpenNeuro", "physionet.org": "PhysioNet", "ebrains.eu": "EBRAINS",
    "archive.org": "Internet Archive", "github.com": "GitHub", "metabolights": "MetaboLights",
    "niagads.org": "NIAGADS", "finngen.fi": "FinnGen", "findata.fi": "Findata",
    "fingenious.fi": "Fingenious", "finbb.fi": "FinnBB",
}


def _noacc_enabled() -> bool:
    try:
        return bool(cfg.int("miner_noacc_review", 1))
    except Exception:  # noqa: BLE001
        return True


def _repo_for_url(url: str) -> str:
    try:
        from urllib.parse import urlparse

        host = (urlparse(url).netloc or "").lower()
    except Exception:  # noqa: BLE001
        host = ""
    if host.startswith("www."):
        host = host[4:]
    for key, label in _DOMAIN_REPO.items():
        if key in host:
            return label
    return host or "web"


def _noacc_review(paper: dict, haystack: str, llm_chat) -> list[dict]:
    """无候选论文的二次复核：只采信"原文逐字"的编号/链接 + 逐字引句（防臆造）。"""
    prompt = _NOACC_PROMPT.format(
        title=(paper.get("title") or "")[:300],
        abstract=(paper.get("abstract") or "")[:3000],
        da_text=(paper.get("da_text") or "")[:3000])
    try:
        out = llm_chat([{"role": "user", "content": prompt}],
                       model=cfg.get("deepseek_scoring_model"), json_mode=True)
        data = out.get("content") or "{}"
        if isinstance(data, str):
            data = json.loads(data)
    except Exception as exc:  # noqa: BLE001
        logger.warning("noacc review LLM failed: %s", exc)
        return []
    items = data.get("items") if isinstance(data, dict) else None

    hay_low = norm_space(haystack).lower()
    findings: list[dict] = []
    seen: set[str] = set()
    for it in (items or []):
        if not isinstance(it, dict):
            continue
        acc = norm_space(it.get("accession"))[:80]
        url = norm_space(it.get("url"))[:500]
        if acc.lower().startswith("http"):
            url = url or acc
            acc = ""
        quote = norm_space(it.get("quote"))[:400]
        if acc and acc.lower() not in hay_low:
            acc = ""
        if url and url.lower() not in hay_low:
            url = ""
        if not acc and not url:
            continue
        token = acc or url
        if not quote or quote.lower() not in hay_low or token.lower() not in quote.lower():
            continue
        acc = _acc_case(acc) if acc else ""
        repo = vocab.repository_for(acc) if acc else ""
        known = bool(repo)
        if not repo and url:
            repo = _repo_for_url(url)
        key = f"{acc}|{url}".lower()
        if key in seen:
            continue
        seen.add(key)
        findings.append({"acc": acc, "url": url, "quote": quote,
                         "repository": repo, "known": known})
    return findings


def _log_misses(paper: dict, findings: list[dict]) -> None:
    """把复核发现沉淀到 extract_misses（供补正则/复盘；失败不影响主流程）。"""
    if not findings:
        return
    try:
        from app.core.db import db, utcnow
        from app.core.utils import new_id

        now = utcnow()
        pid = str(paper.get("id") or "")
        with db() as conn:
            for f in findings:
                acc, url = f.get("acc") or "", f.get("url") or ""
                dup = conn.execute(
                    "SELECT 1 FROM extract_misses WHERE paper_id=? AND accession=? AND url=?",
                    (pid, acc, url)).fetchone()
                if dup:
                    continue   # 去重（L-0259 / C-R54-6）：同论文同发现只留一条
                conn.execute(
                    "INSERT INTO extract_misses(id, paper_id, accession, url, repository, "
                    "quote, known, created_at) VALUES(?,?,?,?,?,?,?,?)",
                    (new_id("miss"), pid, acc, url, f.get("repository") or "",
                     (f.get("quote") or "")[:300], 1 if f.get("known") else 0, now))
    except Exception as exc:  # noqa: BLE001
        logger.warning("log extract_misses failed: %s", exc)


def _recovered_record(paper: dict, f: dict, abstract: str, da_text: str) -> dict | None:
    """复核发现 → 最终记录（L-0248：**只要有可达链接就入档**——门户链接亦可）。

    无链接（既无逐字 URL、又无已知仓库 canonical 链接）→ None（仅留学习日志）。
    """
    acc = str(f.get("acc") or "").strip()
    url = str(f.get("url") or "").strip()
    repo = str(f.get("repository") or "").strip()
    if not url and acc:
        c_repo, c_url, _dl, _cl = vocab.canonical_links(acc)
        if c_repo:
            repo, url = repo or c_repo, c_url
    if not url:
        return None
    ident = acc or url
    repo = repo or (vocab.repository_for(acc) if acc else "") or _repo_for_url(url)
    title0 = (paper.get("title") or "").strip()[:120]
    quote = (f.get("quote") or "")[:700]
    zone = "abstract"
    if da_text and ((acc and acc_in_text(acc, da_text)) or (url and url in da_text)):
        zone = "data_availability"
    return {
        "identifier": ident,
        "repository": repo,
        "repository_label": repo,
        "url": url,
        "download_url": "",
        "link_type": "doi" if url.startswith("https://doi.org/") else "portal",
        "title": f"{title0} — {ident}" if title0 else ident,
        "description": quote[:300],
        "modality": vocab.normalize_modality(quote, paper.get("title")),
        "organism": "",
        "tissue": "",
        "disease": "",
        "platform": "",
        "sample_count": 0,
        "role": "primary",
        "relevant": True,
        "evidence": {"zone": zone, "quote": quote},
    }


_STRUCTURE_PROMPT = """You are a biomedical data mining assistant. A paper related to the research
topic "{topic}" was searched, and these candidate dataset identifiers were found verbatim in the
paper (abstract and/or Data Availability statement):

{identifiers}

## Paper
Title: {title}
Abstract: {abstract}
Data Availability: {da_text}

## Task
Build one JSON record per identifier that is genuinely a dataset produced/released by
THIS paper (not merely citing someone else's data). For each record include:
- "identifier": the exact identifier string
- "title": short dataset title (from the text; else paper title + identifier)
- "description": 1-2 sentence description supported ONLY by the text above ("" if not supported)
- "modality": one of {modalities}
- "organism": "Homo sapiens"/"Mus musculus"/... or "" if not stated
- "tissue": e.g. "brain", "cerebrospinal fluid", "blood", "hippocampus" or ""
- "disease": the disease studied in the paper if any (short verbatim name), or "" if none/unstated
- "platform": e.g. "10x Genomics", "Illumina NovaSeq", "mass spectrometry" or ""
- "sample_count": integer if stated, else 0
- "role": "primary" if produced by this paper, "reuse" if the paper only reused/analyzed the dataset
- "on_request_only": true ONLY if the text says the data is available from authors on request
- "relevant": true if the dataset relates to the research topic "{topic}"

Rules: NEVER invent identifiers or fields not supported by the text; unknown fields = "" (or 0).
Output ONLY a JSON object: {{"records": [...]}}"""


def _modality_list_prompt() -> str:
    return ", ".join(vocab.MODALITIES.values())


def structure_records(
    paper: dict,
    identifiers: list[dict],
    *,
    llm_chat=None,
    topic: str = "",
) -> list[dict]:
    """LLM 结构化（flash）；失败/不可用时退化为每标识一条最小记录。`topic` 注入提示词（L-0225）。"""
    template = _STRUCTURE_PROMPT
    try:
        from app.improve import prompts as _prompts
        template = _prompts.current("structure") or _STRUCTURE_PROMPT
    except Exception:  # noqa: BLE001
        pass
    prompt = template.format(
        identifiers=json.dumps([i["acc"] for i in identifiers], ensure_ascii=False),
        title=paper.get("title", ""),
        abstract=(paper.get("abstract") or "")[:1500],
        da_text=(paper.get("da_text") or "")[:2000],
        modalities=_modality_list_prompt(),
        topic=(topic or "the requested research topic"),
    )
    data: dict = {}
    if llm_chat is not None:
        try:
            out = llm_chat([{"role": "user", "content": prompt}],
                           model=cfg.get("deepseek_scoring_model"),
                           json_mode=True)
            data = out.get("content") or "{}"
            if isinstance(data, str):
                data = json.loads(data)
        except Exception as exc:  # noqa: BLE001
            logger.warning("structure_records LLM failed: %s", exc)
            data = {}
    records = [r for r in (data.get("records") or []) if isinstance(r, dict)]
    if not records:
        _t = (paper.get("title") or "").strip()[:120]
        records = [{"identifier": i["acc"], "role": "primary", "relevant": True,
                    "title": f"{_t} — {i['acc']}" if _t else i["acc"],
                    "description": (paper.get("da_text") or paper.get("abstract") or "")[:300]}
                   for i in identifiers]
    return records


def extract_from_paper(paper: dict, *, llm_chat=None, topic: str = "") -> dict:
    """一篇论文 → {records, identifiers, no_accessions, error}。`topic` 透传（L-0225）。

    L-0246：无候选（no_accessions）时做二次 LLM 复核——只采信"原文逐字"的编号/链接
    （+ 逐字引句）；发现沉淀到 `extract_misses`（供补正则，下次主路径直接命中）。
    """
    abstract = paper.get("abstract") or ""
    da_text = paper.get("da_text") or ""
    haystack = f"{abstract}\n{da_text}"
    identifiers = find_identifiers(haystack)
    if not identifiers and llm_chat is not None and _noacc_enabled():
        recovered = _noacc_review(paper, haystack, llm_chat)
        _log_misses(paper, recovered)
        enriched = [r for r in (_recovered_record(paper, f, abstract, da_text)
                                for f in recovered) if r]
        if enriched:
            return {"records": enriched,
                    "identifiers": [{"acc": f.get("acc") or f.get("url") or "",
                                     "repository": f.get("repository") or ""}
                                    for f in recovered],
                    "no_accessions": False, "error": ""}
    if not identifiers:
        return {"records": [], "identifiers": [], "no_accessions": True, "error": ""}

    records = structure_records(paper, identifiers, llm_chat=llm_chat, topic=topic)
    enriched: list[dict] = []
    for rec in records:
        if not isinstance(rec, dict):
            continue
        if rec.get("on_request_only"):
            continue
        acc = _acc_case(rec.get("identifier"))
        match = next((i for i in identifiers if i["acc"] == acc), None)
        if match is None:
            continue
        repo, url, download, label = vocab.canonical_links(acc)
        if not repo:
            continue
        zone = "data_availability" if da_text and acc_in_text(acc, da_text) else "abstract"
        quote = evidence_for(da_text if zone == "data_availability" else abstract, acc)
        if not quote:
            zone = "data_availability" if da_text else "abstract"
            quote = evidence_for(da_text or abstract, acc)
        record = {
            "identifier": acc,
            "repository": repo,
            "repository_label": label,
            "url": url,
            "download_url": download,
            "link_type": "portal",
            "title": (norm_space(rec.get("title")) or (
                f"{(paper.get('title') or '').strip()[:120]} — {acc}"
                if (paper.get("title") or "").strip() else acc)),
            "description": norm_space(rec.get("description")),
            "modality": vocab.normalize_modality(
                rec.get("modality"), rec.get("description"), rec.get("title")),
            "organism": norm_space(rec.get("organism")),
            "tissue": norm_space(rec.get("tissue")),
            "disease": norm_space(rec.get("disease")),
            "platform": norm_space(rec.get("platform")),
            "sample_count": parse_int(rec.get("sample_count")),
            "role": str(rec.get("role") or "primary").lower(),
            "relevant": bool(rec.get("relevant", True)),
            "evidence": {"zone": zone, "quote": quote},
        }
        # 登记号有时是 DOI 形式（Zenodo/Dryad），URL 规范化为 doi.org
        if record["url"].startswith("https://doi.org/"):
            record["link_type"] = "doi"
        enriched.append(record)
    return {"records": enriched, "identifiers": identifiers,
            "no_accessions": not enriched, "error": ""}