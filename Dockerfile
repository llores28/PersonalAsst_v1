# ── Stage 1: production ───────────────────────────────────────────────
FROM python:3.12-slim AS prod

WORKDIR /app

# System dependencies (prod-only: no build-essential)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl postgresql-client \
    ffmpeg \
    imagemagick \
    git && \
    rm -rf /var/lib/apt/lists/*

# Production Python dependencies only
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browsers in shared path (accessible to non-root user)
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright
RUN playwright install --with-deps chromium && \
    chmod -R o+rx /opt/playwright

# Application code (config, alembic, user_skills all inside src/)
COPY src/ ./src/

# Non-root user
RUN useradd -m -r assistant && chown -R assistant:assistant /app
USER assistant

CMD ["python", "-m", "src.main"]


# ── Stage 2: development/CI (adds dev tools + tests on top of prod) ───
FROM prod AS dev

USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential git && \
    rm -rf /var/lib/apt/lists/*

COPY requirements-dev.txt .
RUN pip install --no-cache-dir -r requirements-dev.txt

COPY tests/ ./tests/
COPY pytest.ini ./

RUN chown -R assistant:assistant /app
USER assistant
