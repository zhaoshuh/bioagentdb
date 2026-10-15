"""保活器：看护三个病种矿机进程。崩溃自动拉起；stop 文件停止。

用法:
  python scripts/supervisor.py --limit 100            # 试点模式（每病 100 篇）
  python scripts/supervisor.py --limit 0              # 全量模式（不限）
  python scripts/supervisor.py --stop                 # 优雅停止全部
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import cfg  # noqa: E402

logger = logging.getLogger("supervisor")

THEMES = cfg.jsonv("supervisor_themes", ["alzheimers-disease", "als", "parkinsons-disease"])
PY = sys.executable   # 用当前解释器（跨平台 / Docker 友好）
MINER = str(Path(__file__).resolve().parent / "miner.py")
STOP_FLAG = Path(cfg.data_dir) / "miner.stop"
MAX_RESTARTS_PER_HOUR = cfg.int("supervisor_max_restarts_per_hour", 6)


def _stop_flag(action: str) -> None:
    if action == "stop":
        STOP_FLAG.touch()
        print(f"[supervisor] stop file created: {STOP_FLAG}")
    else:
        if STOP_FLAG.exists():
            STOP_FLAG.unlink()
        print("[supervisor] stop file removed")


def _watch(theme: str, limit: int, log_dir: Path, workers: int = 3,
           continuous: bool = False, rounds: int = 20) -> None:
    restarts: list[float] = []
    log_file = log_dir / f"miner-{theme}.log"
    round_no = 0
    while not STOP_FLAG.exists():
        cmd = [PY, MINER, "--theme", theme, "--workers", str(workers),
               "--limit", str(limit), "--log", str(log_file)]
        round_no += 1
        logger.info("[%s] starting miner round %s/%s (limit=%s, workers=%s)",
                    theme, round_no, rounds if continuous else 1, limit, workers)
        proc = subprocess.Popen(cmd, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        code = proc.wait()
        if STOP_FLAG.exists():
            logger.info("[%s] miner exited (stop requested, code=%s)", theme, code)
            break
        if code == 0:
            if limit > 0 and not continuous:
                logger.info("[%s] pilot limit %s reached; miner done (no restart)", theme, limit)
                break
            if continuous and round_no >= rounds:
                logger.info("[%s] reached round cap %s; stopping", theme, rounds)
                break
            logger.info("[%s] round %s done; next round in 10s (%s papers)",
                        theme, round_no, limit)
            time.sleep(10)
            continue
        if code == 3:
            # 源站异常（L-0212）：不计崩溃，长退避后继续
            logger.warning("[%s] miner reported source-unwell (exit 3); backing off 10 min", theme)
            time.sleep(600)
            continue
        now = time.time()
        restarts = [t for t in restarts if now - t < 3600]
        if len(restarts) >= MAX_RESTARTS_PER_HOUR:
            logger.error("[%s] too many restarts in the hour; backing off 10 min", theme)
            time.sleep(600)
            continue
        restarts.append(now)
        logger.warning("[%s] miner crashed code=%s; restarting in 5s", theme, code)
        time.sleep(5)


def _ensure_themes() -> None:
    """主题缺失时按需创建（G 线）：干净部署默认零播种（L-0225），但 supervisor 显式点名的
    主题仍须存在，否则 miner 以 "unknown theme" 退出 2、被保活器反复重启。
    已存在=不动；仅创建缺的主题，不影响任何既有数据。"""
    from app.harvest import themes as T

    seeds = {t["slug"]: t for t in T.THEME_SEEDS}
    for theme in THEMES:
        if T.get_theme(theme) is not None:
            continue
        seed = seeds.get(theme) or {}
        name = seed.get("name") or theme.replace("-", " ").title()
        T.ensure_disease_theme(theme, name, queries=T.default_queries(name))
        logger.info("[supervisor] theme %s missing (zero-seed deployment) — created on demand",
                    theme)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=100,
                        help="每病论文配额（试点100；0=全量不限）")
    parser.add_argument("--workers", type=int, default=3, help="每病线程数")
    parser.add_argument("--continuous", action="store_true",
                        help="连续测试轮：一轮跑完自动开下一轮（仍按 --limit 每轮配额）")
    parser.add_argument("--rounds", type=int, default=20, help="连续模式下的轮数上限")
    parser.add_argument("--stop", action="store_true")
    parser.add_argument("--clear-stop", action="store_true")
    parser.add_argument("--log-dir", default=str(cfg.data_dir / "logs"))
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if args.stop:
        _stop_flag("stop")
        return
    if args.clear_stop:
        _stop_flag("clear")
        return
    _stop_flag("clear")

    _ensure_themes()

    log_dir = Path(args.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    threads = []
    for theme in THEMES:
        t = threading.Thread(target=_watch,
                             args=(theme, args.limit, log_dir, args.workers,
                                   args.continuous, args.rounds),
                             daemon=True, name=f"watch-{theme}")
        threads.append(t)
        t.start()
        time.sleep(2)  # 错峰启动，避免三进程同时打 EBI
    mode = f"continuous, {args.rounds} rounds max" if args.continuous else "single round"
    print(f"[supervisor] watching {len(threads)} miners (limit={args.limit}, "
          f"workers={args.workers}, {mode}); stop with: python scripts/supervisor.py --stop")
    try:
        while True:
            time.sleep(5)
            if STOP_FLAG.exists():
                print("[supervisor] stop file detected; exiting (miners will stop)")
                break
    except KeyboardInterrupt:
        print("[supervisor] SIGINT")
    for t in threads:
        t.join(timeout=3)
    sys.exit(0)


if __name__ == "__main__":
    main()