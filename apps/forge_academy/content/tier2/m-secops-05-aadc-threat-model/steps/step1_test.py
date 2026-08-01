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

_graph = globals().get("DESIGN_GRAPH")
assert isinstance(_graph, dict), "DESIGN_GRAPH must be defined."
_stride = globals().get("run_stride_analysis")
assert callable(_stride), "run_stride_analysis() must be defined."

_result = _stride(_graph)
assert isinstance(_result, list), "run_stride_analysis must return a list."
assert len(_result) >= 4, f"Expected at least 4 threats, got {len(_result)}."

_severe = [f for f in _result if str(f.get("severity", "")).upper() in ("CRITICAL", "HIGH")]
assert len(_severe) >= 2, f"Expected at least 2 CRITICAL/HIGH threats, got {len(_severe)}."

for _finding in _result:
    for _field in ("threat_id", "category", "severity"):
        assert _field in _finding, f"Finding missing field {_field!r}: {_finding}"

_valid_ids = {n.get("id") for n in _graph.get("nodes", []) if isinstance(n, dict)}
if _valid_ids:
    for _finding in _result:
        for _nid in _finding.get("affected_nodes", []) or []:
            assert _nid in _valid_ids, f"affected_node {_nid!r} is not in DESIGN_GRAPH."

assert callable(globals().get("map_atlas_techniques")), "map_atlas_techniques() must be defined."
assert callable(globals().get("generate_threat_report")), "generate_threat_report() must be defined."
print("PASS: STRIDE analysis produced graded findings tied to real graph nodes.")
