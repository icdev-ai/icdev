# Auto-grader for m-aie-04 Step 3 — an instruction file whose lines are checkable.
#
# Reads the learner's INSTRUCTIONS string from the shared namespace and checks it
# structurally. It never calls a model: every rule below is a string predicate.

TEST_COMMAND = "python -m pytest tests/test_ledger.py -q"
MAX_LINES = 40
IMPERATIVES = ("never", "always", "do not", "don't", "must")
VAGUE = ("write good code", "best practice", "clean code", "be careful")
DEFECT_CLASSES = [
    ("swallowed exception", ("except",)),
    ("off-by-one boundary", ("limit", "boundary")),
    ("partial write on failure", ("unchanged", "partial", "rollback", "before")),
]

_text = globals().get("INSTRUCTIONS")
assert isinstance(_text, str) and _text.strip(), "Define INSTRUCTIONS as a non-empty string."

_lines = [ln.rstrip() for ln in _text.splitlines()]
_nonblank = [ln for ln in _lines if ln.strip()]
_problems = []

if len(_nonblank) > MAX_LINES:
    _problems.append(
        f"{len(_nonblank)} non-blank lines; keep it to {MAX_LINES} — the harness pays for "
        "this file in every session"
    )

_low = _text.lower()
for phrase in VAGUE:
    if phrase in _low:
        _problems.append(f"uncheckable phrase {phrase!r}: no reviewer can tell whether it was obeyed")


def _section(name):
    """Lines under the `## name` heading, up to the next `#` heading. None if absent."""
    body, inside = [], False
    for ln in _lines:
        stripped = ln.strip()
        if stripped.startswith("#"):
            if inside:
                break
            inside = stripped.lstrip("#").strip().lower() == name.lower()
            continue
        if inside:
            body.append(stripped)
    return body if inside else None


_commands = _section("Commands")
_rules = _section("Rules")
_done = _section("Done means")

if _commands is None:
    _problems.append("missing the '## Commands' section")
else:
    cmd_text = "\n".join(_commands)
    if TEST_COMMAND not in cmd_text:
        _problems.append(f"'## Commands' must contain the exact test command: {TEST_COMMAND}")
    if "|| true" in cmd_text:
        _problems.append("'|| true' makes the test command succeed whatever the tests decide")

if _rules is None:
    _problems.append("missing the '## Rules' section")
else:
    bullets = [ln[2:].strip() for ln in _rules if ln[:2] in ("- ", "* ")]
    if len(bullets) < 3:
        _problems.append(f"'## Rules' has {len(bullets)} bullet rule(s); write at least 3")
    for b in bullets:
        if not b.lower().strip("*`_ ").startswith(IMPERATIVES):
            _problems.append(
                f"rule {b!r} does not start with Never / Always / Do not / Must"
            )
    rules_low = "\n".join(bullets).lower()
    for label, words in DEFECT_CLASSES:
        if not any(w in rules_low for w in words):
            _problems.append(
                f"no rule covers the {label} defect (mention one of: {', '.join(words)})"
            )

if _done is None:
    _problems.append("missing the '## Done means' section")
elif "pytest" not in "\n".join(_done).lower():
    _problems.append(
        "'## Done means' must name the test command — done is the output of a run, not a sentence"
    )

assert not _problems, "Your instruction file is not checkable yet:\n  " + "\n  ".join(_problems)

print(f"PASS: {len(_nonblank)} lines, three sections, every rule checkable.")
