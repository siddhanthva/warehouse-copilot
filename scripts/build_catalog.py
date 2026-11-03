"""Write dbt_target/catalog.json (column types) from the live warehouse, as the read-only role.

Why: schema_docs.py wants column types next to the descriptions in manifest.json. `dbt docs generate`
produces them but would have to run inside the separate dbt repo with admin credentials. Reading
information_schema as copilot_ro gives the same data without touching that repo, in the same shape
schema_docs.parse_dbt_artifacts expects: nodes -> {metadata: {schema, name}, columns: {col: {type}}}.
"""

from __future__ import annotations

import json
import os

import psycopg

from warehouse_copilot.config import get_settings


def build_catalog(database_url: str, tables: tuple[str, ...]) -> dict:
    nodes: dict[str, dict] = {}
    with psycopg.connect(database_url, connect_timeout=30) as conn:
        for qualified in tables:
            schema, _, name = qualified.partition(".")
            rows = conn.execute(
                """select column_name, data_type from information_schema.columns
                   where table_schema = %s and table_name = %s order by ordinal_position""",
                (schema, name),
            ).fetchall()
            nodes[f"model.cms_spine.{name}"] = {
                "metadata": {"schema": schema, "name": name},
                "columns": {col: {"type": dtype} for col, dtype in rows},
            }
    return {"nodes": nodes}


def main() -> None:
    settings = get_settings()
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL (read-only role) is not set")
    catalog = build_catalog(url, settings.allowed_tables)
    out = settings.dbt_target_dir / "catalog.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(catalog, indent=2))
    empty = [n["metadata"]["name"] for n in catalog["nodes"].values() if not n["columns"]]
    print(f"wrote {out} ({len(catalog['nodes'])} tables); tables with no columns: {empty or 'none'}")


if __name__ == "__main__":
    main()
