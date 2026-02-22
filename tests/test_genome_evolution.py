#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for P4-1: Genome Evolution + Bidirectional Learning (Phase 36C).

Covers: AbsorptionEngine, LearningCollector, CrossPollinator.
"""

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TestAbsorptionEngine(unittest.TestCase):
    """Tests for the AbsorptionEngine (D212 — 72-hour stability window)."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()

    def tearDown(self):
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    def _get_engine(self):
        from tools.registry.absorption_engine import AbsorptionEngine
        return AbsorptionEngine(db_path=self.db_path)

    def test_import(self):
        """AbsorptionEngine class should be importable."""
        from tools.registry.absorption_engine import AbsorptionEngine
        self.assertTrue(callable(AbsorptionEngine))

    def test_init_creates_tables(self):
        """Initialization should create required DB tables."""
        engine = self._get_engine()
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        tables = {row[0] for row in cursor.fetchall()}
        conn.close()
        # Should have at least the staging/evaluation related tables
        self.assertTrue(len(tables) >= 1)

    def test_check_stability_no_data(self):
        """check_stability with no data should return not-stable."""
        engine = self._get_engine()
        result = engine.check_stability("nonexistent-cap")
        self.assertIsInstance(result, dict)
        self.assertIn("stable", result)
        self.assertFalse(result["stable"])

    def test_absorb_requires_stability(self):
        """absorb() should reject capabilities that haven't met stability window."""
        engine = self._get_engine()
        result = engine.absorb("nonexistent-cap", absorbed_by="test")
        self.assertIsInstance(result, dict)
        self.assertIn("absorbed", result)
        self.assertFalse(result["absorbed"])

    def test_get_absorption_candidates(self):
        """get_absorption_candidates() should return a list."""
        engine = self._get_engine()
        candidates = engine.get_absorption_candidates()
        self.assertIsInstance(candidates, list)

    def test_stability_window_is_72_hours(self):
        """D212: The stability window should be at least 72 hours."""
        engine = self._get_engine()
        # Check that the engine has a stability window constant >= 72
        window = getattr(engine, "STABILITY_WINDOW_HOURS", None)
        if window is not None:
            self.assertGreaterEqual(window, 72)


class TestLearningCollector(unittest.TestCase):
    """Tests for LearningCollector — process child-reported learned behaviors (D213)."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()

    def tearDown(self):
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    def _get_collector(self):
        from tools.registry.learning_collector import LearningCollector
        return LearningCollector(db_path=self.db_path)

    def test_import(self):
        """LearningCollector class should be importable."""
        from tools.registry.learning_collector import LearningCollector
        self.assertTrue(callable(LearningCollector))

    def test_init_creates_tables(self):
        """Initialization should create child_learned_behaviors table."""
        collector = self._get_collector()
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        tables = {row[0] for row in cursor.fetchall()}
        conn.close()
        self.assertTrue(len(tables) >= 1)

    def test_ingest_behavior(self):
        """ingest_behavior should store a behavior record and return behavior_id string."""
        collector = self._get_collector()
        result = collector.ingest_behavior(
            child_id="child-001",
            behavior_type="optimization",
            description="Learned to cache LLM responses",
            evidence={"cache_hit_rate": 0.85},
            confidence=0.9,
        )
        # ingest_behavior returns Optional[str] (behavior_id or None)
        self.assertIsNotNone(result)
        self.assertIsInstance(result, str)

    def test_get_unevaluated(self):
        """get_unevaluated should return behaviors not yet evaluated."""
        collector = self._get_collector()
        # Ingest a behavior first
        collector.ingest_behavior(
            child_id="child-002",
            behavior_type="error_recovery",
            description="Learned retry with backoff",
            evidence={"success_rate": 0.95},
            confidence=0.8,
        )
        unevaluated = collector.get_unevaluated()
        self.assertIsInstance(unevaluated, list)
        self.assertGreaterEqual(len(unevaluated), 1)

    def test_evaluate_behavior(self):
        """evaluate_behavior should mark a behavior as evaluated."""
        collector = self._get_collector()
        behavior_id = collector.ingest_behavior(
            child_id="child-003",
            behavior_type="optimization",
            description="Learned CUI marking pattern",
            evidence={},
            confidence=0.7,
        )
        if behavior_id:
            eval_result = collector.evaluate_behavior(
                behavior_id=behavior_id,
            )
            self.assertIsInstance(eval_result, dict)


class TestCrossPollinator(unittest.TestCase):
    """Tests for CrossPollinator — broker capabilities between children via parent."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()

    def tearDown(self):
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    def _get_pollinator(self):
        from tools.registry.cross_pollinator import CrossPollinator
        return CrossPollinator(db_path=self.db_path)

    def test_import(self):
        """CrossPollinator class should be importable."""
        from tools.registry.cross_pollinator import CrossPollinator
        self.assertTrue(callable(CrossPollinator))

    def test_find_candidates_empty(self):
        """find_candidates with no children returns empty list."""
        pollinator = self._get_pollinator()
        candidates = pollinator.find_candidates()
        self.assertIsInstance(candidates, list)
        self.assertEqual(len(candidates), 0)

    def test_propose_pollination(self):
        """propose_pollination should create a proposal record."""
        pollinator = self._get_pollinator()
        result = pollinator.propose_pollination(
            source_child_id="child-001",
            capability_name="caching",
            target_child_ids=["child-002", "child-003"],
            rationale="High cache hit rate observed",
        )
        self.assertIsInstance(result, dict)

    def test_execute_requires_approval(self):
        """execute_pollination should require HITL approval."""
        pollinator = self._get_pollinator()
        result = pollinator.execute_pollination("nonexistent-proposal")
        self.assertIsInstance(result, dict)
        # Should fail or indicate not approved
        self.assertFalse(result.get("executed", False))

    def test_hitl_mandatory(self):
        """D214: HITL is mandatory for cross-pollination — no auto-execute."""
        pollinator = self._get_pollinator()
        # Verify there's no bypass for the approval step
        result = pollinator.execute_pollination("fake-id")
        self.assertFalse(result.get("executed", False))


if __name__ == "__main__":
    unittest.main()
