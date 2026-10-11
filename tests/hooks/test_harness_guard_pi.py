# CUI // SP-CTI
"""omx-guard-03 -- Pi's tool calls reach the SAME harness_guard bridge.

``icdev/data/harness_plugins/pi/icdev-guard.ts`` hands every Pi ``tool_call``
to ``python -m tools.hooks.harness_guard --harness pi``. The bridge owns no
check; these tests pin that a call in PI's spelling (``path``, ``edits[]``, a
separate ``powershell`` tool -- omx-spike-01 findings, "Pi") is refused by the
shared checks, that ordinary work is allowed, and that the installer and the
pi_cli adapter wire the extension in.
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
SQLITE_CONNECT = "sqlite3." + "connect('data/icdev.db')"

DENY_FIXTURES = [
    ("dangerous rm", "bash", {"command": RM_ROOT, "timeout": 30}),
    ("rm through the powershell tool", "powershell", {"command": RM_ROOT}),
    ("env file read", "read", {"path": ".env", "offset": 1, "limit": 10}),
    ("env file write", "write", {"path": "/repo/.env", "content": "A=2"}),
    ("env file edit", "edit",
     {"path": "/repo/.env", "edits": [{"oldText": "A=1", "newText": "A=2"}]}),
    ("append-only UPDATE", "bash",
     {"command": 'psql -c "UPDATE audit_trail SET x=1"'}),
    # The offending text is in the SECOND edit: every edit must be checked.
    ("sqlite in a later edit", "edit",
     {"path": "tools/foo/bar.py", "edits": [
         {"oldText": "a = 1", "newText": "a = 2"},
         {"oldText": "conn = None", "newText": f"conn = {SQLITE_CONNECT}"}]}),
]

ALLOW_FIXTURES = [
    ("git status", "bash", {"command": "git status"}),
    ("read a readme", "read", {"path": "README.md"}),
    ("ls", "ls", {"path": "."}),
]


@pytest.fixture(autouse=True)
def no_audit(monkeypatch):
    monkeypatch.setattr(hook_compat, "store_event", lambda *a, **k: 1)
    for env in list(hg.kill_switches().values()) + [hg.ENFORCEMENT_ENV]:
        monkeypatch.delenv(env, raising=False)


@pytest.mark.parametrize("name,tool,args", DENY_FIXTURES,
                         ids=[f[0] for f in DENY_FIXTURES])
def test_bridge_denies_known_bad_in_pi_spelling(name, tool, args):
    verdict = hg.decide("pi", tool, args)
    assert verdict["allowed"] is False, f"{name} was ALLOWED: {verdict}"
    assert verdict["reason"].startswith("BLOCKED")
    assert verdict["advisory"] is False


@pytest.mark.parametrize("name,tool,args", ALLOW_FIXTURES,
                         ids=[f[0] for f in ALLOW_FIXTURES])
def test_bridge_allows_benign_pi_calls(name, tool, args):
    verdict = hg.decide("pi", tool, args)
    assert verdict["allowed"] is True, f"{name}: {verdict['reason']}"


def test_pi_mapping_is_load_bearing():
    """Pi's ``path`` unmapped is a key no file check reads."""
    assert hook_compat.run_pre_tool_check("Read", {"path": ".env"})["allowed"]
    assert hg.to_icdev("pi", "read", {"path": ".env"}) == ("Read", {"file_path": ".env"})
    assert hg.to_icdev("pi", "powershell", {"command": "x"}) == ("Bash", {"command": "x"})
    name, mapped = hg.to_icdev("pi", "edit", {"path": "a", "edits": [
        {"oldText": "o1", "newText": "n1"}, {"oldText": "o2", "newText": "n2"}]})
    assert name == "Edit" and mapped["file_path"] == "a"
    assert mapped["old_string"] == "o1\no2" and mapped["new_string"] == "n1\nn2"


def test_no_check_logic_in_the_bridge_or_extension():
    """The bridge maps and delegates; the extension spawns the bridge."""
    import ast

    bridge = (REPO / "tools" / "hooks" / "harness_guard.py").read_text(encoding="utf-8")
    assert "from tools.airgap.hook_compat import run_pre_tool_check" in bridge
    tree = ast.parse(bridge)
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
                for a in n.names} | {n.module for n in ast.walk(tree)
                                     if isinstance(n, ast.ImportFrom)}
    assert "shared_checks" not in imported and "tools.hooks.shared_checks" not in imported
    assert not [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                and n.name.startswith(("check_", "is_"))]
    ext = (REPO / "icdev" / "data" / "harness_plugins" / "pi" / "icdev-guard.ts"
           ).read_text(encoding="utf-8")
    assert '"tools.hooks.harness_guard", "--harness", "pi"' in ext
    assert 'pi.on("tool_call"' in ext and "block: true" in ext


def test_kill_switch_applies_to_pi(monkeypatch):
    monkeypatch.setenv("ICDEV_DANGEROUS_RM_GUARD", "0")
    assert hg.decide("pi", "bash", {"command": RM_ROOT})["allowed"] is True
    assert hg.decide("pi", "read", {"path": ".env"})["allowed"] is False


def test_enforcement_off_is_advisory_for_pi(monkeypatch):
    monkeypatch.setenv(hg.ENFORCEMENT_ENV, "0")
    verdict = hg.decide("pi", "bash", {"command": RM_ROOT})
    assert verdict["allowed"] is True and verdict["advisory"] is True


def test_broken_guard_fails_open_for_pi(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(hook_compat, "run_pre_tool_check", boom)
    verdict = hg.decide("pi", "bash", {"command": RM_ROOT})
    assert verdict["allowed"] is True and "failed open" in verdict["reason"]


def test_cli_round_trip_in_pi_spelling():
    """The exact process the extension spawns: stdin JSON in, one JSON line out."""
    env = {**os.environ, "ICDEV_STORAGE_BACKEND": "sqlite", "PYTHONPATH": str(REPO)}
    for env_key in list(hg.kill_switches().values()) + [hg.ENFORCEMENT_ENV]:
        env.pop(env_key, None)

    def run(payload):
        proc = subprocess.run(
            [sys.executable, "-m", "tools.hooks.harness_guard", "--harness", "pi"],
            input=json.dumps(payload), capture_output=True, text=True,
            encoding="utf-8", cwd=str(REPO), env=env, timeout=120,
        )
        assert proc.returncode == 0, proc.stderr
        return json.loads(proc.stdout.strip().splitlines()[-1])

    denied = run({"tool": "bash", "args": {"command": RM_ROOT}})
    assert denied["allowed"] is False and denied["tool_name"] == "Bash"
    assert run({"tool": "read", "args": {"path": ".env"}})["allowed"] is False
    assert run({"tool": "bash", "args": {"command": "git status"}})["allowed"] is True


def test_install_guard_pi_project_and_global(tmp_path, monkeypatch, capsys):
    from tools.cli.harness import main as harness_main

    proj = tmp_path / "proj"
    assert harness_main(["install-guard", "pi", "--project", str(proj), "--json"]) == 0
    first = json.loads(capsys.readouterr().out)
    ext = proj / ".pi" / "extensions" / "icdev-guard.ts"
    assert first["changed"] is True and Path(first["path"]) == ext
    text = ext.read_text(encoding="utf-8")
    assert 'pi.on("tool_call"' in text and "__ICDEV_" not in text
    assert json.dumps(sys.executable) in text

    assert harness_main(["install-guard", "pi", "--project", str(proj), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["changed"] is False

    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path / "agent"))
    assert harness_main(["install-guard", "pi", "--global", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert Path(out["path"]) == tmp_path / "agent" / "extensions" / "icdev-guard.ts"


def test_pi_adapter_invoke_installs_and_passes_the_guard(tmp_path, monkeypatch):
    from tools.agents.adapter_base import AgentSession
    from tools.agents.adapters import pi_cli as pc

    monkeypatch.setattr(pc, "resolve_pi_cli", lambda: "pi")
    seen = {}

    def _run(argv, **kw):
        seen["argv"] = argv
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(pc.subprocess, "run", _run)
    work = tmp_path / "work"
    work.mkdir()
    pc.ADAPTER.invoke(AgentSession(task_id="omx-guard-03", prompt="p",
                                   working_dir=str(work)))
    ext = work / ".pi" / "extensions" / "icdev-guard.ts"
    assert ext.is_file()
    argv = seen["argv"]
    assert argv[argv.index("-e") + 1] == str(ext) and argv[-1] == "p"


def test_pi_adapter_refuses_to_run_unguarded(tmp_path, monkeypatch):
    from tools.agents.adapter_base import AgentSession
    from tools.agents.adapters import pi_cli as pc

    def _broken(*a, **k):
        raise OSError("read-only")

    monkeypatch.setattr(pc, "install_guard", _broken)
    monkeypatch.setattr(pc.subprocess, "run",
                        lambda *a, **k: pytest.fail("ran Pi unguarded"))
    result = pc.ADAPTER.invoke(AgentSession(task_id="omx-guard-03", prompt="p",
                                            working_dir=str(tmp_path)))
    assert result.completed is False and "unguarded" in result.error


def test_capability_matrix_reports_pi_guard_present():
    from tools.agents import capability_matrix as cm

    cm.reset_cache()
    cell = cm.probe_adapter("pi_cli")["capabilities"]["guard_wired"]
    assert cell["declared"] is True
    assert cell["actual"] == cm.PRESENT, cell
    assert cell["verdict"] == cm.CONFIRMED
