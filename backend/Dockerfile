# base：API、runtime-worker、ingestion-worker 共用
FROM python:3.12-slim AS base
ENV PYTHONUNBUFFERED=1 PIP_DEFAULT_TIMEOUT=100 PIP_RETRIES=10
WORKDIR /app
# 先装 CPU 版 torch，避免 sentence-transformers 拉取数 GB 的 CUDA 依赖；pip 缓存卷跨构建复用（弱网友好）
RUN --mount=type=cache,target=/root/.cache/pip,id=pip-v2 \
    pip install torch --index-url https://download.pytorch.org/whl/cpu
COPY pyproject.toml ./
COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./
RUN --mount=type=cache,target=/root/.cache/pip,id=pip-v2 \
    pip install ".[ai]"
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

# modelops：training-worker / evaluation-worker（真实 LoRA 与评估 Provider）
FROM base AS modelops
RUN --mount=type=cache,target=/root/.cache/pip,id=pip-v2 \
    pip install ".[modelops]"
