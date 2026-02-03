FROM python:3.9-slim
ENV UV_HTTP_TIMEOUT=300

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

RUN uv pip install --system --no-cache mlflow psycopg2-binary
EXPOSE 5000

# We use local filesystem for artifacts for simplicity in this example
# but in production you'd use S3/GCS/Azure Blob.
CMD ["mlflow", "server", \
     "--backend-store-uri", "sqlite:///mlflow.db", \
     "--default-artifact-root", "./mlruns", \
     "--host", "0.0.0.0", \
     "--port", "5000"]
