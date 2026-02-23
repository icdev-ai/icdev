# [TEMPLATE: CUI // SP-CTI]
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

"""Tests for tools.supply_chain.dependency_graph — supply chain dependency graph
build, traversal, and impact propagation."""

import json
import sqlite3

import pytest

from tools.supply_chain.dependency_graph import (
    CRITICALITY_LEVELS,
    DEPENDENCY_TYPES,
    RISK_TIERS,
    SECTION_889,
    SOURCE_TARGET_TYPES,
    VENDOR_TYPES,
    add_dependency,
    add_vendor,
    build_graph,
    get_critical_path,
    get_downstream,
    get_upstream,
    propagate_impact,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SUPPLY_CHAIN_SCHEMA = """
CREATE TABLE IF NOT EXISTS supply_chain_vendors (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    vendor_name TEXT NOT NULL,
    vendor_type TEXT NOT NULL,
    country_of_origin TEXT,
    scrm_risk_tier TEXT DEFAULT 'moderate',
    section_889_status TEXT DEFAULT 'compliant',
    last_assessed TEXT,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS supply_chain_dependencies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    source_type TEXT NOT NULL DEFAULT 'component',
    source_id TEXT NOT NULL,
    target_type TEXT NOT NULL DEFAULT 'component',
    target_id TEXT NOT NULL,
    dependency_type TEXT NOT NULL,
    criticality TEXT DEFAULT 'medium',
    isa_id TEXT,
    metadata TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS audit_trail (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    project_id TEXT,
    details TEXT,
    classification TEXT DEFAULT 'CUI',
    session_id TEXT,
    source_ip TEXT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

PROJECT_ID = "proj-dep-test"


@pytest.fixture
def dep_db(tmp_path):
    """Temporary database with supply-chain tables and audit_trail."""
    db_path = tmp_path / "dep_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SUPPLY_CHAIN_SCHEMA)
    conn.close()
    return db_path


@pytest.fixture
def seeded_db(dep_db):
    """Database pre-populated with a small dependency graph.

    Graph (all component type, project = PROJECT_ID):
        A --depends_on--> B (critical)
        A --depends_on--> C (medium)
        B --depends_on--> D (high)
        C --depends_on--> D (medium)
        D --depends_on--> E (critical)

    Reading: A depends on B and C; B and C both depend on D; D depends on E.
    Upstream of A = {B, C, D, E}.  Downstream of E = {D, B, C, A}.
    """
    conn = sqlite3.connect(str(dep_db))
    edges = [
        ("A", "B", "depends_on", "critical"),
        ("A", "C", "depends_on", "medium"),
        ("B", "D", "depends_on", "high"),
        ("C", "D", "depends_on", "medium"),
        ("D", "E", "depends_on", "critical"),
    ]
    for src, tgt, dep_type, crit in edges:
        conn.execute(
            """INSERT INTO supply_chain_dependencies
               (project_id, source_type, source_id, target_type, target_id,
                dependency_type, criticality, created_at)
               VALUES (?, 'component', ?, 'component', ?, ?, ?, datetime('now'))""",
            (PROJECT_ID, src, tgt, dep_type, crit),
        )
    conn.commit()
    conn.close()
    return dep_db


# ---------------------------------------------------------------------------
# TestAddVendor
# ---------------------------------------------------------------------------

class TestAddVendor:
    """add_vendor: insert a vendor record and validate constraints."""

    def test_add_vendor_returns_id_and_name(self, dep_db):
        result = add_vendor(
            PROJECT_ID, "Acme Corp", "cots", "US", db_path=str(dep_db))
        assert "vendor_id" in result
        assert result["vendor_name"] == "Acme Corp"
        assert result["scrm_risk_tier"] == "moderate"

    def test_add_vendor_persists_to_db(self, dep_db):
        result = add_vendor(
            PROJECT_ID, "Beta Inc", "oss", "CA",
            scrm_risk_tier="low", db_path=str(dep_db))
        conn = sqlite3.connect(str(dep_db))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM supply_chain_vendors WHERE id = ?",
            (result["vendor_id"],)).fetchone()
        conn.close()
        assert row is not None
        assert row["vendor_name"] == "Beta Inc"
        assert row["vendor_type"] == "oss"
        assert row["country_of_origin"] == "CA"
        assert row["scrm_risk_tier"] == "low"

    def test_add_vendor_invalid_type_raises(self, dep_db):
        with pytest.raises(ValueError, match="vendor_type"):
            add_vendor(PROJECT_ID, "Bad", "invalid_type", "US",
                       db_path=str(dep_db))

    def test_add_vendor_invalid_risk_tier_raises(self, dep_db):
        with pytest.raises(ValueError, match="scrm_risk_tier"):
            add_vendor(PROJECT_ID, "Bad", "cots", "US",
                       scrm_risk_tier="extreme", db_path=str(dep_db))

    def test_add_vendor_invalid_section_889_raises(self, dep_db):
        with pytest.raises(ValueError, match="section_889_status"):
            add_vendor(PROJECT_ID, "Bad", "cots", "US",
                       section_889_status="nope", db_path=str(dep_db))


# ---------------------------------------------------------------------------
# TestAddDependency
# ---------------------------------------------------------------------------

class TestAddDependency:
    """add_dependency: insert a dependency edge with validation."""

    def test_add_dependency_returns_id(self, dep_db):
        result = add_dependency(
            PROJECT_ID, "svc-auth", "svc-db", "depends_on", "high",
            db_path=str(dep_db))
        assert "dependency_id" in result
        assert result["source"] == "svc-auth"
        assert result["target"] == "svc-db"
        assert result["criticality"] == "high"

    def test_add_dependency_invalid_type_raises(self, dep_db):
        with pytest.raises(ValueError, match="dependency_type"):
            add_dependency(PROJECT_ID, "A", "B", "unknown_dep", "medium",
                           db_path=str(dep_db))

    def test_add_dependency_invalid_criticality_raises(self, dep_db):
        with pytest.raises(ValueError, match="criticality"):
            add_dependency(PROJECT_ID, "A", "B", "depends_on", "ultra",
                           db_path=str(dep_db))

    def test_add_dependency_with_vendor_id_in_metadata(self, dep_db):
        vendor = add_vendor(PROJECT_ID, "VendorX", "saas", "US",
                            db_path=str(dep_db))
        result = add_dependency(
            PROJECT_ID, "app", "vendor-lib", "depends_on", "medium",
            vendor_id=vendor["vendor_id"], db_path=str(dep_db))
        conn = sqlite3.connect(str(dep_db))
        row = conn.execute(
            "SELECT metadata FROM supply_chain_dependencies WHERE id = ?",
            (result["dependency_id"],)).fetchone()
        conn.close()
        meta = json.loads(row[0])
        assert meta["vendor_id"] == vendor["vendor_id"]


# ---------------------------------------------------------------------------
# TestBuildGraph
# ---------------------------------------------------------------------------

class TestBuildGraph:
    """build_graph: construct adjacency-list representation from DB."""

    def test_empty_project_returns_empty_graph(self, dep_db):
        result = build_graph("nonexistent-proj", db_path=str(dep_db))
        assert result["nodes"] == []
        assert result["edges"] == []
        assert result["stats"]["total_nodes"] == 0
        assert result["stats"]["total_edges"] == 0

    def test_seeded_graph_node_count(self, seeded_db):
        result = build_graph(PROJECT_ID, db_path=str(seeded_db))
        assert result["stats"]["total_nodes"] == 5  # A, B, C, D, E
        assert result["stats"]["total_edges"] == 5

    def test_seeded_graph_critical_paths(self, seeded_db):
        result = build_graph(PROJECT_ID, db_path=str(seeded_db))
        # Two critical edges: A->B and D->E
        assert result["stats"]["critical_paths"] == 2

    def test_nodes_are_sorted(self, seeded_db):
        result = build_graph(PROJECT_ID, db_path=str(seeded_db))
        assert result["nodes"] == sorted(result["nodes"])


# ---------------------------------------------------------------------------
# TestTraversal
# ---------------------------------------------------------------------------

class TestTraversal:
    """get_upstream / get_downstream: BFS traversal of the graph."""

    def test_upstream_of_a_finds_all(self, seeded_db):
        result = get_upstream(PROJECT_ID, "A", db_path=str(seeded_db))
        names = {entry["component"] for entry in result["upstream"]}
        # A depends on B, C; B,C depend on D; D depends on E
        assert names == {
            "component:B", "component:C",
            "component:D", "component:E",
        }

    def test_upstream_of_leaf_is_empty(self, seeded_db):
        result = get_upstream(PROJECT_ID, "E", db_path=str(seeded_db))
        assert result["upstream"] == []
        assert result["depth"] == 0

    def test_downstream_of_e_finds_all(self, seeded_db):
        result = get_downstream(PROJECT_ID, "E", db_path=str(seeded_db))
        names = {entry["component"] for entry in result["downstream"]}
        # Everything that transitively depends on E: D, B, C, A
        assert names == {
            "component:D", "component:B",
            "component:C", "component:A",
        }
        assert result["impact_radius"] == 4

    def test_downstream_of_root_is_empty(self, seeded_db):
        result = get_downstream(PROJECT_ID, "A", db_path=str(seeded_db))
        assert result["downstream"] == []
        assert result["impact_radius"] == 0


# ---------------------------------------------------------------------------
# TestImpactPropagation
# ---------------------------------------------------------------------------

class TestImpactPropagation:
    """propagate_impact: severity decay and blast radius calculation."""

    def test_critical_impact_from_e(self, seeded_db):
        result = propagate_impact(
            PROJECT_ID, "E", "vulnerability", "critical",
            db_path=str(seeded_db))
        assert result["source_component"] == "E"
        assert result["impact_type"] == "vulnerability"
        assert result["severity"] == "critical"
        assert result["blast_radius"] == 4  # D, B, C, A

    def test_severity_decays_per_hop(self, seeded_db):
        result = propagate_impact(
            PROJECT_ID, "E", "vulnerability", "critical",
            db_path=str(seeded_db))
        # Base score for critical = 10.0, decay = 0.8
        # Hop 1 (D): 10.0 * 0.8 = 8.0
        hop1 = [a for a in result["affected_components"] if a["hop"] == 1]
        assert len(hop1) == 1
        assert hop1[0]["propagated_score"] == 8.0
        assert hop1[0]["propagated_severity"] == "critical"  # >= 8.0

    def test_low_severity_stays_contained(self, dep_db):
        # Single edge: X -> Y
        add_dependency(PROJECT_ID, "X", "Y", "depends_on", "low",
                       db_path=str(dep_db))
        result = propagate_impact(
            PROJECT_ID, "Y", "deprecation", "low",
            db_path=str(dep_db))
        # Only X depends on Y, blast radius = 1
        assert result["blast_radius"] == 1
        affected = result["affected_components"][0]
        # low base = 2.5 * 0.8 = 2.0 -> severity "low"
        assert affected["propagated_score"] == 2.0
        assert affected["propagated_severity"] == "low"

    def test_recommendations_critical_severity(self, seeded_db):
        result = propagate_impact(
            PROJECT_ID, "E", "vulnerability", "critical",
            db_path=str(seeded_db))
        recs = " ".join(result["recommendations"])
        assert "incident response" in recs.lower()

    def test_no_downstream_gives_contained_recommendation(self, dep_db):
        # Isolated node: add one edge so the component exists but has no
        # downstream dependents.
        add_dependency(PROJECT_ID, "solo", "target", "depends_on", "medium",
                       db_path=str(dep_db))
        result = propagate_impact(
            PROJECT_ID, "solo", "outage", "medium",
            db_path=str(dep_db))
        assert result["blast_radius"] == 0
        recs = " ".join(result["recommendations"])
        assert "contained" in recs.lower()


# [TEMPLATE: CUI // SP-CTI]
