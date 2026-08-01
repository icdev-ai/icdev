# CUI // SP-CTI
"""Graph-driven compliance/governance tests for AADC (penta-aadc-02).

Before this fix, tools/aadc/compliance_checker.py and governance_scanner.py
computed the 'AI Compliance Report' PASS/FAIL and 'AI Risk Score' from hardcoded
_DEFAULT_CHECKS / _DEFAULT_POSTURE, never read the design graph, and failed open.
They are now thin adapters over agentic_engine.assess_design.

These tests prove:
  - the fabricated defaults are gone;
  - a crafted risky design yields findings/scores that differ from a safe one
    (i.e. the verdict tracks the design graph);
  - mutating a design's graph changes its verdict;
  - a missing design yields an explicit AssessmentUnavailable (no fabrication);
  - the modules read aadc_* via the canvas connection (get_canvas_connection on
    PG), not the RLS-enforcing tools.db.storage.get_connection.

The canvas aadc_* tables live in a separate store; the shared ``canvas_db``
fixture (tests/_aadc_canvas.py) pins that store at a temp SQLite file by
patching init_db's ``_BACKEND``/``DB_PATH`` rather than replacing its
``get_connection`` — see that module for why the difference leaks across files.
"""
from __future__ import annotations

import importlib
import json
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

import tools.aadc.compliance_checker as cc  # noqa: E402
import tools.aadc.governance_scanner as gs  # noqa: E402
import tools.agentic_ai_canvas.db.init_db as canvas_init_db  # noqa: E402

# The real connection factory, captured before any fixture can swap it — the
# reference point for TestFixtureDoesNotLeakAcrossFiles below.
_PRISTINE_GET_CONNECTION = canvas_init_db.get_connection

# --- Crafted designs -------------------------------------------------------
# Risky: a single unconstrained autonomous agent (no HITL / circuit-breaker /
# confidence gate) -> agentic_engine classifies it autonomy L5 -> CRITICAL.
RISKY_GRAPH = {
    "nodes": [
        {"id": "in1", "type": "inference-input", "label": "Task Input"},
        {"id": "a1", "type": "autonomous-agent", "label": "Rogue Agent"},
    ],
    "edges": [
        {"id": "e1", "source": "in1", "target": "a1"},
    ],
}

# Safe: same agent but wrapped in confidence gate -> circuit breaker -> HITL
# gate -> audit + output validator. Autonomy drops to L1; no CRITICAL.
SAFE_GRAPH = {
    "nodes": [
        {"id": "in1", "type": "inference-input", "label": "Task Input"},
        {"id": "san", "type": "input-sanitizer", "label": "Sanitizer"},
        {"id": "a1", "type": "autonomous-agent", "label": "Agent"},
        {"id": "cf", "type": "confidence-threshold", "label": "Confidence Gate"},
        {"id": "cb", "type": "circuit-breaker", "label": "Circuit Breaker"},
        {"id": "hg", "type": "hitl-gate", "label": "HITL Gate"},
        {"id": "au", "type": "audit-logger", "label": "Audit Logger"},
        {"id": "ov", "type": "output-validator", "label": "Output Validator"},
        {"id": "gr", "type": "guardrail", "label": "Content Filter"},
    ],
    "edges": [
        {"id": "e1", "source": "in1", "target": "san"},
        {"id": "e2", "source": "san", "target": "a1"},
        {"id": "e3", "source": "a1", "target": "cf"},
        {"id": "e4", "source": "cf", "target": "cb"},
        {"id": "e5", "source": "cb", "target": "hg"},
        {"id": "e6", "source": "hg", "target": "au"},
        {"id": "e7", "source": "hg", "target": "gr"},
        {"id": "e8", "source": "gr", "target": "ov"},
    ],
}


def _write(sql, params):
    """Run one write against the canvas store, always releasing the connection.

    The ``finally`` is load-bearing, not style. These helpers previously closed
    on the happy path only, so any statement that raised — a duplicate design
    id, schema drift, a bad placeholder — skipped ``close()`` entirely:

    * On PostgreSQL ``get_connection`` hands back a ``_PooledPgConnection`` from
      the 20-slot pool, and only ``close()`` returns the slot. A connection
      abandoned mid-statement stays checked out AND idle-in-transaction holding
      ACCESS SHARE locks, so a handful of failing tests exhausts the pool for
      the rest of the session (the ``tools/db/storage.py`` lock-storm note).
    * On SQLite it strands a WAL handle on the fixture's ``tmp_path`` file,
      which on Windows blocks pytest from tearing the directory down.

    Either way the damage lands on some *later* test, not the one that failed.
    """
    conn = canvas_init_db.get_connection()
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def _insert_design(design_id, graph, *, domain="", safety=0, rights=0):
    _write(
        "INSERT INTO aadc_designs (id, name, domain, classification, graph_json, "
        "safety_impacting, rights_impacting) VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (design_id, f"design-{design_id}", domain, "CUI", json.dumps(graph), safety, rights),
    )


def _update_graph(design_id, graph):
    _write(
        "UPDATE aadc_designs SET graph_json=%s WHERE id=%s",
        (json.dumps(graph), design_id),
    )


class TestFabricatedDefaultsGone:
    def test_default_checks_removed(self):
        assert not hasattr(cc, "_DEFAULT_CHECKS"), "_DEFAULT_CHECKS must be gone"

    def test_default_posture_removed(self):
        assert not hasattr(gs, "_DEFAULT_POSTURE"), "_DEFAULT_POSTURE must be gone"

    def test_source_has_no_fabricated_defaults(self):
        # A plain grep for the fabricated tokens must come up empty (acceptance).
        cc_src = Path(cc.__file__).read_text(encoding="utf-8")
        gs_src = Path(gs.__file__).read_text(encoding="utf-8")
        assert "_DEFAULT_CHECKS" not in cc_src
        assert "_DEFAULT_POSTURE" not in gs_src
        # The old fabricated model list is gone from the governance scanner.
        assert "gpt-4" not in gs_src and "llama3" not in gs_src and "mistral" not in gs_src


class TestGraphDrivenGovernance:
    def test_risky_vs_safe_scores_differ(self, canvas_db):
        _insert_design("risky", RISKY_GRAPH)
        _insert_design("safe", SAFE_GRAPH)

        risky = gs.scan_governance("risky")
        safe = gs.scan_governance("safe")

        # The AI Risk Score is derived from the design; the safe design must
        # score strictly better (higher = less risk).
        assert safe["ai_risk_score"] > risky["ai_risk_score"]
        # Findings differ between the two designs.
        assert risky["missing_controls"] != safe["missing_controls"]
        # Inventory reflects the actual graph, not a fixed default of 3 agents.
        assert risky["posture"]["agent_count"] == 1
        assert risky["posture"]["node_count"] == 2

    def test_findings_change_when_graph_changes(self, canvas_db):
        _insert_design("d", RISKY_GRAPH)
        before = gs.scan_governance("d")
        _update_graph("d", SAFE_GRAPH)
        after = gs.scan_governance("d")
        assert before["ai_risk_score"] != after["ai_risk_score"]
        assert before["missing_controls"] != after["missing_controls"]


class TestGraphDrivenCompliance:
    def test_risky_design_fails_gate_with_critical(self, canvas_db):
        _insert_design("risky", RISKY_GRAPH)
        result = cc.run_compliance_checks("risky")
        assert result["gate"] == "FAIL"
        severities = {f.get("severity") for f in result["findings"]}
        assert "CRITICAL" in severities, "unconstrained L5 agent must be CRITICAL"

    def test_safe_design_has_no_critical(self, canvas_db):
        _insert_design("safe", SAFE_GRAPH)
        result = cc.run_compliance_checks("safe")
        severities = {f.get("severity") for f in result["findings"]}
        assert "CRITICAL" not in severities

    def test_compliance_findings_track_design(self, canvas_db):
        _insert_design("risky", RISKY_GRAPH)
        _insert_design("safe", SAFE_GRAPH)
        risky = cc.run_compliance_checks("risky")
        safe = cc.run_compliance_checks("safe")
        assert risky["findings"] != safe["findings"]
        assert risky["scores"]["overall"] != safe["scores"]["overall"]


class TestAssessmentUnavailable:
    def test_missing_design_governance_raises(self, canvas_db):
        with pytest.raises(gs.AssessmentUnavailable):
            gs.scan_governance("nonexistent")

    def test_missing_design_compliance_raises(self, canvas_db):
        with pytest.raises(cc.AssessmentUnavailable):
            cc.run_compliance_checks("nonexistent")

    def test_no_fabricated_fallback_on_missing(self, canvas_db):
        # The explicit-unavailable contract: no synthetic PASS / score is
        # returned when the design is absent.
        with pytest.raises(cc.AssessmentUnavailable):
            cc.run_compliance_checks("ghost")


class TestUsesCanvasConnection:
    def test_modules_use_canvas_connection_not_rls_get_connection(self):
        # RLS fix: aadc_* reads must go through the canvas connection factory
        # (which wraps get_canvas_connection on PG), not the RLS-enforcing
        # tools.db.storage.get_connection that raises UndefinedColumn on PG.
        for mod in (cc, gs):
            src = Path(mod.__file__).read_text(encoding="utf-8")
            assert "from tools.agentic_ai_canvas.db.init_db import get_connection" in src
            assert "from tools.db.storage import get_connection" not in src

    def test_ops_config_generator_uses_canvas_connection(self):
        # The aadc_designs read must use the canvas connection factory. (The
        # module also writes kanban_tasks — a main RLS table — via
        # tools.db.storage.get_connection, which is correct and left as-is.)
        import tools.agentic_ai_canvas.ops_config_generator as ocg
        src = Path(ocg.__file__).read_text(encoding="utf-8")
        assert "from tools.agentic_ai_canvas.db.init_db import get_connection" in src
        # The design read no longer sits under a storage.get_connection import.
        design_read_region = src.split("aadc_designs")[0].rsplit("def generate_ops_config", 1)[-1]
        assert "from tools.db.storage import get_connection" not in design_read_region


class TestHelpersReleaseConnections:
    """Regression guard for the unguarded ``close()`` in this file (aca-hyg-06-d4-d3).

    Nothing about the leak is visible in the test that triggers it — it already
    failed. It surfaces as an exhausted PG pool (or a locked ``tmp_path``) in
    whatever runs next, so the only place to catch it is right here.
    """

    def test_failed_write_still_releases_the_connection(self, canvas_db, monkeypatch):
        # Bind canvas_bridge's module-scope factory BEFORE patching, so the
        # lazy-import capture this file's docstring warns about cannot pick up
        # the recording wrapper below.
        importlib.import_module("tools.agentic_ai_canvas.canvas_bridge")

        opened = []
        real_get_connection = canvas_init_db.get_connection

        def _recording_get_connection():
            conn = real_get_connection()
            opened.append(conn)
            return conn

        monkeypatch.setattr(canvas_init_db, "get_connection", _recording_get_connection)

        _insert_design("dupe", SAFE_GRAPH)
        # Same primary key -> the INSERT raises inside the helper.
        with pytest.raises(Exception):
            _insert_design("dupe", SAFE_GRAPH)

        assert len(opened) == 2, "each helper call must open exactly one connection"
        assert all(getattr(c, "_closed", False) for c in opened), (
            "a helper that raised mid-write left its connection open — on PG that "
            "slot never returns to the pool"
        )


class TestFixtureDoesNotLeakAcrossFiles:
    """Regression guard for the third state leak in this suite (aca-hyg-06).

    ``scan_governance`` -> ``assess_design`` imports ``canvas_bridge`` lazily at
    run time, and ``canvas_bridge`` binds ``get_connection`` at module scope.
    When this file's fixture replaced ``init_db.get_connection`` with a closure,
    that first-import captured the closure permanently: monkeypatch restored
    ``init_db``, but ``canvas_bridge`` kept reading a deleted ``tmp_path`` DB,
    so ``test_penta_aadc_p2.py::TestIlMapping`` and one routes smoke test failed
    with "design not found" — but only when run after this file.
    """

    def test_lazy_canvas_bridge_import_binds_the_real_factory(self, canvas_db, monkeypatch):
        # Force the lazy import to happen under the fixture, as assess_design
        # does on a cold session. delitem (not pop) so sys.modules is restored.
        monkeypatch.delitem(sys.modules, "tools.agentic_ai_canvas.canvas_bridge", raising=False)
        _insert_design("leakguard", SAFE_GRAPH)
        gs.scan_governance("leakguard")

        bridge = sys.modules["tools.agentic_ai_canvas.canvas_bridge"]
        # Compare against the factory as it was at import time, NOT against
        # canvas_init_db.get_connection right now: under a fixture that swaps
        # the factory those two are the same object until teardown, and the
        # divergence only appears in the *next* file.
        assert bridge.get_connection is _PRISTINE_GET_CONNECTION, (
            "canvas_bridge captured a fixture-local connection factory; it will "
            "outlive this test and break every later file in the session"
        )
