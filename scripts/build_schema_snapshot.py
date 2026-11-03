"""Write dbt_target/schema_docs.json: the slim, committable copy of the schema docs.

    uv run python scripts/build_schema_snapshot.py

Reads manifest.json + catalog.json (see schema_docs.parse_dbt_artifacts) and keeps only what the
prompts use. Re-run it whenever descriptions or columns change, then commit the file.
"""

from __future__ import annotations

import json

from warehouse_copilot import schema_docs
from warehouse_copilot.config import get_settings


def main() -> None:
    s = get_settings()
    docs = schema_docs.parse_dbt_artifacts(s.dbt_target_dir, s.allowed_tables)
    problems = schema_docs.missing_descriptions(docs)
    if problems:
        raise SystemExit(f"refusing to write a snapshot with missing descriptions: {problems[:10]}")
    out = s.dbt_target_dir / schema_docs.SNAPSHOT_NAME
    out.write_text(json.dumps(docs, indent=1) + "\n")
    print(f"wrote {out} ({out.stat().st_size:,} bytes, {len(docs)} tables)")


if __name__ == "__main__":
    main()
