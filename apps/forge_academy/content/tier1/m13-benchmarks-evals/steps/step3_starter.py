"""
Step 3: Implement pass@k and a golden-set eval.
Goal: fill in pass_at_k, mean_pass_at_k, normalize and score_golden_set.
Everything runs offline over the fixed fixture below -- no model is called.
"""

import math  # noqa: F401 -- math.comb is what your pass_at_k needs

# Fixture 1 -- for each coding problem, did each of 5 samples pass its unit tests?
SAMPLE_RESULTS = {
    "two_sum":      [True, True, True, True, True],
    "parse_date":   [True, False, False, False, False],
    "lru_cache":    [False, False, False, False, False],
    "merge_ranges": [True, True, False, False, False],
}

# Fixture 2 -- recorded answers from one model on a six-case golden set.
# Costs are fixture values for this exercise, not real provider prices.
GOLDEN_ROWS = [
    {"expected": "Paris",   "output": "  paris ",           "latency_ms": 100, "cost_usd": 0.001},
    {"expected": "42",      "output": "42",                 "latency_ms": 80,  "cost_usd": 0.001},
    {"expected": "AC-2",    "output": "ac-2",               "latency_ms": 200, "cost_usd": 0.002},
    {"expected": "No",      "output": "Yes",                "latency_ms": 90,  "cost_usd": 0.001},
    {"expected": "SC-7",    "output": "The answer is SC-7", "latency_ms": 150, "cost_usd": 0.003},
    {"expected": "TLS 1.3", "output": "TLS   1.3",          "latency_ms": 380, "cost_usd": 0.004},
]


def pass_at_k(n: int, c: int, k: int) -> float:
    """Unbiased pass@k: 1 - C(n - c, k) / C(n, k).

    Raise ValueError if k < 1 or k > n. Return 1.0 when n - c < k.
    """
    # YOUR CODE HERE
    pass


def mean_pass_at_k(results: dict, k: int) -> float:
    """Average pass@k over problems. Each value is a list of per-sample booleans."""
    # YOUR CODE HERE
    pass


def normalize(text: str) -> str:
    """Strip, lower-case, and collapse runs of whitespace to a single space."""
    # YOUR CODE HERE
    pass


def score_golden_set(rows: list) -> dict:
    """Return n, correct, accuracy, mean_latency_ms, total_cost_usd, cost_per_correct_usd."""
    # YOUR CODE HERE
    pass


if __name__ == "__main__":
    for k in (1, 3, 5):
        print(f"pass@{k} = {mean_pass_at_k(SAMPLE_RESULTS, k)}")
    print(score_golden_set(GOLDEN_ROWS))
