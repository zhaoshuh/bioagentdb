"""config/bioagentdb.toml 读取器 —— 策略参数的**文件真源**（台账 L-0208）。

- 文件选择：`config/bioagentdb.toml`（用户副本，gitignored）优先；缺失则回退
  `config/bioagentdb.example.toml`（随仓库）。
- 零依赖：Python 3.11+ 标准库 `tomllib`。
- 输出：**扁平化** dict —— 键名 = 各级节名以 "_" 连接
  （`[mining.task] papers_per_round` → `mining_task_papers_per_round`）；
  标量转 str（bool → "1"/"0"；数组/表 → JSON 串）。
- 本文件不含任何机密；机密一律走 `.env`。
"""
from __future__ import annotations

import json
import tomllib
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
CONFIG_DIR = BASE_DIR / "config"
USER_FILE = CONFIG_DIR / "bioagentdb.toml"
EXAMPLE_FILE = CONFIG_DIR / "bioagentdb.example.toml"


def active_path() -> Path | None:
    for p in (USER_FILE, EXAMPLE_FILE):
        if p.exists():
            return p
    return None


def _stringify(v) -> str:
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        return v
    return json.dumps(v, ensure_ascii=False)


# 字典值（inline table）：默认按"节"递归展平；**以下键名整表保留为 JSON 值**（登记制）
# 例：`[verify] weights = {...}` → `verify_weights`（JSON 串），供 cfg.jsonv() 读取。
KEEP_AS_JSON = {"verify_weights"}


def _flatten(prefix: str, node, out: dict[str, str]) -> None:
    if isinstance(node, dict):
        if prefix in KEEP_AS_JSON:
            out[prefix] = _stringify(node)
            return
        for k, v in node.items():
            _flatten(f"{prefix}_{k}" if prefix else str(k), v, out)
    else:
        if prefix:
            out[prefix] = _stringify(node)


def load_file_config() -> dict[str, str]:
    """读取并扁平化配置文件；语法错误 → RuntimeError（带文件与原因，fail fast）。"""
    path = active_path()
    if path is None:
        return {}
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:  # 语法错误：大声失败并指明文件
        raise RuntimeError(f"config file {path} is invalid TOML: {exc}") from exc
    out: dict[str, str] = {}
    _flatten("", data, out)
    return out
