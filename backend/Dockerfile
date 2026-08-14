# syntax=docker/dockerfile:1.7
FROM python:3.12-slim-bookworm AS builder
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /build
RUN apt-get update && apt-get install -y --no-install-recommends build-essential libpq-dev && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml ./
COPY app ./app
RUN pip install --user --no-cache-dir .

FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/home/app/.local/bin:$PATH"
RUN apt-get update && apt-get install -y --no-install-recommends libpq5 curl && rm -rf /var/lib/apt/lists/* \
    && groupadd -r app -g 1000 && useradd -r -u 1000 -g app -d /home/app -s /sbin/nologin app \
    && mkdir -p /home/app /app && chown -R app:app /home/app /app
COPY --from=builder --chown=app:app /root/.local /home/app/.local
WORKDIR /app
COPY --chown=app:app . .
# Round 8-16A — pre-create the inspection-photo directory owned by the
# non-root app user (uid/gid 1000). When a named volume is mounted here,
# Docker seeds the empty volume from this image path INCLUDING its
# ownership, so the volume comes up app:app and the non-root process can
# write to it with no chmod and without the container ever running as root.
# `var/` is dockerignored, so this stays an EMPTY directory in the image —
# stored photos are never baked into the image.
RUN mkdir -p /app/var/inspection-photos && chown -R app:app /app/var
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD curl -fsS http://localhost:8000/health || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
