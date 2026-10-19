# 预览三栈（Docker）— 快速说明

三个隔离部署，用于"真实用户可用性"验证（L-0241）与论文对齐演示（L-0316）：

| 栈 | 端口 | 数据 | 服务 |
|---|---|---|---|
| empty | 8081 | `deploy/empty-data/`（全新，零播种） | app + scheduler |
| live | 8082 | `deploy/live-data/`（真库快照；可选） | app（默认无 scheduler） |
| release | 8083 | `deploy/release-data/`（v1.6 冻结快照 `_v16` 的副本） | app（只读演示） |

## 从干净 clone 起跑（G 线，2026-10-15）

```bash
cp .env.example .env                     # 可选；无 .env 也能起（浏览模式；聊天/挖掘需 DEEPSEEK_API_KEY）
python deploy/bootstrap.py               # 建数据目录；有 data/releases/_v16 时自动装载 release 档并校验 sha256
docker compose -f deploy/compose.preview.yml up -d    # 镜像不存在时自动 build
```

`bootstrap.py` 幂等、只读源库（`VACUUM INTO` 快照），可多次运行：

```bash
python deploy/bootstrap.py --tier release     # 只准备 release 档
python deploy/bootstrap.py --force            # 强制重装 release 快照（需先停 release 容器）
python deploy/bootstrap.py --check            # 对 8081/8082/8083 做 /api/health 探活
```

`release` 档默认取 `data/releases/_v16/bioagentdb.sqlite3`（v1.6 冻结库，sha256
`15C067D8…94958`）；若该文件不在（干净 clone），可用环境变量指向 Zenodo 下载件：
`BIOAGENTDB_RELEASE_SNAPSHOT=/path/to/bioagentdb.sqlite3 python deploy/bootstrap.py --tier release`。
没有快照时 release 档会以空库起（仍是可用的页面）。

## 网络（代理）

容器内无法访问主机 `127.0.0.1` 的代理；compose **默认直连**（`BIOAGENTDB_CONTAINER_PROXY` 留空）。如需让容器走主机上的代理：

```bash
# bash/zsh
# 直连（默认）：不设置该变量，或显式留空
export BIOAGENTDB_CONTAINER_PROXY=
# 或任意 shell：在 deploy/.env 写一行（.env 已被 gitignore；空值=直连）
# BIOAGENTDB_CONTAINER_PROXY=
# 自建代理则填地址：
# BIOAGENTDB_CONTAINER_PROXY=http://host.docker.internal:7890
```

（空值 = 直连；需要代理时填 `http://host.docker.internal:<端口>`。旧版曾默认 `http.docker.internal:3128`，该默认已取消——L-0381。）

## live 档：自备现库快照（可选）

`live` 档不做自动装载——把自己的库用 `VACUUM INTO` 拷进 `deploy/live-data/bioagentdb.sqlite3`
即可（跨平台、WAL 安全；把 `<SRC>` 换成自己的库路径）：

```bash
python - <<'PY'
import sqlite3
c = sqlite3.connect("<SRC>")                      # 源库（只读打开亦可）
c.execute("VACUUM INTO 'deploy/live-data/bioagentdb.sqlite3'")
c.close()
PY
```

（Windows PowerShell 等价写法：`python -c "import sqlite3;c=sqlite3.connect(r'<SRC>');c.execute(\"VACUUM INTO 'deploy/live-data/bioagentdb.sqlite3'\");c.close()"`）

## 构建 + 起栈（手动；可选）

`up -d` 在镜像缺失时会自动 build；需要重建镜像（例如改了 `requirements.txt`）时：

```bash
docker build -t bioagentdb:latest .       # 国内提速可加 --build-arg PIP_INDEX_URL=<镜像源>
docker compose -f deploy/compose.preview.yml up -d
```

## 检查 / 停止

```bash
python deploy/bootstrap.py --check                    # 探活三档（8081/8082/8083）
docker compose -f deploy/compose.preview.yml ps
docker compose -f deploy/compose.preview.yml logs -f empty
docker compose -f deploy/compose.preview.yml down     # 只停本栈（不影响根 compose）
```

## 改代码后

`../:/app` 是卷映射：**前端/页面（JS/CSS/HTML）即时生效**；但 **Python 后端在进程启动时加载**——
改完 `app/`、`scripts/` 里的后端代码后，需要重启容器：

```bash
docker compose -f deploy/compose.preview.yml restart
```

注意：镜像构建上下文已排除 `data/`（真库）、`deploy/`（快照）与 `.env`（密钥仅运行时注入，不进镜像层）。
