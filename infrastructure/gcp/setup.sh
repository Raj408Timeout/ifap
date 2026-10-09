#!/usr/bin/env bash
# One-time (idempotent) Google Cloud setup for IFAP. Safe to re-run.
#
#   ./infrastructure/gcp/setup.sh
#
# Creates: APIs, Artifact Registry repo, runtime + deployer service accounts (least
# privilege), Secret Manager secrets from backend/.env.cloud, and Workload Identity
# Federation so GitHub Actions can deploy without a stored key.
source "$(dirname "$0")/config.sh"

log() { printf '\n==> %s\n' "$*"; }
gcloud config set project "$GCP_PROJECT" >/dev/null
PROJECT_NUMBER="$(gcloud projects describe "$GCP_PROJECT" --format='value(projectNumber)')"

log "Enabling APIs"
gcloud services enable run.googleapis.com artifactregistry.googleapis.com \
  cloudbuild.googleapis.com secretmanager.googleapis.com iamcredentials.googleapis.com \
  sts.googleapis.com cloudtrace.googleapis.com logging.googleapis.com monitoring.googleapis.com

log "Artifact Registry repository '${REGISTRY_REPO}' in ${GCP_REGION}"
gcloud artifacts repositories describe "$REGISTRY_REPO" --location "$GCP_REGION" >/dev/null 2>&1 ||
  gcloud artifacts repositories create "$REGISTRY_REPO" --repository-format=docker \
    --location "$GCP_REGION" --description "IFAP container images"

ensure_sa() {  # name, display name
  gcloud iam service-accounts describe "$1@${GCP_PROJECT}.iam.gserviceaccount.com" >/dev/null 2>&1 ||
    gcloud iam service-accounts create "$1" --display-name "$2"
}
grant() {  # member, role
  gcloud projects add-iam-policy-binding "$GCP_PROJECT" --member "$1" --role "$2" \
    --condition=None --quiet >/dev/null
}

log "Runtime service account (what the running containers may do)"
ensure_sa ifap-runtime "IFAP Cloud Run runtime"
for role in roles/secretmanager.secretAccessor roles/cloudtrace.agent \
            roles/logging.logWriter roles/monitoring.metricWriter; do
  grant "serviceAccount:${RUNTIME_SA}" "$role"
done

log "Deployer service account (what GitHub Actions may do)"
ensure_sa ifap-deployer "IFAP GitHub Actions deployer"
for role in roles/run.admin roles/artifactregistry.writer; do
  grant "serviceAccount:${DEPLOYER_SA}" "$role"
done
gcloud iam service-accounts add-iam-policy-binding "$RUNTIME_SA" \
  --member "serviceAccount:${DEPLOYER_SA}" --role roles/iam.serviceAccountUser --quiet >/dev/null

log "Secrets from backend/.env.cloud (values are never printed)"
read_env() {  # value of KEY in .env.cloud, without surrounding quotes
  grep -E "^$1=" "$CLOUD_ENV_FILE" | head -1 | cut -d= -f2- | sed -E "s/^[\"']//; s/[\"']$//"
}
put_secret() {  # secret name, env key
  local value; value="$(read_env "$2")"
  if [[ -z "$value" ]]; then echo "   skip $1: $2 not set in backend/.env.cloud"; return; fi
  gcloud secrets describe "$1" >/dev/null 2>&1 ||
    gcloud secrets create "$1" --replication-policy=automatic >/dev/null
  printf '%s' "$value" | gcloud secrets versions add "$1" --data-file=- >/dev/null
  echo "   $1: new version stored"
}
[[ -f "$CLOUD_ENV_FILE" ]] || { echo "Missing $CLOUD_ENV_FILE"; exit 1; }
put_secret "$SECRET_DATABASE_URL" IFAP_DATABASE__URL
put_secret "$SECRET_LLM_API_KEY" IFAP_LLM__API_KEY

log "Workload Identity Federation for ${GITHUB_REPO}"
gcloud iam workload-identity-pools describe "$WIF_POOL" --location global >/dev/null 2>&1 ||
  gcloud iam workload-identity-pools create "$WIF_POOL" --location global \
    --display-name "GitHub Actions"
gcloud iam workload-identity-pools providers describe "$WIF_PROVIDER" --location global \
  --workload-identity-pool "$WIF_POOL" >/dev/null 2>&1 ||
  gcloud iam workload-identity-pools providers create-oidc "$WIF_PROVIDER" --location global \
    --workload-identity-pool "$WIF_POOL" --issuer-uri "https://token.actions.githubusercontent.com" \
    --attribute-mapping "google.subject=assertion.sub,attribute.repository=assertion.repository" \
    --attribute-condition "assertion.repository=='${GITHUB_REPO}'"
POOL_ID="projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${WIF_POOL}"
gcloud iam service-accounts add-iam-policy-binding "$DEPLOYER_SA" --role roles/iam.workloadIdentityUser \
  --member "principalSet://iam.googleapis.com/${POOL_ID}/attribute.repository/${GITHUB_REPO}" \
  --quiet >/dev/null

log "Done. Add these as GitHub repository *variables* (Settings > Secrets and variables > Actions > Variables):"
echo "   GCP_PROJECT=${GCP_PROJECT}"
echo "   GCP_REGION=${GCP_REGION}"
echo "   GCP_WIF_PROVIDER=${POOL_ID}/providers/${WIF_PROVIDER}"
echo "   GCP_DEPLOYER_SA=${DEPLOYER_SA}"
