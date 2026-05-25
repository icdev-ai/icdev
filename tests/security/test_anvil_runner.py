"""OPT-42 — tests for tools/anvil/runner.py and generated wrappers."""
from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.anvil import runner as r  # noqa: E402


# ---------------------------------------------------------------------------
# Command extraction + safety
# ---------------------------------------------------------------------------
def test_is_safe_accepts_python_tools():
    assert r._is_safe("python tools/foo.py --json")
    assert r._is_safe("python -m tools.foo")
    assert r._is_safe("!python tools/bar.py")


def test_is_safe_rejects_shell_builtins():
    for bad in ("rm -rf /", "curl x", "cat /etc/passwd", "bash foo.py"):
        assert not r._is_safe(bad)


def test_substitute_no_placeholder():
    assert r._substitute("python tools/x.py", ["unused"]) == "python tools/x.py"


def test_substitute_quotes_args_with_spaces():
    out = r._substitute("python tools/x.py $ARGUMENTS", ["foo bar", "--flag"])
    assert "'foo bar'" in out or '"foo bar"' in out
    assert "--flag" in out


def test_extract_commands_dedup(tmp_path):
    src = tmp_path / "sample.md"
    src.write_text("""\
# Feature
```bash
python tools/a.py
python tools/b.py --json
python tools/a.py
```
""", encoding="utf-8")
    cmds = r.extract_commands(src)
    assert cmds == ["python tools/a.py", "python tools/b.py --json"]


def test_extract_commands_skips_non_python():
    """Only python invocations captured — shell builtins inside fences are ignored
    during extraction because the regex anchors on `python`."""
    p = Path(BASE_DIR) / ".claude" / "commands" / "feature.md"
    assert p.exists()
    cmds = r.extract_commands(p)
    assert all(c.lstrip("!").strip().startswith(("python "))
               for c in cmds if c.strip())


# ---------------------------------------------------------------------------
# run_command error + dry-run paths
# ---------------------------------------------------------------------------
def test_run_command_missing_source():
    result = r.run_command("no/such/file.md", [])
    assert "error" in result
    assert result["source"] == "no/such/file.md"


def test_run_command_dry_run_no_execute(tmp_path, monkeypatch):
    src = tmp_path / "sample.md"
    src.write_text("""\
```bash
python tools/x.py
```
""", encoding="utf-8")
    # Point BASE_DIR at tmp so the relative resolves correctly
    monkeypatch.setattr(r, "BASE_DIR", tmp_path)
    result = r.run_command("sample.md", [], dry_run=True)
    assert result["dry_run"] is True
    assert result["executed_count"] == 0
    assert result["total_commands"] == 1
    assert result["steps"][0]["would_run"] is True


# ---------------------------------------------------------------------------
# run_skill delegation (OPT-41 glue)
# ---------------------------------------------------------------------------
def test_run_skill_unknown():
    result = r.run_skill("no-such-skill", [], dry_run=True)
    assert "error" in result
    assert result["name"] == "no-such-skill"


def test_run_skill_dry_run_known():
    result = r.run_skill("icdev-status", [], dry_run=True)
    assert result["executed_count"] == 0
    assert result["dry_run"] is True
    assert len(result["steps"]) >= 1


# ---------------------------------------------------------------------------
# Wrapper inventory
# ---------------------------------------------------------------------------
def test_all_ten_wrappers_exist():
    for name in ("feature", "bug", "chore", "test", "review", "commit",
                 "status", "monitor", "maintain", "secure", "deploy"):
        p = BASE_DIR / "tools" / "anvil" / f"{name}.py"
        assert p.exists(), f"missing wrapper: {name}"


def test_wrappers_compile():
    import py_compile
    for name in ("feature", "bug", "chore", "test", "review", "commit",
                 "status", "monitor", "maintain", "secure", "deploy"):
        p = BASE_DIR / "tools" / "anvil" / f"{name}.py"
        py_compile.compile(str(p), doraise=True)
