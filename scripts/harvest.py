"""命令行挖掘：python scripts/harvest.py --theme alzheimers-disease --max-papers 500"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.db import init_db  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--theme", default="alzheimers-disease")
    parser.add_argument("--max-papers", type=int, default=None)
    parser.add_argument("--queries", nargs="*", default=None,
                        help="自定义 Europe PMC 检索式（覆盖主题默认）")
    args = parser.parse_args()

    init_db()
    from app.harvest import runner
    from app.core.db import db

    run_id = runner.start_harvest(args.theme, max_papers=args.max_papers,
                                  queries=args.queries)
    print(f"harvest run started: {run_id}  (Ctrl+C 不中断后台线程)")
    try:
        while True:
            time.sleep(2)
            with db() as conn:
                row = conn.execute("SELECT status, stage, progress, counters, error "
                                   "FROM harvest_runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                break
            c = json.loads(row["counters"] or "{}")
            line = (f"[{row['status']}] stage={row['stage']} {round((row['progress'] or 0) * 100)}% "
                    f"scanned={c.get('papers_scanned', 0)} mined={c.get('papers_mined', 0)} "
                    f"verified={c.get('verified', 0)} paper_v={c.get('paper_verified', 0)} "
                    f"rejected={c.get('rejected', 0)}")
            print(line, flush=True)
            if row["status"] in ("done", "error"):
                if row["error"]:
                    print("ERROR:", row["error"])
                break
    except KeyboardInterrupt:
        print("\n(监视中断；后台任务仍在跑，可稍后查看 Harvest Runs)")

    from app.harvest.ingest import update_theme_stats
    update_theme_stats("")


if __name__ == "__main__":
    main()