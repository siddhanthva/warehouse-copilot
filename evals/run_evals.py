"""Run the golden set through the full agent and score execution accuracy.

    python -m evals.run_evals --model gemini-3.5-flash
    python -m evals.run_evals --model gemini-3.1-pro-preview --limit 10
    python -m evals.run_evals --gate          # exit 1 if accuracy drops > 3 pts below baseline

Approvals are auto-approved; ambiguous questions get their stored `clarification`
(or are reported separately when none is stored). Writes
evals/results/<model>_<date>.json and a markdown table next to it.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import statistics
import sys
import time
from pathlib import Path

from evals.compare import results_match

EVALS_DIR = Path(__file__).resolve().parent
RESULTS_DIR = EVALS_DIR / "results"

# USD per 1M tokens. Fill in current Vertex AI prices for your models (check the
# Vertex AI pricing page); used only for the cost-per-question estimate.
# Only add a model here after reading its price on Google's own Vertex AI pricing page. A model
# with no entry reports cost as "unverified" (never $0 and never another model's rate), because
# cost per question is a number you will quote.
PRICES: dict[str, dict[str, float]] = {
    # "gemini-3.8-flash": {"input": ..., "output": ...},
}


def load_golden(path: Path) -> list[dict]:
    items = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    ids = [i["id"] for i in items]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate ids in golden set")
    return items


def load_expected(qid: str) -> list[list[str]]:
    path = EVALS_DIR / "expected" / f"{qid}.csv"
    with path.open(newline="") as f:
        rows = list(csv.reader(f))
    return rows[1:]  # drop header


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float | None:
    p = PRICES.get(model)
    if p is None:
        return None
    return (input_tokens * p["input"] + output_tokens * p["output"]) / 1_000_000


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    k = (len(values) - 1) * pct / 100
    lo, hi = int(k), min(int(k) + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (k - lo)


def run_one(runtime, item: dict, model: str) -> dict:
    """Ask one question, auto-resolving interrupts, and score it."""
    start = time.perf_counter()
    trace = {"question_id": item["id"], "tags": ["eval"]}
    turn = runtime.ask(item["question"], **trace)
    clarified = approved = False
    for _ in range(4):  # bounded: at most a couple of clarify/approve pauses
        if not turn.paused:
            break
        kind = turn.interrupt.get("type")
        if kind == "approve":
            approved = True
            turn = runtime.resume(turn.thread_id, {"approved": True}, **trace)
        elif kind == "clarify":
            if not item.get("clarification"):
                break
            clarified = True
            turn = runtime.resume(turn.thread_id, {"clarification": item["clarification"]}, **trace)
    latency = time.perf_counter() - start
    st = turn.state
    usage = st.get("usage") or {}
    rec = {
        "id": item["id"],
        "difficulty": item.get("difficulty", "unknown"),
        "ambiguous": bool(item.get("ambiguous")),
        "unanswerable": bool(item.get("unanswerable")),
        "declined": turn.status == "unanswerable",
        "status": turn.status,
        "asked_clarification": clarified or (turn.paused and turn.interrupt.get("type") == "clarify"),
        "auto_approved": approved,
        "sql": st.get("validated_sql"),
        "valid_sql": bool(st.get("validated_sql")),
        "first_try_valid": bool(st.get("validated_sql")) and not any(
            a.get("stage") == "guard" for a in st.get("attempts") or []
        ),
        "retries": st.get("retries", 0),
        "latency_s": round(latency, 3),
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "error": st.get("error"),
        "match": False,
    }
    rec["cost_usd"] = cost_usd(model, rec["input_tokens"], rec["output_tokens"])
    if item.get("unanswerable"):
        # Pass = the agent declined and never ran a query. No expected CSV exists for these.
        rec["match"] = turn.status == "unanswerable" and st.get("result") is None
        return rec
    if turn.status == "ok" and st.get("result") is not None:
        try:
            rec["match"] = results_match(
                st["result"]["rows"], load_expected(item["id"]), item.get("order_matters", False)
            )
        except FileNotFoundError:
            rec["error"] = "expected CSV missing; run evals/build_expected.py"
    return rec


def aggregate(records: list[dict]) -> dict:
    scored = [
        r
        for r in records
        if not (r["ambiguous"] and r["status"] == "interrupted") and not r.get("unanswerable")
    ]
    unanswerable = [r for r in records if r.get("unanswerable")]

    def acc(rs: list[dict]) -> float:
        return round(100 * sum(r["match"] for r in rs) / len(rs), 1) if rs else 0.0

    by_diff = {}
    for d in sorted({r["difficulty"] for r in scored}):
        rs = [r for r in scored if r["difficulty"] == d]
        by_diff[d] = {"n": len(rs), "execution_accuracy": acc(rs)}
    lat = [r["latency_s"] for r in scored]
    n = len(scored) or 1
    return {
        "n_questions": len(records),
        "n_scored": len(scored),
        "execution_accuracy": acc(scored),
        "by_difficulty": by_diff,
        "valid_sql_rate": round(100 * sum(r["valid_sql"] for r in scored) / n, 1),
        "first_try_valid_rate": round(100 * sum(r["first_try_valid"] for r in scored) / n, 1),
        "avg_retries": round(statistics.mean([r["retries"] for r in scored]) if scored else 0, 2),
        "latency_p50_s": round(percentile(lat, 50), 2),
        "latency_p95_s": round(percentile(lat, 95), 2),
        "avg_input_tokens": round(sum(r["input_tokens"] for r in scored) / n),
        "avg_output_tokens": round(sum(r["output_tokens"] for r in scored) / n),
        "avg_cost_usd": (
            None if any(r["cost_usd"] is None for r in scored) else round(sum(r["cost_usd"] for r in scored) / n, 5)
        ),
        "ambiguous_asked_clarification": sum(r["asked_clarification"] for r in records if r["ambiguous"]),
        "ambiguous_total": sum(r["ambiguous"] for r in records),
        "unanswerable_declined": sum(r["match"] for r in unanswerable),
        "unanswerable_total": len(unanswerable),
    }


def markdown(model: str, summary: dict, records: list[dict]) -> str:
    lines = [
        f"## {model} — {summary['n_scored']} scored questions",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Execution accuracy | {summary['execution_accuracy']}% |",
    ]
    for d, v in summary["by_difficulty"].items():
        lines.append(f"| Accuracy ({d}, n={v['n']}) | {v['execution_accuracy']}% |")
    lines += [
        f"| Valid-SQL rate (after retries) | {summary['valid_sql_rate']}% |",
        f"| Valid on first try | {summary['first_try_valid_rate']}% |",
        f"| Avg retries | {summary['avg_retries']} |",
        f"| Latency p50 / p95 | {summary['latency_p50_s']}s / {summary['latency_p95_s']}s |",
        f"| Avg tokens in / out | {summary['avg_input_tokens']} / {summary['avg_output_tokens']} |",
        "| Est. cost per question | "
        + ("unverified (no verified price for this model)" if summary["avg_cost_usd"] is None else f"${summary['avg_cost_usd']}")
        + " |",
        f"| Ambiguous questions that asked to clarify | {summary['ambiguous_asked_clarification']}/{summary['ambiguous_total']} |",
        f"| Unanswerable questions correctly declined | {summary['unanswerable_declined']}/{summary['unanswerable_total']} |",
        "",
        "### Failures",
        "",
    ]
    for r in records:
        if not r["match"]:
            lines.append(f"- **{r['id']}** ({r['difficulty']}, {r['status']}): {r['error'] or 'result mismatch'}")
    return "\n".join(lines) + "\n"


def gate(summary: dict, baseline_path: Path, max_drop: float, model: str | None = None) -> tuple[bool, str]:
    base = json.loads(baseline_path.read_text()) if baseline_path.exists() else {}
    ref = base.get("execution_accuracy")
    if ref is None:
        return True, "No baseline recorded yet; gate skipped. Commit a baseline.json to enable it."
    if model and base.get("model") and base["model"] != model:
        # Accuracy is not comparable across models, so fail loudly instead of passing or failing by luck.
        return False, f"baseline was recorded for {base['model']} but this run used {model}: refusing to compare"
    now = summary["execution_accuracy"]
    ok = now >= ref - max_drop
    return ok, f"accuracy {now}% vs baseline {ref}% (allowed drop {max_drop} pts): {'PASS' if ok else 'FAIL'}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=None, help="Gemini model name (default: GEMINI_MODEL)")
    ap.add_argument("--golden", default=str(EVALS_DIR / "golden_set.jsonl"))
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--ids", default=None, help="comma-separated question ids")
    ap.add_argument("--gate", action="store_true", help="fail if below baseline - max-drop")
    ap.add_argument("--baseline", default=str(EVALS_DIR / "baseline.json"))
    ap.add_argument("--max-drop", type=float, default=3.0)
    ap.add_argument("--write-baseline", action="store_true")
    args = ap.parse_args(argv)

    from warehouse_copilot.config import get_settings
    from warehouse_copilot.runtime import AgentRuntime

    model = args.model or get_settings().gemini_model
    items = load_golden(Path(args.golden))
    if args.ids:
        wanted = set(args.ids.split(","))
        items = [i for i in items if i["id"] in wanted]
    if args.limit:
        items = items[: args.limit]

    runtime = AgentRuntime(model=model)
    records = []
    try:
        for item in items:
            rec = run_one(runtime, item, model)
            records.append(rec)
            mark = "PASS" if rec["match"] else "FAIL"
            print(f"[{mark}] {rec['id']} {rec['difficulty']:<6} {rec['latency_s']:>6.1f}s retries={rec['retries']}", flush=True)
    finally:
        runtime.close()

    summary = aggregate(records)
    RESULTS_DIR.mkdir(exist_ok=True)
    stamp = dt.datetime.now(dt.UTC).strftime("%Y-%m-%d")
    safe_model = model.replace("/", "_")
    out = {"model": model, "date": stamp, "summary": summary, "records": records}
    (RESULTS_DIR / f"{safe_model}_{stamp}.json").write_text(json.dumps(out, indent=2))
    md = markdown(model, summary, records)
    (RESULTS_DIR / f"{safe_model}_{stamp}.md").write_text(md)
    print(md)

    if args.write_baseline:
        Path(args.baseline).write_text(json.dumps({"model": model, "date": stamp, **summary}, indent=2))
        print(f"baseline written to {args.baseline}")
    if args.gate:
        ok, msg = gate(summary, Path(args.baseline), args.max_drop, model)
        print(msg)
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
