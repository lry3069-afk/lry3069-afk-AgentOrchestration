FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_SYSTEM_PYTHON=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

COPY pyproject.toml ./
RUN uv sync --frozen --no-dev

FROM builder AS prod

COPY . .
RUN uv sync --frozen

EXPOSE 8000

CMD ["uvicorn", "src.api.server:create_app", "--host", "0.0.0.0", "--port", "8000"]