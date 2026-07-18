# CUI // SP-CTI
"""NOC Operations Canvas (NOCC) Flask Blueprint.

Routes:
  GET  /noc                    — NOCC overview (alarm summary + incident counts + SLA health)
  GET  /noc/alarms             — alarm list with severity filter
  GET  /noc/incidents          — incident list with priority/status filter
  GET  /noc/rfcs               — RFC change request list
  GET  /noc/mops               — MOP list with RFC linkage
  GET  /noc/maintenance        — maintenance window calendar/table
  GET  /noc/sla                — SLA burn rate dashboard

  JSON API:
  GET  /api/noc/overview       — NOCC overview JSON
  GET  /api/noc/alarms         — list active alarms
  POST /api/noc/alarms         — ingest alarm from NMS adapter
  POST /api/noc/alarms/<id>/ack   — acknowledge alarm
  POST /api/noc/alarms/<id>/clear — clear alarm
  GET  /api/noc/incidents      — list incidents
  POST /api/noc/incidents      — create incident
  PUT  /api/noc/incidents/<id> — update incident
  GET  /api/noc/rfcs           — list RFCs
  POST /api/noc/rfcs           — create RFC
  POST /api/noc/mops/generate  — AI-generate MOP from RFC description
  GET  /api/noc/maintenance    — list maintenance windows
  POST /api/noc/maintenance    — create maintenance window
  GET  /api/noc/sla            — SLA dashboard JSON
  POST /api/noc/iqe-query      — IQE natural-language query endpoint
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from flask import Blueprint, jsonify, render_template, request


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_noc_canvas_blueprint() -> Blueprint:
    bp = Blueprint("noc_canvas", __name__, url_prefix="")

    # cnr-ops-01: fail-closed auth on state-changing routes (alarm ingest/ack/clear,
    # incident create/update, RFC create, MOP generate, maintenance create)
    # regardless of ICDEV_ENFORCE_CANVAS_ACCESS. Defense-in-depth alongside
    # guard_component_access.
    from tools.security.canvas_mutation_auth import require_mutation_auth
    bp.before_request(require_mutation_auth)

    # ── Page Routes ──────────────────────────────────────────────────────────

    @bp.route("/noc")
    def noc_index():
        from tools.noc_canvas.db.init_db import get_connection
        from tools.noc_canvas.noc_aggregator import get_noc_overview
        conn = get_connection()
        try:
            overview = get_noc_overview(conn)
        finally:
            conn.close()
        return render_template("noc_canvas/index.html",
                               overview=overview,
                               classification="CUI // SP-CTI")

    @bp.route("/noc/alarms")
    def noc_alarms():
        from tools.noc_canvas.db.init_db import get_connection
        from tools.noc_canvas.alarm_correlator import get_active_alarms, get_alarm_summary
        conn = get_connection()
        try:
            alarms = get_active_alarms(conn)
            summary = get_alarm_summary(conn)
        finally:
            conn.close()
        return render_template("noc_canvas/alarms.html",
                               alarms=alarms, summary=summary,
                               classification="CUI // SP-CTI")

    @bp.route("/noc/incidents")
    def noc_incidents():
        from tools.noc_canvas.db.init_db import get_connection
        conn = get_connection()
        try:
            cur = conn.execute(
                "SELECT * FROM noc_incidents ORDER BY "
                "CASE severity WHEN 'p1' THEN 0 WHEN 'p2' THEN 1 WHEN 'p3' THEN 2 ELSE 3 END, "
                "created_at DESC LIMIT 100"
            )
            cols = [d[0] for d in cur.description]
            incidents = [dict(zip(cols, row)) for row in cur.fetchall()]
        except Exception:
            incidents = []
        finally:
            conn.close()
        return render_template("noc_canvas/incidents.html",
                               incidents=incidents,
                               classification="CUI // SP-CTI")

    @bp.route("/noc/rfcs")
    def noc_rfcs():
        from tools.noc_canvas.db.init_db import get_connection
        conn = get_connection()
        try:
            cur = conn.execute("SELECT * FROM noc_rfcs ORDER BY created_at DESC LIMIT 100")
            cols = [d[0] for d in cur.description]
            rfcs = [dict(zip(cols, row)) for row in cur.fetchall()]
        except Exception:
            rfcs = []
        finally:
            conn.close()
        return render_template("noc_canvas/rfcs.html",
                               rfcs=rfcs,
                               classification="CUI // SP-CTI")

    @bp.route("/noc/mops")
    def noc_mops():
        from tools.noc_canvas.db.init_db import get_connection
        conn = get_connection()
        try:
            cur = conn.execute("SELECT * FROM noc_mops ORDER BY created_at DESC LIMIT 100")
            cols = [d[0] for d in cur.description]
            mops = [dict(zip(cols, row)) for row in cur.fetchall()]
        except Exception:
            mops = []
        finally:
            conn.close()
        return render_template("noc_canvas/mops.html",
                               mops=mops,
                               classification="CUI // SP-CTI")

    @bp.route("/noc/maintenance")
    def noc_maintenance():
        from tools.noc_canvas.db.init_db import get_connection
        from tools.noc_canvas.maintenance_planner import get_upcoming_windows
        conn = get_connection()
        try:
            cur = conn.execute(
                "SELECT * FROM noc_maintenance_windows ORDER BY scheduled_start DESC LIMIT 100"
            )
            cols = [d[0] for d in cur.description]
            windows = [dict(zip(cols, row)) for row in cur.fetchall()]
            upcoming = get_upcoming_windows(conn, days_ahead=7)
        except Exception:
            windows, upcoming = [], []
        finally:
            conn.close()
        return render_template("noc_canvas/maintenance.html",
                               windows=windows, upcoming=upcoming,
                               classification="CUI // SP-CTI")

    @bp.route("/noc/sla")
    def noc_sla():
        from tools.noc_canvas.db.init_db import get_connection
        from tools.noc_canvas.sla_predictor import get_sla_dashboard
        conn = get_connection()
        try:
            sla_data = get_sla_dashboard(conn)
        finally:
            conn.close()
        return render_template("noc_canvas/sla.html",
                               data=sla_data,
                               classification="CUI // SP-CTI")

    # ── JSON API — Alarms ────────────────────────────────────────────────────

    @bp.route("/api/noc/alarms", methods=["GET"])
    def api_noc_alarms_list():
        from tools.noc_canvas.db.init_db import get_connection
        from tools.noc_canvas.alarm_correlator import get_active_alarms, get_alarm_summary
        conn = get_connection()
        try:
            return jsonify({
                "alarms": get_active_alarms(conn),
                "summary": get_alarm_summary(conn),
            })
        finally:
            conn.close()

    @bp.route("/api/noc/alarms", methods=["POST"])
    def api_noc_alarms_create():
        from tools.noc_canvas.db.init_db import get_connection
        from tools.noc_canvas.alarm_correlator import create_alarm
        data = request.get_json(silent=True) or {}
        if not data.get("description") and not data.get("alarm_source"):
            return jsonify({"error": "alarm_source and description are required"}), 400
        conn = get_connection()
        try:
            alarm_id = create_alarm(conn, data)
            return jsonify({"id": alarm_id}), 201
        finally:
            conn.close()

    @bp.route("/api/noc/alarms/<alarm_id>/ack", methods=["POST"])
    def api_noc_alarm_ack(alarm_id):
        from tools.noc_canvas.db.init_db import get_connection
        from tools.noc_canvas.alarm_correlator import acknowledge_alarm
        user_id = (request.get_json(silent=True) or {}).get("user_id", "api")
        conn = get_connection()
        try:
            ok = acknowledge_alarm(conn, alarm_id, user_id)
            return jsonify({"success": ok})
        finally:
            conn.close()

    @bp.route("/api/noc/alarms/<alarm_id>/clear", methods=["POST"])
    def api_noc_alarm_clear(alarm_id):
        from tools.noc_canvas.db.init_db import get_connection
        conn = get_connection()
        try:
            now = _now()
            for sql, params in [
                ("UPDATE noc_alarms SET cleared = TRUE, cleared_at = %s WHERE id = %s",
                 (now, alarm_id)),
                ("UPDATE noc_alarms SET cleared = 1, cleared_at = ? WHERE id = ?",
                 (now, alarm_id)),
            ]:
                try:
                    conn.execute(sql, params)
                    conn.commit()
                    return jsonify({"success": True})
                except Exception:
                    continue
            return jsonify({"success": False}), 500
        finally:
            conn.close()

    # ── JSON API — Incidents ─────────────────────────────────────────────────

    @bp.route("/api/noc/incidents", methods=["GET"])
    def api_noc_incidents_list():
        from tools.noc_canvas.db.init_db import get_connection
        conn = get_connection()
        try:
            cur = conn.execute(
                "SELECT * FROM noc_incidents ORDER BY "
                "CASE severity WHEN 'p1' THEN 0 WHEN 'p2' THEN 1 WHEN 'p3' THEN 2 ELSE 3 END, "
                "created_at DESC LIMIT 100"
            )
            cols = [d[0] for d in cur.description]
            return jsonify([dict(zip(cols, row)) for row in cur.fetchall()])
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500
        finally:
            conn.close()

    @bp.route("/api/noc/incidents", methods=["POST"])
    def api_noc_incidents_create():
        from tools.noc_canvas.db.init_db import get_connection
        data = request.get_json(silent=True) or {}
        if not data.get("title"):
            return jsonify({"error": "title is required"}), 400
        inc_id = str(uuid.uuid4())
        now = _now()
        year = datetime.now().year
        inc_num = f"INC-{year}-{inc_id[:6].upper()}"
        conn = get_connection()
        try:
            for sql, params in [
                (
                    "INSERT INTO noc_incidents (id, incident_number, title, severity, status, "
                    "affected_circuit, affected_carrier, opened_by, assigned_to, classification, created_at, updated_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (inc_id, inc_num, data["title"],
                     data.get("severity", "p3"), data.get("status", "open"),
                     data.get("affected_circuit", ""), data.get("affected_carrier", ""),
                     data.get("opened_by", "api"), data.get("assigned_to", ""),
                     data.get("classification", "CUI"), now, now),
                ),
                (
                    "INSERT INTO noc_incidents (id, incident_number, title, severity, status, "
                    "affected_circuit, affected_carrier, opened_by, assigned_to, classification, created_at, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (inc_id, inc_num, data["title"],
                     data.get("severity", "p3"), data.get("status", "open"),
                     data.get("affected_circuit", ""), data.get("affected_carrier", ""),
                     data.get("opened_by", "api"), data.get("assigned_to", ""),
                     data.get("classification", "CUI"), now, now),
                ),
            ]:
                try:
                    conn.execute(sql, params)
                    conn.commit()
                    return jsonify({"id": inc_id, "incident_number": inc_num}), 201
                except Exception:
                    continue
            return jsonify({"error": "insert failed"}), 500
        finally:
            conn.close()

    @bp.route("/api/noc/incidents/<inc_id>", methods=["PUT"])
    def api_noc_incident_update(inc_id):
        from tools.noc_canvas.db.init_db import get_connection
        data = request.get_json(silent=True) or {}
        allowed = {"status", "severity", "assigned_to", "root_cause", "resolution",
                   "sla_breach", "mttr_minutes", "resolved_at"}
        updates = {k: v for k, v in data.items() if k in allowed}
        if not updates:
            return jsonify({"error": "no updatable fields"}), 400
        updates["updated_at"] = _now()
        conn = get_connection()
        try:
            for placeholder, params_fn in [
                ("%s", lambda k, v: (f"{k} = %s", v)),
                ("?", lambda k, v: (f"{k} = ?", v)),
            ]:
                vals = list(updates.values()) + [inc_id]
                sql_pg = f"UPDATE noc_incidents SET {', '.join([f'{k} = %s' for k in updates])} WHERE id = %s"
                sql_sq = f"UPDATE noc_incidents SET {', '.join([f'{k} = ?' for k in updates])} WHERE id = ?"
                for sql in [sql_pg, sql_sq]:
                    try:
                        conn.execute(sql, vals)
                        conn.commit()
                        return jsonify({"success": True})
                    except Exception:
                        continue
            return jsonify({"success": False}), 500
        finally:
            conn.close()

    # ── JSON API — RFCs ──────────────────────────────────────────────────────

    @bp.route("/api/noc/rfcs", methods=["GET"])
    def api_noc_rfcs_list():
        from tools.noc_canvas.db.init_db import get_connection
        conn = get_connection()
        try:
            cur = conn.execute("SELECT * FROM noc_rfcs ORDER BY created_at DESC LIMIT 100")
            cols = [d[0] for d in cur.description]
            return jsonify([dict(zip(cols, row)) for row in cur.fetchall()])
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500
        finally:
            conn.close()

    @bp.route("/api/noc/rfcs", methods=["POST"])
    def api_noc_rfcs_create():
        from tools.noc_canvas.db.init_db import get_connection
        data = request.get_json(silent=True) or {}
        if not data.get("title"):
            return jsonify({"error": "title is required"}), 400
        rfc_id = str(uuid.uuid4())
        now = _now()
        rfc_num = f"RFC-{datetime.now().year}-{rfc_id[:6].upper()}"
        conn = get_connection()
        try:
            for sql, params in [
                (
                    "INSERT INTO noc_rfcs (id, rfc_number, title, change_type, status, risk_level, "
                    "rollback_plan, change_owner, classification, created_at, updated_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (rfc_id, rfc_num, data["title"],
                     data.get("change_type", "standard"), "draft",
                     data.get("risk_level", "medium"),
                     data.get("rollback_plan", ""), data.get("change_owner", ""),
                     data.get("classification", "CUI"), now, now),
                ),
                (
                    "INSERT INTO noc_rfcs (id, rfc_number, title, change_type, status, risk_level, "
                    "rollback_plan, change_owner, classification, created_at, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (rfc_id, rfc_num, data["title"],
                     data.get("change_type", "standard"), "draft",
                     data.get("risk_level", "medium"),
                     data.get("rollback_plan", ""), data.get("change_owner", ""),
                     data.get("classification", "CUI"), now, now),
                ),
            ]:
                try:
                    conn.execute(sql, params)
                    conn.commit()
                    return jsonify({"id": rfc_id, "rfc_number": rfc_num}), 201
                except Exception:
                    continue
            return jsonify({"error": "insert failed"}), 500
        finally:
            conn.close()

    # ── JSON API — MOPs ──────────────────────────────────────────────────────

    @bp.route("/api/noc/mops/generate", methods=["POST"])
    def api_noc_mop_generate():
        from tools.noc_canvas.db.init_db import get_connection
        from tools.noc_canvas.mop_generator import generate_mop, save_mop
        data = request.get_json(silent=True) or {}
        rfc_id = data.get("rfc_id", "")
        rfc_title = data.get("title", "Network change")
        conn = get_connection()
        try:
            rfc = {"title": rfc_title,
                   "change_type": data.get("change_type", "standard"),
                   "risk_level": data.get("risk_level", "medium")}
            mop = generate_mop(rfc, data.get("context", ""))
            mop["title"] = f"MOP for {rfc_title}"
            mop_id = save_mop(conn, rfc_id, mop)
            return jsonify({"mop_id": mop_id, "mop_number": mop["mop_number"],
                            "steps": mop["steps"], "generated_by": mop["generated_by"]}), 201
        finally:
            conn.close()

    # ── JSON API — Maintenance ───────────────────────────────────────────────

    @bp.route("/api/noc/maintenance", methods=["GET"])
    def api_noc_maintenance_list():
        from tools.noc_canvas.db.init_db import get_connection
        from tools.noc_canvas.maintenance_planner import get_upcoming_windows
        conn = get_connection()
        try:
            return jsonify(get_upcoming_windows(conn, days_ahead=30))
        finally:
            conn.close()

    @bp.route("/api/noc/maintenance", methods=["POST"])
    def api_noc_maintenance_create():
        from tools.noc_canvas.db.init_db import get_connection
        from tools.noc_canvas.maintenance_planner import create_window, check_window_conflicts
        data = request.get_json(silent=True) or {}
        if not data.get("title") or not data.get("scheduled_start") or not data.get("scheduled_end"):
            return jsonify({"error": "title, scheduled_start, scheduled_end required"}), 400
        conn = get_connection()
        try:
            conflicts = check_window_conflicts(
                conn, data["scheduled_start"], data["scheduled_end"],
                data.get("affected_circuits", [])
            )
            win_id = create_window(conn, data)
            return jsonify({"id": win_id, "conflicts": conflicts}), 201
        finally:
            conn.close()

    # ── JSON API — SLA ───────────────────────────────────────────────────────

    @bp.route("/api/noc/sla", methods=["GET"])
    def api_noc_sla():
        from tools.noc_canvas.db.init_db import get_connection
        from tools.noc_canvas.sla_predictor import get_sla_dashboard
        conn = get_connection()
        try:
            return jsonify(get_sla_dashboard(conn))
        finally:
            conn.close()

    # ── JSON API — Overview ──────────────────────────────────────────────────

    @bp.route("/api/noc/overview", methods=["GET"])
    def api_noc_overview():
        from tools.noc_canvas.db.init_db import get_connection
        from tools.noc_canvas.noc_aggregator import get_noc_overview
        conn = get_connection()
        try:
            return jsonify(get_noc_overview(conn))
        finally:
            conn.close()

    # ── Looking Glass ─────────────────────────────────────────────────────────

    @bp.route("/noc/looking-glass")
    def noc_looking_glass():
        # cnr-ops-02: validate HYPERGLASS_URL before embedding it in an iframe.
        # An unvalidated env value could carry a javascript:/data: scheme (XSS) or
        # a malformed URL. Only embed a well-formed http(s) URL; otherwise fall back
        # to the friendly not-configured state and flag the misconfiguration.
        import os
        from urllib.parse import urlparse
        raw = os.environ.get("HYPERGLASS_URL", "").strip()
        hyperglass_url = ""
        hyperglass_invalid = False
        if raw:
            parsed = urlparse(raw)
            if parsed.scheme in ("http", "https") and parsed.netloc:
                hyperglass_url = raw
            else:
                hyperglass_invalid = True
        return render_template("noc_canvas/looking_glass.html",
                               hyperglass_url=hyperglass_url,
                               hyperglass_invalid=hyperglass_invalid,
                               classification="CUI // SP-CTI")

    # ── JSON API — IQE ───────────────────────────────────────────────────────

    @bp.route("/api/noc/iqe-query", methods=["POST"])
    def api_noc_iqe_query():
        import tools.iqe.adapters.nocc  # noqa: F401 — registers collections
        from tools.iqe.executor import execute_nl_query
        data = request.get_json(silent=True) or {}
        q = data.get("q") or data.get("query", "")
        if not q:
            return jsonify({"error": "query required"}), 400
        try:
            result = execute_nl_query(q, canvas="nocc")
            return jsonify(result)
        except Exception as exc:
            return jsonify({"error": str(exc), "rows": []}), 200

    return bp
