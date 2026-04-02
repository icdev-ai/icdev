# CUI // SP-CTI — ICDEV Observability Design Canvas Constants
"""Observability Design Canvas module-level constants.

Object palettes, compliance rules, and assessment frameworks for visual
observability stack design — logs, metrics, traces, SIEM, SOAR.
"""

OBSERVABILITY_OBJECTS = {
    "sources": [
        {"type": "src-app-log", "label": "Application Log", "icon": "ALG", "desc": "Application-level log source (stdout/file/syslog)"},
        {"type": "src-os-log", "label": "OS/System Log", "icon": "SLG", "desc": "Operating system logs (Windows Event Log/journald/syslog)"},
        {"type": "src-network-log", "label": "Network Log", "icon": "NLG", "desc": "Network device logs (firewall/router/switch syslog)"},
        {"type": "src-cloud-log", "label": "Cloud Audit Log", "icon": "CLG", "desc": "Cloud provider audit trail (CloudTrail/Activity Log/Audit Log)"},
        {"type": "src-container-log", "label": "Container Log", "icon": "KLG", "desc": "Container/K8s logs (stdout/fluentd/daemonset)"},
        {"type": "src-db-audit", "label": "DB Audit Log", "icon": "DAL", "desc": "Database audit log (pgaudit/SQL Server Audit/Oracle Audit)"},
        {"type": "src-wef", "label": "Windows Event Forwarding", "icon": "WEF", "desc": "Windows Event Forwarding (WEF) collector"},
        {"type": "src-flow", "label": "Network Flow", "icon": "FLW", "desc": "NetFlow/sFlow/IPFIX flow data"},
        {"type": "src-pcap", "label": "Packet Capture", "icon": "PCP", "desc": "Full packet capture (Zeek/Suricata/Arkime)"},
        {"type": "src-endpoint", "label": "EDR Telemetry", "icon": "EDR", "desc": "Endpoint detection and response telemetry (CrowdStrike/Defender/Carbon Black)"},
        {"type": "src-metric", "label": "Metric Source", "icon": "MET", "desc": "Infrastructure/app metrics (Prometheus exporter/StatsD/CloudWatch)"},
        {"type": "src-trace", "label": "Trace Source", "icon": "TRC", "desc": "Distributed trace spans (OpenTelemetry/Jaeger/X-Ray instrumentation)"},
        {"type": "src-vulnerability", "label": "Vuln Scanner", "icon": "VSC", "desc": "Vulnerability scan results (Nessus/ACAS/Qualys/Tenable)"},
        {"type": "src-iam", "label": "IAM/IdP Log", "icon": "IAM", "desc": "Identity provider logs (Okta/Azure AD/Ping/LDAP audit)"},
    ],
    "collectors": [
        {"type": "col-fluentd", "label": "Fluentd/Fluent Bit", "icon": "FLD", "desc": "Fluentd/Fluent Bit log collector and forwarder"},
        {"type": "col-filebeat", "label": "Filebeat", "icon": "FBT", "desc": "Elastic Filebeat lightweight log shipper"},
        {"type": "col-otel", "label": "OTel Collector", "icon": "OTL", "desc": "OpenTelemetry Collector — vendor-neutral telemetry pipeline"},
        {"type": "col-logstash", "label": "Logstash", "icon": "LSH", "desc": "Logstash — log processing and enrichment pipeline"},
        {"type": "col-syslog-ng", "label": "Syslog-NG", "icon": "SNG", "desc": "Syslog-NG — high-performance log collection and routing"},
        {"type": "col-cribl", "label": "Cribl Stream", "icon": "CRB", "desc": "Cribl Stream — observability pipeline for routing, reduction, enrichment"},
        {"type": "col-kafka", "label": "Kafka/EventHub", "icon": "KFK", "desc": "Kafka/Event Hub as log buffer and streaming backbone"},
        {"type": "col-s3", "label": "Log Archive (S3)", "icon": "S3A", "desc": "Long-term log archive in object storage (S3/ADLS/GCS)"},
    ],
    "platforms": [
        {"type": "plt-splunk", "label": "Splunk", "icon": "SPK", "desc": "Splunk Enterprise/Cloud — SIEM + search + analytics"},
        {"type": "plt-elastic", "label": "Elastic/ELK", "icon": "ELK", "desc": "Elasticsearch + Kibana (ELK) — log analytics and visualization"},
        {"type": "plt-sentinel", "label": "Microsoft Sentinel", "icon": "STN", "desc": "Microsoft Sentinel — cloud-native SIEM + SOAR"},
        {"type": "plt-chronicle", "label": "Google Chronicle", "icon": "CHR", "desc": "Google Chronicle — security analytics on petabyte-scale"},
        {"type": "plt-qradar", "label": "IBM QRadar", "icon": "QRD", "desc": "IBM QRadar — SIEM with offense management"},
        {"type": "plt-prometheus", "label": "Prometheus", "icon": "PRM", "desc": "Prometheus — metric collection, storage, and alerting"},
        {"type": "plt-grafana", "label": "Grafana", "icon": "GRF", "desc": "Grafana — dashboards and visualization for metrics/logs/traces"},
        {"type": "plt-datadog", "label": "Datadog", "icon": "DDG", "desc": "Datadog — cloud monitoring and observability platform"},
        {"type": "plt-jaeger", "label": "Jaeger", "icon": "JGR", "desc": "Jaeger — distributed tracing backend and UI"},
        {"type": "plt-suricata", "label": "Suricata/Zeek", "icon": "SUR", "desc": "Suricata/Zeek — network security monitoring and IDS"},
        {"type": "plt-velociraptor", "label": "Velociraptor", "icon": "VLR", "desc": "Velociraptor — endpoint visibility and digital forensics"},
        {"type": "plt-thehive", "label": "TheHive", "icon": "THV", "desc": "TheHive — incident response case management"},
    ],
    "automation": [
        {"type": "auto-soar", "label": "SOAR Playbook", "icon": "SOA", "desc": "SOAR automation playbook (Cortex XSOAR/Splunk SOAR/Sentinel Playbooks)"},
        {"type": "auto-alert-rule", "label": "Alert Rule", "icon": "ALR", "desc": "Detection/correlation rule — triggers on log pattern or metric threshold"},
        {"type": "auto-enrichment", "label": "Enrichment", "icon": "ENR", "desc": "Threat intel enrichment (MISP/OTX/VirusTotal lookup)"},
        {"type": "auto-ticket", "label": "Ticket System", "icon": "TKT", "desc": "Incident ticket system (ServiceNow/Jira/ITSM)"},
        {"type": "auto-notification", "label": "Notification", "icon": "NTF", "desc": "Alert notification channel (PagerDuty/Slack/Teams/email)"},
        {"type": "auto-runbook", "label": "Runbook", "icon": "RNB", "desc": "Automated or manual runbook for incident response"},
    ],
    "compliance": [
        {"type": "cmp-log-policy", "label": "Log Retention Policy", "icon": "LRP", "desc": "Log retention and archival policy (NARA/DoD 5015.02)"},
        {"type": "cmp-audit-report", "label": "Audit Report", "icon": "RPT", "desc": "Compliance audit report — evidence of monitoring effectiveness"},
        {"type": "cmp-baseline", "label": "Detection Baseline", "icon": "BSL", "desc": "MITRE ATT&CK detection coverage baseline"},
    ],
}

# Observability Compliance Rules
OBSERVABILITY_COMPLIANCE_RULES = [
    {"id": "ODC-LOG-001", "title": "All sources connected to collector",
     "severity": "CAT2", "category": "collection",
     "description": "Every log source must be connected to at least one collector — unconnected sources create blind spots (NIST AU-2)."},

    {"id": "ODC-LOG-002", "title": "Collectors forward to SIEM",
     "severity": "CAT1", "category": "analysis",
     "description": "All collectors must forward to a SIEM/analytics platform for correlation and detection (NIST AU-6, SI-4)."},

    {"id": "ODC-LOG-003", "title": "Log archive for retention compliance",
     "severity": "CAT2", "category": "retention",
     "description": "At least one S3/archive destination for long-term log retention (NIST AU-11 — 1yr online, 7yr archive for DoD)."},

    {"id": "ODC-LOG-004", "title": "OS and network logs collected",
     "severity": "CAT1", "category": "coverage",
     "description": "Design must include OS/system log and network log sources at minimum (NIST AU-2, AU-12)."},

    {"id": "ODC-LOG-005", "title": "Cloud audit logs collected",
     "severity": "CAT1", "category": "coverage",
     "description": "If cloud services are in scope, cloud audit log source (CloudTrail/Activity Log) must be present (NIST AU-2, AU-12)."},

    {"id": "ODC-DET-001", "title": "Alert rules defined",
     "severity": "CAT2", "category": "detection",
     "description": "Design must include at least one alert rule — monitoring without alerting is passive-only (NIST SI-4(5))."},

    {"id": "ODC-DET-002", "title": "SOAR or runbook for automated response",
     "severity": "CAT2", "category": "response",
     "description": "Alert rules should connect to SOAR playbook or runbook for incident response automation (NIST IR-4)."},

    {"id": "ODC-DET-003", "title": "MITRE ATT&CK detection baseline present",
     "severity": "CAT2", "category": "detection",
     "description": "Design should include a detection baseline node mapping coverage to MITRE ATT&CK techniques."},

    {"id": "ODC-RET-001", "title": "Log retention policy defined",
     "severity": "CAT1", "category": "retention",
     "description": "Design must include a log retention policy — DoD requires 1yr online + 7yr archive (NIST AU-11)."},

    {"id": "ODC-SEC-001", "title": "Log transport encrypted",
     "severity": "CAT1", "category": "transport",
     "description": "All collector-to-platform connections must use TLS — log data in transit contains sensitive information (NIST SC-8)."},

    {"id": "ODC-SEC-002", "title": "EDR telemetry collected",
     "severity": "CAT2", "category": "coverage",
     "description": "Design should include EDR telemetry source for endpoint visibility (NIST SI-4, CISA BOD 22-01)."},

    {"id": "ODC-SEC-003", "title": "IAM/IdP logs collected",
     "severity": "CAT1", "category": "coverage",
     "description": "Identity provider logs must be collected for authentication event monitoring (NIST AU-2(d), IA-2)."},

    {"id": "ODC-INT-001", "title": "Incident ticket system connected",
     "severity": "CAT2", "category": "response",
     "description": "SOAR/alert chain should connect to a ticket system for incident tracking and SLA compliance (NIST IR-5, IR-6)."},
]

# ── Recommended source types for coverage scoring ────────────────────────────
RECOMMENDED_SOURCE_TYPES = [
    "src-app-log", "src-os-log", "src-network-log", "src-cloud-log",
    "src-container-log", "src-db-audit", "src-endpoint", "src-iam",
    "src-metric", "src-trace",
]

# ── SIEM/analytics platform types ───────────────────────────────────────────
SIEM_PLATFORM_TYPES = {
    "plt-splunk", "plt-elastic", "plt-sentinel", "plt-chronicle",
    "plt-qradar", "plt-datadog",
}

# ── Collector types ──────────────────────────────────────────────────────────
COLLECTOR_TYPES = {
    "col-fluentd", "col-filebeat", "col-otel", "col-logstash",
    "col-syslog-ng", "col-cribl", "col-kafka", "col-s3",
}

# ── Source types ─────────────────────────────────────────────────────────────
SOURCE_TYPES = {obj["type"] for obj in OBSERVABILITY_OBJECTS["sources"]}

# ── Platform types ───────────────────────────────────────────────────────────
PLATFORM_TYPES = {obj["type"] for obj in OBSERVABILITY_OBJECTS["platforms"]}

# ── Automation types ─────────────────────────────────────────────────────────
AUTOMATION_TYPES = {obj["type"] for obj in OBSERVABILITY_OBJECTS["automation"]}

# ── Compliance types ─────────────────────────────────────────────────────────
COMPLIANCE_TYPES = {obj["type"] for obj in OBSERVABILITY_OBJECTS["compliance"]}

# ── Severity weights for scoring ─────────────────────────────────────────────
SEVERITY_WEIGHTS = {"CAT1": 10, "CAT2": 5, "CAT3": 2}
