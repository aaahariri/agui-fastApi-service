# syntax=docker/dockerfile:1
# Railway: set Root Directory = agent (this Dockerfile + build context live here).
#
# ── Build stage: resolve dependencies from the frozen lockfile ────────────────
FROM python:3.12-slim AS builder

# uv from the official static image (no pip, no curl|bash)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# Copy only manifests first for layer caching
COPY pyproject.toml uv.lock ./

# Install deps into /app/.venv, frozen, prod-only, without building the project
# itself (we run from source via CWD, so the package need not be installed).
RUN uv sync --frozen --no-install-project --no-dev

# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM python:3.12-slim

WORKDIR /app

# Bring the resolved venv and put it first on PATH (so `uvicorn` resolves)
COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

# Copy application source. WORKDIR=/app becomes the CWD at runtime, which is what
# makes the app's bare imports (e.g. `from agent import agent`) resolve.
COPY . .

# Railway injects $PORT at runtime; bind to it directly (falls back to 8000 locally).
# keep-alive sits just above the 120s LLM call ceiling so connections aren't cut early.
CMD uvicorn main:app \
      --host 0.0.0.0 \
      --port ${PORT:-8000} \
      --timeout-keep-alive 130 \
      --workers 1

# Container-level healthcheck (Railway also has its own HTTP healthcheck setting → /health)
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://localhost:%s/health' % os.getenv('PORT','8000'))"
