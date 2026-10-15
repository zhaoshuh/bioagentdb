"""BioAgentDB 统一 CLI。

用法:
  python scripts/cli.py init
  python scripts/cli.py serve [--host 0.0.0.0] [--port 8000]
  python scripts/cli.py mine --theme huntingtons-disease [--workers 3] [--limit 100]
  python scripts/cli.py supervise [--limit 100] [--workers 6] [--continuous] [--rounds 20]
  python scripts/cli.py stop
  python scripts/cli.py status
  python scripts/cli.py cache
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PY = sys.executable


def _run(cmd: list[str], env: dict | None = None) -> int:
    return subprocess.call(cmd, cwd=str(ROOT), env=env)


# ----------------------------------------------------------------- commands
def cmd_init(args) -> int:
    from app.core.db import init_db
    from app.harvest.themes import seed_themes

    init_db()
    seed_themes(force=True)
    print("initialized DB + themes (starter seeding per config)")
    return 0


def cmd_serve(args) -> int:
    from app.core.config import cfg

    host = args.host or cfg.get("host")
    port = args.port or cfg.int("port", 8000)
    return _run([PY, str(ROOT / "scripts" / "serve.py"), "--host", host, "--port", str(port)])


def cmd_mine(args) -> int:
    cmd = [PY, str(ROOT / "scripts" / "miner.py"), "--theme", args.theme,
           "--workers", str(args.workers), "--limit", str(args.limit)]
    if args.log:
        cmd += ["--log", args.log]
    return _run(cmd)


def cmd_supervise(args) -> int:
    cmd = [PY, str(ROOT / "scripts" / "supervisor.py"),
           "--limit", str(args.limit), "--workers", str(args.workers)]
    if args.continuous:
        cmd += ["--continuous", "--rounds", str(args.rounds)]
    return _run(cmd)


def cmd_stop(args) -> int:
    return _run([PY, str(ROOT / "scripts" / "supervisor.py"), "--stop"])


def cmd_pilot(args) -> int:
    """按顺序对每个主题跑一次试点（默认 100 篇）。"""
    from app.harvest.themes import list_themes

    themes = [t.strip() for t in (args.themes or "").split(",") if t.strip()] \
        or [t["slug"] for t in list_themes()
            if t["priority"] > 3 and (t.get("facets") or {}).get("origin") != "on_demand"]
    rc = 0
    for slug in themes:
        print(f"\n=== pilot: {slug} (limit={args.limit}, workers={args.workers}) ===")
        rc |= _run([PY, str(ROOT / "scripts" / "miner.py"), "--theme", slug,
                    "--workers", str(args.workers), "--limit", str(args.limit),
                    "--log", str(ROOT / "data" / "logs" / f"pilot-{slug}.log")])
    return rc


def cmd_status(args) -> int:
    return _run([PY, str(ROOT / "scripts" / "status.py")])


def cmd_cache(args) -> int:
    import json

    from app.harvest.llm import cache_stats

    print(json.dumps(cache_stats(), ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bioagentdb", description="BioAgentDB CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init").set_defaults(fn=cmd_init)

    s = sub.add_parser("serve")
    s.add_argument("--host", default=None)
    s.add_argument("--port", type=int, default=None)
    s.set_defaults(fn=cmd_serve)

    s = sub.add_parser("mine")
    s.add_argument("--theme", required=True)
    s.add_argument("--workers", type=int, default=3)
    s.add_argument("--limit", type=int, default=100)
    s.add_argument("--log", default="")
    s.set_defaults(fn=cmd_mine)

    s = sub.add_parser("supervise")
    s.add_argument("--limit", type=int, default=100)
    s.add_argument("--workers", type=int, default=6)
    s.add_argument("--continuous", action="store_true")
    s.add_argument("--rounds", type=int, default=20)
    s.set_defaults(fn=cmd_supervise)

    sub.add_parser("stop").set_defaults(fn=cmd_stop)

    s = sub.add_parser("pilot")
    s.add_argument("--themes", default="", help="逗号分隔；默认所有 priority>3 的主题")
    s.add_argument("--limit", type=int, default=100)
    s.add_argument("--workers", type=int, default=3)
    s.set_defaults(fn=cmd_pilot)

    sub.add_parser("status").set_defaults(fn=cmd_status)
    sub.add_parser("cache").set_defaults(fn=cmd_cache)
    return p


def main() -> None:
    args = build_parser().parse_args()
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
