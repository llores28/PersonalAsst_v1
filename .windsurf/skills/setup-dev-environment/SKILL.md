---
name: setup-dev-environment
description: Set up the local development environment for PersonalAsst from scratch
---

# Setup Dev Environment

## Prerequisites
- Docker Desktop installed and running
- Python 3.12+ installed
- Git

## Steps

1. Clone the repository
2. Copy `.env.example` to `.env` and fill in required values:
   - `OPENAI_API_KEY` — from OpenAI dashboard
   - `TELEGRAM_BOT_TOKEN` — from @BotFather on Telegram
   - `OWNER_TELEGRAM_ID` — your numeric Telegram ID (use @userinfobot)
   - `DB_PASSWORD` — any random string
3. Build and start all services:
   ```bash
   docker compose build
   docker compose up -d
   ```
4. Verify all containers are healthy:
   ```bash
   docker compose ps
   ```
5. Apply database migrations:
   ```bash
   docker compose exec assistant alembic upgrade head
   ```
6. Send `/start` to your Telegram bot — should respond with setup wizard.

## Verification
- `docker compose ps` shows all containers as `Up (healthy)`
- Bot responds to `/start` in Telegram
- `/help` returns command list
