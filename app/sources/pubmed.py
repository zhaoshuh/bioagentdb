"""PubMed E-utilities 适配器（esearch + efetch XML；空 api_key 不要传）。"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from app.core.config import cfg
from app.sources.base import RawPaper, SourceAdapter, cached_request, cached_text


def _text(el: ET.Element | None) -> str:
    return "".join(el.itertext()).strip() if el is not None else ""


def parse_pubmed_xml(xml_text: str) -> list[RawPaper]:
    out: list[RawPaper] = []
    if not xml_text:
        return out
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return out
    for art in root.findall(".//PubmedArticle"):
        pmid = _text(art.find(".//MedlineCitation/PMID"))
        title = _text(art.find(".//Article/ArticleTitle"))
        if not title:
            continue
        abstract = " ".join(_text(e) for e in art.findall(".//Abstract/AbstractText"))
        journal = _text(art.find(".//Journal/Title"))
        year = _text(art.find(".//JournalIssue/PubDate/Year"))
        if not year:
            year = _text(art.find(".//JournalIssue/PubDate/MedlineDate"))[:4]
        authors = []
        for a in art.findall(".//AuthorList/Author"):
            last = _text(a.find("LastName"))
            fore = _text(a.find("ForeName")) or _text(a.find("Initials"))
            if last:
                authors.append(f"{last} {fore}".strip())
        doi = pmc = ""
        for aid in art.findall(".//ArticleIdList/ArticleId"):
            t = aid.get("IdType")
            if t == "doi":
                doi = (aid.text or "").strip().lower()
            elif t == "pmc":
                pmc = (aid.text or "").strip()
        try:
            year_i = int(year) if year else None
        except ValueError:
            year_i = None
        out.append(RawPaper(
            source="pubmed",
            source_id=pmid or doi,
            doi=doi,
            pmid=pmid,
            pmcid=pmc,
            title=title,
            abstract=abstract,
            year=year_i,
            journal=journal,
            authors=authors,
            landing_url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else "",
        ))
    return out


class PubMedAdapter(SourceAdapter):
    name = "pubmed"
    rate_s = 0.4
    ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
    EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

    def _base_params(self) -> dict:
        params: dict[str, str] = {}
        if cfg.get("ncbi_api_key"):
            params["api_key"] = cfg.get("ncbi_api_key")
        if cfg.get("ncbi_email"):
            params["email"] = cfg.get("ncbi_email")
        return params

    def search(self, theme: str, queries: list[str], want: int,
               seen_ids: set[str]) -> list[RawPaper]:
        out: list[RawPaper] = []
        for q in queries:
            term = f'{q}[Title/Abstract]'
            for retstart in range(0, 1000, 100):
                if len(out) >= want:
                    return out
                data = cached_request(self.name, "GET", self.ESEARCH, params={
                    "db": "pubmed", "term": term, "retmax": 100,
                    "retstart": retstart, "retmode": "json", **self._base_params()})
                ids = ((data or {}).get("esearchresult") or {}).get("idlist") or []
                if not ids:
                    break
                xml = cached_text(self.name, "GET", self.EFETCH, params={
                    "db": "pubmed", "id": ",".join(ids), "retmode": "xml",
                    **self._base_params()})
                for paper in parse_pubmed_xml(xml):
                    if paper.source_id and paper.source_id not in seen_ids:
                        seen_ids.add(paper.source_id)
                        out.append(paper)
                        if len(out) >= want:
                            break
                if len(ids) < 100:
                    break
                self._throttle()
        return out
