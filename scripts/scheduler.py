"""用户矿机调度器（常驻）：把 `mining_requests(kind='task')` 跑到穷尽（L-0206/07/09）。

- 队列：queued → running（起 `miner.py --task-id`；**全局并发上限** + **同主题单线**；
  记录默认发布进公共目录 ＋ 任务日志 `task_records`（`publish=0` 回退私有）；论文池随补货增长，见 L-0211）；
- 轮次：一轮（`papers_per_round` 篇）跑完仍有存量 → 自动排队续下一轮（**先穷尽**）；
- 巡检：到点 idle → `phase=patrol` 增量轮（近 N 天窗、只扫 EPMC 增量；窗随停机时长放宽，L-0215）；
- 用户操作：paused/stopped 时子进程在检查点退出，状态保留；resume 回队列；
- 重启恢复：进程已死的 running 任务自动重新排队；连续失败记 `failures`，超限置 error；
- 系统级总闸（决策 c）：全局并发数、失败上限；创建闸在 API 侧（配置）。

用法: python scripts/scheduler.py            # 常驻
      python scripts/scheduler.py --once     # 跑一遍到空闲后退出（测试用）
      python scripts/scheduler.py --timeout 600   # 附带总时限（秒；0=不限）
      python scripts/scheduler.py --exit-on-lock  # 锁被占时直接退出（旧行为；默认改为等待接管）
"""
from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.config import cfg  # noqa: E402
from app.core.db import db, init_db, utcnow  # noqa: E402
from app.mining import tasks as task_engine  # noqa: E402

PY = sys.executable
MINER = str(Path(__file__).resolve().parent / "miner.py")
logger = logging.getLogger("scheduler")

_children: dict[str, tuple[subprocess.Popen, str]] = {}
_lock = threading.Lock()
_restart_times: list[float] = []          # 失败重启时间戳（每小时限速用）


def _busy_themes() -> set[str]:
    """同主题单线：本进程子进程 + 任何**存活 pid** 的任务行（含孤儿/暂停/停止排空，L-0215/0217）。"""
    from app.chat.mining import _alive

    themes = {theme for _, theme in _children.values()}
    try:
        with db() as conn:
            rows = conn.execute(
                "SELECT pid, theme FROM mining_requests WHERE kind='task' "
                "AND status IN ('running','paused','queued','stopped') AND pid != 0"
            ).fetchall()
    except Exception:  # noqa: BLE001
        return themes
    themes |= {r["theme"] for r in rows if _alive(r["pid"])}
    return themes


def _live_running() -> int:
    """全站存活矿机数（cap 口径；L-0210 修复 C-3；L-0215/0217 扩到"进程仍活"的行）。"""
    from app.chat.mining import _alive

    try:
        with db() as conn:
            rows = conn.execute(
                "SELECT pid FROM mining_requests WHERE kind='task' "
                "AND status IN ('running','paused','queued','stopped') AND pid != 0"
            ).fetchall()
    except Exception:  # noqa: BLE001
        return 0
    return sum(1 for r in rows if _alive(r["pid"]))


def _heartbeat_stale(ts: str, minutes: int) -> bool:
    """任务行心跳是否陈旧（pid 复用/僵死兜底；L-0215 修复 C-6）。空时间戳=不判陈旧。"""
    if not ts:
        return False
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    return (datetime.now(timezone.utc) - dt).total_seconds() > minutes * 60


def _spawn(task: dict) -> None:
    tid = task["id"]
    log = cfg.data_dir / "logs" / f"task-{tid}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    workers = max(1, min(cfg.int("miner_workers_default", 3),
                         cfg.int("miner_workers_max", 6)))
    limit = int(task.get("papers_per_round") or 200)
    patrol = (task.get("phase") or "") == "patrol"
    lookback = 0
    if patrol:
        # 巡检窗游标（L-0215 修复 C-4）：至少 lookback 天；停机/暂停更久则放宽到距上次心跳的时长
        lookback = max(1, cfg.int("scheduler_patrol_lookback_days", 3))
        lh = task.get("last_heartbeat") or ""
        if lh:
            try:
                dt = datetime.fromisoformat(lh)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                hours = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
                lookback = max(lookback, int(hours // 24) + 1)
            except ValueError:
                pass
        cap_days = max(1, cfg.int("scheduler_patrol_max_lookback_days", 30))
        if lookback > cap_days:
            exec_lookback = cap_days
            logger.warning("task %s: patrol window needed %sd > cap %sd; truncated "
                           "(gap beyond the cap needs a fresh exhaust, L-0217 C-12)",
                           tid, lookback, cap_days)
            lookback = exec_lookback
        limit = min(limit, max(1, cfg.int("scheduler_patrol_batch", 100)))
    cmd = [PY, MINER, "--theme", task["theme"], "--task-id", tid,
           "--workers", str(workers),
           "--limit", str(limit),
           "--log", str(log)]
    if patrol:
        cmd += ["--patrol-days", str(lookback)]
    proc = subprocess.Popen(cmd, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    status_text = f"patrolling (last {lookback} days)" if patrol else "mining"
    with db() as conn:
        conn.execute(
            "UPDATE mining_requests SET pid=?, status='running', status_text=? WHERE id=?",
            (proc.pid, status_text, tid))
    with _lock:
        _children[tid] = (proc, task["theme"])
    threading.Thread(target=_watch_child, args=(tid, proc), daemon=True,
                     name=f"watch-{tid}").start()
    logger.info("task %s: miner started (pid=%s, theme=%s, limit=%s%s)",
                tid, proc.pid, task["theme"], limit,
                f", patrol≈{lookback}d" if patrol else "")


def _watch_child(task_id: str, proc: subprocess.Popen) -> None:
    try:
        code = proc.wait()
    except Exception:  # noqa: BLE001
        code = -1
    with _lock:
        _children.pop(task_id, None)
    _on_child_exit(task_id, code)


def _on_child_exit(task_id: str, code: int) -> None:
    t = task_engine.get_task(task_id) or {}
    st = t.get("status") or ""
    if st in ("paused", "stopped", "idle"):
        with db() as conn:
            conn.execute("UPDATE mining_requests SET pid=0 WHERE id=?", (task_id,))
        logger.info("task %s: miner exited (code=%s), task state=%s kept", task_id, code, st)
        return
    if code == 0:
        # 尊重 miner 的结算：轮完(running)→续轮；源站异常(queued+next_check)→保持等待；
        # 穷尽(idle)/用户停→保持（L-0211 修复 F4 的调度侧）
        t2 = task_engine.get_task(task_id) or {}
        st2 = t2.get("status") or ""
        if st2 == "running":
            with db() as conn:
                conn.execute(
                    "UPDATE mining_requests SET status='queued', pid=0, next_check_at='', "
                    "status_text=? WHERE id=?",
                    ("queued — next round", task_id))
        else:
            with db() as conn:
                conn.execute("UPDATE mining_requests SET pid=0 WHERE id=?", (task_id,))
        return
    failures = int(t.get("failures") or 0) + 1
    cap = max(1, cfg.int("scheduler_max_task_failures", 5))
    if failures >= cap:
        with db() as conn:
            conn.execute(
                "UPDATE mining_requests SET status='error', failures=?, status_text=?, "
                "finished_at=? WHERE id=?",
                (failures, f"error — {failures} consecutive failures (exit code {code})", utcnow(), task_id))
        logger.error("task %s: giving up after %s failures (code=%s)", task_id, failures, code)
        return
    # 每小时重启限速（scheduler_restart_max_per_hour）
    now_ts = time.time()
    rate = max(1, cfg.int("scheduler_restart_max_per_hour", 6))
    _restart_times[:] = [x for x in _restart_times if now_ts - x < 3600]
    if len(_restart_times) >= rate:
        with db() as conn:
            conn.execute(
                "UPDATE mining_requests SET status='error', failures=?, status_text=?, "
                "finished_at=? WHERE id=?",
                (failures, f"restarting too often (hourly limit {rate})", utcnow(), task_id))
        logger.error("task %s: restart rate exceeded (%s/h)", task_id, rate)
        return
    _restart_times.append(now_ts)
    backoff = max(0, cfg.int("scheduler_backoff_seconds", 30))
    wait_until = (datetime.now(timezone.utc).replace(microsecond=0) +
                  timedelta(seconds=backoff)).isoformat()
    with db() as conn:
        conn.execute(
            "UPDATE mining_requests SET status='queued', pid=0, failures=?, status_text=?, "
            "next_check_at=? WHERE id=?",
            (failures, f"retrying after failure #{failures} (exit code {code})", wait_until, task_id))
    logger.warning("task %s: miner failed (code=%s); requeued in %ss (%s/%s)",
                   task_id, code, backoff, failures, cap)


_orphans: set[str] = set()
_stale_warned: set[str] = set()
_stale_defers: dict[str, int] = {}


def _adopt_orphans() -> None:
    """接管"存活但未跟踪"的任务矿机（L-0213；L-0215 C-6/V-1；L-0217 C-5/V-R47-4）。

    跟踪到**进程真正死亡**；`pid` 存活但心跳陈旧 → **不立即换矿**（保 pid + 退避，防同 tick
    双矿机）：running 行转 queued + next_check 退避（旧进程仍活则由活 pid/单线/额度继续保护），
    paused/stopped 行不动；死 pid 忽略（running 行由 `reconcile_tasks` 处置——V-5 简化）。
    """
    from app.chat.mining import _alive

    stale_min = max(5, cfg.int("scheduler_orphan_stale_minutes", 60))
    retry_min = max(1, cfg.int("scheduler_orphan_retry_minutes", 5))
    try:
        with db() as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT id, pid, status, last_heartbeat, next_check_at FROM mining_requests "
                "WHERE kind='task' AND status IN ('running','paused','queued','stopped') "
                "AND pid != 0"
            ).fetchall()]
    except Exception:  # noqa: BLE001
        return
    for r in rows:
        tid = r["id"]
        with _lock:
            if tid in _children or tid in _orphans:
                continue
            _orphans.add(tid)
        if not _alive(r["pid"]):
            with _lock:
                _orphans.discard(tid)
                _stale_warned.discard(tid)
            continue
        if _heartbeat_stale(r.get("last_heartbeat") or "", stale_min):
            if r["status"] in ("running", "queued"):
                nxt = str(r.get("next_check_at") or "")
                if nxt and nxt > utcnow():
                    # 退避窗口内：静默等待（不计次、不刷屏；L-0225 修复 F2）
                    with _lock:
                        _orphans.discard(tid)
                    continue
                with _lock:
                    _orphans.discard(tid)
                    first = tid not in _stale_warned
                    _stale_warned.add(tid)
                    defers = _stale_defers.get(tid, 0) + 1
                    _stale_defers[tid] = defers
                max_defers = max(1, cfg.int("scheduler_orphan_max_defers", 3))
                if defers > max_defers:
                    # 升级（L-0221 Cov-C-2 / L-0225 F2）：按"退避周期"计满 → 清 pid 重排
                    logger.warning("task %s: stale-heartbeat deferrals exhausted (%d/%d); "
                                   "force-release pid=%s and requeue",
                                   tid, defers, max_defers, r["pid"])
                    with db() as conn:
                        conn.execute(
                            "UPDATE mining_requests SET status='queued', pid=0, "
                            "next_check_at='', status_text=? "
                            "WHERE id=? AND status IN ('running','queued')",
                            ("heartbeat stale beyond limit — force re-queue", tid))
                    with _lock:
                        _stale_defers.pop(tid, None)
                        _stale_warned.discard(tid)
                    continue
                if first or defers == 1:
                    logger.warning("task %s: pid=%s alive but heartbeat stale (>%s min); "
                                   "deferring retry by %s min (pid kept, %d/%d)",
                                   tid, r["pid"], stale_min, retry_min, defers, max_defers)
                until = (datetime.now(timezone.utc).replace(microsecond=0) +
                         timedelta(minutes=retry_min)).isoformat()
                with db() as conn:
                    conn.execute(
                        "UPDATE mining_requests SET status='queued', next_check_at=?, "
                        "status_text=? WHERE id=? AND status IN ('running','queued')",
                        (until, f"heartbeat stale — re-check in {retry_min} min (keeping pid while the old process lives)", tid))
            else:
                # paused/stopped：清残留 pid（不做 deferring 日志；L-0221 修复 V-R48-5）
                with db() as conn:
                    conn.execute(
                        "UPDATE mining_requests SET pid=0 "
                        "WHERE id=? AND status IN ('paused','stopped')", (tid,))
                with _lock:
                    _stale_defers.pop(tid, None)
            continue
        threading.Thread(target=_watch_pid, args=(tid,), daemon=True,
                         name=f"orphan-{tid}").start()
        logger.info("task %s: adopted live miner pid=%s (status=%s)",
                    tid, r["pid"], r["status"])


def _watch_pid(task_id: str) -> None:
    from app.chat.mining import _alive

    interval = max(10, cfg.int("scheduler_poll_seconds", 10) * 2)
    while True:
        time.sleep(interval)
        with db() as conn:
            row = conn.execute(
                "SELECT pid, status, last_heartbeat FROM mining_requests WHERE id=?",
                (task_id,)).fetchone()
        if row is None or not _alive(row["pid"]):
            break
        # 心跳陈旧但 pid 仍活：继续等待真实死亡（保护换矿，L-0217 V-R47-4）
    with _lock:
        _orphans.discard(task_id)
        _stale_warned.discard(task_id)
    _on_child_exit(task_id, 0)


def _tick() -> int:
    """一次调度：回收死进程 + 接管孤儿 + 到点巡检排队 + 按队列起矿。返回本 tick 起矿数。"""
    task_engine.reconcile_tasks()
    _adopt_orphans()
    cap = max(1, cfg.int("scheduler_global_max_miners", 1))
    single = cfg.bool("scheduler_single_active_per_theme", True)
    now = utcnow()
    spawned = 0

    # 0) 巡检：到点的 idle 任务 → 排队（phase=patrol；L-0213）
    with db() as conn:
        due = [dict(r) for r in conn.execute(
            "SELECT id FROM mining_requests WHERE kind='task' AND status='idle' "
            "AND (next_check_at='' OR next_check_at<=?) "
            "ORDER BY next_check_at ASC LIMIT 5", (now,)).fetchall()]
    for t in due:
        with db() as conn:
            cur = conn.execute(
                "UPDATE mining_requests SET status='queued', phase='patrol', "
                "next_check_at='', status_text=? WHERE id=? AND status='idle'",
                ("queued for patrol", t["id"]))
        if cur.rowcount:
            logger.info("task %s: due -> queued(patrol)", t["id"])

    with db() as conn:
        queued = [dict(r) for r in conn.execute(
            "SELECT * FROM mining_requests WHERE kind='task' AND status='queued' "
            "AND (next_check_at='' OR next_check_at<=?) "
            "ORDER BY created_at ASC LIMIT 10", (now,)).fetchall()]
    from app.chat.mining import _alive

    for t in queued:
        with _lock:
            tracked = len(_children)
        if max(tracked, _live_running()) >= cap:
            break
        with _lock:
            if single and t["theme"] in _busy_themes():
                continue
        if int(t.get("pid") or 0) and _alive(t["pid"]):
            continue    # 同任务旧进程仍活（心跳假死兜底）：不加倍（L-0217 V-R47-4）
        with db() as conn:
            cur = conn.execute(
                "UPDATE mining_requests SET status='running', status_text=? "
                "WHERE id=? AND status='queued'", ("starting", t["id"]))
        if not (cur.rowcount or 0):
            continue    # 原子抢占：另一调度器已接管该行（L-0217 C-14）
        try:
            _spawn(t)
        except Exception as exc:  # noqa: BLE001
            logger.error("task %s: spawn failed: %s", t["id"], exc)
            with db() as conn:
                conn.execute(
                    "UPDATE mining_requests SET status='queued', next_check_at=? WHERE id=?",
                    ((datetime.now(timezone.utc).replace(microsecond=0) +
                      timedelta(minutes=1)).isoformat(), t["id"]))
            continue
        with _lock:
            _stale_warned.discard(t["id"])
            _stale_defers.pop(t["id"], None)
        spawned += 1
    return spawned


def _pending() -> int:
    """"活跃"任务数：running + 已到点的 queued（未到 next_check 的排队不算）。"""
    now = utcnow()
    with db() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM mining_requests WHERE kind='task' AND ("
            "status='running' OR "
            "(status='queued' AND (next_check_at='' OR next_check_at<=?)))",
            (now,)).fetchone()[0]


def _lock_path():
    return cfg.data_dir / "scheduler.lock"


def _refresh_lock() -> None:
    """锁心跳刷新（L-0221）：文件内容 pid + 时间戳，mtime 作为持有者活性信号。"""
    try:
        p = _lock_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"{os.getpid()} {utcnow()}", encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def _acquire_lock() -> bool:
    """单实例锁（L-0217 C-14；L-0221 心跳 + 陈旧窃取；L-0269 容器重启自锁修复）。

    容器部署中调度器恒为 PID 1（Linux PID 命名空间）：上一代实例被外部重启后，
    锁里的 pid 数字与新实例自身相同，"存活检查"永远为真 → 旧逻辑要等 120s 陈旧
    才恢复。自 pid 判定为上一代自己 → 直接接管；不同 pid 的跨进程防重不变。
    多实例部署须各自独立 data_dir（锁按 data_dir 命名空间）。
    """
    from app.chat.mining import _alive

    lock = _lock_path()
    try:
        if lock.exists():
            fresh_s = max(120, cfg.int("scheduler_poll_seconds", 10) * 6)
            try:
                pid = int((lock.read_text(encoding="utf-8") or "0").split()[0] or 0)
            except (ValueError, IndexError):
                pid = 0
            age = time.time() - lock.stat().st_mtime
            if pid and pid == os.getpid():
                logger.warning("scheduler lock belongs to this pid (restarted instance); taking over")
            elif pid and _alive(pid) and age < fresh_s:
                return False
            else:
                logger.warning("stale scheduler lock (pid=%s age=%.0fs); stealing", pid, age)
        _refresh_lock()
        return True
    except Exception:  # noqa: BLE001
        return True


def main() -> None:
    ap = argparse.ArgumentParser(description="BioAgentDB user-mining scheduler")
    ap.add_argument("--once", action="store_true", help="run once, then exit when idle (for testing)")
    ap.add_argument("--timeout", type=int, default=0, help="total time limit in seconds (0 = unlimited)")
    ap.add_argument("--exit-on-lock", action="store_true",
                    help="exit instead of waiting when another scheduler holds the lock")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if cfg.validation_error:
        sys.exit(f"config invalid: {cfg.validation_error}")
    if cfg.unknown_keys:
        logging.warning("unknown config keys (ignored): %s", ", ".join(cfg.unknown_keys))
    if not cfg.bool("scheduler_enabled", True):
        sys.exit("scheduler disabled in config (scheduler.enabled=0)")
    init_db()
    if not args.once:
        if not _acquire_lock():
            if args.exit_on_lock:
                sys.exit(f"another scheduler is already running "
                         f"(lock: {cfg.data_dir / 'scheduler.lock'})")
            wait_s = max(2, cfg.int("scheduler_poll_seconds", 10))
            logger.warning("another scheduler is running; waiting for the lock (every %ss)", wait_s)
            while not _acquire_lock():   # L-0269：容器重启不再退出-退避循环，等待接管
                time.sleep(wait_s)
    logger.info("scheduler up: cap=%s single_per_theme=%s poll=%ss once=%s",
                cfg.int("scheduler_global_max_miners", 1),
                cfg.bool("scheduler_single_active_per_theme", True),
                cfg.int("scheduler_poll_seconds", 10), args.once)

    t0 = time.time()
    while True:
        try:
            _tick()
        except Exception as exc:  # noqa: BLE001
            logger.exception("tick failed: %s", exc)   # 单次失败不整体退出（L-0217 C-13）
        if not args.once:
            _refresh_lock()   # 仅持锁者刷新（L-0225 修复 F1：--once 不再覆盖常驻锁）
        if args.once:
            with _lock:
                busy = bool(_children) or bool(_orphans)
            if not busy and _pending() == 0:
                logger.info("no queued/running tasks; exiting (--once)")
                return
        if args.timeout and (time.time() - t0) >= args.timeout:
            logger.info("timeout reached (%ss); exiting", args.timeout)
            return
        time.sleep(max(2, cfg.int("scheduler_poll_seconds", 10)))


if __name__ == "__main__":
    main()
