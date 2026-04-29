#!/usr/bin/env bash
# provision-gcp.sh — one-shot GCP project provisioning for Atlas Cloud Run deploy
#
# Run AFTER `gcloud auth login` and `gcloud config set project <PROJECT_ID>`.
# Idempotent — safe to re-run; existing resources are left alone.
#
# Usage:
#   bash scripts/provision-gcp.sh [REGION]
#
#   REGION  Cloud Run region (default us-central1; matches cloudbuild.yaml)
#
# What it does:
#   1. Enables the 5 required APIs (cloudbuild, run, artifactregistry,
#      secretmanager, sqladmin).
#   2. Creates the Artifact Registry repo `atlas-images`.
#   3. Grants the Cloud Build service account the 4 roles needed to deploy
#      Cloud Run services and read Secret Manager.
#   4. Creates Secret Manager entries for every secret cloudbuild.yaml
#      references — populated from your local `.env` if present, otherwise
#      with placeholder values you must update via `gcloud secrets versions
#      add` before the first deploy.
#
# What it does NOT do:
#   - Create the Cloud Build trigger (use the GCP Console or
#     `gcloud builds triggers create github` interactively — it needs you to
#     authorize the GitHub connection once).
#   - Provision the data layer (Cloud SQL Postgres, Redis, Qdrant Cloud) —
#     see scripts/provision-data-layer.md for that walkthrough.
#   - Deploy anything. The first deploy fires when you push to main and
#     Cloud Build picks up cloudbuild.yaml.

set -euo pipefail

REGION="${1:-us-central1}"
AR_REPO="atlas-images"

# ── 0. Sanity checks ──────────────────────────────────────────────────
if ! command -v gcloud >/dev/null 2>&1; then
    echo "FATAL: gcloud CLI not found. Install: https://cloud.google.com/sdk/docs/install" >&2
    exit 1
fi

PROJECT_ID="$(gcloud config get-value project 2>/dev/null)"
if [[ -z "$PROJECT_ID" || "$PROJECT_ID" == "(unset)" ]]; then
    echo "FATAL: no active gcloud project. Run:" >&2
    echo "  gcloud config set project <YOUR_PROJECT_ID>" >&2
    exit 1
fi

ACCOUNT="$(gcloud config get-value account 2>/dev/null)"
if [[ -z "$ACCOUNT" || "$ACCOUNT" == "(unset)" ]]; then
    echo "FATAL: no authenticated gcloud account. Run:" >&2
    echo "  gcloud auth login" >&2
    exit 1
fi

echo "=== Provisioning GCP for Atlas ==="
echo "  Project: $PROJECT_ID"
echo "  Region:  $REGION"
echo "  Account: $ACCOUNT"
echo
read -p "Proceed? [y/N] " -n 1 -r REPLY
echo
[[ $REPLY =~ ^[Yy]$ ]] || { echo "Aborted."; exit 0; }

# ── 1. Enable required APIs ───────────────────────────────────────────
echo "[1/4] Enabling required APIs (idempotent)..."
gcloud services enable \
    cloudbuild.googleapis.com \
    run.googleapis.com \
    artifactregistry.googleapis.com \
    secretmanager.googleapis.com \
    sqladmin.googleapis.com \
    --project="$PROJECT_ID"

# ── 2. Artifact Registry repo ─────────────────────────────────────────
echo "[2/4] Ensuring Artifact Registry repo $AR_REPO exists..."
if gcloud artifacts repositories describe "$AR_REPO" --location="$REGION" --project="$PROJECT_ID" >/dev/null 2>&1; then
    echo "  [exists] $AR_REPO @ $REGION"
else
    gcloud artifacts repositories create "$AR_REPO" \
        --repository-format=docker \
        --location="$REGION" \
        --description="Atlas (PersonalAsst) container images" \
        --project="$PROJECT_ID"
    echo "  [created] $AR_REPO @ $REGION"
fi

# ── 3. IAM grants for Cloud Build SA ──────────────────────────────────
echo "[3/4] Granting Cloud Build SA the 4 deploy roles..."
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
CB_SA="${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com"

for ROLE in roles/run.admin roles/iam.serviceAccountUser roles/artifactregistry.writer roles/secretmanager.secretAccessor; do
    gcloud projects add-iam-policy-binding "$PROJECT_ID" \
        --member="serviceAccount:$CB_SA" \
        --role="$ROLE" \
        --condition=None \
        --quiet >/dev/null
    echo "  [granted] $ROLE → $CB_SA"
done

# Cloud Run runtime service account also needs to read secrets.
COMPUTE_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:$COMPUTE_SA" \
    --role="roles/secretmanager.secretAccessor" \
    --condition=None \
    --quiet >/dev/null
echo "  [granted] roles/secretmanager.secretAccessor → $COMPUTE_SA (Cloud Run runtime)"

# ── 4. Secret Manager entries ─────────────────────────────────────────
echo "[4/4] Provisioning Secret Manager entries..."

# Map of cloudbuild.yaml secret names → .env variable names
declare -A SECRETS=(
    [telegram-bot-token]=TELEGRAM_BOT_TOKEN
    [openai-api-key]=OPENAI_API_KEY
    [google-oauth-client-id]=GOOGLE_OAUTH_CLIENT_ID
    [google-oauth-client-secret]=GOOGLE_OAUTH_CLIENT_SECRET
    [workspace-mcp-signing-key]=WORKSPACE_MCP_SIGNING_KEY
    [mem0-api-key]=MEM0_API_KEY
    [database-url]=DATABASE_URL
    [redis-url]=REDIS_URL
    [qdrant-url]=QDRANT_URL
    [qdrant-api-key]=QDRANT_API_KEY
    [dashboard-api-key]=DASHBOARD_API_KEY
)

ENV_FILE=".env"
HAS_ENV=false
if [[ -f "$ENV_FILE" ]]; then
    HAS_ENV=true
    echo "  [info] Reading initial values from local .env (export these only via Secret Manager — never commit)"
fi

read_env_var() {
    local var_name="$1"
    if [[ "$HAS_ENV" == "true" ]]; then
        # Strip quotes, take everything after the first '='
        grep -E "^${var_name}=" "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- | sed -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'$/\1/"
    fi
}

for SECRET_NAME in "${!SECRETS[@]}"; do
    ENV_VAR="${SECRETS[$SECRET_NAME]}"

    if gcloud secrets describe "$SECRET_NAME" --project="$PROJECT_ID" >/dev/null 2>&1; then
        echo "  [exists] $SECRET_NAME (skipping; use 'gcloud secrets versions add' to rotate)"
        continue
    fi

    INITIAL_VALUE="$(read_env_var "$ENV_VAR")"
    if [[ -z "$INITIAL_VALUE" ]]; then
        INITIAL_VALUE="REPLACE_ME_${ENV_VAR}"
        echo "  [created-with-placeholder] $SECRET_NAME — fill via: echo -n 'real-value' | gcloud secrets versions add $SECRET_NAME --data-file=- --project=$PROJECT_ID"
    else
        echo "  [created-with-env-value] $SECRET_NAME ← \$$ENV_VAR from .env"
    fi

    printf "%s" "$INITIAL_VALUE" | gcloud secrets create "$SECRET_NAME" \
        --replication-policy=automatic \
        --data-file=- \
        --project="$PROJECT_ID" >/dev/null
done

echo
echo "=== Done ==="
echo
echo "Verify which secrets still need real values:"
echo "  for s in telegram-bot-token openai-api-key google-oauth-client-id google-oauth-client-secret workspace-mcp-signing-key mem0-api-key database-url redis-url qdrant-url qdrant-api-key dashboard-api-key; do"
echo "    val=\$(gcloud secrets versions access latest --secret=\$s --project=$PROJECT_ID 2>/dev/null)"
echo "    if [[ \$val == REPLACE_ME_* ]]; then echo \"  TODO: \$s\"; fi"
echo "  done"
echo
echo "Next steps:"
echo "  1. Provision data layer (Cloud SQL + Qdrant Cloud + Redis): see docs/RUNBOOK.md \"Cloud Run deploy\""
echo "  2. Replace any REPLACE_ME_* secret values"
echo "  3. Connect the Cloud Build GitHub trigger via Console"
echo "  4. Push to main → first deploy fires"
