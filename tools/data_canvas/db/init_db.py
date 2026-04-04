# CUI // SP-CTI
"""Data Design Canvas — DB initializer.

Creates schema and seeds 6 canonical data model templates.

Dual-backend: SQLite (default) or PostgreSQL.
Set DDC_STORAGE_BACKEND=postgresql + DDC_PG_* env vars to use PostgreSQL.
SQLite is the default for dev, air-gap, and single-user deployments.
"""
import json
import os
import sqlite3
import uuid
from pathlib import Path

# When integrated into ICDEV, DB lives in data/ directory
_ICDEV_ROOT = Path(__file__).resolve().parents[3]  # tools/data_canvas/db -> ICDev root
DB_PATH = _ICDEV_ROOT / "data" / "data_canvas.db"

# Backend detection
_DDC_BACKEND = os.environ.get("DDC_STORAGE_BACKEND", "sqlite").lower()


def get_connection():
    """Get a database connection — SQLite or PostgreSQL.

    Returns a connection that supports:
        conn.execute(sql, params) — with ? placeholders (auto-translated for PG)
        conn.commit()
        conn.close()
        row["column_name"] — dict-like row access
    """
    if _DDC_BACKEND == "postgresql":
        try:
            from tools.db.storage import get_connection as _icdev_conn
            conn = _icdev_conn(
                db_path=os.environ.get("DDC_PG_DATABASE", "data_canvas")
            )
            return conn
        except ImportError:
            pass  # Fall through to SQLite
    # SQLite (default)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS data_designs (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT,
    graph_json      TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[],"boundaries":[]}',
    template_id     TEXT,
    classification  TEXT DEFAULT 'CUI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS dd_templates (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    category      TEXT,
    description   TEXT,
    graph_json    TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[],"boundaries":[]}',
    tags          TEXT DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS dd_snippets (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    category    TEXT,
    description TEXT,
    graph_json  TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[],"boundaries":[]}',
    tags        TEXT DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS dd_assessments (
    id              TEXT PRIMARY KEY,
    design_id       TEXT REFERENCES data_designs(id),
    assessment_type TEXT NOT NULL,
    findings_json   TEXT DEFAULT '[]',
    score           REAL DEFAULT 0,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS dd_audit (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    design_id       TEXT,
    user            TEXT,
    action          TEXT NOT NULL,
    detail          TEXT,
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS dd_versions (
    id              TEXT PRIMARY KEY,
    design_id       TEXT REFERENCES data_designs(id),
    version_number  INTEGER NOT NULL,
    graph_json      TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[],"boundaries":[]}',
    change_summary  TEXT DEFAULT '',
    user_id         TEXT DEFAULT '',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_dd_versions_design ON dd_versions(design_id);

CREATE TABLE IF NOT EXISTS dd_collab_sessions (
    id          TEXT PRIMARY KEY,
    design_id   TEXT NOT NULL REFERENCES data_designs(id) ON DELETE CASCADE,
    user_id     TEXT NOT NULL,
    user_name   TEXT NOT NULL DEFAULT '',
    color       TEXT NOT NULL DEFAULT '#3498db',
    joined_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    is_active   INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_dd_collab_design ON dd_collab_sessions(design_id);

-- Immutability triggers (SQLite)
CREATE TRIGGER IF NOT EXISTS dd_audit_no_update
    BEFORE UPDATE ON dd_audit
    BEGIN
        SELECT RAISE(ABORT, 'Audit records are immutable — NIST AU-6');
    END;

CREATE TRIGGER IF NOT EXISTS dd_audit_no_delete
    BEFORE DELETE ON dd_audit
    BEGIN
        SELECT RAISE(ABORT, 'Audit records cannot be deleted');
    END;
"""


# ── Template seeds ────────────────────────────────────────────────────────────

def _node(nid, label, ntype, x, y, extra=None):
    n = {"id": nid, "label": label, "type": ntype, "x": x, "y": y}
    if extra:
        n.update(extra)
    return n


def _edge(src, dst, label="", edge_type=""):
    e = {"id": str(uuid.uuid4())[:8], "source": src, "target": dst, "label": label}
    if edge_type:
        e["type"] = edge_type
    return e


def _boundary(bid, label, btype, contained_nodes, x=0, y=0, width=300, height=250):
    return {"id": bid, "label": label, "type": btype, "contained_nodes": contained_nodes,
            "x": x, "y": y, "width": width, "height": height}


TEMPLATES = [
    # 1 — OLTP Microservice (PostgreSQL)
    {
        "id": "tpl-ddc-oltp-microservice",
        "name": "OLTP Microservice (PostgreSQL)",
        "category": "Relational",
        "description": "PostgreSQL tables with PK/FK, PII columns, RBAC, audit logging, and CUI classification zone.",
        "tags": json.dumps(["postgresql", "oltp", "microservice", "cui", "pii"]),
        "graph_json": json.dumps({
            "nodes": [
                _node("users-tbl", "users", "ent-table", 200, 150),
                _node("orders-tbl", "orders", "ent-table", 500, 150),
                _node("audit-tbl", "audit_log", "ent-table", 350, 350),
                _node("users-pk", "id (PK)", "col-pk", 80, 80),
                _node("users-email", "email (PII)", "col-pii", 80, 150),
                _node("users-name", "full_name (PII)", "col-pii", 80, 220),
                _node("users-cui", "clearance_level (CUI)", "col-cui", 80, 290),
                _node("users-audit", "created_at", "col-audit", 200, 290),
                _node("orders-pk", "id (PK)", "col-pk", 620, 80),
                _node("orders-fk", "user_id (FK)", "col-fk", 620, 150),
                _node("orders-data", "amount", "col-data", 620, 220),
                _node("orders-audit", "updated_at", "col-audit", 500, 290),
                _node("rbac", "RBAC Policy", "ctrl-rbac", 350, 50),
                _node("enc", "AES-256 TDE", "ctrl-encryption", 200, 450),
                _node("mask", "PII Masking", "ctrl-masking", 80, 400),
                _node("auditlog", "Audit Logging", "ctrl-audit-log", 500, 450),
                _node("retention", "7-Year Retention", "ctrl-retention", 350, 500),
                _node("backup", "Daily Backup (RPO 1h)", "ctrl-backup-policy", 200, 550),
                _node("api-flow", "REST API", "flow-api", 350, 150),
                _node("iac-flyway", "Flyway (Schema Migration)", "flow-etl", 350, 620),
            ],
            "edges": [
                _edge("users-tbl", "users-pk"),
                _edge("users-tbl", "users-email"),
                _edge("users-tbl", "users-name"),
                _edge("users-tbl", "users-cui"),
                _edge("users-tbl", "users-audit"),
                _edge("orders-tbl", "orders-pk"),
                _edge("orders-tbl", "orders-fk"),
                _edge("orders-tbl", "orders-data"),
                _edge("orders-tbl", "orders-audit"),
                _edge("orders-fk", "users-pk", "FK ref"),
                _edge("users-tbl", "rbac"),
                _edge("orders-tbl", "rbac"),
                _edge("users-tbl", "enc"),
                _edge("orders-tbl", "enc"),
                _edge("users-tbl", "mask"),
                _edge("users-tbl", "auditlog"),
                _edge("orders-tbl", "auditlog"),
                _edge("audit-tbl", "auditlog"),
                _edge("users-tbl", "retention"),
                _edge("orders-tbl", "retention"),
                _edge("users-tbl", "backup"),
                _edge("orders-tbl", "backup"),
                _edge("users-tbl", "api-flow", "", "flow-api"),
                _edge("api-flow", "orders-tbl", "", "flow-api"),
                _edge("iac-flyway", "users-tbl", "migrate schema", "flow-etl"),
            ],
            "boundaries": [
                _boundary("cui-zone", "CUI // SP-CTI Zone", "bnd-classification",
                          ["users-tbl", "orders-tbl", "audit-tbl"],
                          x=140, y=80, width=450, height=320),
                _boundary("us-region", "US East (GovCloud)", "bnd-region",
                          ["users-tbl", "orders-tbl", "audit-tbl"],
                          x=130, y=70, width=470, height=340),
            ],
        }),
    },
    # 2 — Data Lake (S3 + Redshift)
    {
        "id": "tpl-ddc-data-lake",
        "name": "Data Lake (S3 + Redshift)",
        "category": "Analytics",
        "description": "S3 data lake with Redshift warehouse, ETL pipeline, and classification zones for CUI analytics.",
        "tags": json.dumps(["s3", "redshift", "datalake", "etl", "analytics"]),
        "graph_json": json.dumps({
            "nodes": [
                _node("raw-lake", "Raw Data Lake (S3)", "ent-datalake", 100, 150),
                _node("curated-lake", "Curated Zone (S3)", "ent-datalake", 350, 150),
                _node("warehouse", "Analytics Warehouse", "ent-warehouse", 600, 150),
                _node("etl1", "Raw -> Curated ETL", "flow-etl", 225, 150),
                _node("etl2", "Curated -> Warehouse ETL", "flow-etl", 475, 150),
                _node("enc", "KMS Encryption", "ctrl-encryption", 350, 50),
                _node("rbac", "IAM RBAC", "ctrl-rbac", 100, 50),
                _node("auditlog", "CloudTrail Audit", "ctrl-audit-log", 600, 50),
                _node("dlp", "DLP Egress Filter", "ctrl-dlp", 600, 300),
                _node("retention", "5-Year Retention", "ctrl-retention", 100, 300),
                _node("backup", "Cross-Region Backup", "ctrl-backup-policy", 350, 300),
                _node("export", "BI Dashboard Export", "flow-export", 750, 150),
                _node("iac-terraform", "Terraform (IaC)", "flow-etl", 350, 400),
            ],
            "edges": [
                _edge("raw-lake", "etl1", "extract", "flow-etl"),
                _edge("etl1", "curated-lake", "load", "flow-etl"),
                _edge("curated-lake", "etl2", "extract", "flow-etl"),
                _edge("etl2", "warehouse", "load", "flow-etl"),
                _edge("warehouse", "export", "export", "flow-export"),
                _edge("raw-lake", "enc"),
                _edge("curated-lake", "enc"),
                _edge("warehouse", "enc"),
                _edge("raw-lake", "rbac"),
                _edge("curated-lake", "rbac"),
                _edge("warehouse", "rbac"),
                _edge("raw-lake", "auditlog"),
                _edge("curated-lake", "auditlog"),
                _edge("warehouse", "auditlog"),
                _edge("warehouse", "dlp"),
                _edge("raw-lake", "retention"),
                _edge("curated-lake", "retention"),
                _edge("warehouse", "retention"),
                _edge("raw-lake", "backup"),
                _edge("warehouse", "backup"),
                _edge("iac-terraform", "raw-lake", "provision infra", "flow-etl"),
            ],
            "boundaries": [
                _boundary("cui-zone", "CUI Analytics Zone", "bnd-classification",
                          ["raw-lake", "curated-lake", "warehouse"],
                          x=50, y=80, width=750, height=150),
                _boundary("govcloud", "AWS GovCloud US-East", "bnd-region",
                          ["raw-lake", "curated-lake", "warehouse"],
                          x=40, y=70, width=770, height=170),
            ],
        }),
    },
    # 3 — Event-Driven (Kafka + MongoDB)
    {
        "id": "tpl-ddc-event-driven",
        "name": "Event-Driven (Kafka + MongoDB)",
        "category": "Event Streaming",
        "description": "Kafka topics, MongoDB collections, CDC streams, and DLP on egress flows.",
        "tags": json.dumps(["kafka", "mongodb", "event-driven", "cdc", "streaming"]),
        "graph_json": json.dumps({
            "nodes": [
                _node("orders-topic", "orders.events", "ent-topic", 100, 150),
                _node("payments-topic", "payments.events", "ent-topic", 100, 300),
                _node("orders-col", "orders", "ent-collection", 400, 150),
                _node("payments-col", "payments", "ent-collection", 400, 300),
                _node("cache", "Session Cache", "ent-cache", 250, 450),
                _node("cdc", "Debezium CDC", "flow-cdc", 550, 225),
                _node("analytics-wh", "Analytics Warehouse", "ent-warehouse", 700, 225),
                _node("api-flow", "API Gateway", "flow-api", 250, 50),
                _node("rbac", "Service RBAC", "ctrl-rbac", 100, 50),
                _node("enc", "Encryption at Rest", "ctrl-encryption", 400, 50),
                _node("auditlog", "Centralized Audit", "ctrl-audit-log", 550, 50),
                _node("dlp", "DLP Egress Policy", "ctrl-dlp", 700, 350),
                _node("retention", "90-Day Retention", "ctrl-retention", 100, 450),
                _node("backup", "Backup Policy", "ctrl-backup-policy", 400, 450),
                _node("iac-terraform", "Terraform (IaC)", "flow-etl", 250, 530),
            ],
            "edges": [
                _edge("api-flow", "orders-topic", "produce", "flow-api"),
                _edge("api-flow", "payments-topic", "produce", "flow-api"),
                _edge("orders-topic", "orders-col", "consume"),
                _edge("payments-topic", "payments-col", "consume"),
                _edge("orders-col", "cdc", "CDC", "flow-cdc"),
                _edge("payments-col", "cdc", "CDC", "flow-cdc"),
                _edge("cdc", "analytics-wh", "replicate", "flow-cdc"),
                _edge("orders-col", "rbac"),
                _edge("payments-col", "rbac"),
                _edge("orders-col", "enc"),
                _edge("payments-col", "enc"),
                _edge("orders-col", "auditlog"),
                _edge("payments-col", "auditlog"),
                _edge("analytics-wh", "dlp"),
                _edge("orders-topic", "retention"),
                _edge("payments-topic", "retention"),
                _edge("orders-col", "backup"),
                _edge("payments-col", "backup"),
                _edge("api-flow", "cache", "session"),
                _edge("iac-terraform", "orders-topic", "provision infra", "flow-etl"),
            ],
            "boundaries": [
                _boundary("cui-zone", "CUI Processing Zone", "bnd-classification",
                          ["orders-topic", "payments-topic", "orders-col", "payments-col",
                           "cache", "analytics-wh"],
                          x=50, y=80, width=720, height=420),
            ],
        }),
    },
    # 4 — HIPAA Compliant (PHI)
    {
        "id": "tpl-ddc-hipaa",
        "name": "HIPAA Compliant (PHI)",
        "category": "Healthcare",
        "description": "PHI-tagged columns, encryption, masking, audit logging, and HIPAA data residency zone.",
        "tags": json.dumps(["hipaa", "phi", "healthcare", "encryption", "masking"]),
        "graph_json": json.dumps({
            "nodes": [
                _node("patients", "patients", "ent-table", 200, 150),
                _node("encounters", "encounters", "ent-table", 500, 150),
                _node("pat-pk", "patient_id (PK)", "col-pk", 80, 80),
                _node("pat-name", "full_name (PII)", "col-pii", 80, 150),
                _node("pat-ssn", "ssn (PII)", "col-pii", 80, 220),
                _node("pat-dx", "diagnosis (PHI)", "col-phi", 80, 290),
                _node("pat-meds", "medications (PHI)", "col-phi", 80, 360),
                _node("pat-audit", "created_at", "col-audit", 200, 360),
                _node("enc-pk", "encounter_id (PK)", "col-pk", 620, 80),
                _node("enc-fk", "patient_id (FK)", "col-fk", 620, 150),
                _node("enc-notes", "clinical_notes (PHI)", "col-phi", 620, 220),
                _node("enc-audit", "updated_at", "col-audit", 500, 290),
                _node("rbac", "HIPAA RBAC", "ctrl-rbac", 350, 50),
                _node("enc", "AES-256 TDE", "ctrl-encryption", 200, 450),
                _node("mask", "PHI Masking", "ctrl-masking", 80, 430),
                _node("auditlog", "HIPAA Audit Log", "ctrl-audit-log", 500, 450),
                _node("retention", "6-Year HIPAA Retention", "ctrl-retention", 350, 500),
                _node("backup", "HIPAA Backup (RPO 15m)", "ctrl-backup-policy", 200, 550),
                _node("dlp", "PHI DLP Policy", "ctrl-dlp", 500, 550),
                _node("classification", "PHI Classification", "ctrl-classification", 350, 400),
                _node("iac-flyway", "Flyway (Schema Migration)", "flow-etl", 350, 630),
            ],
            "edges": [
                _edge("patients", "pat-pk"),
                _edge("patients", "pat-name"),
                _edge("patients", "pat-ssn"),
                _edge("patients", "pat-dx"),
                _edge("patients", "pat-meds"),
                _edge("patients", "pat-audit"),
                _edge("encounters", "enc-pk"),
                _edge("encounters", "enc-fk"),
                _edge("encounters", "enc-notes"),
                _edge("encounters", "enc-audit"),
                _edge("enc-fk", "pat-pk", "FK ref"),
                _edge("patients", "rbac"),
                _edge("encounters", "rbac"),
                _edge("patients", "enc"),
                _edge("encounters", "enc"),
                _edge("patients", "mask"),
                _edge("patients", "auditlog"),
                _edge("encounters", "auditlog"),
                _edge("patients", "retention"),
                _edge("encounters", "retention"),
                _edge("patients", "backup"),
                _edge("encounters", "backup"),
                _edge("patients", "dlp"),
                _edge("encounters", "dlp"),
                _edge("patients", "classification"),
                _edge("encounters", "classification"),
                _edge("iac-flyway", "patients", "migrate schema", "flow-etl"),
            ],
            "boundaries": [
                _boundary("hipaa-zone", "HIPAA PHI Zone", "bnd-classification",
                          ["patients", "encounters"],
                          x=140, y=80, width=540, height=250),
                _boundary("us-hipaa", "US HIPAA Data Residency", "bnd-region",
                          ["patients", "encounters"],
                          x=130, y=70, width=560, height=270),
            ],
        }),
    },
    # 5 — Multi-Classification (CUI + SECRET)
    {
        "id": "tpl-ddc-multi-classification",
        "name": "Multi-Classification (CUI + SECRET)",
        "category": "DoD/IC",
        "description": "Two classification zones (CUI and SECRET) with cross-domain guard between them.",
        "tags": json.dumps(["dod", "secret", "cui", "cross-domain", "cds"]),
        "graph_json": json.dumps({
            "nodes": [
                _node("cui-db", "CUI Operations DB", "ent-table", 150, 200),
                _node("cui-files", "CUI File Store", "ent-file", 150, 350),
                _node("secret-db", "SECRET Intel DB", "ent-table", 650, 200),
                _node("secret-cache", "SECRET Cache", "ent-cache", 650, 350),
                _node("cui-col", "mission_data (CUI)", "col-cui", 30, 200),
                _node("sec-col", "intel_report (SECRET)", "col-secret", 770, 200),
                _node("cui-audit", "created_at", "col-audit", 30, 270),
                _node("sec-audit", "created_at", "col-audit", 770, 270),
                _node("cds-flow", "Cross-Domain Guard", "flow-cross-domain", 400, 275),
                _node("cui-enc", "FIPS 140-2 Encryption", "ctrl-encryption", 150, 100),
                _node("sec-enc", "NSA Type 1 Encryption", "ctrl-encryption", 650, 100),
                _node("cui-rbac", "CUI RBAC (CAC)", "ctrl-rbac", 50, 100),
                _node("sec-rbac", "SECRET RBAC (SCI)", "ctrl-rbac", 750, 100),
                _node("cui-audit-ctrl", "CUI Audit Log", "ctrl-audit-log", 250, 100),
                _node("sec-audit-ctrl", "SECRET Audit Log", "ctrl-audit-log", 550, 100),
                _node("cui-retention", "7-Year Retention", "ctrl-retention", 150, 450),
                _node("sec-retention", "25-Year Retention", "ctrl-retention", 650, 450),
                _node("cui-backup", "GovCloud Backup", "ctrl-backup-policy", 50, 450),
                _node("sec-backup", "SIPR Backup", "ctrl-backup-policy", 750, 450),
                _node("iac-terraform", "Terraform (IaC)", "flow-etl", 400, 530),
            ],
            "edges": [
                _edge("cui-db", "cui-col"),
                _edge("cui-db", "cui-audit"),
                _edge("secret-db", "sec-col"),
                _edge("secret-db", "sec-audit"),
                _edge("cui-db", "cds-flow", "CUI->SECRET", "flow-cross-domain"),
                _edge("cds-flow", "secret-db", "filtered", "flow-cross-domain"),
                _edge("cui-db", "cui-enc"),
                _edge("cui-files", "cui-enc"),
                _edge("secret-db", "sec-enc"),
                _edge("secret-cache", "sec-enc"),
                _edge("cui-db", "cui-rbac"),
                _edge("cui-files", "cui-rbac"),
                _edge("secret-db", "sec-rbac"),
                _edge("secret-cache", "sec-rbac"),
                _edge("cui-db", "cui-audit-ctrl"),
                _edge("cui-files", "cui-audit-ctrl"),
                _edge("secret-db", "sec-audit-ctrl"),
                _edge("secret-cache", "sec-audit-ctrl"),
                _edge("cui-db", "cui-retention"),
                _edge("cui-files", "cui-retention"),
                _edge("secret-db", "sec-retention"),
                _edge("secret-cache", "sec-retention"),
                _edge("cui-db", "cui-backup"),
                _edge("secret-db", "sec-backup"),
                _edge("iac-terraform", "cui-db", "provision infra", "flow-etl"),
            ],
            "boundaries": [
                _boundary("cui-zone", "CUI // SP-CTI Zone", "bnd-classification",
                          ["cui-db", "cui-files"],
                          x=30, y=120, width=300, height=300),
                _boundary("secret-zone", "SECRET // NOFORN Zone", "bnd-classification",
                          ["secret-db", "secret-cache"],
                          x=530, y=120, width=300, height=300),
                _boundary("govcloud", "AWS GovCloud (IL5)", "bnd-region",
                          ["cui-db", "cui-files"],
                          x=20, y=110, width=320, height=320),
                _boundary("sipr", "SIPR Enclave (IL6)", "bnd-region",
                          ["secret-db", "secret-cache"],
                          x=520, y=110, width=320, height=320),
            ],
        }),
    },
    # 6 — Graph + Vector RAG Pipeline
    {
        "id": "tpl-ddc-rag-pipeline",
        "name": "Graph + Vector RAG Pipeline",
        "category": "AI/ML",
        "description": "Knowledge graph, vector DB, and API flows for RAG pipeline with tenant isolation.",
        "tags": json.dumps(["rag", "vector", "graph", "ai", "multi-tenant"]),
        "graph_json": json.dumps({
            "nodes": [
                _node("docs-store", "Document Store (S3)", "ent-file", 100, 150),
                _node("vector-db", "Vector DB (pgvector)", "ent-vector", 350, 150),
                _node("graph-db", "Knowledge Graph (Neo4j)", "ent-graph", 350, 300),
                _node("cache", "Embedding Cache (Redis)", "ent-cache", 600, 150),
                _node("api-ingest", "Ingest API", "flow-api", 225, 75),
                _node("api-query", "Query API", "flow-api", 475, 75),
                _node("etl", "Embedding ETL", "flow-etl", 225, 225),
                _node("rbac", "Tenant RBAC", "ctrl-rbac", 100, 50),
                _node("enc", "AES-256 Encryption", "ctrl-encryption", 350, 50),
                _node("auditlog", "Query Audit Log", "ctrl-audit-log", 600, 50),
                _node("retention", "1-Year Retention", "ctrl-retention", 100, 400),
                _node("backup", "Daily Backup", "ctrl-backup-policy", 350, 400),
                _node("iac-terraform", "Terraform (IaC)", "flow-etl", 225, 470),
            ],
            "edges": [
                _edge("api-ingest", "docs-store", "upload", "flow-api"),
                _edge("docs-store", "etl", "extract", "flow-etl"),
                _edge("etl", "vector-db", "embed", "flow-etl"),
                _edge("etl", "graph-db", "extract entities", "flow-etl"),
                _edge("api-query", "vector-db", "semantic search", "flow-api"),
                _edge("api-query", "graph-db", "graph traversal", "flow-api"),
                _edge("vector-db", "cache", "cache embeddings"),
                _edge("docs-store", "rbac"),
                _edge("vector-db", "rbac"),
                _edge("graph-db", "rbac"),
                _edge("docs-store", "enc"),
                _edge("vector-db", "enc"),
                _edge("graph-db", "enc"),
                _edge("vector-db", "auditlog"),
                _edge("graph-db", "auditlog"),
                _edge("docs-store", "retention"),
                _edge("vector-db", "retention"),
                _edge("graph-db", "retention"),
                _edge("docs-store", "backup"),
                _edge("vector-db", "backup"),
                _edge("graph-db", "backup"),
                _edge("iac-terraform", "docs-store", "provision infra", "flow-etl"),
            ],
            "boundaries": [
                _boundary("cui-zone", "CUI Data Zone", "bnd-classification",
                          ["docs-store", "vector-db", "graph-db", "cache"],
                          x=50, y=80, width=620, height=290),
                _boundary("tenant-a", "Tenant A", "bnd-tenant",
                          ["docs-store", "vector-db", "graph-db"],
                          x=60, y=90, width=370, height=270),
            ],
        }),
    },
]


SNIPPETS = [
    # 1 — PII-Protected Table
    {
        "id": "snp-ddc-pii-protected",
        "name": "PII-Protected Table",
        "category": "Privacy",
        "description": "Table with PII columns, masking policy, RBAC, and audit logging.",
        "tags": json.dumps(["pii", "masking", "rbac", "privacy"]),
        "graph_json": json.dumps({
            "nodes": [
                _node("tbl-1", "users", "ent-table", 100, 50),
                _node("col-email", "email (PII)", "col-pii", 50, 130),
                _node("col-name", "full_name (PII)", "col-pii", 50, 190),
                _node("mask-1", "PII Masking", "ctrl-masking", 250, 100),
                _node("rbac-1", "RBAC Policy", "ctrl-rbac", 250, 170),
                _node("audit-1", "Audit Log", "ctrl-audit-log", 100, 250),
            ],
            "edges": [
                _edge("tbl-1", "col-email"),
                _edge("tbl-1", "col-name"),
                _edge("tbl-1", "mask-1"),
                _edge("tbl-1", "rbac-1"),
                _edge("tbl-1", "audit-1"),
            ],
            "boundaries": [],
        }),
    },
    # 2 — CUI Data Flow
    {
        "id": "snp-ddc-cui-data-flow",
        "name": "CUI Data Flow",
        "category": "Classification",
        "description": "Entity in CUI zone with encryption and DLP on egress.",
        "tags": json.dumps(["cui", "encryption", "dlp", "classification"]),
        "graph_json": json.dumps({
            "nodes": [
                _node("tbl-cui", "CUI Data Store", "ent-table", 50, 50),
                _node("col-cui", "mission_data (CUI)", "col-cui", 50, 130),
                _node("enc-1", "AES-256 TDE", "ctrl-encryption", 250, 50),
                _node("dlp-1", "DLP Egress Filter", "ctrl-dlp", 250, 130),
                _node("export-1", "Data Export", "flow-export", 150, 200),
            ],
            "edges": [
                _edge("tbl-cui", "col-cui"),
                _edge("tbl-cui", "enc-1"),
                _edge("tbl-cui", "export-1", "export", "flow-export"),
                _edge("export-1", "dlp-1"),
            ],
            "boundaries": [
                _boundary("cui-zone", "CUI // SP-CTI Zone", "bnd-classification", ["tbl-cui"],
                          x=20, y=20, width=200, height=170),
            ],
        }),
    },
    # 3 — CDC Replication
    {
        "id": "snp-ddc-cdc-replication",
        "name": "CDC Replication",
        "category": "Replication",
        "description": "Source table with CDC stream replicating to target table.",
        "tags": json.dumps(["cdc", "replication", "debezium", "streaming"]),
        "graph_json": json.dumps({
            "nodes": [
                _node("src-tbl", "Source Table", "ent-table", 50, 80),
                _node("cdc-1", "Debezium CDC", "flow-cdc", 150, 80),
                _node("tgt-tbl", "Target Table", "ent-table", 250, 80),
            ],
            "edges": [
                _edge("src-tbl", "cdc-1", "CDC", "flow-cdc"),
                _edge("cdc-1", "tgt-tbl", "replicate", "flow-cdc"),
            ],
            "boundaries": [],
        }),
    },
    # 4 — Multi-Tenant Isolation
    {
        "id": "snp-ddc-multi-tenant",
        "name": "Multi-Tenant Isolation",
        "category": "Architecture",
        "description": "Two entities in separate tenant boundaries with RBAC.",
        "tags": json.dumps(["multi-tenant", "isolation", "rbac"]),
        "graph_json": json.dumps({
            "nodes": [
                _node("tbl-a", "Tenant A Data", "ent-table", 50, 80),
                _node("tbl-b", "Tenant B Data", "ent-table", 250, 80),
                _node("rbac-1", "Tenant RBAC", "ctrl-rbac", 150, 180),
            ],
            "edges": [
                _edge("tbl-a", "rbac-1"),
                _edge("tbl-b", "rbac-1"),
            ],
            "boundaries": [
                _boundary("tenant-a", "Tenant A", "bnd-tenant", ["tbl-a"],
                          x=20, y=40, width=170, height=120),
                _boundary("tenant-b", "Tenant B", "bnd-tenant", ["tbl-b"],
                          x=220, y=40, width=170, height=120),
            ],
        }),
    },
    # 5 — HIPAA PHI Store
    {
        "id": "snp-ddc-hipaa-phi",
        "name": "HIPAA PHI Store",
        "category": "Healthcare",
        "description": "PHI-tagged columns with encryption, audit log, and HIPAA zone.",
        "tags": json.dumps(["hipaa", "phi", "encryption", "healthcare"]),
        "graph_json": json.dumps({
            "nodes": [
                _node("patients", "patients", "ent-table", 100, 50),
                _node("col-dx", "diagnosis (PHI)", "col-phi", 50, 130),
                _node("col-meds", "medications (PHI)", "col-phi", 50, 200),
                _node("enc-1", "AES-256 TDE", "ctrl-encryption", 250, 80),
                _node("audit-1", "HIPAA Audit Log", "ctrl-audit-log", 250, 160),
            ],
            "edges": [
                _edge("patients", "col-dx"),
                _edge("patients", "col-meds"),
                _edge("patients", "enc-1"),
                _edge("patients", "audit-1"),
            ],
            "boundaries": [
                _boundary("hipaa-zone", "HIPAA PHI Zone", "bnd-classification", ["patients"],
                          x=60, y=20, width=220, height=200),
            ],
        }),
    },
    # 6 — ETL Pipeline
    {
        "id": "snp-ddc-etl-pipeline",
        "name": "ETL Pipeline",
        "category": "Analytics",
        "description": "Data lake to warehouse via ETL with classification tagging.",
        "tags": json.dumps(["etl", "datalake", "warehouse", "analytics"]),
        "graph_json": json.dumps({
            "nodes": [
                _node("lake", "Raw Data Lake", "ent-datalake", 50, 80),
                _node("etl-1", "ETL Pipeline", "flow-etl", 150, 80),
                _node("wh", "Analytics Warehouse", "ent-warehouse", 250, 80),
                _node("class-1", "Data Classification", "ctrl-classification", 150, 180),
            ],
            "edges": [
                _edge("lake", "etl-1", "extract", "flow-etl"),
                _edge("etl-1", "wh", "load", "flow-etl"),
                _edge("lake", "class-1"),
                _edge("wh", "class-1"),
            ],
            "boundaries": [],
        }),
    },
    # 7 — Cross-Domain Guard
    {
        "id": "snp-ddc-cross-domain",
        "name": "Cross-Domain Guard",
        "category": "DoD/IC",
        "description": "CUI entity and SECRET entity with cross-domain data flow.",
        "tags": json.dumps(["cross-domain", "cds", "cui", "secret", "dod"]),
        "graph_json": json.dumps({
            "nodes": [
                _node("cui-db", "CUI Operations DB", "ent-table", 50, 80),
                _node("secret-db", "SECRET Intel DB", "ent-table", 250, 80),
                _node("cds", "Cross-Domain Guard", "flow-cross-domain", 150, 80),
                _node("cui-enc", "FIPS Encryption", "ctrl-encryption", 50, 180),
                _node("sec-enc", "NSA Type 1", "ctrl-encryption", 250, 180),
            ],
            "edges": [
                _edge("cui-db", "cds", "CUI->SECRET", "flow-cross-domain"),
                _edge("cds", "secret-db", "filtered", "flow-cross-domain"),
                _edge("cui-db", "cui-enc"),
                _edge("secret-db", "sec-enc"),
            ],
            "boundaries": [
                _boundary("cui-zone", "CUI Zone", "bnd-classification", ["cui-db"],
                          x=20, y=40, width=170, height=120),
                _boundary("secret-zone", "SECRET Zone", "bnd-classification", ["secret-db"],
                          x=220, y=40, width=170, height=120),
            ],
        }),
    },
    # 8 — Backup + Retention
    {
        "id": "snp-ddc-backup-retention",
        "name": "Backup + Retention",
        "category": "Operations",
        "description": "Entity with backup policy and retention policy.",
        "tags": json.dumps(["backup", "retention", "disaster-recovery", "operations"]),
        "graph_json": json.dumps({
            "nodes": [
                _node("tbl-1", "Production DB", "ent-table", 100, 50),
                _node("backup-1", "Daily Backup (RPO 1h)", "ctrl-backup-policy", 50, 160),
                _node("retention-1", "7-Year Retention", "ctrl-retention", 250, 160),
                _node("enc-1", "Encryption at Rest", "ctrl-encryption", 250, 50),
            ],
            "edges": [
                _edge("tbl-1", "backup-1"),
                _edge("tbl-1", "retention-1"),
                _edge("tbl-1", "enc-1"),
            ],
            "boundaries": [],
        }),
    },
]


def init_db():
    """Initialize the Data Design Canvas database — create tables and seed templates and snippets."""
    conn = get_connection()
    try:
        if _DDC_BACKEND == "postgresql":
            for stmt in SCHEMA.split(";"):
                stmt = stmt.strip()
                if stmt and not stmt.startswith("--"):
                    try:
                        conn.execute(stmt)
                    except Exception:
                        pass  # table/index already exists
            conn.commit()
            # PG audit immutability triggers
            try:
                conn.execute("""
                    CREATE OR REPLACE FUNCTION dd_audit_immutable()
                    RETURNS TRIGGER AS $$
                    BEGIN
                        RAISE EXCEPTION 'Audit records are immutable — NIST AU-6';
                        RETURN NULL;
                    END;
                    $$ LANGUAGE plpgsql;
                """)
                conn.execute("""
                    DROP TRIGGER IF EXISTS dd_audit_no_update ON dd_audit;
                    CREATE TRIGGER dd_audit_no_update
                        BEFORE UPDATE ON dd_audit
                        FOR EACH ROW EXECUTE FUNCTION dd_audit_immutable();
                """)
                conn.execute("""
                    DROP TRIGGER IF EXISTS dd_audit_no_delete ON dd_audit;
                    CREATE TRIGGER dd_audit_no_delete
                        BEFORE DELETE ON dd_audit
                        FOR EACH ROW EXECUTE FUNCTION dd_audit_immutable();
                """)
            except Exception:
                pass
            conn.commit()
        else:
            conn.executescript(SCHEMA)
            conn.commit()
            print(f"[init_db] Data Canvas schema created at {DB_PATH}")

        # Seed templates (upsert)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM dd_templates")
        count = cur.fetchone()[0]
        added = 0
        for t in TEMPLATES:
            cur.execute("SELECT 1 FROM dd_templates WHERE id=?", (t["id"],))
            if not cur.fetchone():
                conn.execute(
                    "INSERT INTO dd_templates (id, name, category, description, graph_json, tags) "
                    "VALUES (?,?,?,?,?,?)",
                    (t["id"], t["name"], t["category"], t["description"],
                     t["graph_json"], t["tags"])
                )
                added += 1
        if added:
            conn.commit()
            print(f"[init_db] Seeded {added} new DDC templates (total: {count + added}).")
        else:
            print(f"[init_db] All {count} DDC templates up to date.")

        # Seed snippets (upsert)
        cur.execute("SELECT COUNT(*) FROM dd_snippets")
        snp_count = cur.fetchone()[0]
        snp_added = 0
        for s in SNIPPETS:
            cur.execute("SELECT 1 FROM dd_snippets WHERE id=?", (s["id"],))
            if not cur.fetchone():
                conn.execute(
                    "INSERT INTO dd_snippets (id, name, category, description, graph_json, tags) "
                    "VALUES (?,?,?,?,?,?)",
                    (s["id"], s["name"], s["category"], s["description"],
                     s["graph_json"], s["tags"])
                )
                snp_added += 1
        if snp_added:
            conn.commit()
            print(f"[init_db] Seeded {snp_added} new DDC snippets (total: {snp_count + snp_added}).")
        else:
            print(f"[init_db] All {snp_count} DDC snippets up to date.")

    finally:
        conn.close()


if __name__ == "__main__":
    init_db()
