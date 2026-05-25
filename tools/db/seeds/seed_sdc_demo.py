"""SDC Demo Seed — Before State (47 STIG findings, 3 attack paths).

Inserts deterministic "before" baseline for the Security Design Canvas demo:
  - 8 security_designs (DoD IL5, cross-domain, IAM, perimeter, pipeline, etc.)
  - 47 sc_threats: CAT1 (15), CAT2 (22), CAT3 (10) — open, unmitigated
  - 3 sdc_attack_snapshots with unencrypted/unauthenticated edges
  - 5 sdc_sops in pending_review status

All data uses random.seed(42) for reproducibility.
Saves to data/security_canvas.db (security canvas DB).

Usage:
  python tools/db/seeds/seed_sdc_demo.py [--json] [--reset]
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT))

random.seed(42)

_NOW = datetime.now(timezone.utc)
_T0 = _NOW - timedelta(hours=48)


def _ts(offset_hours: float = 0.0) -> str:
    return (_T0 + timedelta(hours=offset_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _uid() -> str:
    return str(uuid.UUID(int=random.getrandbits(128)))


def _get_conn():
    """Get security_canvas DB connection."""
    try:
        from tools.security_canvas.db.init_db import get_connection
        return get_connection()
    except Exception:
        import sqlite3
        db = _ROOT / "data" / "security_canvas.db"
        db.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn


# ══════════════════════════════════════════════════════════════════════════════
# Design IDs — stable so snapshots and threats can reference them
# ══════════════════════════════════════════════════════════════════════════════

_D = {
    "bcap_vdss":   "sd-demo-01-bcap-vdss",
    "mission_app": "sd-demo-02-mission-app",
    "xd_solution": "sd-demo-03-xd-solution",
    "iam":         "sd-demo-04-iam",
    "perimeter":   "sd-demo-05-perimeter",
    "data_pipe":   "sd-demo-06-data-pipe",
    "cicd":        "sd-demo-07-cicd",
    "container":   "sd-demo-08-container",
}

# ══════════════════════════════════════════════════════════════════════════════
# Helper: graph JSON builders (mirrored from init_db.py style)
# ══════════════════════════════════════════════════════════════════════════════

def _node(nid, label, ntype, x, y):
    return {"id": nid, "type": ntype, "label": label, "x": x, "y": y}


def _edge(src, dst, label="", protocol="", encrypted=False, authenticated=False):
    return {
        "id": str(uuid.uuid4())[:8],
        "source": src,
        "target": dst,
        "label": label,
        "protocol": protocol,
        "encrypted": encrypted,
        "authenticated": authenticated,
    }


def _boundary(bid, label, btype, classification, il_level, color, x, y, w, h, assets):
    return {
        "id": bid, "label": label, "boundary_type": btype,
        "classification": classification, "il_level": il_level,
        "color": color, "x": x, "y": y, "width": w, "height": h,
        "contained_assets": assets,
    }


# ══════════════════════════════════════════════════════════════════════════════
# 8 Security Designs
# ══════════════════════════════════════════════════════════════════════════════

def _gen_designs() -> list[tuple]:
    graphs = {
        "bcap_vdss": {
            "nodes": [
                _node("bcap", "BCAP Perimeter", "boundary-bcap", 50, 200),
                _node("vdss-fw", "VDSS Firewall", "ctrl-firewall", 220, 150),
                _node("vdss-ids", "VDSS IDS/IPS", "ctrl-ids", 220, 300),
                _node("vdms", "VDMS", "asset-server", 420, 200),
                _node("mission", "Mission App", "asset-server", 620, 200),
                _node("db", "Mission DB", "asset-database", 820, 200),
                _node("siem", "VDMS SIEM", "ctrl-siem", 420, 50),
                _node("encryptor", "Type 1 Encryptor", "ctrl-encryption", 50, 350),
            ],
            "edges": [
                _edge("bcap", "vdss-fw", "Filtered", "IPSec", True, True),
                _edge("vdss-fw", "vdss-ids", "Inspect", ""),
                _edge("vdss-ids", "vdms", "Allowed", "TLS", True, True),
                _edge("vdms", "mission", "Route", "HTTP", False, False),
                _edge("mission", "db", "SQL", "TCP", False, False),
                _edge("mission", "siem", "Logs", "UDP"),
                _edge("bcap", "encryptor", "WAN", "Type 1", True, True),
            ],
            "boundaries": [
                _boundary("bnd-vdss", "VDSS Zone", "network", "CUI", "IL5", "#ff6b6b", 170, 100, 150, 280, ["vdss-fw", "vdss-ids"]),
                _boundary("bnd-vdms", "VDMS Zone", "network", "CUI", "IL5", "#4ecdc4", 370, 30, 150, 250, ["vdms", "siem"]),
                _boundary("bnd-mission", "Mission Zone", "network", "CUI", "IL5", "#45b7d1", 570, 130, 310, 150, ["mission", "db"]),
            ],
        },
        "mission_app": {
            "nodes": [
                _node("client", "Client Workstation", "asset-client", 50, 200),
                _node("lb", "Load Balancer", "asset-server", 230, 200),
                _node("app", "Mission App Server", "asset-server", 430, 200),
                _node("cache", "Redis Cache", "asset-database", 430, 50),
                _node("db", "PostgreSQL DB", "asset-database", 630, 200),
                _node("idp", "IdP / SSO", "ctrl-idp", 230, 50),
            ],
            "edges": [
                _edge("client", "lb", "HTTP", "HTTP/1.1", False, False),
                _edge("lb", "app", "Forward", "HTTP", False, True),
                _edge("app", "cache", "Session", "TCP", False, False),
                _edge("app", "db", "SQL", "TCP", False, False),
                _edge("app", "idp", "AuthN", "SAML", True, True),
            ],
            "boundaries": [
                _boundary("bnd-app", "App Zone", "network", "CUI", "IL4", "#4ecdc4", 180, 130, 320, 150, ["lb", "app"]),
                _boundary("bnd-data", "Data Zone", "network", "CUI", "IL5", "#45b7d1", 580, 130, 150, 150, ["db"]),
            ],
        },
        "xd_solution": {
            "nodes": [
                _node("nipr-src", "NIPR Source", "asset-client", 50, 200),
                _node("xd-guard", "XD Guard", "ctrl-firewall", 250, 200),
                _node("dlp", "DLP Filter", "ctrl-dlp", 450, 200),
                _node("jwics-dst", "JWICS Destination", "asset-server", 650, 200),
                _node("audit", "Transfer Audit Log", "ctrl-siem", 350, 50),
            ],
            "edges": [
                _edge("nipr-src", "xd-guard", "Transfer Request", "TCP", False, False),
                _edge("xd-guard", "dlp", "Inspect", "TLS", True, True),
                _edge("dlp", "jwics-dst", "Transfer", "TLS", True, True),
                _edge("xd-guard", "audit", "Audit", "UDP"),
            ],
            "boundaries": [
                _boundary("bnd-nipr", "NIPR Zone", "network", "CUI", "IL4", "#45b7d1", 20, 150, 170, 120, ["nipr-src"]),
                _boundary("bnd-xd", "Cross-Domain Zone", "dmz", "CUI", "IL5", "#ff6b6b", 210, 130, 300, 150, ["xd-guard", "dlp"]),
                _boundary("bnd-jwics", "JWICS Zone", "enclave", "SECRET", "IL6", "#e94560", 610, 150, 150, 120, ["jwics-dst"]),
            ],
        },
        "iam": {
            "nodes": [
                _node("user", "Admin User", "asset-user", 50, 200),
                _node("pam", "PAM Vault", "ctrl-pam", 250, 200),
                _node("idp", "IdP / MFA", "ctrl-idp", 450, 200),
                _node("ldap", "LDAP / AD", "asset-database", 650, 200),
                _node("audit", "Audit Log", "ctrl-siem", 450, 50),
            ],
            "edges": [
                _edge("user", "pam", "Request", "HTTPS", True, True),
                _edge("pam", "idp", "Verify", "SAML", True, True),
                _edge("idp", "ldap", "Lookup", "LDAP", False, False),
                _edge("idp", "audit", "AuthN Event", "TLS", True, False),
            ],
            "boundaries": [
                _boundary("bnd-iam", "IAM Zone", "network", "CUI", "IL4", "#4ecdc4", 200, 130, 330, 150, ["pam", "idp"]),
            ],
        },
        "perimeter": {
            "nodes": [
                _node("inet", "Internet", "boundary-internet", 50, 200),
                _node("border", "Border Router", "asset-network", 250, 200),
                _node("fw", "Perimeter Firewall", "ctrl-firewall", 450, 200),
                _node("ids", "IDS/IPS", "ctrl-ids", 650, 200),
                _node("siem", "SIEM", "ctrl-siem", 450, 50),
                _node("dns", "DNS Server", "asset-server", 250, 50),
            ],
            "edges": [
                _edge("inet", "border", "BGP", "BGP", False, False),
                _edge("border", "fw", "Routed", "IP", False, False),
                _edge("fw", "ids", "Mirrored", "SPAN"),
                _edge("fw", "siem", "Logs", "UDP"),
                _edge("dns", "siem", "Query Log", "UDP"),
            ],
            "boundaries": [
                _boundary("bnd-dmz", "DMZ", "dmz", "CUI", "IL4", "#ff6b6b", 200, 130, 330, 150, ["border", "fw"]),
            ],
        },
        "data_pipe": {
            "nodes": [
                _node("source", "Data Source", "asset-client", 50, 200),
                _node("ingest", "Ingestion API", "asset-server", 250, 200),
                _node("etl", "ETL Processor", "asset-server", 450, 200),
                _node("lake", "Data Lake (S3)", "asset-storage", 650, 200),
                _node("kms", "KMS", "ctrl-kms", 650, 50),
                _node("audit", "Audit Log", "ctrl-siem", 250, 50),
            ],
            "edges": [
                _edge("source", "ingest", "Raw Data", "HTTP", False, False),
                _edge("ingest", "etl", "Process", "HTTP", False, False),
                _edge("etl", "lake", "Write", "HTTP", False, False),
                _edge("lake", "kms", "Encrypt/Decrypt", "PKCS#11", True, True),
                _edge("ingest", "audit", "Access Log", "UDP"),
            ],
            "boundaries": [
                _boundary("bnd-pipe", "Processing Zone", "network", "CUI", "IL4", "#4ecdc4", 200, 130, 370, 150, ["ingest", "etl"]),
                _boundary("bnd-lake", "Data Lake", "data", "CUI", "IL5", "#45b7d1", 600, 130, 150, 150, ["lake"]),
            ],
        },
        "cicd": {
            "nodes": [
                _node("repo", "Git Repository", "asset-storage", 50, 200),
                _node("ci", "CI Runner", "asset-server", 250, 200),
                _node("registry", "Artifact Registry", "asset-registry", 450, 200),
                _node("gate", "Deploy Gate", "ctrl-firewall", 650, 200),
                _node("prod", "Production", "asset-server", 850, 200),
                _node("scanner", "SAST Scanner", "ctrl-scanner", 250, 50),
            ],
            "edges": [
                _edge("repo", "ci", "Trigger", "SSH", True, True),
                _edge("ci", "scanner", "Scan Code", "API", True, True),
                _edge("ci", "registry", "Push", "TLS", True, True),
                _edge("registry", "gate", "Promote", "TLS", True, True),
                _edge("gate", "prod", "Deploy", "TLS", True, True),
            ],
            "boundaries": [
                _boundary("bnd-build", "Build Zone", "network", "CUI", "IL4", "#f0ad4e", 200, 130, 370, 150, ["ci", "registry"]),
            ],
        },
        "container": {
            "nodes": [
                _node("registry", "Container Registry", "asset-registry", 50, 200),
                _node("admission", "Admission Controller", "ctrl-admission", 280, 200),
                _node("k8s", "K8s Cluster", "asset-server", 500, 200),
                _node("falco", "Falco Runtime", "ctrl-ids", 700, 200),
                _node("siem", "SIEM", "ctrl-siem", 500, 50),
            ],
            "edges": [
                _edge("registry", "admission", "Pull", "TLS", True, True),
                _edge("admission", "k8s", "Admit", "K8s API", True, True),
                _edge("k8s", "falco", "Syscalls", "Agent"),
                _edge("falco", "siem", "Alerts", "TLS", True, False),
            ],
            "boundaries": [
                _boundary("bnd-cluster", "Cluster Boundary", "enclave", "CUI", "IL4", "#4ecdc4", 230, 130, 490, 150, ["admission", "k8s"]),
            ],
        },
    }

    designs_data = [
        (_D["bcap_vdss"],   "DoD IL5 BCAP/VDSS Enclave",        "BEFORE: IL5 enclave with 12 open STIG findings including telnet, default-deny gaps, and cleartext routing.",    graphs["bcap_vdss"],   "sct-dod-il5",     "CUI"),
        (_D["mission_app"], "Mission Application Server",         "BEFORE: Mission app with HTTP endpoints, unencrypted DB connections, and missing session security.",             graphs["mission_app"], None,              "CUI"),
        (_D["xd_solution"], "Cross-Domain Solution Gateway",      "BEFORE: XD guard permitting subnet /24 scope, missing JWICS logging, and unauthenticated transfer requests.",    graphs["xd_solution"], None,              "CUI"),
        (_D["iam"],         "Identity and Access Management",     "BEFORE: PAM using default credentials, missing MFA enforcement, and LDAP using unencrypted cleartext protocol.", graphs["iam"],         None,              "CUI"),
        (_D["perimeter"],   "Network Perimeter Defense",          "BEFORE: Border router BGP unauthenticated, DNS zone transfer unrestricted, IDS in passive-only mode.",           graphs["perimeter"],   None,              "CUI"),
        (_D["data_pipe"],   "Data Processing Pipeline",           "BEFORE: ETL pipeline using HTTP ingestion, plaintext PII at rest, missing data access audit log.",               graphs["data_pipe"],   None,              "CUI"),
        (_D["cicd"],        "CI/CD Deployment Pipeline",          "BEFORE: Pipeline secrets in plaintext env vars, no CVE scan gate before production promote.",                    graphs["cicd"],        "sct-cicd-security","CUI"),
        (_D["container"],   "Container Security Platform",        "BEFORE: Pod security policies unenforced — privileged containers permitted in production namespace.",             graphs["container"],   "sct-container-bb","CUI"),
    ]

    rows = []
    for i, (did, name, desc, graph, tmpl, classification) in enumerate(designs_data):
        t_created = _ts(i * 0.5)
        rows.append((did, name, desc, json.dumps(graph), tmpl, None, classification, t_created, t_created))
    return rows


# ══════════════════════════════════════════════════════════════════════════════
# 47 sc_threats: CAT1 (15), CAT2 (22), CAT3 (10)
# ══════════════════════════════════════════════════════════════════════════════

_THREAT_DATA = [
    # ── Design 1: BCAP/VDSS (12 threats: 6 CAT1, 4 CAT2, 2 CAT3) ──────────
    (_D["bcap_vdss"], "CAT1", "T1078", "Initial Access",
     "Telnet Enabled on VDSS Firewall — Cleartext Exposure",
     "Telnet service active; credentials transmitted in cleartext over NIPR management segment.",
     "high", "critical", 9.8, ["vdss-fw"]),
    (_D["bcap_vdss"], "CAT1", "T1190", "Initial Access",
     "BCAP Perimeter Missing Explicit Default-Deny Rule",
     "No deny-all baseline on BCAP ether1 inbound chain; traffic permitted by default.",
     "high", "critical", 9.5, ["bcap", "vdss-fw"]),
    (_D["bcap_vdss"], "CAT1", "T1552.001", "Credential Access",
     "Hardcoded IAM Key Bypasses TCCM in VDMS Config",
     "Direct IAM key found in BCAP-VDMS configuration file — bypasses TCCM credential lifecycle.",
     "high", "critical", 9.3, ["vdms"]),
    (_D["bcap_vdss"], "CAT1", "T1133", "Initial Access",
     "FTP Service Active on NIPR Gateway — Cleartext File Transfer",
     "FTP enabled on network gateway; allows unauthenticated file retrieval.",
     "high", "critical", 9.1, ["bcap"]),
    (_D["bcap_vdss"], "CAT1", "T1110", "Credential Access",
     "SNMP Community String Set to Default 'public'",
     "Default SNMP read/write community string enables unauthenticated SNMP access to VDSS devices.",
     "high", "high", 8.9, ["vdss-fw", "vdss-ids"]),
    (_D["bcap_vdss"], "CAT1", "T1499", "Impact",
     "VDSS IDS Signatures Outdated — 72-Hour Exposure Window",
     "Layer 7 protocol signatures last updated 72 hours ago; CAT1 exposure to known CVEs.",
     "high", "critical", 9.0, ["vdss-ids"]),
    (_D["bcap_vdss"], "CAT2", "T1040", "Credential Access",
     "SSH Weak Ciphers Permitted on VDSS Firewall",
     "SSH strong-crypto disabled; DES and RC4 cipher suites accepted.",
     "medium", "high", 7.2, ["vdss-fw"]),
    (_D["bcap_vdss"], "CAT2", "T1021", "Lateral Movement",
     "Management Access Reachable from Non-OOB Network Segment",
     "SSH management interface accessible from 10.0.0.0/8 rather than the 10.99.0.0/30 OOB-only subnet.",
     "medium", "high", 6.8, ["vdss-fw", "vdms"]),
    (_D["bcap_vdss"], "CAT2", "T1074", "Collection",
     "IL4 and IL5 Traffic Not Segregated — Mangle Rules Missing",
     "VDMS policy routing does not enforce IL-level workload isolation; mixed traffic path.",
     "medium", "high", 6.5, ["vdms"]),
    (_D["bcap_vdss"], "CAT2", "T1485", "Impact",
     "No Source Classification Validation Before CSP Routing",
     "VDMS forwards traffic to AWS/Azure without verifying source classification label.",
     "medium", "high", 6.3, ["vdms"]),
    (_D["bcap_vdss"], "CAT3", "T1562", "Defense Evasion",
     "VDSS-IDS Alerts Not Forwarded to SOC",
     "Syslog remote action not configured on BCAP-VDSS-IDS; alerts silently dropped.",
     "low", "medium", 4.1, ["vdss-ids"]),
    (_D["bcap_vdss"], "CAT3", "T1530", "Collection",
     "VDSS Firewall Not Logging Connection State",
     "Established and related sessions not captured in firewall logs; forensic blind spot.",
     "low", "medium", 3.8, ["vdss-fw"]),

    # ── Design 2: Mission App (8 threats: 2 CAT1, 4 CAT2, 2 CAT3) ──────────
    (_D["mission_app"], "CAT1", "T1040", "Credential Access",
     "Unencrypted HTTP API Endpoint Exposed on Mission App",
     "Internal REST API served over HTTP/1.1; credentials and session tokens transmitted in cleartext.",
     "high", "critical", 9.4, ["lb", "app"]),
    (_D["mission_app"], "CAT1", "T1552.001", "Credential Access",
     "Hardcoded Database Credentials in Application Config File",
     "PostgreSQL password stored in plaintext application.properties; readable by any process on host.",
     "high", "critical", 9.2, ["app", "db"]),
    (_D["mission_app"], "CAT2", "T1110.003", "Credential Access",
     "No Rate Limiting on Authentication Endpoint",
     "Login endpoint accepts unlimited requests; vulnerable to credential stuffing.",
     "medium", "high", 7.0, ["lb", "app"]),
    (_D["mission_app"], "CAT2", "T1539", "Credential Access",
     "Session Tokens Transmitted Without Secure Flag",
     "HTTP Set-Cookie header missing Secure and HttpOnly attributes.",
     "medium", "high", 6.7, ["app"]),
    (_D["mission_app"], "CAT2", "T1190", "Initial Access",
     "Legacy TLS 1.0/1.1 Protocols Accepted by Load Balancer",
     "Load balancer TLS policy accepts deprecated protocol versions; POODLE/BEAST attack surface.",
     "medium", "high", 6.9, ["lb"]),
    (_D["mission_app"], "CAT2", "T1059", "Execution",
     "No CSRF Protection on State-Changing API Endpoints",
     "Cross-site request forgery tokens absent on POST, PUT, DELETE routes.",
     "medium", "medium", 5.9, ["app"]),
    (_D["mission_app"], "CAT3", "T1592", "Reconnaissance",
     "Verbose Error Messages Expose Internal Stack Traces",
     "Unhandled exceptions return full stack trace to client; leaks file paths and class names.",
     "low", "medium", 4.0, ["app"]),
    (_D["mission_app"], "CAT3", "T1592.002", "Reconnaissance",
     "Missing Security Response Headers on HTTP Responses",
     "X-Content-Type-Options, X-Frame-Options, and CSP headers absent.",
     "low", "low", 3.2, ["lb", "app"]),

    # ── Design 3: Cross-Domain Solution (7 threats: 3 CAT1, 3 CAT2, 1 CAT3) ─
    (_D["xd_solution"], "CAT1", "T1021.002", "Lateral Movement",
     "XD Guard Permits Entire /24 Subnet Instead of Specific /32 Hosts",
     "Cross-domain guard allowlist specifies 10.30.1.0/24; should be individual host /32 entries.",
     "high", "critical", 9.6, ["xd-guard"]),
    (_D["xd_solution"], "CAT1", "T1599", "Defense Evasion",
     "JWICS Default Route 0.0.0.0/0 Points Toward NIPR",
     "Default route via 10.40.0.1 found on JWICS edge; violates JWICS network isolation policy.",
     "high", "critical", 9.7, ["jwics-dst"]),
    (_D["xd_solution"], "CAT1", "T1530", "Collection",
     "XD Transfer Audit Logs Not Forwarded to SOC",
     "XD guard audit action not configured; denied transfer events not reaching SOC SIEM.",
     "high", "high", 8.5, ["xd-guard", "audit"]),
    (_D["xd_solution"], "CAT2", "T1562.001", "Defense Evasion",
     "JWICS Deny Log Prefix Not Configured on Cross-Domain Interface",
     "Denied cross-domain packets not tagged; correlation with SIEM alerts impaired.",
     "medium", "high", 6.4, ["xd-guard"]),
    (_D["xd_solution"], "CAT2", "T1071", "Command and Control",
     "Multicast Traffic Not Blocked on JWICS Interface",
     "Multicast groups not explicitly denied on ether3; potential for covert channel.",
     "medium", "high", 6.1, ["jwics-dst"]),
    (_D["xd_solution"], "CAT2", "T1040", "Credential Access",
     "JWICS NTP Pointing to NIPR Management Network Source",
     "JWICS-EDGE NTP configured to 10.99.0.2 (NIPR MGMT) rather than JWICS Stratum-1.",
     "medium", "medium", 5.5, ["jwics-dst"]),
    (_D["xd_solution"], "CAT3", "T1018", "Discovery",
     "JWICS Route Prefix Visible in NIPR Routing Table via Redistribution",
     "192.168.100.0/24 JWICS prefix appearing in NIPR route table due to route redistribution.",
     "low", "medium", 4.3, ["xd-guard"]),

    # ── Design 4: IAM (6 threats: 2 CAT1, 2 CAT2, 2 CAT3) ──────────────────
    (_D["iam"], "CAT1", "T1078.002", "Credential Access",
     "PAM Vault Using Default Admin Credentials",
     "PAM deployment never had factory default username/password changed post-installation.",
     "high", "critical", 9.9, ["pam"]),
    (_D["iam"], "CAT1", "T1098", "Privilege Escalation",
     "Service Account Assigned Domain Admin Privileges",
     "Application service account member of Domain Admins; violates least-privilege principle.",
     "high", "critical", 9.4, ["idp", "ldap"]),
    (_D["iam"], "CAT2", "T1556", "Credential Access",
     "MFA Not Enforced for Privileged Account Access",
     "Administrative accounts can authenticate with password only; MFA policy not applied.",
     "medium", "high", 7.1, ["pam", "idp"]),
    (_D["iam"], "CAT2", "T1110.002", "Credential Access",
     "Password Policy Minimum Length Below STIG Requirement",
     "Current policy requires 8 characters; DoD STIG mandates 15-character minimum.",
     "medium", "high", 6.6, ["ldap"]),
    (_D["iam"], "CAT3", "T1078", "Initial Access",
     "Inactive User Accounts Not Disabled After 35 Days",
     "Stale accounts for departed personnel remain active beyond the 35-day STIG threshold.",
     "low", "medium", 4.5, ["ldap"]),
    (_D["iam"], "CAT3", "T1110", "Credential Access",
     "Account Lockout Threshold Set to Unlimited Attempts",
     "No account lockout policy enforced; brute-force attempts not blocked.",
     "low", "medium", 4.2, ["ldap", "idp"]),

    # ── Design 5: Perimeter (6 threats: 1 CAT1, 3 CAT2, 2 CAT3) ─────────────
    (_D["perimeter"], "CAT1", "T1557.002", "Credential Access",
     "Border Router BGP Peer Authentication Disabled",
     "No BGP MD5 peer authentication; vulnerable to BGP hijack from spoofed peer.",
     "high", "critical", 9.1, ["border"]),
    (_D["perimeter"], "CAT2", "T1498", "Impact",
     "Perimeter ACL Permits Unrestricted ICMP from External to Internal",
     "Access control list allows all ICMP inbound; enables ping sweep and ICMP tunneling.",
     "medium", "high", 6.8, ["fw"]),
    (_D["perimeter"], "CAT2", "T1590.002", "Reconnaissance",
     "DNS Zone Transfer Allowed to Unauthorized External Hosts",
     "AXFR queries accepted from any source; full zone data exposed to external actors.",
     "medium", "high", 7.3, ["dns"]),
    (_D["perimeter"], "CAT2", "T1499", "Impact",
     "IDS/IPS Operating in Passive-Only (Detection) Mode",
     "Inline IDS configured for alert-only; attack traffic passes through without blocking.",
     "medium", "high", 6.9, ["ids"]),
    (_D["perimeter"], "CAT3", "T1562.008", "Defense Evasion",
     "NTP Not Synchronized to Authoritative DoD Time Source",
     "Network devices not configured to approved DoD NTP server; clock drift > 5 minutes.",
     "low", "medium", 3.9, ["border"]),
    (_D["perimeter"], "CAT3", "T1592", "Reconnaissance",
     "Router Login Banner Missing STIG-Compliant Warning Text",
     "Pre-authentication banner absent; does not meet DoD CIO warning message requirement.",
     "low", "low", 3.0, ["border", "fw"]),

    # ── Design 6: Data Pipeline (5 threats: 1 CAT1, 3 CAT2, 1 CAT3) ─────────
    (_D["data_pipe"], "CAT1", "T1560", "Collection",
     "PII Data Fields Stored in Plaintext — Encryption at Rest Missing",
     "Sensitive PII fields in data lake stored without KMS encryption; readable from storage layer.",
     "high", "critical", 9.5, ["lake"]),
    (_D["data_pipe"], "CAT2", "T1530", "Collection",
     "Database Backup Files Accessible Without Authentication",
     "Backup S3 bucket has public-read ACL; backup archives downloadable without credentials.",
     "medium", "high", 7.5, ["lake"]),
    (_D["data_pipe"], "CAT2", "T1040", "Credential Access",
     "ETL Pipeline Ingesting Data Over Unencrypted HTTP",
     "Ingestion API endpoint accepts data submissions over HTTP without TLS; cleartext data in transit.",
     "medium", "high", 7.2, ["source", "ingest"]),
    (_D["data_pipe"], "CAT2", "T1562.006", "Defense Evasion",
     "Data Access Audit Log Not Enabled on Data Lake",
     "Object-level access logging disabled on S3 bucket; unauthorized reads not recorded.",
     "medium", "medium", 6.0, ["lake", "audit"]),
    (_D["data_pipe"], "CAT3", "T1499.002", "Impact",
     "Database Query Timeout Not Configured",
     "No statement timeout enforced; long-running queries can degrade service availability.",
     "low", "low", 3.1, ["etl"]),

    # ── Design 7: CI/CD (2 threats: 0 CAT1, 2 CAT2, 0 CAT3) ─────────────────
    (_D["cicd"], "CAT2", "T1552.001", "Credential Access",
     "Pipeline Secrets Stored in Unencrypted Environment Variables",
     "CI runner environment variables include API keys and signing certificates in plaintext.",
     "medium", "high", 7.4, ["ci"]),
    (_D["cicd"], "CAT2", "T1195.001", "Initial Access",
     "Container Images Built Without CVE Scan Gate",
     "Pipeline promotes images to production registry without mandatory vulnerability scan blocking.",
     "medium", "high", 7.0, ["ci", "registry"]),

    # ── Design 8: Container (1 threat: 0 CAT1, 1 CAT2, 0 CAT3) ─────────────
    (_D["container"], "CAT2", "T1610", "Execution",
     "Pod Security Policies Unenforced — Privileged Containers Permitted",
     "Kubernetes admission controller allows privileged=true pods in production namespace.",
     "medium", "high", 7.6, ["k8s"]),
]


def _gen_threats() -> list[tuple]:
    rows = []
    for i, (did, cat, tech, tactic, title, desc, likelihood, impact, risk, assets) in enumerate(_THREAT_DATA):
        tid = f"thr-demo-{i+1:02d}"
        t_created = _ts(random.uniform(0, 24))
        rows.append((tid, did, cat, tech, tactic, title, desc,
                     likelihood, impact, risk, json.dumps(assets),
                     "open", 0, t_created))
    return rows


# ══════════════════════════════════════════════════════════════════════════════
# 3 sdc_attack_snapshots — unencrypted/unauthenticated attack paths
# ══════════════════════════════════════════════════════════════════════════════

def _gen_attack_snapshots() -> list[tuple]:
    snap1_nodes = [
        _node("attk-ext", "External Attacker", "asset-client", 50, 200),
        _node("attk-bcap", "BCAP (No Default-Deny)", "boundary-bcap", 250, 200),
        _node("attk-vdss", "VDSS FW (Telnet)", "ctrl-firewall", 450, 200),
        _node("attk-vdms", "VDMS (Hardcoded IAM)", "asset-server", 650, 200),
        _node("attk-mission", "Mission App (HTTP)", "asset-server", 850, 200),
    ]
    snap1_edges = [
        _edge("attk-ext",    "attk-bcap",    "Unsolicited Inbound",  "TCP",    False, False),
        _edge("attk-bcap",   "attk-vdss",    "No Default-Deny",      "Telnet", False, False),
        _edge("attk-vdss",   "attk-vdms",    "Cleartext Lateral",    "HTTP",   False, False),
        _edge("attk-vdms",   "attk-mission", "IAM Key Replay",       "HTTP",   False, False),
    ]

    snap2_nodes = [
        _node("lat-comp",   "Compromised Workstation", "asset-client",  50,  200),
        _node("lat-border", "Border Router (No BGP Auth)", "asset-network", 250, 200),
        _node("lat-core",   "Core Router",             "asset-network", 450, 200),
        _node("lat-srv",    "Internal Server",         "asset-server",  650, 200),
        _node("lat-db",     "Mission DB",              "asset-database",850, 200),
    ]
    snap2_edges = [
        _edge("lat-comp",   "lat-border", "BGP Spoof",          "BGP",    False, False),
        _edge("lat-border", "lat-core",   "Route Hijack",       "IP",     False, False),
        _edge("lat-core",   "lat-srv",    "Telnet Pivot",       "Telnet", False, False),
        _edge("lat-srv",    "lat-db",     "Unencrypted SQL",    "TCP",    False, False),
    ]

    snap3_nodes = [
        _node("exfil-app",  "Mission App (HTTP API)", "asset-server",  50,  200),
        _node("exfil-ingest","ETL Ingestion (HTTP)",  "asset-server",  250, 200),
        _node("exfil-lake", "Data Lake (No Encrypt)", "asset-storage", 450, 200),
        _node("exfil-c2",   "External C2 Server",     "asset-client",  650, 200),
    ]
    snap3_edges = [
        _edge("exfil-app",   "exfil-ingest", "Plaintext POST",      "HTTP", False, False),
        _edge("exfil-ingest","exfil-lake",   "Unencrypted Write",   "HTTP", False, False),
        _edge("exfil-lake",  "exfil-c2",     "Exfil via HTTP GET",  "HTTP", False, False),
    ]

    caldera_ids = ["calop-bcap-bypass-001", "calop-lateral-bgp-002", "calop-exfil-http-003"]

    snapshots = [
        (_uid(), _D["bcap_vdss"],   json.dumps(snap1_nodes), json.dumps(snap1_edges), caldera_ids[0], _ts(2.0)),
        (_uid(), _D["perimeter"],   json.dumps(snap2_nodes), json.dumps(snap2_edges), caldera_ids[1], _ts(4.0)),
        (_uid(), _D["data_pipe"],   json.dumps(snap3_nodes), json.dumps(snap3_edges), caldera_ids[2], _ts(6.0)),
    ]
    return snapshots


# ══════════════════════════════════════════════════════════════════════════════
# 5 sdc_sops — pending_review
# ══════════════════════════════════════════════════════════════════════════════

_SOP_DATA = [
    (
        "sop-demo-01", "BCAP/VDSS STIG CAT1 Remediation Procedure", "remediation",
        "Step-by-step procedure for remediating the 6 CAT1 STIG findings on BCAP/VDSS devices.",
        "Eliminate CAT1 STIG findings to achieve ATO gate compliance on the IL5 enclave.",
        "Applies to VDSS Firewall, VDSS IDS/IPS, and VDMS components within the BCAP perimeter.",
        [
            "1. Disable Telnet service on VDSS Firewall (`ip service set telnet disabled=yes`).",
            "2. Add explicit default-deny rule as lowest priority on BCAP ether1 inbound chain.",
            "3. Remove hardcoded IAM key from VDMS config; rotate through TCCM.",
            "4. Disable FTP service on NIPR gateway.",
            "5. Change SNMP community strings from default; apply SNMPv3 with auth.",
            "6. Update VDSS IDS L7 signatures to current release; schedule 24h auto-update.",
            "7. Run STIG scan post-remediation and verify all CAT1 findings closed.",
        ],
        ["CM-7", "IA-5", "SI-2", "AC-17"],
        "VDSS-Admin-Team", "ISSO-IL5-Enclave",
    ),
    (
        "sop-demo-02", "Cross-Domain Transfer Authorization and Audit Procedure", "operations",
        "Procedure for authorizing and auditing cross-domain data transfers from NIPR to JWICS.",
        "Ensure all NIPR→JWICS transfers are individually authorized and fully audited per DSS guidelines.",
        "Applies to Cross-Domain Solution Gateway and all personnel initiating NIPR to JWICS transfers.",
        [
            "1. Requestor submits transfer request through the approved XD request portal.",
            "2. ISSO reviews request against the approved source host list (/32 hosts only).",
            "3. Verify data classification meets JWICS minimum (SECRET or above).",
            "4. XD Guard administrator approves request in the audit system.",
            "5. Monitor DLP filter pass/fail decision; document result.",
            "6. Confirm transfer audit log entry recorded and forwarded to SOC SIEM.",
            "7. Retain transfer record for 3 years per NIST AU-11.",
        ],
        ["AC-4", "AU-2", "AU-9", "SC-7"],
        "XD-Guard-Admin", "ISSO-CrossDomain",
    ),
    (
        "sop-demo-03", "Privileged Account Management and PAM Vault Hardening", "security",
        "Hardening procedure for PAM vault and privileged accounts identified in IAM design review.",
        "Remediate PAM default credentials and MFA gaps to prevent privileged access compromise.",
        "Applies to PAM vault, IdP/MFA system, and LDAP/AD for all privileged account tiers.",
        [
            "1. Immediately rotate PAM vault admin credentials from factory defaults.",
            "2. Enable MFA (FIDO2 or PIV) for all accounts with privileged access.",
            "3. Remove Domain Admin membership from service accounts; assign least-privilege roles.",
            "4. Update password policy minimum length to 15 characters.",
            "5. Run query for accounts inactive > 35 days; disable and notify account owners.",
            "6. Configure account lockout after 3 failed attempts with 15-minute lockout duration.",
            "7. Review and certify all privileged account memberships quarterly.",
        ],
        ["AC-2", "AC-6", "IA-5", "IA-12"],
        "IAM-Admin-Team", "CISO-Office",
    ),
    (
        "sop-demo-04", "Data Pipeline Encryption and Access Audit Remediation", "remediation",
        "Procedure for encrypting data in transit and at rest within the data processing pipeline.",
        "Close CAT1 and CAT2 data security gaps to protect PII and meet NIST 800-53 SC controls.",
        "Applies to Data Ingestion API, ETL Processor, and Data Lake (S3) components.",
        [
            "1. Enable TLS on ingestion API endpoint; redirect all HTTP traffic to HTTPS.",
            "2. Enable KMS server-side encryption on all S3 buckets; enforce aws:SecureTransport.",
            "3. Remove public-read ACL from backup S3 bucket; apply bucket policy deny.",
            "4. Enable S3 object-level access logging and forward to CloudWatch/SIEM.",
            "5. Configure ETL job to use HTTPS endpoints only; fail on HTTP connections.",
            "6. Set database query statement_timeout = 30000 (30 seconds).",
            "7. Validate encryption in transit using network capture before closing findings.",
        ],
        ["SC-8", "SC-28", "AU-2", "AU-9"],
        "DataOps-Team", "ISSO-DataPipeline",
    ),
    (
        "sop-demo-05", "Incident Response Procedure — Unencrypted Attack Path Detection", "incident_response",
        "IR procedure triggered when attack snapshots detect unencrypted/unauthenticated lateral movement.",
        "Contain and eradicate attacks traversing unencrypted paths before data exfiltration occurs.",
        "Applies to Security Operations Center, VDSS team, and all network segment owners.",
        [
            "1. SOC analyst receives alert from SIEM for unencrypted lateral movement signature.",
            "2. Identify source and destination nodes from attack snapshot graph.",
            "3. Isolate compromised segment by blocking source IP at BCAP perimeter immediately.",
            "4. Capture packet evidence from affected interfaces for forensic preservation.",
            "5. Notify ISSO and CO within 1 hour per CIRT notification procedures.",
            "6. Apply emergency STIG remediation for CAT1 findings on affected devices.",
            "7. Validate containment with follow-up SIEM correlation query; close incident ticket.",
        ],
        ["IR-4", "IR-5", "IR-6", "SI-4"],
        "SOC-Lead", "ISSO-IL5-Enclave",
    ),
]


def _gen_sops() -> list[tuple]:
    rows = []
    for i, (sid, title, sop_type, desc, purpose, scope, steps, controls, owner, reviewer) in enumerate(_SOP_DATA):
        t_created = _ts(i * 1.0)
        next_review = (_NOW + timedelta(days=90)).strftime("%Y-%m-%d")
        rows.append((
            sid, title, sop_type, desc, purpose, scope,
            json.dumps(steps), json.dumps(controls),
            owner, reviewer,
            "pending_review",   # approval_status
            None,               # approved_by
            None,               # approved_at
            None,               # rejected_reason
            "1.0",              # version
            next_review,
            "CUI",
            t_created, t_created,
        ))
    return rows


# ══════════════════════════════════════════════════════════════════════════════
# Ensure schema tables exist before inserting
# ══════════════════════════════════════════════════════════════════════════════

_DDL_ENSURE = [
    """CREATE TABLE IF NOT EXISTS security_designs (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT,
        graph_json TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[],"boundaries":[]}',
        template_id TEXT, source_topology_id TEXT,
        classification TEXT DEFAULT 'CUI',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS sc_threats (
        id TEXT PRIMARY KEY, design_id TEXT,
        threat_category TEXT NOT NULL, mitre_technique TEXT, mitre_tactic TEXT,
        title TEXT NOT NULL, description TEXT,
        likelihood TEXT DEFAULT 'medium', impact TEXT DEFAULT 'medium',
        risk_score REAL DEFAULT 0, affected_assets TEXT DEFAULT '[]',
        status TEXT DEFAULT 'open', is_stale INTEGER DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS sdc_attack_snapshots (
        id TEXT PRIMARY KEY, component_id TEXT NOT NULL,
        nodes_json TEXT NOT NULL DEFAULT '[]',
        edges_json TEXT NOT NULL DEFAULT '[]',
        caldera_op_id TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS sdc_sops (
        id TEXT PRIMARY KEY, title TEXT NOT NULL,
        sop_type TEXT NOT NULL, description TEXT,
        purpose TEXT, scope TEXT,
        steps TEXT DEFAULT '[]', nist_controls TEXT DEFAULT '[]',
        owner TEXT, reviewer TEXT,
        approval_status TEXT DEFAULT 'draft',
        approved_by TEXT, approved_at TEXT, rejected_reason TEXT,
        version TEXT DEFAULT '1.0', next_review_date TEXT,
        classification TEXT DEFAULT 'CUI',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""",
]


# ══════════════════════════════════════════════════════════════════════════════
# Public entry point
# ══════════════════════════════════════════════════════════════════════════════

def seed_before_state(reset: bool = False) -> dict:
    """Insert the 'before' demo state into security_canvas.db."""
    conn = _get_conn()
    counts: dict[str, int] = {}
    try:
        for ddl in _DDL_ENSURE:
            conn.execute(ddl)
        conn.commit()

        tables = {
            "security_designs": (
                "id,name,description,graph_json,template_id,source_topology_id,classification,created_at,updated_at",
                _gen_designs(),
            ),
            "sc_threats": (
                "id,design_id,threat_category,mitre_technique,mitre_tactic,title,description,"
                "likelihood,impact,risk_score,affected_assets,status,is_stale,created_at",
                _gen_threats(),
            ),
            "sdc_attack_snapshots": (
                "id,component_id,nodes_json,edges_json,caldera_op_id,created_at",
                _gen_attack_snapshots(),
            ),
            "sdc_sops": (
                "id,title,sop_type,description,purpose,scope,steps,nist_controls,"
                "owner,reviewer,approval_status,approved_by,approved_at,rejected_reason,"
                "version,next_review_date,classification,created_at,updated_at",
                _gen_sops(),
            ),
        }

        for table, (cols, rows) in tables.items():
            if reset:
                conn.execute(f"DELETE FROM {table} WHERE id LIKE '%-demo-%' OR id LIKE 'thr-demo-%' OR id LIKE 'sop-demo-%'")
            placeholders = ",".join("?" * len(cols.split(",")))
            conn.executemany(
                f"INSERT OR IGNORE INTO {table} ({cols}) VALUES ({placeholders})",
                rows,
            )
            counts[table] = len(rows)

        conn.commit()
    finally:
        conn.close()

    return {
        "status": "ok",
        "counts": counts,
        "total": sum(counts.values()),
        "threat_breakdown": {
            "CAT1": sum(1 for t in _THREAT_DATA if t[1] == "CAT1"),
            "CAT2": sum(1 for t in _THREAT_DATA if t[1] == "CAT2"),
            "CAT3": sum(1 for t in _THREAT_DATA if t[1] == "CAT3"),
        },
        "attack_snapshots": 3,
        "sops_pending_review": 5,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="SDC Demo Seed — Before State")
    parser.add_argument("--json",  action="store_true")
    parser.add_argument("--reset", action="store_true", help="Clear demo rows before insert")
    args = parser.parse_args()
    result = seed_before_state(args.reset)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Seeded {result['total']} demo records:")
        for t, c in result["counts"].items():
            print(f"  {t:30s}: {c:3d} rows")
        bd = result["threat_breakdown"]
        print(f"  Threats — CAT1:{bd['CAT1']}  CAT2:{bd['CAT2']}  CAT3:{bd['CAT3']}")


if __name__ == "__main__":
    main()
