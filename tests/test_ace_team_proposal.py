# CUI // SP-CTI
"""Suggest-then-confirm for chat-initiated ACE teams.

The implicit chat trigger used to LAUNCH a team the moment a message matched
four RICOAS signals — spawning agents with read/write/execute agency off a
heuristic, with no human in the loop. It now writes a proposal and waits.

The explicit ``@team`` path is unchanged and still launches immediately; an
explicit command IS the approval. ``tests/test_ace_chat_trigger.py`` pins that
contract and must keep passing untouched.
"""
from __future__ import annotations

import pytest

# A message that clears the implicit bar: >=200 chars and >=4 RICOAS signals.
GOAL = (
    "The system shall provide a compliance dashboard. It must log every audit "
    "event to an immutable trail. We need to build the ingestion pipeline and "
    "analyze the results for anomalies. Acceptance criteria: all NIST controls "
    "mapped. The system should deploy to GovCloud without manual steps."
)


@pytest.fixture
def ace_db(tmp_path, monkeypatch):
    """Fresh temp ACE canvas DB with the suggestions table applied."""
    monkeypatch.setenv("ICDEV_ACE_DB_URL", str(tmp_path / "ace_proposal.db"))
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    from icdev.tools.ace.db.init_db import init as init_ace_db

    init_ace_db()
    return tmp_path


@pytest.fixture
def ct():
    from icdev.tools.ace import chat_trigger

    return chat_trigger


# ---------------------------------------------------------------------------
# The table must exist, and must not be registered append-only
# ---------------------------------------------------------------------------


def test_suggestions_table_is_created(ace_db, ct):
    conn = ct._conn()
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='ace_team_suggestions'"
        ).fetchall()
    finally:
        conn.close()
    assert rows, "ace_team_suggestions was not created"


def test_suggestions_table_is_not_append_only():
    """It transitions state, so registering it append-only would be wrong.

    ace_skill_candidates IS registered append-only yet skill_promoter updates it
    in place — a modelling inconsistency this table must not repeat. The durable
    record of decisions goes to ace_audit_log, which genuinely is append-only.
    """
    from pathlib import Path

    hook = Path(__file__).resolve().parents[1] / ".claude" / "hooks" / "pre_tool_use.py"
    text = hook.read_text(encoding="utf-8")
    assert '"ace_team_suggestions"' not in text
    assert '"ace_audit_log"' in text


# ---------------------------------------------------------------------------
# Proposing
# ---------------------------------------------------------------------------


def test_implicit_message_proposes_without_launching(ace_db, ct, monkeypatch):
    """The whole point: a heuristic match must not spawn agents."""
    import icdev.tools.ace.controller as ctrl

    class _Boom:
        @classmethod
        def get_instance(cls):
            raise AssertionError("proposing must never launch")

    monkeypatch.setattr(ctrl, "ACEController", _Boom)

    proposal = ct.build_team_proposal("ctx-1", GOAL, user_id="u1")

    assert proposal is not None
    assert proposal["card"] == "sme_team_suggestion"
    assert proposal["suggestion_id"].startswith("sug-")
    assert ct.get_proposal(proposal["suggestion_id"])["state"] == "proposed"


def test_explicit_team_command_is_not_proposed(ace_db, ct):
    """@team stays immediate — the caller launches it directly."""
    assert ct.build_team_proposal("ctx-1", "@team " + GOAL) is None


def test_short_message_never_proposes(ace_db, ct):
    assert ct.build_team_proposal("ctx-1", "fix this bug") is None


def test_proposal_roster_is_capped(ace_db, ct, monkeypatch):
    """Chat-spawned teams cap at 5 without lowering the global MAX_TEAM_SIZE."""
    from icdev.tools.ace.problem_classifier import RoleSlot, TeamManifest

    many = TeamManifest(slots=[RoleSlot(role_id=f"r{i}", count=1) for i in range(12)])
    monkeypatch.setattr(
        "icdev.tools.ace.problem_classifier.ProblemClassifierLens.run",
        lambda self: many,
    )

    proposal = ct.build_team_proposal("ctx-1", GOAL)
    assert len(proposal["roles"]) <= ct.MAX_CHAT_TEAM


def test_global_team_cap_is_not_lowered():
    """Lowering MAX_TEAM_SIZE would regress non-chat paths."""
    from icdev.tools.ace.constants import MAX_TEAM_SIZE

    assert MAX_TEAM_SIZE >= 8


def test_classifier_failure_still_proposes(ace_db, ct, monkeypatch):
    """A classifier outage degrades the roster, not the feature."""
    monkeypatch.setattr(ct, "_classify_roles", lambda c: [])

    proposal = ct.build_team_proposal("ctx-1", GOAL)
    assert proposal is not None
    assert proposal["roles"] == []


# ---------------------------------------------------------------------------
# Confirming
# ---------------------------------------------------------------------------


class _FakeController:
    launched: list[dict] = []

    @classmethod
    def get_instance(cls):
        return cls()

    def launch(self, **kwargs):
        _FakeController.launched.append(kwargs)
        return "ace-test123"


@pytest.fixture
def fake_ctrl(monkeypatch):
    import icdev.tools.ace.controller as ctrl

    _FakeController.launched = []
    monkeypatch.setattr(ctrl, "ACEController", _FakeController)
    return _FakeController


def test_confirm_launches_with_the_right_trigger_source(ace_db, ct, fake_ctrl):
    proposal = ct.build_team_proposal("ctx-9", GOAL, user_id="u1")
    result = ct.confirm_proposal(proposal["suggestion_id"], user_id="u1")

    assert result["ok"] is True
    assert result["instance_id"] == "ace-test123"
    assert len(fake_ctrl.launched) == 1
    assert fake_ctrl.launched[0]["trigger_source"] == "chat_suggestion"
    assert fake_ctrl.launched[0]["trigger_ref"] == "ctx-9"
    assert ct.get_proposal(proposal["suggestion_id"])["state"] == "launched"


def test_confirm_is_idempotent(ace_db, ct, fake_ctrl):
    """A double-click must not launch two teams."""
    proposal = ct.build_team_proposal("ctx-1", GOAL)
    sid = proposal["suggestion_id"]

    first = ct.confirm_proposal(sid)
    second = ct.confirm_proposal(sid)

    assert first["ok"] and second["ok"]
    assert first["instance_id"] == second["instance_id"]
    assert len(fake_ctrl.launched) == 1, "confirmed twice — launched twice"


def test_declined_proposal_cannot_be_confirmed(ace_db, ct, fake_ctrl):
    proposal = ct.build_team_proposal("ctx-1", GOAL)
    sid = proposal["suggestion_id"]

    assert ct.decline_proposal(sid, "not now") is True
    result = ct.confirm_proposal(sid)

    assert result["ok"] is False
    assert "declined" in result["error"]
    assert fake_ctrl.launched == []


def test_unknown_suggestion_is_refused(ace_db, ct, fake_ctrl):
    result = ct.confirm_proposal("sug-doesnotexist")
    assert result["ok"] is False
    assert fake_ctrl.launched == []


def test_expired_proposal_is_refused(ace_db, ct, fake_ctrl, monkeypatch):
    proposal = ct.build_team_proposal("ctx-1", GOAL)
    monkeypatch.setattr(ct, "is_expired", lambda p: True)

    result = ct.confirm_proposal(proposal["suggestion_id"])

    assert result["ok"] is False
    assert result["error"] == "expired"
    assert fake_ctrl.launched == []


def test_only_existing_roles_become_launch_targets(ace_db, ct, fake_ctrl, monkeypatch):
    """A to_generate role must not be sent as a launch target.

    team_assembler swallows RoleNotFoundError into a ghost coworker that fails
    role_not_found, so an unresolvable id is worse than none.
    """
    monkeypatch.setattr(ct, "_classify_roles", lambda c: [
        {"role_id": "ai_developer", "display_name": "AI Dev", "count": 1, "status": "existing"},
        {"role_id": "brand_new_sme", "display_name": "New", "count": 1, "status": "to_generate"},
    ])

    proposal = ct.build_team_proposal("ctx-1", GOAL)
    ct.confirm_proposal(proposal["suggestion_id"])

    role_ids = fake_ctrl.launched[0]["role_ids"]
    assert role_ids == ["ai_developer"]
    assert "brand_new_sme" not in role_ids


def test_decisions_are_written_to_the_append_only_trail(ace_db, ct, fake_ctrl):
    """The state row transitions; the immutable record lives in ace_audit_log."""
    proposal = ct.build_team_proposal("ctx-1", GOAL)
    ct.confirm_proposal(proposal["suggestion_id"])

    conn = ct._conn()
    try:
        rows = conn.execute(
            "SELECT action FROM ace_audit_log WHERE action IN "
            "('team_proposed','team_approved') ORDER BY action"
        ).fetchall()
    finally:
        conn.close()

    actions = {dict(r)["action"] for r in rows}
    assert {"team_proposed", "team_approved"} <= actions
