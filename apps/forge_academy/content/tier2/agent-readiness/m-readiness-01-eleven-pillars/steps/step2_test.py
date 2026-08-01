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

# The pytest version shelled out to run the starter as a CLI and grepped stdout.
# subprocess is blocked, and the runner already executed the learner's __main__ block
# above, so grade the function the CLI is built on instead.
_fn = globals().get("check_and_report")
assert callable(_fn), "check_and_report() must be defined."

import io as _io
import sys as _sys

_buf = _io.StringIO()
_stdout = _sys.stdout
_sys.stdout = _buf
try:
    _rc = _fn(".")
finally:
    _sys.stdout = _stdout
_out = _buf.getvalue()

assert "AGENT READINESS REPORT" in _out, (
    "check_and_report() should print a report headed 'AGENT READINESS REPORT'."
)
assert ("PASS" in _out) or ("FAIL" in _out), (
    "The report should show per-pillar PASS/FAIL status."
)
assert _rc in (0, 1), "check_and_report() should return an exit code of 0 or 1."
print("PASS: readiness report produced with per-pillar status.")
