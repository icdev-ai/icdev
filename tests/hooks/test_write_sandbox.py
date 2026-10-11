# CUI // SP-CTI
"""omx-guard-05 -- the L3 write sandbox: an autonomous worker can only write
inside its task worktree (plus the harness's declared state dirs).

``tools/hooks/write_sandbox.py`` wraps every CLI adapter's command line in an
OS sandbox (bubblewrap, else Landlock via ``landlock_exec.py``) when the kanban
runner asks for one. These tests pin: the writable set IS the shared
write-boundary roots plus declared state (no second definition); on Linux a
real write inside the worktree and into a state dir lands and one outside does
not -- through the backend the host picks AND through Landlock explicitly;
Windows and other OSes report unsupported, explicitly; the kill switches are
the existing ones; a sandbox that cannot be built fails open and says so,
while a Landlock confinement that fails at exec time refuses to run; every CLI
adapter applies it; the runner asks for it (or logs why not); and
``capability_matrix`` measures ``sandboxed``.

The live writes happen under ``~/.cache/icdev/l3-test/<id>`` -- NOT under the
pytest tmp dir, which sits in a sanctioned temp root and so could never be
"outside". Everything written there is removed.
"""
from __future__ import annotations

import shutil
import sys
import uuid
from pathlib import Path

import pytest

from tools.hooks import harness_guard as hg
from tools.hooks import landlock_exec as ll
from tools.hooks import shared_checks
from tools.hooks import write_sandbox as ws

LINUX = sys.platform.startswith("linux")
CLI_ADAPTERS = ("claude_cli", "codex_cli", "copilot_cli", "goose_cli",
                "opencode_cli", "pi_cli")


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    for env in (shared_checks.WRITE_BOUNDARY_GUARD_ENV, hg.ENFORCEMENT_ENV,
                ws.BACKEND_ENV):
        monkeypatch.delenv(env, raising=False)
    ws.reset_cache()
    yield
    ws.reset_cache()


@pytest.fixture
def home_area(monkeypatch):
    """A worktree, a state dir and an outside target -- siblings outside every root."""
    base = Path.home() / ".cache" / "icdev" / "l3-test" / uuid.uuid4().hex[:10]
    worktree, state = base / "wt", base / "state"
    worktree.mkdir(parents=True)
    yaml_path = base / "write_sandbox.yaml"
    yaml_path.write_text(
        f"adapters:\n  fake_cli:\n    state_dirs:\n      - {state}\n", encoding="utf-8")
    monkeypatch.setattr(ws, "STATE_PATH", yaml_path)
    yield {"base": base, "worktree": worktree, "state": state,
           "outside": base / "wt-outside"}
    shutil.rmtree(base, ignore_errors=True)


# ── the writable set ─────────────────────────────────────────────────────────

def test_writable_set_is_the_shared_roots_plus_declared_state(monkeypatch, tmp_path):
    shared = [tmp_path / "wt", tmp_path / "main"]
    monkeypatch.setattr(shared_checks, "sanctioned_write_roots", lambda root: tuple(shared))
    state_dir, state_file = tmp_path / "home" / ".fake", tmp_path / "home" / ".fake.json"
    state_file.parent.mkdir()
    state_file.write_text("{}", encoding="utf-8")
    yaml_path = tmp_path / "ws.yaml"
    yaml_path.write_text(
        "adapters:\n  fake_cli:\n"
        f"    state_dirs: [{state_dir.as_posix()}]\n"
        f"    state_files: [{state_file.as_posix()}, {(tmp_path / 'absent.json').as_posix()}]\n",
        encoding="utf-8")
    roots = ws.writable_roots(str(tmp_path / "wt"), "fake_cli", yaml_path)
    assert roots == [str(p) for p in shared] + [str(state_dir.resolve()), str(state_file.resolve())]
    assert state_dir.is_dir(), "a declared state dir is created before the sandbox starts"


def test_unknown_adapter_gets_only_the_shared_roots(monkeypatch, tmp_path):
    monkeypatch.setattr(shared_checks, "sanctioned_write_roots", lambda root: (tmp_path,))
    assert ws.writable_roots(str(tmp_path), "no_such_adapter") == [str(tmp_path)]


def test_every_cli_adapter_declares_its_state():
    for name in CLI_ADAPTERS:
        assert ws.declared_state(name)["state_dirs"], name


def test_bwrap_prefix_binds_only_the_roots_and_keeps_the_network(monkeypatch):
    monkeypatch.setattr(ws, "writable_roots", lambda *a, **k: ["/w", "/tmp"])  # nosec B108 — fake roots for argv shape; nothing is opened
    argv = ws.prefix("/w", which=ws.BWRAP)
    assert argv[1:5] == ["--ro-bind", "/", "/", "--dev-bind"]
    assert argv[-1] == "--"
    assert ["--bind-try", "/w", "/w"] == argv[argv.index("/w") - 1:argv.index("/w") + 2]
    assert argv.count("--bind-try") == 2
    assert not any(a.startswith("--unshare") for a in argv), "the network stays open"


def test_landlock_prefix_is_the_isolated_helper(monkeypatch):
    monkeypatch.setattr(ws, "writable_roots", lambda *a, **k: ["/w"])
    argv = ws.prefix("/w", which=ws.LANDLOCK)
    assert argv[:3] == [sys.executable, "-I", str(ws.LANDLOCK_HELPER)]
    assert argv[3:] == ["--rw", "/w", "--rw", "/dev", "--"]


# ── per-OS reporting and kill switches ───────────────────────────────────────

def test_windows_is_explicitly_unsupported(monkeypatch):
    monkeypatch.setattr(ws.sys, "platform", "win32")
    st = ws.status()
    assert st["supported"] is False and st["backend"] is None
    assert st["reason"] == ws.UNSUPPORTED_WINDOWS
    assert "NOT confined" in st["reason"]
    with pytest.raises(RuntimeError, match="unsupported on Windows"):
        ws.prefix("C:/w", "codex_cli")


def test_other_os_is_explicitly_unsupported(monkeypatch):
    monkeypatch.setattr(ws.sys, "platform", "darwin")
    st = ws.status()
    assert st["supported"] is False and "darwin" in st["reason"]


def test_linux_prefers_bwrap_then_landlock_then_reports_unavailable(monkeypatch):
    monkeypatch.setattr(ws.sys, "platform", "linux")
    monkeypatch.setattr(ws, "_bwrap_verdict", lambda: (True, "/usr/bin/bwrap"))
    monkeypatch.setattr(ws, "_landlock_verdict", lambda: (True, "ABI 3"))
    assert ws.backend()[0] == ws.BWRAP
    ws.reset_cache()
    monkeypatch.setattr(ws, "_bwrap_verdict", lambda: (False, "cannot create its namespace"))
    name, why = ws.backend()
    assert name == ws.LANDLOCK and "cannot create its namespace" in why
    ws.reset_cache()
    monkeypatch.setattr(ws, "_landlock_verdict", lambda: (False, "not enabled in this kernel"))
    st = ws.status()
    assert st["supported"] is False
    assert "not enabled in this kernel" in st["reason"] and "NOT confined" in st["reason"]


def test_a_forced_backend_is_honoured(monkeypatch):
    monkeypatch.setattr(ws.sys, "platform", "linux")
    monkeypatch.setattr(ws, "_bwrap_verdict", lambda: (True, "/usr/bin/bwrap"))
    monkeypatch.setattr(ws, "_landlock_verdict", lambda: (True, "ABI 3"))
    monkeypatch.setenv(ws.BACKEND_ENV, "landlock")
    assert ws.backend()[0] == ws.LANDLOCK


@pytest.mark.parametrize("env, value, expected", [
    (shared_checks.WRITE_BOUNDARY_GUARD_ENV, "0", ws.OFF),
    (shared_checks.WRITE_BOUNDARY_GUARD_ENV, "monitor", ws.MONITOR),
    (hg.ENFORCEMENT_ENV, "0", ws.OFF),
])
def test_kill_switches_are_the_existing_ones(monkeypatch, env, value, expected):
    monkeypatch.setenv(env, value)
    assert ws.mode() == expected
    st = ws.status()
    assert st["supported"] is False
    assert "kill switch" in st["reason"] and "NOT confined" in st["reason"]


# ── wrap_argv and the adapters ───────────────────────────────────────────────

class _Session:
    def __init__(self, metadata=None, working_dir="/w", task_id="t"):
        self.metadata, self.working_dir, self.task_id = metadata or {}, working_dir, task_id


def test_wrap_argv_is_a_no_op_unless_the_runner_asked():
    assert ws.wrap_argv(["codex", "exec"], _Session(), "codex_cli") == ["codex", "exec"]


def test_wrap_argv_prepends_the_adapters_own_sandbox(monkeypatch):
    seen = []
    monkeypatch.setattr(ws, "prefix", lambda wt, adapter: seen.append((wt, adapter)) or ["SB", "--"])
    argv = ws.wrap_argv(["codex"], _Session({ws.META_KEY: True}), "codex_cli")
    assert argv == ["SB", "--", "codex"] and seen == [("/w", "codex_cli")]


def test_a_sandbox_that_cannot_be_built_fails_open_and_says_so(monkeypatch):
    warnings = []
    monkeypatch.setattr(ws.logger, "warning", lambda msg, *a: warnings.append(msg % a))

    def broken(*a, **k):
        raise RuntimeError("no backend")
    monkeypatch.setattr(ws, "prefix", broken)
    assert ws.wrap_argv(["codex"], _Session({ws.META_KEY: True}), "codex_cli") == ["codex"]
    assert any("NOT applied" in w and "UNCONFINED" in w and "no backend" in w for w in warnings)


@pytest.mark.parametrize("name", CLI_ADAPTERS)
def test_every_cli_adapter_applies_the_sandbox(monkeypatch, name):
    from tools.agents import capability_matrix as cm
    from tools.agents import registry

    monkeypatch.setattr(ws, "prefix", lambda wt, adapter: ["SB", adapter, "--"])
    adapter = registry.get_adapter(name)
    plain = cm._build_argv(adapter, {})
    wrapped = cm._build_argv(adapter, {ws.META_KEY: True})
    assert wrapped == ["SB", name, "--", *plain]


def test_landlock_helper_refuses_to_run_unconfined(monkeypatch, capsys):
    ran = []
    monkeypatch.setattr(ll, "restrict_writes", lambda roots: (_ for _ in ()).throw(OSError("boom")))
    monkeypatch.setattr(ll.os, "execvp", lambda *a: ran.append(a))
    assert ll.main(["--rw", "/w", "--", "true"]) == ll.REFUSED_EXIT
    assert ran == []
    assert "refusing to run unconfined" in capsys.readouterr().err


def test_landlock_rights_grow_with_the_abi():
    assert ll.handled_rights(1) & ll._REFER == 0
    assert ll.handled_rights(2) & ll._REFER
    assert ll.handled_rights(3) & ll._TRUNCATE


# ── live: real writes through the real sandbox ───────────────────────────────

def _live(home_area, which=None):
    cmd = ws.prefix(str(home_area["worktree"]), "fake_cli", which=which)
    roots = [Path(r) for r in ws.writable_roots(str(home_area["worktree"]), "fake_cli")]
    assert not any(home_area["outside"].resolve().is_relative_to(r) for r in roots)
    return ws.self_test(cmd, worktree=home_area["worktree"], outside=home_area["outside"],
                        state_dir=home_area["state"])


def test_inside_and_state_writes_land_and_outside_fails(home_area):
    if not LINUX:
        # Not a pass by omission: off Linux the sandbox must SAY it cannot run.
        with pytest.raises(RuntimeError, match="NOT confined"):
            ws.prefix(str(home_area["worktree"]), "fake_cli")
        assert ws.status()["supported"] is False
        return
    st = ws.status()
    assert st["supported"], f"no Linux write sandbox on this host: {st['reason']}"
    report = _live(home_area)
    assert report["checks"]["worktree_write"]["written"], report
    assert report["checks"]["state_write"]["written"], report
    assert not report["checks"]["outside_write"]["written"], report
    assert report["sandboxed"] is True
    assert not home_area["outside"].exists()


def test_landlock_backend_confines_writes(home_area):
    if not LINUX:
        assert ll.abi() == 0
        return
    assert ll.abi() >= 1, "Landlock is not enabled on this Linux kernel"
    report = _live(home_area, which=ws.LANDLOCK)
    assert report["sandboxed"] is True, report


def test_without_the_sandbox_the_outside_write_lands(home_area):
    """The control: the same write with no prefix DOES land, so the refusal is the sandbox's."""
    if not LINUX:
        return
    report = ws.self_test([], worktree=home_area["worktree"], outside=home_area["outside"],
                          state_dir=home_area["state"])
    assert report["checks"]["outside_write"]["written"] is True
    assert report["sandboxed"] is False


# ── the runner and the capability ────────────────────────────────────────────

def test_runner_session_asks_for_the_sandbox(monkeypatch):
    from tools.genesis.reflexes import kanban

    monkeypatch.setattr(kanban, "_selected_model", lambda: None)
    monkeypatch.setattr(kanban, "_get_task_timeout", lambda tid: 60)
    monkeypatch.setattr(ws, "status", lambda: {"supported": True, "reason": "", "backend": "bwrap"})
    session = kanban._agent_session({"id": "omx-guard-05"}, "x", ".")
    assert session.metadata[ws.META_KEY] is True


def test_runner_logs_when_the_sandbox_is_unsupported(monkeypatch):
    from tools.genesis.reflexes import kanban

    warnings = []
    monkeypatch.setattr(kanban, "_selected_model", lambda: None)
    monkeypatch.setattr(kanban, "_get_task_timeout", lambda tid: 60)
    monkeypatch.setattr(kanban.logger, "warning", lambda msg, *a: warnings.append(msg % a))
    monkeypatch.setattr(ws, "status", lambda: {"supported": False, "backend": None,
                                               "reason": ws.UNSUPPORTED_WINDOWS})
    session = kanban._agent_session({"id": "omx-guard-05"}, "x", ".")
    assert ws.META_KEY not in session.metadata
    assert any("NOT applied" in w and "unsupported on Windows" in w for w in warnings)


def _cell(adapter):
    from tools.agents import capability_matrix as cm

    return cm.probe_adapter(adapter, only=["sandboxed"])["capabilities"]["sandboxed"]


def test_in_process_adapters_are_measured_absent():
    for name in ("local_agent", "local_llm_router"):
        cell = _cell(name)
        assert cell["actual"] == "absent", cell
        assert cell["verdict"] == "agreed_absent"


@pytest.mark.parametrize("sandboxed, actual", [(True, "present"), (False, "absent"),
                                               (None, "unconfirmed")])
def test_the_capability_reports_the_probe(monkeypatch, sandboxed, actual):
    monkeypatch.setattr(ws, "probe", lambda name, build_argv=None: {
        "sandboxed": sandboxed, "reason": "measured", "status": {"backend": "bwrap"}})
    assert _cell("codex_cli")["actual"] == actual


def test_capability_is_probed_through_the_adapters_own_argv(home_area, monkeypatch):
    """Live: codex_cli's own build_argv, real writes. Windows says unsupported."""
    monkeypatch.setattr(ws, "declared_state", lambda adapter, path=None: {
        "state_dirs": [home_area["state"]], "state_files": []})
    cell = _cell("codex_cli")
    if LINUX:
        assert cell["actual"] == "present", cell
        assert cell["verdict"] == "confirmed"
    else:
        assert cell["actual"] == "unconfirmed"
        assert "NOT confined" in cell["evidence"]
