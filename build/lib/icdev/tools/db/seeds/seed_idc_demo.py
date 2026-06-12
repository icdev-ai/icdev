#!/usr/bin/env python3
# CUI // SP-CTI
"""IDC Demo Seed -- populates infra_canvas.db with realistic infrastructure design demo data.

Tables seeded:
  infra_designs (5), idc_assessments (5), idc_audit (10), idc_versions (8),
  idc_collab_sessions (4), idc_infra_resources (12), idc_infra_snapshots (6)

Usage:
    python tools/db/seeds/seed_idc_demo.py --all [--reset] [--json]
    python tools/db/seeds/seed_idc_demo.py --verify --json
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
        from tools.infra_canvas.db.init_db import get_connection, init_db
        conn = get_connection()
        init_db()
        return conn
    except Exception:
        db = _ROOT / "data" / "infra_canvas.db"
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
        "idc_infra_snapshots", "idc_infra_resources", "idc_collab_sessions",
        "idc_versions", "idc_audit", "idc_assessments", "infra_designs",
    ):
        try:
            conn.execute(f"DELETE FROM {tbl}")
        except Exception:
            pass
    conn.commit()


_DESIGN_IDS = {f"idc-design-{i:03d}": _uid() for i in range(5)}

_INFRA_DESIGNS = [
    {
        "id": _DESIGN_IDS["idc-design-000"],
        "name": "AWS GovCloud IL4 VPC",
        "description": "AWS GovCloud VPC architecture with public/private subnets, NAT Gateway, Application Load Balancer, EKS cluster, Aurora PostgreSQL, and FIPS 140-2 KMS encryption.",
        "graph_json": json.dumps({
            "nodes": [
                {"id": "vpc-1", "type": "vpc", "label": "GovCloud VPC", "x": 50, "y": 50},
                {"id": "pub-subnet", "type": "subnet", "label": "Public Subnet", "x": 50, "y": 150},
                {"id": "priv-subnet", "type": "subnet", "label": "Private Subnet", "x": 250, "y": 150},
                {"id": "nat-gw", "type": "nat", "label": "NAT Gateway", "x": 150, "y": 100},
                {"id": "alb", "type": "lb", "label": "Application LB", "x": 50, "y": 250},
                {"id": "eks", "type": "k8s", "label": "EKS Cluster", "x": 250, "y": 250},
                {"id": "aurora", "type": "db", "label": "Aurora PostgreSQL", "x": 450, "y": 250},
                {"id": "kms", "type": "key", "label": "KMS (FIPS)", "x": 450, "y": 150},
                {"id": "s3", "type": "storage", "label": "S3 Bucket", "x": 450, "y": 350},
                {"id": "cloudwatch", "type": "monitor", "label": "CloudWatch", "x": 50, "y": 350},
            ],
            "edges": [
                {"id": "e1", "source": "vpc-1", "target": "pub-subnet"},
                {"id": "e2", "source": "vpc-1", "target": "priv-subnet"},
                {"id": "e3", "source": "pub-subnet", "target": "nat-gw"},
                {"id": "e4", "source": "nat-gw", "target": "priv-subnet"},
                {"id": "e5", "source": "alb", "target": "eks"},
                {"id": "e6", "source": "eks", "target": "aurora"},
                {"id": "e7", "source": "aurora", "target": "kms"},
                {"id": "e8", "source": "s3", "target": "kms"},
                {"id": "e9", "source": "eks", "target": "s3"},
                {"id": "e10", "source": "cloudwatch", "target": "eks"},
            ],
        }),
        "template_id": "tpl-aws-govcloud-il4",
    },
    {
        "id": _DESIGN_IDS["idc-design-001"],
        "name": "Azure Gov VNet Hub-Spoke",
        "description": "Azure Government VNet with hub-spoke topology: hub VNet with Azure Firewall, VPN Gateway, and ExpressRoute; spoke VNets for workloads with NSG and UDR enforcement.",
        "graph_json": json.dumps({
            "nodes": [
                {"id": "hub-vnet", "type": "vnet", "label": "Hub VNet", "x": 300, "y": 50},
                {"id": "spoke-1", "type": "vnet", "label": "Spoke 1 (Workloads)", "x": 100, "y": 200},
                {"id": "spoke-2", "type": "vnet", "label": "Spoke 2 (Data)", "x": 500, "y": 200},
                {"id": "az-fw", "type": "firewall", "label": "Azure Firewall", "x": 300, "y": 150},
                {"id": "vpn-gw", "type": "vpn", "label": "VPN Gateway", "x": 200, "y": 100},
                {"id": "expressroute", "type": "circuit", "label": "ExpressRoute", "x": 400, "y": 100},
                {"id": "aks", "type": "k8s", "label": "AKS", "x": 100, "y": 300},
                {"id": "sql", "type": "db", "label": "Azure SQL", "x": 500, "y": 300},
                {"id": "keyvault", "type": "key", "label": "Key Vault", "x": 300, "y": 300},
            ],
            "edges": [
                {"id": "e1", "source": "hub-vnet", "target": "az-fw"},
                {"id": "e2", "source": "hub-vnet", "target": "vpn-gw"},
                {"id": "e3", "source": "hub-vnet", "target": "expressroute"},
                {"id": "e4", "source": "spoke-1", "target": "hub-vnet"},
                {"id": "e5", "source": "spoke-2", "target": "hub-vnet"},
                {"id": "e6", "source": "aks", "target": "spoke-1"},
                {"id": "e7", "source": "sql", "target": "spoke-2"},
                {"id": "e8", "source": "aks", "target": "keyvault"},
                {"id": "e9", "source": "sql", "target": "keyvault"},
            ],
        }),
        "template_id": "tpl-azure-gov-hub-spoke",
    },
    {
        "id": _DESIGN_IDS["idc-design-002"],
        "name": "On-Prem Data Center",
        "description": "Traditional on-premises data center with core/distribution/access layer switches, VMware vSphere cluster, SAN storage, and Palo Alto firewalls.",
        "graph_json": json.dumps({
            "nodes": [
                {"id": "core-sw", "type": "switch", "label": "Core Switch", "x": 300, "y": 50},
                {"id": "dist-sw-1", "type": "switch", "label": "Distribution SW1", "x": 200, "y": 150},
                {"id": "dist-sw-2", "type": "switch", "label": "Distribution SW2", "x": 400, "y": 150},
                {"id": "access-1", "type": "switch", "label": "Access SW1", "x": 150, "y": 250},
                {"id": "access-2", "type": "switch", "label": "Access SW2", "x": 250, "y": 250},
                {"id": "esxi-1", "type": "host", "label": "ESXi Host 1", "x": 150, "y": 350},
                {"id": "esxi-2", "type": "host", "label": "ESXi Host 2", "x": 250, "y": 350},
                {"id": "san", "type": "storage", "label": "SAN Storage", "x": 450, "y": 350},
                {"id": "fw-1", "type": "firewall", "label": "Palo Alto FW1", "x": 100, "y": 450},
                {"id": "fw-2", "type": "firewall", "label": "Palo Alto FW2", "x": 300, "y": 450},
            ],
            "edges": [
                {"id": "e1", "source": "core-sw", "target": "dist-sw-1"},
                {"id": "e2", "source": "core-sw", "target": "dist-sw-2"},
                {"id": "e3", "source": "dist-sw-1", "target": "access-1"},
                {"id": "e4", "source": "dist-sw-2", "target": "access-2"},
                {"id": "e5", "source": "access-1", "target": "esxi-1"},
                {"id": "e6", "source": "access-2", "target": "esxi-2"},
                {"id": "e7", "source": "dist-sw-2", "target": "san"},
                {"id": "e8", "source": "fw-1", "target": "core-sw"},
                {"id": "e9", "source": "fw-2", "target": "core-sw"},
            ],
        }),
        "template_id": "tpl-onprem-dc",
    },
    {
        "id": _DESIGN_IDS["idc-design-003"],
        "name": "Hybrid Multi-Cloud K8s",
        "description": "Hybrid Kubernetes platform spanning AWS EKS, Azure AKS, and on-prem K8s with Crossplane for unified control plane, ArgoCD for GitOps, and HashiCorp Vault for secrets.",
        "graph_json": json.dumps({
            "nodes": [
                {"id": "crossplane", "type": "control", "label": "Crossplane", "x": 300, "y": 50},
                {"id": "eks", "type": "k8s", "label": "EKS (AWS)", "x": 100, "y": 150},
                {"id": "aks", "type": "k8s", "label": "AKS (Azure)", "x": 300, "y": 150},
                {"id": "onprem-k8s", "type": "k8s", "label": "On-Prem K8s", "x": 500, "y": 150},
                {"id": "argocd", "type": "gitops", "label": "ArgoCD", "x": 300, "y": 250},
                {"id": "vault", "type": "key", "label": "HashiCorp Vault", "x": 500, "y": 250},
                {"id": "ecr", "type": "registry", "label": "ECR", "x": 50, "y": 250},
                {"id": "acr", "type": "registry", "label": "ACR", "x": 150, "y": 250},
                {"id": "harbor", "type": "registry", "label": "Harbor", "x": 400, "y": 350},
                {"id": "prometheus", "type": "monitor", "label": "Prometheus", "x": 100, "y": 350},
            ],
            "edges": [
                {"id": "e1", "source": "crossplane", "target": "eks"},
                {"id": "e2", "source": "crossplane", "target": "aks"},
                {"id": "e3", "source": "crossplane", "target": "onprem-k8s"},
                {"id": "e4", "source": "argocd", "target": "eks"},
                {"id": "e5", "source": "argocd", "target": "aks"},
                {"id": "e6", "source": "argocd", "target": "onprem-k8s"},
                {"id": "e7", "source": "vault", "target": "eks"},
                {"id": "e8", "source": "vault", "target": "aks"},
                {"id": "e9", "source": "ecr", "target": "eks"},
                {"id": "e10", "source": "acr", "target": "aks"},
                {"id": "e11", "source": "harbor", "target": "onprem-k8s"},
                {"id": "e12", "source": "prometheus", "target": "eks"},
            ],
        }),
        "template_id": "tpl-hybrid-k8s",
    },
    {
        "id": _DESIGN_IDS["idc-design-004"],
        "name": "DR Site Architecture",
        "description": "Disaster recovery site architecture with active-passive replication, automated failover via DNS, and cross-region backup with RPO 15 minutes and RTO 4 hours.",
        "graph_json": json.dumps({
            "nodes": [
                {"id": "primary-dc", "type": "dc", "label": "Primary DC", "x": 100, "y": 50},
                {"id": "dr-dc", "type": "dc", "label": "DR DC", "x": 500, "y": 50},
                {"id": "route53", "type": "dns", "label": "Route 53 DNS", "x": 300, "y": 50},
                {"id": "primary-db", "type": "db", "label": "Primary DB", "x": 100, "y": 150},
                {"id": "replica-db", "type": "db", "label": "Replica DB", "x": 500, "y": 150},
                {"id": "s3-primary", "type": "storage", "label": "S3 Primary", "x": 100, "y": 250},
                {"id": "s3-dr", "type": "storage", "label": "S3 DR", "x": 500, "y": 250},
                {"id": "backup", "type": "backup", "label": "AWS Backup", "x": 300, "y": 150},
                {"id": "monitor", "type": "monitor", "label": "DR Monitor", "x": 300, "y": 250},
                {"id": "runbook", "type": "runbook", "label": "Failover Runbook", "x": 300, "y": 350},
            ],
            "edges": [
                {"id": "e1", "source": "route53", "target": "primary-dc"},
                {"id": "e2", "source": "route53", "target": "dr-dc"},
                {"id": "e3", "source": "primary-db", "target": "replica-db", "label": "replicate"},
                {"id": "e4", "source": "s3-primary", "target": "s3-dr", "label": "cross-region"},
                {"id": "e5", "source": "backup", "target": "primary-db"},
                {"id": "e6", "source": "backup", "target": "s3-primary"},
                {"id": "e7", "source": "monitor", "target": "primary-dc"},
                {"id": "e8", "source": "monitor", "target": "dr-dc"},
                {"id": "e9", "source": "runbook", "target": "route53"},
            ],
        }),
        "template_id": "tpl-dr-site",
    },
]

_ASSESSMENTS = []
for i, design in enumerate(_INFRA_DESIGNS):
    score = random.uniform(72.0, 96.0)
    _ASSESSMENTS.append({
        "id": _uid(),
        "design_id": design["id"],
        "assessment_type": random.choice(["compliance", "security", "performance", "cost", "availability"]),
        "findings_json": json.dumps([
            {"severity": "high", "control": "SC-7", "finding": "Perimeter firewall rule review needed"},
            {"severity": "medium", "control": "AC-3", "finding": "RBAC policy gap in dev namespace"},
            {"severity": "low", "control": "AU-6", "finding": "Log retention policy should be 1 year"},
        ]),
        "score": round(score, 1),
    })

_INFRA_RESOURCES = []
_csp_regions = [
    ("aws", "us-gov-west-1"), ("aws", "us-gov-east-1"),
    ("azure", "usgovvirginia"), ("azure", "usgovarizona"),
    ("gcp", "us-central1"), ("oci", "us-ashburn-1"),
]
_resource_types = [
    "ec2", "s3", "rds", "lambda", "eks", "vpc",
    "vm", "storage_account", "sql_database", "aks",
    "compute_instance", "object_storage", "autonomous_db",
]
for i in range(12):
    csp, region = _csp_regions[i % len(_csp_regions)]
    rtype = _resource_types[i % len(_resource_types)]
    cost = round(random.uniform(50.0, 5000.0), 2)
    _INFRA_RESOURCES.append({
        "id": i + 1,
        "csp": csp,
        "region": region,
        "resource_type": rtype,
        "resource_name": f"{csp}-{rtype}-{i+1:03d}",
        "classification": random.choice(["UNCLASSIFIED", "CUI", "SECRET"]),
        "tags": json.dumps({"env": random.choice(["dev", "staging", "prod"]), "owner": f"team-{i+1}"}),
        "cost_per_month": cost,
        "config": json.dumps({"instance_type": random.choice(["t3.medium", "m5.large", "c5.xlarge"]), "encrypted": True}),
    })

_INFRA_SNAPSHOTS = []
for i in range(6):
    csp, region = _csp_regions[i % len(_csp_regions)]
    rc = random.randint(5, 50)
    _INFRA_SNAPSHOTS.append({
        "id": i + 1,
        "snapshot_id": f"snap-{i+1:04d}",
        "csp": csp,
        "region": region,
        "classification": random.choice(["UNCLASSIFIED", "CUI"]),
        "resource_count": rc,
        "baseline_hash": f"sha256:{uuid.uuid4().hex[:16]}",
        "notes": f"Baseline snapshot for {csp}/{region}",
    })


def seed_infra_designs(conn) -> int:
    sql = """INSERT OR IGNORE INTO infra_designs (
        id, name, description, graph_json, template_id, classification, created_at, updated_at
    ) VALUES (?,?,?,?,?,?,?,?)"""
    count = 0
    for row in _INFRA_DESIGNS:
        _safe_execute(conn, sql, (
            row["id"], row["name"], row["description"], row["graph_json"],
            row["template_id"], "CUI", _ts(count * 3), _ts(count * 3),
        ))
        count += 1
    return count


def seed_assessments(conn) -> int:
    sql = """INSERT OR IGNORE INTO idc_assessments (
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
    sql = """INSERT OR IGNORE INTO idc_audit (
        id, design_id, "user", action, detail, classification, created_at
    ) VALUES (?,?,?,?,?,?,?)"""
    count = 0
    actions = [
        ("design_created", "Infrastructure design created"),
        ("assessment_run", "Compliance assessment executed"),
        ("resource_discovered", "New cloud resource discovered via scan"),
        ("snapshot_taken", "Infrastructure baseline snapshot captured"),
        ("control_modified", "Security control configuration modified"),
    ]
    for i in range(10):
        design_id = _INFRA_DESIGNS[i % len(_INFRA_DESIGNS)]["id"]
        action, detail = actions[i % len(actions)]
        _safe_execute(conn, sql, (
            _uid(), design_id, random.choice(["arch-smith", "ops-jones", "system"]),
            action, detail, "CUI", _ts(i * 2),
        ))
        count += 1
    return count


def seed_versions(conn) -> int:
    sql = """INSERT OR IGNORE INTO idc_versions (
        id, design_id, version_number, graph_json, change_summary, user_id, created_at
    ) VALUES (?,?,?,?,?,?,?)"""
    count = 0
    for i in range(8):
        design_id = _INFRA_DESIGNS[i % len(_INFRA_DESIGNS)]["id"]
        _safe_execute(conn, sql, (
            _uid(), design_id, i + 1, _INFRA_DESIGNS[i % len(_INFRA_DESIGNS)]["graph_json"],
            f"Version {i+1}: updated infrastructure topology", "system", _ts(i * 5),
        ))
        count += 1
    return count


def seed_collab_sessions(conn) -> int:
    sql = """INSERT OR IGNORE INTO idc_collab_sessions (
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
        design_id = _INFRA_DESIGNS[i % len(_INFRA_DESIGNS)]["id"]
        user_id, user_name, color = users[i]
        _safe_execute(conn, sql, (
            _uid(), design_id, user_id, user_name, color,
            _ts(i * 6), _ts(i * 6 + 2), 1 if random.random() > 0.3 else 0,
        ))
        count += 1
    return count


def seed_infra_resources(conn) -> int:
    sql = """INSERT OR IGNORE INTO idc_infra_resources (
        id, csp, region, resource_type, resource_name, classification,
        tags, cost_per_month, config, created_at
    ) VALUES (?,?,?,?,?,?,?,?,?,?)"""
    count = 0
    for row in _INFRA_RESOURCES:
        _safe_execute(conn, sql, (
            row["id"], row["csp"], row["region"], row["resource_type"], row["resource_name"],
            row["classification"], row["tags"], row["cost_per_month"], row["config"],
            _ts(count * 2),
        ))
        count += 1
    return count


def seed_infra_snapshots(conn) -> int:
    sql = """INSERT OR IGNORE INTO idc_infra_snapshots (
        id, snapshot_id, csp, region, classification, resource_count,
        baseline_hash, notes, created_at
    ) VALUES (?,?,?,?,?,?,?,?,?)"""
    count = 0
    for row in _INFRA_SNAPSHOTS:
        _safe_execute(conn, sql, (
            row["id"], row["snapshot_id"], row["csp"], row["region"],
            row["classification"], row["resource_count"], row["baseline_hash"],
            row["notes"], _ts(count * 4),
        ))
        count += 1
    return count


def verify(conn) -> dict:
    result = {}
    for tbl in (
        "infra_designs", "idc_assessments", "idc_audit", "idc_versions",
        "idc_collab_sessions", "idc_infra_resources", "idc_infra_snapshots",
    ):
        try:
            row = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()
            result[tbl] = row[0]
        except Exception as exc:
            result[tbl] = f"error: {exc}"
    return result


def main():
    parser = argparse.ArgumentParser(description="IDC Demo Seed")
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
            "infra_designs": seed_infra_designs(conn),
            "idc_assessments": seed_assessments(conn),
            "idc_audit": seed_audit(conn),
            "idc_versions": seed_versions(conn),
            "idc_collab_sessions": seed_collab_sessions(conn),
            "idc_infra_resources": seed_infra_resources(conn),
            "idc_infra_snapshots": seed_infra_snapshots(conn),
        }
        conn.commit()

        if args.json:
            print(json.dumps({"success": True, "seeded": counts, "verify": verify(conn)}, indent=2))
        else:
            print(f"[seed_idc] Seeded {counts}")
            print(f"[seed_idc] Verify: {verify(conn)}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
