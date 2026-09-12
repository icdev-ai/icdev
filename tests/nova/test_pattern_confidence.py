# CUI // SP-CTI
"""Confidence 0..1 on NOVA learned patterns (xrv-shield-02).

`analyze_patterns` used to return a raw frequency behind a `min_count=2` floor
and nothing else, and `skills_lifecycle.maybe_propose_from_session` queued a NOVA
proposal with nothing to refuse on. These tests pin the four claims the card
makes, plus the honesty rails the whole xrv series is built on:

* one session seen once never exceeds 0.5;
* a pattern across 5 sessions this week exceeds 0.7;
* an injection hit caps at 0.5;
* the refusal count is reported by `maybe_propose_from_session`;
* an unmeasured input is None — never 0.0 — and never reads as a clean measure.
"""
import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

# The repo's own placeholder-translating connection: production SQL is authored
# for PostgreSQL (`%s`) and a bare sqlite3 connection would raise on every
# statement, which `index_session_turn`'s best-effort `except` would swallow —
# the test would then assert against a no-op it caused itself.
from _sql_compat import translating

NOW = datetime(2026, 9, 11, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture()
def cfg():
    from tools.nova.skill_generator import load_confidence_config

    return load_confidence_config()


def _score(**kwargs):
    from tools.nova.skill_generator import pattern_confidence

    kwargs.setdefault("now", NOW)
    return pattern_confidence(**kwargs)


# ---------------------------------------------------------------------------
# The formula — the card's four claims
# ---------------------------------------------------------------------------

def test_one_session_seen_once_never_exceeds_half(cfg):
    """count=1 in one session, seen THIS INSTANT — the most generous inputs it
    can possibly have — still cannot reach 0.5."""
    result = _score(count=1, distinct_sessions=1, last_seen=NOW, config=cfg)
    assert result["confidence"] is not None
    assert result["confidence"] <= 0.5, result


def test_five_sessions_this_week_exceeds_threshold(cfg):
    """Five occurrences across five DISTINCT sessions within the last week
    clears the propose bar — at both ends of "this week"."""
    from tools.nova.skill_generator import propose_min_confidence

    bar = propose_min_confidence()
    for age_days in (0, 1, 7):
        result = _score(
            count=5,
            distinct_sessions=5,
            last_seen=NOW - timedelta(days=age_days),
            config=cfg,
        )
        assert result["confidence"] > 0.7, (age_days, result)
        assert result["confidence"] >= bar, (age_days, result)


def test_injection_hit_caps_at_half(cfg):
    """learning_collector's rule: a demote-band hit CAPS, a block-band hit
    REJECTS. Asserted against inputs that would otherwise score near 1.0, so the
    cap is what is being observed and not a weak pattern."""
    strong = dict(count=500, distinct_sessions=99, last_seen=NOW, config=cfg)

    clean = _score(**strong, injection={"scanned": True, "detected": False, "confidence": 0.0})
    assert clean["confidence"] > 0.5, clean  # control: the cap is not always on

    demoted = _score(**strong, injection={"scanned": True, "detected": True, "confidence": 0.6})
    assert demoted["confidence"] <= 0.5, demoted
    assert demoted["basis"] == "injection_demoted"

    blocked = _score(**strong, injection={"scanned": True, "detected": True, "confidence": 0.95})
    assert blocked["confidence"] == 0.0, blocked
    assert blocked["basis"] == "injection_blocked"


def test_unavailable_detector_never_demotes(cfg):
    """A scan that did not RUN is not a clean scan and is not a dirty one. It
    must not move the score in either direction."""
    strong = dict(count=500, distinct_sessions=99, last_seen=NOW, config=cfg)
    unscanned = _score(**strong, injection={"scanned": False, "detected": None, "confidence": None})
    clean = _score(**strong, injection={"scanned": True, "detected": False, "confidence": 0.0})
    assert unscanned["confidence"] == clean["confidence"]
    assert unscanned["injection_scanned"] is False
    assert unscanned["injection_detected"] is None


# ---------------------------------------------------------------------------
# Honesty rails
# ---------------------------------------------------------------------------

def test_nothing_measured_is_none_never_zero(cfg):
    """`confidence` 0.0 is a real verdict — measured, and worthless. A pattern
    with no occurrences was not measured, so it is None."""
    result = _score(count=0, distinct_sessions=None, last_seen=None, config=cfg)
    assert result["confidence"] is None
    assert result["basis"] == "unmeasurable"
    assert result["terms"] == {"frequency": None, "distinct_sessions": None, "recency": None}


def test_unmeasured_session_count_is_not_credited(cfg):
    """Session attribution that could not be measured scores the declared
    `unmeasured_session_credit` (0.0) and SAYS SO — never the same basis as a
    measured spread."""
    unmeasured = _score(count=8, distinct_sessions=None, last_seen=NOW, config=cfg)
    measured = _score(count=8, distinct_sessions=4, last_seen=NOW, config=cfg)
    assert unmeasured["basis"] == "measured_without_sessions"
    assert unmeasured["terms"]["distinct_sessions"] is None
    assert unmeasured["confidence"] < measured["confidence"]


def test_undateable_occurrence_takes_the_floor_not_fresh(cfg):
    """An unknown age must never read as fresh — it takes the decay floor."""
    undateable = _score(count=8, distinct_sessions=4, last_seen=None, config=cfg)
    fresh = _score(count=8, distinct_sessions=4, last_seen=NOW, config=cfg)
    assert undateable["confidence"] < fresh["confidence"]
    assert undateable["basis"] == "measured_without_recency"
    assert undateable["terms"]["recency"] is None


def test_repetition_in_one_session_is_not_corroboration(cfg):
    """Forty hits in ONE session is one observation, not forty, and is capped
    below the propose bar however often it recurred."""
    from tools.nova.skill_generator import propose_min_confidence

    result = _score(count=40, distinct_sessions=1, last_seen=NOW, config=cfg)
    assert result["confidence"] < propose_min_confidence(), result
    assert result["basis"] == "single_session_capped"
    # Control: the same count spread across sessions is NOT capped.
    spread = _score(count=40, distinct_sessions=5, last_seen=NOW, config=cfg)
    assert spread["confidence"] >= propose_min_confidence(), spread


def test_confidence_is_always_within_zero_and_one(cfg):
    for count, sessions in ((1, 1), (10_000, 10_000), (3, 0), (7, 2)):
        result = _score(count=count, distinct_sessions=sessions, last_seen=NOW, config=cfg)
        assert 0.0 <= result["confidence"] <= 1.0, (count, sessions, result)


def test_weights_that_do_not_sum_to_one_are_refused(cfg):
    """A silently renormalised weight set is a formula nobody declared."""
    bad = dict(cfg)
    bad["weights"] = {"frequency": 0.9, "distinct_sessions": 0.9, "recency": 0.9}
    with pytest.raises(ValueError):
        _score(count=4, distinct_sessions=2, last_seen=NOW, config=bad)


def test_shipped_config_weights_sum_to_one():
    """args/nova_config.yaml as shipped is a set the formula accepts."""
    from tools.nova.skill_generator import load_confidence_config

    weights = load_confidence_config()["weights"]
    assert abs(sum(float(v) for v in weights.values()) - 1.0) < 1e-6, weights


def test_half_life_is_read_from_memory_config():
    """Recency uses the memory system's OWN declared decay table, not a number
    restated here. `session` is deliberately not a key, so it falls through to
    that file's `default_half_life`."""
    from tools.nova.skill_generator import _half_life_days, _load_yaml, BASE_DIR

    decay = _load_yaml(BASE_DIR / "args" / "memory_config.yaml")["time_decay"]
    assert "session" not in (decay.get("half_lives") or {})
    assert _half_life_days("session") == float(decay["default_half_life"])
    assert _half_life_days("event") == float(decay["half_lives"]["event"])


# ---------------------------------------------------------------------------
# analyze_patterns carries the score
# ---------------------------------------------------------------------------

def _make_db(*, with_session_ref: bool):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    # `topics`, not `tags` — that is the column the live table carries and the
    # column `index_session_turn` writes (swp-scan-01). A fixture spelling it
    # `tags` makes every INSERT raise into a best-effort `except` and the test
    # then asserts against a no-op it caused itself.
    # NOT NULL on id and content mirrors the schema of record
    # (tools/db/schema/pg_consolidated.sql) — `CREATE TABLE IF NOT EXISTS` never
    # ALTERs, so a fixture that drifts from it is green here and red on a fresh
    # database. Two whole statements rather than one built by concatenation:
    # `tools/ci/schema_drift_census.py` parses the DDL literal, and a spliced
    # one is exactly what it cannot read.
    if with_session_ref:
        conn.execute(
            """CREATE TABLE memory_entries (
                   id TEXT NOT NULL PRIMARY KEY,
                   type TEXT,
                   content TEXT NOT NULL,
                   topics TEXT DEFAULT '',
                   importance INTEGER DEFAULT 5,
                   classification TEXT DEFAULT 'CUI',
                   created_at TEXT,
                   session_ref TEXT)"""
        )
    else:
        conn.execute(
            """CREATE TABLE memory_entries (
                   id TEXT NOT NULL PRIMARY KEY,
                   type TEXT,
                   content TEXT NOT NULL,
                   topics TEXT DEFAULT '',
                   importance INTEGER DEFAULT 5,
                   classification TEXT DEFAULT 'CUI',
                   created_at TEXT)"""
        )
    conn.commit()
    return conn


def _seed(conn, rows, *, with_session_ref: bool):
    for i, (content, created_at, session_ref) in enumerate(rows):
        if with_session_ref:
            conn.execute(
                "INSERT INTO memory_entries (id, type, content, created_at, session_ref)"
                " VALUES (?,?,?,?,?)",
                (f"m{i}", "session_user", content, created_at, session_ref),
            )
        else:
            conn.execute(
                "INSERT INTO memory_entries (id, type, content, created_at)"
                " VALUES (?,?,?,?)",
                (f"m{i}", "session_user", content, created_at),
            )
    conn.commit()


def test_analyze_patterns_carries_confidence_and_session_spread():
    from tools.nova import skill_generator

    cmd = "python tools/memory/hybrid_search.py --query test"
    recent = datetime.now(timezone.utc) - timedelta(days=1)
    rows = [(cmd, recent.isoformat(), f"sess-{i}") for i in range(5)]
    conn = _make_db(with_session_ref=True)
    _seed(conn, rows, with_session_ref=True)

    with patch.object(skill_generator, "_get_conn", return_value=conn):
        patterns = skill_generator.analyze_patterns(limit=10, min_count=2, scan_injection=False)

    hit = next(p for p in patterns if "hybrid_search" in p["pattern"])
    assert hit["count"] == 5
    assert hit["sessions"] == 5
    assert hit["confidence"] is not None
    assert hit["confidence"] > 0.7, hit
    assert hit["confidence_basis"] == "measured"
    assert hit["injection_scanned"] is False  # asked not to scan — NOT "clean"


def test_analyze_patterns_reports_unmeasured_sessions_as_none_not_zero():
    """A database whose memory_entries has no `session_ref` column must still
    surface patterns — with `sessions: None`, never 0, and never an empty list
    because a SELECT raised."""
    from tools.nova import skill_generator

    cmd = "python tools/memory/hybrid_search.py --query test"
    rows = [(cmd, "2026-09-10 08:00:00", None) for _ in range(4)]
    conn = _make_db(with_session_ref=False)
    _seed(conn, rows, with_session_ref=False)

    with patch.object(skill_generator, "_get_conn", return_value=conn):
        patterns = skill_generator.analyze_patterns(limit=10, min_count=2, scan_injection=False)

    hit = next(p for p in patterns if "hybrid_search" in p["pattern"])
    assert hit["sessions"] is None
    assert hit["confidence"] is not None
    assert hit["confidence_basis"] == "measured_without_sessions"


def test_analyze_patterns_is_sorted_by_confidence():
    from tools.nova import skill_generator

    old = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
    new = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    rows = [("icdev stale-cmd", old, f"old-{i}") for i in range(4)]
    rows += [("icdev fresh-cmd", new, f"new-{i}") for i in range(4)]
    conn = _make_db(with_session_ref=True)
    _seed(conn, rows, with_session_ref=True)

    with patch.object(skill_generator, "_get_conn", return_value=conn):
        patterns = skill_generator.analyze_patterns(limit=10, min_count=2, scan_injection=False)

    confidences = [p["confidence"] for p in patterns]
    assert confidences == sorted(confidences, reverse=True)
    assert "fresh-cmd" in patterns[0]["pattern"]


# ---------------------------------------------------------------------------
# The refusal — maybe_propose_from_session
# ---------------------------------------------------------------------------

class _Session:
    def __init__(self, title, turns=5, context_id="ctx-1"):
        self.title = title
        self.turn_count = turns
        self.context_id = context_id


class _Runtime:
    def __init__(self, session):
        self.session = session
        self.llm_function = "code_generation"


def test_sub_threshold_pattern_is_refused_with_the_count_reported():
    """A candidate whose only evidence is one occurrence in one session is
    REFUSED, and the refusal carries both how many were refused and the
    occurrence count each was refused on."""
    from tools.agent_runtime import skills_lifecycle
    from tools.nova import skill_generator

    cmd = "icdev status"
    conn = _make_db(with_session_ref=True)
    _seed(
        conn,
        [(cmd, datetime.now(timezone.utc).isoformat(), "sess-only")],
        with_session_ref=True,
    )

    with patch.object(skill_generator, "_get_conn", return_value=conn):
        result = skills_lifecycle.maybe_propose_from_session(
            _Runtime(_Session(cmd)), force=True
        )

    assert result["proposed"] is False
    assert result["reason"] == "below_confidence"
    assert result["refused"] == 1                      # how many it refused
    assert result["min_confidence"] == pytest.approx(0.7)
    refusal = result["refusals"][0]
    assert refusal["count"] == 1                       # the count reported
    assert refusal["sessions"] == 1
    assert refusal["confidence"] is not None and refusal["confidence"] < 0.7
    assert refusal["reason"] == "below_min_confidence"


def test_uncorroborated_candidate_is_refused_as_unmeasurable():
    """A title history holds NO evidence for is refused with reason
    `unmeasurable` — not proposed, and not reported as a measured low score."""
    from tools.agent_runtime import skills_lifecycle
    from tools.nova import skill_generator

    conn = _make_db(with_session_ref=True)

    with patch.object(skill_generator, "_get_conn", return_value=conn):
        result = skills_lifecycle.maybe_propose_from_session(
            _Runtime(_Session("python tools/never/seen.py --flag")), force=True
        )

    assert result["proposed"] is False
    assert result["refused"] == 1
    assert result["refusals"][0]["confidence"] is None
    assert result["refusals"][0]["reason"] == "unmeasurable"


def test_corroborated_candidate_clears_the_gate_and_reaches_propose_skill():
    """The control the refusal tests need: a well-corroborated candidate is NOT
    refused and is handed to the existing proposal path, with its confidence."""
    from tools.agent_runtime import skills_lifecycle
    from tools.nova import skill_generator

    cmd = "python tools/memory/hybrid_search.py --query test"
    recent = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    conn = _make_db(with_session_ref=True)
    _seed(conn, [(cmd, recent, f"sess-{i}") for i in range(5)], with_session_ref=True)

    with patch.object(skill_generator, "_get_conn", return_value=conn), patch.object(
        skills_lifecycle, "propose_skill", return_value={"proposed": True}
    ) as proposer:
        result = skills_lifecycle.maybe_propose_from_session(
            _Runtime(_Session(cmd)), force=True
        )

    proposer.assert_called_once()
    assert result["proposed"] is True
    assert result["refused"] == 0
    assert result["confidence"] > 0.7


def test_screen_candidates_reports_every_refusal():
    """The screening seam reports a count AND the refused candidates — a gate
    whose refusals are invisible cannot be told from one that never fires."""
    from tools.agent_runtime.skills_lifecycle import screen_candidates
    from tools.nova import skill_generator

    conn = _make_db(with_session_ref=True)

    with patch.object(skill_generator, "_get_conn", return_value=conn):
        screen = screen_candidates(["icdev alpha", "icdev beta", "icdev gamma"])

    assert screen["scored"] == 3
    assert screen["refused_count"] == 3
    assert screen["accepted"] == []
    assert {r["pattern"] for r in screen["refused"]} == {
        "icdev alpha", "icdev beta", "icdev gamma"
    }


def test_screening_has_no_second_formula():
    """skills_lifecycle screens; it does not SCORE. The failure mode is a future
    edit inlining a threshold comparison over its own arithmetic, which a
    behavioural test over today's callers would still pass — so the module's
    source is what is asserted."""
    import ast
    import inspect

    from tools.agent_runtime import skills_lifecycle

    tree = ast.parse(inspect.getsource(skills_lifecycle.screen_candidates))
    names = {
        n.func.id
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert "score_candidate" in names, "the shared scorer must be the one used"
    # No weighting arithmetic of its own.
    assert not [
        n for n in ast.walk(tree)
        if isinstance(n, ast.BinOp) and isinstance(n.op, (ast.Mult, ast.Pow))
    ], "screen_candidates must not compute a score"


# ---------------------------------------------------------------------------
# The MCP surface carries the score
# ---------------------------------------------------------------------------

def test_mcp_handler_carries_confidence():
    """`nova_analyze_patterns` must hand the confidence through — the handler
    adapts the calling convention and computes nothing of its own."""
    from tools.mcp import gap_handlers
    from tools.nova import skill_generator

    cmd = "python tools/memory/hybrid_search.py --query test"
    recent = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    conn = _make_db(with_session_ref=True)
    _seed(conn, [(cmd, recent, f"sess-{i}") for i in range(5)], with_session_ref=True)

    with patch.object(skill_generator, "_get_conn", return_value=conn):
        out = gap_handlers.handle_nova_analyze_patterns(
            {"limit": 10, "min_count": 2, "scan_injection": False}
        )

    assert "error" not in out, out
    assert out["total"] >= 1
    assert out["propose_min_confidence"] == pytest.approx(0.7)
    hit = next(p for p in out["patterns"] if "hybrid_search" in p["pattern"])
    assert hit["confidence"] is not None
    assert hit["confidence_basis"] == "measured"
    assert hit["sessions"] == 5


def test_mcp_handler_reports_none_not_zero_over_an_empty_board():
    """Nothing to score is `scored: None`, never 0 — an empty denominator is not
    a measurement (args/perfect_score_gate.yaml)."""
    from tools.mcp import gap_handlers
    from tools.nova import skill_generator

    conn = _make_db(with_session_ref=True)
    with patch.object(skill_generator, "_get_conn", return_value=conn):
        out = gap_handlers.handle_nova_analyze_patterns({"scan_injection": False})

    assert out["total"] == 0
    assert out["scored"] is None
    assert out["unmeasurable"] == 0


def test_mcp_registry_declares_the_fields_the_handler_returns():
    """A declared schema that does not match what the tool returns is the
    declared-but-untrue defect one layer up from the code it describes."""
    from tools.mcp.tool_registry import TOOL_REGISTRY

    entry = TOOL_REGISTRY["nova_analyze_patterns"]
    description = entry["description"]
    for field in ("confidence", "sessions", "confidence_basis"):
        assert field in description, field
    assert "scan_injection" in entry["input_schema"]["properties"]


# ---------------------------------------------------------------------------
# The writer that makes session attribution measurable at all
# ---------------------------------------------------------------------------

def test_index_session_turn_records_the_session_ref():
    """Without this the turn carries no attribution and the distinct-session
    term is permanently unmeasurable — the score would be a frequency again."""
    from tools.memory import session_indexer

    conn = _make_db(with_session_ref=True)
    with patch.object(
        session_indexer, "_get_conn", return_value=translating(conn, unclosable=True)
    ), patch.object(session_indexer, "_is_sqlite", return_value=False):
        session_indexer.index_session_turn("sess-42", "user", "icdev status")

    row = conn.execute(
        "SELECT type, session_ref FROM memory_entries"
    ).fetchone()
    assert row["type"] == "session_user"
    assert row["session_ref"] == "sess-42"


def test_index_session_turn_still_writes_without_the_column():
    """A database that has not run migration 226 must still index the turn —
    silently dropping it would be worse than losing the attribution."""
    from tools.memory import session_indexer

    conn = _make_db(with_session_ref=False)
    with patch.object(
        session_indexer, "_get_conn", return_value=translating(conn, unclosable=True)
    ), patch.object(session_indexer, "_is_sqlite", return_value=False):
        session_indexer.index_session_turn("sess-42", "user", "icdev status")

    row = conn.execute("SELECT type, content FROM memory_entries").fetchone()
    assert row["type"] == "session_user"
    assert row["content"] == "icdev status"
