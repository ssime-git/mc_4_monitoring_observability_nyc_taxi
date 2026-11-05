FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.9.5 /uv /bin/uv
WORKDIR /app
ENV UV_HTTP_TIMEOUT=300 PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH"
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --only-group frontend --no-install-project
COPY services/frontend services/frontend
CMD ["streamlit", "run", "services/frontend/app.py", "--server.port=8501", "--server.address=0.0.0.0"]
