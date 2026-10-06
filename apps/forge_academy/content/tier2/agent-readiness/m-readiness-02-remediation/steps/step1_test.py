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
import pathlib

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
# Exercise the scanner on a real file. The sandbox cwd is a fresh temp dir, so a
# relative path is safe; the function takes a pathlib.Path, not a str.
_sample = pathlib.Path("stig_sample.py")
_sample.write_text("def login(u):\n    pass\n\n\ndef other():\n    pass\n", encoding="utf-8")
_hits = _find(_sample)
assert isinstance(_hits, list), "find_functions_needing_markers() must return a list."
assert len(_hits) == 1, (
    "In a file defining login() and other(), exactly ONE function (login) needs a "
    f"STIG marker; got {len(_hits)}: {_hits}"
)
_hit = _hits[0]
assert isinstance(_hit, dict), f"Each result must be a dict, got {type(_hit).__name__}"
assert _hit.get("name") == "login", f"Expected the login function, got name={_hit.get('name')!r}"
assert _hit.get("line") == 1, f"login is defined on line 1 of the sample, got line={_hit.get('line')!r}"
assert _hit.get("stig_comment") == _mappings["auth"], (
    "login matches the 'auth' keywords, so its stig_comment must be "
    f"STIG_MAPPINGS['auth'] ({_mappings['auth']!r}); got {_hit.get('stig_comment')!r}"
)
assert callable(globals().get("inject_markers")), "inject_markers() must be defined."
print("PASS: STIG mappings carry V-IDs and the remediation helpers are defined.")
