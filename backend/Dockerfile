# syntax=docker/dockerfile:1

# --- Stage 1: build the React/Vite SPA ---
FROM node:20-alpine AS frontend-build
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install
COPY frontend/ ./
# Single canonical copy (repo root) rather than a duplicate checked into
# frontend/ — Vite's public/ dir is copied verbatim into dist/, so this
# ends up served at /LICENSE by the catch-all static route in app/main.py.
COPY LICENSE ./public/LICENSE
RUN npm run build

# --- Stage 2: Python runtime, serves the built SPA + the API ---
FROM python:3.12-slim AS runtime
WORKDIR /app

# Set by .github/workflows/release.yml to the git tag (e.g. "v0.1.0") on a
# tagged build; "dev" for any locally-built image (docker compose build
# without this arg), which is what a running instance's /api/health and
# the update-check feature both key off of.
ARG APP_VERSION=dev
ENV APP_VERSION=${APP_VERSION}
LABEL org.opencontainers.image.version=${APP_VERSION} \
      org.opencontainers.image.source="https://github.com/BJD1997/YetAnotherDmarcTool"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

COPY backend/requirements.txt ./requirements.txt
RUN pip install -r requirements.txt

COPY backend/app ./app
COPY backend/alembic ./alembic
COPY backend/alembic.ini ./alembic.ini

# Built SPA static assets, served by FastAPI (see app/main.py)
COPY --from=frontend-build /frontend/dist ./static

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
