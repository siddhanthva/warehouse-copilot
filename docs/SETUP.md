# What you still have to do by hand

The code for levels 3–8 is built and tested with a mocked model and a synthetic local
Postgres. These steps need your accounts, your data or your judgement.

## Level 0 — Accounts and setup (≈2 hours)

1. Create the GitHub repo `warehouse-copilot` and push this code.
2. Google Cloud project with the free trial. **Billing → Budgets & alerts**: alerts at $25 and $50.
3. Neon or Supabase Postgres (free tier). Copy the admin connection string.
4. Langfuse cloud account → project → API keys (public + secret).
5. Local tools: Python 3.11+, `uv`, Git, Docker Desktop, Node.js (for MCP Inspector), `gcloud`.
6. Google Cloud:
   ```bash
   gcloud auth login
   gcloud config set project YOUR_PROJECT_ID
   gcloud services enable aiplatform.googleapis.com run.googleapis.com \
     artifactregistry.googleapis.com cloudbuild.googleapis.com secretmanager.googleapis.com \
     iamcredentials.googleapis.com
   gcloud auth application-default login
   ```
7. `cp .env.example .env` and fill it in. Check the model names in **Vertex AI → Model Garden**
   and set `GEMINI_MODEL` / `GEMINI_MODEL_ALT` to what is current for your region.
8. Verify:
   ```bash
   uv sync --dev
   uv run python scripts/gemini_smoke.py     # prints a Gemini reply; check Vertex AI in the console
   uv run pytest -q                          # unit tests, no network
   ```

## Level 1 — Golden set (cannot be delegated)

1. Replace the 4 example lines in `evals/golden_set.jsonl` with 40–60 questions
   (~15 easy, ~25 medium, ~15 hard) plus ~5 ambiguous ones (`"ambiguous": true` and a
   `"clarification"` the eval runner should answer with).
2. Write or verify every `gold_sql` yourself against your real marts.
3. `ADMIN_DATABASE_URL=... uv run python -m evals.build_expected` → `evals/expected/<id>.csv`.
4. Open every CSV and confirm it answers the question.
5. Hold out ~10 questions you never look at while tuning prompts (e.g. the starred questions in evals/question_candidates.md) and report them separately.

## Level 2 — Load the warehouse and lock it down

1. Point your dbt profile at the hosted Postgres and `dbt build` (or `pg_dump | pg_restore`).
2. Create the roles (choose two long passwords):
   ```bash
   psql "$ADMIN_DATABASE_URL" -v ro_password="'...'" -v app_password="'...'" \
     -f scripts/setup_readonly_role.sql
   ```
   Then set `DATABASE_URL` (copilot_ro) and `APP_STATE_DATABASE_URL` (copilot_app) in `.env`.
3. `dbt docs generate`, copy `target/manifest.json` and `target/catalog.json` into `dbt_target/`.
4. Set `ALLOWED_TABLES` to your real mart names (`schema.table`, comma-separated).
5. Rewrite `src/warehouse_copilot/metrics.yaml` against your real column names (8–12 metrics)
   and replace `tests/fixtures/dbt_target` + the test allow-list if you want tests to mirror your schema.
6. Prove layer 2 on the real database:
   ```bash
   RUN_INTEGRATION=1 uv run pytest -q -m integration
   ```
7. Fill the missing descriptions this prints (aim for none):
   ```bash
   uv run python -c "from warehouse_copilot.schema_docs import *; print(missing_descriptions(get_schema_docs()))"
   ```

## Levels 3–8 — run them for real (the code exists)

- MCP Inspector: `npx @modelcontextprotocol/inspector uv run python -m warehouse_copilot.mcp_server`
- Claude Desktop: add to `claude_desktop_config.json`:
  ```json
  {"mcpServers": {"warehouse-copilot": {"command": "uv", "args": ["--directory", "/ABS/PATH/warehouse-copilot", "run", "python", "-m", "warehouse_copilot.mcp_server"]}}}
  ```
- App locally: `uv run streamlit run app/streamlit_app.py`
- Evals for both models, then accept a baseline:
  ```bash
  uv run python -m evals.run_evals --model "$GEMINI_MODEL" --write-baseline
  uv run python -m evals.run_evals --model "$GEMINI_MODEL_ALT"
  ```
  Fill `PRICES` in `evals/run_evals.py` from the Vertex AI pricing page first.
- Prove the gate: temporarily degrade a prompt, run `--gate`, see it fail, revert.

### Workload Identity Federation for the nightly eval
```bash
PROJECT_ID=your-project; REPO=YOUR_GITHUB_USER/warehouse-copilot
PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)')
gcloud iam service-accounts create gh-eval --display-name "GitHub eval"
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member "serviceAccount:gh-eval@$PROJECT_ID.iam.gserviceaccount.com" --role roles/aiplatform.user
gcloud iam workload-identity-pools create github --location global --display-name GitHub
gcloud iam workload-identity-pools providers create-oidc github-oidc --location global \
  --workload-identity-pool github --issuer-uri https://token.actions.githubusercontent.com \
  --attribute-mapping "google.subject=assertion.sub,attribute.repository=assertion.repository" \
  --attribute-condition "assertion.repository=='$REPO'"
gcloud iam service-accounts add-iam-policy-binding gh-eval@$PROJECT_ID.iam.gserviceaccount.com \
  --role roles/iam.workloadIdentityUser \
  --member "principalSet://iam.googleapis.com/projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/github/attribute.repository/$REPO"
```
GitHub repo → Settings → Secrets and variables → Actions:
- **Variables**: `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION`, `GEMINI_MODEL`, `ALLOWED_TABLES`,
  `GCP_WIF_PROVIDER` = `projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/github/providers/github-oidc`,
  `GCP_EVAL_SERVICE_ACCOUNT` = `gh-eval@$PROJECT_ID.iam.gserviceaccount.com`
- **Secrets**: `DATABASE_URL`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`
- The eval workflow also needs `dbt_target/*.json` and `evals/expected/*.csv` committed.

### Cloud Run
```bash
export GOOGLE_CLOUD_PROJECT=... ALLOWED_TABLES=...
./scripts/deploy_cloud_run.sh --setup   # once: service account, Vertex AI User role, secrets
./scripts/deploy_cloud_run.sh           # each deploy (min 0 / max 2 instances)
```
Commit `dbt_target/*.json` first (`git add -f`) so the image has your schema docs.

## Level 9 — Write these yourself
README sections marked **TODO(me)**, `docs/design.md` sections marked **TODO(me)**,
the eval table from `evals/results/*.md`, the 2-minute demo video and `docs/demo.gif`.
Export `docs/architecture.mmd` to `docs/architecture.png` (mermaid.live → PNG).

## Level 10 — Real users and launch
1. Give the live URL + access code to 3–5 people; ask each for 5 real questions.
2. Log each problem as a GitHub issue; fix the top 3; add good questions to the golden set; re-run evals.
3. Add a "User testing" section to the README with before/after numbers.
4. Pin the repo, post on LinkedIn with the demo video, update your resume with measured numbers.
