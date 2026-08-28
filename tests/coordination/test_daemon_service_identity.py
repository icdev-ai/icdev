# CUI // SP-CTI
"""A daemon must say what it IS, and the reaper must actually run (autonomy-id-05).

TWO DEFECTS, ONE OBSERVATION. On 2026-08-28 the coordination hook reported eight active
sessions including what looked like duplicate schedulers, pr_watchers and genesis daemons.
Every one of those pids was dead. Investigating found:

1. `get_agent_type()` returns "claude" whenever CLAUDECODE or CLAUDE_SESSION_ID is in the
   environment, and a supervisor started from a Claude Code terminal hands those to every
   child it spawns. `agent_sessions` carried `local-548f064fde37 / claude / pid 20948`
   while pid 20948 was `tools/proposal_genesis/daemon.py` under launch.py. The registry
   told every other session that a supervised daemon was a human's Claude session -- which
   is what a reader consults before deciding what is safe to stop.

2. `reap_stale()` had existed since the module was written and was called by NOBODY. Rows
   accumulated forever; `list_active()` filters by TTL, so they stopped being DISPLAYED,
   which is exactly why nobody noticed. 4 of 9 rows were past the TTL for provably dead
   pids.

The two compound: the mislabelled rows are the ones that linger.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
BASE = REPO_ROOT / "tools" / "daemon" / "base.py"
REGISTRY = REPO_ROOT / "tools" / "coordination" / "session_registry.py"


def _daemon_modules():
    """Every file declaring `class X(DaemonBase)`, found by AST, not by import.

    Importing them starts config loads and air-gap probes; the declaration is a static
    fact and reading it statically is both cheaper and more honest.
    """
    out = []
    for path in sorted((REPO_ROOT / "tools").rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):  # pragma: no cover
            continue
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            bases = {b.id for b in node.bases if isinstance(b, ast.Name)}
            if "DaemonBase" in bases:
                out.append((path, node))
    return out


def _class_attr(node: ast.ClassDef, name: str):
    for stmt in node.body:
        targets = []
        if isinstance(stmt, ast.Assign):
            targets = [t.id for t in stmt.targets if isinstance(t, ast.Name)]
            value = stmt.value
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            targets = [stmt.target.id]
            value = stmt.value
        else:
            continue
        if name in targets and isinstance(value, ast.Constant):
            return value.value
    return None


# ---------------------------------------------------------------------------
# the declaration
# ---------------------------------------------------------------------------


def test_there_are_daemons_to_check():
    """Guard the guard: an empty sweep would pass every assertion below."""
    assert len(_daemon_modules()) >= 5


@pytest.mark.parametrize("attr", ["service_name", "service_agent"])
def test_every_daemon_declares_what_it_is(attr):
    """A daemon that declares nothing registers under the AMBIENT identity -- which, for
    anything started from a Claude Code terminal, is `claude`. This is what turns the
    next forgotten daemon into a test failure instead of a phantom session."""
    missing = [
        str(path.relative_to(REPO_ROOT)) + f"::{node.name}"
        for path, node in _daemon_modules()
        if not _class_attr(node, attr)
    ]
    assert not missing, (
        f"these daemons declare no {attr} and will register as the ambient agent "
        f"(usually 'claude'): {missing}"
    )


def test_the_identities_are_distinct():
    """Two daemons sharing an id is the SAME defect one layer along -- the registry
    could no longer tell them apart, which is the confusion this card started from."""
    names = [_class_attr(n, "service_name") for _, n in _daemon_modules()]
    agents = [_class_attr(n, "service_agent") for _, n in _daemon_modules()]
    assert len(set(names)) == len(names), f"duplicate service_name: {names}"
    assert len(set(agents)) == len(agents), f"duplicate service_agent: {agents}"


def test_the_base_declares_no_identity_of_its_own():
    """The default must be None, so `claim_identity` REFUSES and warns rather than
    registering every daemon under one shared borrowed name."""
    tree = ast.parse(BASE.read_text(encoding="utf-8"))
    base = next(n for n in ast.walk(tree)
                if isinstance(n, ast.ClassDef) and n.name == "DaemonBase")
    assert _class_attr(base, "service_name") is None
    assert _class_attr(base, "service_agent") is None


# ---------------------------------------------------------------------------
# the claim
# ---------------------------------------------------------------------------


class _Daemon:
    """The two attributes and the method, without importing a real daemon."""

    service_name = "test-service"
    service_agent = "test_agent"
    daemon_name = "Test Daemon"

    def __init__(self):
        from tools.daemon.base import DaemonBase

        self.claim_identity = DaemonBase.claim_identity.__get__(self)


def test_claim_sets_both_env_vars(monkeypatch):
    monkeypatch.delenv("ICDEV_SESSION_ID", raising=False)
    monkeypatch.delenv("ICDEV_AGENT", raising=False)
    import os

    sid = _Daemon().claim_identity()
    assert sid and sid.startswith("test-service-")
    assert os.environ["ICDEV_AGENT"] == "test_agent"


def test_claim_does_not_override_an_earlier_one(monkeypatch):
    """setdefault semantics: pr_watcher / genesis / kanban_scheduler already claim in
    main(), and the base must not overwrite what they chose."""
    monkeypatch.setenv("ICDEV_SESSION_ID", "already-claimed-42")
    monkeypatch.setenv("ICDEV_AGENT", "already_agent")
    assert _Daemon().claim_identity() == "already-claimed-42"
    import os

    assert os.environ["ICDEV_AGENT"] == "already_agent"


def test_a_daemon_with_no_identity_returns_none(monkeypatch):
    """None is the REPORTED gap; run_forever prints a warning on it."""
    monkeypatch.delenv("ICDEV_SESSION_ID", raising=False)
    d = _Daemon()
    d.service_name = None
    assert d.claim_identity() is None


def test_the_claim_happens_before_the_first_heartbeat():
    """Claiming after would leave a `claude`-labelled row visible to every other session
    for a full cycle. Order is the whole point, so assert on it."""
    src = BASE.read_text(encoding="utf-8")
    claim = src.index("_claimed = self.claim_identity()")
    heartbeat = src.index("_sreg.heartbeat()")
    assert claim < heartbeat


# ---------------------------------------------------------------------------
# the reaper
# ---------------------------------------------------------------------------


def test_register_calls_the_reaper():
    """`reap_stale` was imported by nothing and called by nothing. A reaper nobody runs
    is the same defect as a capability nobody consumes."""
    tree = ast.parse(REGISTRY.read_text(encoding="utf-8"))
    register = next(n for n in tree.body
                    if isinstance(n, ast.FunctionDef) and n.name == "register")
    called = {
        n.func.id for n in ast.walk(register)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert "_reap_on_register" in called

    helper = next(n for n in tree.body
                  if isinstance(n, ast.FunctionDef) and n.name == "_reap_on_register")
    inner = {
        n.func.id for n in ast.walk(helper)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert "reap_stale" in inner


def test_the_reaper_is_not_on_the_heartbeat_path():
    """Heartbeat runs every cycle of every daemon; reaping there puts a scan and a DELETE
    on a hot path to clean up something that only changes when a process starts or stops."""
    tree = ast.parse(REGISTRY.read_text(encoding="utf-8"))
    hb = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "heartbeat")
    called = {
        n.func.id for n in ast.walk(hb)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert "reap_stale" not in called and "_reap_on_register" not in called


def test_a_failing_reap_never_stops_a_registration(monkeypatch):
    """A process that cannot announce itself is worse than a stale row."""
    from tools.coordination import session_registry as sr

    def boom(*a, **k):
        raise RuntimeError("database is on fire")

    monkeypatch.setattr(sr, "reap_stale", boom)
    assert sr._reap_on_register() == 0
