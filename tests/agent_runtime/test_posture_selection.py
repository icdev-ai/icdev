# CUI // SP-CTI
"""Unit tests for posture selection as recorded operator intent (hcx-post-02).

hcx-post-01 named the combination of safety knobs. This is the half that makes a
CHOICE of one auditable, and four properties carry the whole card:

1. **Selecting appends a log-only event, before anything moves.** The payload
   names the posture, the actor and the RESOLVED knob values —
   :func:`test_selecting_appends_the_event_before_it_applies` pins the ordering
   by asserting nothing has been applied at the moment the appender is called.

2. **Re-selecting the effective posture appends nothing.** A look is not a
   decision, and a log that grew every time somebody typed ``/posture`` would
   bury the rows that matter.

3. **A knob pinned above the posture layer does not move, and says so.**
   hcx-post-01 put the posture at the bottom of the precedence chain; this
   card's selection must not quietly rewrite the four per-knob environment
   variables to get its way. It under-delivers LOUDLY instead.

4. **An unwritable log refuses to WIDEN, and only to widen.** No unaudited
   ``danger-full-access``. Refusing in the tightening direction too would leave
   an operator stuck in the looser posture whenever the database is unreachable
   — the audit log failing closed onto the less safe state.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import tools.agent_runtime.commands as commands
import tools.agent_runtime.config as config_mod
import tools.agent_runtime.posture_selection as ps
from tools.agent_runtime.event_log import EVENT_TYPES

REPO_ROOT = Path(__file__).resolve().parents[2]


def _translating_conn(raw):
    """The connection handed to the code under test.

    ``unclosable``: ``event_log`` closes its connection in a ``finally``, and the
    fixture's has to outlive that so the assertions can still read the rows.

    A named factory rather than an inline ``translating(...)`` because
    ``coherence_checker.check_test_db_isolation`` seeds its safe-name set from
    local factory FUNCTIONS — handing runtime code a RAW sqlite3 connection
    makes every ``%s`` placeholder raise ``near "%": syntax error``, and this
    module's ``append`` would report that as an unwritable log rather than as a
    broken test.
    """
    from tests._sql_compat import translating

    return translating(raw, unclosable=True)


class _FakeEvent:
    def __init__(self, event_id: str = "ase-test") -> None:
        self.event_id = event_id


class _Recorder:
    """An appender that records its calls instead of touching a database."""

    def __init__(self, fail: str = "", watch: "dict | None" = None) -> None:
        self.calls: list[dict] = []
        self.fail = fail
        #: The environment the selection applies to, snapshotted at the moment
        #: the first event is written — this is how the ordering assertion sees
        #: whether the apply had already happened.
        self.watch = watch
        self.env_at_first_call: "dict | None" = None

    def __call__(self, session_id, event_type, payload, **kwargs):
        if not self.calls and self.watch is not None:
            self.env_at_first_call = dict(self.watch)
        self.calls.append(
            {
                "session_id": session_id,
                "event_type": event_type,
                "payload": payload,
                "kwargs": kwargs,
            }
        )
        if self.fail:
            raise RuntimeError(self.fail)
        return _FakeEvent()


#: Every variable that can pin a posture-governed knob, plus the selector.
_POSTURE_ENV = (
    "ICDEV_PERMISSION_POSTURE",
    "ICDEV_SAG_SANDBOX_MODE",
    "ICDEV_SAG_APPROVAL_MODE",
    "ICDEV_AGENT_APPROVAL_MODE",
    "ICDEV_SAG_ALLOW_MUTATION",
    "ICDEV_AGENT_RUNTIME_CONFIG",
)


@pytest.fixture(autouse=True)
def _clean_posture_env():
    """Every posture-governed variable unset, and the config cache dropped.

    Snapshot-and-restore rather than ``monkeypatch.delenv``, and this is the
    reason: ``delenv(..., raising=False)`` on a variable that is ALREADY absent
    records nothing, so a test that then sets it — which is exactly what
    ``select_posture`` does when no ``environ`` is passed — leaks it into every
    later test in the process. That leak turned two unrelated files
    (``test_safety``, ``test_toolsets_dispatch``) red while this file passed
    alone, which is the in-suite half of the order-dependence CLAUDE.md requires
    both runs to catch.

    Restoring on the way in as well as out also stops a developer's exported
    ``ICDEV_SAG_*`` from pinning a knob and reading as a failure of this module.
    """
    import os

    saved = {name: os.environ.get(name) for name in _POSTURE_ENV}
    for name in _POSTURE_ENV:
        os.environ.pop(name, None)
    config_mod.reset_cache()
    try:
        yield
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        config_mod.reset_cache()


# ---------------------------------------------------------------------------
# The vocabulary
# ---------------------------------------------------------------------------
def test_permission_posture_is_a_member_of_the_event_vocabulary():
    """Without this the append raises ValueError and nothing is ever recorded."""
    assert ps.EVENT_TYPE in EVENT_TYPES


def test_every_knob_this_module_tracks_is_declared_by_the_shipped_postures():
    """A knob with no key in the posture file could never appear in a delta."""
    postures = config_mod.load_postures().postures
    for name, values in postures.items():
        for knob in ps.KNOBS:
            assert knob.key in values, f"posture {name} declares no {knob.key}"


# ---------------------------------------------------------------------------
# 1. The event, and its ordering
# ---------------------------------------------------------------------------
def test_selecting_appends_the_event_before_it_applies():
    env: dict[str, str] = {}
    rec = _Recorder(watch=env)
    result = ps.select_posture(
        "danger-full-access", actor="alice", session_id="ctx-1",
        appender=rec, environ=env,
    )

    assert result.applied is True
    assert result.logged is True
    assert len(rec.calls) == 1
    # The event was written while the posture had NOT yet been applied. An
    # intent recorded after the act cannot survive a crash during it.
    assert rec.env_at_first_call == {}
    assert env == {config_mod.ENV_POSTURE: "danger-full-access"}


def test_the_event_carries_the_posture_the_actor_and_the_resolved_knobs():
    rec = _Recorder()
    ps.select_posture(
        "danger-full-access", actor="alice", session_id="ctx-1",
        appender=rec, environ={},
    )
    call = rec.calls[0]

    assert call["session_id"] == "ctx-1"
    assert call["event_type"] == "permission_posture"
    payload = call["payload"]
    assert payload["posture"] == "danger-full-access"
    assert payload["previous_posture"] == "workspace-write"
    assert payload["actor"] == "alice"
    # The RESOLVED values, which is what the run actually operates under.
    assert payload["knobs"] == {
        "sandbox": "danger-full-access",
        "approval_mode": "off",
        "command_approval_mode": "off",
        "allow_mutation": True,
    }
    assert {c["knob"] for c in payload["changes"]} == {
        "sandbox",
        "approval_mode",
        "command_approval_mode",
        "allow_mutation",
    }


def test_a_missing_actor_is_recorded_as_unknown_rather_than_omitted():
    """A caller that forgot to pass one must be visible, not invisible."""
    rec = _Recorder()
    ps.select_posture(
        "danger-full-access", session_id="ctx-1", appender=rec, environ={}
    )
    assert rec.calls[0]["payload"]["actor"] == "unknown"


def test_the_event_is_log_only_and_changes_no_knob(monkeypatch):
    """The knobs move because of the apply, never because of the event.

    Proved by making the appender the ONLY thing that runs — a refused
    selection writes the event and applies nothing, and the knobs are unmoved.
    """
    rec = _Recorder(fail="log down")
    before = ps.effective_knobs()
    result = ps.select_posture(
        "danger-full-access", actor="alice", session_id="ctx-1", appender=rec,
        environ={},
    )
    assert rec.calls, "the event was attempted"
    assert result.applied is False
    config_mod.reset_cache()
    assert ps.effective_knobs() == before


# ---------------------------------------------------------------------------
# 2. Re-selecting the effective posture
# ---------------------------------------------------------------------------
def test_reselecting_the_effective_posture_appends_nothing():
    rec = _Recorder()
    result = ps.select_posture(
        "workspace-write", actor="alice", session_id="ctx-1", appender=rec,
        environ={},
    )
    assert result.changed is False
    assert result.applied is False
    assert result.logged is False
    assert rec.calls == []


def test_reselecting_after_a_real_selection_also_appends_nothing(monkeypatch):
    env: dict[str, str] = {}
    rec = _Recorder()
    first = ps.select_posture(
        "danger-full-access", actor="alice", session_id="ctx-1",
        appender=rec, environ=env,
    )
    assert first.applied is True
    assert env[config_mod.ENV_POSTURE] == "danger-full-access"

    # Make that selection the live one, then re-select it.
    monkeypatch.setenv(config_mod.ENV_POSTURE, "danger-full-access")
    second = ps.select_posture(
        "danger-full-access", actor="alice", session_id="ctx-1", appender=rec,
        environ={},
    )
    assert second.changed is False
    assert len(rec.calls) == 1, "the second selection appended a second row"


def test_an_unknown_posture_is_refused_and_records_nothing():
    rec = _Recorder()
    result = ps.select_posture(
        "nonsense", actor="alice", session_id="ctx-1", appender=rec, environ={}
    )
    assert result.refused == ps.REFUSED_UNKNOWN
    assert result.applied is False
    assert result.posture == "workspace-write"
    assert rec.calls == []
    assert "nonsense" in result.reason


# ---------------------------------------------------------------------------
# 3. A pinned knob does not move, and says so
# ---------------------------------------------------------------------------
def test_a_pinned_knob_does_not_move_and_is_reported(monkeypatch):
    """Selecting must NOT rewrite a per-knob variable an operator exported."""
    monkeypatch.setenv("ICDEV_SAG_APPROVAL_MODE", "manual")
    config_mod.reset_cache()

    env: dict[str, str] = {}
    rec = _Recorder()
    result = ps.select_posture(
        "danger-full-access", actor="alice", session_id="ctx-1",
        appender=rec, environ=env,
    )

    assert result.applied is True
    # The selection wrote ONE variable and left the operator's alone.
    assert env == {config_mod.ENV_POSTURE: "danger-full-access"}

    pinned = {d.knob: d.pinned_by for d in result.pinned}
    assert pinned == {"approval_mode": "ICDEV_SAG_APPROVAL_MODE"}
    approval = next(d for d in result.deltas if d.knob == "approval_mode")
    assert approval.changed is False
    assert approval.after == "manual"
    assert approval.declared == "off"
    # And it is in the record, not only in the return value.
    assert rec.calls[0]["payload"]["pinned"] == {
        "approval_mode": "ICDEV_SAG_APPROVAL_MODE"
    }


def test_the_summary_names_the_knob_that_did_not_move(monkeypatch):
    # 0, not 1: a pin that AGREES with the posture blocks nothing and is
    # correctly not reported. Only a pin the posture disagrees with is news.
    monkeypatch.setenv("ICDEV_SAG_ALLOW_MUTATION", "0")
    config_mod.reset_cache()
    result = ps.select_posture(
        "danger-full-access", actor="alice", session_id="ctx-1",
        appender=_Recorder(), environ={},
    )
    text = result.summary()
    assert "NOT MOVED" in text
    assert "allow_mutation" in text
    assert "ICDEV_SAG_ALLOW_MUTATION" in text


def test_a_pin_that_agrees_with_the_posture_is_not_reported(monkeypatch):
    """``pinned`` means BLOCKED, not merely 'set somewhere else'.

    Reporting a variable that holds the value the posture wanted anyway would
    train an operator to ignore the line that matters.
    """
    monkeypatch.setenv("ICDEV_SAG_ALLOW_MUTATION", "1")
    config_mod.reset_cache()
    result = ps.select_posture(
        "danger-full-access", actor="alice", session_id="ctx-1",
        appender=_Recorder(), environ={},
    )
    assert result.pinned == ()


def test_an_explicit_config_key_is_reported_as_the_holder(monkeypatch, tmp_path):
    """A pin can come from agent_runtime.yaml as well as from the environment."""
    cfg_path = tmp_path / "agent_runtime.yaml"
    with cfg_path.open("w", encoding="utf-8", newline="") as fh:
        fh.write("subsystems:\n  approval:\n    mode: manual\n")
    monkeypatch.setenv(config_mod.ENV_CONFIG_PATH, str(cfg_path))
    config_mod.reset_cache()

    result = ps.select_posture(
        "danger-full-access", actor="alice", session_id="ctx-1",
        appender=_Recorder(), environ={},
    )
    pinned = {d.knob: d.pinned_by for d in result.pinned}
    assert pinned == {"approval_mode": "agent_runtime.yaml:subsystems.approval.mode"}


def test_knob_deltas_do_not_mutate_the_process(monkeypatch):
    """Computing 'what would happen' must not be a way of making it happen."""
    import os

    before = ps.effective_knobs()
    deltas = ps.knob_deltas("danger-full-access")
    assert any(d.changed for d in deltas)
    assert config_mod.ENV_POSTURE not in os.environ
    assert ps.effective_knobs() == before


# ---------------------------------------------------------------------------
# 4. An unwritable log
# ---------------------------------------------------------------------------
def test_an_unwritable_log_refuses_to_widen():
    result = ps.select_posture(
        "danger-full-access", actor="alice", session_id="ctx-1",
        appender=_Recorder(fail="database is locked"), environ={},
    )
    assert result.refused == ps.REFUSED_UNAUDITED_WIDENING
    assert result.applied is False
    assert result.logged is False
    assert result.posture == "workspace-write"
    assert "database is locked" in result.reason


def test_an_unwritable_log_still_allows_tightening(monkeypatch):
    """Refusing here would strand the operator in the LOOSER posture."""
    monkeypatch.setenv(config_mod.ENV_POSTURE, "danger-full-access")
    config_mod.reset_cache()

    env: dict[str, str] = {}
    result = ps.select_posture(
        "workspace-write", actor="alice", session_id="ctx-1",
        appender=_Recorder(fail="database is locked"), environ=env,
    )
    assert result.refused == ""
    assert result.applied is True
    assert result.logged is False
    assert env[config_mod.ENV_POSTURE] == "workspace-write"
    assert "NOT recorded" in result.summary()


def test_no_session_id_is_treated_as_an_unwritable_log():
    """There is nowhere to file the event, which is the same fact."""
    result = ps.select_posture(
        "danger-full-access", actor="alice", session_id="", environ={}
    )
    assert result.refused == ps.REFUSED_UNAUDITED_WIDENING
    assert result.applied is False


def test_select_posture_never_raises_for_an_operator_error():
    """A REPL must not answer a typo with a traceback."""
    for name in ("", "   ", "Workspace-Write", "danger_full_access"):
        result = ps.select_posture(name, actor="a", session_id="ctx-1", environ={})
        assert result.refused == ps.REFUSED_UNKNOWN


# ---------------------------------------------------------------------------
# Runtime visibility — a posture nobody can see is a posture nobody checks
# ---------------------------------------------------------------------------
def test_describe_reports_the_posture_its_source_and_its_knobs():
    report = ps.describe()
    assert report["posture"] == "workspace-write"
    assert set(report["knobs"]) == {k.key for k in ps.KNOBS}
    assert report["pinned"] == {}
    assert "danger-full-access" in report["available"]


def test_posture_is_registered_as_a_slash_command():
    assert "/posture" in commands.REGISTRY
    assert "/posture" in commands.command_names()


def test_the_commands_docstring_documents_the_new_command():
    """test_goal_commands asserts this too; stated here because it is the
    difference between a shipped command and an invisible one."""
    assert "``/posture``" in (commands.__doc__ or "")


def test_usage_surfaces_the_posture():
    class _Session:
        context_id = "ctx-1"

        def usage(self):
            return {
                "turns": 1, "input_tokens": 2, "output_tokens": 3,
                "total_tokens": 5, "cost_usd": 0.0, "session_id": "s1",
            }

    class _Runtime:
        session = _Session()
        user_id = "alice"
        tenant_id = ""

    text, should_exit = commands._cmd_usage(_Runtime(), "")
    assert should_exit is False
    assert "Posture — workspace-write" in text
    assert "approval_mode" in text


def test_the_posture_command_shows_without_selecting():
    class _Runtime:
        user_id = "alice"
        tenant_id = ""
        session = type("S", (), {"context_id": "ctx-1"})()

    handled, text, should_exit = commands.dispatch(_Runtime(), "/posture")
    assert handled is True and should_exit is False
    assert "Posture: workspace-write" in text
    assert "allow_mutation" in text


def test_the_posture_command_lists_postures():
    handled, text, _ = commands.dispatch(object(), "/posture list")
    assert handled is True
    assert "danger-full-access" in text
    assert "explicit selection only" in text


# ---------------------------------------------------------------------------
# The real write — every test above injects the appender
# ---------------------------------------------------------------------------
@pytest.fixture
def event_db(monkeypatch, tmp_path):
    """The real ``agent_session_events`` table, from the migration's own DDL.

    Every other test in this file hands ``select_posture`` a fake appender, which
    proves the decision logic and proves nothing at all about persistence. A
    capability that is registered, importable and never actually writes a row is
    this platform's signature defect, so one test drives the genuine
    ``event_log.append`` against a real table.

    ``sys.modules["tools.db.storage"]`` and ``icdev.tools.db.storage`` are two
    distinct module objects; ``event_log._connect`` imports from the former
    inside the function, so that is the binding to replace. Patching the other
    one installs a fake nothing calls and the write lands on the LIVE board.
    """
    import sqlite3
    import sys

    ddl = (
        REPO_ROOT / "tools" / "db" / "migrations"
        / "20260816122036_agent_session_events" / "up.sql"
    ).read_text(encoding="utf-8")
    raw = sqlite3.connect(str(tmp_path / "events.db"))
    raw.executescript(ddl)
    conn = _translating_conn(raw)

    storage = sys.modules["tools.db.storage"]
    monkeypatch.setattr(storage, "get_connection", lambda *a, **k: conn)
    monkeypatch.setattr(storage, "table_exists", lambda c, t: True)
    monkeypatch.delenv("ICDEV_AGENT_EVENT_PAYLOAD_RETENTION", raising=False)
    yield raw
    raw.close()


def test_a_selection_lands_a_real_row_in_agent_session_events(event_db):
    result = ps.select_posture(
        "danger-full-access", actor="alice", session_id="ctx-real", environ={}
    )
    assert result.applied is True
    assert result.logged is True
    assert result.event_id

    cur = event_db.execute(
        "SELECT event_id, session_id, seq, event_type, payload_hash, payload_json "
        "FROM agent_session_events ORDER BY seq"
    )
    rows = cur.fetchall()
    assert len(rows) == 1
    event_id, session_id, seq, event_type, payload_hash, payload_json = rows[0]
    assert event_id == result.event_id
    assert session_id == "ctx-real"
    assert seq == 1
    assert event_type == "permission_posture"
    # Always written, whatever the retention policy decides about the document.
    assert payload_hash

    payload = json.loads(payload_json)
    assert payload["posture"] == "danger-full-access"
    assert payload["actor"] == "alice"
    assert payload["previous_posture"] == "workspace-write"
    assert payload["knobs"]["approval_mode"] == "off"


def test_a_no_op_selection_writes_no_row(event_db):
    """The no-op path proved against the table, not against a spy."""
    result = ps.select_posture(
        "workspace-write", actor="alice", session_id="ctx-real", environ={}
    )
    assert result.changed is False
    assert event_db.execute(
        "SELECT COUNT(*) FROM agent_session_events"
    ).fetchone()[0] == 0


# ---------------------------------------------------------------------------
# CLI + packaging
# ---------------------------------------------------------------------------
def test_the_cli_reports_the_posture():
    proc = subprocess.run(
        [sys.executable, "-m", "tools.agent_runtime.posture_selection", "--json"],
        capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    assert report["posture"] in report["available"]


def test_the_packaged_module_mirror_has_not_drifted():
    """``icdev/tools/`` is what a wheel ships; a drifted mirror is a shipped bug."""
    for rel in (
        "tools/agent_runtime/posture_selection.py",
        "tools/agent_runtime/config.py",
        "tools/agent_runtime/commands.py",
        "tools/agent_runtime/event_log.py",
        "tools/agent_runtime/dispatch.py",
    ):
        source = (REPO_ROOT / rel).read_text(encoding="utf-8")
        mirror = (REPO_ROOT / "icdev" / rel).read_text(encoding="utf-8")
        assert source == mirror, f"{rel} has drifted from its icdev/ mirror"
