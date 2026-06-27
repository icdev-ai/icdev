# CUI // SP-CTI
"""DDoS & Security Ops Canvas (DSOC) Flask Blueprint.

Routes:
  GET  /dsoc                          — DSOC overview (threat summary + mitigation stats)
  GET  /dsoc/flowspec                 — FlowSpec rule inventory
  GET  /dsoc/rtbh                     — RTBH (Remotely Triggered Black Hole) entry list
  GET  /dsoc/scrubbing                — Scrubbing center inventory
  GET  /dsoc/threats                  — Active threat feed
  GET  /dsoc/mitigations              — Mitigation tracker

  JSON API:
  GET  /api/dsoc/overview             — DSOC overview JSON
  GET  /api/dsoc/flowspec             — list FlowSpec rules
  POST /api/dsoc/flowspec             — add FlowSpec rule
  PUT  /api/dsoc/flowspec/<id>/withdraw — withdraw a FlowSpec rule
  GET  /api/dsoc/rtbh                 — list RTBH entries
  POST /api/dsoc/rtbh                 — trigger RTBH
  POST /api/dsoc/rtbh/<id>/withdraw   — withdraw RTBH entry
  GET  /api/dsoc/scrubbing            — list scrubbing centers
  POST /api/dsoc/scrubbing            — add scrubbing center
  POST /api/dsoc/scrubbing/<id>/update-load — update current load (gbps)
  GET  /api/dsoc/threats              — list threats
  POST /api/dsoc/threats              — add threat
  GET  /api/dsoc/mitigations          — list mitigations
  POST /api/dsoc/mitigations          — create mitigation
  POST /api/dsoc/mitigations/<id>/complete — mark mitigation completed
  POST /api/dsoc/iqe-query            — IQE natural-language query endpoint
"""
from __future__ import annotations

from datetime import datetime, timezone

from flask import Blueprint, jsonify, render_template, request


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_dsoc_blueprint() -> Blueprint:
    bp = Blueprint("dsoc_canvas", __name__, url_prefix="")

    # ── Page Routes ──────────────────────────────────────────────────────────

    @bp.route("/dsoc")
    def dsoc_index():
        from tools.dsoc_canvas.db.init_db import get_connection
        from tools.dsoc_canvas.dsoc_aggregator import get_dsoc_overview
        conn = get_connection()
        try:
            overview = get_dsoc_overview(conn)
        except Exception:
            overview = {}
        finally:
            conn.close()
        return render_template("dsoc_canvas/index.html", overview=overview)

    @bp.route("/dsoc/flowspec")
    def dsoc_flowspec():
        from tools.dsoc_canvas.db.init_db import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT * FROM dsoc_flowspec_rules ORDER BY created_at DESC"
            ).fetchall()
            rules = [dict(r) for r in rows]
        except Exception:
            rules = []
        finally:
            conn.close()
        return render_template("dsoc_canvas/flowspec.html", rules=rules)

    @bp.route("/dsoc/rtbh")
    def dsoc_rtbh():
        from tools.dsoc_canvas.db.init_db import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT * FROM dsoc_rtbh_entries ORDER BY created_at DESC"
            ).fetchall()
            entries = [dict(r) for r in rows]
        except Exception:
            entries = []
        finally:
            conn.close()
        return render_template("dsoc_canvas/rtbh.html", entries=entries)

    @bp.route("/dsoc/scrubbing")
    def dsoc_scrubbing():
        from tools.dsoc_canvas.db.init_db import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT * FROM dsoc_scrubbing_centers ORDER BY status, name"
            ).fetchall()
            centers = [dict(r) for r in rows]
        except Exception:
            centers = []
        finally:
            conn.close()
        return render_template("dsoc_canvas/scrubbing.html", centers=centers)

    @bp.route("/dsoc/threats")
    def dsoc_threats():
        from tools.dsoc_canvas.db.init_db import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT * FROM dsoc_threats ORDER BY last_seen DESC, confidence_pct DESC"
            ).fetchall()
            threats = [dict(r) for r in rows]
        except Exception:
            threats = []
        finally:
            conn.close()
        return render_template("dsoc_canvas/threats.html", threats=threats)

    @bp.route("/dsoc/mitigations")
    def dsoc_mitigations():
        from tools.dsoc_canvas.db.init_db import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT *, "
                "(SELECT name FROM dsoc_scrubbing_centers WHERE id=dsoc_mitigations.scrubbing_center_id) AS scrubbing_center_name, "
                "(SELECT rule_name FROM dsoc_flowspec_rules WHERE id=dsoc_mitigations.flowspec_rule_id) AS flowspec_rule_name "
                "FROM dsoc_mitigations ORDER BY started_at DESC"
            ).fetchall()
            mitigations = [dict(r) for r in rows]
        except Exception:
            mitigations = []
        finally:
            conn.close()
        return render_template("dsoc_canvas/mitigations.html", mitigations=mitigations)

    # ── JSON API ─────────────────────────────────────────────────────────────

    @bp.route("/api/dsoc/overview")
    def api_dsoc_overview():
        from tools.dsoc_canvas.db.init_db import get_connection
        from tools.dsoc_canvas.dsoc_aggregator import get_dsoc_overview
        conn = get_connection()
        try:
            return jsonify(get_dsoc_overview(conn))
        finally:
            conn.close()

    @bp.route("/api/dsoc/flowspec", methods=["GET"])
    def api_dsoc_flowspec_list():
        from tools.dsoc_canvas.db.init_db import get_connection
        conn = get_connection()
        try:
            action_f = request.args.get("action", "")
            if action_f:
                try:
                    rows = conn.execute(
                        "SELECT * FROM dsoc_flowspec_rules WHERE action=%s ORDER BY created_at DESC",
                        (action_f,)
                    ).fetchall()
                except Exception:
                    rows = conn.execute(
                        "SELECT * FROM dsoc_flowspec_rules WHERE action=%s ORDER BY created_at DESC",
                        (action_f,)
                    ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM dsoc_flowspec_rules ORDER BY created_at DESC"
                ).fetchall()
            return jsonify([dict(r) for r in rows])
        finally:
            conn.close()

    @bp.route("/api/dsoc/flowspec", methods=["POST"])
    def api_dsoc_flowspec_create():
        from tools.dsoc_canvas.db.init_db import get_connection
        data = request.get_json(force=True) or {}
        required = ("rule_name", "action")
        missing = [f for f in required if not data.get(f)]
        if missing:
            return jsonify({"error": f"Missing: {', '.join(missing)}"}), 400
        conn = get_connection()
        try:
            fields = {
                "rule_name":         data["rule_name"],
                "destination_prefix": data.get("destination_prefix", ""),
                "source_prefix":     data.get("source_prefix", ""),
                "protocol":          data.get("protocol", ""),
                "dst_port":          data.get("dst_port", ""),
                "src_port":          data.get("src_port", ""),
                "packet_length":     data.get("packet_length", ""),
                "dscp":              data.get("dscp", ""),
                "action":            data["action"],
                "rate_limit_bps":    int(data.get("rate_limit_bps", 0)),
                "redirect_vrf":      data.get("redirect_vrf", ""),
                "community":         data.get("community", ""),
                "applied_routers":   data.get("applied_routers", ""),
                "expires_at":        data.get("expires_at", ""),
                "created_by":        data.get("created_by", "system"),
            }
            cols = ", ".join(fields.keys())
            try:
                placeholders = ", ".join("?" * len(fields))
                conn.execute(
                    f"INSERT INTO dsoc_flowspec_rules ({cols}) VALUES ({placeholders})",
                    tuple(fields.values())
                )
            except Exception:
                placeholders = ", ".join("%s" * len(fields))
                conn.execute(
                    f"INSERT INTO dsoc_flowspec_rules ({cols}) VALUES ({placeholders})",
                    tuple(fields.values())
                )
            conn.commit()
            return jsonify({"status": "created", "rule_name": data["rule_name"]}), 201
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500
        finally:
            conn.close()

    @bp.route("/api/dsoc/flowspec/<int:rule_id>/withdraw", methods=["PUT"])
    def api_dsoc_flowspec_withdraw(rule_id):
        from tools.dsoc_canvas.db.init_db import get_connection
        from tools.dsoc_canvas.flowspec_engine import withdraw_rule
        conn = get_connection()
        try:
            result = withdraw_rule(conn, rule_id)
            return jsonify(result)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500
        finally:
            conn.close()

    @bp.route("/api/dsoc/rtbh", methods=["GET"])
    def api_dsoc_rtbh_list():
        from tools.dsoc_canvas.db.init_db import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT * FROM dsoc_rtbh_entries ORDER BY created_at DESC"
            ).fetchall()
            return jsonify([dict(r) for r in rows])
        finally:
            conn.close()

    @bp.route("/api/dsoc/rtbh", methods=["POST"])
    def api_dsoc_rtbh_create():
        from tools.dsoc_canvas.db.init_db import get_connection
        from tools.dsoc_canvas.rtbh_manager import trigger_rtbh
        data = request.get_json(force=True) or {}
        required = ("prefix", "trigger_reason")
        missing = [f for f in required if not data.get(f)]
        if missing:
            return jsonify({"error": f"Missing: {', '.join(missing)}"}), 400
        conn = get_connection()
        try:
            result = trigger_rtbh(conn, data)
            return jsonify(result), 201
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500
        finally:
            conn.close()

    @bp.route("/api/dsoc/rtbh/<int:rtbh_id>/withdraw", methods=["POST"])
    def api_dsoc_rtbh_withdraw(rtbh_id):
        from tools.dsoc_canvas.db.init_db import get_connection
        from tools.dsoc_canvas.rtbh_manager import withdraw_rtbh
        conn = get_connection()
        try:
            result = withdraw_rtbh(conn, rtbh_id)
            return jsonify(result)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500
        finally:
            conn.close()

    @bp.route("/api/dsoc/scrubbing", methods=["GET"])
    def api_dsoc_scrubbing_list():
        from tools.dsoc_canvas.db.init_db import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT * FROM dsoc_scrubbing_centers ORDER BY status, name"
            ).fetchall()
            return jsonify([dict(r) for r in rows])
        finally:
            conn.close()

    @bp.route("/api/dsoc/scrubbing", methods=["POST"])
    def api_dsoc_scrubbing_create():
        from tools.dsoc_canvas.db.init_db import get_connection
        data = request.get_json(force=True) or {}
        required = ("name",)
        missing = [f for f in required if not data.get(f)]
        if missing:
            return jsonify({"error": f"Missing: {', '.join(missing)}"}), 400
        conn = get_connection()
        try:
            fields = {
                "name":                data["name"],
                "location":            data.get("location", ""),
                "provider":            data.get("provider", ""),
                "capacity_gbps":       float(data.get("capacity_gbps", 0)),
                "current_load_gbps":   float(data.get("current_load_gbps", 0)),
                "status":              data.get("status", "operational"),
                "upstream_links":      data.get("upstream_links", ""),
                "anycast_prefix":      data.get("anycast_prefix", ""),
            }
            cols = ", ".join(fields.keys())
            try:
                placeholders = ", ".join("?" * len(fields))
                conn.execute(
                    f"INSERT INTO dsoc_scrubbing_centers ({cols}) VALUES ({placeholders})",
                    tuple(fields.values())
                )
            except Exception:
                placeholders = ", ".join("%s" * len(fields))
                conn.execute(
                    f"INSERT INTO dsoc_scrubbing_centers ({cols}) VALUES ({placeholders})",
                    tuple(fields.values())
                )
            conn.commit()
            return jsonify({"status": "created", "name": data["name"]}), 201
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500
        finally:
            conn.close()

    @bp.route("/api/dsoc/scrubbing/<int:center_id>/update-load", methods=["POST"])
    def api_dsoc_scrubbing_update_load(center_id):
        from tools.dsoc_canvas.db.init_db import get_connection
        data = request.get_json(force=True) or {}
        if "current_load_gbps" not in data:
            return jsonify({"error": "Missing: current_load_gbps"}), 400
        conn = get_connection()
        try:
            load = float(data["current_load_gbps"])
            try:
                conn.execute(
                    "UPDATE dsoc_scrubbing_centers SET current_load_gbps=%s WHERE id=%s",
                    (load, center_id)
                )
            except Exception:
                conn.execute(
                    "UPDATE dsoc_scrubbing_centers SET current_load_gbps=%s WHERE id=%s",
                    (load, center_id)
                )
            conn.commit()
            return jsonify({"status": "updated", "id": center_id, "current_load_gbps": load})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500
        finally:
            conn.close()

    @bp.route("/api/dsoc/threats", methods=["GET"])
    def api_dsoc_threats_list():
        from tools.dsoc_canvas.db.init_db import get_connection
        conn = get_connection()
        try:
            active_only = request.args.get("active", "")
            if active_only.lower() in ("1", "true", "yes"):
                try:
                    rows = conn.execute(
                        "SELECT * FROM dsoc_threats WHERE is_active=%s ORDER BY last_seen DESC",
                        (1,)
                    ).fetchall()
                except Exception:
                    rows = conn.execute(
                        "SELECT * FROM dsoc_threats WHERE is_active=%s ORDER BY last_seen DESC",
                        (1,)
                    ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM dsoc_threats ORDER BY last_seen DESC, confidence_pct DESC"
                ).fetchall()
            return jsonify([dict(r) for r in rows])
        finally:
            conn.close()

    @bp.route("/api/dsoc/threats", methods=["POST"])
    def api_dsoc_threats_create():
        from tools.dsoc_canvas.db.init_db import get_connection
        data = request.get_json(force=True) or {}
        required = ("source_prefix", "threat_type")
        missing = [f for f in required if not data.get(f)]
        if missing:
            return jsonify({"error": f"Missing: {', '.join(missing)}"}), 400
        conn = get_connection()
        try:
            now = _now()
            fields = {
                "source_prefix":   data["source_prefix"],
                "threat_type":     data["threat_type"],
                "confidence_pct":  float(data.get("confidence_pct", 0)),
                "attack_vector":   data.get("attack_vector", ""),
                "feed_source":     data.get("feed_source", ""),
                "first_seen":      data.get("first_seen", now),
                "last_seen":       data.get("last_seen", now),
                "packets_per_sec": int(data.get("packets_per_sec", 0)),
                "bits_per_sec":    int(data.get("bits_per_sec", 0)),
                "is_active":       int(data.get("is_active", 1)),
            }
            cols = ", ".join(fields.keys())
            try:
                placeholders = ", ".join("?" * len(fields))
                conn.execute(
                    f"INSERT INTO dsoc_threats ({cols}) VALUES ({placeholders})",
                    tuple(fields.values())
                )
            except Exception:
                placeholders = ", ".join("%s" * len(fields))
                conn.execute(
                    f"INSERT INTO dsoc_threats ({cols}) VALUES ({placeholders})",
                    tuple(fields.values())
                )
            conn.commit()
            return jsonify({"status": "created", "source_prefix": data["source_prefix"]}), 201
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500
        finally:
            conn.close()

    @bp.route("/api/dsoc/mitigations", methods=["GET"])
    def api_dsoc_mitigations_list():
        from tools.dsoc_canvas.db.init_db import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT *, "
                "(SELECT name FROM dsoc_scrubbing_centers WHERE id=dsoc_mitigations.scrubbing_center_id) AS scrubbing_center_name, "
                "(SELECT rule_name FROM dsoc_flowspec_rules WHERE id=dsoc_mitigations.flowspec_rule_id) AS flowspec_rule_name "
                "FROM dsoc_mitigations ORDER BY started_at DESC"
            ).fetchall()
            return jsonify([dict(r) for r in rows])
        finally:
            conn.close()

    @bp.route("/api/dsoc/mitigations", methods=["POST"])
    def api_dsoc_mitigations_create():
        from tools.dsoc_canvas.db.init_db import get_connection
        data = request.get_json(force=True) or {}
        required = ("target_prefix", "mitigation_type")
        missing = [f for f in required if not data.get(f)]
        if missing:
            return jsonify({"error": f"Missing: {', '.join(missing)}"}), 400
        conn = get_connection()
        try:
            ts = datetime.now(timezone.utc).strftime("%Y%m")
            try:
                row = conn.execute(
                    "SELECT COUNT(*) FROM dsoc_mitigations WHERE mitigation_number LIKE %s",
                    (f"MIT-{ts}-%",)
                ).fetchone()
            except Exception:
                row = conn.execute(
                    "SELECT COUNT(*) FROM dsoc_mitigations WHERE mitigation_number LIKE %s",
                    (f"MIT-{ts}-%",)
                ).fetchone()
            n = (row[0] if row else 0) + 1
            mit_num = f"MIT-{ts}-{n:04d}"

            fields = {
                "mitigation_number":  mit_num,
                "target_prefix":      data["target_prefix"],
                "mitigation_type":    data["mitigation_type"],
                "status":             data.get("status", "active"),
                "attack_type":        data.get("attack_type", ""),
                "peak_traffic_gbps":  float(data.get("peak_traffic_gbps", 0)),
                "scrubbing_center_id": int(data["scrubbing_center_id"]) if data.get("scrubbing_center_id") is not None else None,
                "flowspec_rule_id":   int(data["flowspec_rule_id"]) if data.get("flowspec_rule_id") is not None else None,
                "rtbh_id":            int(data["rtbh_id"]) if data.get("rtbh_id") is not None else None,
                "started_by":         data.get("started_by", "system"),
                "started_at":         data.get("started_at", _now()),
                "notes":              data.get("notes", ""),
            }
            cols = ", ".join(fields.keys())
            try:
                placeholders = ", ".join("?" * len(fields))
                conn.execute(
                    f"INSERT INTO dsoc_mitigations ({cols}) VALUES ({placeholders})",
                    tuple(fields.values())
                )
            except Exception:
                placeholders = ", ".join("%s" * len(fields))
                conn.execute(
                    f"INSERT INTO dsoc_mitigations ({cols}) VALUES ({placeholders})",
                    tuple(fields.values())
                )
            conn.commit()
            return jsonify({"status": "created", "mitigation_number": mit_num}), 201
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500
        finally:
            conn.close()

    @bp.route("/api/dsoc/mitigations/<int:mitigation_id>/complete", methods=["POST"])
    def api_dsoc_mitigations_complete(mitigation_id):
        from tools.dsoc_canvas.db.init_db import get_connection
        conn = get_connection()
        try:
            ended = _now()
            try:
                conn.execute(
                    "UPDATE dsoc_mitigations SET status=%s, ended_at=%s WHERE id=%s",
                    ("completed", ended, mitigation_id)
                )
            except Exception:
                conn.execute(
                    "UPDATE dsoc_mitigations SET status=%s, ended_at=%s WHERE id=%s",
                    ("completed", ended, mitigation_id)
                )
            conn.commit()
            return jsonify({"status": "completed", "id": mitigation_id, "ended_at": ended})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500
        finally:
            conn.close()

    @bp.route("/dsoc/bgp-security")
    def dsoc_bgp_security():
        from tools.dsoc_canvas.db.init_db import get_connection
        from tools.dsoc_canvas.bgp_hijack_detector import get_active_hijacks
        conn = get_connection()
        try:
            hijacks = get_active_hijacks(conn)
        except Exception:
            hijacks = []
        finally:
            conn.close()
        return render_template("dsoc_canvas/bgp_security.html", hijacks=hijacks)

    @bp.route("/api/dsoc/bgp-hijacks", methods=["GET"])
    def api_dsoc_bgp_hijacks_list():
        from tools.dsoc_canvas.db.init_db import get_connection
        from tools.dsoc_canvas.bgp_hijack_detector import get_active_hijacks
        conn = get_connection()
        try:
            hijacks = get_active_hijacks(conn)
            return jsonify(hijacks)
        finally:
            conn.close()

    @bp.route("/api/dsoc/bgp-hijacks", methods=["POST"])
    def api_dsoc_bgp_hijacks_create():
        from tools.dsoc_canvas.db.init_db import get_connection
        from tools.dsoc_canvas.bgp_hijack_detector import record_hijack_event
        data = request.get_json(force=True) or {}
        required = ("hijack_type", "detected_prefix", "expected_prefix")
        missing = [f for f in required if not data.get(f)]
        if missing:
            return jsonify({"error": f"Missing: {', '.join(missing)}"}), 400
        conn = get_connection()
        try:
            record = record_hijack_event(
                conn,
                hijack_type=data["hijack_type"],
                detected_prefix=data["detected_prefix"],
                expected_prefix=data["expected_prefix"],
                expected_origin_asn=data.get("expected_origin_asn"),
                observed_origin_asn=data.get("observed_origin_asn"),
                peer_asn=data.get("peer_asn"),
                route_leak_type=data.get("route_leak_type"),
                confidence_pct=float(data.get("confidence_pct", 75.0)),
                detection_source=data.get("detection_source", "manual"),
                notes=data.get("notes", ""),
            )
            return jsonify(record), 201
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500
        finally:
            conn.close()

    @bp.route("/api/dsoc/bgp-hijacks/<int:hijack_id>/resolve", methods=["POST"])
    def api_dsoc_bgp_hijacks_resolve(hijack_id):
        from tools.dsoc_canvas.db.init_db import get_connection
        from tools.dsoc_canvas.bgp_hijack_detector import resolve_hijack
        data = request.get_json(force=True) or {}
        resolution = data.get("resolution", "resolved")
        conn = get_connection()
        try:
            result = resolve_hijack(conn, hijack_id, resolution)
            return jsonify(result)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500
        finally:
            conn.close()

    @bp.route("/api/dsoc/iqe-query", methods=["POST"])
    def api_dsoc_iqe_query():
        from tools.dsoc_canvas.db.init_db import get_connection
        from tools.iqe.adapters.dsoc import handle_query
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
