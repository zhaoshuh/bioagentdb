"""跨源论文去重与合并（试点）：DOI → PMID → PMCID → 标题指纹（年±1 + 姓氏）。

输入：各源的原始记录（dict，含 source/source_id/doi/pmid/pmcid/title/year/authors）。
输出：合并论文列表 + 合并日志 + 统计（全部为普通数据结构，便于入库/报告）。
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata


def norm_doi(doi: str) -> str:
    d = (doi or "").strip().lower()
    for pre in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "doi:"):
        if d.startswith(pre):
            d = d[len(pre):]
    return d.strip().rstrip(".").strip()


def norm_pmid(pmid: str) -> str:
    digits = re.sub(r"\D", "", pmid or "")
    return digits


def norm_pmcid(pmcid: str) -> str:
    p = (pmcid or "").strip().upper()
    if p and not p.startswith("PMC"):
        p = f"PMC{p}"
    return p if p != "PMC" else ""


def norm_title(title: str) -> str:
    t = unicodedata.normalize("NFKC", title or "").lower()
    return "".join(ch for ch in t if ch.isalnum())


def name_tokens(name: str) -> set[str]:
    """姓名 → 候选姓氏 token 集合（≥3 字符）。

    兼容两种顺序：family-first（"Smith A"、"Smith, John"）与 given-family（"John Smith"）。
    仅用于合并门控（标题+年±1 已先命中），token 交集非空即视为作者相符。
    """
    n = unicodedata.normalize("NFKC", name or "").lower()
    n = re.sub(r"[^a-z\s,]", " ", n)
    parts: list[str] = []
    for seg in n.split(","):
        parts.extend(seg.split())
    return {p for p in parts if len(p) >= 3}


def _authors_list(raw) -> list[str]:
    if isinstance(raw, list):
        return [str(x) for x in raw]
    if isinstance(raw, str) and raw.strip():
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                return [str(x) for x in data]
        except Exception:  # noqa: BLE001
            pass
    return []


def _key_tokens(rec: dict) -> set[str]:
    authors = _authors_list(rec.get("authors"))
    if not authors:
        return set()
    toks = name_tokens(authors[0])
    if len(authors) > 1:
        toks |= name_tokens(authors[-1])
    return toks


class _UF:
    def __init__(self, n: int) -> None:
        self.p = list(range(n))

    def find(self, x: int) -> int:
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


def _years_close(y1, y2) -> bool:
    if y1 is None or y2 is None:
        return True
    return abs(int(y1) - int(y2)) <= 1


def merge_records(records: list[dict]) -> dict:
    """records: 每个 (source, source_id) 一条的 dict 列表。"""
    n = len(records)
    uf = _UF(n)

    maps: dict[str, dict[str, int]] = {"doi": {}, "pmid": {}, "pmcid": {}}
    for i, r in enumerate(records):
        for kind, val in (("doi", norm_doi(r.get("doi"))),
                          ("pmid", norm_pmid(r.get("pmid"))),
                          ("pmcid", norm_pmcid(r.get("pmcid")))):
            if not val:
                continue
            if val in maps[kind]:
                uf.union(maps[kind][val], i)
            else:
                maps[kind][val] = i

    title_groups: dict[str, list[int]] = {}
    for i, r in enumerate(records):
        t = norm_title(r.get("title"))
        if len(t) >= 20:
            title_groups.setdefault(t, []).append(i)
    for t, idxs in title_groups.items():
        if len(idxs) < 2:
            continue
        for a in range(len(idxs)):
            for b in range(a + 1, len(idxs)):
                ia, ib = idxs[a], idxs[b]
                ra, rb = records[ia], records[ib]
                if not _years_close(ra.get("year"), rb.get("year")):
                    continue
                ta, tb = _key_tokens(ra), _key_tokens(rb)
                if ta and tb and not (ta & tb):
                    continue  # 作者 token 不符：不合并
                uf.union(ia, ib)

    clusters: dict[int, list[int]] = {}
    for i in range(n):
        clusters.setdefault(uf.find(i), []).append(i)

    papers: list[dict] = []
    merge_log: list[dict] = []
    for root, idxs in clusters.items():
        members = [records[i] for i in idxs]
        sources = sorted({m.get("source", "") for m in members})
        themes = sorted({t for m in members for t in (m.get("themes") or [])})
        method = "single"
        if len(members) > 1:
            norm = {"doi": norm_doi, "pmid": norm_pmid, "pmcid": norm_pmcid}
            for kind in ("doi", "pmid", "pmcid"):
                vals = {norm[kind](m.get(kind)) for m in members if m.get(kind)}
                vals.discard("")
                if len(vals) == 1:
                    method = kind
                    break
            else:
                method = "title"

        def _pick(field: str, choose="first"):
            vals = [m.get(field) for m in members if m.get(field)]
            if not vals:
                return ""
            if choose == "longest":
                return max(vals, key=lambda v: len(str(v)))
            return vals[0]

        year = _pick("year", "first") or None
        doi = _pick("doi")
        pmid = _pick("pmid")
        pmcid = _pick("pmcid")
        member_key = "|".join(sorted(
            f"{m.get('source')}:{m.get('source_id')}" for m in members))
        gid = f"mp_{hashlib.sha1(member_key.encode('utf-8')).hexdigest()[:12]}"
        papers.append({
            "gid": gid,
            "doi": doi,
            "pmid": pmid,
            "pmcid": pmcid,
            "title": _pick("title", "longest"),
            "abstract": _pick("abstract", "longest"),
            "year": int(year) if str(year or "").strip().isdigit() else year,
            "journal": _pick("journal"),
            "sources": json.dumps(sources, ensure_ascii=False),
            "n_sources": len(sources),
            "is_preprint": int(all(m.get("is_preprint") for m in members)),
            "is_oa": int(any(m.get("is_oa") for m in members)),
            "themes": json.dumps(themes, ensure_ascii=False),
        })
        merge_log.append({
            "gid": gid,
            "method": method,
            "n": len(members),
            "members": json.dumps([
                {"source": m.get("source"), "source_id": m.get("source_id"),
                 "doi": m.get("doi"), "pmid": m.get("pmid"),
                 "title": (m.get("title") or "")[:120], "year": m.get("year")}
                for m in members], ensure_ascii=False),
        })

    stats = {
        "raw_records": n,
        "merged_papers": len(papers),
        "multi_source": sum(1 for p in papers if p["n_sources"] >= 2),
        "merge_methods": {},
    }
    for lg in merge_log:
        stats["merge_methods"][lg["method"]] = stats["merge_methods"].get(lg["method"], 0) + 1
    return {"papers": papers, "merge_log": merge_log, "stats": stats}


def check_new_to_db(papers: list[dict], main_db_path: str) -> int:
    """对存量库（v1.2）判重：DOI/PMID/PMCID 命中，或标题+年±1 命中 → 非新。"""
    import sqlite3
    conn = sqlite3.connect(f"file:{main_db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    dois: set[str] = set()
    pmids: set[str] = set()
    pmcids: set[str] = set()
    titles: dict[str, list] = {}
    for r in conn.execute("SELECT doi, pmid, pmcid, title, year FROM papers"):
        if r["doi"]:
            dois.add(norm_doi(r["doi"]))
        if r["pmid"]:
            pmids.add(norm_pmid(r["pmid"]))
        if r["pmcid"]:
            pmcids.add(norm_pmcid(r["pmcid"]))
        t = norm_title(r["title"])
        if len(t) >= 20:
            titles.setdefault(t, []).append(r["year"])
    conn.close()
    new_count = 0
    for p in papers:
        if norm_doi(p.get("doi")) and norm_doi(p.get("doi")) in dois:
            p["new_to_db"] = 0
            continue
        if norm_pmid(p.get("pmid")) and norm_pmid(p.get("pmid")) in pmids:
            p["new_to_db"] = 0
            continue
        if norm_pmcid(p.get("pmcid")) and norm_pmcid(p.get("pmcid")) in pmcids:
            p["new_to_db"] = 0
            continue
        t = norm_title(p.get("title"))
        years = titles.get(t)
        if years:
            py = p.get("year")
            if any(_years_close(py, y) for y in years):
                p["new_to_db"] = 0
                continue
        p["new_to_db"] = 1
        new_count += 1
    return new_count
