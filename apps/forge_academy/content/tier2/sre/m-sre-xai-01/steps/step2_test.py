# Auto-grader — runner idiom.
#
# aca-vv-01: this file used to be a pytest module (def test_*, importlib/subprocess to
# load the starter from disk). The Academy runner concatenates the learner's code and
# this grader into ONE script and runs it with `python -I`, so:
#   * importlib/subprocess are rejected by the sandbox AST allowlist (penta-aca-02),
#     which made this step impossible to complete; and
#   * even unblocked, `def test_*` functions are never called by plain python, so it
#     would have passed everything.
# The learner's module-level names are already in scope here. Assert on those.

import sys
from io import StringIO

_traces = globals().get("get_recent_traces")
assert callable(_traces), "get_recent_traces() must be defined."
assert isinstance(_traces(), list), "get_recent_traces() must return a list."
_run = globals().get("run_attribution_report")
assert callable(_run), "run_attribution_report() must be defined."
_best_fn = globals().get("highest_average_tool")
assert callable(_best_fn), "highest_average_tool() must be defined."

_captured = StringIO()
sys.stdout = _captured
try:
    _results = _run()
finally:
    sys.stdout = sys.__stdout__
_out = _captured.getvalue()

assert isinstance(_results, list), "run_attribution_report() must return a list."
assert len(_results) == 5, f"Expected one attribution per recent trace (5), got {len(_results)}."
for _r in _results:
    assert isinstance(_r, dict) and isinstance(_r.get("tool_attributions"), list), (
        "Each result must be an explain() dict with 'tool_attributions' "
        f"(append the attribution, not the raw trace); got {_r}"
    )
    assert _r["tool_attributions"], f"Trace {_r.get('trace_id')} has no tool attributions."

# Independent re-derivation of the highest-average tool from the returned results.
_totals = {}
for _r in _results:
    for _a in _r["tool_attributions"]:
        _totals[_a["tool"]] = _totals.get(_a["tool"], 0.0) + _a["shap_value"]
_expected = max(_totals, key=lambda t: _totals[t])
_got = _best_fn(_results)
assert _got == _expected, f"highest_average_tool() should be {_expected!r}, got {_got!r}"

assert _expected in _out, "Print the attribution table and the highest-average summary."
assert len(_out.strip().splitlines()) >= 6, (
    "Print a ranked attribution table per trace plus the summary line."
)
print(f"PASS: AgentSHAP attribution over 5 traces; top tool by average SHAP is {_expected}.")
