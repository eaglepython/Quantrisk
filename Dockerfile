# QuantRisk batch + dashboard image
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MPLBACKEND=Agg \
    QR_CONFIG_DIR=/app/config

WORKDIR /app

# dependency layer first so code changes don't reinstall everything
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --upgrade pip && pip install ".[dashboard]" boto3

COPY config ./config
COPY dashboards ./dashboards
COPY sql ./sql

# non-root user
RUN useradd --create-home --uid 10001 quantrisk && mkdir -p /app/data /app/reports && chown -R quantrisk /app
USER quantrisk

ARG GIT_SHA=unknown
ENV GIT_SHA=${GIT_SHA}

# default: run today's batch; override the command for the dashboard
ENTRYPOINT ["quantrisk"]
CMD ["run", "--as-of", "today", "--with-sample"]
