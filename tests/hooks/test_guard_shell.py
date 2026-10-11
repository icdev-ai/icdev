# CUI // SP-CTI
"""omx-guard-04 -- the guarded shell (L2) refuses what the hook refuses, and
changes nothing else.

``tools/hooks/guard_shell.py`` is what a spawned harness's ``bash -c <cmd>``
lands on when the runner has put the shims in front of ``$SHELL`` / ``PATH``.
These tests pin: a benign command comes out byte-identical (argv, exit code,
stdout, stderr) to running the real shell directly; a known-bad one is refused
with ``BLOCKED_EXIT`` and the BLOCKED reason, and the real shell never runs it;
``-c`` / ``-lc`` / a script / stdin are all read; the kill switches apply; a
broken bridge fails open and says so; the runner wires it (or logs why not);
and ``capability_matrix`` measures ``guard_shell`` per adapter.

No test EXECUTES a destructive command: the known-bad commands that reach a
real shell are a ``.env`` read in an empty directory and a ``psql`` UPDATE
(neither can do harm if the guard ever failed), and the root ``rm`` is only
ever DECIDED, never run.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tools.airgap import hook_compat
from tools.hooks import guard_shell as gs
from tools.hooks import harness_guard as hg

REAL_BASH = shutil.which("bash")
# Spelled in pieces so this file is not itself a dangerous command to a scanner.
RM_ROOT = "rm -rf " + "/"


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(hook_compat, "store_event", lambda *a, **k: 1)
    for env in list(hg.kill_switches().values()) + [hg.ENFORCEMENT_ENV, gs.LOG_ENV]:
        monkeypatch.delenv(env, raising=False)


@pytest.fixture
def shim(tmp_path):
    """Run the shim exactly as an installed one does: the generated entry script."""
    assert REAL_BASH, "bash is required (Git Bash on Windows)"
    installed = gs.install_shims(tmp_path / "shims")
    entry = Path(installed["dir"]) / "guard_shell_entry.py"
    work = tmp_path / "work"
    work.mkdir()
    env = {k: v for k, v in os.environ.items()
           if k not in hg.kill_switches().values() and k != hg.ENFORCEMENT_ENV}
    env.update({"ICDEV_STORAGE_BACKEND": "sqlite",
                "ICDEV_DB_PATH": str(tmp_path / "absent.db"),
                gs.REAL_ENV_PREFIX + "BASH": REAL_BASH})

    def run(args, stdin=None, direct=False, extra_env=None):
        argv = [REAL_BASH, *args] if direct else [sys.executable, str(entry), "path", "bash", *args]
        return subprocess.run(argv, input=stdin, capture_output=True, cwd=str(work),
                              env={**env, **(extra_env or {})}, timeout=120)
    run.work = work
    return run


# ── argv -> command text ─────────────────────────────────────────────────────

@pytest.mark.parametrize("args,mode,text", [
    (["-c", "x", "a0", "a1"], "c", "x"),
    (["-lc", "x"], "c", "x"),
    (["-ic", "x"], "c", "x"),
    (["-euc", "x"], "c", "x"),
    (["--login", "-c", "x"], "c", "x"),
    (["-o", "pipefail", "-c", "x"], "c", "x"),
    (["-eo", "pipefail", "-c", "x"], "c", "x"),
    (["--rcfile", "f", "-c", "x"], "c", "x"),
    (["-c", "--", "x"], "c", "x"),
    (["script.sh", "a"], "script", None),
    (["-e", "--", "script.sh"], "script", None),
    (["-s", "a"], "stdin", None),
    ([], "stdin", None),
])
def test_parse_invocation(args, mode, text):
    inv = gs.parse_invocation(args)
    assert inv.mode == mode
    assert inv.text == text


def test_the_bridge_is_the_one_decision():
    assert gs.decide_text(RM_ROOT)["allowed"] is False
    assert gs.decide_text("cat .env")["allowed"] is False
    assert gs.decide_text("git status")["allowed"] is True
    # Claude Code wraps every command; the eval'd text is still refused.
    wrapped = ("source ~/.claude/shell-snapshots/s.sh 2>/dev/null || true && "
               f"eval '{RM_ROOT}' < /dev/null && pwd -P >| /tmp/cwd")
    assert gs.decide_text(wrapped)["allowed"] is False


#: Claude Code's wrapper, verbatim from the 2026-10-11 omarchy run in which the
#: shim ALLOWED it: the `.env` check reads `'cat .env'` as one quoted token.
CLAUDE_WRAPPED_ENV_READ = (
    "source /home/icdev/.claude/shell-snapshots/snapshot-bash-1.sh 2>/dev/null "
    "|| true && shopt -u extglob 2>/dev/null || true && eval 'cat .env' "
    "< /dev/null && pwd -P >| /tmp/claude-a413-cwd"
)


def test_an_eval_payload_is_checked_as_the_shell_runs_it():
    assert gs.eval_payloads(CLAUDE_WRAPPED_ENV_READ)[0].startswith("cat .env")
    assert gs.decide_text(CLAUDE_WRAPPED_ENV_READ)["allowed"] is False
    nested = "eval \"eval 'cat .env'\""
    assert gs.decide_text(nested)["allowed"] is False
    benign = CLAUDE_WRAPPED_ENV_READ.replace("cat .env", "git status")
    assert gs.decide_text(benign)["allowed"] is True


def test_claude_wrapped_env_read_is_blocked_end_to_end(shim):
    proc = shim(["-c", CLAUDE_WRAPPED_ENV_READ.replace("cat .env", "cat .env; touch RAN")])
    assert proc.returncode == gs.BLOCKED_EXIT
    assert not (shim.work / "RAN").exists()


# ── benign commands are untouched ───────────────────────────────────────────

@pytest.mark.parametrize("args", [
    ["-c", 'printf "%s|" "$0" "$@"; echo; exit 3', "zero", "a b", "c'd", '"e"'],
    ["-lc", "echo 'single' \"double\" $((6*7)) && echo tab\there"],
    ["-c", "printf 'unicode: é中\\n'; echo err >&2; exit 0"],
    ["-c", "echo line1\necho line2"],
], ids=["argv+exit", "login+quotes", "unicode+stderr", "multiline"])
def test_benign_command_is_byte_identical(shim, args):
    via_shim, direct = shim(args), shim(args, direct=True)
    assert via_shim.returncode == direct.returncode
    assert via_shim.stdout == direct.stdout
    assert via_shim.stderr == direct.stderr


def test_benign_script_and_stdin_are_byte_identical(shim):
    script = shim.work / "ok.sh"
    script.write_bytes(b"echo from-script \"$1\"\nexit 4\n")
    assert shim([str(script), "arg"]).stdout == shim([str(script), "arg"], direct=True).stdout
    assert shim([str(script), "arg"]).returncode == 4
    stdin = b"echo one\necho two \\\n  three\n"
    via_shim, direct = shim([], stdin=stdin), shim([], stdin=stdin, direct=True)
    assert (via_shim.stdout, via_shim.returncode) == (direct.stdout, direct.returncode)
    assert via_shim.stdout == b"one\ntwo three\n"


# ── known-bad commands are refused, and never reach the real shell ──────────

@pytest.mark.parametrize("args", [
    ["-c", "cat .env; touch RAN"],
    ["-lc", "cat .env; touch RAN"],
    ["-ic", "cat .env; touch RAN"],
    ["-c", 'psql -c "UPDATE audit_trail SET x=1"; touch RAN'],
], ids=["-c", "-lc", "-ic", "append-only"])
def test_known_bad_command_is_blocked(shim, args):
    proc = shim(args)
    assert proc.returncode == gs.BLOCKED_EXIT
    assert b"ICDEV guard: BLOCKED" in proc.stderr
    assert not (shim.work / "RAN").exists(), "the real shell ran a refused command"


def test_known_bad_script_is_blocked(shim):
    script = shim.work / "bad.sh"
    script.write_text("cat .env\ntouch RAN\n", encoding="utf-8")
    proc = shim([str(script)])
    assert proc.returncode == gs.BLOCKED_EXIT
    assert not (shim.work / "RAN").exists()


def test_stdin_drops_the_refused_line_and_keeps_going(shim):
    proc = shim([], stdin=b"echo before\ncat .env && touch RAN\necho after\n")
    assert proc.returncode == gs.BLOCKED_EXIT
    assert proc.stdout == b"before\nafter\n"
    assert b"BLOCKED" in proc.stderr
    assert not (shim.work / "RAN").exists()


def test_a_continuation_cannot_split_a_command_across_two_checks(shim):
    proc = shim([], stdin=b"cat \\\n.env; touch RAN\n")
    assert proc.returncode == gs.BLOCKED_EXIT
    assert not (shim.work / "RAN").exists()


def test_a_continuation_inside_c_is_checked_as_the_shell_reads_it(shim):
    proc = shim(["-c", "cat \\\n.env; touch RAN"])
    assert proc.returncode == gs.BLOCKED_EXIT
    assert not (shim.work / "RAN").exists()


# ── switches and failure modes ──────────────────────────────────────────────

def test_enforce_off_is_advisory(shim):
    proc = shim(["-c", "cat .env; touch RAN"],
                extra_env={hg.ENFORCEMENT_ENV: "0"})
    assert proc.returncode != gs.BLOCKED_EXIT
    assert b"ADVISORY" in proc.stderr
    assert (shim.work / "RAN").exists()


def test_per_check_kill_switch_is_the_hooks_own(shim):
    switch = hg.kill_switches()["append_only_write"]
    proc = shim(["-c", 'psql -c "UPDATE audit_trail SET x=1"; touch RAN'],
                extra_env={switch: "0"})
    assert proc.returncode != gs.BLOCKED_EXIT
    assert (shim.work / "RAN").exists()


def test_broken_bridge_fails_open_and_says_so(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("bridge down")
    monkeypatch.setattr(hg, "decide", boom)
    verdict = gs.decide_text("cat .env")
    assert verdict["allowed"] is True
    assert "failed open" in verdict["reason"]


def test_real_shell_never_resolves_to_the_shim(tmp_path):
    installed = gs.install_shims(tmp_path / "shims")
    env = {"PATH": installed["dir"] + os.pathsep + os.environ.get("PATH", ""),
           gs.SHIM_DIR_ENV: installed["dir"]}
    real = gs.real_shell("bash", env)
    assert real and not Path(real).resolve().is_relative_to(Path(installed["dir"]).resolve())


# ── wiring: the runner's spawn env ──────────────────────────────────────────

def test_wiring_is_supported_on_posix_and_explicitly_not_on_windows(tmp_path):
    wired = gs.wiring(shim_dir=tmp_path / "shims")
    if os.name == "nt":
        assert wired == {"supported": False, "reason": gs.UNSUPPORTED_WINDOWS, "env": {}}
        return
    assert wired["supported"] is True
    env = {**os.environ, **wired["env"]}
    assert env["PATH"].split(os.pathsep)[0] == str(tmp_path / "shims")
    assert Path(env["SHELL"]).parent == tmp_path / "shims" / "login"
    for resolves in (gs.VIA_SHELL, gs.VIA_PATH):
        report = gs.self_test({**env, "ICDEV_STORAGE_BACKEND": "sqlite",
                               "ICDEV_DB_PATH": str(tmp_path / "absent.db")}, resolves)
        assert report["wired"] is True, report["reason"]


def test_runner_session_carries_the_wiring(monkeypatch):
    from tools.genesis.reflexes import kanban

    monkeypatch.setattr(kanban, "_selected_model", lambda: None)
    monkeypatch.setattr(kanban, "_get_task_timeout", lambda tid: 60)
    fake_env = {"SHELL": "/shims/login/bash", gs.SHIM_DIR_ENV: "/shims"}
    monkeypatch.setattr(gs, "wiring", lambda: {"supported": True, "reason": "", "env": fake_env})
    session = kanban._agent_session({"id": "omx-guard-04"}, "x", ".")
    assert session.metadata["env"] == fake_env


def test_runner_logs_when_the_wiring_is_unsupported(monkeypatch):
    from tools.genesis.reflexes import kanban

    warnings = []
    monkeypatch.setattr(kanban, "_selected_model", lambda: None)
    monkeypatch.setattr(kanban, "_get_task_timeout", lambda tid: 60)
    monkeypatch.setattr(kanban.logger, "warning", lambda msg, *a: warnings.append(msg % a))
    monkeypatch.setattr(gs, "wiring", lambda: {"supported": False, "env": {},
                                               "reason": gs.UNSUPPORTED_WINDOWS})
    session = kanban._agent_session({"id": "omx-guard-04"}, "x", ".")
    assert "env" not in session.metadata
    assert any("NOT wired" in w and "unsupported on Windows" in w for w in warnings)


# ── the guard_shell capability ──────────────────────────────────────────────

def _cell(adapter):
    from tools.agents import capability_matrix as cm

    return cm.probe_adapter(adapter, only=["guard_shell"])["capabilities"]["guard_shell"]


def test_measured_absolute_shell_is_absent():
    for adapter in ("codex_cli", "pi_cli"):
        cell = _cell(adapter)
        assert cell["actual"] == "absent", cell
        assert "absolute path" in cell["evidence"]
        assert cell["verdict"] == "agreed_absent"


def test_unmeasured_harness_is_never_present():
    cell = _cell("copilot_cli")
    assert cell["actual"] == "unconfirmed"
    assert cell["verdict"] != "overclaimed"


def test_shell_env_harness_is_probed_through_the_adapter_env(monkeypatch, tmp_path):
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(tmp_path / "absent.db"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    for adapter in ("claude_cli", "opencode_cli"):
        cell = _cell(adapter)
        if os.name == "nt":
            assert cell["actual"] == "unconfirmed"
            assert "unsupported on Windows" in cell["evidence"]
        else:
            assert cell["actual"] == "present", cell
            assert cell["verdict"] == "confirmed"


def test_no_adapter_overclaims_guard_shell():
    from tools.agents import capability_matrix as cm

    matrix = cm.build_matrix(only=["guard_shell"])
    over = [name for name, entry in matrix["adapters"].items()
            if entry["capabilities"]["guard_shell"]["verdict"] == cm.OVERCLAIMED]
    assert over == []


def test_resolution_record_uses_known_values():
    for name, row in gs.load_resolution().items():
        assert row.get("resolves") in gs.RESOLUTIONS, name
