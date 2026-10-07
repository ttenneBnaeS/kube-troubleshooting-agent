# Backend API. Build context: backend/ (see compose.yaml).

# Build stage: resolve the locked environment with uv. uv's download cache
# lives in a BuildKit cache mount, so it speeds rebuilds without being
# baked into a layer (it was ~290MB when it was).
FROM python:3.13-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# Runtime: the venv and the code, no uv.
FROM python:3.13-slim
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    LOG_FORMAT=json
WORKDIR /app
COPY --from=build /opt/venv /opt/venv
COPY . .

# Non-root. /app/data holds the checkpoint DB and is a volume in compose;
# creating it here gives the volume the right owner on first mount. The
# home directory is needed: Voyage's client downloads its tokenizer into
# ~/.cache/huggingface on first embed, at index time and on every query.
RUN useradd --uid 10001 --create-home app && mkdir -p /app/data && chown app /app/data
USER app

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2)"
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
