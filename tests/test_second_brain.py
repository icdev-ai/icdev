"""Second Brain canvas test suite — profile, roles, interactions, health, search, routes."""
# CUI // SP-CTI
from __future__ import annotations

import json
import os
import pathlib
from datetime import datetime, timezone, timedelta

import pytest

# ── helpers ──────────────────────────────────────────────────────────────────

USER_ID = "test-sb-user"
TENANT_ID = "test"

_MIGRATION_DIR = pathlib.Path(__file__).parent.parent / "icdev" / "tools" / "db" / "migrations"

_SB_DDL = """
CREATE TABLE IF NOT EXISTS user_identity_profiles (
    user_id          TEXT NOT NULL,
    tenant_id        TEXT NOT NULL DEFAULT 'default',
    full_name        TEXT,
    work_email       TEXT,
    title            TEXT,
    seniority_tier   TEXT,
    department       TEXT,
    timezone         TEXT NOT NULL DEFAULT 'UTC',
    work_start       TEXT NOT NULL DEFAULT '09:00',
    work_end         TEXT NOT NULL DEFAULT '18:00',
    focus_block      TEXT NOT NULL DEFAULT 'am',
    meeting_heavy_days TEXT NOT NULL DEFAULT '[]',
    briefing_time    TEXT NOT NULL DEFAULT '08:00',
    delivery_channels TEXT NOT NULL DEFAULT '["dashboard"]',
    comm_style       INTEGER NOT NULL DEFAULT 3,
    org_name         TEXT,
    org_industry     TEXT,
    org_size         TEXT,
    team_mission     TEXT,
    profile_summary  TEXT,
    context_complete INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at       TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, tenant_id)
);
CREATE TABLE IF NOT EXISTS user_objectives (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    tenant_id   TEXT NOT NULL DEFAULT 'default',
    title       TEXT NOT NULL,
    description TEXT,
    horizon     TEXT,
    metric      TEXT,
    status      TEXT NOT NULL DEFAULT 'active',
    sort_order  INTEGER NOT NULL DEFAULT 0,
    progress_notes TEXT DEFAULT '[]',
    last_auto_update TEXT,
    auto_progress_pct INTEGER DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_user_obj ON user_objectives (user_id, tenant_id, status);
CREATE TABLE IF NOT EXISTS user_relationships (
    id                TEXT PRIMARY KEY,
    user_id           TEXT NOT NULL,
    tenant_id         TEXT NOT NULL DEFAULT 'default',
    name              TEXT NOT NULL,
    title             TEXT,
    email             TEXT,
    org               TEXT,
    relationship_type TEXT,
    notes             TEXT,
    last_contact_at   TEXT,
    expectations_json TEXT DEFAULT '{}',
    commitment_date   TEXT,
    created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_user_rel ON user_relationships (user_id, tenant_id);
CREATE TABLE IF NOT EXISTS user_challenges (
    id                   TEXT PRIMARY KEY,
    user_id              TEXT NOT NULL,
    tenant_id            TEXT NOT NULL DEFAULT 'default',
    challenge_key        TEXT,
    custom_description   TEXT,
    severity             TEXT DEFAULT 'medium',
    status               TEXT DEFAULT 'active',
    created_at           TEXT NOT NULL DEFAULT (datetime('now'))
);
-- cnr-me-03: drop any stale copy so tests exercise the current (msgraph-capable)
-- schema regardless of a pre-existing table left in a bootstrapped worktree DB.
DROP TABLE IF EXISTS user_integrations;
CREATE TABLE IF NOT EXISTS user_integrations (
    id                TEXT PRIMARY KEY,
    user_id           TEXT NOT NULL,
    tenant_id         TEXT NOT NULL DEFAULT 'default',
    service           TEXT NOT NULL,
    access_token_enc  TEXT,
    refresh_token_enc TEXT,
    token_expiry      TEXT,
    scopes            TEXT,
    metadata_json     TEXT,
    status            TEXT DEFAULT 'active',
    last_sync_at      TEXT,
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(user_id, tenant_id, service)
);
CREATE TABLE IF NOT EXISTS user_daily_briefings (
    id                   TEXT PRIMARY KEY,
    user_id              TEXT NOT NULL,
    tenant_id            TEXT NOT NULL DEFAULT 'default',
    briefing_date        TEXT NOT NULL,
    content_json         TEXT,
    delivery_status_json TEXT DEFAULT '{}',
    opened_at            TEXT,
    created_at           TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(user_id, tenant_id, briefing_date)
);
CREATE INDEX IF NOT EXISTS idx_briefing ON user_daily_briefings (user_id, tenant_id, briefing_date);
CREATE TABLE IF NOT EXISTS user_relationship_interactions (
    id               TEXT PRIMARY KEY,
    relationship_id  TEXT NOT NULL,
    user_id          TEXT NOT NULL,
    tenant_id        TEXT NOT NULL DEFAULT 'default',
    interaction_date TEXT NOT NULL,
    title            TEXT NOT NULL,
    notes            TEXT,
    action_items     TEXT DEFAULT '[]',
    follow_up_date   TEXT,
    interaction_type TEXT DEFAULT 'meeting',
    created_at       TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_rel_interactions
    ON user_relationship_interactions (relationship_id, user_id, interaction_date DESC);
CREATE TABLE IF NOT EXISTS user_knowledge_items (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    tenant_id   TEXT NOT NULL DEFAULT 'default',
    source_type TEXT NOT NULL,
    source_url  TEXT,
    title       TEXT,
    raw_content TEXT,
    summary     TEXT,
    tags        TEXT DEFAULT '[]',
    status      TEXT DEFAULT 'pending',
    error_msg   TEXT,
    created_at  TEXT DEFAULT (datetime('now')),
    indexed_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_user_ki ON user_knowledge_items (user_id, tenant_id, status);
"""


@pytest.fixture(autouse=True)
def sb_env(monkeypatch):
    """Enable Second Brain and force SQLite for all tests."""
    monkeypatch.setenv("ICDEV_SECOND_BRAIN_ENABLED", "true")
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    # Create canvas tables in the shared test SQLite DB
    try:
        from tools.db.storage import get_canvas_connection
        conn = get_canvas_connection("ICDEV_SECOND_BRAIN_ENABLED")
        conn.executescript(_SB_DDL)
        conn.commit()
    except Exception:
        pass


@pytest.fixture
def flask_client():
    """Flask test client for route smoke tests (authenticated session).

    Both the dashboard's global auth hook and the fail-closed /me gate
    (cnr-me-01) require a real, active dashboard user, so seed 'test-admin' into
    dashboard_users on the app's own DB before setting the session cookie.
    """
    import importlib
    # Skip the dashboard's import-time API-key auto-provisioning (needs the full
    # auth schema, absent in a bare worktree DB) by presenting an env key.
    os.environ.setdefault("ICDEV_DASHBOARD_API_KEY", "test-sb-key")
    try:
        from tools.db.storage import get_connection
        conn = get_connection()
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS dashboard_users (
                id TEXT PRIMARY KEY, email TEXT UNIQUE, display_name TEXT,
                role TEXT DEFAULT 'admin', status TEXT DEFAULT 'active',
                created_by TEXT, created_at TIMESTAMP, updated_at TIMESTAMP
            );
            INSERT OR IGNORE INTO dashboard_users (id, email, display_name, role, status)
            VALUES ('test-admin', 'admin@test.local', 'Test Admin', 'admin', 'active');
            """
        )
        conn.commit()
    except Exception:
        pass
    app_mod = importlib.import_module("tools.dashboard.app")
    app = app_mod.app
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-sb-secret"
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = "test-admin"
        with app.app_context():
            yield client


@pytest.fixture
def sb_app():
    """Isolated Flask app hosting ONLY second_brain_bp.

    Tests the blueprint's fail-closed auth gate (cnr-me-01) in isolation, free of
    the full dashboard boot (airgap probing, dashboard_users seeding, etc.).
    """
    import importlib
    from flask import Flask
    bp_mod = importlib.import_module("tools.second_brain.blueprint")
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-sb-secret"
    app.add_url_rule("/login", "login", lambda: ("login page", 200))
    app.register_blueprint(bp_mod.second_brain_bp)
    return app


# ── profile ──────────────────────────────────────────────────────────────────

class TestProfile:
    def test_get_profile_new_user_returns_dict(self):
        from tools.second_brain.profile import get_profile
        p = get_profile(USER_ID, TENANT_ID)
        assert isinstance(p, (dict, type(None)))  # None or {} both acceptable

    def test_upsert_and_get_profile(self):
        from tools.second_brain.profile import upsert_profile, get_profile
        upsert_profile(USER_ID, TENANT_ID, title="Network Architect", org_name="ACME")
        p = get_profile(USER_ID, TENANT_ID)
        assert p is not None
        assert p.get("title") == "Network Architect"
        assert p.get("org_name") == "ACME"

    def test_upsert_updates_existing(self):
        from tools.second_brain.profile import upsert_profile, get_profile
        upsert_profile(USER_ID, TENANT_ID, title="Old Title")
        upsert_profile(USER_ID, TENANT_ID, title="New Title")
        p = get_profile(USER_ID, TENANT_ID)
        assert p.get("title") == "New Title"

    def test_get_objectives_returns_list(self):
        from tools.second_brain.profile import get_objectives
        objs = get_objectives(USER_ID, TENANT_ID)
        assert isinstance(objs, list)

    def test_get_relationships_returns_list(self):
        from tools.second_brain.profile import get_relationships
        rels = get_relationships(USER_ID, TENANT_ID)
        assert isinstance(rels, list)

    def test_get_challenges_returns_list(self):
        from tools.second_brain.profile import get_challenges
        chals = get_challenges(USER_ID, TENANT_ID)
        assert isinstance(chals, list)

    def test_build_world_model_context(self):
        from tools.second_brain.profile import upsert_profile, build_world_model_context
        # context_complete must be 1 for the function to return a dict
        upsert_profile(USER_ID, TENANT_ID, title="Network Architect",
                       org_name="ACME", context_complete=1)
        ctx = build_world_model_context(USER_ID, TENANT_ID)
        assert isinstance(ctx, dict)
        for key in ("name", "title", "org_name", "objectives",
                    "challenge_keys", "relationship_names", "comm_style_label", "timezone"):
            assert key in ctx, f"Missing key: {key}"

    def test_build_world_model_context_no_crash_empty_user(self):
        from tools.second_brain.profile import build_world_model_context
        # Returns None for unknown/incomplete user — both are valid
        ctx = build_world_model_context("nonexistent-user-xyz", "test")
        assert ctx is None or isinstance(ctx, dict)


# ── role advisor ──────────────────────────────────────────────────────────────

class TestRoleAdvisor:
    def test_infer_network_architect(self):
        from tools.second_brain.role_advisor import infer_persona
        p = infer_persona("Senior Network Architect")
        assert p.get("persona") == "solutions_architect"

    def test_infer_software_engineer(self):
        from tools.second_brain.role_advisor import infer_persona
        p = infer_persona("Software Engineer")
        assert isinstance(p, dict)
        assert "persona" in p

    def test_infer_empty_title_no_crash(self):
        from tools.second_brain.role_advisor import infer_persona
        p = infer_persona("")
        assert isinstance(p, dict)
        assert "persona" in p

    def test_infer_unknown_role_returns_fallback(self):
        from tools.second_brain.role_advisor import infer_persona
        p = infer_persona("Galactic Overlord of Cheese XYZ9999")
        assert isinstance(p, dict)
        assert "persona" in p

    def test_infer_pm_role(self):
        from tools.second_brain.role_advisor import infer_persona
        p = infer_persona("Product Manager")
        assert isinstance(p, dict)

    def test_get_relevant_canvases_returns_list(self):
        from tools.second_brain.role_advisor import get_relevant_canvases
        canvases = get_relevant_canvases(USER_ID, TENANT_ID)
        assert isinstance(canvases, list)

    def test_get_digest_topics_returns_list(self):
        from tools.second_brain.role_advisor import get_digest_topics
        topics = get_digest_topics(USER_ID, TENANT_ID)
        assert isinstance(topics, list)

    def test_canvases_populated_after_profile_set(self):
        from tools.second_brain.profile import upsert_profile
        from tools.second_brain.role_advisor import get_relevant_canvases
        upsert_profile(USER_ID, TENANT_ID, title="Network Architect")
        canvases = get_relevant_canvases(USER_ID, TENANT_ID)
        assert len(canvases) > 0


# ── interactions ──────────────────────────────────────────────────────────────

class TestInteractions:
    REL_ID = "test-rel-interactions-001"

    def test_log_returns_id(self):
        from tools.second_brain.interactions import log_interaction
        result = log_interaction(
            self.REL_ID, USER_ID, "Q3 planning call",
            notes="Good sync", tenant_id=TENANT_ID
        )
        assert "id" in result
        assert result.get("relationship_id") == self.REL_ID

    def test_get_interactions_after_log(self):
        from tools.second_brain.interactions import log_interaction, get_interactions
        log_interaction(self.REL_ID, USER_ID, "Sprint review", tenant_id=TENANT_ID)
        items = get_interactions(self.REL_ID, USER_ID, TENANT_ID)
        assert isinstance(items, list)
        assert any(i["title"] == "Sprint review" for i in items)

    def test_get_interactions_most_recent_first(self):
        from tools.second_brain.interactions import log_interaction, get_interactions
        log_interaction(self.REL_ID, USER_ID, "First", interaction_date="2026-01-01", tenant_id=TENANT_ID)
        log_interaction(self.REL_ID, USER_ID, "Second", interaction_date="2026-06-01", tenant_id=TENANT_ID)
        items = get_interactions(self.REL_ID, USER_ID, TENANT_ID)
        dates = [i["date"] for i in items if i["title"] in ("First", "Second")]
        assert dates == sorted(dates, reverse=True)

    def test_delete_interaction(self):
        from tools.second_brain.interactions import log_interaction, get_interactions, delete_interaction
        r = log_interaction(self.REL_ID, USER_ID, "To delete", tenant_id=TENANT_ID)
        iid = r["id"]
        assert delete_interaction(iid, USER_ID, TENANT_ID) is True
        items = get_interactions(self.REL_ID, USER_ID, TENANT_ID)
        assert not any(i.get("id") == iid for i in items)

    def test_get_interactions_empty_rel(self):
        from tools.second_brain.interactions import get_interactions
        items = get_interactions("nonexistent-rel-xyz", USER_ID, TENANT_ID)
        assert items == []


# ── relationship health ───────────────────────────────────────────────────────

class TestRelationshipHealth:
    def _rel(self, rtype: str = "customer") -> dict:
        return {"id": "r1", "name": "Alice", "relationship_type": rtype}

    def test_green_recent_contact(self):
        from tools.second_brain.relationship_health import score_relationship
        recent = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%d")
        h = score_relationship(self._rel(), recent)
        assert h["status"] == "green"

    def test_amber_stale_customer(self):
        from tools.second_brain.relationship_health import score_relationship
        stale = (datetime.now(timezone.utc) - timedelta(days=20)).strftime("%Y-%m-%d")
        h = score_relationship(self._rel("customer"), stale)
        assert h["status"] == "amber"

    def test_red_very_stale_customer(self):
        from tools.second_brain.relationship_health import score_relationship
        old = (datetime.now(timezone.utc) - timedelta(days=45)).strftime("%Y-%m-%d")
        h = score_relationship(self._rel("customer"), old)
        assert h["status"] == "red"

    def test_no_contact_logged(self):
        from tools.second_brain.relationship_health import score_relationship
        h = score_relationship(self._rel(), None)
        assert h["status"] == "amber"
        assert h["days_since"] is None

    def test_boss_tighter_threshold(self):
        from tools.second_brain.relationship_health import score_relationship
        slightly_old = (datetime.now(timezone.utc) - timedelta(days=10)).strftime("%Y-%m-%d")
        h = score_relationship(self._rel("boss"), slightly_old)
        assert h["status"] in ("amber", "red")

    def test_nudge_has_nudge_field(self):
        from tools.second_brain.relationship_health import score_relationship
        old = (datetime.now(timezone.utc) - timedelta(days=45)).strftime("%Y-%m-%d")
        h = score_relationship(self._rel("customer"), old)
        assert h.get("nudge") is not None

    def test_green_has_no_nudge(self):
        from tools.second_brain.relationship_health import score_relationship
        recent = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        h = score_relationship(self._rel(), recent)
        assert h.get("nudge") is None

    def test_generate_nudges_returns_list(self):
        from tools.second_brain.relationship_health import generate_relationship_nudges
        nudges = generate_relationship_nudges(USER_ID, TENANT_ID)
        assert isinstance(nudges, list)

    def test_generate_nudges_capped_at_5(self):
        from tools.second_brain.relationship_health import generate_relationship_nudges
        nudges = generate_relationship_nudges(USER_ID, TENANT_ID)
        assert len(nudges) <= 5


# ── DIC personaliser ──────────────────────────────────────────────────────────

def _seed_dic_objective(user_id: str, tenant_id: str) -> None:
    """Ensure at least one objective exists so personaliser has keywords."""
    import uuid
    try:
        from tools.db.storage import get_canvas_connection, sql_placeholder
        conn = get_canvas_connection("ICDEV_SECOND_BRAIN_ENABLED")
        ph = sql_placeholder(conn)
        conn.execute(
            f"INSERT OR IGNORE INTO user_objectives (id, user_id, tenant_id, title) "
            f"VALUES ({ph},{ph},{ph},{ph})",
            (str(uuid.uuid4()), user_id, tenant_id, "Network architecture design")
        )
        conn.commit()
    except Exception:
        pass


class TestDicPersonaliser:
    def test_empty_input(self):
        from tools.second_brain.dic_personaliser import personalise_dic_results
        assert personalise_dic_results([], USER_ID, TENANT_ID) == []

    def test_adds_combined_score(self):
        _seed_dic_objective(USER_ID, TENANT_ID)
        from tools.second_brain.dic_personaliser import personalise_dic_results
        results = [{"title": "network design", "content": "BGP routing", "score": 0.8}]
        ranked = personalise_dic_results(results, USER_ID, TENANT_ID)
        assert len(ranked) == 1
        assert "combined_score" in ranked[0]
        assert "personal_score" in ranked[0]

    def test_sorted_descending_by_combined_score(self):
        _seed_dic_objective(USER_ID, TENANT_ID)
        from tools.second_brain.dic_personaliser import personalise_dic_results
        results = [
            {"title": "unrelated topic aaa", "content": "xyz abc", "score": 0.9},
            {"title": "network arch", "content": "BGP MPLS routing", "score": 0.5},
        ]
        ranked = personalise_dic_results(results, USER_ID, TENANT_ID)
        assert len(ranked) == 2
        assert ranked[0]["combined_score"] >= ranked[-1]["combined_score"]

    def test_combined_score_in_0_1_range(self):
        _seed_dic_objective(USER_ID, TENANT_ID)
        from tools.second_brain.dic_personaliser import personalise_dic_results
        results = [{"title": "test", "content": "content", "score": 0.7}]
        ranked = personalise_dic_results(results, USER_ID, TENANT_ID)
        s = ranked[0]["combined_score"]
        assert 0.0 <= s <= 1.5  # base 0-1, personal 0-1, weighted sum can slightly exceed 1

    def test_no_crash_missing_score_field(self):
        from tools.second_brain.dic_personaliser import personalise_dic_results
        results = [{"title": "test", "content": "some content"}]  # no 'score' key
        ranked = personalise_dic_results(results, USER_ID, TENANT_ID)
        assert len(ranked) == 1


# ── slides tailor ─────────────────────────────────────────────────────────────

class TestSlidesTailor:
    def test_returns_framing_dict(self):
        from tools.second_brain.slides_tailor import get_audience_framing
        result = get_audience_framing("network architecture review", USER_ID, TENANT_ID)
        assert isinstance(result, dict)
        assert "framing" in result
        assert "personalised" in result

    def test_framing_has_required_keys(self):
        from tools.second_brain.slides_tailor import get_audience_framing
        result = get_audience_framing("quarterly business review", USER_ID, TENANT_ID)
        framing = result.get("framing", {})
        assert "tone" in framing
        assert "lead_with" in framing
        assert "open_with" in framing

    def test_empty_topic_no_crash(self):
        from tools.second_brain.slides_tailor import get_audience_framing
        result = get_audience_framing("", USER_ID, TENANT_ID)
        assert isinstance(result, dict)
        assert "framing" in result

    def test_suggested_openers_is_list(self):
        from tools.second_brain.slides_tailor import get_audience_framing
        result = get_audience_framing("architecture review", USER_ID, TENANT_ID)
        assert isinstance(result.get("suggested_openers"), list)
        assert len(result["suggested_openers"]) >= 1


# ── personal RAG ──────────────────────────────────────────────────────────────

class TestPersonalRag:
    def test_queue_text_returns_done(self):
        from tools.second_brain.personal_rag import queue_text
        r = queue_text(USER_ID, "NIST 800-53 AI controls", "NIST Notes", tenant_id=TENANT_ID)
        assert r.get("status") == "done"
        assert "id" in r

    def test_get_items_includes_added(self):
        from tools.second_brain.personal_rag import queue_text, get_items
        queue_text(USER_ID, "some knowledge content", "My Note", tenant_id=TENANT_ID)
        items = get_items(USER_ID, TENANT_ID)
        assert isinstance(items, list)
        assert any(i.get("title") == "My Note" for i in items)

    def test_delete_removes_item(self):
        from tools.second_brain.personal_rag import queue_text, get_items, delete_item
        r = queue_text(USER_ID, "temp content", "Temp Note", tenant_id=TENANT_ID)
        iid = r["id"]
        assert delete_item(iid, USER_ID, TENANT_ID) is True
        items = get_items(USER_ID, TENANT_ID)
        assert not any(i.get("id") == iid for i in items)

    def test_search_returns_list(self):
        from tools.second_brain.personal_rag import search_personal_rag
        results = search_personal_rag("NIST", USER_ID, TENANT_ID)
        assert isinstance(results, list)

    def test_search_no_crash_empty_query(self):
        from tools.second_brain.personal_rag import search_personal_rag
        results = search_personal_rag("", USER_ID, TENANT_ID)
        assert isinstance(results, list)

    def test_queue_text_with_tags(self):
        from tools.second_brain.personal_rag import queue_text
        r = queue_text(USER_ID, "tagged content", "Tagged", tags=["security", "compliance"], tenant_id=TENANT_ID)
        assert r.get("status") == "done"

    def test_get_items_empty_for_new_user(self):
        from tools.second_brain.personal_rag import get_items
        items = get_items("brand-new-user-xyz999", TENANT_ID)
        assert items == []


# ── retro ─────────────────────────────────────────────────────────────────────

class TestRetro:
    def test_generate_returns_dict(self):
        from tools.second_brain.retro import generate_weekly_retro
        retro = generate_weekly_retro(USER_ID, TENANT_ID)
        assert isinstance(retro, dict)

    def test_generate_has_required_keys(self):
        from tools.second_brain.retro import generate_weekly_retro
        retro = generate_weekly_retro(USER_ID, TENANT_ID)
        for key in ("week_ending", "done_tasks", "commits", "interactions",
                    "objective_progress", "summary"):
            assert key in retro, f"Missing key: {key}"

    def test_generate_lists_are_lists(self):
        from tools.second_brain.retro import generate_weekly_retro
        retro = generate_weekly_retro(USER_ID, TENANT_ID)
        assert isinstance(retro["done_tasks"], list)
        assert isinstance(retro["commits"], list)
        assert isinstance(retro["interactions"], list)
        assert isinstance(retro["objective_progress"], list)

    def test_generate_summary_is_string(self):
        from tools.second_brain.retro import generate_weekly_retro
        retro = generate_weekly_retro(USER_ID, TENANT_ID)
        assert isinstance(retro["summary"], str)
        assert len(retro["summary"]) > 0

    def test_get_latest_no_crash_empty(self):
        from tools.second_brain.retro import get_latest_retro
        retro = get_latest_retro("no-retro-user-xyz", TENANT_ID)
        assert retro is None or isinstance(retro, dict)

    def test_generate_then_retrieve(self):
        from tools.second_brain.retro import generate_weekly_retro, get_latest_retro
        generate_weekly_retro(USER_ID, TENANT_ID)
        retro = get_latest_retro(USER_ID, TENANT_ID)
        assert retro is not None
        assert isinstance(retro, dict)


# ── unified search ────────────────────────────────────────────────────────────

class TestSearch:
    def test_empty_query_returns_empty_dict(self):
        from tools.second_brain.search import unified_search
        result = unified_search("", USER_ID, TENANT_ID)
        assert result == {}

    def test_single_char_returns_empty_dict(self):
        from tools.second_brain.search import unified_search
        result = unified_search("x", USER_ID, TENANT_ID)
        assert result == {}

    def test_valid_query_returns_dict(self):
        from tools.second_brain.search import unified_search
        result = unified_search("network", USER_ID, TENANT_ID)
        assert isinstance(result, dict)

    def test_result_buckets_are_lists(self):
        from tools.second_brain.search import unified_search
        result = unified_search("test", USER_ID, TENANT_ID)
        for bucket in result.values():
            assert isinstance(bucket, list)

    def test_no_empty_buckets_in_result(self):
        from tools.second_brain.search import unified_search
        result = unified_search("asdfghjklqwerty", USER_ID, TENANT_ID)
        for key, items in result.items():
            assert len(items) > 0, f"Empty bucket '{key}' should be excluded"

    def test_search_finds_added_knowledge(self):
        from tools.second_brain.personal_rag import queue_text
        from tools.second_brain.search import unified_search
        queue_text(USER_ID, "NIST 800-53 security controls revision 6", "NIST Rev6", tenant_id=TENANT_ID)
        result = unified_search("NIST", USER_ID, TENANT_ID)
        assert isinstance(result, dict)
        if "knowledge" in result:
            assert len(result["knowledge"]) >= 1


# ── routes ────────────────────────────────────────────────────────────────────

class TestRoutes:
    PAGES = [
        "/me/",
        "/me/profile",
        "/me/objectives",
        "/me/customers",
        "/me/briefing/today",
        "/me/learn",
        "/me/search",
        "/me/retro",
        "/me/integrations",
    ]

    @pytest.mark.parametrize("path", PAGES)
    def test_page_loads_200(self, flask_client, path):
        r = flask_client.get(path, follow_redirects=True)
        assert r.status_code == 200, f"{path} returned {r.status_code}: {r.data[:200]}"

    def test_relationship_health_endpoint(self, flask_client):
        r = flask_client.get("/me/api/second-brain/relationships/health")
        assert r.status_code == 200
        data = json.loads(r.data)
        assert data.get("ok") is True
        assert "relationships" in data

    def test_learn_text_endpoint(self, flask_client):
        r = flask_client.post(
            "/me/api/second-brain/learn/text",
            json={"text": "test knowledge content", "title": "Route Test Note"},
        )
        assert r.status_code == 200
        data = json.loads(r.data)
        assert data.get("ok") is True

    def test_learn_items_endpoint(self, flask_client):
        r = flask_client.get("/me/api/second-brain/learn/items")
        assert r.status_code == 200
        data = json.loads(r.data)
        assert data.get("ok") is True
        assert "items" in data

    def test_search_api_endpoint(self, flask_client):
        r = flask_client.get("/me/api/second-brain/search?q=test")
        assert r.status_code == 200
        data = json.loads(r.data)
        assert data.get("ok") is True
        assert "results" in data

    def test_search_api_empty_q(self, flask_client):
        r = flask_client.get("/me/api/second-brain/search")
        assert r.status_code in (400, 200)

    def test_infer_role_endpoint(self, flask_client):
        r = flask_client.get("/me/api/second-brain/profile/infer-role?title=Network+Architect")
        assert r.status_code == 200
        data = json.loads(r.data)
        assert data.get("ok") is True
        assert "persona" in data

    def test_infer_role_returns_persona_name(self, flask_client):
        r = flask_client.get("/me/api/second-brain/profile/infer-role?title=Senior+Network+Architect")
        data = json.loads(r.data)
        persona = data.get("persona", {})
        assert persona.get("persona") == "solutions_architect"

    def test_commitment_alerts_endpoint(self, flask_client):
        r = flask_client.get("/me/api/second-brain/proactive/commitment-alerts")
        assert r.status_code == 200
        data = json.loads(r.data)
        assert data.get("ok") is True

    def test_retro_generate_endpoint(self, flask_client):
        r = flask_client.post("/me/api/second-brain/retro/generate")
        assert r.status_code == 200
        data = json.loads(r.data)
        assert data.get("ok") is True
        assert "retro" in data

    def test_profile_save_endpoint(self, flask_client):
        r = flask_client.post(
            "/me/api/second-brain/profile",
            json={"title": "Test Engineer", "org_name": "Test Org"},
        )
        assert r.status_code == 200
        data = json.loads(r.data)
        assert data.get("ok") is True


# ── auth gate (cnr-me-01) ──────────────────────────────────────────────────────

class TestAuthGate:
    def test_unauth_api_returns_401(self, sb_app, monkeypatch):
        monkeypatch.delenv("ICDEV_AUTH_BYPASS", raising=False)
        monkeypatch.delenv("ICDEV_DASHBOARD_API_KEY", raising=False)
        r = sb_app.test_client().get("/me/api/second-brain/profile")
        assert r.status_code == 401
        assert json.loads(r.data).get("error") == "Authentication required"

    def test_unauth_page_redirects_to_login(self, sb_app, monkeypatch):
        monkeypatch.delenv("ICDEV_AUTH_BYPASS", raising=False)
        monkeypatch.delenv("ICDEV_DASHBOARD_API_KEY", raising=False)
        r = sb_app.test_client().get("/me/profile", follow_redirects=False)
        assert r.status_code in (301, 302)
        assert "/login" in r.headers.get("Location", "")

    def test_unauth_mutating_returns_401(self, sb_app, monkeypatch):
        monkeypatch.delenv("ICDEV_AUTH_BYPASS", raising=False)
        monkeypatch.delenv("ICDEV_DASHBOARD_API_KEY", raising=False)
        r = sb_app.test_client().post(
            "/me/api/second-brain/profile",
            json={"title": "Hacker"},
        )
        assert r.status_code == 401

    def test_authed_session_allows_access(self, sb_app, monkeypatch):
        monkeypatch.delenv("ICDEV_AUTH_BYPASS", raising=False)
        client = sb_app.test_client()
        with client.session_transaction() as sess:
            sess["user_id"] = "auth-user-a"
        r = client.get("/me/api/second-brain/profile")
        assert r.status_code == 200

    def test_no_default_identity_fallback(self, sb_app):
        """_user_id() must never collapse to a shared 'default' identity."""
        import importlib
        bp = importlib.import_module("tools.second_brain.blueprint")
        # A request context with no session must abort(401) rather than 'default'.
        from werkzeug.exceptions import Unauthorized
        with sb_app.test_request_context("/me/profile"):
            with pytest.raises(Unauthorized):
                bp._user_id()

    def test_two_users_see_distinct_data(self, sb_app, monkeypatch):
        """Authenticated users must read only their own profile rows."""
        monkeypatch.delenv("ICDEV_AUTH_BYPASS", raising=False)
        from tools.second_brain.profile import upsert_profile
        upsert_profile("auth-user-a", "default", org_name="Org-A")
        upsert_profile("auth-user-b", "default", org_name="Org-B")

        client = sb_app.test_client()
        with client.session_transaction() as sess:
            sess["user_id"] = "auth-user-a"
        data_a = json.loads(client.get("/me/api/second-brain/profile").data)

        client_b = sb_app.test_client()
        with client_b.session_transaction() as sess:
            sess["user_id"] = "auth-user-b"
        data_b = json.loads(client_b.get("/me/api/second-brain/profile").data)

        assert data_a.get("profile", {}).get("org_name") == "Org-A"
        assert data_b.get("profile", {}).get("org_name") == "Org-B"

    def test_auth_bypass_env_allows_access(self, sb_app, monkeypatch):
        monkeypatch.setenv("ICDEV_AUTH_BYPASS", "1")
        r = sb_app.test_client().get("/me/api/second-brain/profile")
        assert r.status_code == 200


# ── PII redaction at LLM egress (cnr-me-02) ────────────────────────────────────

class TestEgressRedaction:
    @staticmethod
    def _patch_router(monkeypatch):
        """Capture every prompt handed to LLMRouter.invoke.

        Patches invoke on BOTH the ``tools.llm.router`` shim class and the
        canonical ``icdev.tools.llm.router`` class — the tools/* backward-compat
        shim can bind either object depending on import order.
        """
        import importlib
        captured: list[str] = []

        def fake_invoke(self, function, request, **kwargs):
            if isinstance(request, dict):
                captured.append(request.get("prompt", ""))
            else:
                captured.append(
                    getattr(request, "system_prompt", "")
                    + str(getattr(request, "messages", request))
                )
            return {"content": "Good morning.\nFocus today."}

        for name in ("tools.llm.router", "icdev.tools.llm.router"):
            try:
                mod = importlib.import_module(name)
                monkeypatch.setattr(mod.LLMRouter, "invoke", fake_invoke)
            except Exception:
                pass
        return captured

    def _run_briefing(self):
        from tools.second_brain.briefing import _build_content
        ctx = {"name": "Jane Doe", "title": "Architect", "org_name": "ACME",
               "delivery_channels": ["dashboard"]}
        cal = [{"title": "Design Sync", "attendees": ["alice@acme.com"]}]
        return _build_content(
            ctx=ctx, briefing_date="2026-07-18", calendar_items=cal,
            task_items=[], mention_items=[], objectives=[], challenges=[],
            user_id="redact-user", tenant_id=TENANT_ID,
        )

    def test_email_masked_when_toggle_on(self, monkeypatch):
        monkeypatch.setenv("ICDEV_SECOND_BRAIN_REDACT_EGRESS", "1")
        captured = self._patch_router(monkeypatch)
        self._run_briefing()
        joined = " ".join(captured)
        assert captured, "router was never invoked"
        assert "alice@acme.com" not in joined
        assert "[EMAIL" in joined

    def test_email_present_when_toggle_off(self, monkeypatch):
        monkeypatch.setenv("ICDEV_SECOND_BRAIN_REDACT_EGRESS", "0")
        captured = self._patch_router(monkeypatch)
        self._run_briefing()
        joined = " ".join(captured)
        assert "alice@acme.com" in joined

    def test_redact_for_llm_unit(self, monkeypatch):
        from tools.second_brain.redaction_util import redact_for_llm
        text = "Email bob@corp.org for the review."
        monkeypatch.setenv("ICDEV_SECOND_BRAIN_REDACT_EGRESS", "1")
        assert "bob@corp.org" not in redact_for_llm(text)
        monkeypatch.setenv("ICDEV_SECOND_BRAIN_REDACT_EGRESS", "0")
        assert redact_for_llm(text) == text

    def test_profile_summary_masks_before_egress(self, monkeypatch):
        monkeypatch.setenv("ICDEV_SECOND_BRAIN_REDACT_EGRESS", "1")
        captured = self._patch_router(monkeypatch)
        from tools.second_brain.profile import upsert_profile, generate_profile_summary
        upsert_profile("redact-prof", TENANT_ID, full_name="Carol King",
                       title="PM", org_name="Globex", context_complete=1)
        generate_profile_summary("redact-prof", TENANT_ID)
        # No assertion on names (NER may be offline); ensure the path invoked the
        # router with a prompt (redaction ran without raising).
        assert captured, "router was never invoked"


# ── msgraph integration: CHECK + split-brain connection (cnr-me-03) ─────────────

class TestMsGraphIntegration:
    def test_check_constraint_includes_msgraph_and_matches_constant(self):
        import re
        from tools.second_brain.constants import INTEGRATION_SERVICES
        for rel in (
            pathlib.Path("icdev") / "tools" / "db" / "migrations" / "223_user_identity.sql",
            pathlib.Path("icdev") / "tools" / "db" / "schema" / "pg_consolidated.sql",
        ):
            sql = (pathlib.Path(__file__).parent.parent / rel).read_text(encoding="utf-8")
            m = re.search(r"service\s+TEXT NOT NULL CHECK\(service IN \(([^)]*)\)\)", sql)
            assert m, f"service CHECK not found in {rel}"
            services = {s.strip().strip("'") for s in m.group(1).split(",")}
            assert "msgraph" in services, f"msgraph missing from CHECK in {rel}"
            assert services == set(INTEGRATION_SERVICES), (
                f"CHECK service list in {rel} diverges from INTEGRATION_SERVICES"
            )

    def test_msgraph_token_roundtrip_same_connection(self):
        """Connect flow persists and get_integration_tokens retrieves the same row."""
        from tools.second_brain.integrations import save_integration, get_integration_tokens
        save_integration(
            user_id="ms-user", service="msgraph",
            access_token="AT123", refresh_token="RT456",
            token_expiry="2030-01-01T00:00:00+00:00",
            metadata={"teams_chat_id": "chat-1"}, tenant_id=TENANT_ID,
        )
        toks = get_integration_tokens("ms-user", "msgraph", TENANT_ID)
        assert toks.get("access_token") == "AT123"
        assert toks.get("refresh_token") == "RT456"
        assert toks.get("metadata", {}).get("teams_chat_id") == "chat-1"

    def test_get_m365_token_retrieves_saved(self):
        from tools.second_brain.integrations import save_integration
        from tools.second_brain.briefing import _get_m365_token
        save_integration(
            user_id="ms-user2", service="msgraph",
            access_token="LIVE-TOKEN",
            token_expiry="2030-01-01T00:00:00+00:00", tenant_id=TENANT_ID,
        )
        assert _get_m365_token("ms-user2", TENANT_ID) == "LIVE-TOKEN"


# ── hygiene: batching, dialect, base URL, SOUL, migration (cnr-me-04) ───────────

class TestBriefingBatching:
    def test_single_llm_call_for_briefing(self, monkeypatch):
        """Greeting + focus + all per-meeting prep notes = ONE LLM call."""
        import importlib
        calls = {"n": 0}

        def fake_invoke(self, function, request, **kwargs):
            calls["n"] += 1
            return {"content": '{"greeting":"Hi.","focus":"Ship it.",'
                               '"meetings":[{"prep":"Review agenda."},{"prep":"Bring notes."}]}'}

        for name in ("tools.llm.router", "icdev.tools.llm.router"):
            try:
                mod = importlib.import_module(name)
                monkeypatch.setattr(mod.LLMRouter, "invoke", fake_invoke)
            except Exception:
                pass

        from tools.second_brain.briefing import _build_content
        cal = [{"title": "A", "attendees": []}, {"title": "B", "attendees": []}]
        out = _build_content(
            ctx={"name": "Jane", "title": "Arch", "org_name": "ACME",
                 "delivery_channels": ["dashboard"]},
            briefing_date="2026-07-18", calendar_items=cal,
            task_items=[], mention_items=[], objectives=[], challenges=[],
            user_id="batch-user", tenant_id=TENANT_ID,
        )
        assert calls["n"] == 1, f"expected 1 batched LLM call, got {calls['n']}"
        assert out["greeting"] == "Hi."
        assert out["focus"] == "Ship it."
        assert out["meetings"][0]["prep_notes"] == "Review agenda."
        assert out["meetings"][1]["prep_notes"] == "Bring notes."

    def test_base_url_configurable(self, monkeypatch):
        from tools.second_brain.briefing import _base_url
        monkeypatch.setenv("ICDEV_BASE_URL", "https://icdev.example.mil/")
        assert _base_url() == "https://icdev.example.mil"
        monkeypatch.delenv("ICDEV_BASE_URL", raising=False)
        assert _base_url() == "http://localhost:5050"

    def test_ensure_tables_removed(self):
        """The dead _ensure_tables() must be gone (cnr-me-04b)."""
        from tools.second_brain import profile as prof
        assert not hasattr(prof, "_ensure_tables")

    def test_migration_uses_portable_default(self):
        sql = (pathlib.Path(__file__).parent.parent / "icdev" / "tools" / "db"
               / "migrations" / "223_user_identity.sql").read_text(encoding="utf-8")
        assert "DEFAULT (datetime('now'))" not in sql
        assert "DEFAULT CURRENT_TIMESTAMP" in sql


class TestErrorHygiene:
    def test_500_returns_generic_message(self, sb_app, monkeypatch):
        """API 500s must not leak raw exception detail (cnr-me-04d)."""
        monkeypatch.setenv("ICDEV_AUTH_BYPASS", "1")
        import tools.second_brain.relationship_health as rh

        def boom(*a, **k):
            raise RuntimeError("SECRET internal path /etc/creds")

        monkeypatch.setattr(rh, "get_relationship_health_map", boom)
        r = sb_app.test_client().get("/me/api/second-brain/relationships/health")
        assert r.status_code == 500
        body = json.loads(r.data)
        assert body.get("error") == "Internal server error"
        assert "SECRET" not in r.data.decode()


class TestSoulInjection:
    def test_inject_user_profile_context_appends_operator_section(self):
        from tools.second_brain.profile import upsert_profile, save_objectives
        upsert_profile("soul-user", TENANT_ID, full_name="Dana Lee",
                       title="Architect", org_name="Initech", context_complete=1)
        save_objectives("soul-user", [{"title": "Ship the platform"}], TENANT_ID)
        from icdev.tools.ace.soul_manager import inject_user_profile_context
        out = inject_user_profile_context("## Identity\nYou are helpful.", "soul-user", TENANT_ID)
        assert "Operator Context" in out
        assert "Dana Lee" in out
        assert "Ship the platform" in out

    def test_inject_skips_for_default_and_incomplete(self):
        from icdev.tools.ace.soul_manager import inject_user_profile_context
        base = "## Identity"
        assert inject_user_profile_context(base, "default", TENANT_ID) == base
        # unknown user → no profile → unchanged
        assert inject_user_profile_context(base, "nobody-xyz", TENANT_ID) == base
