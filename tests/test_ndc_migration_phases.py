# [CUI // SP-CTI]
"""Unit tests for tools/network/migration_phases.py (ndc-qa-01).

Covers the load-bearing public API of the migration phases engine:
  - compute_infoboxes / compute_final_infoboxes  (info-box aggregation)
  - generate_phase_graph / generate_final_graph   (phase overlay + apply)
  - generate_phase_physical_graph                 (2-hop physical w/ conn)
  - run_consolidation_analysis                    (consolidation metrics)
  - save_consolidation / load_consolidation       (DB round-trip)

Each function family exercises a happy path with seeded data, a malformed /
degenerate-input path, and an empty-graph (or empty-DB) path. No production
code is modified; DB persistence is routed to a temp SQLite connection via a
shim-aware monkeypatch on tools.db.storage.get_connection.
"""

import sqlite3

import pytest

from tools.db.storage import StorageConnection
from tools.network import migration_phases as mp


# ── Fixtures / helpers ─────────────────────────────────────────────────────────


class _NoCloseConn:
    """Delegates to a StorageConnection but neutralizes .close() so a shared
    in-memory SQLite DB survives the module's close-on-exit calls."""

    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, name):
        if name == "close":
            return lambda: None
        return getattr(self._conn, name)


def _mem_storage_conn():
    raw = sqlite3.connect(":memory:")
    raw.row_factory = sqlite3.Row
    return StorageConnection(raw, "sqlite")


def _seed_current_graph():
    return {
        "nodes": [
            {"id": "r1", "type": "core-router", "label": "CORE-1",
             "vendor": "Cisco",
             "meta": {"rack_units": 4, "power_watts": 400, "bgp_asn": 65001,
                      "ha_pair": True}},
            {"id": "sw1", "type": "access-switch", "label": "ACC-1",
             "meta": {"rack_units": 2, "power_watts": 200, "vlans": [10, 20]}},
            {"id": "fw1", "type": "palo-alto", "label": "FW-1",
             "meta": {"rack_units": 2, "power_watts": 250, "telnet_enabled": True,
                      "zone": "dmz"}},
        ],
        "edges": [
            {"id": "e1", "source": "r1", "target": "sw1", "type": "backbone",
             "bandwidth_gbps": 10, "utilization_pct": 85, "label": "uplink"},
            {"id": "e2", "source": "sw1", "target": "fw1", "type": "inside",
             "bandwidth_gbps": 1, "utilization_pct": 20},
        ],
    }


# ── compute_infoboxes ──────────────────────────────────────────────────────────


def test_compute_infoboxes_happy_path_returns_eight_boxes():
    graph = _seed_current_graph()
    boxes = mp.compute_infoboxes(graph, phase_key="current")

    assert isinstance(boxes, list)
    assert len(boxes) == 8
    ids = {b["id"] for b in boxes}
    assert "device-inventory" in ids
    assert "link-utilization" in ids

    inv = next(b for b in boxes if b["id"] == "device-inventory")
    total_row = next(r for r in inv["rows"] if r["label"] == "Total Devices")
    assert total_row["value"] == "3"
    # Every box must expose the render contract used by the template.
    for b in boxes:
        assert {"id", "title", "icon", "color", "rows"} <= set(b)


def test_compute_infoboxes_phase_specific_boxes_appended():
    graph = _seed_current_graph()
    phase_meta = {
        "phase_num": 1, "total_phases": 3, "title": "Cutover core",
        "risk_level": "HIGH", "changing_devices": ["r1"],
        "new_devices": [{"id": "r2"}], "retiring_devices": [],
        "port_mappings": [{"target": "ge-0/0/1"}],
    }
    boxes = mp.compute_infoboxes(graph, phase_key="phase-1", phase_meta=phase_meta)
    # 8 base + 4 phase-specific
    assert len(boxes) == 12
    ids = {b["id"] for b in boxes}
    assert {"phase-scope", "change-risk", "port-mapping", "validation-checklist"} <= ids


def test_compute_infoboxes_empty_graph():
    boxes = mp.compute_infoboxes({"nodes": [], "edges": []})
    assert len(boxes) == 8
    inv = next(b for b in boxes if b["id"] == "device-inventory")
    total_row = next(r for r in inv["rows"] if r["label"] == "Total Devices")
    assert total_row["value"] == "0"


def test_compute_infoboxes_malformed_graph_missing_keys():
    # Graph with no "nodes"/"edges" keys must not raise.
    boxes = mp.compute_infoboxes({})
    assert len(boxes) == 8
    link = next(b for b in boxes if b["id"] == "link-utilization")
    total_links = next(r for r in link["rows"] if r["label"] == "Total Links")
    assert total_links["value"] == "0"


def test_compute_final_infoboxes_adds_four_final_boxes():
    current = _seed_current_graph()
    final = {"nodes": current["nodes"][:2], "edges": current["edges"][:1]}
    consolidation = mp.run_consolidation_analysis(current, final)
    boxes = mp.compute_final_infoboxes(current, final, consolidation)
    ids = {b["id"] for b in boxes}
    assert {"consolidation-results", "tco-delta",
            "performance-gains", "compliance-improvement"} <= ids
    assert len(boxes) == 12  # 8 base (final graph) + 4 final-specific


# ── generate_phase_graph / generate_final_graph ────────────────────────────────


def test_generate_phase_graph_marks_states():
    current = _seed_current_graph()
    phase = {
        "changing_devices": ["r1"],
        "retiring_devices": ["fw1"],
        "new_devices": [{"id": "r2", "type": "core-router", "label": "CORE-2"}],
    }
    out = mp.generate_phase_graph(current, phase)
    states = {n["id"]: n.get("phase_state") for n in out["nodes"]}
    assert states["r1"] == "changing"
    assert states["fw1"] == "retiring"
    assert states["sw1"] == "existing"
    assert states["r2"] == "new"
    # New + retiring nodes get labels annotated.
    retiring = next(n for n in out["nodes"] if n["id"] == "fw1")
    assert "(retiring)" in retiring["label"]


def test_generate_phase_graph_empty_phase_all_existing():
    current = _seed_current_graph()
    out = mp.generate_phase_graph(current, {})
    assert all(n["phase_state"] == "existing" for n in out["nodes"])
    assert len(out["nodes"]) == 3


def test_generate_final_graph_applies_phases():
    current = _seed_current_graph()
    phases = [
        {"retiring_devices": ["fw1"],
         "new_devices": [{"id": "r2", "type": "core-router"}],
         "changing_devices": ["r1"],
         "device_updates": {"r1": {"label": "CORE-1-UPGRADED"}}},
    ]
    out = mp.generate_final_graph(current, phases)
    ids = {n["id"] for n in out["nodes"]}
    assert "fw1" not in ids       # retired
    assert "r2" in ids            # added
    r1 = next(n for n in out["nodes"] if n["id"] == "r1")
    assert r1["label"] == "CORE-1-UPGRADED"  # updated


def test_generate_final_graph_empty_inputs():
    out = mp.generate_final_graph({"nodes": [], "edges": []}, [])
    assert out == {"nodes": [], "edges": []}


# ── run_consolidation_analysis ─────────────────────────────────────────────────


def test_run_consolidation_analysis_computes_savings():
    current = _seed_current_graph()
    # Final removes fw1 (2 RU, 250 W).
    final = {"nodes": current["nodes"][:2], "edges": current["edges"][:1]}
    result = mp.run_consolidation_analysis(current, final)

    assert result["devices_removed"] == 1
    assert result["rack_units_freed"] == 2      # only fw1's 2 RU freed
    assert result["power_saved_watts"] == 250   # only fw1's 250 W freed
    # OpEx delta should be a saving (negative).
    assert result["opex_annual_delta"] < 0
    assert result["capex_delta"] == -5000       # 1 removed * -5000


def test_run_consolidation_analysis_no_change_zero_savings():
    current = _seed_current_graph()
    result = mp.run_consolidation_analysis(current, current)
    assert result["devices_removed"] == 0
    assert result["rack_units_freed"] == 0
    assert result["power_saved_watts"] == 0
    assert result["latency_change"] == "Neutral"


def test_run_consolidation_analysis_empty_graphs():
    result = mp.run_consolidation_analysis(
        {"nodes": [], "edges": []}, {"nodes": [], "edges": []})
    assert result["devices_removed"] == 0
    assert result["bw_increase_pct"] == 0


# ── generate_phase_physical_graph (needs a DB conn) ────────────────────────────


def _seed_config_conn():
    conn = _mem_storage_conn()
    conn.executescript(
        """
        CREATE TABLE ni_device_configs (
            id TEXT PRIMARY KEY, device_id TEXT, config_type TEXT,
            config_text TEXT, config_hash TEXT, source TEXT,
            version INTEGER DEFAULT 1, created_at TEXT
        );
        CREATE TABLE ni_devices (
            id TEXT PRIMARY KEY, node_id TEXT, label TEXT
        );
        """
    )
    juniper_cfg = 'ge-0/0/0 {\n    description "uplink to core";\n}\n'
    conn.execute(
        "INSERT INTO ni_device_configs (id, device_id, config_text, created_at) "
        "VALUES (%s, %s, %s, %s)",
        ("c1", "r1", juniper_cfg, "2026-01-01T00:00:00Z"),
    )
    conn.commit()
    return conn


def test_generate_phase_physical_graph_annotates_interfaces():
    current = _seed_current_graph()
    phase = {"changing_devices": ["r1"]}
    conn = _seed_config_conn()

    out = mp.generate_phase_physical_graph(current, phase, conn)

    assert "nodes" in out and "edges" in out
    # r1 is the center; its 1-hop neighbor sw1 is included.
    ids = {n["id"] for n in out["nodes"]}
    assert "r1" in ids
    for e in out["edges"]:
        assert e["diagram_type"] == "physical"
    # The r1-side interface should be resolved from the seeded config.
    r1_edges = [e for e in out["edges"] if e.get("source") == "r1"]
    assert any(e.get("source_interface") == "ge-0/0/0" for e in r1_edges)


def test_generate_phase_physical_graph_empty_config_no_interfaces():
    current = _seed_current_graph()
    conn = _mem_storage_conn()
    conn.executescript(
        "CREATE TABLE ni_device_configs (id TEXT PRIMARY KEY, device_id TEXT, "
        "config_text TEXT, created_at TEXT);"
        "CREATE TABLE ni_devices (id TEXT PRIMARY KEY, node_id TEXT);"
    )
    conn.commit()

    out = mp.generate_phase_physical_graph(current, {"changing_devices": ["r1"]}, conn)
    # No configs -> edges still tagged physical, but no interface annotations.
    for e in out["edges"]:
        assert e["diagram_type"] == "physical"
        assert "source_interface" not in e or e["source_interface"] is None


def test_generate_phase_physical_graph_no_centers_returns_full():
    current = _seed_current_graph()
    conn = _seed_config_conn()
    # Empty phase => no center ids => 2-hop subgraph returns the whole graph.
    out = mp.generate_phase_physical_graph(current, {}, conn)
    assert len(out["nodes"]) == len(current["nodes"])


# ── save_consolidation / load_consolidation (DB round-trip) ────────────────────


@pytest.fixture
def _consolidation_db(monkeypatch):
    """Route migration_phases' DB persistence to a shared temp SQLite DB."""
    import importlib
    storage = importlib.import_module("tools.db.storage")

    raw = sqlite3.connect(":memory:")
    raw.row_factory = sqlite3.Row
    raw.executescript(
        """
        CREATE TABLE nc_consolidation_analysis (
            id TEXT PRIMARY KEY,
            topo_id TEXT UNIQUE,
            current_device_count INTEGER,
            final_device_count INTEGER,
            devices_removed INTEGER,
            rack_units_freed INTEGER,
            power_saved_watts INTEGER,
            capex_delta REAL,
            opex_annual_delta REAL,
            tco_3yr_delta REAL,
            bw_increase_pct REAL,
            spof_count_before INTEGER,
            spof_count_after INTEGER,
            analysis_json TEXT,
            created_at TEXT,
            updated_at TEXT
        );
        """
    )
    raw.commit()
    shared = _NoCloseConn(StorageConnection(raw, "sqlite"))
    monkeypatch.setattr(storage, "get_connection", lambda *a, **k: shared)
    return shared


def test_save_and_load_consolidation_round_trip(_consolidation_db):
    current = _seed_current_graph()
    final = {"nodes": current["nodes"][:2], "edges": current["edges"][:1]}
    analysis = mp.run_consolidation_analysis(current, final)

    mp.save_consolidation("topo-1", analysis)
    loaded = mp.load_consolidation("topo-1")

    assert loaded["devices_removed"] == analysis["devices_removed"]
    assert loaded["power_saved_watts"] == analysis["power_saved_watts"]


def test_load_consolidation_missing_returns_empty(_consolidation_db):
    assert mp.load_consolidation("does-not-exist") == {}


def test_save_consolidation_missing_table_is_swallowed(monkeypatch):
    """When the target table is absent, save must warn and not raise."""
    import importlib
    storage = importlib.import_module("tools.db.storage")

    raw = sqlite3.connect(":memory:")
    raw.row_factory = sqlite3.Row  # no table created
    shared = _NoCloseConn(StorageConnection(raw, "sqlite"))
    monkeypatch.setattr(storage, "get_connection", lambda *a, **k: shared)

    # Must not raise even though nc_consolidation_analysis does not exist.
    result = mp.save_consolidation("topo-x", {"devices_removed": 1})
    assert result is None  # benign no-op signalled explicitly, not silently
    assert mp.load_consolidation("topo-x") == {}


def test_save_consolidation_upsert_updates_not_duplicates(_consolidation_db):
    """ndc-fix-03: a second save for the same topo_id must UPDATE the existing
    row (not duplicate, not silently no-op). Regression guard for the
    ON CONFLICT(topo_id) upsert path."""
    first = mp.save_consolidation("topo-up", {"devices_removed": 3, "power_saved_watts": 100})
    second = mp.save_consolidation("topo-up", {"devices_removed": 9, "power_saved_watts": 250})

    # Both calls succeed and return a row id.
    assert first and second

    loaded = mp.load_consolidation("topo-up")
    assert loaded["devices_removed"] == 9          # updated value wins
    assert loaded["power_saved_watts"] == 250

    # Exactly one physical row for this topo_id — the update did not duplicate.
    count = _consolidation_db.execute(
        "SELECT COUNT(*) FROM nc_consolidation_analysis WHERE topo_id=?", ("topo-up",)
    ).fetchone()[0]
    assert count == 1


def test_save_consolidation_missing_unique_constraint_raises(monkeypatch):
    """ndc-fix-03: when the table lacks the UNIQUE(topo_id) needed by
    ON CONFLICT, the save must SURFACE the error rather than warn-and-continue
    (which would make every save a silent no-op)."""
    import importlib
    storage = importlib.import_module("tools.db.storage")

    raw = sqlite3.connect(":memory:")
    raw.row_factory = sqlite3.Row
    # Same columns as production, but NO unique constraint on topo_id.
    raw.executescript(
        """
        CREATE TABLE nc_consolidation_analysis (
            id TEXT PRIMARY KEY,
            topo_id TEXT,
            current_device_count INTEGER,
            final_device_count INTEGER,
            devices_removed INTEGER,
            rack_units_freed INTEGER,
            power_saved_watts INTEGER,
            capex_delta REAL,
            opex_annual_delta REAL,
            tco_3yr_delta REAL,
            bw_increase_pct REAL,
            spof_count_before INTEGER,
            spof_count_after INTEGER,
            analysis_json TEXT,
            created_at TEXT,
            updated_at TEXT
        );
        """
    )
    raw.commit()
    shared = _NoCloseConn(StorageConnection(raw, "sqlite"))
    monkeypatch.setattr(storage, "get_connection", lambda *a, **k: shared)

    with pytest.raises(Exception):
        mp.save_consolidation("topo-noconstraint", {"devices_removed": 1})
