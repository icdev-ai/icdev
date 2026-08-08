# CUI // SP-CTI
"""Every `write_text` under tools/ must pass `newline=`.

`Path.write_text()` performs universal-newline translation, so on Windows it
turns every "\\n" into "\\r\\n". This repo is LF, so a call without `newline=""`
emits CRLF and git reports the WHOLE file as changed the first time anyone
edits it.

Not hypothetical — four fixes on 2026-08-08:
  * tools/genesis/rubric_build_tools.py — the owned build agent rewrote every
    file it patched, so one-line edits produced whole-file diffs (#1389).
  * tools/db/migration_runner.py — every scaffolded migration arrived CRLF
    (#1416).
  * 192 calls across 61 generator modules (#1417) — this gate's first scope.
  * the remaining 695 calls across 434 modules — the uniformity sweep.

SCOPE. This started narrower: only "generators", on the reasoning that CRLF in
output nobody commits is invisible. That reasoning does not survive contact.
Whether a file's output is committed is not a property of the module — it is a
property of where the caller points it, and it changes the moment someone reuses
a helper. Two of the first three bugs were in modules nobody would have called
generators. So the rule is uniform: every `write_text` in tools/ is explicit
about newlines. On Linux that is a no-op; on Windows it is the difference
between a one-line diff and a whole-file rewrite, and there is no case where a
caller WANTS Python to guess.

None of it reproduces on Linux, and all nine CI jobs are ubuntu-latest, so the
platform the team develops on is never tested. Until hgx-port-02 adds a
windows-latest tier, this static gate is what catches it.
"""
import ast
import subprocess
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

#: Known exceptions. Empty on purpose — the sweep fixed every one. Adding an
#: entry means "this file genuinely wants platform-native endings", which for a
#: generator should be rare and worth a comment saying why.
ALLOWLIST: frozenset[str] = frozenset()


def _in_scope(path: pathlib.Path) -> bool:
    """Every module under tools/.

    The gate started life scoped to generators — modules whose output is
    committed — on the reasoning that CRLF elsewhere is invisible. That reasoning
    does not survive contact: "is this file's output committed?" is not a
    property of the module, it is a property of where the caller points it, and
    it changes when someone reuses a helper. Two of the three CRLF bugs found on
    2026-08-08 were in modules nobody would have called generators
    (rubric_build_tools, migration_runner).

    Uniform rule instead: any `write_text` in tools/ passes `newline=`. On Linux
    it is a no-op; on Windows it is the difference between a one-line diff and a
    whole-file rewrite. There is no case where the caller WANTS Python to guess.
    """
    del path  # scope is now unconditional; kept for call-site symmetry
    return True


def _python_files(root: pathlib.Path) -> list[pathlib.Path]:
    """Python files under *root* that git actually TRACKS.

    Not ``rglob``. This gate protects committed content, so it must look at
    exactly what CI checks out. Walking the filesystem instead pulls in
    gitignored local files: ``tools/trading/`` is ignored, and its
    ``genesis_daemon.py`` writes a ``.bat`` startup script that legitimately
    wants CRLF. That gave a gate which was RED on a developer's machine and
    GREEN in CI — the fastest way to get a gate switched off.

    Falls back to a filesystem walk outside a git checkout (an unpacked sdist,
    say) so the test still means something there.
    """
    rel = root.relative_to(REPO_ROOT).as_posix()
    try:
        proc = subprocess.run(
            ["git", "ls-files", "--", f"{rel}/*.py"],
            cwd=REPO_ROOT, capture_output=True, text=True, check=False,
        )
        if proc.returncode == 0:
            return [REPO_ROOT / ln for ln in proc.stdout.splitlines() if ln.strip()]
    except Exception:  # noqa: BLE001 — fall through to the filesystem walk
        pass
    return sorted(root.rglob("*.py"))  # pragma: no cover - non-git checkout


def _offenders(root: pathlib.Path) -> list[str]:
    out: list[str] = []
    if not root.is_dir():
        return out
    for path in _python_files(root):
        if not _in_scope(path):
            continue
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel in ALLOWLIST:
            continue
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # Cheap prescan before the expensive parse. Only ~1 file in 8 mentions
        # write_text at all, and ast.parse over every tracked module costs about
        # 50s — enough to make people reach for -k to skip it.
        if "write_text" not in source:
            continue
        try:
            tree = ast.parse(source)
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
def test_all_tools_pass_newline_to_write_text(tree):
    offenders = _offenders(REPO_ROOT / tree)
    assert not offenders, (
        f"{len(offenders)} write_text() call(s) under tools/ omit newline=\"\", so "
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
    import tests.test_write_text_newline_discipline as mod

    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
    found = mod._offenders(tmp_path / "tools")
    assert found, "the gate failed to flag a known-bad generator"
    assert "thing_generator.py" in found[0]
