# CUI // SP-CTI
"""ICDEV Network Design Canvas -- topology route group.

Extracted verbatim from tools/network/blueprint.py (cvx-net-01 monolith split).
Registered on the shared NDC blueprint via register_topology_routes(bp).
"""
from __future__ import annotations

import json
import os
import uuid as _uuid
import zipfile
from flask import jsonify, request
from tools.network.routes._common import _classify_imported_nodes, logger
from tools.canvas.ai_trace_mixin import record_canvas_decision
from tools.db.storage import sql_placeholder
from tools.network.blueprint_helpers import (
    _audit,
    _now,
    _row_to_dict,
    invalidate_parsed_graph,
    nc_login_required,
)
from tools.network.config_generator import generate_device_configs, generate_device_configs_zip, list_configurable_nodes
from tools.network.constants import CLOUD_OBJECTS
from tools.network.db.init_db import get_connection
from tools.network.export_import import import_drawio, import_svg, import_vdx, to_drawio, to_svg, to_vdx
from tools.network.inventory_export import to_ansible_inventory, to_terraform_hcl
from tools.network.simulation import _add_narrative, _run_simulation
from tools.network.visio_export import export_ops_csvs, export_vsdx


def _route_llm(function, system_prompt, messages, max_tokens, temperature=None):
    """Invoke the configured LLM through LLMRouter (lpx-router-02).

    Replaces the previous direct provider POSTs so provider selection, an
    optional proxy ``base_url``, budgets and audit all flow through the router
    instead of reading a provider API key from the environment and hardcoding a
    Claude model. Returns ``(content, error)``.
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


def register_topology_routes(bp):
    """Register topology routes on the NDC blueprint."""

    @bp.route("/api/projects/<pid>/clone", methods=["POST"])
    @nc_login_required
    def nc_api_clone_project(pid):
        data = request.get_json(force=True, silent=True) or {}
        new_name = data.get("name", "")
        conn = get_connection()
        _ph = sql_placeholder(conn)
        orig = conn.execute(f"SELECT * FROM nc_projects WHERE id={_ph}", (pid,)).fetchone()
        if not orig:
            conn.close()
            return jsonify({"error": "Project not found"}), 404
        orig = _row_to_dict(orig)

        now = _now()
        new_pid = str(_uuid.uuid4())
        conn.execute(
            "INSERT INTO nc_projects "
            "(id, name, customer_id, description, status, owner, "
            f" created_at, updated_at) VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph})",
            (
                new_pid,
                new_name or f"{orig['name']} (Copy)",
                orig.get("customer_id"),
                orig.get("description", ""),
                "draft",
                orig.get("owner", ""),
                now,
                now,
            ),
        )

        # Deep-copy topologies
        topo_map = {}  # old_id -> new_id
        orig_topos = conn.execute(f"SELECT topology_id FROM nc_project_topologies WHERE project_id={_ph}", (pid,)).fetchall()
        for row in orig_topos:
            old_tid = row[0]
            topo = conn.execute(f"SELECT * FROM topologies WHERE id={_ph}", (old_tid,)).fetchone()
            if not topo:
                continue
            topo = _row_to_dict(topo)
            new_tid = str(_uuid.uuid4())
            topo_map[old_tid] = new_tid
            conn.execute(
                "INSERT INTO topologies "
                "(id, name, description, graph_json, template_id, "
                " classification, created_at, updated_at) "
                f"VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph})",
                (
                    new_tid,
                    f"{topo['name']} (Copy)",
                    topo.get("description", ""),
                    topo.get("graph_json", '{"nodes":[],"edges":[]}'),
                    topo.get("template_id"),
                    topo.get("classification", "public"),
                    now,
                    now,
                ),
            )
            conn.execute(f"INSERT INTO nc_project_topologies (project_id, topology_id) VALUES ({_ph},{_ph})", (new_pid, new_tid))

            # Copy compliance profile
            profile = conn.execute(f"SELECT * FROM nc_compliance_profiles WHERE topology_id={_ph}", (old_tid,)).fetchone()
            if profile:
                conn.execute(
                    "INSERT INTO nc_compliance_profiles "
                    "(id, topology_id, regimes, classification, "
                    " environment, auto_audit, created_at, updated_at) "
                    f"VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph})",
                    (
                        str(_uuid.uuid4()),
                        new_tid,
                        profile["regimes"],
                        profile["classification"],
                        profile["environment"],
                        profile["auto_audit"],
                        now,
                        now,
                    ),
                )

            # Copy circuits
            circuits = conn.execute(f"SELECT * FROM nc_circuits WHERE topology_id={_ph}", (old_tid,)).fetchall()
            for c in circuits:
                c = _row_to_dict(c)
                conn.execute(
                    "INSERT INTO nc_circuits "
                    "(id, topology_id, circuit_id, carrier, "
                    " circuit_type, bandwidth, handoff_a, handoff_z, "
                    " customer, site, monthly_cost_usd, "
                    " contract_start, contract_end, sla_uptime_pct, "
                    " install_status, notes, created_at, updated_at) "
                    f"VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph})",
                    (
                        str(_uuid.uuid4()),
                        new_tid,
                        c["circuit_id"],
                        c.get("carrier"),
                        c.get("circuit_type"),
                        c.get("bandwidth"),
                        c.get("handoff_a"),
                        c.get("handoff_z"),
                        c.get("customer"),
                        c.get("site"),
                        c.get("monthly_cost_usd", 0),
                        c.get("contract_start"),
                        c.get("contract_end"),
                        c.get("sla_uptime_pct", 99.9),
                        c.get("install_status", "planned"),
                        c.get("notes"),
                        now,
                        now,
                    ),
                )

            # Copy IPAM blocks
            blocks = conn.execute(f"SELECT * FROM nc_ipam_blocks WHERE topology_id={_ph}", (old_tid,)).fetchall()
            for b in blocks:
                b = _row_to_dict(b)
                conn.execute(
                    "INSERT INTO nc_ipam_blocks "
                    "(id, topology_id, network, vlan_id, vrf, "
                    " description, site_id, gateway, "
                    " utilization_pct, created_at) "
                    f"VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph})",
                    (
                        str(_uuid.uuid4()),
                        new_tid,
                        b["network"],
                        b.get("vlan_id"),
                        b.get("vrf", "global"),
                        b.get("description"),
                        b.get("site_id"),
                        b.get("gateway"),
                        b.get("utilization_pct", 0),
                        now,
                    ),
                )

        conn.commit()
        conn.close()
        _audit("CLONE", "project", new_pid, f"Cloned from {orig['name']} ({pid})")
        return jsonify(
            {
                "id": new_pid,
                "name": new_name or f"{orig['name']} (Copy)",
                "topologies_cloned": len(topo_map),
                "source_id": pid,
            }
        ), 201

    # ══════════════════════════════════════════════════════════════════════
    # API: Health
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/health")
    def nc_api_health():
        try:
            conn = get_connection()
            tc = conn.execute("SELECT COUNT(*) FROM topologies").fetchone()[0]
            tpl = conn.execute("SELECT COUNT(*) FROM nc_templates").fetchone()[0]
            conn.close()
            return jsonify({"status": "ok", "topologies": tc, "templates": tpl, "ts": _now()})
        except Exception as exc:
            return jsonify({"status": "error", "error": str(type(exc).__name__)}), 500

    # ══════════════════════════════════════════════════════════════════════
    # API: Topologies CRUD
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/topologies", methods=["GET"])
    @nc_login_required
    def nc_api_list_topologies():
        conn = get_connection()
        rows = conn.execute(
            "SELECT id, name, description, classification, created_at, updated_at "
            "FROM topologies ORDER BY updated_at DESC"
        ).fetchall()
        conn.close()
        return jsonify([_row_to_dict(r) for r in rows])

    @bp.route("/api/topologies", methods=["POST"])
    @nc_login_required
    def nc_api_create_topology():
        data = request.get_json(force=True, silent=True) or {}
        topo_id = str(_uuid.uuid4())
        name = data.get("name", "Untitled Topology")
        graph = json.dumps(data.get("graph_json", {"nodes": [], "edges": []}))
        now = _now()
        conn = get_connection()
        _ph = sql_placeholder(conn)
        conn.execute(
            "INSERT INTO topologies (id, name, description, graph_json, template_id, classification, created_at, updated_at) "
            f"VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph})",
            (
                topo_id,
                name,
                data.get("description", ""),
                graph,
                data.get("template_id"),
                data.get("classification", "public"),
                now,
                now,
            ),
        )
        conn.commit()
        conn.close()
        _audit("CREATE", "topology", topo_id, name)
        return jsonify({"id": topo_id, "name": name, "created_at": now}), 201

    # Clear-all routes MUST be before <topo_id> parameterized routes
    @bp.route("/api/topologies/clear-all", methods=["DELETE"])
    @nc_login_required
    def nc_api_clear_all_topologies():
        conn = get_connection()
        # Delete child tables first (FK constraints require this order).
        # Grandchild tables (e.g. nc_vuln_findings -> nc_vuln_scans -> topologies)
        # must come before their parents.
        child_tables = [
            "nc_vuln_findings",
            "nc_vuln_hosts",
            "ni_device_configs",
            "nc_device_geo",
            "nc_routing_entries",
            "nc_collected_configs",
            "nc_intent_validations",
            "nc_intent_policies",
            "nc_change_request_items",
            "nc_change_requests",
            "nc_stig_imports",
            "nc_ato_packages",
            "nc_boundaries",
            "nc_netbox_objects",
            "nc_netbox_sync_log",
            "nc_discovery_diffs",
            "nc_discovery_scans",
            "nc_mc_runs",
            "nc_mc_scenarios",
            "simulation_results",
            "nc_objects",
            "nc_circuits",
            "nc_cables",
            "nc_cross_connects",
            "nc_versions",
            "nc_compliance_findings",
            "nc_compliance_checks",
            "nc_compliance_profiles",
            "nc_ipam_blocks",
            "nc_project_topologies",
            "nc_groups",
            "nc_interconnects",
            "nc_port_inventory",
            "nc_module_inventory",
            "nc_bw_simulations",
            "nc_vuln_scans",
            "nc_query_log",
            "nc_collab_sessions",
            "ndc_runbooks",
            "ni_devices",
            "ni_analyses",
            "ni_state_snapshots",
            "nc_documents",
        ]
        for tbl in child_tables:
            conn.execute(f"DELETE FROM {tbl}")  # nosec B608 -- table/column names are internal constants, not user input
        conn.execute("DELETE FROM topologies")
        conn.commit()
        conn.close()
        _audit("CLEAR_ALL", "topologies", "", "All topologies cleared")
        return jsonify({"cleared": "topologies"})

    @bp.route("/api/simulations/clear-all", methods=["DELETE"])
    @nc_login_required
    def nc_api_clear_all_simulations():
        conn = get_connection()
        conn.execute("DELETE FROM simulation_results")
        conn.execute("DELETE FROM nc_mc_runs")
        conn.execute("DELETE FROM nc_mc_scenarios")
        conn.commit()
        conn.close()
        _audit("CLEAR_ALL", "simulations", "", "All simulations cleared")
        return jsonify({"cleared": "simulations"})

    @bp.route("/api/topologies/<topo_id>", methods=["GET"])
    @nc_login_required
    def nc_api_get_topology(topo_id):
        conn = get_connection()
        _ph = sql_placeholder(conn)
        row = conn.execute(f"SELECT * FROM topologies WHERE id={_ph}", (topo_id,)).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Not found"}), 404
        topo = _row_to_dict(row)
        try:
            topo["graph_json"] = json.loads(topo["graph_json"])
        except Exception:
            topo["graph_json"] = {"nodes": [], "edges": []}
        return jsonify(topo)

    @bp.route("/api/topologies/<topo_id>", methods=["PUT"])
    @nc_login_required
    def nc_api_update_topology(topo_id):
        data = request.get_json(force=True, silent=True) or {}
        conn = get_connection()
        _ph = sql_placeholder(conn)
        fields, values = [], []
        for k in ["name", "description", "graph_json", "classification"]:
            if k in data:
                val = json.dumps(data[k]) if k == "graph_json" and isinstance(data[k], dict) else data[k]
                fields.append(f"{k}={_ph}")
                values.append(val)
        if not fields:
            conn.close()
            return jsonify({"error": "No fields"}), 400
        fields.append(f"updated_at={_ph}")
        values.append(_now())
        values.append(topo_id)
        conn.execute(f"UPDATE topologies SET {', '.join(fields)} WHERE id={_ph}", values)  # nosec B608 -- table/column names are internal constants, not user input
        conn.commit()
        conn.close()
        invalidate_parsed_graph(topo_id)  # same-timestamp-save safety (ndc-perf-02)
        _audit("UPDATE", "topology", topo_id)
        # Hook: notify Security Design Canvas of topology change
        sdc_assessment = None
        try:
            from tools.security_canvas.agent import on_ndc_topology_saved

            result = on_ndc_topology_saved(topo_id)
            if result and result.get("status") == "assessed":
                sdc_assessment = {
                    "design_id": result.get("design_id"),
                    "risk_score": result.get("risk_score"),
                    "posture_grade": result.get("posture_grade"),
                    "cat1_count": result.get("cat1_count", 0),
                    "total_findings": result.get("total_findings", 0),
                }
        except Exception:
            pass  # Security Canvas is optional
        # Incremental KG update: re-extract only if graph_json changed
        try:
            from tools.canvas.kg_builder import rebuild_canvas_kg

            rebuild_canvas_kg("ndc", topo_id)
        except Exception:
            pass
        # Blockchain provenance
        try:
            from tools.canvas.provenance import register_canvas_provenance
            register_canvas_provenance(
                canvas_key="ndc",
                design_id=topo_id,
                graph_json=data.get("graph_json", {}),
                project_id=data.get("project_id", ""),
            )
        except Exception:
            pass
        # DIC Canvas Synergy — emit topology change event (dsyn-emit-01)
        try:
            from tools.ndc.event_emitter import emit_topology_change
            emit_topology_change(
                topology_id=topo_id,
                change_summary=f"Topology {topo_id} updated",
                affected_segments=list(data.get("graph_json", {}).get("nodes", []))[:5]
                    if isinstance(data.get("graph_json"), dict) else [],
            )
        except Exception:
            pass  # event emission never blocks topology save

        resp = {"ok": True}
        if sdc_assessment is not None:
            resp["sdc_assessment"] = sdc_assessment
        return jsonify(resp)

    @bp.route("/api/topologies/<topo_id>", methods=["DELETE"])
    @nc_login_required
    def nc_api_delete_topology(topo_id):
        conn = get_connection()
        _ph = sql_placeholder(conn)
        conn.execute(f"DELETE FROM topologies WHERE id={_ph}", (topo_id,))
        conn.commit()
        conn.close()
        _audit("DELETE", "topology", topo_id)
        return jsonify({"deleted": topo_id})

    # ══════════════════════════════════════════════════════════════════════
    # API: Executive Briefing Generator
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/topologies/<topo_id>/briefing", methods=["POST"])
    @nc_login_required
    def nc_api_executive_briefing(topo_id):
        """Generate a plain-English executive briefing from a topology graph for CISO/PM audience."""
        import re

        from tools.http.client import request as _req_request

        conn = get_connection()
        _ph = sql_placeholder(conn)
        row = conn.execute(f"SELECT * FROM topologies WHERE id={_ph}", (topo_id,)).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Topology not found"}), 404

        topo = _row_to_dict(row)
        try:
            graph = json.loads(topo.get("graph_json") or "{}")
        except Exception:
            graph = {}
        nodes = graph.get("nodes", [])
        edges = graph.get("edges", [])

        # ── Build structured graph summary for LLM context ─────────────────
        type_counts: dict = {}
        for n in nodes:
            t = n.get("type", "unknown")
            type_counts[t] = type_counts.get(t, 0) + 1

        security_zones = [n.get("label") or n.get("type") for n in nodes if n.get("type") in (
            "security-zone", "firewall", "vrf", "vlan", "nc_boundary",
        )]

        node_list = ", ".join(
            f"{n.get('label') or n.get('type')} ({n.get('type')})"
            for n in nodes[:40]
        )
        if len(nodes) > 40:
            node_list += f" … and {len(nodes) - 40} more"

        edge_list = ", ".join(
            f"{e.get('source')} → {e.get('target')}"
            + (f" [{e.get('protocol')}]" if e.get("protocol") else "")
            for e in edges[:30]
        )
        if len(edges) > 30:
            edge_list += f" … and {len(edges) - 30} more"

        type_summary = ", ".join(f"{v}× {k}" for k, v in sorted(type_counts.items(), key=lambda x: -x[1]))

        graph_summary = (
            f"Topology name: {topo.get('name', 'Unnamed')}\n"
            f"Classification: {topo.get('classification', 'unclassified')}\n"
            f"Total nodes: {len(nodes)}, Total links: {len(edges)}\n"
            f"Device types: {type_summary or 'none'}\n"
            f"Security zones/firewalls: {', '.join(security_zones) or 'none identified'}\n"
            f"Nodes: {node_list or 'empty'}\n"
            f"Links: {edge_list or 'none'}\n"
        )

        _BRIEFING_SYSTEM = (
            "You are a senior network architect writing a 1–2 page executive briefing for a CISO and Program Manager. "
            "Your audience is non-technical leadership. Use plain English — no jargon acronyms without explanation. "
            "Be concise, confident, and specific. "
            "Output ONLY a Markdown document with exactly these five sections (use ## headings):\n\n"
            "## Network Overview\n"
            "A 3–5 sentence plain-English description of what this network does, its scale, and its purpose.\n\n"
            "## Security Zones\n"
            "List the security boundaries, trust zones, or enclaves. Explain what data or users live in each zone.\n\n"
            "## Critical Path\n"
            "Describe the most important data flows or connectivity paths. Identify single points of failure if visible.\n\n"
            "## Technology Stack\n"
            "Summarize key device categories, vendors (if inferrable from node types), and any cloud services present.\n\n"
            "## Risk Highlights\n"
            "List 3–5 specific risks or concerns visible in this topology (missing redundancy, exposed endpoints, "
            "mixed classification zones, unencrypted paths, etc.). Each risk on its own bullet.\n\n"
            "Do not add any preamble or postscript outside these five sections."
        )

        user_msg = f"Generate an executive briefing for this network topology:\n\n{graph_summary}"

        def _call_claude_briefing():
            """Cloud/router branch (vs the explicit _call_ollama_briefing)."""
            return _route_llm(
                "network_qa",
                _BRIEFING_SYSTEM,
                [{"role": "user", "content": user_msg}],
                2048,
                temperature=0.4,
            )

        def _call_ollama_briefing():
            ollama_url = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
            ollama_model = os.environ.get("OLLAMA_TOPO_MODEL", "llama3.2:3b")
            r = _req_request(
                "POST",
                f"{ollama_url}/api/chat",
                json={
                    "model": ollama_model,
                    "messages": [
                        {"role": "system", "content": _BRIEFING_SYSTEM},
                        {"role": "user", "content": user_msg},
                    ],
                    "stream": False,
                    "options": {"num_predict": 2048, "temperature": 0.4},
                },
                timeout=120,
            )
            r.raise_for_status()
            return r.json().get("message", {}).get("content", ""), None

        try:
            provider = os.environ.get("NC_AI_PROVIDER", "auto")
            content = None

            if provider in ("auto", "claude"):
                content, err = _call_claude_briefing()
                if not content and provider == "claude":
                    return jsonify({"error": f"Claude API failed: {err}"}), 503

            if not content and provider in ("auto", "ollama"):
                content, err = _call_ollama_briefing()
                if not content and provider == "ollama":
                    return jsonify({"error": f"Ollama failed: {err}"}), 503

            if not content:
                return jsonify({"error": "No LLM provider available. Configure a provider in args/llm_config.yaml or start Ollama."}), 503

            # Strip any <think> blocks from reasoning models
            markdown = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
            _audit("BRIEFING", "topology", topo_id)
            return jsonify({"markdown": markdown, "topo_name": topo.get("name", "Unnamed")})

        except Exception as exc:
            logger.exception("Executive briefing failed for %s", topo_id)
            return jsonify({"error": str(exc)}), 500

    # ══════════════════════════════════════════════════════════════════════
    # API: Network Operations Runbook Generator
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/topologies/<topo_id>/runbook", methods=["POST"])
    @nc_login_required
    def nc_api_generate_runbook(topo_id):
        """Generate a downloadable Network Operations Runbook Markdown file from topology graph data.

        Deterministic sections built from graph data:
          1. Document header (name, classification, date)
          2. Device Inventory table
          3. IP Address Table sorted by subnet
          4. Interface/Link Matrix
          5. Standard Troubleshooting Procedures (LLM-generated per device type)
          6. Escalation Contacts fillable template
        """
        import ipaddress
        import re
        from datetime import datetime, timezone

        from tools.http.client import request as _req_request

        conn = get_connection()
        _ph = sql_placeholder(conn)
        row = conn.execute(f"SELECT * FROM topologies WHERE id={_ph}", (topo_id,)).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "Topology not found"}), 404

        try:
            ipam_rows = conn.execute(
                "SELECT network, description, vlan_id, vrf, gateway "
                f"FROM nc_ipam_blocks WHERE topology_id={_ph} ORDER BY network",
                (topo_id,),
            ).fetchall()
        except Exception:
            ipam_rows = []
        conn.close()

        topo = _row_to_dict(row)
        try:
            graph = json.loads(topo.get("graph_json") or "{}")
        except Exception:
            graph = {}
        nodes = graph.get("nodes", [])
        edges = graph.get("edges", [])

        topo_name = topo.get("name", "Unnamed Topology")
        classification = (topo.get("classification") or "UNCLASSIFIED").upper()
        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        _DRAWING_TYPES = {
            "draw-rect", "draw-rounded-rect", "text-heading", "text-label", "text-badge",
        }
        device_nodes = [n for n in nodes if n.get("type") not in _DRAWING_TYPES]

        lines: list = []

        # ── Section 1: Header ─────────────────────────────────────────────
        lines += [
            "# Network Operations Runbook",
            "",
            "| Field | Value |",
            "|-------|-------|",
            f"| **Topology** | {topo_name} |",
            f"| **Classification** | {classification} |",
            f"| **Generated** | {now_utc} |",
            f"| **Nodes** | {len(nodes)} |",
            f"| **Links** | {len(edges)} |",
            "",
            "---",
            "",
        ]

        # ── Section 2: Device Inventory ───────────────────────────────────
        lines.append("## 1. Device Inventory")
        lines.append("")
        if device_nodes:
            lines += [
                "| # | Label | Type | Management IP | OS/Platform | Serial | Notes |",
                "|---|-------|------|---------------|-------------|--------|-------|",
            ]
            for i, n in enumerate(device_nodes, 1):
                cfg = n.get("config") or {}
                label = (n.get("label") or n.get("type") or "Unknown").replace("|", "\\|")
                dtype = n.get("type", "unknown")
                ip = cfg.get("ip") or cfg.get("mgmt_ip") or "—"
                os_name = cfg.get("os") or "—"
                serial = (cfg.get("serial") or "—").replace("|", "\\|")
                notes = (cfg.get("role") or cfg.get("description") or "—").replace("|", "\\|")
                lines.append(f"| {i} | {label} | `{dtype}` | `{ip}` | {os_name} | {serial} | {notes} |")
        else:
            lines.append("*No devices in topology.*")
        lines += ["", "---", ""]

        # ── Section 3: IP Address Table (sorted by subnet) ────────────────
        lines.append("## 2. IP Address Table")
        lines.append("")

        ip_entries: list = []
        for n in device_nodes:
            cfg = n.get("config") or {}
            label = (n.get("label") or n.get("type") or "Unknown")
            dtype = n.get("type", "unknown")
            primary_ip = cfg.get("ip") or cfg.get("mgmt_ip") or ""
            if primary_ip:
                ip_entries.append({"ip": primary_ip, "label": label, "type": dtype, "context": "Management"})
            for vlan in (cfg.get("vlans") or []):
                if isinstance(vlan, dict) and vlan.get("ip"):
                    ip_entries.append({
                        "ip": vlan["ip"],
                        "label": f"{label} VLAN {vlan.get('vlan_id', '?')}",
                        "type": dtype,
                        "context": f"VLAN {vlan.get('vlan_id', '?')}",
                    })
            for extra_ip in (cfg.get("ips") or []):
                if extra_ip and extra_ip != primary_ip:
                    ip_entries.append({"ip": extra_ip, "label": label, "type": dtype, "context": "Interface"})

        for ipam_r in ipam_rows:
            ipam = _row_to_dict(ipam_r)
            network = ipam.get("network", "")
            if network:
                ip_entries.append({
                    "ip": network,
                    "label": f"IPAM: {ipam.get('description') or network}",
                    "type": "subnet",
                    "context": f"VRF {ipam.get('vrf') or 'global'} · GW {ipam.get('gateway') or '—'}",
                })

        def _ip_sort_key(entry):
            raw = entry["ip"].split("/")[0]
            try:
                return (0, int(ipaddress.ip_address(raw)))
            except Exception:
                return (1, 0)

        ip_entries.sort(key=_ip_sort_key)

        if ip_entries:
            lines += [
                "| # | IP / Subnet | Device / Label | Type | Context |",
                "|---|-------------|----------------|------|---------|",
            ]
            for i, e in enumerate(ip_entries, 1):
                lbl = e["label"].replace("|", "\\|")
                lines.append(
                    f"| {i} | `{e['ip']}` | {lbl} | `{e['type']}` | {e['context']} |"
                )
        else:
            lines.append("*No IP addresses configured in topology.*")
        lines += ["", "---", ""]

        # ── Section 4: Interface / Link Matrix ────────────────────────────
        lines.append("## 3. Interface / Link Matrix")
        lines.append("")

        label_by_id = {n.get("id"): (n.get("label") or n.get("type") or "?") for n in nodes}

        if edges:
            lines += [
                "| # | Source | Target | Protocol | Bandwidth | Latency | Label |",
                "|---|--------|--------|----------|-----------|---------|-------|",
            ]
            for i, e in enumerate(edges, 1):
                src = label_by_id.get(e.get("source"), e.get("source") or "?").replace("|", "\\|")
                tgt = label_by_id.get(e.get("target"), e.get("target") or "?").replace("|", "\\|")
                proto = e.get("protocol") or "—"
                bw = e.get("bandwidth") or "—"
                lat = f"{e.get('latency_ms')} ms" if e.get("latency_ms") else "—"
                lbl = (e.get("label") or "—").replace("|", "\\|")
                lines.append(f"| {i} | {src} | {tgt} | {proto} | {bw} | {lat} | {lbl} |")
        else:
            lines.append("*No links configured in topology.*")
        lines += ["", "---", ""]

        # ── Section 5: Troubleshooting Procedures (LLM) ───────────────────
        lines.append("## 4. Standard Troubleshooting Procedures")
        lines.append("")

        type_counts: dict = {}
        for n in device_nodes:
            t = n.get("type", "unknown")
            type_counts[t] = type_counts.get(t, 0) + 1

        device_type_summary = ", ".join(
            f"{v}× {k}" for k, v in sorted(type_counts.items(), key=lambda x: -x[1])
        )
        seen_types: set = set()
        device_samples: list = []
        for n in device_nodes:
            t = n.get("type", "unknown")
            if t not in seen_types:
                seen_types.add(t)
                cfg = n.get("config") or {}
                device_samples.append(
                    f"- {n.get('label') or t} (type={t}, ip={cfg.get('ip') or '—'}, os={cfg.get('os') or '—'})"
                )

        _RUNBOOK_SYSTEM = (
            "You are a senior network operations engineer writing a troubleshooting section for a "
            "Network Operations Runbook. For each device type present in the topology, write concise, "
            "actionable troubleshooting procedures. "
            "Format using Markdown with ### headings for each device type. "
            "Each device type section must include:\n"
            "- **Common failure symptoms** (2–3 bullet points)\n"
            "- **Initial diagnostic commands** (CLI commands in fenced code blocks)\n"
            "- **Step-by-step resolution checklist** (numbered steps)\n"
            "- **Escalation trigger** (one sentence: when to escalate to Tier 3 or vendor)\n\n"
            "Keep each section under 20 lines. Be specific and operational. "
            "Output ONLY the Markdown troubleshooting content — no preamble or postscript."
        )
        _runbook_user_msg = (
            f"Generate troubleshooting procedures for a network topology named '{topo_name}'.\n\n"
            f"Device types present: {device_type_summary or 'none'}\n"
            f"Representative devices:\n" + "\n".join(device_samples[:20]) + "\n\n"
            f"Total links: {len(edges)}\n"
            f"Classification: {classification}"
        )

        def _call_claude_runbook():
            """Cloud/router branch (vs the explicit _call_ollama_runbook)."""
            return _route_llm(
                "network_qa",
                _RUNBOOK_SYSTEM,
                [{"role": "user", "content": _runbook_user_msg}],
                3000,
                temperature=0.3,
            )

        def _call_ollama_runbook():
            ollama_url = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
            ollama_model = os.environ.get("OLLAMA_TOPO_MODEL", "llama3.2:3b")
            r = _req_request(
                "POST",
                f"{ollama_url}/api/chat",
                json={
                    "model": ollama_model,
                    "messages": [
                        {"role": "system", "content": _RUNBOOK_SYSTEM},
                        {"role": "user", "content": _runbook_user_msg},
                    ],
                    "stream": False,
                    "options": {"num_predict": 3000, "temperature": 0.3},
                },
                timeout=180,
            )
            r.raise_for_status()
            return r.json().get("message", {}).get("content", ""), None

        try:
            provider = os.environ.get("NC_AI_PROVIDER", "auto")
            ts_content = None
            if provider in ("auto", "claude"):
                ts_content, _ = _call_claude_runbook()
            if not ts_content and provider in ("auto", "ollama"):
                ts_content, _ = _call_ollama_runbook()
            if ts_content:
                ts_content = re.sub(r"<think>.*?</think>", "", ts_content, flags=re.DOTALL).strip()
                lines.append(ts_content)
            else:
                lines += [
                    "*No LLM provider available — configure a provider in `args/llm_config.yaml` or start Ollama for AI-generated procedures.*",
                    "",
                    "**Generic network troubleshooting checklist:**",
                    "",
                    "1. Verify physical connectivity (cable, SFP, interface admin/oper state)",
                    "2. Check interface state: `show interface status` / `show ip interface brief`",
                    "3. Review routing table: `show ip route` / `show bgp summary`",
                    "4. Check recent log events: `show log | last 50`",
                    "5. Ping gateway: `ping <gateway-ip> repeat 100`",
                    "6. Traceroute to isolate break: `traceroute <destination>`",
                    "7. Verify spanning-tree state (L2): `show spanning-tree`",
                    "8. Review ARP/MAC tables: `show arp` / `show mac address-table`",
                ]
        except Exception:
            logger.exception("Runbook troubleshooting LLM call failed for %s", topo_id)
            lines.append("*Troubleshooting generation failed. Check LLM provider configuration.*")
        lines += ["", "---", ""]

        # ── Section 6: Escalation Contacts (fillable template) ────────────
        lines += [
            "## 5. Escalation Contacts",
            "",
            "> **Instructions:** Fill in contact information before distributing this runbook.",
            "",
            "| Role | Name | Phone | Email | On-Call Hours |",
            "|------|------|-------|-------|---------------|",
            "| Network Operations (Tier 1) | ________________ | ________________ | ________________ | 24/7 |",
            "| Network Engineer (Tier 2) | ________________ | ________________ | ________________ | Business hours |",
            "| Network Architect (Tier 3) | ________________ | ________________ | ________________ | On-call |",
            "| Security / ISSO | ________________ | ________________ | ________________ | On-call |",
            "| Vendor TAC | ________________ | ________________ | ________________ | 24/7 |",
            "| ISP/Carrier NOC | ________________ | ________________ | ________________ | 24/7 |",
            "| Program Manager | ________________ | ________________ | ________________ | Business hours |",
            "",
            "### Escalation Thresholds",
            "",
            "| Severity | Description | Response Time | Escalation Path |",
            "|----------|-------------|---------------|-----------------|",
            "| **P1 — Critical** | Complete outage / mission impact | 15 min | Tier 1 → Tier 2 → Tier 3 → PM |",
            "| **P2 — High** | Partial outage, redundancy lost | 30 min | Tier 1 → Tier 2 |",
            "| **P3 — Medium** | Degraded performance, non-critical path | 2 hr | Tier 1 → Tier 2 (biz hrs) |",
            "| **P4 — Low** | Minor issue, workaround available | Next business day | Tier 1 ticket |",
            "",
            "---",
            "",
            f"*Generated by ICDEV™ NDC Runbook Generator — {now_utc}*",
        ]

        markdown = "\n".join(lines)
        safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", topo_name)[:40]
        filename = f"runbook-{safe_name}.md"

        _audit("RUNBOOK_GENERATED", "topology", topo_id)
        return jsonify({"markdown": markdown, "topo_name": topo_name, "filename": filename})

    # ══════════════════════════════════════════════════════════════════════
    # API: Simulation
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/topologies/<topo_id>/simulate", methods=["POST"])
    @nc_login_required
    def nc_api_simulate(topo_id):
        conn = get_connection()
        _ph = sql_placeholder(conn)
        row = conn.execute(f"SELECT graph_json FROM topologies WHERE id={_ph}", (topo_id,)).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Topology not found"}), 404
        data = request.get_json(force=True, silent=True) or {}
        sim_type = data.get("sim_type", "ping")
        try:
            graph = json.loads(row["graph_json"])
        except Exception:
            graph = {"nodes": [], "edges": []}

        result = _add_narrative(_run_simulation(graph, sim_type, data))
        _narrative = result.get("narrative") if isinstance(result, dict) else None
        if _narrative:
            record_canvas_decision(
                canvas_type="ndc",
                record_id=topo_id,
                decision_type="narrative",
                decision=str(_narrative)[:500],
                rationale=f"Simulation type: {sim_type}",
                model_used=None,
            )
        sim_id = str(_uuid.uuid4())
        now = _now()
        conn = get_connection()
        _ph = sql_placeholder(conn)
        conn.execute(
            "INSERT INTO simulation_results (id, topology_id, sim_type, input_json, result_json, ran_at) "
            f"VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph})",
            (sim_id, topo_id, sim_type, json.dumps(data), json.dumps(result), now),
        )
        conn.commit()
        conn.close()
        _audit("SIMULATE", "topology", topo_id, sim_type)
        return jsonify({"sim_id": sim_id, "result": result})

    # ══════════════════════════════════════════════════════════════════════
    # API: Templates
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/templates", methods=["GET"])
    @nc_login_required
    def nc_api_list_templates():
        conn = get_connection()
        rows = conn.execute(
            "SELECT id, name, category, description, tags FROM nc_templates ORDER BY category, name"
        ).fetchall()
        conn.close()
        result = []
        for r in rows:
            d = _row_to_dict(r)
            try:
                d["tags"] = json.loads(d.get("tags") or "[]")
            except Exception:
                d["tags"] = []
            result.append(d)
        return jsonify(result)

    @bp.route("/api/templates/<tpl_id>", methods=["GET"])
    @nc_login_required
    def nc_api_get_template(tpl_id):
        conn = get_connection()
        _ph = sql_placeholder(conn)
        row = conn.execute(f"SELECT * FROM nc_templates WHERE id={_ph}", (tpl_id,)).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Not found"}), 404
        d = _row_to_dict(row)
        try:
            d["graph_json"] = json.loads(d["graph_json"])
        except Exception:
            d["graph_json"] = {"nodes": [], "edges": []}
        try:
            d["tags"] = json.loads(d.get("tags") or "[]")
        except Exception:
            d["tags"] = []
        return jsonify(d)

    @bp.route("/api/templates/<tpl_id>/docs", methods=["GET"])
    @nc_login_required
    def nc_api_get_template_docs(tpl_id):
        """Get SOP/runbook markdown documentation for a template."""
        conn = get_connection()
        _ph = sql_placeholder(conn)
        rows = conn.execute(
            "SELECT id, template_id, doc_type, title, body_markdown, created_at "
            f"FROM nc_template_docs WHERE template_id = {_ph} ORDER BY created_at",
            (tpl_id,),
        ).fetchall()
        conn.close()
        docs = [_row_to_dict(r) for r in rows]
        return jsonify({"template_id": tpl_id, "docs": docs, "count": len(docs)})

    @bp.route("/api/templates/<tpl_id>/load", methods=["POST"])
    @nc_login_required
    def nc_api_load_template(tpl_id):
        conn = get_connection()
        _ph = sql_placeholder(conn)
        row = conn.execute(f"SELECT * FROM nc_templates WHERE id={_ph}", (tpl_id,)).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "Not found"}), 404
        tpl = _row_to_dict(row)
        # Idempotency: return existing topology for this template rather than duplicating
        existing = conn.execute(
            f"SELECT id, name FROM topologies WHERE template_id={_ph} LIMIT 1", (tpl_id,)
        ).fetchone()
        if existing:
            conn.close()
            ex = _row_to_dict(existing)
            return jsonify({"id": ex["id"], "name": ex["name"], "redirect": f"/network/canvas/{ex['id']}"}), 200
        topo_id = str(_uuid.uuid4())
        now = _now()
        name = f"{tpl['name']} (copy)"
        conn.execute(
            "INSERT INTO topologies (id, name, description, graph_json, template_id, classification, created_at, updated_at) "
            f"VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph})",
            (topo_id, name, tpl.get("description", ""), tpl["graph_json"], tpl_id, "public", now, now),
        )
        conn.commit()
        conn.close()
        _audit("LOAD_TEMPLATE", "topology", topo_id, tpl_id)
        return jsonify({"id": topo_id, "name": name, "redirect": f"/network/canvas/{topo_id}"}), 201

    @bp.route("/api/templates/<tpl_id>", methods=["PUT"])
    @nc_login_required
    def nc_api_update_template(tpl_id):
        conn = get_connection()
        _ph = sql_placeholder(conn)
        row = conn.execute(f"SELECT id FROM nc_templates WHERE id={_ph}", (tpl_id,)).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "Template not found"}), 404
        data = request.get_json(force=True)
        fields, values = [], []
        for k in ["name", "description", "category"]:
            if k in data:
                fields.append(f"{k}={_ph}")
                values.append(data[k])
        if "tags" in data:
            fields.append(f"tags={_ph}")
            values.append(json.dumps(data["tags"]) if isinstance(data["tags"], list) else data["tags"])
        if "graph_json" in data:
            fields.append(f"graph_json={_ph}")
            values.append(
                json.dumps(data["graph_json"]) if isinstance(data["graph_json"], dict) else data["graph_json"]
            )
        if not fields:
            conn.close()
            return jsonify({"error": "No fields to update"}), 400
        values.append(tpl_id)
        conn.execute(f"UPDATE nc_templates SET {', '.join(fields)} WHERE id={_ph}", values)  # nosec B608 -- table/column names are internal constants, not user input
        conn.commit()
        conn.close()
        _audit("UPDATE_TEMPLATE", "template", tpl_id, json.dumps(list(data.keys())))
        return jsonify({"ok": True, "id": tpl_id})

    @bp.route("/api/cloud-objects")
    @nc_login_required
    def nc_api_cloud_objects():
        return jsonify(CLOUD_OBJECTS)

    # ══════════════════════════════════════════════════════════════════════
    # API: Enclave-in-a-Box Snippets
    # Pre-built compliance-validated sub-topologies (SIPR, IL5 DMZ, Tactical Edge).
    # Inserted onto an existing canvas at a user-specified offset.
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/snippets", methods=["GET"])
    @nc_login_required
    def nc_api_list_snippets():
        """List all enclave snippets (metadata only, no graph_json)."""
        conn = get_connection()
        rows = conn.execute(
            "SELECT id, name, category, description, classification_level, "
            "impact_level, stig_controls, tags "
            "FROM nc_enclave_snippets ORDER BY category, name"
        ).fetchall()
        conn.close()
        result = []
        for r in rows:
            d = _row_to_dict(r)
            for field in ("stig_controls", "tags"):
                try:
                    d[field] = json.loads(d.get(field) or "[]")
                except Exception:
                    d[field] = []
            result.append(d)
        return jsonify(result)

    @bp.route("/api/snippets/<snippet_id>", methods=["GET"])
    @nc_login_required
    def nc_api_get_snippet(snippet_id):
        """Return full snippet including graph_json for insertion onto canvas."""
        conn = get_connection()
        _ph = sql_placeholder(conn)
        row = conn.execute(f"SELECT * FROM nc_enclave_snippets WHERE id={_ph}", (snippet_id,)).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Snippet not found"}), 404
        d = _row_to_dict(row)
        try:
            d["graph_json"] = json.loads(d["graph_json"])
        except Exception:
            d["graph_json"] = {"nodes": [], "edges": []}
        for field in ("stig_controls", "tags"):
            try:
                d[field] = json.loads(d.get(field) or "[]")
            except Exception:
                d[field] = []
        _audit("LOAD_SNIPPET", "snippet", snippet_id, d.get("name", ""))
        return jsonify(d)

    # ══════════════════════════════════════════════════════════════════════
    # API: Export / Import
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/export/<topo_id>", methods=["POST"])
    @nc_login_required
    def nc_api_export(topo_id):
        conn = get_connection()
        _ph = sql_placeholder(conn)
        row = conn.execute(f"SELECT * FROM topologies WHERE id={_ph}", (topo_id,)).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Not found"}), 404
        topo = _row_to_dict(row)
        try:
            graph = json.loads(topo["graph_json"])
        except Exception:
            graph = {"nodes": [], "edges": []}
        fmt = (request.get_json(force=True, silent=True) or {}).get("format", "drawio")
        if fmt == "drawio":
            xml = to_drawio(graph, topo["name"])
            return jsonify({"format": "drawio", "filename": f"{topo['name']}.drawio", "content": xml})
        if fmt == "svg":
            svg_content = to_svg(graph, topo["name"])
            return jsonify({"format": "svg", "filename": f"{topo['name']}.svg", "content": svg_content})
        return jsonify({"error": f"Unknown format: {fmt}"}), 400

    @bp.route("/api/export/<topo_id>/visio", methods=["POST"])
    @nc_login_required
    def nc_api_export_visio(topo_id):
        conn = get_connection()
        _ph = sql_placeholder(conn)
        row = conn.execute(f"SELECT * FROM topologies WHERE id={_ph}", (topo_id,)).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Not found"}), 404
        topo = _row_to_dict(row)
        try:
            graph = json.loads(topo["graph_json"])
        except Exception:
            graph = {"nodes": [], "edges": []}
        vdx_xml = to_vdx(graph, topo["name"])
        return jsonify({"format": "vdx", "filename": f"{topo['name']}.vdx", "content": vdx_xml})

    @bp.route("/api/export/<topo_id>/ansible", methods=["POST"])
    @nc_login_required
    def nc_api_export_ansible(topo_id):
        """Export topology as an Ansible inventory INI file.

        Hosts are grouped first by security enclave boundary (from
        nc_boundaries) and then by zone/role derived from node type.
        Cloud-infrastructure nodes (VPCs, subnets, etc.) are emitted as
        comments for reference only.
        """
        conn = get_connection()
        _ph = sql_placeholder(conn)
        row = conn.execute(f"SELECT * FROM topologies WHERE id={_ph}", (topo_id,)).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "Not found"}), 404
        topo = _row_to_dict(row)
        boundary_rows = conn.execute(
            f"SELECT label, classification, node_ids FROM nc_boundaries WHERE topology_id={_ph}",
            (topo_id,),
        ).fetchall()
        conn.close()
        try:
            graph = json.loads(topo["graph_json"])
        except Exception:
            graph = {"nodes": [], "edges": []}
        boundaries = [dict(r) for r in boundary_rows]
        content = to_ansible_inventory(graph, topo["name"], boundaries=boundaries)
        safe_name = topo["name"].replace(" ", "_")
        _audit("EXPORT", "topology", topo_id, "ansible")
        return jsonify(
            {
                "format": "ansible",
                "filename": f"{safe_name}_inventory.ini",
                "content": content,
                "enclave_count": len(boundaries),
            }
        )

    @bp.route("/api/export/<topo_id>/terraform", methods=["POST"])
    @nc_login_required
    def nc_api_export_terraform(topo_id):
        """Export topology as a Terraform HCL skeleton (main.tf).

        Generates provider blocks, resource stubs for every cloud node,
        security-group / NSG / firewall resources for each enclave boundary,
        and a locals block mapping diagram edges to conceptual connectivity
        rules (security-group / NACL / firewall policy inputs).
        """
        conn = get_connection()
        _ph = sql_placeholder(conn)
        row = conn.execute(f"SELECT * FROM topologies WHERE id={_ph}", (topo_id,)).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "Not found"}), 404
        topo = _row_to_dict(row)
        boundary_rows = conn.execute(
            f"SELECT label, classification, node_ids FROM nc_boundaries WHERE topology_id={_ph}",
            (topo_id,),
        ).fetchall()
        conn.close()
        try:
            graph = json.loads(topo["graph_json"])
        except Exception:
            graph = {"nodes": [], "edges": []}
        boundaries = [dict(r) for r in boundary_rows]
        content = to_terraform_hcl(graph, topo["name"], boundaries=boundaries)
        safe_name = topo["name"].replace(" ", "_")
        _audit("EXPORT", "topology", topo_id, "terraform")
        return jsonify(
            {
                "format": "terraform",
                "filename": f"{safe_name}_main.tf",
                "content": content,
                "enclave_count": len(boundaries),
            }
        )

    @bp.route("/api/export/<topo_id>/device-configs", methods=["POST"])
    @nc_login_required
    def nc_api_export_device_configs(topo_id):
        """Export per-device configuration files (IOS/EOS/JunOS) as a ZIP archive.

        Body (JSON, optional):
          format: "zip" (default) | "json"
            - "zip"  — base64-encoded ZIP bytes; client decodes and downloads.
            - "json" — dict mapping filename -> config text for in-browser preview.

        Each configurable node (router, switch, firewall) gets its own config file
        rendered from a Jinja2 template using the node's assigned IPs, VLANs,
        routing protocol settings, and connected edges.
        """
        import base64

        conn = get_connection()
        _ph = sql_placeholder(conn)
        row = conn.execute(f"SELECT * FROM topologies WHERE id={_ph}", (topo_id,)).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Not found"}), 404
        topo = _row_to_dict(row)
        try:
            graph = json.loads(topo["graph_json"])
        except Exception:
            graph = {"nodes": [], "edges": []}

        data = request.get_json(force=True, silent=True) or {}
        fmt = data.get("format", "zip")
        safe_name = topo["name"].replace(" ", "_")

        if fmt == "json":
            configs = generate_device_configs(graph, topo["name"])
            _audit("EXPORT", "topology", topo_id, f"device-configs json devices={len(configs)}")
            return jsonify(
                {
                    "format": "json",
                    "topo_name": topo["name"],
                    "device_count": len(configs),
                    "configs": configs,
                }
            )

        # Default: ZIP
        zip_bytes = generate_device_configs_zip(graph, topo["name"])
        encoded = base64.b64encode(zip_bytes).decode("ascii")
        device_count = len(list_configurable_nodes(graph))
        _audit("EXPORT", "topology", topo_id, f"device-configs zip devices={device_count}")
        return jsonify(
            {
                "format": "zip",
                "filename": f"{safe_name}_device_configs.zip",
                "content_b64": encoded,
                "device_count": device_count,
            }
        )

    @bp.route("/api/export/<topo_id>/device-configs/preview", methods=["GET"])
    @nc_login_required
    def nc_api_export_device_configs_preview(topo_id):
        """Return a list of configurable nodes and their OS type for UI preview."""
        conn = get_connection()
        _ph = sql_placeholder(conn)
        row = conn.execute(f"SELECT graph_json FROM topologies WHERE id={_ph}", (topo_id,)).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Not found"}), 404
        try:
            graph = json.loads(row[0] or '{"nodes":[],"edges":[]}')
        except Exception:
            graph = {"nodes": [], "edges": []}
        nodes = list_configurable_nodes(graph)
        return jsonify({"configurable_nodes": nodes, "count": len(nodes)})

    @bp.route("/api/export/<topo_id>/vsdx", methods=["POST"])
    @nc_login_required
    def nc_api_export_vsdx(topo_id):
        """Export topology as modern Visio .vsdx file with embedded metadata."""
        conn = get_connection()
        _ph = sql_placeholder(conn)
        row = conn.execute(f"SELECT * FROM topologies WHERE id={_ph}", (topo_id,)).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Not found"}), 404
        topo = _row_to_dict(row)
        try:
            graph = json.loads(topo["graph_json"])
        except Exception:
            graph = {"nodes": [], "edges": []}
        import base64
        import re as _re

        vsdx_bytes = export_vsdx(topo["name"], graph)
        encoded = base64.b64encode(vsdx_bytes).decode("ascii")
        safe_name = _re.sub(r"[^a-zA-Z0-9_-]", "_", topo["name"])
        _audit("EXPORT", "topology", topo_id, "vsdx")
        return jsonify(
            {
                "format": "vsdx",
                "filename": f"{safe_name}.vsdx",
                "content_b64": encoded,
            }
        )

    @bp.route("/api/export/pptx/<topo_id>", methods=["GET"])
    @nc_login_required
    def nc_api_export_pptx(topo_id):
        """Export topology as a PowerPoint deck via the tools/viz presentation layer.

        Slides: (1) title + classification, (2) native topology diagram,
        (3) native device-inventory table. 404 when the topology is missing,
        501 (clean JSON) when python-pptx is unavailable, 500 (clean JSON) on
        any other failure — never a stack trace.
        """
        import re as _re

        # Verify the topology exists and resolve a filename (mirrors sibling
        # export routes); 404 before doing any render work.
        conn = get_connection()
        _ph = sql_placeholder(conn)
        row = conn.execute(f"SELECT * FROM topologies WHERE id={_ph}", (topo_id,)).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Not found"}), 404
        topo = _row_to_dict(row)
        safe_name = _re.sub(r"[^a-zA-Z0-9_-]", "_", topo.get("name") or topo_id) or "topology"

        try:
            from tools.network.pptx_export import PptxDependencyError, export_topology_pptx
        except Exception as exc:  # pragma: no cover - defensive import guard
            return jsonify({"error": "PPTX export unavailable", "detail": str(exc)}), 501

        try:
            pptx_bytes = export_topology_pptx(topo_id)
        except PptxDependencyError as exc:
            return (
                jsonify({"error": "PPTX export requires python-pptx", "detail": str(exc)}),
                501,
            )
        except Exception as exc:
            logger.exception("PPTX export failed for topology %s", topo_id)
            return jsonify({"error": "PPTX export failed", "detail": str(exc)}), 500

        if pptx_bytes is None:
            return jsonify({"error": "Not found"}), 404

        from flask import Response

        _audit("EXPORT", "topology", topo_id, "pptx")
        return Response(
            pptx_bytes,
            mimetype="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            headers={"Content-Disposition": f"attachment; filename={safe_name}.pptx"},
        )

    @bp.route("/api/export/<topo_id>/csv", methods=["POST"])
    @nc_login_required
    def nc_api_export_csv(topo_id):
        """Export topology as Ops CSV bundle (device inventory, circuits, cables, IP, peering)."""
        conn = get_connection()
        _ph = sql_placeholder(conn)
        row = conn.execute(f"SELECT * FROM topologies WHERE id={_ph}", (topo_id,)).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Not found"}), 404
        topo = _row_to_dict(row)
        try:
            graph = json.loads(topo["graph_json"])
        except Exception:
            graph = {"nodes": [], "edges": []}
        csv_files = export_ops_csvs(topo["name"], graph)
        import base64
        import io as _io
        import re as _re

        buf = _io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for fname, content in csv_files.items():
                zf.writestr(fname, content)
        encoded = base64.b64encode(buf.getvalue()).decode("ascii")
        safe_name = _re.sub(r"[^a-zA-Z0-9_-]", "_", topo["name"])
        _audit("EXPORT", "topology", topo_id, "csv")
        return jsonify(
            {
                "format": "csv",
                "filename": f"{safe_name}_ops_csvs.zip",
                "content_b64": encoded,
            }
        )

    @bp.route("/api/export/<topo_id>/inventory", methods=["GET"])
    @nc_login_required
    def nc_api_export_inventory(topo_id):
        """Return JSON inventory of all devices with full metadata."""
        conn = get_connection()
        _ph = sql_placeholder(conn)
        row = conn.execute(f"SELECT * FROM topologies WHERE id={_ph}", (topo_id,)).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Not found"}), 404
        topo = _row_to_dict(row)
        try:
            graph = json.loads(topo["graph_json"])
        except Exception:
            graph = {"nodes": [], "edges": []}
        devices = []
        for n in graph.get("nodes", []):
            ntype = n.get("type", "")
            if ntype in ("draw-rect", "zone", "boundary", "text-annotation"):
                continue
            cfg = n.get("config", {})
            devices.append(
                {
                    "id": n.get("id", ""),
                    "label": n.get("label", ""),
                    "type": ntype,
                    "hostname": cfg.get("hostname", ""),
                    "ip": cfg.get("ip", ""),
                    "model": cfg.get("model", ""),
                    "serial": cfg.get("serial", ""),
                    "asset_tag": cfg.get("asset_tag", ""),
                    "site": cfg.get("site", ""),
                    "location": cfg.get("location", ""),
                    "rack": cfg.get("rack", ""),
                    "slot": cfg.get("slot", ""),
                    "port": cfg.get("port", ""),
                    "port_type": cfg.get("port_type", ""),
                    "bandwidth": cfg.get("bandwidth", ""),
                    "vlan": cfg.get("vlan", ""),
                    "vrf": cfg.get("vrf", ""),
                    "asn": cfg.get("asn", ""),
                    "peer_asn": cfg.get("peer_asn", ""),
                    "peer_ip": cfg.get("peer_ip", ""),
                    "peering_type": cfg.get("peering_type", ""),
                }
            )
        return jsonify({"topology": topo["name"], "device_count": len(devices), "devices": devices})

    @bp.route("/api/import", methods=["POST"])
    @nc_login_required
    def nc_api_import():
        data = request.get_json(force=True, silent=True) or {}
        fmt = data.get("format", "drawio")
        content = data.get("content", "")
        name = data.get("name", "Imported Topology")
        if not content:
            return jsonify({"error": "content required"}), 400
        if fmt == "drawio":
            graph = import_drawio(content)
        elif fmt in ("vdx", "visio"):
            graph = import_vdx(content)
        elif fmt == "svg":
            graph = import_svg(content)
        else:
            return jsonify({"error": f"Unsupported format: {fmt}"}), 400
        topo_id = str(_uuid.uuid4())
        now = _now()
        conn = get_connection()
        _ph = sql_placeholder(conn)
        conn.execute(
            "INSERT INTO topologies (id, name, description, graph_json, classification, created_at, updated_at) "
            f"VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph})",
            (topo_id, name, f"Imported from {fmt}", json.dumps(graph), "public", now, now),
        )
        conn.commit()
        conn.close()
        # Phase 1: auto-classify imported nodes
        graph = _classify_imported_nodes(graph)
        conn = get_connection()
        _ph = sql_placeholder(conn)
        conn.execute(f"UPDATE topologies SET graph_json={_ph} WHERE id={_ph}", (json.dumps(graph), topo_id))
        conn.commit()
        conn.close()
        _audit("IMPORT", "topology", topo_id, fmt)
        return jsonify(
            {"id": topo_id, "name": name, "nodes": len(graph.get("nodes", [])), "edges": len(graph.get("edges", []))}
        ), 201

    # ══════════════════════════════════════════════════════════════════════
    # Phase 1: Intelligent Import & Stitching
    # ══════════════════════════════════════════════════════════════════════

    # Semantic node classifier — maps labels to device types
