# CUI // SP-CTI
"""omx-guard-01 -- the harness guard bridge refuses what the hook refuses.

``tools/hooks/harness_guard.py`` is what the opencode ``tool.execute.before``
plugin calls. It owns no check; these tests pin that a call in OPENCODE's
spelling (lowercase tool names, camelCase args) reaches the shared checks and
is refused, that ordinary work is allowed, that the hook's kill switches apply,
and that a broken bridge fails open and says so.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tools.airgap import hook_compat
from tools.hooks import harness_guard as hg

REPO = Path(__file__).resolve().parents[2]

# rm target spelled in pieces so this source file is not itself a dangerous
# command to any scanner reading it.
RM_ROOT = "rm -rf " + "/"

DENY_FIXTURES = [
    ("dangerous rm", "bash", {"command": RM_ROOT}),
    ("env file read", "read", {"filePath": ".env"}),
    ("append-only UPDATE", "bash",
     {"command": 'psql -c "UPDATE audit_trail SET x=1"'}),
    ("env file edit", "edit",
     {"filePath": "/repo/.env", "oldString": "A=1", "newString": "A=2"}),
]


@pytest.fixture(autouse=True)
def no_audit(monkeypatch):
    monkeypatch.setattr(hook_compat, "store_event", lambda *a, **k: 1)
    for env in list(hg.kill_switches().values()) + [hg.ENFORCEMENT_ENV]:
        monkeypatch.delenv(env, raising=False)


@pytest.mark.parametrize("name,tool,args", DENY_FIXTURES,
                         ids=[f[0] for f in DENY_FIXTURES])
def test_bridge_denies_known_bad_in_opencode_spelling(name, tool, args):
    verdict = hg.decide("opencode", tool, args)
    assert verdict["allowed"] is False, f"{name} was ALLOWED"
    assert verdict["reason"].startswith("BLOCKED")
    assert verdict["advisory"] is False


def test_bridge_allows_benign_command():
    verdict = hg.decide("opencode", "bash", {"command": "git status"})
    assert verdict["allowed"] is True, verdict["reason"]


def test_mapping_is_load_bearing():
    """Unmapped lowercase ``bash`` would sail past the Bash-only rm check."""
    assert hook_compat.run_pre_tool_check("bash", {"command": RM_ROOT})["allowed"]
    assert hg.to_icdev("opencode", "bash", {"command": "x"}) == ("Bash", {"command": "x"})
    assert hg.to_icdev("opencode", "edit", {"filePath": "a", "oldString": "b"}) == (
        "Edit", {"file_path": "a", "old_string": "b"})


def test_kill_switch_table_is_the_hooks_own():
    """Read from the hook, never copied: same keys and env names."""
    import importlib.util

    path = REPO / ".claude" / "hooks" / "pre_tool_use.py"
    spec = importlib.util.spec_from_file_location("hg_hook_under_test", path)
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)
    assert hg.kill_switches() == hook.CHECK_KILL_SWITCHES
    # every switchable check is one the headless path actually runs
    assert {f"check_{n}" for n in hook.CHECK_KILL_SWITCHES} <= set(
        hook_compat.HEADLESS_CHECKS)


def test_per_check_kill_switch_skips_only_that_check(monkeypatch):
    monkeypatch.setenv("ICDEV_DANGEROUS_RM_GUARD", "0")
    assert hg.decide("opencode", "bash", {"command": RM_ROOT})["allowed"] is True
    assert hg.decide("opencode", "read", {"filePath": ".env"})["allowed"] is False


def test_enforcement_off_is_advisory(monkeypatch):
    monkeypatch.setenv(hg.ENFORCEMENT_ENV, "0")
    verdict = hg.decide("opencode", "bash", {"command": RM_ROOT})
    assert verdict["allowed"] is True and verdict["advisory"] is True
    assert "BLOCKED" in verdict["reason"]


def test_broken_guard_fails_open_and_says_so(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(hook_compat, "run_pre_tool_check", boom)
    verdict = hg.decide("opencode", "bash", {"command": RM_ROOT})
    assert verdict["allowed"] is True
    assert "failed open" in verdict["reason"] and "db down" in verdict["reason"]


def test_cli_round_trip_denies_and_allows():
    """The exact process the plugin spawns: stdin JSON in, one JSON line out."""
    env = {**os.environ, "ICDEV_STORAGE_BACKEND": "sqlite", "PYTHONPATH": str(REPO)}
    for env_key in hg.kill_switches().values():
        env.pop(env_key, None)
    env.pop(hg.ENFORCEMENT_ENV, None)

    def run(payload):
        proc = subprocess.run(
            [sys.executable, "-m", "tools.hooks.harness_guard", "--harness", "opencode"],
            input=json.dumps(payload), capture_output=True, text=True,
            encoding="utf-8", cwd=str(REPO), env=env, timeout=120,
        )
        assert proc.returncode == 0, proc.stderr
        return json.loads(proc.stdout.strip().splitlines()[-1])

    assert run({"tool": "bash", "args": {"command": RM_ROOT}})["allowed"] is False
    assert run({"tool": "bash", "args": {"command": "git status"}})["allowed"] is True


def test_cli_unreadable_request_fails_open(capsys, monkeypatch):
    import io

    monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
    assert hg.main(["--harness", "opencode"]) == 0
    verdict = json.loads(capsys.readouterr().out)
    assert verdict["allowed"] is True and "failed open" in verdict["reason"]


def test_install_guard_cli_project_and_global(tmp_path, monkeypatch, capsys):
    from tools.cli.harness import main as harness_main

    proj = tmp_path / "proj"
    assert harness_main(["install-guard", "opencode", "--project", str(proj), "--json"]) == 0
    first = json.loads(capsys.readouterr().out)
    plugin = proj / ".opencode" / "plugin" / "icdev-guard.ts"
    assert first["changed"] is True and Path(first["path"]) == plugin
    text = plugin.read_text(encoding="utf-8")
    assert "tool.execute.before" in text and "__ICDEV_" not in text
    assert json.dumps(sys.executable) in text

    # idempotent
    assert harness_main(["install-guard", "opencode", "--project", str(proj), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["changed"] is False

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    assert harness_main(["install-guard", "opencode", "--global", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert Path(out["path"]) == tmp_path / "cfg" / "opencode" / "plugin" / "icdev-guard.ts"
