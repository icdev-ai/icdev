# CUI // SP-CTI
"""GovLift DoD IL4 Cloud Migration Tool — Flask Blueprint.

Routes (page):
  GET /govlift              — executive overview dashboard
  GET /govlift/workloads    — workload inventory
  GET /govlift/waves        — migration wave planner
  GET /govlift/executor     — migration job tracker
  GET /govlift/stig         — STIG compliance checker
  GET /govlift/audit        — audit log viewer

Routes (API):
  GET  /api/govlift/overview
  GET/POST /api/govlift/workloads
  PATCH /api/govlift/workloads/<id>/status
  POST  /api/govlift/workloads/<id>/assign-wave
  GET/POST /api/govlift/waves
  PATCH /api/govlift/waves/<id>/status
  GET/POST /api/govlift/migrations
  POST  /api/govlift/migrations/<id>/start
  POST  /api/govlift/migrations/<id>/complete
  POST  /api/govlift/migrations/<id>/rollback
  GET   /api/govlift/stig
  POST  /api/govlift/stig/scan/<workload_id>
  PATCH /api/govlift/stig/<id>/status
  GET/POST /api/govlift/audit
  GET   /api/govlift/integrations
  POST  /api/iqe-query  (canvas=govlift)
"""

from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request


def create_govlift_blueprint() -> Blueprint:
    bp = Blueprint("govlift", __name__, url_prefix="")

    _CLS = "CUI // SP-CTI"
    # rmf-ui-17: the IQE widget every govlift page includes reads its POST
    # target from the render context; nothing passed one, so each widget
    # rendered data-api="" and could not ask anything. The route is under
    # /govlift, which this prefix-less blueprint owns -- the bare /api/iqe-query
    # it declared collided with ohc and ai_observatory and belongs to the
    # app-level canvas dispatcher.
    _IQE_CTX = {
        "iqe_canvas": "govlift",
        "iqe_api_route": "/govlift/api/iqe-query",
        "iqe_title": "IQE Query — GovLift",
        "iqe_examples": [
            {"label": "All workloads", "query": "foreach w in govlift.workloads select w.name, w.status, w.wave_id"},
            {"label": "Migration waves", "query": "foreach v in govlift.waves select v.name, v.status, v.scheduled_at"},
            {"label": "Open STIG findings", "query": 'foreach f in govlift.stig where f.status = "open" select f.rule_id, f.severity, f.title'},
        ],
    }

    # ── Page Routes ──────────────────────────────────────────────────────────

    @bp.route("/govlift")
    def govlift_index():
        from tools.govlift.workload_scanner import get_scanner_summary
        from tools.govlift.wave_planner import get_wave_summary
        from tools.govlift.migration_executor import get_migration_summary
        from tools.govlift.audit_engine import get_audit_summary
        data = {
            "scanner": get_scanner_summary(),
            "waves": get_wave_summary(),
            "migrations": get_migration_summary(),
            "audit": get_audit_summary(),
        }
        return render_template("govlift/index.html", data=data, classification=_CLS, **_IQE_CTX)

    @bp.route("/govlift/workloads")
    def govlift_workloads():
        from tools.govlift.workload_scanner import list_workloads, get_scanner_summary
        data = {
            "workloads": list_workloads(limit=100),
            "summary": get_scanner_summary(),
        }
        return render_template("govlift/workloads.html", data=data, classification=_CLS, **_IQE_CTX)

    @bp.route("/govlift/waves")
    def govlift_waves():
        from tools.govlift.wave_planner import list_waves, get_wave_summary
        summary = get_wave_summary()
        data = {
            "waves": list_waves(),
            "summary": summary,
        }
        return render_template("govlift/waves.html", data=data, classification=_CLS, **_IQE_CTX)

    @bp.route("/govlift/executor")
    def govlift_executor():
        from tools.govlift.migration_executor import list_migrations, get_migration_summary
        data = {
            "migrations": list_migrations(limit=50),
            "summary": get_migration_summary(),
        }
        return render_template("govlift/executor.html", data=data, classification=_CLS, **_IQE_CTX)

    @bp.route("/govlift/stig")
    def govlift_stig():
        from tools.govlift.stig_checker import list_stig_checks, get_stig_summary
        summary = get_stig_summary()
        data = {
            "checks": list_stig_checks(limit=100),
            "summary": {
                "total": summary.get("total_open", 0) + sum(
                    v.get("not_a_finding", 0) + v.get("not_applicable", 0) + v.get("not_reviewed", 0)
                    for v in summary.get("by_severity", {}).values()
                ),
                "open_cat1": summary.get("by_severity", {}).get("cat1", {}).get("open", 0),
                "open_cat2": summary.get("by_severity", {}).get("cat2", {}).get("open", 0),
                "open_cat3": summary.get("by_severity", {}).get("cat3", {}).get("open", 0),
                "total_open": summary.get("total_open", 0),
                "not_a_finding": sum(v.get("not_a_finding", 0) for v in summary.get("by_severity", {}).values()),
                "not_applicable": sum(v.get("not_applicable", 0) for v in summary.get("by_severity", {}).values()),
                "not_reviewed": sum(v.get("not_reviewed", 0) for v in summary.get("by_severity", {}).values()),
            },
        }
        return render_template("govlift/stig.html", data=data, classification=_CLS, **_IQE_CTX)

    @bp.route("/govlift/audit")
    def govlift_audit():
        from tools.govlift.audit_engine import list_audit_log, get_audit_summary
        data = {
            "log": list_audit_log(limit=200),
            "summary": get_audit_summary(),
        }
        return render_template("govlift/audit.html", data=data, classification=_CLS, **_IQE_CTX)

    @bp.route("/govlift/workloads/<workload_id>")
    def govlift_workload_detail(workload_id):
        from tools.govlift.workload_scanner import get_workload
        from tools.govlift.migration_executor import list_migrations
        from tools.govlift.stig_checker import list_stig_checks
        from tools.govlift.wave_planner import get_wave
        wl = get_workload(workload_id) or {}
        wave = None
        if wl.get("wave_id"):
            wave = get_wave(wl["wave_id"])
        data = {
            "workload": wl,
            "wave": wave,
            "migrations": list_migrations(workload_id=workload_id, limit=20),
            "stig_checks": list_stig_checks(workload_id=workload_id, limit=50),
        }
        return render_template("govlift/workload_detail.html", data=data, classification=_CLS, **_IQE_CTX)

    @bp.route("/govlift/waves/<wave_id>")
    def govlift_wave_detail(wave_id):
        from tools.govlift.wave_planner import get_wave
        from tools.govlift.workload_scanner import list_workloads
        from tools.govlift.migration_executor import list_migrations
        wave = get_wave(wave_id) or {}
        data = {
            "wave": wave,
            "workloads": list_workloads(wave_id=wave_id, limit=100),
            "migrations": list_migrations(wave_id=wave_id, limit=50),
        }
        return render_template("govlift/wave_detail.html", data=data, classification=_CLS, **_IQE_CTX)

    @bp.route("/govlift/migrations/<migration_id>")
    def govlift_migration_detail(migration_id):
        from tools.govlift.migration_executor import get_migration
        from tools.govlift.workload_scanner import get_workload
        from tools.govlift.wave_planner import get_wave
        mig = get_migration(migration_id) or {}
        wl = get_workload(mig.get("workload_id")) if mig.get("workload_id") else None
        wave = get_wave(mig.get("wave_id")) if mig.get("wave_id") else None
        data = {
            "migration": mig,
            "workload": wl,
            "wave": wave,
        }
        return render_template("govlift/migration_detail.html", data=data, classification=_CLS, **_IQE_CTX)

    @bp.route("/govlift/stig/<check_id>")
    def govlift_stig_detail(check_id):
        from tools.govlift.stig_checker import get_stig_check
        from tools.govlift.workload_scanner import get_workload
        chk = get_stig_check(check_id) or {}
        wl = get_workload(chk.get("workload_id")) if chk.get("workload_id") else None
        data = {
            "check": chk,
            "workload": wl,
        }
        return render_template("govlift/stig_detail.html", data=data, classification=_CLS, **_IQE_CTX)

    # ── JSON API ─────────────────────────────────────────────────────────────

    @bp.route("/api/govlift/overview")
    def api_govlift_overview():
        from tools.govlift.workload_scanner import get_scanner_summary
        from tools.govlift.wave_planner import get_wave_summary
        from tools.govlift.migration_executor import get_migration_summary
        from tools.govlift.audit_engine import get_audit_summary
        return jsonify({
            "scanner": get_scanner_summary(),
            "waves": get_wave_summary(),
            "migrations": get_migration_summary(),
            "audit": get_audit_summary(),
        })

    @bp.route("/api/govlift/workloads")
    def api_govlift_workloads_get():
        from tools.govlift.workload_scanner import list_workloads
        workloads = list_workloads(
            status=request.args.get("status") or None,
            wave_id=request.args.get("wave_id") or None,
            risk_level=request.args.get("risk_level") or None,
        )
        return jsonify({"workloads": workloads, "total": len(workloads)})

    @bp.route("/api/govlift/workloads", methods=["POST"])
    def api_govlift_workloads_post():
        from tools.govlift.workload_scanner import create_workload
        body = request.get_json(force=True, silent=True) or {}
        try:
            wl = create_workload(
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
            return jsonify(wl), 201
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400

    @bp.route("/api/govlift/workloads/<workload_id>/status", methods=["PATCH"])
    def api_govlift_workload_status(workload_id):
        from tools.govlift.workload_scanner import update_workload_status
        body = request.get_json(force=True, silent=True) or {}
        result = update_workload_status(workload_id, body.get("migration_status", ""), body.get("wave_id"))
        return jsonify(result)

    @bp.route("/api/govlift/workloads/<workload_id>/assign-wave", methods=["POST"])
    def api_govlift_assign_wave(workload_id):
        from tools.govlift.workload_scanner import assign_wave
        body = request.get_json(force=True, silent=True) or {}
        wave_id = body.get("wave_id", "")
        if not wave_id:
            return jsonify({"error": "wave_id required"}), 400
        return jsonify(assign_wave(workload_id, wave_id))

    @bp.route("/api/govlift/waves")
    def api_govlift_waves_get():
        from tools.govlift.wave_planner import list_waves
        waves = list_waves(status=request.args.get("status") or None)
        return jsonify({"waves": waves, "total": len(waves)})

    @bp.route("/api/govlift/waves", methods=["POST"])
    def api_govlift_waves_post():
        from tools.govlift.wave_planner import create_wave
        body = request.get_json(force=True, silent=True) or {}
        wave = create_wave(
            name=body.get("name", ""),
            sequence_num=int(body.get("sequence_num", 1)),
            planned_start=body.get("planned_start") or None,
            planned_end=body.get("planned_end") or None,
            notes=body.get("notes", ""),
        )
        return jsonify(wave), 201

    @bp.route("/api/govlift/waves/<wave_id>/status", methods=["PATCH"])
    def api_govlift_wave_status(wave_id):
        from tools.govlift.wave_planner import update_wave_status
        body = request.get_json(force=True, silent=True) or {}
        return jsonify(update_wave_status(wave_id, body.get("status", "")))

    @bp.route("/api/govlift/migrations")
    def api_govlift_migrations_get():
        from tools.govlift.migration_executor import list_migrations
        migrations = list_migrations(
            workload_id=request.args.get("workload_id") or None,
            wave_id=request.args.get("wave_id") or None,
            status=request.args.get("status") or None,
        )
        return jsonify({"migrations": migrations, "total": len(migrations)})

    @bp.route("/api/govlift/migrations", methods=["POST"])
    def api_govlift_migrations_post():
        from tools.govlift.migration_executor import create_migration
        body = request.get_json(force=True, silent=True) or {}
        wl_id = body.get("workload_id", "")
        if not wl_id:
            return jsonify({"error": "workload_id required"}), 400
        return jsonify(create_migration(wl_id, body.get("wave_id"))), 201

    @bp.route("/api/govlift/migrations/<mig_id>/start", methods=["POST"])
    def api_govlift_migration_start(mig_id):
        from tools.govlift.migration_executor import start_migration
        return jsonify(start_migration(mig_id))

    @bp.route("/api/govlift/migrations/<mig_id>/complete", methods=["POST"])
    def api_govlift_migration_complete(mig_id):
        from tools.govlift.migration_executor import complete_migration
        body = request.get_json(force=True, silent=True) or {}
        return jsonify(complete_migration(mig_id, bool(body.get("success", True)), body.get("log", "")))

    @bp.route("/api/govlift/migrations/<mig_id>/rollback", methods=["POST"])
    def api_govlift_migration_rollback(mig_id):
        from tools.govlift.migration_executor import rollback_migration
        return jsonify(rollback_migration(mig_id))

    @bp.route("/api/govlift/stig")
    def api_govlift_stig_get():
        from tools.govlift.stig_checker import list_stig_checks
        checks = list_stig_checks(
            workload_id=request.args.get("workload_id") or None,
            severity=request.args.get("severity") or None,
            status=request.args.get("status") or None,
        )
        return jsonify({"stig_checks": checks, "total": len(checks)})

    @bp.route("/api/govlift/stig/scan/<workload_id>", methods=["POST"])
    def api_govlift_stig_scan(workload_id):
        from tools.govlift.stig_checker import run_quick_scan
        results = run_quick_scan(workload_id)
        return jsonify({"scan_results": results, "total": len(results)})

    @bp.route("/api/govlift/stig/<check_id>/status", methods=["PATCH"])
    def api_govlift_stig_status(check_id):
        from tools.govlift.stig_checker import update_check_status
        body = request.get_json(force=True, silent=True) or {}
        return jsonify(update_check_status(check_id, body.get("status", ""), body.get("finding")))

    @bp.route("/api/govlift/audit")
    def api_govlift_audit_get():
        from tools.govlift.audit_engine import list_audit_log
        entries = list_audit_log(
            user_id=request.args.get("user_id") or None,
            action=request.args.get("action") or None,
            limit=int(request.args.get("limit", 100)),
        )
        return jsonify({"audit_log": entries, "total": len(entries)})

    @bp.route("/api/govlift/audit", methods=["POST"])
    def api_govlift_audit_post():
        from tools.govlift.audit_engine import log_action
        body = request.get_json(force=True, silent=True) or {}
        entry = log_action(
            user_id=body.get("user_id", ""),
            action=body.get("action", ""),
            resource_type=body.get("resource_type", ""),
            resource_id=body.get("resource_id", ""),
            details=body.get("details"),
            ip_address=body.get("ip_address", ""),
            session_id=body.get("session_id", ""),
        )
        return jsonify(entry), 201

    @bp.route("/api/govlift/integrations")
    def api_govlift_integrations():
        from tools.db.storage import get_connection
        conn = get_connection()
        try:
            rows = conn.execute("SELECT * FROM govlift_integrations ORDER BY system_name ASC").fetchall()
            return jsonify({"integrations": [dict(r) for r in rows]})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @bp.route("/govlift/api/iqe-query", methods=["POST"])
    def govlift_iqe_query():
        from tools.iqe.nl_to_iqe import nl_to_iqe
        from tools.iqe.parser import IQESyntaxError, parse
        from tools.iqe.executor import execute_query
        import tools.iqe.adapters.govlift  # noqa: F401 — registers govlift.* collections

        body = request.get_json(force=True, silent=True) or {}
        question = (body.get("query") or body.get("question") or "").strip()
        if not question:
            return jsonify({"error": "query required"}), 400

        collections = ["govlift.workloads", "govlift.waves", "govlift.migrations",
                       "govlift.stig", "govlift.audit"]
        try:
            translation = nl_to_iqe(question, collections)
            iqe_str = translation.get("iqe", "")
            explanation = translation.get("explanation", "")
            if not body.get("execute", True):
                return jsonify({"ok": True, "iqe": iqe_str, "explanation": explanation})
            try:
                ast = parse(iqe_str)
                rows = execute_query(ast)
            except IQESyntaxError:
                rows = []
            return jsonify({"ok": True, "iqe": iqe_str, "explanation": explanation,
                            "results": rows, "total": len(rows)})
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc), "results": [], "total": 0})

    return bp
