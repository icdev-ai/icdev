# Auto-grader for m-aie-01 Step 3 — one project, three instruction files.
#
# run_code concatenates the learner's script and this file into ONE program, so the
# learner's build_instruction_files is visible here. Grade it against the shown
# project AND a hidden second one, so text hardcoded for ledger-api cannot pass.

_EXPECTED_PATHS = {"CLAUDE.md", "AGENTS.md", ".cursor/rules/project.mdc"}
_REQUIRED_HEADINGS = ("## Project Overview", "## Commands", "## Conventions", "## Boundaries")

_build = globals().get("build_instruction_files")
assert callable(_build), (
    "build_instruction_files is not defined — keep the starter's function and implement it."
)

_HIDDEN_PROJECT = {
    "name": "fleet-telemetry",
    "summary": "Go service that ingests vehicle telemetry over MQTT.",
    "stack": ["Go 1.23", "MQTT", "TimescaleDB"],
    "commands": {"test": "go test ./...", "lint": "golangci-lint run", "build": "make build"},
    "conventions": ["Wrap every returned error with context", "No package-level mutable state"],
    "boundaries": ["Never vendor dependencies by hand", "Never disable the race detector in CI"],
}


def _check(project):
    files = _build(project)
    assert isinstance(files, dict), "build_instruction_files must return a dict of {path: content}."
    assert set(files) == _EXPECTED_PATHS, (
        f"Expected exactly {sorted(_EXPECTED_PATHS)}, got {sorted(files)}."
    )
    for path, content in files.items():
        assert isinstance(content, str) and content.strip(), f"{path} is empty."
        lines = content.splitlines()
        for heading in _REQUIRED_HEADINGS:
            assert heading in lines, f"{path} is missing the section heading {heading!r} on its own line."
        assert project["name"] in content, f"{path} does not name the project ({project['name']!r})."
        assert project["summary"] in content, f"{path} does not include the project summary."
        for cmd in project["commands"].values():
            assert cmd in content, f"{path} is missing the command {cmd!r}."
        for rule in project["conventions"]:
            assert rule in content, f"{path} is missing the convention {rule!r}."
        for rule in project["boundaries"]:
            assert rule in content, f"{path} is missing the boundary {rule!r}."

    # Cursor .mdc: YAML frontmatter must come FIRST, before any markdown.
    mdc = files[".cursor/rules/project.mdc"].splitlines()
    assert mdc and mdc[0].strip() == "---", ".cursor/rules/project.mdc must start with a '---' line."
    try:
        close = next(i for i in range(1, len(mdc)) if mdc[i].strip() == "---")
    except StopIteration:
        raise AssertionError(".cursor/rules/project.mdc frontmatter is never closed with '---'.")
    header = [ln.strip() for ln in mdc[1:close]]
    assert any(ln.startswith("description:") and ln[len("description:"):].strip() for ln in header), (
        "The Cursor frontmatter needs a non-empty 'description:' line."
    )
    assert "alwaysApply: true" in header, "The Cursor frontmatter needs 'alwaysApply: true'."
    for heading in _REQUIRED_HEADINGS:
        assert heading not in header, "Section headings belong after the frontmatter, not inside it."

    # Plain-markdown harness files must NOT carry Cursor's header.
    for path in ("CLAUDE.md", "AGENTS.md"):
        assert files[path].splitlines()[0].strip() != "---", (
            f"{path} is plain markdown — only the Cursor .mdc file takes YAML frontmatter."
        )


_shown = globals().get("PROJECT")
assert isinstance(_shown, dict), "PROJECT is missing — keep the starter's PROJECT dict."
_check(_shown)
_check(_HIDDEN_PROJECT)

print("PASS: three instruction files, one source, every rule present in each.")
