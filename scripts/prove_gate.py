"""Prove the eval regression gate works, locally and without GitHub.

    uv run python scripts/prove_gate.py [--model gemini-3.8-flash]

Three runs over the 15 easy golden questions (about 10 minutes, a few cents of model calls):
  1. normal prompt, records a throwaway baseline from its own accuracy
  2. deliberately broken prompt (the schema removed from it), the gate MUST fail on accuracy
  3. normal prompt again, the gate MUST pass

The broken prompt is patched in memory only: no repo file changes, so there is nothing to restore.
The model still answers, just without table or column names, so the failure is lower accuracy, not
authentication or a crash. (Appending "always answer SELECT 1" does not work: the model ignores it,
as the prompt itself tells it to.) A crash during a run is reported as a crash, never as a gate failure.
Results go to a temp folder, so the real eval results in evals/results/ are not overwritten.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # so `evals` imports when run as a script

from evals import run_evals  # noqa: E402
from warehouse_copilot.agent import prompts  # noqa: E402

EASY_IDS = ",".join(f"q{n:03d}" for n in range(1, 16))


def run(model: str, baseline: Path, *extra: str) -> int:
    return run_evals.main(["--model", model, "--ids", EASY_IDS, "--baseline", str(baseline), *extra])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=None)
    args = ap.parse_args()
    from warehouse_copilot.config import get_settings

    model = args.model or get_settings().gemini_model

    with tempfile.TemporaryDirectory() as tmp:
        run_evals.RESULTS_DIR = Path(tmp)
        baseline = Path(tmp) / "baseline.json"

        print("\n=== 1/3 normal prompt: record a throwaway baseline ===")
        if run(model, baseline, "--write-baseline") != 0:
            print("step 1 failed (not a gate result): check the database and model first")
            return 2
        acc = json.loads(baseline.read_text())["execution_accuracy"]
        print(f"throwaway baseline accuracy: {acc}%")

        print("\n=== 2/3 deliberately broken prompt: the gate must FAIL ===")
        original = prompts.GENERATE_SQL_SYSTEM
        prompts.GENERATE_SQL_SYSTEM = original.replace("{schema}", "(schema unavailable)")
        try:
            broken = run(model, baseline, "--gate")
        except Exception as err:  # a crash is not a gate result
            print(f"step 2 crashed ({type(err).__name__}); that is not a gate failure")
            return 2
        finally:
            prompts.GENERATE_SQL_SYSTEM = original  # restored even if the run raises

        print("\n=== 3/3 normal prompt again: the gate must PASS ===")
        normal = run(model, baseline, "--gate")

    ok = broken == 1 and normal == 0
    print(f"\nbroken prompt exit code = {broken} (want 1), normal prompt exit code = {normal} (want 0)")
    print("GATE PROVEN" if ok else "GATE NOT PROVEN: inspect the output above")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
