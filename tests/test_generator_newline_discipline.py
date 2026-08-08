# CUI // SP-CTI
"""Generator modules must write LF, on every platform.

`Path.write_text()` performs universal-newline translation, so on Windows it
turns every "\\n" into "\\r\\n". This repo is LF. A generator that writes without
`newline=""` therefore emits CRLF files, and git reports the WHOLE file as
changed the first time anyone edits one.

This is not hypothetical — it cost three separate fixes on 2026-08-08:
  * tools/genesis/rubric_build_tools.py — the owned build agent rewrote every
    file it patched, so one-line edits produced whole-file diffs (#1389).
  * tools/db/migration_runner.py — every scaffolded migration arrived CRLF
    (#1416).
  * 192 calls across 61 generator modules — this gate's motivating sweep.

None of it is reproducible on Linux, and all nine CI jobs are ubuntu-latest, so
the platform the team develops on is never tested. A static gate is the only
thing that catches it today.

Scope is deliberately GENERATORS — modules whose output lands in a git
repository. A `write_text` to a scratch file is not interesting; a `write_text`
that produces a committed source file is.
"""
import ast
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

#: Known exceptions. Empty on purpose — the sweep fixed every one. Adding an
#: entry means "this file genuinely wants platform-native endings", which for a
#: generator should be rare and worth a comment saying why.
ALLOWLIST: frozenset[str] = frozenset()


def _is_generator(path: pathlib.Path) -> bool:
    """A module whose output is intended to be committed."""
    rel = path.relative_to(REPO_ROOT).as_posix()
    # Normalise so the icdev/ mirror classifies the same as the root tree.
    if "/tools/" in rel:
        rel = "tools/" + rel.split("/tools/", 1)[1]
    return (
        rel.startswith("tools/builder/")
        or rel.startswith("tools/dx/")
        or path.name.endswith(
            ("_generator.py", "_writer.py", "_assembler.py", "_organizer.py")
        )
        or path.name.startswith("scaffolder")
    )


def _offenders(root: pathlib.Path) -> list[str]:
    out: list[str] = []
    if not root.is_dir():
        return out
    for path in sorted(root.rglob("*.py")):
        if not _is_generator(path):
            continue
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel in ALLOWLIST:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr == "write_text"):
                continue
            if any(k.arg == "newline" for k in node.keywords):
                continue
            out.append(f"{rel}:{node.lineno}")
    return out


@pytest.mark.parametrize("tree", ["tools", "icdev/tools"])
def test_generators_pass_newline_to_write_text(tree):
    offenders = _offenders(REPO_ROOT / tree)
    assert not offenders, (
        f"{len(offenders)} generator write_text() call(s) omit newline=\"\", so "
        "they emit CRLF on Windows and every generated file shows up as a "
        "whole-file diff:\n  "
        + "\n  ".join(offenders)
        + '\n\nFix: pass newline="" — Path.write_text(text, encoding="utf-8", '
        'newline="") — which disables translation in both directions.'
    )


def test_the_allowlist_is_not_quietly_growing():
    """An allowlist is how a gate dies. Keep it visible and justified."""
    assert len(ALLOWLIST) <= 3, (
        "the newline allowlist is growing; a generator that wants platform-native "
        "endings is rare, so each entry needs a comment explaining why"
    )


def test_the_gate_would_actually_catch_a_regression(tmp_path, monkeypatch):
    """A gate nobody has seen fail is a gate nobody knows works."""
    fake = tmp_path / "tools" / "builder"
    fake.mkdir(parents=True)
    (fake / "thing_generator.py").write_text(
        "from pathlib import Path\n"
        "def w(p: Path):\n"
        "    p.write_text('x', encoding='utf-8')\n",
        encoding="utf-8",
        newline="",
    )
    monkeypatch.setattr(
        pathlib.Path, "relative_to", pathlib.Path.relative_to, raising=False
    )
    import tests.test_generator_newline_discipline as mod

    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
    found = mod._offenders(tmp_path / "tools")
    assert found, "the gate failed to flag a known-bad generator"
    assert "thing_generator.py" in found[0]
