FROM python:3.12-slim

WORKDIR /app

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential git curl postgresql-client && \
    rm -rf /var/lib/apt/lists/*

# Python dependencies
COPY requirements.txt .
COPY requirements-dev.txt .
RUN pip install --no-cache-dir -r requirements-dev.txt

# Install Playwright browsers in shared path (accessible to non-root user)
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright
RUN playwright install --with-deps chromium && \
    chmod -R o+rx /opt/playwright

# Application code (plugins are inside src/tools/plugins/)
COPY src/ ./src/
COPY tests/ ./tests/
COPY config/ ./config/
COPY alembic/ ./alembic/
COPY alembic.ini ./
COPY pytest.ini ./

# Non-root user
RUN useradd -m -r assistant && chown -R assistant:assistant /app
USER assistant

CMD ["python", "-m", "src.main"]
