"""BioAgentDB SQLite 存储层：目录库表结构 + 连接 + settings。"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app.core.config import cfg

SCHEMA = """
CREATE TABLE IF NOT EXISTS themes (
    id             TEXT PRIMARY KEY,
    slug           TEXT NOT NULL UNIQUE,
    name           TEXT NOT NULL,
    description    TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT 'active',
    kind           TEXT NOT NULL DEFAULT 'disease',
    priority       INTEGER NOT NULL DEFAULT 0,
    facets         TEXT NOT NULL DEFAULT '[]',
    target_records INTEGER NOT NULL DEFAULT 1000,
    records_count  INTEGER NOT NULL DEFAULT 0,
    papers_scanned INTEGER NOT NULL DEFAULT 0,
    last_run_at    TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS papers (
    id            TEXT PRIMARY KEY,
    pmid          TEXT NOT NULL DEFAULT '',
    pmcid         TEXT NOT NULL DEFAULT '',
    doi           TEXT NOT NULL DEFAULT '',
    title         TEXT NOT NULL DEFAULT '',
    journal       TEXT NOT NULL DEFAULT '',
    year          INTEGER,
    authors       TEXT NOT NULL DEFAULT '',
    abstract      TEXT NOT NULL DEFAULT '',
    is_preprint   INTEGER NOT NULL DEFAULT 0,
    is_oa         INTEGER NOT NULL DEFAULT 0,
    fulltext_source TEXT NOT NULL DEFAULT '',
    fulltext      TEXT NOT NULL DEFAULT '',
    da_text       TEXT NOT NULL DEFAULT '',
    supp_files    TEXT NOT NULL DEFAULT '[]',
    mined_status  TEXT NOT NULL DEFAULT 'pending',
    mined_at      TEXT NOT NULL DEFAULT '',
    mine_attempts INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_papers_pmid  ON papers(pmid);
CREATE INDEX IF NOT EXISTS idx_papers_pmcid ON papers(pmcid);
CREATE INDEX IF NOT EXISTS idx_papers_doi   ON papers(doi);
CREATE INDEX IF NOT EXISTS idx_papers_year  ON papers(year);

-- 多主题归属：一篇论文可属于多个疾病主题（台账/统计按此；论文仍只挖一次）
CREATE TABLE IF NOT EXISTS paper_themes (
    paper_id   TEXT NOT NULL,
    theme      TEXT NOT NULL,
    source     TEXT NOT NULL DEFAULT 'sweep',
    created_at TEXT NOT NULL,
    PRIMARY KEY (paper_id, theme)
);
CREATE INDEX IF NOT EXISTS idx_ptheme_theme ON paper_themes(theme);
CREATE INDEX IF NOT EXISTS idx_ptheme_paper ON paper_themes(paper_id);

CREATE TABLE IF NOT EXISTS paper_sources (
    paper_id     TEXT NOT NULL,
    source       TEXT NOT NULL,
    source_id    TEXT NOT NULL,
    url          TEXT NOT NULL DEFAULT '',
    license      TEXT NOT NULL DEFAULT '',
    retrieved_at TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (paper_id, source, source_id)
);
CREATE INDEX IF NOT EXISTS idx_psources_source ON paper_sources(source);

CREATE TABLE IF NOT EXISTS record_normalization (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    record_id    TEXT NOT NULL,
    field        TEXT NOT NULL,
    raw          TEXT NOT NULL DEFAULT '',
    canonical    TEXT NOT NULL DEFAULT '',
    part         TEXT NOT NULL DEFAULT '',
    method       TEXT NOT NULL DEFAULT '',
    quote        TEXT NOT NULL DEFAULT '',
    norm_version TEXT NOT NULL,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_norm_record ON record_normalization(record_id, field);
CREATE INDEX IF NOT EXISTS idx_norm_canonical ON record_normalization(field, canonical);

CREATE TABLE IF NOT EXISTS normalization_proposals (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    field        TEXT NOT NULL,
    raw          TEXT NOT NULL,
    part         TEXT NOT NULL DEFAULT '',
    kind         TEXT NOT NULL,
    canonical    TEXT NOT NULL DEFAULT '',
    quote        TEXT NOT NULL DEFAULT '',
    rationale    TEXT NOT NULL DEFAULT '',
    themes       TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'pending',
    valid        INTEGER NOT NULL DEFAULT 0,
    validation   TEXT NOT NULL DEFAULT '',
    source       TEXT NOT NULL DEFAULT 'catalog',
    eval_tag     TEXT NOT NULL DEFAULT '',
    norm_version TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    decided_at   TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_normprop_status ON normalization_proposals(status);

CREATE TABLE IF NOT EXISTS chat_sessions (
    id         TEXT PRIMARY KEY,
    title      TEXT NOT NULL DEFAULT '',
    state      TEXT NOT NULL DEFAULT '{}',
    kind       TEXT NOT NULL DEFAULT 'research',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_messages (
    id         TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    role       TEXT NOT NULL,
    content    TEXT NOT NULL DEFAULT '',
    meta       TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chatmsg_session ON chat_messages(session_id, created_at);

CREATE TABLE IF NOT EXISTS extract_misses (
    id         TEXT PRIMARY KEY,
    paper_id   TEXT NOT NULL DEFAULT '',
    accession  TEXT NOT NULL DEFAULT '',
    url        TEXT NOT NULL DEFAULT '',
    repository TEXT NOT NULL DEFAULT '',
    quote      TEXT NOT NULL DEFAULT '',
    known      INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_extract_misses_repo ON extract_misses(repository);

CREATE TABLE IF NOT EXISTS datasets (
    id             TEXT PRIMARY KEY,
    fingerprint    TEXT NOT NULL UNIQUE,
    title          TEXT NOT NULL DEFAULT '',
    description    TEXT NOT NULL DEFAULT '',
    modality       TEXT NOT NULL DEFAULT '',
    modalities     TEXT NOT NULL DEFAULT '[]',
    organism       TEXT NOT NULL DEFAULT '',
    tissue         TEXT NOT NULL DEFAULT '',
    disease        TEXT NOT NULL DEFAULT '',
    platform       TEXT NOT NULL DEFAULT '',
    sample_count   INTEGER,
    cell_count     INTEGER,
    status         TEXT NOT NULL DEFAULT 'pending',
    confidence     REAL NOT NULL DEFAULT 0,
    evidence_summary TEXT NOT NULL DEFAULT '',
    derived_from_preprint INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_datasets_omic  ON datasets(modality);
CREATE INDEX IF NOT EXISTS idx_datasets_org   ON datasets(organism);
CREATE INDEX IF NOT EXISTS idx_datasets_status ON datasets(status);

CREATE TABLE IF NOT EXISTS dataset_links (
    id           TEXT PRIMARY KEY,
    dataset_id   TEXT NOT NULL,
    repository   TEXT NOT NULL DEFAULT '',
    accession    TEXT NOT NULL DEFAULT '',
    url          TEXT NOT NULL DEFAULT '',
    link_type    TEXT NOT NULL DEFAULT 'portal',
    file_name    TEXT NOT NULL DEFAULT '',
    is_primary   INTEGER NOT NULL DEFAULT 0,
    reachable    INTEGER,
    http_status  TEXT NOT NULL DEFAULT '',
    last_checked TEXT NOT NULL DEFAULT '',
    note         TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_links_dataset ON dataset_links(dataset_id);
CREATE INDEX IF NOT EXISTS idx_links_repo    ON dataset_links(repository);

CREATE TABLE IF NOT EXISTS dataset_papers (
    dataset_id TEXT NOT NULL,
    paper_id   TEXT NOT NULL,
    role       TEXT NOT NULL DEFAULT 'primary',
    PRIMARY KEY (dataset_id, paper_id)
);

CREATE TABLE IF NOT EXISTS evidence (
    id          TEXT PRIMARY KEY,
    dataset_id  TEXT NOT NULL,
    paper_id    TEXT NOT NULL,
    zone        TEXT NOT NULL DEFAULT '',
    quote       TEXT NOT NULL DEFAULT '',
    char_offset INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_evidence_dataset ON evidence(dataset_id);

CREATE TABLE IF NOT EXISTS harvest_runs (
    id          TEXT PRIMARY KEY,
    theme_id    TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'queued',
    stage       TEXT NOT NULL DEFAULT '',
    progress    REAL NOT NULL DEFAULT 0,
    cursor      TEXT NOT NULL DEFAULT '{}',
    counters    TEXT NOT NULL DEFAULT '{}',
    error       TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    started_at  TEXT NOT NULL DEFAULT '',
    finished_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_runs_created ON harvest_runs(created_at);

-- 矿机：每病一个进程的心跳与计数
CREATE TABLE IF NOT EXISTS miner_processes (
    id             TEXT PRIMARY KEY,
    theme          TEXT NOT NULL,
    task_id        TEXT NOT NULL DEFAULT '',
    pid            INTEGER NOT NULL DEFAULT 0,
    workers        INTEGER NOT NULL DEFAULT 3,
    status         TEXT NOT NULL DEFAULT 'starting',
    pilot_limit    INTEGER NOT NULL DEFAULT 0,
    processed      INTEGER NOT NULL DEFAULT 0,
    last_heartbeat TEXT NOT NULL DEFAULT '',
    counters       TEXT NOT NULL DEFAULT '{}',
    started_at     TEXT NOT NULL DEFAULT '',
    log_file       TEXT NOT NULL DEFAULT ''
);

-- 论文级对抗审计
CREATE TABLE IF NOT EXISTS paper_audits (
    id          TEXT PRIMARY KEY,
    paper_id    TEXT NOT NULL,
    theme       TEXT NOT NULL DEFAULT '',
    task_id     TEXT NOT NULL DEFAULT '',
    findings    TEXT NOT NULL DEFAULT '[]',
    verdict     TEXT NOT NULL DEFAULT 'pending',
    records     TEXT NOT NULL DEFAULT '[]',
    timings_ms  TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL,
    duration_ms INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_audit_paper ON paper_audits(paper_id);
CREATE INDEX IF NOT EXISTS idx_audit_theme ON paper_audits(theme);

-- 检索式产出率统计（试点调参用）
CREATE TABLE IF NOT EXISTS query_stats (
    id          TEXT PRIMARY KEY,
    theme       TEXT NOT NULL DEFAULT '',
    task_id     TEXT NOT NULL DEFAULT '',
    query       TEXT NOT NULL DEFAULT '',
    action      TEXT NOT NULL DEFAULT 'sweep',
    added       INTEGER NOT NULL DEFAULT 0,
    mined       INTEGER NOT NULL DEFAULT 0,
    records     INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    duration_ms INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_qs_theme ON query_stats(theme);

CREATE TABLE IF NOT EXISTS candidates (
    id            TEXT PRIMARY KEY,
    run_id        TEXT NOT NULL DEFAULT '',
    paper_id      TEXT NOT NULL DEFAULT '',
    fingerprint   TEXT NOT NULL DEFAULT '',
    source        TEXT NOT NULL DEFAULT '',
    accession     TEXT NOT NULL DEFAULT '',
    title         TEXT NOT NULL DEFAULT '',
    verdict       TEXT NOT NULL DEFAULT 'pending',
    reason        TEXT NOT NULL DEFAULT '',
    payload       TEXT NOT NULL DEFAULT '{}',
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cand_run ON candidates(run_id);

CREATE TABLE IF NOT EXISTS source_health (
    source     TEXT PRIMARY KEY,
    ok         INTEGER NOT NULL DEFAULT 0,
    latency_ms REAL NOT NULL DEFAULT 0,
    detail     TEXT NOT NULL DEFAULT '',
    checked_at TEXT NOT NULL DEFAULT ''
);

-- 对话下单的挖矿请求（由真实矿机执行）
CREATE TABLE IF NOT EXISTS mining_requests (
    id            TEXT PRIMARY KEY,
    theme         TEXT NOT NULL,
    budget        INTEGER NOT NULL DEFAULT 100,
    status        TEXT NOT NULL DEFAULT 'pending',
    session_id    TEXT NOT NULL DEFAULT '',
    note          TEXT NOT NULL DEFAULT '',
    papers_seen   INTEGER NOT NULL DEFAULT 0,
    records_added INTEGER NOT NULL DEFAULT 0,
    base_papers   INTEGER NOT NULL DEFAULT 0,
    base_records  INTEGER NOT NULL DEFAULT 0,
    pid           INTEGER NOT NULL DEFAULT 0,
    error         TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    started_at    TEXT NOT NULL DEFAULT '',
    finished_at   TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_mreq_status ON mining_requests(status);

-- 任务记录区（task_records）：默认与目录同写（L-0225）；publish=0 时为私有区；T1/T2 同门槛
CREATE TABLE IF NOT EXISTS task_records (
    id             TEXT PRIMARY KEY,
    task_id        TEXT NOT NULL,
    fingerprint    TEXT NOT NULL,
    repository     TEXT NOT NULL DEFAULT '',
    accession      TEXT NOT NULL DEFAULT '',
    url            TEXT NOT NULL DEFAULT '',
    download_url   TEXT NOT NULL DEFAULT '',
    link_type      TEXT NOT NULL DEFAULT 'portal',
    title          TEXT NOT NULL DEFAULT '',
    description    TEXT NOT NULL DEFAULT '',
    modality       TEXT NOT NULL DEFAULT '',
    organism       TEXT NOT NULL DEFAULT '',
    tissue         TEXT NOT NULL DEFAULT '',
    disease        TEXT NOT NULL DEFAULT '',
    platform       TEXT NOT NULL DEFAULT '',
    sample_count   INTEGER,
    status         TEXT NOT NULL DEFAULT 'pending',
    confidence     REAL NOT NULL DEFAULT 0,
    evidence_zone  TEXT NOT NULL DEFAULT '',
    evidence_quote TEXT NOT NULL DEFAULT '',
    paper_id       TEXT NOT NULL DEFAULT '',
    theme          TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    UNIQUE(task_id, fingerprint)
);
CREATE INDEX IF NOT EXISTS idx_trec_task ON task_records(task_id, created_at);

-- 任务×论文覆盖日志（L-0215 修复 C-2）：同主题多任务各自记账"自己挖过哪些论文"，
-- 任务可复挖他人 task_done、但绝不重复自己；目录侧不受影响。
CREATE TABLE IF NOT EXISTS task_papers (
    task_id    TEXT NOT NULL,
    paper_id   TEXT NOT NULL,
    outcome    TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    PRIMARY KEY (task_id, paper_id)
);
CREATE INDEX IF NOT EXISTS idx_tpapers_paper ON task_papers(paper_id);

CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);
"""


def utcnow() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = str(db_path or cfg.db_path)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with db() as conn:
        conn.executescript(SCHEMA)
        # 迁移：老库补列（papers.theme 等）
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(papers)").fetchall()}
        if "theme" not in cols:
            conn.execute("ALTER TABLE papers ADD COLUMN theme TEXT NOT NULL DEFAULT ''")
        if "has_db_xref" not in cols:
            conn.execute("ALTER TABLE papers ADD COLUMN has_db_xref INTEGER NOT NULL DEFAULT 0")
        if "has_suppl" not in cols:
            conn.execute("ALTER TABLE papers ADD COLUMN has_suppl INTEGER NOT NULL DEFAULT 0")
        if "mine_attempts" not in cols:
            # L-0217 修复 V-R47-1：领取次数（误差重试上限的计数依据）
            conn.execute(
                "ALTER TABLE papers ADD COLUMN mine_attempts INTEGER NOT NULL DEFAULT 0")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_papers_theme_status "
            "ON papers(theme, mined_status)")
        # 迁移：主题类型（L-0223：disease=公共目录；topic=用户挖掘任意主题）
        tcols2 = {r["name"] for r in conn.execute("PRAGMA table_info(themes)").fetchall()}
        if "kind" not in tcols2:
            conn.execute("ALTER TABLE themes ADD COLUMN kind TEXT NOT NULL DEFAULT 'disease'")
        # 迁移：主题隐藏位（L-0250：软删/下架；可在 My mining 恢复）
        if "hidden" not in tcols2:
            conn.execute("ALTER TABLE themes ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0")
        # 迁移：任务状态文案汉译英（L-0253：UI 全英文；幂等）
        import re as _re_mod

        _zh_exact = {
            "排队中（等待调度器）": "queued — waiting for the scheduler",
            "排队中（用户恢复）": "queued — resumed by user",
            "排队中（用户重启）": "queued — restarted by user",
            "排队中（续轮）": "queued — next round",
            "巡检排队中": "queued for patrol",
            "已停止（用户）": "stopped by user",
            "已暂停（用户）": "paused by user",
            "挖掘中": "mining",
            "调度器重启，重新排队": "scheduler restarted — re-queued",
            "心跳陈旧超限：强制重排": "heartbeat stale beyond limit — force re-queue",
            "启动中": "starting",
        }
        _zh_patterns = (
            (r"^挖掘中（第 (\d+) 轮）$", r"mining — round \1"),
            (r"^巡检中（近 (\d+) 天窗口）$", r"patrolling (last \1 days)"),
            (r"^已挖尽（共读 (\d+) 篇 · 找到 (\d+) 条）；每 (\d+) 小时巡检$",
             r"exhausted — \1 papers read · \2 records found; patrols every \3 h"),
            (r"^巡检完成：近 (\d+) 天无新论文（累计读 (\d+) 篇 · 找到 (\d+) 条）；"
             r"下次按节奏（(\d+)h）$",
             r"patrol — no new papers in the last \1 days (papers read: \2 · "
             r"records found: \3); next check in \4h"),
            (r"^源站异常（连续 (\d+) 次补货失败），(\d+) 分钟后重试$",
             r"sources unreachable (\1 consecutive refill failures); retrying in \2 min"),
            (r"^第 (\d+) 轮完成（共读 (\d+) 篇 · 找到 (\d+) 条）$",
             r"round \1 done — \2 papers read · \3 records found"),
            (r"^出错（连续 (\d+) 次失败，退出码 (-?\d+)）$",
             r"error — \1 consecutive failures (exit code \2)"),
            (r"^重启过于频繁（每小时上限 (\d+)）$",
             r"restarting too often (hourly limit \1)"),
            (r"^重试（第 (\d+) 次失败，退出码 (-?\d+)）$",
             r"retrying after failure #\1 (exit code \2)"),
            (r"^心跳陈旧：(\d+) 分钟后复查（旧进程仍活则继续等待）$",
             r"heartbeat stale — re-check in \1 min (keeping pid while the old process lives)"),
        )
        _mcols = {r["name"] for r in conn.execute(
            "PRAGMA table_info(mining_requests)").fetchall()}
        _srow_list = (conn.execute(
            "SELECT id, status_text FROM mining_requests "
            "WHERE status_text <> ''").fetchall() if "status_text" in _mcols else [])
        for _srow in _srow_list:
            _st_txt = _srow["status_text"] or ""
            if not _re_mod.search(r"[\u4e00-\u9fff]", _st_txt):
                continue
            _en = _zh_exact.get(_st_txt)
            if _en is None:
                for _rx, _rep in _zh_patterns:
                    if _re_mod.match(_rx, _st_txt):
                        _en = _re_mod.sub(_rx, _rep, _st_txt)
                        break
            if _en and _en != _st_txt:
                conn.execute("UPDATE mining_requests SET status_text=? WHERE id=?",
                             (_en, _srow["id"]))
        # 迁移：存量 topic 主题描述更新（L-0231：不再写"不进疾病目录"）
        conn.execute(
            "UPDATE themes SET description = 'On-demand topic: omics datasets announced in "
            "papers about ' || name || ' (user-created theme; mined records publish to the "
            "catalog by default).' "
            "WHERE kind='topic' AND description LIKE '%not part of the public disease catalog%'")
        # 迁移：会话类型（L-0234：research=检索工作台；ming=挖掘对话）
        ccols = {r["name"] for r in conn.execute("PRAGMA table_info(chat_sessions)").fetchall()}
        if "kind" not in ccols:
            conn.execute(
                "ALTER TABLE chat_sessions ADD COLUMN kind TEXT NOT NULL DEFAULT 'research'")
        # 迁移：任务来源列（L-0217；公共端点据此过滤任务行）
        for table, col, decl in (
                ("miner_processes", "task_id", "TEXT NOT NULL DEFAULT ''"),
                ("paper_audits", "task_id", "TEXT NOT NULL DEFAULT ''"),
                ("query_stats", "task_id", "TEXT NOT NULL DEFAULT ''")):
            tcols = {r["name"] for r in conn.execute(
                f"PRAGMA table_info({table})").fetchall()}
            if col not in tcols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
        # 迁移：mining_requests → 长期任务模型（L-0209；旧行为 kind='quick'）
        mcols = {r["name"] for r in conn.execute(
            "PRAGMA table_info(mining_requests)").fetchall()}
        for col, decl in (
                ("title", "TEXT NOT NULL DEFAULT ''"),
                ("queries", "TEXT NOT NULL DEFAULT '[]'"),
                ("cadence_hours", "INTEGER NOT NULL DEFAULT 24"),
                ("papers_per_round", "INTEGER NOT NULL DEFAULT 200"),
                ("phase", "TEXT NOT NULL DEFAULT 'exhaust'"),
                ("rounds_done", "INTEGER NOT NULL DEFAULT 0"),
                ("failures", "INTEGER NOT NULL DEFAULT 0"),
                ("next_check_at", "TEXT NOT NULL DEFAULT ''"),
                ("last_heartbeat", "TEXT NOT NULL DEFAULT ''"),
                ("status_text", "TEXT NOT NULL DEFAULT ''"),
                ("kind", "TEXT NOT NULL DEFAULT 'quick'")):
            if col not in mcols:
                conn.execute(f"ALTER TABLE mining_requests ADD COLUMN {col} {decl}")
        # 迁移：把 papers.theme 回填进多主题归属表（仅当论文与主题疾病相关）
        from app.core.relevance import theme_relevant
        rows = conn.execute(
            "SELECT id, theme, title, abstract FROM papers WHERE theme != ''").fetchall()
        now = utcnow()
        conn.executemany(
            "INSERT OR IGNORE INTO paper_themes(paper_id, theme, source, created_at) "
            "VALUES (?, ?, 'backfill', ?)",
            [(r["id"], r["theme"], now) for r in rows
             if theme_relevant(r["theme"], r["title"], r["abstract"])])
    _load_settings_into_config()


# ---------------------------------------------------------------- settings

def all_settings() -> dict[str, str]:
    with db() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    return {r["key"]: r["value"] for r in rows}


def set_settings(values: dict[str, str]) -> dict[str, str]:
    now = utcnow()
    with db() as conn:
        for key, value in values.items():
            conn.execute(
                "INSERT INTO settings(key, value, updated_at) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (key, "" if value is None else str(value), now),
            )
    _load_settings_into_config()
    return all_settings()


def _load_settings_into_config() -> None:
    try:
        cfg.apply_overrides(all_settings())
    except Exception:
        cfg.reload()