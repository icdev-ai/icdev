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

_mappings = globals().get("STIG_MAPPINGS")
_keywords = globals().get("PATTERN_KEYWORDS")
assert isinstance(_mappings, dict) and _mappings, "STIG_MAPPINGS must be a non-empty dict."
assert isinstance(_keywords, dict), "PATTERN_KEYWORDS must be a dict."

for _key, _comment in _mappings.items():
    assert "V-" in str(_comment), f"STIG mapping {_key!r} is missing its V-ID."

assert set(_mappings) == set(_keywords), (
    "STIG_MAPPINGS and PATTERN_KEYWORDS must cover the same keys; "
    f"difference: {set(_mappings) ^ set(_keywords)}"
)

_find = globals().get("find_functions_needing_markers")
assert callable(_find), "find_functions_needing_markers() must be defined."
assert isinstance(_find("."), list), (
    "find_functions_needing_markers() must return a list."
)
assert callable(globals().get("inject_markers")), "inject_markers() must be defined."
print("PASS: STIG mappings carry V-IDs and the remediation helpers are defined.")
