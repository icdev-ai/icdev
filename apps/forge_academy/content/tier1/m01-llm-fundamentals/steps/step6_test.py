# Auto-grader for M01 Step 6: price a request, fit a prompt to its budget.
#
# run_code concatenates the learner's script and this file into ONE program, so the
# learner's functions are visible here as globals. Expected values are computed by
# this file's OWN reference tokenizer and arithmetic - never by calling the learner's
# helpers - so editing toy_tokenize() or hardcoding one answer cannot pass.

import re as _re

_REF_PIECE = _re.compile(r"\w+|[^\w\s]")


def _ref_count(text):
    return sum((len(p) + 5) // 6 for p in _REF_PIECE.findall(text or ""))


def _ref_cost(usage, prices):
    def _n(key):
        return usage.get(key, 0) or 0
    return (
        _n("input_tokens") * prices["input"]
        + _n("cached_input_tokens") * prices["cached_input"]
        + (_n("output_tokens") + _n("reasoning_tokens")) * prices["output"]
    ) / 1_000_000


def _ref_fit(system_prompt, history, question, window, reserved):
    budget = window - reserved
    used = _ref_count(system_prompt) + _ref_count(question)
    if used > budget:
        raise ValueError("does not fit")
    kept = []
    for turn in reversed(history):
        cost = _ref_count(turn)
        if used + cost > budget:
            break
        used += cost
        kept.append(turn)
    return list(reversed(kept))


# --- 0. The functions must exist ----------------------------------------------
for _name in ("request_cost", "fit_to_budget"):
    assert callable(globals().get(_name)), f"{_name} is not defined - keep the starter's function."

_rc = globals()["request_cost"]
_fit = globals()["fit_to_budget"]

# --- 1. request_cost ------------------------------------------------------------
_PRICES = {
    "a": {"input": 4.00, "cached_input": 0.20, "output": 20.00},
    "b": {"input": 2.00, "cached_input": 0.20, "output": 10.00},
    "c": {"input": 0.80, "cached_input": 0.08, "output": 4.00},
}
_COST_CASES = [
    ({"input_tokens": 5000, "cached_input_tokens": 15000,
      "output_tokens": 500, "reasoning_tokens": 1500}, "b"),
    ({"input_tokens": 1_000_000, "output_tokens": 0}, "a"),
    ({"input_tokens": 1234, "cached_input_tokens": 0,
      "output_tokens": 789, "reasoning_tokens": 4321}, "a"),
    ({"input_tokens": 300, "output_tokens": 2000}, "c"),
    ({"cached_input_tokens": 90_000, "output_tokens": 10}, "c"),
]
for _usage, _row in _COST_CASES:
    _got = _rc(dict(_usage), dict(_PRICES[_row]))
    _want = _ref_cost(_usage, _PRICES[_row])
    assert isinstance(_got, (int, float)), (
        f"request_cost returned {_got!r} - it must return a number of dollars."
    )
    assert abs(_got - _want) < 1e-9, (
        f"request_cost({_usage}, {_PRICES[_row]}) = {_got!r}, expected {_want:.6f}. "
        "Prices are per MILLION tokens, cached input uses the cached rate, and "
        "reasoning tokens are billed at the output rate."
    )

# --- 2. fit_to_budget -----------------------------------------------------------
_SYS = "You are a concise assistant for ICDEV operators."
_Q = "Which deployment failed last night?"
_HIST = [
    "Operator: show yesterday's pipeline runs please.",
    "Assistant: three runs, two green and one red on staging.",
    "Operator: what broke on staging?",
    "Assistant: the helm chart failed linting because of an indentation error.",
    "Operator: fixed it, rerunning now.",
]
_fixed = _ref_count(_SYS) + _ref_count(_Q)
_all = _fixed + sum(_ref_count(t) for t in _HIST)

_FIT_CASES = [
    (_all + 50, 50),                 # everything fits exactly
    (_all + 100, 50),                # room to spare
    (_all + 20, 40),                 # must drop at least one old turn
    (_fixed + 30, 10),               # keeps only the most recent turns
    (_fixed + 5, 5),                 # system + question only
]
for _window, _reserved in _FIT_CASES:
    _given = list(_HIST)
    _got = _fit(_SYS, _given, _Q, _window, _reserved)
    _want = _ref_fit(_SYS, _HIST, _Q, _window, _reserved)
    assert _given == _HIST, "fit_to_budget must not modify the history list it was given."
    assert isinstance(_got, list), f"fit_to_budget returned {_got!r} - return a list of turns."
    assert _got == _want, (
        f"window={_window}, reserved_output={_reserved}: kept {len(_got)} turn(s), "
        f"expected the {len(_want)} most recent. Budget is context_window - "
        "reserved_output; drop from the OLDEST end; keep original order."
    )
    _used = _fixed + sum(_ref_count(t) for t in _got)
    assert _used <= _window - _reserved, "the trimmed prompt still exceeds the budget."

# --- 3. A prompt that cannot fit must be refused ---------------------------------
_raised = False
try:
    _fit(_SYS, list(_HIST), _Q, _fixed + 3, 4)
except ValueError:
    _raised = True
assert _raised, (
    "When the system prompt and question alone exceed the budget, raise ValueError "
    "instead of returning a prompt that will overflow."
)

print("PASS: you priced every request correctly and trimmed every prompt to its budget.")
