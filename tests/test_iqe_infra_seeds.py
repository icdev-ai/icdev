# CUI // SP-CTI
"""Tests for context/iqe/queries/infra/*.iqe (5 seed queries).

Each query must:
  • parse without error and match expected AST shape
  • return a deterministic violation count against a seeded fixture DB
"""
from __future__ import annotations

import pathlib
import sqlite3

from tools.iqe.adapters.infra import resources_adapter
from tools.iqe.ast_nodes import AttrRef, Literal
from tools.iqe.executor import Executor
from tools.iqe.parser import parse

_QUERY_DIR = (
    pathlib.Path(__file__).parent.parent
    / "context" / "iqe" / "queries" / "infra"
)


def _read(name: str) -> str:
    return (_QUERY_DIR / name).read_text(encoding="utf-8")


# ---- Schema -----------------------------------------------------------------

_RESOURCES_SCHEMA = """
CREATE TABLE IF NOT EXISTS idc_infra_resources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    csp TEXT NOT NULL,
    region TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_name TEXT,
    classification TEXT DEFAULT 'UNCLASSIFIED',
    tags TEXT,
    cost_per_month REAL DEFAULT 0.0,
    config TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
"""

# ---- Fixture ----------------------------------------------------------------
#
# 7 resources chosen so each query has a known, deterministic violation count:
#
#   R1: aws  us-east-1      CUI   cost=1200 tags=NULL config={"instance_type":"m5.large"}
#       → high_cost_cui (CUI+cost>500), cross_region (CUI not in gov),
#         untagged (tags=null), fips_compliance (CUI, no "fips" in config)
#
#   R2: aws  us-gov-west-1  CUI   cost=800  tags=json  config={"fips":true,...}
#       → high_cost_cui only  (in gov, has tags, has fips)
#
#   R3: azure usgovvirginia  CUI   cost=200  tags=json  config={"fips":true}
#       → no violations  (in gov, tagged, fips, low cost)
#
#   R4: aws  us-west-2      UNCL  cost=600  tags=NULL config={"cert_expired":true}
#       → untagged, expired_certs  (UNCLASSIFIED → no cui checks)
#
#   R5: aws  us-gov-east-1  CUI   cost=150  tags=json  config={"fips":true}
#       → no violations
#
#   R6: aws  us-east-1      CUI   cost=300  tags=json  config={"instance_type":"t3.small"}
#       → cross_region (CUI, commercial region), fips_compliance (CUI, no "fips")
#
#   R7: gcp  us-central1    UNCL  cost=50   tags=NULL config={"cert_expired":true}
#       → untagged, expired_certs
#
# Summary of expected violation counts per query:
#   high_cost_cui_resources : R1, R2            → 2
#   cross_region_data_paths : R1, R6            → 2
#   untagged_resources      : R1, R4, R7        → 3
#   fips_compliance_check   : R1, R6            → 2
#   expired_certs           : R4, R7            → 2

_FIXTURE_ROWS = [
    # (csp, region, resource_type, resource_name, classification, tags, cost_per_month, config)
    ("aws",   "us-east-1",      "ec2",  "web-server-01",  "CUI",           None,                 1200.0, '{"instance_type":"m5.large"}'),
    ("aws",   "us-gov-west-1",  "ec2",  "api-server-01",  "CUI",           '{"env":"prod"}',      800.0, '{"fips":true,"instance_type":"m5.large"}'),
    ("azure", "usgovvirginia",  "vm",   "db-server-01",   "CUI",           '{"env":"dev"}',       200.0, '{"fips":true}'),
    ("aws",   "us-west-2",      "s3",   "backup-bucket",  "UNCLASSIFIED",  None,                  600.0, '{"cert_expired":true}'),
    ("aws",   "us-gov-east-1",  "rds",  "pg-primary",     "CUI",           '{"env":"prod"}',      150.0, '{"fips":true}'),
    ("aws",   "us-east-1",      "ec2",  "dev-server-01",  "CUI",           '{"env":"dev"}',       300.0, '{"instance_type":"t3.small"}'),
    ("gcp",   "us-central1",    "gke",  "k8s-cluster-01", "UNCLASSIFIED",  None,                   50.0, '{"cert_expired":true}'),
]


def _resources_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(_RESOURCES_SCHEMA)
    conn.executemany(
        "INSERT INTO idc_infra_resources"
        " (csp, region, resource_type, resource_name, classification, tags, cost_per_month, config)"
        " VALUES (?,?,?,?,?,?,?,?)",
        _FIXTURE_ROWS,
    )
    conn.commit()
    return conn


def _ex() -> tuple[Executor, sqlite3.Connection]:
    conn = _resources_db()
    ex = Executor()
    ex.register_collection("infra.resources", resources_adapter)
    return ex, conn


# ============================================================================
# Test 1 — high_cost_cui_resources.iqe
# ============================================================================

def test_high_cost_cui_resources_parses() -> None:
    q = parse(_read("high_cost_cui_resources.iqe"))
    assert q.var == "r"
    assert q.collection == AttrRef(["infra", "resources"])
    assert len(q.where_clauses) == 2
    assert q.where_clauses[0].predicate.op == "=="
    assert q.where_clauses[0].predicate.right == Literal("CUI")
    assert q.where_clauses[1].predicate.op == ">"
    assert q.where_clauses[1].predicate.right == Literal(500)
    fields = [f.parts[-1] for f in q.select.fields]
    assert "id" in fields
    assert "resource_name" in fields
    assert "cost_per_month" in fields
    assert "classification" in fields


def test_high_cost_cui_resources_violation_count() -> None:
    ex, conn = _ex()
    ast = parse(_read("high_cost_cui_resources.iqe"))
    result = ex.run(ast, conn=conn)
    conn.close()
    # R1 (CUI, 1200) + R2 (CUI, 800) → 2 violations
    assert len(result) == 2
    names = {r["resource_name"] for r in result}
    assert "web-server-01" in names
    assert "api-server-01" in names


# ============================================================================
# Test 2 — cross_region_data_paths.iqe
# ============================================================================

def test_cross_region_data_paths_parses() -> None:
    q = parse(_read("cross_region_data_paths.iqe"))
    assert q.var == "r"
    assert q.collection == AttrRef(["infra", "resources"])
    assert len(q.where_clauses) == 2
    assert q.where_clauses[0].predicate.op == "=="
    assert q.where_clauses[0].predicate.right == Literal("CUI")
    # second where: not startswith "us-gov" and not startswith "usgov"
    assert q.where_clauses[1].predicate.op == "and"
    assert q.where_clauses[1].predicate.left.op == "not"
    assert q.where_clauses[1].predicate.right.op == "not"
    fields = [f.parts[-1] for f in q.select.fields]
    assert "resource_name" in fields
    assert "region" in fields
    assert "classification" in fields


def test_cross_region_data_paths_violation_count() -> None:
    ex, conn = _ex()
    ast = parse(_read("cross_region_data_paths.iqe"))
    result = ex.run(ast, conn=conn)
    conn.close()
    # R1 (CUI, us-east-1) + R6 (CUI, us-east-1) → 2 violations
    assert len(result) == 2
    names = {r["resource_name"] for r in result}
    assert "web-server-01" in names
    assert "dev-server-01" in names


# ============================================================================
# Test 3 — untagged_resources.iqe
# ============================================================================

def test_untagged_resources_parses() -> None:
    q = parse(_read("untagged_resources.iqe"))
    assert q.var == "r"
    assert q.collection == AttrRef(["infra", "resources"])
    assert len(q.where_clauses) == 1
    assert q.where_clauses[0].predicate.op == "=="
    assert q.where_clauses[0].predicate.right == Literal(None)
    fields = [f.parts[-1] for f in q.select.fields]
    assert "resource_name" in fields
    assert "csp" in fields
    assert "region" in fields
    assert "resource_type" in fields


def test_untagged_resources_violation_count() -> None:
    ex, conn = _ex()
    ast = parse(_read("untagged_resources.iqe"))
    result = ex.run(ast, conn=conn)
    conn.close()
    # R1, R4, R7 → 3 violations (tags=NULL)
    assert len(result) == 3
    names = {r["resource_name"] for r in result}
    assert "web-server-01" in names
    assert "backup-bucket" in names
    assert "k8s-cluster-01" in names


# ============================================================================
# Test 4 — fips_compliance_check.iqe
# ============================================================================

def test_fips_compliance_check_parses() -> None:
    q = parse(_read("fips_compliance_check.iqe"))
    assert q.var == "r"
    assert q.collection == AttrRef(["infra", "resources"])
    assert len(q.where_clauses) == 2
    assert q.where_clauses[0].predicate.op == "=="
    assert q.where_clauses[0].predicate.right == Literal("CUI")
    # second where: not (config contains "fips")
    assert q.where_clauses[1].predicate.op == "not"
    assert q.where_clauses[1].predicate.right.op == "contains"
    assert q.where_clauses[1].predicate.right.right == Literal("fips")
    fields = [f.parts[-1] for f in q.select.fields]
    assert "resource_name" in fields
    assert "csp" in fields
    assert "classification" in fields


def test_fips_compliance_check_violation_count() -> None:
    ex, conn = _ex()
    ast = parse(_read("fips_compliance_check.iqe"))
    result = ex.run(ast, conn=conn)
    conn.close()
    # R1 (CUI, no fips in config) + R6 (CUI, no fips in config) → 2 violations
    assert len(result) == 2
    names = {r["resource_name"] for r in result}
    assert "web-server-01" in names
    assert "dev-server-01" in names


# ============================================================================
# Test 5 — expired_certs.iqe
# ============================================================================

def test_expired_certs_parses() -> None:
    q = parse(_read("expired_certs.iqe"))
    assert q.var == "r"
    assert q.collection == AttrRef(["infra", "resources"])
    assert len(q.where_clauses) == 1
    assert q.where_clauses[0].predicate.op == "contains"
    assert q.where_clauses[0].predicate.right == Literal("cert_expired")
    fields = [f.parts[-1] for f in q.select.fields]
    assert "resource_name" in fields
    assert "csp" in fields
    assert "region" in fields
    assert "resource_type" in fields


def test_expired_certs_violation_count() -> None:
    ex, conn = _ex()
    ast = parse(_read("expired_certs.iqe"))
    result = ex.run(ast, conn=conn)
    conn.close()
    # R4 (backup-bucket) + R7 (k8s-cluster-01) → 2 violations
    assert len(result) == 2
    names = {r["resource_name"] for r in result}
    assert "backup-bucket" in names
    assert "k8s-cluster-01" in names
