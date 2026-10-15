"""调研选文（生信数据库 + 生信 Agent）：EPMC + OpenAlex + arXiv 多查询矩阵。

输出:
  data/survey/selection.jsonl  （每篇一行：kind/ids/title/venue/year/oa/query/reason）
  docs/paper/survey/selection.md（统计 + 明细）
用法: python scripts/survey_select.py
"""

from __future__ import annotations

import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core import utils  # noqa: E402
from app.harvest.dedup import norm_doi, norm_title  # noqa: E402

OUT_DIR = ROOT / "data" / "survey"
EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
OPENALEX = "https://api.openalex.org/works"
ARXIV = "http://export.arxiv.org/api/query"

EPMC_DB_QUERIES = [
    'JOURNAL:"Nucleic Acids Research" AND (database OR resource OR platform OR webserver) '
    'AND (genomic OR proteomic OR omics OR bioinformatics) AND (FIRST_PDATE:[2019 TO 2026])',
    'JOURNAL:"Database (Oxford)" AND (database OR resource OR portal) AND (FIRST_PDATE:[2019 TO 2026])',
    'JOURNAL:"Bioinformatics" AND (database OR resource OR webserver OR portal) AND (FIRST_PDATE:[2019 TO 2026])',
    'JOURNAL:"Nature Methods" AND (database OR resource OR platform) AND (FIRST_PDATE:[2019 TO 2026])',
    'JOURNAL:"Scientific Data" AND (database OR resource OR dataset) AND (FIRST_PDATE:[2019 TO 2026])',
    'JOURNAL:"Genome Biology" AND (database OR resource OR portal) AND (FIRST_PDATE:[2019 TO 2026])',
    '"data portal" AND (disease OR Alzheimer OR cancer OR Parkinson) AND (FIRST_PDATE:[2020 TO 2026])',
]
EPMC_AGENT_QUERIES = [
    '(agent OR "large language model" OR LLM OR "multi-agent") AND (bioinformatics OR '
    '"biomedical data" OR genomics OR "data discovery" OR "knowledge base") '
    'AND (FIRST_PDATE:[2023 TO 2026])',
    '("AI agent" OR "tool use" OR "agentic") AND (biomedical OR biology OR omics OR genomics) '
    'AND (FIRST_PDATE:[2023 TO 2026])',
    '(LLM OR "large language model") AND (database OR knowledgebase OR curation) AND '
    '(biomedical OR genomics OR omics) AND (FIRST_PDATE:[2023 TO 2026])',
]
OPENALEX_QUERIES = [
    ("db", 'genomics database resource platform'),
    ("db", 'disease data portal omics'),
    ("agent", 'LLM agent bioinformatics biomedical'),
    ("agent", 'AI agent genomics data discovery'),
]
ARXIV_QUERIES = [
    ("agent", 'cat:q-bio.QM AND all:"LLM agent"'),
    ("agent", 'all:"bioinformatics agent" AND all:LLM'),
]

BIO_RE = re.compile(
    r"bio|biomed|genom|omics|prote|transcript|cell|neuro|disease|cancer|clinic|drug|"
    r"gene|protein|microb|patient", re.I)
AGENT_RE = re.compile(
    r"\bagent|multi-agent|agentic|llm|large language model|gpt-|rag\b|tool use|"
    r"copilot|assistant", re.I)
DB_RE = re.compile(
    r"database|data ?base|resource|portal|platform|knowledge ?base|repository|atlas|"
    r"webserver|web server", re.I)


def _clean(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def epmc_search(query: str, want: int) -> list[dict]:
    out, cursor = [], "*"
    for _page in range(6):
        if len(out) >= want:
            break
        data = utils.get_json(EPMC, params={"query": query, "format": "json",
                                            "pageSize": 100, "resultType": "core",
                                            "cursorMark": cursor})
        if not data:
            break
        for it in ((data.get("resultList") or {}).get("result") or []):
            if len(out) >= want:
                break
            out.append({
                "source": "epmc", "source_id": str(it.get("id") or ""),
                "doi": str(it.get("doi") or "").lower(), "pmid": str(it.get("id") or "")
                if str(it.get("source") or "").upper() == "MED" else "",
                "pmcid": str(it.get("pmcid") or ""), "title": _clean(it.get("title")),
                "journal": _clean(((it.get("journalInfo") or {}).get("journal") or {}).get("title")),
                "year": it.get("pubYear"), "abstract": _clean(it.get("abstractText")),
                "is_preprint": str(it.get("source") or "").upper() == "PPR",
                "oa": str(it.get("isOpenAccess") or "").lower() in ("y", "true", "1")
                      or bool(it.get("pmcid")),
                "query": query[:120]})
        cursor = str(data.get("nextCursorMark") or "")
        if not cursor:
            break
        time.sleep(0.25)
    return out


def openalex_search(query: str, want: int) -> list[dict]:
    from app.core.config import cfg
    out = []
    data = utils.get_json(OPENALEX, params={
        "search": query, "per_page": min(100, want), "page": 1,
        "filter": "from_publication_date:2019-01-01,type:article",
        "api_key": cfg.get("openalex_api_key"), "mailto": cfg.get("openalex_email")})
    for it in ((data or {}).get("results") or []):
        ids = it.get("ids") or {}
        out.append({
            "source": "openalex", "source_id": str(it.get("id") or "").rsplit("/", 1)[-1],
            "doi": str(it.get("doi") or "").replace("https://doi.org/", "").lower(),
            "pmid": str(ids.get("pmid") or "").rsplit("/", 1)[-1],
            "pmcid": str(ids.get("pmcid") or "").rsplit("/", 1)[-1],
            "title": _clean(it.get("title") or it.get("display_name")),
            "journal": _clean(((it.get("primary_location") or {}).get("source") or {})
                              .get("display_name")),
            "year": it.get("publication_year"), "abstract": "",
            "is_preprint": str(it.get("type") or "") == "preprint",
            "oa": bool((it.get("open_access") or {}).get("is_oa")),
            "query": f"openalex:{query[:100]}"})
    return out


def arxiv_search(query: str, want: int) -> list[dict]:
    text = utils.get_text(ARXIV, params={"search_query": query, "max_results": want})
    out = []
    if not text:
        return out
    try:
        ns = {"a": "http://www.w3.org/2005/Atom"}
        root = ET.fromstring(text)
        for e in root.findall("a:entry", ns):
            out.append({
                "source": "arxiv", "source_id": _clean(e.findtext("a:id", "", ns)).rsplit("/", 1)[-1],
                "doi": "", "pmid": "", "pmcid": "",
                "title": _clean(e.findtext("a:title", "", ns)),
                "journal": "arXiv", "year": _clean(e.findtext("a:published", "", ns))[:4],
                "abstract": _clean(e.findtext("a:summary", "", ns)),
                "is_preprint": True, "oa": True,
                "query": f"arxiv:{query[:100]}"})
    except Exception:  # noqa: BLE001
        pass
    return out


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    items: list[dict] = []
    for q in EPMC_DB_QUERIES:
        items.extend(epmc_search(q, 15))
        print(f"epmc-db: {len(items)} cumulative")
    for q in EPMC_AGENT_QUERIES:
        items.extend(epmc_search(q, 15))
        print(f"epmc-agent: {len(items)} cumulative")
    for kind, q in OPENALEX_QUERIES:
        got = openalex_search(q, 25)
        items.extend(got)
        print(f"openalex-{kind}: +{len(got)}")
    for kind, q in ARXIV_QUERIES:
        got = arxiv_search(q, 20)
        items.extend(got)
        print(f"arxiv-{kind}: +{len(got)}")

    # 分类 + 过滤 + 去重
    selected: dict[str, dict] = {}
    stats = Counter()
    for it in items:
        text = f"{it['title']} {it.get('abstract') or ''}"
        is_agent = bool(AGENT_RE.search(text))
        is_db = bool(DB_RE.search(text))
        if is_agent:
            if not BIO_RE.search(text):
                stats["skip:agent-nonbio"] += 1
                continue
            kind = "agent"
        elif is_db and BIO_RE.search(text):
            kind = "db"
        else:
            stats["skip:unclassified"] += 1
            continue
        key = norm_doi(it.get("doi") or "") or \
            (f"arxiv:{it['source_id']}" if it["source"] == "arxiv" else "") or \
            norm_title(it.get("title") or "")
        if not key:
            stats["skip:nokey"] += 1
            continue
        cur = selected.get(key)
        if cur is None:
            it["kind"] = kind
            it["reason"] = f"{kind}; oa={it['oa']}; via {it['query'][:60]}"
            selected[key] = it
            stats[f"keep:{kind}"] += 1
        else:
            # 合并来源；OA 或 PMCid 优先
            if it["oa"] and not cur["oa"]:
                cur.update({k: it[k] for k in ("oa", "pmcid", "pmid") if it.get(k)})
            stats["dup"] += 1

    rows = sorted(selected.values(), key=lambda x: (x["kind"] != "db", -int(x.get("year") or 0)))
    with (OUT_DIR / "selection.jsonl").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    db_n = sum(1 for r in rows if r["kind"] == "db")
    ag_n = sum(1 for r in rows if r["kind"] == "agent")
    oa_n = sum(1 for r in rows if r["oa"])
    md = ["# Survey selection — bioinformatics databases & agents", "",
          f"- candidates: {len(rows)}（db {db_n} · agent {ag_n}）· OA {oa_n}",
          f"- raw collected: {len(items)} · " + " · ".join(f"{k} {v}" for k, v in stats.items()),
          "", "| # | kind | year | venue | title | OA | ids |", "|---|---|---|---|---|---|---|"]
    for i, r in enumerate(rows, 1):
        ids = " / ".join(x for x in (r.get("doi"), r.get("pmid"),
                                     r.get("pmcid"), r.get("source_id")) if x)
        md.append(f"| {i} | {r['kind']} | {r.get('year') or ''} | {r.get('journal') or ''} "
                  f"| {r['title'][:80]} | {'Y' if r['oa'] else 'N'} | {ids[:60]} |")
    (ROOT / "docs" / "paper" / "survey").mkdir(parents=True, exist_ok=True)
    (ROOT / "docs" / "paper" / "survey" / "selection.md").write_text(
        "\n".join(md) + "\n", encoding="utf-8")
    print(f"selected: db={db_n} agent={ag_n} oa={oa_n} total={len(rows)}")
    print(f"wrote {OUT_DIR / 'selection.jsonl'}")


if __name__ == "__main__":
    main()
