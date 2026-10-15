"""回填 metadata-only 论文的摘要（EPMC 按 DOI/标题查询）。就地更新 selection.jsonl。"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core import utils  # noqa: E402

SURVEY = ROOT / "data" / "survey"
EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"


def epmc_abstract(doi: str, title: str) -> str:
    queries = []
    if doi:
        queries.append(f'DOI:"{doi}"')
    if title:
        queries.append(f'TITLE:"{title[:120]}"')
    for q in queries:
        data = utils.get_json(EPMC, params={"query": q, "format": "json", "pageSize": 3,
                                            "resultType": "core"}, retries=1)
        for it in ((data or {}).get("resultList") or {}).get("result") or []:
            abstract = str(it.get("abstractText") or "").strip()
            if abstract:
                return abstract
        time.sleep(0.2)
    return ""


def main() -> None:
    path = SURVEY / "selection.jsonl"
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    filled = skipped = 0
    for i, r in enumerate(rows, 1):
        if r.get("abstract"):
            continue
        abstract = epmc_abstract(r.get("doi") or "", r.get("title") or "")
        if abstract:
            r["abstract"] = abstract
            filled += 1
        else:
            skipped += 1
        if i % 10 == 0:
            print(f"  {i}/{len(rows)} · filled={filled} skipped={skipped}")
            path.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in rows) + "\n",
                            encoding="utf-8")
        time.sleep(0.2)
    path.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in rows) + "\n",
                    encoding="utf-8")
    no_abs = sum(1 for r in rows if not r.get("abstract"))
    print(f"done: filled={filled} · still-no-abstract={no_abs}/{len(rows)}")


if __name__ == "__main__":
    main()
