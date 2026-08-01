# CUI // SP-CTI
"""P2 hardening tests for the Agentic AI Canvas (penta-aadc-07).

Covers the batch of P2 fixes:
  * gate-data cache — _load_all_gate_data memoizes the five-engine fan-out per
    (design_id, updated_at) and is evicted on save/delete.
  * IL mapping — canvas_bridge derives the DoD Impact Level from the design's
    'classification' marking (not a nonexistent 'il_level' key), target_il wins.
  * ops_config output — generated YAML is written under data/, never args/.
  * bridge-unavailable marker — assess_design tags its output when the AIMC
    bridge could not run, instead of silently dropping the bridge findings.

All DB access goes through the canvas layer (tools/agentic_ai_canvas/db/init_db)
pinned to a temp SQLite file; runtime is PostgreSQL, SQLite is the conftest-
forced backend only.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from _aadc_canvas import canvas_db as _canvas_db  # noqa: E402

# Re-export so pytest collects the shared fixture from this module. Bound by
# assignment rather than imported under its own name so the test signatures
# below are not each flagged as redefining an import (F811).
canvas_db = _canvas_db


def _insert_design(initdb, did="aadc-p2", classification="CUI", updated_at="2026-01-01T00:00:00"):
    conn = initdb.get_connection()
    try:
        conn.execute(
            "INSERT INTO aadc_designs (id, name, classification, graph_json, updated_at) "
            "VALUES (%s,%s,%s,%s,%s)",
            (did, "P2 Design", classification, '{"nodes":[],"edges":[]}', updated_at),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# (1) Gate-data cache
# ---------------------------------------------------------------------------

class TestGateDataCache:
    @pytest.fixture
    def bp_with_stubs(self, canvas_db, monkeypatch):
        """Blueprint with the five analysis engines stubbed to cheap counters."""
        bp = importlib.import_module("tools.agentic_ai_canvas.blueprint")
        # Fresh cache per test.
        bp._GATE_DATA_CACHE.clear()
        bp._GATE_DATA_CACHE_AT.clear()

        calls = {"ato": 0}
        for modname, fn in (
            ("tools.agentic_ai_canvas.ato_readiness", "run_ato_checklist"),
            ("tools.agentic_ai_canvas.regulatory_tracker", "run_regulatory_analysis"),
            ("tools.agentic_ai_canvas.red_team", "run_red_team"),
            ("tools.agentic_ai_canvas.auto_recommend", "lint_design"),
            ("tools.agentic_ai_canvas.impact_analyzer", "analyze_impact"),
        ):
            mod = importlib.import_module(modname)
            if fn == "run_ato_checklist":
                def _ato(*a, **k):
                    calls["ato"] += 1
                    return {}
                monkeypatch.setattr(mod, fn, _ato, raising=True)
            else:
                monkeypatch.setattr(mod, fn, lambda *a, **k: {}, raising=True)
        return bp, calls

    def test_second_call_is_cache_hit(self, bp_with_stubs, canvas_db):
        bp, calls = bp_with_stubs
        _insert_design(canvas_db)
        conn = canvas_db.get_connection()
        try:
            bp._load_all_gate_data(conn, "aadc-p2")
            bp._load_all_gate_data(conn, "aadc-p2")
        finally:
            conn.close()
        assert calls["ato"] == 1, "engines re-ran on a cache hit"

    def test_invalidation_forces_recompute(self, bp_with_stubs, canvas_db):
        bp, calls = bp_with_stubs
        _insert_design(canvas_db)
        conn = canvas_db.get_connection()
        try:
            bp._load_all_gate_data(conn, "aadc-p2")
            bp._invalidate_gate_cache("aadc-p2")
            bp._load_all_gate_data(conn, "aadc-p2")
        finally:
            conn.close()
        assert calls["ato"] == 2, "invalidation did not force a recompute"

    def test_updated_at_change_is_cache_miss(self, bp_with_stubs, canvas_db):
        bp, calls = bp_with_stubs
        _insert_design(canvas_db)
        conn = canvas_db.get_connection()
        try:
            bp._load_all_gate_data(conn, "aadc-p2")
            conn.execute("UPDATE aadc_designs SET updated_at=%s WHERE id=%s",
                         ("2026-02-02T00:00:00", "aadc-p2"))
            conn.commit()
            bp._load_all_gate_data(conn, "aadc-p2")
        finally:
            conn.close()
        assert calls["ato"] == 2, "changed updated_at should miss the cache"

    def test_missing_design_returns_none_tuple(self, bp_with_stubs, canvas_db):
        bp, _calls = bp_with_stubs
        conn = canvas_db.get_connection()
        try:
            result = bp._load_all_gate_data(conn, "does-not-exist")
        finally:
            conn.close()
        assert result[0] is None


# ---------------------------------------------------------------------------
# (4) IL mapping from classification
# ---------------------------------------------------------------------------

class TestIlMapping:
    @pytest.mark.parametrize("classification,expected", [
        ("CUI", "IL4"),
        ("CUI // SP-CTI", "IL4"),
        ("cui", "IL4"),
        ("UNCLASSIFIED", "IL2"),
        ("SECRET", "IL6"),
        ("TOP SECRET", "IL6"),
        ("IL5", "IL5"),          # already an IL — passthrough
        ("something-weird", "IL4"),  # unknown → default IL4
        (None, "IL4"),
        ("", "IL4"),
    ])
    def test_classification_to_il(self, classification, expected):
        cb = importlib.import_module("tools.agentic_ai_canvas.canvas_bridge")
        assert cb._classification_to_il(classification) == expected

    def test_secret_design_derives_il6_and_flags_il4_only_model(self, canvas_db, monkeypatch):
        cb = importlib.import_module("tools.agentic_ai_canvas.canvas_bridge")
        _insert_design(canvas_db, did="aadc-sec", classification="SECRET")
        # Link an IL4-only model to a node.
        conn = canvas_db.get_connection()
        try:
            conn.execute(
                "INSERT INTO aadc_aimc_model_refs "
                "(id, aadc_design_id, aadc_node_id, aimc_model_id, created_at) "
                "VALUES (%s,%s,%s,%s,%s)",
                ("ref1", "aadc-sec", "node1", "model-il4", "2026-01-01T00:00:00"),
            )
            conn.commit()
        finally:
            conn.close()
        monkeypatch.setattr(cb, "_model_by_id", lambda mid: {
            "name": "IL4 Model", "provider": "openai", "il_suitability": [4],
            "air_gap_ready": False,
        }, raising=True)

        violations = cb.check_il_compatibility("aadc-sec")
        assert violations, "SECRET (IL6) design with an IL4-only model must violate"
        text = " ".join(str(v.get("issues", v)) for v in violations)
        assert "IL6" in text

    def test_target_il_override_wins(self, canvas_db, monkeypatch):
        cb = importlib.import_module("tools.agentic_ai_canvas.canvas_bridge")
        _insert_design(canvas_db, did="aadc-ovr", classification="SECRET")
        conn = canvas_db.get_connection()
        try:
            conn.execute(
                "INSERT INTO aadc_aimc_model_refs "
                "(id, aadc_design_id, aadc_node_id, aimc_model_id, created_at) "
                "VALUES (%s,%s,%s,%s,%s)",
                ("ref2", "aadc-ovr", "node1", "model-il4", "2026-01-01T00:00:00"),
            )
            conn.commit()
        finally:
            conn.close()
        # A model that fully satisfies IL4 (approved provider, air-gap ready,
        # suitable) but would fail at the design's SECRET/IL6 marking.
        monkeypatch.setattr(cb, "_model_by_id", lambda mid: {
            "name": "IL4 Model", "provider": "AWS Bedrock", "il_suitability": [4],
            "air_gap_ready": True,
        }, raising=True)
        # Without override the SECRET design (IL6) would flag this model.
        assert cb.check_il_compatibility("aadc-ovr"), "IL6 should flag an IL4-only model"
        # Override to IL4 — the IL4 model now satisfies the target.
        violations = cb.check_il_compatibility("aadc-ovr", target_il="IL4")
        assert violations == [], "target_il=IL4 override should clear the IL mismatch"


# ---------------------------------------------------------------------------
# (5) ops_config output location — data/, never args/
# ---------------------------------------------------------------------------

class TestOpsConfigOutput:
    def test_writes_under_output_dir_not_args(self, canvas_db, tmp_path, monkeypatch):
        gen = importlib.import_module("tools.agentic_ai_canvas.ops_config_generator")
        out_dir = tmp_path / "ops_out"
        monkeypatch.setattr(gen, "_OUTPUT_DIR", out_dir, raising=True)
        _insert_design(canvas_db, did="aadc-ops")

        result = gen.generate_ops_config("aadc-ops")
        cfg_path = result["config_path"]
        assert cfg_path is not None
        p = Path(cfg_path)
        assert p.exists()
        assert p.parent == out_dir
        # Never inside a source-controlled args/ directory.
        assert "args" not in p.parts[:-1] or p.parts[-2] == "ops_out"
        assert p.name == "ops_config_aadc-ops.yaml"

    def test_env_override_dir(self, canvas_db, tmp_path, monkeypatch):
        monkeypatch.setenv("AADC_OPS_CONFIG_DIR", str(tmp_path / "env_out"))
        # Re-import so the module-level _OUTPUT_DIR picks up the env var.
        import tools.agentic_ai_canvas.ops_config_generator as gen
        gen = importlib.reload(gen)
        _insert_design(canvas_db, did="aadc-env")
        result = gen.generate_ops_config("aadc-env")
        assert result["config_path"] is not None
        assert str(tmp_path / "env_out") in result["config_path"]
        importlib.reload(gen)  # restore default for other tests


# ---------------------------------------------------------------------------
# (6) bridge-unavailable marker
# ---------------------------------------------------------------------------

class TestBridgeMarker:
    def test_marker_available_when_bridge_runs(self, monkeypatch):
        eng = importlib.import_module("tools.agentic_ai_canvas.agentic_engine")
        cb = importlib.import_module("tools.agentic_ai_canvas.canvas_bridge")
        monkeypatch.setattr(cb, "check_il_compatibility", lambda *a, **k: [], raising=True)
        monkeypatch.setattr(cb, "bridge_security_check", lambda *a, **k: [], raising=True)
        result = eng.assess_design("d", {"nodes": [], "edges": []}, {})
        assert result["bridge"] == "available"

    def test_marker_unavailable_when_bridge_raises(self, monkeypatch):
        eng = importlib.import_module("tools.agentic_ai_canvas.agentic_engine")
        cb = importlib.import_module("tools.agentic_ai_canvas.canvas_bridge")

        def _boom(*a, **k):
            raise RuntimeError("bridge down")
        monkeypatch.setattr(cb, "check_il_compatibility", _boom, raising=True)
        result = eng.assess_design("d", {"nodes": [], "edges": []}, {})
        assert result["bridge"] == "unavailable"
