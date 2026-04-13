# AIWorks backend: control plane, domain agents, scraper MCP (Chromium via Playwright).
# Build from repository root: docker build -t aiworks-backend .
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.6.9 /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY app ./app
COPY agents ./agents
COPY tools ./tools
COPY scripts ./scripts
COPY db ./db

RUN uv sync --frozen --no-dev \
    && .venv/bin/playwright install-deps chromium \
    && .venv/bin/playwright install chromium

EXPOSE 8000 8001 8002 8003 8004 8005

# Overridden by docker-compose per service.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
