# CUI // SP-CTI
"""Pytest suite — ZIG scoring correctness (shx-test-02).

Verifies the ZIG maturity scoring math against hand-computed expected values:

  * zig_pillar_scorer  — 40% capability / 60% activity weighting (documented in
    zig_assessor.py reconcile docstring; implemented in score_pillar()).
  * zig_assessor       — run_zig_assessment persistence + return shape,
    reconcile_capability_status transitions, and _try_zta_bridge blend math
    (both the ZTA-present and ZTA-absent / ImportError paths).
  * zig_activity_tracker — CRUD edge cases (idempotent double-complete, unknown
    activity id, empty-DB scoring with no ZeroDivisionError).

Uses a self-contained in-memory SQLite DB whose schema matches the production
security_canvas init_db (tools/security_canvas/db/init_db.py) — including the
target_id column on zig_activity_completions — patched over get_connection in
every ZIG module under test. This mirrors the pattern in
tests/test_zig_external_targets.py and keeps expected values fully deterministic.

Patching of the ZTA bridge symbol is shim-aware: the module object is resolved
via importlib.import_module() and the attribute set on that exact object, so the
tools.* -> icdev.tools.* shim cannot route the patch to a different module.
"""
from __future__ import annotations

import contextlib
import importlib
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.security_canvas.constants import ZIG_PILLARS  # noqa: E402

_PILLAR_SLUGS = [p["slug"] for p in ZIG_PILLARS]

# ── In-memory schema (matches production security_canvas init_db) ─────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS zig_pillars (
    slug            TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    full_name       TEXT,
    pillar_weight   REAL DEFAULT 0.14
);

CREATE TABLE IF NOT EXISTS zig_capabilities (
    id                      TEXT PRIMARY KEY,
    pillar_slug             TEXT NOT NULL,
    title                   TEXT NOT NULL,
    phase                   TEXT NOT NULL,
    maturity_level          TEXT NOT NULL,
    description             TEXT,
    implementation_status   TEXT DEFAULT 'not_started'
);

CREATE TABLE IF NOT EXISTS zig_activities (
    id              TEXT PRIMARY KEY,
    capability_id   TEXT NOT NULL,
    phase           TEXT NOT NULL,
    title           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS zig_activity_completions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    activity_id     TEXT NOT NULL,
    target_id       TEXT NOT NULL DEFAULT 'icdev-self',
    status          TEXT NOT NULL DEFAULT 'not_started',
    evidence_note   TEXT,
    completed_by    TEXT,
    completed_at    TEXT,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_zig_comp_act
    ON zig_activity_completions(activity_id, target_id);

CREATE TABLE IF NOT EXISTS zig_maturity_scores (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    pillar_slug         TEXT NOT NULL,
    target_id           TEXT NOT NULL DEFAULT 'icdev-self',
    score               REAL NOT NULL DEFAULT 0.0,
    maturity_level      TEXT,
    capability_count    INTEGER DEFAULT 0,
    activity_count      INTEGER DEFAULT 0,
    complete_activities INTEGER DEFAULT 0,
    assessment_run_at   TEXT DEFAULT CURRENT_TIMESTAMP,
    created_at          TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS zta_maturity_scores (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL,
    pillar          TEXT NOT NULL,
    score           REAL,
    maturity_level  TEXT,
    evidence        TEXT,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


class _Conn:
    """Duck-typed sqlite wrapper that stays alive across close() calls."""

    def __init__(self):
        self._db = sqlite3.connect(":memory:")
        self._db.row_factory = sqlite3.Row
        for stmt in _SCHEMA.strip().split(";"):
            s = stmt.strip()
            if s and not s.startswith("--"):
                self._db.execute(s)
        self._db.commit()

    def execute(self, sql, params=()):
        return self._db.execute(sql.replace("%s", "?"), params)

    def commit(self):
        self._db.commit()

    def close(self):
        pass  # keep the in-memory DB alive for patched callers

    def cursor(self):
        return self._db.cursor()


# ── Patch helpers ────────────────────────────────────────────────────────────

_ZIG_MODULES = (
    "tools.security_canvas.zig_pillar_scorer",
    "tools.security_canvas.zig_assessor",
    "tools.security_canvas.zig_phase_tracker",
    "tools.security_canvas.zig_activity_tracker",
)


@contextlib.contextmanager
def patch_conn(conn):
    """Patch get_connection to return `conn` in every ZIG module under test."""
    with contextlib.ExitStack() as stack:
        for mod in _ZIG_MODULES:
            stack.enter_context(patch.multiple(mod, get_connection=lambda: conn))
        yield


# ── Seed helpers ─────────────────────────────────────────────────────────────

def _add_cap(conn, cap_id, pillar, status, phase="discovery", maturity="basic"):
    conn.execute(
        "INSERT INTO zig_capabilities "
        "(id, pillar_slug, title, phase, maturity_level, implementation_status) "
        "VALUES (?,?,?,?,?,?)",
        (cap_id, pillar, cap_id, phase, maturity, status),
    )


def _add_act(conn, act_id, cap_id, phase="discovery"):
    conn.execute(
        "INSERT INTO zig_activities (id, capability_id, phase, title) VALUES (?,?,?,?)",
        (act_id, cap_id, phase, act_id),
    )


def _add_completion(conn, act_id, status, target="icdev-self"):
    conn.execute(
        "INSERT INTO zig_activity_completions (activity_id, target_id, status) VALUES (?,?,?)",
        (act_id, target, status),
    )


def _add_zta_score(conn, score_id, project_id, pillar, score, maturity="advanced",
                   created_at="2026-01-01T00:00:00+00:00"):
    """Seed a row in the real zta_maturity_scores table (the accessor's source)."""
    conn.execute(
        "INSERT INTO zta_maturity_scores "
        "(id, project_id, pillar, score, maturity_level, evidence, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (score_id, project_id, pillar, score, maturity, "[]", created_at),
    )


@pytest.fixture
def conn():
    c = _Conn()
    yield c


# ══════════════════════════════════════════════════════════════════════════════
# 1. zig_pillar_scorer — score_pillar / score_all_pillars / aggregate
# ══════════════════════════════════════════════════════════════════════════════


def test_score_pillar_hand_computed_60_40_weighting(conn):
    """40% capability / 60% activity split with partial (0.5) in_progress credit.

    caps: 1 implemented, 1 not_started -> cap_rate = (1 + 0)/2 = 0.5
    acts: 4 total; 2 complete, 1 in_progress, 1 with no completion row
          -> activity_rate = (2 + 0.5*1)/4 = 0.625
    score = 0.6*0.625 + 0.4*0.5 = 0.375 + 0.2 = 0.575  -> intermediate
    """
    _add_cap(conn, "cap-A", "user", "implemented")
    _add_cap(conn, "cap-B", "user", "not_started")
    _add_act(conn, "a1", "cap-A")
    _add_act(conn, "a2", "cap-A")
    _add_act(conn, "a3", "cap-B")
    _add_act(conn, "a4", "cap-B")
    _add_completion(conn, "a1", "complete")
    _add_completion(conn, "a2", "complete")
    _add_completion(conn, "a3", "in_progress")
    # a4 intentionally has no completion row
    conn.commit()

    from tools.security_canvas.zig_pillar_scorer import score_pillar

    with patch_conn(conn):
        result = score_pillar("user")

    assert result["score"] == 0.575
    assert result["maturity_level"] == "intermediate"
    assert result["capability_count"] == 2
    assert result["implemented_capabilities"] == 1
    assert result["activity_count"] == 4
    assert result["complete_activities"] == 2
    assert result["in_progress_activities"] == 1


def test_score_pillar_capability_weight_isolated(conn):
    """No activities -> activity_rate 0, score is purely 40% * cap_rate.

    caps: 2 implemented, 2 not_started -> cap_rate = 2/4 = 0.5
    score = 0.4 * 0.5 = 0.2 -> preparation (< 0.25)
    """
    _add_cap(conn, "d1", "device", "implemented")
    _add_cap(conn, "d2", "device", "implemented")
    _add_cap(conn, "d3", "device", "not_started")
    _add_cap(conn, "d4", "device", "not_started")
    conn.commit()

    from tools.security_canvas.zig_pillar_scorer import score_pillar

    with patch_conn(conn):
        result = score_pillar("device")

    assert result["score"] == 0.2
    assert result["maturity_level"] == "preparation"
    assert result["activity_count"] == 0


def test_score_pillar_in_progress_capability_partial_credit(conn):
    """in_progress capability earns 0.5 credit.

    caps: 1 in_progress, 1 not_started -> cap_rate = (0 + 0.5*1)/2 = 0.25
    no activities -> score = 0.4 * 0.25 = 0.1
    """
    _add_cap(conn, "n1", "network", "in_progress")
    _add_cap(conn, "n2", "network", "not_started")
    conn.commit()

    from tools.security_canvas.zig_pillar_scorer import score_pillar

    with patch_conn(conn):
        result = score_pillar("network")

    assert result["score"] == 0.1
    assert result["capability_count"] == 2
    assert result["implemented_capabilities"] == 0


def test_score_pillar_empty_returns_zero_no_error(conn):
    """Pillar with no capabilities -> early return, all zero, no division error."""
    from tools.security_canvas.zig_pillar_scorer import score_pillar

    with patch_conn(conn):
        result = score_pillar("data")

    assert result["score"] == 0.0
    assert result["maturity_level"] == "preparation"
    assert result["capability_count"] == 0
    assert result["activity_count"] == 0
    assert result["complete_activities"] == 0


def test_score_pillar_activities_without_completions(conn):
    """Activities exist but have zero completion rows -> activity_rate 0."""
    _add_cap(conn, "v1", "visibility", "not_started")
    _add_act(conn, "va1", "v1")
    _add_act(conn, "va2", "v1")
    conn.commit()

    from tools.security_canvas.zig_pillar_scorer import score_pillar

    with patch_conn(conn):
        result = score_pillar("visibility")

    assert result["score"] == 0.0
    assert result["activity_count"] == 2
    assert result["complete_activities"] == 0


def test_score_all_pillars_returns_seven_with_metadata(conn):
    from tools.security_canvas.zig_pillar_scorer import score_all_pillars

    with patch_conn(conn):
        results = score_all_pillars()

    assert len(results) == len(ZIG_PILLARS) == 7
    assert [r["slug"] for r in results] == _PILLAR_SLUGS
    for r in results:
        for key in ("name", "full_name", "color", "icon", "pillar_weight"):
            assert key in r


def test_score_all_pillars_accepts_target_id_kwarg(conn):
    """Regression (cnr-zig-01): /security/zig/portfolio calls
    score_all_pillars(target_id='icdev-self'). The signature must accept the
    kwarg — the old no-arg signature raised TypeError, which the page swallowed
    (pillar_scores=[]) so the portfolio rendered all-zero on every visit. This
    calls the REAL function (only get_connection is patched), so a signature
    regression fails here instead of silently emptying the page."""
    from tools.security_canvas.zig_pillar_scorer import score_all_pillars

    with patch_conn(conn):
        results = score_all_pillars(target_id="icdev-self")

    assert len(results) == len(ZIG_PILLARS) == 7


def test_score_pillar_completions_are_target_scoped(conn):
    """cnr-zig-01/02: a completion recorded for one target must not inflate a
    different target's pillar score."""
    _add_cap(conn, "cap-A", "user", "not_started")
    _add_act(conn, "a1", "cap-A")
    _add_completion(conn, "a1", "complete", target="external-target")
    conn.commit()

    from tools.security_canvas.zig_pillar_scorer import score_pillar

    with patch_conn(conn):
        selfd = score_pillar("user", target_id="icdev-self")
        externald = score_pillar("user", target_id="external-target")

    # The lone completion belongs to external-target, not icdev-self.
    assert selfd["complete_activities"] == 0
    assert externald["complete_activities"] == 1
    assert externald["score"] > selfd["score"]


def test_aggregate_zig_score_weighted_average(conn):
    """Weighted aggregate over explicit pillar scores.

    weighted = (0.8*0.20 + 0.4*0.15 + 0.0*0.15) / (0.20+0.15+0.15)
             = (0.16 + 0.06 + 0.0) / 0.50 = 0.22 / 0.50 = 0.44 -> basic
    fy2027 = min(100, round(0.44 / 0.50 * 100)) = 88
    """
    from tools.security_canvas.zig_pillar_scorer import aggregate_zig_score

    pillar_scores = [
        {"slug": "a", "score": 0.8, "pillar_weight": 0.20},
        {"slug": "b", "score": 0.4, "pillar_weight": 0.15},
        {"slug": "c", "score": 0.0, "pillar_weight": 0.15},
    ]
    agg = aggregate_zig_score(pillar_scores)

    assert agg["score"] == 0.44
    assert agg["maturity_level"] == "basic"
    assert agg["fy2027_readiness_pct"] == 88


def test_aggregate_zig_score_zero_weight(conn):
    """All-zero weights -> guard returns preparation with no fy2027 key."""
    from tools.security_canvas.zig_pillar_scorer import aggregate_zig_score

    agg = aggregate_zig_score([{"slug": "x", "score": 0.9, "pillar_weight": 0.0}])

    assert agg["score"] == 0.0
    assert agg["maturity_level"] == "preparation"
    assert "fy2027_readiness_pct" not in agg


@pytest.mark.parametrize(
    "score,expected",
    [
        (0.0, "preparation"),
        (0.24, "preparation"),
        (0.25, "basic"),
        (0.49, "basic"),
        (0.50, "intermediate"),
        (0.74, "intermediate"),
        (0.75, "advanced"),
        (1.0, "advanced"),
    ],
)
def test_maturity_level_banding(score, expected):
    from tools.security_canvas.zig_pillar_scorer import _maturity_level_for_score

    assert _maturity_level_for_score(score) == expected


# ══════════════════════════════════════════════════════════════════════════════
# 2. zig_assessor — run_zig_assessment / reconcile / _try_zta_bridge
# ══════════════════════════════════════════════════════════════════════════════


def test_run_zig_assessment_persists_one_row_per_pillar(conn):
    """With ZTA bridge absent, run persists exactly one row per pillar (7)."""
    from tools.security_canvas.zig_assessor import run_zig_assessment

    # Force the ImportError path: no get_latest_score symbol reachable.
    with patch.dict(sys.modules, {"tools.devsecops.zta_maturity_scorer": None}):
        with patch_conn(conn):
            result = run_zig_assessment()

    rows = conn.execute(
        "SELECT pillar_slug, score, maturity_level, capability_count, "
        "activity_count, complete_activities FROM zig_maturity_scores"
    ).fetchall()

    assert len(rows) == 7
    persisted_slugs = sorted(r["pillar_slug"] for r in rows)
    assert persisted_slugs == sorted(_PILLAR_SLUGS)
    # No separate aggregate row is persisted (aggregate lives only in the return).
    assert all(r["pillar_slug"] in _PILLAR_SLUGS for r in rows)
    assert "aggregate" in result and "score" in result["aggregate"]
    # Empty DB -> every pillar scores 0.0 with ZTA bridge unavailable.
    assert all(r["score"] == 0.0 for r in rows)


def test_run_zig_assessment_return_shape(conn):
    from tools.security_canvas.zig_assessor import run_zig_assessment

    with patch.dict(sys.modules, {"tools.devsecops.zta_maturity_scorer": None}):
        with patch_conn(conn):
            result = run_zig_assessment()

    for key in ("pillar_scores", "aggregate", "gaps", "phases", "fy2027",
                "assessed_at", "top_gaps"):
        assert key in result
    assert len(result["pillar_scores"]) == 7
    assert len(result["top_gaps"]) <= 10


def test_reconcile_promotes_to_implemented(conn):
    """rate >= 0.70 -> implemented. 2 complete of 2 activities -> rate 1.0."""
    _add_cap(conn, "cap-r1", "user", "not_started")
    _add_act(conn, "r1a", "cap-r1")
    _add_act(conn, "r1b", "cap-r1")
    _add_completion(conn, "r1a", "complete")
    _add_completion(conn, "r1b", "complete")
    conn.commit()

    from tools.security_canvas.zig_assessor import reconcile_capability_status

    with patch_conn(conn):
        changed = reconcile_capability_status()

    assert changed == 1
    row = conn.execute(
        "SELECT implementation_status FROM zig_capabilities WHERE id=?", ("cap-r1",)
    ).fetchone()
    assert row["implementation_status"] == "implemented"


def test_reconcile_promotes_to_in_progress(conn):
    """0.30 <= rate < 0.70 -> in_progress. 1 complete of 2 -> rate 0.5."""
    _add_cap(conn, "cap-r2", "device", "not_started")
    _add_act(conn, "r2a", "cap-r2")
    _add_act(conn, "r2b", "cap-r2")
    _add_completion(conn, "r2a", "complete")
    conn.commit()

    from tools.security_canvas.zig_assessor import reconcile_capability_status

    with patch_conn(conn):
        changed = reconcile_capability_status()

    assert changed == 1
    row = conn.execute(
        "SELECT implementation_status FROM zig_capabilities WHERE id=?", ("cap-r2",)
    ).fetchone()
    assert row["implementation_status"] == "in_progress"


def test_reconcile_below_threshold_unchanged(conn):
    """rate < 0.30 leaves manual not_started untouched. 0 of 4 complete."""
    _add_cap(conn, "cap-r3", "network", "not_started")
    for i in range(4):
        _add_act(conn, f"r3-{i}", "cap-r3")
    conn.commit()

    from tools.security_canvas.zig_assessor import reconcile_capability_status

    with patch_conn(conn):
        changed = reconcile_capability_status()

    assert changed == 0
    row = conn.execute(
        "SELECT implementation_status FROM zig_capabilities WHERE id=?", ("cap-r3",)
    ).fetchone()
    assert row["implementation_status"] == "not_started"


def test_reconcile_no_activities_skipped(conn):
    """Capability with no activities is skipped (never counted as changed)."""
    _add_cap(conn, "cap-r4", "data", "planned")
    conn.commit()

    from tools.security_canvas.zig_assessor import reconcile_capability_status

    with patch_conn(conn):
        changed = reconcile_capability_status()

    assert changed == 0
    row = conn.execute(
        "SELECT implementation_status FROM zig_capabilities WHERE id=?", ("cap-r4",)
    ).fetchone()
    assert row["implementation_status"] == "planned"


def test_try_zta_bridge_present_returns_raw_score(conn):
    """When get_latest_score is injected, the bridge maps user->user_identity.

    _try_zta_bridge returns the RAW zta score; the 0.5 blend happens in
    run_zig_assessment (verified separately below).
    """
    zta_mod = importlib.import_module("tools.devsecops.zta_maturity_scorer")

    def _fake_get_latest_score():
        return {"pillar_scores": {"user_identity": 0.8, "device": 0.6}}

    from tools.security_canvas.zig_assessor import _try_zta_bridge

    with patch.object(zta_mod, "get_latest_score", _fake_get_latest_score, create=True):
        assert _try_zta_bridge("user") == 0.8
        assert _try_zta_bridge("device") == 0.6


def test_try_zta_bridge_present_missing_key_returns_none(conn):
    """Score data lacking the mapped key -> None (no blend applied)."""
    zta_mod = importlib.import_module("tools.devsecops.zta_maturity_scorer")

    def _fake_get_latest_score():
        return {"pillar_scores": {"device": 0.6}}

    from tools.security_canvas.zig_assessor import _try_zta_bridge

    with patch.object(zta_mod, "get_latest_score", _fake_get_latest_score, create=True):
        assert _try_zta_bridge("user") is None


def test_try_zta_bridge_absent_importerror_returns_none(conn):
    """ImportError path -> None (pure ZIG score, no bridge)."""
    from tools.security_canvas.zig_assessor import _try_zta_bridge

    with patch.dict(sys.modules, {"tools.devsecops.zta_maturity_scorer": None}):
        assert _try_zta_bridge("user") is None


def test_run_assessment_blends_zta_as_half_signal(conn):
    """A zero-scoring pillar picks up round(zta * 0.5, 4) when the bridge fires.

    user pillar has no capabilities -> ZIG score 0.0, so the bridge applies:
    zta 0.8 -> persisted/returned score = round(0.8 * 0.5, 4) = 0.4.
    """
    zta_mod = importlib.import_module("tools.devsecops.zta_maturity_scorer")

    def _fake_get_latest_score():
        return {"pillar_scores": {"user_identity": 0.8}}

    from tools.security_canvas.zig_assessor import run_zig_assessment

    with patch.object(zta_mod, "get_latest_score", _fake_get_latest_score, create=True):
        with patch_conn(conn):
            result = run_zig_assessment()

    user = next(p for p in result["pillar_scores"] if p["slug"] == "user")
    assert user["score"] == 0.4
    # A pillar without a mapped zta key stays at pure ZIG 0.0.
    automation = next(p for p in result["pillar_scores"] if p["slug"] == "automation")
    assert automation["score"] == 0.0


def test_run_assessment_no_blend_when_bridge_absent(conn):
    """ImportError path leaves zero-scoring pillars at pure ZIG 0.0."""
    from tools.security_canvas.zig_assessor import run_zig_assessment

    with patch.dict(sys.modules, {"tools.devsecops.zta_maturity_scorer": None}):
        with patch_conn(conn):
            result = run_zig_assessment()

    user = next(p for p in result["pillar_scores"] if p["slug"] == "user")
    assert user["score"] == 0.0


def test_get_latest_score_reads_seeded_real_row(conn):
    """The REAL get_latest_score accessor returns persisted per-pillar scores.

    Seeds two pillar rows + an overall row for one project; the accessor
    (no project_id -> latest project wins) returns pillar_scores keyed by the
    ZTA pillar names in the raw 0.0-1.0 range, plus the overall summary.
    """
    _add_zta_score(conn, "z-u", "proj-A", "user_identity", 0.9, "optimal")
    _add_zta_score(conn, "z-d", "proj-A", "device", 0.6, "advanced")
    _add_zta_score(conn, "z-o", "proj-A", "overall", 0.75, "optimal")
    conn.commit()

    zta_mod = importlib.import_module("tools.devsecops.zta_maturity_scorer")

    with patch.object(zta_mod, "get_connection", lambda: conn):
        data = zta_mod.get_latest_score()

    assert data is not None
    assert data["project_id"] == "proj-A"
    assert data["pillar_scores"]["user_identity"] == 0.9
    assert data["pillar_scores"]["device"] == 0.6
    # 'overall' is surfaced separately, never as a pillar key.
    assert "overall" not in data["pillar_scores"]
    assert data["overall_score"] == 0.75
    assert data["overall_maturity"] == "optimal"


def test_get_latest_score_none_when_empty(conn):
    """No persisted assessment -> accessor returns None cleanly (no error)."""
    zta_mod = importlib.import_module("tools.devsecops.zta_maturity_scorer")

    with patch.object(zta_mod, "get_connection", lambda: conn):
        assert zta_mod.get_latest_score() is None


def test_run_assessment_blends_seeded_real_zta_score(conn):
    """End-to-end: a SEEDED real zta row flows through the REAL accessor and
    the documented 0.5 blend, with no fake injected.

    Seeded user_identity=0.9 -> ZIG 'user' pillar (empty, ZIG score 0.0) picks
    up round(0.9 * 0.5, 4) = 0.45. A pillar with no seeded zta key stays 0.0.
    """
    _add_zta_score(conn, "z-u", "proj-B", "user_identity", 0.9, "optimal")
    conn.commit()

    zta_mod = importlib.import_module("tools.devsecops.zta_maturity_scorer")
    from tools.security_canvas.zig_assessor import run_zig_assessment

    with patch.object(zta_mod, "get_connection", lambda: conn):
        with patch_conn(conn):
            result = run_zig_assessment()

    user = next(p for p in result["pillar_scores"] if p["slug"] == "user")
    assert user["score"] == 0.45
    automation = next(p for p in result["pillar_scores"] if p["slug"] == "automation")
    assert automation["score"] == 0.0


def test_run_assessment_pure_zig_when_no_seeded_zta_score(conn):
    """Bridge present + accessor returns None (empty zta table) -> pure ZIG 0.0.

    Distinct from the ImportError path: here get_latest_score is importable and
    runs, but finds no persisted assessment, so no blend is applied.
    """
    zta_mod = importlib.import_module("tools.devsecops.zta_maturity_scorer")
    from tools.security_canvas.zig_assessor import run_zig_assessment

    with patch.object(zta_mod, "get_connection", lambda: conn):
        with patch_conn(conn):
            result = run_zig_assessment()

    user = next(p for p in result["pillar_scores"] if p["slug"] == "user")
    assert user["score"] == 0.0


# ══════════════════════════════════════════════════════════════════════════════
# 3. zig_activity_tracker — CRUD edge cases
# ══════════════════════════════════════════════════════════════════════════════


def test_complete_same_activity_twice_is_idempotent(conn):
    """Completing the same activity twice updates in place — one row, no error."""
    from tools.security_canvas.zig_activity_tracker import set_activity_status

    with patch_conn(conn):
        set_activity_status("act-x", "complete")
        set_activity_status("act-x", "complete")

    count = conn.execute(
        "SELECT COUNT(*) FROM zig_activity_completions WHERE activity_id=?", ("act-x",)
    ).fetchone()[0]
    assert count == 1
    status = conn.execute(
        "SELECT status FROM zig_activity_completions WHERE activity_id=?", ("act-x",)
    ).fetchone()["status"]
    assert status == "complete"


def test_set_activity_status_transitions_and_completed_at(conn):
    """completed_at is set only on 'complete'; earlier states leave it NULL."""
    from tools.security_canvas.zig_activity_tracker import (
        get_activity_completion,
        set_activity_status,
    )

    with patch_conn(conn):
        set_activity_status("act-t", "not_started")
        rec1 = get_activity_completion("act-t")
        set_activity_status("act-t", "in_progress")
        rec2 = get_activity_completion("act-t")
        set_activity_status("act-t", "complete")
        rec3 = get_activity_completion("act-t")

    assert rec1["status"] == "not_started"
    assert rec1["completed_at"] is None
    assert rec2["status"] == "in_progress"
    assert rec2["completed_at"] is None
    assert rec3["status"] == "complete"
    assert rec3["completed_at"] is not None


def test_set_activity_status_invalid_raises(conn):
    from tools.security_canvas.zig_activity_tracker import set_activity_status

    with patch_conn(conn):
        with pytest.raises(ValueError):
            set_activity_status("act-bad", "done")  # not a valid status


def test_get_activity_completion_unknown_returns_default(conn):
    """Unknown activity id yields a synthetic not_started record, not None/error."""
    from tools.security_canvas.zig_activity_tracker import get_activity_completion

    with patch_conn(conn):
        rec = get_activity_completion("never-seen")

    assert rec["activity_id"] == "never-seen"
    assert rec["status"] == "not_started"
    assert rec["evidence_note"] is None


def test_get_phase_completions_empty_db_no_division_error(conn):
    """Scoring a phase with zero activities must not raise ZeroDivisionError."""
    from tools.security_canvas.zig_activity_tracker import get_phase_completions

    with patch_conn(conn):
        result = get_phase_completions("discovery")

    assert result["total"] == 0
    assert result["complete"] == 0
    assert result["in_progress"] == 0
    assert result["not_started"] == 0
    assert result["pct_complete"] == 0


def test_get_phase_completions_counts_and_pct(conn):
    """1 complete + 1 in_progress of 2 discovery activities -> 50% complete."""
    _add_cap(conn, "cap-p", "user", "not_started")
    _add_act(conn, "p1", "cap-p", phase="discovery")
    _add_act(conn, "p2", "cap-p", phase="discovery")
    conn.commit()

    from tools.security_canvas.zig_activity_tracker import (
        get_phase_completions,
        set_activity_status,
    )

    with patch_conn(conn):
        set_activity_status("p1", "complete")
        set_activity_status("p2", "in_progress")
        result = get_phase_completions("discovery")

    assert result["total"] == 2
    assert result["complete"] == 1
    assert result["in_progress"] == 1
    assert result["not_started"] == 0
    assert result["pct_complete"] == 50


def test_bulk_activity_status_returns_count(conn):
    from tools.security_canvas.zig_activity_tracker import bulk_activity_status

    with patch_conn(conn):
        n = bulk_activity_status(["b1", "b2", "b3"], "complete")

    assert n == 3
    total = conn.execute(
        "SELECT COUNT(*) FROM zig_activity_completions WHERE status='complete'"
    ).fetchone()[0]
    assert total == 3


# ══════════════════════════════════════════════════════════════════════════════
# 4. cnr-zig-02 — end-to-end target scoping (set_activity_status -> score -> run)
# ══════════════════════════════════════════════════════════════════════════════


def test_set_activity_status_writes_to_given_target(conn):
    """cnr-zig-02: the completion INSERT lands on the supplied target_id, not the
    'icdev-self' default. Calls the REAL function (get_connection patched)."""
    _add_cap(conn, "cap-A", "user", "not_started")
    _add_act(conn, "a1", "cap-A")
    conn.commit()

    from tools.security_canvas.zig_activity_tracker import set_activity_status

    with patch_conn(conn):
        result = set_activity_status("a1", "complete", target_id="tgt-z")

    assert result["target_id"] == "tgt-z"
    row = conn.execute(
        "SELECT target_id, status FROM zig_activity_completions WHERE activity_id=?",
        ("a1",),
    ).fetchone()
    assert row["target_id"] == "tgt-z"
    assert row["status"] == "complete"


def test_same_activity_two_targets_recorded_and_scored_independently(conn):
    """cnr-zig-02 acceptance: two targets record independent completions on the
    same activity graph and receive independent pillar scores."""
    _add_cap(conn, "cap-A", "user", "not_started")
    _add_act(conn, "a1", "cap-A")
    _add_act(conn, "a2", "cap-A")
    conn.commit()

    from tools.security_canvas.zig_activity_tracker import set_activity_status
    from tools.security_canvas.zig_pillar_scorer import score_pillar

    with patch_conn(conn):
        set_activity_status("a1", "complete", target_id="tgt-a")
        set_activity_status("a2", "complete", target_id="tgt-a")
        set_activity_status("a1", "in_progress", target_id="tgt-b")

        score_a = score_pillar("user", target_id="tgt-a")
        score_b = score_pillar("user", target_id="tgt-b")

    # Independent completion rows (unique index on activity_id+target_id).
    n = conn.execute("SELECT COUNT(*) FROM zig_activity_completions").fetchone()[0]
    assert n == 3
    assert score_a["complete_activities"] == 2
    assert score_a["in_progress_activities"] == 0
    assert score_b["complete_activities"] == 0
    assert score_b["in_progress_activities"] == 1
    assert score_a["score"] > score_b["score"]


def test_run_zig_assessment_persists_rows_tagged_by_target(conn):
    """cnr-zig-02: persisted zig_maturity_scores rows carry the assessed target,
    so per-target history stays separable for the portfolio comparison."""
    from tools.security_canvas.zig_assessor import run_zig_assessment

    with patch.dict(sys.modules, {"tools.devsecops.zta_maturity_scorer": None}):
        with patch_conn(conn):
            result = run_zig_assessment(target_id="tgt-x")

    assert result["target_id"] == "tgt-x"
    tgts = [
        r["target_id"]
        for r in conn.execute(
            "SELECT DISTINCT target_id FROM zig_maturity_scores"
        ).fetchall()
    ]
    assert tgts == ["tgt-x"]
