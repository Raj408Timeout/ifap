#!/usr/bin/env bash
# Build and deploy IFAP to Cloud Run: API first, then the UI (which needs the API URL).
#
#   ./infrastructure/gcp/deploy.sh               # images built on Cloud Build (default)
#   BUILDER=docker ./infrastructure/gcp/deploy.sh   # images built locally / in GitHub Actions
source "$(dirname "$0")/config.sh"

BUILDER="${BUILDER:-cloudbuild}"
TAG="${TAG:-$(git -C "$ROOT_DIR" rev-parse --short HEAD)-$(date +%Y%m%d%H%M%S)}"
API_IMAGE="${REGISTRY}/api:${TAG}"
WEB_IMAGE="${REGISTRY}/web:${TAG}"
log() { printf '\n==> %s\n' "$*"; }

gcloud config set project "$GCP_PROJECT" >/dev/null
PROJECT_NUMBER="$(gcloud projects describe "$GCP_PROJECT" --format='value(projectNumber)')"
# Cloud Run's deterministic service URLs - known before the first deploy
API_URL="https://${API_SERVICE}-${PROJECT_NUMBER}.${GCP_REGION}.run.app"
WEB_URL="https://${WEB_SERVICE}-${PROJECT_NUMBER}.${GCP_REGION}.run.app"

build() {  # dockerfile, image, [api url build arg]
  if [[ "$BUILDER" == "docker" ]]; then
    docker build --file "$ROOT_DIR/$1" --build-arg "NEXT_PUBLIC_IFAP_API_URL=${3:-}" \
      --tag "$2" "$ROOT_DIR"
    docker push "$2"
  else
    gcloud builds submit "$ROOT_DIR" --region "$GCP_REGION" \
      --config "$ROOT_DIR/infrastructure/gcp/cloudbuild.yaml" \
      --substitutions "_DOCKERFILE=$1,_IMAGE=$2,_API_URL=${3:-}"
  fi
}

ENV_FILE="$(mktemp)"
trap 'rm -f "$ENV_FILE"' EXIT
cat > "$ENV_FILE" <<YAML
IFAP_ENVIRONMENT: cloudrun
IFAP_KNOWLEDGE__PROVIDER: in_memory
IFAP_LLM__PROVIDER: gemini
IFAP_OBSERVABILITY__SERVICE_NAME: ${API_SERVICE}
IFAP_API__CORS_ORIGINS: '["${WEB_URL}", "${VERCEL_PRODUCTION_ORIGIN}", "http://localhost:3000"]'
IFAP_API__CORS_ORIGIN_REGEX: '${VERCEL_ORIGIN_REGEX}'
YAML

log "Building API image ${API_IMAGE}"
build infrastructure/docker/backend.Dockerfile "$API_IMAGE"

log "Deploying ${API_SERVICE}"
gcloud run deploy "$API_SERVICE" --image "$API_IMAGE" --region "$GCP_REGION" \
  --service-account "$RUNTIME_SA" --port 8000 --cpu 1 --memory 1Gi \
  --min-instances 0 --max-instances 2 --concurrency 40 --timeout 300 \
  --env-vars-file "$ENV_FILE" \
  --set-secrets "IFAP_DATABASE__URL=${SECRET_DATABASE_URL}:latest,IFAP_LLM__API_KEY=${SECRET_LLM_API_KEY}:latest" \
  --allow-unauthenticated --quiet

log "Building UI image ${WEB_IMAGE} (API: ${API_URL})"
build infrastructure/docker/frontend.Dockerfile "$WEB_IMAGE" "$API_URL"

log "Deploying ${WEB_SERVICE}"
gcloud run deploy "$WEB_SERVICE" --image "$WEB_IMAGE" --region "$GCP_REGION" \
  --service-account "$WEB_SA" --port 3000 --cpu 1 --memory 512Mi --min-instances 0 --max-instances 2 \
  --allow-unauthenticated --quiet

log "Smoke test"
curl --fail --silent --show-error --max-time 60 "${API_URL}/health" && echo
curl --fail --silent --show-error --max-time 60 --output /dev/null "${WEB_URL}" && echo "UI responds"

log "Live"
echo "   API: ${API_URL}   (docs: ${API_URL}/docs)"
echo "   UI : ${WEB_URL}"
echo "   Vercel: set NEXT_PUBLIC_IFAP_API_URL=${API_URL}"
