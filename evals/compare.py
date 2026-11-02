"""Execution-match comparison: does the agent's result equal the verified gold result?

Rules (from the plan):
  * compare values, not column names (aliases differ),
  * ignore row order unless order_matters is true,
  * round numerics to 2 decimals.
Extra rule: column order is ignored too, by matching each gold column to a predicted
column with identical values (so "SELECT a, b" and "SELECT b, a" both match).
"""

from __future__ import annotations

import math
from collections import Counter
from decimal import Decimal
from itertools import permutations
from typing import Any


def _norm(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, int | float | Decimal):
        f = float(v)
        if math.isnan(f):
            return None
        r = round(f, 2)
        return 0.0 if r == 0 else r
    s = str(v).strip()
    try:  # CSV round-trips numbers as text
        return _norm(float(s)) if s and s.lower() not in {"nan", "inf", "-inf"} else (s or None)
    except ValueError:
        return s


def normalize_rows(rows: list[list[Any]]) -> list[tuple]:
    return [tuple(_norm(v) for v in row) for row in rows]


def _columns(rows: list[tuple], n: int) -> list[tuple]:
    return [tuple(r[i] for r in rows) for i in range(n)]


def _same(a: list[tuple], b: list[tuple], order_matters: bool) -> bool:
    return a == b if order_matters else Counter(a) == Counter(b)


def results_match(pred_rows: list[list[Any]], gold_rows: list[list[Any]], order_matters: bool = False) -> bool:
    pred, gold = normalize_rows(pred_rows), normalize_rows(gold_rows)
    if len(pred) != len(gold):
        return False
    if not gold:
        return True
    n_gold, n_pred = len(gold[0]), len(pred[0]) if pred else 0
    if n_pred < n_gold:
        return False
    if n_pred == n_gold and _same(pred, gold, order_matters):
        return True
    # Find predicted columns that reproduce every gold column (allows reordered or extra columns).
    gold_cols = _columns(gold, n_gold)
    pred_cols = _columns(pred, n_pred)
    candidates = [
        [j for j in range(n_pred) if (pred_cols[j] == g if order_matters else Counter(pred_cols[j]) == Counter(g))]
        for g in gold_cols
    ]
    if any(not c for c in candidates):
        return False
    if n_pred > 8:  # avoid factorial blow-up on very wide results
        perms = [tuple(c[0] for c in candidates)]
    else:
        perms = (p for p in permutations(range(n_pred), n_gold) if all(p[i] in candidates[i] for i in range(n_gold)))
    for p in perms:
        projected = [tuple(row[j] for j in p) for row in pred]
        if _same(projected, gold, order_matters):
            return True
    return False
