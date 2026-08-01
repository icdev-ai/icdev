# CUI // SP-CTI
"""An external task is done when its work LANDED. Nothing else counts.

The phantom completion the first live compass dispatch produced.

The agent built the work, pushed `kanban/prem-rpt-07` to compass, and then marked the
task done through the API with:

    bypass_verification: true
    bypass_reason: "COMPASS repo, not ICDev: it has no CI and ICDev's coherence
                    checker doesn't apply"

Every word of that is TRUE — and the dispatcher's own instruction is what told it so.
ICDev's verification suite genuinely does not apply in compass. So the agent reasonably
concluded the done-gate did not either, bypassed it, and the board went green with the
work sitting on an unmerged branch that nobody would ever look at again.

`bypass_verification` means "ICDev's CodeLens/Coherence/E2E suite could not run". It has
never meant "this work does not have to land anywhere".

So for an external task the gate asks a different question, one that has a factual answer
in every repo and needs no CI: **are the commits on the target repo's origin/<base>?**
And `bypass_verification` does not reach it.
"""
from __future__ import annotations

import importlib

import pytest

api = importlib.import_module("tools.dashboard.api.kanban")
kanban = importlib.import_module("tools.genesis.reflexes.kanban")


class _Target:
    def __init__(self, name="compass", is_external=True):
        self.name = name
        self.is_external = is_external
        self.base_branch = "main"
        self.root = "/somewhere/compass"


@pytest.fixture
def app():
    from flask import Flask

    app = Flask(__name__)
    return app


def _refuse(app, task_id="prem-rpt-07", new="done", current="in_progress"):
    with app.test_request_context():
        return api._external_landing_refusal(task_id, new, current)


# ---------------------------------------------------------------------------
# The refusal
# ---------------------------------------------------------------------------
def test_an_external_task_with_unmerged_work_cannot_be_marked_done(app, monkeypatch):
    monkeypatch.setattr(kanban, "_task_repo_target", lambda tid: _Target())
    monkeypatch.setattr(kanban, "_branch_has_unmerged_commits", lambda tid: True)

    refusal = _refuse(app)

    assert refusal is not None
    body, code = refusal
    assert code == 409
    data = body.get_json()
    assert data["error"] == "external_work_not_landed"
    assert "has not landed" in data["detail"]
    assert data["repo"] == "compass"


def test_the_refusal_says_bypass_does_not_reach_it(app, monkeypatch):
    """Because the agent WILL reach for it — the brief told it ICDev's gates don't apply."""
    monkeypatch.setattr(kanban, "_task_repo_target", lambda tid: _Target())
    monkeypatch.setattr(kanban, "_branch_has_unmerged_commits", lambda tid: True)

    data = _refuse(app)[0].get_json()

    assert "NOT bypassable" in data["detail"]
    assert "bypass_verification" in data["detail"]
    assert "a task is done when its work landed" in data["detail"]


def test_once_the_work_has_landed_the_task_may_be_done(app, monkeypatch):
    monkeypatch.setattr(kanban, "_task_repo_target", lambda tid: _Target())
    monkeypatch.setattr(kanban, "_branch_has_unmerged_commits", lambda tid: False)

    assert _refuse(app) is None


# ---------------------------------------------------------------------------
# It touches nothing else
# ---------------------------------------------------------------------------
def test_an_icdev_task_is_not_affected(app, monkeypatch):
    """ICDev tasks keep their existing gates, byte-unchanged."""
    monkeypatch.setattr(kanban, "_task_repo_target",
                        lambda tid: _Target(name="icdev", is_external=False))
    monkeypatch.setattr(kanban, "_branch_has_unmerged_commits", lambda tid: True)

    assert _refuse(app, task_id="dm-portal-01") is None


@pytest.mark.parametrize("new,current", [
    ("in_progress", "scheduled"),   # not a done transition
    ("done", "done"),               # already done
    ("validating", "in_progress"),
])
def test_only_the_transition_INTO_done_is_gated(app, monkeypatch, new, current):
    monkeypatch.setattr(kanban, "_task_repo_target", lambda tid: _Target())
    monkeypatch.setattr(kanban, "_branch_has_unmerged_commits", lambda tid: True)

    assert _refuse(app, new=new, current=current) is None


def test_it_fails_OPEN_when_git_is_unreachable(app, monkeypatch):
    """An unreachable git must never wedge every task's completion — the same
    fail-open contract _branch_has_unmerged_commits already keeps."""
    def _explode(tid):
        raise RuntimeError("git is gone")

    monkeypatch.setattr(kanban, "_task_repo_target", _explode)

    assert _refuse(app) is None


def test_both_doors_are_guarded():
    """A gate with one door is not a gate. /move and PATCH /tasks/<id> can both write
    status, and the agent used /move."""
    import inspect

    src = inspect.getsource(api)
    assert src.count("_external_landing_refusal(") >= 3, (
        "the guard must be called from BOTH the /move and the PATCH door "
        "(plus its own definition)"
    )


# ---------------------------------------------------------------------------
# The instruction must not invite the bypass
# ---------------------------------------------------------------------------
def test_the_external_brief_forbids_self_reporting_done(monkeypatch):
    monkeypatch.setattr(kanban, "_task_repo_target", lambda tid: _Target())

    brief = kanban._external_repo_brief("prem-rpt-07")

    assert "Do NOT mark this task done" in brief
    assert "do NOT bypass the verification gate" in brief
    assert "phantom completion" in brief
    # And it says what TO do instead.
    assert "Open a PR against compass" in brief


def test_an_icdev_task_gets_no_external_brief(monkeypatch):
    monkeypatch.setattr(kanban, "_task_repo_target",
                        lambda tid: _Target(name="icdev", is_external=False))
    assert kanban._external_repo_brief("dm-portal-01") == ""
