#!/usr/bin/env python3
# CUI // SP-CTI
"""DDC Demo Seed -- populates data_canvas.db with realistic data design demo data.

Tables seeded:
  data_designs (5), dd_assessments (5), dd_audit (10), dd_versions (8),
  dd_collab_sessions (4), dd_lineage (12), data_nodes (20), data_edges (18),
  data_twin_snapshots (5), ddc_runbook_executions (6), ddc_sop_approvals (8),
  dd_explore_sessions (4), dd_explore_profiles (4), dd_anomaly_runs (4),
  dd_query_history (10), dd_quality_rules (8), dd_quality_runs (8),
  dd_freshness_alerts (5), dd_migration_jobs (5)

Usage:
    python tools/db/seeds/seed_ddc_demo.py --all [--reset] [--json]
    python tools/db/seeds/seed_ddc_demo.py --verify --json
"""
from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT))

random.seed(42)

_NOW = datetime.now(timezone.utc)
_T0 = _NOW - timedelta(hours=72)


def _ts(offset_hours: float = 0.0) -> str:
    return (_T0 + timedelta(hours=offset_hours)).isoformat()


def _uid() -> str:
    return str(uuid.uuid4())


def _get_conn():
    try:
        from tools.data_canvas.db.init_db import get_connection, init_db
        conn = get_connection()
        init_db()
        return conn
    except Exception:
        db = _ROOT / "data" / "data_canvas.db"
        db.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        return conn


def _safe_execute(conn, sql, params):
    expected = sql.count("?")
    actual = len(params) if isinstance(params, (list, tuple)) else 1
    if expected != actual:
        raise ValueError(f"Placeholder mismatch: {expected} placeholders but {actual} params")
    conn.execute(sql, params)


def _reset_demo_data(conn) -> None:
    for tbl in (
        "dd_migration_jobs", "dd_freshness_alerts", "dd_quality_runs", "dd_quality_rules",
        "dd_query_history", "dd_anomaly_runs", "dd_explore_profiles", "dd_explore_sessions",
        "ddc_sop_approvals", "ddc_runbook_executions", "data_twin_snapshots", "data_edges",
        "data_nodes", "dd_lineage", "dd_collab_sessions", "dd_versions", "dd_audit",
        "dd_assessments", "data_designs",
    ):
        try:
            conn.execute(f"DELETE FROM {tbl}")
        except Exception:
            pass
    conn.commit()


_DESIGN_IDS = {f"ddc-design-{i:03d}": _uid() for i in range(5)}

_DATA_DESIGNS = [
    {
        "id": _DESIGN_IDS["ddc-design-000"],
        "name": "FedRAMP Healthcare Data Lake",
        "description": "HIPAA-compliant data lake on AWS GovCloud with S3, Redshift, and ETL pipelines for federal healthcare analytics.",
        "graph_json": json.dumps({
            "nodes": [
                {"id": "raw-lake", "type": "ent-datalake", "label": "Raw Data Lake (S3)", "x": 100, "y": 150},
                {"id": "curated-lake", "type": "ent-datalake", "label": "Curated Zone (S3)", "x": 350, "y": 150},
                {"id": "warehouse", "type": "ent-warehouse", "label": "Redshift Warehouse", "x": 600, "y": 150},
                {"id": "etl1", "type": "flow-etl", "label": "Raw -> Curated ETL", "x": 225, "y": 150},
                {"id": "etl2", "type": "flow-etl", "label": "Curated -> Warehouse ETL", "x": 475, "y": 150},
                {"id": "enc", "type": "ctrl-encryption", "label": "KMS Encryption", "x": 350, "y": 50},
                {"id": "rbac", "type": "ctrl-rbac", "label": "IAM RBAC", "x": 100, "y": 50},
                {"id": "auditlog", "type": "ctrl-audit-log", "label": "CloudTrail Audit", "x": 600, "y": 50},
                {"id": "dlp", "type": "ctrl-dlp", "label": "DLP Egress Filter", "x": 600, "y": 300},
                {"id": "retention", "type": "ctrl-retention", "label": "7-Year Retention", "x": 100, "y": 300},
                {"id": "backup", "type": "ctrl-backup-policy", "label": "Cross-Region Backup", "x": 350, "y": 300},
            ],
            "edges": [
                {"id": "e1", "source": "raw-lake", "target": "etl1", "label": "extract", "type": "flow-etl"},
                {"id": "e2", "source": "etl1", "target": "curated-lake", "label": "load", "type": "flow-etl"},
                {"id": "e3", "source": "curated-lake", "target": "etl2", "label": "extract", "type": "flow-etl"},
                {"id": "e4", "source": "etl2", "target": "warehouse", "label": "load", "type": "flow-etl"},
                {"id": "e5", "source": "raw-lake", "target": "enc"},
                {"id": "e6", "source": "curated-lake", "target": "enc"},
                {"id": "e7", "source": "warehouse", "target": "enc"},
                {"id": "e8", "source": "raw-lake", "target": "rbac"},
                {"id": "e9", "source": "curated-lake", "target": "rbac"},
                {"id": "e10", "source": "warehouse", "target": "rbac"},
                {"id": "e11", "source": "warehouse", "target": "dlp"},
                {"id": "e12", "source": "raw-lake", "target": "retention"},
                {"id": "e13", "source": "warehouse", "target": "backup"},
            ],
            "boundaries": [
                {"id": "cui-zone", "label": "CUI Analytics Zone", "type": "bnd-classification", "contained_nodes": ["raw-lake", "curated-lake", "warehouse"], "x": 50, "y": 80, "width": 750, "height": 150},
                {"id": "govcloud", "label": "AWS GovCloud US-East", "type": "bnd-region", "contained_nodes": ["raw-lake", "curated-lake", "warehouse"], "x": 40, "y": 70, "width": 770, "height": 170},
            ],
        }),
        "template_id": "tpl-ddc-data-lake",
    },
    {
        "id": _DESIGN_IDS["ddc-design-001"],
        "name": "DoD Multi-Classification Data Mesh",
        "description": "Data mesh with CUI and SECRET zones, cross-domain guard, federated governance, and OPA policy engine.",
        "graph_json": json.dumps({
            "nodes": [
                {"id": "cui-db", "type": "ent-table", "label": "CUI Operations DB", "x": 150, "y": 200},
                {"id": "cui-files", "type": "ent-file", "label": "CUI File Store", "x": 150, "y": 350},
                {"id": "secret-db", "type": "ent-table", "label": "SECRET Intel DB", "x": 650, "y": 200},
                {"id": "secret-cache", "type": "ent-cache", "label": "SECRET Cache", "x": 650, "y": 350},
                {"id": "cds-flow", "type": "flow-cross-domain", "label": "Cross-Domain Guard", "x": 400, "y": 275},
                {"id": "cui-enc", "type": "ctrl-encryption", "label": "FIPS 140-2 Encryption", "x": 150, "y": 100},
                {"id": "sec-enc", "type": "ctrl-encryption", "label": "NSA Type 1 Encryption", "x": 650, "y": 100},
                {"id": "cui-rbac", "type": "ctrl-rbac", "label": "CUI RBAC (CAC)", "x": 50, "y": 100},
                {"id": "sec-rbac", "type": "ctrl-rbac", "label": "SECRET RBAC (SCI)", "x": 750, "y": 100},
                {"id": "opa", "type": "ctrl-classification", "label": "OPA Policy Engine", "x": 400, "y": 100},
                {"id": "catalog", "type": "twin-catalog", "label": "Global Metadata Catalog", "x": 400, "y": 450},
            ],
            "edges": [
                {"id": "e1", "source": "cui-db", "target": "cds-flow", "label": "CUI->SECRET", "type": "flow-cross-domain"},
                {"id": "e2", "source": "cds-flow", "target": "secret-db", "label": "filtered", "type": "flow-cross-domain"},
                {"id": "e3", "source": "cui-db", "target": "cui-enc"},
                {"id": "e4", "source": "secret-db", "target": "sec-enc"},
                {"id": "e5", "source": "cui-db", "target": "cui-rbac"},
                {"id": "e6", "source": "secret-db", "target": "sec-rbac"},
                {"id": "e7", "source": "opa", "target": "cui-db", "label": "enforce policy"},
                {"id": "e8", "source": "opa", "target": "secret-db", "label": "enforce policy"},
                {"id": "e9", "source": "catalog", "target": "cui-db", "label": "catalog sync"},
                {"id": "e10", "source": "catalog", "target": "secret-db", "label": "catalog sync"},
            ],
            "boundaries": [
                {"id": "cui-zone", "label": "CUI // SP-CTI Zone", "type": "bnd-classification", "contained_nodes": ["cui-db", "cui-files"], "x": 30, "y": 120, "width": 300, "height": 300},
                {"id": "secret-zone", "label": "SECRET // NOFORN Zone", "type": "bnd-classification", "contained_nodes": ["secret-db", "secret-cache"], "x": 530, "y": 120, "width": 300, "height": 300},
                {"id": "govcloud", "label": "AWS GovCloud (IL5)", "type": "bnd-region", "contained_nodes": ["cui-db", "cui-files"], "x": 20, "y": 110, "width": 320, "height": 320},
                {"id": "sipr", "label": "SIPR Enclave (IL6)", "type": "bnd-region", "contained_nodes": ["secret-db", "secret-cache"], "x": 520, "y": 110, "width": 320, "height": 320},
            ],
        }),
        "template_id": "tpl-ddc-multi-classification",
    },
    {
        "id": _DESIGN_IDS["ddc-design-002"],
        "name": "OHDSI OMOP CDM v5.4 (VA Healthcare)",
        "description": "OHDSI Common Data Model for VA healthcare data interoperability with Person, Visit, Condition, Drug, Measurement, and Observation domains.",
        "graph_json": json.dumps({
            "nodes": [
                {"id": "person", "type": "ent-table", "label": "person", "x": 220, "y": 190},
                {"id": "visit", "type": "ent-table", "label": "visit_occurrence", "x": 530, "y": 190},
                {"id": "condition", "type": "ent-table", "label": "condition_occurrence", "x": 100, "y": 370},
                {"id": "drug", "type": "ent-table", "label": "drug_exposure", "x": 310, "y": 370},
                {"id": "measurement", "type": "ent-table", "label": "measurement", "x": 510, "y": 370},
                {"id": "observation", "type": "ent-table", "label": "observation", "x": 710, "y": 370},
                {"id": "concept", "type": "ent-table", "label": "concept (SNOMED/ICD/RxNorm)", "x": 400, "y": 70},
                {"id": "rbac", "type": "ctrl-rbac", "label": "HIPAA RBAC", "x": 350, "y": 500},
                {"id": "enc", "type": "ctrl-encryption", "label": "AES-256 TDE", "x": 150, "y": 500},
                {"id": "mask", "type": "ctrl-masking", "label": "PHI Masking", "x": 50, "y": 470},
                {"id": "auditlog", "type": "ctrl-audit-log", "label": "HIPAA Audit Log", "x": 560, "y": 500},
            ],
            "edges": [
                {"id": "e1", "source": "visit", "target": "person", "label": "person_id FK"},
                {"id": "e2", "source": "condition", "target": "person", "label": "person_id FK"},
                {"id": "e3", "source": "drug", "target": "person", "label": "person_id FK"},
                {"id": "e4", "source": "measurement", "target": "person", "label": "person_id FK"},
                {"id": "e5", "source": "observation", "target": "person", "label": "person_id FK"},
                {"id": "e6", "source": "concept", "target": "condition", "label": "standardize (ICD->SNOMED)", "type": "flow-etl"},
                {"id": "e7", "source": "concept", "target": "drug", "label": "standardize (NDC->RxNorm)", "type": "flow-etl"},
                {"id": "e8", "source": "person", "target": "rbac"},
                {"id": "e9", "source": "person", "target": "enc"},
                {"id": "e10", "source": "person", "target": "mask"},
                {"id": "e11", "source": "person", "target": "auditlog"},
            ],
            "boundaries": [
                {"id": "phi-zone", "label": "HIPAA PHI Zone", "type": "bnd-classification", "contained_nodes": ["person", "visit", "condition", "drug", "measurement", "observation"], "x": 50, "y": 100, "width": 740, "height": 310},
                {"id": "us-hipaa", "label": "US Healthcare Data Residency (VA/HHS)", "type": "bnd-region", "contained_nodes": ["person", "visit", "condition", "drug", "measurement", "observation"], "x": 40, "y": 90, "width": 760, "height": 330},
            ],
        }),
        "template_id": "tpl-ddc-ohdsi-cdm",
    },
    {
        "id": _DESIGN_IDS["ddc-design-003"],
        "name": "ML Feature Store with Lineage",
        "description": "Feature engineering pipeline: raw events -> computed feature store -> model registry with quality gates, freshness guardian, and data lineage.",
        "graph_json": json.dumps({
            "nodes": [
                {"id": "raw-events", "type": "ent-table", "label": "raw_events", "x": 100, "y": 150},
                {"id": "feat-store", "type": "ent-feature-store", "label": "feature_store", "x": 350, "y": 150},
                {"id": "model-reg", "type": "ent-model-registry", "label": "model_registry", "x": 600, "y": 150},
                {"id": "feat-eng", "type": "flow-etl", "label": "Feature Engineering", "x": 225, "y": 370},
                {"id": "train-pipe", "type": "flow-etl", "label": "Training Pipeline", "x": 475, "y": 370},
                {"id": "quality", "type": "twin-quality-gate", "label": "Quality Gate", "x": 350, "y": 450},
                {"id": "freshness", "type": "ctrl-retention", "label": "Freshness Guardian", "x": 100, "y": 350},
                {"id": "lineage", "type": "twin-lineage", "label": "Data Lineage", "x": 600, "y": 350},
                {"id": "rbac", "type": "ctrl-rbac", "label": "RBAC Policy", "x": 350, "y": 50},
            ],
            "edges": [
                {"id": "e1", "source": "raw-events", "target": "feat-eng", "label": "feature engineer", "type": "flow-etl"},
                {"id": "e2", "source": "feat-eng", "target": "feat-store", "label": "load", "type": "flow-etl"},
                {"id": "e3", "source": "feat-store", "target": "train-pipe", "label": "train", "type": "flow-etl"},
                {"id": "e4", "source": "train-pipe", "target": "model-reg", "label": "register", "type": "flow-etl"},
                {"id": "e5", "source": "raw-events", "target": "freshness"},
                {"id": "e6", "source": "feat-store", "target": "quality"},
                {"id": "e7", "source": "feat-store", "target": "lineage"},
                {"id": "e8", "source": "model-reg", "target": "lineage"},
                {"id": "e9", "source": "rbac", "target": "feat-store", "label": "policy"},
                {"id": "e10", "source": "rbac", "target": "model-reg", "label": "policy"},
            ],
            "boundaries": [
                {"id": "ml-zone", "label": "ML Platform Zone", "type": "bnd-schema", "contained_nodes": ["raw-events", "feat-store", "model-reg"], "x": 50, "y": 30, "width": 730, "height": 320},
                {"id": "cui-zone", "label": "CUI // SP-CTI Zone", "type": "bnd-classification", "contained_nodes": ["raw-events"], "x": 60, "y": 100, "width": 150, "height": 150},
            ],
        }),
        "template_id": "tpl-ddc-ml-feature-store",
    },
    {
        "id": _DESIGN_IDS["ddc-design-004"],
        "name": "Federated Data Mesh Governance",
        "description": "Federated governance hub with OPA policy engine, global metadata catalog, cross-domain audit, domain maturity scoring, and OpenLineage emitter.",
        "graph_json": json.dumps({
            "nodes": [
                {"id": "opa-engine", "type": "ctrl-classification", "label": "OPA Policy Engine", "x": 300, "y": 100},
                {"id": "global-cat", "type": "twin-catalog", "label": "Global Metadata Catalog", "x": 300, "y": 280},
                {"id": "domain-a", "type": "ent-domain", "label": "Domain A (Products)", "x": 80, "y": 440},
                {"id": "domain-b", "type": "ent-domain", "label": "Domain B (Analytics)", "x": 300, "y": 440},
                {"id": "domain-c", "type": "ent-domain", "label": "Domain C (ML)", "x": 520, "y": 440},
                {"id": "audit", "type": "ctrl-audit-log", "label": "Cross-Domain Audit Log", "x": 300, "y": 550},
                {"id": "lineage", "type": "twin-lineage", "label": "OpenLineage Emitter", "x": 550, "y": 280},
                {"id": "quality-hub", "type": "twin-quality-gate", "label": "Quality Score Hub", "x": 300, "y": 190},
                {"id": "dlp", "type": "ctrl-dlp", "label": "DLP Egress Filter", "x": 550, "y": 100},
                {"id": "rbac", "type": "ctrl-rbac", "label": "Federation RBAC", "x": 300, "y": 640},
            ],
            "edges": [
                {"id": "e1", "source": "opa-engine", "target": "domain-a", "label": "enforce policy"},
                {"id": "e2", "source": "opa-engine", "target": "domain-b", "label": "enforce policy"},
                {"id": "e3", "source": "opa-engine", "target": "domain-c", "label": "enforce policy"},
                {"id": "e4", "source": "global-cat", "target": "domain-a", "label": "catalog sync"},
                {"id": "e5", "source": "global-cat", "target": "domain-b", "label": "catalog sync"},
                {"id": "e6", "source": "global-cat", "target": "domain-c", "label": "catalog sync"},
                {"id": "e7", "source": "global-cat", "target": "lineage"},
                {"id": "e8", "source": "quality-hub", "target": "domain-a"},
                {"id": "e9", "source": "quality-hub", "target": "domain-b"},
                {"id": "e10", "source": "quality-hub", "target": "domain-c"},
                {"id": "e11", "source": "domain-a", "target": "audit"},
                {"id": "e12", "source": "domain-b", "target": "audit"},
                {"id": "e13", "source": "domain-c", "target": "audit"},
                {"id": "e14", "source": "dlp", "target": "domain-c", "label": "egress filter"},
                {"id": "e15", "source": "rbac", "target": "opa-engine", "label": "admin policy"},
            ],
            "boundaries": [
                {"id": "federation-zone", "label": "Federation Layer", "type": "bnd-classification", "contained_nodes": ["opa-engine", "global-cat", "quality-hub", "lineage"], "x": 200, "y": 60, "width": 420, "height": 270},
                {"id": "domain-ring", "label": "Domain Ring", "type": "bnd-tenant", "contained_nodes": ["domain-a", "domain-b", "domain-c"], "x": 20, "y": 400, "width": 570, "height": 110},
            ],
        }),
        "template_id": "tpl-ddc-federated-governance",
    },
]

_ASSESSMENTS = []
for i, design in enumerate(_DATA_DESIGNS):
    score = random.uniform(75.0, 98.0)
    _ASSESSMENTS.append({
        "id": _uid(),
        "design_id": design["id"],
        "assessment_type": random.choice(["compliance", "security", "coverage", "performance"]),
        "findings_json": json.dumps([
            {"severity": "high", "check": "pii_masking", "finding": "3 PII columns lack masking controls"},
            {"severity": "medium", "check": "lineage_coverage", "finding": "Lineage missing for 2 ETL nodes"},
            {"severity": "low", "check": "backup_policy", "finding": "Backup RPO exceeds 1 hour"},
        ]),
        "score": round(score, 1),
    })

_NODES = []
for i, design in enumerate(_DATA_DESIGNS):
    g = json.loads(design["graph_json"])
    for node in g.get("nodes", []):
        _NODES.append({
            "id": _uid(),
            "design_id": design["id"],
            "node_type": node["type"],
            "label": node["label"],
            "x": node.get("x", 0),
            "y": node.get("y", 0),
            "properties_json": json.dumps({"type": node["type"], "original_id": node["id"]}),
        })

_EDGES = []
for i, design in enumerate(_DATA_DESIGNS):
    g = json.loads(design["graph_json"])
    for edge in g.get("edges", []):
        _EDGES.append({
            "id": _uid(),
            "design_id": design["id"],
            "source_node_id": edge["source"],
            "target_node_id": edge["target"],
            "edge_type": edge.get("type", ""),
            "label": edge.get("label", ""),
        })

_LINEAGE = []
for i, design in enumerate(_DATA_DESIGNS):
    g = json.loads(design["graph_json"])
    for edge in g.get("edges", [])[:3]:
        _LINEAGE.append({
            "id": _uid(),
            "design_id": design["id"],
            "source_node_id": edge["source"],
            "target_node_id": edge["target"],
            "lineage_type": random.choice(["flow", "etl", "cdc"]),
            "column_name": random.choice(["id", "user_id", "created_at", "amount", "status"]),
            "transform_desc": f"Transformed via {random.choice(['SQL', 'Python', 'Spark', 'dbt'])}",
        })

_TWIN_SNAPSHOTS = []
for i, design in enumerate(_DATA_DESIGNS):
    g = json.loads(design["graph_json"])
    _TWIN_SNAPSHOTS.append({
        "id": _uid(),
        "design_id": design["id"],
        "label": f"Snapshot v{i+1}",
        "table_count": len(g.get("nodes", [])),
        "edge_count": len(g.get("edges", [])),
    })

_RUNBOOK_EXECUTIONS = []
rb_ids = ["rb-ddc-pii-exposure", "rb-ddc-lineage-break", "rb-ddc-classification-mismatch", "rb-ddc-retention-violation"]
for i in range(6):
    _RUNBOOK_EXECUTIONS.append({
        "id": _uid(),
        "runbook_id": rb_ids[i % len(rb_ids)],
        "triggered_by": random.choice(["system", "admin", "scheduler"]),
        "status": random.choice(["completed", "completed", "in_progress", "failed"]),
        "notes": f"Execution {i+1} notes",
        "started_at": _ts(i * 8),
        "completed_at": _ts(i * 8 + 2) if random.random() > 0.3 else None,
    })

_SOP_APPROVALS = []
sop_ids = ["sop-ddc-data-classification-review", "sop-ddc-retention-policy-enforcement", "sop-ddc-pii-handling-procedure", "sop-ddc-backup-verification"]
for i in range(8):
    _SOP_APPROVALS.append({
        "id": _uid(),
        "sop_id": sop_ids[i % len(sop_ids)],
        "reviewer": random.choice(["issm-smith", "data-steward-jones", "compliance-officer"]),
        "action": random.choice(["approved", "approved", "rejected", "commented"]),
        "comment": f"Review comment {i+1}",
    })

_EXPLORE_SESSIONS = []
for i in range(4):
    _EXPLORE_SESSIONS.append({
        "id": _uid(),
        "design_id": _DATA_DESIGNS[i % len(_DATA_DESIGNS)]["id"],
        "user": random.choice(["analyst-smith", "data-scientist-lee", "dba-brown"]),
        "db_conn_json": json.dumps({"host": "localhost", "port": 5432, "database": f"db_{i}"}),
        "status": random.choice(["completed", "running", "failed"]),
    })

_EXPLORE_PROFILES = []
for i in range(4):
    _EXPLORE_PROFILES.append({
        "id": _uid(),
        "design_id": _DATA_DESIGNS[i % len(_DATA_DESIGNS)]["id"],
        "session_id": _EXPLORE_SESSIONS[i]["id"],
        "db_conn_json": json.dumps({"host": "localhost", "port": 5432}),
        "profile_json": json.dumps({"tables": random.randint(5, 20), "columns": random.randint(50, 200)}),
        "table_count": random.randint(5, 20),
        "anomaly_json": json.dumps({"outliers": random.randint(0, 5)}),
    })

_ANOMALY_RUNS = []
for i in range(4):
    _ANOMALY_RUNS.append({
        "id": _uid(),
        "profile_id": _EXPLORE_PROFILES[i]["id"],
        "findings_json": json.dumps([{"column": "amount", "deviation": round(random.uniform(0.1, 2.0), 2)}]),
        "overall_risk": random.choice(["low", "medium", "high"]),
        "classification": "CUI",
        "created_at": _ts(i * 5),
    })

_QUERY_HISTORY = []
for i in range(10):
    _QUERY_HISTORY.append({
        "id": _uid(),
        "design_id": _DATA_DESIGNS[i % len(_DATA_DESIGNS)]["id"],
        "user": random.choice(["analyst-smith", "data-scientist-lee"]),
        "sql_text": f"SELECT * FROM {random.choice(['users', 'orders', 'events', 'metrics'])} WHERE created_at > '2024-01-01'",
        "db_conn_json": json.dumps({"host": "localhost", "port": 5432}),
        "row_count": random.randint(10, 10000),
        "exec_ms": random.randint(50, 5000),
    })

_QUALITY_RULES = []
for i in range(8):
    _QUALITY_RULES.append({
        "id": _uid(),
        "design_id": _DATA_DESIGNS[i % len(_DATA_DESIGNS)]["id"],
        "name": f"Rule {i+1}: {random.choice(['Completeness', 'Uniqueness', 'Range', 'Pattern', 'Freshness'])}",
        "table_name": random.choice(["users", "orders", "events", "metrics", "products"]),
        "column_name": random.choice(["id", "email", "amount", "status", "created_at"]),
        "check_type": random.choice(["completeness", "uniqueness", "range", "pattern", "freshness"]),
        "threshold": round(random.uniform(80.0, 99.0), 1),
        "params_json": json.dumps({"window": "7d", "tolerance": 0.05}),
    })

_QUALITY_RUNS = []
for i in range(8):
    _QUALITY_RUNS.append({
        "id": _uid(),
        "rule_id": _QUALITY_RULES[i]["id"],
        "db_conn_json": json.dumps({"host": "localhost", "port": 5432}),
        "passed": 1 if random.random() > 0.3 else 0,
        "actual_value": round(random.uniform(70.0, 100.0), 1),
        "threshold": _QUALITY_RULES[i]["threshold"],
        "detail": f"Quality run {i+1} detail",
    })

_FRESHNESS_ALERTS = []
for i in range(5):
    _FRESHNESS_ALERTS.append({
        "id": _uid(),
        "rule_id": _QUALITY_RULES[i]["id"],
        "design_id": _DATA_DESIGNS[i % len(_DATA_DESIGNS)]["id"],
        "db_conn_json": json.dumps({"host": "localhost", "port": 5432}),
        "last_checked": _ts(i * 4),
        "passed": 1 if random.random() > 0.4 else 0,
        "actual_max_value": _ts(i * 4 - 2),
        "cutoff_value": _ts(i * 4 - 24),
        "detail": f"Freshness check {i+1}",
    })

_MIGRATION_JOBS = []
for i in range(5):
    _MIGRATION_JOBS.append({
        "id": _uid(),
        "design_id": _DATA_DESIGNS[i % len(_DATA_DESIGNS)]["id"],
        "source_type": random.choice(["oracle", "mysql", "mssql", "mongodb", "postgres"]),
        "target_type": random.choice(["postgres", "redshift", "s3", "dynamodb"]),
        "migration_tool": random.choice(["dms", "pgloader", "aws_glue", "manual"]),
        "status": random.choice(["pending", "running", "complete", "failed"]),
        "row_count_source": random.randint(100000, 10000000),
        "row_count_target": random.randint(100000, 10000000),
        "validation_query": "SELECT COUNT(*) FROM target_table",
        "validation_status": random.choice(["pending", "pass", "fail"]),
        "config_json": json.dumps({"parallel_workers": 4, "batch_size": 10000}),
        "notes": f"Migration job {i+1}",
        "started_at": _ts(i * 12),
        "completed_at": _ts(i * 12 + 6) if random.random() > 0.3 else None,
    })


def seed_data_designs(conn) -> int:
    sql = """INSERT OR IGNORE INTO data_designs (
        id, name, description, graph_json, template_id, classification, created_at, updated_at
    ) VALUES (?,?,?,?,?,?,?,?)"""
    count = 0
    for row in _DATA_DESIGNS:
        _safe_execute(conn, sql, (
            row["id"], row["name"], row["description"], row["graph_json"],
            row["template_id"], "CUI", _ts(count * 3), _ts(count * 3),
        ))
        count += 1
    return count


def seed_assessments(conn) -> int:
    sql = """INSERT OR IGNORE INTO dd_assessments (
        id, design_id, assessment_type, findings_json, score, created_at
    ) VALUES (?,?,?,?,?,?)"""
    count = 0
    for row in _ASSESSMENTS:
        _safe_execute(conn, sql, (
            row["id"], row["design_id"], row["assessment_type"],
            row["findings_json"], row["score"], _ts(count * 4),
        ))
        count += 1
    return count


def seed_audit(conn) -> int:
    sql = """INSERT OR IGNORE INTO dd_audit (
        design_id, user, action, detail, classification, created_at
    ) VALUES (?,?,?,?,?,?)"""
    count = 0
    actions = [
        ("design_created", "Data design created"),
        ("assessment_run", "Data quality assessment executed"),
        ("node_added", "New data node added to design"),
        ("lineage_updated", "Data lineage graph updated"),
        ("migration_started", "Data migration job started"),
    ]
    for i in range(10):
        design_id = _DATA_DESIGNS[i % len(_DATA_DESIGNS)]["id"]
        action, detail = actions[i % len(actions)]
        _safe_execute(conn, sql, (
            design_id, random.choice(["data-steward", "dba-jones", "system"]),
            action, detail, "CUI // SP-CTI", _ts(i * 2),
        ))
        count += 1
    return count


def seed_versions(conn) -> int:
    sql = """INSERT OR IGNORE INTO dd_versions (
        id, design_id, version_number, graph_json, change_summary, user_id, created_at
    ) VALUES (?,?,?,?,?,?,?)"""
    count = 0
    for i in range(8):
        design_id = _DATA_DESIGNS[i % len(_DATA_DESIGNS)]["id"]
        _safe_execute(conn, sql, (
            _uid(), design_id, i + 1, _DATA_DESIGNS[i % len(_DATA_DESIGNS)]["graph_json"],
            f"Version {i+1}: updated data model", "system", _ts(i * 5),
        ))
        count += 1
    return count


def seed_collab_sessions(conn) -> int:
    sql = """INSERT OR IGNORE INTO dd_collab_sessions (
        id, design_id, user_id, user_name, color, joined_at, last_seen, is_active
    ) VALUES (?,?,?,?,?,?,?,?)"""
    count = 0
    users = [
        ("user-001", "Alice Smith", "#3498db"),
        ("user-002", "Bob Jones", "#e74c3c"),
        ("user-003", "Carol Lee", "#2ecc71"),
        ("user-004", "David Brown", "#f39c12"),
    ]
    for i in range(4):
        design_id = _DATA_DESIGNS[i % len(_DATA_DESIGNS)]["id"]
        user_id, user_name, color = users[i]
        _safe_execute(conn, sql, (
            _uid(), design_id, user_id, user_name, color,
            _ts(i * 6), _ts(i * 6 + 2), 1 if random.random() > 0.3 else 0,
        ))
        count += 1
    return count


def seed_lineage(conn) -> int:
    sql = """INSERT OR IGNORE INTO dd_lineage (
        id, design_id, source_node_id, target_node_id, lineage_type, column_name, transform_desc, classification, created_at
    ) VALUES (?,?,?,?,?,?,?,?,?)"""
    count = 0
    for row in _LINEAGE:
        _safe_execute(conn, sql, (
            row["id"], row["design_id"], row["source_node_id"], row["target_node_id"],
            row["lineage_type"], row["column_name"], row["transform_desc"], "CUI", _ts(count * 2),
        ))
        count += 1
    return count


def seed_data_nodes(conn) -> int:
    sql = """INSERT OR IGNORE INTO data_nodes (
        id, design_id, node_type, label, x, y, classification, properties_json, created_at
    ) VALUES (?,?,?,?,?,?,?,?,?)"""
    count = 0
    for row in _NODES:
        _safe_execute(conn, sql, (
            row["id"], row["design_id"], row["node_type"], row["label"],
            row["x"], row["y"], "CUI", row["properties_json"], _ts(count * 0.5),
        ))
        count += 1
    return count


def seed_data_edges(conn) -> int:
    sql = """INSERT OR IGNORE INTO data_edges (
        id, design_id, source_node_id, target_node_id, edge_type, label, classification, created_at
    ) VALUES (?,?,?,?,?,?,?,?)"""
    count = 0
    for row in _EDGES:
        _safe_execute(conn, sql, (
            row["id"], row["design_id"], row["source_node_id"], row["target_node_id"],
            row["edge_type"], row["label"], "CUI", _ts(count * 0.5),
        ))
        count += 1
    return count


def seed_twin_snapshots(conn) -> int:
    sql = """INSERT OR IGNORE INTO data_twin_snapshots (
        id, design_id, label, table_count, edge_count, classification, created_at
    ) VALUES (?,?,?,?,?,?,?)"""
    count = 0
    for row in _TWIN_SNAPSHOTS:
        _safe_execute(conn, sql, (
            row["id"], row["design_id"], row["label"], row["table_count"],
            row["edge_count"], "CUI", _ts(count * 4),
        ))
        count += 1
    return count


def seed_runbook_executions(conn) -> int:
    sql = """INSERT OR IGNORE INTO ddc_runbook_executions (
        id, runbook_id, triggered_by, status, notes, started_at, completed_at
    ) VALUES (?,?,?,?,?,?,?)"""
    count = 0
    for row in _RUNBOOK_EXECUTIONS:
        _safe_execute(conn, sql, (
            row["id"], row["runbook_id"], row["triggered_by"], row["status"],
            row["notes"], row["started_at"], row["completed_at"],
        ))
        count += 1
    return count


def seed_sop_approvals(conn) -> int:
    sql = """INSERT OR IGNORE INTO ddc_sop_approvals (
        id, sop_id, reviewer, action, comment, created_at
    ) VALUES (?,?,?,?,?,?)"""
    count = 0
    for row in _SOP_APPROVALS:
        _safe_execute(conn, sql, (
            row["id"], row["sop_id"], row["reviewer"], row["action"],
            row["comment"], _ts(count * 3),
        ))
        count += 1
    return count


def seed_explore_sessions(conn) -> int:
    sql = """INSERT OR IGNORE INTO dd_explore_sessions (
        id, design_id, user, db_conn_json, status, classification, created_at
    ) VALUES (?,?,?,?,?,?,?)"""
    count = 0
    for row in _EXPLORE_SESSIONS:
        _safe_execute(conn, sql, (
            row["id"], row["design_id"], row["user"], row["db_conn_json"],
            row["status"], "CUI // SP-CTI", _ts(count * 5),
        ))
        count += 1
    return count


def seed_explore_profiles(conn) -> int:
    sql = """INSERT OR IGNORE INTO dd_explore_profiles (
        id, design_id, session_id, db_conn_json, profile_json, table_count, anomaly_json, classification, created_at
    ) VALUES (?,?,?,?,?,?,?,?,?)"""
    count = 0
    for row in _EXPLORE_PROFILES:
        _safe_execute(conn, sql, (
            row["id"], row["design_id"], row["session_id"], row["db_conn_json"],
            row["profile_json"], row["table_count"], row["anomaly_json"],
            "CUI // SP-CTI", _ts(count * 5),
        ))
        count += 1
    return count


def seed_anomaly_runs(conn) -> int:
    sql = """INSERT OR IGNORE INTO dd_anomaly_runs (
        id, profile_id, findings_json, overall_risk, classification, created_at
    ) VALUES (?,?,?,?,?,?)"""
    count = 0
    for row in _ANOMALY_RUNS:
        _safe_execute(conn, sql, (
            row["id"], row["profile_id"], row["findings_json"],
            row["overall_risk"], row["classification"], row["created_at"],
        ))
        count += 1
    return count


def seed_query_history(conn) -> int:
    sql = """INSERT OR IGNORE INTO dd_query_history (
        id, design_id, user, sql_text, db_conn_json, row_count, exec_ms, classification, created_at
    ) VALUES (?,?,?,?,?,?,?,?,?)"""
    count = 0
    for row in _QUERY_HISTORY:
        _safe_execute(conn, sql, (
            row["id"], row["design_id"], row["user"], row["sql_text"],
            row["db_conn_json"], row["row_count"], row["exec_ms"],
            "CUI // SP-CTI", _ts(count * 1),
        ))
        count += 1
    return count


def seed_quality_rules(conn) -> int:
    sql = """INSERT OR IGNORE INTO dd_quality_rules (
        id, design_id, name, table_name, column_name, check_type, threshold, params_json, classification, enabled, created_at
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?)"""
    count = 0
    for row in _QUALITY_RULES:
        _safe_execute(conn, sql, (
            row["id"], row["design_id"], row["name"], row["table_name"],
            row["column_name"], row["check_type"], row["threshold"],
            row["params_json"], "CUI // SP-CTI", 1, _ts(count * 2),
        ))
        count += 1
    return count


def seed_quality_runs(conn) -> int:
    sql = """INSERT OR IGNORE INTO dd_quality_runs (
        id, rule_id, db_conn_json, passed, actual_value, threshold, detail, classification, created_at
    ) VALUES (?,?,?,?,?,?,?,?,?)"""
    count = 0
    for row in _QUALITY_RUNS:
        _safe_execute(conn, sql, (
            row["id"], row["rule_id"], row["db_conn_json"], row["passed"],
            row["actual_value"], row["threshold"], row["detail"],
            "CUI // SP-CTI", _ts(count * 3),
        ))
        count += 1
    return count


def seed_freshness_alerts(conn) -> int:
    sql = """INSERT OR IGNORE INTO dd_freshness_alerts (
        id, rule_id, design_id, db_conn_json, last_checked, passed, actual_max_value, cutoff_value, detail, classification, created_at
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?)"""
    count = 0
    for row in _FRESHNESS_ALERTS:
        _safe_execute(conn, sql, (
            row["id"], row["rule_id"], row["design_id"], row["db_conn_json"],
            row["last_checked"], row["passed"], row["actual_max_value"],
            row["cutoff_value"], row["detail"], "CUI // SP-CTI", _ts(count * 4),
        ))
        count += 1
    return count


def seed_migration_jobs(conn) -> int:
    sql = """INSERT OR IGNORE INTO dd_migration_jobs (
        id, design_id, source_type, target_type, migration_tool, status,
        row_count_source, row_count_target, validation_query, validation_status,
        config_json, notes, started_at, completed_at, created_at
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
    count = 0
    for row in _MIGRATION_JOBS:
        _safe_execute(conn, sql, (
            row["id"], row["design_id"], row["source_type"], row["target_type"],
            row["migration_tool"], row["status"], row["row_count_source"],
            row["row_count_target"], row["validation_query"], row["validation_status"],
            row["config_json"], row["notes"], row["started_at"], row["completed_at"],
            _ts(count * 6),
        ))
        count += 1
    return count


def verify(conn) -> dict:
    result = {}
    for tbl in (
        "data_designs", "dd_assessments", "dd_audit", "dd_versions",
        "dd_collab_sessions", "dd_lineage", "data_nodes", "data_edges",
        "data_twin_snapshots", "ddc_runbook_executions", "ddc_sop_approvals",
        "dd_explore_sessions", "dd_explore_profiles", "dd_anomaly_runs",
        "dd_query_history", "dd_quality_rules", "dd_quality_runs",
        "dd_freshness_alerts", "dd_migration_jobs",
    ):
        try:
            row = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()
            result[tbl] = row[0]
        except Exception as exc:
            result[tbl] = f"error: {exc}"
    return result


def main():
    parser = argparse.ArgumentParser(description="DDC Demo Seed")
    parser.add_argument("--reset", action="store_true", help="Clear existing demo data")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument("--verify", action="store_true", help="Only verify counts")
    args = parser.parse_args()

    conn = _get_conn()
    try:
        if args.verify:
            result = verify(conn)
            print(json.dumps(result, indent=2) if args.json else result)
            return

        if args.reset:
            _reset_demo_data(conn)

        counts = {
            "data_designs": seed_data_designs(conn),
            "dd_assessments": seed_assessments(conn),
            "dd_audit": seed_audit(conn),
            "dd_versions": seed_versions(conn),
            "dd_collab_sessions": seed_collab_sessions(conn),
            "dd_lineage": seed_lineage(conn),
            "data_nodes": seed_data_nodes(conn),
            "data_edges": seed_data_edges(conn),
            "data_twin_snapshots": seed_twin_snapshots(conn),
            "ddc_runbook_executions": seed_runbook_executions(conn),
            "ddc_sop_approvals": seed_sop_approvals(conn),
            "dd_explore_sessions": seed_explore_sessions(conn),
            "dd_explore_profiles": seed_explore_profiles(conn),
            "dd_anomaly_runs": seed_anomaly_runs(conn),
            "dd_query_history": seed_query_history(conn),
            "dd_quality_rules": seed_quality_rules(conn),
            "dd_quality_runs": seed_quality_runs(conn),
            "dd_freshness_alerts": seed_freshness_alerts(conn),
            "dd_migration_jobs": seed_migration_jobs(conn),
        }
        conn.commit()

        if args.json:
            print(json.dumps({"success": True, "seeded": counts, "verify": verify(conn)}, indent=2))
        else:
            print(f"[seed_ddc] Seeded {counts}")
            print(f"[seed_ddc] Verify: {verify(conn)}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
