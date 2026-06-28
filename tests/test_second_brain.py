# CUI // SP-CTI
"""Tests for the Second Brain / AI Executive Assistant system."""
from __future__ import annotations

import json
import os
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

# Ensure repo root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("ICDEV_STORAGE_BACKEND", "sqlite")
os.environ.setdefault("ICDEV_SECOND_BRAIN_ENABLED", "true")
os.environ.setdefault("ICDEV_SECRET_KEY", "test-key-for-second-brain")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers — lightweight in-memory DB stub for canvas-scoped connection
# ─────────────────────────────────────────────────────────────────────────────

class _WrappedConn:
    """Thin wrapper adding _backend attr so SQL placeholder detection works on Py3.14."""
    _backend = "sqlite"

    def __init__(self, inner):
        self._inner = inner

    def execute(self, sql, params=()):
        # Translate %s placeholders to ? for SQLite
        sql = sql.replace("%s", "?")
        return self._inner.execute(sql, params)

    def commit(self):
        return self._inner.commit()

    def close(self):
        return self._inner.close()


def _make_canvas_conn():
    """Return a wrapped SQLite in-memory connection with the Second Brain schema."""
    import sqlite3
    inner = sqlite3.connect(":memory:")
    inner.row_factory = sqlite3.Row
    conn = _WrappedConn(inner)

    schema_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "icdev", "tools", "db", "migrations", "223_user_identity.sql",
    )
    if os.path.exists(schema_path):
        with open(schema_path, encoding="utf-8") as fh:
            sql = fh.read()
        for stmt in sql.split(";"):
            stmt = stmt.strip()
            if stmt:
                try:
                    conn.execute(stmt)
                except Exception:
                    pass
    conn.commit()
    return conn


class _CM:
    """Context-manager wrapper for a plain connection."""
    def __init__(self, conn):
        self._conn = conn
    def __enter__(self):
        return self._conn
    def __exit__(self, *_):
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Constants tests
# ─────────────────────────────────────────────────────────────────────────────

class TestSecondBrainConstants(unittest.TestCase):
    def test_challenge_keys_count(self):
        from tools.second_brain.constants import CHALLENGE_KEYS
        self.assertEqual(len(CHALLENGE_KEYS), 10)

    def test_seniority_tiers(self):
        from tools.second_brain.constants import SENIORITY_TIERS
        self.assertIn("ic", SENIORITY_TIERS)
        self.assertIn("executive", SENIORITY_TIERS)

    def test_integration_services(self):
        from tools.second_brain.constants import INTEGRATION_SERVICES
        self.assertIn("github", INTEGRATION_SERVICES)
        self.assertIn("slack", INTEGRATION_SERVICES)

    def test_tier_integrations_keys(self):
        from tools.second_brain.constants import SENIORITY_TIERS, TIER_INTEGRATIONS
        for tier in SENIORITY_TIERS:
            self.assertIn(tier, TIER_INTEGRATIONS)

    def test_relationship_labels_complete(self):
        from tools.second_brain.constants import RELATIONSHIP_LABELS, RELATIONSHIP_TYPES
        for rt in RELATIONSHIP_TYPES:
            self.assertIn(rt, RELATIONSHIP_LABELS)

    def test_challenge_labels_complete(self):
        from tools.second_brain.constants import CHALLENGE_KEYS, CHALLENGE_LABELS
        for k in CHALLENGE_KEYS:
            self.assertIn(k, CHALLENGE_LABELS)

    def test_comm_style_labels(self):
        from tools.second_brain.constants import COMM_STYLE_LABELS
        self.assertEqual(len(COMM_STYLE_LABELS), 5)

    def test_briefing_env_flag(self):
        from tools.second_brain.constants import BRIEFING_ENV_FLAG
        self.assertTrue(BRIEFING_ENV_FLAG.startswith("ICDEV_"))


# ─────────────────────────────────────────────────────────────────────────────
# Profile CRUD tests
# ─────────────────────────────────────────────────────────────────────────────

class TestSecondBrainProfile(unittest.TestCase):
    def setUp(self):
        self._conn = _make_canvas_conn()
        self._cm = _CM(self._conn)
        self._patch = patch("tools.second_brain.profile._conn", return_value=self._cm)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._conn.close()

    def test_upsert_and_get_profile(self):
        from tools.second_brain.profile import get_profile, upsert_profile
        upsert_profile("u1", full_name="Alice", title="Engineer", org_name="ACME")
        p = get_profile("u1")
        self.assertIsNotNone(p)
        self.assertEqual(p["full_name"], "Alice")
        self.assertEqual(p["org_name"], "ACME")

    def test_upsert_updates_existing(self):
        from tools.second_brain.profile import get_profile, upsert_profile
        upsert_profile("u2", full_name="Bob")
        upsert_profile("u2", full_name="Bob Updated", title="Senior Eng")
        p = get_profile("u2")
        self.assertEqual(p["full_name"], "Bob Updated")
        self.assertEqual(p["title"], "Senior Eng")

    def test_get_profile_missing_returns_none(self):
        from tools.second_brain.profile import get_profile
        self.assertIsNone(get_profile("nonexistent-user"))

    def test_save_objectives(self):
        from tools.second_brain.profile import get_objectives, save_objectives, upsert_profile
        upsert_profile("u3")
        objs = [{"title": "Ship v2", "horizon": "quarter"}, {"title": "Learn Rust", "horizon": "long_term"}]
        ids = save_objectives("u3", objs)
        self.assertEqual(len(ids), 2)
        fetched = get_objectives("u3")
        self.assertEqual(len(fetched), 2)
        titles = [o["title"] for o in fetched]
        self.assertIn("Ship v2", titles)

    def test_save_relationships(self):
        from tools.second_brain.profile import get_relationships, save_relationships, upsert_profile
        upsert_profile("u4")
        rels = [{"name": "Alice", "relationship_type": "boss"}, {"name": "Bob", "relationship_type": "peer"}]
        ids = save_relationships("u4", rels)
        self.assertEqual(len(ids), 2)
        fetched = get_relationships("u4")
        names = [r["name"] for r in fetched]
        self.assertIn("Alice", names)
        self.assertIn("Bob", names)

    def test_save_challenges(self):
        from tools.second_brain.profile import get_challenges, save_challenges, upsert_profile
        upsert_profile("u5")
        chals = [{"challenge_key": "meeting_overload", "severity": "high"}]
        ids = save_challenges("u5", chals)
        self.assertEqual(len(ids), 1)
        fetched = get_challenges("u5")
        self.assertEqual(fetched[0]["challenge_key"], "meeting_overload")

    def test_save_full_profile(self):
        from tools.second_brain.profile import get_full_profile, save_full_profile
        data = {
            "full_name": "Carol", "title": "CTO", "org_name": "TechCorp",
            "objectives": [{"title": "Scale platform", "horizon": "quarter"}],
            "relationships": [{"name": "Dave", "relationship_type": "direct"}],
            "challenges": [{"challenge_key": "unclear_priorities", "severity": "medium"}],
        }
        save_full_profile("u6", data)
        full = get_full_profile("u6")
        self.assertIsNotNone(full)
        self.assertEqual(full["profile"]["full_name"], "Carol")
        self.assertEqual(len(full["objectives"]), 1)
        self.assertEqual(len(full["relationships"]), 1)
        self.assertEqual(len(full["challenges"]), 1)

    def test_build_world_model_context(self):
        from tools.second_brain.profile import build_world_model_context, save_full_profile
        save_full_profile("u7", {
            "full_name": "Eve", "title": "VP Eng", "org_name": "Startup",
            "objectives": [{"title": "Grow team", "horizon": "quarter"}],
            "challenges": [{"challenge_key": "team_capacity", "severity": "high"}],
        })
        ctx = build_world_model_context("u7")
        self.assertIsNotNone(ctx)
        self.assertEqual(ctx["name"], "Eve")
        self.assertIn("Grow team", ctx["objectives"])
        self.assertIn("team_capacity", ctx["challenge_keys"])

    def test_objectives_replace_all(self):
        from tools.second_brain.profile import get_objectives, save_objectives, upsert_profile
        upsert_profile("u8")
        save_objectives("u8", [{"title": "Old goal", "horizon": "week"}])
        save_objectives("u8", [{"title": "New goal A"}, {"title": "New goal B"}])
        fetched = get_objectives("u8")
        titles = [o["title"] for o in fetched]
        self.assertNotIn("Old goal", titles)
        self.assertIn("New goal A", titles)


# ─────────────────────────────────────────────────────────────────────────────
# Token encryption tests
# ─────────────────────────────────────────────────────────────────────────────

class TestIntegrationTokens(unittest.TestCase):
    def setUp(self):
        self._conn = _make_canvas_conn()
        self._cm = _CM(self._conn)
        self._patch = patch("tools.second_brain.integrations._conn", return_value=self._cm)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._conn.close()

    def _skip_if_no_cryptography(self):
        try:
            import cryptography  # noqa: F401
        except ImportError:
            self.skipTest("cryptography package not installed")

    def test_encrypt_decrypt_roundtrip(self):
        self._skip_if_no_cryptography()
        from tools.second_brain.integrations import _decrypt, _encrypt
        token = "ghp_test_token_abc123"
        self.assertEqual(_decrypt(_encrypt(token)), token)

    def test_empty_token_roundtrip(self):
        self._skip_if_no_cryptography()
        from tools.second_brain.integrations import _decrypt, _encrypt
        self.assertEqual(_decrypt(_encrypt("")), "")

    def test_save_and_retrieve_token(self):
        self._skip_if_no_cryptography()
        from tools.second_brain.integrations import get_decrypted_token, save_integration
        save_integration("u1", "github", access_token="ghp_abc123")
        retrieved = get_decrypted_token("u1", "github")
        self.assertEqual(retrieved, "ghp_abc123")

    def test_revoke_clears_token(self):
        self._skip_if_no_cryptography()
        from tools.second_brain.integrations import get_decrypted_token, revoke_integration, save_integration
        save_integration("u2", "slack", access_token="xoxb-test")
        revoke_integration("u2", "slack")
        self.assertIsNone(get_decrypted_token("u2", "slack"))

    def test_list_integrations_no_tokens(self):
        self._skip_if_no_cryptography()
        from tools.second_brain.integrations import list_integrations, save_integration
        save_integration("u3", "jira", access_token="secret", metadata={"email": "a@b.com"})
        items = list_integrations("u3")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["service"], "jira")
        self.assertNotIn("access_token_enc", items[0])
        self.assertNotIn("secret", str(items[0]))


# ─────────────────────────────────────────────────────────────────────────────
# Briefing engine tests
# ─────────────────────────────────────────────────────────────────────────────

class TestBriefingEngine(unittest.TestCase):
    def setUp(self):
        self._conn = _make_canvas_conn()
        self._cm = _CM(self._conn)
        self._pconn = patch("tools.second_brain.briefing._conn", return_value=self._cm)
        self._ppconn = patch("tools.second_brain.profile._conn", return_value=self._cm)
        self._pconn.start()
        self._ppconn.start()

    def tearDown(self):
        self._pconn.stop()
        self._ppconn.stop()
        self._conn.close()

    def _seed_user(self):
        from tools.second_brain.profile import save_full_profile
        save_full_profile("brf_user", {
            "full_name": "Frank", "title": "Director", "org_name": "BigCo",
            "objectives": [{"title": "Launch Q3", "horizon": "quarter"}],
            "challenges": [{"challenge_key": "meeting_overload"}],
        })

    def test_generate_briefing_returns_dict(self):
        self._seed_user()
        with patch("tools.second_brain.briefing.get_users_due_for_briefing", return_value=[]):
            from tools.second_brain.briefing import generate_briefing
            result = generate_briefing("brf_user", "2026-06-28")
        self.assertIsInstance(result, dict)
        self.assertIn("date", result)
        self.assertIn("greeting", result)

    def test_generate_briefing_stored(self):
        self._seed_user()
        from tools.second_brain.briefing import generate_briefing, get_todays_briefing
        with patch("tools.second_brain.briefing.get_users_due_for_briefing", return_value=[]):
            generate_briefing("brf_user", "2026-06-28")
        stored = self._conn.execute(
            "SELECT content_json FROM user_daily_briefings WHERE user_id='brf_user'"
        ).fetchone()
        self.assertIsNotNone(stored)

    def test_get_todays_briefing_none_if_not_generated(self):
        from tools.second_brain.briefing import get_todays_briefing
        result = get_todays_briefing("no_such_user")
        self.assertIsNone(result)

    def test_get_users_due_no_profiles(self):
        from tools.second_brain.briefing import get_users_due_for_briefing
        due = get_users_due_for_briefing(8)
        self.assertEqual(due, [])

    def test_briefing_content_structure(self):
        self._seed_user()
        from tools.second_brain.briefing import generate_briefing
        result = generate_briefing("brf_user", "2026-06-28")
        self.assertIn("meetings", result)
        self.assertIn("tasks", result)
        self.assertIsInstance(result["meetings"], list)
        self.assertIsInstance(result["tasks"], list)


# ─────────────────────────────────────────────────────────────────────────────
# Onboarding state tests
# ─────────────────────────────────────────────────────────────────────────────

class TestOnboardingStateExtension(unittest.TestCase):
    def test_default_state_has_second_brain_keys(self):
        from tools.auth.onboarding import _DEFAULT_STATE
        self.assertIn("context_capture_complete", _DEFAULT_STATE)
        self.assertIn("identity_step_done", _DEFAULT_STATE)
        self.assertIn("connections_step_done", _DEFAULT_STATE)
        self.assertIn("world_step_done", _DEFAULT_STATE)
        self.assertIn("cadence_step_done", _DEFAULT_STATE)
        self.assertIn("integration_count", _DEFAULT_STATE)
        self.assertIn("briefing_channels", _DEFAULT_STATE)

    def test_briefing_channels_default(self):
        from tools.auth.onboarding import _DEFAULT_STATE
        self.assertEqual(_DEFAULT_STATE["briefing_channels"], ["dashboard"])


# ─────────────────────────────────────────────────────────────────────────────
# ACE SOUL injection tests
# ─────────────────────────────────────────────────────────────────────────────

class TestSoulManagerInjection(unittest.TestCase):
    def test_inject_returns_original_if_no_profile(self):
        from icdev.tools.ace.soul_manager import inject_user_profile_context
        with patch("tools.second_brain.profile.build_world_model_context", return_value=None):
            result = inject_user_profile_context("SOUL TEXT", "no_user")
        self.assertEqual(result, "SOUL TEXT")

    def test_inject_prepends_block_when_profile_exists(self):
        from icdev.tools.ace.soul_manager import inject_user_profile_context
        mock_ctx = {
            "name": "Grace", "title": "Lead", "org_name": "Org",
            "objectives": ["Goal 1"], "challenge_keys": ["meeting_overload"],
            "relationship_names": ["Alice"], "timezone": "UTC",
            "comm_style_label": "Direct",
        }
        with patch("tools.second_brain.profile.build_world_model_context", return_value=mock_ctx):
            result = inject_user_profile_context("BASE SOUL", "grace_user")
        self.assertIn("WHO YOU ARE WORKING WITH", result)
        self.assertIn("Grace", result)
        self.assertIn("Goal 1", result)
        self.assertIn("BASE SOUL", result)

    def test_inject_is_safe_on_import_error(self):
        from icdev.tools.ace.soul_manager import inject_user_profile_context
        with patch("tools.second_brain.profile.build_world_model_context", side_effect=ImportError):
            result = inject_user_profile_context("SOUL", "user1")
        self.assertEqual(result, "SOUL")


# ─────────────────────────────────────────────────────────────────────────────
# Connector smoke tests (mock HTTP)
# ─────────────────────────────────────────────────────────────────────────────

class TestConnectorSmokeTests(unittest.TestCase):
    def test_github_connector_no_token_returns_empty(self):
        from tools.second_brain.connectors.github import GitHubConnector
        c = GitHubConnector()
        with patch("tools.second_brain.integrations.get_decrypted_token", return_value=None):
            items = c.get_todays_items("test_user")
        self.assertEqual(items, [])

    def test_jira_connector_no_creds_returns_empty(self):
        from tools.second_brain.connectors.jira import JiraConnector
        c = JiraConnector()
        with patch("tools.second_brain.integrations.get_integration_metadata", return_value={}):
            items = c.get_todays_items("test_user")
        self.assertEqual(items, [])

    def test_slack_connector_no_token_returns_empty(self):
        from tools.second_brain.connectors.slack import SlackConnector
        c = SlackConnector()
        with patch("tools.second_brain.integrations.get_decrypted_token", return_value=None):
            items = c.get_todays_items("test_user")
        self.assertEqual(items, [])

    def test_google_connector_no_token_returns_empty(self):
        from tools.second_brain.connectors.google import GoogleConnector
        c = GoogleConnector()
        with patch("tools.second_brain.integrations.get_decrypted_token", return_value=None):
            items = c.get_todays_items("test_user")
        self.assertEqual(items, [])


# ─────────────────────────────────────────────────────────────────────────────
# IQE adapter tests
# ─────────────────────────────────────────────────────────────────────────────

class TestIQEAdapter(unittest.TestCase):
    def test_get_collections(self):
        from tools.iqe.adapters.second_brain import COLLECTIONS, get_collections
        self.assertEqual(get_collections(), COLLECTIONS)
        self.assertGreaterEqual(len(COLLECTIONS), 4)

    def test_query_profile_no_profile(self):
        from tools.iqe.adapters.second_brain import query
        with patch("tools.second_brain.profile.get_full_profile", return_value=None):
            results = query("what is my role", "second_brain.profile", "no_user")
        self.assertIsInstance(results, list)
        self.assertGreater(len(results), 0)

    def test_query_unknown_collection_returns_empty(self):
        from tools.iqe.adapters.second_brain import query
        results = query("test", "second_brain.unknown")
        self.assertEqual(results, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
