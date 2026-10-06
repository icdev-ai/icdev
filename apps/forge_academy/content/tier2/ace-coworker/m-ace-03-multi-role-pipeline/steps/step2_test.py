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

_req = globals().get("PIPELINE_REQUEST")
assert isinstance(_req, dict), "PIPELINE_REQUEST must be defined as a dict."
_stages = _req.get("pipeline")
assert isinstance(_stages, list), "PIPELINE_REQUEST['pipeline'] must be a list."
assert len(_stages) >= 3, f"A pipeline needs at least 3 stages, found {len(_stages)}."

_valid_roles = {
    "ai_developer", "agent_developer", "security_analyst",
    "data_engineer", "devops_engineer", "compliance_officer",
}
for _stage in _stages:
    assert "role" in _stage, f"Stage missing 'role': {_stage}"
    assert "task" in _stage, f"Stage missing 'task': {_stage}"
    assert _stage["role"] in _valid_roles, (
        f"Unknown role {_stage['role']!r}. Valid roles: {sorted(_valid_roles)}"
    )

_run = globals().get("run_pipeline")
assert callable(_run), "run_pipeline() must be defined."

import io as _io
import sys as _sys

_buf = _io.StringIO()
_sys.stdout = _buf
try:
    _run()
finally:
    _sys.stdout = _sys.__stdout__
_out = _buf.getvalue()
for _stage in _stages:
    _want = "pending_hitl" if _stage.get("hitl_required") else "done"
    assert any(_stage["role"] in _l and _want in _l for _l in _out.splitlines()), (
        f"run_pipeline() should print the simulated status of stage {_stage['role']!r} "
        f"('{_want}'), e.g. 'stage 1 {_stage['role']}: {_want}'"
    )
print("PASS: a multi-role pipeline with valid roles and a runner.")
