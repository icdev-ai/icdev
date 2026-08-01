# CUI // SP-CTI
"""Peering Management Canvas (PMC) Flask blueprint.

Feature gate: ICDEV_PMC_ENABLED
Routes: /pmc/*, /api/pmc/*
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from flask import Blueprint, jsonify, render_template, request
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.pmc_canvas.blueprint")


def create_pmc_blueprint() -> Blueprint:
    """Factory — returns the PMC blueprint. Called by app.py canvas loader."""
    bp = Blueprint(
        "pmc_canvas",
        __name__,
        template_folder="../../tools/dashboard/templates",
        url_prefix="",
    )

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _conn():
        from tools.pmc_canvas.db.init_db import get_connection, init_db
        c = get_connection()
        try:
            init_db(c)
        except Exception:
            pass
        return c

    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _try_exec(conn, sql_pg: str, sql_sq: str, params):
        """Execute SQL trying PG params first, then SQLite params."""
        try:
            cur = conn.cursor()
            cur.execute(sql_pg, params)
            try:
                conn.commit()
            except Exception:
                pass
            return cur
        except Exception:
            cur = conn.cursor()
            cur.execute(sql_sq, params)
            try:
                conn.commit()
            except Exception:
                pass
            return cur

    def _fetchall(conn, sql: str, params=()) -> list[dict]:
        try:
            cur = conn.cursor()
            try:
                cur.execute(sql.replace("?", "%s"), params)
            except Exception:
                cur.execute(sql.replace("%s", "?"), params)
            rows = cur.fetchall()
            if not rows:
                return []
            if hasattr(rows[0], "keys"):
                return [dict(r) for r in rows]
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in rows]
        except Exception:
            return []

    def _audit(conn, action: str, entity_type: str, entity_id: str, user_id: str = "system"):
        try:
            _try_exec(
                conn,
                "INSERT INTO pmc_audit(action,entity_type,entity_id,user_id,ts) VALUES(%s,%s,%s,%s,%s)",
                "INSERT INTO pmc_audit(action,entity_type,entity_id,user_id,ts) VALUES(?,?,?,?,?)",
                (action, entity_type, entity_id, user_id, _now()),
            )
        except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
            logger.warning("_audit: best-effort INSERT into pmc_audit failed (non-blocking): %s", exc)

    # ── Page routes ────────────────────────────────────────────────────────────

    @bp.route("/pmc")
    def pmc_index():
        return render_template("pmc_canvas/index.html")

    @bp.route("/pmc/peers")
    def pmc_peers():
        return render_template("pmc_canvas/peers.html")

    @bp.route("/pmc/peers/<peer_id>")
    def pmc_peer_detail(peer_id):
        return render_template("pmc_canvas/peer_detail.html", peer_id=peer_id)

    @bp.route("/pmc/ix")
    def pmc_ix():
        return render_template("pmc_canvas/ix.html")

    @bp.route("/pmc/rpki")
    def pmc_rpki():
        return render_template("pmc_canvas/rpki.html")

    @bp.route("/pmc/policies")
    def pmc_policies():
        return render_template("pmc_canvas/policies.html")

    @bp.route("/pmc/requests")
    def pmc_requests():
        return render_template("pmc_canvas/requests.html")

    # ── API: Overview ──────────────────────────────────────────────────────────

    @bp.route("/api/pmc/overview")
    def api_pmc_overview():
        conn = _conn()
        try:
            from tools.pmc_canvas.pmc_aggregator import get_pmc_overview
            return jsonify(get_pmc_overview(conn))
        finally:
            conn.close()

    # ── API: Peers ─────────────────────────────────────────────────────────────

    @bp.route("/api/pmc/peers", methods=["GET"])
    def api_pmc_peers_list():
        conn = _conn()
        try:
            rows = _fetchall(
                conn,
                "SELECT id,asn,org_name,peer_type,policy,status,traffic_ratio,"
                "ipv4_prefix_count,ipv6_prefix_count,rir,noc_email,irr_as_set,"
                "peeringdb_synced_at,created_at FROM peering_peers ORDER BY org_name",
            )
            return jsonify(rows)
        finally:
            conn.close()

    @bp.route("/api/pmc/peers", methods=["POST"])
    def api_pmc_peers_create():
        data = request.get_json() or {}
        asn = data.get("asn")
        if not asn:
            return jsonify({"error": "asn required"}), 400

        peer_id = str(uuid.uuid4())
        now = _now()
        conn = _conn()
        try:
            _try_exec(
                conn,
                """INSERT INTO peering_peers(id,asn,org_name,peer_type,policy,status,
                   noc_email,irr_as_set,rir,ipv4_prefix_count,ipv6_prefix_count,
                   max_prefixes_v4,max_prefixes_v6,notes,created_at,updated_at)
                   VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                """INSERT INTO peering_peers(id,asn,org_name,peer_type,policy,status,
                   noc_email,irr_as_set,rir,ipv4_prefix_count,ipv6_prefix_count,
                   max_prefixes_v4,max_prefixes_v6,notes,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (peer_id, asn, data.get("org_name", ""), data.get("peer_type", "public"),
                 data.get("policy", "selective"), data.get("status", "evaluation"),
                 data.get("noc_email", ""), data.get("irr_as_set", ""),
                 data.get("rir", "UNKNOWN"),
                 data.get("ipv4_prefix_count", 0), data.get("ipv6_prefix_count", 0),
                 data.get("max_prefixes_v4", 1000), data.get("max_prefixes_v6", 1000),
                 data.get("notes", ""), now, now),
            )
            _audit(conn, "peer_created", "peering_peers", peer_id)
            return jsonify({"id": peer_id, "asn": asn}), 201
        finally:
            conn.close()

    @bp.route("/api/pmc/peers/lookup")
    def api_pmc_peers_lookup():
        asn = request.args.get("asn", type=int)
        if not asn:
            return jsonify({"error": "asn required"}), 400
        try:
            from tools.pmc_canvas.peeringdb_client import get_asn_info
            info = get_asn_info(asn)
            return jsonify(info)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 503

    @bp.route("/api/pmc/peers/sync-asn", methods=["POST"])
    def api_pmc_peers_sync_asn():
        data = request.get_json() or {}
        asn = data.get("asn")
        if not asn:
            return jsonify({"error": "asn required"}), 400
        conn = _conn()
        try:
            from tools.pmc_canvas.peeringdb_client import sync_peer_from_peeringdb
            result = sync_peer_from_peeringdb(asn, conn)
            _audit(conn, "peeringdb_sync", "peering_peers", str(asn))
            return jsonify(result)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 503
        finally:
            conn.close()

    @bp.route("/api/pmc/peers/<peer_id>", methods=["GET"])
    def api_pmc_peer_get(peer_id):
        conn = _conn()
        try:
            rows = _fetchall(conn, "SELECT * FROM peering_peers WHERE id=?", (peer_id,))
            if not rows:
                return jsonify({"error": "not found"}), 404
            return jsonify(rows[0])
        finally:
            conn.close()

    @bp.route("/api/pmc/peers/<peer_id>/sync", methods=["POST"])
    def api_pmc_peer_sync(peer_id):
        conn = _conn()
        try:
            rows = _fetchall(conn, "SELECT asn FROM peering_peers WHERE id=?", (peer_id,))
            if not rows:
                return jsonify({"error": "not found"}), 404
            asn = rows[0]["asn"]
            from tools.pmc_canvas.peeringdb_client import sync_peer_from_peeringdb
            result = sync_peer_from_peeringdb(asn, conn)
            _audit(conn, "peeringdb_sync", "peering_peers", peer_id)
            return jsonify(result)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 503
        finally:
            conn.close()

    @bp.route("/api/pmc/peers/<peer_id>/evaluate", methods=["POST"])
    def api_pmc_peer_evaluate(peer_id):
        conn = _conn()
        try:
            peers = _fetchall(conn, "SELECT * FROM peering_peers WHERE id=?", (peer_id,))
            if not peers:
                return jsonify({"error": "not found"}), 404
            peer = peers[0]
            prefixes = _fetchall(
                conn, "SELECT * FROM peering_prefixes WHERE peer_id=?", (peer_id,)
            )
            from tools.pmc_canvas.peering_decision_engine import evaluate_peer
            result = evaluate_peer(peer, our_asn=0, prefixes=prefixes)
            _audit(conn, "peer_evaluated", "peering_peers", peer_id)
            return jsonify(result)
        finally:
            conn.close()

    @bp.route("/api/pmc/peers/<peer_id>/validate-rpki", methods=["POST"])
    def api_pmc_peer_validate_rpki(peer_id):
        conn = _conn()
        try:
            prefixes = _fetchall(
                conn, "SELECT * FROM peering_prefixes WHERE peer_id=?", (peer_id,)
            )
            from tools.pmc_canvas.rpki_validator import validate_prefix
            updated = 0
            for p in prefixes:
                result = validate_prefix(p["prefix"], p.get("origin_asn") or 0)
                _try_exec(
                    conn,
                    "UPDATE peering_prefixes SET rpki_status=%s,roa_found=%s,last_validated=%s WHERE id=%s",
                    "UPDATE peering_prefixes SET rpki_status=?,roa_found=?,last_validated=? WHERE id=?",
                    (result["status"], result.get("roa_found", False), _now(), p["id"]),
                )
                updated += 1
            _audit(conn, "rpki_validated", "peering_prefixes", peer_id)
            return jsonify({"updated": updated})
        finally:
            conn.close()

    @bp.route("/api/pmc/peers/<peer_id>/generate-config", methods=["POST"])
    def api_pmc_peer_generate_config(peer_id):
        data = request.get_json() or {}
        conn = _conn()
        try:
            peers = _fetchall(conn, "SELECT * FROM peering_peers WHERE id=?", (peer_id,))
            if not peers:
                return jsonify({"error": "not found"}), 404
            from tools.pmc_canvas.bgp_config_generator import generate_peer_session
            cfg = generate_peer_session(
                peer=peers[0],
                our_asn=data.get("our_asn", 0),
                os_type=data.get("os_type", "ios_xr"),
                neighbor_ip=data.get("neighbor_ip", "0.0.0.0"),  # nosec B104 — BGP neighbor IP placeholder, not a bind address
                import_policy=data.get("import_policy", "IMPORT-PEER"),
                export_policy=data.get("export_policy", "EXPORT-PEER"),
                md5_password=data.get("md5_password", ""),
            )
            return jsonify({"config": cfg})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400
        finally:
            conn.close()

    @bp.route("/api/pmc/peers/<peer_id>/rpsl")
    def api_pmc_peer_rpsl(peer_id):
        conn = _conn()
        try:
            peers = _fetchall(conn, "SELECT * FROM peering_peers WHERE id=?", (peer_id,))
            if not peers:
                return jsonify({"error": "not found"}), 404
            peer = peers[0]
            from tools.pmc_canvas.rpsl_generator import generate_aut_num
            rpsl = generate_aut_num(
                our_asn=peer["asn"],
                import_peers=[],
                export_peers=[],
                org_name=peer.get("org_name", ""),
                as_set=peer.get("irr_as_set", ""),
            )
            return jsonify({"rpsl": rpsl})
        finally:
            conn.close()

    @bp.route("/api/pmc/peers/<peer_id>/prefixes")
    def api_pmc_peer_prefixes(peer_id):
        conn = _conn()
        try:
            rows = _fetchall(
                conn, "SELECT * FROM peering_prefixes WHERE peer_id=? ORDER BY prefix", (peer_id,)
            )
            return jsonify(rows)
        finally:
            conn.close()

    # ── API: IX ────────────────────────────────────────────────────────────────

    @bp.route("/api/pmc/ix", methods=["GET"])
    def api_pmc_ix_list():
        conn = _conn()
        try:
            rows = _fetchall(
                conn,
                "SELECT id,ix_name,city,country,our_ipv4,our_ipv6,our_speed_gbps,"
                "monthly_cost_usd,status,peeringdb_ix_id FROM peering_ix ORDER BY ix_name",
            )
            return jsonify(rows)
        finally:
            conn.close()

    @bp.route("/api/pmc/ix", methods=["POST"])
    def api_pmc_ix_create():
        data = request.get_json() or {}
        if not data.get("ix_name"):
            return jsonify({"error": "ix_name required"}), 400
        ix_id = str(uuid.uuid4())
        now = _now()
        conn = _conn()
        try:
            _try_exec(
                conn,
                """INSERT INTO peering_ix(id,ix_name,city,country,our_ipv4,our_ipv6,
                   our_speed_gbps,monthly_cost_usd,status,peeringdb_ix_id,created_at,updated_at)
                   VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                """INSERT INTO peering_ix(id,ix_name,city,country,our_ipv4,our_ipv6,
                   our_speed_gbps,monthly_cost_usd,status,peeringdb_ix_id,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (ix_id, data.get("ix_name"), data.get("city", ""), data.get("country", ""),
                 data.get("our_ipv4", ""), data.get("our_ipv6", ""),
                 data.get("our_speed_gbps", 0.0), data.get("monthly_cost_usd", 0.0),
                 data.get("status", "active"), data.get("peeringdb_ix_id"),
                 now, now),
            )
            _audit(conn, "ix_created", "peering_ix", ix_id)
            return jsonify({"id": ix_id}), 201
        finally:
            conn.close()

    # ── API: RPKI ──────────────────────────────────────────────────────────────

    @bp.route("/api/pmc/rpki/report")
    def api_pmc_rpki_report():
        conn = _conn()
        try:
            from tools.pmc_canvas.rpki_validator import get_rpki_summary
            summary = get_rpki_summary(conn)
            # Also fetch invalid + not-found row detail
            invalid_rows = _fetchall(
                conn,
                "SELECT pp.prefix, pp.origin_asn, pp.address_family, pr.asn, pr.org_name "
                "FROM peering_prefixes pp LEFT JOIN peering_peers pr ON pp.peer_id=pr.id "
                "WHERE pp.rpki_status='invalid'",
            )
            notfound_rows = _fetchall(
                conn,
                "SELECT pp.prefix, pp.origin_asn, pp.address_family, pr.asn, pr.org_name "
                "FROM peering_prefixes pp LEFT JOIN peering_peers pr ON pp.peer_id=pr.id "
                "WHERE pp.rpki_status='not-found' LIMIT 100",
            )
            summary["invalid_rows"] = invalid_rows
            summary["notfound_rows"] = notfound_rows
            return jsonify(summary)
        finally:
            conn.close()

    @bp.route("/api/pmc/rpki/validate")
    def api_pmc_rpki_validate_single():
        prefix = request.args.get("prefix", "")
        asn = request.args.get("asn", 0, type=int)
        if not prefix:
            return jsonify({"error": "prefix required"}), 400
        try:
            from tools.pmc_canvas.rpki_validator import validate_prefix
            return jsonify(validate_prefix(prefix, asn))
        except Exception as exc:
            return jsonify({"error": str(exc)}), 503

    @bp.route("/api/pmc/rpki/validate-all", methods=["POST"])
    def api_pmc_rpki_validate_all():
        conn = _conn()
        try:
            prefixes = _fetchall(conn, "SELECT id,prefix,origin_asn FROM peering_prefixes")
            from tools.pmc_canvas.rpki_validator import validate_prefix
            updated = 0
            for p in prefixes:
                result = validate_prefix(p["prefix"], p.get("origin_asn") or 0)
                _try_exec(
                    conn,
                    "UPDATE peering_prefixes SET rpki_status=%s,roa_found=%s,last_validated=%s WHERE id=%s",
                    "UPDATE peering_prefixes SET rpki_status=?,roa_found=?,last_validated=? WHERE id=?",
                    (result["status"], result.get("roa_found", False), _now(), p["id"]),
                )
                updated += 1
            _audit(conn, "rpki_validate_all", "peering_prefixes", "all")
            return jsonify({"updated": updated})
        finally:
            conn.close()

    # ── API: Policies ──────────────────────────────────────────────────────────

    @bp.route("/api/pmc/policies", methods=["GET"])
    def api_pmc_policies_list():
        conn = _conn()
        try:
            rows = _fetchall(
                conn,
                "SELECT id,policy_name,our_asn,policy_type,action,priority,active "
                "FROM peering_policies ORDER BY policy_type,priority",
            )
            return jsonify(rows)
        finally:
            conn.close()

    @bp.route("/api/pmc/policies", methods=["POST"])
    def api_pmc_policies_create():
        data = request.get_json() or {}
        if not data.get("policy_name"):
            return jsonify({"error": "policy_name required"}), 400
        pol_id = str(uuid.uuid4())
        now = _now()
        conn = _conn()
        try:
            _try_exec(
                conn,
                """INSERT INTO peering_policies(id,policy_name,our_asn,policy_type,action,
                   priority,active,created_at,updated_at)
                   VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                """INSERT INTO peering_policies(id,policy_name,our_asn,policy_type,action,
                   priority,active,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (pol_id, data["policy_name"], data.get("our_asn", 0),
                 data.get("policy_type", "import"), data.get("action", "accept"),
                 data.get("priority", 100), True, now, now),
            )
            _audit(conn, "policy_created", "peering_policies", pol_id)
            return jsonify({"id": pol_id}), 201
        finally:
            conn.close()

    @bp.route("/api/pmc/export/aut-num", methods=["POST"])
    def api_pmc_export_aut_num():
        data = request.get_json() or {}
        try:
            from tools.pmc_canvas.rpsl_generator import generate_aut_num
            rpsl = generate_aut_num(
                our_asn=data.get("our_asn", 0),
                import_peers=data.get("import_peers", []),
                export_peers=data.get("export_peers", []),
                org_name=data.get("org_name", ""),
                as_set=data.get("as_set", ""),
                description=data.get("description", ""),
            )
            return jsonify({"rpsl": rpsl})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400

    # ── API: Requests ──────────────────────────────────────────────────────────

    @bp.route("/api/pmc/requests", methods=["GET"])
    def api_pmc_requests_list():
        conn = _conn()
        try:
            rows = _fetchall(
                conn,
                "SELECT pr.id,pr.request_type,pr.status,pr.contact_method,pr.contact_address,"
                "pr.notes,pr.sent_at,pr.responded_at,pr.created_at,"
                "pp.asn,pp.org_name FROM peering_requests pr "
                "LEFT JOIN peering_peers pp ON pr.peer_id=pp.id ORDER BY pr.created_at DESC",
            )
            return jsonify(rows)
        finally:
            conn.close()

    @bp.route("/api/pmc/requests", methods=["POST"])
    def api_pmc_requests_create():
        data = request.get_json() or {}
        req_id = str(uuid.uuid4())
        now = _now()
        conn = _conn()
        try:
            # Resolve peer_id from asn if provided
            peer_id = data.get("peer_id")
            if not peer_id and data.get("asn"):
                peers = _fetchall(conn, "SELECT id FROM peering_peers WHERE asn=?", (data["asn"],))
                peer_id = peers[0]["id"] if peers else None

            _try_exec(
                conn,
                """INSERT INTO peering_requests(id,peer_id,request_type,status,contact_method,
                   contact_address,notes,created_at,updated_at)
                   VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                """INSERT INTO peering_requests(id,peer_id,request_type,status,contact_method,
                   contact_address,notes,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (req_id, peer_id, data.get("request_type", "new"),
                 data.get("status", "pending"), data.get("contact_method", "email"),
                 data.get("contact_address", ""), data.get("notes", ""), now, now),
            )
            _audit(conn, "request_created", "peering_requests", req_id)
            return jsonify({"id": req_id}), 201
        finally:
            conn.close()

    @bp.route("/api/pmc/requests/<req_id>", methods=["PUT"])
    def api_pmc_requests_update(req_id):
        data = request.get_json() or {}
        status = data.get("status")
        if not status:
            return jsonify({"error": "status required"}), 400
        conn = _conn()
        try:
            _try_exec(
                conn,
                "UPDATE peering_requests SET status=%s,updated_at=%s WHERE id=%s",
                "UPDATE peering_requests SET status=?,updated_at=? WHERE id=?",
                (status, _now(), req_id),
            )
            _audit(conn, f"request_{status}", "peering_requests", req_id)
            return jsonify({"id": req_id, "status": status})
        finally:
            conn.close()

    # ── API: IQE query ─────────────────────────────────────────────────────────

    @bp.route("/api/pmc/iqe-query", methods=["POST"])
    def api_pmc_iqe_query():
        data = request.get_json() or {}
        query = data.get("q", "")
        collection = data.get("collection", "pmc.peers")
        conn = _conn()
        try:
            from tools.iqe.adapters.pmc import (
                peers_adapter, ix_adapter, prefixes_adapter,
                requests_adapter, policies_adapter,
            )
            adapters = {
                "pmc.peers": peers_adapter,
                "pmc.ix_memberships": ix_adapter,
                "pmc.prefixes": prefixes_adapter,
                "pmc.peering_requests": requests_adapter,
                "pmc.route_policies": policies_adapter,
            }
            fn = adapters.get(collection, peers_adapter)
            rows = fn(query, conn=conn)
            return jsonify({"rows": rows, "count": len(rows), "collection": collection})
        except Exception as exc:
            return jsonify({"error": str(exc), "rows": []}), 500
        finally:
            conn.close()

    @bp.route("/pmc/transit")
    def pmc_transit():
        from tools.pmc_canvas.transit_pricing_benchmark import benchmark_transit_cost
        benchmarks = {}
        for region in ["na", "eu", "apac", "latam", "mea"]:
            try:
                benchmarks[region] = benchmark_transit_cost(region, speed_gbps=10.0)
            except Exception:
                benchmarks[region] = {}
        return render_template("pmc_canvas/transit.html", benchmarks=benchmarks)

    @bp.route("/api/pmc/transit/benchmark")
    def api_pmc_transit_benchmark():
        from tools.pmc_canvas.transit_pricing_benchmark import benchmark_transit_cost
        region = request.args.get("region", "na")
        try:
            speed = float(request.args.get("speed_gbps", 10.0))
        except ValueError:
            speed = 10.0
        try:
            return jsonify(benchmark_transit_cost(region, speed_gbps=speed))
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/pmc/transit/roi", methods=["POST"])
    def api_pmc_transit_roi():
        from tools.pmc_canvas.transit_pricing_benchmark import peering_vs_transit_roi
        data = request.get_json(force=True) or {}
        try:
            result = peering_vs_transit_roi(
                peers=data.get("peers", []),
                transit_costs=data.get("transit_costs", {}),
                capex=float(data.get("capex", 0)),
            )
            return jsonify(result)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    return bp
