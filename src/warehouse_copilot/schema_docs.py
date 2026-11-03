"""Schema metadata from dbt.

Why: good table and column descriptions improve SQL accuracy more than clever prompts.
dbt already holds them, so we parse target/manifest.json (descriptions) and
target/catalog.json (column types) instead of writing them twice. Run
`dbt docs generate` and point DBT_TARGET_DIR at the target/ folder.

Output shape:
    {"marts.table": {"description": str,
                     "columns": {"col": {"type": str, "description": str}}}}
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import yaml

from warehouse_copilot.config import get_settings

TableDocs = dict[str, dict]


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def parse_dbt_artifacts(target_dir: Path, allowed_tables: tuple[str, ...]) -> TableDocs:
    """Merge manifest descriptions with catalog types, filtered to the allow-list."""
    manifest = _load_json(target_dir / "manifest.json")
    catalog = _load_json(target_dir / "catalog.json")
    allowed = {t.lower() for t in allowed_tables}
    docs: TableDocs = {}

    for node in manifest.get("nodes", {}).values():
        if node.get("resource_type") not in {"model", "seed", "snapshot"}:
            continue
        name = (node.get("alias") or node.get("name") or "").lower()
        key = f"{(node.get('schema') or '').lower()}.{name}"
        if key not in allowed:
            continue
        columns = {
            col_name.lower(): {
                "type": (col.get("data_type") or "").lower(),
                "description": col.get("description") or "",
            }
            for col_name, col in (node.get("columns") or {}).items()
        }
        docs[key] = {"description": node.get("description") or "", "columns": columns}

    for cat_node in catalog.get("nodes", {}).values():
        meta = cat_node.get("metadata", {})
        key = f"{(meta.get('schema') or '').lower()}.{(meta.get('name') or '').lower()}"
        if key not in allowed:
            continue
        entry = docs.setdefault(key, {"description": meta.get("comment") or "", "columns": {}})
        for col_name, col in (cat_node.get("columns") or {}).items():
            c = entry["columns"].setdefault(col_name.lower(), {"type": "", "description": ""})
            c["type"] = (col.get("type") or c["type"]).lower()
            if not c["description"] and col.get("comment"):
                c["description"] = col["comment"]

    # Allow-listed tables that dbt does not know about still appear (no descriptions),
    # so missing docs are visible instead of silently dropping a table.
    for key in allowed:
        docs.setdefault(key, {"description": "", "columns": {}})
    return dict(sorted(docs.items()))


SNAPSHOT_NAME = "schema_docs.json"


def load_snapshot(target_dir: Path, allowed_tables: tuple[str, ...]) -> TableDocs:
    """Read the slim docs snapshot (tables, columns, types, descriptions only).

    Why: manifest.json is ~1 MB of dbt internals and is gitignored, so CI and the Docker
    build never have it. The snapshot is small, holds only what the prompts use, and is committed.
    """
    raw = json.loads((target_dir / SNAPSHOT_NAME).read_text())
    allowed = {t.lower() for t in allowed_tables}
    docs = {t: info for t, info in raw.items() if t in allowed}
    for key in allowed:
        docs.setdefault(key, {"description": "", "columns": {}})
    return dict(sorted(docs.items()))


@lru_cache(maxsize=1)
def get_schema_docs() -> TableDocs:
    s = get_settings()
    # Freshly generated dbt artifacts win; the committed snapshot is the fallback.
    if not (s.dbt_target_dir / "manifest.json").exists() and (s.dbt_target_dir / SNAPSHOT_NAME).exists():
        return load_snapshot(s.dbt_target_dir, s.allowed_tables)
    return parse_dbt_artifacts(s.dbt_target_dir, s.allowed_tables)


def missing_descriptions(docs: TableDocs) -> list[str]:
    """List tables/columns without a description (used by a test and as a to-do list)."""
    missing = []
    for table, info in docs.items():
        if not info["description"]:
            missing.append(table)
        for col, cinfo in info["columns"].items():
            if not cinfo["description"]:
                missing.append(f"{table}.{col}")
    return missing


def schema_prompt(tables: list[str], docs: TableDocs | None = None) -> str:
    """Render selected tables as compact text for the SQL-generation prompt."""
    docs = docs or get_schema_docs()
    blocks = []
    for table in tables:
        info = docs.get(table.lower())
        if not info:
            continue
        lines = [f"TABLE {table} -- {info['description']}".rstrip(" -")]
        for col, c in info["columns"].items():
            lines.append(f"  {col} {c['type']}  -- {c['description']}".rstrip(" -"))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def table_catalog_prompt(docs: TableDocs | None = None) -> str:
    """One line per table, for the table-selection prompt."""
    docs = docs or get_schema_docs()
    return "\n".join(f"- {t}: {i['description'] or '(no description)'}" for t, i in docs.items())


@lru_cache(maxsize=1)
def get_metrics() -> dict[str, dict]:
    path = get_settings().metrics_path
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text()) or {}


def metrics_prompt(metrics: dict[str, dict] | None = None) -> str:
    metrics = metrics if metrics is not None else get_metrics()
    return "\n".join(
        f"- {name}: {m.get('description', '')}. SQL: {m.get('sql', '')}. Grain: {m.get('grain', '')}"
        for name, m in metrics.items()
    )
