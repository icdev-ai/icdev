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

_fn = globals().get("run_pna_analysis")
assert callable(_fn), "run_pna_analysis() must be defined."

_captured = StringIO()
sys.stdout = _captured
try:
    _result = _fn()
finally:
    sys.stdout = sys.__stdout__
_out = _captured.getvalue()

assert isinstance(_result, list), "run_pna_analysis() must return a list."
assert len(_result) >= 2, (
    f"Expected at least a BGP and a Capacity prediction, got {len(_result)} result(s)."
)

_names = []
for _entry in _result:
    assert isinstance(_entry, (tuple, list)) and len(_entry) == 2, (
        f"Each result must be a (predictor_name, prediction_dict) tuple, got {_entry!r}"
    )
    _name, _pred = _entry
    assert isinstance(_pred, dict), f"Prediction for {_name!r} must be a dict, got {_pred!r}"
    assert "risk_level" in _pred, f"Prediction for {_name!r} is missing 'risk_level': {_pred}"
    _names.append(str(_name).upper())

assert "BGP" in _names, f"Results must include the BGP predictor, got {_names}"
assert "CAPACITY" in _names, f"Results must include the Capacity predictor, got {_names}"
assert _names.count("CAPACITY") == 3, (
    f"Expected one Capacity prediction per top-3 link, got {_names.count('CAPACITY')}"
)

_bgp = next(p for n, p in _result if str(n).upper() == "BGP")
assert _bgp.get("risk_level") == "high", "AS 64512 should be flagged high risk by the BGP predictor."
assert _bgp.get("recommended_action", "") in _out, (
    "Print the risk summary, including the BGP recommended_action."
)
print("PASS: PNA analysis ran BGP + top-3 Capacity predictions and surfaced the risk summary.")
