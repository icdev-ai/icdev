#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for Industry Research Engine (Phase 63).

Covers: vertical_loader, session_manager, source_scanner, challenge_scorer,
regulatory_mapper, capability_mapper, build_buy_analyzer, dossier_generator,
trend_detector, research_engine.
"""

import hashlib
import importlib
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Direct-import helper: bypass the tools/ shim and import from tools/research/
# directly.  The icdev.tools.research namespace does not exist yet (Phase 63
# tooling lives under tools/research/ only), so we load each module by file
# path to avoid the deprecated tools/__init__.py redirect.
# ---------------------------------------------------------------------------
_BASE_DIR = Path(__file__).resolve().parent.parent
_RESEARCH_DIR = _BASE_DIR / "tools" / "research"

sys.path.insert(0, str(_BASE_DIR))


def _load_research_module(module_file: str):
    """Import a module from tools/research/ by filename, bypassing the shim."""
    name = f"tools.research.{Path(module_file).stem}"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name,
        str(_RESEARCH_DIR / module_file),
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# Pre-load all research modules before any cross-imports happen
_vertical_loader = _load_research_module("vertical_loader.py")
_session_manager = _load_research_module("session_manager.py")
_source_scanner = _load_research_module("source_scanner.py")
_challenge_scorer = _load_research_module("challenge_scorer.py")
_regulatory_mapper = _load_research_module("regulatory_mapper.py")
_capability_mapper = _load_research_module("capability_mapper.py")
_build_buy_analyzer = _load_research_module("build_buy_analyzer.py")
_dossier_generator = _load_research_module("dossier_generator.py")
_trend_detector = _load_research_module("trend_detector.py")
_research_engine = _load_research_module("research_engine.py")
_youtube_scanner = _load_research_module("youtube_scanner.py")
_forecast_generator = _load_research_module("forecast_generator.py")

# --- Vertical Loader ---
validate_vertical = _vertical_loader.validate_vertical
discover_verticals = _vertical_loader.discover_verticals
load_vertical_file = _vertical_loader.load_vertical_file
list_verticals = _vertical_loader.list_verticals
load_verticals_to_db = _vertical_loader.load_verticals_to_db
REQUIRED_FIELDS = _vertical_loader.REQUIRED_FIELDS
DEFAULT_VERTICALS_DIR = _vertical_loader.DEFAULT_VERTICALS_DIR

# --- Session Manager ---
create_session = _session_manager.create_session
get_session = _session_manager.get_session
list_sessions = _session_manager.list_sessions
update_session_status = _session_manager.update_session_status
advance_stage = _session_manager.advance_stage
PIPELINE_STAGES = _session_manager.PIPELINE_STAGES
VALID_STATUSES = _session_manager.VALID_STATUSES

# --- Source Scanner ---
SOURCE_SCANNERS = _source_scanner.SOURCE_SCANNERS
list_sources = _source_scanner.list_sources
store_signals = _source_scanner.store_signals

# --- Challenge Scorer ---
extract_keywords = _challenge_scorer.extract_keywords
classify_category = _challenge_scorer.classify_category
score_all_new = _challenge_scorer.score_all_new
cluster_signals = _challenge_scorer.cluster_signals
VALID_CATEGORIES = _challenge_scorer.VALID_CATEGORIES

# --- Regulatory Mapper ---
load_registry = _regulatory_mapper.load_registry
map_regulatory_signals = _regulatory_mapper.map_regulatory_signals
get_regulatory_landscape = _regulatory_mapper.get_regulatory_landscape
map_challenge_regulations = _regulatory_mapper.map_challenge_regulations
get_challenge_regulations = _regulatory_mapper.get_challenge_regulations
_match_body_to_signal = _regulatory_mapper._match_body_to_signal
_count_enforcement_actions = _regulatory_mapper._count_enforcement_actions
_detect_deadline = _regulatory_mapper._detect_deadline
_compute_crosswalk_coverage = _regulatory_mapper._compute_crosswalk_coverage
_determine_icdev_frameworks = _regulatory_mapper._determine_icdev_frameworks
REGISTRY_PATH = _regulatory_mapper.REGISTRY_PATH
ICDEV_FRAMEWORKS = _regulatory_mapper.ICDEV_FRAMEWORKS

# --- Capability Mapper ---
load_capability_catalog = _capability_mapper.load_capability_catalog
compute_coverage_score = _capability_mapper.compute_coverage_score
DEFAULT_CATALOG = _capability_mapper.DEFAULT_CATALOG

# --- Build/Buy Analyzer ---
analyze_all = _build_buy_analyzer.analyze_all

# --- Dossier Generator ---
generate_dossier = _dossier_generator.generate_dossier
list_dossiers = _dossier_generator.list_dossiers
get_dossier = _dossier_generator.get_dossier

# --- Trend Detector ---
detect_trends = _trend_detector.detect_trends
get_trend_report = _trend_detector.get_trend_report

# --- Research Engine ---
get_status = _research_engine.get_status
run_stage = _research_engine.run_stage
STAGE_FUNCTIONS = _research_engine.STAGE_FUNCTIONS

# --- YouTube Scanner ---
scan_videos = _youtube_scanner.scan_videos
_extract_video_id = _youtube_scanner._extract_video_id
_content_hash_yt = _youtube_scanner._content_hash

# --- Forecast Generator ---
generate_forecasts = _forecast_generator.generate_forecasts
get_forecasts = _forecast_generator.get_forecasts
_rank_predictions = _forecast_generator._rank_predictions
_score_surprise = _forecast_generator._score_surprise
_store_forecasts = _forecast_generator._store_forecasts
_deterministic_forecast = _forecast_generator._deterministic_forecast


# ---------------------------------------------------------------------------
# Research table DDL (extracted from tools/db/init_icdev_db.py)
# ---------------------------------------------------------------------------
RESEARCH_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS research_verticals (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    description TEXT,
    config_path TEXT NOT NULL,
    keywords TEXT NOT NULL DEFAULT '[]',
    regulatory_bodies TEXT DEFAULT '[]',
    academic_categories TEXT DEFAULT '[]',
    community_sources TEXT DEFAULT '[]',
    active INTEGER NOT NULL DEFAULT 1,
    session_count INTEGER DEFAULT 0,
    loaded_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_rvert_slug ON research_verticals(slug);
CREATE INDEX IF NOT EXISTS idx_rvert_active ON research_verticals(active);

CREATE TABLE IF NOT EXISTS research_sessions (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    vertical_id TEXT NOT NULL,
    vertical_name TEXT NOT NULL,
    description TEXT,
    focus_areas TEXT DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'created'
        CHECK(status IN ('created','scoping','scanning','synthesizing',
                         'dossier_ready','reviewed','child_app_triggered','archived')),
    pipeline_stage TEXT DEFAULT 'SCOPE'
        CHECK(pipeline_stage IN ('SCOPE','LANDSCAPE','REGULATE','COMMUNITY',
                                  'ACADEMIC','BUILD_BUY','SYNTHESIZE','FORECAST','DOSSIER')),
    signal_count INTEGER DEFAULT 0,
    challenge_count INTEGER DEFAULT 0,
    dossier_id TEXT,
    config_overrides TEXT DEFAULT '{}',
    created_by TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_rsess_vertical ON research_sessions(vertical_id);
CREATE INDEX IF NOT EXISTS idx_rsess_status ON research_sessions(status);
CREATE INDEX IF NOT EXISTS idx_rsess_stage ON research_sessions(pipeline_stage);

CREATE TABLE IF NOT EXISTS research_signals (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES research_sessions(id),
    source TEXT NOT NULL CHECK(source IN ('community_forum','review_site','academic_paper',
                                          'regulatory_body','open_source','saas_commercial',
                                          'news_blog','patent','video','manual')),
    source_type TEXT NOT NULL CHECK(source_type IN ('reddit','stackexchange','discord','forum',
                                                     'g2','capterra','trustpilot','domain_review',
                                                     'arxiv','ieee','acm','scholar',
                                                     'federal_register','regulations_gov','body_rss',
                                                     'github','awesome_list','package_registry',
                                                     'product_page','producthunt',
                                                     'news_article','analyst_report','blog',
                                                     'google_patent','uspto',
                                                     'youtube_search','youtube_manual','youtube_channel',
                                                     'manual_entry','scan_error')),
    title TEXT NOT NULL,
    body TEXT,
    url TEXT,
    author TEXT,
    upvotes INTEGER DEFAULT 0,
    citations INTEGER DEFAULT 0,
    sentiment TEXT CHECK(sentiment IS NULL OR sentiment IN ('positive','negative','neutral','mixed')),
    content_hash TEXT NOT NULL,
    keywords TEXT DEFAULT '[]',
    metadata TEXT DEFAULT '{}',
    discovered_at TEXT NOT NULL,
    classification TEXT DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_rsig_session ON research_signals(session_id);
CREATE INDEX IF NOT EXISTS idx_rsig_source ON research_signals(source);
CREATE INDEX IF NOT EXISTS idx_rsig_hash ON research_signals(content_hash);
CREATE INDEX IF NOT EXISTS idx_rsig_discovered ON research_signals(discovered_at);

CREATE TABLE IF NOT EXISTS research_challenges (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES research_sessions(id),
    title TEXT NOT NULL,
    description TEXT,
    category TEXT NOT NULL CHECK(category IN ('infrastructure','compliance','security','ux',
                                               'performance','integration','data','cost',
                                               'scalability','automation','governance','other')),
    signal_ids TEXT NOT NULL DEFAULT '[]',
    signal_count INTEGER NOT NULL DEFAULT 1,
    keyword_fingerprint TEXT NOT NULL,
    keywords TEXT NOT NULL DEFAULT '[]',
    composite_score REAL,
    score_breakdown TEXT DEFAULT '{}',
    market_demand REAL DEFAULT 0.0,
    regulatory_pressure REAL DEFAULT 0.0,
    technical_complexity REAL DEFAULT 0.0,
    competitive_saturation REAL DEFAULT 0.0,
    icdev_readiness REAL DEFAULT 0.0,
    compliance_alignment REAL DEFAULT 0.0,
    severity TEXT DEFAULT 'notable'
        CHECK(severity IN ('critical','notable','appendix')),
    status TEXT NOT NULL DEFAULT 'new'
        CHECK(status IN ('new','scored','mapped','dossier_included')),
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    metadata TEXT DEFAULT '{}',
    classification TEXT DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_rchal_session ON research_challenges(session_id);
CREATE INDEX IF NOT EXISTS idx_rchal_score ON research_challenges(composite_score);
CREATE INDEX IF NOT EXISTS idx_rchal_category ON research_challenges(category);
CREATE INDEX IF NOT EXISTS idx_rchal_fingerprint ON research_challenges(keyword_fingerprint);

CREATE TABLE IF NOT EXISTS research_regulatory_map (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES research_sessions(id),
    challenge_id TEXT REFERENCES research_challenges(id),
    regulatory_body TEXT NOT NULL,
    regulation_name TEXT NOT NULL,
    regulation_id TEXT,
    regulation_url TEXT,
    enforcement_actions INTEGER DEFAULT 0,
    deadline TEXT,
    nist_controls TEXT DEFAULT '[]',
    icdev_frameworks TEXT DEFAULT '[]',
    crosswalk_coverage REAL DEFAULT 0.0,
    gap_analysis TEXT DEFAULT '{}',
    metadata TEXT DEFAULT '{}',
    mapped_at TEXT NOT NULL DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_rregmap_session ON research_regulatory_map(session_id);
CREATE INDEX IF NOT EXISTS idx_rregmap_challenge ON research_regulatory_map(challenge_id);
CREATE INDEX IF NOT EXISTS idx_rregmap_body ON research_regulatory_map(regulatory_body);

CREATE TABLE IF NOT EXISTS research_build_buy (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES research_sessions(id),
    challenge_id TEXT NOT NULL REFERENCES research_challenges(id),
    recommendation TEXT NOT NULL CHECK(recommendation IN ('build','buy','partner','hybrid')),
    build_score REAL DEFAULT 0.0,
    buy_score REAL DEFAULT 0.0,
    partner_score REAL DEFAULT 0.0,
    build_rationale TEXT,
    buy_rationale TEXT,
    partner_rationale TEXT,
    existing_solutions TEXT DEFAULT '[]',
    icdev_capability_coverage REAL DEFAULT 0.0,
    estimated_effort TEXT CHECK(estimated_effort IS NULL OR estimated_effort IN ('S','M','L','XL')),
    estimated_cost_tier TEXT CHECK(estimated_cost_tier IS NULL OR estimated_cost_tier IN ('low','medium','high','very_high')),
    risk_level TEXT DEFAULT 'medium' CHECK(risk_level IN ('low','medium','high','critical')),
    score_breakdown TEXT DEFAULT '{}',
    metadata TEXT DEFAULT '{}',
    analyzed_at TEXT NOT NULL DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_rbb_session ON research_build_buy(session_id);
CREATE INDEX IF NOT EXISTS idx_rbb_challenge ON research_build_buy(challenge_id);
CREATE INDEX IF NOT EXISTS idx_rbb_recommendation ON research_build_buy(recommendation);

CREATE TABLE IF NOT EXISTS research_dossiers (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES research_sessions(id),
    vertical_id TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    executive_summary TEXT,
    signal_count INTEGER DEFAULT 0,
    challenge_count INTEGER DEFAULT 0,
    critical_challenges INTEGER DEFAULT 0,
    notable_challenges INTEGER DEFAULT 0,
    regulatory_mappings INTEGER DEFAULT 0,
    build_buy_analyses INTEGER DEFAULT 0,
    capability_coverage REAL DEFAULT 0.0,
    overall_opportunity_score REAL DEFAULT 0.0,
    status TEXT NOT NULL DEFAULT 'generated'
        CHECK(status IN ('generated','reviewed','approved','rejected','child_app_triggered')),
    reviewer TEXT,
    reviewed_at TEXT,
    review_notes TEXT,
    fitness_assessment_id TEXT,
    metadata TEXT DEFAULT '{}',
    generated_at TEXT NOT NULL DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_rdoss_session ON research_dossiers(session_id);
CREATE INDEX IF NOT EXISTS idx_rdoss_status ON research_dossiers(status);
CREATE INDEX IF NOT EXISTS idx_rdoss_score ON research_dossiers(overall_opportunity_score);

CREATE TABLE IF NOT EXISTS research_trends (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    vertical_ids TEXT NOT NULL DEFAULT '[]',
    session_ids TEXT NOT NULL DEFAULT '[]',
    challenge_ids TEXT NOT NULL DEFAULT '[]',
    keyword_fingerprint TEXT NOT NULL,
    keywords TEXT NOT NULL DEFAULT '[]',
    signal_count INTEGER NOT NULL DEFAULT 0,
    velocity REAL DEFAULT 0.0,
    acceleration REAL DEFAULT 0.0,
    status TEXT NOT NULL DEFAULT 'emerging'
        CHECK(status IN ('emerging','active','declining','stale')),
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    metadata TEXT DEFAULT '{}',
    detected_at TEXT NOT NULL DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_rtrend_status ON research_trends(status);
CREATE INDEX IF NOT EXISTS idx_rtrend_velocity ON research_trends(velocity);
CREATE INDEX IF NOT EXISTS idx_rtrend_fingerprint ON research_trends(keyword_fingerprint);

CREATE TABLE IF NOT EXISTS research_capability_map (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES research_sessions(id),
    challenge_id TEXT NOT NULL REFERENCES research_challenges(id),
    capability_id TEXT NOT NULL,
    capability_name TEXT NOT NULL,
    coverage_score REAL DEFAULT 0.0,
    keyword_overlap TEXT DEFAULT '[]',
    gap_description TEXT,
    enhancement_needed INTEGER DEFAULT 0,
    metadata TEXT DEFAULT '{}',
    mapped_at TEXT NOT NULL DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_rcapmap_session ON research_capability_map(session_id);
CREATE INDEX IF NOT EXISTS idx_rcapmap_challenge ON research_capability_map(challenge_id);
CREATE INDEX IF NOT EXISTS idx_rcapmap_capability ON research_capability_map(capability_id);

CREATE TABLE IF NOT EXISTS research_forecasts (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES research_sessions(id),
    trend_id TEXT REFERENCES research_trends(id),
    title TEXT NOT NULL,
    description TEXT,
    prediction_type TEXT NOT NULL CHECK(prediction_type IN (
        'trend_trajectory','greenfield','convergence','disruption','regulatory_shift')),
    confidence REAL NOT NULL DEFAULT 0.5,
    surprise_score REAL NOT NULL DEFAULT 0.5,
    composite_rank REAL NOT NULL DEFAULT 0.25,
    time_horizon TEXT NOT NULL DEFAULT '6mo' CHECK(time_horizon IN ('3mo','6mo','1yr','3yr')),
    supporting_evidence TEXT DEFAULT '[]',
    cross_engine_sources TEXT DEFAULT '[]',
    llm_model TEXT,
    llm_raw_response TEXT,
    outcome TEXT CHECK(outcome IS NULL OR outcome IN (
        'confirmed','partially_confirmed','not_confirmed','too_early')),
    outcome_date TEXT,
    outcome_notes TEXT,
    metadata TEXT DEFAULT '{}',
    generated_at TEXT NOT NULL DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_rfor_session ON research_forecasts(session_id);
CREATE INDEX IF NOT EXISTS idx_rfor_trend ON research_forecasts(trend_id);
CREATE INDEX IF NOT EXISTS idx_rfor_type ON research_forecasts(prediction_type);
CREATE INDEX IF NOT EXISTS idx_rfor_composite ON research_forecasts(composite_rank);
CREATE INDEX IF NOT EXISTS idx_rfor_generated ON research_forecasts(generated_at);
"""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def research_db(tmp_path):
    """Create a temporary SQLite DB with all research tables."""
    db_file = tmp_path / "test_research.db"
    conn = sqlite3.connect(str(db_file))
    conn.executescript(RESEARCH_TABLES_SQL)
    conn.close()
    return str(db_file)


def _seed_vertical(db_path, slug="healthcare", name="Healthcare", vert_id="rvert-test001"):
    """Insert a vertical row for testing sessions that need a vertical FK."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT INTO research_verticals
           (id, name, slug, description, config_path, keywords, active, loaded_at, updated_at)
           VALUES (?, ?, ?, 'Test vertical', '', '["health","ehr"]', 1,
                   datetime('now'), datetime('now'))""",
        (vert_id, name, slug),
    )
    conn.commit()
    conn.close()
    return vert_id


def _seed_session(db_path, slug="healthcare", session_name="Test Research"):
    """Insert a vertical + session, return session_id."""
    _seed_vertical(db_path, slug=slug)
    result = create_session(
        name=session_name,
        vertical_slug=slug,
        db_path=db_path,
    )
    return result["id"]


def _seed_signal(
    db_path,
    session_id,
    title="Latency issues in EHR",
    source="community_forum",
    source_type="reddit",
    body="Users report slow query times",
):
    """Insert a signal row directly for testing."""
    sig_id = f"rsig-test-{hashlib.sha256(title.encode()).hexdigest()[:8]}"
    content_hash = hashlib.sha256(f"{title}{body}".encode()).hexdigest()[:32]
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT INTO research_signals
           (id, session_id, source, source_type, title, body, content_hash,
            keywords, discovered_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, '["latency","ehr","query"]', datetime('now'))""",
        (sig_id, session_id, source, source_type, title, body, content_hash),
    )
    conn.commit()
    conn.close()
    return sig_id


def _seed_challenge(
    db_path, session_id, title="EHR Latency", category="performance", status="new", composite_score=None
):
    """Insert a challenge row directly for testing."""
    chal_id = f"rchal-test-{hashlib.sha256(title.encode()).hexdigest()[:8]}"
    kw = json.dumps(["latency", "ehr", "performance", "query"])
    fingerprint = hashlib.sha256(",".join(sorted(["latency", "ehr", "performance", "query"])).encode()).hexdigest()[:32]
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT INTO research_challenges
           (id, session_id, title, description, category, signal_ids, signal_count,
            keyword_fingerprint, keywords, composite_score, severity, status,
            first_seen, last_seen)
           VALUES (?, ?, ?, 'Test challenge', ?, '[]', 1, ?, ?, ?, 'notable', ?,
                   datetime('now'), datetime('now'))""",
        (chal_id, session_id, title, category, fingerprint, kw, composite_score, status),
    )
    conn.commit()
    conn.close()
    return chal_id


# ===========================================================================
# 1. Vertical Loader tests (~5)
# ===========================================================================
class TestVerticalLoader:
    def test_validate_vertical_valid(self):
        data = {
            "id": "rvert-hc",
            "name": "Healthcare",
            "slug": "healthcare",
            "description": "Healthcare vertical",
            "keywords": ["health", "ehr", "hipaa"],
        }
        is_valid, errors = validate_vertical(data)
        assert is_valid is True
        assert errors == []

    def test_validate_vertical_missing_fields(self):
        data = {"id": "rvert-bad", "name": "Bad"}
        is_valid, errors = validate_vertical(data)
        assert is_valid is False
        assert len(errors) >= 2  # Missing slug, description, keywords
        assert any("slug" in e for e in errors)
        assert any("keywords" in e for e in errors)

    def test_discover_verticals(self):
        """Check that context/research/verticals/ has JSON files for all verticals."""
        verticals, errs = discover_verticals()
        assert len(verticals) >= 6
        slugs = {v["slug"] for v in verticals}
        assert "healthcare" in slugs
        assert "trading" in slugs
        assert "defense" in slugs

    def test_load_vertical_file(self):
        """Load a known vertical file and verify structure."""
        hc_path = DEFAULT_VERTICALS_DIR / "healthcare.json"
        if not hc_path.exists():
            pytest.skip("healthcare.json not found in verticals dir")
        data, err = load_vertical_file(str(hc_path))
        assert err is None
        assert data is not None
        assert data["slug"] == "healthcare"
        assert isinstance(data["keywords"], list)
        assert "_config_path" in data  # Added by load_vertical_file

    def test_list_verticals_from_db(self, research_db):
        """Load verticals to DB then list them."""
        load_verticals_to_db(db_path=research_db)
        result = list_verticals(db_path=research_db)
        assert result["total"] >= 6
        slugs = {v["slug"] for v in result["verticals"]}
        assert "healthcare" in slugs


# ===========================================================================
# 2. Session Manager tests (~5)
# ===========================================================================
class TestSessionManager:
    def test_create_session(self, research_db):
        _seed_vertical(research_db, slug="trading", name="Trading")
        result = create_session(
            name="Trading Research",
            vertical_slug="trading",
            db_path=research_db,
        )
        assert "error" not in result
        assert result["id"].startswith("rsess-")
        assert result["status"] == "created"
        assert result["pipeline_stage"] == "SCOPE"

    def test_create_session_with_focus_areas(self, research_db):
        _seed_vertical(research_db, slug="defense", name="Defense")
        result = create_session(
            name="Defense Research",
            vertical_slug="defense",
            focus_areas=["cybersecurity", "supply-chain"],
            db_path=research_db,
        )
        assert "error" not in result
        focus = json.loads(result["focus_areas"])
        assert "cybersecurity" in focus
        assert "supply-chain" in focus

    def test_list_sessions(self, research_db):
        _seed_vertical(research_db, slug="fintech", name="Fintech")
        create_session(name="S1", vertical_slug="fintech", db_path=research_db)
        create_session(name="S2", vertical_slug="fintech", db_path=research_db)
        result = list_sessions(db_path=research_db)
        assert result["total"] == 2
        assert len(result["sessions"]) == 2

    def test_get_session(self, research_db):
        _seed_vertical(research_db, slug="logistics", name="Logistics")
        created = create_session(
            name="Logistics Research",
            vertical_slug="logistics",
            db_path=research_db,
        )
        fetched = get_session(created["id"], db_path=research_db)
        assert fetched["id"] == created["id"]
        assert fetched["name"] == "Logistics Research"

    def test_update_session_status(self, research_db):
        _seed_vertical(research_db, slug="cyber", name="Cybersecurity", vert_id="rvert-cyber")
        created = create_session(
            name="Cyber Session",
            vertical_slug="cyber",
            db_path=research_db,
        )
        updated = update_session_status(created["id"], "reviewed", db_path=research_db)
        assert updated["status"] == "reviewed"

    def test_update_session_status_invalid(self, research_db):
        _seed_vertical(research_db)
        created = create_session(name="X", vertical_slug="healthcare", db_path=research_db)
        result = update_session_status(created["id"], "nonexistent_status", db_path=research_db)
        assert "error" in result


# ===========================================================================
# 3. Source Scanner tests (~3)
# ===========================================================================
class TestSourceScanner:
    def test_list_sources(self):
        """list_sources enumerates exactly the registry, and nothing else.

        Asserted against SOURCE_SCANNERS rather than a literal count:
        list_sources() iterates that dict, so a hardcoded number restates a
        fact with one source of truth and goes stale the moment a scanner is
        registered — which is what happened when dic_collection and
        social_trends were added.
        """
        result = list_sources()
        assert {s["name"] for s in result["sources"]} == set(SOURCE_SCANNERS)
        assert result["total"] == len(SOURCE_SCANNERS)
        assert len(result["sources"]) == len(SOURCE_SCANNERS)

    def test_source_scanners_registry(self):
        """SOURCE_SCANNERS holds exactly these sources, all callable.

        The set is the guard; there is deliberately no separate length
        assertion. Set equality already pins the count exactly and, when it
        fails, names the scanner that appeared or vanished instead of only
        reporting that a number moved.
        """
        expected_keys = {
            "community_forum",
            "review_site",
            "academic_paper",
            "regulatory_body",
            "open_source",
            "saas_commercial",
            "news_blog",
            "patent",
            "video",
            # Lazy adapter shims added after the original nine (adapt-l30-02).
            "dic_collection",
            "social_trends",
        }
        assert set(SOURCE_SCANNERS.keys()) == expected_keys
        for fn in SOURCE_SCANNERS.values():
            assert callable(fn)

    def test_store_signals_dedup(self, research_db):
        """Duplicate content_hash within the same session should be rejected."""
        session_id = _seed_session(research_db)
        now = "2026-03-01T00:00:00Z"
        signals = [
            {
                "id": "rsig-a",
                "source": "community_forum",
                "source_type": "reddit",
                "title": "Signal A",
                "body": "Body A",
                "content_hash": "abc123",
                "keywords": "[]",
                "discovered_at": now,
            },
            {
                "id": "rsig-b",
                "source": "community_forum",
                "source_type": "reddit",
                "title": "Signal B — same hash",
                "body": "Body B",
                "content_hash": "abc123",  # duplicate hash
                "keywords": "[]",
                "discovered_at": now,
            },
        ]
        result = store_signals(signals, session_id, db_path=Path(research_db))
        assert result["stored"] == 1
        assert result["duplicates"] == 1


# ===========================================================================
# 4. Challenge Scorer tests (~5)
# ===========================================================================
class TestChallengeScorer:
    def test_extract_keywords(self):
        text = "The HIPAA compliance framework requires encryption and audit logging"
        kw = extract_keywords(text, top_n=5)
        assert isinstance(kw, list)
        assert len(kw) <= 5
        # Should contain meaningful words, not stopwords
        assert "the" not in kw
        assert "and" not in kw
        # Should contain domain terms
        assert "hipaa" in kw or "compliance" in kw or "encryption" in kw

    def test_extract_keywords_empty(self):
        assert extract_keywords("") == []
        assert extract_keywords(None) == []

    def test_classify_category(self):
        """Security-heavy text should classify as 'security'."""
        text = "encryption vulnerability breach firewall authentication"
        kw = ["encryption", "vulnerability", "breach"]
        cat = classify_category(text, kw)
        assert cat in VALID_CATEGORIES
        assert cat == "security"

    def test_keyword_fingerprint_deterministic(self):
        """Same keywords in any order produce the same fingerprint."""
        _keyword_fingerprint = _challenge_scorer._keyword_fingerprint
        fp1 = _keyword_fingerprint(["alpha", "beta", "gamma"])
        fp2 = _keyword_fingerprint(["gamma", "alpha", "beta"])
        assert fp1 == fp2
        assert len(fp1) == 32  # SHA-256 truncated to 32 hex chars

    def test_score_all_new_with_challenges(self, research_db):
        """score_all_new should score challenges with status='new'."""
        session_id = _seed_session(research_db)
        # Insert a few signals so dimension scorers have data
        _seed_signal(research_db, session_id, title="EHR slow queries", source="community_forum", source_type="reddit")
        _seed_signal(research_db, session_id, title="Performance bottleneck", source="review_site", source_type="g2")
        # Seed a challenge with status='new'
        _seed_challenge(research_db, session_id, title="EHR Performance", category="performance", status="new")
        result = score_all_new(session_id=session_id, db_path=research_db)
        assert "error" not in result
        assert result["scored"] >= 1

    def test_severity_thresholds(self, research_db):
        """Challenges with high scores should get 'critical' severity."""
        session_id = _seed_session(research_db)
        _seed_challenge(
            research_db,
            session_id,
            title="Critical infra challenge",
            category="infrastructure",
            status="new",
            composite_score=None,
        )
        # Insert many signals to boost score
        for i in range(15):
            _seed_signal(
                research_db,
                session_id,
                title=f"Infra issue #{i} infrastructure latency scaling",
                source="community_forum",
                source_type="reddit",
                body=f"Infrastructure problem #{i}",
            )
        result = score_all_new(session_id=session_id, db_path=research_db)
        # Verify scores exist; severity depends on composite
        assert result["scored"] >= 1
        if result.get("top_5"):
            for top in result["top_5"]:
                assert top["severity"] in ("critical", "notable", "appendix")


# ===========================================================================
# 5. Regulatory Mapper tests (~3)
# ===========================================================================
class TestRegulatoryMapper:
    def test_load_registry(self):
        """Registry file should load and contain expected regulatory bodies."""
        bodies = load_registry()
        assert isinstance(bodies, dict)
        if REGISTRY_PATH.exists():
            assert len(bodies) > 0
            # CFTC, SEC, NFA are expected from the file
            assert "CFTC" in bodies
            assert "SEC" in bodies

    def test_registry_has_expected_bodies(self):
        """Registry should include at least CFTC, NFA, SEC from the trading vertical."""
        bodies = load_registry()
        if not bodies:
            pytest.skip("regulatory_registry.json not found")
        expected = {"CFTC", "NFA", "SEC"}
        assert expected.issubset(set(bodies.keys()))

    def test_map_regulatory_signals(self, research_db):
        """map_regulatory_signals should return a result even with no regulatory signals."""
        session_id = _seed_session(research_db)
        result = map_regulatory_signals(session_id, db_path=research_db)
        assert isinstance(result, dict)
        assert "error" not in result
        assert result.get("mapped", 0) == 0  # No regulatory signals seeded

    def test_match_body_to_signal_exact(self):
        """Direct body key match in signal text should produce positive score."""
        body_def = {
            "name": "Commodity Futures Trading Commission",
            "key_regulations": ["CFTC Rule 17a-4"],
            "verticals": ["trading"],
        }
        score = _match_body_to_signal("CFTC", body_def, "CFTC enforcement action", "")
        assert score > 0.0

    def test_match_body_to_signal_no_match(self):
        """Completely unrelated text should produce zero score."""
        body_def = {
            "name": "Commodity Futures Trading Commission",
            "key_regulations": ["CFTC Rule 17a-4"],
            "verticals": ["trading"],
        }
        score = _match_body_to_signal("CFTC", body_def, "healthcare patient records", "hospital systems")
        assert score == 0.0

    def test_match_body_to_signal_empty_text(self):
        """Empty signal text should produce zero score."""
        body_def = {"name": "SEC", "key_regulations": [], "verticals": []}
        score = _match_body_to_signal("SEC", body_def, "", "")
        assert score == 0.0

    def test_count_enforcement_actions(self):
        """Enforcement keywords should be counted correctly."""
        text = "SEC enforcement action: penalty of $1M for violation of Rule 15c3-5"
        count = _count_enforcement_actions(text)
        assert count >= 3  # enforcement, penalty, violation

    def test_count_enforcement_actions_none(self):
        """Text without enforcement keywords should return 0."""
        count = _count_enforcement_actions("proposed rulemaking for new standards")
        assert count == 0

    def test_detect_deadline_iso_date(self):
        """Deadline detection should find ISO date formats."""
        text = "Compliance date: 2025-06-30 for all registered entities"
        deadline = _detect_deadline(text)
        assert deadline is not None
        assert "2025" in deadline

    def test_detect_deadline_natural_date(self):
        """Deadline detection should find natural date formats."""
        text = "Effective date June 30, 2025 for new requirements"
        deadline = _detect_deadline(text)
        assert deadline is not None

    def test_detect_deadline_none(self):
        """Text without deadline language should return None."""
        deadline = _detect_deadline("General discussion about market structure")
        assert deadline is None

    def test_compute_crosswalk_coverage_full(self):
        """Full coverage when all frameworks matched."""
        coverage = _compute_crosswalk_coverage(["AC-2", "AU-2"], list(ICDEV_FRAMEWORKS))
        assert coverage == 1.0

    def test_compute_crosswalk_coverage_partial(self):
        """Partial coverage with some frameworks matched."""
        coverage = _compute_crosswalk_coverage(["AC-2"], ["nist_800_53", "fedramp_moderate"])
        assert 0.0 < coverage < 1.0

    def test_compute_crosswalk_coverage_empty(self):
        """No NIST controls should return 0.0 coverage."""
        coverage = _compute_crosswalk_coverage([], ["nist_800_53"])
        assert coverage == 0.0

    def test_determine_icdev_frameworks_access_control(self):
        """AC-family controls should map to multiple frameworks."""
        frameworks = _determine_icdev_frameworks(["AC-2", "AC-3"])
        assert "nist_800_53" in frameworks
        assert "fedramp_moderate" in frameworks
        assert "nist_800_171" in frameworks

    def test_determine_icdev_frameworks_all(self):
        """'all' marker should return all ICDEV™ frameworks."""
        frameworks = _determine_icdev_frameworks(["all"])
        assert set(frameworks) == set(ICDEV_FRAMEWORKS)

    def test_determine_icdev_frameworks_empty(self):
        """Empty controls list should return empty frameworks."""
        frameworks = _determine_icdev_frameworks([])
        assert frameworks == []

    def test_map_regulatory_signals_with_signals(self, research_db):
        """Mapping with regulatory signals should produce mapped entries."""
        session_id = _seed_session(research_db)
        # Seed a regulatory signal
        _seed_signal(
            research_db,
            session_id,
            title="CFTC enforcement action on trading firm",
            source="regulatory_body",
            source_type="federal_register",
            body="Commodity Futures Trading Commission penalty for Rule 17a-4 violation",
        )
        result = map_regulatory_signals(session_id, db_path=research_db)
        assert isinstance(result, dict)
        assert "error" not in result
        # Should map at least one if registry exists
        if REGISTRY_PATH.exists():
            assert result.get("mapped", 0) >= 1

    def test_get_regulatory_landscape_empty(self, research_db):
        """Landscape for session with no mappings should return zeros."""
        session_id = _seed_session(research_db)
        result = get_regulatory_landscape(session_id, db_path=research_db)
        assert result["total_regulations"] == 0
        assert result["total_enforcement_actions"] == 0
        assert result["avg_crosswalk_coverage"] == 0.0
        assert result["bodies"] == []

    def test_get_regulatory_landscape_with_data(self, research_db):
        """Landscape should aggregate per-body stats after mapping."""
        session_id = _seed_session(research_db)
        _seed_signal(
            research_db,
            session_id,
            title="SEC proposed rule on market structure",
            source="regulatory_body",
            source_type="federal_register",
            body="Securities and Exchange Commission proposed rulemaking",
        )
        map_regulatory_signals(session_id, db_path=research_db)
        result = get_regulatory_landscape(session_id, db_path=research_db)
        assert isinstance(result, dict)
        # If registry matched, we should have bodies
        if result["total_regulations"] > 0:
            assert len(result["bodies"]) > 0
            body = result["bodies"][0]
            assert "regulatory_body" in body
            assert "regulation_count" in body
            assert "enforcement_actions" in body
            assert "avg_coverage" in body

    def test_get_challenge_regulations_empty(self, research_db):
        """Challenge with no linked regulations should return empty list."""
        session_id = _seed_session(research_db)
        chal_id = _seed_challenge(research_db, session_id)
        result = get_challenge_regulations(chal_id, db_path=research_db)
        assert isinstance(result, dict)
        assert result.get("regulations", []) == [] or len(result.get("regulations", [])) == 0

    def test_map_challenge_regulations(self, research_db):
        """map_challenge_regulations should link matching regulations to challenge."""
        session_id = _seed_session(research_db)
        # Seed a regulatory signal and map it
        _seed_signal(
            research_db,
            session_id,
            title="CFTC Rule 17a-4 compliance requirement",
            source="regulatory_body",
            source_type="federal_register",
            body="Commodity Futures Trading Commission trading regulation audit trail",
        )
        map_regulatory_signals(session_id, db_path=research_db)
        # Seed a challenge with overlapping keywords
        chal_id = _seed_challenge(research_db, session_id, title="Audit Trail Compliance", category="compliance")
        result = map_challenge_regulations(chal_id, session_id, db_path=research_db)
        assert isinstance(result, dict)
        assert "error" not in result


# ===========================================================================
# 6. Capability Mapper tests (~2)
# ===========================================================================
class TestCapabilityMapper:
    def test_load_capability_catalog(self):
        """Catalog should load with at least the DEFAULT_CATALOG entries."""
        catalog = load_capability_catalog()
        assert isinstance(catalog, list)
        assert len(catalog) >= len(DEFAULT_CATALOG)
        # Each entry should have id, name, keywords
        for cap in catalog:
            assert "id" in cap
            assert "name" in cap
            assert "keywords" in cap

    def test_compute_coverage_score(self):
        """Keyword overlap produces a score between 0 and 1."""
        challenge_kw = {"security", "encryption", "vulnerability", "firewall"}
        capability_kw = {"security", "encryption", "authentication", "vulnerability", "breach"}
        score = compute_coverage_score(challenge_kw, capability_kw)
        assert 0.0 <= score <= 1.0
        # 3 overlap out of 5 capability keywords -> 0.6
        assert score == pytest.approx(0.6, abs=0.01)


# ===========================================================================
# 7. Build/Buy Analyzer tests (~2)
# ===========================================================================
class TestBuildBuyAnalyzer:
    def test_recommendation_thresholds(self):
        """Verify the _determine_recommendation logic follows documented thresholds."""
        _determine_recommendation = _build_buy_analyzer._determine_recommendation
        # Clear build recommendation: build >> buy and partner
        rec = _determine_recommendation(0.85, 0.40, 0.30, {})
        assert rec == "build"
        # Hybrid: top two within 0.15
        rec2 = _determine_recommendation(0.70, 0.65, 0.30, {})
        assert rec2 == "hybrid"

    def test_analyze_all_empty_session(self, research_db):
        """analyze_all on session with no challenges returns empty result."""
        session_id = _seed_session(research_db)
        result = analyze_all(session_id, db_path=research_db)
        assert isinstance(result, dict)
        assert result.get("analyzed", 0) == 0


# ===========================================================================
# 8. Dossier Generator tests (~3)
# ===========================================================================
class TestDossierGenerator:
    def test_generate_dossier_missing_session(self, research_db):
        """Generating dossier for nonexistent session returns error."""
        result = generate_dossier("rsess-does-not-exist", db_path=research_db)
        assert "error" in result

    def test_generate_dossier_success(self, research_db):
        """Generate a dossier for a session with seeded challenges."""
        session_id = _seed_session(research_db)
        _seed_challenge(
            research_db, session_id, title="Challenge A", category="security", status="scored", composite_score=0.75
        )
        _seed_challenge(
            research_db, session_id, title="Challenge B", category="compliance", status="scored", composite_score=0.60
        )
        result = generate_dossier(session_id, db_path=research_db)
        assert "error" not in result
        assert "dossier_id" in result or "id" in result
        assert result.get("challenge_count", 0) >= 2 or result.get("challenges_included", 0) >= 0

    def test_list_dossiers(self, research_db):
        """list_dossiers should return all dossiers in the DB."""
        session_id = _seed_session(research_db)
        _seed_challenge(research_db, session_id, title="C1", category="data", status="scored", composite_score=0.5)
        generate_dossier(session_id, db_path=research_db)
        result = list_dossiers(db_path=research_db)
        assert isinstance(result, dict)
        assert result.get("total", 0) >= 1


# ===========================================================================
# 9. Trend Detector tests (~2)
# ===========================================================================
class TestTrendDetector:
    def test_detect_trends_empty_db(self, research_db):
        """detect_trends on empty DB returns result with 0 trends."""
        result = detect_trends(db_path=research_db)
        assert isinstance(result, dict)
        assert result.get("trends_detected", 0) == 0

    def test_get_trend_report(self, research_db):
        """get_trend_report returns a summary structure even when empty."""
        result = get_trend_report(db_path=research_db)
        assert isinstance(result, dict)
        # Report includes summary, by_status, by_vertical, generated_at
        assert "summary" in result or "total_trends" in result or "by_status" in result


# ===========================================================================
# 10. Research Engine tests (~2)
# ===========================================================================
class TestResearchEngine:
    def test_get_status(self, research_db):
        """get_status without session_id returns engine-wide status."""
        result = get_status(db_path=research_db)
        assert isinstance(result, dict)
        assert result.get("engine") == "research"
        assert result.get("status") in ("operational", "not_initialized")

    def test_run_stage_invalid(self, research_db):
        """run_stage with an invalid stage name returns an error."""
        session_id = _seed_session(research_db)
        result = run_stage(session_id, "NONEXISTENT_STAGE", db_path=research_db)
        assert "error" in result
        assert "Unknown stage" in result["error"]

    def test_stage_functions_includes_forecast(self):
        """STAGE_FUNCTIONS includes FORECAST stage (D-RES-17)."""
        assert "FORECAST" in STAGE_FUNCTIONS
        assert callable(STAGE_FUNCTIONS["FORECAST"])


# ===========================================================================
# 11. YouTube Scanner tests (~4)
# ===========================================================================
class TestYouTubeScanner:
    def test_extract_video_id_standard(self):
        """_extract_video_id parses standard YouTube URLs."""
        assert _extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_extract_video_id_short(self):
        """_extract_video_id parses youtu.be short URLs."""
        assert _extract_video_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_extract_video_id_invalid(self):
        """_extract_video_id returns None for non-YouTube URLs."""
        assert _extract_video_id("https://example.com/page") is None

    def test_scan_videos_disabled(self):
        """scan_videos returns empty list when video source is disabled."""
        config = {"sources": {"video": {"enabled": False}}}
        result = scan_videos(config)
        assert result == []

    def test_scan_videos_no_session_config(self):
        """scan_videos with enabled but no session_config returns empty."""
        config = {"sources": {"video": {"enabled": True, "platforms": []}}}
        result = scan_videos(config, session_config=None)
        assert isinstance(result, list)
        assert len(result) == 0

    def test_content_hash_deterministic(self):
        """_content_hash produces consistent results."""
        h1 = _content_hash_yt("youtube_test123")
        h2 = _content_hash_yt("youtube_test123")
        assert h1 == h2
        assert len(h1) == 16


# ===========================================================================
# 12. Forecast Generator tests (~4)
# ===========================================================================
class TestForecastGenerator:
    def test_score_surprise_base(self):
        """_score_surprise returns a value between 0 and 1."""
        pred = {"surprise_score": 0.5, "prediction_type": "greenfield"}
        score = _score_surprise(pred, [], [])
        assert 0.0 <= score <= 1.0

    def test_score_surprise_greenfield_boost(self):
        """Greenfield predictions get a surprise boost (D-RES-19)."""
        pred_green = {"surprise_score": 0.5, "prediction_type": "greenfield"}
        pred_trend = {"surprise_score": 0.5, "prediction_type": "trend_trajectory"}
        green_score = _score_surprise(pred_green, [], [])
        trend_score = _score_surprise(pred_trend, [], [])
        assert green_score > trend_score

    def test_rank_predictions_top_5(self):
        """_rank_predictions returns at most max_predictions items."""
        preds = [
            {
                "title": f"Pred {i}",
                "confidence": 0.7,
                "surprise_score": 0.5,
                "prediction_type": "greenfield",
                "time_horizon": "6mo",
            }
            for i in range(10)
        ]
        config = {"forecast": {"max_predictions": 5, "confidence_threshold": 0.3, "surprise_threshold": 0.2}}
        ranked = _rank_predictions(preds, config, [], [])
        assert len(ranked) <= 5

    def test_store_and_get_forecasts(self, research_db):
        """_store_forecasts stores and get_forecasts retrieves predictions."""
        session_id = _seed_session(research_db)
        predictions = [
            {
                "title": "Test prediction",
                "description": "A test forecast",
                "prediction_type": "greenfield",
                "confidence": 0.8,
                "surprise_score": 0.6,
                "composite_rank": 0.48,
                "time_horizon": "6mo",
                "supporting_evidence": [],
                "cross_engine_sources": [],
            }
        ]
        ids = _store_forecasts(session_id, predictions, "test-model", "{}", db_path=research_db)
        assert len(ids) == 1
        assert ids[0].startswith("rfor-")

        # Retrieve
        results = get_forecasts(session_id, db_path=research_db)
        assert len(results) == 1
        assert results[0]["title"] == "Test prediction"
        assert results[0]["prediction_type"] == "greenfield"
        assert float(results[0]["confidence"]) == 0.8

    def test_deterministic_forecast(self, research_db):
        """_deterministic_forecast generates basic trend extrapolation."""
        aggregated = {
            "session": {"vertical_name": "Test"},
            "signals": [],
            "trends": [
                {
                    "id": "rt-1",
                    "name": "AI Growth",
                    "velocity": 0.8,
                    "confidence": 0.7,
                    "signal_count": 15,
                    "keywords": "[]",
                }
            ],
            "challenges": [{"id": "rc-1", "title": "Data gaps", "composite_score": 0.85, "keywords": "[]"}],
            "innovation_trends": [],
            "innovation_signals": [],
            "creative_pain_points": [],
            "creative_feature_gaps": [],
        }
        config = {"forecast": {"max_predictions": 5}}
        preds = _deterministic_forecast(aggregated, config)
        assert len(preds) >= 1
        # Should have trend_trajectory from the trend
        types = [p["prediction_type"] for p in preds]
        assert "trend_trajectory" in types

    def test_generate_forecasts_no_data(self, research_db):
        """generate_forecasts with no signals returns skipped."""
        session_id = _seed_session(research_db)
        config = {
            "forecast": {
                "enabled": True,
                "method": "deterministic",
                "max_predictions": 5,
                "confidence_threshold": 0.3,
                "surprise_threshold": 0.2,
            }
        }
        result = generate_forecasts(session_id, db_path=research_db, config=config)
        assert isinstance(result, dict)
        assert result.get("skipped") is True or result.get("count", 0) == 0


# ===========================================================================
# 13. Pipeline Stage updates
# ===========================================================================
class TestPipelineUpdates:
    def test_pipeline_stages_include_forecast(self):
        """PIPELINE_STAGES has 9 stages including FORECAST (D-RES-17)."""
        assert len(PIPELINE_STAGES) == 9
        assert "FORECAST" in PIPELINE_STAGES
        # FORECAST should be between SYNTHESIZE and DOSSIER
        synth_idx = PIPELINE_STAGES.index("SYNTHESIZE")
        forecast_idx = PIPELINE_STAGES.index("FORECAST")
        dossier_idx = PIPELINE_STAGES.index("DOSSIER")
        assert forecast_idx == synth_idx + 1
        assert dossier_idx == forecast_idx + 1

    def test_source_scanners_include_video(self):
        """video is registered and callable (D-RES-14).

        The registry's full membership is pinned by
        TestSourceScanner::test_source_scanners_registry; this test is about
        video, so it no longer also asserts a total that made an unrelated
        scanner addition fail here.
        """
        assert "video" in SOURCE_SCANNERS
        assert callable(SOURCE_SCANNERS["video"])
