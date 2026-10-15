#!/usr/bin/env bash
# BioAgentDB 一键启动（Docker）。用法:
#   ./start.sh                # 只起服务 + UI
#   ./start.sh --with-mining  # 同时起挖掘：内部预热矿机 + 用户任务调度器（mining profile）
set -euo pipefail
cd "$(dirname "$0")"

WITH_MINING=0
for a in "$@"; do
  case "$a" in
    --with-mining|-m) WITH_MINING=1 ;;
  esac
done

if [ ! -f .env ]; then
  cp .env.example .env
  echo "已从 .env.example 生成 .env —— 请填入 DEEPSEEK_API_KEY 后重新运行。"
fi
mkdir -p data

PROFILE=()
[ "$WITH_MINING" = "1" ] && PROFILE=(--profile mining)

docker compose "${PROFILE[@]}" up -d --build

echo
echo "BioAgentDB:  http://localhost:8000"
echo "logs:        docker compose logs -f app"
[ "$WITH_MINING" = "1" ] && echo "miner logs:  docker compose logs -f miner"
[ "$WITH_MINING" = "1" ] && echo "scheduler:   docker compose logs -f scheduler   (user mining tasks)"
echo "stop:        docker compose down"
