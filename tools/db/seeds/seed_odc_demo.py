#!/usr/bin/env python3
# CUI // SP-CTI
"""ODC Demo Seed -- populates observability_canvas.db with realistic observability design demo data.

Tables seeded:
  observability_designs (5), od_assessments (5), od_audit (10), od_versions (8),
  od_collab_sessions (4), od_ttp_coverage (12), odc_gap_scores (5),
  odc_technique_coverage (15), odc_otel_events (20), odc_sdc_verifications (5),
  odc_mitre_techniques (10)

Usage:
    python tools/db/seeds/seed_odc_demo.py --all [--reset] [--json]
    python tools/db/seeds/seed_odc_demo.py --verify --json
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
        from tools.observability_canvas.db.init_db import get_connection, init_db
        conn = get_connection()
        init_db()
        return conn
    except Exception:
        db = _ROOT / "data" / "observability_canvas.db"
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
        "odc_mitre_techniques", "odc_sdc_verifications", "odc_otel_events",
        "odc_technique_coverage", "odc_gap_scores", "od_ttp_coverage",
        "od_collab_sessions", "od_versions", "od_audit", "od_assessments",
        "observability_designs",
    ):
        try:
            conn.execute(f"DELETE FROM {tbl}")
        except Exception:
            pass
    conn.commit()


_DESIGN_IDS = {f"odc-design-{i:03d}": _uid() for i in range(5)}

_OBSERVABILITY_DESIGNS = [
    {
        "id": _DESIGN_IDS["odc-design-000"],
        "name": "OTel Full-Stack Pipeline",
        "description": "OpenTelemetry Collector ingesting logs, metrics, and traces from microservices into Prometheus, Jaeger, and Grafana for full-stack observability.",
        "graph_json": json.dumps({
            "nodes": [
                {"id": "svc-a", "type": "service", "label": "Service A", "x": 50, "y": 50},
                {"id": "svc-b", "type": "service", "label": "Service B", "x": 50, "y": 150},
                {"id": "svc-c", "type": "service", "label": "Service C", "x": 50, "y": 250},
                {"id": "otel-col", "type": "collector", "label": "OTel Collector", "x": 250, "y": 150},
                {"id": "prom", "type": "metrics", "label": "Prometheus", "x": 450, "y": 50},
                {"id": "jaeger", "type": "trace", "label": "Jaeger", "x": 450, "y": 150},
                {"id": "grafana", "type": "dashboard", "label": "Grafana", "x": 450, "y": 250},
                {"id": "alertmanager", "type": "alert", "label": "Alertmanager", "x": 650, "y": 50},
                {"id": "loki", "type": "logs", "label": "Loki", "x": 650, "y": 150},
                {"id": "pagerduty", "type": "notification", "label": "PagerDuty", "x": 650, "y": 250},
            ],
            "edges": [
                {"id": "e1", "source": "svc-a", "target": "otel-col"},
                {"id": "e2", "source": "svc-b", "target": "otel-col"},
                {"id": "e3", "source": "svc-c", "target": "otel-col"},
                {"id": "e4", "source": "otel-col", "target": "prom"},
                {"id": "e5", "source": "otel-col", "target": "jaeger"},
                {"id": "e6", "source": "otel-col", "target": "loki"},
                {"id": "e7", "source": "prom", "target": "grafana"},
                {"id": "e8", "source": "jaeger", "target": "grafana"},
                {"id": "e9", "source": "prom", "target": "alertmanager"},
                {"id": "e10", "source": "alertmanager", "target": "pagerduty"},
            ],
        }),
        "template_id": "odt-soc-baseline-splunk",
    },
    {
        "id": _DESIGN_IDS["odc-design-001"],
        "name": "Cloud-Native SIEM (Sentinel)",
        "description": "Microsoft Sentinel SIEM with Azure Monitor, Log Analytics, and Microsoft Defender for Cloud integration for cloud-native security operations.",
        "graph_json": json.dumps({
            "nodes": [
                {"id": "azure-ad", "type": "identity", "label": "Azure AD", "x": 50, "y": 50},
                {"id": "defender", "type": "security", "label": "Defender for Cloud", "x": 50, "y": 150},
                {"id": "monitor", "type": "monitor", "label": "Azure Monitor", "x": 250, "y": 100},
                {"id": "log-analytics", "type": "logs", "label": "Log Analytics", "x": 450, "y": 100},
                {"id": "sentinel", "type": "siem", "label": "Microsoft Sentinel", "x": 650, "y": 100},
                {"id": "playbooks", "type": "automation", "label": "Sentinel Playbooks", "x": 650, "y": 200},
                {"id": "soar", "type": "soar", "label": "SOAR", "x": 850, "y": 100},
                {"id": "threat-intel", "type": "intel", "label": "Threat Intel", "x": 450, "y": 200},
                {"id": "snow", "type": "ticketing", "label": "ServiceNow", "x": 850, "y": 200},
            ],
            "edges": [
                {"id": "e1", "source": "azure-ad", "target": "monitor"},
                {"id": "e2", "source": "defender", "target": "monitor"},
                {"id": "e3", "source": "monitor", "target": "log-analytics"},
                {"id": "e4", "source": "log-analytics", "target": "sentinel"},
                {"id": "e5", "source": "sentinel", "target": "playbooks"},
                {"id": "e6", "source": "playbooks", "target": "soar"},
                {"id": "e7", "source": "sentinel", "target": "threat-intel"},
                {"id": "e8", "source": "soar", "target": "snow"},
            ],
        }),
        "template_id": "odt-cloud-native-sentinel",
    },
    {
        "id": _DESIGN_IDS["odc-design-002"],
        "name": "Network Security Monitoring (Suricata + ELK)",
        "description": "Network security monitoring with Suricata IDS, Zeek, and Elastic Stack for deep packet inspection and network forensics.",
        "graph_json": json.dumps({
            "nodes": [
                {"id": "tap", "type": "network", "label": "Network TAP", "x": 50, "y": 100},
                {"id": "suricata", "type": "ids", "label": "Suricata", "x": 250, "y": 50},
                {"id": "zeek", "type": "ids", "label": "Zeek", "x": 250, "y": 150},
                {"id": "filebeat", "type": "shipper", "label": "Filebeat", "x": 450, "y": 100},
                {"id": "logstash", "type": "processor", "label": "Logstash", "x": 650, "y": 100},
                {"id": "elasticsearch", "type": "store", "label": "Elasticsearch", "x": 850, "y": 50},
                {"id": "kibana", "type": "dashboard", "label": "Kibana", "x": 850, "y": 150},
                {"id": "thehive", "type": "case", "label": "TheHive", "x": 650, "y": 200},
                {"id": "misp", "type": "intel", "label": "MISP", "x": 450, "y": 200},
            ],
            "edges": [
                {"id": "e1", "source": "tap", "target": "suricata"},
                {"id": "e2", "source": "tap", "target": "zeek"},
                {"id": "e3", "source": "suricata", "target": "filebeat"},
                {"id": "e4", "source": "zeek", "target": "filebeat"},
                {"id": "e5", "source": "filebeat", "target": "logstash"},
                {"id": "e6", "source": "logstash", "target": "elasticsearch"},
                {"id": "e7", "source": "elasticsearch", "target": "kibana"},
                {"id": "e8", "source": "suricata", "target": "thehive"},
                {"id": "e9", "source": "misp", "target": "thehive"},
            ],
        }),
        "template_id": "odt-nsm-suricata-elk",
    },
    {
        "id": _DESIGN_IDS["odc-design-003"],
        "name": "Full-Stack Observability (Datadog)",
        "description": "Datadog-based observability platform with APM, infrastructure monitoring, log management, and synthetic testing for cloud-native applications.",
        "graph_json": json.dumps({
            "nodes": [
                {"id": "apps", "type": "service", "label": "Applications", "x": 50, "y": 100},
                {"id": "dd-agent", "type": "agent", "label": "Datadog Agent", "x": 250, "y": 100},
                {"id": "dd-apm", "type": "apm", "label": "Datadog APM", "x": 450, "y": 50},
                {"id": "dd-infra", "type": "infra", "label": "Infra Monitoring", "x": 450, "y": 100},
                {"id": "dd-logs", "type": "logs", "label": "Log Management", "x": 450, "y": 150},
                {"id": "dd-synthetic", "type": "test", "label": "Synthetic Tests", "x": 450, "y": 200},
                {"id": "dd-dashboard", "type": "dashboard", "label": "Dashboards", "x": 650, "y": 100},
                {"id": "dd-alerts", "type": "alert", "label": "Alerting", "x": 650, "y": 50},
                {"id": "slack", "type": "notification", "label": "Slack", "x": 850, "y": 50},
                {"id": "pagerduty", "type": "notification", "label": "PagerDuty", "x": 850, "y": 100},
            ],
            "edges": [
                {"id": "e1", "source": "apps", "target": "dd-agent"},
                {"id": "e2", "source": "dd-agent", "target": "dd-apm"},
                {"id": "e3", "source": "dd-agent", "target": "dd-infra"},
                {"id": "e4", "source": "dd-agent", "target": "dd-logs"},
                {"id": "e5", "source": "dd-apm", "target": "dd-dashboard"},
                {"id": "e6", "source": "dd-infra", "target": "dd-dashboard"},
                {"id": "e7", "source": "dd-logs", "target": "dd-dashboard"},
                {"id": "e8", "source": "dd-alerts", "target": "slack"},
                {"id": "e9", "source": "dd-alerts", "target": "pagerduty"},
                {"id": "e10", "source": "dd-synthetic", "target": "dd-alerts"},
            ],
        }),
        "template_id": "odt-fullstack-datadog",
    },
    {
        "id": _DESIGN_IDS["odc-design-004"],
        "name": "DoD IL4 Compliance Stack",
        "description": "Comprehensive DoD IL4 observability stack with STIG compliance monitoring, NIST control mapping, MITRE ATT&CK detection baseline, and FIPS 140-2 encryption for all data at rest and in transit.",
        "graph_json": json.dumps({
            "nodes": [
                {"id": "endpoints", "type": "endpoint", "label": "Endpoints (Windows/Linux)", "x": 50, "y": 100},
                {"id": "wef", "type": "forwarder", "label": "Windows Event Forwarding", "x": 250, "y": 50},
                {"id": "syslog", "type": "forwarder", "label": "Syslog-NG", "x": 250, "y": 150},
                {"id": "splunk", "type": "siem", "label": "Splunk (GovCloud)", "x": 450, "y": 100},
                {"id": "stig", "type": "compliance", "label": "STIG Scanner", "x": 450, "y": 200},
                {"id": "mitre", "type": "framework", "label": "MITRE ATT&CK", "x": 650, "y": 50},
                {"id": "nist", "type": "framework", "label": "NIST 800-53", "x": 650, "y": 150},
                {"id": "soar", "type": "soar", "label": "SOAR Playbook", "x": 650, "y": 250},
                {"id": "emass", "type": "reporting", "label": "eMASS", "x": 850, "y": 100},
                {"id": "xacta", "type": "reporting", "label": "Xacta", "x": 850, "y": 200},
            ],
            "edges": [
                {"id": "e1", "source": "endpoints", "target": "wef"},
                {"id": "e2", "source": "endpoints", "target": "syslog"},
                {"id": "e3", "source": "wef", "target": "splunk"},
                {"id": "e4", "source": "syslog", "target": "splunk"},
                {"id": "e5", "source": "splunk", "target": "stig"},
                {"id": "e6", "source": "splunk", "target": "mitre"},
                {"id": "e7", "source": "splunk", "target": "nist"},
                {"id": "e8", "source": "splunk", "target": "soar"},
                {"id": "e9", "source": "stig", "target": "emass"},
                {"id": "e10", "source": "nist", "target": "xacta"},
            ],
        }),
        "template_id": "odt-dod-il4-compliance",
    },
]

_ASSESSMENTS = []
for i, design in enumerate(_OBSERVABILITY_DESIGNS):
    score = random.uniform(75.0, 98.0)
    _ASSESSMENTS.append({
        "id": _uid(),
        "design_id": design["id"],
        "assessment_type": random.choice(["compliance", "security", "coverage", "performance"]),
        "findings_json": json.dumps([
            {"severity": "high", "check": "log_retention", "finding": "Log retention policy < 1 year"},
            {"severity": "medium", "check": "alert_coverage", "finding": "Missing alert for MITRE T1078"},
            {"severity": "low", "check": "dashboard_freshness", "finding": "Dashboard refresh interval > 5 min"},
        ]),
        "score": round(score, 1),
        "grade": "A" if score >= 90 else "B" if score >= 80 else "C" if score >= 70 else "D",
    })

_MITRE_TECHNIQUES = [
    ("T1059", "Command and Scripting Interpreter", "Execution", "sigma: process_creation AND (powershell OR cmd OR bash)"),
    ("T1078", "Valid Accounts", "Initial Access", "sigma: authentication AND (anomaly OR brute_force)"),
    ("T1071", "Application Layer Protocol", "Command and Control", "sigma: network_connection AND (http OR https OR dns)"),
    ("T1053", "Scheduled Task/Job", "Execution", "sigma: process_creation AND (schtasks OR cron OR at)"),
    ("T1021", "Remote Services", "Lateral Movement", "sigma: network_connection AND (rdp OR ssh OR smb)"),
    ("T1055", "Process Injection", "Defense Evasion", "sigma: process_access AND (injection OR hollowing)"),
    ("T1003", "OS Credential Dumping", "Credential Access", "sigma: process_access AND (lsass OR sam OR ntds)"),
    ("T1110", "Brute Force", "Credential Access", "sigma: authentication AND (failed_attempts > 5)"),
    ("T1486", "Data Encrypted for Impact", "Impact", "sigma: process_creation AND (encryption OR ransomware)"),
    ("T1562", "Impair Defenses", "Defense Evasion", "sigma: process_creation AND (disable_defender OR stop_service)"),
]

_TTP_COVERAGE = []
for i in range(12):
    technique = _MITRE_TECHNIQUES[i % len(_MITRE_TECHNIQUES)]
    state = random.choice(["full", "partial", "none"])
    _TTP_COVERAGE.append({
        "id": _uid(),
        "ttp_id": technique[0],
        "design_id": _OBSERVABILITY_DESIGNS[i % len(_OBSERVABILITY_DESIGNS)]["id"],
        "state": state,
        "sigma_match": 1 if state in ("full", "partial") else 0,
        "baseline_match": 1 if state == "full" else 0,
        "detail": json.dumps({"coverage": state, "rules": random.randint(0, 5)}),
    })

_GAP_SCORES = []
for i, design in enumerate(_OBSERVABILITY_DESIGNS):
    total = 188
    covered = random.randint(120, 170)
    partial = random.randint(10, 30)
    gap = total - covered - partial
    score = round(gap / total * 100, 1)
    _GAP_SCORES.append({
        "id": _uid(),
        "design_id": design["id"],
        "total_techniques": total,
        "covered_count": covered,
        "partial_count": partial,
        "gap_count": gap,
        "overall_gap_score": score,
        "by_tactic": json.dumps({
            "Initial Access": random.randint(5, 15),
            "Execution": random.randint(10, 20),
            "Persistence": random.randint(8, 18),
            "Defense Evasion": random.randint(10, 25),
            "Credential Access": random.randint(5, 15),
            "Discovery": random.randint(8, 20),
            "Lateral Movement": random.randint(5, 15),
            "Collection": random.randint(3, 12),
            "Command and Control": random.randint(5, 15),
            "Exfiltration": random.randint(3, 10),
            "Impact": random.randint(2, 8),
        }),
        "assessed_at": _ts(i * 6),
    })

_TECHNIQUE_COVERAGE = []
for i in range(15):
    technique = _MITRE_TECHNIQUES[i % len(_MITRE_TECHNIQUES)]
    state = random.choice(["covered", "partial", "gap"])
    _TECHNIQUE_COVERAGE.append({
        "id": _uid(),
        "design_id": _OBSERVABILITY_DESIGNS[i % len(_OBSERVABILITY_DESIGNS)]["id"],
        "technique_id": technique[0],
        "coverage_state": state,
        "signal_sources_present": json.dumps(["splunk", "sigma", "sysmon"] if state in ("covered", "partial") else []),
        "signal_sources_missing": json.dumps(["zeek", "suricata"] if state == "gap" else []),
        "gap_score": round(random.uniform(0.0, 1.0), 2),
        "assessed_at": _ts(i * 3),
    })

_OTEL_EVENTS = []
for i in range(20):
    _OTEL_EVENTS.append({
        "design_id": _OBSERVABILITY_DESIGNS[i % len(_OBSERVABILITY_DESIGNS)]["id"],
        "trace_id": f"trace-{i+1:04d}",
        "span_id": f"span-{i+1:04d}",
        "event_name": random.choice(["http.request", "db.query", "cache.hit", "auth.verify", "file.read"]),
        "technique_id": _MITRE_TECHNIQUES[i % len(_MITRE_TECHNIQUES)][0] if random.random() > 0.5 else "",
        "signal_source": random.choice(["otel-collector", "jaeger-agent", "datadog-agent"]),
        "attributes": json.dumps({"service": f"svc-{i % 5 + 1}", "duration_ms": random.randint(10, 500)}),
        "received_at": _ts(i * 0.5),
    })

_SDC_VERIFICATIONS = []
for i, design in enumerate(_OBSERVABILITY_DESIGNS):
    ttp_list = [t[0] for t in _MITRE_TECHNIQUES[:5]]
    covered = [t for t in ttp_list if random.random() > 0.3]
    partial = [t for t in ttp_list if t not in covered and random.random() > 0.5]
    gap = [t for t in ttp_list if t not in covered and t not in partial]
    pct = round(len(covered) / len(ttp_list) * 100, 1)
    _SDC_VERIFICATIONS.append({
        "id": _uid(),
        "design_id": design["id"],
        "ttp_list": json.dumps(ttp_list),
        "covered_ttps": json.dumps(covered),
        "partial_ttps": json.dumps(partial),
        "gap_ttps": json.dumps(gap),
        "coverage_pct": pct,
        "verified_at": _ts(i * 8),
    })


def seed_observability_designs(conn) -> int:
    sql = """INSERT OR IGNORE INTO observability_designs (
        id, name, description, graph_json, template_id, classification, created_at, updated_at
    ) VALUES (?,?,?,?,?,?,?,?)"""
    count = 0
    for row in _OBSERVABILITY_DESIGNS:
        _safe_execute(conn, sql, (
            row["id"], row["name"], row["description"], row["graph_json"],
            row["template_id"], "CUI", _ts(count * 3), _ts(count * 3),
        ))
        count += 1
    return count


def seed_assessments(conn) -> int:
    sql = """INSERT OR IGNORE INTO od_assessments (
        id, design_id, assessment_type, findings_json, score, grade, created_at
    ) VALUES (?,?,?,?,?,?,?)"""
    count = 0
    for row in _ASSESSMENTS:
        _safe_execute(conn, sql, (
            row["id"], row["design_id"], row["assessment_type"],
            row["findings_json"], row["score"], row["grade"], _ts(count * 4),
        ))
        count += 1
    return count


def seed_audit(conn) -> int:
    # The column is actor. `user` is also a reserved word in PostgreSQL, so this
    # statement failed to parse there and od_audit has stayed empty (swp-scan-01).
    sql = """INSERT OR IGNORE INTO od_audit (
        design_id, actor, action, detail, classification, created_at
    ) VALUES (?,?,?,?,?,?)"""
    count = 0
    actions = [
        ("design_created", "Observability design created"),
        ("assessment_run", "Coverage assessment executed"),
        ("ttp_updated", "MITRE technique coverage updated"),
        ("otel_event", "OTel event batch ingested"),
        ("control_modified", "Detection rule modified"),
    ]
    for i in range(10):
        design_id = _OBSERVABILITY_DESIGNS[i % len(_OBSERVABILITY_DESIGNS)]["id"]
        action, detail = actions[i % len(actions)]
        _safe_execute(conn, sql, (
            design_id, random.choice(["sre-smith", "soc-jones", "system"]),
            action, detail, "CUI // SP-CTI", _ts(i * 2),
        ))
        count += 1
    return count


def seed_versions(conn) -> int:
    sql = """INSERT OR IGNORE INTO od_versions (
        id, design_id, version_number, graph_json, change_summary, user_id, created_at
    ) VALUES (?,?,?,?,?,?,?)"""
    count = 0
    for i in range(8):
        design_id = _OBSERVABILITY_DESIGNS[i % len(_OBSERVABILITY_DESIGNS)]["id"]
        _safe_execute(conn, sql, (
            _uid(), design_id, i + 1, _OBSERVABILITY_DESIGNS[i % len(_OBSERVABILITY_DESIGNS)]["graph_json"],
            f"Version {i+1}: updated detection pipeline", "system", _ts(i * 5),
        ))
        count += 1
    return count


def seed_collab_sessions(conn) -> int:
    sql = """INSERT OR IGNORE INTO od_collab_sessions (
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
        design_id = _OBSERVABILITY_DESIGNS[i % len(_OBSERVABILITY_DESIGNS)]["id"]
        user_id, user_name, color = users[i]
        _safe_execute(conn, sql, (
            _uid(), design_id, user_id, user_name, color,
            _ts(i * 6), _ts(i * 6 + 2), 1 if random.random() > 0.3 else 0,
        ))
        count += 1
    return count


def seed_ttp_coverage(conn) -> int:
    sql = """INSERT OR IGNORE INTO od_ttp_coverage (
        id, ttp_id, design_id, state, sigma_match, baseline_match, detail, verified_at
    ) VALUES (?,?,?,?,?,?,?,?)"""
    count = 0
    for row in _TTP_COVERAGE:
        _safe_execute(conn, sql, (
            row["id"], row["ttp_id"], row["design_id"], row["state"],
            row["sigma_match"], row["baseline_match"], row["detail"], _ts(count * 2),
        ))
        count += 1
    return count


def seed_gap_scores(conn) -> int:
    sql = """INSERT OR IGNORE INTO odc_gap_scores (
        id, design_id, total_techniques, covered_count, partial_count, gap_count,
        overall_gap_score, by_tactic, assessed_at
    ) VALUES (?,?,?,?,?,?,?,?,?)"""
    count = 0
    for row in _GAP_SCORES:
        _safe_execute(conn, sql, (
            row["id"], row["design_id"], row["total_techniques"], row["covered_count"],
            row["partial_count"], row["gap_count"], row["overall_gap_score"],
            row["by_tactic"], row["assessed_at"],
        ))
        count += 1
    return count


def seed_technique_coverage(conn) -> int:
    sql = """INSERT OR IGNORE INTO odc_technique_coverage (
        id, design_id, technique_id, coverage_state, signal_sources_present,
        signal_sources_missing, gap_score, assessed_at
    ) VALUES (?,?,?,?,?,?,?,?)"""
    count = 0
    for row in _TECHNIQUE_COVERAGE:
        _safe_execute(conn, sql, (
            row["id"], row["design_id"], row["technique_id"], row["coverage_state"],
            row["signal_sources_present"], row["signal_sources_missing"],
            row["gap_score"], row["assessed_at"],
        ))
        count += 1
    return count


def seed_otel_events(conn) -> int:
    sql = """INSERT OR IGNORE INTO odc_otel_events (
        design_id, trace_id, span_id, event_name, technique_id, signal_source,
        attributes, received_at
    ) VALUES (?,?,?,?,?,?,?,?)"""
    count = 0
    for row in _OTEL_EVENTS:
        _safe_execute(conn, sql, (
            row["design_id"], row["trace_id"], row["span_id"], row["event_name"],
            row["technique_id"], row["signal_source"], row["attributes"], row["received_at"],
        ))
        count += 1
    return count


def seed_sdc_verifications(conn) -> int:
    sql = """INSERT OR IGNORE INTO odc_sdc_verifications (
        id, design_id, ttp_list, covered_ttps, partial_ttps, gap_ttps, coverage_pct, verified_at
    ) VALUES (?,?,?,?,?,?,?,?)"""
    count = 0
    for row in _SDC_VERIFICATIONS:
        _safe_execute(conn, sql, (
            row["id"], row["design_id"], row["ttp_list"], row["covered_ttps"],
            row["partial_ttps"], row["gap_ttps"], row["coverage_pct"], row["verified_at"],
        ))
        count += 1
    return count


def seed_mitre_techniques(conn) -> int:
    sql = """INSERT OR IGNORE INTO odc_mitre_techniques (
        id, technique_id, name, tactic, sigma_template, ingested_at
    ) VALUES (?,?,?,?,?,?)"""
    count = 0
    for row in _MITRE_TECHNIQUES:
        _safe_execute(conn, sql, (
            _uid(), row[0], row[1], row[2], row[3], _ts(count * 1),
        ))
        count += 1
    return count


def verify(conn) -> dict:
    result = {}
    for tbl in (
        "observability_designs", "od_assessments", "od_audit", "od_versions",
        "od_collab_sessions", "od_ttp_coverage", "odc_gap_scores",
        "odc_technique_coverage", "odc_otel_events", "odc_sdc_verifications",
        "odc_mitre_techniques",
    ):
        try:
            row = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()
            result[tbl] = row[0]
        except Exception as exc:
            result[tbl] = f"error: {exc}"
    return result


def main():
    parser = argparse.ArgumentParser(description="ODC Demo Seed")
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
            "observability_designs": seed_observability_designs(conn),
            "od_assessments": seed_assessments(conn),
            "od_audit": seed_audit(conn),
            "od_versions": seed_versions(conn),
            "od_collab_sessions": seed_collab_sessions(conn),
            "od_ttp_coverage": seed_ttp_coverage(conn),
            "odc_gap_scores": seed_gap_scores(conn),
            "odc_technique_coverage": seed_technique_coverage(conn),
            "odc_otel_events": seed_otel_events(conn),
            "odc_sdc_verifications": seed_sdc_verifications(conn),
            "odc_mitre_techniques": seed_mitre_techniques(conn),
        }
        conn.commit()

        if args.json:
            print(json.dumps({"success": True, "seeded": counts, "verify": verify(conn)}, indent=2))
        else:
            print(f"[seed_odc] Seeded {counts}")
            print(f"[seed_odc] Verify: {verify(conn)}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
