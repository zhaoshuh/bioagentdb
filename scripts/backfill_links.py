"""T2 回填：对现有记录的链接做一次可达性检测并写回（试点质量审计用）。"""

import sys, time, logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
logging.basicConfig(level=logging.WARNING)

from app.core.db import db, utcnow
from app.core.utils import http_status


def main() -> None:
    with db() as conn:
        links = conn.execute(
            "SELECT id, url FROM dataset_links WHERE url != ''").fetchall()
    print(f"{len(links)} links to re-check")

    def _one(link):
        ok, detail = http_status(link["url"])
        return link["id"], (1 if ok else 0), detail

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(_one, l) for l in links]
        done = 0
        for fut in as_completed(futures):
            link_id, ok, detail = fut.result()
            with db() as conn:
                conn.execute(
                    "UPDATE dataset_links SET reachable=?, http_status=?, last_checked=? WHERE id=?",
                    (ok, detail[:120], utcnow(), link_id))
            done += 1
    print(f"updated {done} links")


if __name__ == "__main__":
    main()