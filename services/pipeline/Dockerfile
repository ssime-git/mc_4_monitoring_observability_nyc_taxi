FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.9.5 /uv /bin/uv
WORKDIR /app
ENV UV_HTTP_TIMEOUT=300 PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH"
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --only-group pipeline --no-install-project
COPY config config
COPY services/pipeline services/pipeline
CMD ["python", "services/pipeline/flow.py"]
