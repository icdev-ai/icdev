#!/usr/bin/env python3
# CUI // SP-CTI
"""BDC Demo Seed -- populates boundary_canvas.db with realistic boundary design demo data.

Tables seeded:
  boundary_designs (6), bd_assessments (6), bd_isa_tracker (8),
  bd_audit (12), bd_versions (10), bd_collab_sessions (4),
  bd_alerts (6), bd_authorized_components (5)

Usage:
    python tools/db/seeds/seed_bdc_demo.py --all [--reset] [--json]
    python tools/db/seeds/seed_bdc_demo.py --verify --json
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
        from tools.boundary_canvas.db.init_db import get_connection, init_db
        conn = get_connection()
        init_db()
        return conn
    except Exception:
        db = _ROOT / "data" / "boundary_canvas.db"
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
        "bd_authorized_components",
        "bd_alerts",
        "bd_collab_sessions",
        "bd_versions",
        "bd_audit",
        "bd_isa_tracker",
        "bd_assessments",
        "boundary_designs",
    ):
        try:
            conn.execute(f"DELETE FROM {tbl}")
        except Exception:
            pass
    conn.commit()


_DESIGN_IDS = {f"bdc-design-{i:03d}": _uid() for i in range(6)}

_BOUNDARY_DESIGNS = [
    {
        "id": _DESIGN_IDS["bdc-design-000"],
        "name": "DoD IL5 CUI ATO Boundary",
        "description": "Single system ATO boundary with DMZ, internal application server, external API partner, boundary firewall, IDS/IPS, SIEM, and ISA agreement.",
        "graph_json": json.dumps({
            "nodes": [
                {"id": "bnd-1", "type": "bnd-ato", "label": "ATO Boundary", "x": 80, "y": 30, "width": 550, "height": 360},
                {"id": "sys-1", "type": "sys-internal", "label": "Application Server", "x": 400, "y": 150},
                {"id": "ctrl-fw-1", "type": "ctrl-firewall", "label": "Boundary Firewall", "x": 140, "y": 120},
                {"id": "ctrl-ids-1", "type": "ctrl-ids-ips", "label": "IDS/IPS", "x": 200, "y": 300},
                {"id": "ctrl-siem-1", "type": "ctrl-siem", "label": "SIEM", "x": 400, "y": 300},
                {"id": "isa-api-1", "type": "isa-api", "label": "Partner API ISA", "x": 700, "y": 150},
                {"id": "sys-ext-1", "type": "sys-external", "label": "External Partner API", "x": 900, "y": 150},
            ],
            "edges": [
                {"id": "e1", "source": "sys-1", "target": "isa-api-1", "type": "isa-api"},
                {"id": "e2", "source": "isa-api-1", "target": "sys-ext-1"},
                {"id": "e3", "source": "ctrl-fw-1", "target": "bnd-1"},
                {"id": "e4", "source": "ctrl-ids-1", "target": "bnd-1"},
                {"id": "e5", "source": "ctrl-siem-1", "target": "bnd-1"},
            ],
        }),
        "template_id": "bdc-tpl-single-ato",
    },
    {
        "id": _DESIGN_IDS["bdc-design-001"],
        "name": "Multi-Enclave DoD Program",
        "description": "DoD program ATO boundary with CUI (IL5), SECRET (IL6), and TS/SCI enclaves. Includes cross-domain solutions between classification levels, BCAP for cloud egress, and GovCloud VPN interconnection.",
        "graph_json": json.dumps({
            "nodes": [
                {"id": "bnd-ato-1", "type": "bnd-ato", "label": "Program ATO Boundary", "x": 20, "y": 20, "width": 1060, "height": 580},
                {"id": "bnd-cui-1", "type": "bnd-classification", "label": "CUI Enclave (IL5)", "x": 60, "y": 80, "width": 280, "height": 240},
                {"id": "bnd-secret-1", "type": "bnd-classification", "label": "SECRET Enclave (IL6)", "x": 400, "y": 80, "width": 280, "height": 240},
                {"id": "bnd-ts-1", "type": "bnd-classification", "label": "TS/SCI SCIF", "x": 740, "y": 80, "width": 280, "height": 240},
                {"id": "sys-cui-1", "type": "sys-internal", "label": "CUI Mission App", "x": 130, "y": 170},
                {"id": "sys-secret-1", "type": "sys-internal", "label": "SECRET C2 System", "x": 470, "y": 170},
                {"id": "sys-ts-1", "type": "sys-internal", "label": "TS/SCI Intel System", "x": 810, "y": 170},
                {"id": "isa-cds-1", "type": "isa-cross-domain", "label": "CUI-to-SECRET CDS", "x": 350, "y": 270},
                {"id": "isa-cds-2", "type": "isa-cross-domain", "label": "SECRET-to-TS CDS", "x": 690, "y": 270},
                {"id": "ctrl-fw-main", "type": "ctrl-firewall", "label": "Program Firewall", "x": 60, "y": 400},
                {"id": "ctrl-ids-main", "type": "ctrl-ids-ips", "label": "Program IDS/IPS", "x": 230, "y": 400},
                {"id": "ctrl-siem-main", "type": "ctrl-siem", "label": "Program SIEM", "x": 400, "y": 400},
            ],
            "edges": [
                {"id": "e1", "source": "sys-cui-1", "target": "isa-cds-1", "label": "CUI data"},
                {"id": "e2", "source": "isa-cds-1", "target": "sys-secret-1", "label": "filtered"},
                {"id": "e3", "source": "sys-secret-1", "target": "isa-cds-2", "label": "SECRET data"},
                {"id": "e4", "source": "isa-cds-2", "target": "sys-ts-1", "label": "filtered"},
                {"id": "e5", "source": "ctrl-fw-main", "target": "bnd-ato-1", "label": "perimeter"},
                {"id": "e6", "source": "ctrl-ids-main", "target": "bnd-ato-1", "label": "monitor"},
                {"id": "e7", "source": "ctrl-siem-main", "target": "bnd-ato-1", "label": "log"},
            ],
        }),
        "template_id": "bdc-tpl-multi-enclave-dod",
    },
    {
        "id": _DESIGN_IDS["bdc-design-002"],
        "name": "FedRAMP Cloud Authorization",
        "description": "FedRAMP authorization boundary for a CSP with agency application, FedRAMP-authorized SaaS integrations (IdP, SIEM), TIC 3.0 CAP, SAML federation, and agency VPN ISA.",
        "graph_json": json.dumps({
            "nodes": [
                {"id": "bnd-frp-1", "type": "bnd-fedramp", "label": "FedRAMP Authorization Boundary", "x": 50, "y": 20, "width": 550, "height": 480},
                {"id": "sys-int-1", "type": "sys-internal", "label": "Agency Application", "x": 500, "y": 180},
                {"id": "ctrl-fw-1", "type": "ctrl-firewall", "label": "WAF + FW", "x": 100, "y": 180},
                {"id": "ctrl-mfa-1", "type": "ctrl-mfa", "label": "MFA Gateway", "x": 250, "y": 80},
                {"id": "isa-fed-1", "type": "isa-federation", "label": "SAML Federation", "x": 500, "y": 80},
                {"id": "sys-saas-1", "type": "sys-saas", "label": "Okta (IdP)", "x": 700, "y": 80},
                {"id": "isa-api-1", "type": "isa-api", "label": "Splunk API", "x": 500, "y": 280},
                {"id": "sys-saas-2", "type": "sys-saas", "label": "Splunk Cloud", "x": 700, "y": 280},
            ],
            "edges": [
                {"id": "e1", "source": "sys-int-1", "target": "isa-fed-1"},
                {"id": "e2", "source": "isa-fed-1", "target": "sys-saas-1"},
                {"id": "e3", "source": "sys-int-1", "target": "isa-api-1"},
                {"id": "e4", "source": "isa-api-1", "target": "sys-saas-2"},
                {"id": "e5", "source": "ctrl-fw-1", "target": "bnd-frp-1"},
                {"id": "e6", "source": "ctrl-mfa-1", "target": "isa-fed-1"},
            ],
        }),
        "template_id": "bdc-tpl-fedramp-cloud",
    },
    {
        "id": _DESIGN_IDS["bdc-design-003"],
        "name": "Healthcare System (HIPAA)",
        "description": "Healthcare ATO boundary with HIPAA PHI zone and PCI CDE, EHR system, billing, external lab (SFTP ISA) and insurance payer (API ISA). Includes DLP gateway for PHI data protection.",
        "graph_json": json.dumps({
            "nodes": [
                {"id": "bnd-ato-1", "type": "bnd-ato", "label": "Healthcare ATO Boundary", "x": 30, "y": 20, "width": 800, "height": 540},
                {"id": "bnd-hipaa-1", "type": "bnd-hipaa", "label": "HIPAA PHI Zone", "x": 80, "y": 80, "width": 300, "height": 200},
                {"id": "bnd-pci-1", "type": "bnd-pci", "label": "PCI CDE", "x": 450, "y": 80, "width": 300, "height": 200},
                {"id": "sys-ehr-1", "type": "sys-internal", "label": "EHR System", "x": 160, "y": 160},
                {"id": "sys-billing-1", "type": "sys-internal", "label": "Billing/Payment System", "x": 530, "y": 160},
                {"id": "ctrl-fw-1", "type": "ctrl-firewall", "label": "Boundary Firewall", "x": 80, "y": 320},
                {"id": "ctrl-dlp-1", "type": "ctrl-dlp", "label": "DLP Gateway", "x": 250, "y": 420},
                {"id": "isa-file-1", "type": "isa-file", "label": "Lab Results SFTP", "x": 160, "y": 500},
                {"id": "sys-ext-lab", "type": "sys-external", "label": "External Lab System", "x": 160, "y": 600},
                {"id": "isa-api-1", "type": "isa-api", "label": "Claims API", "x": 530, "y": 500},
                {"id": "sys-ext-payer", "type": "sys-external", "label": "Insurance Payer", "x": 530, "y": 600},
            ],
            "edges": [
                {"id": "e1", "source": "sys-ehr-1", "target": "isa-file-1"},
                {"id": "e2", "source": "isa-file-1", "target": "sys-ext-lab"},
                {"id": "e3", "source": "sys-billing-1", "target": "isa-api-1"},
                {"id": "e4", "source": "isa-api-1", "target": "sys-ext-payer"},
                {"id": "e5", "source": "ctrl-fw-1", "target": "bnd-ato-1"},
                {"id": "e6", "source": "ctrl-dlp-1", "target": "bnd-ato-1"},
            ],
        }),
        "template_id": "bdc-tpl-healthcare-hipaa",
    },
    {
        "id": _DESIGN_IDS["bdc-design-004"],
        "name": "Hybrid Multi-Cloud Boundary",
        "description": "Enterprise ATO boundary spanning AWS GovCloud and Azure Gov enclaves with on-prem data center. Includes Direct Connect VPN, ExpressRoute VPN, Azure AD federation, partner API ISA, and BCAP for cloud traffic.",
        "graph_json": json.dumps({
            "nodes": [
                {"id": "bnd-ato-1", "type": "bnd-ato", "label": "Enterprise ATO Boundary", "x": 20, "y": 20, "width": 900, "height": 560},
                {"id": "bnd-enc-aws", "type": "bnd-enclave", "label": "AWS GovCloud Enclave", "x": 60, "y": 80, "width": 320, "height": 200},
                {"id": "bnd-enc-azure", "type": "bnd-enclave", "label": "Azure Gov Enclave", "x": 450, "y": 80, "width": 320, "height": 200},
                {"id": "sys-aws-1", "type": "sys-cloud", "label": "AWS GovCloud Workloads", "x": 140, "y": 170},
                {"id": "sys-azure-1", "type": "sys-cloud", "label": "Azure Gov Workloads", "x": 530, "y": 170},
                {"id": "isa-vpn-1", "type": "isa-vpn", "label": "AWS Direct Connect VPN", "x": 140, "y": 320},
                {"id": "sys-onprem-1", "type": "sys-internal", "label": "On-Prem Data Center", "x": 360, "y": 320},
                {"id": "isa-vpn-2", "type": "isa-vpn", "label": "Azure ExpressRoute VPN", "x": 580, "y": 320},
                {"id": "ctrl-bcap-1", "type": "ctrl-bcap", "label": "BCAP", "x": 60, "y": 520},
                {"id": "ctrl-fw-1", "type": "ctrl-firewall", "label": "Enterprise Firewall", "x": 80, "y": 440},
                {"id": "isa-api-1", "type": "isa-api", "label": "Partner REST API", "x": 800, "y": 320},
                {"id": "sys-ext-1", "type": "sys-external", "label": "Partner Organization", "x": 1000, "y": 320},
            ],
            "edges": [
                {"id": "e1", "source": "sys-onprem-1", "target": "isa-vpn-1"},
                {"id": "e2", "source": "isa-vpn-1", "target": "sys-aws-1"},
                {"id": "e3", "source": "sys-onprem-1", "target": "isa-vpn-2"},
                {"id": "e4", "source": "isa-vpn-2", "target": "sys-azure-1"},
                {"id": "e5", "source": "sys-azure-1", "target": "isa-api-1"},
                {"id": "e6", "source": "isa-api-1", "target": "sys-ext-1"},
                {"id": "e7", "source": "ctrl-fw-1", "target": "bnd-ato-1"},
                {"id": "e8", "source": "ctrl-bcap-1", "target": "bnd-ato-1"},
            ],
        }),
        "template_id": "bdc-tpl-hybrid-multi-cloud",
    },
    {
        "id": _DESIGN_IDS["bdc-design-005"],
        "name": "SCCA Authorization Boundary",
        "description": "DoD SCCA authorization boundary with all 4 SCCA functional areas: BCAP (Boundary Cloud Access Point), VDSS (Virtual Data Center Security Stack), VDMS (Virtual Data Center Managed Services), and TCCM (Tenant Cloud Credential Manager).",
        "graph_json": json.dumps({
            "nodes": [
                {"id": "bnd-ato-1", "type": "bnd-ato", "label": "SCCA ATO Boundary", "x": 20, "y": 20, "width": 1000, "height": 600},
                {"id": "bnd-bcap", "type": "bnd-enclave", "label": "BCAP Zone", "x": 40, "y": 60, "width": 300, "height": 180},
                {"id": "bnd-vdss", "type": "bnd-enclave", "label": "VDSS Zone", "x": 370, "y": 60, "width": 300, "height": 180},
                {"id": "bnd-vdms", "type": "bnd-enclave", "label": "VDMS Zone", "x": 40, "y": 280, "width": 300, "height": 180},
                {"id": "bnd-tccm", "type": "bnd-enclave", "label": "TCCM Zone", "x": 370, "y": 280, "width": 300, "height": 180},
                {"id": "sys-bcap-proxy", "type": "sys-internal", "label": "BCAP Proxy", "x": 70, "y": 120},
                {"id": "sys-bcap-nfw", "type": "sys-internal", "label": "Network Firewall", "x": 220, "y": 120},
                {"id": "sys-vdss-stack", "type": "sys-internal", "label": "Security Stack", "x": 450, "y": 120},
                {"id": "sys-vdms-svc", "type": "sys-internal", "label": "Managed Services", "x": 120, "y": 340},
                {"id": "sys-tccm-cred", "type": "sys-internal", "label": "Credential Manager", "x": 450, "y": 340},
                {"id": "ctrl-fw-1", "type": "ctrl-firewall", "label": "Boundary Firewall", "x": 720, "y": 80},
                {"id": "ctrl-ids-1", "type": "ctrl-ids-ips", "label": "IDS/IPS", "x": 720, "y": 200},
                {"id": "ctrl-siem-1", "type": "ctrl-siem", "label": "SIEM", "x": 720, "y": 320},
                {"id": "isa-disn", "type": "isa-dedicated", "label": "DISN/DREN Circuit", "x": 720, "y": 480},
                {"id": "isa-vpn-1", "type": "isa-vpn", "label": "VPN to On-Prem", "x": 900, "y": 480},
            ],
            "edges": [
                {"id": "e1", "source": "sys-bcap-proxy", "target": "bnd-ato-1"},
                {"id": "e2", "source": "sys-vdss-stack", "target": "bnd-ato-1"},
                {"id": "e3", "source": "sys-vdms-svc", "target": "bnd-ato-1"},
                {"id": "e4", "source": "sys-tccm-cred", "target": "bnd-ato-1"},
                {"id": "e5", "source": "ctrl-fw-1", "target": "bnd-ato-1"},
                {"id": "e6", "source": "ctrl-ids-1", "target": "bnd-ato-1"},
                {"id": "e7", "source": "ctrl-siem-1", "target": "bnd-ato-1"},
                {"id": "e8", "source": "isa-vpn-1", "target": "bnd-ato-1"},
            ],
        }),
        "template_id": "bdc-tpl-scca-auth-boundary",
    },
]

_ASSESSMENTS = []
for i, design in enumerate(_BOUNDARY_DESIGNS):
    cat1 = random.randint(0, 3)
    cat2 = random.randint(0, 5)
    cat3 = random.randint(0, 8)
    score = max(0, 100 - cat1 * 25 - cat2 * 10 - cat3 * 5)
    grade = "A" if score >= 90 else "B" if score >= 80 else "C" if score >= 70 else "D" if score >= 60 else "F"
    _ASSESSMENTS.append({
        "id": _uid(),
        "design_id": design["id"],
        "assessment_type": random.choice(["full", "compliance", "security", "boundary"]),
        "findings_json": json.dumps([
            {"cat": "CAT1", "count": cat1, "detail": "Critical boundary control gap"},
            {"cat": "CAT2", "count": cat2, "detail": "Moderate ISA documentation issue"},
            {"cat": "CAT3", "count": cat3, "detail": "Minor PPS matrix inconsistency"},
        ]),
        "score": score,
        "grade": grade,
        "cat1_findings": cat1,
        "cat2_findings": cat2,
        "cat3_findings": cat3,
        "nist_coverage_json": json.dumps({
            "AC-3": random.choice([True, True, False]),
            "AC-17": random.choice([True, False]),
            "CA-3": random.choice([True, True, True, False]),
            "SC-7": random.choice([True, True, False]),
            "SC-13": random.choice([True, False]),
        }),
    })

_ISA_TRACKER = []
_isa_statuses = ["draft", "signed", "expired", "renewal_needed", "terminated"]
for i in range(8):
    design_id = _BOUNDARY_DESIGNS[i % len(_BOUNDARY_DESIGNS)]["id"]
    status = _isa_statuses[i % len(_isa_statuses)]
    days = random.randint(30, 730)
    _ISA_TRACKER.append({
        "id": _uid(),
        "design_id": design_id,
        "interconnection_id": f"ISA-2026-{i+1:03d}",
        "isa_doc_id": f"DOC-ISA-{i+1:03d}",
        "status": status,
        "expiry_date": (_T0 + timedelta(days=days)).isoformat(),
        "review_date": (_T0 + timedelta(days=days - 30)).isoformat(),
        "owner": random.choice(["ISSO-Smith", "ISSO-Jones", "ISSO-Lee", "ISSO-Brown"]),
        "notes": f"ISA for interconnection {i+1} in design {design_id[:8]}",
    })

_ALERTS = []
for i in range(6):
    design_id = _BOUNDARY_DESIGNS[i % len(_BOUNDARY_DESIGNS)]["id"]
    isa_id = _ISA_TRACKER[i % len(_ISA_TRACKER)]["id"] if i < len(_ISA_TRACKER) else None
    severity = random.choice(["low", "medium", "high", "critical"])
    days_left = random.randint(-30, 90)
    _ALERTS.append({
        "id": _uid(),
        "design_id": design_id,
        "isa_id": isa_id,
        "alert_type": random.choice(["isa_expiry", "control_gap", "compliance_drift", "unauthorized_interconnection"]),
        "severity": severity,
        "days_until_expiry": days_left if days_left > 0 else None,
        "message": f"Boundary alert: {severity} severity issue detected in design {design_id[:8]}",
        "acknowledged": 1 if random.random() > 0.5 else 0,
        "acknowledged_by": "ISSO-Smith" if random.random() > 0.5 else "",
        "acknowledged_at": _ts(i * 4 + 2) if random.random() > 0.5 else None,
    })

_AUTH_COMPONENTS = [
    ("ac-001", "airgap_bundle", "ICDEV Core Bundle v1.2.3", "1.2.3", "/bundles/icdev-core-1.2.3.tar.gz", "sha256:aabb...", "/sboms/icdev-core-1.2.3-sbom.json", '["IL4", "IL5"]', 120, 1, "authorized", "FedRAMP-authorized ICDEV core runtime bundle"),
    ("ac-002", "container_image", "icdev/sdc-agent:latest", "latest", "", "sha256:ccdd...", "/sboms/sdc-agent-sbom.json", '["IL4"]', 15, 1, "authorized", "Security Design Canvas agent container"),
    ("ac-003", "container_image", "icdev/bdc-agent:latest", "latest", "", "sha256:eeff...", "/sboms/bdc-agent-sbom.json", '["IL4", "IL5"]', 12, 1, "authorized", "Boundary Design Canvas agent container"),
    ("ac-004", "helm_chart", "icdev-platform", "2.1.0", "/charts/icdev-platform-2.1.0.tgz", "sha256:1122...", "/sboms/icdev-platform-sbom.json", '["IL4"]', 45, 1, "authorized", "ICDEV platform Helm chart for Kubernetes deployment"),
    ("ac-005", "airgap_bundle", "ICDEV Security Bundle v1.1.0", "1.1.0", "/bundles/icdev-security-1.1.0.tar.gz", "sha256:3344...", "/sboms/icdev-security-1.1.0-sbom.json", '["IL4", "IL5", "IL6"]', 85, 1, "pending", "Pending AO approval for IL6 deployment"),
]


def seed_boundary_designs(conn) -> int:
    sql = """INSERT OR IGNORE INTO boundary_designs (
        id, name, description, graph_json, template_id, classification, created_at, updated_at
    ) VALUES (?,?,?,?,?,?,?,?)"""
    count = 0
    for row in _BOUNDARY_DESIGNS:
        _safe_execute(conn, sql, (
            row["id"], row["name"], row["description"], row["graph_json"],
            row["template_id"], "CUI", _ts(count * 3), _ts(count * 3),
        ))
        count += 1
    return count


def seed_assessments(conn) -> int:
    sql = """INSERT OR IGNORE INTO bd_assessments (
        id, design_id, assessment_type, findings_json, score, grade,
        cat1_findings, cat2_findings, cat3_findings, nist_coverage_json, created_at
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?)"""
    count = 0
    for row in _ASSESSMENTS:
        _safe_execute(conn, sql, (
            row["id"], row["design_id"], row["assessment_type"], row["findings_json"],
            row["score"], row["grade"], row["cat1_findings"], row["cat2_findings"],
            row["cat3_findings"], row["nist_coverage_json"], _ts(count * 4),
        ))
        count += 1
    return count


def seed_isa_tracker(conn) -> int:
    sql = """INSERT OR IGNORE INTO bd_isa_tracker (
        id, design_id, interconnection_id, isa_doc_id, status, expiry_date,
        review_date, owner, notes, created_at, updated_at
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?)"""
    count = 0
    for row in _ISA_TRACKER:
        _safe_execute(conn, sql, (
            row["id"], row["design_id"], row["interconnection_id"], row["isa_doc_id"],
            row["status"], row["expiry_date"], row["review_date"], row["owner"],
            row["notes"], _ts(count * 2), _ts(count * 2),
        ))
        count += 1
    return count


def seed_audit(conn) -> int:
    sql = """INSERT OR IGNORE INTO bd_audit (
        design_id, "user", action, detail, classification, created_at
    ) VALUES (?,?,?,?,?,?)"""
    count = 0
    actions = [
        ("design_created", "Boundary design created from template"),
        ("assessment_run", "Compliance assessment executed"),
        ("isa_updated", "ISA tracker updated with new expiry"),
        ("control_modified", "Boundary control configuration modified"),
        ("design_exported", "Design exported to OSCAL format"),
    ]
    for i in range(12):
        design_id = _BOUNDARY_DESIGNS[i % len(_BOUNDARY_DESIGNS)]["id"]
        action, detail = actions[i % len(actions)]
        _safe_execute(conn, sql, (
            design_id, random.choice(["ISSO-Smith", "ISSO-Jones", "system"]),
            action, detail, "CUI // SP-CTI", _ts(i * 2),
        ))
        count += 1
    return count


def seed_versions(conn) -> int:
    sql = """INSERT OR IGNORE INTO bd_versions (
        id, design_id, version_number, graph_json, change_summary, user_id, created_at
    ) VALUES (?,?,?,?,?,?,?)"""
    count = 0
    for i in range(10):
        design_id = _BOUNDARY_DESIGNS[i % len(_BOUNDARY_DESIGNS)]["id"]
        _safe_execute(conn, sql, (
            _uid(), design_id, i + 1, _BOUNDARY_DESIGNS[i % len(_BOUNDARY_DESIGNS)]["graph_json"],
            f"Version {i+1}: updated boundary controls", "system", _ts(i * 5),
        ))
        count += 1
    return count


def seed_collab_sessions(conn) -> int:
    sql = """INSERT OR IGNORE INTO bd_collab_sessions (
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
        design_id = _BOUNDARY_DESIGNS[i % len(_BOUNDARY_DESIGNS)]["id"]
        user_id, user_name, color = users[i]
        _safe_execute(conn, sql, (
            _uid(), design_id, user_id, user_name, color,
            _ts(i * 6), _ts(i * 6 + 2), 1 if random.random() > 0.3 else 0,
        ))
        count += 1
    return count


def seed_alerts(conn) -> int:
    sql = """INSERT OR IGNORE INTO bd_alerts (
        id, design_id, isa_id, alert_type, severity, days_until_expiry,
        message, acknowledged, acknowledged_by, acknowledged_at, created_at
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?)"""
    count = 0
    for row in _ALERTS:
        _safe_execute(conn, sql, (
            row["id"], row["design_id"], row["isa_id"], row["alert_type"],
            row["severity"], row["days_until_expiry"], row["message"],
            row["acknowledged"], row["acknowledged_by"], row["acknowledged_at"],
            _ts(count * 3),
        ))
        count += 1
    return count


def seed_authorized_components(conn) -> int:
    sql = """INSERT OR IGNORE INTO bd_authorized_components (
        id, component_type, name, version, bundle_path, sha256_manifest,
        sbom_path, impact_levels, file_count, sbom_count, status, notes,
        classification, registered_by, created_at, updated_at
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
    count = 0
    for row in _AUTH_COMPONENTS:
        _safe_execute(conn, sql, (
            row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7],
            row[8], row[9], row[10], row[11], "CUI // SP-CTI", "icdev-airgap-engine",
            _ts(count * 2), _ts(count * 2),
        ))
        count += 1
    return count


def verify(conn) -> dict:
    result = {}
    for tbl in (
        "boundary_designs", "bd_assessments", "bd_isa_tracker", "bd_audit",
        "bd_versions", "bd_collab_sessions", "bd_alerts", "bd_authorized_components",
    ):
        try:
            row = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()
            result[tbl] = row[0]
        except Exception as exc:
            result[tbl] = f"error: {exc}"
    return result


def main():
    parser = argparse.ArgumentParser(description="BDC Demo Seed")
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
            "boundary_designs": seed_boundary_designs(conn),
            "bd_assessments": seed_assessments(conn),
            "bd_isa_tracker": seed_isa_tracker(conn),
            "bd_audit": seed_audit(conn),
            "bd_versions": seed_versions(conn),
            "bd_collab_sessions": seed_collab_sessions(conn),
            "bd_alerts": seed_alerts(conn),
            "bd_authorized_components": seed_authorized_components(conn),
        }
        conn.commit()

        if args.json:
            print(json.dumps({"success": True, "seeded": counts, "verify": verify(conn)}, indent=2))
        else:
            print(f"[seed_bdc] Seeded {counts}")
            print(f"[seed_bdc] Verify: {verify(conn)}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
