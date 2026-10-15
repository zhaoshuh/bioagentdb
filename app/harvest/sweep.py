"""论文扫描：Europe PMC（含 bioRxiv/medRxiv 预印本）分页拉取 ALZ 主题论文。"""

from __future__ import annotations

import json
import logging
import re
from typing import Iterator

from app.core.db import db, utcnow
from app.core.utils import clean_html, get_json, new_id, parse_year

logger = logging.getLogger(__name__)

from app.core.config import cfg  # noqa: E402

SEARCH_URL = cfg.get("sources_europepmc_search_url",
                     "https://www.ebi.ac.uk/europepmc/webservices/rest/search")
PAGE_SIZE = cfg.int("sources_europepmc_page_size", 100)


def _result_to_paper(item: dict) -> dict | None:
    pmid = str(item.get("id") or "").strip()
    source = str(item.get("source") or "").upper()  # MED / PPR (preprint) / PMC ...
    if not pmid and not item.get("doi"):
        return None
    is_preprint = 1 if source == "PPR" else 0
    is_oa = 1 if str(item.get("isOpenAccess") or "").lower() in ("y", "true", "1") else 0
    has_db_xref = 1 if str(item.get("hasDbCrossReferences") or "").lower() in ("y", "true", "1") else 0
    has_suppl = 1 if str(item.get("hasSuppl") or "").lower() in ("y", "true", "1") else 0
    title = clean_html(item.get("title"))
    if not title:
        return None
    authors = clean_html(item.get("authorString"))
    journal = clean_html((item.get("journalInfo") or {}).get("journal", {}).get("title"))
    year = parse_year(item.get("pubYear")) or parse_year(item.get("firstPublicationDate"))
    return {
        "id": new_id("paper"),
        "pmid": pmid,
        "pmcid": str(item.get("pmcid") or "").strip(),
        "doi": str(item.get("doi") or "").strip().lower(),
        "title": title,
        "journal": journal,
        "year": year,
        "authors": authors,
        "abstract": clean_html(item.get("abstractText")),
        "is_preprint": is_preprint,
        "is_oa": is_oa,
        "fulltext_source": ("pmc_oa" if is_oa and item.get("pmcid") else
                            ("preprint" if is_preprint else "")),
        "has_db_xref": has_db_xref,
        "has_suppl": has_suppl,
        "supp_files": [],
        "fulltext": "",
        "da_text": "",
        "mined_status": "pending",
        "created_at": utcnow(),
    }


def sweep_papers(query: str, *, max_papers: int, cursor: dict | None = None,
                 progress=None, theme: str = "", link_only: bool = False,
                 max_pages: int = 200) -> dict:
    """用 cursorMark 游标分页扫描 Europe PMC（分页稳定、页面不重叠）。

    - 命中已存在的论文：不再跳过，而是补一条主题归属（多主题）
    - link_only=True：只补归属，绝不插入新论文（回填用）
    返回 {added, skipped, linked, cursor_next:{cursor_mark}}。
    """
    cursor_mark = (cursor or {}).get("cursor_mark", "*")
    added = 0
    skipped = 0
    linked = 0
    hit_count = 0
    page_error = False
    params = {
        "query": query,
        "format": "json",
        "pageSize": PAGE_SIZE,
        "resultType": "core",  # 注意：Europe PMC 的 sort 参数现会返回 503，不要加
    }
    for _page in range(max_pages):
        if not link_only and added >= max_papers:
            break
        params["cursorMark"] = cursor_mark
        data = get_json(SEARCH_URL, params=params)
        if data is None:
            # 请求失败（≠ 空结果）：显式记录为 error，交由调用方判定（L-0211 修复 F4）
            logger.warning("Europe PMC sweep failed (cursorMark %s)", cursor_mark)
            page_error = True
            break
        hit_count = int(data.get("hitCount", 0))
        results = ((data.get("resultList") or {}).get("result")) or []
        if not results:
            break
        for item in results:
            paper = _result_to_paper(item)
            if paper is None:
                continue
            with db() as conn:
                existing = _find_duplicate(conn, paper)
                if existing:
                    if theme and _link_theme(conn, existing["id"], theme,
                                             existing["theme"] or ""):
                        linked += 1
                    skipped += 1
                    continue
                if link_only:
                    continue
                conn.execute(
                    """INSERT INTO papers(id, pmid, pmcid, doi, title, journal, year,
                       authors, abstract, is_preprint, is_oa, fulltext_source, supp_files,
                       mined_status, theme, has_db_xref, has_suppl, created_at, updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (paper["id"], paper["pmid"], paper["pmcid"], paper["doi"],
                     paper["title"], paper["journal"], paper["year"], paper["authors"],
                     paper["abstract"], paper["is_preprint"], paper["is_oa"],
                     paper["fulltext_source"], "[]", "pending", theme,
                     paper.get("has_db_xref", 0), paper.get("has_suppl", 0),
                     paper["created_at"], paper["created_at"]))
                if theme:
                    _link_theme(conn, paper["id"], theme, theme)
                sid = paper["pmid"] or paper["doi"]
                if paper["pmid"]:
                    url = (("https://europepmc.org/article/PPR/" if paper["is_preprint"]
                            else "https://europepmc.org/article/MED/") + paper["pmid"])
                else:
                    url = f"https://doi.org/{paper['doi']}"
                conn.execute(
                    """INSERT OR IGNORE INTO paper_sources(paper_id, source, source_id,
                       url, license, retrieved_at) VALUES(?,?,?,?,?,?)""",
                    (paper["id"], "europepmc", sid, url, "", paper["created_at"]))
                added += 1
                if not link_only and added >= max_papers:
                    break
        if progress:
            progress(f"Europe PMC sweep: {query[:40]}... page {len(results)}, +{added} new, "
                     f"{linked} linked")
        nxt = str(data.get("nextCursorMark") or "")
        if not nxt or nxt == cursor_mark or nxt == "*":
            break
        cursor_mark = nxt
    return {"added": added, "skipped": skipped, "linked": linked,
            "hit_count": hit_count, "error": page_error,
            "cursor_next": {"cursor_mark": cursor_mark}}


def _link_theme(conn, paper_id: str, theme: str, current: str) -> bool:
    """补一条主题归属；顺带把空的 papers.theme 补上。返回是否新增归属。"""
    cur = conn.execute(
        "INSERT OR IGNORE INTO paper_themes(paper_id, theme, source, created_at) "
        "VALUES(?,?,?,?)", (paper_id, theme, "link", utcnow()))
    if not current:
        conn.execute("UPDATE papers SET theme=?, updated_at=? WHERE id=?",
                     (theme, utcnow(), paper_id))
    return cur.rowcount > 0


def _find_duplicate(conn, paper: dict) -> dict | None:
    """任一非空标识命中即视为重复（空标识绝不能匹配全部行）。"""
    pmid, doi, pmcid = paper["pmid"], paper["doi"], paper["pmcid"]
    row = conn.execute(
        """SELECT id, theme FROM papers
           WHERE (? != '' AND pmid = ?)
              OR (? != '' AND doi = ?)
              OR (? != '' AND pmcid = ?)
           LIMIT 1""",
        (pmid, pmid, doi, doi, pmcid, pmcid)).fetchone()
    return dict(row) if row else None


def window_queries(queries: list[str], recent_days: int) -> list[str]:
    """给检索式加"近 N 天"窗口（Europe PMC `FIRST_PDATE`），用于**巡检增量**（L-0213）。"""
    if recent_days <= 0:
        return list(queries)
    from datetime import date, timedelta
    end = date.today()
    start = end - timedelta(days=max(1, int(recent_days)))
    return [f'({q}) AND (FIRST_PDATE:[{start.isoformat()} TO {end.isoformat()}])'
            for q in queries]


def expand_queries(theme: dict) -> list[str]:
    """主题 → 检索式列表（静态 facets 兜底；LLM 扩展可选）。"""
    facets = theme.get("facets") or {}
    base = [q for q in facets.get("base_queries", []) if q]
    if not base:
        base = [f'"{theme.get("name", "")}" AND (OPEN_ACCESS:Y OR HAS_DATA:Y)']
    extra = []
    for q in base:
        extra.append(f"{q} AND (SRC:PPR OR SRC:MED)")
    return list(dict.fromkeys(base + extra)) or base