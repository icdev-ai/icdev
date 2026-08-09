# CUI // SP-CTI
"""hgx-guard-02 — the headless path must guard as well as the Claude Code hook.

`.claude/hooks/pre_tool_use.py` runs eight blocking checks. `hook_compat.
run_pre_tool_check` — what SAG and every non-Claude-Code orchestrator actually
calls — ran two, and waved through any tool whose name was not in a six-name
list. An agent running OUTSIDE Claude Code was therefore materially LESS guarded
than one inside it, which is exactly backwards for an IL5/IL6 platform.

Both paths now call `tools/hooks/shared_checks.py`, so they cannot drift again.
"""
import pytest

from tools.airgap import hook_compat
from tools.hooks import shared_checks


@pytest.fixture
def no_audit(monkeypatch):
    """Keep the decision tests off the DB. NOT autouse — the audit-failure tests
    below need the real store_event path."""
    monkeypatch.setattr(hook_compat, "store_event", lambda *a, **k: 1)


def _blocked(tool, inp):
    r = hook_compat.run_pre_tool_check(tool, inp)
    return r["allowed"] is False, r.get("reason", "")


# ── Parity: every check the Claude Code hook blocks, the headless path blocks ──

PARITY_CASES = [
    ("dangerous rm",        "Bash",  {"command": "rm -rf /"}),
    ("env file read",       "Read",  {"file_path": ".env"}),
    ("env file write",      "Write", {"file_path": "/repo/.env", "content": "K=v"}),
    # The sqlite check deliberately scopes to files under tools/ — that is the
    # surface where a raw sqlite3.connect() bypasses the storage layer.
    ("direct sqlite3",      "Write", {"file_path": "tools/x/y.py",
                                      "content": "import sqlite3\nsqlite3.connect('a.db')"}),
    ("append-only UPDATE",  "Bash",  {"command": "psql -c \"UPDATE audit_trail SET x=1\""}),
    ("git force push",      "Bash",  {"command": "git push --force origin main"}),
    ("git reset --hard",    "Bash",  {"command": "git reset --hard origin/main"}),
]


@pytest.mark.parametrize("name,tool,inp", PARITY_CASES, ids=[c[0] for c in PARITY_CASES])
def test_headless_blocks_what_the_hook_blocks(name, tool, inp, no_audit):
    blocked, reason = _blocked(tool, inp)
    assert blocked, f"{name} was ALLOWED by the headless path"
    assert reason, "a block must carry a reason"


def test_the_six_name_short_circuit_is_gone(no_audit):
    """A tool outside the old six-name list must now be SCANNED.

    The list was ("Bash","bash","shell","sql","Write","Edit"); anything else
    returned allowed without looking at the payload. `Read` was not on it, so a
    `.env` read went straight through — the check existed and never ran.

    This asserts the tool is *scanned*, not that every check fires for every
    tool: each check still scopes itself (check_dangerous_rm is Bash-only), and
    that judgement belongs with the check rather than a list here.
    """
    blocked, _ = _blocked("Read", {"file_path": ".env"})
    assert blocked, "a tool outside the old list must not bypass the checks"


def test_ordinary_work_is_still_allowed(no_audit):
    """A guard that blocks real work gets disabled, which is worse than no guard."""
    for tool, inp in [
        ("Bash", {"command": "git status"}),
        ("Bash", {"command": "python -m pytest tests/ -q"}),
        ("Read", {"file_path": "tools/kanban/cli.py"}),
        ("Write", {"file_path": "notes.md", "content": "# hello"}),
    ]:
        r = hook_compat.run_pre_tool_check(tool, inp)
        assert r["allowed"] is True, f"{tool} {inp} was wrongly blocked: {r['reason']}"


def test_every_shared_check_is_actually_wired():
    """Guards against a check existing but never being called — the failure mode
    this whole slice exists to fix."""
    wired = set(hook_compat.HEADLESS_CHECKS)
    available = {n for n in dir(shared_checks) if n.startswith("check_")}
    missing = available - wired
    assert not missing, f"shared_checks exposes checks the headless path never runs: {missing}"


# ── The audit write must never be able to take the guard down ─────────────────


def test_store_event_failure_does_not_break_the_guard(monkeypatch):
    """`hook_events.id` had a desynced PostgreSQL sequence, so every insert raised
    UniqueViolation. The old `except sqlite3.OperationalError` did not catch it, so
    the exception propagated out of run_pre_tool_check — auditing a block took the
    guard down with it. A decision must still be returned."""
    def _boom(*a, **k):
        raise RuntimeError("simulated audit failure (e.g. UniqueViolation)")

    monkeypatch.setattr(hook_compat, "store_event", _boom)
    r = hook_compat.run_pre_tool_check("Bash", {"command": "rm -rf /"})
    assert r["allowed"] is False, "the block decision must survive an audit failure"


def test_postgres_audit_is_not_gated_on_a_sqlite_file(monkeypatch, tmp_path):
    """store_event early-returned whenever the local SQLite file was absent —
    including on PostgreSQL deployments and from any git worktree. Combined with
    a desynced sequence, the headless hook's NIST AU trail was silently empty."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "postgresql")
    monkeypatch.setattr(hook_compat, "DB_PATH", tmp_path / "definitely-absent.db")

    wrote = {}

    class _Cur:
        lastrowid = 7

        def execute(self, *a, **k):
            wrote["called"] = True

    class _Conn:
        def cursor(self):
            return _Cur()

        def commit(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(hook_compat, "get_connection", lambda *a, **k: _Conn())
    assert hook_compat.store_event("s", "pre_tool_use", "Bash", {"x": 1}) == 7
    assert wrote.get("called"), "the audit INSERT must run on PostgreSQL"


def test_sqlite_audit_still_short_circuits_when_the_file_is_absent(monkeypatch, tmp_path):
    """The gate is correct FOR SQLITE — keep it there."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setattr(hook_compat, "DB_PATH", tmp_path / "absent.db")
    assert hook_compat.store_event("s", "pre_tool_use", "Bash", {"x": 1}) == -1


def test_store_event_never_raises(monkeypatch):
    """store_event itself swallows any backend error and reports it."""
    def _boom(*a, **k):
        raise RuntimeError("db exploded")

    monkeypatch.setattr(hook_compat, "get_connection", _boom, raising=False)
    assert hook_compat.store_event("s", "pre_tool_use", "Bash", {"x": 1}) == -1
