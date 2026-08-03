"""ars-scope-01 — path/tool scoping declared in SKILL.md frontmatter.

Three invariants, one test group each:

  1. A skill declaring ``paths:``/``tools:`` cannot act outside them.
  2. A skill without those fields behaves exactly as it does today.
  3. A violation FAILS (command never runs, exit code 1) — it never warns.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.skills import invoke as inv  # noqa: E402
from tools.skills import registry as reg  # noqa: E402


def _entry(name="fixture-skill", commands=None, paths=None, tools=None):
    """A registry entry with no `path`, so scope comes from the entry itself."""
    return {
        "name": name,
        "description": "fixture",
        "allowed_tools": [],
        "paths": paths or [],
        "tools": tools or [],
        "commands": commands or [],
        "mcp_references": [],
    }


def _registry(entry):
    return {"skills": {entry["name"]: entry}, "count": 1,
            "schema_version": reg.SCHEMA_VERSION}


# ---------------------------------------------------------------------------
# Frontmatter parsing — both fields optional, both YAML list styles
# ---------------------------------------------------------------------------
def test_frontmatter_block_list():
    text = ("---\nname: s\npaths:\n  - tools/security/\n  - docs/security/\n"
            "tools:\n  - tools/security/sast_runner.py\n---\n\nbody\n")
    fm, body = reg._parse_frontmatter(text)
    assert reg._as_list(fm["paths"]) == ["tools/security/", "docs/security/"]
    assert reg._as_list(fm["tools"]) == ["tools/security/sast_runner.py"]
    assert body.strip() == "body"


def test_frontmatter_inline_and_comma_lists():
    fm, _ = reg._parse_frontmatter("---\nname: s\npaths: [a/, b/]\ntools: c/, d/\n---\n\nx\n")
    assert reg._as_list(fm["paths"]) == ["a/", "b/"]
    assert reg._as_list(fm["tools"]) == ["c/", "d/"]


def test_frontmatter_scalars_unchanged_by_list_support():
    """A description with commas/colons must stay a plain string."""
    fm, _ = reg._parse_frontmatter(
        '---\nname: s\ndescription: "Does a, b and c. Use when x."\n'
        "allowed-tools: Bash, Read\n---\n\nx\n")
    assert fm["description"] == "Does a, b and c. Use when x."
    assert reg._as_list(fm["allowed-tools"]) == ["Bash", "Read"]


def test_every_shipped_skill_still_parses():
    r = reg.build_registry()
    assert r["count"] >= 20
    for name, entry in r["skills"].items():
        assert "error" not in entry, name
        assert isinstance(entry["paths"], list)
        assert isinstance(entry["tools"], list)


def test_registry_cache_with_old_schema_is_rebuilt(tmp_path, monkeypatch):
    """A committed registry.json predating the scope fields must not be trusted."""
    stale = tmp_path / "registry.json"
    stale.write_text('{"skills": {}, "count": 0}', encoding="utf-8")
    monkeypatch.setattr(reg, "REGISTRY_PATH", stale)
    loaded = reg.load_registry()
    assert loaded["schema_version"] == reg.SCHEMA_VERSION
    assert loaded["count"] >= 20, "stale cache was served instead of rebuilt"


# ---------------------------------------------------------------------------
# 1. Scoped skills cannot act outside their declaration
# ---------------------------------------------------------------------------
def test_tools_scope_allows_declared_module():
    scope = {"paths": [], "tools": ["tools/security/"]}
    assert inv.check_scope(scope, "python tools/security/sast_runner.py --json") == []


@pytest.mark.parametrize("cmd", [
    "python tools/db/storage.py --health",
    "python -m tools.db.storage --health",
    "python -c \"from tools.db.storage import get_connection; get_connection()\"",
])
def test_tools_scope_blocks_undeclared_module(cmd):
    scope = {"paths": [], "tools": ["tools/security/"]}
    v = inv.check_scope(scope, cmd)
    assert v and v[0]["field"] == "tools"


def test_tools_scope_accepts_dotted_and_exact_declarations():
    assert inv.check_scope({"tools": ["tools.security"]},
                           "python tools/security/sast_runner.py") == []
    assert inv.check_scope({"tools": ["tools/security/sast_runner.py"]},
                           "python -m tools.security.sast_runner") == []


def test_tools_scope_fails_closed_on_unresolvable_target():
    v = inv.check_scope({"tools": ["tools/security/"]}, "cat args/security_gates.yaml")
    assert v and v[0]["value"] == "<unresolved>"


def test_paths_scope_allows_operand_inside():
    scope = {"paths": ["tools/security/"], "tools": []}
    assert inv.check_scope(scope, "python tools/foo.py --scan tools/security/sast_runner.py") == []


@pytest.mark.parametrize("operand", [
    "docs/reference/commands.md",          # elsewhere in the repo
    "../../etc/passwd",                    # traversal out of the repo
    "C:/Windows/System32",                 # absolute, off-tree
    "/etc/shadow",
])
def test_paths_scope_blocks_operand_outside(operand):
    scope = {"paths": ["tools/security/"], "tools": []}
    v = inv.check_scope(scope, f"python tools/foo.py --scan {operand}")
    assert v and v[0]["field"] == "paths"


def test_paths_scope_checks_equals_form_and_c_literals():
    scope = {"paths": ["tools/security/"], "tools": []}
    assert inv.check_scope(scope, "python tools/foo.py --scan=docs/x.md")
    assert inv.check_scope(scope, "python -c \"open('docs/x.md')\"")


def test_paths_scope_ignores_non_path_flags_and_values():
    scope = {"paths": ["tools/security/"], "tools": []}
    assert inv.check_scope(scope, "python tools/foo.py --json --format markdown") == []
    assert inv.check_scope(scope, "python tools/foo.py --url https://example.com/x") == []


def test_paths_scope_does_not_require_the_script_itself():
    """`paths:` scopes what a skill acts ON, not the tool it acts WITH."""
    scope = {"paths": ["docs/"], "tools": []}
    assert inv.check_scope(scope, "python tools/foo.py docs/reference/commands.md") == []


def test_user_supplied_arguments_cannot_escape_scope(monkeypatch):
    """$ARGUMENTS is substituted before the check, so callers can't widen it."""
    entry = _entry(commands=["python tools/security/sast_runner.py $ARGUMENTS"],
                   paths=["tools/security/"])
    monkeypatch.setattr(inv, "load_registry", lambda *a, **k: _registry(entry))
    ok = inv.invoke_skill("fixture-skill", ["--project-dir", "tools/security/"], dry_run=True)
    assert ok["blocked_count"] == 0
    bad = inv.invoke_skill("fixture-skill", ["--project-dir", "../../etc"], dry_run=True)
    assert bad["blocked_count"] == 1


def test_scope_narrows_never_widens(monkeypatch):
    """Declaring a tool does not lift the command-prefix allowlist."""
    entry = _entry(commands=["curl https://example.com"], tools=["tools/"])
    monkeypatch.setattr(inv, "load_registry", lambda *a, **k: _registry(entry))
    result = inv.run_command("curl https://example.com", [],
                             scope={"paths": [], "tools": ["tools/", "curl"]})
    assert result.get("skipped") or result.get("blocked")
    assert "returncode" not in result


def test_live_skill_card_wins_over_stale_cache(tmp_path):
    """A cached entry must not be able to under-report a card's scope."""
    card = tmp_path / ".agents" / "skills" / "s" / "SKILL.md"
    card.parent.mkdir(parents=True)
    card.write_text("---\nname: s\npaths:\n  - tools/security/\n---\n\nbody\n",
                    encoding="utf-8")
    entry = {"name": "s", "path": ".agents/skills/s/SKILL.md", "paths": [], "tools": []}
    assert inv.resolve_scope(entry, root=tmp_path)["paths"] == ["tools/security/"]


def test_cached_scope_used_when_card_is_missing():
    """Fail closed: a skill recorded as scoped stays scoped."""
    entry = {"name": "s", "path": ".agents/skills/does-not-exist/SKILL.md",
             "paths": ["tools/security/"], "tools": []}
    assert inv.resolve_scope(entry)["paths"] == ["tools/security/"]


# ---------------------------------------------------------------------------
# 2. Unscoped skills behave exactly as today
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("scope", [None, {}, inv.EMPTY_SCOPE,
                                   {"paths": [], "tools": []}])
def test_no_declaration_means_no_constraint(scope):
    assert inv.check_scope(scope, "python tools/anything.py /etc/passwd ../..") == []
    assert not inv.is_scoped(scope)


def test_unscoped_skill_dry_run_unchanged(monkeypatch):
    entry = _entry(commands=["python tools/db/storage.py --health",
                             "cat args/security_gates.yaml"])
    monkeypatch.setattr(inv, "load_registry", lambda *a, **k: _registry(entry))
    result = inv.invoke_skill("fixture-skill", [], dry_run=True)
    assert result["scoped"] is False
    assert result["blocked_count"] == 0
    assert [s["would_run"] for s in result["steps"]] == [True, False]


def test_shipped_skills_are_all_unscoped_today():
    """Every existing skill keeps today's behaviour — no field, no constraint."""
    r = reg.build_registry()
    scoped = [n for n, e in r["skills"].items() if e["paths"] or e["tools"]]
    assert scoped == [], f"unexpected scoped skills: {scoped}"


def test_unscoped_run_command_does_not_gain_a_check():
    result = inv.run_command("cat /etc/passwd", [])
    assert result["skipped"] is True
    assert "blocked" not in result


# ---------------------------------------------------------------------------
# 3. A violation fails, it does not warn
# ---------------------------------------------------------------------------
def test_violation_blocks_execution_and_stops_the_run(monkeypatch):
    entry = _entry(commands=["python tools/db/storage.py --health",
                             "python tools/security/sast_runner.py --json"],
                   tools=["tools/security/"])
    monkeypatch.setattr(inv, "load_registry", lambda *a, **k: _registry(entry))
    result = inv.invoke_skill("fixture-skill", [])
    assert result["blocked_count"] == 1
    assert result["executed_count"] == 0, "a blocked command must not execute"
    assert len(result["steps"]) == 1, "the run must stop at the violation"
    assert result["steps"][0]["blocked"] is True
    assert "scope violation" in result["steps"][0]["error"]


def test_violation_is_not_downgraded_by_keep_going(monkeypatch):
    entry = _entry(commands=["python tools/db/storage.py --health",
                             "python tools/security/sast_runner.py"],
                   tools=["tools/security/"])
    monkeypatch.setattr(inv, "load_registry", lambda *a, **k: _registry(entry))
    result = inv.invoke_skill("fixture-skill", [], keep_going=True)
    assert result["blocked_count"] == 1
    assert result["executed_count"] == 0


def test_run_command_blocks_before_spawning_a_process():
    result = inv.run_command("python tools/db/storage.py --health", [],
                             scope={"paths": [], "tools": ["tools/security/"]})
    assert result["blocked"] is True
    assert "returncode" not in result and "stdout" not in result


def test_cli_exit_code_is_nonzero_on_violation(monkeypatch, capsys):
    entry = _entry(commands=["python tools/db/storage.py --health"],
                   tools=["tools/security/"])
    monkeypatch.setattr(inv, "load_registry", lambda *a, **k: _registry(entry))
    assert inv.main(["--dry-run", "fixture-skill"]) == 1
    assert "BLOCKED" in capsys.readouterr().out
    assert inv.main(["--exec", "fixture-skill"]) == 1


def test_dry_run_reports_the_violation_without_running(monkeypatch):
    entry = _entry(commands=["python tools/foo.py ../../etc/passwd"],
                   paths=["tools/security/"])
    monkeypatch.setattr(inv, "load_registry", lambda *a, **k: _registry(entry))
    result = inv.invoke_skill("fixture-skill", [], dry_run=True)
    assert result["blocked_count"] == 1
    assert result["steps"][0]["would_run"] is False
    assert result["steps"][0]["violations"][0]["field"] == "paths"
