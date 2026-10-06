# ruff: noqa: F821 -- pass_at_k & co. are defined by the learner submission this is appended to
# Auto-grader for M13 Step 3: pass@k and a golden-set eval
#
# Appended to the learner's submission and run in the code_runner sandbox. The
# fixtures are re-declared here under their own names, so editing the starter's
# fixture cannot change what is graded.

_GRADER_SAMPLES = {
    "two_sum":      [True, True, True, True, True],
    "parse_date":   [True, False, False, False, False],
    "lru_cache":    [False, False, False, False, False],
    "merge_ranges": [True, True, False, False, False],
}

_GRADER_ROWS = [
    {"expected": "Paris",   "output": "  paris ",           "latency_ms": 100, "cost_usd": 0.001},
    {"expected": "42",      "output": "42",                 "latency_ms": 80,  "cost_usd": 0.001},
    {"expected": "AC-2",    "output": "ac-2",               "latency_ms": 200, "cost_usd": 0.002},
    {"expected": "No",      "output": "Yes",                "latency_ms": 90,  "cost_usd": 0.001},
    {"expected": "SC-7",    "output": "The answer is SC-7", "latency_ms": 150, "cost_usd": 0.003},
    {"expected": "TLS 1.3", "output": "TLS   1.3",          "latency_ms": 380, "cost_usd": 0.004},
]


def _close(actual, expected, tol=1e-9):
    return isinstance(actual, (int, float)) and abs(actual - expected) <= tol


# -- pass@k ------------------------------------------------------------------
assert pass_at_k(5, 1, 1) is not None, "pass_at_k() returned None -- did you implement it?"
assert _close(pass_at_k(5, 1, 1), 0.2), f"pass@1 with c=1 of n=5 is c/n = 0.2, got {pass_at_k(5, 1, 1)}"
assert _close(pass_at_k(5, 1, 3), 0.6), f"pass@3 with c=1 of n=5 is 1 - C(4,3)/C(5,3) = 0.6, got {pass_at_k(5, 1, 3)}"
assert _close(pass_at_k(5, 2, 3), 0.9), f"pass@3 with c=2 of n=5 is 1 - C(3,3)/C(5,3) = 0.9, got {pass_at_k(5, 2, 3)}"
assert _close(pass_at_k(5, 0, 3), 0.0), "a problem with no passing sample has pass@k = 0.0"
assert _close(pass_at_k(5, 3, 3), 1.0), "when n - c < k every draw contains a pass: expected 1.0"
assert _close(pass_at_k(10, 3, 1), 0.3), "pass@1 must equal c / n"

for _bad_k in (0, 6):
    try:
        pass_at_k(5, 2, _bad_k)
    except ValueError:
        pass
    else:
        raise AssertionError(f"pass_at_k(5, 2, {_bad_k}) must raise ValueError (k must be 1..n)")

assert _close(mean_pass_at_k(_GRADER_SAMPLES, 1), 0.4), \
    f"mean pass@1 over the fixture is 0.4, got {mean_pass_at_k(_GRADER_SAMPLES, 1)}"
assert _close(mean_pass_at_k(_GRADER_SAMPLES, 3), 0.625), \
    f"mean pass@3 over the fixture is 0.625, got {mean_pass_at_k(_GRADER_SAMPLES, 3)}"
assert _close(mean_pass_at_k(_GRADER_SAMPLES, 5), 0.75), \
    f"mean pass@5 over the fixture is 0.75, got {mean_pass_at_k(_GRADER_SAMPLES, 5)}"

# -- golden-set eval ----------------------------------------------------------
assert normalize("  TLS   1.3 ") == "tls 1.3", f"normalize() must strip, lower and collapse whitespace, got {normalize('  TLS   1.3 ')!r}"

_report = score_golden_set(_GRADER_ROWS)
assert isinstance(_report, dict), f"score_golden_set() must return a dict, got {type(_report)}"
for _key in ("n", "correct", "accuracy", "mean_latency_ms", "total_cost_usd", "cost_per_correct_usd"):
    assert _key in _report, f"score_golden_set() result is missing {_key!r}"

assert _report["n"] == 6, f"n should be 6, got {_report['n']}"
assert _report["correct"] == 4, (
    f"exactly 4 rows match after normalisation (row 4 is wrong, row 5 fails exact match), got {_report['correct']}"
)
assert _close(_report["accuracy"], 4 / 6), f"accuracy should be 4/6, got {_report['accuracy']}"
assert _close(_report["mean_latency_ms"], 1000 / 6), f"mean latency should be 1000/6 ms, got {_report['mean_latency_ms']}"
assert _close(_report["total_cost_usd"], 0.012), f"total cost should be 0.012, got {_report['total_cost_usd']}"
assert _close(_report["cost_per_correct_usd"], 0.003), f"cost per correct should be 0.012/4 = 0.003, got {_report['cost_per_correct_usd']}"

_none_right = score_golden_set([{"expected": "a", "output": "b", "latency_ms": 10, "cost_usd": 0.5}])
assert _none_right["correct"] == 0 and _none_right["cost_per_correct_usd"] == float("inf"), \
    "with no correct answers, cost_per_correct_usd must be float('inf')"

print("PASS: pass@k and the golden-set eval reproduce the expected numbers.")
