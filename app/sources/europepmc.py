"""Europe PMC 适配器（试点对照源；子类用于预印本 SRC:PPR）。"""

from __future__ import annotations

from app.core import utils
from app.sources.base import RawPaper, SourceAdapter, cached_request


class EuropePmcAdapter(SourceAdapter):
    name = "europepmc"
    paper_source = "europepmc"
    rate_s = 0.25
    SEARCH_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"

    def transform_query(self, query: str) -> str:
        return query

    def search(self, theme: str, queries: list[str], want: int,
               seen_ids: set[str]) -> list[RawPaper]:
        out: list[RawPaper] = []
        for q in queries:
            cursor = "*"
            for _page in range(30):
                if len(out) >= want:
                    return out
                data = cached_request(self.name, "GET", self.SEARCH_URL, params={
                    "query": self.transform_query(q), "format": "json",
                    "pageSize": 100, "resultType": "core", "cursorMark": cursor})
                if not data:
                    break
                results = ((data.get("resultList") or {}).get("result")) or []
                if not results:
                    break
                for item in results:
                    paper = self.parse_item(item)
                    if paper and paper.source_id not in seen_ids:
                        seen_ids.add(paper.source_id)
                        out.append(paper)
                        if len(out) >= want:
                            break
                cursor = str(data.get("nextCursorMark") or "")
                if not cursor:
                    break
                self._throttle()
        return out

    def parse_item(self, item: dict) -> RawPaper | None:
        src = str(item.get("source") or "").upper()
        sid = str(item.get("id") or "").strip()
        doi = str(item.get("doi") or "").strip().lower()
        if not sid and not doi:
            return None
        title = utils.clean_html(item.get("title"))
        if not title:
            return None
        year = (utils.parse_year(item.get("pubYear"))
                or utils.parse_year(item.get("firstPublicationDate")))
        authors = [a.strip() for a in
                   (utils.clean_html(item.get("authorString")) or "").split(",")
                   if a.strip()]
        pmcid = str(item.get("pmcid") or "").strip()
        is_oa = str(item.get("isOpenAccess") or "").lower() in ("y", "true", "1")
        landing = (f"https://europepmc.org/article/{src or 'MED'}/{sid}"
                   if sid else f"https://doi.org/{doi}")
        return RawPaper(
            source=self.paper_source,
            source_id=sid or doi,
            doi=doi,
            pmid=sid if src == "MED" and sid.isdigit() else "",
            pmcid=pmcid,
            title=title,
            abstract=utils.clean_html(item.get("abstractText")),
            year=year,
            journal=utils.clean_html(
                (item.get("journalInfo") or {}).get("journal", {}).get("title")),
            authors=authors,
            is_preprint=(src == "PPR"),
            is_oa=is_oa,
            landing_url=landing,
            extra={"epmc_source": src},
        )


class PreprintsAdapter(EuropePmcAdapter):
    """bioRxiv/medRxiv 预印本：经 EPMC SRC:PPR 发现。"""

    name = "preprints"
    paper_source = "preprints"
    rate_s = 0.25

    def transform_query(self, query: str) -> str:
        return f"({query}) AND SRC:PPR"
