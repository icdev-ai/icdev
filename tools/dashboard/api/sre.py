
from tools.logging.icdev_logger import get_logger
# CUI // SP-CTI
"""SRE API Blueprint — exposes SLO, incident, runbook, DORA, and chaos endpoints.

Phases 1-3 of SRE integration: API endpoints, alert→incident→runbook chain,
DORA metrics computation.

Registers at /api/sre/* in the ICDEV dashboard.
"""

import json
from datetime import datetime, timezone, timedelta
from flask import Blueprint, jsonify, request, g
from tools.common.helpers import now_isoformat
from tools.db.storage import get_connection

# nav-comp-05: state-changing SRE endpoints (incident create/triage/escalate/
# resolve/postmortem/close, runbook create/execute/match, SLO create/measure,
# and the alert/SLO-breach chain processors) must be restricted to operations
# roles, not merely any authenticated session. Before this fix every POST here
# relied on global auth only, so the lowest-privilege ``developer`` could create
# incidents, execute runbooks (which run shell steps), and drive the full
# self-heal chain. ``require_role`` reads ``g.current_user`` (set by the
# dashboard before_request hook), 401s an anonymous caller and 403s an
# unauthorized role. ``component_admin`` is the platform/infrastructure
# administrator persona (GovLift, migration 139) — the natural SRE operator in
# this RBAC vocabulary; ``admin``/``isso`` retain full access. Every role here
# also appears in ``VALID_DASHBOARD_ROLES`` (tools/dashboard/auth.py).
from tools.dashboard.auth import require_role

logger = get_logger("icdev.sre.api")

sre_api = Blueprint("sre_api", __name__, url_prefix="/api/sre")

# Roles permitted to drive SRE state changes / operational actions.
_SRE_MUTATION_ROLES = ("admin", "isso", "component_admin")


def _actor() -> str:
    """Resolve the acting user for attribution/audit from the session identity.

    Never trust a request-body actor field — attribution comes from
    ``g.current_user`` (set by the dashboard auth middleware).
    """
    user = getattr(g, "current_user", None)
    if isinstance(user, dict):
        return user.get("username") or user.get("email") or user.get("id") or "system"
    return "system"


def _parse_ts(value):
    """Parse a stored timestamp (ISO8601 or ``YYYY-MM-DD HH:MM:SS``) into an
    aware :class:`datetime`. Returns ``None`` when the value can't be parsed.

    PG returns ``datetime`` objects; SQLite returns strings — handle both so
    date math is done in Python (PG-primary pattern) rather than in dialect
    SQL that only runs on one backend.
    """
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip().replace("Z", "+00:00")
    dt = None
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                dt = datetime.strptime(text, fmt)
                break
            except ValueError:
                dt = None
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


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
@require_role(*_SRE_MUTATION_ROLES)
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
@require_role(*_SRE_MUTATION_ROLES)
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
@require_role(*_SRE_MUTATION_ROLES)
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
@require_role(*_SRE_MUTATION_ROLES)
def api_sre_triage(incident_id):
    """Triage an incident."""
    data = request.get_json(force=True, silent=True) or {}
    try:
        from tools.sre.incident_commander import triage_incident

        return jsonify(triage_incident(incident_id, data.get("root_cause")))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@sre_api.route("/incidents/<incident_id>/escalate", methods=["POST"])
@require_role(*_SRE_MUTATION_ROLES)
def api_sre_escalate(incident_id):
    """Escalate an incident."""
    data = request.get_json(force=True, silent=True) or {}
    try:
        from tools.sre.incident_commander import escalate_incident

        return jsonify(escalate_incident(incident_id, data.get("reason", "")))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@sre_api.route("/incidents/<incident_id>/resolve", methods=["POST"])
@require_role(*_SRE_MUTATION_ROLES)
def api_sre_resolve(incident_id):
    """Resolve an incident."""
    data = request.get_json(force=True, silent=True) or {}
    try:
        from tools.sre.incident_commander import resolve_incident

        return jsonify(resolve_incident(incident_id, data.get("mitigation_steps", [])))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@sre_api.route("/incidents/<incident_id>/postmortem", methods=["POST"])
@require_role(*_SRE_MUTATION_ROLES)
def api_sre_postmortem(incident_id):
    """Add postmortem to an incident."""
    data = request.get_json(force=True, silent=True) or {}
    try:
        from tools.sre.incident_commander import add_postmortem

        return jsonify(add_postmortem(incident_id, data.get("url", ""), data.get("lessons_learned", "")))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@sre_api.route("/incidents/<incident_id>/close", methods=["POST"])
@require_role(*_SRE_MUTATION_ROLES)
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
@require_role(*_SRE_MUTATION_ROLES)
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
@require_role(*_SRE_MUTATION_ROLES)
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
@require_role(*_SRE_MUTATION_ROLES)
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
@require_role(*_SRE_MUTATION_ROLES)
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
                    json.dumps(
                        {
                            "severity": severity,
                            "service": service,
                            "chain_status": result["chain_status"],
                            "actor": _actor(),
                        }
                    ),
                    now_isoformat(),
                ),
            )
            conn.commit()
        except Exception as _exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
            logger.warning("api_sre_process_alert: best-effort INSERT into audit_trail failed (non-blocking): %s", _exc)
        finally:
            conn.close()

    except Exception as exc:
        result["error"] = str(exc)
        result["chain_status"] = "error"

    return jsonify(result)


@sre_api.route("/chain/slo-breach", methods=["POST"])
@require_role(*_SRE_MUTATION_ROLES)
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

        # DORA ratings — only applied when a metric is actually assessed.
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

        # A DB error, or the honest absence of data, must NEVER be laundered into
        # a favorable ("Elite") rating. When a metric can't be assessed we emit
        # NOT_ASSESSED and (for DB errors) an ``error`` flag — a fail-loud
        # degraded state, not a fabricated score.
        NOT_ASSESSED = "Not Assessed"

        # ── Deploy Frequency ────────────────────────────────────────────────
        deploy_count = None  # None ⇒ query failed (distinct from a real 0)
        deploy_metric = {"unit": "deploys/day"}
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM audit_trail "
                "WHERE event_type IN ('deployment_initiated', 'deploy', 'ci_deploy') "
                "AND created_at >= %s",
                (cutoff,),
            ).fetchone()
            deploy_count = int(row[0]) if row and row[0] is not None else 0
            deploy_freq = deploy_count / max(days, 1)
            deploy_metric.update(
                {
                    "value": round(deploy_freq, 2),
                    "total_deploys": deploy_count,
                    "rating": _rate_freq(deploy_freq),
                }
            )
        except Exception as exc:
            deploy_metric.update({"value": None, "error": True, "detail": str(exc), "rating": NOT_ASSESSED})

        # ── Change Failure Rate ─────────────────────────────────────────────
        cfr_metric = {"unit": "%"}
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM audit_trail "
                "WHERE event_type IN ('deployment_failed', 'deploy_failed', 'rollback_executed', 'deploy_rollback', 'rollback') "
                "AND created_at >= %s",
                (cutoff,),
            ).fetchone()
            failed_deploys = int(row[0]) if row and row[0] is not None else 0
            if deploy_count is None:
                # Denominator query failed — CFR is not computable, don't fake it.
                raise RuntimeError("deploy count unavailable")
            if deploy_count == 0:
                cfr_metric.update(
                    {
                        "value": None,
                        "failed_deploys": failed_deploys,
                        "total_deploys": 0,
                        "rating": NOT_ASSESSED,
                        "note": "no deploys in window",
                    }
                )
            else:
                cfr = (failed_deploys / deploy_count) * 100
                cfr_metric.update(
                    {
                        "value": round(cfr, 1),
                        "failed_deploys": failed_deploys,
                        "total_deploys": deploy_count,
                        "rating": _rate_cfr(cfr),
                    }
                )
        except Exception as exc:
            cfr_metric.update({"value": None, "error": True, "detail": str(exc), "rating": NOT_ASSESSED})

        # ── MTTR (from resolved incidents) ──────────────────────────────────
        mttr_metric = {"unit": "seconds"}
        try:
            rows = conn.execute(
                "SELECT mttr_seconds FROM sre_incidents "
                "WHERE status IN ('resolved', 'postmortem', 'closed') "
                "AND resolved_at >= %s AND mttr_seconds IS NOT NULL",
                (cutoff,),
            ).fetchall()
            if rows:
                incident_count = len(rows)
                mttr_seconds = sum(r[0] for r in rows) / incident_count
                mttr_metric.update(
                    {
                        "value": round(mttr_seconds),
                        "incidents_resolved": incident_count,
                        "rating": _rate_mttr(mttr_seconds),
                    }
                )
            else:
                mttr_metric.update(
                    {
                        "value": None,
                        "incidents_resolved": 0,
                        "rating": NOT_ASSESSED,
                        "note": "no resolved incidents in window",
                    }
                )
        except Exception as exc:
            mttr_metric.update({"value": None, "error": True, "detail": str(exc), "rating": NOT_ASSESSED})

        # ── Lead Time for Changes ───────────────────────────────────────────
        # Real Python-side date math: fetch pipeline start/complete timestamps
        # and average the deltas. The prior implementation used SQLite-only
        # ``julianday``/``datetime(created_at, '-1 hour')`` that (a) always
        # raised on PostgreSQL and was swallowed to a constant 1.0h, and
        # (b) was tautological even on SQLite (created_at minus created_at-1h
        # is 1h by construction). Now derived from ci_pipeline_runs durations.
        lead_time_metric = {"unit": "hours"}
        try:
            rows = conn.execute(
                "SELECT created_at, completed_at FROM ci_pipeline_runs "
                "WHERE status = 'completed' AND completed_at IS NOT NULL AND created_at >= %s",
                (cutoff,),
            ).fetchall()
            deltas = []
            for started, completed in rows:
                s = _parse_ts(started)
                c = _parse_ts(completed)
                if s and c and c >= s:
                    deltas.append((c - s).total_seconds() / 3600.0)
            if deltas:
                avg_hours = sum(deltas) / len(deltas)
                lead_time_metric.update(
                    {
                        "value": round(avg_hours, 2),
                        "samples": len(deltas),
                        "rating": _rate_lt(avg_hours),
                    }
                )
            else:
                lead_time_metric.update(
                    {
                        "value": None,
                        "samples": 0,
                        "rating": NOT_ASSESSED,
                        "note": "no completed pipeline runs in window",
                    }
                )
        except Exception as exc:
            lead_time_metric.update({"value": None, "error": True, "detail": str(exc), "rating": NOT_ASSESSED})

        dora = {
            "window_days": days,
            "deploy_frequency": deploy_metric,
            "lead_time": lead_time_metric,
            "change_failure_rate": cfr_metric,
            "mttr": mttr_metric,
        }

        # Overall DORA score — averaged over ASSESSED metrics only. If nothing
        # could be assessed, the overall is Not Assessed (never a default rank).
        rank_map = {"Elite": 4, "High": 3, "Medium": 2, "Low": 1}
        assessed_ranks = [
            rank_map[m["rating"]]
            for m in (deploy_metric, lead_time_metric, cfr_metric, mttr_metric)
            if m.get("rating") in rank_map
        ]
        if assessed_ranks:
            avg_rank = sum(assessed_ranks) / len(assessed_ranks)
            dora["overall_rating"] = (
                "Elite" if avg_rank >= 3.5 else "High" if avg_rank >= 2.5 else "Medium" if avg_rank >= 1.5 else "Low"
            )
        else:
            dora["overall_rating"] = NOT_ASSESSED
        dora["metrics_assessed"] = len(assessed_ranks)

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


@sre_api.route("/invocations", methods=["GET"])
def api_sre_invocations():
    """Runtime invocation rollup — which MCP tools / agents are slow or failing.

    Read-only, so it carries no ``require_role`` mutation gate; the dashboard's
    global auth hook still applies. Backed by the same
    ``InvocationStore.report`` the ``icdev runtime top`` CLI uses, so the panel
    and the terminal cannot drift apart or disagree about the arithmetic.

    Windowed to ``days`` (default 30) where the CLI defaults to all time, and
    the asymmetry is deliberate. ``runtime_invocations`` has no entry in
    ``args/retention_policies.yaml``, so it grows without bound, and the rollup
    is a ``GROUP BY`` with no natural ceiling. That is fine for a one-off
    report an operator asked for; it is not fine on a page that re-runs it on
    every load. ``started_at`` is the indexed column
    (``idx_runtime_inv_surface_started``), so the window is also the cheap
    filter. The window is echoed back as ``window_days`` and rendered in the
    panel heading — a windowed number presented as a lifetime total would be a
    quiet lie.
    """
    try:
        from tools.observability.invocation_store import (
            InvocationFilter,
            InvocationStore,
        )

        limit = max(1, min(int(request.args.get("limit", 15)), 200))
        days = max(1, min(int(request.args.get("days", 30)), 3650))
        since = request.args.get("since") or (
            datetime.now(timezone.utc) - timedelta(days=days)
        ).isoformat()

        report = InvocationStore().report(InvocationFilter(
            surface=request.args.get("surface") or None,
            since=since,
            limit=limit,
        ))
        report["window_days"] = days if not request.args.get("since") else None
        return jsonify(report)
    except Exception as exc:  # noqa: BLE001 — a telemetry panel must not 500 the page
        logger.warning("sre invocations rollup failed: %s", exc)
        return jsonify({"surfaces": [], "names": [], "total_names": 0,
                        "error": str(exc)})


@sre_api.route("/health", methods=["GET"])
def api_sre_health():
    """Overall SRE health check."""
    return jsonify({"status": "ok", "module": "sre"})
