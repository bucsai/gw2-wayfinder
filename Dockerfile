FROM python:3.14-slim AS base

COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    WAYFINDER_DATA_DIR=/data \
    PYDANTIC_AI_NO_BANNER=1

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY app app
COPY static static

RUN useradd --create-home wayfinder && mkdir /data && chown wayfinder /data
VOLUME /data
EXPOSE 8000

HEALTHCHECK CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*", "--no-access-log"]

# Local development: dev dependencies (OpenTelemetry for tracing) and app code writable for `docker compose watch`.
FROM base AS dev
RUN uv sync --frozen && chown -R wayfinder /app
USER wayfinder

# Last stage, so the default build target.
FROM base AS prod
USER wayfinder
