"""CORE 适配器（POST JSON；q 禁含引号；必须经代理可达）。"""

from __future__ import annotations

from app.core.config import cfg
from app.sources.base import RawPaper, SourceAdapter, cached_request


class CoreAdapter(SourceAdapter):
    name = "core"
    rate_s = 0.6
    URL = "https://api.core.ac.uk/v3/search/works"
    PAGE = 100

    def search(self, theme: str, queries: list[str], want: int,
               seen_ids: set[str]) -> list[RawPaper]:
        out: list[RawPaper] = []
        headers = {"Authorization": f"Bearer {cfg.get('core_api_key')}"}
        for q in queries:
            # CORE 的 q 不支持引号（实测含引号 → 500），去掉引号用纯文本
            plain = q.replace('"', " ").strip()
            for offset in range(0, 10 * self.PAGE, self.PAGE):
                if len(out) >= want:
                    return out
                data = cached_request(self.name, "POST", self.URL, json_body={
                    "q": plain, "limit": self.PAGE, "offset": offset},
                    headers=headers)
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
                if len(results) < self.PAGE:
                    break
                self._throttle()
        return out

    @staticmethod
    def parse_item(item: dict) -> RawPaper | None:
        sid = str(item.get("id") or "").strip()
        doi = str(item.get("doi") or "").strip().lower()
        if not sid and not doi:
            return None
        title = str(item.get("title") or "").strip()
        if not title:
            return None
        authors = []
        for a in item.get("authors") or []:
            name = (a.get("name") if isinstance(a, dict) else str(a)) or ""
            name = name.strip()
            if name:
                authors.append(name)
        lang = item.get("language") or {}
        return RawPaper(
            source="core",
            source_id=sid or doi,
            doi=doi,
            pmid=str(item.get("pubmedId") or "").strip(),
            title=title,
            abstract=str(item.get("abstract") or "").strip(),
            year=item.get("yearPublished"),
            journal=str(((item.get("journals") or [{}])[0] or {}).get("title") or ""),
            authors=authors,
            is_preprint=str(item.get("documentType") or "").lower() == "preprint",
            is_oa=True,  # CORE 全部为 OA 仓储内容
            oa_url=str(item.get("downloadUrl") or ""),
            landing_url=str(item.get("downloadUrl") or ""),
            license="",
            extra={"language": lang.get("code") if isinstance(lang, dict) else ""},
        )
