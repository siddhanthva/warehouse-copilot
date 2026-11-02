#!/usr/bin/env bash
# Deploy to Cloud Run. Run the one-time setup section once, then just the deploy.
set -euo pipefail
: "${GOOGLE_CLOUD_PROJECT:?set GOOGLE_CLOUD_PROJECT}"
REGION="${GOOGLE_CLOUD_LOCATION:-us-central1}"
SA="warehouse-copilot-run@${GOOGLE_CLOUD_PROJECT}.iam.gserviceaccount.com"

if [[ "${1:-}" == "--setup" ]]; then
  gcloud iam service-accounts create warehouse-copilot-run --display-name "Warehouse Copilot (Cloud Run)"
  gcloud projects add-iam-policy-binding "$GOOGLE_CLOUD_PROJECT" --member "serviceAccount:$SA" --role roles/aiplatform.user
  # Secrets: you will be prompted for each value (nothing is echoed or stored in shell history).
  for name in db-url app-state-db-url langfuse-public-key langfuse-secret-key app-access-code; do
    read -rsp "Value for secret $name: " value; echo
    printf '%s' "$value" | gcloud secrets create "$name" --data-file=- --replication-policy=automatic
    gcloud secrets add-iam-policy-binding "$name" --member "serviceAccount:$SA" --role roles/secretmanager.secretAccessor
  done
  exit 0
fi

gcloud run deploy warehouse-copilot \
  --source . --region "$REGION" \
  --service-account "$SA" \
  --min-instances 0 --max-instances 2 --memory 1Gi --cpu 1 --timeout 300 \
  --set-secrets "DATABASE_URL=db-url:latest,APP_STATE_DATABASE_URL=app-state-db-url:latest,LANGFUSE_PUBLIC_KEY=langfuse-public-key:latest,LANGFUSE_SECRET_KEY=langfuse-secret-key:latest,APP_ACCESS_CODE=app-access-code:latest" \
  --set-env-vars "GOOGLE_CLOUD_PROJECT=$GOOGLE_CLOUD_PROJECT,GOOGLE_CLOUD_LOCATION=$REGION,GEMINI_MODEL=${GEMINI_MODEL:-gemini-3.5-flash},ALLOWED_TABLES=${ALLOWED_TABLES:?set ALLOWED_TABLES},LANGFUSE_BASE_URL=https://cloud.langfuse.com" \
  --allow-unauthenticated
