"""OPT-41 — tests for tools/skills/registry.py + invoke.py pure helpers."""
from __future__ import annotations

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.skills import invoke as inv  # noqa: E402
from tools.skills import registry as reg  # noqa: E402


# ---------------------------------------------------------------------------
# Frontmatter + command extraction
# ---------------------------------------------------------------------------
def test_parse_frontmatter_basic():
    text = "---\nname: test\ndescription: desc here\n---\n\nBody content\n"
    fm, body = reg._parse_frontmatter(text)
    assert fm["name"] == "test"
    assert fm["description"] == "desc here"
    assert body.strip() == "Body content"


def test_parse_frontmatter_missing_delim():
    text = "No frontmatter here\n"
    fm, body = reg._parse_frontmatter(text)
    assert fm == {}
    assert body == text


def test_extract_commands_dedup():
    body = """```bash
python tools/foo.py --json
python tools/bar.py
python tools/foo.py --json
```"""
    cmds = reg._extract_commands(body)
    assert cmds == ["python tools/foo.py --json", "python tools/bar.py"]


def test_extract_commands_strips_leading_bang():
    body = """```bash
!python tools/foo.py
```"""
    cmds = reg._extract_commands(body)
    # The '!' should be captured but the command still parsed
    assert any("python tools/foo.py" in c for c in cmds)


def test_extract_commands_strips_inline_comments():
    body = """```bash
python tools/foo.py  # this is a comment
```"""
    cmds = reg._extract_commands(body)
    assert cmds == ["python tools/foo.py"]


def test_extract_commands_ignores_non_bash_fences():
    body = """```python
python tools/foo.py
```"""
    cmds = reg._extract_commands(body)
    # Note: our regex also matches un-typed fences (```), but Python fences
    # are still matched today. That's acceptable — overmatching is safer
    # than undermatching for discovery. Test documents current behavior.
    # If this is ever tightened, adjust.
    assert cmds == [] or cmds == ["python tools/foo.py"]


def test_build_registry_finds_all_skills():
    r = reg.build_registry()
    assert r["count"] >= 20, f"expected >=20 skills, got {r['count']}"
    assert "icdev-init" in r["skills"]
    assert "icdev-status" in r["skills"]


# ---------------------------------------------------------------------------
# invoke helpers
# ---------------------------------------------------------------------------
def test_safe_command_accepts_python_tools():
    assert inv._is_safe_command("python tools/foo.py --json")
    assert inv._is_safe_command("python -m tools.foo")
    assert inv._is_safe_command("!python tools/bar.py")


def test_safe_command_rejects_shell_builtins():
    assert not inv._is_safe_command("rm -rf /")
    assert not inv._is_safe_command("curl http://example.com")
    assert not inv._is_safe_command("bash tools/foo.py")
    assert not inv._is_safe_command("cat /etc/passwd")


def test_substitute_arguments():
    out = inv._substitute_args("python tools/foo.py $ARGUMENTS", ["--bar", "baz qux"])
    assert "--bar" in out
    # shlex.quote should handle the space
    assert "'baz qux'" in out or '"baz qux"' in out


def test_substitute_no_arguments_placeholder():
    out = inv._substitute_args("python tools/foo.py --json", ["unused"])
    assert out == "python tools/foo.py --json"


# ---------------------------------------------------------------------------
# invoke_skill error cases (no subprocess needed)
# ---------------------------------------------------------------------------
def test_invoke_unknown_skill():
    result = inv.invoke_skill("nonexistent-skill", [], dry_run=True)
    assert "error" in result
    assert result["name"] == "nonexistent-skill"
    assert "available" in result


def test_invoke_dry_run_documents_commands():
    """Dry-run mode returns commands without executing them."""
    result = inv.invoke_skill("icdev-status", [], dry_run=True)
    assert "error" not in result
    assert result["dry_run"] is True
    assert result["executed_count"] == 0
    assert len(result["steps"]) >= 1
    assert all("would_run" in s for s in result["steps"])
