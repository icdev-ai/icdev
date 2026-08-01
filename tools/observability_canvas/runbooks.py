# CUI // SP-CTI
"""Observability Design Canvas — Runbooks.

Operational runbooks for common observability incidents. Unlike SOPs (process
docs), runbooks are triggered by specific alert conditions and include
step-by-step remediation with expected outcomes and rollback actions.

Examples: alert storm triage, log pipeline failure, SIEM gap detected,
metric collection outage.
"""

import json
import uuid
from datetime import datetime, timezone


def _get_conn():
    from tools.observability_canvas.db.init_db import get_connection
    return get_connection()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _parse_json_field(value, default):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return default
    if value is None:
        return default
    return value


def _runbook_to_dict(row):
    if not row:
        return None
    d = dict(row)
    d["steps"] = _parse_json_field(d.get("steps"), [])
    d["nist_controls"] = _parse_json_field(d.get("nist_controls"), [])
    d["tags"] = _parse_json_field(d.get("tags"), [])
    return d


# ── Read ────────────────────────────────────────────────────────────────────────

def get_all_runbooks(category=None, severity=None):
    """Return all runbooks, optionally filtered by category and/or severity."""
    if category and severity:
        sql = "SELECT * FROM odc_runbooks WHERE category=%s AND severity=%s ORDER BY severity, title"
        params = [category, severity]
    elif category:
        sql = "SELECT * FROM odc_runbooks WHERE category=%s ORDER BY severity, title"
        params = [category]
    elif severity:
        sql = "SELECT * FROM odc_runbooks WHERE severity=%s ORDER BY title"
        params = [severity]
    else:
        sql = "SELECT * FROM odc_runbooks ORDER BY severity, title"
        params = []
    with _get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_runbook_to_dict(r) for r in rows]


def get_runbook_by_id(runbook_id):
    """Return a single runbook dict or None."""
    with _get_conn() as conn:
        row = conn.execute("SELECT * FROM odc_runbooks WHERE id=%s", (runbook_id,)).fetchone()
    return _runbook_to_dict(row)


# ── Write ───────────────────────────────────────────────────────────────────────

def create_runbook(data):
    """Create a new runbook. Returns the new runbook dict."""
    rb_id = str(uuid.uuid4())
    now = _now()
    steps = json.dumps(data.get("steps", []))
    nist_controls = json.dumps(data.get("nist_controls", []))
    tags = json.dumps(data.get("tags", []))
    with _get_conn() as conn:
        conn.execute(
            """INSERT INTO odc_runbooks
               (id, title, category, trigger_condition, severity, description,
                steps, nist_controls, tags, owner, estimated_duration_min,
                classification, created_at, updated_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                rb_id,
                data.get("title", "Untitled Runbook"),
                data.get("category", "general"),
                data.get("trigger_condition", ""),
                data.get("severity", "medium"),
                data.get("description", ""),
                steps,
                nist_controls,
                tags,
                data.get("owner", ""),
                int(data.get("estimated_duration_min", 30)),
                data.get("classification", "CUI"),
                now,
                now,
            ),
        )
        conn.commit()
    return get_runbook_by_id(rb_id)


def update_runbook(runbook_id, data):
    """Update an existing runbook. Returns updated dict or None."""
    existing = get_runbook_by_id(runbook_id)
    if not existing:
        return None
    now = _now()
    steps = json.dumps(data.get("steps", existing["steps"]))
    nist_controls = json.dumps(data.get("nist_controls", existing["nist_controls"]))
    tags = json.dumps(data.get("tags", existing["tags"]))
    with _get_conn() as conn:
        conn.execute(
            """UPDATE odc_runbooks SET
               title=%s, category=%s, trigger_condition=%s, severity=%s,
               description=%s, steps=%s, nist_controls=%s, tags=%s,
               owner=%s, estimated_duration_min=%s, classification=%s, updated_at=%s
               WHERE id=%s""",
            (
                data.get("title", existing["title"]),
                data.get("category", existing["category"]),
                data.get("trigger_condition", existing["trigger_condition"]),
                data.get("severity", existing["severity"]),
                data.get("description", existing["description"]),
                steps,
                nist_controls,
                tags,
                data.get("owner", existing["owner"]),
                int(data.get("estimated_duration_min", existing["estimated_duration_min"])),
                data.get("classification", existing["classification"]),
                now,
                runbook_id,
            ),
        )
        conn.commit()
    return get_runbook_by_id(runbook_id)


def delete_runbook(runbook_id):
    """Delete a runbook. Returns True if deleted, False if not found."""
    existing = get_runbook_by_id(runbook_id)
    if not existing:
        return False
    with _get_conn() as conn:
        conn.execute("DELETE FROM odc_runbooks WHERE id=%s", (runbook_id,))
        conn.commit()
    return True


def record_execution(runbook_id):
    """Increment execution_count and set last_executed_at. Returns updated dict or None."""
    existing = get_runbook_by_id(runbook_id)
    if not existing:
        return None
    now = _now()
    with _get_conn() as conn:
        conn.execute(
            "UPDATE odc_runbooks SET execution_count=execution_count+1, last_executed_at=%s, updated_at=%s WHERE id=%s",
            (now, now, runbook_id),
        )
        conn.commit()
    return get_runbook_by_id(runbook_id)


# ── Seeds ───────────────────────────────────────────────────────────────────────

_SEED_RUNBOOKS = [
    {
        "id": "rb-odc-alert-storm-triage",
        "title": "Alert Storm Triage",
        "category": "alerting",
        "trigger_condition": "SIEM or monitoring platform fires >50 alerts/minute for >5 minutes",
        "severity": "critical",
        "description": (
            "Triage procedure for alert storm conditions where a flood of correlated or "
            "duplicate alerts overwhelm on-call responders. Goal: identify root cause, "
            "silence noise, and restore normal alert fidelity within 30 minutes."
        ),
        "steps": [
            {
                "action": "Silence noisy alert rule(s) in the monitoring platform for 30 minutes to prevent responder fatigue.",
                "expected_outcome": "Alert flood stops; responders can focus on root-cause investigation.",
                "rollback_action": "Re-enable alert rule(s) after investigation.",
            },
            {
                "action": "Identify the alert rule(s) firing at highest volume. Check Splunk/Datadog/Grafana alert summary or run: SELECT alert_name, COUNT(*) FROM alerts WHERE created_at > NOW()-INTERVAL 10 MIN GROUP BY alert_name ORDER BY 2 DESC LIMIT 10.",
                "expected_outcome": "Top 3 noisy rules identified by name.",
                "rollback_action": "",
            },
            {
                "action": "Correlate noisy alerts with recent deployments, config pushes, or infrastructure changes in the last 2 hours (check CI/CD pipeline, Terraform apply logs, or change tickets).",
                "expected_outcome": "Root cause linked to a specific change event or infrastructure fault.",
                "rollback_action": "If deployment is the cause, trigger rollback procedure.",
            },
            {
                "action": "If storm is caused by a threshold misconfiguration, adjust the alert threshold or add a minimum-count filter. Document the change in the runbook log.",
                "expected_outcome": "Alert rule tuned; storm ceases without masking real signals.",
                "rollback_action": "Revert threshold to previous value if false negatives appear.",
            },
            {
                "action": "Resume alert rule(s). Monitor for 15 minutes. Verify alert rate returns to baseline (<5 alerts/minute).",
                "expected_outcome": "Alert fidelity restored; no genuine incidents masked.",
                "rollback_action": "",
            },
            {
                "action": "File post-incident review ticket. Document: trigger, blast radius (alerts fired, responder time lost), fix applied, and tuning recommendations.",
                "expected_outcome": "Ticket created in ServiceNow/JIRA with IR-category label.",
                "rollback_action": "",
            },
        ],
        "nist_controls": ["IR-4", "IR-5", "SI-4", "AU-6"],
        "tags": ["alerts", "triage", "noise-reduction", "on-call"],
        "owner": "SOC Team",
        "estimated_duration_min": 30,
        "classification": "CUI",
    },
    {
        "id": "rb-odc-log-pipeline-failure",
        "title": "Log Pipeline Failure",
        "category": "pipeline",
        "trigger_condition": "Log ingest rate drops >80% for >10 minutes OR log collector heartbeat missing",
        "severity": "high",
        "description": (
            "Recovery runbook for log pipeline failures affecting collectors (Fluentd, Filebeat, "
            "OTel, Logstash, Cribl), forwarders, or the SIEM/SIEM-adjacent platform ingest API. "
            "Ensures audit-trail continuity required by NIST AU-9 and AU-12."
        ),
        "steps": [
            {
                "action": "Verify the pipeline failure scope: check log ingest dashboard for drop in events/sec. Identify which collector(s) are affected (all vs. specific host/cluster).",
                "expected_outcome": "Affected collector(s) identified by name and host.",
                "rollback_action": "",
            },
            {
                "action": "SSH to collector host and check service status: sudo systemctl status fluentd (or filebeat/otelcol/logstash). Review last 100 lines of collector log: sudo journalctl -u fluentd -n 100 --no-pager.",
                "expected_outcome": "Root cause identified (crash, disk full, auth failure, network error, bad config).",
                "rollback_action": "If config change is the cause, revert to last known-good config from version control.",
            },
            {
                "action": "If disk is full: identify large log files consuming space (du -sh /var/log/* | sort -rh | head -10). Archive or rotate logs. Free minimum 20% disk space.",
                "expected_outcome": "Disk usage below 80%; collector can resume writing buffer.",
                "rollback_action": "",
            },
            {
                "action": "If network/auth error: verify SIEM HEC token or API key is valid and not expired. Re-test connectivity: curl -k -X POST https://<siem-host>:8088/services/collector/event -H 'Authorization: Splunk <token>' -d '{\"event\":\"test\"}'.",
                "expected_outcome": "200 OK response from SIEM ingest endpoint.",
                "rollback_action": "Rotate HEC token and update collector config.",
            },
            {
                "action": "Restart the collector service: sudo systemctl restart fluentd. Monitor ingest rate for 5 minutes on dashboard.",
                "expected_outcome": "Ingest rate recovers to >80% of baseline within 5 minutes.",
                "rollback_action": "If restart fails, escalate to infrastructure team and enable failover collector path.",
            },
            {
                "action": "Verify buffered/queued events are flushed (check collector buffer dir size: du -sh /var/lib/fluentd/buffer/). Confirm no log data loss gap in SIEM for the outage window.",
                "expected_outcome": "Buffer drains to 0; SIEM shows continuous log ingestion with no gap.",
                "rollback_action": "If data loss confirmed, open AU-12 deficiency finding and notify ISSO.",
            },
            {
                "action": "Update monitoring: add collector-specific heartbeat check if missing. Document outage duration and data gap (if any) in audit log.",
                "expected_outcome": "Heartbeat alert created; outage documented in POAM if data loss occurred.",
                "rollback_action": "",
            },
        ],
        "nist_controls": ["AU-4", "AU-9", "AU-12", "SI-7"],
        "tags": ["log-pipeline", "fluentd", "filebeat", "ingest", "audit-trail"],
        "owner": "Platform Engineering",
        "estimated_duration_min": 45,
        "classification": "CUI",
    },
    {
        "id": "rb-odc-siem-gap-detected",
        "title": "SIEM Gap Detected",
        "category": "siem",
        "trigger_condition": "Observability assessment score <60 OR MITRE ATT&CK coverage drops >15% OR critical log source missing for >1 hour",
        "severity": "high",
        "description": (
            "Remediation runbook for detected gaps in SIEM coverage — missing log sources, "
            "unmonitored ATT&CK techniques, or degraded detection coverage identified by the "
            "Observability Design Canvas assessment engine or a manual gap analysis."
        ),
        "steps": [
            {
                "action": "Run the ODC assessment on the affected design: navigate to /observability/canvas/<design_id>, click Assess, and export the gap report. Note which log sources are marked MISSING.",
                "expected_outcome": "Gap report generated listing missing sources, coverage score, and MITRE techniques with no detection.",
                "rollback_action": "",
            },
            {
                "action": "Prioritize gaps by MITRE ATT&CK tactic impact (Execution > Persistence > Exfiltration > Impact). Focus first on Critical/High severity gaps and any sources required by NIST AU-2 or FedRAMP.",
                "expected_outcome": "Prioritized gap list with owner assigned per gap item.",
                "rollback_action": "",
            },
            {
                "action": "For each missing log source: verify the source system is operational and logging (check application log file exists and is updating). If source is down, escalate to system owner.",
                "expected_outcome": "Log source confirmed operational or escalation ticket created.",
                "rollback_action": "",
            },
            {
                "action": "Deploy or reconfigure the appropriate collector (Filebeat/Fluentd/OTel) for the missing source. Use the ODC snippet library for pre-built collection patterns. Apply IaC (Ansible/Terraform) where available.",
                "expected_outcome": "Collector deployed and forwarding events to SIEM within 15 minutes.",
                "rollback_action": "Revert IaC apply if collector causes resource contention on source host.",
            },
            {
                "action": "Create or update SIEM detection rule for the newly ingested source. Baseline normal event volume and set alert threshold at 3× baseline for anomaly detection.",
                "expected_outcome": "Alert rule active; test event triggers alert within 2 minutes.",
                "rollback_action": "Disable rule if false positive rate exceeds 10 alerts/hour.",
            },
            {
                "action": "Re-run ODC assessment. Verify coverage score improvement. Update the design in Observability Canvas to reflect new log sources and detection coverage.",
                "expected_outcome": "Coverage score ≥70%; MITRE gap count reduced by count of remediated items.",
                "rollback_action": "",
            },
            {
                "action": "If gap was a NIST AU-2/AU-3 compliance finding, update POAM with remediation date and close the finding.",
                "expected_outcome": "POAM item closed or risk accepted with documented rationale.",
                "rollback_action": "",
            },
        ],
        "nist_controls": ["AU-2", "AU-3", "AU-6", "SI-4", "CA-7"],
        "tags": ["siem", "coverage-gap", "mitre", "detection", "compliance"],
        "owner": "Security Engineering",
        "estimated_duration_min": 120,
        "classification": "CUI",
    },
    {
        "id": "rb-odc-metric-collection-outage",
        "title": "Metric Collection Outage",
        "category": "metrics",
        "trigger_condition": "Prometheus scrape failure >5 targets OR Grafana dashboard shows No Data for >15 minutes OR OTel metric export errors >10%",
        "severity": "medium",
        "description": (
            "Recovery runbook for metric collection outages affecting Prometheus, OTel Collector, "
            "Datadog Agent, or Grafana datasource. Covers scrape failures, exporter crashes, "
            "remote write failures, and datasource connectivity issues."
        ),
        "steps": [
            {
                "action": "Identify which metrics are missing: check Grafana for panels showing 'No Data' or 'N/A'. List affected dashboards and metric names (e.g., node_cpu_seconds_total, container_memory_usage_bytes).",
                "expected_outcome": "List of affected metric names and source exporters identified.",
                "rollback_action": "",
            },
            {
                "action": "Check Prometheus targets page (http://<prometheus>:9090/targets) for DOWN or unhealthy targets. For OTel: check collector logs for export errors (sudo journalctl -u otelcol -n 50).",
                "expected_outcome": "Failing scrape targets or export errors identified with error message.",
                "rollback_action": "",
            },
            {
                "action": "If exporter is down: SSH to the target host and restart the exporter service (e.g., sudo systemctl restart node_exporter). Verify port is listening: ss -tlnp | grep 9100.",
                "expected_outcome": "Exporter service running; port 9100 (or configured port) is listening.",
                "rollback_action": "If exporter crash-loops, check for config errors: node_exporter --check-config.",
            },
            {
                "action": "If Prometheus remote write is failing: check remote write queue metrics (prometheus_remote_storage_failed_samples_total). Verify endpoint connectivity and auth token validity.",
                "expected_outcome": "Remote write queue draining; failed_samples counter stops increasing.",
                "rollback_action": "Increase remote_write timeout and max_samples_per_send if under load spike.",
            },
            {
                "action": "If OTel Collector is the bottleneck: check memory/CPU usage. Increase batch processor send_batch_size or reduce scrape_interval if resource-constrained. Restart collector if OOM: sudo systemctl restart otelcol.",
                "expected_outcome": "OTel Collector memory usage <80%; export success rate >95%.",
                "rollback_action": "Roll back OTel config to previous version if restart does not resolve.",
            },
            {
                "action": "Verify Grafana datasource: Settings → Data Sources → Test. Refresh affected dashboards. Confirm historical data shows the collection gap but current data is flowing.",
                "expected_outcome": "Grafana datasource test returns 'Data source is working'; dashboards show live data.",
                "rollback_action": "",
            },
            {
                "action": "Check alerting rules referencing affected metrics. Verify they re-evaluate correctly after recovery. Document the outage window and data gap in the monitoring runbook log.",
                "expected_outcome": "Alert rules firing correctly on synthetic test; outage documented.",
                "rollback_action": "",
            },
        ],
        "nist_controls": ["SI-4", "AU-6", "CM-6"],
        "tags": ["metrics", "prometheus", "otel", "grafana", "scrape-failure"],
        "owner": "Platform Engineering",
        "estimated_duration_min": 45,
        "classification": "CUI",
    },
]


def seed_runbooks():
    """Insert seed runbooks if they don't already exist."""
    added = 0
    with _get_conn() as conn:
        for rb in _SEED_RUNBOOKS:
            existing = conn.execute(
                "SELECT id FROM odc_runbooks WHERE id=%s", (rb["id"],)
            ).fetchone()
            if existing:
                continue
            now = _now()
            conn.execute(
                """INSERT INTO odc_runbooks
                   (id, title, category, trigger_condition, severity, description,
                    steps, nist_controls, tags, owner, estimated_duration_min,
                    classification, created_at, updated_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    rb["id"],
                    rb["title"],
                    rb["category"],
                    rb["trigger_condition"],
                    rb["severity"],
                    rb["description"],
                    json.dumps(rb["steps"]),
                    json.dumps(rb["nist_controls"]),
                    json.dumps(rb["tags"]),
                    rb["owner"],
                    rb["estimated_duration_min"],
                    rb["classification"],
                    now,
                    now,
                ),
            )
            added += 1
        if added:
            conn.commit()
    return added
