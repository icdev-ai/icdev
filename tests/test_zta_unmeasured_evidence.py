# [TEMPLATE: CUI // SP-CTI]
"""ZTA maturity refuses to score over empty evidence (rmf-zt-02).

THE DEFECT. ``zta_posture_evidence`` carries an ``evidence_data`` column that
nothing was required to fill. The scorer counted rows whose ``status`` was
``'current'`` and divided by the number of DECLARED evidence types::

    posture_score = current_evidence / total_types if total_types > 0 else 0.0

so a pillar whose rows were all ticks with nothing behind them scored a ratio
over a CHECKBOX LIST, and a pillar with no rows at all scored a structural
``0/5`` — a constant wearing the name of a measurement. Either number was then
averaged into the pillar score, persisted with a maturity BAND, and read
downstream (``cato_monitor``'s ``cato_contribution``, the ZIG bridge, the MCP
``zta_posture_check``) as though somebody had assessed the pillar.

MEASURED on the live PostgreSQL board 2026-09-02: ``zta_posture_evidence`` and
``zta_maturity_scores`` both hold ZERO rows, so every one of the seven pillars
would have reported a maturity band over an evidence table that has never been
written to.

The tests below pin the three states apart — ``evidence_backed`` (proven),
``self_attested`` (claimed) and ``unmeasured`` (nobody looked) — and pin that a
MEASURED zero still reads as a measured zero, because the inverse mistake
(hiding a real finding behind "unmeasured") is just as bad.
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Translating wrapper — zta_maturity_scorer authors %s for PostgreSQL.
from _sql_compat import connect as _tconnect  # noqa: E402

from tools.devsecops.zta_maturity_scorer import (  # noqa: E402
    PILLARS,
    _load_config,
    get_latest_score,
    score_all_pillars,
    score_pillar,
)

# Pinned as a LITERAL, not imported from the module under test. Importing the
# constant would make these assertions pass against any rename, and it would
# turn the pre-change tree's failure into a collection ImportError — proof that
# a symbol is new, which is not the same as proof that the behaviour changed.
UNMEASURED = "unmeasured"

PROJECT_ID = "proj-zta-unmeasured"
PILLAR = "network"

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    classification TEXT NOT NULL DEFAULT 'CUI'
);
CREATE TABLE IF NOT EXISTS project_controls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    control_id TEXT NOT NULL,
    implementation_status TEXT NOT NULL DEFAULT 'planned',
    UNIQUE(project_id, control_id)
);
CREATE TABLE IF NOT EXISTS zta_posture_evidence (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    evidence_type TEXT NOT NULL,
    evidence_data TEXT,
    status TEXT DEFAULT 'not_collected',
    collected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS devsecops_profiles (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    active_stages TEXT
);
-- The live CHECK admits 'unmeasured' via migration 20260903003116; the fixture
-- carries the post-migration shape.
CREATE TABLE IF NOT EXISTS zta_maturity_scores (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    pillar TEXT NOT NULL,
    score REAL,
    maturity_level TEXT CHECK(
        maturity_level IN ('traditional', 'advanced', 'optimal', 'unmeasured')
    ),
    evidence TEXT,
    assessed_by TEXT DEFAULT 'icdev-devsecops-agent',
    created_at TEXT
);
"""

_SHIPPED = _load_config().get("pillars") or {}

# Weights and bands are pinned for determinism; the control lists and evidence
# types come from the SHIPPED args/zta_config.yaml, so a test cannot pass
# against an empty declaration the way an earlier revision of the sibling suite
# did.
TEST_CONFIG = {
    "pillars": {
        p: {
            "weight": 1.0 / len(PILLARS),
            "nist_800_53_controls": list(_SHIPPED.get(p, {}).get("nist_800_53_controls", [])),
            "evidence_types": list(_SHIPPED.get(p, {}).get("evidence_types", [])),
        }
        for p in PILLARS
    },
    "maturity_levels": {
        "traditional": {"score_range": [0.0, 0.33]},
        "advanced": {"score_range": [0.34, 0.66]},
        "optimal": {"score_range": [0.67, 1.0]},
    },
}

EVIDENCE_TYPES = TEST_CONFIG["pillars"][PILLAR]["evidence_types"]
CONTROLS = TEST_CONFIG["pillars"][PILLAR]["nist_800_53_controls"]

assert EVIDENCE_TYPES, "args/zta_config.yaml declares no evidence types for the test pillar"
assert CONTROLS, "args/zta_config.yaml declares no NIST controls for the test pillar"


@pytest.fixture
def db(tmp_path):
    """A project with NO evidence and NO control rows — the live board's shape."""
    path = tmp_path / "icdev.db"
    conn = _tconnect(path)
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT INTO projects (id, name) VALUES (%s, %s)", (PROJECT_ID, "ZTA Unmeasured")
    )
    conn.commit()
    conn.close()
    return path


def _seed_evidence(db_path, rows):
    """rows: iterable of (evidence_type, status, evidence_data)."""
    conn = _tconnect(db_path)
    for i, (etype, status, data) in enumerate(rows):
        conn.execute(
            "INSERT INTO zta_posture_evidence "
            "(id, project_id, evidence_type, status, evidence_data) "
            "VALUES (%s, %s, %s, %s, %s)",
            (f"ev-{i}", PROJECT_ID, etype, status, data),
        )
    conn.commit()
    conn.close()


def _seed_controls(db_path, statuses):
    """statuses: dict control_id -> status."""
    conn = _tconnect(db_path)
    for cid, status in statuses.items():
        conn.execute(
            "INSERT INTO project_controls (project_id, control_id, implementation_status) "
            "VALUES (%s, %s, %s)",
            (PROJECT_ID, cid, status),
        )
    conn.commit()
    conn.close()


def _patched(db_path):
    return (
        patch(
            "tools.devsecops.zta_maturity_scorer.get_connection",
            lambda *a, **k: _tconnect(db_path),
        ),
        patch(
            "tools.devsecops.zta_maturity_scorer._load_config",
            return_value=TEST_CONFIG,
        ),
    )


def _score_pillar(db_path, pillar=PILLAR):
    pdb, pcfg = _patched(db_path)
    with pdb, pcfg:
        return score_pillar(PROJECT_ID, pillar)


def _score_all(db_path):
    pdb, pcfg = _patched(db_path)
    with pdb, pcfg:
        return score_all_pillars(PROJECT_ID)


# ---------------------------------------------------------------------------
# has_evidence_data — the predicate everything else rests on
# ---------------------------------------------------------------------------


class TestHasEvidenceData:
    # Imported inside each test, not at module scope: a module-level import of a
    # symbol this branch introduces would abort COLLECTION on the pre-change
    # tree, so every behavioural test below would report an ImportError instead
    # of the wrong VALUE it is actually pinning.
    @pytest.mark.parametrize("value", [None, "", "   ", "null", "NULL", "none", "{}", "[]", '""'])
    def test_empty_forms_are_not_evidence(self, value):
        from tools.devsecops.zta_maturity_scorer import has_evidence_data

        assert has_evidence_data(value) is False

    @pytest.mark.parametrize(
        "value",
        ['{"mtls": true}', "collected 2026-09-02", 0, False, {"a": 1}, ["x"], 0.0],
    )
    def test_real_values_are_evidence(self, value):
        # A scalar 0 / False IS an answer. Treating "we measured it and it was
        # zero" as absence is the same defect inverted.
        from tools.devsecops.zta_maturity_scorer import has_evidence_data

        assert has_evidence_data(value) is True


# ---------------------------------------------------------------------------
# The card's acceptance criterion, first half
# ---------------------------------------------------------------------------


class TestPillarWithNullEvidenceDataIsUnmeasured:
    """A pillar whose evidence rows carry no evidence_data reports unmeasured."""

    def test_all_null_evidence_data_gives_no_score(self, db):
        # Every declared evidence type ticked 'current' with NOTHING behind it.
        _seed_evidence(db, [(t, "current", None) for t in EVIDENCE_TYPES])
        result = _score_pillar(db)

        # Before rmf-zt-02 this was 1.0 / 'optimal': five of five ticks.
        assert result["score"] is None, "a checkbox list must not produce a ratio"
        assert result["maturity_level"] == UNMEASURED
        assert result["measured"] is False

    def test_empty_string_evidence_data_is_also_unmeasured(self, db):
        _seed_evidence(db, [(t, "current", "") for t in EVIDENCE_TYPES])
        result = _score_pillar(db)
        assert result["score"] is None
        assert result["maturity_level"] == UNMEASURED

    def test_no_evidence_rows_at_all_is_unmeasured_not_zero(self, db):
        # The LIVE board's shape: zta_posture_evidence holds nothing.
        result = _score_pillar(db)
        assert result["score"] is None, "0/5 over an empty table is not a measurement"
        assert result["maturity_level"] == UNMEASURED
        posture = next(c for c in result["evidence"] if c["type"] == "posture_evidence")
        assert posture["state"] == "no_evidence_rows"
        assert posture["measured"] is False
        assert posture["score"] is None

    def test_unmeasured_state_names_the_cause(self, db):
        _seed_evidence(db, [(t, "current", None) for t in EVIDENCE_TYPES])
        result = _score_pillar(db)
        posture = next(c for c in result["evidence"] if c["type"] == "posture_evidence")
        # "nobody wrote a row" and "somebody ticked a box" are different fixes.
        assert posture["state"] == "self_attested_only"

    def test_partial_evidence_scores_only_the_backed_rows(self, db):
        # Two of five carry real evidence; three are bare ticks.
        rows = [(t, "current", '{"proof": 1}') for t in EVIDENCE_TYPES[:2]]
        rows += [(t, "current", None) for t in EVIDENCE_TYPES[2:]]
        _seed_evidence(db, rows)
        result = _score_pillar(db)

        posture = next(c for c in result["evidence"] if c["type"] == "posture_evidence")
        assert posture["measured"] is True
        assert posture["score"] == pytest.approx(2 / len(EVIDENCE_TYPES), abs=1e-3)
        # The ticks are NOT credited to the evidence-backed score.
        assert posture["score"] < posture["self_attested_score"]


class TestMeasuredZeroStaysAZero:
    """The inverse mistake: hiding a real finding behind 'unmeasured'."""

    def test_control_rows_all_planned_is_a_measured_zero(self, db):
        _seed_controls(db, {c: "planned" for c in CONTROLS})
        result = _score_pillar(db)
        nist = next(c for c in result["evidence"] if c["type"] == "nist_controls")
        assert nist["measured"] is True
        assert nist["score"] == 0.0, "a measured 0% is a finding and must stay one"
        # The pillar therefore HAS a signal and is not unmeasured.
        assert result["score"] == 0.0
        assert result["maturity_level"] != UNMEASURED

    def test_absent_control_rows_are_unmeasured_not_zero(self, db):
        result = _score_pillar(db)
        nist = next(c for c in result["evidence"] if c["type"] == "nist_controls")
        assert nist["measured"] is False
        assert nist["state"] == "no_control_rows"
        assert nist["score"] is None


# ---------------------------------------------------------------------------
# The card's acceptance criterion, second half
# ---------------------------------------------------------------------------


class TestTwoNumbersNeverMerged:
    """evidence_backed and self_attested are surfaced as two numbers."""

    def test_pillar_reports_both_counts_separately(self, db):
        rows = [(t, "current", '{"proof": 1}') for t in EVIDENCE_TYPES[:2]]
        rows += [(t, "current", None) for t in EVIDENCE_TYPES[2:]]
        _seed_evidence(db, rows)
        result = _score_pillar(db)

        assert result["evidence_backed"] == 2
        assert result["self_attested"] == len(EVIDENCE_TYPES) - 2
        # Two numbers, not one total.
        assert result["evidence_backed"] != result["self_attested"]

    def test_pillar_reports_both_scores_separately(self, db):
        _seed_evidence(db, [(t, "current", None) for t in EVIDENCE_TYPES])
        result = _score_pillar(db)

        # Proven: nothing. Claimed: everything. These must not be one figure.
        assert result["score"] is None
        assert result["self_attested_score"] == 1.0
        assert result["self_attested_maturity"] == "optimal"
        assert result["maturity_level"] == UNMEASURED

    def test_self_attested_score_is_none_not_zero_when_nothing_claimed(self, db):
        result = _score_pillar(db)
        assert result["self_attested_score"] is None, (
            "'claimed nothing' must not render as 'claimed zero'"
        )

    def test_overall_reports_both_numbers(self, db):
        _seed_evidence(db, [(t, "current", None) for t in EVIDENCE_TYPES])
        result = _score_all(db)

        assert result["overall_score"] is None
        assert result["overall_maturity"] == UNMEASURED
        assert result["self_attested_score"] is not None
        assert result["self_attested_maturity"] != UNMEASURED

    def test_evidence_backed_and_self_attested_types_are_listed(self, db):
        rows = [(EVIDENCE_TYPES[0], "current", '{"proof": 1}')]
        rows += [(t, "current", None) for t in EVIDENCE_TYPES[1:]]
        _seed_evidence(db, rows)
        result = _score_pillar(db)
        posture = next(c for c in result["evidence"] if c["type"] == "posture_evidence")
        assert posture["evidence_backed_types"] == [EVIDENCE_TYPES[0]]
        assert set(posture["self_attested_types"]) == set(EVIDENCE_TYPES[1:])


# ---------------------------------------------------------------------------
# Aggregate coverage
# ---------------------------------------------------------------------------


class TestAggregateCoverage:
    def test_empty_board_reports_unmeasured_never_zero(self, db):
        result = _score_all(db)
        assert result["overall_score"] is None
        assert result["overall_maturity"] == UNMEASURED
        assert result["measured_pillars"] == []
        assert sorted(result["unmeasured_pillars"]) == sorted(PILLARS)
        assert result["declared_pillars"] == len(PILLARS)

    def test_unmeasured_pillar_is_excluded_from_the_denominator(self, db):
        # One pillar fully evidence-backed; the other six unmeasured.
        _seed_evidence(db, [(t, "current", '{"proof": 1}') for t in EVIDENCE_TYPES])
        result = _score_all(db)

        assert result["measured_pillars"] == [PILLAR]
        assert len(result["unmeasured_pillars"]) == len(PILLARS) - 1
        # Carrying the six at 0.0 would give ~0.14 and a 'traditional' band.
        assert result["overall_score"] == pytest.approx(1.0, abs=1e-3)
        assert result["overall_maturity"] == "optimal"

    def test_recommendation_names_the_unmeasured_pillars(self, db):
        result = _score_all(db)
        assert "UNMEASURED" in result["recommendation"]
        assert "not a clean bill of health" in result["recommendation"]

    def test_weakest_pillars_never_include_an_unmeasured_one(self, db):
        _seed_evidence(db, [(t, "current", '{"proof": 1}') for t in EVIDENCE_TYPES])
        result = _score_all(db)
        weakest = {w["pillar"] for w in result["weakest_pillars"]}
        assert weakest.isdisjoint(set(result["unmeasured_pillars"]))


# ---------------------------------------------------------------------------
# Persistence and the downstream bridge
# ---------------------------------------------------------------------------


class TestPersistence:
    def test_unmeasured_pillar_persists_null_score(self, db):
        _score_pillar(db)
        conn = _tconnect(db)
        row = conn.execute(
            "SELECT score, maturity_level FROM zta_maturity_scores "
            "WHERE project_id = %s AND pillar = %s",
            (PROJECT_ID, PILLAR),
        ).fetchone()
        conn.close()
        assert row["score"] is None
        assert row["maturity_level"] == UNMEASURED

    def test_overall_row_records_which_pillars_were_unmeasured(self, db):
        _score_all(db)
        conn = _tconnect(db)
        row = conn.execute(
            "SELECT evidence FROM zta_maturity_scores "
            "WHERE project_id = %s AND pillar = 'overall'",
            (PROJECT_ID,),
        ).fetchone()
        conn.close()
        payload = json.loads(row["evidence"])
        assert sorted(payload["unmeasured_pillars"]) == sorted(PILLARS)
        assert payload["measured_pillars"] == []


class TestZigBridgeContract:
    """get_latest_score feeds zig_assessor._try_zta_bridge, which indexes
    pillar_scores[key] to get a NUMBER. An unmeasured pillar must be absent
    from that map, not present as a fabricated 0.0."""

    def test_unmeasured_pillars_are_omitted_from_pillar_scores(self, db):
        _score_all(db)
        pdb, pcfg = _patched(db)
        with pdb, pcfg:
            latest = get_latest_score(PROJECT_ID)

        assert latest is not None
        assert latest["pillar_scores"] == {}, (
            "an unmeasured pillar must not hand the ZIG bridge a 0.0"
        )
        assert sorted(latest["unmeasured_pillars"]) == sorted(PILLARS)
        assert latest["overall_score"] is None
        assert latest["overall_maturity"] == UNMEASURED

    def test_measured_pillar_is_present_for_the_bridge(self, db):
        _seed_evidence(db, [(t, "current", '{"proof": 1}') for t in EVIDENCE_TYPES])
        _score_all(db)
        pdb, pcfg = _patched(db)
        with pdb, pcfg:
            latest = get_latest_score(PROJECT_ID)

        assert PILLAR in latest["pillar_scores"]
        assert latest["pillar_scores"][PILLAR] == pytest.approx(1.0, abs=1e-3)


# ---------------------------------------------------------------------------
# The render surface — "rendered separately and labelled"
# ---------------------------------------------------------------------------


def _render_panel(zta):
    """Render the ZTA panel out of the real ZIG assessment template.

    The real template is used, not a fixture copy: a fixture built to satisfy
    the invariant cannot test it. security_canvas/base.html is stubbed only
    because it calls url_for, which needs an app context this test has no
    business standing up.
    """
    from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader

    root = Path(__file__).resolve().parent.parent
    env = Environment(
        loader=ChoiceLoader(
            [
                DictLoader(
                    {
                        "security_canvas/base.html": (
                            "{% block head %}{% endblock %}"
                            "{% block content %}{% endblock %}"
                        )
                    }
                ),
                FileSystemLoader(str(root / "tools" / "dashboard" / "templates")),
            ]
        ),
        autoescape=True,
    )
    html = env.get_template("security_canvas/zig/assessment.html").render(
        latest=None, zta=zta
    )
    start = html.index("DoD 7-Pillar ZTA Posture")
    return html[start : html.index("What Gets Assessed", start)]


_UNMEASURED_PANEL = {
    "state": "unmeasured",
    "evidence_backed_score": None,
    "evidence_backed_maturity": "unmeasured",
    "self_attested_score": 1.0,
    "self_attested_maturity": "optimal",
    "declared_pillars": 7,
    "measured_pillars": [],
    "unmeasured_pillars": ["network"],
    "assessed_at": "2026-09-02T00:00:00Z",
    "pillars": [
        {
            "pillar": "network",
            "label": "Network",
            "score": None,
            "maturity_level": "unmeasured",
            "self_attested_score": 1.0,
            "measured": False,
        }
    ],
}


class TestPanelRendersTwoLabelledNumbers:
    def test_both_numbers_are_labelled(self):
        html = _render_panel(_UNMEASURED_PANEL)
        assert "Evidence-backed" in html
        assert "Self-attested" in html
        assert "(proven)" in html
        assert "(claimed)" in html

    def test_the_two_numbers_are_not_merged(self):
        html = _render_panel(_UNMEASURED_PANEL)
        # The evidence-backed figure is UNMEASURED while the self-attested one
        # is 100%. A single blended number could not show both.
        assert "UNMEASURED" in html
        assert "100.0%" in html

    def test_unmeasured_says_it_is_not_a_clean_bill_of_health(self):
        html = _render_panel(_UNMEASURED_PANEL)
        assert "not a clean bill of health" in html

    def test_unmeasured_pillar_draws_a_rule_not_an_empty_bar(self):
        import re

        html = _render_panel(_UNMEASURED_PANEL)
        # An empty bar is what a MEASURED 0% looks like; the two must differ.
        assert "zta-rule" in html
        # Anchored, not a bare substring: "0.0%" is inside "100.0%", and a
        # substring check would have passed on a panel printing a real zero.
        assert re.search(r"(?<![\d.])0\.0%", html) is None

    def test_never_assessed_state_is_stated_in_words(self):
        html = _render_panel({"state": "never_assessed"})
        assert "Never assessed" in html
        assert "not a clean bill of health" in html
        # No score of any kind is drawn for a deployment nobody has assessed.
        assert "%" not in html

    def test_partial_coverage_names_its_denominator(self):
        html = _render_panel(
            {
                "state": "partial",
                "evidence_backed_score": 0.4,
                "evidence_backed_maturity": "advanced",
                "self_attested_score": 0.9,
                "self_attested_maturity": "optimal",
                "declared_pillars": 7,
                "measured_pillars": ["network"],
                "unmeasured_pillars": ["data"],
                "assessed_at": "2026-09-02T00:00:00Z",
                "pillars": [],
            }
        )
        assert "1 of 7 pillars" in html
        assert "40.0%" in html and "90.0%" in html

    def test_a_broken_panel_still_says_it_is_broken(self):
        # A panel that vanishes on error is indistinguishable from a clean board.
        html = _render_panel({"state": "never_assessed", "error": "store unreachable"})
        assert "ZTA posture unavailable" in html
        assert "not a clean bill of health" in html


class TestLatestPostureSummary:
    """The read-only presenter behind the panel."""

    def test_never_assessed_when_nothing_persisted(self, db):
        from tools.devsecops.zta_maturity_scorer import latest_posture_summary

        pdb, pcfg = _patched(db)
        with pdb, pcfg:
            summary = latest_posture_summary()
        assert summary["state"] == "never_assessed"
        assert summary["evidence_backed_score"] is None
        assert summary["self_attested_score"] is None

    def test_checkbox_rows_report_unmeasured_with_a_self_attested_number(self, db):
        from tools.devsecops.zta_maturity_scorer import latest_posture_summary

        _seed_evidence(db, [(t, "current", None) for t in EVIDENCE_TYPES])
        pdb, pcfg = _patched(db)
        with pdb, pcfg:
            score_all_pillars(PROJECT_ID)
            summary = latest_posture_summary()

        assert summary["state"] == "unmeasured"
        assert summary["evidence_backed_score"] is None
        assert summary["self_attested_score"] is not None
        assert summary["self_attested_score"] > 0

    def test_partial_state_when_some_pillars_measured(self, db):
        from tools.devsecops.zta_maturity_scorer import latest_posture_summary

        _seed_evidence(db, [(t, "current", '{"proof": 1}') for t in EVIDENCE_TYPES])
        pdb, pcfg = _patched(db)
        with pdb, pcfg:
            score_all_pillars(PROJECT_ID)
            summary = latest_posture_summary()

        assert summary["state"] == "partial"
        assert summary["measured_pillars"] == [PILLAR]
        assert summary["evidence_backed_score"] is not None

    def test_presenter_writes_nothing(self, db):
        """A browse surface that scored on render could not check the writer."""
        from tools.devsecops.zta_maturity_scorer import latest_posture_summary

        pdb, pcfg = _patched(db)
        with pdb, pcfg:
            latest_posture_summary()
        conn = _tconnect(db)
        n = conn.execute("SELECT COUNT(*) AS n FROM zta_maturity_scores").fetchone()["n"]
        conn.close()
        assert n == 0


# ---------------------------------------------------------------------------
# The ZIG -> ZTA evidence bridge (rmf-zt-02)
#
# The card said to backfill posture evidence "from the seven ZIG pillar
# orchestrators, which already compute real signals". They DO compute real
# signals — each set_activity_status call carries a note describing what it
# deployed — but MEASURED on the live board 2026-09-02 all 91 completions were
# written by 'seed-script' with NO note, and all 42 capabilities likewise. So
# the source is itself a checkbox list, and a backfill that copied it would
# launder ticks into evidence. These tests pin BOTH halves: a real note crosses,
# a bare tick never does.
# ---------------------------------------------------------------------------

ZIG_SCHEMA = """
CREATE TABLE IF NOT EXISTS zig_capabilities (
    id TEXT PRIMARY KEY,
    pillar_slug TEXT,
    implementation_status TEXT,
    evidence_note TEXT
);
CREATE TABLE IF NOT EXISTS zig_activities (
    id TEXT PRIMARY KEY,
    capability_id TEXT
);
CREATE TABLE IF NOT EXISTS zig_activity_completions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    activity_id TEXT,
    target_id TEXT,
    status TEXT,
    evidence_note TEXT,
    completed_by TEXT,
    completed_at TEXT
);
"""


@pytest.fixture
def zig_db(tmp_path):
    path = tmp_path / "zig.db"
    conn = _tconnect(path)
    conn.executescript(ZIG_SCHEMA)
    conn.execute(
        "INSERT INTO zig_capabilities (id, pillar_slug, implementation_status) "
        "VALUES (%s, %s, %s)",
        ("cap-net-1", "network", "implemented"),
    )
    for aid in ("act-1", "act-2"):
        conn.execute(
            "INSERT INTO zig_activities (id, capability_id) VALUES (%s, %s)",
            (aid, "cap-net-1"),
        )
    conn.commit()
    conn.close()
    return path


def _complete(zig_path, activity_id, note, by="seed-script"):
    conn = _tconnect(zig_path)
    conn.execute(
        "INSERT INTO zig_activity_completions "
        "(activity_id, target_id, status, evidence_note, completed_by, completed_at) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (activity_id, "icdev-self", "complete", note, by, "2026-09-02T00:00:00Z"),
    )
    conn.commit()
    conn.close()


def _patch_bridge(zig_path, zta_path=None):
    from contextlib import ExitStack

    stack = ExitStack()
    stack.enter_context(
        patch(
            "tools.devsecops.zta_zig_backfill._zig_conn",
            lambda: _tconnect(zig_path),
        )
    )
    if zta_path is not None:
        stack.enter_context(
            patch(
                "tools.devsecops.zta_zig_backfill._zta_conn",
                lambda: _tconnect(zta_path),
            )
        )
    return stack


class TestZigBridgeRefusesToLaunderTicks:
    def test_note_less_completions_are_self_attested_not_backfillable(self, zig_db):
        # The live board's exact shape: complete, by seed-script, no note.
        _complete(zig_db, "act-1", None)
        _complete(zig_db, "act-2", "")
        from tools.devsecops.zta_zig_backfill import survey

        with _patch_bridge(zig_db):
            result = survey()

        assert result["state"] == "self_attested_only"
        assert result["backfillable"] == 0
        assert result["self_attested"] == 2
        assert "checkbox" in result["note"]

    def test_backfill_writes_nothing_when_the_source_has_no_evidence(self, zig_db, db):
        _complete(zig_db, "act-1", None)
        _complete(zig_db, "act-2", None)
        from tools.devsecops.zta_zig_backfill import backfill

        with _patch_bridge(zig_db, db):
            result = backfill(PROJECT_ID, write=True)

        assert result["outcome"] == "nothing_to_backfill"
        assert result["written"] == 0
        conn = _tconnect(db)
        n = conn.execute("SELECT COUNT(*) AS n FROM zta_posture_evidence").fetchone()["n"]
        conn.close()
        assert n == 0, "a tick must never become a zta_posture_evidence row"

    def test_a_real_orchestrator_note_does_cross(self, zig_db, db):
        _complete(
            zig_db,
            "act-1",
            "Macro-segmentation deployed. 5 default-deny zones; 12 allow flows.",
            by="network_pillar_orchestrator",
        )
        _complete(zig_db, "act-2", None)  # still a tick, still refused
        from tools.devsecops.zta_zig_backfill import backfill

        with _patch_bridge(zig_db, db):
            result = backfill(PROJECT_ID, write=True)

        assert result["state"] == "backfillable"
        assert result["written"] == 1
        assert result["skipped_self_attested"] == 1

        conn = _tconnect(db)
        rows = conn.execute(
            "SELECT evidence_type, evidence_data, status FROM zta_posture_evidence"
        ).fetchall()
        conn.close()
        assert len(rows) == 1
        assert rows[0]["evidence_type"] == "zig:act-1"
        assert rows[0]["status"] == "current"
        payload = json.loads(rows[0]["evidence_data"])
        assert payload["zta_pillar"] == "network"
        assert "default-deny" in payload["note"]

    def test_backfill_is_idempotent(self, zig_db, db):
        _complete(zig_db, "act-1", "real note", by="network_pillar_orchestrator")
        from tools.devsecops.zta_zig_backfill import backfill

        with _patch_bridge(zig_db, db):
            backfill(PROJECT_ID, write=True)
            backfill(PROJECT_ID, write=True)

        conn = _tconnect(db)
        n = conn.execute("SELECT COUNT(*) AS n FROM zta_posture_evidence").fetchone()["n"]
        conn.close()
        assert n == 1, "re-running must update in place, never duplicate"

    def test_dry_run_writes_nothing(self, zig_db, db):
        _complete(zig_db, "act-1", "real note", by="network_pillar_orchestrator")
        from tools.devsecops.zta_zig_backfill import backfill

        with _patch_bridge(zig_db, db):
            result = backfill(PROJECT_ID, write=False)

        assert result["outcome"] == "dry_run"
        assert result["candidates"] == 1
        assert result["written"] == 0
        conn = _tconnect(db)
        n = conn.execute("SELECT COUNT(*) AS n FROM zta_posture_evidence").fetchone()["n"]
        conn.close()
        assert n == 0

    def test_unreadable_source_is_not_an_empty_one(self):
        from tools.devsecops.zta_zig_backfill import survey

        def _boom():
            raise RuntimeError("zig tables missing")

        with patch("tools.devsecops.zta_zig_backfill._zig_conn", _boom):
            result = survey()
        assert result["state"] == "unreadable"
        assert result["backfillable"] is None, (
            "an unreadable source must not report a confident zero"
        )

    def test_bridge_pillar_map_matches_the_zig_assessor(self):
        # Two halves of one bridge must not disagree about which pillar is which.
        import inspect

        from tools.devsecops.zta_zig_backfill import ZIG_TO_ZTA
        from tools.security_canvas import zig_assessor

        src = inspect.getsource(zig_assessor._try_zta_bridge)
        for zig_slug, zta_key in ZIG_TO_ZTA.items():
            assert f'"{zig_slug}": "{zta_key}"' in src, (
                f"{zig_slug} -> {zta_key} disagrees with zig_assessor._try_zta_bridge"
            )


class TestBackfilledEvidenceIsCountedAsEvidence:
    """A bridged row must actually make a pillar measurable — otherwise the
    bridge is a declared capability nobody consumes."""

    def test_a_bridged_row_counts_as_evidence_backed_for_cato(self, zig_db, db):
        from tools.devsecops.zta_maturity_scorer import has_evidence_data
        from tools.devsecops.zta_zig_backfill import backfill

        _complete(zig_db, "act-1", "real note", by="network_pillar_orchestrator")
        with _patch_bridge(zig_db, db):
            backfill(PROJECT_ID, write=True)

        conn = _tconnect(db)
        row = conn.execute(
            "SELECT status, evidence_data FROM zta_posture_evidence"
        ).fetchone()
        conn.close()
        # This is the exact predicate cato_monitor.check_zta_posture applies.
        assert row["status"] == "current"
        assert has_evidence_data(row["evidence_data"]) is True


# ---------------------------------------------------------------------------
# The fixture must not invent columns
#
# THE DEFECT THIS EXISTS FOR. `_gather_pillar_evidence` queried
# `SELECT control_id, status FROM project_controls`. There is no `status`
# column — init_icdev_db.py declares `implementation_status`, and so does every
# live database. On PostgreSQL that query raised UndefinedColumn on EVERY call,
# so the ZTA scorer had never completed a single assessment and
# zta_maturity_scores held 0 rows. It survived because the test fixture DECLARED
# a `status` column: the suite passed, in full, against a schema that does not
# exist anywhere.
#
# A fixture you write to satisfy the code under test cannot test the code under
# test. These assertions read the CANONICAL DDL and the QUERY, so the fixture is
# no longer the authority on what the schema is.
# ---------------------------------------------------------------------------


def _canonical_ddl(table: str) -> str:
    root = Path(__file__).resolve().parent.parent
    src = (root / "tools" / "db" / "init_icdev_db.py").read_text(encoding="utf-8")
    marker = f"CREATE TABLE IF NOT EXISTS {table} ("
    start = src.index(marker)
    return src[start : src.index(");", start)]


class TestFixtureMatchesTheRealSchema:
    def test_project_controls_has_no_status_column(self):
        ddl = _canonical_ddl("project_controls")
        assert "implementation_status" in ddl
        # The bare column the scorer used to select. Anchored so it cannot match
        # inside "implementation_status".
        import re

        assert re.search(r"^\s*status\s+TEXT", ddl, re.MULTILINE) is None

    def test_scorer_selects_the_column_that_exists(self):
        root = Path(__file__).resolve().parent.parent
        src = (root / "tools" / "devsecops" / "zta_maturity_scorer.py").read_text(
            encoding="utf-8"
        )
        assert "SELECT control_id, implementation_status FROM project_controls" in src
        assert "SELECT control_id, status FROM project_controls" not in src

    def test_fixture_declares_only_real_project_controls_columns(self):
        ddl_cols = set()
        for line in _canonical_ddl("project_controls").splitlines()[1:]:
            line = line.strip()
            if not line or line.startswith(("UNIQUE", "PRIMARY", "FOREIGN", "CHECK", "--")):
                continue
            ddl_cols.add(line.split()[0].strip(","))

        fixture_cols = set()
        block = SCHEMA[SCHEMA.index("CREATE TABLE IF NOT EXISTS project_controls (") :]
        for line in block[: block.index(");")].splitlines()[1:]:
            line = line.strip()
            if not line or line.startswith(("UNIQUE", "PRIMARY", "FOREIGN", "CHECK", "--")):
                continue
            fixture_cols.add(line.split()[0].strip(","))

        invented = fixture_cols - ddl_cols
        assert not invented, (
            f"fixture declares column(s) the real schema does not have: {invented}"
        )

    def test_zta_posture_evidence_fixture_matches_the_real_schema(self):
        ddl = _canonical_ddl("zta_posture_evidence")
        for col in ("evidence_data", "evidence_type", "status", "project_id"):
            assert col in ddl, f"{col} missing from canonical DDL"
        # evidence_data is NULLABLE by design — that nullability IS the defect
        # rmf-zt-02 handles, so a migration making it NOT NULL would change what
        # this card's whole design rests on.
        assert "evidence_data TEXT," in ddl

    def test_unmeasured_is_admitted_by_the_canonical_maturity_check(self):
        ddl = _canonical_ddl("zta_maturity_scores")
        assert "'unmeasured'" in ddl, (
            "score_pillar persists maturity_level='unmeasured'; a fresh database "
            "whose CHECK omits it would reject every unmeasured pillar"
        )


# ---------------------------------------------------------------------------
# The cATO consumer (ADR D123)
#
# check_zta_posture feeds the ZTA score into cATO readiness. It used to coerce a
# NULL score with `row["score"] or 0.0` and publish `cato_contribution` as a
# hard 0.0 — an assessment that "ran and contributed nothing", which is a very
# different claim from "nobody assessed this". These pin the None, and the two
# evidence counts, INSIDE posture_evidence beside the freshness counts they
# qualify: putting them at the top level built a result whose own printer raised
# KeyError, and only running the real CLI found it.
# ---------------------------------------------------------------------------

CATO_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS zta_maturity_scores (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    pillar TEXT NOT NULL,
    score REAL,
    maturity_level TEXT,
    evidence TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS zta_posture_evidence (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    evidence_type TEXT NOT NULL,
    evidence_data TEXT,
    status TEXT,
    collected_at TIMESTAMP,
    expires_at TIMESTAMP
);
"""


@pytest.fixture
def cato_db(tmp_path):
    path = tmp_path / "cato.db"
    conn = _tconnect(path)
    conn.executescript(CATO_SCHEMA)
    conn.execute("INSERT INTO projects (id, name) VALUES (%s, %s)", (PROJECT_ID, "p"))
    conn.commit()
    conn.close()
    return path


def _cato_posture(cato_path):
    from tools.compliance import cato_monitor

    with patch.object(cato_monitor, "_get_connection", lambda *a, **k: _tconnect(cato_path)):
        return cato_monitor.check_zta_posture(PROJECT_ID)


class TestCatoConsumerHonoursUnmeasured:
    def test_unmeasured_overall_contributes_none_not_zero(self, cato_db):
        conn = _tconnect(cato_db)
        conn.execute(
            "INSERT INTO zta_maturity_scores "
            "(id, project_id, pillar, score, maturity_level, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            ("s1", PROJECT_ID, "overall", None, "unmeasured", "2026-09-02T00:00:00Z"),
        )
        conn.commit()
        conn.close()

        result = _cato_posture(cato_db)
        assert result["overall_score"] is None
        assert result["overall_maturity"] == "unmeasured"
        assert result["cato_contribution"] is None, (
            "an unmeasured ZTA posture must not contribute a measured 0.0 to cATO"
        )

    def test_measured_zero_still_contributes_a_measured_zero(self, cato_db):
        conn = _tconnect(cato_db)
        conn.execute(
            "INSERT INTO zta_maturity_scores "
            "(id, project_id, pillar, score, maturity_level, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            ("s1", PROJECT_ID, "overall", 0.0, "traditional", "2026-09-02T00:00:00Z"),
        )
        conn.commit()
        conn.close()

        result = _cato_posture(cato_db)
        assert result["overall_score"] == 0.0
        assert result["cato_contribution"] == 0.0, "a measured zero is a real finding"

    def test_evidence_counts_live_inside_posture_evidence(self, cato_db):
        conn = _tconnect(cato_db)
        for i, (status, data) in enumerate(
            [("current", '{"proof": 1}'), ("current", None), ("stale", '{"x": 1}')]
        ):
            conn.execute(
                "INSERT INTO zta_posture_evidence "
                "(id, project_id, evidence_type, evidence_data, status) "
                "VALUES (%s, %s, %s, %s, %s)",
                (f"e{i}", PROJECT_ID, f"t{i}", data, status),
            )
        conn.commit()
        conn.close()

        pe = _cato_posture(cato_db)["posture_evidence"]
        # The shape the module's own printer indexes. A KeyError here is a
        # crash on the CLI path, which is exactly how this was found.
        assert pe["evidence_backed"] == 1
        assert pe["self_attested"] == 1
        assert pe["current"] == 2
        assert pe["stale"] == 1
        assert pe["total"] == 3

    def test_the_printer_does_not_raise(self, cato_db, capsys):
        # check_zta_posture prints its own summary; a missing key there took the
        # whole CLI down while every unit test passed.
        _cato_posture(cato_db)
        out = capsys.readouterr().out
        assert "evidence-backed" in out
        assert "self-attested" in out
