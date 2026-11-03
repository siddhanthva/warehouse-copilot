Copy `manifest.json` and `catalog.json` here from your dbt project after running
`dbt docs generate` (they live in your dbt project's `target/` folder).
The app reads table and column descriptions from them. They are git-ignored by
default; commit them (`git add -f`) if you want CI and Cloud Run builds to include them.

Recommended instead: run `uv run python scripts/build_schema_snapshot.py` to write the slim
`schema_docs.json` (tables, columns, types, descriptions only). That file is committed and is
used when manifest.json is absent (CI, Docker).
