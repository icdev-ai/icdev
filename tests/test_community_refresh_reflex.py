# CUI // SP-CTI
"""The community refresh reflex keeps DIC GraphRAG summaries fresh — and is wired.

A Genesis reflex only runs if it is registered at all three points (module +
REFLEX_NAMES + genesis_config.yaml). Miss one and it silently never fires. It
must also never crash the daemon, and delegate to the idempotent build engine.
"""
from __future__ import annotations

import importlib
from pathlib import Path
from unittest.mock import patch

import yaml

from tools.genesis.reflexes import community_refresh as cr

REPO_ROOT = Path(__file__).resolve().parents[1]

# tools.* is a shim over icdev.tools.*; importlib resolves the real submodule.
_daemon = importlib.import_module("tools.genesis.daemon")


class TestRegistration:
    """Three points or it silently never runs."""

    def test_in_daemon_reflex_names(self):
        assert "community_refresh" in _daemon.REFLEX_NAMES

    def test_in_genesis_config(self):
        cfg = yaml.safe_load((REPO_ROOT / "args" / "genesis_config.yaml").read_text(encoding="utf-8"))
        reflexes = cfg.get("reflexes") or cfg
        assert "community_refresh" in reflexes
        assert reflexes["community_refresh"]["enabled"] is True

    def test_module_exposes_dispatch_contract(self):
        assert callable(cr.run)


class TestReflexRun:
    def test_delegates_to_build_communities(self):
        with patch.object(cr, "run", wraps=cr.run), \
             patch("tools.knowledge_graph.community_engine.build_communities",
                   return_value={"graphs": 2, "communities": 7, "skipped_small": 1}) as mock_build, \
             patch("tools.db.storage.get_connection", return_value=object()):
            out = cr.run({})
        assert out["success"] is True
        assert out["metric_value"] == 7.0
        assert out["details"]["communities"] == 7
        assert mock_build.called

    def test_dry_run_writes_nothing(self):
        with patch("tools.knowledge_graph.community_engine.build_communities") as mock_build, \
             patch("tools.knowledge_graph.community_engine._dic_graph_ids", return_value={"g1", "g2"}), \
             patch("tools.db.storage.get_connection", return_value=object()):
            out = cr.run({"dry_run": True})
        assert out["success"] is True
        assert out["details"]["graphs_available"] == 2
        assert not mock_build.called

    def test_failure_is_reported_not_raised(self):
        """A reflex must never crash the daemon."""
        with patch("tools.knowledge_graph.community_engine.build_communities",
                   side_effect=RuntimeError("kg down")), \
             patch("tools.db.storage.get_connection", return_value=object()):
            out = cr.run({})
        assert out["success"] is False
        assert "kg down" in out["details"]["error"]
