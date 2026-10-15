"""矿机任务队列：原子抢单 + 按病补给（增量 sweep）。"""

from __future__ import annotations

import logging
import time

from app.core.db import db, utcnow
from app.harvest.sweep import sweep_papers

logger = logging.getLogger(__name__)


def claim_paper(theme: str, *, scope: str = "catalog", task_id: str = "") -> dict | None:
    """原子领取该病下一篇论文（多主题归属；L-0213 池分离 / L-0215 任务覆盖）。

    scope='catalog'：公共目录挖掘 — 含 `task_done` 复挖（受 `mining_task_recatalog` 控制）；
    scope='task'：用户任务 — 领 `pending/fetched`，以及**本任务尚未覆盖**的 `task_done`
    （即其他任务挖过的论文可为本任务复挖；同一任务绝不重复自己——C-2 修复）。
    """
    from app.core.config import cfg

    now = utcnow()
    if scope == "task" and task_id:
        # L-0217 修复 V-R47-2：对本任务覆盖行**全线**排除（含被回收重置为 pending 的论文）
        sql = """SELECT * FROM papers p
                 WHERE NOT EXISTS (SELECT 1 FROM task_papers tp
                                   WHERE tp.task_id = ? AND tp.paper_id = p.id)
                   AND p.mined_status IN ('pending','fetched','task_done')
                   AND EXISTS (SELECT 1 FROM paper_themes pt
                               WHERE pt.paper_id = p.id AND pt.theme = ?)
                 ORDER BY (p.mined_status='task_done') ASC,
                          (p.has_db_xref * 3 + p.has_suppl) DESC,
                          (p.fulltext_source != '') DESC,
                          p.year DESC, p.created_at DESC
                 LIMIT 1"""
        params: tuple = (task_id, theme)
    else:
        statuses = ["pending", "fetched"]
        if scope == "catalog" and cfg.bool("mining_task_recatalog", True):
            statuses.append("task_done")
        marks = ",".join(f"'{s}'" for s in statuses)
        sql = f"""SELECT * FROM papers p
                  WHERE p.mined_status IN ({marks})
                    AND EXISTS (SELECT 1 FROM paper_themes pt
                                WHERE pt.paper_id = p.id AND pt.theme = ?)
                  ORDER BY (p.has_db_xref * 3 + p.has_suppl) DESC,
                           (p.fulltext_source != '') DESC,
                           p.year DESC, p.created_at DESC
                  LIMIT 1"""
        params = (theme,)
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(sql, params).fetchone()
        if row is None:
            return None
        conn.execute("UPDATE papers SET mined_status='claimed', "
                     "mine_attempts=mine_attempts+1, updated_at=? WHERE id=?",
                     (now, row["id"]))
        paper = dict(row)
    return paper


def release_paper(paper_id: str, *, reason: str = "error", task_id: str = "") -> None:
    """释放回池（错误/崩溃恢复；L-0217 修复 V-R47-1/V-R47-2）。

    - 本任务已有覆盖行 → 恢复 `task_done`（本任务不再重领；他任务不受影响）；
    - `error` 且未达 `miner_max_attempts` → 回 `pending`（可重试）；达上限 → `error`（终态）；
    - 其它 reason 原样落库。
    """
    from app.core.config import cfg

    with db() as conn:
        if task_id and conn.execute(
                "SELECT 1 FROM task_papers WHERE task_id=? AND paper_id=?",
                (task_id, paper_id)).fetchone():
            new_status = "task_done"
        elif reason == "error":
            row = conn.execute("SELECT mine_attempts FROM papers WHERE id=?",
                               (paper_id,)).fetchone()
            attempts = int(row["mine_attempts"] or 0) if row else 0
            cap = max(1, cfg.int("miner_max_attempts", 3))
            new_status = "error" if attempts >= cap else "pending"
        else:
            new_status = reason
        conn.execute("UPDATE papers SET mined_status=?, updated_at=? WHERE id=?",
                     (new_status, utcnow(), paper_id))


def reap_stale_claims(theme: str, *, older_than_minutes: int = 30) -> int:
    """回收**陈旧 claimed**（进程被杀/看门狗退出留下的 in-flight 论文）→ 回池 pending。

    L-0211 修复 F3：矿机启动时调用；两类都收：
    ① 属于本主题且超过时限的 claimed；② **无任何主题归属的孤儿 claimed**（claim 按主题领取，孤儿永不可再领）。
    """
    from app.core.config import cfg
    from datetime import datetime, timedelta, timezone

    cutoff = (datetime.now(timezone.utc).replace(microsecond=0) -
              timedelta(minutes=max(1, int(older_than_minutes)))).isoformat()
    cap = max(1, cfg.int("miner_max_attempts", 3))
    # L-0217 修复 V-R47-2 / L-0221（R48）：覆盖行回 `task_done`；未达重试上限回 `pending`；达上限落 `error`
    with db() as conn:
        cur1 = conn.execute(
            """UPDATE papers SET mined_status=CASE
                   WHEN EXISTS (SELECT 1 FROM task_papers tp WHERE tp.paper_id = papers.id)
                     THEN 'task_done'
                   WHEN mine_attempts >= ? THEN 'error'
                   ELSE 'pending' END, updated_at=?
               WHERE mined_status='claimed' AND updated_at < ?
                 AND EXISTS (SELECT 1 FROM paper_themes pt
                             WHERE pt.paper_id = papers.id AND pt.theme = ?)""",
            (cap, utcnow(), cutoff, theme))
        cur2 = conn.execute(
            """UPDATE papers SET mined_status=CASE
                   WHEN EXISTS (SELECT 1 FROM task_papers tp WHERE tp.paper_id = papers.id)
                     THEN 'task_done'
                   WHEN mine_attempts >= ? THEN 'error'
                   ELSE 'pending' END, updated_at=?
               WHERE mined_status='claimed' AND updated_at < ?
                 AND NOT EXISTS (SELECT 1 FROM paper_themes pt WHERE pt.paper_id = papers.id)""",
            (cap, utcnow(), cutoff))
        return int(cur1.rowcount or 0) + int(cur2.rowcount or 0)


def count_pending(theme: str) -> int:
    with db() as conn:
        n = conn.execute(
            """SELECT COUNT(*) FROM papers p
               WHERE p.mined_status IN ('pending','fetched')
                 AND EXISTS (SELECT 1 FROM paper_themes pt
                             WHERE pt.paper_id = p.id AND pt.theme = ?)""",
            (theme,)).fetchone()[0]
    return n


def count_open(theme: str, task_id: str = "") -> int:
    """未完结工作量（含 **claimed**）。

    L-0212 V-2：任务口径把在飞 claimed 算进来（并发矿机持有 claim 时不误判挖尽）。
    L-0215 C-2：任务口径（task_id 非空）额外计入**本任务尚未覆盖**的 `task_done`。
    L-0217 C-9：目录口径（task_id 空）在 `mining_task_recatalog=1` 时同样计入 `task_done`
    （目录仍可复挖 → collect/status 的"挖尽"判定不得提前）。
    L-0217 V-R47-2：任务口径对本任务覆盖行**全线**排除（含被回收重置为 pending 的论文）。
    """
    from app.core.config import cfg

    if task_id:
        sql = """SELECT COUNT(*) FROM papers p
                 WHERE NOT EXISTS (SELECT 1 FROM task_papers tp
                                   WHERE tp.task_id = ? AND tp.paper_id = p.id)
                   AND p.mined_status IN ('pending','fetched','claimed','task_done')
                   AND EXISTS (SELECT 1 FROM paper_themes pt
                               WHERE pt.paper_id = p.id AND pt.theme = ?)"""
        params: list = [task_id, theme]
    else:
        extra = ("OR p.mined_status='task_done'"
                 if cfg.bool("mining_task_recatalog", True) else "")
        sql = ("""SELECT COUNT(*) FROM papers p
                  WHERE (p.mined_status IN ('pending','fetched','claimed') """
               + extra + """)
                  AND EXISTS (SELECT 1 FROM paper_themes pt
                              WHERE pt.paper_id = p.id AND pt.theme = ?)""")
        params = [theme]
    with db() as conn:
        n = conn.execute(sql, tuple(params)).fetchone()[0]
    return n


def refill(theme: str, max_new: int, *, queries: list[str] | None = None,
           progress=None, recent_days: int = 0, task_id: str = "") -> int:
    """为该病池补给新论文。返回新增数；`recent_days>0` 时只取"近 N 天"（巡检，L-0213）。

    多源模式（默认）：EPMC sweep + OpenAlex/CORE/PubMed/预印本（过滤式接入）；
    失败时回退 EPMC-only sweep。`task_id` 非空 → 该次检索的 `query_stats` 行打任务标记
    （L-0217 C-2：公共端点过滤，不暴露用户检索式）。
    """
    from app.core.config import cfg

    if cfg.bool("refill_multisource", True):
        try:
            from app.harvest.multisource import refill_multisource
            return refill_multisource(theme, max_new, queries=queries, progress=progress,
                                      recent_days=recent_days, task_id=task_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("multisource refill failed (%s); falling back to EPMC-only", exc)

    from app.harvest.sweep import window_queries
    from app.harvest.themes import get_theme
    th = get_theme(theme)
    if th is None:
        return 0
    # L-0225 修复 M-R49-2：显式 queries 先清洗（空串过滤），空则回落主题 base
    queries = [q.strip() for q in (queries or []) if q and q.strip()] or \
        [q for q in (th.get("facets") or {}).get("base_queries", []) if q]
    if recent_days > 0:
        queries = window_queries(queries, recent_days)
    if not queries:
        # 无检索式 = 真零（可挖尽），不是源站异常（L-0215）；此处留 WARNING 以免配置错误静默（L-0217 C-11）
        logger.warning("refill: no queries for theme %s (treated as genuine empty)", theme)
        return 0
    added_total = 0
    ok = 0
    for q in queries:
        if added_total >= max_new:
            break
        t0 = time.time()
        try:
            out = sweep_papers(q, max_papers=max_new - added_total, theme=theme,
                               progress=progress)
        except Exception as exc:  # noqa: BLE001
            logger.warning("refill sweep failed for %s: %s", q[:40], exc)
            if added_total == 0:
                raise RuntimeError(f"EPMC sweep error (EPMC-only path): {exc}") from exc
            continue
        if out.get("error") and not out.get("added") and added_total == 0:
            # 同一源首错即抛：与"确实零新增"区分（L-0211 修复 F4）；已有部分产出则保留
            logger.warning("EPMC request failed for %s", q[:40])
            raise RuntimeError("EPMC request failed (EPMC-only path)")
        ok += 1
        added_total += out["added"]
        _log_query(theme, q, out, int((time.time() - t0) * 1000), task_id)
    if ok == 0:
        # 全部检索式失败：与"确实零新增"区分（L-0211 修复 F4）
        raise RuntimeError("all EPMC queries failed (EPMC-only path)")
    return added_total


def _log_query(theme: str, query: str, outcome: dict, duration_ms: int,
               task_id: str = "") -> None:
    from app.core.utils import new_id
    with db() as conn:
        conn.execute(
            """INSERT INTO query_stats(id, theme, task_id, query, action, added, mined, records,
               created_at, duration_ms) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (new_id("qs"), theme, task_id, query[:300], "sweep", outcome.get("added", 0), 0, 0,
             utcnow(), duration_ms))