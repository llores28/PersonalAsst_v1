FROM python:3.12-slim

WORKDIR /app

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential git curl postgresql-client && \
    rm -rf /var/lib/apt/lists/*

# Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code
COPY src/ ./src/
COPY config/ ./config/
COPY alembic.ini ./
COPY tools/_example/ ./tools/_example/

# Non-root user
RUN useradd -m -r assistant && chown -R assistant:assistant /app
USER assistant

CMD ["python", "-m", "src.main"]
