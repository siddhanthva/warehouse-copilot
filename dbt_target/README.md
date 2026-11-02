Copy `manifest.json` and `catalog.json` here from your dbt project after running
`dbt docs generate` (they live in your dbt project's `target/` folder).
The app reads table and column descriptions from them. They are git-ignored by
default; commit them (`git add -f`) if you want CI and Cloud Run builds to include them.
