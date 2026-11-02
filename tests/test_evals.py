"""Eval scoring rules, aggregation and the regression gate. No model, no database."""

from __future__ import annotations

import json

from evals import run_evals
from evals.compare import results_match
from warehouse_copilot.runtime import Turn


def test_match_ignores_row_order_by_default():
    assert results_match([["a", 1], ["b", 2]], [["b", "2"], ["a", "1"]])


def test_match_respects_order_when_required():
    assert not results_match([["a", 1], ["b", 2]], [["b", 2], ["a", 1]], order_matters=True)
    assert results_match([["a", 1], ["b", 2]], [["a", 1], ["b", 2]], order_matters=True)


def test_match_rounds_numerics_to_two_decimals():
    assert results_match([["x", 10.004]], [["x", "10.0"]])
    assert not results_match([["x", 10.02]], [["x", "10.0"]])


def test_match_ignores_column_names_and_order():
    # gold: (name, value); prediction: (value, name)
    assert results_match([[1.5, "a"], [2.5, "b"]], [["a", 1.5], ["b", 2.5]])


def test_match_allows_extra_predicted_columns():
    assert results_match([["a", 1, "extra"]], [["a", 1]])


def test_match_rejects_missing_columns_or_rows():
    assert not results_match([["a"]], [["a", 1]])
    assert not results_match([["a", 1]], [["a", 1], ["b", 2]])


def test_match_nulls_and_empty():
    assert results_match([[None, 1]], [["", "1"]])
    assert results_match([], [])


def _rec(i, diff, match, latency=1.0, ambiguous=False, status="ok"):
    return {
        "id": i, "difficulty": diff, "ambiguous": ambiguous, "status": status, "match": match,
        "valid_sql": True, "first_try_valid": True, "retries": 0, "latency_s": latency,
        "input_tokens": 100, "output_tokens": 10, "cost_usd": 0.0, "asked_clarification": ambiguous,
        "error": None,
    }


def test_aggregate_by_difficulty_and_percentiles():
    recs = [_rec("1", "easy", True, 1), _rec("2", "easy", False, 2), _rec("3", "hard", True, 10)]
    s = run_evals.aggregate(recs)
    assert s["execution_accuracy"] == 66.7
    assert s["by_difficulty"]["easy"]["execution_accuracy"] == 50.0
    assert s["by_difficulty"]["hard"]["execution_accuracy"] == 100.0
    assert s["latency_p50_s"] == 2.0


def test_gate(tmp_path):
    base = tmp_path / "baseline.json"
    base.write_text(json.dumps({"execution_accuracy": 80.0}))
    assert run_evals.gate({"execution_accuracy": 77.0}, base, 3.0)[0]
    assert not run_evals.gate({"execution_accuracy": 76.9}, base, 3.0)[0]
    base.write_text(json.dumps({"execution_accuracy": None}))
    assert run_evals.gate({"execution_accuracy": 0.0}, base, 3.0)[0]


class FakeRuntime:
    """Pauses once for clarification, once for approval, then answers."""

    def __init__(self):
        self.resumes = []

    def ask(self, question, **_):
        return Turn("t1", "interrupted", {"type": "clarify", "question": "which?"})

    def resume(self, thread_id, value, **_):
        self.resumes.append(value)
        if len(self.resumes) == 1:
            return Turn("t1", "interrupted", {"type": "approve", "sql": "SELECT 1"})
        state = {"validated_sql": "SELECT 1", "retries": 0, "attempts": [],
                 "result": {"rows": [["a", 1.0]]}, "usage": {"input_tokens": 5, "output_tokens": 1}}
        return Turn("t1", "ok", None, state)


def test_run_one_auto_clarifies_and_approves(tmp_path, monkeypatch):
    (tmp_path / "expected").mkdir()
    (tmp_path / "expected" / "q9.csv").write_text("name,val\na,1.0\n")
    monkeypatch.setattr(run_evals, "EVALS_DIR", tmp_path)
    rt = FakeRuntime()
    item = {"id": "q9", "question": "top?", "difficulty": "easy", "ambiguous": True, "clarification": "by payment"}
    rec = run_evals.run_one(rt, item, "m")
    assert rt.resumes == [{"clarification": "by payment"}, {"approved": True}]
    assert rec["match"] and rec["auto_approved"] and rec["asked_clarification"]


def test_golden_set_file_is_valid():
    items = run_evals.load_golden(run_evals.EVALS_DIR / "golden_set.jsonl")
    for i in items:
        assert {"id", "question", "difficulty", "gold_sql"} <= set(i)
        assert i["difficulty"] in {"easy", "medium", "hard"}
