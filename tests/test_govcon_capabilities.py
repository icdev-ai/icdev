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

    from _sql_compat import connect as _tconnect

    def _get_db():
        conn = _tconnect(db_path)
        return conn

    flask_app = Flask(__name__, template_folder=str(
        Path(__file__).parent.parent / "tools" / "dashboard" / "templates"
    ))
    flask_app.config["TESTING"] = True

    from tools.dashboard.app import _register_govcon_pages
    with patch("tools.dashboard.app.require_role", lambda *roles: lambda f: f):
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

    from _sql_compat import connect as _tconnect

    def _get_db():
        inner_conn = _tconnect(db_path)
        return inner_conn

    flask_app = Flask(__name__, template_folder=str(
        Path(__file__).parent.parent / "tools" / "dashboard" / "templates"
    ))
    flask_app.config["TESTING"] = True

    from tools.dashboard.app import _register_govcon_pages
    with patch("tools.dashboard.app.require_role", lambda *roles: lambda f: f):
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
    from flask import g
    from tools.dashboard.api.govcon import govcon_api

    flask_app = Flask(__name__)
    flask_app.config["TESTING"] = True

    @flask_app.before_request
    def _fake_auth():
        g.current_user = {"id": "test-user", "role": "admin"}

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
        from _sql_compat import connect as _tconnect

        def _fake_get_db():
            conn = _tconnect(db_path)
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
        from _sql_compat import connect as _tconnect

        def _fake_get_db():
            conn = _tconnect(db_path)
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
        from _sql_compat import connect as _tconnect

        def _fake_get_db():
            conn = _tconnect(db_path)
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


# ---------------------------------------------------------------------------
# Fake data: POST /api/govcon/opportunities/<id>/auto-compliance (gcpl-dft-03)
# ---------------------------------------------------------------------------

_FAKE_AUTO_COMPLIANCE_MATRIX = [
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

_FAKE_POPULATE_RESULT_OK = {
    "status": "ok",
    "opportunity_id": "opp-test-123",
    "total_requirements": 3,
    "L_compliant": 1,
    "M_partial": 1,
    "N_gap": 1,
    "compliance_rate": 0.3333,
    "matrix": _FAKE_AUTO_COMPLIANCE_MATRIX,
}

_FAKE_POPULATE_RESULT_NO_STMTS = {
    "status": "error",
    "message": "No shall statements for opportunity opp-empty",
}

_FAKE_POPULATE_RESULT_EMPTY_MATRIX = {
    "status": "ok",
    "opportunity_id": "opp-no-matrix",
    "total_requirements": 0,
    "L_compliant": 0,
    "M_partial": 0,
    "N_gap": 0,
    "compliance_rate": 0.0,
    "matrix": [],
}


def _make_mock_conn():
    """Return a MagicMock simulating a DB connection for the compliance write path."""
    from unittest.mock import MagicMock

    mock = MagicMock()
    # fetchone returns None → item doesn't exist yet → INSERT path runs
    mock.execute.return_value.fetchone.return_value = None
    return mock


# ---------------------------------------------------------------------------
# API tests: POST /api/govcon/opportunities/<id>/auto-compliance (gcpl-dft-03)
# ---------------------------------------------------------------------------


class TestAutoComplianceAPIEndpoint:
    """POST /api/govcon/opportunities/<id>/auto-compliance returns compliance matrix rows."""

    @pytest.fixture()
    def api_app(self):
        return _build_api_test_app()

    def _post(self, api_app, opp_id="opp-test-123", fake_result=None):
        if fake_result is None:
            fake_result = _FAKE_POPULATE_RESULT_OK
        mock_conn = _make_mock_conn()
        with patch(
            "tools.govcon.compliance_populator.populate_compliance_matrix",
            return_value=fake_result,
        ):
            with patch("tools.dashboard.api.govcon._get_db", return_value=mock_conn):
                with api_app.test_client() as c:
                    resp = c.post(f"/api/govcon/opportunities/{opp_id}/auto-compliance")
        return resp

    def test_post_returns_200(self, api_app):
        resp = self._post(api_app)
        assert resp.status_code == 200

    def test_response_content_type_is_json(self, api_app):
        resp = self._post(api_app)
        assert resp.content_type.startswith("application/json")

    def test_response_has_status_key(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        assert "status" in data

    def test_response_status_is_ok(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        assert data["status"] == "ok"

    def test_response_has_matrix_key(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        assert "matrix" in data

    def test_matrix_is_a_list(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        assert isinstance(data["matrix"], list)

    def test_matrix_is_non_empty(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        assert len(data["matrix"]) > 0

    def test_matrix_rows_have_grade_key(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        for row in data["matrix"]:
            assert "grade" in row, f"Matrix row missing 'grade': {row}"

    def test_matrix_grades_are_L_M_or_N(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        for row in data["matrix"]:
            assert row["grade"] in {"L", "M", "N"}, f"Invalid grade: {row['grade']}"

    def test_matrix_rows_have_coverage_score(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        for row in data["matrix"]:
            assert "coverage_score" in row, f"Matrix row missing 'coverage_score': {row}"

    def test_matrix_coverage_scores_are_between_0_and_1(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        for row in data["matrix"]:
            score = row["coverage_score"]
            assert 0.0 <= score <= 1.0, f"coverage_score {score!r} out of range"

    def test_matrix_rows_have_statement_key(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        for row in data["matrix"]:
            assert "statement" in row, f"Matrix row missing 'statement': {row}"

    def test_matrix_rows_have_domain_key(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        for row in data["matrix"]:
            assert "domain" in row, f"Matrix row missing 'domain': {row}"

    def test_l_grade_row_has_score_gte_080(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        l_rows = [r for r in data["matrix"] if r["grade"] == "L"]
        for row in l_rows:
            assert row["coverage_score"] >= 0.80, (
                f"L-grade row coverage_score below 0.80: {row['coverage_score']}"
            )

    def test_m_grade_row_has_score_between_040_and_080(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        m_rows = [r for r in data["matrix"] if r["grade"] == "M"]
        for row in m_rows:
            assert 0.40 <= row["coverage_score"] < 0.80, (
                f"M-grade row coverage_score {row['coverage_score']!r} out of [0.40, 0.80)"
            )

    def test_n_grade_row_has_score_below_040(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        n_rows = [r for r in data["matrix"] if r["grade"] == "N"]
        for row in n_rows:
            assert row["coverage_score"] < 0.40, (
                f"N-grade row coverage_score {row['coverage_score']!r} not below 0.40"
            )

    def test_response_has_total_requirements(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        assert "total_requirements" in data

    def test_response_has_L_compliant(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        assert "L_compliant" in data

    def test_response_has_M_partial(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        assert "M_partial" in data

    def test_response_has_N_gap(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        assert "N_gap" in data

    def test_response_has_compliance_rate(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        assert "compliance_rate" in data

    def test_compliance_rate_is_between_0_and_1(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        rate = data["compliance_rate"]
        assert 0.0 <= rate <= 1.0, f"compliance_rate {rate!r} out of range [0.0, 1.0]"

    def test_grade_counts_sum_to_total_requirements(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        total = data["total_requirements"]
        assert data["L_compliant"] + data["M_partial"] + data["N_gap"] == total, (
            f"Grade counts don't sum to total_requirements: "
            f"L={data['L_compliant']} M={data['M_partial']} N={data['N_gap']} total={total}"
        )

    def test_response_has_compliance_items_created(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        assert "compliance_items_created" in data, (
            "Response missing 'compliance_items_created' — endpoint should set this after DB write"
        )

    def test_compliance_items_created_is_integer(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        created = data.get("compliance_items_created")
        assert isinstance(created, int) and not isinstance(created, bool), (
            f"compliance_items_created must be int, got {type(created).__name__}: {created!r}"
        )

    def test_compliance_items_created_is_non_negative(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        assert data["compliance_items_created"] >= 0

    def test_matrix_length_matches_total_requirements(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        assert len(data["matrix"]) == data["total_requirements"], (
            f"matrix length {len(data['matrix'])} != total_requirements {data['total_requirements']}"
        )

    def test_endpoint_accepts_arbitrary_opp_id(self, api_app):
        for opp_id in ("abc-123", "uuid-9999", "opportunity-xyz"):
            resp = self._post(api_app, opp_id=opp_id)
            assert resp.status_code == 200, f"Expected 200 for opp_id={opp_id!r}"

    def test_returns_500_when_populate_raises(self, api_app):
        with patch(
            "tools.govcon.compliance_populator.populate_compliance_matrix",
            side_effect=RuntimeError("populator offline"),
        ):
            with api_app.test_client() as c:
                resp = c.post("/api/govcon/opportunities/opp-err/auto-compliance")
        assert resp.status_code == 500

    def test_500_response_has_error_key(self, api_app):
        with patch(
            "tools.govcon.compliance_populator.populate_compliance_matrix",
            side_effect=RuntimeError("populator offline"),
        ):
            with api_app.test_client() as c:
                resp = c.post("/api/govcon/opportunities/opp-err/auto-compliance")
        data = resp.get_json()
        assert "error" in data

    def test_no_shall_statements_returns_200_with_status_error(self, api_app):
        """Endpoint passes through populate_compliance_matrix error status unchanged."""
        with patch(
            "tools.govcon.compliance_populator.populate_compliance_matrix",
            return_value=_FAKE_POPULATE_RESULT_NO_STMTS,
        ):
            with api_app.test_client() as c:
                resp = c.post("/api/govcon/opportunities/opp-empty/auto-compliance")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "error"

    def test_empty_matrix_returns_200(self, api_app):
        mock_conn = _make_mock_conn()
        with patch(
            "tools.govcon.compliance_populator.populate_compliance_matrix",
            return_value=_FAKE_POPULATE_RESULT_EMPTY_MATRIX,
        ):
            with patch("tools.dashboard.api.govcon._get_db", return_value=mock_conn):
                with api_app.test_client() as c:
                    resp = c.post("/api/govcon/opportunities/opp-no-matrix/auto-compliance")
        assert resp.status_code == 200

    def test_empty_matrix_has_zero_compliance_items_created(self, api_app):
        """Empty matrix skips the DB write loop; compliance_items_created should be absent or 0."""
        mock_conn = _make_mock_conn()
        with patch(
            "tools.govcon.compliance_populator.populate_compliance_matrix",
            return_value=_FAKE_POPULATE_RESULT_EMPTY_MATRIX,
        ):
            with patch("tools.dashboard.api.govcon._get_db", return_value=mock_conn):
                with api_app.test_client() as c:
                    resp = c.post("/api/govcon/opportunities/opp-no-matrix/auto-compliance")
        data = resp.get_json()
        created = data.get("compliance_items_created", 0)
        assert created == 0, f"Empty matrix should yield compliance_items_created=0, got {created!r}"

    def test_matrix_row_statement_is_non_empty_string(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        for row in data["matrix"]:
            stmt = row["statement"]
            assert isinstance(stmt, str) and stmt, (
                f"Matrix row statement must be non-empty string, got {stmt!r}"
            )

    def test_matrix_row_domain_is_non_empty_string(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        for row in data["matrix"]:
            domain = row["domain"]
            assert isinstance(domain, str) and domain, (
                f"Matrix row domain must be non-empty string, got {domain!r}"
            )

    def test_response_has_opportunity_id(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        assert "opportunity_id" in data

    def test_opportunity_id_matches_url_param(self, api_app):
        resp = self._post(api_app, opp_id="opp-test-123")
        data = resp.get_json()
        assert data["opportunity_id"] == "opp-test-123"


# Fake data: POST /api/govcon/opportunities/<id>/auto-draft (gcpl-dft-04)
# ---------------------------------------------------------------------------

_FAKE_DRAFT_RESULTS = [
    {
        "status": "ok",
        "draft_id": "draft-uuid-1",
        "shall_id": "shall-1",
        "method": "template",
        "confidence": 0.85,
        "best_coverage": 0.92,
        "capabilities_matched": 3,
        "kb_blocks_used": 2,
        "draft_length": 412,
    },
    {
        "status": "ok",
        "draft_id": "draft-uuid-2",
        "shall_id": "shall-2",
        "method": "template",
        "confidence": 0.72,
        "best_coverage": 0.61,
        "capabilities_matched": 2,
        "kb_blocks_used": 1,
        "draft_length": 388,
    },
    {
        "status": "ok",
        "draft_id": "draft-uuid-3",
        "shall_id": "shall-3",
        "method": "template",
        "confidence": 0.55,
        "best_coverage": 0.25,
        "capabilities_matched": 1,
        "kb_blocks_used": 0,
        "draft_length": 310,
    },
]

_FAKE_DRAFT_ALL_RESULT_OK = {
    "status": "ok",
    "opportunity_id": "opp-test-123",
    "total_statements": 3,
    "drafted": 3,
    "avg_confidence": 0.71,
    "results": _FAKE_DRAFT_RESULTS,
}

_FAKE_DRAFT_ALL_RESULT_NO_STMTS = {
    "status": "error",
    "message": "No shall statements for opp-empty",
}

_FAKE_DRAFT_ALL_RESULT_EMPTY = {
    "status": "ok",
    "opportunity_id": "opp-no-drafts",
    "total_statements": 0,
    "drafted": 0,
    "avg_confidence": 0.0,
    "results": [],
}


# ---------------------------------------------------------------------------
# API tests: POST /api/govcon/opportunities/<id>/auto-draft (gcpl-dft-04)
# ---------------------------------------------------------------------------


class TestAutoDraftAPIEndpoint:
    """POST /api/govcon/opportunities/<id>/auto-draft returns draft section list."""

    @pytest.fixture()
    def api_app(self):
        return _build_api_test_app()

    def _post(self, api_app, opp_id="opp-test-123", fake_result=None, body=None):
        if fake_result is None:
            fake_result = _FAKE_DRAFT_ALL_RESULT_OK
        with patch(
            "tools.govcon.response_drafter.draft_all_for_opportunity",
            return_value=fake_result,
        ):
            with api_app.test_client() as c:
                resp = c.post(
                    f"/api/govcon/opportunities/{opp_id}/auto-draft",
                    json=body,
                    content_type="application/json",
                )
        return resp

    def test_post_returns_200(self, api_app):
        resp = self._post(api_app)
        assert resp.status_code == 200

    def test_response_content_type_is_json(self, api_app):
        resp = self._post(api_app)
        assert resp.content_type.startswith("application/json")

    def test_response_has_status_key(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        assert "status" in data

    def test_response_status_is_ok(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        assert data["status"] == "ok"

    def test_response_has_opportunity_id(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        assert "opportunity_id" in data

    def test_opportunity_id_matches_url_param(self, api_app):
        resp = self._post(api_app, opp_id="opp-test-123")
        data = resp.get_json()
        assert data["opportunity_id"] == "opp-test-123"

    def test_response_has_total_statements(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        assert "total_statements" in data

    def test_total_statements_is_non_negative_integer(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        total = data["total_statements"]
        assert isinstance(total, int) and not isinstance(total, bool)
        assert total >= 0

    def test_response_has_drafted(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        assert "drafted" in data

    def test_drafted_is_non_negative_integer(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        drafted = data["drafted"]
        assert isinstance(drafted, int) and not isinstance(drafted, bool)
        assert drafted >= 0

    def test_drafted_does_not_exceed_total_statements(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        assert data["drafted"] <= data["total_statements"], (
            f"drafted={data['drafted']} exceeds total_statements={data['total_statements']}"
        )

    def test_response_has_avg_confidence(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        assert "avg_confidence" in data

    def test_avg_confidence_is_numeric(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        avg = data["avg_confidence"]
        assert isinstance(avg, (int, float)) and not isinstance(avg, bool), (
            f"avg_confidence must be numeric, got {type(avg).__name__}: {avg!r}"
        )

    def test_avg_confidence_is_between_0_and_1(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        avg = data["avg_confidence"]
        assert 0.0 <= avg <= 1.0, f"avg_confidence {avg!r} out of range [0.0, 1.0]"

    def test_response_has_results(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        assert "results" in data

    def test_results_is_a_list(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        assert isinstance(data["results"], list)

    def test_results_length_matches_total_statements(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        assert len(data["results"]) == data["total_statements"], (
            f"results length {len(data['results'])} != total_statements {data['total_statements']}"
        )

    def test_each_result_has_status(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        for i, r in enumerate(data["results"]):
            assert "status" in r, f"results[{i}] missing 'status'"

    def test_each_result_has_draft_id(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        for i, r in enumerate(data["results"]):
            assert "draft_id" in r, f"results[{i}] missing 'draft_id'"

    def test_each_result_draft_id_is_non_empty_string(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        for i, r in enumerate(data["results"]):
            assert isinstance(r["draft_id"], str) and r["draft_id"], (
                f"results[{i}]['draft_id'] must be non-empty string, got {r['draft_id']!r}"
            )

    def test_each_result_has_shall_id(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        for i, r in enumerate(data["results"]):
            assert "shall_id" in r, f"results[{i}] missing 'shall_id'"

    def test_each_result_has_method(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        for i, r in enumerate(data["results"]):
            assert "method" in r, f"results[{i}] missing 'method'"

    def test_each_result_method_is_valid_string(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        valid_methods = {"auto", "template", "llm"}
        for i, r in enumerate(data["results"]):
            assert r["method"] in valid_methods, (
                f"results[{i}]['method']={r['method']!r} not in {valid_methods}"
            )

    def test_each_result_has_confidence(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        for i, r in enumerate(data["results"]):
            assert "confidence" in r, f"results[{i}] missing 'confidence'"

    def test_each_result_confidence_is_float_in_range(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        for i, r in enumerate(data["results"]):
            c = r["confidence"]
            assert isinstance(c, (int, float)) and not isinstance(c, bool), (
                f"results[{i}]['confidence'] must be numeric, got {type(c).__name__}: {c!r}"
            )
            assert 0.0 <= c <= 1.0, (
                f"results[{i}]['confidence']={c!r} out of range [0.0, 1.0]"
            )

    def test_each_result_has_draft_length(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        for i, r in enumerate(data["results"]):
            assert "draft_length" in r, f"results[{i}] missing 'draft_length'"

    def test_each_result_draft_length_is_positive_integer(self, api_app):
        resp = self._post(api_app)
        data = resp.get_json()
        for i, r in enumerate(data["results"]):
            dl = r["draft_length"]
            assert isinstance(dl, int) and not isinstance(dl, bool) and dl > 0, (
                f"results[{i}]['draft_length'] must be positive int, got {dl!r}"
            )

    def test_endpoint_accepts_arbitrary_opp_id(self, api_app):
        for opp_id in ("abc-123", "uuid-9999", "opportunity-xyz"):
            resp = self._post(api_app, opp_id=opp_id)
            assert resp.status_code == 200, f"Expected 200 for opp_id={opp_id!r}"

    def test_method_param_defaults_to_auto(self, api_app):
        captured = {}

        def _capture(opp_id, method="auto"):
            captured["method"] = method
            return _FAKE_DRAFT_ALL_RESULT_OK

        with patch(
            "tools.govcon.response_drafter.draft_all_for_opportunity",
            side_effect=_capture,
        ):
            with api_app.test_client() as c:
                c.post("/api/govcon/opportunities/opp-test-123/auto-draft")
        assert captured.get("method") == "auto"

    def test_method_param_passed_from_request_body(self, api_app):
        captured = {}

        def _capture(opp_id, method="auto"):
            captured["method"] = method
            return _FAKE_DRAFT_ALL_RESULT_OK

        with patch(
            "tools.govcon.response_drafter.draft_all_for_opportunity",
            side_effect=_capture,
        ):
            with api_app.test_client() as c:
                c.post(
                    "/api/govcon/opportunities/opp-test-123/auto-draft",
                    json={"method": "template"},
                    content_type="application/json",
                )
        assert captured.get("method") == "template"

    def test_returns_500_when_drafter_raises(self, api_app):
        with patch(
            "tools.govcon.response_drafter.draft_all_for_opportunity",
            side_effect=RuntimeError("drafter offline"),
        ):
            with api_app.test_client() as c:
                resp = c.post("/api/govcon/opportunities/opp-err/auto-draft")
        assert resp.status_code == 500

    def test_500_response_has_error_key(self, api_app):
        with patch(
            "tools.govcon.response_drafter.draft_all_for_opportunity",
            side_effect=RuntimeError("drafter offline"),
        ):
            with api_app.test_client() as c:
                resp = c.post("/api/govcon/opportunities/opp-err/auto-draft")
        data = resp.get_json()
        assert "error" in data

    def test_no_shall_statements_returns_200_with_status_error(self, api_app):
        resp = self._post(api_app, opp_id="opp-empty", fake_result=_FAKE_DRAFT_ALL_RESULT_NO_STMTS)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "error"

    def test_empty_results_returns_200(self, api_app):
        resp = self._post(api_app, opp_id="opp-no-drafts", fake_result=_FAKE_DRAFT_ALL_RESULT_EMPTY)
        assert resp.status_code == 200

    def test_empty_results_has_zero_drafted(self, api_app):
        resp = self._post(api_app, opp_id="opp-no-drafts", fake_result=_FAKE_DRAFT_ALL_RESULT_EMPTY)
        data = resp.get_json()
        assert data.get("drafted", 0) == 0

    def test_empty_results_list_is_empty(self, api_app):
        resp = self._post(api_app, opp_id="opp-no-drafts", fake_result=_FAKE_DRAFT_ALL_RESULT_EMPTY)
        data = resp.get_json()
        assert data.get("results", []) == []


# ---------------------------------------------------------------------------
# DB schema and helpers: GET /api/govcon/opportunities/<id>/drafts (gcpl-dft-05)
# ---------------------------------------------------------------------------

_DRAFT_LIST_SCHEMA = """
CREATE TABLE IF NOT EXISTS proposal_section_drafts (
    id TEXT PRIMARY KEY,
    section_id TEXT,
    opportunity_id TEXT NOT NULL,
    shall_statement_id TEXT,
    capability_ids TEXT DEFAULT '[]',
    knowledge_block_ids TEXT DEFAULT '[]',
    draft_content TEXT NOT NULL,
    draft_method TEXT,
    confidence_score REAL DEFAULT 0.0,
    confidence REAL DEFAULT 0.0,
    domain_category TEXT,
    generation_model TEXT,
    status TEXT NOT NULL DEFAULT 'draft',
    reviewed_by TEXT,
    reviewed_at TEXT,
    review_notes TEXT,
    reviewer_notes TEXT,
    metadata TEXT DEFAULT '{}',
    created_at TEXT NOT NULL,
    classification TEXT
);

CREATE TABLE IF NOT EXISTS rfp_shall_statements (
    id TEXT PRIMARY KEY,
    sam_opportunity_id TEXT,
    statement_text TEXT,
    domain_category TEXT,
    statement_type TEXT,
    keywords TEXT,
    created_at TEXT
);
"""

_LIST_DRAFTS_SHALLS = [
    {
        "id": "shall-list-1",
        "statement_text": "The system shall implement zero-trust networking controls.",
        "domain_category": "devsecops",
    },
    {
        "id": "shall-list-2",
        "statement_text": "The system shall encrypt all data at rest using FIPS 140-2 algorithms.",
        "domain_category": "security",
    },
]

# Three drafts for opp-test-123; one for a different opp; third has no shall link.
# Timestamps descend so the query ORDER BY created_at DESC yields ld-draft-1 first.
_LIST_DRAFTS_ROWS = [
    {
        "id": "ld-draft-1",
        "opportunity_id": "opp-test-123",
        "shall_statement_id": "shall-list-1",
        "draft_content": "We provide zero-trust networking via our ZTA platform.",
        "draft_method": "template",
        "confidence_score": 0.85,
        "status": "draft",
        "created_at": "2026-05-20T12:00:00",
    },
    {
        "id": "ld-draft-2",
        "opportunity_id": "opp-test-123",
        "shall_statement_id": "shall-list-2",
        "draft_content": "Our team implements FIPS 140-2 compliant encryption at rest.",
        "draft_method": "template",
        "confidence_score": 0.72,
        "status": "approved",
        "created_at": "2026-05-20T11:00:00",
    },
    {
        "id": "ld-draft-3",
        "opportunity_id": "opp-test-123",
        "shall_statement_id": None,
        "draft_content": "Supply chain risk management approach.",
        "draft_method": "llm",
        "confidence_score": 0.55,
        "status": "rejected",
        "created_at": "2026-05-20T10:00:00",
    },
    {
        "id": "ld-draft-4",
        "opportunity_id": "opp-other",
        "shall_statement_id": None,
        "draft_content": "Other opportunity draft content.",
        "draft_method": "auto",
        "confidence_score": 0.60,
        "status": "draft",
        "created_at": "2026-05-20T09:00:00",
    },
]


def _make_drafts_db(tmp_path, draft_rows=None, shall_rows=None):
    """Create a seeded SQLite DB for list_drafts endpoint tests."""
    if draft_rows is None:
        draft_rows = _LIST_DRAFTS_ROWS
    if shall_rows is None:
        shall_rows = _LIST_DRAFTS_SHALLS

    db_path = tmp_path / "drafts_list_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_DRAFT_LIST_SCHEMA)

    for s in shall_rows:
        conn.execute(
            "INSERT INTO rfp_shall_statements (id, statement_text, domain_category) VALUES (?, ?, ?)",
            (s["id"], s["statement_text"], s.get("domain_category")),
        )

    for d in draft_rows:
        conn.execute(
            """INSERT INTO proposal_section_drafts
               (id, opportunity_id, shall_statement_id, draft_content,
                draft_method, confidence_score, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                d["id"],
                d["opportunity_id"],
                d.get("shall_statement_id"),
                d["draft_content"],
                d.get("draft_method"),
                d.get("confidence_score", 0.0),
                d.get("status", "draft"),
                d["created_at"],
            ),
        )

    conn.commit()
    conn.close()
    return db_path


def _build_drafts_list_api_app(tmp_path):
    """Return (flask_app, fake_get_db) for testing GET /api/govcon/opportunities/<id>/drafts."""
    db_path = _make_drafts_db(tmp_path)

    from _sql_compat import connect as _tconnect

    def _fake_get_db():
        conn = _tconnect(db_path)
        return conn

    from tools.dashboard.api.govcon import govcon_api

    flask_app = Flask(__name__)
    flask_app.config["TESTING"] = True
    flask_app.register_blueprint(govcon_api)

    return flask_app, _fake_get_db


# ---------------------------------------------------------------------------
# API tests: GET /api/govcon/opportunities/<id>/drafts (gcpl-dft-05)
# ---------------------------------------------------------------------------


class TestListDraftsAPIEndpoint:
    """GET /api/govcon/opportunities/<id>/drafts returns draft records."""

    @pytest.fixture()
    def api_app(self, tmp_path):
        return _build_drafts_list_api_app(tmp_path)

    def _get(self, api_app_pair, opp_id="opp-test-123", params=None):
        flask_app, fake_get_db = api_app_pair
        with patch("tools.dashboard.api.govcon._get_db", side_effect=fake_get_db):
            with flask_app.test_client() as c:
                resp = c.get(
                    f"/api/govcon/opportunities/{opp_id}/drafts",
                    query_string=params or {},
                )
        return resp

    def test_get_returns_200(self, api_app):
        resp = self._get(api_app)
        assert resp.status_code == 200

    def test_response_content_type_is_json(self, api_app):
        resp = self._get(api_app)
        assert resp.content_type.startswith("application/json")

    def test_response_has_drafts_key(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        assert "drafts" in data

    def test_drafts_is_a_list(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        assert isinstance(data["drafts"], list)

    def test_response_has_total_key(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        assert "total" in data

    def test_total_is_non_negative_integer(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        total = data["total"]
        assert isinstance(total, int) and not isinstance(total, bool)
        assert total >= 0

    def test_total_equals_len_of_drafts(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        assert data["total"] == len(data["drafts"])

    def test_seeded_opp_returns_expected_count(self, api_app):
        resp = self._get(api_app, opp_id="opp-test-123")
        data = resp.get_json()
        assert data["total"] == 3

    def test_returns_only_drafts_for_given_opp_id(self, api_app):
        resp = self._get(api_app, opp_id="opp-test-123")
        data = resp.get_json()
        for d in data["drafts"]:
            assert d["opportunity_id"] == "opp-test-123"

    def test_unknown_opp_id_returns_empty_drafts_list(self, api_app):
        resp = self._get(api_app, opp_id="opp-does-not-exist")
        data = resp.get_json()
        assert data["drafts"] == []

    def test_unknown_opp_id_returns_total_zero(self, api_app):
        resp = self._get(api_app, opp_id="opp-does-not-exist")
        data = resp.get_json()
        assert data["total"] == 0

    def test_returns_200_for_unknown_opp_id(self, api_app):
        resp = self._get(api_app, opp_id="opp-completely-empty")
        assert resp.status_code == 200

    def test_each_draft_has_id(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        for i, d in enumerate(data["drafts"]):
            assert "id" in d, f"drafts[{i}] missing 'id'"

    def test_each_draft_has_opportunity_id(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        for i, d in enumerate(data["drafts"]):
            assert "opportunity_id" in d, f"drafts[{i}] missing 'opportunity_id'"

    def test_each_draft_has_draft_content(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        for i, d in enumerate(data["drafts"]):
            assert "draft_content" in d, f"drafts[{i}] missing 'draft_content'"

    def test_each_draft_content_is_non_empty_string(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        for i, d in enumerate(data["drafts"]):
            assert isinstance(d["draft_content"], str) and d["draft_content"], (
                f"drafts[{i}]['draft_content'] must be non-empty string"
            )

    def test_each_draft_has_status(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        for i, d in enumerate(data["drafts"]):
            assert "status" in d, f"drafts[{i}] missing 'status'"

    def test_each_draft_status_is_valid(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        valid_statuses = {"draft", "reviewed", "approved", "rejected"}
        for i, d in enumerate(data["drafts"]):
            assert d["status"] in valid_statuses, (
                f"drafts[{i}]['status']={d['status']!r} not in {valid_statuses}"
            )

    def test_each_draft_has_created_at(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        for i, d in enumerate(data["drafts"]):
            assert "created_at" in d, f"drafts[{i}] missing 'created_at'"

    def test_shall_linked_draft_has_shall_text(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        shall_drafts = [d for d in data["drafts"] if d.get("shall_statement_id")]
        assert shall_drafts, "Expected at least one draft with shall_statement_id"
        for d in shall_drafts:
            assert "shall_text" in d, (
                f"Draft {d['id']} with shall_statement_id missing 'shall_text'"
            )

    def test_shall_linked_draft_has_domain(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        shall_drafts = [d for d in data["drafts"] if d.get("shall_statement_id")]
        assert shall_drafts, "Expected at least one draft with shall_statement_id"
        for d in shall_drafts:
            assert "domain" in d, (
                f"Draft {d['id']} with shall_statement_id missing 'domain'"
            )

    def test_shall_text_matches_seeded_statement(self, api_app):
        resp = self._get(api_app, opp_id="opp-test-123")
        data = resp.get_json()
        draft1 = next((d for d in data["drafts"] if d["id"] == "ld-draft-1"), None)
        assert draft1 is not None, "Expected draft ld-draft-1 in results"
        assert draft1["shall_text"] == "The system shall implement zero-trust networking controls."

    def test_shall_domain_matches_seeded_shall(self, api_app):
        resp = self._get(api_app, opp_id="opp-test-123")
        data = resp.get_json()
        draft1 = next((d for d in data["drafts"] if d["id"] == "ld-draft-1"), None)
        assert draft1 is not None, "Expected draft ld-draft-1 in results"
        assert draft1["domain"] == "devsecops"

    def test_status_filter_returns_only_draft_status(self, api_app):
        resp = self._get(api_app, params={"status": "draft"})
        data = resp.get_json()
        for d in data["drafts"]:
            assert d["status"] == "draft"

    def test_status_filter_approved_returns_only_approved(self, api_app):
        resp = self._get(api_app, params={"status": "approved"})
        data = resp.get_json()
        for d in data["drafts"]:
            assert d["status"] == "approved"

    def test_status_filter_total_matches_filtered_count(self, api_app):
        resp = self._get(api_app, params={"status": "draft"})
        data = resp.get_json()
        assert data["total"] == len(data["drafts"])

    def test_status_filter_unknown_returns_empty(self, api_app):
        resp = self._get(api_app, params={"status": "nonexistent_status"})
        data = resp.get_json()
        assert data["drafts"] == []
        assert data["total"] == 0

    def test_results_ordered_newest_first(self, api_app):
        resp = self._get(api_app, opp_id="opp-test-123")
        data = resp.get_json()
        created_ats = [d["created_at"] for d in data["drafts"]]
        assert created_ats == sorted(created_ats, reverse=True), (
            f"Drafts not in descending created_at order: {created_ats}"
        )

    def test_other_opp_drafts_excluded(self, api_app):
        resp = self._get(api_app, opp_id="opp-test-123")
        data = resp.get_json()
        ids = {d["id"] for d in data["drafts"]}
        assert "ld-draft-4" not in ids, (
            "Draft from 'opp-other' must not appear in 'opp-test-123' results"
        )

    def test_accepts_arbitrary_opp_id(self, api_app):
        for opp_id in ("abc-001", "uuid-xyz", "opp-9999"):
            resp = self._get(api_app, opp_id=opp_id)
            assert resp.status_code == 200, f"Expected 200 for opp_id={opp_id!r}"


# ---------------------------------------------------------------------------
# DB helpers: drafts with metadata.best_coverage seeded (gcpl-dft-06)
# ---------------------------------------------------------------------------

import json as _json

_DRAFT_SCHEMA_WITH_METADATA = _DRAFT_LIST_SCHEMA  # reuse — metadata column already declared

_QUALITY_DRAFT_ROWS = [
    {
        "id": "qd-draft-1",
        "opportunity_id": "opp-quality-test",
        "shall_statement_id": "shall-list-1",
        "draft_content": "We provide zero-trust networking via our ZTA platform.",
        "draft_method": "template",
        "confidence_score": 0.85,
        "status": "draft",
        "created_at": "2026-05-20T12:00:00",
        "metadata": _json.dumps({"best_coverage": 0.92, "capability_count": 3, "kb_count": 2}),
    },
    {
        "id": "qd-draft-2",
        "opportunity_id": "opp-quality-test",
        "shall_statement_id": "shall-list-2",
        "draft_content": "Our team implements FIPS 140-2 compliant encryption at rest.",
        "draft_method": "template",
        "confidence_score": 0.60,
        "status": "draft",
        "created_at": "2026-05-20T11:00:00",
        "metadata": _json.dumps({"best_coverage": 0.61, "capability_count": 2, "kb_count": 1}),
    },
    {
        "id": "qd-draft-3",
        "opportunity_id": "opp-quality-test",
        "shall_statement_id": None,
        "draft_content": "Supply chain risk management approach.",
        "draft_method": "llm",
        "confidence_score": 0.0,
        "status": "draft",
        "created_at": "2026-05-20T10:00:00",
        "metadata": _json.dumps({"best_coverage": 0.0, "capability_count": 0, "kb_count": 0}),
    },
]


def _make_quality_drafts_db(tmp_path, draft_rows=None, shall_rows=None):
    """Create a seeded DB for quality_score validation tests."""
    if draft_rows is None:
        draft_rows = _QUALITY_DRAFT_ROWS
    if shall_rows is None:
        shall_rows = _LIST_DRAFTS_SHALLS

    db_path = tmp_path / "quality_drafts_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_DRAFT_SCHEMA_WITH_METADATA)

    for s in shall_rows:
        conn.execute(
            "INSERT INTO rfp_shall_statements (id, statement_text, domain_category) VALUES (?, ?, ?)",
            (s["id"], s["statement_text"], s.get("domain_category")),
        )

    for d in draft_rows:
        conn.execute(
            """INSERT INTO proposal_section_drafts
               (id, opportunity_id, shall_statement_id, draft_content,
                draft_method, confidence_score, status, created_at, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                d["id"],
                d["opportunity_id"],
                d.get("shall_statement_id"),
                d["draft_content"],
                d.get("draft_method"),
                d.get("confidence_score", 0.0),
                d.get("status", "draft"),
                d["created_at"],
                d.get("metadata", "{}"),
            ),
        )

    conn.commit()
    conn.close()
    return db_path


def _build_quality_drafts_api_app(tmp_path):
    """Return (flask_app, fake_get_db) for quality_score endpoint tests."""
    db_path = _make_quality_drafts_db(tmp_path)

    from _sql_compat import connect as _tconnect

    def _fake_get_db():
        conn = _tconnect(db_path)
        return conn

    from tools.dashboard.api.govcon import govcon_api

    flask_app = Flask(__name__)
    flask_app.config["TESTING"] = True
    flask_app.register_blueprint(govcon_api)

    return flask_app, _fake_get_db


# ---------------------------------------------------------------------------
# Tests: draft records have status='draft' by default (gcpl-dft-06)
# ---------------------------------------------------------------------------


class TestDraftDefaultStatus:
    """Newly stored draft records must have status='draft' by default."""

    def test_db_schema_default_status_is_draft(self, tmp_path):
        """DB schema DEFAULT 'draft' must produce status='draft' on INSERT without status."""
        db_path = tmp_path / "schema_default.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.executescript(_DRAFT_LIST_SCHEMA)
        draft_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO proposal_section_drafts "
            "(id, opportunity_id, draft_content, created_at) VALUES (?, ?, ?, ?)",
            (draft_id, "opp-default-test", "content", "2026-05-20T00:00:00"),
        )
        conn.commit()
        row = conn.execute(
            "SELECT status FROM proposal_section_drafts WHERE id = ?", (draft_id,)
        ).fetchone()
        conn.close()
        assert row["status"] == "draft", (
            f"DB DEFAULT 'draft' not applied — got {row['status']!r}"
        )

    def test_seeded_new_drafts_have_status_draft(self, tmp_path):
        """All seeded _QUALITY_DRAFT_ROWS have status='draft'."""
        for d in _QUALITY_DRAFT_ROWS:
            assert d["status"] == "draft", (
                f"Draft {d['id']} has status={d['status']!r}, expected 'draft'"
            )

    def test_api_returns_status_field_on_each_draft(self, tmp_path):
        """GET /api/govcon/opportunities/<id>/drafts — every draft record exposes status."""
        flask_app, fake_get_db = _build_quality_drafts_api_app(tmp_path)
        with patch("tools.dashboard.api.govcon._get_db", side_effect=fake_get_db):
            with flask_app.test_client() as c:
                resp = c.get("/api/govcon/opportunities/opp-quality-test/drafts")
        data = resp.get_json()
        for i, d in enumerate(data["drafts"]):
            assert "status" in d, f"drafts[{i}] missing 'status'"

    def test_api_new_drafts_all_have_draft_status(self, tmp_path):
        """All seeded drafts (all status='draft') are returned with status='draft'."""
        flask_app, fake_get_db = _build_quality_drafts_api_app(tmp_path)
        with patch("tools.dashboard.api.govcon._get_db", side_effect=fake_get_db):
            with flask_app.test_client() as c:
                resp = c.get(
                    "/api/govcon/opportunities/opp-quality-test/drafts",
                    query_string={"status": "draft"},
                )
        data = resp.get_json()
        assert data["total"] == 3, f"Expected 3 draft-status records, got {data['total']}"
        for d in data["drafts"]:
            assert d["status"] == "draft", f"Expected status='draft', got {d['status']!r}"

    def test_status_is_string_not_none(self, tmp_path):
        """draft status field must be a non-None string."""
        flask_app, fake_get_db = _build_quality_drafts_api_app(tmp_path)
        with patch("tools.dashboard.api.govcon._get_db", side_effect=fake_get_db):
            with flask_app.test_client() as c:
                resp = c.get("/api/govcon/opportunities/opp-quality-test/drafts")
        data = resp.get_json()
        for d in data["drafts"]:
            assert isinstance(d["status"], str) and d["status"], (
                f"status must be non-empty string, got {d['status']!r}"
            )

    def test_status_is_in_valid_set(self, tmp_path):
        """draft status must be one of the recognised lifecycle values."""
        flask_app, fake_get_db = _build_quality_drafts_api_app(tmp_path)
        with patch("tools.dashboard.api.govcon._get_db", side_effect=fake_get_db):
            with flask_app.test_client() as c:
                resp = c.get("/api/govcon/opportunities/opp-quality-test/drafts")
        data = resp.get_json()
        valid = {"draft", "reviewed", "approved", "rejected"}
        for d in data["drafts"]:
            assert d["status"] in valid, (
                f"status {d['status']!r} not in valid set {valid}"
            )

    def test_response_drafter_stores_draft_status(self, tmp_path):
        """draft_response() must INSERT with status='draft' (verified via DB read-back)."""
        db_path = tmp_path / "drafter_status.db"
        conn = sqlite3.connect(str(db_path))
        _DRAFTER_SCHEMA = _DRAFT_LIST_SCHEMA.replace(
            "created_at TEXT NOT NULL",
            "created_at TEXT NOT NULL, updated_at TEXT",
        )
        conn.executescript(_DRAFTER_SCHEMA + """
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
""")
        shall_id = str(uuid.uuid4())
        opp_id = "opp-drafter-test"
        conn.execute(
            "INSERT INTO rfp_shall_statements "
            "(id, sam_opportunity_id, statement_text, domain_category, keywords, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (shall_id, opp_id, "The system shall implement ZTA.", "devsecops", "[]", "2026-01-01T00:00:00"),
        )
        conn.commit()
        conn.close()

        from _sql_compat import connect as _tconnect

        def _fake_conn():
            c = _tconnect(db_path)
            c.execute("PRAGMA journal_mode=WAL")
            return c

        with patch("tools.govcon.response_drafter._get_db", side_effect=_fake_conn):
            with patch("tools.govcon.response_drafter._try_llm_draft", return_value=(None, None)):
                with patch(
                    "tools.govcon.capability_mapper.load_capability_catalog",
                    return_value=[],
                ):
                    with patch(
                        "tools.govcon.knowledge_base.search_blocks",
                        return_value={"results": []},
                    ):
                        from tools.govcon.response_drafter import draft_response

                        result = draft_response(shall_id)

        assert result.get("status") == "ok", f"draft_response failed: {result}"

        verify_conn = sqlite3.connect(str(db_path))
        verify_conn.row_factory = sqlite3.Row
        row = verify_conn.execute(
            "SELECT status FROM proposal_section_drafts WHERE id = ?",
            (result["draft_id"],),
        ).fetchone()
        verify_conn.close()
        assert row is not None, "Draft row was not written to DB"
        assert row["status"] == "draft", (
            f"draft_response must store status='draft', got {row['status']!r}"
        )


# ---------------------------------------------------------------------------
# Tests: optional Specialist Council consult wiring (idea_lab reverse
# integration, off-by-default via ICDEV_PROPOSAL_SPECIALIST_CONSULT_ENABLED)
# ---------------------------------------------------------------------------


class _FakeDraftCursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeDraftConn:
    """Lightweight, DB-free stand-in for draft_response()'s `conn` -- avoids
    the pre-existing %s/sqlite3 placeholder mismatch that already fails
    TestDraftDefaultStatus's real-sqlite fixture in this environment
    (response_drafter.py's SQL is authored for PostgreSQL, per CLAUDE.md;
    a raw sqlite3.connect() has no %s support). This fake only needs to
    answer the one SELECT and capture the one INSERT draft_response() does."""

    def __init__(self, shall_row):
        self._shall_row = shall_row
        self.inserted_params = None

    def execute(self, sql, params=None):
        if "SELECT * FROM rfp_shall_statements" in sql:
            return _FakeDraftCursor(dict(self._shall_row))
        if "INSERT INTO proposal_section_drafts" in sql:
            self.inserted_params = params
        return _FakeDraftCursor(None)

    def commit(self):
        pass

    def close(self):
        pass


class TestSpecialistConsultWiring:
    """draft_response() must only call out to idea_lab's Specialist when
    explicitly enabled, and a consult failure must never break drafting."""

    def _draft_via(self, shall_id="shall-specialist-test"):
        shall_row = {
            "id": shall_id,
            "sam_opportunity_id": "opp-specialist-test",
            "statement_text": "The system shall implement ZTA.",
            "domain_category": "devsecops",
            "keywords": "[]",
        }
        fake_conn = _FakeDraftConn(shall_row)

        with patch("tools.govcon.response_drafter._get_db", return_value=fake_conn):
            with patch("tools.govcon.response_drafter._try_llm_draft", return_value=(None, None)):
                with patch("tools.govcon.capability_mapper.load_capability_catalog", return_value=[]):
                    with patch("tools.govcon.knowledge_base.search_blocks", return_value={"results": []}):
                        from tools.govcon.response_drafter import draft_response
                        result = draft_response(shall_id)
        return result, fake_conn

    def _inserted_metadata(self, fake_conn):
        # metadata is the last positional param in the INSERT (see
        # tools/govcon/response_drafter.py's proposal_section_drafts insert).
        return _json.loads(fake_conn.inserted_params[-1])

    def test_consult_not_attempted_when_flag_off(self, monkeypatch):
        monkeypatch.delenv("ICDEV_PROPOSAL_SPECIALIST_CONSULT_ENABLED", raising=False)

        with patch("tools.govcon.specialist_consult.request_council_consult") as mock_consult:
            result, fake_conn = self._draft_via()

        mock_consult.assert_not_called()
        assert result.get("status") == "ok"
        assert "specialist_consult" not in self._inserted_metadata(fake_conn)

    def test_consult_result_attached_to_metadata_when_flag_on(self, monkeypatch):
        monkeypatch.setenv("ICDEV_PROPOSAL_SPECIALIST_CONSULT_ENABLED", "true")

        fake_result = {"verdict": "Proceed with caution.", "stop_reason": "completed", "source": "icdev_council"}
        with patch("tools.govcon.specialist_consult.request_council_consult", return_value=fake_result):
            result, fake_conn = self._draft_via()

        assert result.get("status") == "ok"
        assert self._inserted_metadata(fake_conn)["specialist_consult"] == fake_result

    def test_consult_failure_does_not_break_draft_response(self, monkeypatch):
        monkeypatch.setenv("ICDEV_PROPOSAL_SPECIALIST_CONSULT_ENABLED", "true")

        with patch("tools.govcon.specialist_consult.request_council_consult", side_effect=RuntimeError("boom")):
            result, fake_conn = self._draft_via()

        assert result.get("status") == "ok", f"draft_response failed: {result}"
        assert "specialist_consult" not in self._inserted_metadata(fake_conn)


class TestDraftQualityScorePresence:
    """GET /api/govcon/opportunities/<id>/drafts must return quality_score on each record."""

    @pytest.fixture()
    def api_app(self, tmp_path):
        return _build_quality_drafts_api_app(tmp_path)

    def _get(self, api_app_pair, opp_id="opp-quality-test"):
        flask_app, fake_get_db = api_app_pair
        with patch("tools.dashboard.api.govcon._get_db", side_effect=fake_get_db):
            with flask_app.test_client() as c:
                resp = c.get(f"/api/govcon/opportunities/{opp_id}/drafts")
        return resp

    def test_each_draft_has_quality_score_key(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        for i, d in enumerate(data["drafts"]):
            assert "quality_score" in d, f"drafts[{i}] missing 'quality_score'"

    def test_quality_score_is_not_none(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        for i, d in enumerate(data["drafts"]):
            assert d["quality_score"] is not None, f"drafts[{i}]['quality_score'] is None"

    def test_quality_score_is_numeric(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        for i, d in enumerate(data["drafts"]):
            qs = d["quality_score"]
            assert isinstance(qs, (int, float)) and not isinstance(qs, bool), (
                f"drafts[{i}]['quality_score'] must be numeric, got {type(qs).__name__}: {qs!r}"
            )

    def test_quality_score_is_not_string(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        for i, d in enumerate(data["drafts"]):
            assert not isinstance(d["quality_score"], str), (
                f"drafts[{i}]['quality_score'] must not be a string"
            )

    def test_quality_score_is_between_0_and_1(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        for i, d in enumerate(data["drafts"]):
            qs = d["quality_score"]
            assert 0.0 <= qs <= 1.0, (
                f"drafts[{i}]['quality_score']={qs!r} out of range [0.0, 1.0]"
            )

    def test_quality_score_gte_0_for_zero_confidence(self, api_app):
        resp = self._get(api_app)
        data = resp.get_json()
        zero_conf = [d for d in data["drafts"] if d.get("confidence_score", 1) == 0.0]
        for d in zero_conf:
            assert d["quality_score"] >= 0.0, (
                "quality_score must be >= 0.0 even for zero-confidence draft"
            )

    def test_high_confidence_draft_has_higher_quality_than_low(self, api_app):
        """qd-draft-1 (confidence=0.85, coverage=0.92) must outscore qd-draft-3 (both=0.0)."""
        resp = self._get(api_app)
        data = resp.get_json()
        high = next((d for d in data["drafts"] if d["id"] == "qd-draft-1"), None)
        low = next((d for d in data["drafts"] if d["id"] == "qd-draft-3"), None)
        assert high is not None, "Expected draft qd-draft-1 in response"
        assert low is not None, "Expected draft qd-draft-3 in response"
        assert high["quality_score"] > low["quality_score"], (
            f"High-confidence draft quality_score {high['quality_score']!r} "
            f"should exceed zero-confidence {low['quality_score']!r}"
        )

    def test_zero_confidence_zero_coverage_quality_score_is_zero(self, api_app):
        """qd-draft-3 has confidence=0.0, best_coverage=0.0 → quality_score must be 0.0."""
        resp = self._get(api_app)
        data = resp.get_json()
        draft3 = next((d for d in data["drafts"] if d["id"] == "qd-draft-3"), None)
        assert draft3 is not None, "Expected qd-draft-3 in response"
        assert draft3["quality_score"] == 0.0, (
            f"zero confidence + zero coverage should yield quality_score=0.0, "
            f"got {draft3['quality_score']!r}"
        )

    def test_quality_score_for_max_confidence_and_coverage(self, tmp_path):
        """A draft with confidence_score=1.0 and best_coverage=1.0 must have quality_score=1.0."""
        perfect_row = [{
            "id": "qd-perfect",
            "opportunity_id": "opp-perfect",
            "shall_statement_id": None,
            "draft_content": "Perfect content.",
            "draft_method": "template",
            "confidence_score": 1.0,
            "status": "draft",
            "created_at": "2026-05-20T09:00:00",
            "metadata": _json.dumps({"best_coverage": 1.0, "capability_count": 5, "kb_count": 3}),
        }]
        db_path = _make_quality_drafts_db(tmp_path, draft_rows=perfect_row, shall_rows=[])

        from _sql_compat import connect as _tconnect

        def _fake_get_db():
            c = _tconnect(db_path)
            return c

        from tools.dashboard.api.govcon import govcon_api

        flask_app = Flask(__name__)
        flask_app.config["TESTING"] = True
        flask_app.register_blueprint(govcon_api)

        with patch("tools.dashboard.api.govcon._get_db", side_effect=_fake_get_db):
            with flask_app.test_client() as c:
                resp = c.get("/api/govcon/opportunities/opp-perfect/drafts")
        data = resp.get_json()
        assert data["total"] == 1
        qs = data["drafts"][0]["quality_score"]
        assert qs == 1.0, f"Perfect draft should have quality_score=1.0, got {qs!r}"


# ---------------------------------------------------------------------------
# Unit tests: _compute_quality_score() composite formula (gcpl-dft-06)
# ---------------------------------------------------------------------------


class TestComputeQualityScoreFunction:
    """_compute_quality_score(confidence_score, best_coverage) = 0.6*c + 0.4*b."""

    def _call(self, confidence_score, best_coverage):
        from tools.govcon.response_drafter import _compute_quality_score

        return _compute_quality_score(confidence_score, best_coverage)

    def test_returns_float(self):
        result = self._call(0.7, 0.8)
        assert isinstance(result, float), (
            f"_compute_quality_score must return float, got {type(result).__name__}"
        )

    def test_zero_inputs_return_zero(self):
        assert self._call(0.0, 0.0) == 0.0

    def test_max_inputs_return_one(self):
        result = self._call(1.0, 1.0)
        assert result == pytest.approx(1.0, abs=1e-4)

    def test_confidence_weight_is_0_6(self):
        """confidence_score=1.0, best_coverage=0.0 → quality_score = 0.6."""
        result = self._call(1.0, 0.0)
        assert result == pytest.approx(0.6, abs=1e-4), (
            f"confidence weight 0.6 failed: expected 0.6, got {result!r}"
        )

    def test_coverage_weight_is_0_4(self):
        """confidence_score=0.0, best_coverage=1.0 → quality_score = 0.4."""
        result = self._call(0.0, 1.0)
        assert result == pytest.approx(0.4, abs=1e-4), (
            f"coverage weight 0.4 failed: expected 0.4, got {result!r}"
        )

    def test_result_is_in_0_1_range(self):
        for c, b in [(0.85, 0.92), (0.60, 0.61), (0.5, 0.5), (0.0, 0.0), (1.0, 1.0)]:
            result = self._call(c, b)
            assert 0.0 <= result <= 1.0, (
                f"_compute_quality_score({c}, {b}) = {result!r} out of [0.0, 1.0]"
            )

    def test_composite_at_typical_values(self):
        """confidence=0.85, coverage=0.92 → 0.85*0.6 + 0.92*0.4 = 0.51 + 0.368 = 0.878."""
        result = self._call(0.85, 0.92)
        expected = 0.85 * 0.6 + 0.92 * 0.4
        assert result == pytest.approx(expected, abs=1e-4), (
            f"Expected {expected!r}, got {result!r}"
        )

    def test_result_not_bool(self):
        result = self._call(0.7, 0.8)
        assert not isinstance(result, bool), "_compute_quality_score must not return bool"

    def test_partial_confidence_zero_coverage(self):
        result = self._call(0.72, 0.0)
        assert result == pytest.approx(0.72 * 0.6, abs=1e-4)

    def test_zero_confidence_partial_coverage(self):
        result = self._call(0.0, 0.61)
        assert result == pytest.approx(0.61 * 0.4, abs=1e-4)

    @pytest.mark.parametrize(
        "confidence,coverage",
        [
            (0.85, 0.92),
            (0.60, 0.61),
            (0.55, 0.25),
            (1.0, 1.0),
            (0.0, 0.0),
            (0.5, 0.5),
        ],
    )
    def test_parametrized_composite_formula(self, confidence, coverage):
        expected = confidence * 0.6 + coverage * 0.4
        result = self._call(confidence, coverage)
        assert result == pytest.approx(expected, abs=1e-4), (
            f"_compute_quality_score({confidence}, {coverage}) = {result!r}, "
            f"expected {expected!r}"
        )


# ---------------------------------------------------------------------------
# Schema + helpers for PUT /api/govcon/drafts/<id>/approve tests (gcpl-dft-07)
# ---------------------------------------------------------------------------

_APPROVE_SCHEMA = """
CREATE TABLE IF NOT EXISTS proposal_section_drafts (
    id TEXT PRIMARY KEY,
    section_id TEXT,
    opportunity_id TEXT NOT NULL,
    shall_statement_id TEXT,
    capability_ids TEXT DEFAULT '[]',
    knowledge_block_ids TEXT DEFAULT '[]',
    draft_content TEXT NOT NULL,
    draft_method TEXT,
    confidence REAL DEFAULT 0.0,
    confidence_score REAL DEFAULT 0.0,
    generation_model TEXT,
    status TEXT NOT NULL DEFAULT 'draft',
    reviewed_by TEXT,
    reviewed_at TEXT,
    review_notes TEXT,
    reviewer_notes TEXT,
    metadata TEXT DEFAULT '{}',
    created_at TEXT NOT NULL,
    classification TEXT DEFAULT 'CUI'
);

CREATE TABLE IF NOT EXISTS proposal_sections (
    id TEXT PRIMARY KEY,
    volume_id TEXT,
    opportunity_id TEXT,
    section_number TEXT NOT NULL DEFAULT '1',
    title TEXT NOT NULL DEFAULT 'Test Section',
    status TEXT NOT NULL DEFAULT 'not_started',
    notes TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS proposal_status_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    old_status TEXT,
    new_status TEXT NOT NULL,
    changed_by TEXT,
    reason TEXT,
    created_at TEXT DEFAULT (datetime('now'))
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

_APPROVE_DRAFT_ID = "approve-draft-1"
_APPROVE_OPP_ID = "opp-approve-test"
_APPROVE_SECTION_ID = "section-approve-1"
_APPROVE_DRAFT_CONTENT = "Our team provides zero-trust networking via our ZTA platform."


def _make_approve_db(tmp_path, section_status="not_started", include_section=True):
    """Create a SQLite test DB seeded with one draft (and optionally a linked section)."""
    db_path = tmp_path / "approve_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_APPROVE_SCHEMA)

    section_id = _APPROVE_SECTION_ID if include_section else None

    conn.execute(
        """INSERT INTO proposal_section_drafts
           (id, section_id, opportunity_id, draft_content, draft_method, confidence_score, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            _APPROVE_DRAFT_ID,
            section_id,
            _APPROVE_OPP_ID,
            _APPROVE_DRAFT_CONTENT,
            "template",
            0.82,
            "draft",
            "2026-05-20T10:00:00",
        ),
    )

    if include_section:
        conn.execute(
            "INSERT INTO proposal_sections (id, section_number, title, status) VALUES (?, ?, ?, ?)",
            (_APPROVE_SECTION_ID, "1.1", "Technical Approach", section_status),
        )

    conn.commit()
    conn.close()
    return db_path


def _build_approve_api_app(tmp_path, section_status="not_started", include_section=True):
    """Return (flask_app, fake_get_db) for approve endpoint tests."""
    db_path = _make_approve_db(tmp_path, section_status=section_status, include_section=include_section)

    from _sql_compat import connect as _tconnect

    def fake_get_db():
        c = _tconnect(db_path)
        return c

    from tools.dashboard.api.govcon import govcon_api

    flask_app = Flask(__name__)
    flask_app.config["TESTING"] = True
    flask_app.register_blueprint(govcon_api)

    return flask_app, fake_get_db, db_path


# ---------------------------------------------------------------------------
# Tests: PUT /api/govcon/drafts/<id>/approve — response shape (gcpl-dft-07)
# ---------------------------------------------------------------------------


class TestApproveDraftEndpoint:
    """PUT /api/govcon/drafts/<id>/approve returns ok and transitions draft status."""

    @pytest.fixture()
    def app_trio(self, tmp_path):
        return _build_approve_api_app(tmp_path)

    def _put(self, app_trio, draft_id=_APPROVE_DRAFT_ID, body=None):
        flask_app, fake_get_db, _ = app_trio
        with patch("tools.dashboard.api.govcon._get_db", side_effect=fake_get_db):
            with flask_app.test_client() as c:
                resp = c.put(
                    f"/api/govcon/drafts/{draft_id}/approve",
                    json=body or {},
                    content_type="application/json",
                )
        return resp

    def test_approve_returns_200(self, app_trio):
        resp = self._put(app_trio)
        assert resp.status_code == 200

    def test_approve_content_type_is_json(self, app_trio):
        resp = self._put(app_trio)
        assert resp.content_type.startswith("application/json")

    def test_approve_response_has_status_key(self, app_trio):
        resp = self._put(app_trio)
        data = resp.get_json()
        assert "status" in data

    def test_approve_response_status_is_ok(self, app_trio):
        resp = self._put(app_trio)
        data = resp.get_json()
        assert data["status"] == "ok"

    def test_approve_response_has_draft_id(self, app_trio):
        resp = self._put(app_trio)
        data = resp.get_json()
        assert "draft_id" in data

    def test_approve_draft_id_matches_requested(self, app_trio):
        resp = self._put(app_trio)
        data = resp.get_json()
        assert data["draft_id"] == _APPROVE_DRAFT_ID

    def test_approve_response_approved_is_true(self, app_trio):
        resp = self._put(app_trio)
        data = resp.get_json()
        assert data.get("approved") is True

    def test_approve_nonexistent_draft_returns_404(self, app_trio):
        resp = self._put(app_trio, draft_id="no-such-draft-id")
        assert resp.status_code == 404

    def test_404_response_has_error_key(self, app_trio):
        resp = self._put(app_trio, draft_id="no-such-draft-id")
        data = resp.get_json()
        assert "error" in data

    def test_approve_inserts_new_approved_row(self, tmp_path):
        flask_app, fake_get_db, db_path = _build_approve_api_app(tmp_path)
        with patch("tools.dashboard.api.govcon._get_db", side_effect=fake_get_db):
            with flask_app.test_client() as c:
                c.put(
                    f"/api/govcon/drafts/{_APPROVE_DRAFT_ID}/approve",
                    json={},
                    content_type="application/json",
                )
        verify = sqlite3.connect(str(db_path))
        verify.row_factory = sqlite3.Row
        rows = verify.execute(
            "SELECT * FROM proposal_section_drafts WHERE status = 'approved'"
        ).fetchall()
        verify.close()
        assert len(rows) == 1, (
            f"Expected exactly 1 approved row, got {len(rows)}"
        )

    def test_approved_row_preserves_draft_content(self, tmp_path):
        flask_app, fake_get_db, db_path = _build_approve_api_app(tmp_path)
        with patch("tools.dashboard.api.govcon._get_db", side_effect=fake_get_db):
            with flask_app.test_client() as c:
                c.put(
                    f"/api/govcon/drafts/{_APPROVE_DRAFT_ID}/approve",
                    json={},
                    content_type="application/json",
                )
        verify = sqlite3.connect(str(db_path))
        verify.row_factory = sqlite3.Row
        row = verify.execute(
            "SELECT draft_content FROM proposal_section_drafts WHERE status = 'approved'"
        ).fetchone()
        verify.close()
        assert row is not None
        assert row["draft_content"] == _APPROVE_DRAFT_CONTENT

    def test_original_draft_row_unchanged(self, tmp_path):
        flask_app, fake_get_db, db_path = _build_approve_api_app(tmp_path)
        with patch("tools.dashboard.api.govcon._get_db", side_effect=fake_get_db):
            with flask_app.test_client() as c:
                c.put(
                    f"/api/govcon/drafts/{_APPROVE_DRAFT_ID}/approve",
                    json={},
                    content_type="application/json",
                )
        verify = sqlite3.connect(str(db_path))
        verify.row_factory = sqlite3.Row
        original = verify.execute(
            "SELECT status FROM proposal_section_drafts WHERE id = ?",
            (_APPROVE_DRAFT_ID,),
        ).fetchone()
        verify.close()
        assert original is not None, "Original draft row was deleted"
        assert original["status"] == "draft", (
            f"Original row must remain status='draft', got {original['status']!r}"
        )

    def test_approved_row_reviewed_by_defaults_to_govcon_api(self, tmp_path):
        flask_app, fake_get_db, db_path = _build_approve_api_app(tmp_path)
        with patch("tools.dashboard.api.govcon._get_db", side_effect=fake_get_db):
            with flask_app.test_client() as c:
                c.put(
                    f"/api/govcon/drafts/{_APPROVE_DRAFT_ID}/approve",
                    content_type="application/json",
                )
        verify = sqlite3.connect(str(db_path))
        verify.row_factory = sqlite3.Row
        row = verify.execute(
            "SELECT reviewed_by FROM proposal_section_drafts WHERE status = 'approved'"
        ).fetchone()
        verify.close()
        assert row is not None
        assert row["reviewed_by"] == "govcon_api", (
            f"Default reviewed_by must be 'govcon_api', got {row['reviewed_by']!r}"
        )

    def test_approved_row_reviewed_by_from_request_body(self, tmp_path):
        flask_app, fake_get_db, db_path = _build_approve_api_app(tmp_path)
        with patch("tools.dashboard.api.govcon._get_db", side_effect=fake_get_db):
            with flask_app.test_client() as c:
                c.put(
                    f"/api/govcon/drafts/{_APPROVE_DRAFT_ID}/approve",
                    json={"reviewed_by": "alice@example.com"},
                    content_type="application/json",
                )
        verify = sqlite3.connect(str(db_path))
        verify.row_factory = sqlite3.Row
        row = verify.execute(
            "SELECT reviewed_by FROM proposal_section_drafts WHERE status = 'approved'"
        ).fetchone()
        verify.close()
        assert row is not None
        assert row["reviewed_by"] == "alice@example.com", (
            f"reviewed_by should come from body, got {row['reviewed_by']!r}"
        )


# ---------------------------------------------------------------------------
# Tests: section status transition on approve (gcpl-dft-07)
# ---------------------------------------------------------------------------


class TestApproveDraftSectionTransition:
    """Approving a draft advances linked section from not_started/outlining to drafting."""

    def _approve(self, flask_app, fake_get_db, draft_id=_APPROVE_DRAFT_ID, body=None):
        with patch("tools.dashboard.api.govcon._get_db", side_effect=fake_get_db):
            with flask_app.test_client() as c:
                return c.put(
                    f"/api/govcon/drafts/{draft_id}/approve",
                    json=body or {},
                    content_type="application/json",
                )

    def test_section_not_started_advances_to_drafting(self, tmp_path):
        flask_app, fake_get_db, db_path = _build_approve_api_app(tmp_path, section_status="not_started")
        self._approve(flask_app, fake_get_db)
        verify = sqlite3.connect(str(db_path))
        verify.row_factory = sqlite3.Row
        section = verify.execute(
            "SELECT status FROM proposal_sections WHERE id = ?", (_APPROVE_SECTION_ID,)
        ).fetchone()
        verify.close()
        assert section is not None
        assert section["status"] == "drafting", (
            f"Section status should advance to 'drafting', got {section['status']!r}"
        )

    def test_section_outlining_advances_to_drafting(self, tmp_path):
        flask_app, fake_get_db, db_path = _build_approve_api_app(tmp_path, section_status="outlining")
        self._approve(flask_app, fake_get_db)
        verify = sqlite3.connect(str(db_path))
        verify.row_factory = sqlite3.Row
        section = verify.execute(
            "SELECT status FROM proposal_sections WHERE id = ?", (_APPROVE_SECTION_ID,)
        ).fetchone()
        verify.close()
        assert section["status"] == "drafting", (
            f"Section 'outlining' must advance to 'drafting', got {section['status']!r}"
        )

    def test_section_already_drafting_not_changed(self, tmp_path):
        flask_app, fake_get_db, db_path = _build_approve_api_app(tmp_path, section_status="drafting")
        self._approve(flask_app, fake_get_db)
        verify = sqlite3.connect(str(db_path))
        verify.row_factory = sqlite3.Row
        section = verify.execute(
            "SELECT status FROM proposal_sections WHERE id = ?", (_APPROVE_SECTION_ID,)
        ).fetchone()
        verify.close()
        assert section["status"] == "drafting", (
            f"Section already in 'drafting' must remain 'drafting', got {section['status']!r}"
        )

    def test_section_internal_review_not_downgraded(self, tmp_path):
        flask_app, fake_get_db, db_path = _build_approve_api_app(
            tmp_path, section_status="internal_review"
        )
        self._approve(flask_app, fake_get_db)
        verify = sqlite3.connect(str(db_path))
        verify.row_factory = sqlite3.Row
        section = verify.execute(
            "SELECT status FROM proposal_sections WHERE id = ?", (_APPROVE_SECTION_ID,)
        ).fetchone()
        verify.close()
        assert section["status"] == "internal_review", (
            f"Section in 'internal_review' must not be downgraded, got {section['status']!r}"
        )

    def test_status_history_row_inserted_on_transition(self, tmp_path):
        flask_app, fake_get_db, db_path = _build_approve_api_app(tmp_path, section_status="not_started")
        self._approve(flask_app, fake_get_db)
        verify = sqlite3.connect(str(db_path))
        verify.row_factory = sqlite3.Row
        rows = verify.execute(
            "SELECT * FROM proposal_status_history WHERE entity_id = ?",
            (_APPROVE_SECTION_ID,),
        ).fetchall()
        verify.close()
        assert len(rows) == 1, (
            f"Expected 1 proposal_status_history row for section, got {len(rows)}"
        )

    def test_status_history_entity_type_is_section(self, tmp_path):
        flask_app, fake_get_db, db_path = _build_approve_api_app(tmp_path, section_status="not_started")
        self._approve(flask_app, fake_get_db)
        verify = sqlite3.connect(str(db_path))
        verify.row_factory = sqlite3.Row
        row = verify.execute(
            "SELECT entity_type FROM proposal_status_history WHERE entity_id = ?",
            (_APPROVE_SECTION_ID,),
        ).fetchone()
        verify.close()
        assert row is not None
        assert row["entity_type"] == "section", (
            f"entity_type must be 'section', got {row['entity_type']!r}"
        )

    def test_status_history_old_status_is_not_started(self, tmp_path):
        flask_app, fake_get_db, db_path = _build_approve_api_app(tmp_path, section_status="not_started")
        self._approve(flask_app, fake_get_db)
        verify = sqlite3.connect(str(db_path))
        verify.row_factory = sqlite3.Row
        row = verify.execute(
            "SELECT old_status FROM proposal_status_history WHERE entity_id = ?",
            (_APPROVE_SECTION_ID,),
        ).fetchone()
        verify.close()
        assert row["old_status"] == "not_started", (
            f"old_status must be 'not_started', got {row['old_status']!r}"
        )

    def test_status_history_new_status_is_drafting(self, tmp_path):
        flask_app, fake_get_db, db_path = _build_approve_api_app(tmp_path, section_status="not_started")
        self._approve(flask_app, fake_get_db)
        verify = sqlite3.connect(str(db_path))
        verify.row_factory = sqlite3.Row
        row = verify.execute(
            "SELECT new_status FROM proposal_status_history WHERE entity_id = ?",
            (_APPROVE_SECTION_ID,),
        ).fetchone()
        verify.close()
        assert row["new_status"] == "drafting", (
            f"new_status must be 'drafting', got {row['new_status']!r}"
        )

    def test_no_history_row_when_section_not_advanced(self, tmp_path):
        flask_app, fake_get_db, db_path = _build_approve_api_app(tmp_path, section_status="internal_review")
        self._approve(flask_app, fake_get_db)
        verify = sqlite3.connect(str(db_path))
        verify.row_factory = sqlite3.Row
        rows = verify.execute(
            "SELECT * FROM proposal_status_history WHERE entity_id = ?",
            (_APPROVE_SECTION_ID,),
        ).fetchall()
        verify.close()
        assert len(rows) == 0, (
            f"No history row expected when section not advanced, got {len(rows)}"
        )

    def test_approve_without_section_id_returns_ok(self, tmp_path):
        flask_app, fake_get_db, db_path = _build_approve_api_app(tmp_path, include_section=False)
        resp = self._approve(flask_app, fake_get_db)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"

    def test_section_notes_mention_reviewer(self, tmp_path):
        flask_app, fake_get_db, db_path = _build_approve_api_app(tmp_path, section_status="not_started")
        self._approve(flask_app, fake_get_db, body={"reviewed_by": "bob@example.com"})
        verify = sqlite3.connect(str(db_path))
        verify.row_factory = sqlite3.Row
        section = verify.execute(
            "SELECT notes FROM proposal_sections WHERE id = ?", (_APPROVE_SECTION_ID,)
        ).fetchone()
        verify.close()
        assert section is not None
        assert "bob@example.com" in (section["notes"] or ""), (
            f"Section notes must mention reviewer 'bob@example.com', got {section['notes']!r}"
        )


# ---------------------------------------------------------------------------
# Schema + helpers for PUT /api/govcon/drafts/<id>/reject tests (gcpl-dft-08)
# ---------------------------------------------------------------------------

_REJECT_DRAFT_ID = "reject-draft-1"
_REJECT_OPP_ID = "opp-reject-test"
_REJECT_SECTION_ID = "section-reject-1"
_REJECT_DRAFT_CONTENT = "Our team leverages containerized microservices for rapid deployment."


def _make_reject_db(tmp_path, section_status="not_started", include_section=True):
    """Create a SQLite test DB seeded with one draft (and optionally a linked section)."""
    db_path = tmp_path / "reject_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_APPROVE_SCHEMA)

    section_id = _REJECT_SECTION_ID if include_section else None

    conn.execute(
        """INSERT INTO proposal_section_drafts
           (id, section_id, opportunity_id, draft_content, draft_method, confidence_score, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            _REJECT_DRAFT_ID,
            section_id,
            _REJECT_OPP_ID,
            _REJECT_DRAFT_CONTENT,
            "llm",
            0.55,
            "draft",
            "2026-05-20T10:00:00",
        ),
    )

    if include_section:
        conn.execute(
            "INSERT INTO proposal_sections (id, section_number, title, status) VALUES (?, ?, ?, ?)",
            (_REJECT_SECTION_ID, "2.1", "Management Approach", section_status),
        )

    conn.commit()
    conn.close()
    return db_path


def _build_reject_api_app(tmp_path, section_status="not_started", include_section=True):
    """Return (flask_app, fake_get_db, db_path) for reject endpoint tests."""
    db_path = _make_reject_db(tmp_path, section_status=section_status, include_section=include_section)

    from _sql_compat import connect as _tconnect

    def fake_get_db():
        c = _tconnect(db_path)
        return c

    from tools.dashboard.api.govcon import govcon_api

    flask_app = Flask(__name__)
    flask_app.config["TESTING"] = True
    flask_app.register_blueprint(govcon_api)

    return flask_app, fake_get_db, db_path


# ---------------------------------------------------------------------------
# Tests: PUT /api/govcon/drafts/<id>/reject — response shape (gcpl-dft-08)
# ---------------------------------------------------------------------------


class TestRejectDraftEndpoint:
    """PUT /api/govcon/drafts/<id>/reject records reason and sets status=rejected."""

    @pytest.fixture()
    def app_trio(self, tmp_path):
        return _build_reject_api_app(tmp_path)

    def _put(self, app_trio, draft_id=_REJECT_DRAFT_ID, body=None):
        flask_app, fake_get_db, _ = app_trio
        with patch("tools.dashboard.api.govcon._get_db", side_effect=fake_get_db):
            with flask_app.test_client() as c:
                resp = c.put(
                    f"/api/govcon/drafts/{draft_id}/reject",
                    json=body or {},
                    content_type="application/json",
                )
        return resp

    def test_reject_returns_200(self, app_trio):
        resp = self._put(app_trio)
        assert resp.status_code == 200

    def test_reject_content_type_is_json(self, app_trio):
        resp = self._put(app_trio)
        assert resp.content_type.startswith("application/json")

    def test_reject_response_has_status_key(self, app_trio):
        resp = self._put(app_trio)
        data = resp.get_json()
        assert "status" in data

    def test_reject_response_status_is_ok(self, app_trio):
        resp = self._put(app_trio)
        data = resp.get_json()
        assert data["status"] == "ok"

    def test_reject_response_has_draft_id(self, app_trio):
        resp = self._put(app_trio)
        data = resp.get_json()
        assert "draft_id" in data

    def test_reject_draft_id_matches_requested(self, app_trio):
        resp = self._put(app_trio)
        data = resp.get_json()
        assert data["draft_id"] == _REJECT_DRAFT_ID

    def test_reject_response_rejected_is_true(self, app_trio):
        resp = self._put(app_trio)
        data = resp.get_json()
        assert data.get("rejected") is True

    def test_reject_nonexistent_draft_returns_404(self, app_trio):
        resp = self._put(app_trio, draft_id="no-such-draft-id")
        assert resp.status_code == 404

    def test_404_response_has_error_key(self, app_trio):
        resp = self._put(app_trio, draft_id="no-such-draft-id")
        data = resp.get_json()
        assert "error" in data

    def test_reject_inserts_new_rejected_row(self, tmp_path):
        flask_app, fake_get_db, db_path = _build_reject_api_app(tmp_path)
        with patch("tools.dashboard.api.govcon._get_db", side_effect=fake_get_db):
            with flask_app.test_client() as c:
                c.put(
                    f"/api/govcon/drafts/{_REJECT_DRAFT_ID}/reject",
                    json={},
                    content_type="application/json",
                )
        verify = sqlite3.connect(str(db_path))
        verify.row_factory = sqlite3.Row
        rows = verify.execute(
            "SELECT * FROM proposal_section_drafts WHERE status = 'rejected'"
        ).fetchall()
        verify.close()
        assert len(rows) == 1, f"Expected exactly 1 rejected row, got {len(rows)}"

    def test_rejected_row_preserves_draft_content(self, tmp_path):
        flask_app, fake_get_db, db_path = _build_reject_api_app(tmp_path)
        with patch("tools.dashboard.api.govcon._get_db", side_effect=fake_get_db):
            with flask_app.test_client() as c:
                c.put(
                    f"/api/govcon/drafts/{_REJECT_DRAFT_ID}/reject",
                    json={},
                    content_type="application/json",
                )
        verify = sqlite3.connect(str(db_path))
        verify.row_factory = sqlite3.Row
        row = verify.execute(
            "SELECT draft_content FROM proposal_section_drafts WHERE status = 'rejected'"
        ).fetchone()
        verify.close()
        assert row is not None
        assert row["draft_content"] == _REJECT_DRAFT_CONTENT

    def test_original_draft_row_unchanged(self, tmp_path):
        flask_app, fake_get_db, db_path = _build_reject_api_app(tmp_path)
        with patch("tools.dashboard.api.govcon._get_db", side_effect=fake_get_db):
            with flask_app.test_client() as c:
                c.put(
                    f"/api/govcon/drafts/{_REJECT_DRAFT_ID}/reject",
                    json={},
                    content_type="application/json",
                )
        verify = sqlite3.connect(str(db_path))
        verify.row_factory = sqlite3.Row
        original = verify.execute(
            "SELECT status FROM proposal_section_drafts WHERE id = ?",
            (_REJECT_DRAFT_ID,),
        ).fetchone()
        verify.close()
        assert original is not None, "Original draft row was deleted"
        assert original["status"] == "draft", (
            f"Original row must remain status='draft', got {original['status']!r}"
        )

    def test_rejected_row_reviewed_by_defaults_to_govcon_api(self, tmp_path):
        flask_app, fake_get_db, db_path = _build_reject_api_app(tmp_path)
        with patch("tools.dashboard.api.govcon._get_db", side_effect=fake_get_db):
            with flask_app.test_client() as c:
                c.put(
                    f"/api/govcon/drafts/{_REJECT_DRAFT_ID}/reject",
                    content_type="application/json",
                )
        verify = sqlite3.connect(str(db_path))
        verify.row_factory = sqlite3.Row
        row = verify.execute(
            "SELECT reviewed_by FROM proposal_section_drafts WHERE status = 'rejected'"
        ).fetchone()
        verify.close()
        assert row is not None
        assert row["reviewed_by"] == "govcon_api", (
            f"Default reviewed_by must be 'govcon_api', got {row['reviewed_by']!r}"
        )

    def test_rejected_row_reviewed_by_from_request_body(self, tmp_path):
        flask_app, fake_get_db, db_path = _build_reject_api_app(tmp_path)
        with patch("tools.dashboard.api.govcon._get_db", side_effect=fake_get_db):
            with flask_app.test_client() as c:
                c.put(
                    f"/api/govcon/drafts/{_REJECT_DRAFT_ID}/reject",
                    json={"reviewed_by": "carol@example.com"},
                    content_type="application/json",
                )
        verify = sqlite3.connect(str(db_path))
        verify.row_factory = sqlite3.Row
        row = verify.execute(
            "SELECT reviewed_by FROM proposal_section_drafts WHERE status = 'rejected'"
        ).fetchone()
        verify.close()
        assert row is not None
        assert row["reviewed_by"] == "carol@example.com", (
            f"reviewed_by should come from body, got {row['reviewed_by']!r}"
        )

    def test_rejected_row_stores_review_notes(self, tmp_path):
        flask_app, fake_get_db, db_path = _build_reject_api_app(tmp_path)
        with patch("tools.dashboard.api.govcon._get_db", side_effect=fake_get_db):
            with flask_app.test_client() as c:
                c.put(
                    f"/api/govcon/drafts/{_REJECT_DRAFT_ID}/reject",
                    json={"review_notes": "Content does not meet compliance requirements."},
                    content_type="application/json",
                )
        verify = sqlite3.connect(str(db_path))
        verify.row_factory = sqlite3.Row
        row = verify.execute(
            "SELECT review_notes FROM proposal_section_drafts WHERE status = 'rejected'"
        ).fetchone()
        verify.close()
        assert row is not None
        assert row["review_notes"] == "Content does not meet compliance requirements.", (
            f"review_notes must be stored, got {row['review_notes']!r}"
        )

    def test_rejected_row_review_notes_defaults_to_rejected(self, tmp_path):
        flask_app, fake_get_db, db_path = _build_reject_api_app(tmp_path)
        with patch("tools.dashboard.api.govcon._get_db", side_effect=fake_get_db):
            with flask_app.test_client() as c:
                c.put(
                    f"/api/govcon/drafts/{_REJECT_DRAFT_ID}/reject",
                    json={},
                    content_type="application/json",
                )
        verify = sqlite3.connect(str(db_path))
        verify.row_factory = sqlite3.Row
        row = verify.execute(
            "SELECT review_notes FROM proposal_section_drafts WHERE status = 'rejected'"
        ).fetchone()
        verify.close()
        assert row is not None
        assert row["review_notes"] == "Rejected", (
            f"Default review_notes must be 'Rejected', got {row['review_notes']!r}"
        )


# ---------------------------------------------------------------------------
# Tests: section status NOT changed on reject (gcpl-dft-08)
# ---------------------------------------------------------------------------


class TestRejectDraftSectionNotChanged:
    """Rejecting a draft must NOT change the linked section's status."""

    def _reject(self, flask_app, fake_get_db, draft_id=_REJECT_DRAFT_ID, body=None):
        with patch("tools.dashboard.api.govcon._get_db", side_effect=fake_get_db):
            with flask_app.test_client() as c:
                return c.put(
                    f"/api/govcon/drafts/{draft_id}/reject",
                    json=body or {},
                    content_type="application/json",
                )

    def test_section_not_started_status_unchanged(self, tmp_path):
        flask_app, fake_get_db, db_path = _build_reject_api_app(tmp_path, section_status="not_started")
        self._reject(flask_app, fake_get_db)
        verify = sqlite3.connect(str(db_path))
        verify.row_factory = sqlite3.Row
        section = verify.execute(
            "SELECT status FROM proposal_sections WHERE id = ?", (_REJECT_SECTION_ID,)
        ).fetchone()
        verify.close()
        assert section is not None
        assert section["status"] == "not_started", (
            f"Section status must remain 'not_started' after reject, got {section['status']!r}"
        )

    def test_section_drafting_status_unchanged(self, tmp_path):
        flask_app, fake_get_db, db_path = _build_reject_api_app(tmp_path, section_status="drafting")
        self._reject(flask_app, fake_get_db)
        verify = sqlite3.connect(str(db_path))
        verify.row_factory = sqlite3.Row
        section = verify.execute(
            "SELECT status FROM proposal_sections WHERE id = ?", (_REJECT_SECTION_ID,)
        ).fetchone()
        verify.close()
        assert section["status"] == "drafting", (
            f"Section status must remain 'drafting' after reject, got {section['status']!r}"
        )

    def test_section_internal_review_status_unchanged(self, tmp_path):
        flask_app, fake_get_db, db_path = _build_reject_api_app(tmp_path, section_status="internal_review")
        self._reject(flask_app, fake_get_db)
        verify = sqlite3.connect(str(db_path))
        verify.row_factory = sqlite3.Row
        section = verify.execute(
            "SELECT status FROM proposal_sections WHERE id = ?", (_REJECT_SECTION_ID,)
        ).fetchone()
        verify.close()
        assert section["status"] == "internal_review", (
            f"Section status must remain 'internal_review' after reject, got {section['status']!r}"
        )

    def test_no_status_history_row_inserted_on_reject(self, tmp_path):
        flask_app, fake_get_db, db_path = _build_reject_api_app(tmp_path, section_status="not_started")
        self._reject(flask_app, fake_get_db)
        verify = sqlite3.connect(str(db_path))
        verify.row_factory = sqlite3.Row
        rows = verify.execute(
            "SELECT * FROM proposal_status_history WHERE entity_id = ?",
            (_REJECT_SECTION_ID,),
        ).fetchall()
        verify.close()
        assert len(rows) == 0, (
            f"No status_history row expected on reject, got {len(rows)}"
        )

    def test_reject_without_section_id_returns_ok(self, tmp_path):
        flask_app, fake_get_db, db_path = _build_reject_api_app(tmp_path, include_section=False)
        resp = self._reject(flask_app, fake_get_db)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"


# ---------------------------------------------------------------------------
# Schema + helpers for GET /api/govcon/opportunities/<id>/questions (gcpl-dft-10)
# ---------------------------------------------------------------------------

_QUESTIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS proposal_questions (
    id TEXT PRIMARY KEY,
    opportunity_id TEXT NOT NULL,
    question_number INTEGER,
    question_text TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'scope',
    priority TEXT NOT NULL DEFAULT 'medium',
    source TEXT NOT NULL DEFAULT 'manual',
    rfp_section_ref TEXT,
    status TEXT NOT NULL DEFAULT 'draft',
    ambiguity_trigger TEXT,
    content_hash TEXT,
    created_by TEXT,
    approved_by TEXT,
    approved_at TEXT,
    submitted_at TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
"""

_Q_OPP_ID = "opp-questions-test"
_Q_OTHER_OPP_ID = "opp-other"

_Q_ROW_1 = {
    "id": "q-001",
    "opportunity_id": _Q_OPP_ID,
    "question_number": 1,
    "question_text": "What is the period of performance for this contract?",
    "category": "scope",
    "priority": "high",
    "source": "auto",
    "status": "draft",
    "classification": "CUI",
}
_Q_ROW_2 = {
    "id": "q-002",
    "opportunity_id": _Q_OPP_ID,
    "question_number": 2,
    "question_text": "Please clarify the evaluation criteria weights for Section L.",
    "category": "evaluation_criteria",
    "priority": "medium",
    "source": "manual",
    "status": "approved",
    "classification": "CUI",
}
_Q_ROW_3 = {
    "id": "q-003",
    "opportunity_id": _Q_OPP_ID,
    "question_number": 3,
    "question_text": "Are small business subcontracting requirements applicable?",
    "category": "small_business",
    "priority": "low",
    "source": "auto",
    "status": "draft",
    "classification": "CUI",
}
_Q_ROW_OTHER = {
    "id": "q-other-1",
    "opportunity_id": _Q_OTHER_OPP_ID,
    "question_number": 1,
    "question_text": "Unrelated question for a different opportunity.",
    "category": "scope",
    "priority": "medium",
    "source": "manual",
    "status": "draft",
    "classification": "CUI",
}


def _make_questions_db(tmp_path, rows=None):
    """Create a SQLite test DB seeded with proposal_questions rows."""
    db_path = tmp_path / "questions_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_QUESTIONS_SCHEMA)
    if rows is None:
        rows = [_Q_ROW_1, _Q_ROW_2, _Q_ROW_3, _Q_ROW_OTHER]
    for r in rows:
        conn.execute(
            """INSERT INTO proposal_questions
               (id, opportunity_id, question_number, question_text,
                category, priority, source, status, classification)
               VALUES (:id, :opportunity_id, :question_number, :question_text,
                       :category, :priority, :source, :status, :classification)""",
            r,
        )
    conn.commit()
    conn.close()
    return db_path


def _build_questions_api_app(tmp_path, rows=None):
    """Return (flask_app, fake_get_db, db_path) for questions list endpoint tests."""
    db_path = _make_questions_db(tmp_path, rows=rows)

    from _sql_compat import connect as _tconnect

    def fake_get_db():
        c = _tconnect(db_path)
        return c

    from tools.dashboard.api.govcon import govcon_api

    flask_app = Flask(__name__)
    flask_app.config["TESTING"] = True
    flask_app.register_blueprint(govcon_api)

    return flask_app, fake_get_db, db_path


# ---------------------------------------------------------------------------
# Tests: GET /api/govcon/opportunities/<id>/questions — response shape (gcpl-dft-10)
# ---------------------------------------------------------------------------


class TestListQuestionsEndpoint:
    """GET /api/govcon/opportunities/<id>/questions returns a 200 JSON response."""

    @pytest.fixture()
    def app_trio(self, tmp_path):
        return _build_questions_api_app(tmp_path)

    def _get(self, app_trio, opp_id=_Q_OPP_ID, params=None):
        flask_app, fake_get_db, _ = app_trio
        with patch("tools.dashboard.api.govcon._get_db", side_effect=fake_get_db):
            with flask_app.test_client() as c:
                url = f"/api/govcon/opportunities/{opp_id}/questions"
                resp = c.get(url, query_string=params or {})
        return resp

    def test_returns_200(self, app_trio):
        resp = self._get(app_trio)
        assert resp.status_code == 200

    def test_content_type_is_json(self, app_trio):
        resp = self._get(app_trio)
        assert resp.content_type.startswith("application/json")

    def test_response_has_questions_key(self, app_trio):
        resp = self._get(app_trio)
        data = resp.get_json()
        assert "questions" in data

    def test_questions_value_is_list(self, app_trio):
        resp = self._get(app_trio)
        data = resp.get_json()
        assert isinstance(data["questions"], list)

    def test_response_has_stats_key(self, app_trio):
        resp = self._get(app_trio)
        data = resp.get_json()
        assert "stats" in data

    def test_stats_value_is_dict(self, app_trio):
        resp = self._get(app_trio)
        data = resp.get_json()
        assert isinstance(data["stats"], dict)


# ---------------------------------------------------------------------------
# Tests: GET /api/govcon/opportunities/<id>/questions — Q&A records (gcpl-dft-10)
# ---------------------------------------------------------------------------


class TestListQuestionsRecords:
    """Questions list returns only records for the requested opportunity_id."""

    @pytest.fixture()
    def app_trio(self, tmp_path):
        return _build_questions_api_app(tmp_path)

    def _get(self, app_trio, opp_id=_Q_OPP_ID):
        flask_app, fake_get_db, _ = app_trio
        with patch("tools.dashboard.api.govcon._get_db", side_effect=fake_get_db):
            with flask_app.test_client() as c:
                resp = c.get(f"/api/govcon/opportunities/{opp_id}/questions")
        return resp

    def test_returns_three_questions_for_target_opp(self, app_trio):
        data = self._get(app_trio).get_json()
        assert len(data["questions"]) == 3, (
            f"Expected 3 questions for {_Q_OPP_ID!r}, got {len(data['questions'])}"
        )

    def test_questions_belong_to_requested_opp(self, app_trio):
        data = self._get(app_trio).get_json()
        for q in data["questions"]:
            assert q["opportunity_id"] == _Q_OPP_ID, (
                f"All questions must belong to {_Q_OPP_ID!r}, got {q['opportunity_id']!r}"
            )

    def test_other_opp_questions_not_included(self, app_trio):
        data = self._get(app_trio).get_json()
        ids = [q["id"] for q in data["questions"]]
        assert _Q_ROW_OTHER["id"] not in ids, (
            "Questions from a different opportunity must not appear in results"
        )

    def test_questions_ordered_by_question_number(self, app_trio):
        data = self._get(app_trio).get_json()
        numbers = [q["question_number"] for q in data["questions"]]
        assert numbers == sorted(numbers), (
            f"Questions must be ordered by question_number ASC, got {numbers}"
        )

    def test_first_question_text_matches_seeded_value(self, app_trio):
        data = self._get(app_trio).get_json()
        first = data["questions"][0]
        assert first["question_text"] == _Q_ROW_1["question_text"], (
            f"First question text mismatch: {first['question_text']!r}"
        )

    def test_question_record_has_id_field(self, app_trio):
        data = self._get(app_trio).get_json()
        for q in data["questions"]:
            assert "id" in q, "Each question record must have an 'id' field"

    def test_question_record_has_category_field(self, app_trio):
        data = self._get(app_trio).get_json()
        for q in data["questions"]:
            assert "category" in q, "Each question record must have a 'category' field"

    def test_question_record_has_status_field(self, app_trio):
        data = self._get(app_trio).get_json()
        for q in data["questions"]:
            assert "status" in q, "Each question record must have a 'status' field"

    def test_question_record_has_priority_field(self, app_trio):
        data = self._get(app_trio).get_json()
        for q in data["questions"]:
            assert "priority" in q, "Each question record must have a 'priority' field"

    def test_unknown_opp_returns_empty_list(self, app_trio):
        data = self._get(app_trio, opp_id="opp-nonexistent").get_json()
        assert data["questions"] == [], (
            f"Unknown opportunity_id must return empty list, got {data['questions']!r}"
        )

    def test_unknown_opp_returns_zero_total(self, app_trio):
        data = self._get(app_trio, opp_id="opp-nonexistent").get_json()
        assert data["stats"]["total"] == 0, (
            f"Unknown opportunity must have stats.total=0, got {data['stats']['total']}"
        )


# ---------------------------------------------------------------------------
# Tests: GET /api/govcon/opportunities/<id>/questions — stats (gcpl-dft-10)
# ---------------------------------------------------------------------------


class TestListQuestionsStats:
    """stats block aggregates total, by_category, by_status, by_priority correctly."""

    @pytest.fixture()
    def app_trio(self, tmp_path):
        return _build_questions_api_app(tmp_path)

    def _get(self, app_trio, opp_id=_Q_OPP_ID, params=None):
        flask_app, fake_get_db, _ = app_trio
        with patch("tools.dashboard.api.govcon._get_db", side_effect=fake_get_db):
            with flask_app.test_client() as c:
                resp = c.get(
                    f"/api/govcon/opportunities/{opp_id}/questions",
                    query_string=params or {},
                )
        return resp

    def test_stats_total_equals_question_count(self, app_trio):
        data = self._get(app_trio).get_json()
        assert data["stats"]["total"] == len(data["questions"]), (
            f"stats.total must equal len(questions), "
            f"got total={data['stats']['total']} vs len={len(data['questions'])}"
        )

    def test_stats_has_by_category(self, app_trio):
        data = self._get(app_trio).get_json()
        assert "by_category" in data["stats"]

    def test_stats_has_by_status(self, app_trio):
        data = self._get(app_trio).get_json()
        assert "by_status" in data["stats"]

    def test_stats_has_by_priority(self, app_trio):
        data = self._get(app_trio).get_json()
        assert "by_priority" in data["stats"]

    def test_by_category_scope_count_is_one(self, app_trio):
        data = self._get(app_trio).get_json()
        assert data["stats"]["by_category"].get("scope") == 1, (
            f"Expected by_category['scope']=1, got {data['stats']['by_category'].get('scope')}"
        )

    def test_by_category_evaluation_criteria_count_is_one(self, app_trio):
        data = self._get(app_trio).get_json()
        assert data["stats"]["by_category"].get("evaluation_criteria") == 1, (
            f"Expected by_category['evaluation_criteria']=1, "
            f"got {data['stats']['by_category'].get('evaluation_criteria')}"
        )

    def test_by_status_draft_count_is_two(self, app_trio):
        data = self._get(app_trio).get_json()
        assert data["stats"]["by_status"].get("draft") == 2, (
            f"Expected by_status['draft']=2, got {data['stats']['by_status'].get('draft')}"
        )

    def test_by_status_approved_count_is_one(self, app_trio):
        data = self._get(app_trio).get_json()
        assert data["stats"]["by_status"].get("approved") == 1, (
            f"Expected by_status['approved']=1, got {data['stats']['by_status'].get('approved')}"
        )

    def test_by_priority_high_count_is_one(self, app_trio):
        data = self._get(app_trio).get_json()
        assert data["stats"]["by_priority"].get("high") == 1, (
            f"Expected by_priority['high']=1, got {data['stats']['by_priority'].get('high')}"
        )

    def test_by_priority_low_count_is_one(self, app_trio):
        data = self._get(app_trio).get_json()
        assert data["stats"]["by_priority"].get("low") == 1, (
            f"Expected by_priority['low']=1, got {data['stats']['by_priority'].get('low')}"
        )

    def test_by_category_values_sum_to_total(self, app_trio):
        data = self._get(app_trio).get_json()
        cat_sum = sum(data["stats"]["by_category"].values())
        assert cat_sum == data["stats"]["total"], (
            f"Sum of by_category values {cat_sum} must equal total {data['stats']['total']}"
        )

    def test_by_status_values_sum_to_total(self, app_trio):
        data = self._get(app_trio).get_json()
        status_sum = sum(data["stats"]["by_status"].values())
        assert status_sum == data["stats"]["total"], (
            f"Sum of by_status values {status_sum} must equal total {data['stats']['total']}"
        )

    def test_by_priority_values_sum_to_total(self, app_trio):
        data = self._get(app_trio).get_json()
        pri_sum = sum(data["stats"]["by_priority"].values())
        assert pri_sum == data["stats"]["total"], (
            f"Sum of by_priority values {pri_sum} must equal total {data['stats']['total']}"
        )


# ---------------------------------------------------------------------------
# Tests: GET /api/govcon/opportunities/<id>/questions — filters (gcpl-dft-10)
# ---------------------------------------------------------------------------


class TestListQuestionsFilters:
    """Query-string filters narrow the returned questions list."""

    @pytest.fixture()
    def app_trio(self, tmp_path):
        return _build_questions_api_app(tmp_path)

    def _get(self, app_trio, opp_id=_Q_OPP_ID, params=None):
        flask_app, fake_get_db, _ = app_trio
        with patch("tools.dashboard.api.govcon._get_db", side_effect=fake_get_db):
            with flask_app.test_client() as c:
                resp = c.get(
                    f"/api/govcon/opportunities/{opp_id}/questions",
                    query_string=params or {},
                )
        return resp

    def test_filter_by_status_draft_returns_two(self, app_trio):
        data = self._get(app_trio, params={"status": "draft"}).get_json()
        assert len(data["questions"]) == 2, (
            f"status=draft filter must return 2, got {len(data['questions'])}"
        )

    def test_filter_by_status_approved_returns_one(self, app_trio):
        data = self._get(app_trio, params={"status": "approved"}).get_json()
        assert len(data["questions"]) == 1, (
            f"status=approved filter must return 1, got {len(data['questions'])}"
        )

    def test_filter_by_status_matches_returned_records(self, app_trio):
        data = self._get(app_trio, params={"status": "approved"}).get_json()
        for q in data["questions"]:
            assert q["status"] == "approved", (
                f"All returned questions must have status='approved', got {q['status']!r}"
            )

    def test_filter_by_category_scope_returns_one(self, app_trio):
        data = self._get(app_trio, params={"category": "scope"}).get_json()
        assert len(data["questions"]) == 1, (
            f"category=scope filter must return 1, got {len(data['questions'])}"
        )

    def test_filter_by_category_matches_returned_records(self, app_trio):
        data = self._get(app_trio, params={"category": "scope"}).get_json()
        for q in data["questions"]:
            assert q["category"] == "scope", (
                f"All returned questions must have category='scope', got {q['category']!r}"
            )

    def test_filter_by_priority_high_returns_one(self, app_trio):
        data = self._get(app_trio, params={"priority": "high"}).get_json()
        assert len(data["questions"]) == 1, (
            f"priority=high filter must return 1, got {len(data['questions'])}"
        )

    def test_filter_by_priority_matches_returned_records(self, app_trio):
        data = self._get(app_trio, params={"priority": "high"}).get_json()
        for q in data["questions"]:
            assert q["priority"] == "high", (
                f"All returned questions must have priority='high', got {q['priority']!r}"
            )

    def test_filter_by_source_auto_returns_two(self, app_trio):
        data = self._get(app_trio, params={"source": "auto"}).get_json()
        assert len(data["questions"]) == 2, (
            f"source=auto filter must return 2, got {len(data['questions'])}"
        )

    def test_filter_by_source_manual_returns_one(self, app_trio):
        data = self._get(app_trio, params={"source": "manual"}).get_json()
        assert len(data["questions"]) == 1, (
            f"source=manual filter must return 1, got {len(data['questions'])}"
        )

    def test_filter_no_match_returns_empty_list(self, app_trio):
        data = self._get(app_trio, params={"status": "submitted"}).get_json()
        assert data["questions"] == [], (
            f"Filter with no matching rows must return empty list, got {data['questions']!r}"
        )

    def test_filter_no_match_stats_total_is_zero(self, app_trio):
        data = self._get(app_trio, params={"status": "submitted"}).get_json()
        assert data["stats"]["total"] == 0, (
            f"stats.total must be 0 when no questions match filter, "
            f"got {data['stats']['total']}"
        )


# ---------------------------------------------------------------------------
# Schema + helpers for PUT /api/govcon/questions/<id>/status (gcpl-dft-11)
# ---------------------------------------------------------------------------

_QUESTION_STATUS_HISTORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS proposal_status_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    old_status TEXT,
    new_status TEXT NOT NULL,
    changed_by TEXT,
    reason TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
"""

_QS_OPP_ID = "opp-status-test"
_QS_DRAFT_ID = "q-status-draft"
_QS_APPROVED_ID = "q-status-approved"
_QS_SUBMITTED_ID = "q-status-submitted"
_QS_ANSWERED_ID = "q-status-answered"

_QS_DRAFT_ROW = {
    "id": _QS_DRAFT_ID,
    "opportunity_id": _QS_OPP_ID,
    "question_number": 1,
    "question_text": "What is the period of performance for this contract?",
    "category": "scope",
    "priority": "high",
    "source": "manual",
    "status": "draft",
    "classification": "CUI",
}
_QS_APPROVED_ROW = {
    "id": _QS_APPROVED_ID,
    "opportunity_id": _QS_OPP_ID,
    "question_number": 2,
    "question_text": "Please clarify the evaluation criteria weights for Section L.",
    "category": "evaluation_criteria",
    "priority": "medium",
    "source": "auto",
    "status": "approved",
    "classification": "CUI",
}
_QS_SUBMITTED_ROW = {
    "id": _QS_SUBMITTED_ID,
    "opportunity_id": _QS_OPP_ID,
    "question_number": 3,
    "question_text": "Are small business subcontracting requirements applicable?",
    "category": "small_business",
    "priority": "low",
    "source": "manual",
    "status": "submitted",
    "classification": "CUI",
}
_QS_ANSWERED_ROW = {
    "id": _QS_ANSWERED_ID,
    "opportunity_id": _QS_OPP_ID,
    "question_number": 4,
    "question_text": "What security clearance level is required for personnel?",
    "category": "compliance_security",
    "priority": "high",
    "source": "auto",
    "status": "answered",
    "classification": "CUI",
}


def _make_question_status_db(tmp_path):
    """Create a SQLite test DB with proposal_questions and proposal_status_history seeded."""
    db_path = tmp_path / "question_status_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_QUESTIONS_SCHEMA)
    conn.executescript(_QUESTION_STATUS_HISTORY_SCHEMA)
    for r in [_QS_DRAFT_ROW, _QS_APPROVED_ROW, _QS_SUBMITTED_ROW, _QS_ANSWERED_ROW]:
        conn.execute(
            """INSERT INTO proposal_questions
               (id, opportunity_id, question_number, question_text,
                category, priority, source, status, classification)
               VALUES (:id, :opportunity_id, :question_number, :question_text,
                       :category, :priority, :source, :status, :classification)""",
            r,
        )
    conn.commit()
    conn.close()
    return db_path


def _build_question_status_api_app(tmp_path):
    """Return (flask_app, fake_get_db, db_path) for question status transition tests."""
    db_path = _make_question_status_db(tmp_path)

    from _sql_compat import connect as _tconnect

    def fake_get_db():
        c = _tconnect(db_path)
        return c

    from tools.dashboard.api.govcon import govcon_api

    flask_app = Flask(__name__)
    flask_app.config["TESTING"] = True
    flask_app.register_blueprint(govcon_api)

    return flask_app, fake_get_db, db_path


# ---------------------------------------------------------------------------
# Tests: PUT /api/govcon/questions/<id>/status — response shape (gcpl-dft-11)
# ---------------------------------------------------------------------------


class TestChangeQuestionStatusResponse:
    """PUT /api/govcon/questions/<id>/status returns 200 JSON with correct shape."""

    @pytest.fixture()
    def app_trio(self, tmp_path):
        return _build_question_status_api_app(tmp_path)

    def _put(self, app_trio, q_id=_QS_DRAFT_ID, body=None):
        flask_app, fake_get_db, _ = app_trio
        if body is None:
            body = {"status": "approved"}
        with patch("tools.dashboard.api.govcon._get_db", side_effect=fake_get_db):
            with flask_app.test_client() as c:
                resp = c.put(f"/api/govcon/questions/{q_id}/status", json=body)
        return resp

    def test_returns_200(self, app_trio):
        resp = self._put(app_trio)
        assert resp.status_code == 200

    def test_content_type_is_json(self, app_trio):
        resp = self._put(app_trio)
        assert resp.content_type.startswith("application/json")

    def test_response_has_status_key(self, app_trio):
        data = self._put(app_trio).get_json()
        assert "status" in data

    def test_response_status_is_ok(self, app_trio):
        data = self._put(app_trio).get_json()
        assert data["status"] == "ok"

    def test_response_has_question_id(self, app_trio):
        data = self._put(app_trio).get_json()
        assert "question_id" in data

    def test_response_question_id_matches_request(self, app_trio):
        data = self._put(app_trio).get_json()
        assert data["question_id"] == _QS_DRAFT_ID

    def test_response_has_old_status(self, app_trio):
        data = self._put(app_trio).get_json()
        assert "old_status" in data

    def test_response_has_new_status(self, app_trio):
        data = self._put(app_trio).get_json()
        assert "new_status" in data

    def test_response_old_status_reflects_prior_state(self, app_trio):
        data = self._put(app_trio).get_json()
        assert data["old_status"] == "draft", (
            f"old_status must be 'draft' for a question seeded as draft, got {data['old_status']!r}"
        )

    def test_response_new_status_reflects_requested_transition(self, app_trio):
        data = self._put(app_trio).get_json()
        assert data["new_status"] == "approved", (
            f"new_status must be 'approved' after draft→approved transition, got {data['new_status']!r}"
        )


# ---------------------------------------------------------------------------
# Tests: PUT /api/govcon/questions/<id>/status — valid transitions (gcpl-dft-11)
# ---------------------------------------------------------------------------


class TestChangeQuestionStatusValidTransitions:
    """State machine allows only permitted forward and backward transitions."""

    @pytest.fixture()
    def app_trio(self, tmp_path):
        return _build_question_status_api_app(tmp_path)

    def _put(self, app_trio, q_id, new_status, changed_by=None):
        flask_app, fake_get_db, _ = app_trio
        body = {"status": new_status}
        if changed_by:
            body["changed_by"] = changed_by
        with patch("tools.dashboard.api.govcon._get_db", side_effect=fake_get_db):
            with flask_app.test_client() as c:
                resp = c.put(f"/api/govcon/questions/{q_id}/status", json=body)
        return resp

    def test_draft_to_approved_returns_200(self, app_trio):
        resp = self._put(app_trio, _QS_DRAFT_ID, "approved")
        assert resp.status_code == 200

    def test_draft_to_approved_new_status_in_response(self, app_trio):
        data = self._put(app_trio, _QS_DRAFT_ID, "approved").get_json()
        assert data["new_status"] == "approved", (
            f"new_status must be 'approved' after draft→approved, got {data.get('new_status')!r}"
        )

    def test_approved_to_submitted_returns_200(self, app_trio):
        resp = self._put(app_trio, _QS_APPROVED_ID, "submitted")
        assert resp.status_code == 200

    def test_approved_to_submitted_new_status_in_response(self, app_trio):
        data = self._put(app_trio, _QS_APPROVED_ID, "submitted").get_json()
        assert data["new_status"] == "submitted", (
            f"new_status must be 'submitted' after approved→submitted, got {data.get('new_status')!r}"
        )

    def test_approved_to_draft_returns_200(self, app_trio):
        resp = self._put(app_trio, _QS_APPROVED_ID, "draft")
        assert resp.status_code == 200

    def test_approved_to_draft_new_status_in_response(self, app_trio):
        data = self._put(app_trio, _QS_APPROVED_ID, "draft").get_json()
        assert data["new_status"] == "draft", (
            f"new_status must be 'draft' after approved→draft, got {data.get('new_status')!r}"
        )

    def test_submitted_to_answered_returns_200(self, app_trio):
        resp = self._put(app_trio, _QS_SUBMITTED_ID, "answered")
        assert resp.status_code == 200

    def test_submitted_to_answered_new_status_in_response(self, app_trio):
        data = self._put(app_trio, _QS_SUBMITTED_ID, "answered").get_json()
        assert data["new_status"] == "answered", (
            f"new_status must be 'answered' after submitted→answered, got {data.get('new_status')!r}"
        )


# ---------------------------------------------------------------------------
# Tests: PUT /api/govcon/questions/<id>/status — invalid transitions (gcpl-dft-11)
# ---------------------------------------------------------------------------


class TestChangeQuestionStatusInvalidTransitions:
    """State machine rejects forbidden transitions with 400 and an error key."""

    @pytest.fixture()
    def app_trio(self, tmp_path):
        return _build_question_status_api_app(tmp_path)

    def _put(self, app_trio, q_id, new_status):
        flask_app, fake_get_db, _ = app_trio
        with patch("tools.dashboard.api.govcon._get_db", side_effect=fake_get_db):
            with flask_app.test_client() as c:
                resp = c.put(
                    f"/api/govcon/questions/{q_id}/status",
                    json={"status": new_status},
                )
        return resp

    def test_draft_to_submitted_returns_400(self, app_trio):
        resp = self._put(app_trio, _QS_DRAFT_ID, "submitted")
        assert resp.status_code == 400

    def test_draft_to_answered_returns_400(self, app_trio):
        resp = self._put(app_trio, _QS_DRAFT_ID, "answered")
        assert resp.status_code == 400

    def test_draft_to_draft_returns_400(self, app_trio):
        resp = self._put(app_trio, _QS_DRAFT_ID, "draft")
        assert resp.status_code == 400

    def test_submitted_to_draft_returns_400(self, app_trio):
        resp = self._put(app_trio, _QS_SUBMITTED_ID, "draft")
        assert resp.status_code == 400

    def test_submitted_to_approved_returns_400(self, app_trio):
        resp = self._put(app_trio, _QS_SUBMITTED_ID, "approved")
        assert resp.status_code == 400

    def test_answered_to_submitted_returns_400(self, app_trio):
        resp = self._put(app_trio, _QS_ANSWERED_ID, "submitted")
        assert resp.status_code == 400

    def test_invalid_transition_response_has_error_key(self, app_trio):
        data = self._put(app_trio, _QS_DRAFT_ID, "submitted").get_json()
        assert "error" in data, (
            f"Invalid transition response must include 'error' key, got {list(data.keys())}"
        )


# ---------------------------------------------------------------------------
# Tests: PUT /api/govcon/questions/<id>/status — error cases (gcpl-dft-11)
# ---------------------------------------------------------------------------


class TestChangeQuestionStatusErrorCases:
    """Endpoint returns correct error codes for missing status and unknown question."""

    @pytest.fixture()
    def app_trio(self, tmp_path):
        return _build_question_status_api_app(tmp_path)

    def _put(self, app_trio, q_id, body=None):
        flask_app, fake_get_db, _ = app_trio
        with patch("tools.dashboard.api.govcon._get_db", side_effect=fake_get_db):
            with flask_app.test_client() as c:
                resp = c.put(f"/api/govcon/questions/{q_id}/status", json=body or {})
        return resp

    def test_missing_status_returns_400(self, app_trio):
        resp = self._put(app_trio, _QS_DRAFT_ID, body={})
        assert resp.status_code == 400

    def test_missing_status_response_has_error_key(self, app_trio):
        data = self._put(app_trio, _QS_DRAFT_ID, body={}).get_json()
        assert "error" in data, (
            f"Missing status response must include 'error' key, got {list(data.keys())}"
        )

    def test_unknown_question_id_returns_404(self, app_trio):
        resp = self._put(app_trio, "q-does-not-exist", body={"status": "approved"})
        assert resp.status_code == 404

    def test_unknown_question_id_response_has_error_key(self, app_trio):
        data = self._put(app_trio, "q-does-not-exist", body={"status": "approved"}).get_json()
        assert "error" in data, (
            f"Unknown question response must include 'error' key, got {list(data.keys())}"
        )


# ---------------------------------------------------------------------------
# Tests: PUT /api/govcon/questions/<id>/status — DB side effects (gcpl-dft-11)
# ---------------------------------------------------------------------------


class TestChangeQuestionStatusSideEffects:
    """Transitions persist updated status, set timestamp fields, and write a history record."""

    @pytest.fixture()
    def app_trio(self, tmp_path):
        return _build_question_status_api_app(tmp_path)

    def _put(self, app_trio, q_id, new_status, changed_by=None, notes=None):
        flask_app, fake_get_db, db_path = app_trio
        body = {"status": new_status}
        if changed_by:
            body["changed_by"] = changed_by
        if notes:
            body["notes"] = notes
        with patch("tools.dashboard.api.govcon._get_db", side_effect=fake_get_db):
            with flask_app.test_client() as c:
                c.put(f"/api/govcon/questions/{q_id}/status", json=body)
        return db_path

    def _fetch_question(self, db_path, q_id):
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM proposal_questions WHERE id = ?", (q_id,)).fetchone()
        conn.close()
        return dict(row) if row else None

    def _fetch_history(self, db_path, entity_id):
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM proposal_status_history WHERE entity_id = ?", (entity_id,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def test_draft_to_approved_persists_new_status(self, app_trio):
        db_path = self._put(app_trio, _QS_DRAFT_ID, "approved")
        q = self._fetch_question(db_path, _QS_DRAFT_ID)
        assert q["status"] == "approved", (
            f"DB status must be 'approved' after transition, got {q['status']!r}"
        )

    def test_draft_to_approved_sets_approved_by(self, app_trio):
        db_path = self._put(app_trio, _QS_DRAFT_ID, "approved", changed_by="reviewer_1")
        q = self._fetch_question(db_path, _QS_DRAFT_ID)
        assert q["approved_by"] == "reviewer_1", (
            f"approved_by must be set to changed_by on approval, got {q['approved_by']!r}"
        )

    def test_draft_to_approved_sets_approved_at(self, app_trio):
        db_path = self._put(app_trio, _QS_DRAFT_ID, "approved")
        q = self._fetch_question(db_path, _QS_DRAFT_ID)
        assert q["approved_at"] is not None, (
            "approved_at must be set to a timestamp after approval transition"
        )

    def test_approved_to_submitted_sets_submitted_at(self, app_trio):
        db_path = self._put(app_trio, _QS_APPROVED_ID, "submitted")
        q = self._fetch_question(db_path, _QS_APPROVED_ID)
        assert q["submitted_at"] is not None, (
            "submitted_at must be set to a timestamp after submitted transition"
        )

    def test_approved_to_submitted_persists_new_status(self, app_trio):
        db_path = self._put(app_trio, _QS_APPROVED_ID, "submitted")
        q = self._fetch_question(db_path, _QS_APPROVED_ID)
        assert q["status"] == "submitted", (
            f"DB status must be 'submitted' after approved→submitted, got {q['status']!r}"
        )

    def test_transition_creates_history_record(self, app_trio):
        db_path = self._put(app_trio, _QS_DRAFT_ID, "approved")
        history = self._fetch_history(db_path, _QS_DRAFT_ID)
        assert len(history) == 1, (
            f"One history record must be created per transition, got {len(history)}"
        )

    def test_history_record_has_correct_entity_type(self, app_trio):
        db_path = self._put(app_trio, _QS_DRAFT_ID, "approved")
        history = self._fetch_history(db_path, _QS_DRAFT_ID)
        assert history[0]["entity_type"] == "question", (
            f"History entity_type must be 'question', got {history[0]['entity_type']!r}"
        )

    def test_history_record_has_correct_old_status(self, app_trio):
        db_path = self._put(app_trio, _QS_DRAFT_ID, "approved")
        history = self._fetch_history(db_path, _QS_DRAFT_ID)
        assert history[0]["old_status"] == "draft", (
            f"History old_status must be 'draft', got {history[0]['old_status']!r}"
        )

    def test_history_record_has_correct_new_status(self, app_trio):
        db_path = self._put(app_trio, _QS_DRAFT_ID, "approved")
        history = self._fetch_history(db_path, _QS_DRAFT_ID)
        assert history[0]["new_status"] == "approved", (
            f"History new_status must be 'approved', got {history[0]['new_status']!r}"
        )

    def test_history_record_stores_changed_by(self, app_trio):
        db_path = self._put(app_trio, _QS_DRAFT_ID, "approved", changed_by="test_actor")
        history = self._fetch_history(db_path, _QS_DRAFT_ID)
        assert history[0]["changed_by"] == "test_actor", (
            f"History changed_by must be 'test_actor', got {history[0]['changed_by']!r}"
        )

    def test_history_record_stores_notes_as_reason(self, app_trio):
        db_path = self._put(app_trio, _QS_DRAFT_ID, "approved", notes="LPTA review complete")
        history = self._fetch_history(db_path, _QS_DRAFT_ID)
        assert history[0]["reason"] == "LPTA review complete", (
            f"History reason must be 'LPTA review complete', got {history[0]['reason']!r}"
        )


# ---------------------------------------------------------------------------
# Schema + helpers for GET /api/govcon/knowledge-base (gcpl-dft-12)
# ---------------------------------------------------------------------------

_KB_SCHEMA = """
CREATE TABLE IF NOT EXISTS proposal_knowledge_base (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'capability_description',
    domain TEXT NOT NULL DEFAULT 'general',
    volume_type TEXT DEFAULT 'technical',
    keywords TEXT DEFAULT '[]',
    naics_codes TEXT DEFAULT '[]',
    usage_count INTEGER DEFAULT 0,
    status TEXT DEFAULT 'active',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI // SP-CTI'
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

_KB_BLOCK_1 = {
    "id": "kb-001",
    "title": "DevSecOps Pipeline Capability",
    "content": "Our DevSecOps pipeline integrates SAST, DAST, and SCA scanning at every stage.",
    "category": "capability_description",
    "domain": "devsecops",
    "volume_type": "technical",
    "keywords": '["devsecops", "pipeline", "sast", "dast"]',
    "naics_codes": "[]",
    "usage_count": 5,
    "status": "active",
    "classification": "CUI // SP-CTI",
}
_KB_BLOCK_2 = {
    "id": "kb-002",
    "title": "Zero Trust Architecture Approach",
    "content": "We implement zero trust principles using identity-based access controls and micro-segmentation.",
    "category": "approach",
    "domain": "security",
    "volume_type": "technical",
    "keywords": '["zero trust", "identity", "micro-segmentation"]',
    "naics_codes": "[]",
    "usage_count": 3,
    "status": "active",
    "classification": "CUI // SP-CTI",
}
_KB_BLOCK_3 = {
    "id": "kb-003",
    "title": "AI/ML Model Training Staffing Plan",
    "content": "Our staffing plan includes data scientists, ML engineers, and DevOps specialists.",
    "category": "staffing",
    "domain": "ai_ml",
    "volume_type": "staffing",
    "keywords": '["ai", "ml", "data scientist", "staffing"]',
    "naics_codes": "[]",
    "usage_count": 1,
    "status": "active",
    "classification": "CUI // SP-CTI",
}

_KB_INSERT_SQL = (
    "INSERT INTO proposal_knowledge_base "
    "(id, title, content, category, domain, volume_type, keywords, naics_codes, "
    "usage_count, status, classification) "
    "VALUES (:id, :title, :content, :category, :domain, :volume_type, :keywords, "
    ":naics_codes, :usage_count, :status, :classification)"
)


def _make_kb_db(tmp_path, rows=None):
    """Create a SQLite test DB seeded with proposal_knowledge_base rows."""
    db_path = tmp_path / "kb_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_KB_SCHEMA)
    if rows is None:
        rows = [_KB_BLOCK_1, _KB_BLOCK_2, _KB_BLOCK_3]
    for r in rows:
        conn.execute(_KB_INSERT_SQL, r)
    conn.commit()
    conn.close()
    return db_path


def _build_kb_api_app(tmp_path, rows=None):
    """Return (flask_app, fake_get_db, db_path) for knowledge-base endpoint tests."""
    db_path = _make_kb_db(tmp_path, rows=rows)

    from _sql_compat import connect as _tconnect

    def fake_get_db():
        c = _tconnect(db_path)
        return c

    from tools.dashboard.api.govcon import govcon_api

    flask_app = Flask(__name__)
    flask_app.config["TESTING"] = True
    flask_app.register_blueprint(govcon_api)

    return flask_app, fake_get_db, db_path


# ---------------------------------------------------------------------------
# Tests: GET /api/govcon/knowledge-base — list response shape (gcpl-dft-12)
# ---------------------------------------------------------------------------


class TestKnowledgeBaseListResponse:
    """GET /api/govcon/knowledge-base (no query) returns 200 JSON with correct shape."""

    @pytest.fixture()
    def app_trio(self, tmp_path):
        return _build_kb_api_app(tmp_path)

    def _get(self, app_trio, params=None):
        flask_app, fake_get_db, _ = app_trio
        with patch("tools.govcon.knowledge_base._get_db", side_effect=fake_get_db):
            with flask_app.test_client() as c:
                resp = c.get("/api/govcon/knowledge-base", query_string=params or {})
        return resp

    def test_returns_200(self, app_trio):
        resp = self._get(app_trio)
        assert resp.status_code == 200

    def test_content_type_is_json(self, app_trio):
        resp = self._get(app_trio)
        assert resp.content_type.startswith("application/json"), (
            f"Content-Type must be application/json, got {resp.content_type!r}"
        )

    def test_response_has_status_key(self, app_trio):
        data = self._get(app_trio).get_json()
        assert "status" in data, (
            f"Response must include 'status' key, got {list(data.keys())}"
        )

    def test_response_status_is_ok(self, app_trio):
        data = self._get(app_trio).get_json()
        assert data["status"] == "ok", (
            f"Response status must be 'ok', got {data['status']!r}"
        )

    def test_response_has_total_key(self, app_trio):
        data = self._get(app_trio).get_json()
        assert "total" in data, (
            f"Response must include 'total' key, got {list(data.keys())}"
        )

    def test_response_total_is_integer(self, app_trio):
        data = self._get(app_trio).get_json()
        assert isinstance(data["total"], int), (
            f"Response 'total' must be an integer, got {type(data['total'])!r}"
        )

    def test_response_has_blocks_key(self, app_trio):
        data = self._get(app_trio).get_json()
        assert "blocks" in data, (
            f"Response must include 'blocks' key, got {list(data.keys())}"
        )

    def test_response_blocks_is_list(self, app_trio):
        data = self._get(app_trio).get_json()
        assert isinstance(data["blocks"], list), (
            f"Response 'blocks' must be a list, got {type(data['blocks'])!r}"
        )

    def test_total_matches_blocks_length(self, app_trio):
        data = self._get(app_trio).get_json()
        assert data["total"] == len(data["blocks"]), (
            f"total={data['total']} must equal len(blocks)={len(data['blocks'])}"
        )

    def test_returns_all_seeded_blocks(self, app_trio):
        data = self._get(app_trio).get_json()
        assert data["total"] == 3, (
            f"list with no filters must return all 3 seeded blocks, got {data['total']}"
        )

    def test_each_block_has_id_field(self, app_trio):
        data = self._get(app_trio).get_json()
        for blk in data["blocks"]:
            assert "id" in blk, f"Each block must have 'id' field, block keys: {list(blk.keys())}"

    def test_each_block_has_title_field(self, app_trio):
        data = self._get(app_trio).get_json()
        for blk in data["blocks"]:
            assert "title" in blk, f"Each block must have 'title' field, block keys: {list(blk.keys())}"

    def test_each_block_has_content_field(self, app_trio):
        data = self._get(app_trio).get_json()
        for blk in data["blocks"]:
            assert "content" in blk, f"Each block must have 'content' field, block keys: {list(blk.keys())}"

    def test_each_block_has_category_field(self, app_trio):
        data = self._get(app_trio).get_json()
        for blk in data["blocks"]:
            assert "category" in blk, f"Each block must have 'category' field, block keys: {list(blk.keys())}"

    def test_each_block_has_domain_field(self, app_trio):
        data = self._get(app_trio).get_json()
        for blk in data["blocks"]:
            assert "domain" in blk, f"Each block must have 'domain' field, block keys: {list(blk.keys())}"

    def test_empty_db_returns_total_zero(self, tmp_path):
        app_trio = _build_kb_api_app(tmp_path, rows=[])
        data = self._get(app_trio).get_json()
        assert data["total"] == 0, (
            f"Empty DB must return total=0, got {data['total']}"
        )

    def test_empty_db_returns_empty_blocks_list(self, tmp_path):
        app_trio = _build_kb_api_app(tmp_path, rows=[])
        data = self._get(app_trio).get_json()
        assert data["blocks"] == [], (
            f"Empty DB must return blocks=[], got {data['blocks']!r}"
        )


# ---------------------------------------------------------------------------
# Tests: GET /api/govcon/knowledge-base — domain/category filtering (gcpl-dft-12)
# ---------------------------------------------------------------------------


class TestKnowledgeBaseListFiltering:
    """GET /api/govcon/knowledge-base filters blocks by domain and category params."""

    @pytest.fixture()
    def app_trio(self, tmp_path):
        return _build_kb_api_app(tmp_path)

    def _get(self, app_trio, params=None):
        flask_app, fake_get_db, _ = app_trio
        with patch("tools.govcon.knowledge_base._get_db", side_effect=fake_get_db):
            with flask_app.test_client() as c:
                resp = c.get("/api/govcon/knowledge-base", query_string=params or {})
        return resp

    def test_filter_by_domain_devsecops_returns_one(self, app_trio):
        data = self._get(app_trio, params={"domain": "devsecops"}).get_json()
        assert data["total"] == 1, (
            f"domain=devsecops filter must return 1 block, got {data['total']}"
        )

    def test_filter_by_domain_matches_returned_records(self, app_trio):
        data = self._get(app_trio, params={"domain": "devsecops"}).get_json()
        for blk in data["blocks"]:
            assert blk["domain"] == "devsecops", (
                f"All returned blocks must have domain='devsecops', got {blk['domain']!r}"
            )

    def test_filter_by_domain_security_returns_one(self, app_trio):
        data = self._get(app_trio, params={"domain": "security"}).get_json()
        assert data["total"] == 1, (
            f"domain=security filter must return 1 block, got {data['total']}"
        )

    def test_filter_by_category_approach_returns_one(self, app_trio):
        data = self._get(app_trio, params={"category": "approach"}).get_json()
        assert data["total"] == 1, (
            f"category=approach filter must return 1 block, got {data['total']}"
        )

    def test_filter_by_category_matches_returned_records(self, app_trio):
        data = self._get(app_trio, params={"category": "approach"}).get_json()
        for blk in data["blocks"]:
            assert blk["category"] == "approach", (
                f"All returned blocks must have category='approach', got {blk['category']!r}"
            )

    def test_filter_by_category_staffing_returns_one(self, app_trio):
        data = self._get(app_trio, params={"category": "staffing"}).get_json()
        assert data["total"] == 1, (
            f"category=staffing filter must return 1 block, got {data['total']}"
        )

    def test_filter_no_domain_match_returns_empty(self, app_trio):
        data = self._get(app_trio, params={"domain": "cloud"}).get_json()
        assert data["total"] == 0, (
            f"domain=cloud filter (no matching blocks) must return total=0, got {data['total']}"
        )

    def test_filter_no_domain_match_blocks_list_is_empty(self, app_trio):
        data = self._get(app_trio, params={"domain": "cloud"}).get_json()
        assert data["blocks"] == [], (
            f"domain=cloud filter (no match) must return empty blocks, got {data['blocks']!r}"
        )

    def test_combined_domain_and_category_filter(self, app_trio):
        data = self._get(app_trio, params={"domain": "devsecops", "category": "capability_description"}).get_json()
        assert data["total"] == 1, (
            f"domain=devsecops + category=capability_description must return 1, got {data['total']}"
        )

    def test_combined_filter_wrong_category_returns_empty(self, app_trio):
        data = self._get(app_trio, params={"domain": "devsecops", "category": "staffing"}).get_json()
        assert data["total"] == 0, (
            f"domain=devsecops + category=staffing must return 0 (no match), got {data['total']}"
        )


# ---------------------------------------------------------------------------
# Tests: GET /api/govcon/knowledge-base?q= — search response (gcpl-dft-12)
# ---------------------------------------------------------------------------


class TestKnowledgeBaseSearchResponse:
    """GET /api/govcon/knowledge-base?q=<query> returns search results with correct shape."""

    @pytest.fixture()
    def app_trio(self, tmp_path):
        return _build_kb_api_app(tmp_path)

    def _get(self, app_trio, params=None):
        flask_app, fake_get_db, _ = app_trio
        with patch("tools.govcon.knowledge_base._get_db", side_effect=fake_get_db):
            with flask_app.test_client() as c:
                resp = c.get("/api/govcon/knowledge-base", query_string=params or {})
        return resp

    def test_search_returns_200(self, app_trio):
        resp = self._get(app_trio, params={"q": "devsecops pipeline"})
        assert resp.status_code == 200

    def test_search_response_has_status_key(self, app_trio):
        data = self._get(app_trio, params={"q": "devsecops pipeline"}).get_json()
        assert "status" in data, (
            f"Search response must include 'status' key, got {list(data.keys())}"
        )

    def test_search_response_status_is_ok(self, app_trio):
        data = self._get(app_trio, params={"q": "devsecops pipeline"}).get_json()
        assert data["status"] == "ok", (
            f"Search response status must be 'ok', got {data['status']!r}"
        )

    def test_search_response_has_query_key(self, app_trio):
        data = self._get(app_trio, params={"q": "devsecops pipeline"}).get_json()
        assert "query" in data, (
            f"Search response must include 'query' key, got {list(data.keys())}"
        )

    def test_search_response_query_matches_input(self, app_trio):
        data = self._get(app_trio, params={"q": "devsecops pipeline"}).get_json()
        assert data["query"] == "devsecops pipeline", (
            f"Response 'query' must echo the input, got {data['query']!r}"
        )

    def test_search_response_has_results_key(self, app_trio):
        data = self._get(app_trio, params={"q": "devsecops pipeline"}).get_json()
        assert "results" in data, (
            f"Search response must include 'results' key, got {list(data.keys())}"
        )

    def test_search_response_results_is_list(self, app_trio):
        data = self._get(app_trio, params={"q": "devsecops pipeline"}).get_json()
        assert isinstance(data["results"], list), (
            f"Search response 'results' must be a list, got {type(data['results'])!r}"
        )

    def test_search_matching_term_returns_result(self, app_trio):
        data = self._get(app_trio, params={"q": "devsecops pipeline"}).get_json()
        assert len(data["results"]) >= 1, (
            f"Search for 'devsecops pipeline' must return at least 1 result, got {len(data['results'])}"
        )

    def test_search_result_contains_matching_block_id(self, app_trio):
        data = self._get(app_trio, params={"q": "devsecops pipeline"}).get_json()
        ids = [r["id"] for r in data["results"]]
        assert _KB_BLOCK_1["id"] in ids, (
            f"Search 'devsecops pipeline' must include block {_KB_BLOCK_1['id']!r}, got ids={ids}"
        )

    def test_search_no_match_returns_empty_results(self, app_trio):
        data = self._get(app_trio, params={"q": "xyzzy_nonexistent_term_999"}).get_json()
        assert data["results"] == [], (
            f"Search with no matching term must return empty results, got {data['results']!r}"
        )

    def test_search_no_match_still_has_query_key(self, app_trio):
        data = self._get(app_trio, params={"q": "xyzzy_nonexistent_term_999"}).get_json()
        assert "query" in data, (
            f"Empty search response must still include 'query' key, got {list(data.keys())}"
        )


# ---------------------------------------------------------------------------
# Tests: POST /api/govcon/knowledge-base — creates new KB entry (gcpl-dft-13)
# ---------------------------------------------------------------------------

_NEW_BLOCK_PAYLOAD = {
    "title": "Agile Delivery Framework",
    "content": "We use SAFe and Scrum to deliver incremental value aligned with customer priorities.",
    "category": "approach",
    "domain": "agile",
    "volume_type": "management",
    "keywords": ["agile", "safe", "scrum"],
}


def _post_kb(app_trio, payload=None, content_type="application/json"):
    flask_app, fake_get_db, db_path = app_trio
    with patch("tools.govcon.knowledge_base._get_db", side_effect=fake_get_db):
        with flask_app.test_client() as c:
            resp = c.post(
                "/api/govcon/knowledge-base",
                json=payload if content_type == "application/json" else None,
                data=payload if content_type != "application/json" else None,
                content_type=content_type,
            )
    return resp, db_path


class TestCreateKnowledgeBlockStatus:
    """POST /api/govcon/knowledge-base returns 200 with status=ok on valid input."""

    @pytest.fixture()
    def app_trio(self, tmp_path):
        return _build_kb_api_app(tmp_path, rows=[])

    def test_returns_200(self, app_trio):
        resp, _ = _post_kb(app_trio, _NEW_BLOCK_PAYLOAD)
        assert resp.status_code == 200, (
            f"POST /api/govcon/knowledge-base must return 200, got {resp.status_code}"
        )

    def test_content_type_is_json(self, app_trio):
        resp, _ = _post_kb(app_trio, _NEW_BLOCK_PAYLOAD)
        assert resp.content_type.startswith("application/json"), (
            f"Content-Type must be application/json, got {resp.content_type!r}"
        )

    def test_response_status_is_ok(self, app_trio):
        data, _ = _post_kb(app_trio, _NEW_BLOCK_PAYLOAD)
        data = data.get_json()
        assert data.get("status") == "ok", (
            f"Response status must be 'ok', got {data.get('status')!r}"
        )


class TestCreateKnowledgeBlockResponseShape:
    """POST response includes block_id and title fields."""

    @pytest.fixture()
    def app_trio(self, tmp_path):
        return _build_kb_api_app(tmp_path, rows=[])

    def _post(self, app_trio, payload=None):
        resp, db_path = _post_kb(app_trio, payload or _NEW_BLOCK_PAYLOAD)
        return resp.get_json(), db_path

    def test_response_has_block_id_key(self, app_trio):
        data, _ = self._post(app_trio)
        assert "block_id" in data, (
            f"Response must include 'block_id', got keys={list(data.keys())}"
        )

    def test_block_id_is_string(self, app_trio):
        data, _ = self._post(app_trio)
        assert isinstance(data["block_id"], str), (
            f"'block_id' must be a string, got {type(data['block_id'])!r}"
        )

    def test_block_id_is_not_empty(self, app_trio):
        data, _ = self._post(app_trio)
        assert data["block_id"], "Response 'block_id' must not be empty"

    def test_response_has_title_key(self, app_trio):
        data, _ = self._post(app_trio)
        assert "title" in data, (
            f"Response must include 'title', got keys={list(data.keys())}"
        )

    def test_response_title_matches_payload(self, app_trio):
        data, _ = self._post(app_trio)
        assert data["title"] == _NEW_BLOCK_PAYLOAD["title"], (
            f"Response 'title' must match payload, expected {_NEW_BLOCK_PAYLOAD['title']!r}, "
            f"got {data['title']!r}"
        )


class TestCreateKnowledgeBlockPersistence:
    """POST /api/govcon/knowledge-base persists the entry to the database."""

    @pytest.fixture()
    def app_trio(self, tmp_path):
        return _build_kb_api_app(tmp_path, rows=[])

    def test_block_exists_in_db_after_post(self, app_trio):
        resp, db_path = _post_kb(app_trio, _NEW_BLOCK_PAYLOAD)
        block_id = resp.get_json()["block_id"]

        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT id FROM proposal_knowledge_base WHERE id = ?", (block_id,)
        ).fetchone()
        conn.close()

        assert row is not None, (
            f"Block {block_id!r} must exist in proposal_knowledge_base after POST"
        )

    def test_db_record_title_matches_payload(self, app_trio):
        resp, db_path = _post_kb(app_trio, _NEW_BLOCK_PAYLOAD)
        block_id = resp.get_json()["block_id"]

        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT title FROM proposal_knowledge_base WHERE id = ?", (block_id,)
        ).fetchone()
        conn.close()

        assert row[0] == _NEW_BLOCK_PAYLOAD["title"], (
            f"DB title must match payload '{_NEW_BLOCK_PAYLOAD['title']}', got {row[0]!r}"
        )

    def test_db_record_category_matches_payload(self, app_trio):
        resp, db_path = _post_kb(app_trio, _NEW_BLOCK_PAYLOAD)
        block_id = resp.get_json()["block_id"]

        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT category FROM proposal_knowledge_base WHERE id = ?", (block_id,)
        ).fetchone()
        conn.close()

        assert row[0] == _NEW_BLOCK_PAYLOAD["category"], (
            f"DB category must be {_NEW_BLOCK_PAYLOAD['category']!r}, got {row[0]!r}"
        )

    def test_db_record_domain_matches_payload(self, app_trio):
        resp, db_path = _post_kb(app_trio, _NEW_BLOCK_PAYLOAD)
        block_id = resp.get_json()["block_id"]

        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT domain FROM proposal_knowledge_base WHERE id = ?", (block_id,)
        ).fetchone()
        conn.close()

        assert row[0] == _NEW_BLOCK_PAYLOAD["domain"], (
            f"DB domain must be {_NEW_BLOCK_PAYLOAD['domain']!r}, got {row[0]!r}"
        )

    def test_db_record_classification_is_cui(self, app_trio):
        resp, db_path = _post_kb(app_trio, _NEW_BLOCK_PAYLOAD)
        block_id = resp.get_json()["block_id"]

        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT classification FROM proposal_knowledge_base WHERE id = ?", (block_id,)
        ).fetchone()
        conn.close()

        assert row[0] == "CUI // SP-CTI", (
            f"DB classification must be 'CUI // SP-CTI', got {row[0]!r}"
        )


class TestCreateKnowledgeBlockDefaults:
    """POST with minimal payload uses default category and domain."""

    @pytest.fixture()
    def app_trio(self, tmp_path):
        return _build_kb_api_app(tmp_path, rows=[])

    def test_default_category_is_capability_description(self, app_trio):
        minimal = {"title": "Minimal Block", "content": "Some content here."}
        resp, db_path = _post_kb(app_trio, minimal)
        block_id = resp.get_json()["block_id"]

        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT category FROM proposal_knowledge_base WHERE id = ?", (block_id,)
        ).fetchone()
        conn.close()

        assert row[0] == "capability_description", (
            f"Default category must be 'capability_description', got {row[0]!r}"
        )

    def test_default_domain_is_general(self, app_trio):
        minimal = {"title": "Minimal Block", "content": "Some content here."}
        resp, db_path = _post_kb(app_trio, minimal)
        block_id = resp.get_json()["block_id"]

        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT domain FROM proposal_knowledge_base WHERE id = ?", (block_id,)
        ).fetchone()
        conn.close()

        assert row[0] == "general", (
            f"Default domain must be 'general', got {row[0]!r}"
        )

    def test_empty_body_uses_defaults(self, app_trio):
        resp, _ = _post_kb(app_trio, {})
        data = resp.get_json()
        assert data.get("status") == "ok", (
            f"Empty payload must succeed with defaults, got status={data.get('status')!r}"
        )


class TestCreateKnowledgeBlockValidation:
    """POST with invalid category or domain returns error status (no 500)."""

    @pytest.fixture()
    def app_trio(self, tmp_path):
        return _build_kb_api_app(tmp_path, rows=[])

    def test_invalid_category_returns_200(self, app_trio):
        bad = {**_NEW_BLOCK_PAYLOAD, "category": "not_a_real_category"}
        resp, _ = _post_kb(app_trio, bad)
        assert resp.status_code == 200, (
            f"Invalid category must return 200 (add_block handles it), got {resp.status_code}"
        )

    def test_invalid_category_response_status_is_error(self, app_trio):
        bad = {**_NEW_BLOCK_PAYLOAD, "category": "not_a_real_category"}
        data = _post_kb(app_trio, bad)[0].get_json()
        assert data.get("status") == "error", (
            f"Invalid category must yield status='error', got {data.get('status')!r}"
        )

    def test_invalid_category_response_has_message(self, app_trio):
        bad = {**_NEW_BLOCK_PAYLOAD, "category": "not_a_real_category"}
        data = _post_kb(app_trio, bad)[0].get_json()
        assert "message" in data, (
            f"Error response must include 'message', got keys={list(data.keys())}"
        )

    def test_invalid_domain_returns_200(self, app_trio):
        bad = {**_NEW_BLOCK_PAYLOAD, "domain": "not_a_real_domain"}
        resp, _ = _post_kb(app_trio, bad)
        assert resp.status_code == 200, (
            f"Invalid domain must return 200 (add_block handles it), got {resp.status_code}"
        )

    def test_invalid_domain_response_status_is_error(self, app_trio):
        bad = {**_NEW_BLOCK_PAYLOAD, "domain": "not_a_real_domain"}
        data = _post_kb(app_trio, bad)[0].get_json()
        assert data.get("status") == "error", (
            f"Invalid domain must yield status='error', got {data.get('status')!r}"
        )


class TestCreateKnowledgeBlockException:
    """POST /api/govcon/knowledge-base returns 500 when add_block raises."""

    @pytest.fixture()
    def app_trio(self, tmp_path):
        return _build_kb_api_app(tmp_path, rows=[])

    def test_add_block_exception_returns_500(self, app_trio):
        flask_app, _, _ = app_trio
        with patch(
            "tools.govcon.knowledge_base.add_block",
            side_effect=RuntimeError("DB write failed"),
        ):
            with flask_app.test_client() as c:
                resp = c.post("/api/govcon/knowledge-base", json=_NEW_BLOCK_PAYLOAD)
        assert resp.status_code == 500, (
            f"add_block exception must yield 500, got {resp.status_code}"
        )

    def test_add_block_exception_response_has_error_key(self, app_trio):
        flask_app, _, _ = app_trio
        with patch(
            "tools.govcon.knowledge_base.add_block",
            side_effect=RuntimeError("DB write failed"),
        ):
            with flask_app.test_client() as c:
                resp = c.post("/api/govcon/knowledge-base", json=_NEW_BLOCK_PAYLOAD)
        data = resp.get_json()
        assert "error" in data, (
            f"500 response must include 'error' key, got keys={list(data.keys())}"
        )
