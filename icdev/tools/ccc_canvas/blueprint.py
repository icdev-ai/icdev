# CUI // SP-CTI
"""Circuit & Capacity Canvas (CCC) Flask Blueprint.

Routes:
  GET  /ccc                      — CCC overview (circuit health + capacity summary)
  GET  /ccc/circuits             — circuit inventory
  GET  /ccc/cross-connects       — cross-connect list
  GET  /ccc/loa                  — LOA request tracker
  GET  /ccc/capacity             — capacity planning dashboard
  GET  /ccc/dwdm                 — DWDM span inventory

  JSON API:
  GET  /api/ccc/overview         — CCC overview JSON
  GET  /api/ccc/circuits         — list circuits
  POST /api/ccc/circuits         — add circuit
  PUT  /api/ccc/circuits/<id>    — update circuit
  POST /api/ccc/circuits/<id>/refresh-utilization — recompute capacity plan
  GET  /api/ccc/cross-connects   — list cross-connects
  POST /api/ccc/cross-connects   — add cross-connect
  GET  /api/ccc/loa              — list LOA requests
  POST /api/ccc/loa              — create LOA request
  GET  /api/ccc/loa/<id>/document — generate LOA text document
  GET  /api/ccc/capacity/report  — run capacity analysis for all circuits
  GET  /api/ccc/dwdm             — list DWDM spans
  POST /api/ccc/dwdm             — add DWDM span
  POST /api/ccc/iqe-query        — IQE natural-language query endpoint
"""
from __future__ import annotations

from datetime import datetime, timezone

from flask import Blueprint, jsonify, render_template, request


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_ccc_blueprint() -> Blueprint:
    bp = Blueprint("ccc_canvas", __name__, url_prefix="")

    # ── Page Routes ──────────────────────────────────────────────────────────

    @bp.route("/ccc")
    def ccc_index():
        from tools.ccc_canvas.db.init_db import get_connection
        from tools.ccc_canvas.circuit_aggregator import get_ccc_overview
        conn = get_connection()
        try:
            overview = get_ccc_overview(conn)
        except Exception:
            overview = {}
        finally:
            conn.close()
        return render_template("ccc_canvas/index.html", overview=overview)

    @bp.route("/ccc/circuits")
    def ccc_circuits():
        from tools.ccc_canvas.db.init_db import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT * FROM ccc_circuits ORDER BY status, carrier, circuit_id"
            ).fetchall()
            circuits = [dict(r) for r in rows]
        except Exception:
            circuits = []
        finally:
            conn.close()
        return render_template("ccc_canvas/circuits.html", circuits=circuits)

    @bp.route("/ccc/cross-connects")
    def ccc_cross_connects():
        from tools.ccc_canvas.db.init_db import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT *, (SELECT circuit_id FROM ccc_circuits WHERE id=ccc_cross_connects.circuit_id) AS linked_circuit "
                "FROM ccc_cross_connects ORDER BY facility, xc_number"
            ).fetchall()
            xcs = [dict(r) for r in rows]
        except Exception:
            xcs = []
        finally:
            conn.close()
        return render_template("ccc_canvas/cross_connects.html", xcs=xcs)

    @bp.route("/ccc/loa")
    def ccc_loa():
        from tools.ccc_canvas.db.init_db import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT * FROM ccc_loa_requests ORDER BY created_at DESC"
            ).fetchall()
            loas = [dict(r) for r in rows]
        except Exception:
            loas = []
        finally:
            conn.close()
        return render_template("ccc_canvas/loa.html", loas=loas)

    @bp.route("/ccc/capacity")
    def ccc_capacity():
        from tools.ccc_canvas.db.init_db import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT cp.*, c.circuit_id, c.carrier, c.bandwidth_gbps "
                "FROM ccc_capacity_plans cp JOIN ccc_circuits c ON c.id=cp.circuit_id "
                "ORDER BY cp.plan_date DESC, cp.current_utilization_pct DESC LIMIT 200"
            ).fetchall()
            plans = [dict(r) for r in rows]
        except Exception:
            plans = []
        finally:
            conn.close()
        return render_template("ccc_canvas/capacity.html", plans=plans)

    @bp.route("/ccc/dwdm")
    def ccc_dwdm():
        from tools.ccc_canvas.db.init_db import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT * FROM ccc_dwdm_spans ORDER BY status, span_id"
            ).fetchall()
            spans = [dict(r) for r in rows]
        except Exception:
            spans = []
        finally:
            conn.close()
        return render_template("ccc_canvas/dwdm.html", spans=spans)

    # ── JSON API ─────────────────────────────────────────────────────────────

    @bp.route("/api/ccc/overview")
    def api_ccc_overview():
        from tools.ccc_canvas.db.init_db import get_connection
        from tools.ccc_canvas.circuit_aggregator import get_ccc_overview
        conn = get_connection()
        try:
            return jsonify(get_ccc_overview(conn))
        finally:
            conn.close()

    @bp.route("/api/ccc/circuits", methods=["GET"])
    def api_ccc_circuits_list():
        from tools.ccc_canvas.db.init_db import get_connection
        conn = get_connection()
        try:
            status_f = request.args.get("status", "")
            if status_f:
                try:
                    rows = conn.execute("SELECT * FROM ccc_circuits WHERE status=%s ORDER BY carrier", (status_f,)).fetchall()
                except Exception:
                    rows = conn.execute("SELECT * FROM ccc_circuits WHERE status=%s ORDER BY carrier", (status_f,)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM ccc_circuits ORDER BY carrier, circuit_id").fetchall()
            return jsonify([dict(r) for r in rows])
        finally:
            conn.close()

    @bp.route("/api/ccc/circuits", methods=["POST"])
    def api_ccc_circuits_create():
        from tools.ccc_canvas.db.init_db import get_connection
        data = request.get_json(force=True) or {}
        required = ("circuit_id", "circuit_type", "carrier")
        missing = [f for f in required if not data.get(f)]
        if missing:
            return jsonify({"error": f"Missing: {', '.join(missing)}"}), 400
        conn = get_connection()
        try:
            fields = {
                "circuit_id":   data["circuit_id"],
                "circuit_type": data["circuit_type"],
                "carrier":      data["carrier"],
                "bandwidth_gbps": float(data.get("bandwidth_gbps", 0)),
                "endpoint_a":   data.get("endpoint_a", ""),
                "endpoint_z":   data.get("endpoint_z", ""),
                "status":       data.get("status", "active"),
                "provider_circuit_id": data.get("provider_circuit_id", ""),
                "mrr_usd":      float(data.get("mrr_usd", 0)),
                "install_date": data.get("install_date", ""),
                "contract_end": data.get("contract_end", ""),
                "utilization_pct": float(data.get("utilization_pct", 0)),
            }
            cols = ", ".join(fields.keys())
            try:
                placeholders = ", ".join(["%s"] * len(fields))
                conn.execute(f"INSERT INTO ccc_circuits ({cols}) VALUES ({placeholders})", tuple(fields.values()))
            except Exception:
                placeholders = ", ".join("%s" * len(fields))
                conn.execute(f"INSERT INTO ccc_circuits ({cols}) VALUES ({placeholders})", tuple(fields.values()))
            conn.commit()
            return jsonify({"status": "created", "circuit_id": data["circuit_id"]}), 201
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500
        finally:
            conn.close()

    @bp.route("/api/ccc/circuits/<circuit_id>", methods=["PUT"])
    def api_ccc_circuits_update(circuit_id):
        from tools.ccc_canvas.db.init_db import get_connection
        data = request.get_json(force=True) or {}
        allowed = {"status", "bandwidth_gbps", "mrr_usd", "utilization_pct",
                   "endpoint_a", "endpoint_z", "contract_end", "provider_circuit_id"}
        updates = {k: v for k, v in data.items() if k in allowed}
        if not updates:
            return jsonify({"error": "No valid fields to update"}), 400
        conn = get_connection()
        try:
            updates["updated_at"] = _now()
            set_clause = ", ".join(f"{k}=?" for k in updates)
            vals = list(updates.values()) + [circuit_id]
            try:
                conn.execute(f"UPDATE ccc_circuits SET {set_clause} WHERE circuit_id=%s", vals)
            except Exception:
                set_clause = ", ".join(f"{k}=%s" for k in updates)
                conn.execute(f"UPDATE ccc_circuits SET {set_clause} WHERE circuit_id=%s", vals)
            conn.commit()
            return jsonify({"status": "updated", "circuit_id": circuit_id})
        finally:
            conn.close()

    @bp.route("/api/ccc/circuits/<int:circuit_pk>/refresh-utilization", methods=["POST"])
    def api_ccc_capacity_refresh(circuit_pk):
        from tools.ccc_canvas.db.init_db import get_connection
        from tools.ccc_canvas.capacity_engine import analyze_circuit
        conn = get_connection()
        try:
            plan = analyze_circuit(conn, circuit_pk)
            return jsonify(plan)
        finally:
            conn.close()

    @bp.route("/api/ccc/cross-connects", methods=["GET"])
    def api_ccc_xc_list():
        from tools.ccc_canvas.db.init_db import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT *, (SELECT circuit_id FROM ccc_circuits WHERE id=ccc_cross_connects.circuit_id) AS linked_circuit "
                "FROM ccc_cross_connects ORDER BY facility, xc_number"
            ).fetchall()
            return jsonify([dict(r) for r in rows])
        finally:
            conn.close()

    @bp.route("/api/ccc/cross-connects", methods=["POST"])
    def api_ccc_xc_create():
        from tools.ccc_canvas.db.init_db import get_connection
        data = request.get_json(force=True) or {}
        if not data.get("xc_number") or not data.get("facility"):
            return jsonify({"error": "xc_number and facility required"}), 400
        conn = get_connection()
        try:
            fields = {
                "xc_number":        data["xc_number"],
                "facility":         data["facility"],
                "facility_id":      data.get("facility_id", ""),
                "port_a":           data.get("port_a", ""),
                "port_z":           data.get("port_z", ""),
                "speed_gbps":       float(data.get("speed_gbps", 1)),
                "status":           data.get("status", "active"),
                "circuit_id":       data.get("circuit_id"),
                "monthly_cost_usd": float(data.get("monthly_cost_usd", 0)),
                "patch_panel":      data.get("patch_panel", ""),
                "notes":            data.get("notes", ""),
            }
            cols = ", ".join(fields.keys())
            try:
                placeholders = ", ".join(["%s"] * len(fields))
                conn.execute(f"INSERT INTO ccc_cross_connects ({cols}) VALUES ({placeholders})", tuple(fields.values()))
            except Exception:
                placeholders = ", ".join("%s" * len(fields))
                conn.execute(f"INSERT INTO ccc_cross_connects ({cols}) VALUES ({placeholders})", tuple(fields.values()))
            conn.commit()
            return jsonify({"status": "created", "xc_number": data["xc_number"]}), 201
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500
        finally:
            conn.close()

    @bp.route("/api/ccc/loa", methods=["GET"])
    def api_ccc_loa_list():
        from tools.ccc_canvas.db.init_db import get_connection
        conn = get_connection()
        try:
            rows = conn.execute("SELECT * FROM ccc_loa_requests ORDER BY created_at DESC").fetchall()
            return jsonify([dict(r) for r in rows])
        finally:
            conn.close()

    @bp.route("/api/ccc/loa", methods=["POST"])
    def api_ccc_loa_create():
        from tools.ccc_canvas.db.init_db import get_connection
        from tools.ccc_canvas.loa_workflow import create_loa_request
        data = request.get_json(force=True) or {}
        if not data.get("facility"):
            return jsonify({"error": "facility required"}), 400
        conn = get_connection()
        try:
            result = create_loa_request(conn, data)
            return jsonify(result), 201
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500
        finally:
            conn.close()

    @bp.route("/api/ccc/loa/<int:loa_id>/document")
    def api_ccc_loa_document(loa_id):
        from tools.ccc_canvas.db.init_db import get_connection
        from tools.ccc_canvas.loa_workflow import generate_loa_text
        conn = get_connection()
        try:
            text = generate_loa_text(conn, loa_id)
            return jsonify({"loa_id": loa_id, "document": text})
        finally:
            conn.close()

    # ── Cross-Connect Order Workflow ──────────────────────────────────────

    @bp.route("/ccc/orders")
    def ccc_orders_page():
        return render_template("ccc_canvas/orders.html")

    @bp.route("/api/ccc/cross-connect-orders", methods=["GET"])
    def api_ccc_xc_orders_list():
        from tools.ccc_canvas.db.init_db import get_connection
        from tools.ccc_canvas.xc_order_manager import list_in_flight_orders
        conn = get_connection()
        try:
            return jsonify(list_in_flight_orders(conn))
        finally:
            conn.close()

    @bp.route("/api/ccc/cross-connects/<int:xc_id>/order", methods=["POST"])
    def api_ccc_xc_order_create(xc_id):
        from tools.ccc_canvas.db.init_db import get_connection
        from tools.ccc_canvas.xc_order_manager import create_cross_connect_order
        data = request.get_json(force=True) or {}
        ordered_by = data.get("ordered_by", "noc")
        conn = get_connection()
        try:
            result = create_cross_connect_order(conn, xc_id, ordered_by)
            return jsonify(result), 201
        except ValueError as e:
            return jsonify({"error": str(e)}), 404
        except Exception as e:
            return jsonify({"error": str(e)}), 500
        finally:
            conn.close()

    @bp.route("/api/ccc/cross-connects/<int:xc_id>/cancel-order", methods=["POST"])
    def api_ccc_xc_order_cancel(xc_id):
        from tools.ccc_canvas.db.init_db import get_connection
        from tools.ccc_canvas.xc_order_manager import cancel_order
        data = request.get_json(force=True) or {}
        reason = data.get("reason", "")
        actor = data.get("actor", "noc")
        conn = get_connection()
        try:
            result = cancel_order(conn, xc_id, reason, actor)
            return jsonify(result)
        except ValueError as e:
            return jsonify({"error": str(e)}), 404
        finally:
            conn.close()

    @bp.route("/api/ccc/cross-connects/<int:xc_id>/order-status", methods=["GET"])
    def api_ccc_xc_order_status(xc_id):
        from tools.ccc_canvas.db.init_db import get_connection
        from tools.ccc_canvas.xc_order_manager import poll_order_status
        conn = get_connection()
        try:
            result = poll_order_status(conn, xc_id)
            return jsonify(result)
        except ValueError as e:
            return jsonify({"error": str(e)}), 404
        finally:
            conn.close()

    @bp.route("/api/ccc/cross-connects/<int:xc_id>/generate-loa", methods=["POST"])
    def api_ccc_xc_generate_loa(xc_id):
        from tools.ccc_canvas.db.init_db import get_connection
        from tools.ccc_canvas.loa_workflow import create_loa_request, generate_loa_for_facility
        data = request.get_json(force=True) or {}
        conn = get_connection()
        try:
            try:
                xc = conn.execute("SELECT * FROM ccc_cross_connects WHERE id=%s", (xc_id,)).fetchone()
            except Exception:
                xc = conn.execute("SELECT * FROM ccc_cross_connects WHERE id=%s", (xc_id,)).fetchone()
            if not xc:
                return jsonify({"error": "cross-connect not found"}), 404
            xc_d = dict(xc)
            loa_data = {
                "facility": xc_d.get("facility", "other"),
                "xc_id": xc_id,
                "requester_name": data.get("requester_name", "NOC"),
                "requester_email": data.get("requester_email", ""),
                "requester_company": data.get("requester_company", ""),
                "rack_a": data.get("rack_a", ""),
                "rack_z": data.get("rack_z", ""),
                "patch_panel_a": data.get("patch_panel_a", ""),
                "patch_panel_z": data.get("patch_panel_z", ""),
                "notes": data.get("notes", ""),
            }
            loa_result = create_loa_request(conn, loa_data)
            _loa_id = loa_result.get("loa_id") or loa_result.get("loa_number")  # noqa: F841
            # Fetch the numeric id for generate_loa_for_facility
            try:
                loa_row = conn.execute("SELECT id FROM ccc_loa_requests WHERE loa_number=%s", (loa_result.get("loa_number"),)).fetchone()
            except Exception:
                loa_row = conn.execute("SELECT id FROM ccc_loa_requests WHERE loa_number=%s", (loa_result.get("loa_number"),)).fetchone()
            loa_numeric_id = loa_row[0] if loa_row else None
            doc = generate_loa_for_facility(conn, loa_numeric_id, xc_d.get("facility", "generic"))
            return jsonify({
                "loa_number": loa_result.get("loa_number"),
                "loa_id": loa_numeric_id,
                "document": doc,
            }), 201
        except Exception as e:
            return jsonify({"error": str(e)}), 500
        finally:
            conn.close()

    @bp.route("/api/ccc/capacity/report")
    def api_ccc_capacity_report():
        from tools.ccc_canvas.db.init_db import get_connection
        from tools.ccc_canvas.capacity_engine import run_all_circuits
        conn = get_connection()
        try:
            plans = run_all_circuits(conn)
            return jsonify({"plans": plans, "count": len(plans), "generated_at": _now()})
        finally:
            conn.close()

    @bp.route("/api/ccc/dwdm", methods=["GET"])
    def api_ccc_dwdm_list():
        from tools.ccc_canvas.db.init_db import get_connection
        conn = get_connection()
        try:
            rows = conn.execute("SELECT * FROM ccc_dwdm_spans ORDER BY status, span_id").fetchall()
            return jsonify([dict(r) for r in rows])
        finally:
            conn.close()

    @bp.route("/api/ccc/dwdm", methods=["POST"])
    def api_ccc_dwdm_create():
        from tools.ccc_canvas.db.init_db import get_connection
        data = request.get_json(force=True) or {}
        if not data.get("span_id"):
            return jsonify({"error": "span_id required"}), 400
        conn = get_connection()
        try:
            fields = {
                "span_id":              data["span_id"],
                "fiber_route":          data.get("fiber_route", ""),
                "total_capacity_tbps":  float(data.get("total_capacity_tbps", 0)),
                "used_capacity_tbps":   float(data.get("used_capacity_tbps", 0)),
                "available_wavelengths": int(data.get("available_wavelengths", 0)),
                "amplifier_count":      int(data.get("amplifier_count", 0)),
                "osnr_db":              float(data.get("osnr_db", 0)),
                "status":               data.get("status", "operational"),
                "notes":                data.get("notes", ""),
            }
            cols = ", ".join(fields.keys())
            try:
                placeholders = ", ".join(["%s"] * len(fields))
                conn.execute(f"INSERT INTO ccc_dwdm_spans ({cols}) VALUES ({placeholders})", tuple(fields.values()))
            except Exception:
                placeholders = ", ".join("%s" * len(fields))
                conn.execute(f"INSERT INTO ccc_dwdm_spans ({cols}) VALUES ({placeholders})", tuple(fields.values()))
            conn.commit()
            return jsonify({"status": "created", "span_id": data["span_id"]}), 201
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500
        finally:
            conn.close()

    @bp.route("/api/ccc/iqe-query", methods=["POST"])
    def api_ccc_iqe_query():
        from tools.ccc_canvas.db.init_db import get_connection
        from tools.iqe.adapters.ccc import handle_query
        data = request.get_json(force=True) or {}
        q = data.get("q", "").strip()
        if not q:
            return jsonify({"error": "q required"}), 400
        conn = get_connection()
        try:
            result = handle_query(conn, q)
            return jsonify(result)
        finally:
            conn.close()

    return bp
