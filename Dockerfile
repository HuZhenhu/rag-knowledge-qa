# ======================================================================
# T2.7 多阶段生产镜像：builder 安装依赖 → runtime 最小运行环境
# 用法：
#   docker build -t rag-knowledge-qa:latest .
#   docker run -p 8080:8080 -e UVICORN_WORKERS=4 rag-knowledge-qa:latest
# ======================================================================

# ---- Stage 1: builder（构建依赖层，可缓存）----
FROM python:3.12-slim AS builder
WORKDIR /app
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN pip install uv
COPY requirements.txt .
RUN uv pip install --system -r requirements.txt

# ---- Stage 2: runtime（最小运行环境）----
FROM python:3.12-slim
LABEL org.opencontainers.image.title="rag-knowledge-qa" \
      org.opencontainers.image.description="RAG 智能问答 API（T2.7 生产镜像）"

WORKDIR /app

# 非 root 运行：创建 app 用户并授权数据目录
RUN groupadd --system app && useradd --system --gid app --home-dir /app app \
    && mkdir -p /app/data && chown -R app:app /app

# 仅复制 site-packages 与可执行文件，避免构建链残留
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

COPY src/ src/
COPY main.py .
COPY build_index.py .

# 生产可写数据目录（缓存/队列/索引等落地于此）
VOLUME ["/app/data"]
ENV DATA_DIR=/app/data \
    FILE_QUEUE_DIR=/app/data/queue \
    DEFAULT_KV_DB=/app/data/rag_cache.db

# 默认端口（与 src/config API_PORT 对齐）
EXPOSE 8080

# K8s 探针 / 运维健康检查均走 /health/live
HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health/live', timeout=2)" || exit 1

USER app

# 启动参数环境变量化，便于 k8s Deployment / docker run 覆盖
CMD ["sh", "-c", "uvicorn main:app --host ${API_HOST:-0.0.0.0} --port ${API_PORT:-8080} --workers ${UVICORN_WORKERS:-4}"]
