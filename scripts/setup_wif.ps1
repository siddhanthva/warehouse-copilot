# One-time Google Cloud setup so GitHub Actions can call Vertex AI without a downloaded key
# (Workload Identity Federation). Safe to re-run: "already exists" errors are expected and ignored.
#
#   powershell -ExecutionPolicy Bypass -File scripts/setup_wif.ps1
#
# It creates: a service account (gh-eval) with only the Vertex AI User role, a workload identity
# pool + GitHub OIDC provider that only accepts tokens from THIS repository, and lets that
# repository impersonate the service account. It prints the values to paste into GitHub.

$ErrorActionPreference = "Continue"
$gcloud = "$env:LOCALAPPDATA\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"
if (-not (Test-Path $gcloud)) { $gcloud = "gcloud" }

$PROJECT_ID = "warehouse-copilot-513522"
$REPO       = "siddhanthva/warehouse-copilot"
$PROJECT_NUMBER = (& $gcloud projects describe $PROJECT_ID --format="value(projectNumber)").Trim()
$SA = "gh-eval@$PROJECT_ID.iam.gserviceaccount.com"

Write-Host "Project $PROJECT_ID ($PROJECT_NUMBER), repo $REPO"

& $gcloud iam service-accounts create gh-eval --display-name "GitHub eval" --project $PROJECT_ID

& $gcloud projects add-iam-policy-binding $PROJECT_ID `
  --member "serviceAccount:$SA" --role roles/aiplatform.user --condition=None | Out-Null

& $gcloud iam workload-identity-pools create github --location global `
  --display-name GitHub --project $PROJECT_ID

& $gcloud iam workload-identity-pools providers create-oidc github-oidc --location global `
  --workload-identity-pool github --issuer-uri https://token.actions.githubusercontent.com `
  --attribute-mapping "google.subject=assertion.sub,attribute.repository=assertion.repository" `
  --attribute-condition "assertion.repository=='$REPO'" --project $PROJECT_ID

& $gcloud iam service-accounts add-iam-policy-binding $SA --role roles/iam.workloadIdentityUser `
  --member "principalSet://iam.googleapis.com/projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/github/attribute.repository/$REPO" `
  --project $PROJECT_ID | Out-Null

$PROVIDER = & $gcloud iam workload-identity-pools providers describe github-oidc --location global `
  --workload-identity-pool github --project $PROJECT_ID --format="value(name)"

Write-Host ""
Write-Host "=== Paste into GitHub: repo > Settings > Secrets and variables > Actions > VARIABLES ==="
Write-Host "GCP_WIF_PROVIDER          = $PROVIDER"
Write-Host "GCP_EVAL_SERVICE_ACCOUNT  = $SA"
Write-Host "GOOGLE_CLOUD_PROJECT      = $PROJECT_ID"
Write-Host "GOOGLE_CLOUD_LOCATION     = global"
Write-Host "GEMINI_MODEL              = gemini-3.8-flash"
Write-Host "LANGFUSE_BASE_URL         = https://us.cloud.langfuse.com"
Write-Host ""
Write-Host "=== ... and SECRETS (copy the values from your own .env; do not paste them anywhere else) ==="
Write-Host "DATABASE_URL  (the read-only copilot_ro URL)"
Write-Host "LANGFUSE_PUBLIC_KEY"
Write-Host "LANGFUSE_SECRET_KEY"
