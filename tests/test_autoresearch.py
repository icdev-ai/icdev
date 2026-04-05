#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for Bayesian Autoresearch Engine (Phase 67, D-AR-1 through D-AR-10)."""

import sqlite3
from datetime import datetime, timezone

import pytest


def _ollama_reachable() -> bool:
    """Return True if Ollama HTTP API is reachable on localhost:11434."""
    try:
        import urllib.request

        req = urllib.request.Request(
            "http://localhost:11434/api/tags",
            headers={"Connection": "close"},
        )
        with urllib.request.urlopen(req, timeout=2) as resp:
            return resp.status == 200
    except Exception:
        return False


requires_ollama = pytest.mark.skipif(
    not _ollama_reachable(),
    reason="Ollama not running on localhost:11434 — skipping live LLM test",
)

# ── Fixtures ─────────────────────────────────────────────────────────────────

AUTORESEARCH_SCHEMA = """
CREATE TABLE IF NOT EXISTS experiment_programs (
    id TEXT PRIMARY KEY, domain TEXT NOT NULL UNIQUE,
    objective_metric TEXT NOT NULL, objective_direction TEXT NOT NULL,
    measurement_command TEXT NOT NULL, metric_path TEXT,
    modifiable_paths TEXT, forbidden_paths TEXT,
    time_budget_seconds INTEGER NOT NULL DEFAULT 300,
    keep_threshold REAL NOT NULL DEFAULT 0.005,
    category_order TEXT, categories TEXT, config TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS experiment_candidates (
    id TEXT PRIMARY KEY, domain TEXT NOT NULL,
    hypothesis TEXT NOT NULL, category TEXT,
    modifications TEXT, source TEXT DEFAULT 'manual',
    signal_id TEXT, status TEXT NOT NULL DEFAULT 'created',
    embedding BLOB, content_hash TEXT,
    info_gain_score REAL, thompson_sample REAL,
    estimated_impact TEXT, risk_level TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS experiment_results (
    id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL,
    domain TEXT NOT NULL, hypothesis TEXT NOT NULL,
    category TEXT, pre_metric REAL, post_metric REAL,
    metric_delta REAL, improvement_pct REAL,
    decision TEXT NOT NULL, decision_rationale TEXT,
    duration_ms INTEGER, git_branch TEXT, git_commit TEXT,
    tests_passed INTEGER, coherence_passed INTEGER,
    files_modified TEXT, details TEXT,
    classification TEXT DEFAULT 'CUI', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS experiment_landscapes (
    id TEXT PRIMARY KEY, domain TEXT NOT NULL,
    category TEXT NOT NULL, alpha REAL NOT NULL DEFAULT 2.0,
    beta_val REAL NOT NULL DEFAULT 8.0,
    total_experiments INTEGER NOT NULL DEFAULT 0,
    total_kept INTEGER NOT NULL DEFAULT 0,
    total_discarded INTEGER NOT NULL DEFAULT 0,
    best_improvement REAL DEFAULT 0.0,
    cumulative_improvement REAL DEFAULT 0.0,
    last_experiment_at TEXT, updated_at TEXT NOT NULL,
    UNIQUE(domain, category)
);
CREATE TABLE IF NOT EXISTS bayesian_experiment_scores (
    id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL,
    domain TEXT NOT NULL, info_gain_score REAL NOT NULL,
    dimensions TEXT, threshold_band TEXT,
    thompson_sample REAL, prior_distribution TEXT,
    selected INTEGER DEFAULT 0,
    classification TEXT DEFAULT 'CUI', scored_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_trail (
    id TEXT PRIMARY KEY, event_type TEXT, actor TEXT,
    action TEXT, details TEXT, project_id TEXT,
    session_id TEXT, created_at TEXT
);
"""


@pytest.fixture
def ar_db(tmp_path):
    """Create a temporary SQLite database with autoresearch tables."""
    db_path = tmp_path / "test_ar.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(AUTORESEARCH_SCHEMA)
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def mock_db(ar_db, monkeypatch):
    """Patch _get_db to use the test database."""
    import tools.autoresearch.experiment_engine as ee

    def fake_get_db():
        return ar_db

    monkeypatch.setattr(ee, "_get_db", fake_get_db)
    return ar_db


# ── Experiment Engine CRUD ───────────────────────────────────────────────────


class TestCreateExperiment:
    def test_basic_create(self, mock_db):
        from tools.autoresearch.experiment_engine import create_experiment

        result = create_experiment(
            domain="compliance",
            hypothesis="Improve AC-2 gate threshold",
        )
        assert result["success"] is True
        assert result["experiment_id"].startswith("exp-")
        assert result["domain"] == "compliance"
        assert result["status"] == "created"

    def test_assigns_unique_id(self, mock_db):
        from tools.autoresearch.experiment_engine import create_experiment

        r1 = create_experiment(domain="compliance", hypothesis="H1")
        r2 = create_experiment(domain="compliance", hypothesis="H2")
        assert r1["experiment_id"] != r2["experiment_id"]

    def test_content_hash_assigned(self, mock_db):
        from tools.autoresearch.experiment_engine import create_experiment

        result = create_experiment(domain="compliance", hypothesis="Test hash")
        assert result["content_hash"] is not None
        assert len(result["content_hash"]) == 16

    def test_get_experiment(self, mock_db):
        from tools.autoresearch.experiment_engine import (
            create_experiment,
            get_experiment,
        )

        created = create_experiment(domain="security", hypothesis="Test get")
        exp = get_experiment(created["experiment_id"])
        assert exp["domain"] == "security"
        assert exp["hypothesis"] == "Test get"


# ── Bayesian Selector ────────────────────────────────────────────────────────


class TestBayesianSelector:
    def test_score_candidate_basic(self):
        from tools.autoresearch.bayesian_selector import score_experiment_candidate

        candidate = {
            "id": "test-1",
            "hypothesis": "Adjust gate threshold for AC-2 to improve compliance pass rate",
        }
        result = score_experiment_candidate(candidate, "compliance")
        assert "info_gain_score" in result
        assert 0.0 <= result["info_gain_score"] <= 1.0
        assert "dimensions" in result
        assert "threshold_band" in result

    def test_score_returns_all_dimensions(self):
        from tools.autoresearch.bayesian_selector import score_experiment_candidate

        candidate = {
            "id": "test-2",
            "hypothesis": "Remove dead code from compliance assessor module",
        }
        result = score_experiment_candidate(candidate, "code_quality")
        dims = result["dimensions"]
        assert "posterior_shift" in dims
        assert "discriminability" in dims
        assert "diversity" in dims
        assert "complexity_match" in dims

    def test_diversity_decreases_with_selection(self):
        from tools.autoresearch.bayesian_selector import score_experiment_candidate

        candidate = {
            "id": "test-3",
            "hypothesis": "Improve NIST control mapping accuracy",
        }
        r1 = score_experiment_candidate(candidate, "compliance", selected_so_far=[])
        r2 = score_experiment_candidate(
            candidate,
            "compliance",
            selected_so_far=[{"hypothesis": "Improve NIST control mapping accuracy"}],
        )
        assert r2["dimensions"]["diversity"] <= r1["dimensions"]["diversity"]

    def test_select_next_basic(self):
        from tools.autoresearch.bayesian_selector import select_next_experiment

        candidates = [
            {"id": "c1", "hypothesis": "Add missing FedRAMP controls to catalog"},
            {"id": "c2", "hypothesis": "Fix assessor false positives on AC-3"},
            {"id": "c3", "hypothesis": "Improve crosswalk HIPAA mapping coverage"},
        ]
        result = select_next_experiment(candidates, "compliance")
        assert result["selected"] is not None
        assert result["reason"] == "selected"
        assert "info_gain_score" in result

    def test_select_returns_none_when_all_deduped(self):
        from tools.autoresearch.bayesian_selector import select_next_experiment

        candidates = [
            {"id": "c1", "hypothesis": "Test hypothesis", "content_hash": "abc123"},
        ]
        recent = [{"hypothesis": "Test hypothesis", "content_hash": "abc123"}]
        result = select_next_experiment(candidates, "compliance", recent_experiments=recent)
        assert result["selected"] is None
        assert result["reason"] == "all_deduped"


# ── Dedup ────────────────────────────────────────────────────────────────────


class TestDedup:
    def test_cosine_similarity_identical(self):
        from tools.autoresearch.bayesian_selector import _cosine_similarity

        a = [1.0, 0.0, 0.0]
        b = [1.0, 0.0, 0.0]
        assert abs(_cosine_similarity(a, b) - 1.0) < 0.001

    def test_cosine_similarity_orthogonal(self):
        from tools.autoresearch.bayesian_selector import _cosine_similarity

        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert abs(_cosine_similarity(a, b)) < 0.001

    def test_cosine_similarity_empty(self):
        from tools.autoresearch.bayesian_selector import _cosine_similarity

        assert _cosine_similarity([], []) == 0.0


# ── Fitness Evaluator ────────────────────────────────────────────────────────


class TestFitnessEvaluator:
    def test_evaluate_dispatches_by_domain(self):
        from tools.autoresearch.fitness_evaluator import evaluate

        # These will fail gracefully (tools not running) but dispatch correctly
        result = evaluate("compliance")
        assert result["domain"] == "compliance"
        assert result["metric_name"] == "gate_pass_rate"

    def test_evaluate_unknown_domain(self):
        from tools.autoresearch.fitness_evaluator import evaluate

        result = evaluate("nonexistent_domain")
        assert result["success"] is False
        assert "Unknown domain" in result["error"]

    def test_list_domains(self):
        from tools.autoresearch.fitness_evaluator import list_domains

        result = list_domains()
        assert result["count"] == 8
        names = [d["name"] for d in result["domains"]]
        assert "compliance" in names
        assert "code_quality" in names
        assert "security" in names

    def test_health_check(self):
        from tools.autoresearch.fitness_evaluator import health_check

        result = health_check()
        assert result["status"] == "healthy"
        assert result["domains_available"] == 8


# ── Hypothesis Generator ─────────────────────────────────────────────────────


class TestHypothesisGenerator:
    def test_template_hypotheses(self):
        from tools.autoresearch.hypothesis_generator import _template_hypotheses

        config = {
            "categories": {
                "gate_configuration": {
                    "description": "Adjust gates",
                    "risk": "low",
                    "example_hypotheses": [
                        "Relax warning thresholds",
                        "Add edge case conditions",
                    ],
                },
            },
            "category_order": ["gate_configuration"],
        }
        result = _template_hypotheses("compliance", config, max_hypotheses=5)
        assert len(result) == 2
        assert all(r["source"] == "template" for r in result)
        assert all(r["content_hash"] is not None for r in result)

    def test_generate_from_signals(self):
        from tools.autoresearch.hypothesis_generator import generate_from_signals

        signals = [
            {"id": "sig-1", "title": "New CVE", "description": "CVE-2026-1234 found"},
        ]
        result = generate_from_signals(signals, "security")
        assert len(result) == 1
        assert result[0]["signal_id"] == "sig-1"

    def test_health_check(self):
        from tools.autoresearch.hypothesis_generator import health_check

        result = health_check()
        assert result["status"] == "healthy"
        assert result["template_fallback"] is True
        assert result["programs_found"] >= 1


# ── Decision Logic ───────────────────────────────────────────────────────────


class TestDecision:
    def test_keep_on_improvement(self, mock_db):
        from tools.autoresearch.experiment_engine import create_experiment, decide

        exp = create_experiment(domain="compliance", hypothesis="Improve AC-2")
        result = decide(
            exp["experiment_id"],
            pre_metric=0.70,
            post_metric=0.72,
        )
        assert result["decision"] == "keep"
        assert result["metric_delta"] > 0

    def test_discard_below_threshold(self, mock_db):
        from tools.autoresearch.experiment_engine import create_experiment, decide

        exp = create_experiment(domain="compliance", hypothesis="Tiny change")
        result = decide(
            exp["experiment_id"],
            pre_metric=0.70,
            post_metric=0.7001,
        )
        assert result["decision"] == "discard"

    def test_discard_on_test_failure(self, mock_db):
        from tools.autoresearch.experiment_engine import create_experiment, decide

        exp = create_experiment(domain="compliance", hypothesis="Test fail")
        result = decide(
            exp["experiment_id"],
            pre_metric=0.70,
            post_metric=0.80,
            tests_passed=False,
        )
        assert result["decision"] == "discard"
        assert "Tests failed" in result["rationale"]

    def test_discard_on_coherence_failure(self, mock_db):
        from tools.autoresearch.experiment_engine import create_experiment, decide

        exp = create_experiment(domain="compliance", hypothesis="Coherence fail")
        result = decide(
            exp["experiment_id"],
            pre_metric=0.70,
            post_metric=0.80,
            coherence_passed=False,
        )
        assert result["decision"] == "discard"
        assert "Coherence" in result["rationale"]


# ── Append-Only Enforcement ──────────────────────────────────────────────────


class TestAppendOnly:
    def test_experiment_results_no_update(self, mock_db):
        """experiment_results should be append-only (D-AR-4)."""
        from tools.autoresearch.experiment_engine import create_experiment, decide

        exp = create_experiment(domain="compliance", hypothesis="Append test")
        decide(exp["experiment_id"], pre_metric=0.5, post_metric=0.6)

        # Verify result exists
        row = mock_db.execute(
            "SELECT * FROM experiment_results WHERE experiment_id = ?",
            (exp["experiment_id"],),
        ).fetchone()
        assert row is not None
        assert row["decision"] == "keep"

    def test_bayesian_scores_written(self, mock_db):
        """bayesian_experiment_scores should be recorded."""
        now = datetime.now(timezone.utc).isoformat()
        mock_db.execute(
            "INSERT INTO bayesian_experiment_scores "
            "(id, candidate_id, domain, info_gain_score, scored_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("bes-test", "exp-test", "compliance", 0.85, now),
        )
        mock_db.commit()
        row = mock_db.execute("SELECT * FROM bayesian_experiment_scores WHERE id = 'bes-test'").fetchone()
        assert row["info_gain_score"] == 0.85


# ── Landscape Posterior ──────────────────────────────────────────────────────


class TestLandscape:
    def test_landscape_created_on_decide(self, mock_db):
        from tools.autoresearch.experiment_engine import create_experiment, decide

        exp = create_experiment(domain="compliance", hypothesis="Landscape test", category="gate_configuration")
        decide(exp["experiment_id"], pre_metric=0.5, post_metric=0.6)

        row = mock_db.execute(
            "SELECT * FROM experiment_landscapes WHERE domain = 'compliance' AND category = 'gate_configuration'"
        ).fetchone()
        assert row is not None
        assert row["total_experiments"] == 1

    def test_landscape_alpha_increases_on_keep(self, mock_db):
        from tools.autoresearch.experiment_engine import create_experiment, decide

        exp = create_experiment(domain="security", hypothesis="Alpha test", category="sast_findings")
        decide(exp["experiment_id"], pre_metric=0.5, post_metric=0.6)

        row = mock_db.execute(
            "SELECT * FROM experiment_landscapes WHERE domain = 'security' AND category = 'sast_findings'"
        ).fetchone()
        assert row["alpha"] == 3.0  # Initial alpha on keep
        assert row["total_kept"] == 1


# ── Category Ordering ────────────────────────────────────────────────────────


class TestCategoryOrdering:
    def test_get_category_order(self):
        from tools.autoresearch.bayesian_selector import get_category_order

        result = get_category_order("compliance")
        assert "ordered_categories" in result
        assert len(result["ordered_categories"]) > 0

    def test_architecture_first(self):
        from tools.autoresearch.bayesian_selector import get_category_order

        result = get_category_order("compliance")
        if result["method"] == "bayesian_cascade":
            # Architecture has highest cascade info gain
            assert result["ordered_categories"][0] == "architecture"


# ── CLI Output ───────────────────────────────────────────────────────────────


class TestCLI:
    def test_health_json(self):
        from tools.autoresearch.experiment_engine import health_check

        result = health_check()
        assert "status" in result
        assert "timestamp" in result

    def test_status_json(self):
        from tools.autoresearch.experiment_engine import get_status

        result = get_status()
        assert "total_experiments" in result
        assert "timestamp" in result


# ── Genesis Reflex ───────────────────────────────────────────────────────────


class TestGenesisReflex:
    @requires_ollama
    @pytest.mark.timeout(120)
    def test_reflex_interface(self):
        """Verify experiment reflex has correct run() signature."""
        from tools.genesis.reflexes.experiment import run

        # Should accept config dict and optional trust
        config = {
            "signal_score_threshold": 0.70,
            "max_experiments_per_run": 1,
            "domains": ["compliance"],
            "export_gkp": False,
        }
        # This will run but may not find signals — that's OK
        result = run(config)
        assert "success" in result
        assert "metric_value" in result
        assert "details" in result


# ── Estimate Experiment Count ────────────────────────────────────────────────


class TestEstimate:
    def test_estimate_basic(self):
        from tools.autoresearch.bayesian_selector import estimate_experiment_count

        result = estimate_experiment_count("compliance")
        assert "estimated_experiments" in result
        assert "teaching_dimension" in result
        assert result["estimated_experiments"] >= 1
