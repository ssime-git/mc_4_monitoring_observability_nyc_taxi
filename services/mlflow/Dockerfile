FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.9.5 /uv /bin/uv
WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" UV_HTTP_TIMEOUT=300
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --only-group mlflow --no-install-project
COPY services/mlflow services/mlflow
CMD ["mlflow", "server", "--backend-store-uri", "sqlite:////mlflow-data/mlflow.db", "--serve-artifacts", "--artifacts-destination", "s3://mlflow-artifacts", "--host", "0.0.0.0", "--port", "5000"]
