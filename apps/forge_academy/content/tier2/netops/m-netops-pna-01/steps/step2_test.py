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

_fn = globals().get("run_pna_analysis")
assert callable(_fn), "run_pna_analysis() must be defined."
_result = _fn()
assert isinstance(_result, list), "run_pna_analysis() must return a list."
print("PASS: PNA analysis returns a list of predictions.")
