# ---- build stage: resolve and install dependencies into a virtualenv ----
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install -r requirements.txt
# Trim what the runtime never uses: LiteLLM's proxy dashboard assets, bytecode caches
# and the packaging tools. Then fail the build if anything the app imports broke.
RUN rm -rf /opt/venv/lib/python3.12/site-packages/litellm/proxy/_experimental/out \
    && find /opt/venv -type d -name __pycache__ -prune -exec rm -rf {} + \
    && (pip uninstall -y pip setuptools wheel >/dev/null 2>&1; true) \
    && python -c "import litellm, fastapi, uvicorn; from litellm.caching.caching import Cache; Cache(type='local')" \
    && find /opt/venv -type d -name __pycache__ -prune -exec rm -rf {} +

# ---- runtime stage: slim image with only the venv and app code ----
FROM python:3.12-slim AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATABASE_URL=sqlite:////data/app.db \
    PORT=8000

RUN useradd --create-home --uid 10001 app \
    && mkdir -p /data && chown app:app /data

COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
COPY app ./app

USER app
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/health', timeout=4)" || exit 1

# PORT is honoured so the same image runs on Render / Railway / Fly.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers"]
