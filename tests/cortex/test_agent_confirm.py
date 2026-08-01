# CUI // SP-CTI
"""The Cortex chat agent facade: propose, then launch only on explicit confirm.

``_run_facade`` used to return ``requires_confirm: True`` and nothing ever
consumed the follow-up — ``_resolve_facade`` already accepted a ``confirm_agent``
parameter, but the branch ignored it, so the affordance was dead. Confirming did
nothing.

The safety property under test: an agent is launched **only** when
``confirm_agent`` is truthy, and the launch goes through ``cortex_api.agent`` so
it inherits the governance pipeline rather than reaching ACEController directly.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tools.cortex import blueprint as bp


@pytest.fixture
def ctx():
    from tools.cortex.schemas import CortexContext

    return CortexContext(tenant_id="t1", user_id="u1", classification="CUI", domain="")


GOAL = (
    "Build a compliance dashboard with audit logging, write the tests, "
    "and prepare it for deployment"
)


# ---------------------------------------------------------------------------
# Unconfirmed: propose only
# ---------------------------------------------------------------------------


def test_unconfirmed_agent_does_not_launch(ctx, monkeypatch):
    """Without confirm_agent, nothing may be launched."""
    launched = MagicMock()
    monkeypatch.setattr(bp, "_launch_confirmed_agent", launched)

    out = bp._run_facade("agent", GOAL, ctx, confirm_agent=False)

    assert out["requires_confirm"] is True
    launched.assert_not_called()


def test_proposal_names_the_actual_roster(ctx):
    """The card must show the team being approved, not 'an agent'."""
    out = bp._run_facade("agent", GOAL, ctx, confirm_agent=False)

    assert out["requires_confirm"] is True
    assert isinstance(out["proposed_roles"], list)
    for role in out["proposed_roles"]:
        assert role["role_id"]
        assert "exists" in role


def test_proposal_survives_classifier_failure(ctx, monkeypatch):
    """A classifier outage must degrade the roster, not the proposal."""
    monkeypatch.setattr(bp, "_propose_roles", lambda q: [])

    out = bp._run_facade("agent", GOAL, ctx, confirm_agent=False)

    assert out["requires_confirm"] is True
    assert out["proposed_roles"] == []
    assert "confirm to proceed" in out["answer"]


# ---------------------------------------------------------------------------
# Confirmed: launch, through the governed facade
# ---------------------------------------------------------------------------


def test_confirmed_agent_launches_via_governed_facade(ctx, monkeypatch):
    """Confirming must call cortex_api.agent — not ACEController directly.

    Routing through the facade is what puts the TRUST gates on the launch,
    which is the moment governance matters most.
    """
    from tools.cortex import api as cortex_api

    fake = SimpleNamespace(
        text="Launched ACE team run ace-abc123.",
        data={"mode": "team", "instance_id": "ace-abc123"},
        governance=None,
    )
    agent_mock = MagicMock(return_value=fake)
    monkeypatch.setattr(cortex_api, "agent", agent_mock)

    out = bp._run_facade("agent", GOAL, ctx, confirm_agent=True)

    agent_mock.assert_called_once()
    assert out["requires_confirm"] is False
    assert out["instance_id"] == "ace-abc123"
    assert out["deep_link"] == "/coworker/ace-abc123"
    assert "/coworker/ace-abc123" in out["answer"]


def test_confirmed_launch_passes_only_real_roles(ctx, monkeypatch):
    """A role that is not on disk must never be sent as a launch target.

    team_assembler swallows RoleNotFoundError into a ghost co-worker that then
    fails role_not_found, so passing an unknown id is worse than passing none.
    """
    from tools.cortex import api as cortex_api

    monkeypatch.setattr(bp, "_propose_roles", lambda q: [
        {"role_id": "ai_developer", "display_name": "AI Developer", "count": 1, "exists": True},
        {"role_id": "invented_role", "display_name": "Invented", "count": 1, "exists": False},
    ])
    agent_mock = MagicMock(return_value=SimpleNamespace(text="ok", data={}, governance=None))
    monkeypatch.setattr(cortex_api, "agent", agent_mock)

    bp._run_facade("agent", GOAL, ctx, confirm_agent=True)

    roles = agent_mock.call_args.kwargs["roles"]
    assert roles == ["ai_developer"]
    assert "invented_role" not in roles


def test_launch_failure_degrades_instead_of_500(ctx, monkeypatch):
    """The chat route must never 500 on a downstream failure."""
    from tools.cortex import api as cortex_api

    monkeypatch.setattr(
        cortex_api, "agent", MagicMock(side_effect=RuntimeError("controller down"))
    )

    out = bp._run_facade("agent", GOAL, ctx, confirm_agent=True)

    assert out["degraded"] is True
    assert out["requires_confirm"] is False
    assert "could not be launched" in out["answer"]


def test_no_roles_still_launches_a_single_agent(ctx, monkeypatch):
    """An empty roster must pass roles=None so the facade picks single mode."""
    from tools.cortex import api as cortex_api

    monkeypatch.setattr(bp, "_propose_roles", lambda q: [])
    agent_mock = MagicMock(return_value=SimpleNamespace(text="ok", data={}, governance=None))
    monkeypatch.setattr(cortex_api, "agent", agent_mock)

    bp._run_facade("agent", GOAL, ctx, confirm_agent=True)

    assert agent_mock.call_args.kwargs["roles"] is None


def test_governance_summary_is_normalised(ctx, monkeypatch):
    """A CortexResult's governance report must reach the chat shape intact."""
    from tools.cortex import api as cortex_api

    report = SimpleNamespace(
        gates_run=["injection_screen", "citation_grounding"],
        outcomes={"injection_screen": "pass"},
        blocked=False,
    )
    monkeypatch.setattr(cortex_api, "agent", MagicMock(
        return_value=SimpleNamespace(text="ok", data={"instance_id": "x"}, governance=report)
    ))

    out = bp._run_facade("agent", GOAL, ctx, confirm_agent=True)

    assert out["governance"]["gates_run"] == ["injection_screen", "citation_grounding"]
    assert out["governance"]["blocked"] is False
