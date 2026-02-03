FROM python:3.9-slim

WORKDIR /app
ENV UV_HTTP_TIMEOUT=300
ENV PYTHONUNBUFFERED=1

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

COPY requirements.txt .
RUN uv pip install --system --no-cache -r requirements.txt

COPY . .

CMD ["python", "etl.py"]
