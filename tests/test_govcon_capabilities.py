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
