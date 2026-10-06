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
_check = globals().get("run_readiness_check")
assert callable(_check), "run_readiness_check() (the provided stub) must be defined."

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
_lines = _out.splitlines()

assert "AGENT READINESS REPORT" in _out, (
    "check_and_report() should print a report headed 'AGENT READINESS REPORT'."
)

_expected = _check(".")
for _pid, _score in _expected["pillar_scores"].items():
    # The real checker reports "percentage" as a fraction (0.0-1.0); an older starter
    # used 0-100. Accept either scale so a learner's stub decides, not the grader.
    _pct = float(_score.get("percentage", 0) or 0)
    _frac = _pct / 100.0 if _pct > 1.0 else _pct
    _status = "PASS" if _frac >= 0.7 else "FAIL"
    assert any(f"[{_status}]" in _l and _pid in _l for _l in _lines), (
        f"Expected a line like '[{_status}] {_pid}: NN%' for pillar {_pid!r}."
    )
    if _status == "FAIL":
        for _crit in _expected["icdev_checks"].get(_pid, []):
            if not _crit.get("passed"):
                assert _crit["message"] in _out, (
                    f"Failed pillar {_pid!r} should list its failing criterion: {_crit['message']!r}"
                )

_gate = 1 if _expected["overall_readiness_score"] < 0.7 else 0
assert _rc == _gate, (
    f"Overall score {_expected['overall_readiness_score']} -> exit code {_gate} "
    f"(deployment gate is 0.7), got {_rc!r}"
)
print("PASS: readiness report produced with per-pillar status, failing criteria and the 0.7 gate.")
