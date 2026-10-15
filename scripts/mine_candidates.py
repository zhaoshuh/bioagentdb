"""定点挖矿：对 import_candidates.py 导入的候选逐篇跑完整流水线（含 3 对抗者）。

用法: python scripts/mine_candidates.py [--workers 6] [--limit 0]

- 输入 data/pilot/imported.json（paper_id + theme）。
- 每篇调用 app.miner.paperflow.run_paper（抽取 → 3 对抗者 → 合并/修正 → T1/T2 → 入库 → 审计）。
- 出错自动释放回池（release_paper）。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.db import db, init_db  # noqa: E402
from app.miner.claim import release_paper  # noqa: E402
from app.miner.paperflow import run_paper  # noqa: E402

IN_JSON = ROOT / "data" / "pilot" / "imported.json"

logger = logging.getLogger("mine_candidates")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--log", default="")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s",
                        filename=args.log or None)

    init_db()
    items = json.loads(IN_JSON.read_text(encoding="utf-8"))
    if args.limit > 0:
        items = items[:args.limit]
    print(f"mining {len(items)} candidates with {args.workers} workers ...")

    counters = {"mined": 0, "no_data": 0, "off_topic": 0, "error": 0,
                "verified": 0, "paper_verified": 0, "rejected": 0}
    t0 = time.time()

    def _one(item: dict) -> str:
        pid, theme = item["paper_id"], item["theme"]
        with db() as conn:
            conn.execute("UPDATE papers SET mined_status='claimed' WHERE id=?", (pid,))
        try:
            res = run_paper(pid, theme)
        except Exception as exc:  # noqa: BLE001
            logger.exception("paperflow failed %s: %s", pid, exc)
            release_paper(pid, reason="error")
            return "error"
        verdict = res.get("verdict", "")
        for k in ("verified", "paper_verified", "rejected"):
            counters[k] += int(res.get(k) or 0)
        return verdict or "error"

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(_one, it) for it in items]
        done = 0
        for fut in as_completed(futures):
            verdict = fut.result()
            counters[verdict if verdict in counters else "error"] += 1
            done += 1
            if done % 25 == 0:
                print(f"  {done}/{len(items)} · {counters} · {int(time.time()-t0)}s")

    print(f"done in {int(time.time()-t0)}s: {counters}")


if __name__ == "__main__":
    main()
