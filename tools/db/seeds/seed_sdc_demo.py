#!/usr/bin/env python3
# CUI // SP-CTI
"""SDC Demo Seed -- populates security_canvas.db with realistic before/after demo data.

Before-state (seed_before_state): 8 designs, 47 STRIDE threats (15 CAT1, 22 CAT2, 10 CAT3),
  3 attack snapshots (unencrypted edges), 5 SOPs in pending_review.

After-state (seed_after_state): all threats mitigated, controls implemented,
  attack edges encrypted+authenticated, SOPs approved, compliance timeline rows.

Also seeds: ISSO workflow simulation records, sdc_compliance_timeline, sdc_roi_metrics.

Usage:
    python tools/db/seeds/seed_sdc_demo.py --all [--reset] [--json]
    python tools/db/seeds/seed_sdc_demo.py --verify --json
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
_T0 = _NOW - timedelta(hours=48)
_PRIMARY_DESIGN_ID = "demo-design-001"


def _ts(offset_hours: float = 0.0) -> str:
    return (_T0 + timedelta(hours=offset_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _uid() -> str:
    return str(uuid.uuid4())


def _get_conn():
    try:
        from tools.security_canvas.db.init_db import get_connection
        return get_connection()
    except Exception:
        db = _ROOT / "data" / "security_canvas.db"
        db.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        return conn


def _ensure_demo_tables(conn) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS sdc_compliance_timeline ("
        "id TEXT PRIMARY KEY, design_id TEXT NOT NULL, "
        "snapshot_label TEXT NOT NULL DEFAULT 'baseline', "
        "cat1_count INTEGER NOT NULL DEFAULT 0, cat2_count INTEGER NOT NULL DEFAULT 0, "
        "cat3_count INTEGER NOT NULL DEFAULT 0, risk_score REAL NOT NULL DEFAULT 0.0, "
        "posture_grade TEXT NOT NULL DEFAULT 'F', controls_implemented INTEGER NOT NULL DEFAULT 0, "
        "controls_total INTEGER NOT NULL DEFAULT 0, remediation_hours REAL NOT NULL DEFAULT 0.0, "
        "snapshot_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "classification TEXT NOT NULL DEFAULT 'CUI')"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS sdc_roi_metrics ("
        "id TEXT PRIMARY KEY, design_id TEXT NOT NULL, "
        "manual_hours REAL NOT NULL DEFAULT 200.0, automated_hours REAL NOT NULL DEFAULT 4.0, "
        "cost_per_hour REAL NOT NULL DEFAULT 150.0, roi_multiplier REAL NOT NULL DEFAULT 0.0, "
        "engagement_type TEXT NOT NULL DEFAULT 'standard', "
        "created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "classification TEXT NOT NULL DEFAULT 'CUI')"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS sdc_workflow_step_runs ("
        "id TEXT PRIMARY KEY, design_id TEXT NOT NULL, "
        "step_id TEXT NOT NULL, step_name TEXT NOT NULL, "
        "status TEXT NOT NULL DEFAULT 'pending', "
        "approved_by TEXT, approved_at TEXT, started_at TEXT, completed_at TEXT, "
        "output_json TEXT DEFAULT '{}', "
        "created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
    )
    conn.commit()


_DESIGNS = [
    {"id": "demo-design-001", "name": "BCAP IL5 Web Application Platform",
     "description": "3-tier web app on DOD IL5 boundary with legacy auth stack. STIG findings pending.",
     "classification": "CUI", "template_id": "sct-dod-il5"},
    {"id": "demo-design-002", "name": "NIPR/SIPR Cross-Domain Service",
     "description": "Cross-domain solution for NIPR to SIPR data transfer with PKI.",
     "classification": "CUI", "template_id": "sct-zero-trust"},
    {"id": "demo-design-003", "name": "FedRAMP SaaS Boundary",
     "description": "FedRAMP Moderate SaaS platform seeking cATO. 22 open CAT2 findings.",
     "classification": "CUI", "template_id": "sct-fedramp-mod"},
    {"id": "demo-design-004", "name": "Kubernetes Microservices Cluster",
     "description": "Multi-tenant K8s cluster with service mesh. mTLS not enforced on all links.",
     "classification": "CUI", "template_id": "sct-k8s-cluster"},
    {"id": "demo-design-005", "name": "Zero Trust Network Architecture",
     "description": "NIST SP 800-207 ZTA design with PEP/PDP components. Policy engine gaps.",
     "classification": "CUI", "template_id": "sct-zt-micro"},
    {"id": "demo-design-006", "name": "DevSecOps CI/CD Pipeline",
     "description": "GitLab CI/CD pipeline with SAST/DAST/SCA gates. Container registry unsigned.",
     "classification": "CUI", "template_id": "sct-devsecops"},
    {"id": "demo-design-007", "name": "Multi-Cloud VPC Interconnect",
     "description": "AWS GovCloud + Azure Gov dual-cloud with VPN mesh. BGP not filtered.",
     "classification": "CUI", "template_id": "sct-multi-cloud"},
    {"id": "demo-design-008", "name": "Data Enclave -- PII/PHI Processing",
     "description": "Sensitive data enclave. Encryption at rest missing on 3 data stores.",
     "classification": "CUI", "template_id": "sct-data-enclave"},
]

_THREATS_BEFORE = [
    # CAT1 (15)
    {"cat": "CAT1", "category": "Spoofing", "technique": "T1078", "tactic": "Initial Access",
     "title": "Unauthenticated Admin Console Access",
     "desc": "Management console accessible without MFA.", "likelihood": "high", "impact": "high", "risk_score": 9.2},
    {"cat": "CAT1", "category": "Tampering", "technique": "T1552", "tactic": "Credential Access",
     "title": "Plaintext Credentials in Config Files",
     "desc": "Database passwords stored without encryption.", "likelihood": "high", "impact": "high", "risk_score": 9.0},
    {"cat": "CAT1", "category": "Information Disclosure", "technique": "T1040", "tactic": "Credential Access",
     "title": "Unencrypted Data-in-Transit (FIPS 140-2 Violation)",
     "desc": "Service calls use HTTP; PII traverses boundary without TLS.",
     "likelihood": "high", "impact": "high", "risk_score": 8.9},
    {"cat": "CAT1", "category": "Elevation of Privilege", "technique": "T1068", "tactic": "Privilege Escalation",
     "title": "Container Running as Root (CAT I STIG V-242415)",
     "desc": "All containers execute as UID 0.", "likelihood": "medium", "impact": "high", "risk_score": 8.7},
    {"cat": "CAT1", "category": "Spoofing", "technique": "T1190", "tactic": "Initial Access",
     "title": "Public-Facing Web App Vulnerable to SQLi (CVE-2024-0519)",
     "desc": "Blind SQLi confirmed via automated scanner.",
     "likelihood": "high", "impact": "high", "risk_score": 9.5},
    {"cat": "CAT1", "category": "Information Disclosure", "technique": "T1071", "tactic": "Exfiltration",
     "title": "S3 Bucket Publicly Readable -- PII Exposure",
     "desc": "demo-uploads bucket allows s3:GetObject without authentication.",
     "likelihood": "high", "impact": "high", "risk_score": 9.8},
    {"cat": "CAT1", "category": "Denial of Service", "technique": "T1499", "tactic": "Impact",
     "title": "No Rate Limiting on Authentication Endpoint",
     "desc": "Login accepts unlimited requests; credential stuffing possible.",
     "likelihood": "high", "impact": "medium", "risk_score": 8.1},
    {"cat": "CAT1", "category": "Elevation of Privilege", "technique": "T1548", "tactic": "Privilege Escalation",
     "title": "SUID Binaries Not Audited (STIG V-238200)",
     "desc": "22 SUID binaries outside approved baseline.", "likelihood": "medium", "impact": "high", "risk_score": 8.5},
    {"cat": "CAT1", "category": "Tampering", "technique": "T1565", "tactic": "Impact",
     "title": "Audit Log Integrity Not Enforced",
     "desc": "Audit logs writable by application user.", "likelihood": "medium", "impact": "high", "risk_score": 8.3},
    {"cat": "CAT1", "category": "Information Disclosure", "technique": "T1213", "tactic": "Collection",
     "title": "API Key in Public Git Repository",
     "desc": "AWS access key committed to GitHub 8 days ago.", "likelihood": "high", "impact": "high", "risk_score": 9.6},
    {"cat": "CAT1", "category": "Spoofing", "technique": "T1539", "tactic": "Credential Access",
     "title": "Session Tokens Not Invalidated on Logout",
     "desc": "JWT tokens remain valid 24 hours after logout.", "likelihood": "medium", "impact": "high", "risk_score": 8.2},
    {"cat": "CAT1", "category": "Tampering", "technique": "T1195", "tactic": "Initial Access",
     "title": "Unsigned Container Images from Public Registry",
     "desc": "Docker images pulled without signature verification.",
     "likelihood": "medium", "impact": "high", "risk_score": 8.6},
    {"cat": "CAT1", "category": "Elevation of Privilege", "technique": "T1611", "tactic": "Privilege Escalation",
     "title": "Kubernetes API Server Accessible from Pod Network",
     "desc": "K8s API server listens on 0.0.0.0:6443.",
     "likelihood": "medium", "impact": "high", "risk_score": 8.8},
    {"cat": "CAT1", "category": "Information Disclosure", "technique": "T1530", "tactic": "Collection",
     "title": "CloudTrail Logging Disabled in us-gov-west-1",
     "desc": "CloudTrail not enabled in secondary region.",
     "likelihood": "high", "impact": "high", "risk_score": 8.4},
    {"cat": "CAT1", "category": "Repudiation", "technique": "T1562", "tactic": "Defense Evasion",
     "title": "Host-Based IDS Disabled on 6 Instances",
     "desc": "HIDS uninstalled and never re-enabled.", "likelihood": "high", "impact": "high", "risk_score": 8.0},
    # CAT2 (22)
    {"cat": "CAT2", "category": "Information Disclosure", "technique": "T1046", "tactic": "Discovery",
     "title": "Verbose Error Messages Expose Stack Traces",
     "desc": "Production API returns full Python tracebacks.", "likelihood": "medium", "impact": "medium", "risk_score": 6.1},
    {"cat": "CAT2", "category": "Spoofing", "technique": "T1557", "tactic": "Credential Access",
     "title": "DNS Not DNSSEC-Signed", "desc": "DNS cache poisoning and MitM feasible.",
     "likelihood": "low", "impact": "high", "risk_score": 6.5},
    {"cat": "CAT2", "category": "Tampering", "technique": "T1070", "tactic": "Defense Evasion",
     "title": "System Clock Not Synced to Authoritative NTP (STIG V-238208)",
     "desc": "3 of 12 hosts use public NTP without auth.", "likelihood": "low", "impact": "medium", "risk_score": 5.2},
    {"cat": "CAT2", "category": "Information Disclosure", "technique": "T1595", "tactic": "Reconnaissance",
     "title": "Banner Grabbing Returns Version Strings",
     "desc": "SSH/HTTP banners reveal EOL versions.", "likelihood": "medium", "impact": "medium", "risk_score": 5.8},
    {"cat": "CAT2", "category": "Denial of Service", "technique": "T1498", "tactic": "Impact",
     "title": "No WAF in Front of Public APIs",
     "desc": "OWASP Top 10 vectors unblocked.", "likelihood": "medium", "impact": "medium", "risk_score": 6.3},
    {"cat": "CAT2", "category": "Elevation of Privilege", "technique": "T1098", "tactic": "Persistence",
     "title": "Stale IAM Roles with AdministratorAccess",
     "desc": "7 IAM roles unused 90+ days.", "likelihood": "low", "impact": "high", "risk_score": 6.7},
    {"cat": "CAT2", "category": "Tampering", "technique": "T1505", "tactic": "Persistence",
     "title": "Web Shell Indicators in /var/www/upload/",
     "desc": "3 .php files via unrestricted upload.", "likelihood": "medium", "impact": "medium", "risk_score": 6.9},
    {"cat": "CAT2", "category": "Spoofing", "technique": "T1110", "tactic": "Credential Access",
     "title": "Weak Password Policy (< 12 chars, no complexity)",
     "desc": "AD allows 8-char passwords without special characters.", "likelihood": "medium", "impact": "medium", "risk_score": 5.9},
    {"cat": "CAT2", "category": "Information Disclosure", "technique": "T1020", "tactic": "Exfiltration",
     "title": "Egress Traffic Not Filtered",
     "desc": "No DLP or CASB on egress path.", "likelihood": "low", "impact": "high", "risk_score": 6.4},
    {"cat": "CAT2", "category": "Tampering", "technique": "T1059", "tactic": "Execution",
     "title": "PowerShell Execution Policy Not Restricted",
     "desc": "AllSigned not enforced.", "likelihood": "medium", "impact": "medium", "risk_score": 5.7},
    {"cat": "CAT2", "category": "Elevation of Privilege", "technique": "T1078", "tactic": "Defense Evasion",
     "title": "Service Accounts with Interactive Login Rights",
     "desc": "4 service accounts have log on locally rights.", "likelihood": "low", "impact": "high", "risk_score": 6.2},
    {"cat": "CAT2", "category": "Information Disclosure", "technique": "T1119", "tactic": "Collection",
     "title": "Unencrypted Backups on S3 Standard Storage",
     "desc": "DB backups without SSE-KMS.", "likelihood": "low", "impact": "high", "risk_score": 6.6},
    {"cat": "CAT2", "category": "Denial of Service", "technique": "T1496", "tactic": "Impact",
     "title": "Resource Quotas Not Set on Kubernetes Namespaces",
     "desc": "No LimitRange or ResourceQuota.", "likelihood": "medium", "impact": "medium", "risk_score": 5.5},
    {"cat": "CAT2", "category": "Spoofing", "technique": "T1566", "tactic": "Initial Access",
     "title": "Phishing-Resistant MFA Not Required for Privileged Accounts",
     "desc": "16 admin accounts use TOTP, not FIDO2.", "likelihood": "medium", "impact": "high", "risk_score": 7.0},
    {"cat": "CAT2", "category": "Tampering", "technique": "T1601", "tactic": "Defense Evasion",
     "title": "Network Device Firmware Not Validated",
     "desc": "4 devices running unsigned firmware.", "likelihood": "low", "impact": "high", "risk_score": 6.8},
    {"cat": "CAT2", "category": "Information Disclosure", "technique": "T1083", "tactic": "Discovery",
     "title": "Directory Listing Enabled on Web Server",
     "desc": "nginx autoindex on for /static/.", "likelihood": "medium", "impact": "low", "risk_score": 4.8},
    {"cat": "CAT2", "category": "Elevation of Privilege", "technique": "T1574", "tactic": "Privilege Escalation",
     "title": "DLL Search Order Hijacking Vector",
     "desc": "App installer loads DLLs from PATH before system32.", "likelihood": "low", "impact": "medium", "risk_score": 5.3},
    {"cat": "CAT2", "category": "Tampering", "technique": "T1036", "tactic": "Defense Evasion",
     "title": "Process Name Masquerading Not Detected",
     "desc": "EDR not configured for process name mismatches.", "likelihood": "low", "impact": "medium", "risk_score": 4.9},
    {"cat": "CAT2", "category": "Repudiation", "technique": "T1562", "tactic": "Defense Evasion",
     "title": "Security Events Not Forwarded to SIEM",
     "desc": "3 subnets not sending Windows Event Log to Splunk.", "likelihood": "medium", "impact": "medium", "risk_score": 6.0},
    {"cat": "CAT2", "category": "Spoofing", "technique": "T1134", "tactic": "Privilege Escalation",
     "title": "Token Impersonation via SeImpersonatePrivilege",
     "desc": "IIS app pool has SeImpersonatePrivilege.", "likelihood": "low", "impact": "high", "risk_score": 6.4},
    {"cat": "CAT2", "category": "Information Disclosure", "technique": "T1552", "tactic": "Credential Access",
     "title": "Environment Variables Logged to CloudWatch Plaintext",
     "desc": "Lambda functions log os.environ at startup.", "likelihood": "medium", "impact": "medium", "risk_score": 5.6},
    {"cat": "CAT2", "category": "Denial of Service", "technique": "T1485", "tactic": "Impact",
     "title": "Disaster Recovery RTO Not Tested in 12 Months",
     "desc": "Last DR test was 14 months ago.", "likelihood": "low", "impact": "high", "risk_score": 6.1},
    # CAT3 (10)
    {"cat": "CAT3", "category": "Information Disclosure", "technique": "T1592", "tactic": "Reconnaissance",
     "title": "HTTPS Strict-Transport-Security Header Missing",
     "desc": "HSTS not set on 2 subdomains.", "likelihood": "low", "impact": "low", "risk_score": 3.2},
    {"cat": "CAT3", "category": "Tampering", "technique": "T1036", "tactic": "Defense Evasion",
     "title": "X-Frame-Options Not Set",
     "desc": "UI redressing possible.", "likelihood": "low", "impact": "low", "risk_score": 2.8},
    {"cat": "CAT3", "category": "Information Disclosure", "technique": "T1016", "tactic": "Discovery",
     "title": "SNMP Community String = 'public'",
     "desc": "3 legacy switches with default SNMP community.", "likelihood": "low", "impact": "low", "risk_score": 3.5},
    {"cat": "CAT3", "category": "Spoofing", "technique": "T1557", "tactic": "Credential Access",
     "title": "ARP Inspection Not Enabled",
     "desc": "ARP spoofing possible within VLAN 10.", "likelihood": "low", "impact": "low", "risk_score": 3.0},
    {"cat": "CAT3", "category": "Information Disclosure", "technique": "T1014", "tactic": "Defense Evasion",
     "title": "Debug Endpoint /debug/vars Reachable Internally",
     "desc": "Go pprof debug endpoint on port 6060.", "likelihood": "low", "impact": "low", "risk_score": 2.5},
    {"cat": "CAT3", "category": "Tampering", "technique": "T1491", "tactic": "Impact",
     "title": "Web Server Version in Nginx Server Header",
     "desc": "Server: nginx/1.18.0 in all HTTP responses.", "likelihood": "low", "impact": "low", "risk_score": 2.2},
    {"cat": "CAT3", "category": "Denial of Service", "technique": "T1499", "tactic": "Impact",
     "title": "Connection Pool Not Bounded on Database",
     "desc": "Max connections not configured.", "likelihood": "low", "impact": "low", "risk_score": 3.1},
    {"cat": "CAT3", "category": "Information Disclosure", "technique": "T1217", "tactic": "Discovery",
     "title": "Backup Files Accessible at /backup.zip",
     "desc": "Old deployment artifact at web root.", "likelihood": "low", "impact": "low", "risk_score": 2.9},
    {"cat": "CAT3", "category": "Spoofing", "technique": "T1557", "tactic": "Lateral Movement",
     "title": "IPv6 SLAAC Not Disabled on IPv4-Only Subnets",
     "desc": "Router Advertisements not filtered.", "likelihood": "low", "impact": "low", "risk_score": 2.7},
    {"cat": "CAT3", "category": "Tampering", "technique": "T1070", "tactic": "Defense Evasion",
     "title": "Security Baseline Not Documented (Deviation from SCAP)",
     "desc": "Approved deviations not captured in POA&M.", "likelihood": "low", "impact": "low", "risk_score": 2.3},
]

_CONTROLS_AFTER = [
    {"family": "AC", "control_id": "AC-2", "title": "Account Management",
     "desc": "Automated IAM lifecycle with MFA.",
     "status": "implemented", "notes": "IaC generated by SDC."},
    {"family": "SC", "control_id": "SC-8", "title": "Transmission Confidentiality and Integrity",
     "desc": "All service calls enforced via mTLS TLS 1.3.",
     "status": "implemented", "notes": "Cert-manager deployed in K8s."},
    {"family": "AU", "control_id": "AU-9", "title": "Protection of Audit Information",
     "desc": "Audit logs streamed to immutable S3 with Object Lock + KMS.",
     "status": "implemented", "notes": "CloudTrail enabled all regions."},
    {"family": "IA", "control_id": "IA-5", "title": "Authenticator Management",
     "desc": "Secrets in AWS Secrets Manager with 30-day rotation.",
     "status": "implemented", "notes": "Vault agent injected to all pods."},
    {"family": "CM", "control_id": "CM-7", "title": "Least Functionality",
     "desc": "Container images rebuilt rootless (UID 65534).",
     "status": "implemented", "notes": "OPA Gatekeeper policy enforced."},
    {"family": "SI", "control_id": "SI-10", "title": "Information Input Validation",
     "desc": "Parameterized queries via ORM. WAF with OWASP CRS 3.3.",
     "status": "implemented", "notes": "AWS WAF + rate limiting 1000 req/min."},
    {"family": "SC", "control_id": "SC-28", "title": "Protection of Information at Rest",
     "desc": "All S3 buckets encrypted with SSE-KMS. Public access blocked.",
     "status": "implemented", "notes": "0 public buckets remaining."},
    {"family": "IR", "control_id": "IR-4", "title": "Incident Handling",
     "desc": "HIDS re-enabled. Security Hub + GuardDuty auto-routed to SIEM.",
     "status": "implemented", "notes": "Mean detection time target: <15 min."},
]

_SOPS = [
    {"title": "Incident Response SOP -- Ransomware", "sop_type": "incident_response",
     "desc": "Step-by-step ransomware containment procedure.",
     "nist": ["IR-4", "IR-8", "CP-10"], "status": "pending_review"},
    {"title": "Patch Management SOP -- OS and Middleware", "sop_type": "vulnerability_management",
     "desc": "Monthly OS patch cycle procedure.",
     "nist": ["SI-2", "CM-6", "CM-8"], "status": "pending_review"},
    {"title": "Access Provisioning SOP -- Privileged Accounts", "sop_type": "access_control",
     "desc": "ISSO-approved privileged account workflow.",
     "nist": ["AC-2", "AC-6", "IA-5"], "status": "pending_review"},
    {"title": "Backup and Recovery SOP -- DR Runbook", "sop_type": "continuity",
     "desc": "Recovery procedures for primary database failure.",
     "nist": ["CP-9", "CP-10", "SI-12"], "status": "pending_review"},
    {"title": "Vulnerability Scanning SOP -- SCAP/Nessus Cadence", "sop_type": "vulnerability_management",
     "desc": "Weekly SCAP scans, monthly Nessus, quarterly pen test.",
     "nist": ["RA-5", "SI-2", "CA-7"], "status": "pending_review"},
]

_WORKFLOW_STEPS = [
    ("step-01", "Threat Scan",           "completed",  0.0,   0.5),
    ("step-02", "STIG Check",            "completed",  0.5,   1.8),
    ("step-03", "Risk Scoring",          "completed",  1.8,   2.2),
    ("step-04", "ISSO Approval Gate",    "completed",  2.2,  10.0),
    ("step-05", "IaC Generation",        "completed", 10.0,  10.8),
    ("step-06", "Security Policy Gen",   "completed", 10.8,  11.2),
    ("step-07", "Terraform Plan",        "completed", 11.2,  11.9),
    ("step-08", "Terraform Apply",       "completed", 11.9,  13.5),
    ("step-09", "Ansible Remediation",   "completed", 13.5,  15.2),
    ("step-10", "Post-Deploy Scan",      "completed", 15.2,  16.0),
    ("step-11", "Compliance Crosswalk",  "completed", 16.0,  16.4),
    ("step-12", "Evidence Package",      "completed", 16.4,  16.8),
]


def seed_before_state(conn, reset: bool = False) -> dict:
    if reset:
        for tbl in ("sc_threats", "sc_controls", "sc_assets", "sc_data_flows",
                    "sc_trust_boundaries", "sdc_attack_snapshots", "sdc_sops",
                    "sdc_workflow_step_runs", "sdc_compliance_timeline", "sdc_roi_metrics"):
            try:
                conn.execute(f"DELETE FROM {tbl} WHERE design_id LIKE 'demo-design-%'")  # nosec B608
            except Exception:
                try:
                    conn.execute(f"DELETE FROM {tbl}")  # nosec B608
                except Exception:
                    pass
        try:
            conn.execute("DELETE FROM security_designs WHERE id LIKE 'demo-design-%'")
        except Exception:
            pass
        conn.commit()

    counts: dict = {}
    for d in _DESIGNS:
        conn.execute(
            "INSERT OR IGNORE INTO security_designs "
            "(id, name, description, classification, template_id, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (d["id"], d["name"], d["description"], d["classification"],
             d.get("template_id", ""), _ts(0), _ts(0)),
        )
    counts["designs"] = len(_DESIGNS)

    threat_ids = []
    for i, t in enumerate(_THREATS_BEFORE):
        tid = f"demo-threat-{i+1:03d}"
        conn.execute(
            "INSERT OR IGNORE INTO sc_threats "
            "(id, design_id, threat_category, mitre_technique, mitre_tactic, "
            "title, description, likelihood, impact, risk_score, status, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (tid, _PRIMARY_DESIGN_ID, t["category"], t["technique"], t["tactic"],
             t["title"], t["desc"], t["likelihood"], t["impact"], t["risk_score"],
             "open", _ts(-36)),
        )
        threat_ids.append(tid)
    counts["threats_before"] = len(threat_ids)

    conn.execute(
        "INSERT OR IGNORE INTO sc_trust_boundaries "
        "(id, design_id, label, boundary_type, classification, il_level, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        ("demo-boundary-001", _PRIMARY_DESIGN_ID, "IL5 Outer Perimeter",
         "network", "CUI", "IL5", _ts(-36)),
    )

    for aid, atype, alabel, adesc in [
        ("demo-asset-web", "web_application", "Public Web App", "IL5 exposed"),
        ("demo-asset-app", "application",     "App Server",     "Backend logic"),
        ("demo-asset-db",  "database",        "Primary DB",     "PII/PHI storage"),
    ]:
        conn.execute(
            "INSERT OR IGNORE INTO sc_assets "
            "(id, design_id, asset_type, label, description, classification, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (aid, _PRIMARY_DESIGN_ID, atype, alabel, adesc, "CUI", _ts(-36)),
        )
    counts["assets"] = 3

    for fid, src, dst, proto in [
        ("demo-flow-001", "demo-asset-web", "demo-asset-app", "HTTP"),
        ("demo-flow-002", "demo-asset-app", "demo-asset-db",  "TCP"),
        ("demo-flow-003", "demo-asset-db",  "demo-asset-web", "HTTP"),
    ]:
        conn.execute(
            "INSERT OR IGNORE INTO sc_data_flows "
            "(id, design_id, source_asset_id, target_asset_id, protocol, "
            "encrypted, authenticated, crosses_boundary, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (fid, _PRIMARY_DESIGN_ID, src, dst, proto, 0, 0, 1, _ts(-36)),
        )

    snapshots = [
        ("demo-snap-001", _PRIMARY_DESIGN_ID, -24,
         [{"id": "n1", "label": "External Attacker", "type": "attacker"},
          {"id": "n2", "label": "Public Web App (SQLi)", "type": "entry_point"},
          {"id": "n3", "label": "App Server", "type": "pivot"},
          {"id": "n4", "label": "Primary DB", "type": "target"}],
         [{"src": "n1", "dst": "n2", "encrypted": False, "authenticated": False},
          {"src": "n2", "dst": "n3", "encrypted": False, "authenticated": False},
          {"src": "n3", "dst": "n4", "encrypted": False, "authenticated": False}]),
        ("demo-snap-002", _PRIMARY_DESIGN_ID, -20,
         [{"id": "n1", "label": "Phishing Victim", "type": "attacker"},
          {"id": "n2", "label": "TOTP Bypass", "type": "entry_point"},
          {"id": "n3", "label": "Admin Console", "type": "pivot"},
          {"id": "n4", "label": "S3 Bucket (PII)", "type": "target"}],
         [{"src": "n1", "dst": "n2", "encrypted": False, "authenticated": False},
          {"src": "n2", "dst": "n3", "encrypted": True, "authenticated": False},
          {"src": "n3", "dst": "n4", "encrypted": True, "authenticated": False}]),
        ("demo-snap-003", _PRIMARY_DESIGN_ID, -18,
         [{"id": "n1", "label": "Compromised Container", "type": "attacker"},
          {"id": "n2", "label": "K8s API Server", "type": "entry_point"},
          {"id": "n3", "label": "Secrets Store", "type": "pivot"},
          {"id": "n4", "label": "Production DB", "type": "target"}],
         [{"src": "n1", "dst": "n2", "encrypted": False, "authenticated": False},
          {"src": "n2", "dst": "n3", "encrypted": True, "authenticated": False},
          {"src": "n3", "dst": "n4", "encrypted": False, "authenticated": False}]),
    ]
    for snap_id, comp_id, offset, nodes, edges in snapshots:
        conn.execute(
            "INSERT OR IGNORE INTO sdc_attack_snapshots "
            "(id, component_id, nodes_json, edges_json, created_at) VALUES (?,?,?,?,?)",
            (snap_id, comp_id, json.dumps(nodes), json.dumps(edges), _ts(offset)),
        )
    counts["attack_snapshots"] = len(snapshots)

    sop_ids = []
    for s in _SOPS:
        sid = f"demo-sop-{len(sop_ids)+1:03d}"
        conn.execute(
            "INSERT OR IGNORE INTO sdc_sops "
            "(id, title, sop_type, description, nist_controls, approval_status, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (sid, s["title"], s["sop_type"], s["desc"],
             json.dumps(s["nist"]), s["status"], _ts(-48), _ts(-48)),
        )
        sop_ids.append(sid)
    counts["sops"] = len(sop_ids)

    conn.commit()
    return counts


def seed_after_state(conn) -> dict:
    counts: dict = {}
    conn.execute(
        "UPDATE sc_threats SET status='mitigated' WHERE design_id=? AND id LIKE 'demo-threat-%'",
        (_PRIMARY_DESIGN_ID,),
    )
    mitigated_row = conn.execute("SELECT changes()").fetchone()
    counts["threats_mitigated"] = mitigated_row[0] if mitigated_row else 47

    ctrl_ids = []
    for i, c in enumerate(_CONTROLS_AFTER):
        cid = f"demo-ctrl-{i+1:03d}"
        conn.execute(
            "INSERT OR IGNORE INTO sc_controls "
            "(id, design_id, control_family, control_id, title, description, "
            "implementation_status, implementation_notes, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (cid, _PRIMARY_DESIGN_ID, c["family"], c["control_id"],
             c["title"], c["desc"], c["status"], c["notes"], _ts(16)),
        )
        ctrl_ids.append(cid)
    counts["controls_implemented"] = len(ctrl_ids)

    conn.execute(
        "UPDATE sc_data_flows SET encrypted=1, authenticated=1 "
        "WHERE design_id=? AND id LIKE 'demo-flow-%'",
        (_PRIMARY_DESIGN_ID,),
    )
    conn.execute(
        "UPDATE sdc_sops SET approval_status='approved', "
        "approved_by='isso-demo@agency.gov', approved_at=? "
        "WHERE id LIKE 'demo-sop-%'",
        (_ts(16),),
    )

    for (did, label, c1, c2, c3, risk, grade, ci, ct, rem) in [
        (_PRIMARY_DESIGN_ID, "before", 15, 22, 10, 8.7, "F", 0, len(_CONTROLS_AFTER), 200.0),
        (_PRIMARY_DESIGN_ID, "after",  0,  2,  1, 1.2, "A", len(_CONTROLS_AFTER), len(_CONTROLS_AFTER), 4.0),
    ]:
        conn.execute(
            "INSERT OR IGNORE INTO sdc_compliance_timeline "
            "(id, design_id, snapshot_label, cat1_count, cat2_count, cat3_count, "
            "risk_score, posture_grade, controls_implemented, controls_total, "
            "remediation_hours, snapshot_at, classification) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"demo-timeline-{label}", did, label, c1, c2, c3, risk, grade,
             ci, ct, rem, _ts(-48.0 if label == "before" else 16.8), "CUI"),
        )
    counts["timeline_rows"] = 2

    conn.execute(
        "INSERT OR IGNORE INTO sdc_roi_metrics "
        "(id, design_id, manual_hours, automated_hours, cost_per_hour, "
        "roi_multiplier, engagement_type, created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        ("demo-roi-001", _PRIMARY_DESIGN_ID, 200.0, 4.0, 150.0, 50.0, "government", _ts(17)),
    )
    counts["roi_rows"] = 1
    conn.commit()
    return counts


def seed_isso_workflow(conn) -> dict:
    counts: dict = {}
    for step_id, step_name, status, start_off, end_off in _WORKFLOW_STEPS:
        run_id = f"demo-run-{step_id}"
        approved_by = "isso-demo@agency.gov" if step_id in ("step-04", "step-09") else None
        approved_at = _ts(end_off) if approved_by else None
        conn.execute(
            "INSERT OR IGNORE INTO sdc_workflow_step_runs "
            "(id, design_id, step_id, step_name, status, approved_by, approved_at, "
            "started_at, completed_at, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (run_id, _PRIMARY_DESIGN_ID, step_id, step_name, status,
             approved_by, approved_at, _ts(start_off), _ts(end_off), _ts(start_off)),
        )
    counts["workflow_steps"] = len(_WORKFLOW_STEPS)
    conn.commit()
    return counts


def verify_sdc_demo_data(conn) -> dict:
    parameterized = {
        "threats_before": ("SELECT COUNT(*) FROM sc_threats WHERE design_id=? AND id LIKE 'demo-threat-%'", 47),
        "threats_mitigated": ("SELECT COUNT(*) FROM sc_threats WHERE design_id=? AND status='mitigated' AND id LIKE 'demo-threat-%'", 40),
        "attack_snapshots": ("SELECT COUNT(*) FROM sdc_attack_snapshots WHERE component_id=?", 3),
        "timeline_rows": ("SELECT COUNT(*) FROM sdc_compliance_timeline WHERE design_id=?", 2),
        "workflow_steps": ("SELECT COUNT(*) FROM sdc_workflow_step_runs WHERE design_id=?", 12),
        "controls": ("SELECT COUNT(*) FROM sc_controls WHERE design_id=? AND id LIKE 'demo-ctrl-%'", len(_CONTROLS_AFTER)),
    }
    simple = {
        "designs": ("SELECT COUNT(*) FROM security_designs WHERE id LIKE 'demo-design-%'", 8),
        "sops": ("SELECT COUNT(*) FROM sdc_sops WHERE id LIKE 'demo-sop-%'", 5),
    }
    checks: list[dict] = []
    for name, (sql, minimum) in simple.items():
        try:
            actual = conn.execute(sql).fetchone()[0]
            checks.append({"check": name, "ok": actual >= minimum, "actual": actual, "minimum": minimum})
        except Exception as exc:
            checks.append({"check": name, "ok": False, "actual": -1, "minimum": minimum, "error": str(exc)})
    for name, (sql, minimum) in parameterized.items():
        try:
            actual = conn.execute(sql, (_PRIMARY_DESIGN_ID,)).fetchone()[0]
            checks.append({"check": name, "ok": actual >= minimum, "actual": actual, "minimum": minimum})
        except Exception as exc:
            checks.append({"check": name, "ok": False, "actual": -1, "minimum": minimum, "error": str(exc)})
    all_ok = all(c["ok"] for c in checks)
    return {"status": "pass" if all_ok else "fail", "checks": checks, "primary_design_id": _PRIMARY_DESIGN_ID}


def main(reset: bool = False, verify: bool = False, json_output: bool = False) -> dict:
    conn = _get_conn()
    _ensure_demo_tables(conn)
    if verify:
        result = verify_sdc_demo_data(conn)
        conn.close()
        if json_output:
            print(json.dumps(result, indent=2))
        else:
            for c in result["checks"]:
                print(f"  [{'OK' if c['ok'] else 'FAIL'}] {c['check']}: {c.get('actual','?')} / {c['minimum']}")
        return result
    from tools.security_canvas.db.init_db import init_db as _init_sc
    _init_sc()
    b = seed_before_state(conn, reset=reset)
    a = seed_after_state(conn)
    w = seed_isso_workflow(conn)
    conn.close()
    summary = {"status": "ok", "before_state": b, "after_state": a, "workflow": w}
    if json_output:
        print(json.dumps(summary, indent=2))
    else:
        print(f"SDC Demo Seed complete. Threats:{b['threats_before']} Controls:{a['controls_implemented']} Steps:{w['workflow_steps']}")
    return summary


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="SDC Demo Seed")
    parser.add_argument("--all", action="store_true", dest="run_all")
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args()
    if args.verify:
        conn = _get_conn()
        _ensure_demo_tables(conn)
        result = verify_sdc_demo_data(conn)
        conn.close()
        if args.json_output:
            print(json.dumps(result, indent=2))
        sys.exit(0 if result["status"] == "pass" else 1)
    elif args.run_all:
        main(reset=args.reset, json_output=args.json_output)
    else:
        parser.print_help()
