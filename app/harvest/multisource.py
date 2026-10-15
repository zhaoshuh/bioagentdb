"""矿机补给多源化（B）：EPMC 保留 + OpenAlex/CORE/PubMed/预印本 过滤式接入。

过滤（防稀释）:
  F1 本地数据信号：标题+摘要命中登记号模式 或 DA 短语；
  F2 EPMC HAS_DATA 旗标：批量 DOI/PMID 查询（(DOI:"…" OR …) AND HAS_DATA:Y）。
写入: papers(mined_status=pending) + paper_themes（相关性守卫）+ paper_sources（溯源）。
"""

from __future__ import annotations

import json
import logging
import re
import time

from app.core.config import cfg
from app.core.db import db, utcnow
from app.core.relevance import theme_relevant
from app.core.utils import new_id
from app.harvest.dedup import norm_doi, norm_pmid, norm_title
from app.sources.base import cached_request

logger = logging.getLogger(__name__)

_EPMC = cfg.get("sources_europepmc_search_url",
                "https://www.ebi.ac.uk/europepmc/webservices/rest/search")

DA_RE = re.compile(
    r"data (?:are|is|have been|were|has been) (?:available|deposited|uploaded|freely)|"
    r"available (?:at|from|in|via|through) (?:the )?(?:GEO|SRA|ENA|PRIDE|Zenodo|figshare|"
    r"Dryad|ArrayExpress|dbGaP|EGA|GSA|OMIX|BioProject|NCBI)|"
    r"accession (?:number|no|code)|data availability statement|"
    r"deposited (?:in|at|with|to) (?:the )?(?:GEO|SRA|ENA|PRIDE|Zenodo|figshare|Dryad|"
    r"ArrayExpress|dbGaP|EGA|GSA|OMIX|NCBI|BioProject)", re.I)

# EPMC 的 HAS_DATA 旗标对老文献不可靠（实测 1970s 文章亦被标记）→ 年线下限
MIN_YEAR = 2015

THEME_QUERIES: dict[str, list[str]] = {
    "alzheimers-disease": ["Alzheimer disease", "Alzheimer's disease", "dementia"],
    "als": ["amyotrophic lateral sclerosis", "motor neuron disease"],
    "parkinsons-disease": ["Parkinson disease", "Parkinson's disease"],
    "frontotemporal-dementia": ["frontotemporal dementia",
                                "frontotemporal lobar degeneration"],
    "huntingtons-disease": ["Huntington disease", "Huntington's disease"],
    "prion-disease": ["Creutzfeldt-Jakob disease", "prion disease"],
    "progressive-supranuclear-palsy": ["progressive supranuclear palsy"],
    "dementia-with-lewy-bodies": ["Lewy body dementia", "dementia with Lewy bodies"],
    "multiple-system-atrophy": ["multiple system atrophy"],
    "spinocerebellar-ataxia": ["spinocerebellar ataxia"],
    "breast-cancer": ["breast cancer", "triple-negative breast cancer"],
}


def _data_signal(row: dict) -> bool:
    text = f"{row.get('title') or ''} {row.get('abstract') or ''}"
    from app.harvest.extract import find_identifiers
    return bool(find_identifiers(text)) or bool(DA_RE.search(text))


def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def epmc_has_data(dois: list[str], pmids: list[str]) -> tuple[set, set]:
    """批量问 EPMC：这些 DOI/PMID 里哪些带 HAS_DATA:Y。"""
    hit_d, hit_p = set(), set()
    for ch in _chunks(sorted(set(d for d in dois if d)), 15):
        q = "(" + " OR ".join(f'DOI:"{d}"' for d in ch) + ") AND HAS_DATA:Y"
        data = cached_request("filter", "GET", _EPMC, params={
            "query": q, "format": "json", "pageSize": 100, "resultType": "lite"})
        for item in ((data or {}).get("resultList") or {}).get("result") or []:
            d = norm_doi(str(item.get("doi") or ""))
            if d in ch:
                hit_d.add(d)
        time.sleep(0.2)
    for ch in _chunks(sorted(set(p for p in pmids if p)), 15):
        q = "(" + " OR ".join(f"EXT_ID:{p}" for p in ch) + ") AND HAS_DATA:Y"
        data = cached_request("filter", "GET", _EPMC, params={
            "query": q, "format": "json", "pageSize": 100, "resultType": "lite"})
        for item in ((data or {}).get("resultList") or {}).get("result") or []:
            p = norm_pmid(str(item.get("id") or ""))
            if p in ch:
                hit_p.add(p)
        time.sleep(0.2)
    return hit_d, hit_p


def fetch_candidates(theme: str, want: int, per_source: int = 20,
                     queries: list[str] | None = None) -> list[dict]:
    """多源候选（不含 EPMC）→ F1 本地信号 ∪ F2 EPMC 旗标 → 去重。

    L-0217 修复 C-1 / L-0221（R48）：`queries` 非空时作为检索面（任务合并集，**任务检索式在前**），
    每个适配器取前 **3** 条；空串过滤；缺省回落硬编码 `THEME_QUERIES` / 主题名。
    """
    from app.sources.core_api import CoreAdapter
    from app.sources.europepmc import PreprintsAdapter
    from app.sources.openalex import OpenAlexAdapter
    from app.sources.pubmed import PubMedAdapter

    qs = [q.strip() for q in (queries or THEME_QUERIES.get(theme) or [theme.replace("-", " ")])
          if q and q.strip()]
    seen: set[str] = set()
    raw: list[dict] = []
    for adapter in (OpenAlexAdapter(), CoreAdapter(), PubMedAdapter(), PreprintsAdapter()):
        try:
            papers = adapter.search(theme, qs[:3], per_source, seen)
            raw.extend(p.to_row() for p in papers)
        except Exception as exc:  # noqa: BLE001
            logger.warning("refill source %s failed: %s", adapter.name, exc)

    # 批内去重 + 年线下限（HAS_DATA 旗标对老文献不可靠）
    dedup: dict[str, dict] = {}
    for r in raw:
        key = norm_doi(r.get("doi") or "") or norm_pmid(r.get("pmid") or "") or \
            norm_title(r.get("title") or "")
        if key and key not in dedup:
            dedup[key] = r
    items = [r for r in dedup.values()
             if r.get("year") is None or int(r.get("year") or 0) >= MIN_YEAR]

    f1 = [r for r in items if _data_signal(r)]
    rest = [r for r in items if r not in f1]
    hit_d, hit_p = epmc_has_data(
        [norm_doi(r.get("doi") or "") for r in rest],
        [norm_pmid(r.get("pmid") or "") for r in rest])
    f2 = [r for r in rest
          if (norm_doi(r.get("doi") or "") and norm_doi(r["doi"]) in hit_d)
          or (norm_pmid(r.get("pmid") or "") and norm_pmid(r["pmid"]) in hit_p)]
    for r in f1:
        r["_filter"] = "f1"
    for r in f2:
        r["_filter"] = "f2"
    passed = (f1 + f2)[:want]
    logger.info("refill candidates: raw=%d f1=%d f2=%d passed=%d",
                len(items), len(f1), len(f2), len(passed))
    return passed


def insert_candidates(theme: str, cands: list[dict], max_new: int) -> int:
    """入库（pending + 主题守卫 + 溯源）。返回新增数。"""
    added = 0
    now = utcnow()
    for c in cands:
        if added >= max_new:
            break
        doi = norm_doi(c.get("doi") or "")
        pmid = norm_pmid(c.get("pmid") or "")
        pmcid = (c.get("pmcid") or "").strip()
        title = (c.get("title") or "").strip()
        with db() as conn:
            dup = conn.execute(
                """SELECT id FROM papers WHERE (? != '' AND doi = ?)
                   OR (? != '' AND pmid = ?) OR (? != '' AND pmcid = ?)
                   OR (? != '' AND title = ?) LIMIT 1""",
                (doi, doi, pmid, pmid, pmcid, pmcid, title, title)).fetchone()
            if dup:
                if theme_relevant(theme, title, c.get("abstract") or ""):
                    conn.execute(
                        "INSERT OR IGNORE INTO paper_themes(paper_id, theme, source, "
                        "created_at) VALUES(?,?,?,?)",
                        (dup["id"], theme, "multisource", now))
                continue
            pid = new_id("paper")
            authors = c.get("authors")
            if isinstance(authors, str):
                try:
                    authors = ", ".join(json.loads(authors))
                except Exception:  # noqa: BLE001
                    authors = authors
            conn.execute(
                """INSERT INTO papers(id, pmid, pmcid, doi, title, journal, year, authors,
                     abstract, is_preprint, is_oa, fulltext_source, fulltext, da_text,
                     supp_files, mined_status, mined_at, created_at, updated_at, theme,
                     has_db_xref, has_suppl)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (pid, pmid, pmcid, doi, title, c.get("journal") or "", c.get("year"),
                 authors or "", c.get("abstract") or "",
                 int(c.get("is_preprint") or 0), int(c.get("is_oa") or 0),
                 "", "", "", "[]", "pending", "", now, now, theme, 0, 0))
            if theme_relevant(theme, title, c.get("abstract") or ""):
                conn.execute(
                    "INSERT OR IGNORE INTO paper_themes(paper_id, theme, source, created_at) "
                    "VALUES(?,?,?,?)", (pid, theme, "multisource", now))
            conn.execute(
                """INSERT OR IGNORE INTO paper_sources(paper_id, source, source_id, url,
                     license, retrieved_at) VALUES(?,?,?,?,?,?)""",
                (pid, c.get("source") or "", c.get("source_id") or "",
                 c.get("landing_url") or c.get("oa_url") or "", c.get("license") or "", now))
        added += 1
    return added


def refill_multisource(theme: str, max_new: int, *, queries: list[str] | None = None,
                       progress=None, recent_days: int = 0, task_id: str = "") -> int:
    """EPMC sweep（保留 HAS_DATA 查询）+ 多源过滤接入。

    返回新增数；`recent_days>0` 时检索面加"近 N 天"窗口（巡检增量，L-0213；**只对 EPMC 段**）；
    L-0217 修复 C-1：非巡检轮四源候选同样消费调用方检索式（原样透传，不加窗）；
    **全部来源都失败时抛 RuntimeError**（与"确实零新增"区分——L-0211 修复 F4 / L-0212 修复 C-1）。
    """
    added = 0
    ok_sources = 0
    errs: list[str] = []
    from app.harvest.themes import get_theme

    th = get_theme(theme)
    raw_qs = [q for q in (queries or ((th or {}).get("facets") or {}).get("base_queries", [])) if q]
    qs = list(raw_qs)
    if qs and recent_days > 0:
        from app.harvest.sweep import window_queries
        qs = window_queries(qs, recent_days)
    if qs:
        try:
            from app.harvest.sweep import sweep_papers

            ok_q = 0
            for q in qs:
                if added >= max_new:
                    break
                out = sweep_papers(q, max_papers=max_new - added, theme=theme, progress=progress)
                if out.get("error") and not out.get("added") and added == 0:
                    # 同一源首错即抛（有部分产出则保留）；多源路径由上层 catch 记 errs
                    raise RuntimeError(f"EPMC request failed for query {q[:50]!r}")
                ok_q += 1
                added += out["added"]
            if qs and ok_q == 0:
                raise RuntimeError("all EPMC queries failed")
            ok_sources += 1
        except Exception as exc:  # noqa: BLE001
            errs.append(f"europepmc: {exc}")
            logger.warning("EPMC sweep part failed: %s", exc)
    # 无检索式：不算源站错误（返 0 = 真零，可挖尽；L-0215）
    if added < max_new and recent_days <= 0:
        # 巡检（recent_days>0）只扫 EPMC 窗口增量：多源候选无日期过滤，跳过以免多花与语义走样（L-0214）
        try:
            cands = fetch_candidates(theme, max_new - added, queries=(raw_qs or None))
            added += insert_candidates(theme, cands, max_new - added)
            ok_sources += 1
        except Exception as exc:  # noqa: BLE001
            errs.append(f"multisource: {exc}")
            logger.warning("multisource part failed: %s", exc)
    if added == 0 and errs:
        # 只要有任何一段失败且**零产出** → 不能确认"确实零新增"（部分失败也抛；L-0212 修复 C-1）
        raise RuntimeError("refill incomplete/failed (added=0) -> " + "; ".join(errs)[:300])
    return added
