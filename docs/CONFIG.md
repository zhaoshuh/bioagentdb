# CONFIG — BioAgentDB 配置说明（文件真源；台账 L-0208）

## 一、三层配置与优先级

| 层 | 文件 | 装什么 |
|---|---|---|
| 策略参数（唯一真源） | `config/bioagentdb.toml`（复制自 `config/bioagentdb.example.toml`；`.gitignore`） | 任务限界、调度器、矿机、检索源、核验、工作台、collect、护栏 |
| 机密与部署 | `.env`（复制自 `.env.example`） | LLM 钥匙/BASE/模型、NCBI、代理、host/port、数据目录 |
| 任务级取值 | DB（`mining_requests`） | 用户建任务时选的预算/节奏/过滤（其**边界**来自 toml） |

**优先级**：进程环境变量 > `.env`（不再覆盖已存在的进程环境变量）> `config/bioagentdb.toml` > DB settings（遗留兜底，仅填补缺省键）> 代码默认。
**生效方式**：修改后**重启**服务 / 调度器。
**机密**：一律只进 `.env`，绝不写入 toml。
**校验**：启动时做类型/范围校验；`config/bioagentdb.toml` 语法错误或护栏键越界会**拒绝启动**并给出键名与允许范围。
**来源查看**：`GET /api/settings`（只读）返回 `{"values": {...}, "sources": {"<key>": "file|env|default|db-legacy"}}`；`PUT` 返回 403（配置一律走文件）。

## 二、键名规则

- 键名 = 各级节名以 `_` 连接：`[mining.task] papers_per_round` → `mining_task_papers_per_round`。
- 环境变量覆盖名 = `BIOAGENTDB_` + 大写键名，例：`BIOAGENTDB_MINER_POOL_KEEP=100`；
  既有键保留原有 ENV_MAP 名（如 `DEEPSEEK_API_KEY`、`HARVEST_CONCURRENCY`）。
- JSON 型键（列表/表）：`supervisor_themes`（数组）、`verify_weights`（字典）。
- 字典值（inline table）需登记在 `app/core/config_file.py` 的 `KEEP_AS_JSON`（当前仅 `verify_weights`），整表以 JSON 串保存；未登记的字典会按节递归展平（`a_b_key`）。
- 用户 toml 是**覆盖层**：只写要改的键即可；未写的键回退到代码默认（与 example 相同）。

## 三、参数清单（默认值 = example 文件）

### mining / mining.task — 用户矿机
| 键 | 默认 | 说明 |
|---|---|---|
| mining_task_creation_enabled | 1 | 是否允许创建任务（0=关闭入口） |
| mining_task_papers_per_round | 200 | 每轮论文数（任务默认） |
| mining_task_papers_per_round_min / _max | 50 / 2000 | 用户可设范围（新建任务时钳制） |
| mining_task_cadence_hours | 24 | 巡检间隔（小时；先穷尽后巡检。巡检轮只扫 EPMC 增量；跨停机超出巡窗上限（`scheduler_patrol_max_lookback_days`）的空档需重建任务或删除其 `next_check_at` 触发全量） |
| mining_task_cadence_hours_min / _max | 1 / 168 | 巡检间隔范围 |
| mining_task_max_tasks_per_session | 10 | 同会话任务上限 |
| mining_task_max_active_tasks | 3 | 全站"排队+运行"上限（系统总闸） |
| mining_task_recatalog | 1 | 目录可复挖任务挖过的论文（1=池分离开启；0=任务消耗池） |
| mining_task_publish_records | 1 | 任务记录发布进公共目录（1=默认公开；0=旧私有模式，L-0225） |
| mining_ming_max_rounds | 12 | Ming 对话防跑飞安全阀（轮；正常由 agent 自行收敛，L-0235） |
| —— API |  | `POST /api/mining/analyze` = 意图分析（只读，不建任何东西；L-0223）；`/api/chat/sessions` 带 `kind=ming` = Ming 挖掘对话（L-0234） |
| mining_task_cost_hint_per_paper_usd | 0.0 | 费用预估系数（0=只显示篇数） |
| mining_task_budget_test_cap / _full_cap | 100 / 2000 | 旧版"一句话下单"预算闸（兼容） |

> 主题类型字段：`theme_kind='topic'` = 任意研究主题（L-0223；记录默认发布进公共目录，L-0225）。
> 端点口径（L-0231，supersedes L-0219-C2）：发布模式（默认）下 `/api/audits`、`/api/query-stats` **含任务行**；私有模式（publish=0）隐藏任务行。

### scheduler — 常驻调度器
| 键 | 默认 | 说明 |
|---|---|---|
| scheduler_enabled | 1 | 调度器总开关 |
| scheduler_poll_seconds | 10 | 轮询间隔 |
| scheduler_global_max_miners | 1 | 同时运行的矿机数（系统总闸） |
| scheduler_single_active_per_theme | 1 | 同主题单活跃矿线 |
| scheduler_restart_max_per_hour | 6 | 崩溃重启上限 |
| scheduler_backoff_seconds | 30 | 重启退避 |
| scheduler_max_task_failures | 5 | 单任务连续失败上限 → error |
| scheduler_patrol_batch | 100 | 巡检轮论文上限（min(每轮上限, 本值)） |
| scheduler_patrol_lookback_days | 3 | 巡检增量窗口（只扫近 N 天，L-0213；巡检轮只扫 EPMC 增量） |
| scheduler_patrol_max_lookback_days | 30 | 巡检窗上限（停机更久也不超过 N 天，L-0215） |
| scheduler_orphan_stale_minutes | 60 | 孤儿矿机"pid 活但心跳陈旧"判定窗（分钟；兜底 pid 复用，L-0215） |
| scheduler_orphan_retry_minutes | 5 | 心跳陈旧后的复查退避（分钟；保 pid 防同 tick 换矿，L-0217） |
| scheduler_orphan_max_defers | 3 | 推迟上限（超过→强制清 pid 重排；防 pid 复用永久占闸，L-0221） |

### miner — 矿机进程
| 键 | 默认 | 说明 |
|---|---|---|
| miner_workers_default / _max | 3 / 6 | 单矿机线程数 |
| miner_pool_keep | 200 | 论文池补给阈值 |
| miner_idle_sleep | 45 | 池空休眠（秒） |
| miner_heartbeat_every | 20 | 心跳间隔（秒） |
| miner_exhaust_probe_s | 20 | 任务模式：穷尽探测间隔（秒） |
| miner_exhaust_streak | 2 | 连续"空补货"次数即判挖尽 |
| miner_claim_stale_minutes | 30 | 陈旧 `claimed` 回收时限（分钟） |
| miner_drain_timeout_s | 300 | 退出前排空 in-flight 论文的最长等待（秒） |
| miner_refill_error_streak | 3 | 连续补货**异常**次数 → 判"源站异常"，退避重试（不假报挖尽） |
| miner_source_retry_minutes | 15 | 源站异常后的重试间隔（分钟） |
| miner_max_attempts | 3 | 单篇论文领取-失败重试上限（达到后落 `error` 终态；L-0217） |
| miner_noacc_review | 1 | no_accessions 论文二次 LLM 复核（只采信原文逐字；**有链接即入档**（含门户链接），无链接仅登记；发现沉淀 `extract_misses`，操作说明 `docs/NOACC-REVIEW.md`；L-0246/L-0248） |

### harvest / sources / verify
| 键 | 默认 | 说明 |
|---|---|---|
| harvest_batch_size | 100 | 服务内 runner 批大小 |
| seed_starter_themes | 0 | 顶层键：起始主题播种（0=空部署纯空白；1=写入疾病起始主题，L-0225） |
| sources_europepmc_search_url | EPMC REST | 检索端点 |
| sources_europepmc_page_size | 100 | 分页大小 |
| verify_weights | {t1_paper_grounding:1.5, t2_link_reachable:1.0, t2_source_metadata:1.5} | 核验加权（键 = 检查项名；`app/harvest/verify.py` 实际消费） |

### workbench — 对话工作台
`workbench_top_n=10` · `workbench_k_fulltext=8` · `workbench_source_want=15` ·
`workbench_time_budget_s=25` · `workbench_verify_max=6` · `workbench_max_clarify=2` ·
`workbench_pdf_session_cap=20` · `workbench_default_organism="Homo sapiens"`

### web_research — 联网查证（v1.6，L-0292/L-0295）
`web_research_enabled=1`（总开关）· `web_research_max_actions=6`（单次动作上限：搜索+读页）·
`web_research_timeout_s=30`（联网动作墙钟：动作启动门；单动作最坏 ≈2×单请求上限——直连+代理各一次，
`retries=0`；LLM 蒸馏另有 ≤60s 上限）· `web_research_cache_days=7`（简报缓存 TTL；**0=不缓存**）·
`web_research_max_terms=8`（并入检索词上限）；顶层 `web_search_provider="auto"|"off"`（off/false/0 等拼写均视为关闭）。
触发四路：画像 LLM 信号 / 赛事模式兜底 / 用户明示（"查一下/look up"：强制，不被同画像缓存短路）/
首轮全空重跑一次（有界）。简报只存摘要+术语+来源链接（缓存 `data/web_cache/`）；关闭或失败时与既有行为完全一致。

### supervisor / collect
| 键 | 默认 | 说明 |
|---|---|---|
| supervisor_themes | ["alzheimers-disease","als","parkinsons-disease"] | 内部预热矿机主题 |
| supervisor_max_restarts_per_hour | 6 | 保活器重启上限 |
| collect_workers / collect_limit / collect_rounds / collect_empty_rounds | 3 / 100 / 50 / 2 | 用户 CLI `collect.py` 默认 |

## 四、与代码的关系

- 所有**产品/运维可调**参数都在 example 文件中可寻（台账 L-0208 的"散落常量收敛清单"已收编：`supervisor` 主题表与重启上限、`miner` 阈值、`sweep` 分页、`verify` 权重（按检查项名真正消费）、`workbench` 八项、`collect` 默认、`runner` 批大小、旧下单预算闸）。
- 代码只保留纯实现常量（如阶段进度系数）；新增可调参数必须先进 `config/bioagentdb.example.toml` 与本文档。
- **未知键**：加载时收集到 `cfg.unknown_keys`，`serve.py` / `scheduler.py` 启动打印 WARNING，并经 `GET /api/settings` 的 `unknown_keys` 字段可见（不静默生效）。
- **运维键已接线**：`miner_workers_max`（矿机线程上限钳制）、`scheduler_restart_max_per_hour`（失败重启限速）、`scheduler_backoff_seconds`（失败重试退避，写入 `next_check_at`）。
- **常驻调度器**：`python scripts/scheduler.py`；Docker 下随 `docker compose --profile mining up -d`（服务名 `scheduler`）。
- **会话标注而非隔离**：任务列表对匿名会话标注 "yours"，但**不做强制隔离**（无账号体系，见 L-0207 决策 c）。

## 五、部署键（BIOAGENTDB_*）—— 干净部署与三档预览（G 线，2026-10-15）

统一口径：**部署/路径/调度全部走环境变量**（`BIOAGENTDB_` + 大写键名；`.env` 由 start 脚本/compose
读取，也可直接 `export`）。默认值 = 原行为：serve 默认 `127.0.0.1:8000`、数据 `<repo>/data`、
库 `<data>/bioagentdb.sqlite3`；**论文对齐发布快照固定为 `_v16`**（`data/releases/_v16/`，
sha256 `15C067D8…94958`，release 档由 `deploy/bootstrap.py` 装载进 `deploy/release-data/`）。

| 用途 | 键（env） | 默认 | 说明 |
|---|---|---|---|
| 监听 | `BIOAGENTDB_HOST` / `BIOAGENTDB_PORT` | 127.0.0.1 / 8000 | Docker compose 显式设 0.0.0.0:8000；本机默认只绑本机 |
| 数据 | `BIOAGENTDB_DATA_DIR` | `<repo>/data` | 所有运行期产物（库/缓存/日志/备份）随它走 |
| 库 | `BIOAGENTDB_DB_PATH` | `<data>/bioagentdb.sqlite3` | 指向现成的库即可直接服务 |
| 演示只读 | `BIOAGENTDB_PUBLIC_DEMO` | 0 | 1=写操作全 403（公网演示） |
| 起始主题 | `BIOAGENTDB_SEED_STARTER_THEMES` | 0 | 0=空部署零播种（默认）；1=写入疾病起始主题 |
| 发布模式 | `BIOAGENTDB_MINING_TASK_PUBLISH_RECORDS` | 1 | 任务记录发布进公共目录；0=旧私有模式 |
| 调度 | `BIOAGENTDB_SCHEDULER_*` / `BIOAGENTDB_MINER_*` | 见 §三 | 例如 `BIOAGENTDB_MINER_POOL_KEEP=100`、`BIOAGENTDB_SCHEDULER_POLL_SECONDS=10` |
| 代理 | `HTTP_PROXY` / `HTTPS_PROXY` | 空（直连优先） | 仅用于外网请求；容器内可用 `BIOAGENTDB_CONTAINER_PROXY`（compose 档）覆盖 |
| 预览端口 | `BIOAGENTDB_PREVIEW_PORTS` | 8081,8082,8083 | 仅 `deploy/bootstrap.py --check` 探活用 |

三档预览（`deploy/compose.preview.yml`）：empty=8081 空库（带 scheduler）· live=8082 自备快照 ·
release=8083 v1.6 冻结对齐。一步准备 + 一键起：`python deploy/bootstrap.py` →
`docker compose -f deploy/compose.preview.yml up -d`（详见 `deploy/README.md`、`docs/QUICKSTART.md`、
第三方十步 `docs/SELF-HOST.md`）。
