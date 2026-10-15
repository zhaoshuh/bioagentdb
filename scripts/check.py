"""自检：数据库、主题、外部源连通性（供运维/开机后使用）。"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.db import db, init_db  # noqa: E402


def main() -> None:
    init_db()
    with db() as conn:
        for t in ("themes", "papers", "datasets", "dataset_links", "evidence"):
            n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            print(f"{t}: {n}")
    print("--- source connectivity ---")
    from app.core import utils

    targets = [
        ("EuropePMC", "https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=cancer&format=json&pageSize=1"),
        ("NCBI_Eutils", "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/einfo.fcgi"),
        ("ENA", "https://www.ebi.ac.uk/ena/portal/api/search?result=study&fields=study_accession&format=json&limit=1"),
        ("PRIDE", "https://www.ebi.ac.uk/pride/ws/archive/v2/search/projects?keyword=cancer&pageSize=1"),
        ("Zenodo", "https://zenodo.org/api/records?q=cancer&size=1"),
    ]
    for name, url in targets:
        t0 = time.time()
        resp = utils._request("GET", url, retries=1)
        ms = round((time.time() - t0) * 1000)
        code = resp.status_code if resp is not None else "N/A"
        ok = resp is not None and resp.status_code < 400
        print(f"  {name:12} {'OK' if ok else 'FAIL'}  HTTP {code}  {ms}ms")


if __name__ == "__main__":
    main()