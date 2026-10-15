"""对话下单的挖矿订单：创建一个真实矿机进程执行，后台线程跟踪进度。

要点：
- 真正干活的仍是 scripts/miner.py（确定性引擎：挖掘→3 对抗者→核验→入库）
- 订单表记录基线（下单前该主题的论文数/记录数），据此算增量
- 任意主题可下单（疾病只是主题之一）：下单前 ensure_*_theme() 动态建主题；记录默认发布进公共目录（L-0225）
"""

from __future__ import annotations

import logging
import subprocess
import sys
import threading
import time
from pathlib import Path

from app.core.db import db, utcnow
from app.core.utils import new_id

logger = logging.getLogger(__name__)

PY = sys.executable   # 用当前解释器（跨平台 / Docker 友好）
MINER = str(Path(__file__).resolve().parents[2] / "scripts" / "miner.py")

from app.core.config import cfg

MAX_BUDGET_TEST = cfg.int("mining_task_budget_test_cap", 100)     # 一句话下单：测试档上限
MAX_BUDGET_FULL = cfg.int("mining_task_budget_full_cap", 2000)    # 一句话下单：全量档上限


def theme_label(theme: str) -> str:
    """主题 → 展示用病名（库里查不到就退回 slug）。"""
    try:
        from app.harvest.themes import get_theme

        th = get_theme(theme)
        if th and th.get("name"):
            return th["name"]
    except Exception:  # noqa: BLE001
        pass
    return theme


def _counts(theme: str) -> tuple[int, int]:
    """(该主题已读论文数, 该主题已入库记录数)，按多主题归属。"""
    with db() as conn:
        papers = conn.execute(
            "SELECT COUNT(*) FROM papers p JOIN paper_themes pt ON pt.paper_id=p.id "
            "WHERE pt.theme=? AND p.mined_status IN ('mined','no_accessions','off_topic')",
            (theme,)).fetchone()[0]
        records = conn.execute(
            """SELECT COUNT(DISTINCT dp.dataset_id) FROM dataset_papers dp
               JOIN paper_themes pt ON pt.paper_id = dp.paper_id
               JOIN datasets d ON d.id = dp.dataset_id
               WHERE pt.theme=? AND d.status IN ('verified','paper_verified')""",
            (theme,)).fetchone()[0]
    return papers, records


def start_task(theme: str, *, papers_per_round=None, cadence_hours=None, name: str = "",
               queries: list[str] | None = None, session_id: str = "", note: str = "",
               theme_kind: str = "") -> dict:
    """对话下单（L-0209）：创建**长期任务**（执行者 = 调度器）。

    L-0225：`theme_kind='topic'` 可指向任意研究主题；记录默认发布进公共目录
    （`mining_task_publish_records=1`）。
    """
    from app.mining import tasks as task_engine

    return task_engine.create_task(theme, title=name, name=name, queries=queries,
                                   papers_per_round=papers_per_round,
                                   cadence_hours=cadence_hours,
                                   session_id=session_id, note=note, theme_kind=theme_kind)


def start_order(theme: str, budget: int, *, name: str = "", queries: list[str] | None = None,
                session_id: str = "", note: str = "", full: bool = False) -> dict:
    """下单：确保主题存在，然后创建一个矿机进程。"""
    theme = (theme or "").strip()
    if not theme:
        return {"ok": False, "reason": "no_theme",
                "message": "I could not tell which disease to mine."}
    from app.harvest.themes import ensure_disease_theme, get_theme

    if get_theme(theme) is None:
        ensure_disease_theme(theme, name or theme, queries=queries)

    cap = MAX_BUDGET_FULL if full else MAX_BUDGET_TEST
    budget = max(10, min(int(budget or cap), cap))

    papers0, records0 = _counts(theme)
    order_id = new_id("order")
    with db() as conn:
        conn.execute(
            """INSERT INTO mining_requests(id, theme, budget, status, session_id, note,
               base_papers, base_records, created_at, started_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (order_id, theme, budget, "running", session_id, note[:300],
             papers0, records0, utcnow(), utcnow()))

    cmd = [PY, MINER, "--theme", theme, "--workers", "3", "--limit", str(budget),
           "--log", str(cfg.data_dir / "logs" / f"order-{theme}.log")]
    try:
        proc = subprocess.Popen(cmd, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as exc:  # noqa: BLE001
        with db() as conn:
            conn.execute("UPDATE mining_requests SET status='error', error=?, finished_at=? WHERE id=?",
                         (str(exc)[:300], utcnow(), order_id))
        return {"ok": False, "reason": "spawn_failed", "message": str(exc)[:200]}

    with db() as conn:
        conn.execute("UPDATE mining_requests SET pid=? WHERE id=?", (proc.pid, order_id))
    threading.Thread(target=_watch, args=(order_id, proc), daemon=True,
                     name=f"order-{order_id}").start()
    return {"ok": True, "order_id": order_id, "theme": theme,
            "theme_label": theme_label(theme), "budget": budget,
            "base_papers": papers0, "base_records": records0}


def _watch(order_id: str, proc: subprocess.Popen) -> None:
    """后台跟踪：矿机退出即结算增量。"""
    try:
        proc.wait()
    except Exception:  # noqa: BLE001
        pass
    with db() as conn:
        row = conn.execute("SELECT theme, base_papers, base_records FROM mining_requests WHERE id=?",
                           (order_id,)).fetchone()
    if not row:
        return
    papers1, records1 = _counts(row["theme"])
    with db() as conn:
        conn.execute(
            """UPDATE mining_requests SET status='done', finished_at=?,
               papers_seen=?, records_added=? WHERE id=?""",
            (utcnow(), max(0, papers1 - row["base_papers"]),
             max(0, records1 - row["base_records"]), order_id))


def _alive(pid: int) -> bool:
    """进程是否仍存活（Windows: OpenProcess；POSIX: kill(pid, 0)）。

    用于服务器重启对账、调度锁、同主题单线与全站并发计数。
    （L-0269：补 POSIX 分支——此前 Linux 容器里 ctypes.windll 不存在，
    异常兜底恒返回 True，导致死 pid 被当活：锁自锁死循环、主题误判忙、并发误占。）
    """
    if not pid:
        return False
    try:
        if not sys.platform.startswith("win"):
            try:
                os.kill(int(pid), 0)
                return True
            except ProcessLookupError:
                return False
            except PermissionError:
                return True
        import ctypes

        STILL_ACTIVE = 259
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return bool(ok) and code.value == STILL_ACTIVE
    except Exception:  # noqa: BLE001
        return True  # 无法判断时保守认为在跑


def reconcile() -> int:
    """把"进程已死但仍显示 running"的**旧版订单**（kind='quick'）结算掉。

    长期任务（kind='task'）不由这里结算：由调度器 `reconcile_tasks()` 重新排队（L-0210 修复 C-2）。
    """
    with db() as conn:
        rows = conn.execute("SELECT id, theme, base_papers, base_records, pid "
                            "FROM mining_requests WHERE status='running' AND kind='quick'").fetchall()
    n = 0
    for row in rows:
        if _alive(row["pid"]):
            continue
        papers1, records1 = _counts(row["theme"])
        with db() as conn:
            conn.execute(
                """UPDATE mining_requests SET status='interrupted', finished_at=?,
                   papers_seen=?, records_added=? WHERE id=?""",
                (utcnow(), max(0, papers1 - row["base_papers"]),
                 max(0, records1 - row["base_records"]), row["id"]))
        n += 1
    return n


def order_status(order_id: str) -> dict | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM mining_requests WHERE id=?", (order_id,)).fetchone()
    if not row:
        return None
    if (row["kind"] or "") == "task":
        # 长期任务：走任务引擎（私有记录计数），不用公共目录增量重算
        from app.mining import tasks as task_engine
        return task_engine.get_task(order_id)
    if row["status"] == "running" and not _alive(row["pid"]):
        reconcile()
        with db() as conn:
            row = conn.execute("SELECT * FROM mining_requests WHERE id=?", (order_id,)).fetchone()
        if not row:
            return None
    out = dict(row)
    papers1, records1 = _counts(out["theme"])
    out["papers_seen"] = max(out["papers_seen"], papers1 - out["base_papers"])
    out["records_added"] = max(out["records_added"], records1 - out["base_records"])
    out["theme_label"] = theme_label(out["theme"])
    return out


def wait_order(order_id: str, *, poll: float = 4.0, emit=None,
               max_seconds: int = 1800) -> dict:
    """等待订单完成，期间回调进度。"""
    t0 = time.time()
    while time.time() - t0 < max_seconds:
        st = order_status(order_id)
        if st is None:
            break
        if emit:
            try:
                emit(st)
            except Exception:  # noqa: BLE001
                pass
        if st["status"] in ("done", "error"):
            return st
        time.sleep(poll)
    return order_status(order_id) or {"status": "timeout"}