"""用户矿机任务引擎（L-0206/07/08）。

- 任务表 = 原有的 `mining_requests` **演进**（不新建 `mining_tasks`；`kind='task'` 为长期任务，
  旧行为 `kind='quick'`），偏差已登记台账。
- 执行者 = `scripts/scheduler.py` → `scripts/miner.py --task-id …`；
  记录**默认发布进公共目录**（L-0225；`mining_task_publish_records=0` 回退 `task_records`
  私有区、目录记录表零写入）；papers/paper_themes/主题统计随补货增长——共享池口径见 L-0211。
- 预算/节奏在系统限界内用户可设（决策 d）；创建开关与总闸来自配置文件（决策 c/f）。
"""

from __future__ import annotations

import csv
import io
import json

from app.core.config import cfg
from app.core.db import db, utcnow
from app.core.utils import new_id
from app.mining.store import task_record_counts

TERMINAL = ("stopped", "error")


def theme_label(theme: str) -> str:
    try:
        from app.harvest.themes import get_theme
        th = get_theme(theme)
        if th and th.get("name"):
            return th["name"]
    except Exception:  # noqa: BLE001
        pass
    return theme


def _clamp(value, lo, hi, default):
    try:
        v = int(value)
    except (TypeError, ValueError):
        v = int(default)
    return max(int(lo), min(int(hi), v))


# ---------------------------------------------------------------- create

def create_task(theme: str, *, title: str = "", name: str = "", queries: list[str] | None = None,
                papers_per_round: int | None = None, cadence_hours: int | None = None,
                session_id: str = "", note: str = "", kind: str = "task",
                theme_kind: str = "") -> dict:
    """建一条长期挖掘任务（queued）。返回 {ok, task_id/order_id, …}。

    `theme_kind='topic'`（L-0223/0225）→ 新主题按**任意研究主题**创建（低优先级、公开可见）；
    缺省按疾病主题。已存在的主题一律复用。
    """
    if not cfg.bool("mining_task_creation_enabled", True):
        return {"ok": False, "reason": "disabled",
                "message": "Task creation is disabled in config (mining.task_creation_enabled=0)."}
    theme = (theme or "").strip()
    if not theme:
        return {"ok": False, "reason": "no_theme",
                "message": "I could not tell which topic to mine."}
    pr = _clamp(papers_per_round or cfg.int("mining_task_papers_per_round", 200),
                cfg.int("mining_task_papers_per_round_min", 50),
                cfg.int("mining_task_papers_per_round_max", 2000),
                cfg.int("mining_task_papers_per_round", 200))
    cad = _clamp(cadence_hours or cfg.int("mining_task_cadence_hours", 24),
                 cfg.int("mining_task_cadence_hours_min", 1),
                 cfg.int("mining_task_cadence_hours_max", 168),
                 cfg.int("mining_task_cadence_hours", 24))

    with db() as conn:
        active = conn.execute(
            "SELECT COUNT(*) FROM mining_requests WHERE kind='task' "
            "AND status IN ('queued','running')").fetchone()[0]
        if active >= cfg.int("mining_task_max_active_tasks", 3):
            return {"ok": False, "reason": "at_capacity",
                    "message": "The miner is at capacity; please try again later."}
        if session_id:
            mine = conn.execute(
                "SELECT COUNT(*) FROM mining_requests WHERE kind='task' AND session_id=? "
                "AND status NOT IN ('stopped','error')", (session_id,)).fetchone()[0]
            if mine >= cfg.int("mining_task_max_tasks_per_session", 10):
                return {"ok": False, "reason": "session_cap",
                        "message": "You have reached the limit of active tasks."}

    # 主题按需创建放到**容量校验之后**（L-0225 修复 F7：拒绝路径不再先建主题）
    from app.harvest.themes import (ensure_disease_theme, ensure_topic_theme, get_theme,
                                    is_hidden, set_hidden)

    if get_theme(theme) is None:
        if (theme_kind or "") == "topic":
            ensure_topic_theme(theme, name or theme, queries=queries)
        else:
            ensure_disease_theme(theme, name or theme, queries=queries)
    if is_hidden(theme):
        set_hidden(theme, False)   # L-0250：隐藏的主题被再次挖掘 → 自动恢复显示

    tid = new_id("task")
    now = utcnow()
    with db() as conn:
        conn.execute(
            """INSERT INTO mining_requests(id, theme, budget, status, session_id, note,
               base_papers, base_records, created_at, started_at, title, queries,
               cadence_hours, papers_per_round, phase, rounds_done, kind, status_text)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (tid, theme, pr, "queued", session_id, note[:300], 0, 0, now, "",
             (title or theme_label(theme))[:200],
             json.dumps(queries or [], ensure_ascii=False), cad, pr, "exhaust", 0, kind,
             "queued — waiting for the scheduler"))
    return {"ok": True, "task_id": tid, "order_id": tid, "theme": theme,
            "theme_label": theme_label(theme), "papers_per_round": pr,
            "cadence_hours": cad, "budget": pr, "kind": kind}


# ---------------------------------------------------------------- read

def _cost_hint(row: dict) -> float:
    """费用估算（`mining_task_cost_hint_per_paper_usd`>0 时启用；否则 0 = 不显示金额）。"""
    coef = cfg.float("mining_task_cost_hint_per_paper_usd", 0.0)
    if coef <= 0:
        return 0.0
    return round(coef * int(row.get("papers_seen") or 0), 4)


def get_task(task_id: str) -> dict | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM mining_requests WHERE id=?", (task_id,)).fetchone()
    if not row:
        return None
    out = dict(row)
    out["theme_label"] = theme_label(out.get("theme", ""))
    counts = task_record_counts(task_id)
    out["records_added"] = counts["kept"]
    out["records_total"] = counts["total"]
    out["records_by_status"] = counts["by_status"]
    out["cost_estimate_usd"] = _cost_hint(out)
    try:
        out["queries"] = json.loads(out.get("queries") or "[]")
    except (TypeError, json.JSONDecodeError):
        out["queries"] = []
    return out


def list_tasks(*, session_id: str = "", limit: int = 50, kind: str = "task") -> list[dict]:
    sql = "SELECT * FROM mining_requests WHERE kind=?"
    args: list = [kind]
    if session_id:
        sql += " AND session_id=?"
        args.append(session_id)
    sql += " ORDER BY created_at DESC LIMIT ?"
    args.append(int(limit))
    with db() as conn:
        rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
    for r in rows:
        r["theme_label"] = theme_label(r.get("theme", ""))
        counts = task_record_counts(r["id"])
        r["records_added"] = counts["kept"]
        r["records_total"] = counts["total"]
        r["cost_estimate_usd"] = _cost_hint(r)
        try:
            r["queries"] = json.loads(r.get("queries") or "[]")
        except (TypeError, json.JSONDecodeError):
            r["queries"] = []
    return rows


def count_tasks(*, session_id: str = "", kind: str = "task") -> int:
    """任务总数（分页口径；L-0217 C-16：`/api/mining_tasks` 的 total 用真计数）。"""
    sql = "SELECT COUNT(*) FROM mining_requests WHERE kind=?"
    args: list = [kind]
    if session_id:
        sql += " AND session_id=?"
        args.append(session_id)
    with db() as conn:
        return int(conn.execute(sql, args).fetchone()[0])


# ---------------------------------------------------------------- actions

def set_task_state(task_id: str, action: str) -> dict:
    """pause / resume / stop / restart（用户操作；miner/调度器在下一个检查点响应）。"""
    now = utcnow()
    unhide_theme = ""
    with db() as conn:
        row = conn.execute("SELECT * FROM mining_requests WHERE id=?", (task_id,)).fetchone()
        if not row:
            return {"ok": False, "message": "task not found"}
        st = row["status"]
        if action == "pause":
            if st in ("stopped", "done", "error"):
                return {"ok": False, "message": f"cannot pause a {st} task"}
            conn.execute(
                "UPDATE mining_requests SET status='paused', status_text=?, "
                "next_check_at='' WHERE id=?",
                ("paused by user", task_id))
        elif action == "resume":
            if st != "paused":
                return {"ok": False, "message": f"cannot resume a {st} task"}
            conn.execute(
                "UPDATE mining_requests SET status='queued', next_check_at='', "
                "status_text=? WHERE id=?",
                ("queued — resumed by user", task_id))
        elif action == "stop":
            if st in ("stopped",):
                return {"ok": False, "message": "already stopped"}
            conn.execute(
                "UPDATE mining_requests SET status='stopped', finished_at=?, status_text=? "
                "WHERE id=?",
                (now, "stopped by user", task_id))
        elif action == "restart":
            # L-0250：stopped/error → 回到队列（清 pid/完成时间/失败计数）
            if st not in ("stopped", "error"):
                return {"ok": False, "message": f"cannot restart a {st} task"}
            conn.execute(
                "UPDATE mining_requests SET status='queued', pid=0, finished_at='', error='', "
                "next_check_at='', failures=0, status_text=? WHERE id=?",
                ("queued — restarted by user", task_id))
            unhide_theme = row["theme"] or ""
        else:
            return {"ok": False, "message": f"unknown action {action}"}
    if unhide_theme:
        try:
            from app.harvest import themes as _themes

            if _themes.is_hidden(unhide_theme):
                _themes.set_hidden(unhide_theme, False)   # 隐藏主题被重启 → 自动恢复显示
        except Exception:  # noqa: BLE001
            pass
    return {"ok": True, "task_id": task_id, "action": action}


# ---------------------------------------------------------------- records / export

def records(task_id: str, *, limit: int = 200, offset: int = 0,
            include_rejected: bool = True) -> list[dict]:
    where = "task_id=?"
    args: list = [task_id]
    if not include_rejected:
        where += " AND status IN ('verified','paper_verified')"
    with db() as conn:
        rows = [dict(r) for r in conn.execute(
            f"SELECT * FROM task_records WHERE {where} "
            "ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (*args, int(limit), int(offset))).fetchall()]
    return rows


def export_task(task_id: str, fmt: str = "jsonl") -> tuple[str, str, str]:
    """导出任务记录。返回 (filename, content_type, text)。"""
    task = get_task(task_id)
    if task is None:
        return "", "", ""
    rows = records(task_id, limit=100000, include_rejected=False)
    if fmt == "csv":
        buf = io.StringIO()
        cols = ["repository", "accession", "name", "description", "modality", "organism",
                "tissue", "disease", "platform", "sample_count", "status", "confidence",
                "evidence_zone", "evidence_quote", "url", "download_url", "paper_id",
                "theme", "created_at"]
        w = csv.writer(buf)
        w.writerow(cols)
        for r in rows:
            w.writerow([
                r.get("repository", ""), r.get("accession", ""), r.get("title", ""),
                r.get("description", ""), r.get("modality", ""), r.get("organism", ""),
                r.get("tissue", ""), r.get("disease", ""), r.get("platform", ""),
                r.get("sample_count") or "", r.get("status", ""), r.get("confidence", 0),
                r.get("evidence_zone", ""), r.get("evidence_quote", ""), r.get("url", ""),
                r.get("download_url", ""), r.get("paper_id", ""), r.get("theme", ""),
                r.get("created_at", "")])
        return f"{task_id}.csv", "text/csv; charset=utf-8", buf.getvalue()
    lines = []
    for r in rows:
        r = dict(r)
        r["theme_label"] = task.get("theme_label", "")
        lines.append(json.dumps(r, ensure_ascii=False))
    return f"{task_id}.jsonl", "application/x-ndjson; charset=utf-8", "\n".join(lines) + "\n"


# ---------------------------------------------------------------- scheduler support

def reconcile_tasks() -> int:
    """把"进程已死但 status=running"的任务交还调度器（重新排队）。"""
    from app.chat.mining import _alive  # 复用 Windows 进程存活判断

    n = 0
    with db() as conn:
        rows = conn.execute(
            "SELECT id, pid FROM mining_requests WHERE kind='task' AND status='running'"
        ).fetchall()
    for row in rows:
        if _alive(row["pid"]):
            continue
        with db() as conn:
            conn.execute(
                "UPDATE mining_requests SET status='queued', status_text=? WHERE id=?",
                ("scheduler restarted — re-queued", row["id"]))
        n += 1
    return n
