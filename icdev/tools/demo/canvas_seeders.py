# CUI // SP-CTI
"""Canvas-specific demo data seeders for 6 key canvases.

Public API:
    seed_network_canvas(tenant_id)    — 20 nodes, 35 edges, 3 anomaly flags
    seed_security_canvas(tenant_id)   — 1 design, 8 threats, 12 controls, 2 findings
    seed_dic_canvas(tenant_id)        — 5 collections, 15 documents, 200 total chunks
    seed_compliance_canvas(tenant_id) — 10 frameworks, 50 controls mapped
    seed_agentic_canvas(tenant_id)    — 3 agent workflows with nodes/edges
    seed_kanban_canvas(tenant_id)     — 12 demo tasks across 3 epics

All seeders:
  - Are idempotent (INSERT OR IGNORE / ON CONFLICT DO NOTHING).
  - Accept an optional ``_conn`` parameter for unit-test injection.
  - Return a dict with counts and ``ok: True`` on success, or ``ok: False``
    with ``error`` on failure — callers should log but never raise.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_NOW = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _uid(prefix: str, idx: int) -> str:
    raw = f"{prefix}-{idx}".encode()
    return prefix + "-" + hashlib.sha256(raw).hexdigest()[:12]


def _node(nid: str, label: str, ntype: str, x: int, y: int, **kw: Any) -> dict:
    return {"id": nid, "type": ntype, "label": label, "x": x, "y": y, **kw}


def _edge(eid: str, src: str, dst: str, label: str = "", **kw: Any) -> dict:
    return {"id": eid, "source": src, "target": dst, "label": label, **kw}


# ---------------------------------------------------------------------------
# 1. Network Canvas
# ---------------------------------------------------------------------------

_NET_NODES: list[dict] = [
    _node(_uid("nn", i), label, ntype, x, y)
    for i, (label, ntype, x, y) in enumerate([
        ("Core Router A",      "router",       100,  200),
        ("Core Router B",      "router",       300,  200),
        ("Edge Router C",      "router",       500,  200),
        ("Edge Router D",      "router",       700,  200),
        ("Distribution SW-1",  "switch",       100,  400),
        ("Distribution SW-2",  "switch",       300,  400),
        ("Access SW-3",        "switch",       500,  400),
        ("Access SW-4",        "switch",       700,  400),
        ("App Server 1",       "server",       100,  600),
        ("App Server 2",       "server",       300,  600),
        ("DB Server Primary",  "server",       500,  600),
        ("DB Server Replica",  "server",       700,  600),
        ("Perimeter FW-1",     "firewall",       50,  100),
        ("Perimeter FW-2",     "firewall",      350,  100),
        ("Internal FW-3",      "firewall",      600,  100),
        ("DMZ FW-4",           "firewall",      800,  100),
        ("Load Balancer A",    "load_balancer", 200,  500),
        ("Load Balancer B",    "load_balancer", 600,  500),
        ("Primary DB",         "database",      500,  700),
        ("VPN Gateway",        "vpn_gateway",   900,  300),
    ])
]

# 35 edges (idx 0-34); edges 32, 33, 34 are flagged as anomalies
_NET_EDGES: list[dict] = []
_NET_EDGE_PAIRS = [
    (0,1),(0,4),(1,5),(2,6),(3,7),(4,8),(4,9),(5,8),(5,9),
    (6,10),(6,11),(7,10),(7,11),(8,16),(9,16),(10,17),(11,17),
    (16,8),(17,10),(18,10),(18,11),(12,0),(13,1),(14,2),(15,3),
    (0,2),(1,3),(4,6),(5,7),(19,3),(16,18),(17,18),(12,19),
    (13,14),(14,15),
]
for _i, (_s, _d) in enumerate(_NET_EDGE_PAIRS):
    _anomaly = _i >= 32
    _NET_EDGES.append(_edge(
        _uid("ne", _i),
        _NET_NODES[_s]["id"],
        _NET_NODES[_d]["id"],
        anomaly=_anomaly,
    ))


def _init_network_schema(conn: Any) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS topologies (
            id          TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            description TEXT,
            graph_json  TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
            template_id TEXT,
            classification TEXT DEFAULT 'public',
            created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at  TEXT DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()


def seed_network_canvas(tenant_id: str, _conn: Any = None) -> dict:
    """Seed network canvas: 20 nodes, 35 edges, 3 anomaly-flagged edges."""
    close = _conn is None
    try:
        if _conn is None:
            from tools.network.db.init_db import get_connection, init_db
            _conn = get_connection()
            init_db()

        graph = json.dumps({"nodes": _NET_NODES, "edges": _NET_EDGES})
        _conn.execute(
            """INSERT OR IGNORE INTO topologies
               (id, name, description, graph_json, classification, created_at, updated_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (
                _uid("topo", 0),
                "Demo Network — ACME Corp HQ",
                "Synthetic demo topology for ICDEV demonstration",
                graph,
                "CUI",
                _NOW, _NOW,
            ),
        )
        _conn.commit()
        anomalies = sum(1 for e in _NET_EDGES if e.get("anomaly"))
        return {"ok": True, "nodes": len(_NET_NODES), "edges": len(_NET_EDGES), "anomalies": anomalies}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        if close and _conn is not None:
            try:
                _conn.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# 2. Security Canvas
# ---------------------------------------------------------------------------

_SC_DESIGN_ID = _uid("scd", 0)

_SC_THREATS = [
    ("Initial Access",    "T1190", "TA0001", "Exploit Public-Facing Application",  "high",   "critical", 9.1),
    ("Credential Access", "T1110", "TA0006", "Brute Force — Password Spraying",    "medium", "high",     7.2),
    ("Persistence",       "T1053", "TA0003", "Scheduled Task / Cron Abuse",        "low",    "medium",   4.5),
    ("Exfiltration",      "T1041", "TA0010", "Exfiltration Over C2 Channel",       "high",   "critical", 8.8),
    ("Lateral Movement",  "T1021", "TA0008", "Remote Services — SSH",              "medium", "high",     6.9),
    ("Defense Evasion",   "T1070", "TA0005", "Indicator Removal On Host",          "low",    "medium",   5.0),
    ("Discovery",         "T1046", "TA0007", "Network Service Scanning",           "high",   "medium",   6.2),
    ("Impact",            "T1486", "TA0040", "Data Encrypted for Impact (Ransom)", "high",   "critical", 9.5),
]

_SC_CONTROLS = [
    ("AC", "AC-2",  "Account Management",              "implemented"),
    ("AC", "AC-3",  "Access Enforcement",              "implemented"),
    ("AC", "AC-17", "Remote Access",                   "implemented"),
    ("AU", "AU-2",  "Audit Events",                    "implemented"),
    ("AU", "AU-12", "Audit Record Generation",         "implemented"),
    ("CM", "CM-6",  "Configuration Settings",          "planned"),
    ("CM", "CM-7",  "Least Functionality",             "planned"),
    ("IA", "IA-2",  "Identification and Authentication","implemented"),
    ("IA", "IA-5",  "Authenticator Management",        "in_progress"),
    ("SI", "SI-2",  "Flaw Remediation",                "implemented"),
    ("SI", "SI-3",  "Malicious Code Protection",       "implemented"),
    ("SC", "SC-8",  "Transmission Confidentiality",    "in_progress"),
]


def _init_security_schema(conn: Any) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS security_designs (
            id TEXT PRIMARY KEY, name TEXT NOT NULL,
            description TEXT, graph_json TEXT NOT NULL DEFAULT '{}',
            classification TEXT DEFAULT 'CUI',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS sc_threats (
            id TEXT PRIMARY KEY, design_id TEXT,
            threat_category TEXT NOT NULL, mitre_technique TEXT,
            mitre_tactic TEXT, title TEXT NOT NULL, description TEXT,
            likelihood TEXT DEFAULT 'medium', impact TEXT DEFAULT 'medium',
            risk_score REAL DEFAULT 0, affected_assets TEXT DEFAULT '[]',
            status TEXT DEFAULT 'open', is_stale INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS sc_controls (
            id TEXT PRIMARY KEY, design_id TEXT,
            control_family TEXT NOT NULL, control_id TEXT NOT NULL,
            title TEXT NOT NULL, description TEXT,
            implementation_status TEXT DEFAULT 'planned',
            mitigates_threats TEXT DEFAULT '[]',
            protects_assets TEXT DEFAULT '[]',
            evidence_json TEXT DEFAULT '{}',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS sc_assessments (
            id TEXT PRIMARY KEY, design_id TEXT,
            assessment_type TEXT NOT NULL, total_threats INTEGER DEFAULT 0,
            total_controls INTEGER DEFAULT 0, risk_score REAL DEFAULT 0,
            posture_grade TEXT DEFAULT 'C',
            findings_json TEXT DEFAULT '[]',
            recommendations_json TEXT DEFAULT '[]',
            ran_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()


def seed_security_canvas(tenant_id: str, _conn: Any = None) -> dict:
    """Seed security canvas: 8 threats, 12 controls, 2 assessment findings."""
    close = _conn is None
    try:
        if _conn is None:
            from tools.security_canvas.db.init_db import get_connection, init_db
            _conn = get_connection()
            init_db()

        _conn.execute(
            """INSERT OR IGNORE INTO security_designs
               (id, name, description, graph_json, classification, created_at, updated_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (
                _SC_DESIGN_ID,
                "Demo Security Design — ACME Corp",
                "Synthetic NIST-aligned security design for ICDEV demonstration",
                json.dumps({"nodes": [], "edges": [], "boundaries": []}),
                "CUI",
                _NOW, _NOW,
            ),
        )

        for i, (cat, tech, tactic, title, likelihood, impact, score) in enumerate(_SC_THREATS):
            _conn.execute(
                """INSERT OR IGNORE INTO sc_threats
                   (id, design_id, threat_category, mitre_technique, mitre_tactic,
                    title, likelihood, impact, risk_score, status, created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    _uid("sct", i), _SC_DESIGN_ID, cat, tech, tactic,
                    title, likelihood, impact, score, "open", _NOW,
                ),
            )

        for i, (family, ctrl_id, title, status) in enumerate(_SC_CONTROLS):
            _conn.execute(
                """INSERT OR IGNORE INTO sc_controls
                   (id, design_id, control_family, control_id, title,
                    implementation_status, created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                (_uid("scc", i), _SC_DESIGN_ID, family, ctrl_id, title, status, _NOW),
            )

        findings = [
            {"title": "High-severity unpatched vulnerability", "severity": "high"},
            {"title": "MFA not enforced on admin accounts",    "severity": "critical"},
        ]
        for i, finding_set in enumerate(
            [findings[:1], findings[1:]]
        ):
            _conn.execute(
                """INSERT OR IGNORE INTO sc_assessments
                   (id, design_id, assessment_type, total_threats, total_controls,
                    risk_score, posture_grade, findings_json, ran_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    _uid("sca", i), _SC_DESIGN_ID, "automated",
                    len(_SC_THREATS), len(_SC_CONTROLS),
                    8.2 - i * 0.5, "C",
                    json.dumps(finding_set),
                    _NOW,
                ),
            )

        _conn.commit()
        return {"ok": True, "threats": len(_SC_THREATS), "controls": len(_SC_CONTROLS), "findings": 2}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        if close and _conn is not None:
            try:
                _conn.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# 3. DIC Canvas
# ---------------------------------------------------------------------------

_DIC_COLLECTIONS = [
    ("Policy Documents",      "Organizational policies and procedures"),
    ("Technical Standards",   "Engineering and architecture standards"),
    ("Compliance Artifacts",  "ATO packages, SSPs, POAMs"),
    ("Project Documentation", "Project plans, requirements, design docs"),
    ("Research Reports",      "Threat intelligence and research findings"),
]

_DIC_DOCUMENTS = [
    # (filename, collection_idx, chunks_total, doc_id_suffix)
    ("access-control-policy.pdf",          0, 14, "d01"),
    ("password-policy.pdf",                0, 9,  "d02"),
    ("incident-response-plan.pdf",         0, 18, "d03"),
    ("network-architecture-standard.pdf",  1, 16, "d04"),
    ("api-design-guidelines.md",           1, 11, "d05"),
    ("secure-coding-standard.pdf",         1, 15, "d06"),
    ("fedramp-ssp-template.docx",          2, 22, "d07"),
    ("poam-tracker.xlsx",                  2, 8,  "d08"),
    ("ato-package-v3.pdf",                 2, 20, "d09"),
    ("project-charter-2026.docx",          3, 12, "d10"),
    ("system-requirements-spec.pdf",       3, 17, "d11"),
    ("architecture-decision-records.md",   3, 10, "d12"),
    ("apt29-threat-report.pdf",            4, 19, "d13"),
    ("zero-trust-research-2026.pdf",       4, 14, "d14"),
    ("supply-chain-risk-brief.pdf",        4, 15, "d15"),
]


def _init_dic_schema(conn: Any) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS dic_collections (
            collection_id TEXT PRIMARY KEY,
            name TEXT NOT NULL, description TEXT DEFAULT '',
            owner_id TEXT DEFAULT '', retention_days INTEGER DEFAULT 90,
            classification TEXT DEFAULT 'CUI',
            tenant_id TEXT DEFAULT 'default',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS dic_ingest_jobs (
            job_id TEXT PRIMARY KEY,
            filename TEXT NOT NULL,
            collection_id TEXT DEFAULT 'default',
            status TEXT DEFAULT 'completed',
            stage_detail TEXT DEFAULT '',
            chunks_total INTEGER DEFAULT 0,
            chunks_done INTEGER DEFAULT 0,
            doc_id TEXT,
            errors_json TEXT DEFAULT '[]',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            tenant_id TEXT DEFAULT 'default'
        );
    """)
    conn.commit()


def seed_dic_canvas(tenant_id: str, _conn: Any = None) -> dict:
    """Seed DIC canvas: 5 collections, 15 documents, 200 total chunks."""
    close = _conn is None
    try:
        if _conn is None:
            from tools.db.storage import get_connection
            _conn = get_connection()

        _init_dic_schema(_conn)

        col_ids: list[str] = []
        for i, (name, desc) in enumerate(_DIC_COLLECTIONS):
            cid = _uid("dcc", i)
            col_ids.append(cid)
            _conn.execute(
                """INSERT OR IGNORE INTO dic_collections
                   (collection_id, name, description, owner_id,
                    classification, tenant_id, created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                (cid, name, desc, "demo-admin", "CUI", tenant_id, _NOW),
            )

        total_chunks = 0
        for i, (filename, col_idx, chunks, suffix) in enumerate(_DIC_DOCUMENTS):
            doc_id = f"doc-{suffix}-{tenant_id[:8]}"
            _conn.execute(
                """INSERT OR IGNORE INTO dic_ingest_jobs
                   (job_id, filename, collection_id, status, chunks_total,
                    chunks_done, doc_id, tenant_id, created_at, updated_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    _uid("dcj", i), filename, col_ids[col_idx],
                    "completed", chunks, chunks, doc_id, tenant_id, _NOW, _NOW,
                ),
            )
            total_chunks += chunks

        _conn.commit()
        return {
            "ok": True,
            "collections": len(_DIC_COLLECTIONS),
            "documents": len(_DIC_DOCUMENTS),
            "chunks": total_chunks,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        if close and _conn is not None:
            try:
                _conn.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# 4. Compliance Canvas
# ---------------------------------------------------------------------------

_COMPLIANCE_FRAMEWORKS = [
    ("NIST 800-53 Rev 5",   "nist-800-53-r5",  "Federal baseline security controls"),
    ("FedRAMP Moderate",    "fedramp-mod",     "Cloud service provider authorization baseline"),
    ("CMMC Level 2",        "cmmc-l2",         "DoD contractor cybersecurity requirements"),
    ("SOC 2 Type II",       "soc2-t2",         "Service organization controls for trust"),
    ("PCI DSS v4.0",        "pci-dss-v4",      "Payment card industry data security standard"),
    ("HIPAA Security Rule", "hipaa-sec",       "Healthcare information privacy and security"),
    ("ISO 27001:2022",      "iso-27001",       "Information security management systems"),
    ("NIST CSF 2.0",        "nist-csf-2",      "Cybersecurity framework for critical infrastructure"),
    ("CIS Controls v8",     "cis-v8",          "Prioritized set of cybersecurity best practices"),
    ("DISA STIG RHEL 9",    "disa-stig-rhel9", "Security Technical Implementation Guide for RHEL 9"),
]

# 5 controls per framework = 50 total
_CONTROL_TEMPLATE = [
    ("Access Control",          "Implement role-based access controls",    "implemented"),
    ("Audit & Accountability",  "Enable audit logging for all systems",    "implemented"),
    ("Configuration Mgmt",      "Enforce CIS benchmark configurations",    "in_progress"),
    ("Incident Response",       "Maintain IR plan tested annually",        "planned"),
    ("Risk Assessment",         "Conduct annual risk assessments",         "implemented"),
]


def seed_compliance_canvas(tenant_id: str, _conn: Any = None) -> dict:
    """Seed compliance canvas: 10 frameworks, 50 controls mapped."""
    close = _conn is None
    try:
        if _conn is None:
            from tools.db.storage import get_connection
            _conn = get_connection()

        _conn.executescript("""
            CREATE TABLE IF NOT EXISTS demo_compliance_frameworks (
                id              TEXT PRIMARY KEY,
                name            TEXT NOT NULL,
                slug            TEXT NOT NULL,
                description     TEXT,
                tenant_id       TEXT NOT NULL,
                status          TEXT DEFAULT 'active',
                created_at      TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS demo_compliance_controls (
                id              TEXT PRIMARY KEY,
                framework_id    TEXT NOT NULL,
                title           TEXT NOT NULL,
                description     TEXT,
                status          TEXT DEFAULT 'planned',
                tenant_id       TEXT NOT NULL,
                created_at      TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """)
        _conn.commit()

        ctrl_count = 0
        for fi, (name, slug, desc) in enumerate(_COMPLIANCE_FRAMEWORKS):
            fid = _uid("dcf", fi)
            _conn.execute(
                """INSERT OR IGNORE INTO demo_compliance_frameworks
                   (id, name, slug, description, tenant_id, created_at)
                   VALUES (%s,%s,%s,%s,%s,%s)""",
                (fid, name, slug, desc, tenant_id, _NOW),
            )
            for ci, (ctrl_title, ctrl_desc, ctrl_status) in enumerate(_CONTROL_TEMPLATE):
                _conn.execute(
                    """INSERT OR IGNORE INTO demo_compliance_controls
                       (id, framework_id, title, description, status, tenant_id, created_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        _uid(f"dcc-{fi}", ci), fid,
                        ctrl_title, ctrl_desc, ctrl_status, tenant_id, _NOW,
                    ),
                )
                ctrl_count += 1

        _conn.commit()
        return {
            "ok": True,
            "frameworks": len(_COMPLIANCE_FRAMEWORKS),
            "controls": ctrl_count,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        if close and _conn is not None:
            try:
                _conn.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# 5. Agentic Canvas
# ---------------------------------------------------------------------------

def _wf_nodes(names: list[tuple[str, str]], x0: int = 100, y0: int = 100) -> list[dict]:
    return [_node(_uid(f"an{i}", i), label, ntype, x0 + i * 150, y0) for i, (label, ntype) in enumerate(names)]


def _wf_edges(nodes: list[dict]) -> list[dict]:
    return [_edge(_uid("ae", i), nodes[i]["id"], nodes[i + 1]["id"]) for i in range(len(nodes) - 1)]


_AGENTIC_WORKFLOWS = [
    {
        "name":        "Demo — Document Ingestion Agent",
        "description": "Automated document ingestion pipeline with LLM extraction",
        "domain":      "document_intelligence",
        "autonomy":    2,
        "safety":      0,
        "nodes": _wf_nodes([
            ("Input Trigger",    "trigger"),
            ("Pre-processor",    "transform"),
            ("LLM Extractor",    "llm_call"),
            ("Chunk Embedder",   "embedding"),
            ("Index Writer",     "storage"),
            ("Notify Operator",  "notification"),
        ]),
    },
    {
        "name":        "Demo — Threat Hunt Automation",
        "description": "Continuous threat hunting across log streams using MITRE ATT&CK",
        "domain":      "security",
        "autonomy":    1,
        "safety":      1,
        "nodes": _wf_nodes([
            ("Log Collector",   "data_source"),
            ("SIEM Query",      "query"),
            ("Anomaly Detect",  "ml_model"),
            ("MITRE Mapper",    "enrichment"),
            ("Alert Router",    "router"),
            ("HITL Review",     "human_review"),
            ("Remediate",       "action"),
        ]),
    },
    {
        "name":        "Demo — Compliance Assessment Agent",
        "description": "Automated NIST 800-53 control evidence collection and gap analysis",
        "domain":      "compliance",
        "autonomy":    3,
        "safety":      0,
        "nodes": _wf_nodes([
            ("Framework Loader", "config"),
            ("Evidence Gatherer","data_source"),
            ("Control Mapper",   "transform"),
            ("Gap Analyzer",     "llm_call"),
            ("Report Generator", "output"),
        ]),
    },
]


def _init_agentic_schema(conn: Any) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS aadc_designs (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            domain TEXT DEFAULT '',
            classification TEXT DEFAULT 'CUI',
            graph_json TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
            autonomy_max INTEGER DEFAULT 0,
            safety_impacting INTEGER DEFAULT 0,
            rights_impacting INTEGER DEFAULT 0,
            hitl_required INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()


def seed_agentic_canvas(tenant_id: str, _conn: Any = None) -> dict:
    """Seed agentic canvas: 3 agent workflows with nodes/edges."""
    close = _conn is None
    try:
        if _conn is None:
            from tools.agentic_ai_canvas.db.init_db import get_connection, init_db
            _conn = get_connection()
            init_db()

        total_nodes = 0
        total_edges = 0
        for i, wf in enumerate(_AGENTIC_WORKFLOWS):
            nodes = wf["nodes"]
            edges = _wf_edges(nodes)
            graph = json.dumps({"nodes": nodes, "edges": edges})
            _conn.execute(
                """INSERT OR IGNORE INTO aadc_designs
                   (id, name, description, domain, classification,
                    graph_json, autonomy_max, safety_impacting,
                    hitl_required, created_at, updated_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    _uid("wf", i),
                    wf["name"], wf["description"], wf["domain"],
                    "CUI", graph,
                    wf["autonomy"], wf["safety"],
                    1 if wf["safety"] else 0,
                    _NOW, _NOW,
                ),
            )
            total_nodes += len(nodes)
            total_edges += len(edges)

        _conn.commit()
        return {
            "ok": True,
            "workflows": len(_AGENTIC_WORKFLOWS),
            "nodes": total_nodes,
            "edges": total_edges,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        if close and _conn is not None:
            try:
                _conn.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# 6. Kanban Canvas
# ---------------------------------------------------------------------------

_KANBAN_EPICS = [
    ("build",   "Build",    "build",   "high"),
    ("sec",     "Security", "build",   "high"),
    ("deploy",  "Deploy",   "chore",   "medium"),
]

_KANBAN_TASKS = [
    # (epic_key, title, task_type, priority)
    ("build",  "Implement user authentication module",        "build",   "high"),
    ("build",  "Design API schema for core resources",        "build",   "high"),
    ("build",  "Build dashboard analytics widgets",           "build",   "medium"),
    ("build",  "Integrate LLM router with canvas API",        "build",   "medium"),
    ("sec",    "Run SAST scan on new authentication code",    "build",   "high"),
    ("sec",    "Review dependency audit findings",            "build",   "high"),
    ("sec",    "Update SBOM after new package additions",     "chore",   "medium"),
    ("sec",    "Validate RLS predicates on tenant tables",    "build",   "high"),
    ("deploy", "Provision staging environment namespaces",    "chore",   "medium"),
    ("deploy", "Run smoke tests against staging deploy",      "build",   "high"),
    ("deploy", "Update Helm chart values for production",     "chore",   "medium"),
    ("deploy", "Coordinate go/no-go gate with stakeholders",  "chore",   "low"),
]


def _init_kanban_schema(conn: Any) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS kanban_tasks (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT,
            task_type TEXT DEFAULT 'build',
            priority TEXT DEFAULT 'high',
            status TEXT DEFAULT 'backlog',
            scheduled_at TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            completed_at TEXT,
            executor_type TEXT DEFAULT 'claude_cli',
            failure_count INTEGER DEFAULT 0,
            dispatch_source TEXT DEFAULT 'demo_seeder'
        );
    """)
    conn.commit()


def seed_kanban_canvas(tenant_id: str, _conn: Any = None) -> dict:
    """Seed kanban canvas: 12 demo tasks across 3 epics."""
    close = _conn is None
    try:
        if _conn is None:
            from tools.db.storage import get_connection
            _conn = get_connection()

        _init_kanban_schema(_conn)

        for i, (epic, title, ttype, priority) in enumerate(_KANBAN_TASKS):
            task_id = f"demo-{epic}-{i + 1:02d}-{tenant_id[:8]}"
            desc = f"[Demo] {title}. Seeded for tenant {tenant_id}."
            _conn.execute(
                """INSERT OR IGNORE INTO kanban_tasks
                   (id, title, description, task_type, priority, status,
                    dispatch_source, created_at, updated_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (task_id, title, desc, ttype, priority, "backlog", "demo_seeder", _NOW, _NOW),
            )

        _conn.commit()
        return {"ok": True, "tasks": len(_KANBAN_TASKS)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        if close and _conn is not None:
            try:
                _conn.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Convenience: run all seeders
# ---------------------------------------------------------------------------

_ALL_SEEDERS = {
    "network":    seed_network_canvas,
    "security":   seed_security_canvas,
    "dic":        seed_dic_canvas,
    "compliance": seed_compliance_canvas,
    "agentic":    seed_agentic_canvas,
    "kanban":     seed_kanban_canvas,
}


def seed_all_canvases(tenant_id: str) -> dict[str, dict]:
    """Run all 6 canvas seeders and return per-canvas results."""
    return {name: fn(tenant_id) for name, fn in _ALL_SEEDERS.items()}
