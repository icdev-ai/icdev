# CUI // SP-CTI
"""GovLift DoD IL4 Cloud Migration Tool — Flask Blueprint.

Registers all page and API routes under /govlift.
All module imports are lazy (inside route function bodies) to allow the
blueprint to load even if optional dependencies are unavailable at import time.

Factory function: create_govlift_blueprint()
"""

from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request


def create_govlift_blueprint() -> Blueprint:
    """Create and return the configured GovLift Blueprint."""

    bp = Blueprint("govlift", __name__, url_prefix="/govlift")

    _CLASSIFICATION = "CUI // SP-CTI"

    # ── Page Routes ──────────────────────────────────────────────────────────

    @bp.route("", methods=["GET"])
    @bp.route("/", methods=["GET"])
    def index():
        from tools.govlift.workload_scanner import get_scanner_summary
        from tools.govlift.wave_planner import get_wave_summary
        from tools.govlift.migration_executor import get_migration_summary
        from tools.govlift.audit_engine import get_audit_summary

        return render_template(
            "govlift/index.html",
            classification=_CLASSIFICATION,
            scanner_summary=get_scanner_summary(),
            wave_summary=get_wave_summary(),
            migration_summary=get_migration_summary(),
            audit_summary=get_audit_summary(),
        )

    @bp.route("/workloads", methods=["GET"])
    def workloads():
        from tools.govlift.workload_scanner import list_workloads, get_scanner_summary

        return render_template(
            "govlift/workloads.html",
            classification=_CLASSIFICATION,
            workloads=list_workloads(limit=100),
            scanner_summary=get_scanner_summary(),
        )

    @bp.route("/waves", methods=["GET"])
    def waves():
        from tools.govlift.wave_planner import list_waves, get_wave_summary

        return render_template(
            "govlift/waves.html",
            classification=_CLASSIFICATION,
            waves=list_waves(),
            wave_summary=get_wave_summary(),
        )

    @bp.route("/executor", methods=["GET"])
    def executor():
        from tools.govlift.migration_executor import list_migrations, get_migration_summary

        return render_template(
            "govlift/executor.html",
            classification=_CLASSIFICATION,
            migrations=list_migrations(limit=50),
            migration_summary=get_migration_summary(),
        )

    @bp.route("/stig", methods=["GET"])
    def stig():
        from tools.govlift.stig_checker import list_stig_checks, get_stig_summary

        return render_template(
            "govlift/stig.html",
            classification=_CLASSIFICATION,
            stig_checks=list_stig_checks(limit=100),
            stig_summary=get_stig_summary(),
        )

    @bp.route("/audit", methods=["GET"])
    def audit():
        from tools.govlift.audit_engine import list_audit_log, get_audit_summary

        return render_template(
            "govlift/audit.html",
            classification=_CLASSIFICATION,
            audit_log=list_audit_log(limit=200),
            audit_summary=get_audit_summary(),
        )

    # ── JSON API Routes ───────────────────────────────────────────────────────

    @bp.route("/api/govlift/overview", methods=["GET"])
    def api_overview():
        from tools.govlift.workload_scanner import get_scanner_summary
        from tools.govlift.wave_planner import get_wave_summary
        from tools.govlift.migration_executor import get_migration_summary
        from tools.govlift.audit_engine import get_audit_summary

        try:
            return jsonify({
                "scanner": get_scanner_summary(),
                "waves": get_wave_summary(),
                "migrations": get_migration_summary(),
                "audit": get_audit_summary(),
            })
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    # ── Workload API ──────────────────────────────────────────────────────────

    @bp.route("/api/govlift/workloads", methods=["GET"])
    def api_list_workloads():
        from tools.govlift.workload_scanner import list_workloads

        try:
            status = request.args.get("status") or None
            wave_id = request.args.get("wave_id") or None
            risk_level = request.args.get("risk_level") or None
            workloads = list_workloads(status=status, wave_id=wave_id, risk_level=risk_level)
            return jsonify({"workloads": workloads, "total": len(workloads)})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/govlift/workloads", methods=["POST"])
    def api_create_workload():
        from tools.govlift.workload_scanner import create_workload

        try:
            body = request.get_json(force=True, silent=True) or {}
            workload = create_workload(
                name=body.get("name", ""),
                workload_type=body.get("workload_type", "web_app"),
                os_name=body.get("os_name", ""),
                os_version=body.get("os_version", ""),
                environment=body.get("environment", "production"),
                ip_address=body.get("ip_address", ""),
                cpu_cores=int(body.get("cpu_cores", 4)),
                memory_gb=float(body.get("memory_gb", 8.0)),
                storage_tb=float(body.get("storage_tb", 1.0)),
                risk_level=body.get("risk_level", "medium"),
                notes=body.get("notes", ""),
            )
            return jsonify(workload), 201
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/govlift/workloads/<workload_id>/status", methods=["PATCH"])
    def api_update_workload_status(workload_id: str):
        from tools.govlift.workload_scanner import update_workload_status

        try:
            body = request.get_json(force=True, silent=True) or {}
            migration_status = body.get("migration_status", "")
            wave_id = body.get("wave_id") or None
            updated = update_workload_status(workload_id, migration_status, wave_id=wave_id)
            return jsonify(updated)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/govlift/workloads/<workload_id>/assign-wave", methods=["POST"])
    def api_assign_wave(workload_id: str):
        from tools.govlift.workload_scanner import assign_wave

        try:
            body = request.get_json(force=True, silent=True) or {}
            wave_id = body.get("wave_id", "")
            if not wave_id:
                return jsonify({"error": "wave_id is required"}), 400
            updated = assign_wave(workload_id, wave_id)
            return jsonify(updated)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    # ── Wave API ──────────────────────────────────────────────────────────────

    @bp.route("/api/govlift/waves", methods=["GET"])
    def api_list_waves():
        from tools.govlift.wave_planner import list_waves

        try:
            status = request.args.get("status") or None
            waves = list_waves(status=status)
            return jsonify({"waves": waves, "total": len(waves)})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/govlift/waves", methods=["POST"])
    def api_create_wave():
        from tools.govlift.wave_planner import create_wave

        try:
            body = request.get_json(force=True, silent=True) or {}
            wave = create_wave(
                name=body.get("name", ""),
                sequence_num=int(body.get("sequence_num", 1)),
                planned_start=body.get("planned_start") or None,
                planned_end=body.get("planned_end") or None,
                notes=body.get("notes", ""),
            )
            return jsonify(wave), 201
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/govlift/waves/<wave_id>/status", methods=["PATCH"])
    def api_update_wave_status(wave_id: str):
        from tools.govlift.wave_planner import update_wave_status

        try:
            body = request.get_json(force=True, silent=True) or {}
            status = body.get("status", "")
            updated = update_wave_status(wave_id, status)
            return jsonify(updated)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    # ── Migration API ─────────────────────────────────────────────────────────

    @bp.route("/api/govlift/migrations", methods=["GET"])
    def api_list_migrations():
        from tools.govlift.migration_executor import list_migrations

        try:
            workload_id = request.args.get("workload_id") or None
            wave_id = request.args.get("wave_id") or None
            status = request.args.get("status") or None
            migrations = list_migrations(workload_id=workload_id, wave_id=wave_id, status=status)
            return jsonify({"migrations": migrations, "total": len(migrations)})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/govlift/migrations", methods=["POST"])
    def api_create_migration():
        from tools.govlift.migration_executor import create_migration

        try:
            body = request.get_json(force=True, silent=True) or {}
            workload_id = body.get("workload_id", "")
            if not workload_id:
                return jsonify({"error": "workload_id is required"}), 400
            wave_id = body.get("wave_id") or None
            migration = create_migration(workload_id=workload_id, wave_id=wave_id)
            return jsonify(migration), 201
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/govlift/migrations/<mig_id>/start", methods=["POST"])
    def api_start_migration(mig_id: str):
        from tools.govlift.migration_executor import start_migration

        try:
            updated = start_migration(mig_id)
            return jsonify(updated)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/govlift/migrations/<mig_id>/complete", methods=["POST"])
    def api_complete_migration(mig_id: str):
        from tools.govlift.migration_executor import complete_migration

        try:
            body = request.get_json(force=True, silent=True) or {}
            success = bool(body.get("success", True))
            log = body.get("log", "")
            updated = complete_migration(mig_id, success=success, log=log)
            return jsonify(updated)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/govlift/migrations/<mig_id>/rollback", methods=["POST"])
    def api_rollback_migration(mig_id: str):
        from tools.govlift.migration_executor import rollback_migration

        try:
            updated = rollback_migration(mig_id)
            return jsonify(updated)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    # ── STIG API ──────────────────────────────────────────────────────────────

    @bp.route("/api/govlift/stig", methods=["GET"])
    def api_list_stig():
        from tools.govlift.stig_checker import list_stig_checks

        try:
            workload_id = request.args.get("workload_id") or None
            severity = request.args.get("severity") or None
            status = request.args.get("status") or None
            checks = list_stig_checks(workload_id=workload_id, severity=severity, status=status)
            return jsonify({"stig_checks": checks, "total": len(checks)})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/govlift/stig/scan/<workload_id>", methods=["POST"])
    def api_run_stig_scan(workload_id: str):
        from tools.govlift.stig_checker import run_quick_scan

        try:
            results = run_quick_scan(workload_id)
            return jsonify({"scan_results": results, "total": len(results)})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/govlift/stig/<check_id>/status", methods=["PATCH"])
    def api_update_stig_status(check_id: str):
        from tools.govlift.stig_checker import update_check_status

        try:
            body = request.get_json(force=True, silent=True) or {}
            status = body.get("status", "")
            finding = body.get("finding") or None
            updated = update_check_status(check_id, status, finding=finding)
            return jsonify(updated)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    # ── Audit API ─────────────────────────────────────────────────────────────

    @bp.route("/api/govlift/audit", methods=["GET"])
    def api_list_audit():
        from tools.govlift.audit_engine import list_audit_log

        try:
            user_id = request.args.get("user_id") or None
            action = request.args.get("action") or None
            limit = int(request.args.get("limit", 100))
            entries = list_audit_log(user_id=user_id, action=action, limit=limit)
            return jsonify({"audit_log": entries, "total": len(entries)})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/govlift/audit", methods=["POST"])
    def api_log_audit():
        from tools.govlift.audit_engine import log_action

        try:
            body = request.get_json(force=True, silent=True) or {}
            entry = log_action(
                user_id=body.get("user_id", ""),
                action=body.get("action", ""),
                resource_type=body.get("resource_type", ""),
                resource_id=body.get("resource_id", ""),
                details=body.get("details") or None,
                ip_address=body.get("ip_address", ""),
                session_id=body.get("session_id", ""),
            )
            return jsonify(entry), 201
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    # ── Integrations API ──────────────────────────────────────────────────────

    @bp.route("/api/govlift/integrations", methods=["GET"])
    def api_list_integrations():
        from tools.db.storage import get_connection, translate_sql

        try:
            conn = get_connection()
            try:
                rows = conn.execute(
                    translate_sql(
                        "SELECT * FROM govlift_integrations ORDER BY system_name ASC"
                    )
                ).fetchall()
                integrations = [dict(r) for r in rows]
            finally:
                conn.close()
            return jsonify({"integrations": integrations, "total": len(integrations)})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    # ── IQE Query ─────────────────────────────────────────────────────────────

    @bp.route("/api/iqe-query", methods=["POST"])
    def govlift_iqe():
        from tools.iqe.engine import handle_iqe_query

        body = request.get_json(force=True, silent=True) or {}
        result = handle_iqe_query(
            canvas="govlift",
            query=body.get("query", ""),
            context_id=body.get("context_id"),
        )
        return jsonify(result)

    return bp
