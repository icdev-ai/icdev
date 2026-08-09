# CUI // SP-CTI
"""The four agent-facing self-suspension tools (agov-wake-02).

Three things have to be true, and each has a class here:

  1. Each tool actually writes the row it claims to. A tool that returns
     "suspended: wake-abc" and persisted nothing is the worst possible failure
     here — the agent stops working and nothing will ever resume it, and the
     string it returned says otherwise.
  2. The bounds refuse BEFORE the INSERT. A sleep past the cap and a ``when``
     already in the past must both leave the table empty; a rejection that still
     wrote a row would park a session exactly as effectively as no check at all.
  3. All four classify ``reversible``, and none is a ``command_tool``. That tier
     is what exempts them from content escalation, which is not cosmetic: the
     most natural ``note`` an agent writes names what it is waiting for, and
     "waiting for the git push to finish" matches the ``git\\s+push``
     irreversible pattern. Without the exemption the tool that exists to suspend
     an unattended session halts it for human approval instead. The
     ``command_tools`` assertion is the other half — a name added there would
     both re-enable that escalation and let a downgrade pattern lower some other
     call's tier.

The table is built from the agov-wake-01 migration's own ``up.sql``, the same as
``tests/test_agent_wake_store.py``, so these exercise the real INSERT against the
real schema rather than a hand-written stand-in.
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from tests._sql_compat import translating
from tools.agent_runtime import approval_gate, wake_tools
from tools.agent_runtime.wake import (
    KIND_COMPLETION,
    KIND_EVENT,
    KIND_TIMER,
    STATE_PENDING,
    TABLE,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATION = "20260809221051_agov_agent_wakes"

SESSION = "sess-agov-wake-tools"

#: The tools under test, and the tier the card requires for all four.
FOUR = ("sleep_for", "sleep_until", "wake_on", "wake_on_event")


# ---------------------------------------------------------------------------
# Fixtures — the real table, behind the production %s translation
# ---------------------------------------------------------------------------
def _migration_ddl() -> str:
    path = REPO_ROOT / "tools" / "db" / "migrations" / MIGRATION / "up.sql"
    return path.read_text(encoding="utf-8")


def _translating_conn(raw: sqlite3.Connection):
    """The connection handed to the code under test.

    A named factory rather than an inline ``translating(...)`` because
    ``coherence_checker.check_test_db_isolation`` seeds its safe-name set from
    local factory FUNCTIONS.
    """
    return translating(raw, unclosable=True)


def _storage_module():
    """The module ``wake`` actually resolves ``get_connection`` from.

    ``tools.db.storage`` in ``sys.modules`` is the compat shim, while
    ``import tools.db.storage`` binds the canonical ``icdev.tools.db.storage``.
    ``wake._connect`` imports the shim, so patching the canonical module would
    patch nothing and every test below would assert its own no-op.
    """
    return sys.modules["tools.db.storage"]


@pytest.fixture
def wake_db(monkeypatch, tmp_path):
    raw = sqlite3.connect(str(tmp_path / "wake_tools.db"))
    raw.executescript(_migration_ddl())
    conn = _translating_conn(raw)
    monkeypatch.setattr(_storage_module(), "get_connection", lambda *a, **k: conn)
    yield raw
    raw.close()


def _rows(raw: sqlite3.Connection) -> list[dict[str, Any]]:
    cur = raw.execute(f"SELECT * FROM {TABLE}")
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _one(raw: sqlite3.Connection) -> dict[str, Any]:
    rows = _rows(raw)
    assert len(rows) == 1, f"expected exactly one wake row, got {rows}"
    return rows[0]


@pytest.fixture
def fresh_config():
    """Drop the memoized ``AgentRuntimeConfig``, before AND after the test.

    ``load_config`` caches process-wide, so a test that changes
    ``ICDEV_SAG_MAX_SLEEP_SECONDS`` would otherwise read a stale cap — and would
    leave its own cap behind for whatever runs next once monkeypatch reverts the
    env var.
    """
    from tools.agent_runtime import config as config_module

    config_module.reset_cache()
    yield config_module.reset_cache
    config_module.reset_cache()


@pytest.fixture
def policy():
    """A freshly loaded policy, so an edit to the YAML is what is tested.

    ``load_policy`` memoizes process-wide and another test may have populated
    the cache from a different working directory.
    """
    return approval_gate.load_policy(refresh=True)


# ---------------------------------------------------------------------------
# 1. Each tool writes the row it claims to
# ---------------------------------------------------------------------------
class TestRowCreation:
    def test_sleep_for_creates_a_pending_timer(self, wake_db):
        before = datetime.now(timezone.utc)
        out = wake_tools.sleep_for(600, note="waiting for CI", session_id=SESSION)

        row = _one(wake_db)
        assert row["kind"] == KIND_TIMER
        assert row["state"] == STATE_PENDING
        assert row["session_id"] == SESSION
        assert row["note"] == "waiting for CI"
        assert row["job_id"] is None and row["event_key"] is None
        fire_at = datetime.fromisoformat(row["fire_at"])
        assert before + timedelta(seconds=595) <= fire_at <= before + timedelta(seconds=650)
        assert row["wake_id"] in out

    def test_sleep_until_creates_a_timer_at_the_time_asked_for(self, wake_db):
        target = datetime.now(timezone.utc) + timedelta(hours=2)
        out = wake_tools.sleep_until(target.isoformat(), note="overnight", session_id=SESSION)

        row = _one(wake_db)
        assert row["kind"] == KIND_TIMER
        assert row["state"] == STATE_PENDING
        assert datetime.fromisoformat(row["fire_at"]) == target
        assert row["wake_id"] in out

    def test_sleep_until_reads_a_naive_timestamp_as_utc(self, wake_db):
        target = datetime.now(timezone.utc) + timedelta(hours=1)
        wake_tools.sleep_until(target.replace(tzinfo=None).isoformat(), session_id=SESSION)

        assert datetime.fromisoformat(_one(wake_db)["fire_at"]) == target

    def test_wake_on_creates_a_completion_wake(self, wake_db):
        out = wake_tools.wake_on("job-42", note="build", session_id=SESSION)

        row = _one(wake_db)
        assert row["kind"] == KIND_COMPLETION
        assert row["state"] == STATE_PENDING
        assert row["job_id"] == "job-42"
        assert row["fire_at"] is None and row["event_key"] is None
        assert row["wake_id"] in out

    def test_wake_on_event_creates_an_event_wake(self, wake_db):
        out = wake_tools.wake_on_event("pr:1342:ci_green", note="CI", session_id=SESSION)

        row = _one(wake_db)
        assert row["kind"] == KIND_EVENT
        assert row["state"] == STATE_PENDING
        assert row["event_key"] == "pr:1342:ci_green"
        assert row["fire_at"] is None and row["job_id"] is None
        assert row["wake_id"] in out

    def test_every_tool_tells_the_model_to_stop(self, wake_db):
        """The row is only half of it — the model has to be told to end the turn.

        Without this the agent reads "suspended: wake-abc" as bookkeeping and
        carries straight on with the turn it just suspended, which is the exact
        polling behaviour these tools exist to replace.
        """
        for out in (
            wake_tools.sleep_for(60, session_id=SESSION),
            wake_tools.sleep_until(
                (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
                session_id=SESSION,
            ),
            wake_tools.wake_on("job-1", session_id=SESSION),
            wake_tools.wake_on_event("evt", session_id=SESSION),
        ):
            assert out.startswith("suspended: ")
            assert "Stop working now" in out
            assert "Do not poll" in out


# ---------------------------------------------------------------------------
# 2. The bounds refuse before the INSERT
# ---------------------------------------------------------------------------
class TestBounds:
    def test_sleep_until_in_the_past_is_rejected_and_writes_nothing(self, wake_db):
        past = datetime.now(timezone.utc) - timedelta(hours=1)

        out = wake_tools.sleep_until(past.isoformat(), session_id=SESSION)

        assert out.startswith("error:")
        assert "past" in out
        assert _rows(wake_db) == []

    def test_sleep_for_beyond_the_cap_is_rejected_and_writes_nothing(self, wake_db):
        cap = wake_tools.max_sleep_seconds()

        out = wake_tools.sleep_for(cap + 1, session_id=SESSION)

        assert out.startswith("error:")
        assert str(cap) in out
        assert _rows(wake_db) == []

    def test_sleep_until_beyond_the_cap_is_rejected_and_writes_nothing(self, wake_db):
        """A far-future `when` parks a session exactly as well as a long sleep.

        The cap is on the SLEEP, not on the spelling of it — otherwise refusing
        sleep_for(a year) while allowing sleep_until(next year) is a bound in
        name only.
        """
        cap = wake_tools.max_sleep_seconds()
        far = datetime.now(timezone.utc) + timedelta(seconds=cap + 3600)

        out = wake_tools.sleep_until(far.isoformat(), session_id=SESSION)

        assert out.startswith("error:")
        assert str(cap) in out
        assert _rows(wake_db) == []

    @pytest.mark.parametrize("seconds", [0, -1, -3600])
    def test_a_non_positive_sleep_is_rejected_and_writes_nothing(self, wake_db, seconds):
        out = wake_tools.sleep_for(seconds, session_id=SESSION)

        assert out.startswith("error:")
        assert _rows(wake_db) == []

    @pytest.mark.parametrize("seconds", ["soon", None, float("nan"), float("inf")])
    def test_an_unusable_duration_is_rejected_and_writes_nothing(self, wake_db, seconds):
        out = wake_tools.sleep_for(seconds, session_id=SESSION)

        assert out.startswith("error:")
        assert _rows(wake_db) == []

    def test_an_unparseable_when_is_rejected_and_writes_nothing(self, wake_db):
        out = wake_tools.sleep_until("next tuesday", session_id=SESSION)

        assert out.startswith("error:")
        assert _rows(wake_db) == []

    @pytest.mark.parametrize("blank", ["", "   ", None])
    def test_a_blank_condition_is_rejected_and_writes_nothing(self, wake_db, blank):
        assert wake_tools.wake_on(blank, session_id=SESSION).startswith("error:")
        assert wake_tools.wake_on_event(blank, session_id=SESSION).startswith("error:")
        assert wake_tools.sleep_until(blank, session_id=SESSION).startswith("error:")
        assert _rows(wake_db) == []

    def test_the_cap_is_configurable(self, wake_db, monkeypatch, fresh_config):
        """The env var moves the bound — it is config, not a constant in code."""
        monkeypatch.setenv(wake_tools.MAX_SLEEP_ENV, "120")
        fresh_config()

        assert wake_tools.max_sleep_seconds() == 120
        assert wake_tools.sleep_for(121, session_id=SESSION).startswith("error:")
        assert _rows(wake_db) == []
        assert wake_tools.sleep_for(119, session_id=SESSION).startswith("suspended:")
        assert len(_rows(wake_db)) == 1

    @pytest.mark.parametrize("bad", ["0", "-1", "not-a-number"])
    def test_an_unusable_cap_falls_back_to_the_default_not_to_unbounded(
        self, monkeypatch, bad, fresh_config
    ):
        """A cap a typo can switch off is not a cap."""
        monkeypatch.setenv(wake_tools.MAX_SLEEP_ENV, bad)
        fresh_config()

        assert wake_tools.max_sleep_seconds() == wake_tools.DEFAULT_MAX_SLEEP_SECONDS


# ---------------------------------------------------------------------------
# 3. The approval tier
# ---------------------------------------------------------------------------
class TestApprovalTier:
    @pytest.mark.parametrize("name", FOUR)
    def test_classifies_reversible(self, name, policy):
        cls = approval_gate.classify(name, {}, policy=policy)

        assert cls.tier == approval_gate.REVERSIBLE
        assert cls.requires_approval is False

    @pytest.mark.parametrize("name", FOUR)
    def test_is_not_a_command_tool(self, name, policy):
        """A shell never gets the escalation exemption — and none of these is one.

        Adding a name here would silently disable the property the tier was
        granted for, and would additionally let a DOWNGRADE pattern lower an
        unrelated call's tier.
        """
        assert name.lower() not in {
            str(n).lower() for n in (policy.get("command_tools") or [])
        }

    @pytest.mark.parametrize(
        "note",
        [
            "waiting for the git push to finish",
            "sleeping until gh pr merge completes",
            "waiting for terraform apply",
            "until the rm -rf cleanup job is done",
        ],
    )
    def test_a_note_naming_an_irreversible_action_does_not_escalate(self, note, policy):
        """The reason these are `reversible` rather than merely auto-allowed.

        Escalation runs the irreversible patterns against the flattened input of
        every call. The single most natural note an agent writes names what it
        is waiting for, and those names match. Without the exemption, the tool
        whose purpose is to suspend an UNATTENDED session halts it for human
        approval — and there is nobody there.
        """
        cls = approval_gate.classify("sleep_for", {"seconds": 600, "note": note}, policy=policy)

        assert cls.tier == approval_gate.REVERSIBLE
        assert cls.rule == "reversible_tool"
        assert cls.requires_approval is False

    def test_the_exemption_is_not_extended_to_a_shell(self, policy):
        """Guardrail on the test above: escalation still works where it must."""
        cls = approval_gate.classify(
            "run_command", {"command": "git push origin main"}, policy=policy
        )

        assert cls.tier == approval_gate.IRREVERSIBLE
        assert cls.requires_approval is True


# ---------------------------------------------------------------------------
# 4. Registration
# ---------------------------------------------------------------------------
class TestRegistration:
    @pytest.mark.parametrize("name", FOUR)
    def test_is_in_the_builtin_toolset(self, name):
        from tools.agent_runtime.builtin_tools import build_builtin_toolset

        tools, handlers = build_builtin_toolset()

        assert name in handlers
        assert name in {t["function"]["name"] for t in tools}

    @pytest.mark.parametrize("name", FOUR)
    def test_is_in_the_wake_bundle(self, name):
        from tools.agent_runtime.toolsets import resolve_bundles

        assert name in resolve_bundles(["wake"])

    def test_the_wake_bundle_is_exactly_these_four(self):
        from tools.agent_runtime.toolsets import resolve_bundles

        assert resolve_bundles(["wake"]) == set(FOUR)

    @pytest.mark.parametrize("name", FOUR)
    def test_the_schema_is_honest_about_writing(self, name):
        """`is_read_only` is a claim about STATE; the tier is a claim about REACH.

        These write a row, so the schema says so — the agent loop uses that flag
        to decide what may be dispatched in parallel. The separate claim that
        they cannot ACT lives in the operator's policy file, which is exactly
        why the approval gate trusts it and does not trust this one.
        """
        schema = wake_tools.SCHEMAS[name]

        assert schema["is_read_only"] is False
        assert schema["function"]["is_read_only"] is False

    def test_the_handlers_honour_the_agent_loop_contract(self, wake_db, monkeypatch):
        """``handler(input_dict, stop_event) -> str``, and the row lands."""
        monkeypatch.setenv("ICDEV_SESSION_ID", SESSION)
        _, handlers = wake_tools.build_wake_toolset()

        assert handlers["sleep_for"]({"seconds": 60, "note": "n"}, None).startswith(
            "suspended:"
        )
        assert handlers["wake_on"]({"job_id": "j1"}, None).startswith("suspended:")
        assert handlers["wake_on_event"]({"event_key": "e1"}, None).startswith(
            "suspended:"
        )
        assert handlers["sleep_until"](
            {"when": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()},
            None,
        ).startswith("suspended:")

        rows = _rows(wake_db)
        assert len(rows) == 4
        assert {r["session_id"] for r in rows} == {SESSION}

    def test_the_names_are_declared_once(self):
        assert set(wake_tools.TOOL_NAMES) == set(FOUR)
        assert wake_tools.wake_tool_names() == sorted(FOUR)


# ---------------------------------------------------------------------------
# 5. A store that will not accept the row is reported, not swallowed
# ---------------------------------------------------------------------------
class TestStoreFailure:
    def test_an_unavailable_store_returns_an_error_rather_than_suspending(
        self, monkeypatch
    ):
        """An agent that suspends against a store that refused the row never returns.

        The handler must not report success, and must not raise into the loop.
        """
        from tools.agent_runtime import wake as wake_module

        def _boom(*_a, **_k):
            raise wake_module.WakeStoreUnavailable("database is gone")

        monkeypatch.setattr(wake_module, "add_timer_in", _boom)

        out = wake_tools.sleep_for(60, session_id=SESSION)

        assert out.startswith("error:")
        assert "database is gone" in out
