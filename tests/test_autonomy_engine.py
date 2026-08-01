#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for Autonomy Engine — trust, kill switch, self-evolution, federation (Phase 68)."""

import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# trust_engine authors %s placeholders for PostgreSQL and expects
# _get_connection to rewrite them; a bare sqlite3 connection does not.
from _sql_compat import connect as _tconnect  # noqa: E402


def _create_autonomy_db(db_path):
    """Create minimal autonomy DB for testing."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS autonomy_trust_state (
            category TEXT PRIMARY KEY,
            alpha REAL NOT NULL DEFAULT 2.0,
            beta REAL NOT NULL DEFAULT 8.0,
            ceiling REAL NOT NULL DEFAULT 1.0,
            total_observations INTEGER DEFAULT 0,
            total_successes INTEGER DEFAULT 0,
            total_failures INTEGER DEFAULT 0,
            current_tier TEXT DEFAULT 'observer',
            last_updated TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS autonomy_observations (
            id TEXT PRIMARY KEY,
            category TEXT NOT NULL,
            outcome TEXT NOT NULL,
            alpha_before REAL, beta_before REAL,
            alpha_after REAL, beta_after REAL,
            mean_before REAL, mean_after REAL,
            tier_before TEXT, tier_after TEXT,
            source TEXT, details TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS autonomy_actions (
            id TEXT PRIMARY KEY,
            category TEXT NOT NULL,
            action_type TEXT NOT NULL,
            decision TEXT NOT NULL,
            trust_mean REAL, thompson_sample REAL,
            tier_required TEXT, tier_current TEXT,
            details TEXT, coherence_passed INTEGER,
            remediation_applied INTEGER, outcome TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS autonomy_behavior_log (
            id TEXT PRIMARY KEY,
            signal_type TEXT NOT NULL,
            finding_id TEXT, pr_url TEXT,
            alpha_delta REAL DEFAULT 0.0,
            beta_delta REAL DEFAULT 0.0,
            category TEXT, details TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
    """)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------
class TestAutonomyConfig:
    def test_config_file_exists(self):
        assert (BASE_DIR / "args" / "autonomy_config.yaml").exists()

    def test_config_has_required_sections(self):
        try:
            import yaml
        except ImportError:
            return
        with open(BASE_DIR / "args" / "autonomy_config.yaml", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        assert "never_autonomous" in config
        assert "tiers" in config
        assert "priors" in config
        assert "kill_switch" in config
        assert "thompson_sampling" in config
        assert "federation" in config

    def test_nine_safety_invariants(self):
        try:
            import yaml
        except ImportError:
            return
        with open(BASE_DIR / "args" / "autonomy_config.yaml", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        invariants = config["never_autonomous"]
        assert len(invariants) == 9
        assert "delete_vcs_repos" in invariants
        assert "delete_backups" in invariants
        assert "delete_production_data" in invariants

    def test_five_trust_tiers(self):
        try:
            import yaml
        except ImportError:
            return
        with open(BASE_DIR / "args" / "autonomy_config.yaml", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        tiers = config["tiers"]
        assert "observer" in tiers
        assert "contributor" in tiers
        assert "builder" in tiers
        assert "architect" in tiers
        assert "creator" in tiers

    def test_eight_prior_categories(self):
        try:
            import yaml
        except ImportError:
            return
        with open(BASE_DIR / "args" / "autonomy_config.yaml", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        priors = config["priors"]
        assert len(priors) == 8
        for name, prior in priors.items():
            assert "alpha" in prior, f"{name} missing alpha"
            assert "beta" in prior, f"{name} missing beta"
            assert "ceiling" in prior, f"{name} missing ceiling"


# ---------------------------------------------------------------------------
# Trust Engine tests
# ---------------------------------------------------------------------------
class TestTrustEngine:
    def test_get_trust_state_initializes(self, tmp_path):
        db_path = tmp_path / "test.db"
        _create_autonomy_db(db_path).close()

        def _conn():
            return _tconnect(db_path)

        with patch("tools.autonomy.trust_engine._get_connection", side_effect=_conn):
            from tools.autonomy.trust_engine import get_trust_state

            state = get_trust_state("auto_fix")
            assert state["category"] == "auto_fix"
            assert state["alpha"] == 5.0  # From config
            assert state["beta"] == 5.0
            assert "mean" in state
            assert state["mean"] == 0.5

    def test_observe_success_increases_alpha(self, tmp_path):
        db_path = tmp_path / "test.db"
        _create_autonomy_db(db_path).close()

        def _conn():
            return _tconnect(db_path)

        with patch("tools.autonomy.trust_engine._get_connection", side_effect=_conn):
            from tools.autonomy.trust_engine import get_trust_state, observe

            # Initialize
            get_trust_state("auto_fix")
            # Observe success
            result = observe("auto_fix", success=True, source="test")
            assert result["outcome"] == "success"
            assert result["mean_after"] > result["mean_before"]

    def test_observe_failure_increases_beta(self, tmp_path):
        db_path = tmp_path / "test.db"
        _create_autonomy_db(db_path).close()

        def _conn():
            return _tconnect(db_path)

        with patch("tools.autonomy.trust_engine._get_connection", side_effect=_conn):
            from tools.autonomy.trust_engine import get_trust_state, observe

            get_trust_state("auto_fix")
            result = observe("auto_fix", success=False, source="test")
            assert result["outcome"] == "failure"
            assert result["mean_after"] < result["mean_before"]

    def test_ceiling_enforced(self, tmp_path):
        db_path = tmp_path / "test.db"
        _create_autonomy_db(db_path).close()

        def _conn():
            return _tconnect(db_path)

        with patch("tools.autonomy.trust_engine._get_connection", side_effect=_conn):
            from tools.autonomy.trust_engine import get_trust_state, observe

            get_trust_state("create_tool")
            # 50 successes should approach but not exceed ceiling (0.90)
            for _ in range(50):
                result = observe("create_tool", success=True, source="test")
            assert result["mean_after"] <= 0.90

    def test_safety_invariant_blocks_delete_repo(self):
        from tools.autonomy.trust_engine import check_safety_invariant

        is_safe, reason = check_safety_invariant("gh repo delete my-repo")
        assert is_safe is False
        assert "delete_vcs_repos" in reason

    def test_safety_invariant_blocks_delete_backup(self):
        from tools.autonomy.trust_engine import check_safety_invariant

        is_safe, reason = check_safety_invariant("rm backup directory")
        assert is_safe is False
        assert "delete_backups" in reason

    def test_safety_invariant_allows_normal_action(self):
        from tools.autonomy.trust_engine import check_safety_invariant

        is_safe, reason = check_safety_invariant("create new tool module for parsing")
        assert is_safe is True

    def test_safety_invariant_blocks_drop_table(self):
        from tools.autonomy.trust_engine import check_safety_invariant

        is_safe, reason = check_safety_invariant("drop table audit_trail")
        assert is_safe is False


# ---------------------------------------------------------------------------
# Kill Switch tests
# ---------------------------------------------------------------------------
class TestKillSwitch:
    def test_not_killed_by_default(self):
        import os

        os.environ.pop("ICDEV_AUTONOMY_KILL", None)
        with patch("tools.autonomy.kill_switch.LOCKFILE", Path("/nonexistent/path")):
            from tools.autonomy.kill_switch import is_killed

            assert is_killed() is False

    def test_killed_by_env_var(self):
        import os

        os.environ["ICDEV_AUTONOMY_KILL"] = "true"
        try:
            from tools.autonomy.kill_switch import is_killed

            assert is_killed() is True
        finally:
            os.environ.pop("ICDEV_AUTONOMY_KILL", None)

    def test_killed_by_lockfile(self, tmp_path):
        lockfile = tmp_path / "kill.lock"
        lockfile.write_text('{"activated_at": "now"}', encoding="utf-8")
        with patch("tools.autonomy.kill_switch.LOCKFILE", lockfile):
            from tools.autonomy.kill_switch import is_killed

            assert is_killed() is True

    def test_activate_deactivate(self, tmp_path):
        lockfile = tmp_path / "kill.lock"
        with patch("tools.autonomy.kill_switch.LOCKFILE", lockfile):
            from tools.autonomy.kill_switch import activate, deactivate

            activate(reason="test")
            assert lockfile.exists()
            deactivate()
            assert not lockfile.exists()

    def test_status(self, tmp_path):
        lockfile = tmp_path / "kill.lock"
        with patch("tools.autonomy.kill_switch.LOCKFILE", lockfile):
            from tools.autonomy.kill_switch import status

            s = status()
            assert "killed" in s
            assert "env_var_active" in s
            assert "lockfile_active" in s


# ---------------------------------------------------------------------------
# Self-Evolution tests
# ---------------------------------------------------------------------------
class TestSelfEvolve:
    def test_evolve_blocked_by_kill_switch(self):
        import os

        os.environ["ICDEV_AUTONOMY_KILL"] = "true"
        try:
            from tools.autonomy.self_evolve import evolve_action

            result = evolve_action("create_tool", lambda: {}, "test action")
            assert result["final_status"] == "killed"
        finally:
            os.environ.pop("ICDEV_AUTONOMY_KILL", None)

    def test_evolve_blocked_by_safety_invariant(self, tmp_path):
        import os

        os.environ.pop("ICDEV_AUTONOMY_KILL", None)
        db_path = tmp_path / "test.db"
        _create_autonomy_db(db_path).close()

        def _conn():
            return _tconnect(db_path)

        with (
            patch("tools.autonomy.trust_engine._get_connection", side_effect=_conn),
            patch("tools.autonomy.kill_switch.LOCKFILE", Path("/nonexistent")),
        ):
            from tools.autonomy.self_evolve import evolve_action

            result = evolve_action("create_tool", lambda: {}, "delete backup files")
            assert result["final_status"] == "safety_blocked"


# ---------------------------------------------------------------------------
# Append-only protection tests
# ---------------------------------------------------------------------------
class TestAppendOnlyProtection:
    def test_observations_protected(self):
        content = (BASE_DIR / ".claude" / "hooks" / "pre_tool_use.py").read_text(encoding="utf-8")
        assert "autonomy_observations" in content

    def test_actions_protected(self):
        content = (BASE_DIR / ".claude" / "hooks" / "pre_tool_use.py").read_text(encoding="utf-8")
        assert "autonomy_actions" in content

    def test_behavior_log_protected(self):
        content = (BASE_DIR / ".claude" / "hooks" / "pre_tool_use.py").read_text(encoding="utf-8")
        assert "autonomy_behavior_log" in content


# ---------------------------------------------------------------------------
# Security gate test
# ---------------------------------------------------------------------------
class TestSecurityGate:
    def test_autonomy_gate_exists(self):
        try:
            import yaml
        except ImportError:
            return
        with open(BASE_DIR / "args" / "security_gates.yaml", encoding="utf-8") as f:
            gates = yaml.safe_load(f)
        assert "autonomy" in gates
        assert "blocking" in gates["autonomy"]
        assert "safety_invariant_violated" in gates["autonomy"]["blocking"]
