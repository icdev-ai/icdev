"""Parse + execute all 10 NDC seed .iqe files (dt-iqe-07 / dt-iqe-08)."""
import pathlib
import sqlite3

import pytest

from tools.iqe.ast_nodes import AttrRef, BinOp, ForeachNode, Literal, SelectNode
from tools.iqe.executor import Executor
from tools.iqe.parser import parse

_QUERY_DIR = pathlib.Path(__file__).parent.parent / "context" / "iqe" / "queries" / "network"


def _read(name: str) -> str:
    return (_QUERY_DIR / name).read_text(encoding="utf-8")


@pytest.fixture
def mem_conn():
    """A scratch in-memory DB that is closed when the test ends.

    These tests seed rows and never commit, so a `sqlite3.connect()` opened
    inline in the test body finishes holding an implicit write transaction —
    which is what the transaction-leak guard in conftest.py fails on (tsh-leak-01).
    Closing here ends the transaction with the connection.
    """
    conn = sqlite3.connect(":memory:")
    try:
        yield conn
    finally:
        conn.close()


# 1 — vendor_inventory -------------------------------------------------------

def test_vendor_inventory_parses():
    q = parse(_read("vendor_inventory.iqe"))
    assert isinstance(q, ForeachNode)
    assert q.var == "d"
    assert q.collection == AttrRef(["network", "devices"])
    assert q.where_clauses == []
    assert isinstance(q.select, SelectNode)
    assert q.select.wildcard is False
    fields = [f.parts[-1] for f in q.select.fields]
    assert fields == ["hostname", "vendor", "model", "os_version"]


# 2 — bgp_peer_asymmetry -----------------------------------------------------

def test_bgp_peer_asymmetry_parses():
    q = parse(_read("bgp_peer_asymmetry.iqe"))
    assert isinstance(q, ForeachNode)
    assert q.var == "p"
    assert q.collection == AttrRef(["network", "bgp_peers"])
    assert len(q.where_clauses) == 1
    pred = q.where_clauses[0].predicate
    assert isinstance(pred, BinOp)
    assert pred.op == "!="
    assert pred.right == Literal("established")
    fields = [f.parts[-1] for f in q.select.fields]
    assert "peer_ip" in fields
    assert "session_state" in fields


# 3 — iface_admin_oper_mismatch ----------------------------------------------

def test_iface_admin_oper_mismatch_parses():
    q = parse(_read("iface_admin_oper_mismatch.iqe"))
    assert isinstance(q, ForeachNode)
    assert q.var == "i"
    assert q.collection == AttrRef(["network", "interfaces"])
    assert len(q.where_clauses) == 2
    preds = [w.predicate for w in q.where_clauses]
    assert all(isinstance(p, BinOp) for p in preds)
    assert preds[0].right == Literal("up")
    assert preds[1].right == Literal("down")
    fields = [f.parts[-1] for f in q.select.fields]
    assert "admin_status" in fields
    assert "oper_status" in fields


# 4 — stig_check -------------------------------------------------------------

def test_stig_check_parses():
    q = parse(_read("stig_check.iqe"))
    assert isinstance(q, ForeachNode)
    assert q.var == "f"
    assert q.collection == AttrRef(["network", "findings"])
    assert len(q.where_clauses) == 2
    severity_pred = q.where_clauses[0].predicate
    status_pred = q.where_clauses[1].predicate
    assert severity_pred.right == Literal("CAT1")
    assert status_pred.right == Literal("open")
    fields = [f.parts[-1] for f in q.select.fields]
    assert "rule_id" in fields
    assert "vuln_id" in fields


# 5 — capacity_threshold -----------------------------------------------------

def test_capacity_threshold_parses():
    q = parse(_read("capacity_threshold.iqe"))
    assert isinstance(q, ForeachNode)
    assert q.var == "i"
    assert q.collection == AttrRef(["network", "interfaces"])
    assert len(q.where_clauses) == 1
    pred = q.where_clauses[0].predicate
    assert isinstance(pred, BinOp)
    assert pred.op == ">"
    assert pred.right == Literal(80)
    fields = [f.parts[-1] for f in q.select.fields]
    assert "utilization_pct" in fields
    assert "speed_mbps" in fields


# 6 — routing_loops ----------------------------------------------------------

def test_routing_loops_parses_and_executes(mem_conn):
    q = parse(_read("routing_loops.iqe"))
    assert isinstance(q, ForeachNode)
    assert q.var == "r"
    assert q.collection == AttrRef(["network", "routes"])
    assert len(q.where_clauses) == 1
    pred = q.where_clauses[0].predicate
    assert isinstance(pred, BinOp)
    assert pred.op == ">"
    assert pred.right == Literal(15)
    fields = [f.parts[-1] for f in q.select.fields]
    assert "hop_count" in fields
    assert "prefix" in fields
    conn = mem_conn
    conn.execute(
        "CREATE TABLE routes"
        " (device TEXT, prefix TEXT, next_hop TEXT, hop_count INTEGER, protocol TEXT)"
    )
    conn.executemany(
        "INSERT INTO routes VALUES (?,?,?,?,?)",
        [
            ("R1", "10.0.0.0/24", "192.168.1.1", 16, "OSPF"),
            ("R2", "10.0.1.0/24", "192.168.1.2", 20, "BGP"),
            ("R3", "10.0.2.0/24", "192.168.1.3", 18, "OSPF"),
            ("R4", "10.0.3.0/24", "192.168.1.4", 10, "BGP"),
            ("R5", "10.0.4.0/24", "192.168.1.5", 5, "OSPF"),
        ],
    )
    rows = Executor().run(q, conn)
    assert len(rows) == 3  # hop_count in {16, 20, 18}


# 7 — mtu_mismatch -----------------------------------------------------------

def test_mtu_mismatch_parses_and_executes(mem_conn):
    q = parse(_read("mtu_mismatch.iqe"))
    assert isinstance(q, ForeachNode)
    assert q.var == "i"
    assert q.collection == AttrRef(["network", "interfaces"])
    assert len(q.where_clauses) == 1
    pred = q.where_clauses[0].predicate
    assert isinstance(pred, BinOp)
    assert pred.op == "!="
    assert isinstance(pred.right, AttrRef)
    assert pred.right.parts[-1] == "peer_mtu"
    fields = [f.parts[-1] for f in q.select.fields]
    assert "mtu" in fields
    assert "peer_mtu" in fields
    conn = mem_conn
    conn.execute(
        "CREATE TABLE interfaces"
        " (device TEXT, name TEXT, mtu INTEGER, peer_device TEXT, peer_mtu INTEGER)"
    )
    conn.executemany(
        "INSERT INTO interfaces VALUES (?,?,?,?,?)",
        [
            ("R1", "GE0/0", 9000, "R2", 1500),
            ("R2", "GE0/1", 1500, "R3", 1500),
            ("R3", "GE0/2", 9000, "R4", 9000),
            ("R4", "GE0/3", 1500, "R5", 9000),
        ],
    )
    rows = Executor().run(q, conn)
    assert len(rows) == 2  # rows where mtu != peer_mtu


# 8 — vlan_coverage ----------------------------------------------------------

def test_vlan_coverage_parses_and_executes(mem_conn):
    q = parse(_read("vlan_coverage.iqe"))
    assert isinstance(q, ForeachNode)
    assert q.var == "v"
    assert q.collection == AttrRef(["network", "vlans"])
    assert len(q.where_clauses) == 1
    pred = q.where_clauses[0].predicate
    assert isinstance(pred, BinOp)
    assert pred.op == "<"
    assert pred.right == Literal(2)
    fields = [f.parts[-1] for f in q.select.fields]
    assert "vlan_id" in fields
    assert "active_ports" in fields
    conn = mem_conn
    conn.execute(
        "CREATE TABLE vlans"
        " (vlan_id INTEGER, name TEXT, active_ports INTEGER, site TEXT, tagged_devices TEXT)"
    )
    conn.executemany(
        "INSERT INTO vlans VALUES (?,?,?,?,?)",
        [
            (10, "MGMT", 1, "DC1", "R1"),
            (20, "DATA", 4, "DC1", "R1,R2"),
            (30, "VOICE", 0, "DC2", ""),
            (40, "WAN", 3, "DC2", "R3,R4,R5"),
            (50, "TEST", 2, "DC3", "R6,R7"),
        ],
    )
    rows = Executor().run(q, conn)
    assert len(rows) == 2  # active_ports in {1, 0}


# 9 — acl_drift --------------------------------------------------------------

def test_acl_drift_parses_and_executes(mem_conn):
    q = parse(_read("acl_drift.iqe"))
    assert isinstance(q, ForeachNode)
    assert q.var == "a"
    assert q.collection == AttrRef(["network", "acl_rules"])
    assert len(q.where_clauses) == 1
    pred = q.where_clauses[0].predicate
    assert isinstance(pred, BinOp)
    assert pred.op == "=="
    assert pred.right == Literal("drift")
    fields = [f.parts[-1] for f in q.select.fields]
    assert "acl_name" in fields
    assert "status" in fields
    conn = mem_conn
    conn.execute(
        "CREATE TABLE acl_rules"
        " (device TEXT, acl_name TEXT, rule_id INTEGER, action TEXT, status TEXT)"
    )
    conn.executemany(
        "INSERT INTO acl_rules VALUES (?,?,?,?,?)",
        [
            ("FW1", "INBOUND", 10, "permit", "drift"),
            ("FW1", "INBOUND", 20, "deny", "compliant"),
            ("FW2", "OUTBOUND", 30, "permit", "drift"),
            ("FW2", "OUTBOUND", 40, "deny", "compliant"),
            ("FW3", "FORWARD", 50, "deny", "drift"),
        ],
    )
    rows = Executor().run(q, conn)
    assert len(rows) == 3  # status == "drift"


# 10 — bgp_convergence -------------------------------------------------------

def test_bgp_convergence_parses_and_executes(mem_conn):
    q = parse(_read("bgp_convergence.iqe"))
    assert isinstance(q, ForeachNode)
    assert q.var == "p"
    assert q.collection == AttrRef(["network", "bgp_peers"])
    assert len(q.where_clauses) == 1
    pred = q.where_clauses[0].predicate
    assert isinstance(pred, BinOp)
    assert pred.op == ">"
    assert pred.right == Literal(30)
    fields = [f.parts[-1] for f in q.select.fields]
    assert "peer_ip" in fields
    assert "convergence_sec" in fields
    conn = mem_conn
    conn.execute(
        "CREATE TABLE bgp_peers"
        " (device TEXT, peer_ip TEXT, local_as INTEGER, remote_as INTEGER,"
        "  convergence_sec INTEGER)"
    )
    conn.executemany(
        "INSERT INTO bgp_peers VALUES (?,?,?,?,?)",
        [
            ("R1", "10.0.0.1", 65001, 65002, 45),
            ("R2", "10.0.0.2", 65001, 65003, 15),
            ("R3", "10.0.0.3", 65001, 65004, 60),
            ("R4", "10.0.0.4", 65001, 65005, 30),
        ],
    )
    rows = Executor().run(q, conn)
    assert len(rows) == 2  # convergence_sec in {45, 60}
