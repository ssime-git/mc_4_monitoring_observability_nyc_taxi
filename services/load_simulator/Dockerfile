FROM python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159
COPY --from=ghcr.io/astral-sh/uv:0.9.5 /uv /bin/uv
WORKDIR /app
ENV UV_HTTP_TIMEOUT=300 PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH"
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --only-group simulator --no-install-project
COPY services/load_simulator services/load_simulator
CMD ["uvicorn", "simulator:app", "--app-dir", "services/load_simulator", "--host", "0.0.0.0", "--port", "8010"]
