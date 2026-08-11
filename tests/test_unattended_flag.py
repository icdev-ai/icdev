# CUI // SP-CTI
"""The per-session unattended flag — ROUTING ONLY (agov-inbox-04).

Three things have to be true, they are the card's acceptance criteria, and each
has a test class here:

  1. **Still gated.** With ``unattended=True`` an irreversible tool call
     produces a ``pending`` ``approval_items`` row and BLOCKS. It does not
     execute. Approving it is what lets it run — a human is still the decider,
     the question was merely delivered somewhere a human will see it.
  2. **Ceiling unchanged.** The set of tiers requiring approval is
     byte-identical with the flag True and False, and so is the per-tool
     classification and the gate's resolved mode.
  3. **Durable and never implicit.** The flag survives a process restart, and a
     non-TTY stdin never sets it.

Why each of these is a test rather than a code comment: every one of them fails
SILENTLY. A regression that made unattended auto-approve would look like a
feature working beautifully — the agent would stop asking — right up until an
overnight session force-pushed something. A regression that made unattended
imply ``mode=off`` would not show up in the policy file at all. And a flag that
quietly stopped persisting would produce a session that "randomly" denies
everything after a restart, with the cause two layers away from the symptom.

The schema comes from the migration's own DDL rather than a hand-written copy,
so a column added to one and not the other fails here instead of at runtime
inside a swallowed exception (CLAUDE.md).
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import sqlite3
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

import pytest

from tests._sql_compat import translating
from tools.agent_runtime import unattended as unattended_mod
from tools.agent_runtime.approval_gate import (
    IRREVERSIBLE,
    UNKNOWN,
    ApprovalRequest,
    build_approval_hook,
    classify,
    console_approver,
)
from tools.agent_runtime.approval_inbox import (
    STATE_PENDING,
    STATE_RESOLVED,
)
from tools.agent_runtime.approval_inbox import TABLE as ITEMS_TABLE
from tools.agent_runtime.approval_inbox import (
    resolve as resolve_item,
)
from tools.agent_runtime.unattended import (
    COLUMNS,
    ENV_UNATTENDED,
    SOURCE_CLI,
    SOURCE_DEFAULT,
    SOURCE_ENV,
    TABLE,
    UnattendedStoreUnavailable,
    approval_surface,
    approver_for,
    clear_unattended,
    is_unattended,
    list_unattended,
    safety_approver_for,
    set_unattended,
    surface_digest,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

MIGRATION_DIR = (
    REPO_ROOT / "tools" / "db" / "migrations" / "20260809213046_agov_unattended_sessions"
)

# `git_push` is enumerated irreversible in args/agent_approval_policy.yaml and is
# not a command_tool, so the classification does not depend on pattern matching
# against the argument string.
TOOL = "git_push"
TOOL_INPUT = {"remote": "origin", "branch": "main"}

SESSION = "sess-unattended-1"

# Fast enough that the suite does not crawl, slow enough that "it blocked" is
# observable rather than a race.
POLL = 0.05
GRACE = 10.0


# ---------------------------------------------------------------------------
# Schema — from the migrations themselves
# ---------------------------------------------------------------------------
def _load_migration(name: str) -> Any:
    path = MIGRATION_DIR / name
    spec = importlib.util.spec_from_file_location(f"_m_unattended_{name}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _unattended_ddl() -> str:
    return _load_migration("up.py")._DDL


def _approval_items_ddl() -> str:
    return (
        REPO_ROOT / "tools" / "db" / "migrations"
        / "20260809203855_agov_approval_items" / "up.sql"
    ).read_text(encoding="utf-8")


def _approval_log_ddl() -> str:
    path = (
        REPO_ROOT / "tools" / "db" / "migrations"
        / "20260803002224_agent_approval_log" / "up.py"
    )
    spec = importlib.util.spec_from_file_location("_m_agent_approval_log", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module._DDL


def _cron_jobs_ddl() -> str:
    """The PRE-migration ``agent_cron_jobs``, i.e. migration 289's own DDL.

    Deliberately the OLD shape: the point of the cron half of this change is
    that a table created by 289 has no ``unattended`` column, and
    ``CREATE TABLE IF NOT EXISTS`` will never add one.
    """
    sql = (
        REPO_ROOT / "tools" / "db" / "migrations" / "289_agent_cron_jobs.sql"
    ).read_text(encoding="utf-8")
    # Just the jobs table; the runs table is irrelevant here and its DDL brings
    # along statements this fixture does not need.
    start = sql.index("CREATE TABLE IF NOT EXISTS agent_cron_jobs")
    end = sql.index(";", start)
    return sql[start : end + 1]


def _fresh_module_instance() -> Any:
    """A second, independent copy of ``unattended.py``, sharing no state.

    Stands in for the restarted process. Registered in ``sys.modules`` under a
    name of its own — ``@dataclass`` resolves annotations through
    ``sys.modules[cls.__module__]`` and raises without it — so it neither
    replaces nor mutates the instance the rest of the suite imported.
    """
    path = Path(unattended_mod.__file__)
    name = f"_restarted_unattended_{len(sys.modules)}"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


def _storage_module():
    """The module the store actually resolves ``get_connection`` from.

    ``tools.db.storage`` in ``sys.modules`` is the compat shim, and
    ``import tools.db.storage`` binds the canonical ``icdev.tools.db.storage``
    — two different objects. Every module under test imports the shim from
    inside its functions, so patching the canonical module (which is what
    monkeypatch's string form resolves to) would patch nothing and every
    assertion below would be asserting its own no-op.
    """
    return sys.modules["tools.db.storage"]


@pytest.fixture
def flag_db(monkeypatch, tmp_path):
    """Every real table involved, in one file DB, behind the ``%s`` translation.

    A FILE rather than an in-memory DB, and a FRESH connection per call rather
    than one shared object, because the unattended approver blocks on one thread
    while the test resolves on another — which is the whole point of the feature
    and is exactly what a single ``check_same_thread`` connection forbids.
    """
    db_path = tmp_path / "unattended.db"
    boot = sqlite3.connect(str(db_path))
    boot.executescript(_unattended_ddl())
    boot.executescript(_approval_items_ddl())
    boot.executescript(_approval_log_ddl())
    boot.executescript(_cron_jobs_ddl())
    boot.commit()
    boot.close()

    def _open(*_a, **_k):
        return translating(sqlite3.connect(str(db_path), timeout=30.0))

    storage = _storage_module()
    monkeypatch.setattr(storage, "get_connection", _open)
    monkeypatch.setattr(storage, "table_exists", lambda _c, _t: True)
    monkeypatch.setenv("ICDEV_APPROVAL_ACTOR", "test-operator")
    for name in (
        ENV_UNATTENDED,
        "ICDEV_APPROVAL_INBOX",
        "ICDEV_APPROVAL_INBOX_TIMEOUT",
        "ICDEV_APPROVAL_INBOX_POLL",
        "ICDEV_AGENT_APPROVAL_MODE",
    ):
        monkeypatch.delenv(name, raising=False)
    yield db_path


def _rows(db_path: Path, table: str) -> list[dict[str, Any]]:
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute(f"SELECT * FROM {table}")
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        conn.close()


def _await_pending(db_path: Path, *, timeout: float = GRACE) -> dict[str, Any]:
    """Block until a pending item exists. Fails the test on timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rows = [r for r in _rows(db_path, ITEMS_TABLE) if r["state"] == STATE_PENDING]
        if rows:
            return rows[0]
        time.sleep(0.01)
    pytest.fail(f"no pending {ITEMS_TABLE} row appeared within {timeout}s")


class _Runner(threading.Thread):
    """Runs a callable on another thread and keeps its result."""

    def __init__(self, fn, *args):
        super().__init__(daemon=True)
        self._fn, self._args = fn, args
        self.result: Any = None
        self.error: Optional[BaseException] = None
        self.finished = threading.Event()

    def run(self) -> None:
        try:
            self.result = self._fn(*self._args)
        except BaseException as exc:  # noqa: BLE001 — re-raised by the assertion
            self.error = exc
        finally:
            self.finished.set()

    def join_ok(self, timeout: float = GRACE) -> Any:
        self.finished.wait(timeout)
        assert self.finished.is_set(), "the call never returned"
        if self.error is not None:
            raise self.error
        return self.result


# ---------------------------------------------------------------------------
# Schema parity
# ---------------------------------------------------------------------------
class TestSchema:
    def test_columns_match_the_migration(self, flag_db):
        live = [r[1] for r in sqlite3.connect(str(flag_db)).execute(
            f"PRAGMA table_info({TABLE})"
        ).fetchall()]
        assert list(COLUMNS) == live

    def test_insert_matches_the_live_schema(self, flag_db):
        set_unattended(SESSION, True, reason="overnight run")
        rows = _rows(flag_db, TABLE)
        assert len(rows) == 1
        assert set(rows[0]) == set(COLUMNS)
        assert rows[0]["unattended"] == 1
        assert rows[0]["source"] == SOURCE_CLI

    def test_the_cron_column_is_added_to_a_pre_migration_table(self, flag_db):
        """The fixture built ``agent_cron_jobs`` from migration 289's OLD DDL.

        ``cron._ensure_schema`` runs ``CREATE TABLE IF NOT EXISTS``, which is a
        no-op on an existing table and would leave the column missing — the
        exact silent-INSERT-failure shape CLAUDE.md names. ``_ensure_columns``
        is what closes it.
        """
        from tools.agent_runtime import cron

        before = {
            r[1] for r in sqlite3.connect(str(flag_db)).execute(
                "PRAGMA table_info(agent_cron_jobs)"
            ).fetchall()
        }
        assert "unattended" not in before, "fixture must start from the 289 shape"

        conn = _storage_module().get_connection()
        cron._ensure_schema(conn)
        conn.close()

        after = {
            r[1] for r in sqlite3.connect(str(flag_db)).execute(
                "PRAGMA table_info(agent_cron_jobs)"
            ).fetchall()
        }
        assert "unattended" in after

    def test_the_migration_column_add_is_idempotent(self, flag_db):
        from tools.agent_runtime import cron

        for _ in range(3):
            conn = _storage_module().get_connection()
            cron._ensure_schema(conn)
            conn.close()
        cols = [
            r[1] for r in sqlite3.connect(str(flag_db)).execute(
                "PRAGMA table_info(agent_cron_jobs)"
            ).fetchall()
        ]
        assert cols.count("unattended") == 1


# ---------------------------------------------------------------------------
# 1. THE LOAD-BEARING TEST — unattended is still gated
# ---------------------------------------------------------------------------
class TestStillGatedWhenUnattended:
    def _hook(self, session_id: str = SESSION):
        """The REAL gate, with the approver unattended routing picks."""
        return build_approval_hook(
            approver=approver_for(
                session_id,
                unattended=True,
                inbox="ops",
                timeout_seconds=GRACE,
                poll_seconds=POLL,
            ),
            mode="enforce",
            actor="test-operator",
            # The headless hard-block list is a separate mechanism with its own
            # tests; consulting it here would decide the call before the
            # approver ever ran.
            consult_pre_tool_check=False,
        )

    def test_an_irreversible_call_queues_a_pending_item_and_blocks(self, flag_db):
        set_unattended(SESSION, True, reason="overnight run")
        assert is_unattended(SESSION) is True

        hook = self._hook()
        runner = _Runner(hook, TOOL, TOOL_INPUT)
        runner.start()

        row = _await_pending(flag_db)
        assert row["tool_name"] == TOOL
        assert row["tier"] == IRREVERSIBLE
        assert row["state"] == STATE_PENDING
        assert row["session_id"] == SESSION
        assert row["expires_at"], "the item must carry its own deadline for the sweep"

        # BLOCKED: several poll intervals later there is still no verdict. This
        # is the assertion the whole card exists for — an auto-approver would
        # have returned instantly.
        assert not runner.finished.wait(POLL * 6), "unattended did not block the call"

        # A human answering is what releases it.
        assert resolve_item(row["item_id"], approved=True, resolved_by="alice")
        assert runner.join_ok() is None
        assert _rows(flag_db, ITEMS_TABLE)[0]["state"] == STATE_RESOLVED

    def test_the_tool_does_not_execute_while_the_ask_is_pending(self, flag_db):
        """Not "the hook returns a string" — the handler never runs.

        Driven through ``dispatch.make_handler`` + the real ``SafetyGate``, i.e.
        the path a SAG toolset actually executes on, with a sentinel that
        records whether the tool body was entered.
        """
        from tools.agent_runtime.dispatch import make_handler
        from tools.agent_runtime.discovery import ToolSpec
        from tools.agent_runtime.safety import build_safety_gate

        set_unattended(SESSION, True)
        executed: list[dict[str, Any]] = []

        def _tool(**kwargs: Any) -> str:
            executed.append(kwargs)
            return "pushed"

        spec = ToolSpec(
            name=TOOL,
            schema={"type": "function", "function": {"name": TOOL, "parameters": {}}},
            source="decorated",
            read_only=False,
            callable=_tool,
        )
        handler = make_handler(
            spec,
            gate=build_safety_gate(
                mode="manual",
                approver=safety_approver_for(
                    SESSION,
                    unattended=True,
                    inbox="ops",
                    timeout_seconds=GRACE,
                    poll_seconds=POLL,
                ),
                checkpoint=False,
            ),
        )

        runner = _Runner(handler, dict(TOOL_INPUT), None)
        runner.start()
        row = _await_pending(flag_db)

        assert not runner.finished.wait(POLL * 6), "the call did not suspend"
        assert executed == [], "the tool RAN while its approval was still pending"

        resolve_item(row["item_id"], approved=False, resolved_by="bob", reason="no")
        result = runner.join_ok()
        assert "blocked" in str(result).lower()
        assert executed == [], "the tool RAN after the ask was denied"

    def test_a_denial_halts_the_call_with_the_gate_s_own_message(self, flag_db):
        set_unattended(SESSION, True)
        hook = self._hook()
        runner = _Runner(hook, TOOL, TOOL_INPUT)
        runner.start()
        row = _await_pending(flag_db)

        resolve_item(row["item_id"], approved=False, resolved_by="bob", reason="nope")
        blocked = runner.join_ok()

        assert isinstance(blocked, str)
        assert "BLOCKED by the approval gate" in blocked

    def test_unattended_never_returns_an_approver_that_allows_by_itself(self, flag_db):
        """The routing seam has exactly two outcomes, and neither self-approves.

        Attended resolves to the console approver (denies on EOF); unattended
        resolves to the inbox approver, which cannot answer without a row. If a
        third branch is ever added that returns something else, this fails.
        """
        assert approver_for(SESSION, unattended=False) is console_approver

        approve = approver_for(
            SESSION, unattended=True, inbox="ops", timeout_seconds=GRACE, poll_seconds=POLL
        )
        assert approve is not console_approver

        # With the store unreachable it must DENY, not proceed: an ask that was
        # never queued is not an ask anybody can answer.
        request = ApprovalRequest(
            tool_name=TOOL,
            tool_input=TOOL_INPUT,
            classification=classify(TOOL, TOOL_INPUT),
            actor="agent",
        )
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(_storage_module(), "table_exists", lambda _c, _t: False)
            decision = approve(request)
        assert decision.approved is False
        assert "failing closed" in decision.reason

    def test_an_unavailable_inbox_falls_back_to_the_stricter_approver(
        self, flag_db, monkeypatch
    ):
        """A broken import degrades toward deny-on-EOF, never toward allow."""
        monkeypatch.setitem(sys.modules, "tools.agent_runtime.inbox_approver", None)
        assert approver_for(SESSION, unattended=True) is console_approver


# ---------------------------------------------------------------------------
# 2. THE INVARIANT — the autonomy ceiling does not move
# ---------------------------------------------------------------------------
class TestCeilingUnchanged:
    def test_the_approval_surface_is_byte_identical(self, flag_db):
        """Byte-identical, not merely equivalent.

        The surface spans the three places a leak could hide: the policy
        (``default_tier`` / ``require_approval_tiers``), the per-tool
        classification, and the gate's resolved mode — the last because
        "unattended implies mode=off" is the most tempting wrong shortcut and
        would not show up in the policy at all.
        """
        set_unattended(SESSION, True)
        on = surface_digest(SESSION, unattended=True)
        set_unattended(SESSION, False)
        off = surface_digest(SESSION, unattended=False)
        assert on == off

    def test_the_stored_flag_does_not_move_the_surface_either(self, flag_db):
        """Not just the argument — the PERSISTED state must not move it."""
        set_unattended(SESSION, True)
        stored_on = surface_digest(SESSION)
        clear_unattended(SESSION)
        stored_off = surface_digest(SESSION)
        assert stored_on == stored_off

    def test_the_required_tiers_are_the_same_set(self, flag_db):
        on = approval_surface(SESSION, unattended=True)
        off = approval_surface(SESSION, unattended=False)
        assert on["require_approval_tiers"] == off["require_approval_tiers"]
        assert IRREVERSIBLE in on["require_approval_tiers"]
        assert UNKNOWN in on["require_approval_tiers"]
        assert on["default_tier"] == off["default_tier"] == UNKNOWN
        assert on["gate_mode"] == off["gate_mode"]

    def test_no_tool_is_downgraded(self, flag_db):
        on = approval_surface(SESSION, unattended=True)
        off = approval_surface(SESSION, unattended=False)
        assert on["tools"] == off["tools"]
        assert on["probes"] == off["probes"]
        # And the probes still say what they must: an unenumerated tool needs a
        # human, in BOTH states.
        unenumerated = [
            v for k, v in on["probes"].items() if k.startswith("a_tool_nobody_enumerated")
        ]
        assert unenumerated and all(p["requires_approval"] for p in unenumerated)

    def test_the_same_calls_reach_an_approver_in_both_states(self, flag_db):
        """The behavioural form of the same claim.

        A surface built from ``classify`` would not catch a leak added inside
        ``build_approval_hook``, so drive the real hook over a probe set and
        compare which calls actually consulted an approver.
        """
        probes = [
            (TOOL, TOOL_INPUT),
            ("run_command", {"command": "git push --force origin main"}),
            ("run_command", {"command": "git add ."}),
            ("read_file", {"path": "README.md"}),
            ("a_tool_nobody_enumerated", {"anything": "at all"}),
        ]

        def _consulted(flag: bool) -> list[str]:
            seen: list[str] = []

            def _spy(request):
                seen.append(request.tool_name)
                from tools.agent_runtime.approval_gate import ApprovalDecision

                return ApprovalDecision(False, "spy denies", "spy")

            # `unattended` is applied to the SESSION, and the approver is held
            # constant, so any difference can only come from the flag.
            set_unattended(SESSION, flag)
            hook = build_approval_hook(
                approver=_spy, mode="enforce", actor="spy",
                consult_pre_tool_check=False,
            )
            for name, payload in probes:
                hook(name, payload)
            return seen

        assert _consulted(True) == _consulted(False)


# ---------------------------------------------------------------------------
# 3. DURABLE, AND NEVER IMPLICIT
# ---------------------------------------------------------------------------
class TestPersistence:
    def test_the_flag_is_on_disk_not_in_memory(self, flag_db):
        """Read the bytes back with a driver that never saw the writer.

        A plain ``sqlite3`` handle opened after the write, bypassing the module
        entirely: if the value were held in a module-level cache this would show
        an empty table.
        """
        set_unattended(SESSION, True, reason="overnight run", actor="alice")
        rows = _rows(flag_db, TABLE)
        assert len(rows) == 1
        assert rows[0]["session_id"] == SESSION
        assert rows[0]["unattended"] == 1
        assert rows[0]["reason"] == "overnight run"

    def test_it_survives_a_process_restart(self, flag_db):
        """Load a SECOND, independent instance of the module and ask it.

        A fresh instance rather than ``importlib.reload``: reload mutates the
        shared module in place, which leaves every other test holding functions
        whose exception classes no longer match the ones it imported. This
        instance shares nothing with the writer above — no cache, no dataclass,
        not even the same class objects — so if the flag lived anywhere but the
        database it would come back empty.
        """
        set_unattended(SESSION, True, reason="overnight run")

        restarted = _fresh_module_instance()
        assert restarted.is_unattended(SESSION) is True
        state = restarted.get_unattended(SESSION)
        assert state is not None
        assert state.unattended is True
        assert state.reason == "overnight run"
        # Nothing was shared: even the dataclass is a different class object.
        assert type(state) is not unattended_mod.UnattendedState

        # And the restart preserves "explicitly attended" too, which is a
        # different fact from "never asked".
        restarted.set_unattended(SESSION, False, reason="handing back")
        again = _fresh_module_instance()
        stored = again.get_unattended(SESSION)
        assert stored is not None and stored.unattended is False

    def test_set_at_is_preserved_across_a_flip(self, flag_db):
        first = set_unattended(SESSION, True)
        second = set_unattended(SESSION, False, reason="handing back")
        assert second.set_at == first.set_at
        assert second.updated_at >= first.updated_at

    def test_a_cron_job_carries_its_own_flag(self, flag_db):
        from tools.agent_runtime import cron

        job = cron.create_job(
            "overnight", "agent", "do the backlog", "interval", "1h", unattended=True
        )
        assert cron.job_is_unattended(job) is True

        # Re-read it the way a restarted ticker would: a fresh query, no shared
        # in-process object.
        reread = cron.get_job(job["id"])
        assert cron.job_is_unattended(reread) is True

        cron.set_job_unattended(job["id"], False)
        assert cron.job_is_unattended(cron.get_job(job["id"])) is False

    def test_a_job_row_without_the_column_reads_as_attended(self):
        """A row from a DB that predates the migration is not unattended."""
        from tools.agent_runtime import cron

        assert cron.job_is_unattended({"id": "cron-old"}) is False
        assert cron.job_is_unattended({"id": "cron-old", "unattended": None}) is False

    def test_an_unpersistable_flag_raises_rather_than_lying(self, flag_db, monkeypatch):
        monkeypatch.setattr(_storage_module(), "table_exists", lambda _c, _t: False)
        with pytest.raises(UnattendedStoreUnavailable):
            set_unattended(SESSION, True)

    def test_a_missing_store_reads_as_attended(self, flag_db, monkeypatch):
        set_unattended(SESSION, True)
        monkeypatch.setattr(_storage_module(), "table_exists", lambda _c, _t: False)
        # Degrading toward the STRICTER path: no store means console approver,
        # which denies on EOF.
        assert is_unattended(SESSION) is False
        assert list_unattended() == []


class TestNeverImplicit:
    def test_a_non_tty_stdin_does_not_enable_it(self, flag_db, monkeypatch):
        """The whole point. "No TTY" is true of CI, cron, Docker and pytest."""

        class _NotATTY:
            def isatty(self) -> bool:
                return False

            def read(self, *_a) -> str:
                return ""

        monkeypatch.setattr(sys, "stdin", _NotATTY())
        assert is_unattended(SESSION) is False
        assert unattended_mod.resolve(SESSION).source == SOURCE_DEFAULT

    def test_a_closed_stdin_does_not_enable_it_either(self, flag_db, monkeypatch):
        monkeypatch.setattr(sys, "stdin", None)
        assert is_unattended(SESSION) is False

    def test_the_module_never_consults_a_tty(self):
        """A source-level assertion, because the inference is the failure mode.

        An implementation that grew ``if not sys.stdin.isatty(): unattended =
        True`` would pass every behavioural test above by construction — it
        would only be wrong in production, in exactly the automated contexts
        where nobody reads the logs.
        """
        source = Path(unattended_mod.__file__).read_text(encoding="utf-8")
        body = source.split('"""', 2)[-1]  # skip the module docstring, which names it
        assert "isatty" not in body
        assert "istty" not in body

    def test_neither_does_the_cli_wiring(self):
        from tools.agent_runtime import cli

        source = Path(cli.__file__).read_text(encoding="utf-8")
        assert "isatty" not in source

    def test_the_env_var_is_an_explicit_act_not_an_inference(self, flag_db, monkeypatch):
        """Exporting it is explicit; ``=0`` is a statement, not an absence."""
        assert is_unattended(SESSION) is False

        monkeypatch.setenv(ENV_UNATTENDED, "1")
        state = unattended_mod.resolve(SESSION)
        assert state.unattended is True
        assert state.source == SOURCE_ENV

        monkeypatch.setenv(ENV_UNATTENDED, "0")
        assert is_unattended(SESSION) is False

        monkeypatch.setenv(ENV_UNATTENDED, "   ")
        assert unattended_mod.resolve(SESSION).source == SOURCE_DEFAULT

    def test_a_stored_flag_outranks_the_env(self, flag_db, monkeypatch):
        """The session's own recorded decision wins over a process-wide default."""
        set_unattended(SESSION, False, reason="explicitly attended")
        monkeypatch.setenv(ENV_UNATTENDED, "1")
        assert is_unattended(SESSION) is False

    def test_an_unknown_source_is_refused(self, flag_db):
        with pytest.raises(ValueError, match="unknown source"):
            set_unattended(SESSION, True, source="because-there-was-no-tty")

    def test_a_flag_needs_a_session_to_belong_to(self, flag_db, monkeypatch):
        for key in ("ICDEV_SESSION_ID", "CLAUDE_SESSION_ID"):
            monkeypatch.delenv(key, raising=False)
        with pytest.raises(ValueError, match="session id is required"):
            set_unattended("", True)


# ---------------------------------------------------------------------------
# The CLI surface
# ---------------------------------------------------------------------------
class TestCli:
    def test_set_show_list_and_clear_round_trip(self, flag_db, capsys):
        assert unattended_mod.main(["--set", SESSION, "--on", "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["unattended"] is True

        assert unattended_mod.main(["--show", SESSION, "--json"]) == 0
        assert json.loads(capsys.readouterr().out)["unattended"] is True

        assert unattended_mod.main(["--list", "--json"]) == 0
        assert len(json.loads(capsys.readouterr().out)) == 1

        assert unattended_mod.main(["--clear", SESSION, "--json"]) == 0
        assert json.loads(capsys.readouterr().out)["removed"] is True

    def test_setting_it_requires_an_unambiguous_answer(self, flag_db):
        """Enabling unattended is an explicit act, so the command may not guess."""
        with pytest.raises(SystemExit):
            unattended_mod.main(["--set", SESSION, "--json"])
        with pytest.raises(SystemExit):
            unattended_mod.main(["--set", SESSION, "--on", "--off", "--json"])

    def test_the_surface_command_prints_the_invariant(self, flag_db, capsys):
        assert unattended_mod.main(["--surface", "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert UNKNOWN in payload["require_approval_tiers"]
        assert payload["default_tier"] == UNKNOWN
