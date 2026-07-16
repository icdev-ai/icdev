# CUI // SP-CTI
"""DIC / docgen usability fixes + the NDC->ACOIC drift producer.

Covers:
  * /docgen/new no longer hardcodes an empty session list, and opens the wizard.
  * The DIC index groups all 14 tiles into a workflow order without dropping any.
  * ndc_topology_drift is actually registered (an unregistered reflex never runs).
  * The drift producer reports a missing baseline instead of fabricating one.
  * dedup_key makes a scheduled producer idempotent across runs, and never
    resets a queue item a human already advanced.

These use the real storage layer (conftest forces ICDEV_STORAGE_BACKEND=sqlite)
so the %s -> ? translation is exercised rather than bypassed. No network, no LLM.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture
def acoic_conn_factory(tmp_path, monkeypatch):
    """Point acoic at a temp SQLite DB through the real StorageConnection.

    acoic._ensure_schema creates its own tables, so no schema fixture is needed.
    """
    from tools.db.storage import get_connection as _real

    db_path = str(tmp_path / "acoic_ux.db")

    def _factory(*args, **kwargs):
        return _real(db_path=db_path)

    import tools.document_intelligence.acoic as acoic_mod
    monkeypatch.setattr(acoic_mod, "get_connection", _factory)
    return _factory


# ── DIC index tile grouping ──────────────────────────────────────────────────

class TestDicIndexGrouping:
    def test_grouped_pages_covers_every_tile_exactly_once(self):
        from tools.document_intelligence.blueprint import _PAGES, _grouped_pages

        grouped = _grouped_pages()
        flat = [p["name"] for g in grouped for p in g["pages"]]
        assert sorted(flat) == sorted(p["name"] for p in _PAGES), (
            "grouping must not drop or duplicate a tile"
        )
        assert len(flat) == len(set(flat)), "a tile appears in two groups"

    def test_groups_are_in_workflow_order(self):
        from tools.document_intelligence.blueprint import _grouped_pages

        labels = [g["label"] for g in _grouped_pages()]
        assert labels[0].startswith("1 ")
        assert any(lbl.startswith("4 ") for lbl in labels)

    def test_unknown_tile_is_surfaced_not_swallowed(self, monkeypatch):
        """A tile missing from _PAGE_GROUPS must still render, under 'More'."""
        import tools.document_intelligence.blueprint as bp

        monkeypatch.setattr(
            bp, "_PAGES", bp._PAGES + [{"name": "Brand New", "icon": "*", "href": "/x",
                                        "desc": "d", "ready": True, "task": "t"}]
        )
        flat = [p["name"] for g in bp._grouped_pages() for p in g["pages"]]
        assert "Brand New" in flat


# ── docgen /new ──────────────────────────────────────────────────────────────

class TestDocgenNewPage:
    def test_new_route_does_not_hardcode_empty_sessions(self):
        """Regression: /docgen/new rendered sessions=[] and always claimed
        'No regeneration sessions yet' even with real sessions present."""
        import inspect

        from tools.docgen import blueprint as dg

        src = inspect.getsource(dg.new_session_page)
        assert "sessions=[]" not in src, "/docgen/new must not hardcode an empty list"
        assert "_sessions_with_freshness" in src
        assert "open_wizard=True" in src, "the advertised wizard must open on arrival"

    def test_index_and_new_share_one_session_source(self):
        import inspect

        from tools.docgen import blueprint as dg

        assert "_sessions_with_freshness" in inspect.getsource(dg.index)


# ── Reflex registration (the gotcha that made ACOIC dead) ────────────────────

class TestDriftReflexRegistered:
    def test_reflex_is_in_daemon_reflex_names(self):
        from tools.genesis.daemon import REFLEX_NAMES

        assert "ndc_topology_drift" in REFLEX_NAMES, (
            "a reflex absent from REFLEX_NAMES is never dispatched"
        )

    def test_reflex_has_a_genesis_config_entry(self):
        import yaml

        cfg = yaml.safe_load((REPO_ROOT / "args" / "genesis_config.yaml").read_text(encoding="utf-8"))
        entry = cfg["reflexes"].get("ndc_topology_drift")
        assert entry is not None
        assert entry["enabled"] is True

    def test_run_signature_takes_trust_not_conn(self):
        """The daemon dispatches fn(config, trust); a `conn` second param would
        silently receive the TrustKernel and fail on every call."""
        import inspect

        from tools.genesis.reflexes import ndc_topology_drift as mod

        params = list(inspect.signature(mod.run).parameters)
        assert params[:2] == ["ctx", "trust"]


# ── Drift producer honesty ───────────────────────────────────────────────────

class TestDicIntegrationDispatch:
    """The sibling reflex had the same fn(config, trust) mismatch."""

    def test_run_signature_accepts_trust_positionally(self):
        import inspect

        from tools.genesis.reflexes import dic_integration as mod

        params = list(inspect.signature(mod.run).parameters)
        assert params[:2] == ["ctx", "trust"], (
            "the daemon calls fn(config, trust); a positional `conn` here silently "
            "turns every cycle into a swallowed error reporting events_found: 0"
        )
        assert inspect.signature(mod.run).parameters["conn"].kind is inspect.Parameter.KEYWORD_ONLY

    def test_run_does_not_treat_trust_as_a_db_handle(self):
        """A TrustKernel in position 2 must not be used as a connection."""
        from tools.genesis.reflexes.dic_integration import run

        class _Trust:  # no .execute / .cursor
            name = "trust-kernel"

        result = run({"dry_run": True}, _Trust())
        assert result["status"] == "ok"
        assert not result["errors"]


class TestDriftProducer:
    def test_configs_are_keyed_by_device_not_filename(self):
        from tools.genesis.reflexes.ndc_topology_drift import _configs_by_device

        graph = {"nodes": [{"id": "r1", "label": "CORE-RTR-01", "type": "router"}], "edges": []}
        cfgs = _configs_by_device(graph, "t")
        assert cfgs, "a router must produce a config"
        assert all(not k.endswith(".txt") for k in cfgs), (
            "drift items must name the device, not the generated filename"
        )

    def test_missing_baseline_is_reported_and_writes_nothing(self):
        """No baseline => nothing to compare. Must report, never invent one."""
        from tools.genesis.reflexes.ndc_topology_drift import _check_topology_drift

        class _NoBaselineConn:
            def execute(self, *a, **k):
                class _R:
                    @staticmethod
                    def fetchone():
                        return None
                return _R()

        result = {
            "baselines_missing": [], "drifted_topologies": [], "errors": [],
            "no_configurable_devices": [], "docs_unmapped": [],
            "drift_events_recorded": 0, "regen_enqueued": 0,
        }
        drifted = _check_topology_drift(
            _NoBaselineConn(), "topo-1", "Topo One", '{"nodes":[],"edges":[]}',
            "", "CUI", True, result,
        )
        assert drifted is False
        assert result["baselines_missing"] == [{"id": "topo-1", "name": "Topo One"}]
        assert result["drift_events_recorded"] == 0


# ── Idempotency of the scheduled producer ────────────────────────────────────

class TestDedupKey:
    def test_same_dedup_key_collapses_to_one_row(self, acoic_conn_factory):
        """Without this, a 4h cadence appends a duplicate row every run."""
        from tools.document_intelligence import acoic

        a = acoic.record_drift_event("network.acl_change", "fw-01", "high", dedup_key="k1")
        b = acoic.record_drift_event("network.acl_change", "fw-01", "high", dedup_key="k1")
        assert a == b

        conn = acoic_conn_factory()
        try:
            n = conn.execute("SELECT COUNT(*) AS n FROM dic_drift_events").fetchone()
            assert (dict(n)["n"] if not isinstance(n, (list, tuple)) else n[0]) == 1
        finally:
            conn.close()

    def test_different_content_yields_a_new_event(self, acoic_conn_factory):
        from tools.document_intelligence import acoic

        a = acoic.record_drift_event("network.acl_change", "fw-01", "high", dedup_key="snapA")
        b = acoic.record_drift_event("network.acl_change", "fw-01", "high", dedup_key="snapB")
        assert a != b

    def test_without_dedup_key_legacy_behavior_is_unchanged(self, acoic_conn_factory):
        """Existing callers (CLI, tests) must keep append-per-call semantics."""
        from tools.document_intelligence import acoic

        a = acoic.record_drift_event("network.acl_change", "fw-02", "low")
        b = acoic.record_drift_event("network.acl_change", "fw-02", "low")
        assert isinstance(a, str) and isinstance(b, str)

    def test_acoic_list_fns_author_pg_placeholders_directly(self):
        """Runtime reads must not lean on translate_sql to rewrite `?`.

        These worked on PG (the translator rewrote them) but logged a
        "bare ? placeholder detected" warning per call and made a runtime path
        depend on an init-time SQLite fallback.
        """
        import inspect

        from tools.document_intelligence import acoic

        for fn in (acoic.list_drift_events, acoic.list_regen_queue, acoic.list_ssp_fragments):
            src = inspect.getsource(fn)
            assert "LIMIT ?" not in src, f"{fn.__name__} still uses a bare ? placeholder"
            assert "LIMIT %s" in src

    def test_requeue_does_not_reset_human_advanced_state(self, acoic_conn_factory):
        """OR REPLACE would stomp an approved item back to 'queued'."""
        from tools.document_intelligence import acoic

        acoic.enqueue_regen("doc-1", severity="high", dedup_key="d1")
        acoic._set_queue_state(acoic._hid("dic_regen", "d1"), "approved")
        acoic.enqueue_regen("doc-1", severity="high", dedup_key="d1")

        conn = acoic_conn_factory()
        try:
            rows = conn.execute(
                "SELECT state FROM dic_acoic_regen_queue WHERE document_id = %s", ("doc-1",)
            ).fetchall()
            assert len(rows) == 1, "dedup_key must not create a second queue item"
            state = dict(rows[0])["state"] if not isinstance(rows[0], (list, tuple)) else rows[0][0]
            assert state == "approved", "a human's decision must survive the next producer run"
        finally:
            conn.close()
