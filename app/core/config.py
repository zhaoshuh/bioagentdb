"""BioAgentDB 服务配置。

优先级（L-0208：文件真源）：环境变量 / .env > `config/bioagentdb.toml`（或 example）
> DB settings（**遗留兜底**，仅填补缺省键）> 内置默认。

- 策略参数的文件真源 = `config/bioagentdb.example.toml`（键名 = 节名以 "_" 连接）；
  环境变量覆盖名 = 既有 `ENV_MAP` 或自动 `BIOAGENTDB_ + 大写键名`。
- 取值来源可在 `cfg.public_dict(with_sources=True)` 中查看（设置页只读展示用）。
- 启动校验：`cfg.validation_error` 非空即表示配置非法（serve/scheduler 应拒绝启动）。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from threading import RLock
from typing import Any

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[2]

ENV_MAP: dict[str, str] = {
    "data_dir": "BIOAGENTDB_DATA_DIR",
    "db_path": "BIOAGENTDB_DB_PATH",
    "deepseek_api_key": "DEEPSEEK_API_KEY",
    "deepseek_base_url": "DEEPSEEK_BASE_URL",
    "deepseek_model": "DEEPSEEK_MODEL",
    "deepseek_scoring_model": "DEEPSEEK_SCORING_MODEL",
    "http_proxy": "HTTP_PROXY",
    "https_proxy": "HTTPS_PROXY",
    "request_timeout": "BIOAGENTDB_REQUEST_TIMEOUT",
    "user_agent": "BIOAGENTDB_USER_AGENT",
    "ncbi_email": "NCBI_EMAIL",
    "ncbi_api_key": "NCBI_API_KEY",
    "port": "BIOAGENTDB_PORT",
    "host": "BIOAGENTDB_HOST",
    "harvest_max_papers_per_run": "HARVEST_MAX_PAPERS_PER_RUN",
    "harvest_concurrency": "HARVEST_CONCURRENCY",
    "verify_concurrency": "HARVEST_VERIFY_CONCURRENCY",
    "min_confidence": "BIOAGENTDB_MIN_CONFIDENCE",
    "api_token": "BIOAGENTDB_API_TOKEN",
    "web_search_provider": "BIOAGENTDB_WEB_SEARCH_PROVIDER",
    "go_api_key": "OPENCODE_GO_API_KEY",
    "go_base_url": "OPENCODE_GO_BASE_URL",
    "vision_model": "OPENCODE_GO_VISION_MODEL",
    "llm_cache": "BIOAGENTDB_LLM_CACHE",
    "openalex_api_key": "OPENALEX_API_KEY",
    "openalex_email": "OPENALEX_EMAIL",
    "core_api_key": "CORE_API_KEY",
    "refill_multisource": "BIOAGENTDB_REFILL_MULTISOURCE",
}

DEFAULTS: dict[str, str] = {
    "data_dir": str(BASE_DIR / "data"),
    "db_path": "",
    "deepseek_api_key": "",
    "deepseek_base_url": "https://api.deepseek.com",
    "deepseek_model": "deepseek-v4-pro",
    "deepseek_scoring_model": "deepseek-v4-flash",
    "http_proxy": "",
    "https_proxy": "",
    "request_timeout": "30",
    "user_agent": "BioAgentDB/0.1 (academic data catalog)",
    "ncbi_email": "",
    "ncbi_api_key": "",
    "port": "8000",
    "host": "127.0.0.1",
    "harvest_max_papers_per_run": "500",
    "harvest_concurrency": "6",
    "verify_concurrency": "8",
    "min_confidence": "0.6",
    "api_token": "",
    "web_search_provider": "auto",
    "go_api_key": "",
    "go_base_url": "https://api.opencode.ai/v1",
    "vision_model": "deepseek-v4-flash-vision-exp",
    "llm_cache": "0",
    "openalex_api_key": "",
    "openalex_email": "",
    "core_api_key": "",
    "refill_multisource": "1",
    # ---- 用户矿机（L-0206/07/08；文件真源 = config/bioagentdb.example.toml）----
    "mining_task_creation_enabled": "1",
    "mining_ming_max_rounds": "12",
    "mining_task_papers_per_round": "200",
    "mining_task_papers_per_round_min": "50",
    "mining_task_papers_per_round_max": "2000",
    "mining_task_cadence_hours": "24",
    "mining_task_cadence_hours_min": "1",
    "mining_task_cadence_hours_max": "168",
    "mining_task_max_tasks_per_session": "10",
    "mining_task_max_active_tasks": "3",
    "mining_task_cost_hint_per_paper_usd": "0.0",
    "mining_task_budget_test_cap": "100",
    "mining_task_budget_full_cap": "2000",
    "scheduler_enabled": "1",
    "scheduler_poll_seconds": "10",
    "scheduler_global_max_miners": "1",
    "scheduler_single_active_per_theme": "1",
    "scheduler_restart_max_per_hour": "6",
    "scheduler_backoff_seconds": "30",
    "scheduler_patrol_lookback_days": "3",
    "miner_workers_default": "3",
    "miner_workers_max": "6",
    "miner_pool_keep": "200",
    "miner_idle_sleep": "45",
    "miner_heartbeat_every": "20",
    "miner_exhaust_probe_s": "20",
    "miner_exhaust_streak": "2",
    "miner_claim_stale_minutes": "30",
    "miner_drain_timeout_s": "300",
    "miner_refill_error_streak": "3",
    "miner_source_retry_minutes": "15",
    "scheduler_patrol_batch": "100",
    "scheduler_patrol_max_lookback_days": "30",
    "scheduler_orphan_stale_minutes": "60",
    "scheduler_orphan_retry_minutes": "5",
    "scheduler_orphan_max_defers": "3",
    "scheduler_max_task_failures": "5",
    "miner_max_attempts": "3",
    "miner_noacc_review": "1",
    "mining_task_recatalog": "1",
    "mining_task_publish_records": "1",
    "seed_starter_themes": "0",
    "harvest_batch_size": "100",
    "sources_europepmc_search_url":
        "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
    "sources_europepmc_page_size": "100",
    "verify_weights": '{"t1_paper_grounding": 1.5, "t2_link_reachable": 1.0, "t2_source_metadata": 1.5}',
    "workbench_top_n": "10",
    "workbench_k_fulltext": "8",
    "workbench_source_want": "15",
    "workbench_time_budget_s": "25",
    "workbench_verify_max": "6",
    "workbench_max_clarify": "2",
    "workbench_pdf_session_cap": "20",
    "workbench_default_organism": "Homo sapiens",
    # ---- 联网查证（v1.6，L-0292；文件真源 = config/bioagentdb.example.toml）----
    "web_research_enabled": "1",
    "web_research_max_actions": "6",
    "web_research_timeout_s": "30",
    "web_research_cache_days": "7",
    "web_research_max_terms": "8",
    "supervisor_themes": '["alzheimers-disease", "als", "parkinsons-disease"]',
    "supervisor_max_restarts_per_hour": "6",
    "collect_workers": "3",
    "collect_limit": "100",
    "collect_rounds": "50",
    "collect_empty_rounds": "2",
}

SECRET_KEYS = {"deepseek_api_key", "ncbi_api_key", "api_token", "go_api_key",
               "openalex_api_key", "core_api_key"}

# 关键护栏键的范围校验（键 → (最小值, 最大值, 说明)；非法 → 启动拒绝）
GUARD_RANGES: dict[str, tuple[float, float]] = {
    "mining_ming_max_rounds": (1, 100),
    "mining_task_papers_per_round": (1, 100000),
    "mining_task_papers_per_round_min": (1, 100000),
    "mining_task_papers_per_round_max": (1, 100000),
    "mining_task_cadence_hours": (1, 24 * 30),
    "mining_task_cadence_hours_min": (1, 24 * 30),
    "mining_task_cadence_hours_max": (1, 24 * 30),
    "mining_task_max_tasks_per_session": (1, 1000),
    "mining_task_max_active_tasks": (1, 100),
    "scheduler_global_max_miners": (1, 64),
    "scheduler_poll_seconds": (1, 3600),
    "miner_workers_default": (1, 64),
    "miner_workers_max": (1, 64),
    "miner_pool_keep": (1, 100000),
}


class Config:
    def __init__(self) -> None:
        self._lock = RLock()
        self._values: dict[str, str] = dict(DEFAULTS)
        self._sources: dict[str, str] = {k: "default" for k in DEFAULTS}
        self._db_overrides: dict[str, str] = {}
        self.validation_error: str = ""
        self.unknown_keys: list[str] = []
        self.reload()

    # ------------------------------------------------------------- loading
    def reload(self) -> None:
        load_dotenv(BASE_DIR / ".env", override=False)   # 进程环境变量 > .env（标准口径）
        with self._lock:
            values = dict(DEFAULTS)
            sources = {k: "default" for k in DEFAULTS}
            file_error = ""

            # 1) 文件层：config/bioagentdb.toml（或 example）
            file_vals: dict[str, str] = {}
            try:
                from app.core.config_file import load_file_config
                file_vals = load_file_config()
            except Exception as exc:  # 语法错误等 → 记为校验失败（启动时拒绝）
                file_error = str(exc)
            for key, raw in file_vals.items():
                values[key] = raw
                sources[key] = "file"
            self.unknown_keys = sorted(k for k in file_vals if k not in DEFAULTS)

            # 2) 环境变量（显式 ENV_MAP + 自动 BIOAGENTDB_<KEY>）
            for key, env_name in ENV_MAP.items():
                raw = os.environ.get(env_name)
                if raw is not None and raw.strip() != "":
                    values[key] = raw.strip()
                    sources[key] = "env"
            for key in list(values):
                if key in ENV_MAP:
                    continue
                raw = os.environ.get("BIOAGENTDB_" + key.upper())
                if raw is not None and raw.strip() != "":
                    values[key] = raw.strip()
                    sources[key] = "env"

            # 3) DB settings —— 遗留兜底（L-0208：只填补未被文件/环境设置的键）
            for key, raw in self._db_overrides.items():
                if raw is None or str(raw).strip() == "":
                    continue
                if sources.get(key, "default") == "default":
                    values[key] = str(raw).strip()
                    sources[key] = "db-legacy"

            # 4) 派生与兼容
            if not values["https_proxy"]:
                values["https_proxy"] = values["http_proxy"]
            values["data_dir"] = values["data_dir"] or str(BASE_DIR / "data")
            values["db_path"] = values["db_path"] or str(
                Path(values["data_dir"]) / "bioagentdb.sqlite3")

            self._values = values
            self._sources = sources
            self.validation_error = file_error or self._validate(values)
            self._apply_proxy_env()

    def _validate(self, values: dict[str, str]) -> str:
        """护栏键范围与跨键一致性校验；返回错误文本（空 = 通过）。"""
        for key, (lo, hi) in GUARD_RANGES.items():
            try:
                v = float(values.get(key, ""))
            except (TypeError, ValueError):
                return f"{key} is not a number (got {values.get(key)!r})"
            if not (lo <= v <= hi):
                return f"{key}={v:g} out of range [{lo:g}, {hi:g}]"
        try:
            lo = self._f(values, "mining_task_papers_per_round_min")
            hi = self._f(values, "mining_task_papers_per_round_max")
            mid = self._f(values, "mining_task_papers_per_round")
            if not (lo <= mid <= hi):
                return (f"mining_task_papers_per_round={mid:g} not in "
                        f"[min {lo:g}, max {hi:g}]")
            clo = self._f(values, "mining_task_cadence_hours_min")
            chi = self._f(values, "mining_task_cadence_hours_max")
            cmid = self._f(values, "mining_task_cadence_hours")
            if not (clo <= cmid <= chi):
                return f"mining_task_cadence_hours={cmid:g} not in [min {clo:g}, max {chi:g}]"
            wd = self._f(values, "miner_workers_default")
            wm = self._f(values, "miner_workers_max")
            if wd > wm:
                return f"miner_workers_default={wd:g} > miner_workers_max={wm:g}"
        except (TypeError, ValueError):
            return "numeric guard keys could not be parsed"
        return ""

    @staticmethod
    def _f(values: dict[str, str], key: str) -> float:
        return float(values.get(key, "0") or 0)

    # ------------------------------------------------------------- DB legacy
    def apply_overrides(self, overrides: dict[str, str]) -> None:
        """DB settings → **遗留兜底**（L-0208）：仅填补缺省键，不覆盖文件/环境。"""
        self._db_overrides = dict(overrides or {})
        self.validation_error = ""
        self.reload()

    def _apply_proxy_env(self) -> None:
        """仅供进程内 httpx 客户端参考；不污染全局环境变量（我们自管代理逻辑）。"""
        pass

    # ------------------------------------------------------------- accessors
    def get(self, key: str, default: Any = None) -> str:
        with self._lock:
            return self._values.get(key, DEFAULTS.get(key, default))

    def sources(self) -> dict[str, str]:
        """取值来源（default / file / env / db-legacy），供设置页只读展示。"""
        with self._lock:
            return dict(self._sources)

    def int(self, key: str, default: int = 0) -> int:
        try:
            return int(float(self.get(key, default)))
        except (TypeError, ValueError):
            return default

    def float(self, key: str, default: float = 0.0) -> float:
        try:
            return float(self.get(key, default))
        except (TypeError, ValueError):
            return default

    def bool(self, key: str, default: bool = False) -> bool:
        return str(self.get(key, default)).strip().lower() in {"1", "true", "yes", "on", "y"}

    def jsonv(self, key: str, default: Any = None) -> Any:
        """JSON 型配置（列表/字典）。"""
        try:
            data = json.loads(self.get(key) or "")
            return data if data is not None else default
        except (TypeError, ValueError, json.JSONDecodeError):
            return default

    @property
    def data_dir(self) -> Path:
        p = Path(self.get("data_dir"))
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def db_path(self) -> Path:
        return Path(self.get("db_path"))

    @property
    def cache_dir(self) -> Path:
        p = self.data_dir / "cache"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def backup_dir(self) -> Path:
        p = self.data_dir / "backups"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def public_dict(self, with_sources: bool = False) -> Any:
        out: dict[str, Any] = {}
        for key in DEFAULTS:
            value = self.get(key, "")
            if key in SECRET_KEYS:
                out[key] = ""
                out[key + "_set"] = bool(value)
            else:
                out[key] = value
        if with_sources:
            return {"values": out, "sources": self.sources(),
                    "unknown_keys": list(self.unknown_keys)}
        return out


cfg = Config()
