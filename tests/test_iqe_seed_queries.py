"""Parse all 5 NDC seed .iqe files and assert AST shape (dt-iqe-07)."""
import pathlib

from tools.iqe.ast_nodes import AttrRef, BinOp, ForeachNode, Literal, SelectNode
from tools.iqe.parser import parse

_QUERY_DIR = pathlib.Path(__file__).parent.parent / "context" / "iqe" / "queries" / "network"


def _read(name: str) -> str:
    return (_QUERY_DIR / name).read_text(encoding="utf-8")


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
