"""Central configuration.

Everything that differs between laptop, CI and Cloud Run comes from environment
variables so no secret ever lands in the repo. Limits live here (not in prompts)
because safety rules must be enforced in code, not requested from the model.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parent.parent

# Allow-list of the dbt marts the agent may query (schema.table). The schema is the dbt target
# schema ("dbt_dev"); override both values via DEFAULT_SCHEMA / ALLOWED_TABLES if it differs.
# dim_date is deliberately left out: no useful question needs it and fewer tables means a
# smaller prompt and a smaller attack surface.
DEFAULT_SCHEMA = "dbt_dev"
DEFAULT_ALLOWED_TABLES = tuple(
    f"{DEFAULT_SCHEMA}.{t}"
    for t in (
        "dim_hospital",
        "dim_drg",
        "dim_geography",
        "fct_medicare_drg",
        "fct_hospital_finance",
        "mart_finance_scorecard",
        "mart_reimbursement_gap",
        "mart_service_line_margin",
        "mart_burden_vs_community",
    )
)


def _load_dotenv() -> None:
    """Load .env if python-dotenv style file exists, without adding a dependency."""
    env_file = REPO_ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.split(" #", 1)[0].strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


def _csv(value: str | None, default: tuple[str, ...]) -> tuple[str, ...]:
    if not value:
        return default
    return tuple(v.strip().lower() for v in value.split(",") if v.strip())


@dataclass(frozen=True)
class Settings:
    # Google Cloud / Gemini on Vertex AI
    google_cloud_project: str | None = None
    google_cloud_location: str = "us-central1"
    gemini_model: str = "gemini-3.5-flash"  # verify current name in Vertex Model Garden
    gemini_model_alt: str = "gemini-3.1-pro-preview"
    # Gemini 3+: leave temperature unset (library default) and keep thinking low for cost.
    thinking_level: str = "low"

    # Databases. DATABASE_URL must be the read-only role (copilot_ro).
    database_url: str | None = None
    # Separate writable store for the LangGraph checkpointer and the daily request
    # counter. Never the warehouse role: the read-only role cannot write checkpoints.
    app_state_database_url: str | None = None

    # Langfuse
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_host: str = "https://cloud.langfuse.com"

    # App
    app_access_code: str | None = None
    daily_request_cap: int = 200
    github_repo_url: str = "https://github.com/siddhanthva/warehouse-copilot"

    # Metadata
    dbt_target_dir: Path = REPO_ROOT / "dbt_target"
    metrics_path: Path = PACKAGE_DIR / "metrics.yaml"
    default_schema: str = DEFAULT_SCHEMA
    allowed_tables: tuple[str, ...] = field(default=DEFAULT_ALLOWED_TABLES)

    # Limits (enforced in code)
    max_rows: int = 200
    statement_timeout_ms: int = 10_000
    connect_timeout_s: int = 10
    max_retries: int = 2
    max_clarifications: int = 2
    max_question_chars: int = 2_000
    approval_row_threshold: int = 2_000_000
    max_candidate_tables: int = 5

    @property
    def langfuse_enabled(self) -> bool:
        return bool(self.langfuse_public_key and self.langfuse_secret_key)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Read settings once per process; tests call get_settings.cache_clear()."""
    _load_dotenv()
    env = os.environ.get
    return Settings(
        google_cloud_project=env("GOOGLE_CLOUD_PROJECT"),
        google_cloud_location=env("GOOGLE_CLOUD_LOCATION", "us-central1"),
        gemini_model=env("GEMINI_MODEL", Settings.gemini_model),
        gemini_model_alt=env("GEMINI_MODEL_ALT", Settings.gemini_model_alt),
        thinking_level=env("GEMINI_THINKING_LEVEL", "low"),
        database_url=env("DATABASE_URL"),
        app_state_database_url=env("APP_STATE_DATABASE_URL"),
        langfuse_public_key=env("LANGFUSE_PUBLIC_KEY"),
        langfuse_secret_key=env("LANGFUSE_SECRET_KEY"),
        langfuse_host=env("LANGFUSE_BASE_URL") or env("LANGFUSE_HOST") or Settings.langfuse_host,
        app_access_code=env("APP_ACCESS_CODE"),
        daily_request_cap=int(env("DAILY_REQUEST_CAP", "200")),
        github_repo_url=env("GITHUB_REPO_URL", Settings.github_repo_url),
        dbt_target_dir=Path(env("DBT_TARGET_DIR", str(REPO_ROOT / "dbt_target"))),
        metrics_path=Path(env("METRICS_PATH", str(PACKAGE_DIR / "metrics.yaml"))),
        default_schema=env("DEFAULT_SCHEMA", DEFAULT_SCHEMA).lower(),
        allowed_tables=_csv(env("ALLOWED_TABLES"), DEFAULT_ALLOWED_TABLES),
        approval_row_threshold=int(env("APPROVAL_ROW_THRESHOLD", "2000000")),
    )
