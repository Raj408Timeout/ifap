#!/usr/bin/env bash
# Shared settings for the GCP scripts. Override any of them in the environment, e.g.
#   GCP_PROJECT=ifap-prod GCP_REGION=us-east1 ./infrastructure/gcp/deploy.sh
set -euo pipefail

GCP_PROJECT="${GCP_PROJECT:-ifap-prod}"
GCP_REGION="${GCP_REGION:-us-east1}"           # keep close to the Neon database region
GITHUB_REPO="${GITHUB_REPO:-Raj408Timeout/ifap}"

REGISTRY_REPO="ifap"
REGISTRY="${GCP_REGION}-docker.pkg.dev/${GCP_PROJECT}/${REGISTRY_REPO}"
API_SERVICE="ifap-api"
WEB_SERVICE="ifap-web"
RUNTIME_SA="ifap-runtime@${GCP_PROJECT}.iam.gserviceaccount.com"
WEB_SA="ifap-web@${GCP_PROJECT}.iam.gserviceaccount.com"  # UI: needs no permissions at all
DEPLOYER_SA="ifap-deployer@${GCP_PROJECT}.iam.gserviceaccount.com"
WIF_POOL="github"
WIF_PROVIDER="github-actions"

SECRET_DATABASE_URL="ifap-database-url"
SECRET_LLM_API_KEY="ifap-llm-api-key"
# Vercel URLs for project "ifap" in *your* Vercel scope ("ifap"): ifap-<hash>-ifap.vercel.app
# and ifap-git-<branch>-ifap.vercel.app. Anchored on the scope suffix - a bare "ifap*" pattern
# would also match other people's projects (https://ifap.vercel.app is not ours).
VERCEL_SCOPE="${VERCEL_SCOPE:-ifap}"
VERCEL_ORIGIN_REGEX="https://ifap-[a-z0-9-]+-${VERCEL_SCOPE}\.vercel\.app"
# The production domain (Vercel > Settings > Domains), allowed explicitly
VERCEL_PRODUCTION_ORIGIN="${VERCEL_PRODUCTION_ORIGIN:-https://ifap-${VERCEL_SCOPE}.vercel.app}"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CLOUD_ENV_FILE="${ROOT_DIR}/backend/.env.cloud"
