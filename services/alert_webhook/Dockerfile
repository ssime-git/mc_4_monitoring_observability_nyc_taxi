FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.9.5 /uv /bin/uv
WORKDIR /app
ENV UV_HTTP_TIMEOUT=300 PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH"
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --only-group simulator --no-install-project
COPY services/alert_webhook services/alert_webhook
CMD ["uvicorn", "app:app", "--app-dir", "services/alert_webhook", "--host", "0.0.0.0", "--port", "8020"]
