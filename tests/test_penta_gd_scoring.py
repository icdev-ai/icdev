# CUI // SP-CTI
"""penta-gd-04 — AI GameDay fail-loud scoring + template XSS regression tests.

Part A — fail-loud judge scoring (tools/ttx/ai_scorer.py):
  Previously any LLM/parse failure or missing rubric silently assigned 50/100,
  indistinguishable from a real score. Now such cases are marked UNSCORED
  (judge_pts 0, ``unscored`` flag + reason persisted in judge_rationale_json,
  never counted as 50). The real (LLM-available) scoring math is unchanged.

Part B — stored-XSS escaping across the GameDay templates:
  Attacker-influenced team/player names, response text, inject titles and tool
  names were interpolated into innerHTML. Every such sink is now wrapped in a
  per-template esc() helper that escapes & < > " '.

Mocks the LLM router via importlib + setattr on tools.llm.router (ai_scorer
imports LLMRouter/LLMRequest at call time). Uses a temp SQLite DB seeded with
the shared conftest schema + storage translate layer (%s params) — no raw
sqlite3 in the query path.
"""

from __future__ import annotations

import importlib
import json
import pathlib
import re

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
TPL = REPO_ROOT / "tools" / "dashboard" / "templates"


# ===========================================================================
# Part A — fail-loud scoring
# ===========================================================================

class _FakeReq:
    def __init__(self, **kw):
        self.kw = kw


class _FakeResp:
    def __init__(self, content):
        self.content = content


def _make_router(content="", has_llm=True):
    class _FakeRouter:
        def __init__(self, *a, **k):
            pass

        def has_any_llm(self):
            return has_llm

        def invoke(self, function, req):
            return _FakeResp(content)

    return _FakeRouter


@pytest.fixture
def patch_router(monkeypatch):
    """Return a helper that installs a fake LLMRouter/LLMRequest on tools.llm.router."""
    router_mod = importlib.import_module("tools.llm.router")

    def _install(content="", has_llm=True):
        monkeypatch.setattr(router_mod, "LLMRouter", _make_router(content, has_llm))
        monkeypatch.setattr(router_mod, "LLMRequest", _FakeReq)

    return _install


_RUBRIC = {
    "dimensions": [
        {"id": "d1", "weight": 2, "prompt": "clarity"},
        {"id": "d2", "weight": 1, "prompt": "depth"},
    ]
}


def test_judge_response_real_path_is_weighted_not_50(patch_router):
    from tools.ttx import ai_scorer

    patch_router('{"scores": {"d1": 8, "d2": 2}, "rationale": "ok", "confidence": 0.9}', has_llm=True)
    out = ai_scorer.judge_response("inject", "response", _RUBRIC)
    # weighted = (8*2 + 2*1)/3 = 6.0 → *10 = 60
    assert out["total"] == 60
    assert not out.get("unscored")
    assert out["total"] != 50


def test_weighted_total_math():
    from tools.ttx.ai_scorer import _weighted_total

    assert _weighted_total({"d1": 8, "d2": 2}, _RUBRIC["dimensions"]) == 60
    assert _weighted_total({"d1": 10, "d2": 10}, _RUBRIC["dimensions"]) == 100
    # zero-weight rubric floors at 0, never a fabricated midpoint
    assert _weighted_total({"d1": 5}, [{"id": "d1", "weight": 0, "prompt": "x"}]) == 0


def test_judge_response_llm_unavailable_is_unscored(patch_router):
    from tools.ttx import ai_scorer

    patch_router(has_llm=False)
    out = ai_scorer.judge_response("inject", "response", _RUBRIC)
    assert out["unscored"] is True
    assert out["total"] == 0
    assert out["total"] != 50
    assert "unavailable" in out["unscored_reason"].lower()


def test_judge_response_missing_rubric_is_unscored(patch_router):
    from tools.ttx import ai_scorer

    patch_router('{"scores": {}}', has_llm=True)
    out = ai_scorer.judge_response("inject", "response", {})
    assert out["unscored"] is True
    assert out["total"] == 0


def test_judge_response_zero_weight_rubric_is_unscored(patch_router):
    from tools.ttx import ai_scorer

    patch_router('{"scores": {}}', has_llm=True)
    out = ai_scorer.judge_response(
        "inject", "response", {"dimensions": [{"id": "d1", "weight": 0, "prompt": "x"}]}
    )
    assert out["unscored"] is True
    assert out["total"] == 0


def test_judge_response_unparseable_is_unscored(patch_router):
    from tools.ttx import ai_scorer

    patch_router("this is not json at all", has_llm=True)
    out = ai_scorer.judge_response("inject", "response", _RUBRIC)
    assert out["unscored"] is True
    assert out["total"] == 0


# ---- score_response persistence + leaderboard unscored count ----------------

@pytest.fixture
def scoring_db(tmp_path, monkeypatch):
    from tests.conftest import MINIMAL_ICDEV_SCHEMA

    db_path = tmp_path / "icdev.db"
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))

    import sqlite3

    conn = sqlite3.connect(str(db_path))
    conn.executescript(MINIMAL_ICDEV_SCHEMA)
    conn.commit()
    conn.close()

    # db.migrate() (process-global) creates all ttx_* tables via _DDL.
    db_mod = importlib.import_module("apps.ai_gameday.db")
    db_mod._migrated = False
    db_mod.migrate()
    return db_path


def _seed_response(session_slug="ai_gameday"):
    """Create a session + team + response and return (sid, team_id, inject_id, response_id)."""
    from tools.ttx.engine import TTXEngine
    from tools.db.storage import get_connection

    engine = TTXEngine()
    session = engine.create_session(session_slug, "Facilitator")
    sid = session["session_id"]
    team = engine.create_team(sid, "Alpha")
    team_id = team["team_id"]

    conn = get_connection()
    inject_id = conn.execute(
        "SELECT inject_id FROM ttx_injects WHERE session_id = %s LIMIT 1", (sid,)
    ).fetchone()["inject_id"]
    conn.execute(
        "INSERT INTO ttx_responses (team_id, inject_id, response_text) VALUES (%s, %s, %s)",
        (team_id, inject_id, "our response"),
    )
    conn.commit()
    response_id = conn.execute(
        "SELECT response_id FROM ttx_responses WHERE team_id = %s ORDER BY response_id DESC LIMIT 1",
        (team_id,),
    ).fetchone()["response_id"]
    return sid, team_id, inject_id, response_id


def test_score_response_unscored_persists_and_excludes_judge(scoring_db, patch_router):
    from tools.ttx import ai_scorer
    from tools.ttx.leaderboard import _team_unscored_count, compute_leaderboard
    from tools.db.storage import get_connection

    patch_router(has_llm=False)  # LLM outage
    sid, team_id, inject_id, response_id = _seed_response()

    out = ai_scorer.score_response(
        response_id=response_id, team_id=team_id, inject_id=inject_id, session_id=sid,
        response_text="our response", inject_body="body", receipts=[], rubric=_RUBRIC,
        bonus_per_call=5, max_receipt_bonus=25, time_taken_s=None,
    )
    assert out["judge_unscored"] is True
    assert out["judge_pts"] == 0
    assert out["total_pts"] == 0  # receipt(0) + judge(0) + time(0) — no fabricated 50

    conn = get_connection()
    row = dict(conn.execute(
        "SELECT judge_pts, judge_rationale_json FROM ttx_scores WHERE response_id = %s",
        (response_id,),
    ).fetchone())
    assert row["judge_pts"] == 0
    assert json.loads(row["judge_rationale_json"]).get("unscored") is True

    assert _team_unscored_count(conn, team_id) == 1
    lb = compute_leaderboard(sid)
    alpha = next(r for r in lb if r["team_id"] == team_id)
    assert alpha["unscored_count"] == 1


def test_score_response_real_path_persists_judge(scoring_db, patch_router):
    from tools.ttx import ai_scorer
    from tools.ttx.leaderboard import _team_unscored_count
    from tools.db.storage import get_connection

    patch_router('{"scores": {"d1": 8, "d2": 2}, "rationale": "solid", "confidence": 0.9}', has_llm=True)
    sid, team_id, inject_id, response_id = _seed_response()

    out = ai_scorer.score_response(
        response_id=response_id, team_id=team_id, inject_id=inject_id, session_id=sid,
        response_text="our response", inject_body="body", receipts=[], rubric=_RUBRIC,
        bonus_per_call=5, max_receipt_bonus=25, time_taken_s=None,
    )
    assert out["judge_unscored"] is False
    assert out["judge_pts"] == 60
    assert out["total_pts"] == 60

    conn = get_connection()
    assert _team_unscored_count(conn, team_id) == 0


# ===========================================================================
# Part B — template XSS escaping
# ===========================================================================

_TEMPLATES = {
    "facilitator": TPL / "ai_gameday" / "facilitator.html",
    "player": TPL / "ai_gameday" / "player.html",
    "simulate": TPL / "ai_gameday" / "simulate.html",
    "leaderboard": TPL / "ai_gameday" / "leaderboard.html",
    # gdx-dead-01: the "session" entry pointed at gameday/session.html, an
    # orphaned template no route ever rendered (it fetched the dead
    # /gameday/api/sessions* prefix). Deleted with the template — the live
    # session surfaces are player/facilitator/simulate, already covered above.
}

# User/attacker-controlled sinks that MUST be esc()-wrapped in each template.
_REQUIRED_ESCAPED = {
    "facilitator": ["esc(row.team_name)", "esc(resp.team_name", "esc(resp.response_preview"],
    "player": ["esc(inj.title)", "esc(r.tool)", "esc(row.team_name)"],
    "simulate": ["esc(r.team_name)", "esc(t.team_name)", "esc(label)"],
    "leaderboard": ["esc(row.team_name)"],
}

# Bare (unescaped) interpolations that must NOT appear anymore.
_FORBIDDEN_BARE = {
    "facilitator": ["${row.team_name}", "${resp.response_preview||''}"],
    "player": ["${inj.title}", "${row.team_name}"],
    "simulate": ["${r.team_name}", "${t.team_name}"],
    "leaderboard": ["${row.team_name}"],
}


@pytest.mark.parametrize("name", sorted(_TEMPLATES))
def test_template_defines_esc_helper(name):
    src = _TEMPLATES[name].read_text(encoding="utf-8")
    assert "function esc(" in src, f"{name}.html must define an esc() helper"
    # esc() must escape all five HTML-significant characters (element + attribute).
    # The apostrophe key is written with double quotes in the template mapping.
    for mapping in ["'&':'&amp;'", "'<':'&lt;'", "'>':'&gt;'", "'\"':'&quot;'", '"\'":\'&#39;\'']:
        assert mapping in src, f"{name}.html esc() must contain mapping {mapping}"


@pytest.mark.parametrize("name", sorted(_REQUIRED_ESCAPED))
def test_required_sinks_are_escaped(name):
    src = _TEMPLATES[name].read_text(encoding="utf-8")
    for needle in _REQUIRED_ESCAPED[name]:
        assert needle in src, f"{name}.html must escape sink: {needle}"


@pytest.mark.parametrize("name", sorted(_FORBIDDEN_BARE))
def test_no_bare_user_data_sinks(name):
    src = _TEMPLATES[name].read_text(encoding="utf-8")
    for bad in _FORBIDDEN_BARE[name]:
        assert bad not in src, f"{name}.html still has an UNescaped sink: {bad}"


def _js_esc(s: str) -> str:
    """Python mirror of the per-template esc() transform (same 5-char mapping)."""
    table = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}
    return re.sub(r"[&<>\"']", lambda m: table[m.group()], s)


def test_hostile_team_name_renders_inert():
    payload = '<img src=x onerror=alert(1)>'
    out = _js_esc(payload)
    assert "<img" not in out
    assert "onerror=alert(1)>" not in out
    assert out == "&lt;img src=x onerror=alert(1)&gt;"
