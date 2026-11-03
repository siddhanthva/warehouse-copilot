"""Run every gold_sql with the ADMIN connection and save results to evals/expected/<id>.csv.

    ADMIN_DATABASE_URL=... python -m evals.build_expected

Uses the admin URL on purpose: gold SQL is written and verified by you, not the model,
and is not subject to the agent's allow-list. Read every CSV it writes (Level 1).
"""

from __future__ import annotations

import csv
import os
import sys
from pathlib import Path

import psycopg
from dotenv import dotenv_values

from evals.run_evals import EVALS_DIR, load_golden
from warehouse_copilot.db import _jsonable

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    url = os.environ.get("ADMIN_DATABASE_URL") or dotenv_values(ROOT / ".env").get("ADMIN_DATABASE_URL")
    if not url:
        print("ADMIN_DATABASE_URL is not set", file=sys.stderr)
        return 2
    out_dir = EVALS_DIR / "expected"
    out_dir.mkdir(exist_ok=True)
    golden = Path(sys.argv[1]) if len(sys.argv) > 1 else EVALS_DIR / "golden_set.jsonl"
    failures = 0
    with psycopg.connect(url) as conn:
        # Set the timeout after connecting: Neon's pooled hosts reject it as a startup option.
        conn.autocommit = True
        conn.execute("set statement_timeout = 60000")
        conn.autocommit = False
        conn.read_only = True
        for item in load_golden(golden):
            if not item.get("gold_sql"):
                continue
            try:
                with conn.cursor() as cur:
                    cur.execute(item["gold_sql"])
                    cols = [d.name for d in cur.description]
                    rows = cur.fetchall()
                conn.rollback()
            except Exception as err:
                conn.rollback()
                failures += 1
                print(f"[ERROR] {item['id']}: {err}")
                continue
            with (out_dir / f"{item['id']}.csv").open("w", newline="") as f:
                w = csv.writer(f)
                w.writerow(cols)
                w.writerows([[_jsonable(v) for v in r] for r in rows])
            print(f"[ok] {item['id']}: {len(rows)} rows")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
