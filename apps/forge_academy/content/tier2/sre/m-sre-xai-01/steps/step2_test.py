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

_traces = globals().get("get_recent_traces")
assert callable(_traces), "get_recent_traces() must be defined."
assert isinstance(_traces(), list), "get_recent_traces() must return a list."
assert callable(globals().get("run_attribution_report")), (
    "run_attribution_report() must be defined."
)
print("PASS: traces retrieved and an attribution report is available.")
