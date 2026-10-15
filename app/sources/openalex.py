"""OpenAlex 适配器（免费 key；search= + page 分页）。"""

from __future__ import annotations

from app.core.config import cfg
from app.sources.base import RawPaper, SourceAdapter, cached_request


def reconstruct_abstract(inv: dict | None) -> str:
    """abstract_inverted_index → 文本。"""
    if not inv:
        return ""
    pos: dict[int, str] = {}
    for word, idxs in inv.items():
        for i in idxs or []:
            pos[int(i)] = word
    return " ".join(pos[i] for i in sorted(pos))


class OpenAlexAdapter(SourceAdapter):
    name = "openalex"
    rate_s = 0.3
    URL = "https://api.openalex.org/works"

    def search(self, theme: str, queries: list[str], want: int,
               seen_ids: set[str]) -> list[RawPaper]:
        out: list[RawPaper] = []
        for q in queries:
            for page in range(1, 21):
                if len(out) >= want:
                    return out
                data = cached_request(self.name, "GET", self.URL, params={
                    "search": q, "per_page": 100, "page": page,
                    "api_key": cfg.get("openalex_api_key"),
                    "mailto": cfg.get("openalex_email")})
                if not data:
                    break
                results = data.get("results") or []
                if not results:
                    break
                for item in results:
                    paper = self.parse_item(item)
                    if paper and paper.source_id not in seen_ids:
                        seen_ids.add(paper.source_id)
                        out.append(paper)
                        if len(out) >= want:
                            break
                if len(results) < 100:
                    break
                self._throttle()
        return out

    @staticmethod
    def parse_item(item: dict) -> RawPaper | None:
        wid = str(item.get("id") or "")
        sid = wid.rsplit("/", 1)[-1] if wid else ""
        ids = item.get("ids") or {}
        doi = str(item.get("doi") or "").replace("https://doi.org/", "").strip().lower()
        pmid = str(ids.get("pmid") or "").rstrip("/").rsplit("/", 1)[-1]
        pmcid = str(ids.get("pmcid") or "").rstrip("/").rsplit("/", 1)[-1]
        if not sid and not doi:
            return None
        title = str(item.get("title") or item.get("display_name") or "").strip()
        if not title:
            return None
        authors = []
        for a in item.get("authorships") or []:
            name = ((a.get("author") or {}).get("display_name") or "").strip()
            if name:
                authors.append(name)
        oa = item.get("open_access") or {}
        best = item.get("best_oa_location") or {}
        primary = item.get("primary_location") or {}
        journal = ((primary.get("source") or {}).get("display_name") or "").strip()
        return RawPaper(
            source="openalex",
            source_id=sid or doi,
            doi=doi,
            pmid=pmid,
            pmcid=pmcid,
            title=title,
            abstract=reconstruct_abstract(item.get("abstract_inverted_index")),
            year=item.get("publication_year"),
            journal=journal,
            authors=authors,
            is_preprint=str(item.get("type") or "") == "preprint",
            is_oa=bool(oa.get("is_oa")),
            oa_url=str(best.get("pdf_url") or best.get("landing_page_url") or ""),
            landing_url=str(primary.get("landing_page_url") or wid),
            license=str(best.get("license") or ""),
        )
