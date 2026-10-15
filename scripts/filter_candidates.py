"""过滤式候选集（S5）：对试点 5,000 条做数据信号过滤，产出高价值候选。

过滤器：
  F1 terms   —— 标题+摘要：登记号模式 或 DA 短语
  F2 epmc    —— EPMC 数据旗标（批量 DOI/EXT_ID + HAS_DATA:Y）
  F3 da_scan —— 带 PMCID 者实取 DA 段并扫登记号

输出：data/pilot/candidates-filtered.jsonl + filtered-summary.json
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.harvest.dedup import norm_doi, norm_pmid  # noqa: E402
from app.harvest.extract import find_identifiers  # noqa: E402
from app.harvest.fulltext import fetch_paper_assets  # noqa: E402
from app.sources.base import cached_request  # noqa: E402

PILOT_DB = ROOT / "data" / "pilot" / "multisource-pilot.sqlite3"
OUT_JSONL = ROOT / "data" / "pilot" / "candidates-filtered.jsonl"
OUT_SUM = ROOT / "data" / "pilot" / "filtered-summary.json"
EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"

DA_RE = re.compile(
    r"data (?:are|is|have been|were|has been) (?:available|deposited|uploaded|freely)|"
    r"available (?:at|from|in|via|through) (?:the )?(?:GEO|SRA|ENA|PRIME|PRIDE|Zenodo|"
    r"figshare|Dryad|ArrayExpress|dbGaP|EGA|GSA|OMIX|SRA|BioProject|NCBI)|"
    r"accession (?:number|no|code)|data availability statement|"
    r"deposited (?:in|at|with|to) (?:the )?(?:GEO|SRA|ENA|PRIDE|Zenodo|figshare|Dryad|"
    r"ArrayExpress|dbGaP|EGA|GSA|OMIX|NCBI|BioProject)", re.I)


def chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def epmc_oracle(dois: list[str], pmids: list[str]) -> tuple[set, set]:
    """批量问 EPMC：这些 DOI/PMID 里哪些带 HAS_DATA:Y。"""
    hit_d, hit_p = set(), set()
    for ch in chunks(sorted(set(dois)), 15):
        q = "(" + " OR ".join(f'DOI:"{d}"' for d in ch) + ") AND HAS_DATA:Y"
        data = cached_request("filter", "GET", EPMC, params={
            "query": q, "format": "json", "pageSize": 100, "resultType": "lite"})
        for item in ((data or {}).get("resultList") or {}).get("result") or []:
            d = norm_doi(str(item.get("doi") or ""))
            if d in ch:
                hit_d.add(d)
        time.sleep(0.2)
    for ch in chunks(sorted(set(pmids)), 15):
        q = "(" + " OR ".join(f"EXT_ID:{p}" for p in ch) + ") AND HAS_DATA:Y"
        data = cached_request("filter", "GET", EPMC, params={
            "query": q, "format": "json", "pageSize": 100, "resultType": "lite"})
        for item in ((data or {}).get("resultList") or {}).get("result") or []:
            p = norm_pmid(str(item.get("id") or ""))
            if p in ch:
                hit_p.add(p)
        time.sleep(0.2)
    return hit_d, hit_p


def main() -> None:
    conn = sqlite3.connect(PILOT_DB)
    conn.row_factory = sqlite3.Row
    papers = [dict(r) for r in conn.execute(
        "SELECT * FROM merged_papers WHERE new_to_db=1")]
    print(f"new_to_db candidates: {len(papers)}")

    # F1
    f1 = {}
    for p in papers:
        text = f"{p.get('title') or ''} {p.get('abstract') or ''}"
        f1[p["gid"]] = bool(find_identifiers(text)) or bool(DA_RE.search(text))
    print(f"F1 terms pass: {sum(f1.values())}")

    # F2
    dois = [norm_doi(p.get("doi") or "") for p in papers]
    dois = [d for d in dois if d]
    pmids = [norm_pmid(p.get("pmid") or "") for p in papers]
    pmids = [p for p in pmids if p]
    print(f"F2 oracle over {len(set(dois))} DOIs + {len(set(pmids))} PMIDs ...")
    hit_d, hit_p = epmc_oracle(dois, pmids)
    print(f"F2 epmc_data hits: doi={len(hit_d)} pmid={len(hit_p)}")

    # F3（带 PMCID 者实取 DA；限 400；带断点缓存）
    f3 = {}
    da_cache = ROOT / "data" / "pilot" / "da_cache"
    pmc_papers = [p for p in papers if (p.get("pmcid") or "").strip()][:400]
    print(f"F3 da_scan over {len(pmc_papers)} PMC-bearing ...")
    for i, p in enumerate(pmc_papers, 1):
        key = (p.get("pmcid") or p.get("gid") or "").strip()
        fp = da_cache / f"{key}.json"
        da = ""
        if fp.exists():
            try:
                da = json.loads(fp.read_text(encoding="utf-8")).get("da", "")
            except Exception:  # noqa: BLE001
                da = ""
        else:
            assets = fetch_paper_assets({
                "pmcid": p.get("pmcid") or "", "pmid": p.get("pmid") or "",
                "doi": p.get("doi") or "", "is_preprint": bool(p.get("is_preprint")),
                "has_suppl": False})
            da = assets.get("da_text") or ""
            da_cache.mkdir(parents=True, exist_ok=True)
            fp.write_text(json.dumps({"da": da}, ensure_ascii=False), encoding="utf-8")
            time.sleep(0.2)
        ids = find_identifiers(f"{p.get('abstract') or ''}\n{da}")
        f3[p["gid"]] = bool(ids)
        if i % 50 == 0:
            print(f"  {i}/{len(pmc_papers)} · hits={sum(f3.values())}")

    # 汇总
    out = []
    per_source: dict[str, dict] = {}
    for p in papers:
        gid = p["gid"]
        doi = norm_doi(p.get("doi") or "")
        pmid = norm_pmid(p.get("pmid") or "")
        flags = []
        if f1.get(gid):
            flags.append("terms")
        if (doi and doi in hit_d) or (pmid and pmid in hit_p):
            flags.append("epmc_data")
        if f3.get(gid):
            flags.append("da_scan")
        srcs = json.loads(p["sources"])
        for s in srcs:
            d = per_source.setdefault(s, {"total": 0, "pass": 0, "epmc_data": 0})
            d["total"] += 1
            if flags:
                d["pass"] += 1
            if "epmc_data" in flags:
                d["epmc_data"] += 1
        if flags:
            themes = json.loads(p.get("themes") or "[]")
            out.append({
                "gid": gid, "sources": srcs, "doi": doi, "pmid": pmid,
                "pmcid": p.get("pmcid") or "", "title": p.get("title") or "",
                "abstract": p.get("abstract") or "", "year": p.get("year"),
                "journal": p.get("journal") or "", "themes": themes,
                "filters": flags, "is_preprint": p.get("is_preprint"),
            })
    OUT_JSONL.write_text("\n".join(json.dumps(o, ensure_ascii=False) for o in out) + "\n",
                         encoding="utf-8")
    summary = {
        "new_to_db": len(papers),
        "f1_terms": sum(f1.values()),
        "f2_epmc_data": len(hit_d) + len(hit_p),
        "f3_da_scan": sum(f3.values()),
        "union": len(out),
        "per_source": per_source,
    }
    OUT_SUM.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"wrote {OUT_JSONL}")


if __name__ == "__main__":
    main()
