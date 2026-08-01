# CUI // SP-CTI
"""penta-gd-07 — ai_scorer scoring-math units NOT covered by
tests/test_penta_gd_scoring.py.

penta-gd-04 (test_penta_gd_scoring.py) already covers ``judge_response``
weighting on the 2-dim rubric, the fail-loud ``_unscored`` cases, and
``score_response`` persistence. This module fills the remaining math surface:

  * ``validate_receipts`` — receipt verification against ttx_api_log (only a
    call_id that was actually logged for this (team, session) is credited; the
    sha256 result_hash written by the api-log endpoint round-trips), plus the
    per-call bonus and max-bonus cap.
  * ``compute_time_bonus`` — the full TIME_BONUS_BRACKETS ladder incl. bracket
    boundaries and the None / over-limit cases.
  * ``_weighted_total`` — rubric-weight normalization: the divide-by-total-weight
    scaling, the default-5 fill for a dimension absent from the LLM's scores,
    and proportional-weight invariance.

Uses a temp SQLite DB seeded with the shared conftest schema + full ttx_* schema
via db.migrate(); receipts are logged through the real engine path. Storage
translate layer (%s params) — no raw sqlite3 in the query path.
"""

from __future__ import annotations

import hashlib
import importlib

import pytest

from tools.ttx import ai_scorer
from tools.ttx.ai_scorer import _weighted_total, compute_time_bonus, validate_receipts
from tools.ttx.constants import TIME_BONUS_BRACKETS


# ---------------------------------------------------------------------------
# DB fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def scorer_db(tmp_path, monkeypatch):
    from tests.conftest import MINIMAL_ICDEV_SCHEMA

    db_path = tmp_path / "icdev.db"
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))

    import sqlite3

    conn = sqlite3.connect(str(db_path))
    conn.executescript(MINIMAL_ICDEV_SCHEMA)
    conn.commit()
    conn.close()

    db_mod = importlib.import_module("apps.ai_gameday.db")
    db_mod._migrated = False
    db_mod.migrate()
    return db_path


def _seed_session_team():
    """Create a real session + team so ttx_api_log FK constraints are satisfied.
    Returns (session_id, team_id)."""
    from tools.ttx.session_manager import create_session
    from tools.ttx.team_manager import create_team

    sess = create_session(
        scenario_slug="scorer-math", session_mode="live", facilitator_name="F"
    )
    sid = sess["session_id"]
    team = create_team(sid, "Alpha")
    return sid, team["team_id"]


def _log(session_id, team_id, call_id, tool_slug="knowledge.search", payload="x"):
    """Log a receipt through the engine, mirroring the api-log endpoint's
    sha256[:16] result_hash."""
    from tools.ttx.engine import TTXEngine

    result_hash = hashlib.sha256(str(payload).encode()).hexdigest()[:16]
    TTXEngine().log_api_receipt(
        session_id=session_id,
        team_id=team_id,
        tool_slug=tool_slug,
        endpoint="/x",
        call_id=call_id,
        result_hash=result_hash,
    )
    return result_hash


# ---------------------------------------------------------------------------
# validate_receipts — receipt verification (call_id logged in ttx_api_log)
# ---------------------------------------------------------------------------

def test_validate_receipts_credits_only_logged_call_ids(scorer_db):
    sid, tid = _seed_session_team()
    _log(sid, tid, "call-A")
    _log(sid, tid, "call-B")

    pts, count = validate_receipts(
        tid, sid,
        receipts=[{"call_id": "call-A"}, {"call_id": "call-B"}, {"call_id": "call-GHOST"}],
        bonus_per_call=50, max_bonus=1000,
    )
    assert count == 2  # ghost (never logged) not credited
    assert pts == 100  # 2 * 50


def test_validate_receipts_skips_blank_call_ids(scorer_db):
    sid, tid = _seed_session_team()
    _log(sid, tid, "real")
    pts, count = validate_receipts(
        tid, sid,
        receipts=[{"call_id": ""}, {"tool": "x"}, {"call_id": "real"}],
        bonus_per_call=10, max_bonus=1000,
    )
    assert count == 1
    assert pts == 10


def test_validate_receipts_caps_at_max_bonus(scorer_db):
    sid, tid = _seed_session_team()
    for i in range(5):
        _log(sid, tid, f"c{i}")
    pts, count = validate_receipts(
        tid, sid,
        receipts=[{"call_id": f"c{i}"} for i in range(5)],
        bonus_per_call=50, max_bonus=120,
    )
    assert count == 5
    assert pts == 120  # min(5*50, 120)


def test_validate_receipts_is_scoped_to_team_and_session(scorer_db):
    """A call_id logged for a different team is not credited (anti-spoof)."""
    sid, tid = _seed_session_team()
    _log(session_id=sid, team_id=tid, call_id="shared-id")
    # Same call_id string + session, but queried for a different (non-owning) team.
    pts, count = validate_receipts(
        team_id=tid + 1000, session_id=sid,
        receipts=[{"call_id": "shared-id"}],
        bonus_per_call=50, max_bonus=1000,
    )
    assert count == 0
    assert pts == 0


def test_logged_receipt_hash_is_sha256_prefix(scorer_db):
    """The result_hash the engine/api-log path stores is sha256(payload)[:16]."""
    from tools.db.storage import get_connection

    sid, tid = _seed_session_team()
    rh = _log(sid, tid, "hash-call", payload="the-payload")
    assert rh == hashlib.sha256(b"the-payload").hexdigest()[:16]
    conn = get_connection()
    stored = conn.execute(
        "SELECT result_hash FROM ttx_api_log WHERE call_id = %s", ("hash-call",)
    ).fetchone()["result_hash"]
    assert stored == rh


# ---------------------------------------------------------------------------
# compute_time_bonus — the full bracket ladder
# ---------------------------------------------------------------------------

def test_time_bonus_brackets_are_as_configured():
    # Guard the constant so the boundary cases below stay meaningful.
    assert TIME_BONUS_BRACKETS == [(120, 50), (300, 25), (600, 10)]


@pytest.mark.parametrize(
    "seconds,expected",
    [
        (None, 0),
        (0, 50),
        (120, 50),     # inclusive upper bound of fastest bracket
        (120.0, 50),
        (121, 25),     # just over -> next bracket
        (300, 25),
        (301, 10),
        (600, 10),
        (601, 0),      # past the last bracket -> no bonus
        (5000, 0),
    ],
)
def test_compute_time_bonus(seconds, expected):
    assert compute_time_bonus(seconds) == expected


# ---------------------------------------------------------------------------
# _weighted_total — rubric-weight normalization
# ---------------------------------------------------------------------------

def _dims(*pairs):
    return [{"id": i, "weight": w, "prompt": i} for i, w in pairs]


def test_weighted_total_normalizes_by_total_weight():
    # (10*3 + 0*1) / (3+1) = 7.5 -> *10 = 75
    assert _weighted_total({"a": 10, "b": 0}, _dims(("a", 3), ("b", 1))) == 75


def test_weighted_total_absent_dim_defaults_to_five():
    # No scores at all -> every dim defaults to 5 -> normalized 5 -> *10 = 50.
    assert _weighted_total({}, _dims(("a", 1), ("b", 1))) == 50
    # One present (10), one absent (->5): (10 + 5)/2 = 7.5 -> 75.
    assert _weighted_total({"a": 10}, _dims(("a", 1), ("b", 1))) == 75


def test_weighted_total_is_invariant_to_proportional_weights():
    scores = {"a": 10, "b": 0, "c": 5}
    small = _weighted_total(scores, _dims(("a", 0.4), ("b", 0.4), ("c", 0.2)))
    large = _weighted_total(scores, _dims(("a", 2), ("b", 2), ("c", 1)))
    assert small == large


def test_weighted_total_bounds():
    dims = _dims(("a", 1), ("b", 1), ("c", 1))
    assert _weighted_total({"a": 10, "b": 10, "c": 10}, dims) == 100
    assert _weighted_total({"a": 0, "b": 0, "c": 0}, dims) == 0


def test_weighted_total_zero_weight_floors_at_zero():
    # Defensive floor — never a fabricated midpoint.
    assert _weighted_total({"a": 9}, _dims(("a", 0))) == 0


# ---------------------------------------------------------------------------
# score_aadc_design — automated-compliance branch fallback
# ---------------------------------------------------------------------------

def test_score_aadc_design_missing_design_id_scores_zero(scorer_db):
    out = ai_scorer.score_aadc_design("", ["chk1"])
    assert out["judge_pts"] == 0
    assert out["check_results"] == []
