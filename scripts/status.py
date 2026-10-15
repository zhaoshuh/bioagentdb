"""状态速览：矿机进程 + 吞吐 + 池子。用法: python scripts/status.py"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.db import db

DEFAULT_TIMEOUT = 10


def api_base() -> str:
    """服务基址：跟随配置（BIOAGENTDB_HOST/PORT；默认 127.0.0.1:8000）。"""
    from app.core.config import cfg

    host = cfg.get("host") or "127.0.0.1"
    if host in ("0.0.0.0", "::", "[::]"):
        host = "127.0.0.1"
    return f"http://{host}:{cfg.int('port', 8000)}"


def main() -> None:
    import urllib.request

    api = api_base()

    def get(path):
        try:
            return json.load(urllib.request.urlopen(api + path, timeout=DEFAULT_TIMEOUT))
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}

    m = get("/api/miner/status")
    run = [p for p in (m.get("processes") or []) if p.get("status") == "running"]
    print(f"矿机进程: {len(run)} 个运行中")
    for p in run:
        c = p.get("counters", {})
        print(f"  {p['theme']:22} workers={p['workers']} limit={p['pilot_limit']} "
              f"claimed={c.get('papers_claimed')} verified={c.get('verified')} "
              f"refills={c.get('refills')} hb={p.get('last_heartbeat')}")
    t = get("/api/throughput")
    print("吞吐:", json.dumps(t, ensure_ascii=False))
    with db() as conn:
        from app.core.config import cfg
        extra = ", 'task_done'" if cfg.bool("mining_task_recatalog", True) else ""
        for th in ("alzheimers-disease", "als", "parkinsons-disease"):
            p = conn.execute(
                "SELECT COUNT(*) FROM papers pa "
                "JOIN paper_themes pt ON pt.paper_id = pa.id "
                "WHERE pt.theme=? AND pa.mined_status IN "
                "('pending','fetched'" + extra + ")", (th,)).fetchone()[0]
            print(f"  池[{th}] 待挖 {p}")
        print("  datasets:", conn.execute("SELECT COUNT(*) FROM datasets").fetchone()[0])


if __name__ == "__main__":
    main()