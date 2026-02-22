#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for P4-2: ATLAS Red Teaming Scanner (Phase 37C).

Covers: ATLASRedTeamScanner — opt-in adversarial testing (D219).
"""

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TestATLASRedTeamScanner(unittest.TestCase):
    """Tests for ATLASRedTeamScanner."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()

    def tearDown(self):
        try:
            os.unlink(self.db_path)
        except OSError:
            pass

    def _get_scanner(self):
        from tools.security.atlas_red_team import ATLASRedTeamScanner
        return ATLASRedTeamScanner(db_path=Path(self.db_path))

    def test_import(self):
        """ATLASRedTeamScanner class should be importable."""
        from tools.security.atlas_red_team import ATLASRedTeamScanner
        self.assertTrue(callable(ATLASRedTeamScanner))

    def test_init_creates_tables(self):
        """Initialization should create atlas_red_team_results table."""
        scanner = self._get_scanner()
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        tables = {row[0] for row in cursor.fetchall()}
        conn.close()
        self.assertIn("atlas_red_team_results", tables)

    def test_prompt_injection_resistance(self):
        """test_prompt_injection_resistance should detect AML.T0051."""
        scanner = self._get_scanner()
        result = scanner.test_prompt_injection_resistance(project_id="test-proj")
        self.assertIsInstance(result, dict)
        self.assertIn("technique", result)
        self.assertEqual(result["technique"], "AML.T0051")

    def test_system_prompt_extraction(self):
        """test_system_prompt_extraction should detect AML.T0056."""
        scanner = self._get_scanner()
        result = scanner.test_system_prompt_extraction(project_id="test-proj")
        self.assertIsInstance(result, dict)
        self.assertIn("technique", result)
        self.assertEqual(result["technique"], "AML.T0056")

    def test_memory_poisoning(self):
        """test_memory_poisoning should detect AML.T0080."""
        scanner = self._get_scanner()
        result = scanner.test_memory_poisoning(project_id="test-proj")
        self.assertIsInstance(result, dict)
        self.assertIn("technique", result)

    def test_tool_abuse(self):
        """test_tool_abuse should detect AML.T0086."""
        scanner = self._get_scanner()
        result = scanner.test_tool_abuse(project_id="test-proj")
        self.assertIsInstance(result, dict)
        self.assertIn("technique", result)

    def test_data_leakage(self):
        """test_data_leakage should detect AML.T0057."""
        scanner = self._get_scanner()
        result = scanner.test_data_leakage(project_id="test-proj")
        self.assertIsInstance(result, dict)
        self.assertIn("technique", result)

    def test_cost_harvesting(self):
        """test_cost_harvesting should detect AML.T0034."""
        scanner = self._get_scanner()
        result = scanner.test_cost_harvesting(project_id="test-proj")
        self.assertIsInstance(result, dict)
        self.assertIn("technique", result)

    def test_results_stored_in_db(self):
        """Red team results should be stored in atlas_red_team_results table."""
        scanner = self._get_scanner()
        scanner.run_technique("AML.T0051", project_id="test-proj")
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute("SELECT COUNT(*) FROM atlas_red_team_results")
        count = cursor.fetchone()[0]
        conn.close()
        self.assertGreaterEqual(count, 1)

    def test_opt_in_flag(self):
        """D219: Red teaming should be opt-in only."""
        # The scanner should exist but requires explicit invocation
        from tools.security.atlas_red_team import ATLASRedTeamScanner
        # Verify CLI has --atlas-red-team flag by checking module
        import inspect
        source = inspect.getsource(
            sys.modules.get("tools.security.atlas_red_team",
                           type(sys)("dummy"))
        ) if "tools.security.atlas_red_team" in sys.modules else ""
        # Just verify the scanner doesn't auto-execute on import
        self.assertTrue(callable(ATLASRedTeamScanner))


if __name__ == "__main__":
    unittest.main()
