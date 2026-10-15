"""单病矿机进程：python scripts/miner.py --theme alzheimers-disease --workers 3 [--limit 100]

- 线程池：每线程 = 一篇论文的 挖掘Agent+3对抗者 流水线
- 领完即补：池子 < 阈值 → 有序从该病检索面补给；线程结束立刻领下一篇
- --limit 0 = 无配额（全量）；先试点 100/病
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import cfg  # noqa: E402
from app.core.db import db, init_db, utcnow  # noqa: E402
from app.core.utils import new_id  # noqa: E402

logger = logging.getLogger("miner")
POOL_KEEP = cfg.int("miner_pool_keep", 200)      # 池中待挖低于此值即补给（连续测试模式）
IDLE_SLEEP = cfg.int("miner_idle_sleep", 45)     # 补给失败时的重试间隔
HEARTBEAT_EVERY = cfg.int("miner_heartbeat_every", 20)
STOP_FLAG = Path(cfg.data_dir) / "miner.stop"


def _stop_requested() -> bool:
    try:
        return STOP_FLAG.exists()
    except Exception:  # noqa: BLE001
        return False


def _heartbeat(proc_id: str, counters_get, task_id: str = "",
               done: "threading.Event | None" = None) -> None:
    """心跳线程（L-0217 修复 C-5）：单次写入异常不致死；运行到进程结束（事件制，不看 STOP_FLAG）。"""
    while done is None or not done.is_set():
        try:
            with db() as conn:
                conn.execute(
                    "UPDATE miner_processes SET last_heartbeat=?, counters=? WHERE id=?",
                    (utcnow(), _counters_json(counters_get), proc_id))
                if task_id:
                    conn.execute("UPDATE mining_requests SET last_heartbeat=? WHERE id=?",
                                 (utcnow(), task_id))
        except Exception as exc:  # noqa: BLE001
            logger.warning("heartbeat write failed: %s", exc)
        time.sleep(HEARTBEAT_EVERY)


def _counters_json(counters_get) -> str:
    import json
    try:
        return json.dumps(counters_get(), ensure_ascii=False)
    except Exception:  # noqa: BLE001
        return "{}"


def _settle_task(task_id: str, counters: dict, exhausted: bool,
                 source_down: bool = False, patrol_days: int = 0) -> None:
    """任务结算（L-0209/0212/0213）：
    穷尽→idle+下次巡检；巡检轮无增量→idle+"巡检完成"；源站异常→queued+退避重试；轮完→running；用户停→不动。
    """
    from datetime import datetime, timedelta, timezone
    from app.mining import tasks as task_engine

    t = task_engine.get_task(task_id) or {}
    if (t.get("status") or "") in ("paused", "stopped"):
        return
    rounds = int(t.get("rounds_done") or 0) + 1
    seen = int(t.get("papers_seen") or 0) + int(counters.get("papers_claimed") or 0)
    kept = int(t.get("records_added") or 0)
    cad = int(t.get("cadence_hours") or 24)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    if exhausted:
        nxt = (now + timedelta(hours=cad)).isoformat()
        if patrol_days > 0:
            st, note = "idle", (f"patrol — no new papers in the last {patrol_days} days "
                                f"(papers read: {seen} · records found: {kept}); next check in {cad}h")
        else:
            st, note = "idle", f"exhausted — {seen} papers read · {kept} records found; patrols every {cad} h"
    elif source_down:
        retry = max(1, cfg.int("miner_source_retry_minutes", 15))
        errs = int(counters.get("refill_errors") or 0)
        nxt = (now + timedelta(minutes=retry)).isoformat()
        st, note = "queued", f"sources unreachable ({errs} consecutive refill failures); retrying in {retry} min"
    else:
        nxt, st = "", "running"
        note = f"round {rounds} done — {seen} papers read · {kept} records found"
    with db() as conn:
        conn.execute(
            "UPDATE mining_requests SET status=?, pid=0, rounds_done=?, papers_seen=?, "
            "records_added=?, next_check_at=?, status_text=? WHERE id=?",
            (st, rounds, seen, kept, nxt, note, task_id))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--theme", required=True)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--limit", type=int, default=0, help="max papers to process (0 = unlimited; e.g. 100 for a pilot)")
    parser.add_argument("--log", default="")
    parser.add_argument("--task-id", default="",
                        help="task id: records publish to the public catalog by default (publish=0 = private); an empty pool settles the task (exhaust first)")
    parser.add_argument("--patrol-days", type=int, default=0,
                        help="patrol window in days (>0 = only sweep the last N days; L-0213)")
    args = parser.parse_args()
    args.workers = max(1, min(int(args.workers), cfg.int("miner_workers_max", 6)))

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        filename=args.log or None,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)   # 降噪：避免日志里出现带第三方 key 的完整 URL

    init_db()
    from app.harvest.themes import seed_themes, get_theme
    seed_themes(force=True)
    if get_theme(args.theme) is None:
        logger.error("unknown theme %s", args.theme)
        sys.exit(2)

    proc_id = new_id("miner")
    with db() as conn:
        conn.execute(
            """INSERT INTO miner_processes(id, theme, task_id, pid, workers, status, pilot_limit,
               last_heartbeat, started_at)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (proc_id, args.theme, args.task_id or "", os.getpid(), args.workers, "running",
             args.limit, utcnow(), utcnow()))

    # 任务模式（L-0209）：认领任务、进入 running；退出时按"穷尽/轮完/用户停"结算
    task_row: dict = {}
    task_queries: list = []
    if args.task_id:
        from app.mining import tasks as task_engine
        task_row = task_engine.get_task(args.task_id) or {}
        if not task_row:
            logger.error("unknown task %s", args.task_id)
            sys.exit(2)
        task_queries = [q for q in (task_row.get("queries") or []) if q]
        with db() as conn:
            round_note = (f"patrolling (last {args.patrol_days} days)" if args.patrol_days > 0
                          else f"mining — round {int(task_row.get('rounds_done') or 0) + 1}")
            conn.execute(
                """UPDATE mining_requests SET status='running',
                   started_at=CASE WHEN started_at='' THEN ? ELSE started_at END,
                   last_heartbeat=?, status_text=? WHERE id=?""",
                (utcnow(), utcnow(), round_note, args.task_id))

    from app.miner.claim import claim_paper, count_open, count_pending, reap_stale_claims, refill
    from app.miner.paperflow import run_paper

    try:
        reaped = reap_stale_claims(args.theme,
                                   older_than_minutes=cfg.int("miner_claim_stale_minutes", 30))
        if reaped:
            logger.info("reaped %d stale claim(s) (theme=%s)", reaped, args.theme)
    except Exception as exc:  # noqa: BLE001
        logger.warning("reap stale claims failed: %s", exc)

    counters = {"papers_claimed": 0, "mined": 0, "no_data": 0, "off_topic": 0,
                "verified": 0, "paper_verified": 0, "rejected": 0,
                "corrections": 0, "errors": 0, "refills": 0, "empty_refills": 0,
                "refill_inflight": 0, "refill_errors": 0}
    c_lock = threading.Lock()
    stop = threading.Event()
    heartbeat_done = threading.Event()

    def _bump(key: str, n: int = 1) -> None:
        with c_lock:
            counters[key] = int(counters.get(key, 0)) + n

    threading.Thread(target=_heartbeat,
                     args=(proc_id, lambda: dict(counters), args.task_id, heartbeat_done),
                     daemon=True).start()

    # 穷尽/源站看门狗——由 **worker 的实际补货结果** 驱动（不自己打源站）：
    # - 任务模式：判尽 = 连续 N 次"补货 0 篇"（empty_refills）且无补货进行中
    #   （refill_inflight=0）且复查 pending==0（防"迟到补货"误判）→ 收尾（先穷尽、后巡检）；
    # - 所有模式：源站不健康（连续补货**异常**）→ source_down（任务退避重试；独立矿机 exit 3）。
    exhausted = threading.Event()
    source_down = threading.Event()

    def _exhaust_watch() -> None:
        probe = max(5, cfg.int("miner_exhaust_probe_s", 20))
        need = max(1, cfg.int("miner_exhaust_streak", 2))
        err_need = max(1, cfg.int("miner_refill_error_streak", 3))
        while not stop.is_set():
            time.sleep(probe)
            if args.task_id:
                # 周期回收陈旧 claim（L-0217 修复 C-6；L-0221：同主题另有活矿机时让位 + 回收留日志）
                try:
                    skip = False
                    with db() as conn:
                        rows = conn.execute(
                            "SELECT id, pid, status FROM miner_processes WHERE theme=?",
                            (args.theme,)).fetchall()
                    from app.chat.mining import _alive
                    for r in rows:
                        if r["id"] == proc_id:
                            continue
                        if r["status"] == "running" and r["pid"] and _alive(r["pid"]):
                            skip = True     # 兄弟矿机在飞：勿动其 claim
                            break
                    if not skip:
                        from app.miner.claim import reap_stale_claims as _reap
                        n_reaped = _reap(
                            args.theme,
                            older_than_minutes=cfg.int("miner_claim_stale_minutes", 30))
                        if n_reaped:
                            logger.info("task %s: periodic reap released %d stale claim(s)",
                                        args.task_id, n_reaped)
                except Exception:  # noqa: BLE001
                    pass
            with c_lock:
                empty = int(counters.get("empty_refills") or 0)
                inflight = int(counters.get("refill_inflight") or 0)
                errs = int(counters.get("refill_errors") or 0)
            if errs >= err_need:
                logger.warning("refill failing repeatedly (%d); source unwell -> back off",
                               errs)
                source_down.set()
                stop.set()
                return
            if args.task_id and empty >= need and inflight == 0:
                try:
                    if count_open(args.theme, task_id=args.task_id) > 0:
                        continue     # 迟到补货或在飞 claim 可见 → 不判尽（L-0212 修复 V-2；
                                     # L-0215：含"本任务尚未覆盖的 task_done"）
                except Exception:  # noqa: BLE001
                    pass
                logger.info("no refill yield (empty_refills=%d); task %s exhausted",
                            empty, args.task_id)
                exhausted.set()
                stop.set()
                return

    threading.Thread(target=_exhaust_watch, daemon=True,
                     name=f"exhaust-{args.theme}").start()

    def _note_refill(added: int) -> None:
        """补货结果记账：空补货累计（任务模式判"穷尽"用）。"""
        with c_lock:
            if added:
                counters["empty_refills"] = 0
            else:
                counters["empty_refills"] = int(counters.get("empty_refills") or 0) + 1

    def _do_refill(want: int) -> int:
        """补货（标记 in-flight，供看门狗避开"迟到补货"窗口）。

        L-0215 C-7 / L-0221（R48）：任务模式补货采用合并集，**任务检索式优先**（用户意图面在前），
        主题 base 去重补足；空串过滤。巡检窗照常套用。
        """
        qs = None
        if args.task_id and task_queries:
            try:
                from app.harvest.themes import get_theme
                th = get_theme(args.theme) or {}
                base = [q.strip() for q in (th.get("facets") or {}).get("base_queries", [])
                        if q and q.strip()]
                extras = [q.strip() for q in task_queries if q and q.strip()]
                # L-0225 修复 F4：并集（extras 全保留 + base 去重补足；原对称差会丢交集式子）
                qs = (extras + [q for q in base if q not in extras])[:16]
            except Exception:  # noqa: BLE001
                qs = None
        with c_lock:
            counters["refill_inflight"] = int(counters.get("refill_inflight") or 0) + 1
        try:
            return refill(args.theme, want, queries=qs, recent_days=args.patrol_days,
                          task_id=(args.task_id or ""))
        finally:
            with c_lock:
                counters["refill_inflight"] = int(counters.get("refill_inflight") or 0) - 1

    # 启动前把池补到至少 POOL_KEEP（任务模式按"本任务未覆盖"口径计数，L-0217 观察#10）
    def _prime_pool() -> None:
        if args.task_id:
            pending = count_open(args.theme, task_id=args.task_id)
        else:
            pending = count_pending(args.theme)
        if pending < POOL_KEEP:
            try:
                added = _do_refill(POOL_KEEP - pending)
                _bump("refills", 1)
                _note_refill(added)
                logger.info("primed pool: +%d papers (theme=%s)", added, args.theme)
            except Exception as exc:  # noqa: BLE001
                logger.warning("prime failed: %s", exc)

    _prime_pool()

    def _worker(wid: int) -> None:
        while not stop.is_set():
            if args.limit > 0:
                with c_lock:
                    if counters["papers_claimed"] >= args.limit:
                        return
            if args.task_id:
                with db() as conn:
                    trow = conn.execute(
                        "SELECT status FROM mining_requests WHERE id=?",
                        (args.task_id,)).fetchone()
                if trow is None or trow["status"] not in ("queued", "running"):
                    stop.set()   # 用户暂停/停止 → 尽快收尾
                    return
            paper = None
            try:
                paper = claim_paper(args.theme,
                                    scope=("task" if args.task_id else "catalog"),
                                    task_id=(args.task_id or ""))
            except Exception as exc:  # noqa: BLE001
                logger.warning("claim failed: %s", exc)
            if paper is None:
                # 补给不足 → 再拉；拉不到 → 休眠重试
                pending = 0
                try:
                    pending = count_pending(args.theme)
                    if pending < POOL_KEEP:
                        try:
                            added = _do_refill(100)
                            _bump("refills", 1)
                            _note_refill(added)
                            with c_lock:
                                counters["refill_errors"] = 0
                            pending += added
                        except Exception as exc:  # noqa: BLE001
                            logger.warning("refill failed: %s", exc)
                            with c_lock:
                                counters["refill_errors"] = int(
                                    counters.get("refill_errors") or 0) + 1
                except Exception:  # noqa: BLE001
                    pending = 0
                if pending <= 0 and _stop_requested():
                    return
                time.sleep(IDLE_SLEEP if pending <= 0 else 1)
                continue
            _bump("papers_claimed")
            try:
                result = run_paper(paper["id"], args.theme, task_id=(args.task_id or None))
                _bump("mined")
                for key in ("verified", "paper_verified", "rejected"):
                    if result.get(key):
                        _bump(key, result[key])
                verdict = result.get("verdict", "")
                if verdict == "no_data":
                    _bump("no_data")
                elif verdict == "off_topic":
                    _bump("off_topic")
            except Exception as exc:  # noqa: BLE001
                logger.exception("paper flow failed: %s", exc)
                _bump("errors")
                from app.miner.claim import release_paper
                try:
                    release_paper(paper["id"], reason="error",
                                  task_id=(args.task_id or ""))
                except Exception:  # noqa: BLE001
                    pass
            with db() as conn:
                conn.execute(
                    "UPDATE miner_processes SET processed=?, counters=? WHERE id=?",
                    (counters["papers_claimed"], _counters_json(lambda: dict(counters)),
                     proc_id))
            _ = wid

    threads: list[threading.Thread] = []
    for i in range(max(1, args.workers)):
        t = threading.Thread(target=_worker, args=(i,), daemon=True,
                             name=f"worker-{args.theme}-{i}")
        threads.append(t)
        t.start()

    try:
        while not stop.is_set():
            alive = [t for t in threads if t.is_alive()]
            if not alive:
                break
            with c_lock:
                claimed = counters["papers_claimed"]
            if args.limit > 0 and claimed >= args.limit:
                stop.set()
            time.sleep(2)
    except KeyboardInterrupt:
        logger.info("stopping miner (SIGINT)")
        stop.set()
    finally:
        stop.set()
        drain_s = max(5, cfg.int("miner_drain_timeout_s", 300))
        for t in threads:
            t.join(timeout=drain_s)          # 排空 in-flight 论文（L-0211 修复 F3）
        heartbeat_done.set()                 # L-0221：排空结束后才停心跳（与"运行至退出"口径对齐）
        with db() as conn:
            conn.execute(
                "UPDATE miner_processes SET status='stopped', processed=?, counters=? "
                "WHERE id=?",
                (int(counters.get("papers_claimed") or 0),
                 _counters_json(lambda: dict(counters)), proc_id))   # 退出落终值（L-0212 修复 M-4）
        logger.info("miner %s finished: %s", args.theme, dict(counters))
        if args.task_id:
            _settle_task(args.task_id, counters, exhausted.is_set(),
                         source_down.is_set(), args.patrol_days)
        from app.harvest.ingest import update_theme_stats
        try:
            update_theme_stats(args.theme)
        except Exception:  # noqa: BLE001
            pass
        # 独立矿机（非任务）在源站异常时以 **exit 3** 收尾（supervisor/collect 据此退避，L-0212）
        sys.exit(3 if (source_down.is_set() and not args.task_id) else 0)


if __name__ == "__main__":
    main()