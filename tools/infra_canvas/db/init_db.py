# CUI // SP-CTI
"""Infrastructure Design Canvas — DB initializer.

Creates schema and seeds canonical templates.
Dual-backend: SQLite (default) or PostgreSQL.
"""

import json
import os
import sys
import uuid
from pathlib import Path

_ICDEV_ROOT = Path(__file__).resolve().parents[3]
DB_PATH = _ICDEV_ROOT / "data" / "infra_canvas.db"

_IDC_BACKEND = os.environ.get("IDC_STORAGE_BACKEND", os.environ.get("ICDEV_CANVAS_STORAGE_BACKEND", os.environ.get("ICDEV_STORAGE_BACKEND", "postgresql"))).lower()


def get_connection():
    """Get a canvas database connection — SQLite or PostgreSQL.

    cvx-sql-03: infra canvas tables (infra_designs, idc_templates, ...) have no
    tenant_id column, so the storage-global RLS predicate would raise
    UndefinedColumn. Disable RLS on the returned connection, mirroring
    get_canvas_connection(). This wrapper keeps the dedicated IDC_PG_DATABASE
    contract (get_canvas_connection() cannot target a per-canvas PG database).
    """
    from tools.db.storage import get_connection as _storage_conn
    conn = None
    if _IDC_BACKEND == "postgresql":
        try:
            conn = _storage_conn(db_path=os.environ.get("IDC_PG_DATABASE", "infra_canvas"))
        except Exception:
            conn = None
    if conn is None:
        conn = _storage_conn(db_path=str(DB_PATH))
    # Canvas tables lack tenant_id/classification cols — disable the global RLS
    # predicate so it does not raise UndefinedColumn on every query.
    try:
        conn.set_security_context(None)  # rls-bypass: canvas tables lack tenant_id/classification cols
    except AttributeError:
        pass
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS infra_designs (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT,
    graph_json      TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
    template_id     TEXT,
    classification  TEXT DEFAULT 'CUI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS idc_templates (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    category      TEXT,
    description   TEXT,
    graph_json    TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
    tags          TEXT DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS idc_snippets (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    category    TEXT,
    description TEXT,
    graph_json  TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
    tags        TEXT DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS idc_assessments (
    id              TEXT PRIMARY KEY,
    design_id       TEXT NOT NULL,
    assessment_type TEXT DEFAULT 'compliance',
    findings_json   TEXT DEFAULT '[]',
    score           REAL DEFAULT 0.0,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS idc_audit (
    id              TEXT PRIMARY KEY,
    design_id       TEXT,
    "user"          TEXT,
    action          TEXT,
    detail          TEXT,
    classification  TEXT DEFAULT 'CUI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS idc_versions (
    id              TEXT PRIMARY KEY,
    design_id       TEXT REFERENCES infra_designs(id),
    version_number  INTEGER NOT NULL,
    graph_json      TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
    change_summary  TEXT DEFAULT '',
    user_id         TEXT DEFAULT '',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_idc_versions_design ON idc_versions(design_id);

CREATE TABLE IF NOT EXISTS idc_collab_sessions (
    id          TEXT PRIMARY KEY,
    design_id   TEXT NOT NULL REFERENCES infra_designs(id) ON DELETE CASCADE,
    user_id     TEXT NOT NULL,
    user_name   TEXT NOT NULL DEFAULT '',
    color       TEXT NOT NULL DEFAULT '#3498db',
    joined_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    is_active   INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_idc_collab_design ON idc_collab_sessions(design_id);

CREATE TABLE IF NOT EXISTS idc_runbooks (
    id                      TEXT PRIMARY KEY,
    title                   TEXT NOT NULL,
    category                TEXT NOT NULL DEFAULT 'general',
    trigger_condition       TEXT DEFAULT '',
    severity                TEXT NOT NULL DEFAULT 'medium'
                                CHECK (severity IN ('critical','high','medium','low')),
    description             TEXT DEFAULT '',
    steps                   TEXT DEFAULT '[]',
    nist_controls           TEXT DEFAULT '[]',
    tags                    TEXT DEFAULT '[]',
    owner                   TEXT DEFAULT '',
    estimated_duration_min  INTEGER DEFAULT 30,
    last_executed_at        TEXT DEFAULT '',
    execution_count         INTEGER DEFAULT 0,
    classification          TEXT DEFAULT 'CUI',
    created_at              TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at              TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_idc_runbooks_category ON idc_runbooks(category);
CREATE INDEX IF NOT EXISTS idx_idc_runbooks_severity ON idc_runbooks(severity);

CREATE TABLE IF NOT EXISTS idc_sops (
    id                      TEXT PRIMARY KEY,
    title                   TEXT NOT NULL,
    category                TEXT NOT NULL DEFAULT 'general'
                                CHECK (category IN (
                                    'infra_change_management',
                                    'capacity_planning',
                                    'cloud_account_onboarding',
                                    'patch_management',
                                    'disaster_recovery',
                                    'access_management',
                                    'general'
                                )),
    description             TEXT DEFAULT '',
    purpose                 TEXT DEFAULT '',
    scope                   TEXT DEFAULT '',
    steps                   TEXT DEFAULT '[]',
    status                  TEXT NOT NULL DEFAULT 'draft'
                                CHECK (status IN ('draft','pending_review','approved','rejected','archived')),
    version                 TEXT DEFAULT '1.0',
    owner                   TEXT DEFAULT '',
    reviewer                TEXT DEFAULT '',
    approver                TEXT DEFAULT '',
    reviewed_at             TEXT DEFAULT '',
    approved_at             TEXT DEFAULT '',
    effective_date          TEXT DEFAULT '',
    review_interval_days    INTEGER DEFAULT 365,
    nist_controls           TEXT DEFAULT '[]',
    tags                    TEXT DEFAULT '[]',
    classification          TEXT DEFAULT 'CUI',
    created_at              TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at              TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_idc_sops_category ON idc_sops(category);
CREATE INDEX IF NOT EXISTS idx_idc_sops_status ON idc_sops(status);

CREATE TABLE IF NOT EXISTS idc_infra_resources (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    csp             TEXT NOT NULL,
    region          TEXT NOT NULL,
    resource_type   TEXT NOT NULL,
    resource_name   TEXT,
    classification  TEXT DEFAULT 'UNCLASSIFIED',
    tags            TEXT,
    cost_per_month  REAL DEFAULT 0.0,
    config          TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_idc_infra_resources_csp ON idc_infra_resources(csp);
CREATE INDEX IF NOT EXISTS idx_idc_infra_resources_classification ON idc_infra_resources(classification);

CREATE TABLE IF NOT EXISTS idc_infra_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id     TEXT NOT NULL UNIQUE,
    taken_at        TEXT NOT NULL DEFAULT (datetime('now')),
    csp             TEXT NOT NULL,
    region          TEXT NOT NULL,
    classification  TEXT DEFAULT 'UNCLASSIFIED',
    resource_count  INTEGER DEFAULT 0,
    baseline_hash   TEXT,
    notes           TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_idc_infra_snapshots_csp ON idc_infra_snapshots(csp);
"""

# ── Templates ────────────────────────────────────────────────────────────────


def _uid():
    return uuid.uuid4().hex[:10]


def _node(ntype, label, x, y, **kwargs):
    n = {"id": f"n-{_uid()}", "type": ntype, "label": label, "x": x, "y": y, "metadata": {}}
    n.update(kwargs)
    return n


def _edge(source_id, target_id, label=""):
    return {"id": f"e-{_uid()}", "source": source_id, "target": target_id, "label": label}


def _build_templates():
    templates = []

    # 1. AWS Three-Tier Web Application
    n1 = _node("aws-apigw", "API Gateway", 100, 50)
    n2 = _node("aws-lambda", "Lambda (API)", 300, 50)
    n3 = _node("aws-aurora", "Aurora PostgreSQL", 500, 50)
    n4 = _node("aws-s3", "S3 (Static Assets)", 100, 200)
    n5 = _node("aws-elasticache", "ElastiCache Redis", 500, 200)
    n6 = _node("aws-kms", "KMS", 700, 50)
    n7 = _node("aws-iam", "IAM", 700, 140)
    n8 = _node("aws-secrets", "Secrets Manager", 700, 230)
    n9 = _node("aws-securityhub", "Security Hub", 300, 200)
    n10 = _node("iac-terraform", "Terraform", 100, 350)
    nodes = [n1, n2, n3, n4, n5, n6, n7, n8, n9, n10]
    edges = [
        _edge(n1["id"], n2["id"], "invoke"),
        _edge(n2["id"], n3["id"], "read/write"),
        _edge(n2["id"], n5["id"], "cache"),
        _edge(n3["id"], n6["id"], "encrypt"),
        _edge(n5["id"], n6["id"], "encrypt"),
        _edge(n2["id"], n7["id"], "auth"),
        _edge(n2["id"], n8["id"], "secrets"),
        _edge(n2["id"], n4["id"], "static assets"),
        _edge(n2["id"], n9["id"], "findings"),
        _edge(n10["id"], n1["id"], "provision"),
    ]
    templates.append(
        {
            "id": f"tpl-{_uid()}",
            "name": "AWS Serverless Three-Tier",
            "category": "aws",
            "description": "API Gateway + Lambda + Aurora + S3 + ElastiCache with KMS encryption and IAM",
            "graph_json": json.dumps({"nodes": nodes, "edges": edges}),
            "tags": json.dumps(["aws", "serverless", "three-tier", "CUI"]),
        }
    )

    # 2. Azure Enterprise K8s
    n1 = _node("az-aks", "AKS Cluster", 300, 100)
    n2 = _node("az-acr", "ACR (Registry)", 100, 100)
    n3 = _node("az-postgres", "PostgreSQL Flex", 500, 100)
    n4 = _node("az-redis", "Redis Cache", 500, 250)
    n5 = _node("az-keyvault", "Key Vault", 700, 100)
    n6 = _node("az-entra", "Entra ID", 700, 250)
    n7 = _node("az-blob", "Blob Storage", 100, 250)
    n8 = _node("az-defender", "Defender for Cloud", 300, 250)
    n9 = _node("az-backup", "Azure Backup", 500, 400)
    n10 = _node("iac-argocd", "ArgoCD", 100, 400)
    nodes = [n1, n2, n3, n4, n5, n6, n7, n8, n9, n10]
    edges = [
        _edge(n2["id"], n1["id"], "pull images"),
        _edge(n1["id"], n3["id"], "read/write"),
        _edge(n1["id"], n4["id"], "cache"),
        _edge(n3["id"], n5["id"], "encrypt"),
        _edge(n1["id"], n6["id"], "OIDC auth"),
        _edge(n1["id"], n7["id"], "persist"),
        _edge(n3["id"], n9["id"], "backup"),
        _edge(n10["id"], n1["id"], "GitOps deploy"),
        _edge(n1["id"], n8["id"], "CSPM"),
    ]
    templates.append(
        {
            "id": f"tpl-{_uid()}",
            "name": "Azure Enterprise Kubernetes",
            "category": "azure",
            "description": "AKS + ACR + PostgreSQL + Redis + Key Vault + Entra ID + ArgoCD GitOps",
            "graph_json": json.dumps({"nodes": nodes, "edges": edges}),
            "tags": json.dumps(["azure", "kubernetes", "enterprise", "CUI"]),
        }
    )

    # 3. GCP Data Analytics Platform
    n1 = _node("gcp-pubsub", "Pub/Sub", 100, 100)
    n2 = _node("gcp-functions", "Cloud Functions", 300, 100)
    n3 = _node("gcp-bigquery", "BigQuery", 500, 100)
    n4 = _node("gcp-gcs", "Cloud Storage (Lake)", 300, 250)
    n5 = _node("gcp-vertex", "Vertex AI", 500, 250)
    n6 = _node("gcp-kms", "Cloud KMS", 700, 100)
    n7 = _node("gcp-iam", "Cloud IAM", 700, 250)
    n8 = _node("gcp-scc", "Security Command Center", 100, 250)
    n9 = _node("iac-terraform", "Terraform", 100, 400)
    nodes = [n1, n2, n3, n4, n5, n6, n7, n8, n9]
    edges = [
        _edge(n1["id"], n2["id"], "trigger"),
        _edge(n2["id"], n4["id"], "store raw"),
        _edge(n4["id"], n3["id"], "load"),
        _edge(n3["id"], n5["id"], "train/predict"),
        _edge(n3["id"], n6["id"], "encrypt"),
        _edge(n4["id"], n6["id"], "encrypt"),
        _edge(n2["id"], n7["id"], "auth"),
        _edge(n3["id"], n8["id"], "compliance"),
        _edge(n9["id"], n1["id"], "provision"),
    ]
    templates.append(
        {
            "id": f"tpl-{_uid()}",
            "name": "GCP Data Analytics + AI",
            "category": "gcp",
            "description": "Pub/Sub + Functions + GCS data lake + BigQuery warehouse + Vertex AI",
            "graph_json": json.dumps({"nodes": nodes, "edges": edges}),
            "tags": json.dumps(["gcp", "analytics", "ai", "data-lake"]),
        }
    )

    # 4. Multi-Cloud Hybrid (AWS + Azure + On-Prem)
    n1 = _node("aws-eks", "EKS (AWS)", 100, 100)
    n2 = _node("az-aks", "AKS (Azure)", 400, 100)
    n3 = _node("op-k8s", "K8s (On-Prem)", 250, 250)
    n4 = _node("aws-ecr", "ECR", 100, 250)
    n5 = _node("az-acr", "ACR", 400, 250)
    n6 = _node("op-harbor", "Harbor", 250, 400)
    n7 = _node("aws-rds", "RDS PostgreSQL", 100, 400)
    n8 = _node("az-cosmos", "Cosmos DB", 400, 400)
    n9 = _node("iac-crossplane", "Crossplane", 250, 50)
    n10 = _node("aws-kms", "AWS KMS", 600, 100)
    n11 = _node("az-keyvault", "Azure Key Vault", 600, 250)
    nodes = [n1, n2, n3, n4, n5, n6, n7, n8, n9, n10, n11]
    edges = [
        _edge(n9["id"], n1["id"], "manage"),
        _edge(n9["id"], n2["id"], "manage"),
        _edge(n4["id"], n1["id"], "pull"),
        _edge(n5["id"], n2["id"], "pull"),
        _edge(n6["id"], n3["id"], "pull"),
        _edge(n1["id"], n7["id"], "read/write"),
        _edge(n2["id"], n8["id"], "read/write"),
        _edge(n7["id"], n10["id"], "encrypt"),
        _edge(n8["id"], n11["id"], "encrypt"),
    ]
    templates.append(
        {
            "id": f"tpl-{_uid()}",
            "name": "Multi-Cloud Hybrid (AWS + Azure + On-Prem)",
            "category": "multi_cloud",
            "description": "EKS + AKS + on-prem K8s managed by Crossplane with per-CSP encryption",
            "graph_json": json.dumps({"nodes": nodes, "edges": edges}),
            "tags": json.dumps(["multi-cloud", "hybrid", "kubernetes", "crossplane"]),
        }
    )

    # 5. OCI Enterprise (GovCloud)
    n1 = _node("oci-compute", "Compute (Flex)", 100, 100)
    n2 = _node("oci-oke", "OKE Cluster", 300, 100)
    n3 = _node("oci-adb", "Autonomous DB", 500, 100)
    n4 = _node("oci-os", "Object Storage", 100, 250)
    n5 = _node("oci-vault", "OCI Vault", 500, 250)
    n6 = _node("oci-iam", "IAM", 700, 100)
    n7 = _node("oci-cspm", "Cloud Guard", 700, 250)
    n8 = _node("oci-ocir", "OCIR", 300, 250)
    n9 = _node("iac-terraform", "OCI Resource Manager", 300, 400)
    nodes = [n1, n2, n3, n4, n5, n6, n7, n8, n9]
    edges = [
        _edge(n8["id"], n2["id"], "pull images"),
        _edge(n2["id"], n3["id"], "read/write"),
        _edge(n1["id"], n4["id"], "persist"),
        _edge(n3["id"], n5["id"], "encrypt"),
        _edge(n4["id"], n5["id"], "encrypt"),
        _edge(n2["id"], n6["id"], "auth"),
        _edge(n2["id"], n7["id"], "posture"),
        _edge(n9["id"], n1["id"], "provision"),
    ]
    templates.append(
        {
            "id": f"tpl-{_uid()}",
            "name": "OCI Enterprise (GovCloud)",
            "category": "oci",
            "description": "OCI Compute + OKE + Autonomous DB + Object Storage + Vault + Cloud Guard",
            "graph_json": json.dumps({"nodes": nodes, "edges": edges}),
            "tags": json.dumps(["oci", "govcloud", "enterprise", "CUI"]),
        }
    )

    # 6. IBM Cloud AI Platform
    n1 = _node("ibm-openshift", "ROKS Cluster", 250, 100)
    n2 = _node("ibm-watsonx", "watsonx.ai", 450, 100)
    n3 = _node("ibm-postgres", "PostgreSQL", 250, 250)
    n4 = _node("ibm-cos", "Cloud Object Storage", 450, 250)
    n5 = _node("ibm-kms", "Key Protect", 650, 100)
    n6 = _node("ibm-iam", "IAM", 650, 250)
    n7 = _node("ibm-event-streams", "Event Streams", 50, 100)
    n8 = _node("ibm-cr", "Container Registry", 50, 250)
    n9 = _node("iac-terraform", "IBM Schematics", 250, 400)
    nodes = [n1, n2, n3, n4, n5, n6, n7, n8, n9]
    edges = [
        _edge(n7["id"], n1["id"], "events"),
        _edge(n8["id"], n1["id"], "pull"),
        _edge(n1["id"], n2["id"], "inference"),
        _edge(n1["id"], n3["id"], "read/write"),
        _edge(n2["id"], n4["id"], "model store"),
        _edge(n3["id"], n5["id"], "encrypt"),
        _edge(n4["id"], n5["id"], "encrypt"),
        _edge(n1["id"], n6["id"], "auth"),
        _edge(n9["id"], n1["id"], "provision"),
    ]
    templates.append(
        {
            "id": f"tpl-{_uid()}",
            "name": "IBM Cloud AI Platform",
            "category": "ibm",
            "description": "ROKS + watsonx.ai + PostgreSQL + Object Storage + Event Streams + Key Protect",
            "graph_json": json.dumps({"nodes": nodes, "edges": edges}),
            "tags": json.dumps(["ibm", "ai", "openshift", "watsonx"]),
        }
    )

    # 7. DoD IL4 Reference Architecture (AWS GovCloud)
    n1 = _node("aws-eks", "EKS (GovCloud)", 230, 50)
    n2 = _node("aws-ecr", "ECR", 50, 50)
    n3 = _node("aws-aurora", "Aurora (FIPS)", 430, 50)
    n4 = _node("aws-s3", "S3 (FIPS)", 230, 200)
    n5 = _node("aws-kms", "KMS (FIPS 140-2 L3)", 630, 50)
    n6 = _node("aws-iam", "IAM (GovCloud)", 630, 200)
    n7 = _node("aws-secrets", "Secrets Manager", 430, 200)
    n8 = _node("aws-securityhub", "Security Hub", 50, 200)
    n9 = _node("aws-config", "AWS Config", 50, 350)
    n10 = _node("aws-backup", "AWS Backup", 430, 350)
    n11 = _node("aws-ec2-dedicated", "Dedicated Host", 230, 350)
    n12 = _node("iac-terraform", "Terraform", 630, 350)
    nodes = [n1, n2, n3, n4, n5, n6, n7, n8, n9, n10, n11, n12]
    edges = [
        _edge(n2["id"], n1["id"], "pull"),
        _edge(n1["id"], n3["id"], "read/write"),
        _edge(n1["id"], n4["id"], "persist"),
        _edge(n3["id"], n5["id"], "encrypt"),
        _edge(n4["id"], n5["id"], "encrypt"),
        _edge(n1["id"], n6["id"], "IRSA auth"),
        _edge(n1["id"], n7["id"], "secrets"),
        _edge(n3["id"], n10["id"], "backup"),
        _edge(n1["id"], n8["id"], "findings"),
        _edge(n9["id"], n8["id"], "compliance"),
        _edge(n1["id"], n11["id"], "runs on"),
        _edge(n12["id"], n1["id"], "provision"),
    ]
    templates.append(
        {
            "id": f"tpl-{_uid()}",
            "name": "DoD IL4 Reference (AWS GovCloud)",
            "category": "aws",
            "description": "AWS GovCloud — EKS + Aurora + S3 + KMS FIPS L3 + Dedicated Hosts + Security Hub + Config",
            "graph_json": json.dumps({"nodes": nodes, "edges": edges}),
            "tags": json.dumps(["aws", "govcloud", "il4", "dod", "CUI", "FIPS"]),
        }
    )

    # 8. AWS SCCA Multi-Account Landing Zone (DoD Reference Architecture)
    # Row 1 (y=50): Management Account & Security/Audit Account
    n1 = _node("aws-iam", "AWS Organizations", 50, 50)
    n2 = _node("aws-ec2", "CloudFormation", 220, 50)
    n3 = _node("aws-ec2", "CodePipeline (CI/CD)", 390, 50)
    n4 = _node("aws-securityhub", "Security Hub (Admin)", 600, 50)
    n5 = _node("aws-config", "AWS Config (Admin)", 770, 50)
    n6 = _node("aws-ec2", "GuardDuty (Admin)", 940, 50)
    # Row 2 (y=180): Log Archive Account
    n7 = _node("aws-ec2", "Amazon Kinesis", 600, 180)
    n8 = _node("aws-s3", "Log Archive (S3)", 770, 180)
    n9 = _node("aws-kms", "KMS (FIPS 140-2 L3)", 940, 180)
    # Row 3 (y=310): Network Account — Inspection VPC
    n10 = _node("aws-ec2", "Internet Gateway", 50, 310)
    n11 = _node("aws-ec2", "Firewall Subnet (AZ-A)", 220, 310)
    n12 = _node("aws-ec2", "Firewall Subnet (AZ-B)", 390, 310)
    # Row 3b (y=400): IDS/IPS appliances
    n13 = _node("aws-ec2", "IDS/IPS (AZ-A)", 220, 400)
    n14 = _node("aws-ec2", "IDS/IPS (AZ-B)", 390, 400)
    # Row 4 (y=490): Transit & Connectivity
    n15 = _node("aws-ec2", "Direct Connect Gateway", 50, 490)
    n16 = _node("aws-ec2", "VPN Gateway", 220, 490)
    n17 = _node("aws-ec2-asg", "AWS Transit Gateway", 500, 490)
    # Row 5 (y=620): Workload Accounts — Production Application VPC
    n18 = _node("aws-ec2", "App Subnet (AZ-A)", 600, 620)
    n19 = _node("aws-ec2", "App Subnet (AZ-B)", 770, 620)
    # Row 5b (y=700): Data tier
    n20 = _node("aws-rds", "Data Subnet (RDS)", 600, 700)
    n21 = _node("aws-s3", "Data Subnet (S3)", 770, 700)
    # Row 6 (y=830): Shared Services Account
    n22 = _node("aws-ec2", "External Access VPC (Public)", 50, 830)
    n23 = _node("aws-ec2", "External Access VPC (Private)", 220, 830)
    n24 = _node("aws-ec2", "Shared Services VPC", 500, 830)
    n25 = _node("aws-ecr", "Container Registry", 670, 830)
    n26 = _node("aws-ec2", "Active Directory", 840, 830)
    # Bottom (y=950): IaC
    n27 = _node("iac-terraform", "Landing Zone Accelerator (Terraform)", 400, 950)
    nodes = [
        n1,
        n2,
        n3,
        n4,
        n5,
        n6,
        n7,
        n8,
        n9,
        n10,
        n11,
        n12,
        n13,
        n14,
        n15,
        n16,
        n17,
        n18,
        n19,
        n20,
        n21,
        n22,
        n23,
        n24,
        n25,
        n26,
        n27,
    ]
    edges = [
        # Internet → Firewall subnets
        _edge(n10["id"], n11["id"], "ingress"),
        _edge(n10["id"], n12["id"], "ingress"),
        # Firewall → IDS/IPS
        _edge(n11["id"], n13["id"], "inspect"),
        _edge(n12["id"], n14["id"], "inspect"),
        # IDS/IPS → Transit Gateway
        _edge(n13["id"], n17["id"], "filtered traffic"),
        _edge(n14["id"], n17["id"], "filtered traffic"),
        # Connectivity → Transit Gateway
        _edge(n15["id"], n17["id"], "DISN circuit"),
        _edge(n16["id"], n17["id"], "VPN tunnel"),
        # Transit Gateway → Workload subnets
        _edge(n17["id"], n18["id"], "workload route"),
        _edge(n17["id"], n19["id"], "workload route"),
        # Transit Gateway → Shared Services
        _edge(n17["id"], n24["id"], "shared svc route"),
        # Transit Gateway → External Access
        _edge(n17["id"], n22["id"], "external route"),
        # Governance: Organizations → Config
        _edge(n1["id"], n5["id"], "governance"),
        # Compliance: Config → Security Hub
        _edge(n5["id"], n4["id"], "compliance"),
        # Findings: GuardDuty → Security Hub
        _edge(n6["id"], n4["id"], "findings"),
        # Log streaming: Kinesis → S3
        _edge(n7["id"], n8["id"], "stream"),
        # Encryption: Log Archive → KMS
        _edge(n8["id"], n9["id"], "encrypt"),
        # Encryption: RDS → KMS
        _edge(n20["id"], n9["id"], "encrypt"),
        # Data: App Subnet → RDS
        _edge(n18["id"], n20["id"], "data"),
        # Container Registry → Shared Services
        _edge(n25["id"], n24["id"], "hosts"),
        # LZA Terraform → Organizations
        _edge(n27["id"], n1["id"], "provision"),
    ]
    templates.append(
        {
            "id": f"tpl-{_uid()}",
            "name": "AWS SCCA Multi-Account Landing Zone",
            "category": "aws",
            "description": "DoD SCCA multi-account reference architecture on AWS GovCloud — "
            "Management, Audit, Log Archive, Network (Inspection VPC), Workload, "
            "and Shared Services accounts connected via Transit Gateway. "
            "Landing Zone Accelerator (Terraform) provisioning with FIPS 140-2 L3 KMS.",
            "graph_json": json.dumps({"nodes": nodes, "edges": edges}),
            "tags": json.dumps(
                [
                    "aws",
                    "scca",
                    "dod",
                    "lza",
                    "govcloud",
                    "il4",
                    "il5",
                    "multi-account",
                    "transit-gateway",
                    "inspection",
                ]
            ),
        }
    )

    # 9. Azure CAF Landing Zone (Hub-Spoke)
    n1 = _node("az-entra", "Entra ID (Tenant)", 100, 50)
    n2 = _node("az-defender", "Defender for Cloud", 300, 50)
    n3 = _node("az-fw", "Azure Firewall (Hub)", 100, 200)
    n4 = _node("az-vpn-gw", "VPN Gateway", 300, 200)
    n5 = _node("az-er", "ExpressRoute", 500, 200)
    n6 = _node("az-entra", "Conditional Access", 100, 350)
    n7 = _node("az-keyvault", "Key Vault (Platform)", 300, 350)
    n8 = _node("az-sentinel-sec", "Microsoft Sentinel", 500, 350)
    n9 = _node("az-backup", "Azure Backup", 700, 350)
    n10 = _node("az-aks", "AKS (Corp Workload)", 100, 500)
    n11 = _node("az-sql", "SQL Database", 300, 500)
    n12 = _node("az-app-service", "App Service", 100, 650)
    n13 = _node("az-cosmos", "Cosmos DB", 300, 650)
    n14 = _node("iac-terraform", "ALZ Terraform Module", 500, 500)
    nodes = [n1, n2, n3, n4, n5, n6, n7, n8, n9, n10, n11, n12, n13, n14]
    edges = [
        _edge(n3["id"], n4["id"], "peering"),
        _edge(n3["id"], n5["id"], "peering"),
        _edge(n10["id"], n7["id"], "secrets"),
        _edge(n10["id"], n3["id"], "hub-spoke"),
        _edge(n11["id"], n7["id"], "encrypt"),
        _edge(n8["id"], n2["id"], "alerts"),
        _edge(n14["id"], n1["id"], "provision"),
        _edge(n6["id"], n1["id"], "enforce"),
        _edge(n9["id"], n11["id"], "backup"),
        _edge(n12["id"], n3["id"], "hub-spoke"),
        _edge(n13["id"], n7["id"], "encrypt"),
    ]
    templates.append(
        {
            "id": f"tpl-{_uid()}",
            "name": "Azure CAF Landing Zone (Hub-Spoke)",
            "category": "azure",
            "description": "Azure Cloud Adoption Framework enterprise-scale landing zone with hub-spoke topology",
            "graph_json": json.dumps({"nodes": nodes, "edges": edges}),
            "tags": json.dumps(["azure", "caf", "landing-zone", "hub-spoke", "enterprise-scale"]),
        }
    )

    # 10. GCP Enterprise Landing Zone
    n1 = _node("gcp-iam", "Cloud IAM (Org)", 100, 50)
    n2 = _node("gcp-scc", "Security Command Center", 300, 50)
    n3 = _node("gcp-vpc", "Shared VPC (Host)", 100, 200)
    n4 = _node("gcp-kms", "Cloud KMS", 300, 200)
    n5 = _node("gcp-secret", "Secret Manager", 500, 200)
    n6 = _node("gcp-router", "Cloud Router", 100, 350)
    n7 = _node("gcp-vpn", "Cloud VPN (HA)", 300, 350)
    n8 = _node("gcp-armor", "Cloud Armor", 500, 350)
    n9 = _node("gcp-gke", "GKE (Prod)", 100, 500)
    n10 = _node("gcp-cloudsql", "Cloud SQL", 300, 500)
    n11 = _node("gcp-gcs", "Cloud Storage", 500, 500)
    n12 = _node("gcp-vertex", "Vertex AI (Optional)", 300, 650)
    n13 = _node("iac-terraform", "Cloud Foundation Toolkit", 500, 650)
    nodes = [n1, n2, n3, n4, n5, n6, n7, n8, n9, n10, n11, n12, n13]
    edges = [
        _edge(n9["id"], n3["id"], "service project"),
        _edge(n10["id"], n4["id"], "encrypt"),
        _edge(n11["id"], n4["id"], "encrypt"),
        _edge(n6["id"], n7["id"], "route"),
        _edge(n8["id"], n9["id"], "protect"),
        _edge(n2["id"], n1["id"], "audit"),
        _edge(n13["id"], n1["id"], "provision"),
        _edge(n5["id"], n9["id"], "secrets"),
        _edge(n12["id"], n11["id"], "data"),
    ]
    templates.append(
        {
            "id": f"tpl-{_uid()}",
            "name": "GCP Enterprise Landing Zone",
            "category": "gcp",
            "description": "Google Cloud organization-based landing zone with Shared VPC and Cloud Foundation Toolkit",
            "graph_json": json.dumps({"nodes": nodes, "edges": edges}),
            "tags": json.dumps(["gcp", "landing-zone", "shared-vpc", "organization", "assured-workloads"]),
        }
    )

    # 11. OCI Landing Zone (CIS Benchmark)
    n1 = _node("oci-iam", "IAM (Compartments)", 100, 50)
    n2 = _node("oci-cspm", "Cloud Guard", 300, 50)
    n3 = _node("oci-vcn", "Hub VCN", 100, 200)
    n4 = _node("oci-drg", "DRG (Transit)", 300, 200)
    n5 = _node("oci-fc", "FastConnect", 500, 200)
    n6 = _node("oci-vault", "OCI Vault (HSM)", 100, 350)
    n7 = _node("oci-os", "Audit Logs (Bucket)", 300, 350)
    n8 = _node("oci-oke", "OKE Cluster", 100, 500)
    n9 = _node("oci-adb", "Autonomous DB", 300, 500)
    n10 = _node("oci-os", "Object Storage", 500, 500)
    n11 = _node("iac-terraform", "OCI LZ Terraform", 300, 650)
    nodes = [n1, n2, n3, n4, n5, n6, n7, n8, n9, n10, n11]
    edges = [
        _edge(n8["id"], n3["id"], "network"),
        _edge(n4["id"], n3["id"], "transit"),
        _edge(n9["id"], n6["id"], "encrypt"),
        _edge(n11["id"], n1["id"], "provision"),
        _edge(n2["id"], n1["id"], "monitor"),
        _edge(n5["id"], n4["id"], "circuit"),
        _edge(n7["id"], n2["id"], "audit"),
        _edge(n10["id"], n8["id"], "persist"),
    ]
    templates.append(
        {
            "id": f"tpl-{_uid()}",
            "name": "OCI Landing Zone (CIS Benchmark)",
            "category": "oci",
            "description": "Oracle Cloud compartment-based landing zone with CIS foundations and DRG transit",
            "graph_json": json.dumps({"nodes": nodes, "edges": edges}),
            "tags": json.dumps(["oci", "landing-zone", "cis", "compartments", "govcloud"]),
        }
    )

    # 12. IBM Cloud for Financial Services
    n1 = _node("ibm-iam", "IAM", 100, 50)
    n2 = _node("ibm-scc", "Security & Compliance Center", 300, 50)
    n3 = _node("ibm-vpc", "Management VPC", 100, 200)
    n4 = _node("ibm-vpc", "Workload VPC", 300, 200)
    n5 = _node("ibm-vpn", "VPN Gateway", 500, 200)
    n6 = _node("ibm-kms", "Key Protect (FIPS)", 100, 350)
    n7 = _node("ibm-secrets", "Secrets Manager", 300, 350)
    n8 = _node("ibm-openshift", "ROKS (FS Cloud)", 100, 500)
    n9 = _node("ibm-postgres", "PostgreSQL", 300, 500)
    n10 = _node("ibm-cos", "Cloud Object Storage", 500, 500)
    n11 = _node("iac-terraform", "IBM Deployable Architecture", 300, 650)
    nodes = [n1, n2, n3, n4, n5, n6, n7, n8, n9, n10, n11]
    edges = [
        _edge(n8["id"], n4["id"], "network"),
        _edge(n4["id"], n3["id"], "peering"),
        _edge(n8["id"], n6["id"], "encrypt"),
        _edge(n11["id"], n1["id"], "provision"),
        _edge(n2["id"], n1["id"], "compliance"),
        _edge(n5["id"], n4["id"], "connect"),
        _edge(n7["id"], n8["id"], "secrets"),
        _edge(n9["id"], n6["id"], "encrypt"),
        _edge(n10["id"], n6["id"], "encrypt"),
    ]
    templates.append(
        {
            "id": f"tpl-{_uid()}",
            "name": "IBM Cloud for Financial Services",
            "category": "ibm",
            "description": "IBM Cloud landing zone for regulated financial services with ROKS and Key Protect",
            "graph_json": json.dumps({"nodes": nodes, "edges": edges}),
            "tags": json.dumps(["ibm", "landing-zone", "financial-services", "regulated", "fs-cloud"]),
        }
    )

    # 13. Healthcare Landing Zone (HIPAA)
    n1 = _node("aws-cognito", "SSO/IdP", 100, 50)
    n2 = _node("aws-kms", "KMS (HIPAA)", 300, 50)
    n3 = _node("aws-ec2", "NAT Gateway", 100, 200)
    n4 = _node("aws-ec2", "Network Firewall", 300, 200)
    n5 = _node("aws-rds", "RDS (PHI Encrypted)", 100, 350)
    n6 = _node("aws-s3", "S3 (PHI Archive)", 300, 350)
    n7 = _node("aws-elasticache", "ElastiCache (Session)", 500, 350)
    n8 = _node("aws-fargate", "Fargate (HIPAA App)", 100, 500)
    n9 = _node("aws-apigw", "API Gateway", 300, 500)
    n10 = _node("aws-securityhub", "Security Hub", 100, 650)
    n11 = _node("aws-config", "AWS Config", 300, 650)
    n12 = _node("iac-terraform", "HIPAA Reference Architecture", 500, 650)
    nodes = [n1, n2, n3, n4, n5, n6, n7, n8, n9, n10, n11, n12]
    edges = [
        _edge(n8["id"], n5["id"], "read/write"),
        _edge(n8["id"], n7["id"], "session"),
        _edge(n9["id"], n8["id"], "route"),
        _edge(n5["id"], n2["id"], "encrypt"),
        _edge(n6["id"], n2["id"], "encrypt"),
        _edge(n10["id"], n11["id"], "compliance"),
        _edge(n12["id"], n1["id"], "provision"),
        _edge(n3["id"], n8["id"], "egress"),
        _edge(n4["id"], n3["id"], "inspect"),
    ]
    templates.append(
        {
            "id": f"tpl-{_uid()}",
            "name": "Healthcare Landing Zone (HIPAA)",
            "category": "aws",
            "description": "HIPAA-compliant healthcare architecture with PHI encryption and Security Hub monitoring",
            "graph_json": json.dumps({"nodes": nodes, "edges": edges}),
            "tags": json.dumps(["aws", "hipaa", "healthcare", "phi", "regulated", "landing-zone"]),
        }
    )

    # 14. Financial Services Landing Zone (PCI-DSS)
    n1 = _node("az-entra", "Entra ID", 100, 50)
    n2 = _node("az-keyvault", "Key Vault (HSM)", 300, 50)
    n3 = _node("az-fw", "Azure Firewall (Premium)", 100, 200)
    n4 = _node("az-appgw", "App Gateway + WAF", 300, 200)
    n5 = _node("az-sql", "SQL Database (TDE)", 100, 350)
    n6 = _node("az-redis", "Redis (Encrypted)", 300, 350)
    n7 = _node("az-blob", "Blob (Immutable)", 500, 350)
    n8 = _node("az-aks", "AKS (PCI Workload)", 100, 500)
    n9 = _node("az-apim", "API Management", 300, 500)
    n10 = _node("az-sentinel-sec", "Microsoft Sentinel", 100, 650)
    n11 = _node("az-defender", "Defender for Cloud", 300, 650)
    n12 = _node("iac-terraform", "PCI-DSS Blueprint", 500, 650)
    nodes = [n1, n2, n3, n4, n5, n6, n7, n8, n9, n10, n11, n12]
    edges = [
        _edge(n8["id"], n5["id"], "read/write"),
        _edge(n8["id"], n6["id"], "cache"),
        _edge(n9["id"], n8["id"], "route"),
        _edge(n5["id"], n2["id"], "encrypt"),
        _edge(n10["id"], n11["id"], "alerts"),
        _edge(n3["id"], n4["id"], "filter"),
        _edge(n12["id"], n1["id"], "provision"),
        _edge(n7["id"], n5["id"], "audit-logs"),
    ]
    templates.append(
        {
            "id": f"tpl-{_uid()}",
            "name": "Financial Services Landing Zone (PCI-DSS)",
            "category": "azure",
            "description": "PCI-DSS compliant architecture for financial services with CDE isolation and HSM encryption",
            "graph_json": json.dumps({"nodes": nodes, "edges": edges}),
            "tags": json.dumps(["azure", "pci-dss", "financial", "cde", "regulated", "landing-zone"]),
        }
    )

    # 15. FedRAMP High Landing Zone
    n1 = _node("aws-ec2", "BCAP/TIC 3.0", 100, 50)
    n2 = _node("aws-ec2", "WAF + Shield", 300, 50)
    n3 = _node("aws-iam", "IAM (GovCloud)", 100, 200)
    n4 = _node("aws-cognito", "Identity Center", 300, 200)
    n5 = _node("aws-kms", "KMS (FIPS 140-2 L3)", 500, 200)
    n6 = _node("aws-ec2-asg", "Transit Gateway", 100, 350)
    n7 = _node("aws-ec2", "Network Firewall", 300, 350)
    n8 = _node("aws-ec2", "VPC Endpoints", 500, 350)
    n9 = _node("aws-eks", "EKS (FedRAMP)", 100, 500)
    n10 = _node("aws-aurora", "Aurora (FedRAMP)", 300, 500)
    n11 = _node("aws-s3", "S3 (FedRAMP)", 500, 500)
    n12 = _node("aws-securityhub", "Security Hub", 100, 650)
    n13 = _node("aws-config", "Config Rules", 300, 650)
    n14 = _node("aws-backup", "AWS Backup", 500, 650)
    n15 = _node("iac-terraform", "FedRAMP Terraform", 300, 800)
    nodes = [n1, n2, n3, n4, n5, n6, n7, n8, n9, n10, n11, n12, n13, n14, n15]
    edges = [
        _edge(n9["id"], n6["id"], "transit"),
        _edge(n6["id"], n1["id"], "egress"),
        _edge(n10["id"], n5["id"], "encrypt"),
        _edge(n11["id"], n5["id"], "encrypt"),
        _edge(n12["id"], n13["id"], "compliance"),
        _edge(n9["id"], n3["id"], "auth"),
        _edge(n15["id"], n3["id"], "provision"),
        _edge(n2["id"], n1["id"], "protect"),
        _edge(n4["id"], n3["id"], "federation"),
        _edge(n7["id"], n6["id"], "inspect"),
        _edge(n8["id"], n9["id"], "private-access"),
        _edge(n14["id"], n10["id"], "backup"),
    ]
    templates.append(
        {
            "id": f"tpl-{_uid()}",
            "name": "FedRAMP High Landing Zone",
            "category": "aws",
            "description": "FedRAMP High baseline architecture on AWS GovCloud with TIC 3.0 and FIPS encryption",
            "graph_json": json.dumps({"nodes": nodes, "edges": edges}),
            "tags": json.dumps(["aws", "fedramp", "high", "govcloud", "tic3", "regulated", "landing-zone"]),
        }
    )

    # 16. Multi-Cloud Landing Zone
    n1 = _node("iac-terraform", "Terraform Cloud", 100, 50)
    n2 = _node("iac-crossplane", "Crossplane", 300, 50)
    n3 = _node("aws-eks", "EKS", 100, 200)
    n4 = _node("aws-kms", "AWS KMS", 300, 200)
    n5 = _node("aws-s3", "S3", 500, 200)
    n6 = _node("az-aks", "AKS", 100, 350)
    n7 = _node("az-keyvault", "Key Vault", 300, 350)
    n8 = _node("az-blob", "Blob", 500, 350)
    n9 = _node("gcp-gke", "GKE", 100, 500)
    n10 = _node("gcp-kms", "Cloud KMS", 300, 500)
    n11 = _node("gcp-gcs", "GCS", 500, 500)
    n12 = _node("op-harbor", "Harbor Registry", 100, 650)
    n13 = _node("iac-argocd", "ArgoCD (GitOps)", 300, 650)
    n14 = _node("iac-vault", "HashiCorp Vault", 500, 650)
    nodes = [n1, n2, n3, n4, n5, n6, n7, n8, n9, n10, n11, n12, n13, n14]
    edges = [
        _edge(n2["id"], n3["id"], "manage"),
        _edge(n2["id"], n6["id"], "manage"),
        _edge(n2["id"], n9["id"], "manage"),
        _edge(n13["id"], n3["id"], "deploy"),
        _edge(n13["id"], n6["id"], "deploy"),
        _edge(n13["id"], n9["id"], "deploy"),
        _edge(n3["id"], n4["id"], "encrypt"),
        _edge(n6["id"], n7["id"], "encrypt"),
        _edge(n9["id"], n10["id"], "encrypt"),
        _edge(n1["id"], n2["id"], "orchestrate"),
        _edge(n5["id"], n4["id"], "encrypt"),
        _edge(n8["id"], n7["id"], "encrypt"),
        _edge(n11["id"], n10["id"], "encrypt"),
        _edge(n12["id"], n13["id"], "images"),
        _edge(n14["id"], n2["id"], "secrets"),
    ]
    templates.append(
        {
            "id": f"tpl-{_uid()}",
            "name": "Multi-Cloud Landing Zone",
            "category": "multi_cloud",
            "description": "Enterprise multi-cloud pattern with centralized governance via Crossplane and ArgoCD GitOps",
            "graph_json": json.dumps({"nodes": nodes, "edges": edges}),
            "tags": json.dumps(["multi-cloud", "aws", "azure", "gcp", "crossplane", "gitops", "landing-zone"]),
        }
    )

    # 17. AWS LZA Account Services Overview
    n1 = _node("aws-iam", "Control Tower", 50, 50)
    n2 = _node("aws-iam", "AWS Organizations", 220, 50)
    n3 = _node("aws-ec2", "CloudFormation", 390, 50)
    n4 = _node("aws-ec2", "CodeCommit", 50, 140)
    n5 = _node("aws-ec2", "CodePipeline", 220, 140)
    n6 = _node("aws-ec2", "CodeBuild", 390, 140)
    # Security OU — Log Archive Account
    n7 = _node("aws-ec2", "Amazon Kinesis", 600, 270)
    n8 = _node("aws-s3", "Amazon S3 (Logs)", 770, 270)
    n9 = _node("aws-kms", "AWS KMS", 940, 270)
    # Security OU — Audit Account
    n10 = _node("aws-securityhub", "Security Hub (Admin)", 600, 370)
    n11 = _node("aws-config", "AWS Config (Admin)", 770, 370)
    n12 = _node("aws-ec2", "GuardDuty (Admin)", 940, 370)
    n13 = _node("aws-ec2", "Amazon Macie (Admin)", 1110, 370)
    # Infrastructure OU — Network Account
    n14 = _node("aws-ec2", "Amazon VPC (Network)", 50, 500)
    n15 = _node("aws-ec2-asg", "Transit Gateway", 220, 500)
    n16 = _node("aws-ec2", "Direct Connect", 390, 500)
    n17 = _node("aws-ec2", "Network Firewall", 560, 500)
    # Infrastructure OU — Shared Services Account
    n18 = _node("aws-ec2", "Amazon VPC (Shared)", 50, 600)
    n19 = _node("aws-ec2", "Directory Service", 220, 600)
    # Workload Accounts
    n20 = _node("aws-ec2", "Dev Workload Account", 600, 730)
    n21 = _node("aws-ec2", "Test Workload Account", 770, 730)
    n22 = _node("aws-fargate", "Prod Workload Account", 940, 730)
    # IaC
    n23 = _node("iac-terraform", "LZA Accelerator", 50, 830)
    nodes = [n1, n2, n3, n4, n5, n6, n7, n8, n9, n10, n11, n12, n13, n14, n15, n16, n17, n18, n19, n20, n21, n22, n23]
    edges = [
        # Management Account governance
        _edge(n2["id"], n1["id"], "governance"),
        # CI/CD chain
        _edge(n5["id"], n6["id"], "build"),
        _edge(n6["id"], n4["id"], "source"),
        # Log Archive
        _edge(n7["id"], n8["id"], "stream"),
        _edge(n8["id"], n9["id"], "encrypt"),
        # Security Hub aggregation
        _edge(n11["id"], n10["id"], "compliance"),
        _edge(n12["id"], n10["id"], "findings"),
        _edge(n13["id"], n10["id"], "data findings"),
        # Infrastructure connectivity
        _edge(n15["id"], n16["id"], "connectivity"),
        _edge(n15["id"], n17["id"], "inspect"),
        _edge(n15["id"], n22["id"], "route"),
        _edge(n15["id"], n18["id"], "route"),
        # Shared Services auth
        _edge(n19["id"], n18["id"], "auth"),
        # LZA provisioning
        _edge(n23["id"], n2["id"], "provision"),
    ]
    templates.append(
        {
            "id": f"tpl-{_uid()}",
            "name": "AWS LZA Account Services Overview",
            "category": "aws",
            "description": "High-level view of AWS Landing Zone Accelerator showing key services "
            "in each account: Management (Control Tower, CI/CD), Security (Security Hub, "
            "Config, GuardDuty, Log Archive), Infrastructure (Transit Gateway, Network "
            "Firewall, Directory Service), and Workload accounts.",
            "graph_json": json.dumps({"nodes": nodes, "edges": edges}),
            "tags": json.dumps(
                ["aws", "lza", "landing-zone", "overview", "multi-account", "control-tower", "security-hub"]
            ),
        }
    )

    # 18. AWS LZA Deployment Pipeline
    n1 = _node("aws-ec2", "CloudFormation Installer Stack", 50, 50)
    n2 = _node("aws-ec2", "CodePipeline (Installer)", 250, 50)
    n3 = _node("aws-ec2", "CodePipeline (Core Pipeline)", 500, 50)
    n4 = _node("aws-ec2", "CodeBuild (CDK Synth)", 500, 150)
    n5 = _node("aws-s3", "S3 Config Bucket", 250, 150)
    n6 = _node("aws-kms", "KMS Key (Installer)", 50, 150)
    n7 = _node("aws-kms", "KMS Key (Core)", 700, 50)
    n8 = _node("aws-ec2", "SNS Topic (Alerts)", 700, 150)
    n9 = _node("aws-ec2", "Manual Approval Stage", 500, 250)
    n10 = _node("aws-ec2", "CDK Deploy (Multi-Account)", 250, 350)
    n11 = _node("aws-iam", "Management Account", 50, 450)
    n12 = _node("aws-s3", "Log Archive Account", 250, 450)
    n13 = _node("aws-securityhub", "Audit Account", 450, 450)
    n14 = _node("aws-ec2-asg", "Network Account", 650, 450)
    n15 = _node("aws-ec2", "Workload Accounts", 850, 450)
    n16 = _node("iac-terraform", "Config Files (YAML)", 50, 350)
    nodes = [n1, n2, n3, n4, n5, n6, n7, n8, n9, n10, n11, n12, n13, n14, n15, n16]
    edges = [
        _edge(n1["id"], n2["id"], "deploy"),
        _edge(n2["id"], n3["id"], "trigger"),
        _edge(n3["id"], n4["id"], "build"),
        _edge(n4["id"], n5["id"], "read"),
        _edge(n3["id"], n7["id"], "encrypt"),
        _edge(n2["id"], n6["id"], "encrypt"),
        _edge(n3["id"], n8["id"], "notify"),
        _edge(n4["id"], n9["id"], "review"),
        _edge(n9["id"], n10["id"], "approved"),
        _edge(n10["id"], n11["id"], "deploy"),
        _edge(n10["id"], n12["id"], "deploy"),
        _edge(n10["id"], n13["id"], "deploy"),
        _edge(n10["id"], n14["id"], "deploy"),
        _edge(n10["id"], n15["id"], "deploy"),
        _edge(n16["id"], n5["id"], "upload"),
    ]
    templates.append(
        {
            "id": f"tpl-{_uid()}",
            "name": "AWS LZA Deployment Pipeline",
            "category": "aws",
            "description": "AWS Landing Zone Accelerator deployment pipeline showing the 2-stage "
            "CodePipeline architecture — Installer stack bootstraps the Core pipeline, "
            "which runs CDK synth/deploy through a manual approval gate to provision "
            "Management, Log Archive, Audit, Network, and Workload accounts. "
            "YAML config files drive all account and service configuration.",
            "graph_json": json.dumps({"nodes": nodes, "edges": edges}),
            "tags": json.dumps(["aws", "lza", "pipeline", "cicd", "cdk", "deployment", "codepipeline"]),
        }
    )

    return templates


def _build_snippets():
    """Build reusable IDC snippet fragments."""
    snippets = []

    # 1. AWS Lambda + API Gateway
    n1 = _node("aws-apigw", "API Gateway", 50, 50)
    n2 = _node("aws-lambda", "Lambda", 200, 50)
    n3 = _node("aws-iam", "IAM Role", 200, 150)
    edges = [_edge(n1["id"], n2["id"], "invoke"), _edge(n2["id"], n3["id"], "assume")]
    snippets.append(
        {
            "id": "snp-idc-lambda-apigw",
            "name": "AWS Lambda + API Gateway",
            "category": "aws",
            "description": "Lambda function behind API Gateway with IAM role",
            "graph_json": json.dumps({"nodes": [n1, n2, n3], "edges": edges}),
            "tags": json.dumps(["aws", "serverless", "lambda", "api-gateway"]),
        }
    )

    # 2. EKS + ECR + ArgoCD
    n1 = _node("aws-eks", "EKS Cluster", 100, 50)
    n2 = _node("aws-ecr", "ECR Registry", 50, 150)
    n3 = _node("iac-argocd", "ArgoCD", 250, 150)
    edges = [_edge(n2["id"], n1["id"], "pull images"), _edge(n3["id"], n1["id"], "GitOps deploy")]
    snippets.append(
        {
            "id": "snp-idc-eks-ecr-argocd",
            "name": "EKS + ECR + ArgoCD",
            "category": "aws",
            "description": "Kubernetes cluster with container registry and GitOps deployment",
            "graph_json": json.dumps({"nodes": [n1, n2, n3], "edges": edges}),
            "tags": json.dumps(["aws", "kubernetes", "eks", "gitops", "argocd"]),
        }
    )

    # 3. RDS + ElastiCache + KMS
    n1 = _node("aws-aurora", "Aurora PostgreSQL", 50, 50)
    n2 = _node("aws-elasticache", "ElastiCache Redis", 50, 150)
    n3 = _node("aws-kms", "KMS", 250, 100)
    edges = [_edge(n1["id"], n3["id"], "encrypt"), _edge(n2["id"], n3["id"], "encrypt")]
    snippets.append(
        {
            "id": "snp-idc-rds-cache-kms",
            "name": "RDS + ElastiCache + KMS",
            "category": "aws",
            "description": "Database with in-memory cache and KMS encryption",
            "graph_json": json.dumps({"nodes": [n1, n2, n3], "edges": edges}),
            "tags": json.dumps(["aws", "database", "cache", "encryption"]),
        }
    )

    # 4. S3 + CloudFront
    n1 = _node("aws-s3", "S3 Bucket", 50, 50)
    n2 = _node("aws-cloudfront", "CloudFront CDN", 250, 50)
    n3 = _node("aws-kms", "KMS", 50, 150)
    edges = [_edge(n1["id"], n2["id"], "origin"), _edge(n1["id"], n3["id"], "encrypt")]
    snippets.append(
        {
            "id": "snp-idc-s3-cloudfront",
            "name": "S3 + CloudFront",
            "category": "aws",
            "description": "Object storage with CDN distribution and encryption",
            "graph_json": json.dumps({"nodes": [n1, n2, n3], "edges": edges}),
            "tags": json.dumps(["aws", "storage", "cdn", "s3"]),
        }
    )

    # 5. Serverless Event Pipeline
    n1 = _node("aws-eventbridge", "EventBridge", 50, 50)
    n2 = _node("aws-lambda", "Lambda Processor", 200, 50)
    n3 = _node("aws-sqs", "SQS Queue", 200, 150)
    n4 = _node("aws-dynamodb", "DynamoDB", 50, 150)
    edges = [
        _edge(n1["id"], n2["id"], "trigger"),
        _edge(n2["id"], n3["id"], "enqueue"),
        _edge(n2["id"], n4["id"], "write"),
    ]
    snippets.append(
        {
            "id": "snp-idc-event-pipeline",
            "name": "Serverless Event Pipeline",
            "category": "aws",
            "description": "EventBridge triggers Lambda which writes to SQS and DynamoDB",
            "graph_json": json.dumps({"nodes": [n1, n2, n3, n4], "edges": edges}),
            "tags": json.dumps(["aws", "serverless", "event-driven", "eventbridge"]),
        }
    )

    # 6. Azure AKS + ACR + Key Vault
    n1 = _node("az-aks", "AKS Cluster", 100, 50)
    n2 = _node("az-acr", "ACR Registry", 50, 150)
    n3 = _node("az-keyvault", "Key Vault", 250, 150)
    edges = [_edge(n2["id"], n1["id"], "pull images"), _edge(n1["id"], n3["id"], "secrets")]
    snippets.append(
        {
            "id": "snp-idc-aks-acr-keyvault",
            "name": "Azure AKS + ACR + Key Vault",
            "category": "azure",
            "description": "Azure Kubernetes with container registry and secret management",
            "graph_json": json.dumps({"nodes": [n1, n2, n3], "edges": edges}),
            "tags": json.dumps(["azure", "kubernetes", "aks", "keyvault"]),
        }
    )

    # 7. GCP BigQuery + Pub/Sub
    n1 = _node("gcp-pubsub", "Pub/Sub", 50, 50)
    n2 = _node("gcp-functions", "Cloud Function", 200, 50)
    n3 = _node("gcp-bigquery", "BigQuery", 200, 150)
    edges = [_edge(n1["id"], n2["id"], "trigger"), _edge(n2["id"], n3["id"], "load")]
    snippets.append(
        {
            "id": "snp-idc-gcp-bq-pubsub",
            "name": "GCP BigQuery + Pub/Sub",
            "category": "gcp",
            "description": "Analytics pipeline with Pub/Sub ingestion into BigQuery",
            "graph_json": json.dumps({"nodes": [n1, n2, n3], "edges": edges}),
            "tags": json.dumps(["gcp", "analytics", "bigquery", "pubsub"]),
        }
    )

    # 8. Multi-Cloud K8s
    n1 = _node("aws-eks", "EKS (AWS)", 50, 50)
    n2 = _node("az-aks", "AKS (Azure)", 250, 50)
    n3 = _node("iac-crossplane", "Crossplane", 150, 150)
    edges = [_edge(n3["id"], n1["id"], "manage"), _edge(n3["id"], n2["id"], "manage")]
    snippets.append(
        {
            "id": "snp-idc-multi-cloud-k8s",
            "name": "Multi-Cloud K8s",
            "category": "multi_cloud",
            "description": "EKS and AKS clusters managed by Crossplane",
            "graph_json": json.dumps({"nodes": [n1, n2, n3], "edges": edges}),
            "tags": json.dumps(["multi-cloud", "kubernetes", "crossplane"]),
        }
    )

    # 9. GPU ML Inference
    n1 = _node("aws-sagemaker", "SageMaker Endpoint", 50, 50)
    n2 = _node("aws-ec2-dedicated", "GPU Instance", 250, 50)
    n3 = _node("aws-s3", "Model Artifacts (S3)", 150, 150)
    edges = [_edge(n3["id"], n1["id"], "load model"), _edge(n1["id"], n2["id"], "inference")]
    snippets.append(
        {
            "id": "snp-idc-gpu-ml",
            "name": "GPU ML Inference",
            "category": "aws",
            "description": "SageMaker endpoint with GPU instance and S3 model store",
            "graph_json": json.dumps({"nodes": [n1, n2, n3], "edges": edges}),
            "tags": json.dumps(["aws", "ml", "gpu", "sagemaker"]),
        }
    )

    # 10. FIPS Encryption Stack
    n1 = _node("aws-kms", "KMS (FIPS 140-2 L3)", 50, 50)
    n2 = _node("aws-secrets", "Secrets Manager", 250, 50)
    n3 = _node("aws-ec2-dedicated", "Dedicated Host", 150, 150)
    edges = [_edge(n3["id"], n1["id"], "encrypt"), _edge(n3["id"], n2["id"], "retrieve")]
    snippets.append(
        {
            "id": "snp-idc-fips-encryption",
            "name": "FIPS Encryption Stack",
            "category": "security",
            "description": "FIPS 140-2 Level 3 KMS with Secrets Manager on dedicated host",
            "graph_json": json.dumps({"nodes": [n1, n2, n3], "edges": edges}),
            "tags": json.dumps(["fips", "encryption", "kms", "compliance", "CUI"]),
        }
    )

    # 11. SCCA BCAP (Boundary Cloud Access Point)
    n1 = _node("aws-ec2", "BCAP Proxy", 50, 50)
    n2 = _node("aws-ec2", "Network Firewall", 220, 50)
    n3 = _node("aws-ec2-asg", "Transit GW", 390, 50)
    edges = [_edge(n1["id"], n2["id"], "filter"), _edge(n2["id"], n3["id"], "route")]
    snippets.append(
        {
            "id": "snp-idc-scca-bcap",
            "name": "SCCA BCAP (Boundary Cloud Access Point)",
            "category": "security",
            "description": "DoD SCCA boundary with BCAP proxy, firewall, and transit gateway",
            "graph_json": json.dumps({"nodes": [n1, n2, n3], "edges": edges}),
            "tags": json.dumps(["scca", "bcap", "dod", "boundary", "network"]),
        }
    )

    # 12. Azure Hub-Spoke
    n1 = _node("az-fw", "Azure Firewall (Hub)", 150, 50)
    n2 = _node("az-vpn-gw", "VPN Gateway", 50, 170)
    n3 = _node("az-er", "ExpressRoute", 300, 170)
    edges = [_edge(n2["id"], n1["id"], "peering"), _edge(n3["id"], n1["id"], "peering")]
    snippets.append(
        {
            "id": "snp-idc-azure-hub-spoke",
            "name": "Azure Hub-Spoke",
            "category": "azure",
            "description": "Azure hub-spoke network with firewall, VPN gateway, and ExpressRoute",
            "graph_json": json.dumps({"nodes": [n1, n2, n3], "edges": edges}),
            "tags": json.dumps(["azure", "hub-spoke", "network", "firewall"]),
        }
    )

    # 13. GCP Shared VPC
    n1 = _node("gcp-vpc", "Shared VPC Host", 150, 50)
    n2 = _node("gcp-router", "Cloud Router", 50, 170)
    n3 = _node("gcp-armor", "Cloud Armor", 300, 170)
    edges = [_edge(n2["id"], n1["id"], "route"), _edge(n3["id"], n1["id"], "protect")]
    snippets.append(
        {
            "id": "snp-idc-gcp-shared-vpc",
            "name": "GCP Shared VPC",
            "category": "gcp",
            "description": "GCP Shared VPC host project with Cloud Router and Cloud Armor",
            "graph_json": json.dumps({"nodes": [n1, n2, n3], "edges": edges}),
            "tags": json.dumps(["gcp", "shared-vpc", "network", "router", "armor"]),
        }
    )

    # 14. Centralized Logging Stack
    n1 = _node("aws-s3", "Log Archive (S3)", 50, 50)
    n2 = _node("aws-securityhub", "Security Hub", 220, 50)
    n3 = _node("aws-config", "AWS Config", 390, 50)
    edges = [_edge(n3["id"], n2["id"], "findings"), _edge(n2["id"], n1["id"], "archive")]
    snippets.append(
        {
            "id": "snp-idc-central-logging",
            "name": "Centralized Logging Stack",
            "category": "security",
            "description": "Centralized logging with AWS Config, Security Hub, and S3 archive",
            "graph_json": json.dumps({"nodes": [n1, n2, n3], "edges": edges}),
            "tags": json.dumps(["aws", "logging", "security-hub", "config", "compliance"]),
        }
    )

    # 15. FIPS Encryption Stack (Multi-Cloud)
    n1 = _node("aws-kms", "AWS KMS (FIPS L3)", 50, 50)
    n2 = _node("az-keyvault", "Azure Key Vault (HSM)", 220, 50)
    n3 = _node("iac-vault", "HashiCorp Vault", 390, 50)
    edges = [
        _edge(n3["id"], n1["id"], "federation"),
        _edge(n3["id"], n2["id"], "federation"),
    ]
    snippets.append(
        {
            "id": "snp-idc-fips-multicloud",
            "name": "FIPS Encryption Stack (Multi-Cloud)",
            "category": "security",
            "description": "Multi-cloud FIPS encryption with AWS KMS, Azure Key Vault, and HashiCorp Vault federation",
            "graph_json": json.dumps({"nodes": [n1, n2, n3], "edges": edges}),
            "tags": json.dumps(["fips", "encryption", "multi-cloud", "kms", "keyvault", "vault"]),
        }
    )

    # 16. Zero Trust Identity
    n1 = _node("aws-cognito", "Identity Center", 50, 50)
    n2 = _node("az-entra", "Entra ID", 220, 50)
    n3 = _node("aws-kms", "KMS", 390, 50)
    n4 = _node("aws-iam", "IAM Policies", 220, 170)
    edges = [
        _edge(n1["id"], n4["id"], "federate"),
        _edge(n2["id"], n4["id"], "federate"),
        _edge(n3["id"], n4["id"], "encrypt"),
    ]
    snippets.append(
        {
            "id": "snp-idc-zero-trust-identity",
            "name": "Zero Trust Identity",
            "category": "security",
            "description": "Zero trust identity with Identity Center, Entra ID, KMS, and IAM policy enforcement",
            "graph_json": json.dumps({"nodes": [n1, n2, n3, n4], "edges": edges}),
            "tags": json.dumps(["zero-trust", "identity", "iam", "entra", "sso"]),
        }
    )

    # 17. Transit Gateway Hub
    n1 = _node("aws-ec2-asg", "Transit Gateway", 150, 50)
    n2 = _node("aws-ec2", "Inspection VPC", 50, 170)
    n3 = _node("aws-ec2", "Egress VPC", 300, 170)
    edges = [_edge(n1["id"], n2["id"], "inspect"), _edge(n1["id"], n3["id"], "egress")]
    snippets.append(
        {
            "id": "snp-idc-transit-gw-hub",
            "name": "Transit Gateway Hub",
            "category": "aws",
            "description": "AWS Transit Gateway with inspection and egress VPC attachments",
            "graph_json": json.dumps({"nodes": [n1, n2, n3], "edges": edges}),
            "tags": json.dumps(["aws", "transit-gateway", "network", "inspection", "egress"]),
        }
    )

    # 18. VDSS (Virtual Data Center Security Stack)
    n1 = _node("aws-ec2", "Security Groups", 50, 50)
    n2 = _node("aws-ec2", "NACLs", 220, 50)
    n3 = _node("aws-ec2", "VPC Endpoints", 390, 50)
    n4 = _node("aws-securityhub", "GuardDuty", 220, 170)
    edges = [_edge(n1["id"], n4["id"], "monitor"), _edge(n2["id"], n4["id"], "monitor")]
    snippets.append(
        {
            "id": "snp-idc-vdss",
            "name": "VDSS (Virtual Data Center Security Stack)",
            "category": "security",
            "description": "VDSS with Security Groups, NACLs, VPC Endpoints, and GuardDuty monitoring",
            "graph_json": json.dumps({"nodes": [n1, n2, n3, n4], "edges": edges}),
            "tags": json.dumps(["vdss", "security", "guardduty", "vpc", "network"]),
        }
    )

    # 19. LZA CI/CD Pipeline (Installer + Core)
    n1 = _node("aws-ec2", "CloudFormation (Installer Stack)", 50, 50)
    n2 = _node("aws-ec2", "CodePipeline (Core Pipeline)", 250, 50)
    n3 = _node("aws-ec2", "CodeBuild (CDK Synth)", 450, 50)
    n4 = _node("aws-s3", "S3 Config Bucket (aws-accelerator-config)", 250, 150)
    n5 = _node("aws-kms", "KMS (Pipeline Encryption)", 450, 150)
    edges = [
        _edge(n1["id"], n2["id"], "deploy"),
        _edge(n2["id"], n3["id"], "build"),
        _edge(n3["id"], n4["id"], "read config"),
        _edge(n2["id"], n5["id"], "encrypt"),
    ]
    snippets.append(
        {
            "id": "snp-idc-lza-cicd-pipeline",
            "name": "LZA CI/CD Pipeline (Installer + Core)",
            "category": "aws",
            "description": "LZA 2-stage deployment pipeline — CloudFormation installer bootstraps "
            "CodePipeline which runs CodeBuild CDK synth against S3 config bucket",
            "graph_json": json.dumps({"nodes": [n1, n2, n3, n4, n5], "edges": edges}),
            "tags": json.dumps(["aws", "lza", "pipeline", "cicd", "cdk"]),
        }
    )

    # 20. Centralized Inspection (NFW + GWLB + TGW)
    n1 = _node("aws-ec2", "Network Firewall", 50, 50)
    n2 = _node("aws-ec2", "Gateway Load Balancer", 250, 50)
    n3 = _node("aws-ec2-asg", "Transit Gateway (Appliance Mode)", 150, 150)
    n4 = _node("aws-ec2", "Inspection VPC Subnet AZ-A", 50, 250)
    n5 = _node("aws-ec2", "Inspection VPC Subnet AZ-B", 250, 250)
    edges = [
        _edge(n3["id"], n1["id"], "route"),
        _edge(n1["id"], n2["id"], "inline inspect"),
        _edge(n2["id"], n4["id"], "forward"),
        _edge(n2["id"], n5["id"], "forward"),
        _edge(n4["id"], n3["id"], "return"),
    ]
    snippets.append(
        {
            "id": "snp-idc-lza-centralized-inspection",
            "name": "Centralized Inspection (NFW + GWLB + TGW)",
            "category": "aws",
            "description": "LZA network security pattern — appliance-mode Transit Gateway routes "
            "through Network Firewall and Gateway Load Balancer for centralized inspection",
            "graph_json": json.dumps({"nodes": [n1, n2, n3, n4, n5], "edges": edges}),
            "tags": json.dumps(["aws", "lza", "inspection", "nfw", "gwlb", "appliance-mode"]),
        }
    )

    # 21. IPAM Pool Hierarchy
    n1 = _node("aws-ec2", "IPAM (Base Pool 10.0.0.0/8)", 150, 50)
    n2 = _node("aws-ec2", "Home Region Pool (10.0.0.0/16)", 50, 150)
    n3 = _node("aws-ec2", "West Region Pool (10.1.0.0/16)", 250, 150)
    n4 = _node("aws-ec2", "Prod Pool (10.0.0.0/24)", 50, 250)
    edges = [
        _edge(n1["id"], n2["id"], "allocate"),
        _edge(n1["id"], n3["id"], "allocate"),
        _edge(n2["id"], n4["id"], "allocate"),
    ]
    snippets.append(
        {
            "id": "snp-idc-lza-ipam-hierarchy",
            "name": "IPAM Pool Hierarchy",
            "category": "aws",
            "description": "AWS IPAM 3-tier pool hierarchy for centralized IP address management "
            "— base pool allocates to regional pools which allocate to environment pools",
            "graph_json": json.dumps({"nodes": [n1, n2, n3, n4], "edges": edges}),
            "tags": json.dumps(["aws", "lza", "ipam", "ip-management"]),
        }
    )

    # 22. Route53 DNS Architecture
    n1 = _node("aws-ec2", "Route 53 Private Hosted Zone", 150, 50)
    n2 = _node("aws-ec2", "Resolver Inbound Endpoint", 50, 150)
    n3 = _node("aws-ec2", "Resolver Outbound Endpoint", 250, 150)
    n4 = _node("aws-ec2", "DNS Firewall Rule Group", 150, 250)
    edges = [
        _edge(n2["id"], n1["id"], "resolve"),
        _edge(n3["id"], n1["id"], "forward"),
        _edge(n4["id"], n1["id"], "filter"),
    ]
    snippets.append(
        {
            "id": "snp-idc-lza-dns-architecture",
            "name": "Route53 DNS Architecture",
            "category": "aws",
            "description": "Centralized DNS with Route 53 private hosted zones, resolver inbound/outbound "
            "endpoints, and DNS Firewall rule groups for domain filtering",
            "graph_json": json.dumps({"nodes": [n1, n2, n3, n4], "edges": edges}),
            "tags": json.dumps(["aws", "lza", "dns", "route53", "resolver"]),
        }
    )

    return snippets


def init_db():
    """Create tables and seed templates and snippets."""
    conn = get_connection()
    try:
        for stmt in SCHEMA.split(";"):
            stmt = stmt.strip()
            if stmt:
                conn.execute(stmt)
        conn.commit()

        # Seed templates if empty
        existing = conn.execute("SELECT COUNT(*) AS cnt FROM idc_templates").fetchone()
        if dict(existing).get("cnt", 0) == 0:
            for tpl in _build_templates():
                conn.execute(
                    "INSERT INTO idc_templates "
                    "(id, name, category, description, graph_json, tags) "
                    "VALUES (%s,%s,%s,%s,%s,%s)",
                    (
                        tpl["id"],
                        tpl["name"],
                        tpl["category"],
                        tpl["description"],
                        tpl["graph_json"],
                        tpl["tags"],
                    ),
                )
            conn.commit()
            print(f"IDC: seeded {len(_build_templates())} templates", file=sys.stderr)

        # Seed snippets (upsert)
        added = 0
        for snp in _build_snippets():
            existing_snp = conn.execute("SELECT 1 FROM idc_snippets WHERE id=%s", (snp["id"],)).fetchone()
            if not existing_snp:
                conn.execute(
                    "INSERT INTO idc_snippets (id, name, category, description, graph_json, tags) VALUES (%s,%s,%s,%s,%s,%s)",
                    (snp["id"], snp["name"], snp["category"], snp["description"], snp["graph_json"], snp["tags"]),
                )
                added += 1
        if added:
            conn.commit()
            print(f"IDC: seeded {added} snippets", file=sys.stderr)

        # Seed runbooks (upsert by id)
        from tools.infra_canvas.runbooks import seed_runbooks
        rb_added = seed_runbooks()
        if rb_added:
            print(f"IDC: seeded {rb_added} runbooks", file=sys.stderr)

        # CAM extension: idc_migration_baselines — before/after snapshot for any container migration
        conn.executescript("""
CREATE TABLE IF NOT EXISTS idc_migration_baselines (
    id              TEXT PRIMARY KEY,
    design_id       TEXT REFERENCES infra_designs(id) ON DELETE CASCADE,
    baseline_type   TEXT NOT NULL DEFAULT 'source'
        CHECK(baseline_type IN ('source','target','cutover','rollback')),
    platform        TEXT NOT NULL DEFAULT 'k8s',
    label           TEXT DEFAULT '',
    snapshot_json   TEXT DEFAULT '{}',
    diff_json       TEXT DEFAULT '{}',
    captured_at     TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_idc_migration_baselines_design ON idc_migration_baselines(design_id);
CREATE INDEX IF NOT EXISTS idx_idc_migration_baselines_type   ON idc_migration_baselines(baseline_type);
""")
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    if str(_ICDEV_ROOT) not in sys.path:
        sys.path.insert(0, str(_ICDEV_ROOT))
    init_db()
    print("IDC database initialized.")
