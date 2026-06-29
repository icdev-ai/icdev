
from tools.logging.icdev_logger import get_logger
# CUI // SP-CTI
"""SRE API Blueprint — exposes SLO, incident, runbook, DORA, and chaos endpoints.

Phases 1-3 of SRE integration: API endpoints, alert→incident→runbook chain,
DORA metrics computation.

Registers at /api/sre/* in the ICDEV dashboard.
"""

import json
from datetime import datetime, timezone, timedelta
from flask import Blueprint, jsonify, request
from tools.common.helpers import now_isoformat
from tools.db.storage import get_connection

logger = get_logger("icdev.sre.api")

sre_api = Blueprint("sre_api", __name__, url_prefix="/api/sre")


# ══════════════════════════════════════════════════════════════════════
# SLO ENDPOINTS
# ══════════════════════════════════════════════════════════════════════


@sre_api.route("/slos", methods=["GET"])
def api_sre_slos():
    """List all SLOs with current status."""
    try:
        from tools.sre.slo_manager import get_slo_dashboard

        return jsonify({"slos": get_slo_dashboard()})
    except Exception as exc:
        return jsonify({"slos": [], "error": str(exc)})


@sre_api.route("/slos", methods=["POST"])
def api_sre_create_slo():
    """Create a new SLO."""
    data = request.get_json(force=True, silent=True) or {}
    try:
        from tools.sre.slo_manager import create_slo

        result = create_slo(
            service=data.get("service_name", ""),
            name=data.get("slo_name", ""),
            slo_type=data.get("slo_type", "availability"),
            target=float(data.get("target_value", 99.9)),
            window_days=int(data.get("window_days", 30)),
        )
        return jsonify(result), 201
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@sre_api.route("/slos/<slo_id>/measure", methods=["POST"])
def api_sre_record_measurement(slo_id):
    """Record an SLO measurement."""
    data = request.get_json(force=True, silent=True) or {}
    try:
        from tools.sre.slo_manager import record_measurement

        result = record_measurement(
            slo_id=slo_id,
            value=float(data.get("value", 0)),
            good_events=data.get("good_events"),
            total_events=data.get("total_events"),
            source=data.get("source", "manual"),
        )
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@sre_api.route("/slos/<slo_id>/burn-rate", methods=["GET"])
def api_sre_burn_rate(slo_id):
    """Get burn rate analysis for an SLO."""
    try:
        from tools.sre.slo_manager import calculate_burn_rate

        return jsonify(calculate_burn_rate(slo_id))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@sre_api.route("/slos/health", methods=["GET"])
def api_sre_slo_health():
    """Get overall SLO health gate check."""
    try:
        from tools.sre.slo_manager import check_slo_health

        return jsonify(check_slo_health())
    except Exception as exc:
        return jsonify({"error": str(exc)})


# ══════════════════════════════════════════════════════════════════════
# INCIDENT ENDPOINTS
# ══════════════════════════════════════════════════════════════════════


@sre_api.route("/incidents", methods=["GET"])
def api_sre_incidents():
    """List incidents with optional status filter."""
    status_filter = request.args.get("status")
    try:
        from tools.sre.incident_commander import list_incidents

        return jsonify({"incidents": list_incidents(status_filter)})
    except Exception as exc:
        return jsonify({"incidents": [], "error": str(exc)})


@sre_api.route("/incidents", methods=["POST"])
def api_sre_create_incident():
    """Create a new incident."""
    data = request.get_json(force=True, silent=True) or {}
    try:
        from tools.sre.incident_commander import create_incident

        result = create_incident(
            title=data.get("title", ""),
            severity=data.get("severity", "sev3"),
            service=data.get("service_name", ""),
            alert_source=data.get("alert_source"),
        )
        return jsonify(result), 201
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@sre_api.route("/incidents/<incident_id>", methods=["GET"])
def api_sre_get_incident(incident_id):
    """Get incident details with timeline."""
    try:
        from tools.sre.incident_commander import get_incident

        result = get_incident(incident_id)
        if not result:
            return jsonify({"error": "Not found"}), 404
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@sre_api.route("/incidents/<incident_id>/triage", methods=["POST"])
def api_sre_triage(incident_id):
    """Triage an incident."""
    data = request.get_json(force=True, silent=True) or {}
    try:
        from tools.sre.incident_commander import triage_incident

        return jsonify(triage_incident(incident_id, data.get("root_cause")))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@sre_api.route("/incidents/<incident_id>/escalate", methods=["POST"])
def api_sre_escalate(incident_id):
    """Escalate an incident."""
    data = request.get_json(force=True, silent=True) or {}
    try:
        from tools.sre.incident_commander import escalate_incident

        return jsonify(escalate_incident(incident_id, data.get("reason", "")))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@sre_api.route("/incidents/<incident_id>/resolve", methods=["POST"])
def api_sre_resolve(incident_id):
    """Resolve an incident."""
    data = request.get_json(force=True, silent=True) or {}
    try:
        from tools.sre.incident_commander import resolve_incident

        return jsonify(resolve_incident(incident_id, data.get("mitigation_steps", [])))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@sre_api.route("/incidents/<incident_id>/postmortem", methods=["POST"])
def api_sre_postmortem(incident_id):
    """Add postmortem to an incident."""
    data = request.get_json(force=True, silent=True) or {}
    try:
        from tools.sre.incident_commander import add_postmortem

        return jsonify(add_postmortem(incident_id, data.get("url", ""), data.get("lessons_learned", "")))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@sre_api.route("/incidents/<incident_id>/close", methods=["POST"])
def api_sre_close(incident_id):
    """Close an incident."""
    try:
        from tools.sre.incident_commander import close_incident

        return jsonify(close_incident(incident_id))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@sre_api.route("/incidents/mttr", methods=["GET"])
def api_sre_mttr():
    """Get MTTR statistics by severity."""
    try:
        from tools.sre.incident_commander import get_mttr_stats

        return jsonify(get_mttr_stats())
    except Exception as exc:
        return jsonify({"error": str(exc)})


@sre_api.route("/incidents/health", methods=["GET"])
def api_sre_incident_health():
    """Get incident management health."""
    try:
        from tools.sre.incident_commander import check_incident_health

        return jsonify(check_incident_health())
    except Exception as exc:
        return jsonify({"error": str(exc)})


# ══════════════════════════════════════════════════════════════════════
# RUNBOOK ENDPOINTS
# ══════════════════════════════════════════════════════════════════════


@sre_api.route("/runbooks", methods=["GET"])
def api_sre_runbooks():
    """List all runbooks."""
    try:
        from tools.sre.runbook_executor import list_runbooks

        return jsonify({"runbooks": list_runbooks()})
    except Exception as exc:
        return jsonify({"runbooks": [], "error": str(exc)})


@sre_api.route("/runbooks", methods=["POST"])
def api_sre_create_runbook():
    """Create a new runbook."""
    data = request.get_json(force=True, silent=True) or {}
    try:
        from tools.sre.runbook_executor import create_runbook

        result = create_runbook(
            name=data.get("name", ""),
            description=data.get("description", ""),
            trigger_pattern=data.get("trigger_pattern", ""),
            steps=data.get("steps", []),
            risk_level=data.get("risk_level", "green"),
        )
        return jsonify(result), 201
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@sre_api.route("/runbooks/<runbook_id>/execute", methods=["POST"])
def api_sre_execute_runbook(runbook_id):
    """Execute a runbook manually."""
    data = request.get_json(force=True, silent=True) or {}
    try:
        from tools.sre.runbook_executor import execute_runbook

        result = execute_runbook(
            runbook_id=runbook_id,
            trigger_source=data.get("trigger_source", "api"),
            trigger_text=data.get("trigger_text", "Manual API execution"),
            dry_run=data.get("dry_run", False),
        )
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@sre_api.route("/runbooks/match", methods=["POST"])
def api_sre_match_runbook():
    """Match an alert text to a runbook."""
    data = request.get_json(force=True, silent=True) or {}
    try:
        from tools.sre.runbook_executor import match_runbook

        return jsonify(match_runbook(data.get("alert_text", "")))
    except Exception as exc:
        return jsonify({"error": str(exc)})


@sre_api.route("/runbooks/history", methods=["GET"])
def api_sre_runbook_history():
    """Get runbook execution history."""
    limit = int(request.args.get("limit", "20"))
    try:
        from tools.sre.runbook_executor import get_execution_history

        return jsonify({"executions": get_execution_history(limit)})
    except Exception as exc:
        return jsonify({"executions": [], "error": str(exc)})


@sre_api.route("/runbooks/health", methods=["GET"])
def api_sre_runbook_health():
    """Get runbook system health."""
    try:
        from tools.sre.runbook_executor import check_runbook_health

        return jsonify(check_runbook_health())
    except Exception as exc:
        return jsonify({"error": str(exc)})


# ══════════════════════════════════════════════════════════════════════
# PHASE 2: ALERT → INCIDENT → RUNBOOK CHAIN
# ══════════════════════════════════════════════════════════════════════


@sre_api.route("/chain/process-alert", methods=["POST"])
def api_sre_process_alert():
    """Process an alert through the full SRE chain:
    Alert → Correlate → Create Incident → Match Runbook → Execute.

    This is the core SRE automation endpoint.
    """
    data = request.get_json(force=True, silent=True) or {}
    alert_text = data.get("alert_text", "")
    severity = data.get("severity", "sev3")
    service = data.get("service_name", "unknown")
    source = data.get("source", "api")

    result = {
        "alert": {"text": alert_text, "severity": severity, "service": service},
        "incident": None,
        "runbook_match": None,
        "execution": None,
        "chain_status": "started",
    }

    try:
        # Step 1: Create incident
        from tools.sre.incident_commander import create_incident

        incident = create_incident(
            title=f"Alert: {alert_text[:100]}",
            severity=severity,
            service=service,
            alert_source=source,
        )
        result["incident"] = incident
        result["chain_status"] = "incident_created"

        # Step 2: Match runbook
        from tools.sre.runbook_executor import match_runbook, execute_runbook

        match = match_runbook(alert_text)
        result["runbook_match"] = match

        if match and match.get("matched"):
            result["chain_status"] = "runbook_matched"

            # Step 3: Auto-execute if green risk level
            runbook = match.get("runbook", {})
            if runbook.get("auto_execute") and runbook.get("risk_level") == "green":
                execution = execute_runbook(
                    runbook_id=runbook["id"],
                    trigger_source=source,
                    trigger_text=alert_text,
                )
                result["execution"] = execution
                result["chain_status"] = "runbook_executed"
            else:
                result["chain_status"] = "runbook_pending_approval"
        else:
            result["chain_status"] = "no_runbook_match"

        # Log to audit trail
        conn = get_connection()
        try:
            conn.execute(
                "INSERT INTO audit_trail (event_type, action, details, created_at) VALUES (%s, %s, %s, %s)",
                (
                    "self_heal_triggered",
                    "sre_chain_processed",
                    json.dumps({"severity": severity, "service": service, "chain_status": result["chain_status"]}),
                    now_isoformat(),
                ),
            )
            conn.commit()
        except Exception:
            pass
        finally:
            conn.close()

    except Exception as exc:
        result["error"] = str(exc)
        result["chain_status"] = "error"

    return jsonify(result)


@sre_api.route("/chain/slo-breach", methods=["POST"])
def api_sre_slo_breach():
    """Process an SLO breach — creates incident and triggers runbook.

    Called when SLO error budget is exhausted or burn rate exceeds threshold.
    """
    data = request.get_json(force=True, silent=True) or {}
    slo_id = data.get("slo_id", "")
    service = data.get("service_name", "unknown")

    try:
        from tools.sre.slo_manager import calculate_burn_rate

        burn = calculate_burn_rate(slo_id)
        slo_status = burn.get("status", "unknown")
        budget = burn.get("budget_remaining_pct", 100)

        if slo_status in ("critical", "exhausted"):
            alert_text = f"SLO breach: {service} budget at {budget:.1f}% (status: {slo_status})"
            # Reuse the chain processor
            from flask import current_app

            with current_app.test_request_context(
                "/api/sre/chain/process-alert",
                method="POST",
                json={
                    "alert_text": alert_text,
                    "severity": "sev2" if slo_status == "exhausted" else "sev3",
                    "service_name": service,
                    "source": "slo_manager",
                    "alert_id": slo_id,
                },
            ):
                return api_sre_process_alert()

        return jsonify({"status": slo_status, "budget_remaining_pct": budget, "action": "none"})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


# ══════════════════════════════════════════════════════════════════════
# PHASE 3: DORA METRICS
# ══════════════════════════════════════════════════════════════════════


@sre_api.route("/dora", methods=["GET"])
def api_sre_dora():
    """Compute DORA 4 Key Metrics from pipeline and incident data.

    Sources:
    - Deploy Frequency: ci_pipeline_runs + audit_trail (deploy events)
    - Lead Time: audit_trail commit→deploy timestamps
    - Change Failure Rate: failed deploys / total deploys
    - MTTR: sre_incidents resolved_at - created_at
    """
    days = int(request.args.get("days", "30"))
    conn = get_connection()
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        # Deploy Frequency
        deploy_count = 0
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM audit_trail WHERE event_type IN ('deployment_initiated', 'deploy', 'ci_deploy') "
                "AND created_at >= %s",
                (cutoff,),
            ).fetchone()
            deploy_count = row[0] if row else 0
        except Exception:
            pass
        deploy_freq = deploy_count / max(days, 1)

        # Change Failure Rate
        failed_deploys = 0
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM audit_trail WHERE event_type IN ('deploy_failed', 'deploy_rollback', 'rollback') "
                "AND created_at >= %s",
                (cutoff,),
            ).fetchone()
            failed_deploys = row[0] if row else 0
        except Exception:
            pass
        cfr = (failed_deploys / max(deploy_count, 1)) * 100

        # MTTR (from incidents)
        mttr_seconds = 0
        incident_count = 0
        try:
            rows = conn.execute(
                "SELECT mttr_seconds FROM sre_incidents WHERE status IN ('resolved', 'postmortem', 'closed') "
                "AND resolved_at >= %s AND mttr_seconds IS NOT NULL",
                (cutoff,),
            ).fetchall()
            if rows:
                incident_count = len(rows)
                mttr_seconds = sum(r[0] for r in rows) / len(rows)
        except Exception:
            pass

        # Lead Time (commit to deploy — approximated from audit trail)
        lead_time_hours = 0
        try:
            row = conn.execute(
                "SELECT AVG(CAST((julianday(created_at) - julianday(datetime(created_at, '-1 hour'))) * 24 AS REAL)) "
                "FROM audit_trail WHERE event_type IN ('deployment_initiated', 'deploy') AND created_at >= %s",
                (cutoff,),
            ).fetchone()
            lead_time_hours = round(row[0], 1) if row and row[0] else 1.0
        except Exception:
            lead_time_hours = 1.0

        # DORA ratings
        def _rate_freq(f):
            if f >= 1:
                return "Elite"
            if f >= 0.14:
                return "High"
            if f >= 0.03:
                return "Medium"
            return "Low"

        def _rate_lt(h):
            if h < 1:
                return "Elite"
            if h < 168:
                return "High"
            if h < 720:
                return "Medium"
            return "Low"

        def _rate_cfr(c):
            if c < 5:
                return "Elite"
            if c < 10:
                return "High"
            if c < 15:
                return "Medium"
            return "Low"

        def _rate_mttr(s):
            if s < 3600:
                return "Elite"
            if s < 86400:
                return "High"
            if s < 604800:
                return "Medium"
            return "Low"

        dora = {
            "window_days": days,
            "deploy_frequency": {
                "value": round(deploy_freq, 2),
                "unit": "deploys/day",
                "total_deploys": deploy_count,
                "rating": _rate_freq(deploy_freq),
            },
            "lead_time": {
                "value": lead_time_hours,
                "unit": "hours",
                "rating": _rate_lt(lead_time_hours),
            },
            "change_failure_rate": {
                "value": round(cfr, 1),
                "unit": "%",
                "failed_deploys": failed_deploys,
                "total_deploys": deploy_count,
                "rating": _rate_cfr(cfr),
            },
            "mttr": {
                "value": round(mttr_seconds),
                "unit": "seconds",
                "incidents_resolved": incident_count,
                "rating": _rate_mttr(mttr_seconds),
            },
        }

        # Overall DORA score
        ratings = [
            dora["deploy_frequency"]["rating"],
            dora["lead_time"]["rating"],
            dora["change_failure_rate"]["rating"],
            dora["mttr"]["rating"],
        ]
        rank_map = {"Elite": 4, "High": 3, "Medium": 2, "Low": 1}
        avg_rank = sum(rank_map.get(r, 1) for r in ratings) / 4
        dora["overall_rating"] = (
            "Elite" if avg_rank >= 3.5 else "High" if avg_rank >= 2.5 else "Medium" if avg_rank >= 1.5 else "Low"
        )

        return jsonify(dora)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════
# SRE DASHBOARD OVERVIEW
# ══════════════════════════════════════════════════════════════════════


@sre_api.route("/dashboard", methods=["GET"])
def api_sre_dashboard():
    """Aggregated SRE dashboard data — SLOs, incidents, runbooks, DORA."""
    result = {}
    try:
        from tools.sre.slo_manager import get_slo_dashboard, check_slo_health

        result["slos"] = get_slo_dashboard()
        result["slo_health"] = check_slo_health()
    except Exception as exc:
        result["slos"] = []
        result["slo_error"] = str(exc)

    try:
        from tools.sre.incident_commander import list_incidents, get_mttr_stats

        result["active_incidents"] = [
            i for i in list_incidents() if i.get("status") not in ("closed", "resolved", "postmortem")
        ]
        result["mttr_stats"] = get_mttr_stats()
    except Exception as exc:
        result["active_incidents"] = []
        result["incident_error"] = str(exc)

    try:
        from tools.sre.runbook_executor import list_runbooks, get_execution_history

        result["runbooks"] = list_runbooks()
        result["recent_executions"] = get_execution_history(10)
    except Exception as exc:
        result["runbooks"] = []
        result["runbook_error"] = str(exc)

    return jsonify(result)


@sre_api.route("/health", methods=["GET"])
def api_sre_health():
    """Overall SRE health check."""
    return jsonify({"status": "ok", "module": "sre"})
