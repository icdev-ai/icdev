# CUI // SP-CTI
"""ICDEV Network Design Canvas -- twin_migration route group.

Extracted verbatim from tools/network/blueprint.py (cvx-net-01 monolith split).
Registered on the shared NDC blueprint via register_twin_migration_routes(bp).
"""
from __future__ import annotations

import json
import uuid as _uuid
from flask import current_app, jsonify, render_template, request
from tools.network.routes._common import _AI_MIGRATION_PLAN_PROMPT, logger
from tools.db.storage import sql_placeholder
from tools.network.blueprint_helpers import (
    _audit,
    _now,
    _row_to_dict,
    get_parsed_graph,
    nc_login_required,
)
from tools.network.db.init_db import get_connection


def _route_llm(function, system_prompt, messages, max_tokens, temperature=None):
    """Invoke the configured LLM through LLMRouter (lpx-router-02).

    Replaces the previous direct provider POST so provider selection, an optional
    proxy ``base_url``, budgets and audit all flow through the router instead of
    reading a provider API key from the environment and hardcoding a Claude
    model. Returns ``(content, error)``.
    """
    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest
    except Exception as exc:  # pragma: no cover - import guard
        return None, "LLM router unavailable: {}".format(exc)
    kwargs = {
        "messages": messages,
        "system_prompt": system_prompt,
        "max_tokens": max_tokens,
    }
    if temperature is not None:
        kwargs["temperature"] = temperature
    try:
        resp = LLMRouter().invoke(function, LLMRequest(**kwargs))
    except Exception as exc:
        return None, str(exc)
    return (resp.content or ""), None


def register_twin_migration_routes(bp, nc_config=None):
    """Register twin_migration routes on the NDC blueprint."""
    NC_CONFIG = nc_config or {}

    @bp.route("/ingestion")
    @nc_login_required
    def nc_ingestion_page():
        """Network data ingestion dashboard."""
        return render_template("network/ingestion.html",
                               classification_banner=NC_CONFIG.get("app", {}).get("classification", ""))

    # ── GraphRAG /ask endpoint (per-canvas Q&A pattern, DT adaptation #1) ──
    _NDC_KG_PROJECT_ID = "ndc-network-intelligence"
    _NDC_KG_PROFILE = "network_infrastructure"

    @bp.route("/ask")
    @nc_login_required
    def nc_ask_page():
        return render_template(
            "network/ask.html",
            classification_banner=NC_CONFIG.get("app", {}).get("classification", ""),
        )

    @bp.route("/api/ask", methods=["POST"])
    @nc_login_required
    def nc_api_ask():
        from tools.knowledge_graph.canvas_ask import handle_ask_request
        data = request.get_json(silent=True) or {}
        payload = handle_ask_request(
            query=data.get("query", ""),
            graph_id=_NDC_KG_PROJECT_ID,
            profile=_NDC_KG_PROFILE,
            top_k=int(data.get("top_k", 10)),
            narrate=bool(data.get("narrate", False)),
            canvas_label="network topology",
        )
        status = payload.pop("_status", 200)
        return jsonify(payload), status

    # ── Digital Twin ───────────────────────────────────────────────────────
    @bp.route("/twin/<topo_id>")
    @nc_login_required
    def nc_twin_page(topo_id):
        conn = get_connection()
        _ph = sql_placeholder(conn)
        topo = conn.execute(
            f"SELECT * FROM topologies WHERE id = {_ph}", (topo_id,)
        ).fetchone()
        if not topo:
            return render_template("404.html"), 404
        topo = _row_to_dict(topo)

        try:
            snaps = conn.execute(
                f"SELECT * FROM network_twin_snapshots WHERE project_id = {_ph} ORDER BY created_at DESC LIMIT 20",
                (topo_id,),
            ).fetchall()
        except Exception:
            snaps = []

        from tools.network.constants import INTENT_RULES
        from tools.network.narrative_generator import load_personas as _ng_personas
        try:
            _tfw_personas = _ng_personas()
        except Exception:
            _tfw_personas = []
        return render_template(
            "network/twin.html",
            project=topo,
            snapshots=[_row_to_dict(s) for s in snaps],
            intent_rules=INTENT_RULES,
            classification_banner=NC_CONFIG.get("app", {}).get("classification", ""),
            personas=_tfw_personas,
        )

    @bp.route("/api/twin/<topo_id>/snapshot", methods=["POST"])
    @nc_login_required
    def nc_api_twin_snapshot(topo_id):
        from tools.network.twin import take_snapshot
        data = request.get_json(silent=True) or {}
        snap = take_snapshot(topo_id, label=data.get("label"))
        return jsonify(snap), 201

    @bp.route("/api/twin/<topo_id>/simulate", methods=["POST"])
    @nc_login_required
    def nc_api_twin_simulate(topo_id):
        from tools.network.twin import simulate_delta
        data = request.get_json(silent=True) or {}
        result = simulate_delta(
            topo_id,
            topology_delta=data.get("topology_delta", {}),
            intent_rules=data.get("intent_rules", []),
            baseline_snap_id=data.get("baseline_snap_id"),
        )
        return jsonify(result), 200

    @bp.route("/api/twin/<topo_id>/blast-radius", methods=["POST"])
    @nc_login_required
    def nc_api_twin_blast_radius(topo_id):
        from tools.network.twin import blast_radius
        data = request.get_json(silent=True) or {}
        result = blast_radius(
            topo_id,
            node_id=data.get("node_id", ""),
            topology_delta=data.get("topology_delta"),
            baseline_snap_id=data.get("baseline_snap_id"),
        )
        return jsonify(result), 200

    @bp.route("/api/twin/<topo_id>/analyze-path", methods=["POST"])
    @nc_login_required
    def nc_api_twin_analyze_path(topo_id):
        from tools.network.path_analyzer import find_paths
        import json as _json
        data = request.get_json(silent=True) or {}
        src_query = (data.get("src") or data.get("src_id") or "").strip()
        dst_query = (data.get("dst") or data.get("dst_id") or "").strip()
        if not src_query or not dst_query:
            return jsonify({"error": "src and dst are required"}), 400
        conn = get_connection()
        _ph = sql_placeholder(conn)
        try:
            row = conn.execute(f"SELECT graph_json FROM topologies WHERE id={_ph}", (topo_id,)).fetchone()
        finally:
            conn.close()
        if not row or not row["graph_json"]:
            return jsonify({"error": "Topology not found"}), 404
        try:
            raw_graph = _json.loads(row["graph_json"])
        except Exception:
            return jsonify({"error": "Invalid topology JSON"}), 500
        nodes_list = raw_graph.get("nodes", [])
        nodes_dict = {n["id"]: n for n in nodes_list if isinstance(n, dict) and "id" in n}
        graph = {"nodes": nodes_dict, "edges": raw_graph.get("edges", [])}
        result = find_paths(src_query, dst_query, graph)
        resolve_parts = []
        if result["src"] != src_query:
            src_label = nodes_dict.get(result["src"], {}).get("label") or result["src"]
            resolve_parts.append(f'"{src_query}" → {src_label}')
        if result["dst"] != dst_query:
            dst_label = nodes_dict.get(result["dst"], {}).get("label") or result["dst"]
            resolve_parts.append(f'"{dst_query}" → {dst_label}')
        result["resolve_note"] = "Matched: " + ", ".join(resolve_parts) if resolve_parts else None
        if result["reachable"]:
            result["verdict"] = "green"
        elif result["blocked_by_acl"]:
            result["verdict"] = "amber"
        else:
            result["verdict"] = "red"
        return jsonify(result), 200

    @bp.route("/api/twin/<topo_id>/current-topology", methods=["GET"])
    @nc_login_required
    def nc_api_twin_current_topology(topo_id):
        conn = get_connection()
        _ph = sql_placeholder(conn)
        row = conn.execute(f"SELECT graph_json FROM topologies WHERE id={_ph}", (topo_id,)).fetchone()
        if not row:
            return jsonify({"error": "Topology not found"}), 404
        try:
            graph = json.loads(row["graph_json"] or "{}")
        except Exception:
            graph = {}
        return jsonify({"graph_json": graph}), 200

    @bp.route("/api/twin/<topo_id>/chat-delta", methods=["POST"])
    @nc_login_required
    def nc_api_twin_chat_delta(topo_id):
        from tools.twin_chat import network_chat_to_delta
        data = request.get_json(silent=True) or {}
        message = (data.get("message") or "").strip()
        if not message:
            return jsonify({"error": "message is required"}), 400
        result = network_chat_to_delta(message, data.get("graph_json"))
        return jsonify(result), (500 if "error" in result else 200)

    @bp.route("/api/twin/<topo_id>/nl-query", methods=["POST"])
    @nc_login_required
    def nc_api_twin_nl_query(topo_id):
        """IQE AI Assist — answer a natural language question about a topology."""
        from tools.network.nl_query import answer_query
        data = request.get_json(silent=True) or {}
        question = (data.get("question") or "").strip()
        if not question:
            return jsonify({"error": "question is required"}), 400
        conn = get_connection()
        try:
            result = answer_query(topo_id, question, conn)
        finally:
            conn.close()
        return jsonify(result), 200

    @bp.route("/api/twin/<topo_id>/iqe-query", methods=["POST"])
    @nc_login_required
    def nc_api_twin_iqe_query(topo_id):
        """IQE structured query — translate NL to IQE and optionally execute against topology."""
        from tools.iqe.nl_to_iqe import nl_to_iqe
        from tools.iqe.parser import IQESyntaxError, parse
        from tools.iqe.executor import Executor

        data = request.get_json(silent=True) or {}
        question = (data.get("question") or "").strip()
        if not question:
            return jsonify({"error": "question is required"}), 400
        execute = data.get("execute", True)

        # Derive available collections from topology node/edge tables
        collections = ["nodes", "edges"]

        translation = nl_to_iqe(question, collections)
        iqe_str = translation.get("iqe", "")
        explanation = translation.get("explanation", "")

        if not execute:
            return jsonify({"iqe": iqe_str, "explanation": explanation}), 200

        conn = get_connection()
        try:
            ast = parse(iqe_str)

            # Build adapters for topology node/edge data
            def _nodes_adapter(c):
                # Read-only — take the shared cached parse (ndc-perf-02); the
                # sibling _edges_adapter reuses it as a hit within this query.
                graph = get_parsed_graph(c, topo_id)
                if graph is None:
                    return []
                cells = graph.get("cells") or []
                result = []
                for cell in cells:
                    kind = (cell.get("type") or "").lower()
                    if "link" in kind or "edge" in kind:
                        continue
                    attrs = cell.get("attrs") or {}
                    label = ""
                    for key in ("label", "text", "body"):
                        val = attrs.get(key)
                        if isinstance(val, dict):
                            label = val.get("text") or val.get("textWrap", {}).get("text", "") or ""
                        if label:
                            break
                    if not label:
                        label = cell.get("label") or cell.get("name") or cell.get("id", "")
                    result.append({
                        "id": cell.get("id", ""),
                        "label": str(label).strip(),
                        "type": cell.get("deviceType") or attrs.get("deviceType", "") or cell.get("type", ""),
                        "position": cell.get("position", {}),
                    })
                return result

            def _edges_adapter(c):
                # Read-only — shared cached parse (ndc-perf-02); typically a hit
                # after _nodes_adapter primed the cache for this topology.
                graph = get_parsed_graph(c, topo_id)
                if graph is None:
                    return []
                cells = graph.get("cells") or []
                result = []
                for cell in cells:
                    kind = (cell.get("type") or "").lower()
                    if "link" not in kind and "edge" not in kind:
                        continue
                    src = (cell.get("source") or {})
                    tgt = (cell.get("target") or {})
                    result.append({
                        "id": cell.get("id", ""),
                        "source": src.get("id", "") if isinstance(src, dict) else str(src),
                        "target": tgt.get("id", "") if isinstance(tgt, dict) else str(tgt),
                        "protocol": (cell.get("attrs") or {}).get("line", {}).get("strokeDasharray", "") or cell.get("protocol", ""),
                        "bandwidth": cell.get("bandwidth", ""),
                    })
                return result

            executor = Executor()
            executor.register_collection("nodes", _nodes_adapter)
            executor.register_collection("edges", _edges_adapter)

            rows = executor.run(ast, conn)
            return jsonify({
                "iqe": iqe_str,
                "explanation": explanation,
                "results": rows,
                "row_count": len(rows),
            }), 200
        except IQESyntaxError as exc:
            return jsonify({"error": f"IQE syntax error: {exc}", "iqe": iqe_str}), 400
        except Exception as exc:
            logger.warning("IQE query execution error: %s", exc)
            return jsonify({"error": str(exc), "iqe": iqe_str}), 500
        finally:
            conn.close()

    @bp.route("/api/twin/<topo_id>/nodes/<node_id>/domain-policy", methods=["GET", "POST"])
    @nc_login_required
    def nc_api_twin_node_domain_policy(topo_id, node_id):
        """GET: load domain policy for a node. POST: save domain policy for a node."""
        import json as _json
        import uuid
        from datetime import datetime, timezone

        conn = get_connection()
        _ph = sql_placeholder(conn)
        try:
            if request.method == "GET":
                row = conn.execute(
                    "SELECT domain_type, domain_label, security_policy, routing_policy, vpn_policy"
                    f" FROM nc_security_domain_policies WHERE topology_id={_ph} AND node_id={_ph}",
                    (topo_id, node_id),
                ).fetchone()
                if not row:
                    return jsonify({"domain_type": "on_prem", "security_policy": {}, "routing_policy": {}, "vpn_policy": {}}), 200
                return jsonify({
                    "domain_type": row["domain_type"],
                    "domain_label": row["domain_label"] or "",
                    "security_policy": _json.loads(row["security_policy"] or "{}"),
                    "routing_policy": _json.loads(row["routing_policy"] or "{}"),
                    "vpn_policy": _json.loads(row["vpn_policy"] or "{}"),
                }), 200
            else:
                data = request.get_json(silent=True) or {}
                domain_type = (data.get("domain_type") or "on_prem").strip()
                domain_label = (data.get("domain_label") or "").strip()
                sec = _json.dumps(data.get("security_policy") or {})
                route = _json.dumps(data.get("routing_policy") or {})
                vpn = _json.dumps(data.get("vpn_policy") or {})
                now = datetime.now(timezone.utc).isoformat()
                existing = conn.execute(
                    f"SELECT id FROM nc_security_domain_policies WHERE topology_id={_ph} AND node_id={_ph}",
                    (topo_id, node_id),
                ).fetchone()
                if existing:
                    conn.execute(
                        "UPDATE nc_security_domain_policies"
                        f" SET domain_type={_ph}, domain_label={_ph}, security_policy={_ph}, routing_policy={_ph}, vpn_policy={_ph}, updated_at={_ph}"
                        f" WHERE topology_id={_ph} AND node_id={_ph}",
                        (domain_type, domain_label, sec, route, vpn, now, topo_id, node_id),
                    )
                else:
                    conn.execute(
                        "INSERT INTO nc_security_domain_policies"
                        " (id, topology_id, node_id, domain_type, domain_label, security_policy, routing_policy, vpn_policy, created_at, updated_at)"
                        f" VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph})",
                        (str(uuid.uuid4()), topo_id, node_id, domain_type, domain_label, sec, route, vpn, now, now),
                    )
                conn.commit()
                return jsonify({"ok": True}), 200
        except Exception as exc:
            logger.warning("domain-policy error: %s", exc)
            return jsonify({"error": str(exc)}), 500
        finally:
            conn.close()

    @bp.route("/api/twin/<topo_id>/domain-policy-templates", methods=["GET"])
    @nc_login_required
    def nc_api_twin_domain_policy_templates(topo_id):
        """Return pre-built JSON templates for each domain type."""
        templates = {
            "on_prem": {
                "security": {"allowed_ports": [22, 80, 443], "denied_ports": [], "inspection_type": "stateful", "encryption_required": False, "mfa_required": False, "pki_required": False, "cdm_sensor": False, "idps_enabled": False},
                "routing": {"bgp_as": None, "static_routes": [], "next_hops": [], "failover_group": None, "load_balance_algo": "round_robin", "advertised_prefixes": []},
                "vpn": {"ike_version": 2, "encryption_algo": "AES-256", "auth_algo": "SHA-256", "pfs_group": 14, "tunnel_endpoints": [], "dpd_timeout": 30},
            },
            "nipr": {
                "security": {"allowed_ports": [22, 80, 443, 8080], "denied_ports": [], "inspection_type": "deep_packet", "encryption_required": True, "mfa_required": True, "pki_required": True, "cdm_sensor": True, "idps_enabled": True},
                "routing": {"bgp_as": 65001, "static_routes": [], "next_hops": [], "failover_group": "nipr-primary", "load_balance_algo": "weighted", "advertised_prefixes": []},
                "vpn": {"ike_version": 2, "encryption_algo": "AES-256-GCM", "auth_algo": "SHA-384", "pfs_group": 20, "tunnel_endpoints": [], "dpd_timeout": 10},
            },
            "sipr": {
                "security": {"allowed_ports": [22, 443], "denied_ports": [80, 8080], "inspection_type": "deep_packet", "encryption_required": True, "mfa_required": True, "pki_required": True, "cdm_sensor": True, "idps_enabled": True},
                "routing": {"bgp_as": 65002, "static_routes": [], "next_hops": [], "failover_group": "sipr-primary", "load_balance_algo": "weighted", "advertised_prefixes": []},
                "vpn": {"ike_version": 2, "encryption_algo": "AES-256-GCM", "auth_algo": "SHA-512", "pfs_group": 21, "tunnel_endpoints": [], "dpd_timeout": 5},
            },
            "bcap_vdms": {
                "security": {"allowed_ports": [443], "denied_ports": [80], "inspection_type": "deep_packet", "encryption_required": True, "mfa_required": True, "pki_required": True, "cdm_sensor": True, "idps_enabled": True},
                "routing": {"bgp_as": None, "static_routes": [], "next_hops": [], "failover_group": "bcap-vdms", "load_balance_algo": "least_conn", "advertised_prefixes": []},
                "vpn": {"ike_version": 2, "encryption_algo": "AES-256-GCM", "auth_algo": "SHA-384", "pfs_group": 20, "tunnel_endpoints": [], "dpd_timeout": 10},
            },
            "bcap_vdss": {
                "security": {"allowed_ports": [443], "denied_ports": [80, 22], "inspection_type": "deep_packet", "encryption_required": True, "mfa_required": True, "pki_required": True, "cdm_sensor": True, "idps_enabled": True},
                "routing": {"bgp_as": None, "static_routes": [], "next_hops": [], "failover_group": "bcap-vdss", "load_balance_algo": "least_conn", "advertised_prefixes": []},
                "vpn": {"ike_version": 2, "encryption_algo": "AES-256-GCM", "auth_algo": "SHA-512", "pfs_group": 21, "tunnel_endpoints": [], "dpd_timeout": 5},
            },
            "csp_il2": {
                "security": {"allowed_ports": [80, 443], "denied_ports": [], "inspection_type": "stateful", "encryption_required": False, "mfa_required": False, "pki_required": False, "cdm_sensor": False, "idps_enabled": False},
                "routing": {"bgp_as": None, "static_routes": [], "next_hops": [], "failover_group": None, "load_balance_algo": "round_robin", "advertised_prefixes": []},
                "vpn": {"ike_version": 2, "encryption_algo": "AES-128", "auth_algo": "SHA-256", "pfs_group": 14, "tunnel_endpoints": [], "dpd_timeout": 30},
            },
            "csp_il4": {
                "security": {"allowed_ports": [443], "denied_ports": [80], "inspection_type": "deep_packet", "encryption_required": True, "mfa_required": True, "pki_required": True, "cdm_sensor": True, "idps_enabled": True},
                "routing": {"bgp_as": None, "static_routes": [], "next_hops": [], "failover_group": "il4-primary", "load_balance_algo": "weighted", "advertised_prefixes": []},
                "vpn": {"ike_version": 2, "encryption_algo": "AES-256-GCM", "auth_algo": "SHA-384", "pfs_group": 20, "tunnel_endpoints": [], "dpd_timeout": 10},
            },
            "csp_il5": {
                "security": {"allowed_ports": [443], "denied_ports": [80, 22], "inspection_type": "deep_packet", "encryption_required": True, "mfa_required": True, "pki_required": True, "cdm_sensor": True, "idps_enabled": True},
                "routing": {"bgp_as": None, "static_routes": [], "next_hops": [], "failover_group": "il5-primary", "load_balance_algo": "weighted", "advertised_prefixes": []},
                "vpn": {"ike_version": 2, "encryption_algo": "AES-256-GCM", "auth_algo": "SHA-512", "pfs_group": 21, "tunnel_endpoints": [], "dpd_timeout": 5},
            },
            "csp_il6": {
                "security": {"allowed_ports": [443], "denied_ports": [80, 22, 8080], "inspection_type": "deep_packet", "encryption_required": True, "mfa_required": True, "pki_required": True, "cdm_sensor": True, "idps_enabled": True},
                "routing": {"bgp_as": 65006, "static_routes": [], "next_hops": [], "failover_group": "il6-primary", "load_balance_algo": "weighted", "advertised_prefixes": []},
                "vpn": {"ike_version": 2, "encryption_algo": "AES-256-GCM", "auth_algo": "SHA-512", "pfs_group": 21, "tunnel_endpoints": [], "dpd_timeout": 5},
            },
            "inter_csp": {
                "security": {"allowed_ports": [443], "denied_ports": [80], "inspection_type": "deep_packet", "encryption_required": True, "mfa_required": True, "pki_required": True, "cdm_sensor": True, "idps_enabled": True},
                "routing": {"bgp_as": None, "static_routes": [], "next_hops": [], "failover_group": "inter-csp", "load_balance_algo": "weighted", "advertised_prefixes": []},
                "vpn": {"ike_version": 2, "encryption_algo": "AES-256-GCM", "auth_algo": "SHA-384", "pfs_group": 20, "tunnel_endpoints": [], "dpd_timeout": 10},
            },
            "dmz": {
                "security": {"allowed_ports": [80, 443, 25, 53], "denied_ports": [], "inspection_type": "stateful", "encryption_required": False, "mfa_required": False, "pki_required": False, "cdm_sensor": False, "idps_enabled": True},
                "routing": {"bgp_as": None, "static_routes": [], "next_hops": [], "failover_group": None, "load_balance_algo": "round_robin", "advertised_prefixes": []},
                "vpn": {"ike_version": 2, "encryption_algo": "AES-256", "auth_algo": "SHA-256", "pfs_group": 14, "tunnel_endpoints": [], "dpd_timeout": 30},
            },
            "internet": {
                "security": {"allowed_ports": [80, 443], "denied_ports": [], "inspection_type": "none", "encryption_required": False, "mfa_required": False, "pki_required": False, "cdm_sensor": False, "idps_enabled": False},
                "routing": {"bgp_as": None, "static_routes": [], "next_hops": [], "failover_group": None, "load_balance_algo": "round_robin", "advertised_prefixes": []},
                "vpn": {},
            },
            "custom": {"security": {}, "routing": {}, "vpn": {}},
        }
        return jsonify(templates), 200

    @bp.route("/api/twin/<topo_id>/persona-definitions", methods=["GET"])
    @nc_login_required
    def nc_api_twin_persona_definitions(topo_id):
        """Return all TFW persona definitions from tfw_personas.yaml."""
        from tools.network.narrative_generator import load_personas
        return jsonify({"personas": load_personas()}), 200

    @bp.route("/api/twin/<topo_id>/traffic-flows", methods=["GET", "POST"])
    @nc_login_required
    def nc_api_twin_traffic_flows(topo_id):
        """GET: list flows for a topology. POST: create a new flow (returns 201 + id)."""
        from tools.network.traffic_flow import TrafficFlowEngine
        from tools.network.db.init_db import get_connection as _gc

        conn = _gc()
        try:
            engine = TrafficFlowEngine()
            if request.method == "GET":
                flows = engine.list_flows(topo_id, conn)
                phase_filter = request.args.get("phase_id")
                if phase_filter:
                    flows = [f for f in flows if f.get("phase_id") == phase_filter]
                return jsonify({"flows": flows}), 200
            # POST — create
            data = request.get_json(silent=True) or {}
            name = (data.get("name") or "").strip()
            src_zone = (data.get("src_zone") or "").strip()
            dst_zone = (data.get("dst_zone") or "").strip()
            app_type = (data.get("app_type") or "sso_saml").strip()
            classification = (data.get("classification") or "NIPR").strip()
            if not name or not src_zone or not dst_zone:
                return jsonify({"error": "name, src_zone, and dst_zone are required"}), 400
            flow_id = engine.create_flow(topo_id, name, src_zone, dst_zone, app_type, classification, conn)
            return jsonify({"id": flow_id, "name": name, "src_zone": src_zone,
                            "dst_zone": dst_zone, "app_type": app_type,
                            "classification": classification}), 201
        except Exception as exc:
            logger.warning("traffic-flows error: %s", exc)
            return jsonify({"error": str(exc)}), 500
        finally:
            conn.close()

    @bp.route("/api/twin/<topo_id>/traffic-flows/<flow_id>/walkthrough", methods=["POST"])
    @nc_login_required
    def nc_api_twin_tfw_walkthrough(topo_id, flow_id):
        """Run multi-persona walkthrough for a traffic flow.

        Request body (JSON, all optional):
          personas        : list of persona IDs to generate (default: all 7)
          classification  : override flow classification (NIPR, IL4, IL5, IL6, SIPR)
          use_llm         : bool, default True
          force_regenerate: bool, default False — bypass the read-through
                            narrative cache and re-generate every step × persona

        Returns:
          {steps: [...], summary: {...}}
          Each step has: step_number, node_id, node_label, action_type,
          persona_responses: {persona_id: {narrative, detail_json}}
        """
        from tools.network.narrative_generator import generate_all
        from tools.network.db.init_db import get_connection

        body = request.get_json(silent=True) or {}
        personas = body.get("personas") or None
        use_llm = bool(body.get("use_llm", True))
        force_regenerate = bool(
            body.get("force_regenerate", request.args.get("force_regenerate") in ("1", "true", "True"))
        )
        phase_id_filter = request.args.get("phase_id") or body.get("phase_id")

        try:
            conn = get_connection()
            _ph = sql_placeholder(conn)
            engine_cls = None
            try:
                from tools.network.traffic_flow import TrafficFlowEngine
                engine_cls = TrafficFlowEngine
            except Exception:
                pass

            if engine_cls:
                engine = engine_cls()
                engine._ensure_tables(conn)
                if phase_id_filter:
                    engine.generate_walkthrough(flow_id, conn, phase_id=phase_id_filter)

            # Verify flow belongs to this topology
            flow_row = conn.execute(
                f"SELECT * FROM nc_traffic_flows WHERE id = {_ph} AND topology_id = {_ph}",
                (flow_id, topo_id),
            ).fetchone()
            if not flow_row:
                return jsonify({"error": "flow not found"}), 404

            flow = dict(flow_row)
            classification = body.get("classification") or flow.get("classification", "NIPR")

            result = generate_all(
                flow_id=flow_id,
                conn=conn,
                personas=personas,
                classification=classification,
                use_llm=use_llm,
                force_regenerate=force_regenerate,
            )

            # Reformat steps to use 'persona_responses' key for API consumers
            api_steps = []
            for step in result.get("steps", []):
                api_step = {
                    "step_number": step["step_number"],
                    "node_id":     step["node_id"],
                    "node_label":  step["node_label"],
                    "action_type": step["action_type"],
                    "persona_responses": step.get("personas", {}),
                }
                api_steps.append(api_step)

            return jsonify({"steps": api_steps, "summary": result.get("summary", {})}), 200
        except Exception as exc:
            logger.warning("walkthrough error: %s", exc)
            return jsonify({"error": str(exc)}), 500
        finally:
            try:
                conn.close()
            except Exception:
                pass

    @bp.route("/api/twin/<topo_id>/traffic-flows/<flow_id>/assign-phase", methods=["POST"])
    @nc_login_required
    def nc_api_twin_tfw_assign_phase(topo_id, flow_id):
        """Assign (or unassign) a traffic flow to a migration phase.

        Body: {"phase_id": "<id>"}  — pass null to unassign.
        """
        from tools.network.db.init_db import get_connection as _gc
        data = request.get_json(silent=True) or {}
        phase_id = data.get("phase_id")

        conn = _gc()
        _ph = sql_placeholder(conn)
        try:
            row = conn.execute(
                f"SELECT id FROM nc_traffic_flows WHERE id={_ph} AND topology_id={_ph}",
                (flow_id, topo_id),
            ).fetchone()
            if not row:
                return jsonify({"error": "flow not found"}), 404

            conn.execute(
                f"UPDATE nc_traffic_flows SET phase_id={_ph} WHERE id={_ph}",
                (phase_id, flow_id),
            )
            conn.commit()
            return jsonify({"status": "ok", "flow_id": flow_id, "phase_id": phase_id}), 200
        except Exception as exc:
            logger.warning("assign-phase error: %s", exc)
            return jsonify({"error": str(exc)}), 500
        finally:
            conn.close()

    # ══════════════════════════════════════════════════════════════════════
    # API: IP Address Planning Assistant
    # ══════════════════════════════════════════════════════════════════════

    _IP_PLAN_SYSTEM_PROMPT = """You are a network IP address planner. Given a supernet and topology nodes, allocate non-overlapping CIDRs.

Output ONLY a valid JSON object — no markdown, no explanation:
{"assignments": [{"node_id": "...", "label": "...", "type": "...", "cidr": "x.x.x.x/xx", "gateway": "x.x.x.x", "vlan": N}]}

Strategy rules:
- balanced: divide the supernet into equal-sized subnets for each logical segment
- power-of-2: size each subnet to the next power-of-2 that fits expected hosts
- flat: assign sequential /24 subnets

Planning rules:
1. Stay within the given supernet — never assign IPs outside it
2. Never overlap CIDRs
3. Logical segments (type=subnet, vlan, security-zone, vrf, aws-subnet, aws-vpc, az-vnet, gcp-vpc): assign a network CIDR (e.g. 10.0.1.0/24)
4. Devices (router, switch-l2, switch-l3, firewall, server, load-balancer, wap, siem, sdwan-edge, etc.): assign a host IP within the appropriate segment CIDR (e.g. 10.0.1.1/24)
5. If no explicit segments exist, group devices by inferred function and assign subnets accordingly
6. gateway = first usable IP (.1) of the subnet
7. vlan: assign sequential VLAN IDs starting at 10, incrementing by 10 per segment
8. Include ALL provided nodes in the assignments array
9. Output ONLY the JSON object"""

    @bp.route("/api/topologies/<topo_id>/plan-ips", methods=["POST"])
    @nc_login_required
    def nc_api_plan_ips(topo_id):
        """AI-assisted IP address planning: supernet → subnet/host allocation.

        Uses ICDEV™ LLM Router when available; falls back to deterministic
        subnet allocation so the feature works regardless of API keys.
        """
        conn = get_connection()
        _ph = sql_placeholder(conn)
        topo = conn.execute(
            f"SELECT id, name, graph_json FROM topologies WHERE id={_ph}",
            (topo_id,),
        ).fetchone()
        conn.close()
        if not topo:
            return jsonify({"error": "Topology not found"}), 404

        data = request.get_json(force=True, silent=True) or {}
        supernet = data.get("supernet", "").strip()
        strategy = data.get("strategy", "balanced").strip()
        if not supernet:
            return jsonify({"error": "supernet required"}), 400
        if strategy not in ("balanced", "power-of-2", "flat"):
            strategy = "balanced"

        # Parse graph nodes
        try:
            graph_data = json.loads(topo["graph_json"] or "{}")
        except Exception:
            graph_data = {}
        nodes = graph_data.get("nodes", [])

        _SKIP_TYPES = {
            "draw-rect", "draw-rounded-rect", "text-heading", "text-label",
            "text-badge", "media-fiber", "media-ge", "media-10ge", "media-100ge",
            "patch-panel", "meet-me-room", "cross-connect",
        }
        routable = [
            {"id": n["id"], "label": n.get("label", ""), "type": n.get("type", "")}
            for n in nodes
            if n.get("type", "") not in _SKIP_TYPES and n.get("type", "")
        ]
        if not routable:
            return jsonify({"error": "No addressable nodes found in topology"}), 400

        def _plan_ips_deterministic(nodes, strategy):
            """Deterministic subnet allocator using Python ipaddress."""
            import ipaddress
            net = ipaddress.ip_network(supernet, strict=False)

            SEGMENT_TYPES = {
                "subnet", "vlan", "security-zone", "vrf",
                "aws-subnet", "aws-vpc", "az-vnet", "gcp-vpc",
            }
            INFRA_TYPES = {"router", "firewall", "sdwan-edge"}
            DIST_TYPES = {"switch-l2", "switch-l3", "switch", "load-balancer"}
            COMPUTE_TYPES = {"server", "siem", "wap"}

            groups = {}
            for node in nodes:
                t = node.get("type", "")
                if t in SEGMENT_TYPES:
                    key = "segment_" + t
                elif t in INFRA_TYPES:
                    key = "infra"
                elif t in DIST_TYPES:
                    key = "distribution"
                elif t in COMPUTE_TYPES:
                    key = "compute"
                else:
                    key = "general"
                groups.setdefault(key, []).append(node)

            if not groups:
                return {"assignments": []}

            group_items = list(groups.items())

            if strategy == "flat":
                prefix_len = 24
                base = int(net.network_address)
                subnets = []
                for i in range(len(group_items)):
                    subnets.append(
                        ipaddress.ip_network(
                            f"{ipaddress.ip_address(base + i * 256)}/{prefix_len}",
                            strict=False,
                        )
                    )
            else:
                needed_bits = (len(group_items) - 1).bit_length()
                new_prefix = net.prefixlen + needed_bits
                if new_prefix > 30:
                    new_prefix = 30
                subnets = list(net.subnets(new_prefix=new_prefix))[:len(group_items)]

            assignments = []
            vlan_base = 10
            for idx, (key, members) in enumerate(group_items):
                subnet = subnets[idx]
                hosts = list(subnet.hosts())
                gateway = str(hosts[0]) if hosts else str(subnet.network_address + 1)
                vlan = vlan_base + idx * 10
                host_idx = 1
                for node in members:
                    t = node.get("type", "")
                    if t in SEGMENT_TYPES:
                        cidr = f"{subnet.network_address}/{subnet.prefixlen}"
                    else:
                        if host_idx < len(hosts):
                            ip = hosts[host_idx]
                            host_idx += 1
                        else:
                            ip = subnet.network_address + 2
                        cidr = f"{ip}/{subnet.prefixlen}"
                    assignments.append({
                        "node_id": node.get("id", ""),
                        "label": node.get("label", ""),
                        "type": t,
                        "cidr": cidr,
                        "gateway": gateway,
                        "vlan": vlan,
                    })
            return {"assignments": assignments}

        def _parse_ip_plan(content):
            import re
            text = content.strip()
            text = re.sub(r"\n?[\s\S]*?\n?", "", text, flags=re.DOTALL).strip()
            if text.startswith("```"):
                lines = [ln for ln in text.split("\n") if not ln.strip().startswith("```")]
                text = "\n".join(lines).strip()
            start = text.find("{")
            end = text.rfind("}") + 1
            if start < 0 or end <= start:
                return None, text[:500]
            try:
                result = json.loads(text[start:end])
            except json.JSONDecodeError:
                return None, text[:500]
            if "assignments" not in result:
                return None, text[:500]
            return result, None

        # Deterministic result is always available
        used_provider = "deterministic"
        result = _plan_ips_deterministic(routable, strategy)

        # Attempt LLM enhancement via ICDEV™ router (optional, non-blocking)
        try:
            from icdev.tools.llm.router import LLMRouter
            from icdev.tools.llm.provider import LLMRequest
            router = LLMRouter()
            req = LLMRequest(
                system_prompt=_IP_PLAN_SYSTEM_PROMPT,
                messages=[
                    {"role": "user", "content": (
                        f"Topology: {topo['name']}\n"
                        f"Supernet: {supernet}\n"
                        f"Strategy: {strategy}\n\n"
                        f"Nodes:\n{json.dumps(routable, indent=2)}\n\n"
                        f"Assign IP CIDRs from the supernet to these nodes using the {strategy} strategy."
                    )},
                ],
                max_tokens=4096,
                temperature=0.1,
            )
            resp = router.invoke("ip_planning", req)
            if resp and resp.content:
                llm_result, raw = _parse_ip_plan(resp.content)
                if llm_result and llm_result.get("assignments"):
                    result = llm_result
                    used_provider = resp.provider or "llm"
        except Exception:
            pass  # deterministic result already set

        _audit(
            "IP_PLAN", "topology", topo_id,
            f"[{used_provider}] {supernet} ({strategy}) → {len(result['assignments'])} assignments",
        )
        return jsonify({
            "assignments": result["assignments"],
            "supernet": supernet,
            "strategy": strategy,
            "provider": used_provider,
        })

    # ── AI Context Messages ────────────────────────────────────────────────
    @bp.route("/api/ai-context/<ctx_id>/messages", methods=["GET"])
    def nc_api_ai_context_messages(ctx_id):
        """Return up to 50 messages for an AI context, ordered by turn_number."""
        conn = get_connection()
        _ph = sql_placeholder(conn)
        try:
            rows = conn.execute(
                "SELECT id, role, content, turn_number FROM chat_messages"
                f" WHERE context_id = {_ph} ORDER BY turn_number ASC LIMIT 50",
                (ctx_id,),
            ).fetchall()
            messages = [
                {"role": r[1], "content": r[2], "turn_number": r[3]}
                for r in rows
            ]
            return jsonify({"messages": messages})
        finally:
            conn.close()

    # ══════════════════════════════════════════════════════════════════════
    # Subnet Calculator — page + API
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/subnet-calc")
    @nc_login_required
    def nc_subnet_calc():
        conn = get_connection()
        _ph = sql_placeholder(conn)
        project_id = request.args.get("project", "")
        projects = [_row_to_dict(r) for r in conn.execute("SELECT id, name FROM nc_projects ORDER BY name").fetchall()]
        if project_id:
            rows = conn.execute(
                f"SELECT * FROM nc_subnet_calc_history WHERE project_id={_ph} ORDER BY created_at DESC",
                (project_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT h.*, p.name AS project_name FROM nc_subnet_calc_history h "
                "LEFT JOIN nc_projects p ON p.id=h.project_id ORDER BY h.created_at DESC LIMIT 200"
            ).fetchall()
        history = [_row_to_dict(r) for r in rows]
        conn.close()
        active_project = next((p for p in projects if p["id"] == project_id), None)
        return render_template(
            "network/subnet_calc.html",
            projects=projects,
            history=history,
            filter_project=project_id,
            active_project=active_project,
        )

    @bp.route("/api/nc-projects", methods=["POST"])
    @nc_login_required
    def nc_api_create_project_quick():
        """Lightweight project creation — name + optional description/owner only."""
        data = request.get_json(force=True, silent=True) or {}
        name = (data.get("name") or "").strip()
        if not name:
            return jsonify({"error": "name is required"}), 400
        pid = str(_uuid.uuid4())
        now = _now()
        conn = get_connection()
        _ph = sql_placeholder(conn)
        conn.execute(
            "INSERT INTO nc_projects (id, name, description, status, owner, created_at, updated_at) "
            f"VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph})",
            (pid, name, data.get("description", ""), data.get("status", "draft"), data.get("owner", ""), now, now),
        )
        conn.commit()
        conn.close()
        _audit("CREATE", "project", pid, name)
        return jsonify({"id": pid, "name": name}), 201

    @bp.route("/api/nc-projects", methods=["GET"])
    @nc_login_required
    def nc_api_list_projects_quick():
        """Return all nc_projects as id/name pairs for dropdowns."""
        conn = get_connection()
        rows = conn.execute("SELECT id, name, status FROM nc_projects ORDER BY name").fetchall()
        conn.close()
        return jsonify([_row_to_dict(r) for r in rows])

    @bp.route("/api/subnet-calc", methods=["GET"])
    @nc_login_required
    def nc_api_list_subnet_calc():
        conn = get_connection()
        _ph = sql_placeholder(conn)
        project_id = request.args.get("project", "")
        if project_id:
            rows = conn.execute(
                f"SELECT * FROM nc_subnet_calc_history WHERE project_id={_ph} ORDER BY created_at DESC",
                (project_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM nc_subnet_calc_history ORDER BY created_at DESC LIMIT 200"
            ).fetchall()
        conn.close()
        return jsonify([_row_to_dict(r) for r in rows])

    @bp.route("/api/subnet-calc", methods=["POST"])
    @nc_login_required
    def nc_api_save_subnet_calc():
        import ipaddress
        data = request.get_json(force=True, silent=True) or {}
        cidr = (data.get("cidr") or "").strip()
        project_id = (data.get("project_id") or "").strip()
        if not cidr or not project_id:
            return jsonify({"error": "cidr and project_id required"}), 400

        # Validate and compute server-side
        try:
            net = ipaddress.ip_network(cidr, strict=False)
        except ValueError as exc:
            return jsonify({"error": f"Invalid CIDR: {exc}"}), 422

        prefix_len = net.prefixlen
        af = "ipv6" if net.version == 6 else "ipv4"
        total_hosts = net.num_addresses
        if net.version == 4:
            usable = max(0, total_hosts - 2) if prefix_len < 31 else total_hosts
            subnet_mask = str(net.netmask)
            wildcard = str(net.hostmask)
            first_addr = str(net.network_address + 1) if prefix_len < 31 else str(net.network_address)
            last_addr = str(net.broadcast_address - 1) if prefix_len < 31 else str(net.broadcast_address)
            first_octet = int(str(net.network_address).split(".")[0])
            if first_octet < 128:
                ip_class = "A"
            elif first_octet < 192:
                ip_class = "B"
            elif first_octet < 224:
                ip_class = "C"
            elif first_octet < 240:
                ip_class = "D"
            else:
                ip_class = "E"
            broadcast = str(net.broadcast_address)
        else:
            usable = None   # too large for SQLite INTEGER; frontend uses prefix_len
            total_hosts = None
            subnet_mask = f"/{prefix_len}"
            wildcard = str(net.hostmask)
            first_addr = str(net.network_address + 1)
            last_addr = str(net.broadcast_address - 1)
            ip_class = data.get("ip_class") or "Global Unicast"
            broadcast = str(net.broadcast_address)

        entry_id = str(_uuid.uuid4())
        now = _now()
        conn = get_connection()
        _ph = sql_placeholder(conn)
        # Dedup: INSERT OR REPLACE (UNIQUE on cidr+project_id)
        existing = conn.execute(
            f"SELECT id FROM nc_subnet_calc_history WHERE cidr={_ph} AND project_id={_ph}",
            (str(net.with_prefixlen), project_id),
        ).fetchone()
        if existing:
            entry_id = existing[0]
            conn.execute(
                f"UPDATE nc_subnet_calc_history SET network_addr={_ph},broadcast={_ph},first_host={_ph},last_host={_ph},"
                f"total_hosts={_ph},usable_hosts={_ph},prefix_len={_ph},subnet_mask={_ph},wildcard_mask={_ph},address_family={_ph},"
                f"ip_class={_ph},notes={_ph},created_at={_ph} WHERE id={_ph}",
                (str(net.network_address), broadcast, first_addr, last_addr,
                 total_hosts, usable, prefix_len, subnet_mask, wildcard,
                 af, ip_class, data.get("notes", ""), now, entry_id),
            )
        else:
            conn.execute(
                "INSERT INTO nc_subnet_calc_history "
                "(id,project_id,cidr,network_addr,broadcast,first_host,last_host,"
                "total_hosts,usable_hosts,prefix_len,subnet_mask,wildcard_mask,"
                "address_family,ip_class,notes,created_at) "
                f"VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph})",
                (entry_id, project_id, str(net.with_prefixlen),
                 str(net.network_address), broadcast, first_addr, last_addr,
                 total_hosts, usable, prefix_len, subnet_mask, wildcard,
                 af, ip_class, data.get("notes", ""), now),
            )
        conn.commit()
        conn.close()
        _audit("SAVE", "subnet_calc", entry_id, cidr)
        return jsonify({"id": entry_id, "cidr": str(net.with_prefixlen), "updated": bool(existing)}), 201

    @bp.route("/api/subnet-calc/<entry_id>", methods=["DELETE"])
    @nc_login_required
    def nc_api_delete_subnet_calc(entry_id):
        conn = get_connection()
        _ph = sql_placeholder(conn)
        conn.execute(f"DELETE FROM nc_subnet_calc_history WHERE id={_ph}", (entry_id,))
        conn.commit()
        conn.close()
        _audit("DELETE", "subnet_calc", entry_id)
        return jsonify({"deleted": entry_id})

    # ── Migration Phases View ───────────────────────────────────────────────

    @bp.route("/migration-phases/<topo_id>")
    @nc_login_required
    def nc_migration_phases(topo_id):
        """Three-panel migration phases view: Current → Phase N → Final/To-Be."""
        conn = get_connection()
        _ph = sql_placeholder(conn)
        topo = conn.execute(
            f"SELECT id, name, graph_json FROM topologies WHERE id={_ph}", (topo_id,)
        ).fetchone()
        if not topo:
            conn.close()
            return "Topology not found", 404

        # Fetch migration phases linked to any project that uses this topology
        phases = conn.execute(
            f"""
            SELECT mp.id, mp.phase_num, mp.title, mp.description,
                   mp.duration_days, mp.parallel_run, mp.rollback_criteria,
                   mp.maintenance_window, mp.dependencies, mp.status
            FROM nc_migration_phases mp
            JOIN nc_project_topologies pt ON pt.project_id = mp.project_id
            WHERE pt.topology_id = {_ph}
            ORDER BY mp.phase_num
            """,
            (topo_id,),
        ).fetchall()
        conn.close()

        topo_name = topo["name"] if hasattr(topo, "__getitem__") else topo[1]
        phases_list = [dict(p) if hasattr(p, "keys") else {
            "id": p[0], "phase_num": p[1], "title": p[2], "description": p[3],
            "duration_days": p[4], "parallel_run": p[5], "rollback_criteria": p[6],
            "maintenance_window": p[7], "dependencies": p[8], "status": p[9],
        } for p in phases]

        return render_template(
            "network/migration_phases.html",
            topo_id=topo_id,
            topo_name=topo_name,
            phases=phases_list,
            phase_count=len(phases_list),
        )

    @bp.route("/api/migration-phases/<topo_id>/data", methods=["GET"])
    @nc_login_required
    def nc_api_migration_phases_data(topo_id):
        """Return all phase graphs + info boxes + consolidation as JSON."""
        import json as _json
        from tools.network.migration_phases import (
            compute_infoboxes, compute_final_infoboxes,
            generate_phase_graph, generate_final_graph,
            generate_phase_physical_graph, generate_phase_logical_graph,
            compute_physical_infoboxes, compute_logical_infoboxes,
            run_consolidation_analysis, load_consolidation,
        )

        conn = get_connection()
        _ph = sql_placeholder(conn)
        topo_row = conn.execute(
            f"SELECT graph_json, name FROM topologies WHERE id={_ph}", (topo_id,)
        ).fetchone()
        if not topo_row:
            conn.close()
            return jsonify({"error": "topology not found"}), 404

        graph_json, topo_name = topo_row[0], topo_row[1]
        current_graph = _json.loads(graph_json) if graph_json else {"nodes": [], "edges": []}

        phases = conn.execute(
            f"""
            SELECT mp.id, mp.phase_num, mp.title, mp.description,
                   mp.duration_days, mp.parallel_run, mp.rollback_criteria,
                   mp.maintenance_window, mp.dependencies, mp.status,
                   mp.properties_json
            FROM nc_migration_phases mp
            JOIN nc_project_topologies pt ON pt.project_id = mp.project_id
            WHERE pt.topology_id = {_ph}
            ORDER BY mp.phase_num
            """,
            (topo_id,),
        ).fetchall()

        phases_list = []
        for p in phases:
            d = dict(p) if hasattr(p, "keys") else {
                "id": p[0], "phase_num": p[1], "title": p[2], "description": p[3],
                "duration_days": p[4], "parallel_run": p[5], "rollback_criteria": p[6],
                "maintenance_window": p[7], "dependencies": p[8], "status": p[9],
                "properties_json": p[10] if len(p) > 10 else "{}",
            }
            d["total_phases"] = len(phases)
            phases_list.append(d)

        # Build per-phase graphs and info boxes
        phase_data = []
        for pm in phases_list:
            pm["total_phases"] = len(phases_list)
            ph_graph = generate_phase_graph(current_graph, pm)
            ph_boxes = compute_infoboxes(ph_graph, phase_key=f"phase-{pm['phase_num']}", phase_meta=pm)

            # Physical & Logical views (2-hop subgraphs with annotations)
            try:
                phy_graph = generate_phase_physical_graph(current_graph, pm, conn)
                phy_boxes = compute_physical_infoboxes(phy_graph, phase_meta=pm)
            except Exception:
                phy_graph = ph_graph
                phy_boxes = ph_boxes
            try:
                log_graph = generate_phase_logical_graph(current_graph, pm)
                log_boxes = compute_logical_infoboxes(log_graph, phase_meta=pm)
            except Exception:
                log_graph = ph_graph
                log_boxes = ph_boxes

            phase_data.append({
                "phase_num": pm["phase_num"],
                "title": pm["title"],
                "status": pm["status"],
                "graph": ph_graph,
                "infoboxes": ph_boxes,
                "graph_physical": phy_graph,
                "infoboxes_physical": phy_boxes,
                "graph_logical": log_graph,
                "infoboxes_logical": log_boxes,
            })

        conn.close()

        # Final/To-Be graph + consolidation
        final_graph = generate_final_graph(current_graph, phases_list)
        consolidation = load_consolidation(topo_id)
        if not consolidation:
            consolidation = run_consolidation_analysis(current_graph, final_graph)
            consolidation["current_device_count"] = len(current_graph.get("nodes", []))
            consolidation["final_device_count"] = len(final_graph.get("nodes", []))
        final_boxes = compute_final_infoboxes(current_graph, final_graph, consolidation)

        return jsonify({
            "topo_id": topo_id,
            "topo_name": topo_name,
            "current": {
                "graph": current_graph,
                "infoboxes": compute_infoboxes(current_graph, phase_key="current"),
            },
            "phases": phase_data,
            "final": {
                "graph": final_graph,
                "infoboxes": final_boxes,
                "consolidation": consolidation,
            },
        })

    @bp.route("/api/migration-phases/<topo_id>/export/<phase_key>/<fmt>", methods=["POST"])
    @nc_login_required
    def nc_api_migration_phases_export(topo_id, phase_key, fmt):
        """Export a single phase panel as PDF, Visio, or Draw.io.

        phase_key: 'current', 'phase-N', or 'final'
        fmt: 'pdf', 'visio', 'drawio'
        """
        import json as _json
        from tools.network.migration_phases import (
            compute_infoboxes, compute_final_infoboxes,
            generate_phase_graph, generate_final_graph,
            run_consolidation_analysis, load_consolidation,
        )

        conn = get_connection()
        _ph = sql_placeholder(conn)
        topo_row = conn.execute(
            f"SELECT graph_json, name FROM topologies WHERE id={_ph}", (topo_id,)
        ).fetchone()
        if not topo_row:
            conn.close()
            return jsonify({"error": "topology not found"}), 404
        graph_json, topo_name = topo_row[0], topo_row[1]
        current_graph = _json.loads(graph_json) if graph_json else {"nodes": [], "edges": []}
        conn.close()

        # Resolve which graph + label to export
        if phase_key == "current":
            graph = current_graph
            phase_label = "Current State"
            infoboxes = compute_infoboxes(graph, phase_key="current")
            consolidation = None
            phase_meta = None
        elif phase_key == "final":
            phases_raw = []  # fetch from DB if needed — simplified: use empty for now
            graph = generate_final_graph(current_graph, phases_raw)
            phase_label = "Final / To-Be State"
            consolidation = load_consolidation(topo_id) or run_consolidation_analysis(current_graph, graph)
            infoboxes = compute_final_infoboxes(current_graph, graph, consolidation)
            phase_meta = None
        else:
            # phase-N
            try:
                pnum = int(phase_key.split("-")[1])
            except Exception:
                pnum = 1
            phase_meta = {"phase_num": pnum, "total_phases": pnum, "title": f"Phase {pnum}"}
            graph = generate_phase_graph(current_graph, phase_meta)
            phase_label = f"Phase {pnum}"
            infoboxes = compute_infoboxes(graph, phase_key=phase_key, phase_meta=phase_meta)
            consolidation = None

        safe_name = topo_name.replace(" ", "_").replace("/", "-")[:40]

        if fmt == "pdf":
            from tools.network.pdf_export import export_phase_pdf
            pdf_bytes = export_phase_pdf(
                topo_name, phase_label, graph, infoboxes, consolidation, phase_meta
            )
            is_html = pdf_bytes[:15].startswith(b"<!DOCTYPE")
            if is_html:
                return current_app.response_class(
                    pdf_bytes,
                    mimetype="text/html",
                    headers={"Content-Disposition": f'attachment; filename="{safe_name}_{phase_key}_report.html"'},
                )
            return current_app.response_class(
                pdf_bytes,
                mimetype="application/pdf",
                headers={"Content-Disposition": f'attachment; filename="{safe_name}_{phase_key}.pdf"'},
            )

        if fmt == "visio":
            from tools.network.visio_export import export_vsdx
            vsdx_bytes = export_vsdx(f"{topo_name} — {phase_label}", graph)
            return current_app.response_class(
                vsdx_bytes,
                mimetype="application/vnd.visio",
                headers={"Content-Disposition": f'attachment; filename="{safe_name}_{phase_key}.vsdx"'},
            )

        if fmt == "drawio":
            from tools.network.export_import import to_drawio
            xml = to_drawio(graph, f"{topo_name} — {phase_label}")
            return current_app.response_class(
                xml.encode("utf-8"),
                mimetype="application/octet-stream",
                headers={"Content-Disposition": f'attachment; filename="{safe_name}_{phase_key}.drawio"'},
            )

        return jsonify({"error": f"Unknown format: {fmt}"}), 400

    # ── AI Migration Plan Generator ────────────────────────────────────────
    @bp.route("/api/migration-plan/generate", methods=["POST"])
    @nc_login_required
    def nc_api_migration_plan_generate():
        """Decompose a NL description into migration phases using LLM.

        Body: {"description": "...", "project_id": "...", "topo_id": "..."}
        Returns: {"phases_created": N, "phase_ids": [...]}
        """
        import uuid as _uuid

        data = request.get_json(force=True, silent=True) or {}
        description = (data.get("description") or "").strip()
        project_id = (data.get("project_id") or "").strip()
        if not description:
            return jsonify({"error": "description is required"}), 400

        try:
            raw_text, err = _route_llm(
                "network_topology",
                _AI_MIGRATION_PLAN_PROMPT,
                [{"role": "user", "content": description}],
                2048,
            )
            if err:
                return jsonify({"error": f"LLM unavailable: {err}"}), 503
            raw_text = (raw_text or "").strip()
            # Strip markdown fences if present
            if raw_text.startswith("```"):
                raw_text = raw_text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            phases_data = json.loads(raw_text)
            if not isinstance(phases_data, list):
                return jsonify({"error": "LLM returned unexpected format"}), 500
        except Exception as exc:
            logger.exception("migration-plan/generate LLM call failed")
            return jsonify({"error": str(exc)}), 500

        if not project_id:
            return jsonify({"error": "project_id is required"}), 400

        conn = get_connection()
        _ph = sql_placeholder(conn)
        try:
            phase_ids = []
            for ph in phases_data:
                pid = str(_uuid.uuid4())
                conn.execute(
                    f"""INSERT INTO nc_migration_phases
                       (id, project_id, phase_num, title, description,
                        duration_days, parallel_run, rollback_criteria,
                        maintenance_window, classification, impact_level, status)
                       VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},'planned')""",

                    (
                        pid,
                        project_id,
                        int(ph.get("phase_num", len(phase_ids) + 1)),
                        str(ph.get("title", f"Phase {len(phase_ids)+1}")),
                        str(ph.get("description", "")),
                        int(ph.get("duration_days", 7)),
                        int(ph.get("parallel_run", 0)),
                        str(ph.get("rollback_criteria", "")),
                        str(ph.get("maintenance_window", "")),
                        str(ph.get("classification", "CUI")),
                        str(ph.get("impact_level", "IL4")),
                    ),
                )
                phase_ids.append(pid)
                # Auto-link SOPs: extract meaningful tokens from phase text, match against SOP titles
                _stop = {"the","and","or","for","with","from","that","this","into","will","phase",
                         "have","been","each","only","when","also","are","was","were","all","not"}
                phase_text = f"{ph.get('title','')} {ph.get('description','')}".lower()
                import re as _re
                raw_tokens = _re.findall(r"[a-z][a-z0-9\-]{3,}", phase_text)
                sop_keywords = {t for t in raw_tokens if t not in _stop}
                if sop_keywords:
                    sop_rows = conn.execute(
                        "SELECT sop_id, title FROM ndc_sops WHERE status = 'approved'"
                    ).fetchall()
                    for sop_row in sop_rows:
                        sop_title_lower = (sop_row["title"] or "").lower()
                        if any(kw in sop_title_lower for kw in sop_keywords):
                            link_id = str(_uuid.uuid4())
                            try:
                                conn.execute(
                                    f"""INSERT OR IGNORE INTO nc_phase_documents
                                       (id, phase_id, project_id, doc_source, doc_id,
                                        doc_title, doc_type, relevance_note, display_order)
                                       VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},0)""",

                                    (link_id, pid, project_id, "sop",
                                     sop_row["sop_id"], sop_row["title"],
                                     "sop", "auto-linked by keyword match"),
                                )
                            except Exception as _exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
                                logger.warning(
                                    "nc_api_migration_plan_generate: best-effort INSERT into nc_phase_documents failed "
                                    "(non-blocking): %s",
                                    _exc,
                                )
            conn.commit()
            return jsonify({"phases_created": len(phase_ids), "phase_ids": phase_ids}), 201
        except Exception as exc:
            logger.warning("migration-plan insert error: %s", exc)
            return jsonify({"error": str(exc)}), 500
        finally:
            conn.close()

    # ── Phase Status Update + Snapshot ────────────────────────────────────
    @bp.route("/api/migration-phases/<topo_id>/<phase_id>/status", methods=["PUT"])
    @nc_login_required
    def nc_api_migration_phase_status(topo_id, phase_id):
        """Update phase status; if completed, snapshot the topology.

        Body (JSON):
          status         : "completed" | "in_progress" | "rolled_back"
          classification : optional override ("PUBLIC"|"CUI"|"SECRET"|"TS")
          impact_level   : optional override ("IL2"|"IL4"|"IL5"|"IL6")
        """
        import json as _json
        import uuid as _uuid
        from tools.network.migration_phases import (
            generate_phase_graph,
            run_consolidation_analysis,
            save_consolidation,
        )

        data = request.get_json(silent=True) or {}
        new_status = data.get("status", "").strip()
        valid_statuses = {"planned", "in_progress", "completed", "rolled_back"}
        if new_status not in valid_statuses:
            return jsonify({"error": f"status must be one of {sorted(valid_statuses)}"}), 400

        conn = get_connection()
        _ph = sql_placeholder(conn)
        try:
            phase_row = conn.execute(
                f"SELECT * FROM nc_migration_phases WHERE id={_ph}", (phase_id,)
            ).fetchone()
            if not phase_row:
                return jsonify({"error": "phase not found"}), 404
            phase = dict(phase_row)

            update_fields = [f"status={_ph}"]
            update_vals: list = [new_status]
            classification = data.get("classification")
            impact_level = data.get("impact_level")
            if classification:
                update_fields.append(f"classification={_ph}")
                update_vals.append(classification)
            if impact_level:
                update_fields.append(f"impact_level={_ph}")
                update_vals.append(impact_level)
            update_vals.extend([phase_id])

            conn.execute(
                f"UPDATE nc_migration_phases SET {', '.join(update_fields)} WHERE id={_ph}",
                update_vals,
            )

            snapshot_id = None
            if new_status == "completed":
                topo_row = conn.execute(
                    f"SELECT graph_json FROM topologies WHERE id={_ph}", (topo_id,)
                ).fetchone()
                if topo_row:
                    current_graph = _json.loads(topo_row[0] or "{}") or {"nodes": [], "edges": []}
                    phase_meta = dict(phase)
                    phase_meta["status"] = new_status
                    post_graph = generate_phase_graph(current_graph, phase_meta)
                    snapshot_id = str(_uuid.uuid4())
                    conn.execute(
                        f"""INSERT INTO nc_topology_snapshots (id, topo_id, phase_id, label, graph_json)
                           VALUES ({_ph},{_ph},{_ph},{_ph},{_ph})""",

                        (
                            snapshot_id,
                            topo_id,
                            phase_id,
                            f"Phase {phase.get('phase_num', f'{_ph}')} Complete",
                            _json.dumps(post_graph),
                        ),
                    )
                    try:
                        analysis = run_consolidation_analysis(current_graph, post_graph)
                        save_consolidation(topo_id, analysis)
                    except Exception:
                        # Best-effort cache write — never block the phase status
                        # update — but log the failure so a broken upsert
                        # (e.g. missing UNIQUE constraint) is visible rather than
                        # silently swallowed (ndc-fix-03).
                        logger.warning(
                            "consolidation cache update failed during phase completion "
                            "(topo_id=%s)", topo_id, exc_info=True,
                        )

                _audit(conn, "phase_completed", {
                    "phase_id": phase_id,
                    "topo_id": topo_id,
                    "classification": classification or phase.get("classification", "CUI"),
                })
                # DIC Canvas Synergy — emit migration phase complete event (dsyn-emit-02)
                try:
                    from tools.network.event_emitter import emit_migration_phase_complete
                    emit_migration_phase_complete(
                        phase_id=phase_id,
                        migration_id=topo_id,
                        phase_name=phase.get("phase_name", ""),
                        classification=classification or phase.get("classification", "CUI"),
                    )
                except Exception:
                    pass  # event emission never blocks phase status update

            # DIC Canvas Synergy — emit anomaly event on rollback (dsyn-emit-02)
            if new_status == "rolled_back":
                try:
                    from tools.network.event_emitter import emit_anomaly_detected
                    emit_anomaly_detected(
                        migration_id=topo_id,
                        anomaly_type="phase_rollback",
                        severity="high",
                        change_summary=f"Migration phase '{phase.get('phase_name', phase_id)}' was rolled back",
                    )
                except Exception:
                    pass  # event emission never blocks phase status update

            conn.commit()
            return jsonify({"status": "ok", "snapshot_id": snapshot_id}), 200
        except Exception as exc:
            logger.warning("phase status update error: %s", exc)
            return jsonify({"error": str(exc)}), 500
        finally:
            conn.close()

    # ── Migration Hub ──────────────────────────────────────────────────────
    @bp.route("/migration-hub")
    def migration_hub():
        """Hub page: all migration projects, phases, and linked documentation."""
        return render_template("network/migration_hub.html")

    @bp.route("/api/migration-hub/data", methods=["GET"])
    def migration_hub_data():
        """Return all projects + phases + linked docs as JSON for the hub."""
        conn = get_connection()
        try:
            # Projects
            projects = [dict(r) for r in conn.execute(
                "SELECT id, name, description, status, owner, created_at FROM nc_projects ORDER BY created_at DESC"
            ).fetchall()]

            # Phases per project
            phases_raw = conn.execute(
                """SELECT id, project_id, phase_num, title, description,
                          duration_days, status, maintenance_window, rollback_criteria,
                          dependencies, classification, impact_level, created_at
                   FROM nc_migration_phases
                   ORDER BY project_id, phase_num"""
            ).fetchall()
            phases_by_project: dict = {}
            for row in phases_raw:
                r = dict(row)
                pid = r["project_id"]
                phases_by_project.setdefault(pid, []).append(r)

            # Phase documents
            phase_docs_raw = conn.execute(
                """SELECT id, phase_id, project_id, doc_source, doc_id,
                          doc_title, doc_type, relevance_note, display_order
                   FROM nc_phase_documents
                   ORDER BY project_id, display_order"""
            ).fetchall()
            docs_by_phase: dict = {}
            for row in phase_docs_raw:
                r = dict(row)
                docs_by_phase.setdefault(r["phase_id"], []).append(r)

            # Standalone uploaded docs (not yet linked to a phase but in project)
            docs_raw = conn.execute(
                """SELECT id, file_name, doc_type, project_id,
                          topology_id, classification, status, ingested_at
                   FROM nc_documents
                   WHERE status = 'ingested'
                   ORDER BY project_id, ingested_at DESC"""
            ).fetchall()
            docs_by_project: dict = {}
            for row in docs_raw:
                r = dict(row)
                pid = r["project_id"] or "default"
                docs_by_project.setdefault(pid, []).append(r)

            # Runbooks
            runbooks_raw = conn.execute(
                """SELECT id, title, trigger_event, severity, owner, topology_id, classification
                   FROM ndc_runbooks ORDER BY title"""
            ).fetchall()
            runbooks = [dict(r) for r in runbooks_raw]

            # SOPs
            sops_raw = conn.execute(
                """SELECT sop_id AS id, title, category, version, status,
                          description, classification, author
                   FROM ndc_sops ORDER BY category, title"""
            ).fetchall()
            sops = [dict(r) for r in sops_raw]

            # Topologies (for MP links)
            topos_raw = conn.execute(
                "SELECT id, name FROM topologies ORDER BY name"
            ).fetchall()
            topos = [dict(r) for r in topos_raw]

            # First topology per project (for 3-panel diagram links)
            topo_by_project = {}
            for r in conn.execute(
                "SELECT project_id, topology_id FROM nc_project_topologies"
            ).fetchall():
                topo_by_project.setdefault(r[0], r[1])

            # Attach phases and docs to projects
            for proj in projects:
                pid = proj["id"]
                proj_phases = phases_by_project.get(pid, [])
                for phase in proj_phases:
                    phase["documents"] = docs_by_phase.get(phase["id"], [])
                proj["phases"] = proj_phases
                proj["documents"] = docs_by_project.get(pid, [])
                proj["topology_id"] = topo_by_project.get(pid)

            return jsonify({
                "projects": projects,
                "runbooks": runbooks,
                "sops": sops,
                "topologies": topos,
            })
        finally:
            conn.close()

    @bp.route("/api/migration-hub/phase-docs", methods=["POST"])
    def migration_hub_link_doc():
        """Link a document/runbook/SOP to a migration phase."""
        data = request.get_json(force=True) or {}
        required = {"phase_id", "project_id", "doc_source", "doc_id", "doc_title"}
        if missing := required - data.keys():
            return jsonify({"error": f"Missing fields: {missing}"}), 400

        conn = get_connection()
        _ph = sql_placeholder(conn)
        try:
            doc_id = str(_uuid.uuid4())
            conn.execute(
                f"""INSERT INTO nc_phase_documents
                   (id, phase_id, project_id, doc_source, doc_id,
                    doc_title, doc_type, relevance_note, display_order)
                   VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph})""",

                (
                    doc_id,
                    data["phase_id"],
                    data["project_id"],
                    data["doc_source"],
                    data["doc_id"],
                    data["doc_title"],
                    data.get("doc_type", ""),
                    data.get("relevance_note", ""),
                    data.get("display_order", 0),
                ),
            )
            conn.commit()
            return jsonify({"id": doc_id, "status": "linked"})
        finally:
            conn.close()

    @bp.route("/api/migration-hub/phase-docs/<doc_link_id>", methods=["DELETE"])
    def migration_hub_unlink_doc(doc_link_id: str):
        """Remove a document link from a migration phase."""
        conn = get_connection()
        _ph = sql_placeholder(conn)
        try:
            conn.execute(f"DELETE FROM nc_phase_documents WHERE id = {_ph}", (doc_link_id,))
            conn.commit()
            return jsonify({"status": "deleted"})
        finally:
            conn.close()

    # ── Unified Project Dashboard ──────────────────────────────────────────
