# CUI // SP-CTI
"""The Migration Canvas reflex must mint a STABLE kanban id per finding.

Regression guard for mc-reflex-d64e568f: the reflex re-runs every 24h and
re-reports every finding that is still open.  The kanban INSERT is
`INSERT OR IGNORE` (→ `ON CONFLICT DO NOTHING` on PostgreSQL), so the id is the
only thing standing between "re-report" and "new card".  A uuid4-per-run id
defeated it entirely: 60 standing findings had produced 291 board cards, 168 of
which the runner auto-promoted to `scheduled` and dispatched agent sessions
against.
"""
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.genesis.reflexes.migration_canvas import _task_id


def _stale_session(sid):
    return {"type": "stale_migration_session", "session_id": sid, "message": f"Session {sid} ..."}


def _stale_plan(sid, protocol):
    return {
        "type": "stale_protocol_plan",
        "session_id": sid,
        "protocol": protocol,
        "message": f"Protocol plan for {protocol} in session {sid} ...",
    }


class TestTaskIdIsStable:
    def test_same_finding_yields_same_id(self):
        # The property that makes ON CONFLICT DO NOTHING work across runs.
        a = _task_id(_stale_session("nmig-1252a784a8fd"))
        b = _task_id(_stale_session("nmig-1252a784a8fd"))
        assert a == b

    def test_id_survives_message_rewording(self):
        # Confidence/day-count drift into the message on every run; the id must
        # key off finding identity, not off prose that changes daily.
        base = _stale_session("nmig-1252a784a8fd")
        drifted = dict(base, message="Session nmig-1252a784a8fd ... for 46 days")
        assert _task_id(base) == _task_id(drifted)

    def test_keeps_mc_reflex_prefix(self):
        # The board and this test suite both select cards by the id prefix.
        assert _task_id(_stale_session("nmig-abc")).startswith("mc-reflex-")


class TestTaskIdDiscriminates:
    def test_distinct_sessions_get_distinct_ids(self):
        assert _task_id(_stale_session("nmig-aaa")) != _task_id(_stale_session("nmig-bbb"))

    def test_distinct_types_on_same_session_get_distinct_ids(self):
        sid = "nmig-1252a784a8fd"
        assert _task_id(_stale_session(sid)) != _task_id(_stale_plan(sid, "vlan"))

    def test_distinct_protocols_in_one_session_get_distinct_ids(self):
        # One session can hold several draft plans; each is its own card, so
        # session_id alone is NOT a sufficient key for stale_protocol_plan.
        sid = "nmig-12c5241b6621"
        assert _task_id(_stale_plan(sid, "vlan")) != _task_id(_stale_plan(sid, "ospf"))

    def test_device_findings_keyed_by_device(self):
        a = {"type": "eol_no_migration", "device_id": "dev-1", "message": "..."}
        b = {"type": "eol_no_migration", "device_id": "dev-2", "message": "..."}
        assert _task_id(a) != _task_id(b)
