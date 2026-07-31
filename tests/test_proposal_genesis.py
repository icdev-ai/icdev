#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for Proposal Genesis — autonomous capture-to-delivery daemon (Phases A–F).

Covers: daemon, reflexes (discover, extract, map, draft, polish, scout, shape, engage, publish,
monitor, fulfill, decide, analyze, train), dashboard API including Phase B–F endpoints.
"""

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import translate_sql  # noqa: E402


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def pg_db(tmp_path):
    """Create a minimal in-memory DB with Proposal Genesis tables."""
    db_path = tmp_path / "test_pg.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")

    # Core tables needed by reflexes
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS proposal_opportunities (
            id TEXT PRIMARY KEY, title TEXT, description TEXT,
            status TEXT DEFAULT 'tracking',
            sam_gov_opportunity_id TEXT,
            agency TEXT, naics_code TEXT,
            response_deadline TEXT, estimated_value REAL,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS rfp_shall_statements (
            id TEXT PRIMARY KEY, opportunity_id TEXT, statement_text TEXT,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS icdev_capability_map (
            id TEXT PRIMARY KEY, opportunity_id TEXT,
            coverage_score REAL, created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS proposal_section_drafts (
            id TEXT PRIMARY KEY, opportunity_id TEXT,
            section_id TEXT,
            section_text TEXT, draft_content TEXT,
            domain_category TEXT,
            confidence_score REAL DEFAULT 0.0,
            status TEXT DEFAULT 'draft',
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS rfp_requirement_patterns (
            id TEXT PRIMARY KEY, opportunity_id TEXT,
            pattern_text TEXT, created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS proposal_knowledge_base (
            id TEXT PRIMARY KEY, title TEXT, content TEXT,
            category TEXT, domain TEXT, volume_type TEXT,
            keywords TEXT, usage_count INTEGER DEFAULT 0,
            created_at TEXT, updated_at TEXT, classification TEXT
        );
        CREATE TABLE IF NOT EXISTS pg_proposal_genesis_audit (
            id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            reflex_name TEXT,
            risk_tier TEXT,
            opportunity_id TEXT,
            details TEXT,
            success INTEGER,
            duration_ms INTEGER,
            metric_name TEXT,
            metric_value REAL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS pg_proposal_genesis_state (
            reflex_name TEXT PRIMARY KEY, enabled INTEGER DEFAULT 1,
            last_run_at TEXT, next_run_at TEXT,
            consecutive_failures INTEGER DEFAULT 0,
            circuit_breaker_open INTEGER DEFAULT 0,
            circuit_breaker_tripped_at TEXT,
            total_runs INTEGER DEFAULT 0,
            total_successes INTEGER DEFAULT 0,
            total_failures INTEGER DEFAULT 0,
            last_metric_value REAL,
            last_error TEXT,
            updated_at TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS pg_amendment_diffs (
            id TEXT PRIMARY KEY, opportunity_id TEXT,
            diff_type TEXT, section TEXT,
            old_text TEXT, new_text TEXT,
            re_extracted INTEGER DEFAULT 0,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS pg_pulse_proposal_links (
            id TEXT PRIMARY KEY, opportunity_id TEXT,
            pulse_post_id TEXT, section_id TEXT,
            link_type TEXT,
            relevance_score REAL, created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS pulse_posts (
            id TEXT PRIMARY KEY, title TEXT NOT NULL,
            slug TEXT, status TEXT NOT NULL DEFAULT 'draft',
            topic TEXT, body_markdown TEXT,
            readability_score REAL DEFAULT 0.0,
            author_id TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS pg_proposal_quality_scores (
            id TEXT PRIMARY KEY, opportunity_id TEXT,
            draft_id TEXT, composite_score REAL,
            grammar_score REAL, readability_score REAL,
            tone_score REAL, plagiarism_score REAL,
            ai_detection_score REAL,
            check_details TEXT, created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS pg_teaming_partners (
            id TEXT PRIMARY KEY, name TEXT NOT NULL,
            partner_type TEXT NOT NULL DEFAULT 'subcontractor',
            capabilities TEXT, past_performance TEXT,
            contract_vehicles TEXT, certifications TEXT,
            set_asides TEXT,
            status TEXT DEFAULT 'active',
            created_at TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS pg_capture_plans (
            id TEXT PRIMARY KEY,
            opportunity_id TEXT NOT NULL,
            status TEXT DEFAULT 'draft',
            win_strategy TEXT, discriminators TEXT,
            teaming_strategy TEXT, price_strategy TEXT,
            gate_reviews TEXT,
            current_phase TEXT DEFAULT 'qualify',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS pg_capture_gate_decisions (
            id TEXT PRIMARY KEY,
            capture_plan_id TEXT NOT NULL,
            opportunity_id TEXT,
            from_phase TEXT NOT NULL,
            to_phase TEXT NOT NULL,
            decision TEXT NOT NULL,
            rationale TEXT,
            decided_by TEXT,
            gate_criteria_met TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS pg_capture_activities (
            id TEXT PRIMARY KEY,
            capture_plan_id TEXT NOT NULL,
            activity_type TEXT NOT NULL,
            description TEXT, assigned_to TEXT,
            due_date TEXT,
            status TEXT DEFAULT 'pending',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS pg_teaming_assessments (
            id TEXT PRIMARY KEY,
            opportunity_id TEXT NOT NULL,
            partner_id TEXT NOT NULL,
            fit_score REAL DEFAULT 0.0,
            capability_gaps_filled TEXT,
            risk_assessment TEXT,
            recommendation TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS govcon_awards (
            id TEXT PRIMARY KEY,
            solicitation_number TEXT, title TEXT,
            agency TEXT, naics_code TEXT,
            awardee_name TEXT NOT NULL,
            award_amount REAL, award_date TEXT,
            content_hash TEXT NOT NULL DEFAULT '',
            discovered_at TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS audit_trail (
            id TEXT PRIMARY KEY, timestamp TEXT,
            event_type TEXT, actor TEXT,
            action TEXT, details TEXT,
            session_id TEXT
        );
        CREATE TABLE IF NOT EXISTS pg_crm_accounts (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, agency TEXT,
            sub_agency TEXT, account_type TEXT DEFAULT 'other',
            website TEXT, naics_codes TEXT, set_asides TEXT,
            notes TEXT, status TEXT DEFAULT 'active',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS pg_crm_contacts (
            id TEXT PRIMARY KEY, account_id TEXT, name TEXT,
            title TEXT, email TEXT, phone TEXT,
            role_in_procurement TEXT, influence_level TEXT DEFAULT 'unknown',
            notes TEXT, last_contact_at TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS pg_crm_interactions (
            id TEXT PRIMARY KEY, contact_id TEXT, account_id TEXT,
            interaction_type TEXT DEFAULT 'other',
            subject TEXT, notes TEXT, opportunity_id TEXT,
            interaction_date TEXT, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS pg_crm_engagement_scores (
            id TEXT PRIMARY KEY, account_id TEXT NOT NULL,
            score REAL DEFAULT 0.0, score_breakdown TEXT,
            interaction_count INTEGER DEFAULT 0,
            last_interaction_at TEXT, opportunity_count INTEGER DEFAULT 0,
            win_rate REAL DEFAULT 0.0, created_at TEXT NOT NULL
        );
    """)
    conn.commit()
    # Wrapped, not bare: tests patch get_connection with this object, and the
    # reflexes it stands in for author %s for PostgreSQL. Translation is a
    # no-op for the ? SQL this fixture's own setup uses, so wrapping is safe
    # for every existing consumer.
    yield _TranslatingConn(conn), db_path
    conn.close()


@pytest.fixture
def pg_config():
    """Minimal proposal genesis config."""
    return {
        "enabled": True,
        "reflexes": {
            "discover": {"enabled": True, "risk_tier": "green", "schedule": "every_6h"},
            "extract": {"enabled": True, "risk_tier": "green", "schedule": "on_demand"},
            "map": {"enabled": True, "risk_tier": "green", "schedule": "on_demand"},
            "draft": {"enabled": True, "risk_tier": "yellow", "schedule": "on_demand"},
            "polish": {"enabled": True, "risk_tier": "green", "schedule": "on_demand"},
            "scout": {"enabled": True, "risk_tier": "green", "schedule": "daily_07"},
            "shape": {"enabled": True, "risk_tier": "yellow", "schedule": "daily_08"},
        },
        "trust_kernel": {"tiers": {"green": {"auto_approve": True}, "yellow": {"auto_approve": True}}},
        "quality_threshold": 0.65,
    }


# ── Daemon Tests ──────────────────────────────────────────────────────────────


class TestDaemonModule:
    """Tests for proposal_genesis/daemon.py."""

    def test_reflex_names_constant(self):
        from tools.proposal_genesis.daemon import REFLEX_NAMES

        assert len(REFLEX_NAMES) == 24
        assert "discover" in REFLEX_NAMES
        assert "polish" in REFLEX_NAMES
        assert "train" in REFLEX_NAMES

    def test_phase_a_reflexes_constant(self):
        from tools.proposal_genesis.daemon import PHASE_A_REFLEXES

        assert PHASE_A_REFLEXES == ["discover", "extract", "map", "draft", "polish"]

    def test_phase_b_reflexes_constant(self):
        from tools.proposal_genesis.daemon import PHASE_B_REFLEXES

        assert PHASE_B_REFLEXES == ["scout", "shape"]

    def test_pipeline_chain_constant(self):
        from tools.proposal_genesis.daemon import PIPELINE_CHAIN

        assert PIPELINE_CHAIN["discover"] == "extract"
        assert PIPELINE_CHAIN["extract"] == "map"
        assert PIPELINE_CHAIN["map"] == "draft"
        assert PIPELINE_CHAIN["draft"] == "polish"
        assert PIPELINE_CHAIN["polish"] == "review"
        assert PIPELINE_CHAIN["review"] == "decide"

    def test_reflex_state_init(self):
        from tools.proposal_genesis.daemon import ReflexState

        state = ReflexState(name="test", config={"enabled": True})
        assert state.name == "test"
        assert state.config["enabled"] is True

    def test_trust_kernel_can_execute_green(self, pg_config):
        from tools.proposal_genesis.daemon import TrustKernel

        tk = TrustKernel(pg_config)
        allowed, reason = tk.can_execute("green")
        assert allowed is True

    def test_trust_kernel_can_execute_yellow(self, pg_config):
        from tools.proposal_genesis.daemon import TrustKernel

        tk = TrustKernel(pg_config)
        allowed, reason = tk.can_execute("yellow")
        assert allowed is True

    @patch("tools.proposal_genesis.daemon.get_connection")
    def test_daemon_get_status(self, mock_conn, pg_config):
        from tools.proposal_genesis.daemon import ProposalGenesisDaemon

        mock_db = MagicMock()
        mock_db.execute.return_value.fetchall.return_value = []
        mock_db.execute.return_value.fetchone.return_value = {"cnt": 0}
        mock_conn.return_value = mock_db
        daemon = ProposalGenesisDaemon(pg_config)
        status = daemon.get_status()
        assert "daemon" in status
        assert "reflexes" in status
        assert status["daemon"]["enabled"] is True


# ── Polish Reflex Tests ───────────────────────────────────────────────────────


class TestPolishReflex:
    """Tests for reflexes/polish.py quality checks."""

    def test_check_grammar_clean(self):
        from tools.proposal_genesis.reflexes.polish import _check_grammar

        result = _check_grammar("This is a clean sentence. Another one here.")
        assert result["score"] >= 0.7
        assert isinstance(result["issues"], list)

    def test_check_grammar_issues(self):
        from tools.proposal_genesis.reflexes.polish import _check_grammar

        result = _check_grammar("this  is  bad.  not capitalized. the the repeated")
        assert result["score"] < 1.0
        assert len(result["issues"]) > 0

    def test_check_readability(self):
        from tools.proposal_genesis.reflexes.polish import _check_readability

        text = (
            "Our team will deliver a comprehensive solution for the government's needs. "
            "We have extensive experience in cybersecurity and compliance frameworks. "
            "The proposed approach leverages industry best practices for secure development."
        )
        result = _check_readability(text)
        assert "score" in result
        assert "grade_level" in result
        assert result["grade_level"] > 0

    def test_check_tone_professional(self):
        from tools.proposal_genesis.reflexes.polish import _check_tone

        text = (
            "We will implement a proven DevSecOps pipeline that demonstrates "
            "our expertise in secure software delivery. Our team has delivered "
            "compliant solutions for over 10 government clients."
        )
        result = _check_tone(text)
        assert result["score"] >= 0.7

    def test_check_tone_informal(self):
        from tools.proposal_genesis.reflexes.polish import _check_tone

        text = "We're gonna try to basically do stuff and things that are pretty much ok."
        result = _check_tone(text)
        assert result["score"] < 0.8
        assert len(result["issues"]) > 0

    def test_check_ai_detection(self):
        from tools.proposal_genesis.reflexes.polish import _check_ai_detection

        text = " ".join(["This is sentence number {}.".format(i) for i in range(20)])
        result = _check_ai_detection(text)
        assert "score" in result
        assert "burstiness" in result

    def test_compute_composite_score(self):
        from tools.proposal_genesis.reflexes.polish import _compute_composite_score

        checks = {
            "grammar": {"score": 0.9},
            "readability": {"score": 0.8},
            "tone": {"score": 0.85},
            "plagiarism": {"score": 1.0},
            "ai_detection": {"score": 0.7},
        }
        score = _compute_composite_score(checks)
        assert 0 <= score <= 1.0
        # Weighted: grammar(0.18) + readability(0.22) + tone(0.22) +
        # plagiarism(0.14) + ai_detection(0.14) + stub_detection(0.10)
        # stub_detection not in checks dict, so contributes 0
        expected = 0.9 * 0.18 + 0.8 * 0.22 + 0.85 * 0.22 + 1.0 * 0.14 + 0.7 * 0.14
        assert abs(score - round(expected, 3)) < 0.01

    def test_count_syllables(self):
        from tools.proposal_genesis.reflexes.polish import _count_syllables

        assert _count_syllables("cat") == 1
        assert _count_syllables("hello") == 2
        assert _count_syllables("computer") >= 2

    def test_get_ngrams(self):
        from tools.proposal_genesis.reflexes.polish import _get_ngrams

        ngrams = _get_ngrams("hello world", 4)
        assert isinstance(ngrams, set)
        assert len(ngrams) > 0
        assert "hell" in ngrams


# ── Draft Reflex Tests ────────────────────────────────────────────────────────


class TestDraftReflex:
    """Tests for reflexes/draft.py."""

    def test_extract_keywords(self):
        from tools.proposal_genesis.reflexes.draft import _extract_keywords

        kws = _extract_keywords("Cybersecurity Assessment", "Provide comprehensive security testing for DoD systems")
        assert isinstance(kws, list)
        assert len(kws) > 0
        assert "cybersecurity" in kws or "security" in kws

    def test_keyword_overlap_score(self):
        from tools.proposal_genesis.reflexes.draft import _keyword_overlap_score

        score = _keyword_overlap_score(
            ["security", "compliance", "testing"], "Our security and compliance testing approach is comprehensive."
        )
        assert score > 0

    def test_keyword_overlap_zero(self):
        from tools.proposal_genesis.reflexes.draft import _keyword_overlap_score

        score = _keyword_overlap_score(["security", "compliance"], "Completely unrelated text about cooking recipes.")
        assert score < 0.5

    def test_keyword_overlap_empty(self):
        from tools.proposal_genesis.reflexes.draft import _keyword_overlap_score

        assert _keyword_overlap_score([], "Some text") == 0.0


# ── Discover Reflex Tests ─────────────────────────────────────────────────────


class TestDiscoverReflex:
    """Tests for reflexes/discover.py."""

    def test_is_air_gapped_default(self):
        from tools.proposal_genesis.reflexes.discover import _is_air_gapped

        with patch.dict(os.environ, {}, clear=False):
            if "ICDEV_ENVIRONMENT" in os.environ:
                del os.environ["ICDEV_ENVIRONMENT"]
            assert _is_air_gapped() is False

    def test_is_air_gapped_true(self):
        from tools.proposal_genesis.reflexes.discover import _is_air_gapped

        with patch.dict(os.environ, {"ICDEV_ENVIRONMENT": "air-gapped"}):
            assert _is_air_gapped() is True

    @patch("tools.proposal_genesis.reflexes.discover.get_connection")
    def test_run_air_gapped(self, mock_conn):
        from tools.proposal_genesis.reflexes.discover import run

        with patch.dict(os.environ, {"ICDEV_ENVIRONMENT": "air-gapped"}):
            result = run({}, None)
            assert result["success"] is True
            assert result["metric_value"] == 0
            assert result["details"]["status"] == "air_gapped"

    @patch("tools.proposal_genesis.reflexes.discover.get_connection")
    def test_run_normal(self, mock_conn):
        from tools.proposal_genesis.reflexes.discover import run

        mock_db = MagicMock()
        mock_db.execute.return_value.fetchall.return_value = []
        mock_conn.return_value = mock_db
        with patch("tools.proposal_genesis.reflexes.discover._scan_sam_gov", return_value={"new_count": 3}):
            with patch(
                "tools.proposal_genesis.reflexes.discover._check_amendments", return_value={"amendments_found": 1}
            ):
                result = run({}, None)
                assert result["success"] is True
                assert result["metric_value"] == 4.0


# ── Extract Reflex Tests ──────────────────────────────────────────────────────


class TestExtractReflex:
    """Tests for reflexes/extract.py."""

    @patch("tools.proposal_genesis.reflexes.extract.get_connection")
    def test_run_no_opportunities(self, mock_conn):
        from tools.proposal_genesis.reflexes.extract import run

        mock_db = MagicMock()
        mock_db.execute.return_value.fetchall.return_value = []
        mock_conn.return_value = mock_db
        result = run({}, None)
        assert result["success"] is True
        assert result["metric_value"] == 0.0
        assert result["details"]["opportunities_processed"] == 0


# ── Map Reflex Tests ──────────────────────────────────────────────────────────


class TestMapReflex:
    """Tests for reflexes/map.py."""

    @patch("tools.proposal_genesis.reflexes.map.get_connection")
    def test_run_no_opportunities(self, mock_conn):
        from tools.proposal_genesis.reflexes.map import run

        mock_db = MagicMock()
        mock_db.execute.return_value.fetchall.return_value = []
        mock_conn.return_value = mock_db
        result = run({}, None)
        assert result["success"] is True
        assert result["metric_value"] == 0
        assert result["details"]["opportunities_mapped"] == 0


# ── Dashboard API Tests ───────────────────────────────────────────────────────


class TestProposalGenesisAPI:
    """Tests for dashboard API blueprint."""

    @pytest.fixture
    def app(self, pg_db):
        """Create a minimal Flask app with the blueprint."""
        from flask import Flask
        from tools.dashboard.api.proposal_genesis import proposal_genesis_api

        conn, db_path = pg_db
        app = Flask(__name__)
        app.config["TESTING"] = True
        @app.before_request
        def _inject_fake_auth_0():
            from flask import g
            g.current_user = {"username": "test_user", "role": "admin", "email": "test@test.mil", "classification": "CUI"}

        app.register_blueprint(proposal_genesis_api)

        def _mock_get_db():
            c = sqlite3.connect(str(db_path))
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            # _TranslatingConn, not the bare connection: the API authors %s for
            # PostgreSQL and the production _get_db path translates it. See that
            # class's docstring — it called this out as "~34 other already-broken
            # fixtures … a separate, wider cleanup out of this task's scope".
            # This is that cleanup.
            return _TranslatingConn(c)

        with patch("tools.dashboard.api.proposal_genesis._get_db", side_effect=_mock_get_db):
            yield app

    def test_summary_endpoint(self, app, pg_db):
        conn, db_path = pg_db
        # Insert test data
        conn.execute(
            "INSERT INTO proposal_opportunities (id, title, status, created_at) VALUES (?, ?, ?, ?)",
            ("opp-1", "Test Opp", "tracking", "2026-03-14T00:00:00Z"),
        )
        conn.commit()

        with app.test_client() as client:
            resp = client.get("/api/proposal-genesis/summary")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["opportunities"] >= 1

    def test_quality_scores_endpoint(self, app, pg_db):
        conn, db_path = pg_db
        conn.execute(
            "INSERT INTO pg_proposal_quality_scores "
            "(id, opportunity_id, draft_id, composite_score, grammar_score, "
            "readability_score, tone_score, plagiarism_score, ai_detection_score, "
            "check_details, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("qs-1", "opp-1", "draft-1", 0.82, 0.9, 0.8, 0.85, 1.0, 0.7, "{}", "2026-03-14T00:00:00Z"),
        )
        conn.commit()

        with app.test_client() as client:
            resp = client.get("/api/proposal-genesis/quality-scores")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["count"] >= 1
            assert data["scores"][0]["composite_score"] == 0.82

    def test_audit_endpoint(self, app, pg_db):
        conn, db_path = pg_db
        conn.execute(
            "INSERT INTO pg_proposal_genesis_audit "
            "(id, event_type, reflex_name, risk_tier, details, success, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("aud-1", "pg.reflex.completed", "discover", "green", "Found 3 opps", 1, "2026-03-14T12:00:00Z"),
        )
        conn.commit()

        with app.test_client() as client:
            resp = client.get("/api/proposal-genesis/audit")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["count"] >= 1
            assert data["events"][0]["event_type"] == "pg.reflex.completed"

    def test_pulse_links_endpoint(self, app, pg_db):
        conn, db_path = pg_db
        with app.test_client() as client:
            resp = client.get("/api/proposal-genesis/pulse-links")
            assert resp.status_code == 200
            data = resp.get_json()
            assert "links" in data

    def test_reflex_unknown(self, app, pg_db):
        conn, db_path = pg_db
        with app.test_client() as client:
            resp = client.post("/api/proposal-genesis/reflex/invalid_reflex")
            assert resp.status_code == 400
            data = resp.get_json()
            assert "error" in data

    def test_capture_plans_endpoint(self, app, pg_db):
        conn, db_path = pg_db
        conn.execute(
            "INSERT INTO proposal_opportunities (id, title, status, created_at) VALUES (?, ?, ?, ?)",
            ("opp-cp1", "Cloud Modernization", "tracking", "2026-03-14T00:00:00Z"),
        )
        conn.execute(
            "INSERT INTO pg_capture_plans "
            "(id, opportunity_id, status, win_strategy, discriminators, "
            "teaming_strategy, price_strategy, gate_reviews, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "cp-1",
                "opp-cp1",
                "draft",
                "Technical differentiation",
                '["FedRAMP cloud"]',
                "",
                "",
                "{}",
                "2026-03-14T00:00:00Z",
                "2026-03-14T00:00:00Z",
            ),
        )
        conn.commit()

        with app.test_client() as client:
            resp = client.get("/api/proposal-genesis/capture-plans")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["count"] >= 1
            assert data["plans"][0]["opportunity_title"] == "Cloud Modernization"

    def test_teaming_assessments_endpoint(self, app, pg_db):
        conn, db_path = pg_db
        conn.execute(
            "INSERT INTO proposal_opportunities (id, title, status, created_at) VALUES (?, ?, ?, ?)",
            ("opp-ta1", "Cyber Defense", "tracking", "2026-03-14T00:00:00Z"),
        )
        conn.execute(
            "INSERT INTO pg_teaming_partners "
            "(id, name, partner_type, capabilities, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "tp-1",
                "SecureTech Inc",
                "subcontractor",
                "cybersecurity devsecops",
                "active",
                "2026-03-14T00:00:00Z",
                "2026-03-14T00:00:00Z",
            ),
        )
        conn.execute(
            "INSERT INTO pg_teaming_assessments "
            "(id, opportunity_id, partner_id, fit_score, capability_gaps_filled, "
            "risk_assessment, recommendation, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("ta-1", "opp-ta1", "tp-1", 0.72, '["keyword overlap"]', "[]", "strong_fit", "2026-03-14T00:00:00Z"),
        )
        conn.commit()

        with app.test_client() as client:
            resp = client.get("/api/proposal-genesis/teaming-assessments")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["count"] >= 1
            assert data["assessments"][0]["partner_name"] == "SecureTech Inc"
            assert data["assessments"][0]["fit_score"] == 0.72

    def test_summary_includes_phase_b_stats(self, app, pg_db):
        conn, db_path = pg_db
        conn.execute(
            "INSERT INTO pg_capture_plans (id, opportunity_id, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            ("cp-s1", "opp-1", "draft", "2026-03-14T00:00:00Z", "2026-03-14T00:00:00Z"),
        )
        conn.execute(
            "INSERT INTO pg_teaming_assessments "
            "(id, opportunity_id, partner_id, fit_score, recommendation, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("ta-s1", "opp-1", "tp-1", 0.65, "good_fit", "2026-03-14T00:00:00Z"),
        )
        conn.commit()

        with app.test_client() as client:
            resp = client.get("/api/proposal-genesis/summary")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["capture_plans"] >= 1
            assert data["teaming_assessments"] >= 1


# ── Capture Phase-Gate Tests (prop-cap-11) ───────────────────────────────────


class _TranslatingConn:
    """Wraps a raw sqlite3 connection, translating %s -> ? (via
    tools.db.storage.translate_sql) before executing.

    tools/dashboard/api/proposal_genesis.py's capture-plan queries are
    written in Postgres-style %s syntax (the project's real backend); the
    production _get_db() -> get_connection() path handles this
    transparently, but this test file's other fixtures mock _get_db with a
    bare sqlite3.connect(), which doesn't understand %s at all and either
    500s or (for endpoints with a bare except-and-degrade) silently returns
    an empty result. Confirmed live while writing this test class -- same
    root cause as the pre-existing failures documented in prop-fix-10/11's
    PR, not something new. Fixed here (scoped to this class only) rather
    than touching the ~34 other already-broken fixtures, which is a
    separate, wider cleanup out of this task's scope.
    """
    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=()):
        return self._conn.execute(translate_sql(sql, backend="sqlite"), params)

    def executemany(self, sql, seq):
        return self._conn.executemany(translate_sql(sql, backend="sqlite"), seq)

    def __getattr__(self, name):
        return getattr(self._conn, name)


class TestCapturePhaseGates:
    """Tests for the Shipley phase-gate lifecycle on pg_capture_plans:
    GET .../gates, POST .../advance, GET .../pipeline-summary."""

    @pytest.fixture
    def app(self, pg_db):
        """Minimal Flask app with a fake-authenticated admin user so
        @require_role("capture_mgr", "admin") on /advance and
        /pipeline-summary doesn't unconditionally 401 under test (see
        prop-fix-10/11 for the same pattern applied fleet-wide)."""
        from flask import Flask, g
        from tools.dashboard.api.proposal_genesis import proposal_genesis_api

        conn, db_path = pg_db
        app = Flask(__name__)
        app.config["TESTING"] = True

        @app.before_request
        def _inject_fake_auth():
            g.current_user = {"username": "test_capture_mgr", "role": "capture_mgr", "email": "cm@test.mil"}

        app.register_blueprint(proposal_genesis_api)

        def _mock_get_db():
            c = sqlite3.connect(str(db_path))
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            return _TranslatingConn(c)

        with patch("tools.dashboard.api.proposal_genesis._get_db", side_effect=_mock_get_db):
            yield app

    @pytest.fixture
    def capture_plan(self, pg_db):
        """Seed one opportunity + one capture plan at the default 'qualify' phase."""
        conn, db_path = pg_db
        conn.execute(
            "INSERT INTO proposal_opportunities (id, title, status, created_at) VALUES (?, ?, ?, ?)",
            ("opp-cap-1", "Capture Test Opp", "tracking", "2026-03-14T00:00:00Z"),
        )
        conn.execute(
            "INSERT INTO pg_capture_plans "
            "(id, opportunity_id, status, current_phase, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("cap-1", "opp-cap-1", "draft", "qualify", "2026-03-14T00:00:00Z", "2026-03-14T00:00:00Z"),
        )
        conn.commit()
        return "cap-1", "opp-cap-1"

    def test_list_capture_plans_includes_current_phase(self, app, capture_plan):
        plan_id, _ = capture_plan
        with app.test_client() as client:
            resp = client.get("/api/proposal-genesis/capture-plans")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["count"] >= 1
            plan = next(p for p in data["plans"] if p["id"] == plan_id)
            assert plan["current_phase"] == "qualify"

    def test_gates_endpoint_starts_empty_at_qualify(self, app, capture_plan):
        plan_id, _ = capture_plan
        with app.test_client() as client:
            resp = client.get(f"/api/proposal-genesis/capture-plans/{plan_id}/gates")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["current_phase"] == "qualify"
            assert data["phase_label"] == "Qualify"
            assert data["phases"] == ["qualify", "pursue", "capture", "bid", "proposal"]
            assert data["gates"] == []

    def test_gates_endpoint_unknown_plan_404s(self, app):
        with app.test_client() as client:
            resp = client.get("/api/proposal-genesis/capture-plans/no-such-plan/gates")
            assert resp.status_code == 404

    def test_advance_moves_to_next_phase_and_records_gate_decision(self, app, capture_plan, pg_db):
        plan_id, opp_id = capture_plan
        conn, _ = pg_db
        with app.test_client() as client:
            resp = client.post(
                f"/api/proposal-genesis/capture-plans/{plan_id}/advance",
                json={"decision": "advance", "rationale": "Strong incumbent relationship", "decided_by": "cm1"},
            )
            assert resp.status_code == 201
            data = resp.get_json()
            assert data["from_phase"] == "qualify"
            assert data["to_phase"] == "pursue"
            assert data["new_phase"] == "pursue"

        row = conn.execute("SELECT current_phase FROM pg_capture_plans WHERE id = ?", (plan_id,)).fetchone()
        assert row["current_phase"] == "pursue"

        gates = conn.execute(
            "SELECT * FROM pg_capture_gate_decisions WHERE capture_plan_id = ?", (plan_id,)
        ).fetchall()
        assert len(gates) == 1
        assert gates[0]["decision"] == "advance"
        assert gates[0]["opportunity_id"] == opp_id

    def test_advance_through_full_lifecycle(self, app, capture_plan):
        plan_id, _ = capture_plan
        expected = ["pursue", "capture", "bid", "proposal"]
        with app.test_client() as client:
            for expected_phase in expected:
                resp = client.post(
                    f"/api/proposal-genesis/capture-plans/{plan_id}/advance",
                    json={"decision": "advance", "decided_by": "cm1"},
                )
                assert resp.status_code == 201
                assert resp.get_json()["to_phase"] == expected_phase

            # Already at final phase -- next advance must be rejected, not silently no-op.
            resp = client.post(
                f"/api/proposal-genesis/capture-plans/{plan_id}/advance",
                json={"decision": "advance", "decided_by": "cm1"},
            )
            assert resp.status_code == 400
            assert resp.get_json()["error"] == "already_at_final_phase"

    def test_advance_hold_decision_does_not_change_phase(self, app, capture_plan, pg_db):
        plan_id, _ = capture_plan
        conn, _ = pg_db
        with app.test_client() as client:
            resp = client.post(
                f"/api/proposal-genesis/capture-plans/{plan_id}/advance",
                json={"decision": "hold", "rationale": "Awaiting funding confirmation", "decided_by": "cm1"},
            )
            assert resp.status_code == 201
            data = resp.get_json()
            assert data["to_phase"] == "qualify"
            assert data["new_phase"] == "qualify"

        row = conn.execute("SELECT current_phase FROM pg_capture_plans WHERE id = ?", (plan_id,)).fetchone()
        assert row["current_phase"] == "qualify"

    def test_advance_no_bid_decision_records_gate_without_advancing(self, app, capture_plan, pg_db):
        plan_id, _ = capture_plan
        conn, _ = pg_db
        with app.test_client() as client:
            resp = client.post(
                f"/api/proposal-genesis/capture-plans/{plan_id}/advance",
                json={"decision": "no_bid", "rationale": "Insufficient past performance", "decided_by": "cm1"},
            )
            assert resp.status_code == 201
            data = resp.get_json()
            assert data["to_phase"] == "no_bid"

        row = conn.execute("SELECT current_phase FROM pg_capture_plans WHERE id = ?", (plan_id,)).fetchone()
        assert row["current_phase"] == "qualify"  # unchanged -- no_bid halts, doesn't advance

    def test_advance_rejects_invalid_decision(self, app, capture_plan):
        plan_id, _ = capture_plan
        with app.test_client() as client:
            resp = client.post(
                f"/api/proposal-genesis/capture-plans/{plan_id}/advance",
                json={"decision": "skip_ahead"},
            )
            assert resp.status_code == 400
            assert "invalid decision" in resp.get_json()["error"]

    def test_advance_unknown_plan_404s(self, app):
        with app.test_client() as client:
            resp = client.post(
                "/api/proposal-genesis/capture-plans/no-such-plan/advance",
                json={"decision": "advance"},
            )
            assert resp.status_code == 404

    def test_pipeline_summary_aggregates_by_phase(self, app, pg_db):
        conn, _ = pg_db
        conn.execute(
            "INSERT INTO proposal_opportunities (id, title, status, created_at) VALUES (?, ?, ?, ?)",
            ("opp-cap-2", "Second Opp", "tracking", "2026-03-14T00:00:00Z"),
        )
        conn.executemany(
            "INSERT INTO pg_capture_plans "
            "(id, opportunity_id, status, current_phase, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            [
                ("cap-a", "opp-cap-2", "draft", "qualify", "2026-03-14T00:00:00Z", "2026-03-14T00:00:00Z"),
                ("cap-b", "opp-cap-2", "draft", "qualify", "2026-03-14T00:00:00Z", "2026-03-14T00:00:00Z"),
                ("cap-c", "opp-cap-2", "draft", "pursue", "2026-03-14T00:00:00Z", "2026-03-14T00:00:00Z"),
            ],
        )
        conn.commit()

        with app.test_client() as client:
            resp = client.get("/api/proposal-genesis/capture-plans/pipeline-summary")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["summary"]["qualify"] == 2
            assert data["summary"]["pursue"] == 1
            assert data["phases"] == ["qualify", "pursue", "capture", "bid", "proposal"]

    def test_advance_requires_capture_mgr_or_admin_role(self, pg_db, capture_plan):
        """Same app as the class fixture, but with a role NOT in
        ("capture_mgr", "admin") -- confirms @require_role is actually
        enforcing, not just decoratively present."""
        from flask import Flask, g
        from tools.dashboard.api.proposal_genesis import proposal_genesis_api

        conn, db_path = pg_db
        plan_id, _ = capture_plan
        app = Flask(__name__)
        app.config["TESTING"] = True

        @app.before_request
        def _inject_wrong_role():
            g.current_user = {"username": "test_bd", "role": "bd", "email": "bd@test.mil"}

        app.register_blueprint(proposal_genesis_api)

        def _mock_get_db():
            c = sqlite3.connect(str(db_path))
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            return _TranslatingConn(c)

        with patch("tools.dashboard.api.proposal_genesis._get_db", side_effect=_mock_get_db):
            with app.test_client() as client:
                resp = client.post(
                    f"/api/proposal-genesis/capture-plans/{plan_id}/advance",
                    json={"decision": "advance"},
                )
                assert resp.status_code == 403


# ── Scout Reflex Tests (Phase B) ────────────────────────────────────────────


class TestScoutReflex:
    """Tests for reflexes/scout.py (R2)."""

    def test_is_air_gapped_default(self):
        from tools.proposal_genesis.reflexes.scout import _is_air_gapped

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ICDEV_AIR_GAPPED", None)
            assert _is_air_gapped() is False

    def test_is_air_gapped_true(self):
        from tools.proposal_genesis.reflexes.scout import _is_air_gapped

        with patch.dict(os.environ, {"ICDEV_AIR_GAPPED": "true"}):
            assert _is_air_gapped() is True

    def test_generate_brief_with_data(self):
        from tools.proposal_genesis.reflexes.scout import _generate_brief

        award_scan = {"status": "ok", "new_awards": 5, "total_fetched": 20, "lookback_days": 90}
        leaderboard = [
            {"rank": 1, "vendor": "Acme Corp", "awards": 15, "total_value": 5000000, "naics_diversity": 3},
            {"rank": 2, "vendor": "Beta LLC", "awards": 10, "total_value": 3000000, "naics_diversity": 2},
        ]
        overlaps = [
            {
                "opportunity_id": "opp-1",
                "opportunity_title": "Cloud Migration Support",
                "naics": "541512",
                "agency": "DoD",
                "likely_competitors": [{"name": "Acme Corp", "awards_in_space": 5}],
            }
        ]
        brief = _generate_brief(award_scan, leaderboard, overlaps)
        assert "Competitor Intelligence Brief" in brief
        assert "CUI // SP-CTI" in brief
        assert "Acme Corp" in brief
        assert "Cloud Migration Support" in brief
        assert "5 awards in space" in brief

    def test_generate_brief_air_gapped(self):
        from tools.proposal_genesis.reflexes.scout import _generate_brief

        brief = _generate_brief({"status": "air_gapped"}, [], [])
        assert "Air-gapped mode" in brief
        assert "No award data available" in brief

    def test_generate_brief_error(self):
        from tools.proposal_genesis.reflexes.scout import _generate_brief

        brief = _generate_brief({"status": "error", "message": "timeout"}, [], [])
        assert "timeout" in brief

    @patch("tools.proposal_genesis.reflexes.scout.get_connection")
    def test_find_competitor_overlaps_no_opps(self, mock_conn):
        from tools.proposal_genesis.reflexes.scout import _find_competitor_overlaps

        mock_db = MagicMock()
        mock_db.execute.return_value.fetchall.return_value = []
        mock_conn.return_value = mock_db
        result = _find_competitor_overlaps()
        assert result == []

    @patch("tools.proposal_genesis.reflexes.scout._store_brief")
    @patch("tools.proposal_genesis.reflexes.scout._match_awards_to_decisions")
    @patch("tools.proposal_genesis.reflexes.scout._find_competitor_overlaps")
    @patch("tools.proposal_genesis.reflexes.scout._get_top_competitors")
    @patch("tools.proposal_genesis.reflexes.scout._scan_awards")
    def test_run_air_gapped(self, mock_scan, mock_leaders, mock_overlaps, mock_match, mock_store):
        from tools.proposal_genesis.reflexes.scout import run

        mock_leaders.return_value = []
        mock_overlaps.return_value = []
        mock_match.return_value = {"outcomes_recorded": 0, "win_loss_created": 0}
        mock_store.return_value = "/tmp/brief.md"

        with patch.dict(os.environ, {"ICDEV_AIR_GAPPED": "true"}):
            result = run({}, None)
            assert result["success"] is True
            assert result["metric_value"] == 1.0
            assert result["details"]["air_gapped"] is True
            mock_scan.assert_not_called()

    @patch("tools.proposal_genesis.reflexes.scout._store_brief")
    @patch("tools.proposal_genesis.reflexes.scout._match_awards_to_decisions")
    @patch("tools.proposal_genesis.reflexes.scout._find_competitor_overlaps")
    @patch("tools.proposal_genesis.reflexes.scout._get_top_competitors")
    @patch("tools.proposal_genesis.reflexes.scout._scan_awards")
    def test_run_connected(self, mock_scan, mock_leaders, mock_overlaps, mock_match, mock_store):
        from tools.proposal_genesis.reflexes.scout import run

        mock_scan.return_value = {"status": "ok", "new_awards": 3}
        mock_leaders.return_value = [{"rank": 1, "vendor": "X", "awards": 5}]
        mock_overlaps.return_value = []
        mock_match.return_value = {"outcomes_recorded": 2, "win_loss_created": 2}
        mock_store.return_value = "/tmp/brief.md"

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ICDEV_AIR_GAPPED", None)
            result = run({}, None)
            assert result["success"] is True
            assert result["details"]["new_awards"] == 3
            assert result["details"]["competitors_tracked"] == 1
            assert result["details"]["outcomes_recorded"] == 2
            assert result["details"]["win_loss_created"] == 2

    @patch("tools.proposal_genesis.reflexes.scout.get_connection")
    def test_match_awards_to_decisions_no_pending(self, mock_conn):
        """No pending bid decisions → zero outcomes."""
        from tools.proposal_genesis.reflexes.scout import _match_awards_to_decisions

        mock_db = MagicMock()
        mock_db.execute.return_value.fetchall.return_value = []
        mock_conn.return_value = mock_db
        result = _match_awards_to_decisions()
        assert result["outcomes_recorded"] == 0
        assert result["win_loss_created"] == 0

    @patch("tools.proposal_genesis.reflexes.scout.get_connection")
    def test_match_awards_to_decisions_with_match(self, mock_conn):
        """Pending bid decision + matching award → outcome + win/loss record."""
        from tools.proposal_genesis.reflexes.scout import _match_awards_to_decisions

        mock_db = MagicMock()

        pending_row = {
            "decision_id": "bd-1",
            "opportunity_id": "opp-1",
            "decision": "bid",
            "title": "Cloud Svc",
            "agency": "DoD",
            "naics_code": "541512",
            "sol_number": "SOL-001",
        }
        award_row = {
            "awardee_name": "Some Other Corp",
            "award_amount": 500000,
            "award_date": "2026-03-10",
        }


        def execute_side_effect(sql, params=None):
            result = MagicMock()
            sql_lower = sql.strip().lower()
            if sql_lower.startswith("select bd.id"):
                result.fetchall.return_value = [pending_row]
            elif sql_lower.startswith("select awardee_name") and "sol_number" in sql_lower:
                result.fetchone.return_value = award_row
            elif sql_lower.startswith("insert"):
                pass
            else:
                result.fetchall.return_value = []
                result.fetchone.return_value = None
            return result

        mock_db.execute.side_effect = execute_side_effect
        mock_conn.return_value = mock_db

        result = _match_awards_to_decisions()
        assert result["outcomes_recorded"] == 1
        assert result["win_loss_created"] == 1
        mock_db.commit.assert_called()

    @patch("tools.proposal_genesis.reflexes.scout.get_connection")
    def test_match_awards_no_award_found(self, mock_conn):
        """Pending bid decision but no matching award → skip."""
        from tools.proposal_genesis.reflexes.scout import _match_awards_to_decisions

        mock_db = MagicMock()

        pending_row = {
            "decision_id": "bd-2",
            "opportunity_id": "opp-2",
            "decision": "bid",
            "title": "Network Ops",
            "agency": "DHS",
            "naics_code": "541519",
            "sol_number": "",
        }

        def execute_side_effect(sql, params=None):
            result = MagicMock()
            sql_lower = sql.strip().lower()
            if sql_lower.startswith("select bd.id"):
                result.fetchall.return_value = [pending_row]
            else:
                result.fetchall.return_value = []
                result.fetchone.return_value = None
            return result

        mock_db.execute.side_effect = execute_side_effect
        mock_conn.return_value = mock_db

        result = _match_awards_to_decisions()
        assert result["outcomes_recorded"] == 0
        assert result["win_loss_created"] == 0


# ── Shape Reflex Tests (Phase B) ────────────────────────────────────────────


class TestShapeReflex:
    """Tests for reflexes/shape.py (R3)."""

    def test_derive_win_strategy_tech(self):
        from tools.proposal_genesis.reflexes.shape import _derive_win_strategy

        strategy = _derive_win_strategy({"title": "Cloud Modernization Platform", "agency": "DoD"})
        assert "Technical differentiation" in strategy or "automation" in strategy

    def test_derive_win_strategy_compliance(self):
        from tools.proposal_genesis.reflexes.shape import _derive_win_strategy

        strategy = _derive_win_strategy({"title": "FedRAMP ATO Support", "agency": "DHS"})
        assert "Compliance" in strategy or "ATO" in strategy

    def test_derive_win_strategy_dod(self):
        from tools.proposal_genesis.reflexes.shape import _derive_win_strategy

        strategy = _derive_win_strategy({"title": "Network Security", "agency": "DoD"})
        assert "Mission understanding" in strategy or "DoD" in strategy

    def test_derive_win_strategy_default(self):
        from tools.proposal_genesis.reflexes.shape import _derive_win_strategy

        strategy = _derive_win_strategy({"title": "General Services", "agency": "GSA"})
        assert "Best-value" in strategy

    def test_derive_discriminators_cloud(self):
        from tools.proposal_genesis.reflexes.shape import _derive_discriminators

        discs = _derive_discriminators({"title": "Cloud Infrastructure Migration"})
        assert any("FedRAMP" in d for d in discs)

    def test_derive_discriminators_ai(self):
        from tools.proposal_genesis.reflexes.shape import _derive_discriminators

        discs = _derive_discriminators({"title": "AI-driven Analytics Platform"})
        assert any("AI" in d or "NIST" in d for d in discs)

    def test_derive_discriminators_default(self):
        from tools.proposal_genesis.reflexes.shape import _derive_discriminators

        discs = _derive_discriminators({"title": "General Consulting"})
        assert any("SDLC" in d or "deployment" in d.lower() for d in discs)

    def test_assess_partner_fit_strong(self):
        from tools.proposal_genesis.reflexes.shape import _assess_partner_fit

        opp = {"title": "Cloud Cybersecurity DevSecOps Modernization", "naics_code": "541512"}
        partner = {
            "capabilities": "cloud cybersecurity devsecops automation modernization",
            "certifications": "fedramp cmmc iso soc cleared",
            "contract_vehicles": "gwac idiq gsa alliant",
            "set_asides": "8a sdvosb small business",
            "status": "active",
        }
        result = _assess_partner_fit(opp, partner)
        assert result["fit_score"] > 0.3
        assert result["recommendation"] in ("strong_fit", "good_fit", "marginal")
        assert len(result["capability_gaps_filled"]) > 0

    def test_assess_partner_fit_no_capabilities(self):
        from tools.proposal_genesis.reflexes.shape import _assess_partner_fit

        opp = {"title": "Test Opportunity", "naics_code": "541512"}
        partner = {
            "capabilities": "",
            "certifications": "",
            "contract_vehicles": "",
            "set_asides": "",
            "status": "prospect",
        }
        result = _assess_partner_fit(opp, partner)
        assert result["fit_score"] < 0.5
        assert "No capabilities documented" in result["risk_assessment"]
        assert "prospect" in " ".join(result["risk_assessment"]).lower()

    def test_assess_partner_fit_marginal(self):
        from tools.proposal_genesis.reflexes.shape import _assess_partner_fit

        opp = {"title": "Advanced Quantum Computing Research", "naics_code": "541715"}
        partner = {
            "capabilities": "data entry word processing scanning",
            "certifications": "",
            "contract_vehicles": "",
            "set_asides": "",
            "status": "active",
        }
        result = _assess_partner_fit(opp, partner)
        assert result["recommendation"] in ("marginal", "not_recommended")

    @patch("tools.proposal_genesis.reflexes.shape._update_teaming_strategy")
    @patch("tools.proposal_genesis.reflexes.shape._get_unassessed_opportunities")
    @patch("tools.proposal_genesis.reflexes.shape._get_partners")
    @patch("tools.proposal_genesis.reflexes.shape._create_capture_plan")
    @patch("tools.proposal_genesis.reflexes.shape._get_opportunities_needing_plans")
    def test_run_creates_plans(self, mock_needs, mock_create, mock_partners, mock_unassessed, mock_update):
        from tools.proposal_genesis.reflexes.shape import run

        mock_needs.return_value = [
            {"id": "opp-1", "title": "Test", "agency": "DoD", "naics_code": "541512"},
        ]
        mock_create.return_value = "pgcp-abc123"
        mock_partners.return_value = []
        mock_unassessed.return_value = []

        result = run({}, None)
        assert result["success"] is True
        assert result["details"]["plans_created"] == 1
        mock_create.assert_called_once()

    @patch("tools.proposal_genesis.reflexes.shape._update_teaming_strategy")
    @patch("tools.proposal_genesis.reflexes.shape._store_assessment")
    @patch("tools.proposal_genesis.reflexes.shape._get_unassessed_opportunities")
    @patch("tools.proposal_genesis.reflexes.shape._get_partners")
    @patch("tools.proposal_genesis.reflexes.shape._create_capture_plan")
    @patch("tools.proposal_genesis.reflexes.shape._get_opportunities_needing_plans")
    def test_run_assesses_partners(
        self, mock_needs, mock_create, mock_partners, mock_unassessed, mock_store, mock_update
    ):
        from tools.proposal_genesis.reflexes.shape import run

        mock_needs.return_value = []
        mock_partners.return_value = [
            {
                "id": "tp-1",
                "name": "Partner A",
                "capabilities": "cloud devsecops",
                "certifications": "fedramp",
                "contract_vehicles": "gwac",
                "set_asides": "8a",
                "status": "active",
            },
        ]
        mock_unassessed.return_value = [
            {"opportunity_id": "opp-1", "title": "Cloud DevSecOps", "naics_code": "541512", "agency": "DoD"},
        ]
        mock_store.return_value = "pgta-xyz789"

        result = run({}, None)
        assert result["success"] is True
        assert result["details"]["partners_evaluated"] == 1
        assert result["details"]["opportunities_assessed"] == 1

    @patch("tools.proposal_genesis.reflexes.shape._update_teaming_strategy")
    @patch("tools.proposal_genesis.reflexes.shape._get_unassessed_opportunities")
    @patch("tools.proposal_genesis.reflexes.shape._get_partners")
    @patch("tools.proposal_genesis.reflexes.shape._get_opportunities_needing_plans")
    def test_run_no_work(self, mock_needs, mock_partners, mock_unassessed, mock_update):
        from tools.proposal_genesis.reflexes.shape import run

        mock_needs.return_value = []
        mock_partners.return_value = []
        mock_unassessed.return_value = []

        result = run({}, None)
        assert result["success"] is True
        assert result["metric_value"] == 0.0
        assert result["details"]["plans_created"] == 0
        assert result["details"]["assessments_made"] == 0


# ── Engage Reflex Tests (Phase C) ──────────────────────────────────────────


class TestEngageReflex:
    """Tests for reflexes/engage.py (R4)."""

    def test_classify_account_type_government(self):
        from tools.proposal_genesis.reflexes.engage import _classify_account_type

        assert _classify_account_type("Department of Defense") == "government"
        assert _classify_account_type("DHS") == "government"
        assert _classify_account_type("Army Futures Command") == "government"
        assert _classify_account_type("DISA") == "government"
        assert _classify_account_type("Bureau of Land Management") == "government"

    def test_classify_account_type_other(self):
        from tools.proposal_genesis.reflexes.engage import _classify_account_type

        assert _classify_account_type("Acme Corp") == "other"
        assert _classify_account_type("") == "other"
        assert _classify_account_type(None) == "other"

    def test_map_event_to_interaction(self):
        from tools.proposal_genesis.reflexes.engage import _map_event_to_interaction

        assert _map_event_to_interaction("capture_plan_created") == "rfi_response"
        assert _map_event_to_interaction("draft_completed") == "rfi_response"
        assert _map_event_to_interaction("pg.reflex.completed") == "other"
        assert _map_event_to_interaction("brief_generated") == "other"
        assert _map_event_to_interaction("quality_checked") == "other"
        assert _map_event_to_interaction("unknown_event") is None

    def test_score_recency_today(self):
        from tools.proposal_genesis.reflexes.engage import _score_recency

        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        score = _score_recency(now_iso)
        assert score >= 0.99

    def test_score_recency_old(self):
        from tools.proposal_genesis.reflexes.engage import _score_recency

        score = _score_recency("2020-01-01T00:00:00Z")
        assert score == 0.0

    def test_score_recency_none(self):
        from tools.proposal_genesis.reflexes.engage import _score_recency

        assert _score_recency(None) == 0.0
        assert _score_recency("") == 0.0

    def test_score_recency_30_days_ago(self):
        from tools.proposal_genesis.reflexes.engage import _score_recency
        from datetime import timedelta

        thirty_days_ago = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        score = _score_recency(thirty_days_ago)
        assert 0.6 < score < 0.7  # ~0.667

    @patch("tools.proposal_genesis.reflexes.engage.get_connection")
    def test_create_account_from_opportunity(self, mock_conn):
        from tools.proposal_genesis.reflexes.engage import _create_account_from_opportunity

        mock_db = MagicMock()
        mock_db.execute.return_value.fetchone.return_value = None  # no existing
        mock_conn.return_value = mock_db

        opp = {"id": "opp-1", "agency": "Department of Defense", "naics_code": "541512"}
        result = _create_account_from_opportunity(opp)
        assert result is not None
        assert result.startswith("pgacct-")
        mock_db.commit.assert_called_once()

    @patch("tools.proposal_genesis.reflexes.engage.get_connection")
    def test_create_account_existing(self, mock_conn):
        from tools.proposal_genesis.reflexes.engage import _create_account_from_opportunity

        mock_db = MagicMock()
        mock_db.execute.return_value.fetchone.return_value = {"id": "existing-id"}
        mock_conn.return_value = mock_db

        opp = {"id": "opp-1", "agency": "DoD", "naics_code": ""}
        result = _create_account_from_opportunity(opp)
        assert result == "existing-id"

    @patch("tools.proposal_genesis.reflexes.engage.get_connection")
    def test_create_account_no_agency(self, mock_conn):
        from tools.proposal_genesis.reflexes.engage import _create_account_from_opportunity

        opp = {"id": "opp-1", "agency": "", "naics_code": ""}
        result = _create_account_from_opportunity(opp)
        assert result is None

    @patch("tools.proposal_genesis.reflexes.engage.get_connection")
    def test_log_interaction(self, mock_conn):
        from tools.proposal_genesis.reflexes.engage import _log_interaction

        mock_db = MagicMock()
        mock_conn.return_value = mock_db

        result = _log_interaction(
            account_id="acct-1",
            interaction_type="rfi_response",
            subject="Test subject",
            opportunity_id="opp-1",
            notes="Some notes",
        )
        assert result is not None
        assert result.startswith("pgint-")
        mock_db.commit.assert_called_once()

    @patch("tools.proposal_genesis.reflexes.engage.get_connection")
    def test_get_account_for_agency(self, mock_conn):
        from tools.proposal_genesis.reflexes.engage import _get_account_for_agency

        mock_db = MagicMock()
        mock_db.execute.return_value.fetchone.return_value = {"id": "acct-1"}
        mock_conn.return_value = mock_db

        result = _get_account_for_agency("DoD")
        assert result == "acct-1"

    @patch("tools.proposal_genesis.reflexes.engage.get_connection")
    def test_get_account_for_agency_not_found(self, mock_conn):
        from tools.proposal_genesis.reflexes.engage import _get_account_for_agency

        mock_db = MagicMock()
        mock_db.execute.return_value.fetchone.return_value = None
        mock_conn.return_value = mock_db

        result = _get_account_for_agency("Unknown Agency")
        assert result is None

    def test_get_account_for_agency_empty(self):
        from tools.proposal_genesis.reflexes.engage import _get_account_for_agency

        assert _get_account_for_agency("") is None
        assert _get_account_for_agency(None) is None

    @patch("tools.proposal_genesis.reflexes.engage.get_connection")
    def test_compute_win_rate_with_data(self, mock_conn):
        from tools.proposal_genesis.reflexes.engage import _compute_win_rate

        mock_db = MagicMock()
        mock_db.execute.return_value.fetchone.return_value = {"total": 10, "wins": 7}
        result = _compute_win_rate(mock_db, "DoD")
        assert result == 0.7

    @patch("tools.proposal_genesis.reflexes.engage.get_connection")
    def test_compute_win_rate_no_data(self, mock_conn):
        from tools.proposal_genesis.reflexes.engage import _compute_win_rate

        mock_db = MagicMock()
        mock_db.execute.return_value.fetchone.return_value = {"total": 0, "wins": 0}
        result = _compute_win_rate(mock_db, "Unknown Agency")
        assert result == 0.0

    @patch("tools.proposal_genesis.reflexes.engage.get_connection")
    def test_compute_win_rate_none_row(self, mock_conn):
        from tools.proposal_genesis.reflexes.engage import _compute_win_rate

        mock_db = MagicMock()
        mock_db.execute.return_value.fetchone.return_value = None
        result = _compute_win_rate(mock_db, "DoD")
        assert result == 0.0

    @patch("tools.proposal_genesis.reflexes.engage.get_connection")
    def test_compute_win_rate_exception(self, mock_conn):
        from tools.proposal_genesis.reflexes.engage import _compute_win_rate

        mock_db = MagicMock()
        mock_db.execute.side_effect = Exception("DB error")
        result = _compute_win_rate(mock_db, "DoD")
        assert result == 0.0

    @patch("tools.proposal_genesis.reflexes.engage.get_connection")
    def test_compute_win_rate_all_wins(self, mock_conn):
        from tools.proposal_genesis.reflexes.engage import _compute_win_rate

        mock_db = MagicMock()
        mock_db.execute.return_value.fetchone.return_value = {"total": 5, "wins": 5}
        result = _compute_win_rate(mock_db, "Navy")
        assert result == 1.0

    @patch("tools.proposal_genesis.reflexes.engage._compute_engagement_scores")
    @patch("tools.proposal_genesis.reflexes.engage._log_interaction")
    @patch("tools.proposal_genesis.reflexes.engage._get_account_for_agency")
    @patch("tools.proposal_genesis.reflexes.engage._get_recent_audit_interactions")
    @patch("tools.proposal_genesis.reflexes.engage._create_account_from_opportunity")
    @patch("tools.proposal_genesis.reflexes.engage._get_opportunities_without_accounts")
    def test_run_full(self, mock_opps, mock_create, mock_audits, mock_agency, mock_log, mock_scores):
        from tools.proposal_genesis.reflexes.engage import run

        mock_opps.return_value = [
            {"id": "opp-1", "agency": "DoD", "naics_code": "541512"},
        ]
        mock_create.return_value = "pgacct-abc"
        mock_audits.return_value = [
            {"agency": "DoD", "event_type": "capture_plan_created", "opportunity_id": "opp-1", "details": "{}"},
        ]
        mock_agency.return_value = "pgacct-abc"
        mock_log.return_value = "pgint-abc"
        mock_scores.return_value = [
            {"account_id": "pgacct-abc", "account_name": "DoD", "score": 0.45, "breakdown": {}},
        ]

        result = run({}, None)
        assert result["success"] is True
        assert result["metric_value"] == 1.0
        assert result["details"]["accounts_created"] == 1
        assert result["details"]["accounts_scored"] == 1
        assert result["details"]["interactions_logged"] == 1

    @patch("tools.proposal_genesis.reflexes.engage._compute_engagement_scores")
    @patch("tools.proposal_genesis.reflexes.engage._get_recent_audit_interactions")
    @patch("tools.proposal_genesis.reflexes.engage._get_opportunities_without_accounts")
    def test_run_no_work(self, mock_opps, mock_audits, mock_scores):
        from tools.proposal_genesis.reflexes.engage import run

        mock_opps.return_value = []
        mock_audits.return_value = []
        mock_scores.return_value = []

        result = run({}, None)
        assert result["success"] is True
        assert result["metric_value"] == 0.0
        assert result["details"]["accounts_created"] == 0


# ── Phase C Dashboard API Tests ─────────────────────────────────────────────


class TestProposalGenesisPhaseCAPI:
    """Tests for Phase C dashboard API endpoints."""

    @pytest.fixture
    def app(self, pg_db):
        from flask import Flask
        from tools.dashboard.api.proposal_genesis import proposal_genesis_api

        conn, db_path = pg_db
        app = Flask(__name__)
        app.config["TESTING"] = True
        @app.before_request
        def _inject_fake_auth_1():
            from flask import g
            g.current_user = {"username": "test_user", "role": "admin", "email": "test@test.mil", "classification": "CUI"}

        app.register_blueprint(proposal_genesis_api)

        def _mock_get_db():
            c = sqlite3.connect(str(db_path))
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            # _TranslatingConn, not the bare connection: the API authors %s for
            # PostgreSQL and the production _get_db path translates it. See that
            # class's docstring — it called this out as "~34 other already-broken
            # fixtures … a separate, wider cleanup out of this task's scope".
            # This is that cleanup.
            return _TranslatingConn(c)

        with patch("tools.dashboard.api.proposal_genesis._get_db", side_effect=_mock_get_db):
            yield app

    def test_crm_accounts_endpoint(self, app, pg_db):
        conn, db_path = pg_db
        conn.execute(
            "INSERT INTO pg_crm_accounts "
            "(id, name, agency, account_type, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("acct-1", "DoD", "DoD", "government", "active", "2026-03-14T00:00:00Z", "2026-03-14T00:00:00Z"),
        )
        conn.commit()

        with app.test_client() as client:
            resp = client.get("/api/proposal-genesis/crm-accounts")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["count"] >= 1
            assert data["accounts"][0]["name"] == "DoD"
            assert data["accounts"][0]["account_type"] == "government"

    def test_crm_accounts_with_engagement_score(self, app, pg_db):
        conn, db_path = pg_db
        conn.execute(
            "INSERT INTO pg_crm_accounts "
            "(id, name, agency, account_type, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("acct-2", "DHS", "DHS", "government", "active", "2026-03-14T00:00:00Z", "2026-03-14T00:00:00Z"),
        )
        conn.execute(
            "INSERT INTO pg_crm_engagement_scores "
            "(id, account_id, score, score_breakdown, interaction_count, "
            "opportunity_count, win_rate, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("eng-1", "acct-2", 0.65, '{"recency":0.8,"frequency":0.5}', 5, 2, 0.0, "2026-03-14T00:00:00Z"),
        )
        conn.commit()

        with app.test_client() as client:
            resp = client.get("/api/proposal-genesis/crm-accounts")
            assert resp.status_code == 200
            data = resp.get_json()
            acct = next((a for a in data["accounts"] if a["name"] == "DHS"), None)
            assert acct is not None
            assert acct["latest_engagement_score"] == 0.65

    def test_crm_interactions_endpoint(self, app, pg_db):
        conn, db_path = pg_db
        conn.execute(
            "INSERT INTO pg_crm_accounts (id, name, agency, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("acct-3", "Army", "Army", "active", "2026-03-14T00:00:00Z", "2026-03-14T00:00:00Z"),
        )
        conn.execute(
            "INSERT INTO pg_crm_interactions "
            "(id, contact_id, account_id, interaction_type, subject, "
            "notes, opportunity_id, interaction_date, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "int-1",
                "",
                "acct-3",
                "rfi_response",
                "RFI for cloud",
                "Notes here",
                "opp-1",
                "2026-03-14T12:00:00Z",
                "2026-03-14T12:00:00Z",
            ),
        )
        conn.commit()

        with app.test_client() as client:
            resp = client.get("/api/proposal-genesis/crm-interactions")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["count"] >= 1
            assert data["interactions"][0]["account_name"] == "Army"
            assert data["interactions"][0]["interaction_type"] == "rfi_response"

    def test_engagement_scores_endpoint(self, app, pg_db):
        conn, db_path = pg_db
        conn.execute(
            "INSERT INTO pg_crm_accounts (id, name, agency, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("acct-4", "Navy", "Navy", "active", "2026-03-14T00:00:00Z", "2026-03-14T00:00:00Z"),
        )
        conn.execute(
            "INSERT INTO pg_crm_engagement_scores "
            "(id, account_id, score, score_breakdown, interaction_count, "
            "opportunity_count, win_rate, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "eng-2",
                "acct-4",
                0.72,
                '{"recency":0.9,"frequency":0.6,"pipeline":0.4,"win_rate":0.0}',
                8,
                3,
                0.0,
                "2026-03-14T00:00:00Z",
            ),
        )
        conn.commit()

        with app.test_client() as client:
            resp = client.get("/api/proposal-genesis/engagement-scores")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["count"] >= 1
            assert data["scores"][0]["account_name"] == "Navy"
            assert data["scores"][0]["score"] == 0.72

    def test_summary_includes_phase_c_stats(self, app, pg_db):
        conn, db_path = pg_db
        conn.execute(
            "INSERT INTO pg_crm_accounts (id, name, agency, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("acct-5", "DISA", "DISA", "active", "2026-03-14T00:00:00Z", "2026-03-14T00:00:00Z"),
        )
        conn.execute(
            "INSERT INTO pg_crm_interactions "
            "(id, contact_id, account_id, interaction_type, subject, "
            "interaction_date, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("int-s1", "", "acct-5", "other", "test", "2026-03-14T00:00:00Z", "2026-03-14T00:00:00Z"),
        )
        conn.execute(
            "INSERT INTO pg_crm_engagement_scores "
            "(id, account_id, score, score_breakdown, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("eng-s1", "acct-5", 0.55, "{}", "2026-03-14T00:00:00Z"),
        )
        conn.commit()

        with app.test_client() as client:
            resp = client.get("/api/proposal-genesis/summary")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["crm_accounts"] >= 1
            assert data["crm_interactions"] >= 1
            assert data["avg_engagement"] > 0

    def test_crm_accounts_empty(self, app, pg_db):
        conn, db_path = pg_db
        with app.test_client() as client:
            resp = client.get("/api/proposal-genesis/crm-accounts")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["count"] == 0
            assert data["accounts"] == []

    def test_crm_interactions_empty(self, app, pg_db):
        conn, db_path = pg_db
        with app.test_client() as client:
            resp = client.get("/api/proposal-genesis/crm-interactions")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["count"] == 0

    def test_engagement_scores_empty(self, app, pg_db):
        conn, db_path = pg_db
        with app.test_client() as client:
            resp = client.get("/api/proposal-genesis/engagement-scores")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["count"] == 0


# ── Phase C Daemon Integration Tests ────────────────────────────────────────


class TestDaemonPhaseC:
    """Tests for Phase C daemon integration."""

    def test_phase_c_reflexes_constant(self):
        from tools.proposal_genesis.daemon import PHASE_C_REFLEXES

        assert PHASE_C_REFLEXES == ["engage"]

    def test_engage_in_default_config(self):
        """Engage should be enabled by default in the config loading path."""
        from tools.proposal_genesis.daemon import REFLEX_NAMES

        assert "engage" in REFLEX_NAMES


# ── Phase D: R12 Publish Reflex Tests ────────────────────────────────────────


class TestPublishReflex:
    """Tests for tools/proposal_genesis/reflexes/publish.py."""

    def test_run_no_publishable_drafts(self, pg_db):
        """With no approved drafts, publish returns success with 0 articles."""
        conn, db_path = pg_db
        with patch("tools.proposal_genesis.reflexes.publish.get_connection", return_value=conn):
            from tools.proposal_genesis.reflexes.publish import run

            result = run({"max_articles_per_run": 5}, MagicMock())
            assert result["success"] is True
            assert result["metric_value"] == 0.0
            assert result["details"]["status"] == "no_publishable_drafts"

    def test_generate_case_study_basic(self):
        """Case study generation produces title, slug, body, tags."""
        from tools.proposal_genesis.reflexes.publish import _generate_case_study

        draft = {
            "opportunity_title": "Cybersecurity Assessment Platform",
            "agency": "Department of Defense",
            "domain_category": "cybersecurity",
            "draft_content": "We provide zero trust architecture. FedRAMP compliance ensures security.",
            "naics_code": "541512",
        }
        article = _generate_case_study(draft, [])
        assert "Case Study:" in article["title"]
        assert article["slug"]
        assert "## Challenge" in article["body"]
        assert "## Approach" in article["body"]
        assert len(article["tags"]) >= 1

    def test_generate_case_study_with_kb_blocks(self):
        """KB blocks inject Technical Depth section."""
        from tools.proposal_genesis.reflexes.publish import _generate_case_study

        draft = {
            "opportunity_title": "Cloud Migration",
            "agency": "DHS",
            "domain_category": "cloud",
            "draft_content": "Migrate legacy systems to cloud.",
            "naics_code": "541519",
        }
        kb = [
            {
                "title": "Cloud Best Practices",
                "content": "Use containerization with Kubernetes for scalability and resilience.",
            }
        ]
        article = _generate_case_study(draft, kb)
        assert "## Technical Depth" in article["body"]
        assert "Cloud Best Practices" in article["body"]

    def test_extract_capabilities(self):
        """Capability extraction finds keywords from proposal text."""
        from tools.proposal_genesis.reflexes.publish import _extract_capabilities

        text = "We implement zero trust architecture with FedRAMP compliance and CI/CD pipelines."
        caps = _extract_capabilities(text)
        assert "zero trust" in caps
        assert "FedRAMP" in caps
        assert "CI/CD" in caps

    def test_extract_capabilities_empty(self):
        from tools.proposal_genesis.reflexes.publish import _extract_capabilities

        assert _extract_capabilities("") == []
        assert _extract_capabilities(None) == []

    def test_sanitize_title(self):
        from tools.proposal_genesis.reflexes.publish import _sanitize_title

        # Removes solicitation numbers
        assert "FA-22-R-" not in _sanitize_title("FA8773-22-R-0001 Cloud Services")
        # Truncates long titles
        long = "A" * 200
        assert len(_sanitize_title(long)) <= 120

    def test_slugify(self):
        from tools.proposal_genesis.reflexes.publish import _slugify

        assert _slugify("Hello World! Test") == "hello-world-test"
        assert len(_slugify("A" * 200)) <= 80

    def test_stage_article(self, pg_db):
        """Stage article creates a pulse_posts entry with status draft."""
        conn, db_path = pg_db

        def mock_conn():
            c = sqlite3.connect(str(db_path))
            c.row_factory = sqlite3.Row
            # publish.py authors %s for PostgreSQL; without translation its
            # INSERTs raise and the reflex's own except returns None, which
            # reads here as "staging produced no post".
            return _TranslatingConn(c)

        with patch("tools.proposal_genesis.reflexes.publish.get_connection", side_effect=mock_conn):
            from tools.proposal_genesis.reflexes.publish import _stage_article

            article = {
                "title": "Case Study: Test",
                "slug": "case-study-test",
                "body": "# Test body",
                "domain": "cybersecurity",
            }
            draft = {"quality_score": 85.0}
            post_id = _stage_article(article, draft)
            assert post_id is not None
            assert post_id.startswith("pgpub-")
            row = conn.execute("SELECT * FROM pulse_posts WHERE id = ?", (post_id,)).fetchone()
            assert row is not None
            assert dict(row)["status"] == "draft"
            assert dict(row)["author_id"] == "pg_publish"

    def test_create_pulse_link(self, pg_db):
        """Create pulse link stores traceability entry."""
        conn, db_path = pg_db

        def mock_conn():
            c = sqlite3.connect(str(db_path))
            c.row_factory = sqlite3.Row
            # publish.py authors %s for PostgreSQL; without translation its
            # INSERTs raise and the reflex's own except returns None, which
            # reads here as "staging produced no post".
            return _TranslatingConn(c)

        with patch("tools.proposal_genesis.reflexes.publish.get_connection", side_effect=mock_conn):
            from tools.proposal_genesis.reflexes.publish import _create_pulse_link

            link_id = _create_pulse_link("post-1", "opp-1", "sec-1", "cdrl_to_case_study", 0.85)
            assert link_id is not None
            assert link_id.startswith("pglink-")
            # Re-read from db_path since get_connection creates a new conn
            c2 = sqlite3.connect(str(db_path))
            c2.row_factory = sqlite3.Row
            row = c2.execute("SELECT * FROM pg_pulse_proposal_links WHERE id = ?", (link_id,)).fetchone()
            c2.close()
            assert row is not None
            assert dict(row)["link_type"] == "cdrl_to_case_study"

    def test_audit_publish(self, pg_db):
        """Audit publish logs event to audit trail."""
        conn, db_path = pg_db

        def mock_conn():
            c = sqlite3.connect(str(db_path))
            c.row_factory = sqlite3.Row
            # publish.py authors %s for PostgreSQL; without translation its
            # INSERTs raise and the reflex's own except returns None, which
            # reads here as "staging produced no post".
            return _TranslatingConn(c)

        with patch("tools.proposal_genesis.reflexes.publish.get_connection", side_effect=mock_conn):
            from tools.proposal_genesis.reflexes.publish import _audit_publish

            _audit_publish("article_staged", "opp-1", {"post_id": "p-1"}, success=True)
            c2 = sqlite3.connect(str(db_path))
            c2.row_factory = sqlite3.Row
            row = c2.execute("SELECT * FROM pg_proposal_genesis_audit WHERE event_type = 'article_staged'").fetchone()
            c2.close()
            assert row is not None
            assert dict(row)["reflex_name"] == "publish"
            assert dict(row)["success"] == 1

    def test_extract_challenge_with_content(self):
        from tools.proposal_genesis.reflexes.publish import _extract_challenge

        text = "First sentence. Second sentence. Third sentence. Fourth sentence."
        result = _extract_challenge(text, "DoD")
        assert "First sentence" in result

    def test_extract_challenge_empty(self):
        from tools.proposal_genesis.reflexes.publish import _extract_challenge

        result = _extract_challenge("", "DoD")
        assert "DoD" in result

    def test_extract_approach_with_capabilities(self):
        from tools.proposal_genesis.reflexes.publish import _extract_approach

        text = "A. B. C. D. E. F. G. H. I."
        caps = ["zero trust", "FedRAMP"]
        result = _extract_approach(text, caps)
        assert "zero trust" in result


# ── Phase D: Dashboard API Tests ─────────────────────────────────────────────


class TestProposalGenesisPhaseD:
    """Tests for Phase D dashboard API endpoints."""

    @pytest.fixture
    def app(self, pg_db):
        from flask import Flask
        from tools.dashboard.api.proposal_genesis import proposal_genesis_api

        conn, db_path = pg_db
        app = Flask(__name__)
        app.config["TESTING"] = True
        @app.before_request
        def _inject_fake_auth_2():
            from flask import g
            g.current_user = {"username": "test_user", "role": "admin", "email": "test@test.mil", "classification": "CUI"}

        app.register_blueprint(proposal_genesis_api)

        def _mock_get_db():
            c = sqlite3.connect(str(db_path))
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            # _TranslatingConn, not the bare connection: the API authors %s for
            # PostgreSQL and the production _get_db path translates it. See that
            # class's docstring — it called this out as "~34 other already-broken
            # fixtures … a separate, wider cleanup out of this task's scope".
            # This is that cleanup.
            return _TranslatingConn(c)

        with patch("tools.dashboard.api.proposal_genesis._get_db", side_effect=_mock_get_db):
            yield app

    def test_published_articles_empty(self, app, pg_db):
        with app.test_client() as client:
            resp = client.get("/api/proposal-genesis/published-articles")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["count"] == 0
            assert data["articles"] == []

    def test_published_articles_with_data(self, app, pg_db):
        conn, db_path = pg_db
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO pulse_posts (id, title, slug, status, topic, body_markdown, "
            "readability_score, author_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "pgpub-test1",
                "Case Study: Test",
                "case-study-test",
                "draft",
                "cybersecurity",
                "# Test",
                82.5,
                "pg_publish",
                now,
                now,
            ),
        )
        conn.commit()
        with app.test_client() as client:
            resp = client.get("/api/proposal-genesis/published-articles")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["count"] >= 1
            assert data["articles"][0]["author_id"] == "pg_publish"

    def test_case_study_links_empty(self, app, pg_db):
        with app.test_client() as client:
            resp = client.get("/api/proposal-genesis/case-study-links")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["count"] == 0
            assert data["links"] == []

    def test_case_study_links_with_data(self, app, pg_db):
        conn, db_path = pg_db
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO proposal_opportunities (id, title, agency, created_at) VALUES (?, ?, ?, ?)",
            ("opp-d1", "Test Opportunity", "DoD", now),
        )
        conn.execute(
            "INSERT INTO pg_pulse_proposal_links (id, pulse_post_id, opportunity_id, "
            "section_id, link_type, relevance_score, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("link-d1", "pgpub-test1", "opp-d1", "sec-1", "cdrl_to_case_study", 0.85, now),
        )
        conn.commit()
        with app.test_client() as client:
            resp = client.get("/api/proposal-genesis/case-study-links")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["count"] >= 1
            assert data["links"][0]["link_type"] == "cdrl_to_case_study"

    def test_summary_includes_phase_d_stats(self, app, pg_db):
        conn, db_path = pg_db
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO pulse_posts (id, title, slug, status, topic, body_markdown, "
            "readability_score, author_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("pgpub-s1", "CS: Summary", "cs-summary", "draft", "cyber", "# S", 80.0, "pg_publish", now, now),
        )
        conn.execute(
            "INSERT INTO pg_pulse_proposal_links (id, pulse_post_id, opportunity_id, "
            "section_id, link_type, relevance_score, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("link-s1", "pgpub-s1", "opp-s1", "sec-s1", "cdrl_to_case_study", 0.9, now),
        )
        conn.commit()
        with app.test_client() as client:
            resp = client.get("/api/proposal-genesis/summary")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["published_articles"] >= 1
            assert data["cdrl_case_studies"] >= 1


# ── Phase D Daemon Integration Tests ────────────────────────────────────────


class TestDaemonPhaseD:
    """Tests for Phase D daemon integration."""

    def test_phase_d_reflexes_constant(self):
        from tools.proposal_genesis.daemon import PHASE_D_REFLEXES

        assert PHASE_D_REFLEXES == ["publish"]

    def test_publish_in_reflex_names(self):
        from tools.proposal_genesis.daemon import REFLEX_NAMES

        assert "publish" in REFLEX_NAMES

    def test_publish_in_config(self):
        """Publish should be enabled in proposal_genesis_config.yaml."""
        import yaml

        config_path = BASE_DIR / "args" / "proposal_genesis_config.yaml"
        if config_path.exists():
            with open(config_path) as f:
                cfg = yaml.safe_load(f)
            assert cfg["reflexes"]["publish"]["enabled"] is True
            assert cfg["reflexes"]["publish"]["risk_tier"] == "yellow"
            assert cfg["reflexes"]["publish"]["phase"] == "deliver"


# ── Phase E: Monitor Reflex Tests ──────────────────────────────────────────


class TestMonitorReflex:
    """Tests for reflexes/monitor.py — CPARS prediction + EVM early warnings."""

    def test_assess_evm_health_no_data(self):
        from tools.proposal_genesis.reflexes.monitor import _assess_evm_health

        result = _assess_evm_health(None)
        assert result["score"] == 1.0
        assert result["alerts"] == []
        assert result["cpi"] is None

    def test_assess_evm_health_healthy(self):
        from tools.proposal_genesis.reflexes.monitor import _assess_evm_health

        evm = {"cpi": 1.02, "spi": 0.98, "tcpi": 1.05}
        result = _assess_evm_health(evm)
        assert result["score"] == 1.0
        assert len(result["alerts"]) == 0

    def test_assess_evm_health_cpi_warning(self):
        from tools.proposal_genesis.reflexes.monitor import _assess_evm_health

        evm = {"cpi": 0.87, "spi": 1.0, "tcpi": 1.0}
        result = _assess_evm_health(evm)
        assert result["score"] < 1.0
        assert any(a["metric"] == "CPI" and a["level"] == "warning" for a in result["alerts"])

    def test_assess_evm_health_critical(self):
        from tools.proposal_genesis.reflexes.monitor import _assess_evm_health

        evm = {"cpi": 0.75, "spi": 0.70, "tcpi": 1.30}
        result = _assess_evm_health(evm)
        assert result["score"] < 0.20  # heavy penalties from CPI + SPI + TCPI
        assert any(a["level"] == "critical" for a in result["alerts"])

    def test_assess_evm_health_info_level(self):
        from tools.proposal_genesis.reflexes.monitor import _assess_evm_health

        evm = {"cpi": 0.93, "spi": 0.94, "tcpi": 1.0}
        result = _assess_evm_health(evm)
        assert result["score"] == pytest.approx(0.90)  # -0.05 each for CPI and SPI info
        assert all(a["level"] == "info" for a in result["alerts"])

    def test_assess_schedule_health_no_overdue(self):
        from tools.proposal_genesis.reflexes.monitor import _assess_schedule_health

        result = _assess_schedule_health([], [])
        assert result["score"] == 1.0
        assert result["overdue_count"] == 0

    def test_assess_schedule_health_overdue(self):
        from tools.proposal_genesis.reflexes.monitor import _assess_schedule_health

        overdue = [
            {"id": "d1", "title": "SSP", "days_overdue": 15},
            {"id": "d2", "title": "SBOM", "days_overdue": 45},
        ]
        result = _assess_schedule_health(overdue, [])
        assert result["score"] < 1.0
        assert result["overdue_count"] == 2
        assert any(a["level"] == "critical" for a in result["alerts"])  # 45 > 30

    def test_assess_schedule_health_surge(self):
        from tools.proposal_genesis.reflexes.monitor import _assess_schedule_health

        upcoming = [{"id": f"d{i}", "title": f"D{i}"} for i in range(5)]
        result = _assess_schedule_health([], upcoming)
        assert result["upcoming_count"] == 5
        assert any("surge" in a.get("metric", "") for a in result["alerts"])

    def test_assess_risk_health_no_events(self):
        from tools.proposal_genesis.reflexes.monitor import _assess_risk_health

        result = _assess_risk_health([])
        assert result["score"] == 1.0
        assert result["open_events"] == 0

    def test_assess_risk_health_critical_event(self):
        from tools.proposal_genesis.reflexes.monitor import _assess_risk_health

        events = [{"event_type": "data_breach", "severity": "critical", "description": "PII exposed"}]
        result = _assess_risk_health(events)
        assert result["score"] == 0.70
        assert result["open_events"] == 1

    def test_predict_cpars_all_healthy(self):
        from tools.proposal_genesis.reflexes.monitor import _predict_cpars

        evm_h = {"score": 1.0, "alerts": [], "cpi": 1.0, "spi": 1.0}
        sched_h = {"score": 1.0, "alerts": [], "overdue_count": 0}
        risk_h = {"score": 1.0, "alerts": [], "open_events": 0}
        result = _predict_cpars("c1", evm_h, sched_h, risk_h)
        assert result["predicted_score"] >= 4.0
        assert result["predicted_rating"] in ("exceptional", "very_good")
        assert "components" in result

    def test_predict_cpars_degraded(self):
        from tools.proposal_genesis.reflexes.monitor import _predict_cpars

        evm_h = {"score": 0.5, "alerts": [], "cpi": 0.85, "spi": 0.85}
        sched_h = {"score": 0.6, "alerts": [], "overdue_count": 2}
        risk_h = {"score": 0.7, "alerts": [], "open_events": 1}
        result = _predict_cpars("c1", evm_h, sched_h, risk_h)
        assert result["predicted_score"] < 4.0
        assert result["predicted_rating"] in ("satisfactory", "marginal")

    def test_compute_contract_health_green(self):
        from tools.proposal_genesis.reflexes.monitor import _compute_contract_health

        evm = {"score": 1.0}
        sched = {"score": 1.0}
        risk = {"score": 1.0}
        result = _compute_contract_health(evm, sched, risk)
        assert result["health"] == "green"
        assert result["health_score"] >= 0.80

    def test_compute_contract_health_yellow(self):
        from tools.proposal_genesis.reflexes.monitor import _compute_contract_health

        evm = {"score": 0.6}
        sched = {"score": 0.7}
        risk = {"score": 0.8}
        result = _compute_contract_health(evm, sched, risk)
        assert result["health"] == "yellow"
        assert 0.60 <= result["health_score"] < 0.80

    def test_compute_contract_health_red(self):
        from tools.proposal_genesis.reflexes.monitor import _compute_contract_health

        evm = {"score": 0.2}
        sched = {"score": 0.3}
        risk = {"score": 0.1}
        result = _compute_contract_health(evm, sched, risk)
        assert result["health"] == "red"
        assert result["health_score"] < 0.60

    @patch("tools.proposal_genesis.reflexes.monitor.get_connection")
    def test_monitor_run_no_contracts(self, mock_conn):
        from tools.proposal_genesis.reflexes.monitor import run

        mock_db = MagicMock()
        mock_db.execute.return_value.fetchall.return_value = []
        mock_conn.return_value = mock_db
        result = run({}, None)
        assert result["success"] is True
        assert result["details"]["contracts_monitored"] == 0

    @patch("tools.proposal_genesis.reflexes.monitor.get_connection")
    def test_monitor_run_with_contract(self, mock_conn):
        from tools.proposal_genesis.reflexes.monitor import run

        call_count = [0]

        def mock_conn_factory():
            call_count[0] += 1
            db = MagicMock()
            # First call: _get_active_contracts
            if call_count[0] == 1:
                db.execute.return_value.fetchall.return_value = [
                    {
                        "id": "c1",
                        "contract_number": "FA8750-25-C-0001",
                        "title": "Test",
                        "agency": "USAF",
                        "total_value": 1000000,
                        "funded_value": 500000,
                        "billed_value": 200000,
                        "pop_start": "2025-01-01",
                        "pop_end": "2026-12-31",
                        "status": "active",
                        "health": "green",
                        "health_score": 1.0,
                        "cpars_rating_current": None,
                        "opportunity_id": "opp1",
                    }
                ]
            # Subsequent calls: EVM/deliverables/events queries + audit writes
            else:
                db.execute.return_value.fetchall.return_value = []
                db.execute.return_value.fetchone.return_value = None
            return db

        mock_conn.side_effect = mock_conn_factory
        result = run({}, None)
        assert result["success"] is True
        assert result["details"]["contracts_monitored"] == 1


# ── Phase E: Fulfill Reflex Tests ──────────────────────────────────────────


class TestFulfillReflex:
    """Tests for reflexes/fulfill.py — CDRL auto-generation + compliance refresh."""

    def test_resolve_cdrl_type_ssp(self):
        from tools.proposal_genesis.reflexes.fulfill import _resolve_cdrl_type

        d = {"cdrl_number": "A001-SSP", "title": "System Security Plan", "deliverable_type": "documentation"}
        assert _resolve_cdrl_type(d) == "ssp"

    def test_resolve_cdrl_type_sbom(self):
        from tools.proposal_genesis.reflexes.fulfill import _resolve_cdrl_type

        d = {"cdrl_number": "B002", "title": "Software Bill of Materials", "deliverable_type": "software"}
        assert _resolve_cdrl_type(d) == "sbom"

    def test_resolve_cdrl_type_poam(self):
        from tools.proposal_genesis.reflexes.fulfill import _resolve_cdrl_type

        d = {"cdrl_number": "", "title": "Plan of Action and Milestones", "deliverable_type": "documentation"}
        assert _resolve_cdrl_type(d) == "poam"

    def test_resolve_cdrl_type_stig(self):
        from tools.proposal_genesis.reflexes.fulfill import _resolve_cdrl_type

        d = {"cdrl_number": "STIG-001", "title": "STIG Checklist", "deliverable_type": "documentation"}
        assert _resolve_cdrl_type(d) == "stig_checklist"

    def test_resolve_cdrl_type_evm(self):
        from tools.proposal_genesis.reflexes.fulfill import _resolve_cdrl_type

        d = {"cdrl_number": "EVM-RPT", "title": "Earned Value Report", "deliverable_type": "data"}
        assert _resolve_cdrl_type(d) == "evm_report"

    def test_resolve_cdrl_type_test(self):
        from tools.proposal_genesis.reflexes.fulfill import _resolve_cdrl_type

        d = {"cdrl_number": "", "title": "Test Report Deliverable", "deliverable_type": "test_result"}
        assert _resolve_cdrl_type(d) == "test_report"

    def test_resolve_cdrl_type_fallback(self):
        from tools.proposal_genesis.reflexes.fulfill import _resolve_cdrl_type

        d = {"cdrl_number": "", "title": "Custom Report", "deliverable_type": "documentation"}
        assert _resolve_cdrl_type(d) == "ssp"  # fallback from DELIVERABLE_TYPE_TO_CDRL

    def test_resolve_cdrl_type_unknown(self):
        from tools.proposal_genesis.reflexes.fulfill import _resolve_cdrl_type

        d = {"cdrl_number": "", "title": "Random Item", "deliverable_type": "other"}
        assert _resolve_cdrl_type(d) is None

    def test_tool_mapping_completeness(self):
        from tools.proposal_genesis.reflexes.fulfill import TOOL_MAPPING

        assert len(TOOL_MAPPING) >= 9
        assert "ssp" in TOOL_MAPPING
        assert "sbom" in TOOL_MAPPING
        assert "evm_report" in TOOL_MAPPING

    @patch("tools.proposal_genesis.reflexes.fulfill.get_connection")
    def test_fulfill_run_no_deliverables(self, mock_conn):
        from tools.proposal_genesis.reflexes.fulfill import run

        mock_db = MagicMock()
        mock_db.execute.return_value.fetchall.return_value = []
        mock_conn.return_value = mock_db
        result = run({"days_ahead": 14, "max_generations_per_run": 10, "stale_threshold_days": 90}, None)
        assert result["success"] is True
        assert result["details"]["cdrls_generated"] == 0

    @patch("tools.proposal_genesis.reflexes.fulfill.subprocess")
    @patch("tools.proposal_genesis.reflexes.fulfill.get_connection")
    def test_fulfill_generate_cdrl_tool_not_found(self, mock_conn, mock_subprocess):
        from tools.proposal_genesis.reflexes.fulfill import _generate_cdrl

        deliv = {"contract_id": "c1", "opportunity_id": "opp1"}
        success, result = _generate_cdrl(deliv, "nonexistent_type")
        assert success is False
        assert "No tool mapping" in result.get("error", "")


# ── Phase E: Daemon Integration Tests ──────────────────────────────────────


class TestDaemonPhaseE:
    """Tests for Phase E daemon integration."""

    def test_phase_e_reflexes_constant(self):
        from tools.proposal_genesis.daemon import PHASE_E_REFLEXES

        assert PHASE_E_REFLEXES == ["monitor", "fulfill"]

    def test_monitor_in_reflex_names(self):
        from tools.proposal_genesis.daemon import REFLEX_NAMES

        assert "monitor" in REFLEX_NAMES
        assert "fulfill" in REFLEX_NAMES

    def test_monitor_config_enabled(self):
        """Monitor and fulfill should be enabled in proposal_genesis_config.yaml."""
        import yaml

        config_path = BASE_DIR / "args" / "proposal_genesis_config.yaml"
        if config_path.exists():
            with open(config_path) as f:
                cfg = yaml.safe_load(f)
            assert cfg["reflexes"]["monitor"]["enabled"] is True
            assert cfg["reflexes"]["monitor"]["risk_tier"] == "green"
            assert cfg["reflexes"]["monitor"]["phase"] == "deliver"
            assert cfg["reflexes"]["fulfill"]["enabled"] is True
            assert cfg["reflexes"]["fulfill"]["risk_tier"] == "yellow"
            assert cfg["reflexes"]["fulfill"]["phase"] == "deliver"
            assert cfg["reflexes"]["fulfill"]["days_ahead"] == 14


# ── Phase E: API Tests ─────────────────────────────────────────────────────


class TestProposalGenesisPhaseEAPI:
    """Tests for Phase E dashboard API endpoints."""

    @pytest.fixture
    def app(self, pg_db):
        from flask import Flask
        from tools.dashboard.api.proposal_genesis import proposal_genesis_api

        conn, db_path = pg_db
        # Add CPMP tables needed for Phase E
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS cpmp_contracts (
                id TEXT PRIMARY KEY, contract_number TEXT, title TEXT,
                agency TEXT, status TEXT DEFAULT 'active',
                health TEXT DEFAULT 'green', health_score REAL DEFAULT 1.0,
                total_value REAL, funded_value REAL, billed_value REAL,
                pop_start TEXT, pop_end TEXT,
                cpars_rating_current TEXT,
                opportunity_id TEXT
            );
            CREATE TABLE IF NOT EXISTS cpmp_deliverables (
                id TEXT PRIMARY KEY, contract_id TEXT, cdrl_number TEXT,
                title TEXT, deliverable_type TEXT, due_date TEXT,
                status TEXT DEFAULT 'not_started', days_overdue INTEGER DEFAULT 0,
                generated_by_tool TEXT, updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS cpmp_cdrl_generations (
                id TEXT PRIMARY KEY, deliverable_id TEXT, contract_id TEXT,
                cdrl_type TEXT, generation_tool TEXT, status TEXT,
                error_message TEXT, generated_by TEXT, created_at TEXT
            );
        """)
        conn.commit()

        app = Flask(__name__)
        app.config["TESTING"] = True
        @app.before_request
        def _inject_fake_auth_3():
            from flask import g
            g.current_user = {"username": "test_user", "role": "admin", "email": "test@test.mil", "classification": "CUI"}

        app.register_blueprint(proposal_genesis_api)

        def _mock_get_db():
            c = sqlite3.connect(str(db_path))
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            # _TranslatingConn, not the bare connection: the API authors %s for
            # PostgreSQL and the production _get_db path translates it. See that
            # class's docstring — it called this out as "~34 other already-broken
            # fixtures … a separate, wider cleanup out of this task's scope".
            # This is that cleanup.
            return _TranslatingConn(c)

        with patch("tools.dashboard.api.proposal_genesis._get_db", side_effect=_mock_get_db):
            yield app

    def test_contract_health_endpoint_empty(self, app, pg_db):
        _, db_path = pg_db
        with app.test_client() as client:
            resp = client.get("/api/proposal-genesis/contract-health")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["contracts"] == []
            assert data["count"] == 0

    def test_contract_health_endpoint_with_data(self, app, pg_db):
        conn, db_path = pg_db
        conn.execute(
            "INSERT INTO cpmp_contracts (id, contract_number, title, agency, status, "
            "health, health_score, total_value, pop_start, pop_end) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "c1",
                "FA8750-25-C-0001",
                "Cyber Defense",
                "USAF",
                "active",
                "yellow",
                0.72,
                2500000,
                "2025-01-01",
                "2026-12-31",
            ),
        )
        conn.commit()

        with app.test_client() as client:
            resp = client.get("/api/proposal-genesis/contract-health")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["count"] == 1
            assert data["contracts"][0]["contract_number"] == "FA8750-25-C-0001"
            assert data["contracts"][0]["health_score"] == 0.72

    def test_overdue_deliverables_endpoint(self, app, pg_db):
        conn, db_path = pg_db
        conn.execute(
            "INSERT INTO cpmp_contracts (id, contract_number, title, status) VALUES (?, ?, ?, ?)",
            ("c1", "FA8750-25-C-0001", "Test", "active"),
        )
        conn.execute(
            "INSERT INTO cpmp_deliverables (id, contract_id, cdrl_number, title, "
            "deliverable_type, due_date, status, days_overdue) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("d1", "c1", "A001", "System Security Plan", "documentation", "2026-02-01", "overdue", 41),
        )
        conn.commit()

        with app.test_client() as client:
            resp = client.get("/api/proposal-genesis/overdue-deliverables")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["count"] == 1
            assert data["deliverables"][0]["days_overdue"] == 41

    def test_cdrl_generations_endpoint(self, app, pg_db):
        conn, db_path = pg_db
        conn.execute(
            "INSERT INTO cpmp_contracts (id, contract_number, title, status) VALUES (?, ?, ?, ?)",
            ("c1", "W912DY-25-C-0002", "Test", "active"),
        )
        conn.execute(
            "INSERT INTO cpmp_deliverables (id, contract_id, cdrl_number, title, "
            "deliverable_type, status) VALUES (?, ?, ?, ?, ?, ?)",
            ("d1", "c1", "B002", "SBOM", "software", "in_progress"),
        )
        conn.execute(
            "INSERT INTO cpmp_cdrl_generations (id, deliverable_id, contract_id, "
            "cdrl_type, generation_tool, status, generated_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "cg1",
                "d1",
                "c1",
                "sbom",
                "tools/compliance/sbom_generator.py",
                "generated",
                "pg_fulfill",
                "2026-03-14T09:00:00Z",
            ),
        )
        conn.commit()

        with app.test_client() as client:
            resp = client.get("/api/proposal-genesis/cdrl-generations")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["count"] == 1
            assert data["generations"][0]["cdrl_type"] == "sbom"
            assert data["generations"][0]["status"] == "generated"

    def test_cpars_predictions_endpoint(self, app, pg_db):
        conn, db_path = pg_db
        conn.execute(
            "INSERT INTO pg_proposal_genesis_audit "
            "(id, event_type, reflex_name, risk_tier, opportunity_id, details, success, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "pa1",
                "contract_monitored",
                "monitor",
                "green",
                "c1",
                json.dumps({"health": {"health": "green", "health_score": 0.95}}),
                1,
                "2026-03-14T08:00:00Z",
            ),
        )
        conn.commit()

        with app.test_client() as client:
            resp = client.get("/api/proposal-genesis/cpars-predictions")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["count"] == 1
            assert data["predictions"][0]["parsed"]["health"]["health"] == "green"

    def test_summary_phase_e_stats(self, app, pg_db):
        conn, db_path = pg_db
        conn.execute(
            "INSERT INTO cpmp_contracts (id, contract_number, title, status, health, health_score) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("c1", "FA8750", "Test", "active", "red", 0.45),
        )
        conn.execute(
            "INSERT INTO cpmp_deliverables (id, contract_id, title, status, days_overdue) VALUES (?, ?, ?, ?, ?)",
            ("d1", "c1", "SSP", "overdue", 10),
        )
        conn.execute(
            "INSERT INTO cpmp_cdrl_generations (id, deliverable_id, contract_id, "
            "cdrl_type, generation_tool, status, generated_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("cg1", "d1", "c1", "ssp", "ssp_gen.py", "generated", "pg_fulfill", "2026-03-14T00:00:00Z"),
        )
        conn.commit()

        with app.test_client() as client:
            resp = client.get("/api/proposal-genesis/summary")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["active_contracts"] == 1
            assert data["at_risk_contracts"] == 1
            assert data["overdue_deliverables"] == 1
            assert data["cdrls_generated"] == 1


# ── Phase F: Daemon Tests ─────────────────────────────────────────────────


class TestDaemonPhaseF:
    """Tests for Phase F daemon integration."""

    def test_phase_f_reflexes_constant(self):
        from tools.proposal_genesis.daemon import PHASE_F_REFLEXES

        assert PHASE_F_REFLEXES == ["decide", "analyze", "train"]

    def test_decide_in_reflex_names(self):
        from tools.proposal_genesis.daemon import REFLEX_NAMES

        assert "decide" in REFLEX_NAMES
        assert "analyze" in REFLEX_NAMES
        assert "train" in REFLEX_NAMES

    def test_phase_f_config_enabled(self):
        """Decide, analyze, and train should be enabled in config."""
        import yaml

        config_path = BASE_DIR / "args" / "proposal_genesis_config.yaml"
        if config_path.exists():
            with open(config_path) as f:
                cfg = yaml.safe_load(f)
            assert cfg["reflexes"]["decide"]["enabled"] is True
            assert cfg["reflexes"]["decide"]["risk_tier"] == "green"
            assert cfg["reflexes"]["decide"]["phase"] == "propose"
            assert cfg["reflexes"]["analyze"]["enabled"] is True
            assert cfg["reflexes"]["analyze"]["risk_tier"] == "green"
            assert cfg["reflexes"]["analyze"]["phase"] == "learn"
            assert cfg["reflexes"]["train"]["enabled"] is True
            assert cfg["reflexes"]["train"]["risk_tier"] == "yellow"
            assert cfg["reflexes"]["train"]["phase"] == "learn"


# ── Phase F: Decide Reflex Tests ──────────────────────────────────────────


class TestDecideReflex:
    """Tests for reflexes/decide.py bid/no-bid scoring."""

    def test_score_weights_sum_to_one(self):
        from tools.proposal_genesis.reflexes.decide import SCORE_WEIGHTS

        total = sum(SCORE_WEIGHTS.values())
        assert abs(total - 1.0) < 0.001

    def test_score_weights_has_six_dimensions(self):
        from tools.proposal_genesis.reflexes.decide import SCORE_WEIGHTS

        assert len(SCORE_WEIGHTS) == 6
        assert "capability_fit" in SCORE_WEIGHTS
        assert "past_performance" in SCORE_WEIGHTS
        assert "competitive_position" in SCORE_WEIGHTS
        assert "compliance_readiness" in SCORE_WEIGHTS
        assert "resource_availability" in SCORE_WEIGHTS
        assert "strategic_alignment" in SCORE_WEIGHTS

    def test_thresholds(self):
        from tools.proposal_genesis.reflexes.decide import BID_THRESHOLD, NO_BID_THRESHOLD

        assert BID_THRESHOLD == 0.60
        assert NO_BID_THRESHOLD == 0.35
        assert BID_THRESHOLD > NO_BID_THRESHOLD

    @patch("tools.proposal_genesis.reflexes.decide.get_connection")
    def test_score_opportunity_minimal(self, mock_conn):
        """Score opportunity with all-zero sub-scores returns deferred or no_bid."""
        from tools.proposal_genesis.reflexes.decide import score_opportunity

        mock_db = MagicMock()
        # All sub-queries return zeros
        mock_row = MagicMock()
        mock_row.__getitem__ = lambda self, key: 0
        mock_db.execute.return_value.fetchone.return_value = mock_row
        mock_conn.return_value = mock_db

        opp = {
            "id": "opp-1",
            "title": "Test",
            "agency": "DoD",
            "naics_code": "541512",
            "set_aside": "",
            "estimated_value": 100000,
        }
        result = score_opportunity(opp)

        assert "dimensions" in result
        assert "composite" in result
        assert "decision" in result
        assert result["decision"] in ("bid", "no_bid", "deferred")
        assert 0 <= result["composite"] <= 1.0

    @patch("tools.proposal_genesis.reflexes.decide.get_connection")
    def test_run_with_no_opportunities(self, mock_conn):
        """Run with empty opportunities returns success with zero decisions."""
        from tools.proposal_genesis.reflexes.decide import run

        mock_db = MagicMock()
        mock_db.execute.return_value.fetchall.return_value = []
        mock_conn.return_value = mock_db

        result = run({"max_decisions_per_run": 5}, None)
        assert result["success"] is True
        assert result["metric_value"] == 0.0
        assert result["details"]["opportunities_evaluated"] == 0


# ── Phase F: Analyze Reflex Tests ─────────────────────────────────────────


class TestAnalyzeReflex:
    """Tests for reflexes/analyze.py win/loss analysis."""

    def test_category_keywords_covers_seven(self):
        from tools.proposal_genesis.reflexes.analyze import CATEGORY_KEYWORDS

        assert len(CATEGORY_KEYWORDS) >= 6
        for cat in ["technical", "management", "pricing", "past_performance", "compliance", "staffing"]:
            assert cat in CATEGORY_KEYWORDS

    def test_classify_category_technical(self):
        from tools.proposal_genesis.reflexes.analyze import _classify_category

        assert _classify_category("cloud infrastructure migration") == "technical"

    def test_classify_category_pricing(self):
        from tools.proposal_genesis.reflexes.analyze import _classify_category

        assert _classify_category("budget cost overrun on LPTA bid") == "pricing"

    def test_classify_category_management(self):
        from tools.proposal_genesis.reflexes.analyze import _classify_category

        assert _classify_category("schedule timeline slippage") == "management"

    def test_classify_category_compliance(self):
        from tools.proposal_genesis.reflexes.analyze import _classify_category

        assert _classify_category("fedramp cmmc nist compliance") == "compliance"

    def test_classify_category_fallback(self):
        from tools.proposal_genesis.reflexes.analyze import _classify_category

        assert _classify_category("xyzzy foobar") == "other"

    @patch("tools.proposal_genesis.reflexes.analyze.get_connection")
    def test_run_with_no_completed(self, mock_conn):
        """Run with no completed opportunities returns success with zero analyses."""
        from tools.proposal_genesis.reflexes.analyze import run

        mock_db = MagicMock()
        mock_db.execute.return_value.fetchall.return_value = []
        mock_conn.return_value = mock_db

        result = run({"max_analyses_per_run": 10}, None)
        assert result["success"] is True
        assert result["metric_value"] == 0.0
        assert result["details"]["analyses_completed"] == 0

    def test_analyze_opportunity_generates_lessons(self):
        from tools.proposal_genesis.reflexes.analyze import _analyze_opportunity
        import json

        opp = {
            "id": "opp-1",
            "outcome": "lost",
            "decision": "bid",
            "win_probability": 0.75,
            "score_breakdown": json.dumps(
                {
                    "capability_fit": 0.3,
                    "compliance_readiness": 0.4,
                    "resource_availability": 0.35,
                }
            ),
            "agency": "Army",
        }
        analysis = _analyze_opportunity(opp)
        assert analysis["outcome"] == "lost"
        assert len(analysis["lessons"]) >= 1
        # Should flag overestimated win probability
        found_mgmt = any(l["category"] == "management" for l in analysis["lessons"])
        assert found_mgmt


# ── Phase F: Train Reflex Tests ───────────────────────────────────────────


class TestTrainReflex:
    """Tests for reflexes/train.py fine-tuning pair generation."""

    def test_content_hash_deterministic(self):
        from tools.proposal_genesis.reflexes.train import _content_hash

        h1 = _content_hash("sys", "user", "expected")
        h2 = _content_hash("sys", "user", "expected")
        assert h1 == h2
        assert len(h1) == 64  # SHA-256 hex

    def test_content_hash_different_input(self):
        from tools.proposal_genesis.reflexes.train import _content_hash

        h1 = _content_hash("sys1", "user1", "expected1")
        h2 = _content_hash("sys2", "user2", "expected2")
        assert h1 != h2

    def test_generate_draft_pairs_short_content(self):
        from tools.proposal_genesis.reflexes.train import _generate_draft_pairs

        # Too-short content should yield no pairs
        draft = {"final_content": "Short", "section_type": "technical"}
        pairs = _generate_draft_pairs(draft)
        assert pairs == []

    def test_generate_draft_pairs_with_content(self):
        from tools.proposal_genesis.reflexes.train import _generate_draft_pairs

        content = "x" * 500  # Long enough
        draft = {
            "final_content": content,
            "section_type": "technical",
            "agency": "DoD",
            "opp_title": "Cyber Program",
            "id": "d-001",
        }
        pairs = _generate_draft_pairs(draft)
        assert len(pairs) >= 1
        assert pairs[0]["source_type"] == "approved_draft"
        assert "system_prompt" in pairs[0]
        assert "user_input" in pairs[0]
        assert "expected_output" in pairs[0]

    def test_generate_lesson_pairs(self):
        from tools.proposal_genesis.reflexes.train import _generate_lesson_pairs

        lesson = {
            "id": "l-001",
            "lesson": "Budget estimation was 30% below actual cost",
            "category": "pricing",
            "outcome": "lost",
            "agency": "Navy",
            "our_strengths": "Technical depth",
            "our_weaknesses": "Pricing accuracy",
        }
        pairs = _generate_lesson_pairs(lesson)
        assert len(pairs) == 1
        assert pairs[0]["source_type"] == "win_loss_lesson"

    def test_generate_lesson_pairs_short_text(self):
        from tools.proposal_genesis.reflexes.train import _generate_lesson_pairs

        lesson = {"lesson": "ok", "id": "l-002"}
        pairs = _generate_lesson_pairs(lesson)
        assert pairs == []

    def test_generate_kb_pairs(self):
        from tools.proposal_genesis.reflexes.train import _generate_kb_pairs

        entry = {
            "id": "kb-001",
            "title": "Zero Trust Architecture",
            "content": "x" * 200,
            "domain": "cybersecurity",
        }
        pairs = _generate_kb_pairs(entry)
        assert len(pairs) == 1
        assert pairs[0]["source_type"] == "knowledge_base"

    @patch("tools.proposal_genesis.reflexes.train.get_connection")
    def test_run_with_no_sources(self, mock_conn):
        """Run with no source data returns success with zero pairs."""
        from tools.proposal_genesis.reflexes.train import run

        mock_db = MagicMock()
        mock_db.execute.return_value.fetchall.return_value = []
        mock_db.execute.return_value.fetchone.return_value = {"id": "ds-existing"}
        mock_conn.return_value = mock_db

        result = run({"max_pairs_per_run": 50}, None)
        assert result["success"] is True
        assert result["metric_value"] == 0.0
        assert result["details"]["pairs_generated"] == 0

    @patch("tools.proposal_genesis.reflexes.train.get_connection")
    def test_get_or_create_dataset_existing(self, mock_conn):
        """Finds existing proposal_drafting dataset."""
        from tools.proposal_genesis.reflexes.train import _get_or_create_dataset

        mock_db = MagicMock()
        mock_db.execute.return_value.fetchone.return_value = {"id": "ds-existing123"}
        mock_conn.return_value = mock_db
        ds_id = _get_or_create_dataset()
        assert ds_id == "ds-existing123"

    @patch("tools.proposal_genesis.reflexes.train.get_connection")
    def test_get_or_create_dataset_creates_new(self, mock_conn):
        """Creates new dataset when none found."""
        from tools.proposal_genesis.reflexes.train import _get_or_create_dataset

        mock_db = MagicMock()
        mock_db.execute.return_value.fetchone.return_value = None
        mock_conn.return_value = mock_db
        ds_id = _get_or_create_dataset()
        assert ds_id is not None
        assert ds_id.startswith("ds-")
        # Should have committed after INSERT
        mock_db.commit.assert_called()

    @patch("tools.proposal_genesis.reflexes.train.get_connection")
    def test_get_or_create_dataset_exception(self, mock_conn):
        """Returns None on exception."""
        from tools.proposal_genesis.reflexes.train import _get_or_create_dataset

        mock_db = MagicMock()
        mock_db.execute.side_effect = Exception("DB error")
        mock_conn.return_value = mock_db
        ds_id = _get_or_create_dataset()
        assert ds_id is None

    @patch("tools.proposal_genesis.reflexes.train.get_connection")
    def test_update_dataset_count(self, mock_conn):
        """Increments example_count on dataset."""
        from tools.proposal_genesis.reflexes.train import _update_dataset_count

        mock_db = MagicMock()
        mock_conn.return_value = mock_db
        _update_dataset_count("ds-abc", 5)
        mock_db.execute.assert_called_once()
        mock_db.commit.assert_called()

    @patch("tools.proposal_genesis.reflexes.train.get_connection")
    def test_update_dataset_count_zero_skips(self, mock_conn):
        """Zero or negative count does nothing."""
        from tools.proposal_genesis.reflexes.train import _update_dataset_count

        mock_db = MagicMock()
        mock_conn.return_value = mock_db
        _update_dataset_count("ds-abc", 0)
        mock_db.execute.assert_not_called()

    @patch("tools.proposal_genesis.reflexes.train.get_connection")
    def test_update_dataset_count_no_id_skips(self, mock_conn):
        """No dataset_id does nothing."""
        from tools.proposal_genesis.reflexes.train import _update_dataset_count

        mock_db = MagicMock()
        mock_conn.return_value = mock_db
        _update_dataset_count(None, 5)
        mock_db.execute.assert_not_called()

    @patch("tools.proposal_genesis.reflexes.train.get_connection")
    def test_run_includes_dataset_id(self, mock_conn):
        """Run result includes dataset_id in details."""
        from tools.proposal_genesis.reflexes.train import run

        mock_db = MagicMock()
        mock_db.execute.return_value.fetchall.return_value = []
        mock_db.execute.return_value.fetchone.return_value = {"id": "ds-test456"}
        mock_conn.return_value = mock_db

        result = run({"max_pairs_per_run": 10}, None)
        assert result["success"] is True
        assert "dataset_id" in result["details"]


# ── Phase F: API Tests ────────────────────────────────────────────────────


class TestProposalGenesisPhaseF_API:
    """Tests for Phase F dashboard API endpoints."""

    @pytest.fixture
    def app(self, pg_db):
        from flask import Flask
        from tools.dashboard.api.proposal_genesis import proposal_genesis_api

        conn, db_path = pg_db
        # Add Phase F tables
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS sam_gov_opportunities (
                id TEXT PRIMARY KEY, title TEXT, agency TEXT,
                naics_code TEXT, set_aside TEXT,
                response_deadline TEXT, estimated_value REAL,
                status TEXT, sol_number TEXT
            );
            CREATE TABLE IF NOT EXISTS pg_bid_decisions (
                id TEXT PRIMARY KEY, opportunity_id TEXT,
                decision TEXT, win_probability REAL,
                score_breakdown TEXT, rationale TEXT,
                decided_by TEXT, created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS pg_bid_decision_outcomes (
                id TEXT PRIMARY KEY, bid_decision_id TEXT,
                outcome TEXT, actual_award_date TEXT,
                award_amount REAL, notes TEXT, created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS pg_win_loss_records (
                id TEXT PRIMARY KEY, opportunity_id TEXT,
                outcome TEXT, competitor_name TEXT,
                competitor_strengths TEXT, our_strengths TEXT,
                our_weaknesses TEXT, lessons_learned TEXT,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS pg_win_loss_lessons (
                id TEXT PRIMARY KEY, win_loss_id TEXT,
                category TEXT, lesson TEXT,
                actionable INTEGER DEFAULT 0,
                applied INTEGER DEFAULT 0,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS pg_training_pair_sources (
                id TEXT PRIMARY KEY, source_type TEXT NOT NULL,
                source_id TEXT NOT NULL,
                pair_count INTEGER NOT NULL DEFAULT 0,
                content_hash TEXT,
                created_at TEXT NOT NULL
            );
        """)
        conn.commit()

        app = Flask(__name__)
        app.config["TESTING"] = True
        @app.before_request
        def _inject_fake_auth_4():
            from flask import g
            g.current_user = {"username": "test_user", "role": "admin", "email": "test@test.mil", "classification": "CUI"}

        app.register_blueprint(proposal_genesis_api)

        def _mock_get_db():
            c = sqlite3.connect(str(db_path))
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            # _TranslatingConn, not the bare connection: the API authors %s for
            # PostgreSQL and the production _get_db path translates it. See that
            # class's docstring — it called this out as "~34 other already-broken
            # fixtures … a separate, wider cleanup out of this task's scope".
            # This is that cleanup.
            return _TranslatingConn(c)

        with patch("tools.dashboard.api.proposal_genesis._get_db", side_effect=_mock_get_db):
            yield app

    def test_bid_decisions_empty(self, app, pg_db):
        _, db_path = pg_db
        with app.test_client() as client:
            resp = client.get("/api/proposal-genesis/bid-decisions")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["decisions"] == []
            assert data["count"] == 0

    def test_bid_decisions_with_data(self, app, pg_db):
        conn, db_path = pg_db
        conn.execute(
            "INSERT INTO sam_gov_opportunities (id, title, agency, naics_code, status) VALUES (?, ?, ?, ?, ?)",
            ("opp-1", "Cyber Defense", "USAF", "541512", "tracked"),
        )
        conn.execute(
            "INSERT INTO pg_bid_decisions (id, opportunity_id, decision, win_probability, "
            "score_breakdown, rationale, decided_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "dec-1",
                "opp-1",
                "bid",
                0.72,
                '{"capability_fit":0.8,"past_performance":0.6}',
                "Composite score: 72%. Recommend: BID.",
                "pg_decide",
                "2026-03-14T10:00:00Z",
            ),
        )
        conn.commit()

        with app.test_client() as client:
            resp = client.get("/api/proposal-genesis/bid-decisions")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["count"] == 1
            assert data["decisions"][0]["decision"] == "bid"
            assert data["decisions"][0]["scores"]["capability_fit"] == 0.8
            assert data["decisions"][0]["opportunity_title"] == "Cyber Defense"

    def test_win_loss_records_empty(self, app, pg_db):
        _, db_path = pg_db
        with app.test_client() as client:
            resp = client.get("/api/proposal-genesis/win-loss-records")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["records"] == []

    def test_win_loss_records_with_data(self, app, pg_db):
        conn, db_path = pg_db
        conn.execute(
            "INSERT INTO sam_gov_opportunities (id, title, agency) VALUES (?, ?, ?)",
            ("opp-1", "AI Platform", "Army"),
        )
        conn.execute(
            "INSERT INTO pg_win_loss_records (id, opportunity_id, outcome, competitor_name, "
            "our_strengths, our_weaknesses, lessons_learned, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "wl-1",
                "opp-1",
                "lost",
                "Competitor X",
                "Technical depth (80%)",
                "Pricing (35%)",
                '["Improve pricing strategy"]',
                "2026-03-14T10:00:00Z",
            ),
        )
        conn.commit()

        with app.test_client() as client:
            resp = client.get("/api/proposal-genesis/win-loss-records")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["count"] == 1
            assert data["records"][0]["outcome"] == "lost"
            assert data["records"][0]["lessons_parsed"] == ["Improve pricing strategy"]

    def test_win_loss_lessons_empty(self, app, pg_db):
        _, db_path = pg_db
        with app.test_client() as client:
            resp = client.get("/api/proposal-genesis/win-loss-lessons")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["lessons"] == []

    def test_win_loss_lessons_with_filter(self, app, pg_db):
        conn, db_path = pg_db
        conn.execute(
            "INSERT INTO pg_win_loss_records (id, opportunity_id, outcome, created_at) VALUES (?, ?, ?, ?)",
            ("wl-1", "opp-1", "lost", "2026-03-14T00:00:00Z"),
        )
        conn.execute(
            "INSERT INTO pg_win_loss_lessons (id, win_loss_id, category, lesson, "
            "actionable, applied, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("les-1", "wl-1", "technical", "Improve cloud architecture coverage", 1, 0, "2026-03-14T10:00:00Z"),
        )
        conn.execute(
            "INSERT INTO pg_win_loss_lessons (id, win_loss_id, category, lesson, "
            "actionable, applied, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("les-2", "wl-1", "pricing", "LPTA strategy needs rework", 1, 0, "2026-03-14T10:01:00Z"),
        )
        conn.commit()

        with app.test_client() as client:
            # All lessons
            resp = client.get("/api/proposal-genesis/win-loss-lessons")
            assert resp.get_json()["count"] == 2

            # Filter by category
            resp = client.get("/api/proposal-genesis/win-loss-lessons?category=technical")
            data = resp.get_json()
            assert data["count"] == 1
            assert data["lessons"][0]["category"] == "technical"

    def test_training_pairs_empty(self, app, pg_db):
        _, db_path = pg_db
        with app.test_client() as client:
            resp = client.get("/api/proposal-genesis/training-pairs")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["pairs"] == []
            assert data["by_source"] == {}

    def test_training_pairs_with_data(self, app, pg_db):
        conn, db_path = pg_db
        conn.execute(
            "INSERT INTO pg_training_pair_sources (id, source_type, source_id, "
            "pair_count, content_hash, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("tp-1", "approved_draft", "d-001", 2, "abc123", "2026-03-14T10:00:00Z"),
        )
        conn.execute(
            "INSERT INTO pg_training_pair_sources (id, source_type, source_id, "
            "pair_count, content_hash, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("tp-2", "win_loss_lesson", "l-001", 1, "def456", "2026-03-14T10:01:00Z"),
        )
        conn.commit()

        with app.test_client() as client:
            resp = client.get("/api/proposal-genesis/training-pairs")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["count"] == 2
            assert "approved_draft" in data["by_source"]
            assert "win_loss_lesson" in data["by_source"]
            assert data["by_source"]["approved_draft"]["count"] == 1

    def test_summary_phase_f_stats(self, app, pg_db):
        conn, db_path = pg_db
        conn.execute(
            "INSERT INTO pg_bid_decisions (id, opportunity_id, decision, win_probability, "
            "decided_by, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("dec-1", "opp-1", "bid", 0.72, "pg_decide", "2026-03-14T00:00:00Z"),
        )
        conn.execute(
            "INSERT INTO pg_bid_decisions (id, opportunity_id, decision, win_probability, "
            "decided_by, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("dec-2", "opp-2", "no_bid", 0.25, "pg_decide", "2026-03-14T00:01:00Z"),
        )
        conn.execute(
            "INSERT INTO pg_win_loss_records (id, opportunity_id, outcome, created_at) VALUES (?, ?, ?, ?)",
            ("wl-1", "opp-1", "won", "2026-03-14T00:00:00Z"),
        )
        conn.execute(
            "INSERT INTO pg_win_loss_lessons (id, win_loss_id, category, lesson, "
            "actionable, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("les-1", "wl-1", "management", "Good bid decision", 1, "2026-03-14T00:00:00Z"),
        )
        conn.execute(
            "INSERT INTO pg_training_pair_sources (id, source_type, source_id, "
            "pair_count, content_hash, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("tp-1", "approved_draft", "d-1", 2, "hash1", "2026-03-14T00:00:00Z"),
        )
        conn.commit()

        with app.test_client() as client:
            resp = client.get("/api/proposal-genesis/summary")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["bid_decisions"] == 2
            assert data["bid_recommendations"] == 1
            assert data["win_loss_records"] == 1
            assert data["win_loss_lessons"] == 1
            assert data["training_pairs"] == 1


# ── Trace Reflex Tests ────────────────────────────────────────────────────────


class TestTraceReflex:
    """Tests for reflexes/trace.py (R22).

    Covers the AI-ify opp 5451 modernization: inline magic-number thresholds were
    extracted into named module-level constants (hardcoded_threshold ->
    anomaly_detection). These tests lock the constants and verify run() still
    produces a well-formed result when there are no opportunities to trace.
    """

    def test_threshold_constants_extracted(self):
        from tools.proposal_genesis.reflexes import trace

        # Magic numbers are now named constants, not inline literals.
        assert trace._OPPS_WITH_MATRICES_LIMIT == 20
        assert trace._SECTION_IDS_LIMIT == 10
        assert trace._STALE_AMENDMENTS_LIMIT == 10
        assert trace._CHANGE_SUMMARY_CHARS == 100

    def test_query_uses_limit_constant(self):
        # The opportunities query interpolates the named constant, not a literal.
        from tools.proposal_genesis.reflexes import trace

        with patch("tools.proposal_genesis.reflexes.trace.get_connection") as mock_conn:
            mock_db = MagicMock()
            mock_db.execute.return_value.fetchall.return_value = []
            mock_conn.return_value = mock_db
            trace._get_opportunities_with_matrices()
            sql = mock_db.execute.call_args[0][0]
            assert f"LIMIT {trace._OPPS_WITH_MATRICES_LIMIT}" in sql

    @patch("tools.proposal_genesis.reflexes.trace.get_connection")
    def test_run_no_opportunities(self, mock_conn):
        from tools.proposal_genesis.reflexes.trace import run

        mock_db = MagicMock()
        mock_db.execute.return_value.fetchall.return_value = []
        mock_conn.return_value = mock_db
        result = run({}, None)
        assert result["success"] is True
        assert result["metric_value"] == 0.0
        assert result["details"]["opportunities_traced"] == 0
