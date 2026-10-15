FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
# 国内提速：--build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple（pip 自动读取该环境变量）
ARG PIP_INDEX_URL
RUN pip install -r requirements.txt

# 代码在运行时通过 compose 卷映射进来（见 docker-compose.yml），这里先拷一份保证镜像自洽
COPY . .

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1

CMD ["python", "scripts/serve.py", "--host", "0.0.0.0", "--port", "8000"]
