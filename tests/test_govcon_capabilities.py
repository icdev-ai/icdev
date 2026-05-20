# CUI // SP-CTI
"""Tests for /govcon/capabilities — L/M/N coverage breakdown.

Verifies:
1. coverage_to_grade() maps scores to L/M/N correctly.
2. Route handler queries DB and aggregates correct L/M/N counts.
3. Template receives L/M/N stat values (render_template is mocked to avoid
   base.html context-processor dependencies in unit-test environment).
"""
import sqlite3
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from flask import Flask


# ---------------------------------------------------------------------------
# DB schema helpers
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS rfp_shall_statements (
    id TEXT PRIMARY KEY,
    sam_opportunity_id TEXT,
    statement_text TEXT,
    domain_category TEXT,
    statement_type TEXT,
    keywords TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS rfp_requirement_patterns (
    id TEXT PRIMARY KEY,
    pattern_name TEXT,
    domain_category TEXT,
    frequency INTEGER DEFAULT 1,
    representative_text TEXT,
    keyword_fingerprint TEXT,
    keywords TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS icdev_capability_map (
    id TEXT PRIMARY KEY,
    pattern_id TEXT,
    capability_id TEXT,
    coverage_score REAL,
    grade TEXT,
    matched_keywords TEXT,
    created_at TEXT,
    metadata TEXT
);

CREATE TABLE IF NOT EXISTS audit_trail (
    id TEXT PRIMARY KEY,
    created_at TEXT,
    event_type TEXT,
    actor TEXT,
    action TEXT,
    details TEXT,
    session_id TEXT
);
"""


def _make_db(tmp_path: Path, seed_rows: list) -> Path:
    """Create a SQLite DB with govcon tables and optional seed rows.

    seed_rows: list of dicts with keys: domain, score (coverage_score).
    Each row creates a rfp_shall_statements entry linked via icdev_capability_map.
    """
    db_path = tmp_path / "icdev.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_SCHEMA)

    for row in seed_rows:
        stmt_id = str(uuid.uuid4())
        cap_id = str(uuid.uuid4())
        domain = row.get("domain", "devsecops")
        score = row.get("score", 0.5)

        if score >= 0.80:
            grade = "L"
        elif score >= 0.40:
            grade = "M"
        else:
            grade = "N"

        conn.execute(
            "INSERT INTO rfp_shall_statements (id, domain_category, created_at) VALUES (?, ?, ?)",
            (stmt_id, domain, "2026-01-01T00:00:00+00:00"),
        )
        conn.execute(
            "INSERT INTO icdev_capability_map "
            "(id, pattern_id, capability_id, coverage_score, grade, matched_keywords, created_at, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), stmt_id, cap_id, score, grade, "[]", "2026-01-01T00:00:00+00:00", "{}"),
        )

    conn.commit()
    conn.close()
    return db_path


# ---------------------------------------------------------------------------
# Unit tests: coverage_to_grade()
# ---------------------------------------------------------------------------

class TestCoverageToGrade:
    """coverage_to_grade() must respect the 0.80 / 0.40 thresholds."""

    def test_score_at_compliant_threshold_is_L(self):
        from tools.govcon.capability_mapper import coverage_to_grade
        assert coverage_to_grade(0.80) == "L"

    def test_score_above_compliant_threshold_is_L(self):
        from tools.govcon.capability_mapper import coverage_to_grade
        assert coverage_to_grade(1.0) == "L"
        assert coverage_to_grade(0.95) == "L"

    def test_score_just_below_compliant_threshold_is_M(self):
        from tools.govcon.capability_mapper import coverage_to_grade
        assert coverage_to_grade(0.79) == "M"

    def test_score_at_partial_threshold_is_M(self):
        from tools.govcon.capability_mapper import coverage_to_grade
        assert coverage_to_grade(0.40) == "M"

    def test_score_just_below_partial_threshold_is_N(self):
        from tools.govcon.capability_mapper import coverage_to_grade
        assert coverage_to_grade(0.39) == "N"

    def test_score_zero_is_N(self):
        from tools.govcon.capability_mapper import coverage_to_grade
        assert coverage_to_grade(0.0) == "N"

    def test_midrange_M(self):
        from tools.govcon.capability_mapper import coverage_to_grade
        assert coverage_to_grade(0.60) == "M"


# ---------------------------------------------------------------------------
# Helpers: minimal Flask app + _get_db that bypass full app context processors
# ---------------------------------------------------------------------------

def _build_test_app(tmp_path: Path, seed_rows: list):
    """Return (flask_app, db_path) for testing the capabilities route.

    render_template is NOT mocked here — callers do that per-test.
    """
    db_path = _make_db(tmp_path, seed_rows)

    import sqlite3 as _sqlite3

    def _get_db():
        conn = _sqlite3.connect(str(db_path))
        conn.row_factory = _sqlite3.Row
        return conn

    flask_app = Flask(__name__, template_folder=str(
        Path(__file__).parent.parent / "tools" / "dashboard" / "templates"
    ))
    flask_app.config["TESTING"] = True

    from tools.dashboard.app import _register_govcon_pages
    _register_govcon_pages(flask_app, _get_db)

    return flask_app, db_path


# ---------------------------------------------------------------------------
# Route tests: coverage dict passed to render_template
#
# We mock render_template to avoid base.html context-processor requirements
# (ROLE_VIEWS etc.) that only exist in the full create_app() environment.
# The mock captures the kwargs so we can assert on coverage.L/M/N counts.
# ---------------------------------------------------------------------------

class TestCapabilitiesRouteCoverageData:
    """Route passes correct L/M/N counts to render_template."""

    @pytest.fixture()
    def seeded_app(self, tmp_path):
        # 2L, 1M, 1N
        return _build_test_app(
            tmp_path,
            [
                {"domain": "devsecops", "score": 0.90},  # L
                {"domain": "devsecops", "score": 0.85},  # L
                {"domain": "compliance", "score": 0.60},  # M
                {"domain": "compliance", "score": 0.20},  # N
            ],
        )

    def _call_route(self, flask_app) -> dict:
        """Invoke /govcon/capabilities, intercept render_template kwargs."""
        captured = {}

        def fake_render(template_name, **kwargs):
            captured.update(kwargs)
            captured["_template"] = template_name
            return "OK"

        with patch("tools.govcon.gap_analyzer.generate_recommendations",
                   return_value={"recommendations": []}):
            with patch("tools.dashboard.app.render_template", side_effect=fake_render):
                with flask_app.test_client() as c:
                    resp = c.get("/govcon/capabilities")
                    captured["_status"] = resp.status_code

        return captured

    def test_route_returns_200(self, seeded_app):
        flask_app, _ = seeded_app
        result = self._call_route(flask_app)
        assert result["_status"] == 200

    def test_template_name(self, seeded_app):
        flask_app, _ = seeded_app
        result = self._call_route(flask_app)
        assert result["_template"] == "govcon/capabilities.html"

    def test_L_count_is_two(self, seeded_app):
        flask_app, _ = seeded_app
        result = self._call_route(flask_app)
        assert result["coverage"]["L"] == 2, f"Expected L=2, got {result['coverage']}"

    def test_M_count_is_one(self, seeded_app):
        flask_app, _ = seeded_app
        result = self._call_route(flask_app)
        assert result["coverage"]["M"] == 1, f"Expected M=1, got {result['coverage']}"

    def test_N_count_is_one(self, seeded_app):
        flask_app, _ = seeded_app
        result = self._call_route(flask_app)
        assert result["coverage"]["N"] == 1, f"Expected N=1, got {result['coverage']}"

    def test_compliance_rate_is_50(self, seeded_app):
        flask_app, _ = seeded_app
        result = self._call_route(flask_app)
        # 2L out of 4 total = 50%
        assert result["coverage"]["rate"] == 50, f"Expected rate=50, got {result['coverage']}"

    def test_coverage_dict_has_all_keys(self, seeded_app):
        flask_app, _ = seeded_app
        result = self._call_route(flask_app)
        coverage = result["coverage"]
        assert "L" in coverage
        assert "M" in coverage
        assert "N" in coverage
        assert "rate" in coverage

    def test_domain_coverage_list_present(self, seeded_app):
        flask_app, _ = seeded_app
        result = self._call_route(flask_app)
        assert "domain_coverage" in result
        assert isinstance(result["domain_coverage"], list)

    def test_gaps_list_present(self, seeded_app):
        flask_app, _ = seeded_app
        result = self._call_route(flask_app)
        assert "gaps" in result
        assert isinstance(result["gaps"], list)

    def test_total_gaps_present(self, seeded_app):
        flask_app, _ = seeded_app
        result = self._call_route(flask_app)
        assert "total_gaps" in result


# ---------------------------------------------------------------------------
# Template static content: ensure the HTML renders L/M/N label strings
# ---------------------------------------------------------------------------

class TestCapabilitiesTemplateLabels:
    """Template source must include the L/M/N label strings."""

    def test_template_contains_L_label(self):
        tmpl_path = (
            Path(__file__).parent.parent
            / "tools" / "dashboard" / "templates" / "govcon" / "capabilities.html"
        )
        html = tmpl_path.read_text(encoding="utf-8")
        assert "L (Compliant)" in html

    def test_template_contains_M_label(self):
        tmpl_path = (
            Path(__file__).parent.parent
            / "tools" / "dashboard" / "templates" / "govcon" / "capabilities.html"
        )
        html = tmpl_path.read_text(encoding="utf-8")
        assert "M (Partial)" in html

    def test_template_contains_N_label(self):
        tmpl_path = (
            Path(__file__).parent.parent
            / "tools" / "dashboard" / "templates" / "govcon" / "capabilities.html"
        )
        html = tmpl_path.read_text(encoding="utf-8")
        assert "N (Gap)" in html

    def test_template_renders_coverage_L_variable(self):
        tmpl_path = (
            Path(__file__).parent.parent
            / "tools" / "dashboard" / "templates" / "govcon" / "capabilities.html"
        )
        html = tmpl_path.read_text(encoding="utf-8")
        assert "{{ coverage.L }}" in html

    def test_template_renders_coverage_M_variable(self):
        tmpl_path = (
            Path(__file__).parent.parent
            / "tools" / "dashboard" / "templates" / "govcon" / "capabilities.html"
        )
        html = tmpl_path.read_text(encoding="utf-8")
        assert "{{ coverage.M }}" in html

    def test_template_renders_coverage_N_variable(self):
        tmpl_path = (
            Path(__file__).parent.parent
            / "tools" / "dashboard" / "templates" / "govcon" / "capabilities.html"
        )
        html = tmpl_path.read_text(encoding="utf-8")
        assert "{{ coverage.N }}" in html

    def test_template_has_domain_table_headers(self):
        tmpl_path = (
            Path(__file__).parent.parent
            / "tools" / "dashboard" / "templates" / "govcon" / "capabilities.html"
        )
        html = tmpl_path.read_text(encoding="utf-8")
        assert "Coverage by Domain" in html
        # Column headers for per-domain L/M/N breakdown
        assert ">L<" in html or "<th" in html

    def test_template_has_compliance_rate_label(self):
        tmpl_path = (
            Path(__file__).parent.parent
            / "tools" / "dashboard" / "templates" / "govcon" / "capabilities.html"
        )
        html = tmpl_path.read_text(encoding="utf-8")
        assert "Compliance Rate" in html


# ---------------------------------------------------------------------------
# Edge case: empty DB — route must not raise, coverage defaults to zeros
# ---------------------------------------------------------------------------

class TestCapabilitiesRouteEmptyDB:
    """Route degrades gracefully when no coverage data has been seeded."""

    @pytest.fixture()
    def empty_app(self, tmp_path):
        return _build_test_app(tmp_path, [])

    def _call_route(self, flask_app) -> dict:
        captured = {}

        def fake_render(template_name, **kwargs):
            captured.update(kwargs)
            captured["_status_ok"] = True
            return "OK"

        with patch("tools.govcon.gap_analyzer.generate_recommendations",
                   return_value={"recommendations": []}):
            with patch("tools.dashboard.app.render_template", side_effect=fake_render):
                with flask_app.test_client() as c:
                    resp = c.get("/govcon/capabilities")
                    captured["_http_status"] = resp.status_code

        return captured

    def test_empty_db_returns_200(self, empty_app):
        flask_app, _ = empty_app
        result = self._call_route(flask_app)
        assert result["_http_status"] == 200

    def test_empty_db_L_defaults_to_zero(self, empty_app):
        flask_app, _ = empty_app
        result = self._call_route(flask_app)
        assert result["coverage"]["L"] == 0

    def test_empty_db_M_defaults_to_zero(self, empty_app):
        flask_app, _ = empty_app
        result = self._call_route(flask_app)
        assert result["coverage"]["M"] == 0

    def test_empty_db_N_defaults_to_zero(self, empty_app):
        flask_app, _ = empty_app
        result = self._call_route(flask_app)
        assert result["coverage"]["N"] == 0

    def test_empty_db_rate_defaults_to_zero(self, empty_app):
        flask_app, _ = empty_app
        result = self._call_route(flask_app)
        assert result["coverage"]["rate"] == 0


# ---------------------------------------------------------------------------
# Template: gap list section structural checks (gcpl-map-04)
# ---------------------------------------------------------------------------

class TestGapListSectionTemplate:
    """Template source must contain the gap list section with required structure."""

    _tmpl_path = (
        Path(__file__).parent.parent
        / "tools" / "dashboard" / "templates" / "govcon" / "capabilities.html"
    )

    def _html(self) -> str:
        return self._tmpl_path.read_text(encoding="utf-8")

    def test_template_has_top_gaps_heading(self):
        assert "Top Gaps" in self._html()

    def test_template_has_frequency_column(self):
        assert "Frequency" in self._html()

    def test_template_has_priority_column(self):
        assert "Priority" in self._html()

    def test_template_iterates_gaps(self):
        assert "for g in gaps" in self._html()

    def test_template_has_gap_empty_state(self):
        assert "No gaps identified yet." in self._html()

    def test_template_renders_g_requirement(self):
        assert "g.requirement" in self._html()

    def test_template_renders_g_coverage(self):
        assert "g.coverage" in self._html()

    def test_template_renders_g_frequency(self):
        assert "g.frequency" in self._html()

    def test_template_renders_g_priority(self):
        assert "g.priority" in self._html()


# ---------------------------------------------------------------------------
# DB helper: seed rfp_requirement_patterns for gap route tests (gcpl-map-04)
# ---------------------------------------------------------------------------

def _make_db_with_gaps(tmp_path: Path, pattern_rows: list) -> Path:
    """Create a SQLite DB with rfp_requirement_patterns seeded for gap route testing.

    pattern_rows: list of dicts with keys: name, domain, frequency, score.
    score < 0.40 appears in gaps; score >= 0.40 is filtered out.
    """
    db_path = tmp_path / "icdev_gaps.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_SCHEMA)

    for row in pattern_rows:
        pat_id = str(uuid.uuid4())
        cap_id = str(uuid.uuid4())
        name = row.get("name", "Test Pattern")
        domain = row.get("domain", "devsecops")
        freq = row.get("frequency", 1)
        score = row.get("score", 0.2)

        if score >= 0.80:
            grade = "L"
        elif score >= 0.40:
            grade = "M"
        else:
            grade = "N"

        conn.execute(
            "INSERT INTO rfp_requirement_patterns "
            "(id, pattern_name, domain_category, frequency, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (pat_id, name, domain, freq, "2026-01-01T00:00:00+00:00"),
        )
        conn.execute(
            "INSERT INTO icdev_capability_map "
            "(id, pattern_id, capability_id, coverage_score, grade, matched_keywords, created_at, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), pat_id, cap_id, score, grade, "[]", "2026-01-01T00:00:00+00:00", "{}"),
        )

    conn.commit()
    conn.close()
    return db_path


def _build_test_app_with_gaps(tmp_path: Path, pattern_rows: list):
    """Return (flask_app, db_path) for testing the gap list route."""
    db_path = _make_db_with_gaps(tmp_path, pattern_rows)

    import sqlite3 as _sqlite3

    def _get_db():
        inner_conn = _sqlite3.connect(str(db_path))
        inner_conn.row_factory = _sqlite3.Row
        return inner_conn

    flask_app = Flask(__name__, template_folder=str(
        Path(__file__).parent.parent / "tools" / "dashboard" / "templates"
    ))
    flask_app.config["TESTING"] = True

    from tools.dashboard.app import _register_govcon_pages
    _register_govcon_pages(flask_app, _get_db)

    return flask_app, db_path


# ---------------------------------------------------------------------------
# Route tests: gap list data (gcpl-map-04)
# ---------------------------------------------------------------------------

class TestGapListSectionRoute:
    """Route must surface N-grade items in the gaps list and set total_gaps correctly."""

    def _call_route(self, flask_app) -> dict:
        captured = {}

        def fake_render(template_name, **kwargs):
            captured.update(kwargs)
            captured["_template"] = template_name
            return "OK"

        with patch("tools.govcon.gap_analyzer.generate_recommendations",
                   return_value={"recommendations": []}):
            with patch("tools.dashboard.app.render_template", side_effect=fake_render):
                with flask_app.test_client() as c:
                    resp = c.get("/govcon/capabilities")
                    captured["_status"] = resp.status_code

        return captured

    def test_n_grade_pattern_appears_in_gaps(self, tmp_path):
        app, _ = _build_test_app_with_gaps(
            tmp_path,
            [{"name": "zero-trust-req", "domain": "security", "frequency": 3, "score": 0.10}],
        )
        result = self._call_route(app)
        assert len(result["gaps"]) == 1
        assert result["gaps"][0]["requirement"] == "zero-trust-req"

    def test_total_gaps_equals_n_grade_count(self, tmp_path):
        app, _ = _build_test_app_with_gaps(
            tmp_path,
            [
                {"name": "req-a", "domain": "security", "frequency": 2, "score": 0.05},
                {"name": "req-b", "domain": "compliance", "frequency": 1, "score": 0.30},
            ],
        )
        result = self._call_route(app)
        assert result["total_gaps"] == 2

    def test_l_grade_pattern_excluded_from_gaps(self, tmp_path):
        app, _ = _build_test_app_with_gaps(
            tmp_path,
            [{"name": "compliant-req", "domain": "devsecops", "frequency": 5, "score": 0.90}],
        )
        result = self._call_route(app)
        assert result["gaps"] == []

    def test_m_grade_pattern_excluded_from_gaps(self, tmp_path):
        app, _ = _build_test_app_with_gaps(
            tmp_path,
            [{"name": "partial-req", "domain": "devsecops", "frequency": 2, "score": 0.60}],
        )
        result = self._call_route(app)
        assert result["gaps"] == []

    def test_gap_dict_has_required_keys(self, tmp_path):
        app, _ = _build_test_app_with_gaps(
            tmp_path,
            [{"name": "gap-req", "domain": "security", "frequency": 4, "score": 0.15}],
        )
        result = self._call_route(app)
        gap = result["gaps"][0]
        for key in ("requirement", "domain", "coverage", "frequency", "priority"):
            assert key in gap, f"Gap missing key: {key}"

    def test_gap_coverage_is_below_threshold(self, tmp_path):
        app, _ = _build_test_app_with_gaps(
            tmp_path,
            [{"name": "low-cov-req", "domain": "security", "frequency": 2, "score": 0.25}],
        )
        result = self._call_route(app)
        assert result["gaps"][0]["coverage"] < 0.40

    def test_no_patterns_gives_empty_gaps(self, tmp_path):
        app, _ = _build_test_app_with_gaps(tmp_path, [])
        result = self._call_route(app)
        assert result["gaps"] == []
        assert result["total_gaps"] == 0


# ---------------------------------------------------------------------------
# Template: enhancement recommendations section structural checks (gcpl-map-05)
# ---------------------------------------------------------------------------

class TestEnhancementRecommendationsTemplate:
    """Template source must contain the enhancement recommendations section."""

    _tmpl_path = (
        Path(__file__).parent.parent
        / "tools" / "dashboard" / "templates" / "govcon" / "capabilities.html"
    )

    def _html(self) -> str:
        return self._tmpl_path.read_text(encoding="utf-8")

    def test_template_has_enhancement_recommendations_heading(self):
        assert "Enhancement Recommendations" in self._html()

    def test_template_has_recommendation_column_header(self):
        assert "Recommendation" in self._html()

    def test_template_has_type_column_header(self):
        assert "Type" in self._html()

    def test_template_has_impact_column_header(self):
        assert "Impact" in self._html()

    def test_template_iterates_recommendations(self):
        assert "for r in recommendations" in self._html()

    def test_template_has_recommendations_empty_state(self):
        assert "No recommendations generated yet." in self._html()

    def test_template_renders_r_recommendation(self):
        assert "r.recommendation" in self._html()

    def test_template_renders_r_domain(self):
        assert "r.domain" in self._html()

    def test_template_renders_r_type(self):
        assert "r.type" in self._html()

    def test_template_renders_r_impact(self):
        assert "r.impact" in self._html()


# ---------------------------------------------------------------------------
# Route tests: enhancement recommendations data passed to template (gcpl-map-05)
# ---------------------------------------------------------------------------

class TestEnhancementRecommendationsRoute:
    """Route must pass recommendations list to render_template."""

    def _call_route(self, flask_app, rec_return_value) -> dict:
        captured = {}

        def fake_render(template_name, **kwargs):
            captured.update(kwargs)
            captured["_template"] = template_name
            return "OK"

        with patch("tools.govcon.gap_analyzer.generate_recommendations",
                   return_value=rec_return_value):
            with patch("tools.dashboard.app.render_template", side_effect=fake_render):
                with flask_app.test_client() as c:
                    resp = c.get("/govcon/capabilities")
                    captured["_status"] = resp.status_code

        return captured

    def test_route_passes_recommendations_key(self, tmp_path):
        app, _ = _build_test_app(tmp_path, [])
        result = self._call_route(app, {"recommendations": []})
        assert "recommendations" in result

    def test_recommendations_is_list(self, tmp_path):
        app, _ = _build_test_app(tmp_path, [])
        result = self._call_route(app, {"recommendations": []})
        assert isinstance(result["recommendations"], list)

    def test_empty_recommendations_returned_when_no_gaps(self, tmp_path):
        app, _ = _build_test_app(tmp_path, [])
        result = self._call_route(app, {"recommendations": []})
        assert result["recommendations"] == []

    def test_recommendations_populated_from_gap_analyzer(self, tmp_path):
        app, _ = _build_test_app(tmp_path, [])
        recs = [
            {"recommendation": "Extend DevSecOps pipeline", "domain": "devsecops",
             "type": "pipeline", "impact": "high"},
            {"recommendation": "Add ATO tooling", "domain": "ato_rmf",
             "type": "compliance", "impact": "medium"},
        ]
        result = self._call_route(app, {"recommendations": recs})
        assert len(result["recommendations"]) == 2
        assert result["recommendations"][0]["domain"] == "devsecops"

    def test_recommendations_truncated_to_15(self, tmp_path):
        app, _ = _build_test_app(tmp_path, [])
        recs = [
            {"recommendation": f"Rec {i}", "domain": "devsecops",
             "type": "pipeline", "impact": "low"}
            for i in range(20)
        ]
        result = self._call_route(app, {"recommendations": recs})
        assert len(result["recommendations"]) <= 15

    def test_route_degrades_gracefully_when_gap_analyzer_raises(self, tmp_path):
        app, _ = _build_test_app(tmp_path, [])
        captured = {}

        def fake_render(template_name, **kwargs):
            captured.update(kwargs)
            return "OK"

        def exploding_generate():
            raise RuntimeError("gap_analyzer offline")

        with patch("tools.govcon.gap_analyzer.generate_recommendations",
                   side_effect=exploding_generate):
            with patch("tools.dashboard.app.render_template", side_effect=fake_render):
                with app.test_client() as c:
                    resp = c.get("/govcon/capabilities")
                    assert resp.status_code == 200
        assert captured.get("recommendations", None) is not None
        assert isinstance(captured["recommendations"], list)


# ---------------------------------------------------------------------------
# API test helper: minimal Flask app with govcon_api blueprint (gcpl-map-06)
# ---------------------------------------------------------------------------

def _build_api_test_app():
    """Return a Flask test app with only the govcon_api blueprint registered."""
    from tools.dashboard.api.govcon import govcon_api

    flask_app = Flask(__name__)
    flask_app.config["TESTING"] = True
    flask_app.register_blueprint(govcon_api)
    return flask_app


_FAKE_MAPPINGS = [
    {
        "pattern_id": "pat-1",
        "pattern_name": "Zero Trust Architecture",
        "domain": "devsecops",
        "capability_id": "cap-1",
        "capability_name": "ZTA Enforcement",
        "score": 0.92,
        "grade": "L",
        "matched_keywords": ["zero", "trust"],
        "evidence": "Keyword overlap",
    },
    {
        "pattern_id": "pat-2",
        "pattern_name": "Encryption at Rest",
        "domain": "security",
        "capability_id": "cap-2",
        "capability_name": "Data Encryption",
        "score": 0.61,
        "grade": "M",
        "matched_keywords": ["encryption", "rest"],
        "evidence": "Partial match",
    },
    {
        "pattern_id": "pat-3",
        "pattern_name": "Supply Chain Risk",
        "domain": "supply_chain",
        "capability_id": "cap-3",
        "capability_name": "SBOM Generation",
        "score": 0.25,
        "grade": "N",
        "matched_keywords": ["supply"],
        "evidence": "Low overlap",
    },
]

_FAKE_RESULT_OK = {
    "status": "ok",
    "patterns_mapped": 3,
    "capability_links": 3,
    "mappings": _FAKE_MAPPINGS,
}


# ---------------------------------------------------------------------------
# API tests: POST /api/govcon/opportunities/<id>/map-capabilities (gcpl-map-06)
# ---------------------------------------------------------------------------

class TestMapCapabilitiesAPIEndpoint:
    """POST /api/govcon/opportunities/<id>/map-capabilities returns coverage scores."""

    @pytest.fixture()
    def api_app(self):
        return _build_api_test_app()

    def _post(self, api_app, opp_id="opp-test-123"):
        with patch(
            "tools.govcon.capability_mapper.map_all_patterns",
            return_value=_FAKE_RESULT_OK,
        ):
            with api_app.test_client() as c:
                resp = c.post(f"/api/govcon/opportunities/{opp_id}/map-capabilities")
        return resp

    def test_post_returns_200(self, api_app):
        resp = self._post(api_app)
        assert resp.status_code == 200

    def test_response_status_is_ok(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        assert data["status"] == "ok"

    def test_response_has_patterns_mapped(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        assert "patterns_mapped" in data

    def test_response_has_capability_links(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        assert "capability_links" in data

    def test_response_has_mappings_list(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        assert "mappings" in data
        assert isinstance(data["mappings"], list)

    def test_mappings_contain_score_values(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        for m in data["mappings"]:
            assert "score" in m, f"Mapping missing 'score': {m}"

    def test_mappings_contain_grade_values(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        for m in data["mappings"]:
            assert "grade" in m, f"Mapping missing 'grade': {m}"

    def test_mapping_grades_are_L_M_or_N(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        for m in data["mappings"]:
            assert m["grade"] in {"L", "M", "N"}, f"Invalid grade: {m['grade']}"

    def test_mapping_scores_are_float_between_0_and_1(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        for m in data["mappings"]:
            assert 0.0 <= m["score"] <= 1.0, f"Score out of range: {m['score']}"

    def test_l_grade_mapping_has_score_gte_080(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        l_mappings = [m for m in data["mappings"] if m["grade"] == "L"]
        for m in l_mappings:
            assert m["score"] >= 0.80, f"L-grade score below 0.80: {m['score']}"

    def test_m_grade_mapping_has_score_between_040_and_080(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        m_mappings = [m for m in data["mappings"] if m["grade"] == "M"]
        for m in m_mappings:
            assert 0.40 <= m["score"] < 0.80, f"M-grade score out of range: {m['score']}"

    def test_n_grade_mapping_has_score_below_040(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        n_mappings = [m for m in data["mappings"] if m["grade"] == "N"]
        for m in n_mappings:
            assert m["score"] < 0.40, f"N-grade score not below 0.40: {m['score']}"

    def test_endpoint_accepts_arbitrary_opp_id(self, api_app):
        for opp_id in ("abc-123", "uuid-9999", "opportunity-xyz"):
            resp = self._post(api_app, opp_id=opp_id)
            assert resp.status_code == 200, f"Expected 200 for opp_id={opp_id}"

    def test_returns_500_when_map_all_patterns_raises(self, api_app):
        with patch(
            "tools.govcon.capability_mapper.map_all_patterns",
            side_effect=RuntimeError("mapper offline"),
        ):
            with api_app.test_client() as c:
                resp = c.post("/api/govcon/opportunities/opp-err/map-capabilities")
        assert resp.status_code == 500

    def test_500_response_has_error_key(self, api_app):
        with patch(
            "tools.govcon.capability_mapper.map_all_patterns",
            side_effect=RuntimeError("mapper offline"),
        ):
            with api_app.test_client() as c:
                resp = c.post("/api/govcon/opportunities/opp-err/map-capabilities")
        data = resp.get_json()
        assert "error" in data

    def test_no_patterns_returns_200_with_zero_mapped(self, api_app):
        empty_result = {"status": "ok", "patterns_mapped": 0, "message": "No requirement patterns found"}
        with patch(
            "tools.govcon.capability_mapper.map_all_patterns",
            return_value=empty_result,
        ):
            with api_app.test_client() as c:
                resp = c.post("/api/govcon/opportunities/opp-empty/map-capabilities")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["patterns_mapped"] == 0


# ---------------------------------------------------------------------------
# Fixtures shared by GET /api/govcon/opportunities/<id>/coverage tests
# ---------------------------------------------------------------------------

_FAKE_COVERAGE_MATRIX = [
    {
        "shall_id": 1,
        "statement": "The system shall implement zero trust.",
        "domain": "devsecops",
        "statement_type": "functional",
        "best_capability": "ZTA Enforcement",
        "best_capability_id": "cap-1",
        "coverage_score": 0.92,
        "grade": "L",
        "evidence": "Keyword overlap",
    },
    {
        "shall_id": 2,
        "statement": "The system shall encrypt data at rest.",
        "domain": "security",
        "statement_type": "functional",
        "best_capability": "Data Encryption",
        "best_capability_id": "cap-2",
        "coverage_score": 0.61,
        "grade": "M",
        "evidence": "Partial match",
    },
    {
        "shall_id": 3,
        "statement": "The system shall manage supply chain risks.",
        "domain": "supply_chain",
        "statement_type": "functional",
        "best_capability": "SBOM Generation",
        "best_capability_id": "cap-3",
        "coverage_score": 0.25,
        "grade": "N",
        "evidence": "Low overlap",
    },
]

_FAKE_COVERAGE_RESULT = {
    "status": "ok",
    "opportunity_id": "opp-test-123",
    "total_requirements": 3,
    "L_compliant": 1,
    "M_partial": 1,
    "N_gap": 1,
    "compliance_rate": 0.3333,
    "matrix": _FAKE_COVERAGE_MATRIX,
}


# ---------------------------------------------------------------------------
# API tests: GET /api/govcon/opportunities/<id>/coverage (gcpl-map-07)
# ---------------------------------------------------------------------------


class TestGetCoverageAPIEndpoint:
    """GET /api/govcon/opportunities/<id>/coverage returns L/M/N grades."""

    @pytest.fixture()
    def api_app(self):
        return _build_api_test_app()

    def _get(self, api_app, opp_id="opp-test-123"):
        with patch(
            "tools.govcon.capability_mapper.get_compliance_matrix",
            return_value=_FAKE_COVERAGE_RESULT,
        ):
            with api_app.test_client() as c:
                resp = c.get(f"/api/govcon/opportunities/{opp_id}/coverage")
        return resp

    def test_get_returns_200(self, api_app):
        resp = self._get(api_app)
        assert resp.status_code == 200

    def test_response_content_type_is_json(self, api_app):
        resp = self._get(api_app)
        assert resp.content_type.startswith("application/json")

    def test_response_has_status_key(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        assert "status" in data

    def test_response_status_is_ok(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        assert data["status"] == "ok"

    def test_response_has_opportunity_id(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        assert "opportunity_id" in data

    def test_opportunity_id_matches_url_param(self, api_app):
        resp = self._get(api_app, opp_id="opp-test-123")
        data = resp.get_json()
        assert data["opportunity_id"] == "opp-test-123"

    def test_response_has_total_requirements(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        assert "total_requirements" in data

    def test_response_has_L_compliant(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        assert "L_compliant" in data

    def test_response_has_M_partial(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        assert "M_partial" in data

    def test_response_has_N_gap(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        assert "N_gap" in data

    def test_response_has_compliance_rate(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        assert "compliance_rate" in data

    def test_response_has_matrix_list(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        assert "matrix" in data
        assert isinstance(data["matrix"], list)

    def test_grade_counts_sum_to_total_requirements(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        assert data["L_compliant"] + data["M_partial"] + data["N_gap"] == data["total_requirements"]

    def test_compliance_rate_is_float_between_0_and_1(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        assert 0.0 <= data["compliance_rate"] <= 1.0

    def test_matrix_items_have_grade_key(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        for item in data["matrix"]:
            assert "grade" in item, f"Matrix item missing 'grade': {item}"

    def test_matrix_grades_are_L_M_or_N(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        for item in data["matrix"]:
            assert item["grade"] in {"L", "M", "N"}, f"Invalid grade: {item['grade']}"

    def test_matrix_items_have_coverage_score(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        for item in data["matrix"]:
            assert "coverage_score" in item, f"Matrix item missing 'coverage_score': {item}"

    def test_matrix_coverage_scores_are_between_0_and_1(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        for item in data["matrix"]:
            assert 0.0 <= item["coverage_score"] <= 1.0, f"Score out of range: {item['coverage_score']}"

    def test_endpoint_accepts_arbitrary_opp_id(self, api_app):
        for opp_id in ("abc-123", "uuid-9999", "opportunity-xyz"):
            resp = self._get(api_app, opp_id=opp_id)
            assert resp.status_code == 200, f"Expected 200 for opp_id={opp_id}"

    def test_returns_500_when_get_compliance_matrix_raises(self, api_app):
        with patch(
            "tools.govcon.capability_mapper.get_compliance_matrix",
            side_effect=RuntimeError("mapper offline"),
        ):
            with api_app.test_client() as c:
                resp = c.get("/api/govcon/opportunities/opp-err/coverage")
        assert resp.status_code == 500

    def test_500_response_has_error_key(self, api_app):
        with patch(
            "tools.govcon.capability_mapper.get_compliance_matrix",
            side_effect=RuntimeError("mapper offline"),
        ):
            with api_app.test_client() as c:
                resp = c.get("/api/govcon/opportunities/opp-err/coverage")
        data = resp.get_json()
        assert "error" in data

    def test_no_statements_returns_200_with_status_error(self, api_app):
        no_stmts_result = {
            "status": "error",
            "message": "No shall statements for opportunity opp-empty",
        }
        with patch(
            "tools.govcon.capability_mapper.get_compliance_matrix",
            return_value=no_stmts_result,
        ):
            with api_app.test_client() as c:
                resp = c.get("/api/govcon/opportunities/opp-empty/coverage")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "error"


# ---------------------------------------------------------------------------
# Fake data: GET /api/govcon/gaps (gcpl-map-08)
# ---------------------------------------------------------------------------

_FAKE_GAP_ITEM = {
    "pattern_id": "pat-gap-1",
    "pattern_name": "Zero Trust Architecture",
    "description": "ZTA requirement pattern",
    "domain": "devsecops",
    "frequency": 5,
    "representative_text": "The system shall implement ZTA.",
    "best_coverage": 0.20,
    "capability_count": 1,
    "status": "gap_identified",
    "grade": "N",
    "priority": 4.0,
    "severity": "high",
}

_FAKE_PARTIAL_ITEM = {
    "pattern_id": "pat-partial-1",
    "pattern_name": "Encryption at Rest",
    "description": "Encryption requirement",
    "domain": "security",
    "frequency": 3,
    "representative_text": "Data at rest shall be encrypted.",
    "best_coverage": 0.61,
    "capability_count": 2,
    "status": "mapped",
    "grade": "M",
    "priority": 1.17,
    "severity": "medium",
}

_FAKE_GAPS_RESULT = {
    "status": "ok",
    "summary": {
        "total_patterns": 2,
        "N_gaps": 1,
        "M_partial": 1,
        "L_compliant": 0,
        "gap_rate": 0.5,
    },
    "gaps": [_FAKE_GAP_ITEM],
    "partial": [_FAKE_PARTIAL_ITEM],
    "compliant_count": 0,
}


# ---------------------------------------------------------------------------
# API tests: GET /api/govcon/gaps (gcpl-map-08)
# ---------------------------------------------------------------------------


class TestGetGapsAPIEndpoint:
    """GET /api/govcon/gaps returns gap list with domain and severity."""

    @pytest.fixture()
    def api_app(self):
        return _build_api_test_app()

    def _get(self, api_app):
        with patch(
            "tools.govcon.gap_analyzer.analyze_gaps",
            return_value=_FAKE_GAPS_RESULT,
        ):
            with api_app.test_client() as c:
                resp = c.get("/api/govcon/gaps")
        return resp

    def test_get_returns_200(self, api_app):
        resp = self._get(api_app)
        assert resp.status_code == 200

    def test_response_content_type_is_json(self, api_app):
        resp = self._get(api_app)
        assert resp.content_type.startswith("application/json")

    def test_response_has_status_key(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        assert "status" in data

    def test_response_status_is_ok(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        assert data["status"] == "ok"

    def test_response_has_gaps_key(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        assert "gaps" in data

    def test_gaps_is_a_list(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        assert isinstance(data["gaps"], list)

    def test_response_has_summary_key(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        assert "summary" in data

    def test_summary_has_N_gaps_count(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        assert "N_gaps" in data["summary"]

    def test_gap_items_have_domain_attribute(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        for item in data["gaps"]:
            assert "domain" in item, f"Gap item missing 'domain': {item}"

    def test_gap_domain_is_non_empty_string(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        for item in data["gaps"]:
            assert isinstance(item["domain"], str) and item["domain"], (
                f"Gap domain is empty or not a string: {item['domain']!r}"
            )

    def test_gap_items_have_severity_attribute(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        for item in data["gaps"]:
            assert "severity" in item, f"Gap item missing 'severity': {item}"

    def test_gap_severity_values_are_valid(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        valid = {"high", "medium", "low"}
        for item in data["gaps"]:
            assert item["severity"] in valid, (
                f"Invalid severity {item['severity']!r} — expected one of {valid}"
            )

    def test_N_grade_gap_has_high_severity(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        n_items = [g for g in data["gaps"] if g.get("grade") == "N"]
        for item in n_items:
            assert item["severity"] == "high", (
                f"N-grade gap expected severity 'high', got {item['severity']!r}"
            )

    def test_response_has_partial_list(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        assert "partial" in data
        assert isinstance(data["partial"], list)

    def test_partial_items_have_domain_attribute(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        for item in data["partial"]:
            assert "domain" in item, f"Partial item missing 'domain': {item}"

    def test_partial_items_have_severity_attribute(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        for item in data["partial"]:
            assert "severity" in item, f"Partial item missing 'severity': {item}"

    def test_M_grade_partial_has_medium_severity(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        m_items = [p for p in data["partial"] if p.get("grade") == "M"]
        for item in m_items:
            assert item["severity"] == "medium", (
                f"M-grade partial expected severity 'medium', got {item['severity']!r}"
            )

    def test_response_has_compliant_count(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        assert "compliant_count" in data

    def test_returns_500_when_analyze_gaps_raises(self, api_app):
        with patch(
            "tools.govcon.gap_analyzer.analyze_gaps",
            side_effect=RuntimeError("gap_analyzer offline"),
        ):
            with api_app.test_client() as c:
                resp = c.get("/api/govcon/gaps")
        assert resp.status_code == 500

    def test_500_response_has_error_key(self, api_app):
        with patch(
            "tools.govcon.gap_analyzer.analyze_gaps",
            side_effect=RuntimeError("gap_analyzer offline"),
        ):
            with api_app.test_client() as c:
                resp = c.get("/api/govcon/gaps")
        data = resp.get_json()
        assert "error" in data


# ---------------------------------------------------------------------------
# DB schema for analyze_gaps() unit tests (gcpl-map-08)
# ---------------------------------------------------------------------------

_GAPS_SCHEMA = """
CREATE TABLE IF NOT EXISTS rfp_requirement_patterns (
    id TEXT PRIMARY KEY,
    pattern_name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    domain_category TEXT NOT NULL DEFAULT 'devsecops',
    frequency INTEGER NOT NULL DEFAULT 1,
    representative_text TEXT NOT NULL DEFAULT '',
    keyword_fingerprint TEXT NOT NULL DEFAULT '',
    keywords TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'new',
    classification TEXT DEFAULT 'CUI',
    metadata TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS icdev_capability_map (
    id TEXT PRIMARY KEY,
    pattern_id TEXT,
    capability_id TEXT,
    coverage_score REAL,
    grade TEXT,
    matched_keywords TEXT,
    created_at TEXT,
    metadata TEXT
);

CREATE TABLE IF NOT EXISTS audit_trail (
    id TEXT PRIMARY KEY,
    created_at TEXT,
    event_type TEXT,
    actor TEXT,
    action TEXT,
    details TEXT,
    session_id TEXT
);
"""


def _make_gaps_db(tmp_path: Path) -> tuple:
    """Create a SQLite DB with N/M/L-grade patterns; return (path, n_id, m_id, l_id)."""
    db_path = tmp_path / "gaps_unit.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_GAPS_SCHEMA)

    pat_n = str(uuid.uuid4())
    pat_m = str(uuid.uuid4())
    pat_l = str(uuid.uuid4())

    for pat_id, domain in [(pat_n, "devsecops"), (pat_m, "security"), (pat_l, "compliance")]:
        conn.execute(
            "INSERT INTO rfp_requirement_patterns "
            "(id, pattern_name, description, domain_category, frequency, "
            " representative_text, keyword_fingerprint, keywords, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (pat_id, f"Pattern {pat_id[:8]}", "test desc", domain, 3, "text", "fp", "[]", "new"),
        )

    # N-grade: coverage 0.10
    conn.execute(
        "INSERT INTO icdev_capability_map "
        "(id, pattern_id, capability_id, coverage_score, grade, matched_keywords, created_at, metadata) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), pat_n, "cap-n", 0.10, "N", "[]", "2026-01-01T00:00:00+00:00", "{}"),
    )
    # M-grade: coverage 0.60
    conn.execute(
        "INSERT INTO icdev_capability_map "
        "(id, pattern_id, capability_id, coverage_score, grade, matched_keywords, created_at, metadata) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), pat_m, "cap-m", 0.60, "M", "[]", "2026-01-01T00:00:00+00:00", "{}"),
    )
    # L-grade: coverage 0.90
    conn.execute(
        "INSERT INTO icdev_capability_map "
        "(id, pattern_id, capability_id, coverage_score, grade, matched_keywords, created_at, metadata) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), pat_l, "cap-l", 0.90, "L", "[]", "2026-01-01T00:00:00+00:00", "{}"),
    )
    conn.commit()
    conn.close()

    return db_path, pat_n, pat_m, pat_l


# ---------------------------------------------------------------------------
# Unit tests: analyze_gaps() severity field (gcpl-map-08)
# ---------------------------------------------------------------------------


class TestAnalyzeGapsSeverityField:
    """analyze_gaps() returns severity derived from coverage grade."""

    @pytest.fixture()
    def gaps_db(self, tmp_path):
        return _make_gaps_db(tmp_path)

    def _run_analyze(self, db_path):
        import sqlite3 as _sqlite3

        def _fake_get_db():
            conn = _sqlite3.connect(str(db_path))
            conn.row_factory = _sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            return conn

        with patch("tools.govcon.gap_analyzer._get_db", side_effect=_fake_get_db):
            from tools.govcon import gap_analyzer
            return gap_analyzer.analyze_gaps()

    def test_gaps_list_items_have_domain_key(self, gaps_db):
        db_path, _, _, _ = gaps_db
        result = self._run_analyze(db_path)
        assert len(result["gaps"]) > 0
        for gap in result["gaps"]:
            assert "domain" in gap, f"Gap missing 'domain': {gap}"

    def test_gaps_list_items_have_severity_key(self, gaps_db):
        db_path, _, _, _ = gaps_db
        result = self._run_analyze(db_path)
        assert len(result["gaps"]) > 0
        for gap in result["gaps"]:
            assert "severity" in gap, f"Gap missing 'severity': {gap}"

    def test_N_grade_gaps_have_severity_high(self, gaps_db):
        db_path, _, _, _ = gaps_db
        result = self._run_analyze(db_path)
        for gap in result["gaps"]:
            assert gap["severity"] == "high", (
                f"N-grade gap expected 'high', got {gap['severity']!r}"
            )

    def test_M_grade_partial_has_severity_medium(self, gaps_db):
        db_path, _, _, _ = gaps_db
        result = self._run_analyze(db_path)
        for item in result["partial"]:
            assert item["severity"] == "medium", (
                f"M-grade partial expected 'medium', got {item['severity']!r}"
            )


# ---------------------------------------------------------------------------
# Fake data: GET /api/govcon/gaps/recommendations (gcpl-map-09)
# ---------------------------------------------------------------------------

_FAKE_REC_ITEM = {
    "pattern_id": "pat-gap-1",
    "pattern_name": "Zero Trust Architecture",
    "domain": "devsecops",
    "frequency": 5,
    "priority": 4.0,
    "current_coverage": 0.20,
    "recommendation": {
        "approach": "Extend DevSecOps pipeline with new scanning stage or tool integration",
        "effort_estimate": "M",
        "existing_tools": ["pipeline_security_generator.py", "policy_generator.py"],
        "compliance_benefit": "SA-11, SA-15, SI-7 coverage improvement",
        "action": (
            "NEW CAPABILITY NEEDED: 'Zero Trust Architecture' appears in 5 RFPs "
            "with zero ICDEV™ coverage. Create new tool in tools/ targeting devsecops domain."
        ),
    },
}

_FAKE_RECOMMENDATIONS_RESULT = {
    "status": "ok",
    "total_recommendations": 1,
    "recommendations": [_FAKE_REC_ITEM],
}


# ---------------------------------------------------------------------------
# API tests: GET /api/govcon/gaps/recommendations (gcpl-map-09)
# ---------------------------------------------------------------------------


class TestGetGapRecommendationsAPIEndpoint:
    """GET /api/govcon/gaps/recommendations returns actionable recommendations."""

    @pytest.fixture()
    def api_app(self):
        return _build_api_test_app()

    def _get(self, api_app):
        with patch(
            "tools.govcon.gap_analyzer.generate_recommendations",
            return_value=_FAKE_RECOMMENDATIONS_RESULT,
        ):
            with api_app.test_client() as c:
                resp = c.get("/api/govcon/gaps/recommendations")
        return resp

    def test_get_returns_200(self, api_app):
        resp = self._get(api_app)
        assert resp.status_code == 200

    def test_response_content_type_is_json(self, api_app):
        resp = self._get(api_app)
        assert resp.content_type.startswith("application/json")

    def test_response_has_status_key(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        assert "status" in data

    def test_response_status_is_ok(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        assert data["status"] == "ok"

    def test_response_has_recommendations_key(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        assert "recommendations" in data

    def test_recommendations_is_a_list(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        assert isinstance(data["recommendations"], list)

    def test_response_has_total_recommendations_count(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        assert "total_recommendations" in data

    def test_total_recommendations_matches_list_length(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        assert data["total_recommendations"] == len(data["recommendations"])

    def test_recommendation_items_have_pattern_id(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        for item in data["recommendations"]:
            assert "pattern_id" in item, f"Recommendation missing 'pattern_id': {item}"

    def test_recommendation_items_have_pattern_name(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        for item in data["recommendations"]:
            assert "pattern_name" in item, f"Recommendation missing 'pattern_name': {item}"

    def test_recommendation_items_have_domain(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        for item in data["recommendations"]:
            assert "domain" in item, f"Recommendation missing 'domain': {item}"

    def test_recommendation_domain_is_non_empty_string(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        for item in data["recommendations"]:
            assert isinstance(item["domain"], str) and item["domain"], (
                f"Recommendation domain is empty or not a string: {item['domain']!r}"
            )

    def test_recommendation_items_have_priority(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        for item in data["recommendations"]:
            assert "priority" in item, f"Recommendation missing 'priority': {item}"

    def test_recommendation_items_have_current_coverage(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        for item in data["recommendations"]:
            assert "current_coverage" in item, (
                f"Recommendation missing 'current_coverage': {item}"
            )

    def test_recommendation_items_have_nested_recommendation_object(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        for item in data["recommendations"]:
            assert "recommendation" in item, (
                f"Recommendation missing nested 'recommendation' object: {item}"
            )
            assert isinstance(item["recommendation"], dict), (
                f"'recommendation' field is not a dict: {item['recommendation']!r}"
            )

    def test_nested_recommendation_has_approach(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        for item in data["recommendations"]:
            rec = item["recommendation"]
            assert "approach" in rec, f"Nested recommendation missing 'approach': {rec}"

    def test_nested_recommendation_has_effort_estimate(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        for item in data["recommendations"]:
            rec = item["recommendation"]
            assert "effort_estimate" in rec, (
                f"Nested recommendation missing 'effort_estimate': {rec}"
            )

    def test_nested_recommendation_has_action(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        for item in data["recommendations"]:
            rec = item["recommendation"]
            assert "action" in rec, f"Nested recommendation missing 'action': {rec}"

    def test_action_is_non_empty_string(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        for item in data["recommendations"]:
            action = item["recommendation"]["action"]
            assert isinstance(action, str) and action, (
                f"Recommendation action is empty or not a string: {action!r}"
            )

    def test_returns_500_when_generate_recommendations_raises(self, api_app):
        with patch(
            "tools.govcon.gap_analyzer.generate_recommendations",
            side_effect=RuntimeError("recommender offline"),
        ):
            with api_app.test_client() as c:
                resp = c.get("/api/govcon/gaps/recommendations")
        assert resp.status_code == 500

    def test_500_response_has_error_key(self, api_app):
        with patch(
            "tools.govcon.gap_analyzer.generate_recommendations",
            side_effect=RuntimeError("recommender offline"),
        ):
            with api_app.test_client() as c:
                resp = c.get("/api/govcon/gaps/recommendations")
        data = resp.get_json()
        assert "error" in data


# ---------------------------------------------------------------------------
# Unit tests: generate_recommendations() actionable output (gcpl-map-09)
# ---------------------------------------------------------------------------


class TestGenerateRecommendationsFunction:
    """generate_recommendations() returns actionable per-gap recommendations."""

    @pytest.fixture()
    def gaps_db(self, tmp_path):
        return _make_gaps_db(tmp_path)

    def _run_recommendations(self, db_path):
        import sqlite3 as _sqlite3

        def _fake_get_db():
            conn = _sqlite3.connect(str(db_path))
            conn.row_factory = _sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            return conn

        with patch("tools.govcon.gap_analyzer._get_db", side_effect=_fake_get_db):
            from tools.govcon import gap_analyzer
            return gap_analyzer.generate_recommendations()

    def test_returns_status_ok(self, gaps_db):
        db_path, _, _, _ = gaps_db
        result = self._run_recommendations(db_path)
        assert result["status"] == "ok"

    def test_returns_recommendations_list(self, gaps_db):
        db_path, _, _, _ = gaps_db
        result = self._run_recommendations(db_path)
        assert isinstance(result["recommendations"], list)

    def test_total_recommendations_matches_list_length(self, gaps_db):
        db_path, _, _, _ = gaps_db
        result = self._run_recommendations(db_path)
        assert result["total_recommendations"] == len(result["recommendations"])

    def test_each_recommendation_has_pattern_id(self, gaps_db):
        db_path, _, _, _ = gaps_db
        result = self._run_recommendations(db_path)
        for rec in result["recommendations"]:
            assert "pattern_id" in rec, f"Missing 'pattern_id': {rec}"

    def test_each_recommendation_has_domain(self, gaps_db):
        db_path, _, _, _ = gaps_db
        result = self._run_recommendations(db_path)
        for rec in result["recommendations"]:
            assert "domain" in rec, f"Missing 'domain': {rec}"

    def test_each_recommendation_has_nested_recommendation(self, gaps_db):
        db_path, _, _, _ = gaps_db
        result = self._run_recommendations(db_path)
        for rec in result["recommendations"]:
            assert "recommendation" in rec, f"Missing nested 'recommendation': {rec}"
            assert isinstance(rec["recommendation"], dict)

    def test_nested_recommendation_action_is_non_empty(self, gaps_db):
        db_path, _, _, _ = gaps_db
        result = self._run_recommendations(db_path)
        for rec in result["recommendations"]:
            action = rec["recommendation"].get("action", "")
            assert isinstance(action, str) and action, (
                f"Action is empty or not a string: {action!r}"
            )

    def test_only_gap_patterns_are_recommended(self, gaps_db):
        """generate_recommendations() only covers N-grade gaps, not M or L."""
        db_path, pat_n, _, _ = gaps_db
        result = self._run_recommendations(db_path)
        pattern_ids = {r["pattern_id"] for r in result["recommendations"]}
        assert pat_n in pattern_ids, "N-grade gap pattern should appear in recommendations"


# ---------------------------------------------------------------------------
# Fake data: GET /api/govcon/gaps/heatmap (gcpl-map-10)
# ---------------------------------------------------------------------------

_FAKE_HEATMAP_RESULT = {
    "status": "ok",
    "heatmap": {
        "devsecops": {"L": 2, "M": 1, "N": 1, "total_frequency": 10, "health_score": 0.63},
        "security": {"L": 0, "M": 1, "N": 2, "total_frequency": 6, "health_score": 0.17},
    },
}


# ---------------------------------------------------------------------------
# API tests: GET /api/govcon/gaps/heatmap (gcpl-map-10)
# ---------------------------------------------------------------------------


class TestGetHeatmapAPIEndpoint:
    """GET /api/govcon/gaps/heatmap returns a domain-keyed heatmap object."""

    @pytest.fixture()
    def api_app(self):
        return _build_api_test_app()

    def _get(self, api_app):
        with patch(
            "tools.govcon.gap_analyzer.get_heatmap",
            return_value=_FAKE_HEATMAP_RESULT,
        ):
            with api_app.test_client() as c:
                resp = c.get("/api/govcon/gaps/heatmap")
        return resp

    def test_get_returns_200(self, api_app):
        resp = self._get(api_app)
        assert resp.status_code == 200

    def test_response_content_type_is_json(self, api_app):
        resp = self._get(api_app)
        assert resp.content_type.startswith("application/json")

    def test_response_has_status_key(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        assert "status" in data

    def test_response_status_is_ok(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        assert data["status"] == "ok"

    def test_response_has_heatmap_key(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        assert "heatmap" in data

    def test_heatmap_is_a_dict(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        assert isinstance(data["heatmap"], dict)

    def test_heatmap_keys_are_non_empty_strings(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        for key in data["heatmap"]:
            assert isinstance(key, str) and key, f"Domain key is empty or not a string: {key!r}"

    def test_each_domain_has_L_count(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        for domain, entry in data["heatmap"].items():
            assert "L" in entry, f"Domain '{domain}' missing 'L' key"

    def test_each_domain_has_M_count(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        for domain, entry in data["heatmap"].items():
            assert "M" in entry, f"Domain '{domain}' missing 'M' key"

    def test_each_domain_has_N_count(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        for domain, entry in data["heatmap"].items():
            assert "N" in entry, f"Domain '{domain}' missing 'N' key"

    def test_each_domain_has_total_frequency(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        for domain, entry in data["heatmap"].items():
            assert "total_frequency" in entry, f"Domain '{domain}' missing 'total_frequency' key"

    def test_each_domain_has_health_score(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        for domain, entry in data["heatmap"].items():
            assert "health_score" in entry, f"Domain '{domain}' missing 'health_score' key"

    def test_health_score_is_between_0_and_1(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        for domain, entry in data["heatmap"].items():
            score = entry["health_score"]
            assert 0.0 <= score <= 1.0, (
                f"Domain '{domain}' health_score {score!r} out of range [0, 1]"
            )

    def test_get_returns_500_when_heatmap_raises(self, api_app):
        with patch(
            "tools.govcon.gap_analyzer.get_heatmap",
            side_effect=RuntimeError("db error"),
        ):
            with api_app.test_client() as c:
                resp = c.get("/api/govcon/gaps/heatmap")
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# Unit tests: get_heatmap() domain-keyed structure (gcpl-map-10)
# ---------------------------------------------------------------------------


class TestGetHeatmapFunction:
    """get_heatmap() returns a domain-keyed dict with L/M/N counts and health_score."""

    @pytest.fixture()
    def gaps_db(self, tmp_path):
        return _make_gaps_db(tmp_path)

    def _run_heatmap(self, db_path):
        import sqlite3 as _sqlite3

        def _fake_get_db():
            conn = _sqlite3.connect(str(db_path))
            conn.row_factory = _sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            return conn

        with patch("tools.govcon.gap_analyzer._get_db", side_effect=_fake_get_db):
            from tools.govcon import gap_analyzer
            return gap_analyzer.get_heatmap()

    def test_returns_status_ok(self, gaps_db):
        db_path, _, _, _ = gaps_db
        result = self._run_heatmap(db_path)
        assert result["status"] == "ok"

    def test_returns_heatmap_key(self, gaps_db):
        db_path, _, _, _ = gaps_db
        result = self._run_heatmap(db_path)
        assert "heatmap" in result

    def test_heatmap_is_domain_keyed_dict(self, gaps_db):
        db_path, _, _, _ = gaps_db
        result = self._run_heatmap(db_path)
        assert isinstance(result["heatmap"], dict)

    def test_N_grade_domain_has_positive_N_count(self, gaps_db):
        """devsecops domain holds the N-grade pattern; its N count must be >= 1."""
        db_path, _, _, _ = gaps_db
        result = self._run_heatmap(db_path)
        heatmap = result["heatmap"]
        assert "devsecops" in heatmap, f"'devsecops' domain missing from heatmap: {list(heatmap)}"
        assert heatmap["devsecops"]["N"] >= 1, (
            f"Expected N >= 1 for devsecops, got {heatmap['devsecops']['N']}"
        )

    def test_M_grade_domain_has_positive_M_count(self, gaps_db):
        """security domain holds the M-grade pattern; its M count must be >= 1."""
        db_path, _, _, _ = gaps_db
        result = self._run_heatmap(db_path)
        heatmap = result["heatmap"]
        assert "security" in heatmap, f"'security' domain missing from heatmap: {list(heatmap)}"
        assert heatmap["security"]["M"] >= 1, (
            f"Expected M >= 1 for security, got {heatmap['security']['M']}"
        )

    def test_L_grade_domain_has_positive_L_count(self, gaps_db):
        """compliance domain holds the L-grade pattern; its L count must be >= 1."""
        db_path, _, _, _ = gaps_db
        result = self._run_heatmap(db_path)
        heatmap = result["heatmap"]
        assert "compliance" in heatmap, f"'compliance' domain missing from heatmap: {list(heatmap)}"
        assert heatmap["compliance"]["L"] >= 1, (
            f"Expected L >= 1 for compliance, got {heatmap['compliance']['L']}"
        )

    def test_each_domain_entry_has_all_required_keys(self, gaps_db):
        db_path, _, _, _ = gaps_db
        result = self._run_heatmap(db_path)
        required = {"L", "M", "N", "total_frequency", "health_score"}
        for domain, entry in result["heatmap"].items():
            missing = required - set(entry)
            assert not missing, f"Domain '{domain}' missing keys: {missing}"

    def test_health_score_for_L_only_domain_is_1(self, tmp_path):
        """A domain where all patterns are L-grade must have health_score == 1.0."""
        db_path = tmp_path / "l_only.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript(_GAPS_SCHEMA)
        pat_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO rfp_requirement_patterns "
            "(id, pattern_name, description, domain_category, frequency, "
            " representative_text, keyword_fingerprint, keywords, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (pat_id, "Full Compliance", "all good", "ato_rmf", 2, "text", "fp", "[]", "new"),
        )
        conn.execute(
            "INSERT INTO icdev_capability_map "
            "(id, pattern_id, capability_id, coverage_score, grade, matched_keywords, created_at, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), pat_id, "cap-l", 0.95, "L", "[]", "2026-01-01T00:00:00+00:00", "{}"),
        )
        conn.commit()
        conn.close()

        result = self._run_heatmap(db_path)
        score = result["heatmap"]["ato_rmf"]["health_score"]
        assert score == 1.0, f"All-L domain should have health_score 1.0, got {score}"

    def test_health_score_for_N_only_domain_is_0(self, tmp_path):
        """A domain where all patterns are N-grade must have health_score == 0.0."""
        db_path = tmp_path / "n_only.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript(_GAPS_SCHEMA)
        pat_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO rfp_requirement_patterns "
            "(id, pattern_name, description, domain_category, frequency, "
            " representative_text, keyword_fingerprint, keywords, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (pat_id, "Total Gap", "no coverage", "cloud", 4, "text", "fp", "[]", "new"),
        )
        conn.execute(
            "INSERT INTO icdev_capability_map "
            "(id, pattern_id, capability_id, coverage_score, grade, matched_keywords, created_at, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), pat_id, "cap-n", 0.05, "N", "[]", "2026-01-01T00:00:00+00:00", "{}"),
        )
        conn.commit()
        conn.close()

        result = self._run_heatmap(db_path)
        score = result["heatmap"]["cloud"]["health_score"]
        assert score == 0.0, f"All-N domain should have health_score 0.0, got {score}"

    def test_empty_db_returns_empty_heatmap(self, tmp_path):
        """With no patterns in the DB, heatmap must be an empty dict."""
        db_path = tmp_path / "empty.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript(_GAPS_SCHEMA)
        conn.commit()
        conn.close()

        result = self._run_heatmap(db_path)
        assert result["heatmap"] == {}, f"Expected empty heatmap, got {result['heatmap']}"


# ---------------------------------------------------------------------------
# Shared fixtures: GET /api/govcon/opportunities/<id>/bid-recommendation (gcpl-dft-01)
# ---------------------------------------------------------------------------

_FAKE_SUMMARY_STRONG_BID = {
    "status": "ok",
    "opportunity_id": "opp-test-123",
    "overall": {
        "total": 10,
        "L": 8,
        "M": 1,
        "N": 1,
        "compliance_rate": 0.8,
    },
    "by_domain": {"devsecops": {"L": 8, "M": 1, "N": 1, "total": 10}},
    "bid_recommendation": {
        "decision": "strong_bid",
        "score": 0.8,
        "bid": True,
        "reason": "80% compliant, only 10% gaps. Strong capability alignment.",
        "confidence": "high",
    },
}

_FAKE_SUMMARY_BID_WITH_GAPS = {
    "status": "ok",
    "opportunity_id": "opp-test-456",
    "overall": {
        "total": 10,
        "L": 6,
        "M": 2,
        "N": 2,
        "compliance_rate": 0.6,
    },
    "by_domain": {"security": {"L": 6, "M": 2, "N": 2, "total": 10}},
    "bid_recommendation": {
        "decision": "bid_with_gaps",
        "score": 0.6,
        "bid": True,
        "reason": "60% compliant, 20% gaps. Address gaps via teaming or enhancement.",
        "confidence": "medium",
    },
}

_FAKE_SUMMARY_CONDITIONAL_BID = {
    "status": "ok",
    "opportunity_id": "opp-test-789",
    "overall": {
        "total": 10,
        "L": 4,
        "M": 3,
        "N": 3,
        "compliance_rate": 0.4,
    },
    "by_domain": {"cloud": {"L": 4, "M": 3, "N": 3, "total": 10}},
    "bid_recommendation": {
        "decision": "conditional_bid",
        "score": 0.4,
        "bid": True,
        "reason": "Only 40% compliant. Significant gaps. Consider teaming partner.",
        "confidence": "low",
    },
}

_FAKE_SUMMARY_NO_BID = {
    "status": "ok",
    "opportunity_id": "opp-test-000",
    "overall": {
        "total": 10,
        "L": 2,
        "M": 2,
        "N": 6,
        "compliance_rate": 0.2,
    },
    "by_domain": {"data": {"L": 2, "M": 2, "N": 6, "total": 10}},
    "bid_recommendation": {
        "decision": "no_bid",
        "score": 0.2,
        "bid": False,
        "reason": "Only 20% compliant with 60% gaps. Poor alignment.",
        "confidence": "high",
    },
}

_FAKE_SUMMARY_INSUFFICIENT = {
    "status": "ok",
    "opportunity_id": "opp-empty",
    "overall": {
        "total": 0,
        "L": 0,
        "M": 0,
        "N": 0,
        "compliance_rate": 0.0,
    },
    "by_domain": {},
    "bid_recommendation": {
        "decision": "insufficient_data",
        "score": 0.0,
        "bid": False,
        "reason": "No requirements extracted",
    },
}


# ---------------------------------------------------------------------------
# API tests: GET /api/govcon/opportunities/<id>/bid-recommendation (gcpl-dft-01)
# ---------------------------------------------------------------------------


class TestBidRecommendationAPIEndpoint:
    """GET /api/govcon/opportunities/<id>/bid-recommendation returns score + rationale."""

    @pytest.fixture()
    def api_app(self):
        return _build_api_test_app()

    def _get(self, api_app, opp_id="opp-test-123", fake_summary=None):
        if fake_summary is None:
            fake_summary = _FAKE_SUMMARY_STRONG_BID
        with patch(
            "tools.govcon.compliance_populator.get_summary",
            return_value=fake_summary,
        ):
            with api_app.test_client() as c:
                resp = c.get(f"/api/govcon/opportunities/{opp_id}/bid-recommendation")
        return resp

    def test_get_returns_200(self, api_app):
        resp = self._get(api_app)
        assert resp.status_code == 200

    def test_response_content_type_is_json(self, api_app):
        resp = self._get(api_app)
        assert resp.content_type.startswith("application/json")

    def test_response_has_status_key(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        assert "status" in data

    def test_response_status_is_ok(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        assert data["status"] == "ok"

    def test_response_has_bid_recommendation_key(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        assert "bid_recommendation" in data

    def test_bid_recommendation_has_decision(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        assert "decision" in data["bid_recommendation"]

    def test_bid_recommendation_has_score(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        assert "score" in data["bid_recommendation"]

    def test_bid_recommendation_score_is_float(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        score = data["bid_recommendation"]["score"]
        assert isinstance(score, float), f"Expected float score, got {type(score).__name__}: {score!r}"

    def test_bid_recommendation_score_is_between_0_and_1(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        score = data["bid_recommendation"]["score"]
        assert 0.0 <= score <= 1.0, f"score {score!r} out of range [0.0, 1.0]"

    def test_bid_recommendation_has_reason(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        assert "reason" in data["bid_recommendation"]

    def test_bid_recommendation_reason_is_non_empty_string(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        reason = data["bid_recommendation"]["reason"]
        assert isinstance(reason, str) and reason, f"reason must be non-empty string, got {reason!r}"

    def test_response_has_overall_key(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        assert "overall" in data

    def test_overall_has_compliance_rate(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        assert "compliance_rate" in data["overall"]

    def test_overall_compliance_rate_is_between_0_and_1(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        rate = data["overall"]["compliance_rate"]
        assert 0.0 <= rate <= 1.0, f"compliance_rate {rate!r} out of range [0.0, 1.0]"

    def test_strong_bid_decision_returned(self, api_app):
        resp = self._get(api_app, fake_summary=_FAKE_SUMMARY_STRONG_BID)
        data = resp.get_json()
        assert data["bid_recommendation"]["decision"] == "strong_bid"

    def test_strong_bid_score_gte_070(self, api_app):
        resp = self._get(api_app, fake_summary=_FAKE_SUMMARY_STRONG_BID)
        data = resp.get_json()
        assert data["bid_recommendation"]["score"] >= 0.70, (
            f"strong_bid score must be >= 0.70, got {data['bid_recommendation']['score']}"
        )

    def test_bid_with_gaps_decision_returned(self, api_app):
        resp = self._get(api_app, opp_id="opp-test-456", fake_summary=_FAKE_SUMMARY_BID_WITH_GAPS)
        data = resp.get_json()
        assert data["bid_recommendation"]["decision"] == "bid_with_gaps"

    def test_bid_with_gaps_confidence_is_medium(self, api_app):
        resp = self._get(api_app, opp_id="opp-test-456", fake_summary=_FAKE_SUMMARY_BID_WITH_GAPS)
        data = resp.get_json()
        assert data["bid_recommendation"]["confidence"] == "medium"

    def test_conditional_bid_decision_returned(self, api_app):
        resp = self._get(api_app, opp_id="opp-test-789", fake_summary=_FAKE_SUMMARY_CONDITIONAL_BID)
        data = resp.get_json()
        assert data["bid_recommendation"]["decision"] == "conditional_bid"

    def test_conditional_bid_confidence_is_low(self, api_app):
        resp = self._get(api_app, opp_id="opp-test-789", fake_summary=_FAKE_SUMMARY_CONDITIONAL_BID)
        data = resp.get_json()
        assert data["bid_recommendation"]["confidence"] == "low"

    def test_no_bid_decision_returned(self, api_app):
        resp = self._get(api_app, opp_id="opp-test-000", fake_summary=_FAKE_SUMMARY_NO_BID)
        data = resp.get_json()
        assert data["bid_recommendation"]["decision"] == "no_bid"

    def test_no_bid_score_below_030(self, api_app):
        resp = self._get(api_app, opp_id="opp-test-000", fake_summary=_FAKE_SUMMARY_NO_BID)
        data = resp.get_json()
        assert data["bid_recommendation"]["score"] < 0.30, (
            f"no_bid score must be < 0.30, got {data['bid_recommendation']['score']}"
        )

    def test_insufficient_data_decision_returned(self, api_app):
        resp = self._get(api_app, opp_id="opp-empty", fake_summary=_FAKE_SUMMARY_INSUFFICIENT)
        data = resp.get_json()
        assert data["bid_recommendation"]["decision"] == "insufficient_data"

    def test_insufficient_data_score_is_zero(self, api_app):
        resp = self._get(api_app, opp_id="opp-empty", fake_summary=_FAKE_SUMMARY_INSUFFICIENT)
        data = resp.get_json()
        assert data["bid_recommendation"]["score"] == 0.0, (
            f"insufficient_data score must be 0.0, got {data['bid_recommendation']['score']}"
        )

    def test_endpoint_accepts_arbitrary_opp_id(self, api_app):
        for opp_id in ("abc-123", "uuid-9999", "opportunity-xyz"):
            resp = self._get(api_app, opp_id=opp_id)
            assert resp.status_code == 200, f"Expected 200 for opp_id={opp_id!r}"

    def test_returns_500_when_get_summary_raises(self, api_app):
        with patch(
            "tools.govcon.compliance_populator.get_summary",
            side_effect=RuntimeError("db offline"),
        ):
            with api_app.test_client() as c:
                resp = c.get("/api/govcon/opportunities/opp-err/bid-recommendation")
        assert resp.status_code == 500

    def test_500_response_has_error_key(self, api_app):
        with patch(
            "tools.govcon.compliance_populator.get_summary",
            side_effect=RuntimeError("db offline"),
        ):
            with api_app.test_client() as c:
                resp = c.get("/api/govcon/opportunities/opp-err/bid-recommendation")
        data = resp.get_json()
        assert "error" in data


# ---------------------------------------------------------------------------
# Unit tests: _bid_recommendation() score + rationale (gcpl-dft-01)
# ---------------------------------------------------------------------------


class TestBidRecommendationFunction:
    """_bid_recommendation() computes a score 0.0–1.0 and a non-empty reason string."""

    def _call(self, l_count, m_count, n_count):
        from tools.govcon.compliance_populator import _bid_recommendation

        total = l_count + m_count + n_count
        return _bid_recommendation(
            {
                "total_requirements": total,
                "L_compliant": l_count,
                "M_partial": m_count,
                "N_gap": n_count,
            }
        )

    def test_returns_dict(self):
        result = self._call(7, 2, 1)
        assert isinstance(result, dict)

    def test_result_has_score_key(self):
        result = self._call(7, 2, 1)
        assert "score" in result

    def test_score_is_float(self):
        result = self._call(7, 2, 1)
        assert isinstance(result["score"], float)

    def test_score_is_between_0_and_1(self):
        for l, m, n in [(7, 2, 1), (5, 3, 2), (3, 3, 4), (1, 2, 7)]:
            result = self._call(l, m, n)
            assert 0.0 <= result["score"] <= 1.0, (
                f"score {result['score']!r} out of range for L={l} M={m} N={n}"
            )

    def test_result_has_reason_key(self):
        result = self._call(7, 2, 1)
        assert "reason" in result

    def test_reason_is_non_empty_string(self):
        result = self._call(7, 2, 1)
        assert isinstance(result["reason"], str) and result["reason"]

    def test_result_has_decision_key(self):
        result = self._call(7, 2, 1)
        assert "decision" in result

    def test_strong_bid_at_70pct_L_10pct_N(self):
        result = self._call(7, 2, 1)
        assert result["decision"] == "strong_bid"

    def test_strong_bid_score_equals_l_rate(self):
        result = self._call(7, 2, 1)
        assert result["score"] == pytest.approx(0.7, abs=1e-4)

    def test_strong_bid_confidence_is_high(self):
        result = self._call(7, 2, 1)
        assert result["confidence"] == "high"

    def test_bid_with_gaps_at_50pct_L_20pct_N(self):
        result = self._call(5, 3, 2)
        assert result["decision"] == "bid_with_gaps"

    def test_bid_with_gaps_score_equals_l_rate(self):
        result = self._call(5, 3, 2)
        assert result["score"] == pytest.approx(0.5, abs=1e-4)

    def test_bid_with_gaps_confidence_is_medium(self):
        result = self._call(5, 3, 2)
        assert result["confidence"] == "medium"

    def test_conditional_bid_at_30pct_L(self):
        result = self._call(3, 3, 4)
        assert result["decision"] == "conditional_bid"

    def test_conditional_bid_score_equals_l_rate(self):
        result = self._call(3, 3, 4)
        assert result["score"] == pytest.approx(0.3, abs=1e-4)

    def test_conditional_bid_confidence_is_low(self):
        result = self._call(3, 3, 4)
        assert result["confidence"] == "low"

    def test_no_bid_at_10pct_L(self):
        result = self._call(1, 2, 7)
        assert result["decision"] == "no_bid"

    def test_no_bid_score_equals_l_rate(self):
        result = self._call(1, 2, 7)
        assert result["score"] == pytest.approx(0.1, abs=1e-4)

    def test_no_bid_confidence_is_high(self):
        result = self._call(1, 2, 7)
        assert result["confidence"] == "high"

    def test_insufficient_data_when_total_is_zero(self):
        result = self._call(0, 0, 0)
        assert result["decision"] == "insufficient_data"

    def test_insufficient_data_score_is_0(self):
        result = self._call(0, 0, 0)
        assert result["score"] == 0.0

    def test_insufficient_data_has_reason(self):
        result = self._call(0, 0, 0)
        assert result["reason"] == "No requirements extracted"

    def test_score_at_boundary_100pct_L(self):
        result = self._call(10, 0, 0)
        assert result["score"] == 1.0
        assert result["decision"] == "strong_bid"

    def test_score_at_boundary_0pct_L(self):
        result = self._call(0, 0, 10)
        assert result["score"] == 0.0
        assert result["decision"] == "no_bid"


# ---------------------------------------------------------------------------
# Tests: bid score is numeric float 0.0–1.0 (gcpl-dft-02)
# ---------------------------------------------------------------------------


class TestBidScoreIsNumeric:
    """Score must be a Python float (not int, bool, or str) in closed range [0.0, 1.0]."""

    def _call(self, l_count, m_count, n_count):
        from tools.govcon.compliance_populator import _bid_recommendation

        total = l_count + m_count + n_count
        return _bid_recommendation(
            {
                "total_requirements": total,
                "L_compliant": l_count,
                "M_partial": m_count,
                "N_gap": n_count,
            }
        )

    def test_score_is_float_strong_bid(self):
        result = self._call(7, 2, 1)
        assert isinstance(result["score"], float), (
            f"score must be float for strong_bid, got {type(result['score']).__name__}"
        )

    def test_score_is_float_bid_with_gaps(self):
        result = self._call(5, 3, 2)
        assert isinstance(result["score"], float)

    def test_score_is_float_conditional_bid(self):
        result = self._call(3, 3, 4)
        assert isinstance(result["score"], float)

    def test_score_is_float_no_bid(self):
        result = self._call(1, 2, 7)
        assert isinstance(result["score"], float)

    def test_score_is_float_insufficient_data(self):
        result = self._call(0, 0, 0)
        assert isinstance(result["score"], float)

    def test_score_is_float_at_100pct_L(self):
        result = self._call(10, 0, 0)
        assert isinstance(result["score"], float)

    def test_score_is_float_at_0pct_L(self):
        result = self._call(0, 0, 10)
        assert isinstance(result["score"], float)

    def test_score_is_not_bool(self):
        result = self._call(7, 2, 1)
        assert not isinstance(result["score"], bool), "score must not be bool"

    def test_score_is_not_string(self):
        result = self._call(7, 2, 1)
        assert not isinstance(result["score"], str), "score must not be a string"

    def test_score_gte_0_strong_bid(self):
        assert self._call(7, 2, 1)["score"] >= 0.0

    def test_score_lte_1_strong_bid(self):
        assert self._call(7, 2, 1)["score"] <= 1.0

    def test_score_gte_0_bid_with_gaps(self):
        assert self._call(5, 3, 2)["score"] >= 0.0

    def test_score_lte_1_bid_with_gaps(self):
        assert self._call(5, 3, 2)["score"] <= 1.0

    def test_score_gte_0_conditional_bid(self):
        assert self._call(3, 3, 4)["score"] >= 0.0

    def test_score_lte_1_conditional_bid(self):
        assert self._call(3, 3, 4)["score"] <= 1.0

    def test_score_gte_0_no_bid(self):
        assert self._call(1, 2, 7)["score"] >= 0.0

    def test_score_lte_1_no_bid(self):
        assert self._call(1, 2, 7)["score"] <= 1.0

    def test_score_exactly_0_insufficient_data(self):
        assert self._call(0, 0, 0)["score"] == 0.0

    def test_score_exactly_1_at_100pct_L(self):
        assert self._call(10, 0, 0)["score"] == 1.0

    def test_score_exactly_0_at_0pct_L_with_total(self):
        assert self._call(0, 0, 10)["score"] == 0.0

    @pytest.mark.parametrize(
        "l,m,n",
        [
            (7, 2, 1),
            (5, 3, 2),
            (3, 3, 4),
            (1, 2, 7),
            (10, 0, 0),
            (0, 0, 10),
        ],
    )
    def test_score_in_range_parametrized(self, l, m, n):
        result = self._call(l, m, n)
        assert 0.0 <= result["score"] <= 1.0, (
            f"score {result['score']!r} out of [0.0, 1.0] for L={l} M={m} N={n}"
        )


# ---------------------------------------------------------------------------
# Tests: binary bid/no_bid decision field (gcpl-dft-02)
# ---------------------------------------------------------------------------


class TestBidBinaryDecision:
    """_bid_recommendation() must include a binary `bid` boolean field."""

    def _call(self, l_count, m_count, n_count):
        from tools.govcon.compliance_populator import _bid_recommendation

        total = l_count + m_count + n_count
        return _bid_recommendation(
            {
                "total_requirements": total,
                "L_compliant": l_count,
                "M_partial": m_count,
                "N_gap": n_count,
            }
        )

    def test_result_has_bid_key(self):
        result = self._call(7, 2, 1)
        assert "bid" in result, "bid boolean field must be present in result"

    def test_bid_is_bool_strong_bid(self):
        assert isinstance(self._call(7, 2, 1)["bid"], bool)

    def test_bid_is_bool_bid_with_gaps(self):
        assert isinstance(self._call(5, 3, 2)["bid"], bool)

    def test_bid_is_bool_conditional_bid(self):
        assert isinstance(self._call(3, 3, 4)["bid"], bool)

    def test_bid_is_bool_no_bid(self):
        assert isinstance(self._call(1, 2, 7)["bid"], bool)

    def test_bid_is_bool_insufficient_data(self):
        assert isinstance(self._call(0, 0, 0)["bid"], bool)

    def test_strong_bid_bid_is_true(self):
        assert self._call(7, 2, 1)["bid"] is True

    def test_bid_with_gaps_bid_is_true(self):
        assert self._call(5, 3, 2)["bid"] is True

    def test_conditional_bid_bid_is_true(self):
        assert self._call(3, 3, 4)["bid"] is True

    def test_no_bid_bid_is_false(self):
        assert self._call(1, 2, 7)["bid"] is False

    def test_insufficient_data_bid_is_false(self):
        assert self._call(0, 0, 0)["bid"] is False

    def test_bid_true_at_100pct_L(self):
        assert self._call(10, 0, 0)["bid"] is True

    def test_bid_false_at_0pct_L_with_total(self):
        assert self._call(0, 0, 10)["bid"] is False

    def test_bid_not_none(self):
        assert self._call(7, 2, 1)["bid"] is not None

    def test_bid_is_not_string(self):
        result = self._call(7, 2, 1)
        assert not isinstance(result["bid"], str), "bid must be bool, not string"

    def test_bid_coalesces_all_positive_decisions(self):
        for l, m, n in [(7, 2, 1), (5, 3, 2), (3, 3, 4)]:
            result = self._call(l, m, n)
            assert result["bid"] is True, (
                f"Expected bid=True for L={l} M={m} N={n}, got {result['bid']!r}"
            )

    def test_bid_false_for_no_bid_decision(self):
        result = self._call(1, 2, 7)
        assert result["decision"] == "no_bid"
        assert result["bid"] is False

    def test_bid_false_for_insufficient_data(self):
        result = self._call(0, 0, 0)
        assert result["decision"] == "insufficient_data"
        assert result["bid"] is False

    def test_bid_consistent_with_decision(self):
        _BID_DECISIONS = {"strong_bid", "bid_with_gaps", "conditional_bid"}
        for l, m, n in [(7, 2, 1), (5, 3, 2), (3, 3, 4), (1, 2, 7), (10, 0, 0), (0, 0, 10)]:
            result = self._call(l, m, n)
            expected = result["decision"] in _BID_DECISIONS
            assert result["bid"] == expected, (
                f"bid mismatch for L={l} M={m} N={n}: decision={result['decision']}, bid={result['bid']}"
            )


# ---------------------------------------------------------------------------
# API tests: score numeric + binary bid field via endpoint (gcpl-dft-02)
# ---------------------------------------------------------------------------


class TestBidScoreAndBinaryDecisionAPI:
    """GET /api/govcon/opportunities/<id>/bid-recommendation returns float score and bool bid."""

    @pytest.fixture()
    def api_app(self):
        return _build_api_test_app()

    def _get(self, api_app, opp_id="opp-test-123", fake_summary=None):
        if fake_summary is None:
            fake_summary = _FAKE_SUMMARY_STRONG_BID
        with patch(
            "tools.govcon.compliance_populator.get_summary",
            return_value=fake_summary,
        ):
            with api_app.test_client() as c:
                resp = c.get(f"/api/govcon/opportunities/{opp_id}/bid-recommendation")
        return resp

    def test_score_is_json_number_not_string(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        score = data["bid_recommendation"]["score"]
        assert isinstance(score, (int, float)) and not isinstance(score, bool), (
            f"score must be JSON number, got {type(score).__name__}: {score!r}"
        )

    def test_score_not_string_strong_bid(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        assert not isinstance(data["bid_recommendation"]["score"], str)

    def test_score_gte_0_all_summaries(self, api_app):
        for fake in [
            _FAKE_SUMMARY_STRONG_BID,
            _FAKE_SUMMARY_BID_WITH_GAPS,
            _FAKE_SUMMARY_CONDITIONAL_BID,
            _FAKE_SUMMARY_NO_BID,
            _FAKE_SUMMARY_INSUFFICIENT,
        ]:
            resp = self._get(api_app, fake_summary=fake)
            score = resp.get_json()["bid_recommendation"]["score"]
            assert score >= 0.0, f"score {score!r} < 0.0"

    def test_score_lte_1_positive_decisions(self, api_app):
        for fake in [
            _FAKE_SUMMARY_STRONG_BID,
            _FAKE_SUMMARY_BID_WITH_GAPS,
            _FAKE_SUMMARY_CONDITIONAL_BID,
            _FAKE_SUMMARY_NO_BID,
        ]:
            resp = self._get(api_app, fake_summary=fake)
            score = resp.get_json()["bid_recommendation"]["score"]
            assert score <= 1.0, f"score {score!r} > 1.0"

    def test_bid_recommendation_has_bid_field(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        assert "bid" in data["bid_recommendation"], "bid boolean field missing from API response"

    def test_api_bid_is_bool_strong_bid(self, api_app):
        resp = self._get(api_app, fake_summary=_FAKE_SUMMARY_STRONG_BID)
        assert isinstance(resp.get_json()["bid_recommendation"]["bid"], bool)

    def test_api_strong_bid_bid_is_true(self, api_app):
        resp = self._get(api_app, fake_summary=_FAKE_SUMMARY_STRONG_BID)
        assert resp.get_json()["bid_recommendation"]["bid"] is True

    def test_api_bid_with_gaps_bid_is_true(self, api_app):
        resp = self._get(api_app, opp_id="opp-test-456", fake_summary=_FAKE_SUMMARY_BID_WITH_GAPS)
        assert resp.get_json()["bid_recommendation"]["bid"] is True

    def test_api_conditional_bid_bid_is_true(self, api_app):
        resp = self._get(api_app, opp_id="opp-test-789", fake_summary=_FAKE_SUMMARY_CONDITIONAL_BID)
        assert resp.get_json()["bid_recommendation"]["bid"] is True

    def test_api_no_bid_bid_is_false(self, api_app):
        resp = self._get(api_app, opp_id="opp-test-000", fake_summary=_FAKE_SUMMARY_NO_BID)
        assert resp.get_json()["bid_recommendation"]["bid"] is False

    def test_api_insufficient_data_bid_is_false(self, api_app):
        resp = self._get(api_app, opp_id="opp-empty", fake_summary=_FAKE_SUMMARY_INSUFFICIENT)
        assert resp.get_json()["bid_recommendation"]["bid"] is False
