# CUI // SP-CTI
"""ICDEV Network Design Canvas -- analytics route group.

Extracted verbatim from tools/network/blueprint.py (cvx-net-01 monolith split).
Registered on the shared NDC blueprint via register_analytics_routes(bp).
"""
from __future__ import annotations

import json
import os
import uuid as _uuid
from datetime import datetime, timezone
from pathlib import Path
from flask import abort, jsonify, redirect, render_template, request, session
from tools.network.routes._common import _ICDEV_ROOT, logger
from tools.db.storage import sql_placeholder
from tools.network.ato_generator import export_pps_as_ssp_table, generate_ato_package, generate_pps_matrix_for_pair, get_topology_enclaves
from tools.network.blueprint_helpers import _audit, _now, _row_to_dict, nc_login_required
from tools.network.db.init_db import get_connection


def register_analytics_routes(bp):
    """Register analytics routes on the NDC blueprint."""

    @bp.route("/api/ato/<topo_id>/generate", methods=["POST"])
    @nc_login_required
    def nc_api_ato_generate(topo_id):
        """Generate a partial ATO package from a topology (or region)."""
        conn = get_connection()
        _ph = sql_placeholder(conn)
        topo = conn.execute(
            f"SELECT id, name, graph_json, classification FROM topologies WHERE id={_ph}",
            (topo_id,),
        ).fetchone()
        if not topo:
            conn.close()
            return jsonify({"error": "Topology not found"}), 404

        data = request.get_json(force=True, silent=True) or {}
        region_id = data.get("region_id")  # optional group ID
        system_name = data.get("system_name", topo["name"])
        classification = data.get("classification", topo["classification"] or "CUI")

        # Load regimes from compliance profile or request
        profile = conn.execute(
            f"SELECT regimes, classification, environment FROM nc_compliance_profiles WHERE topology_id={_ph}",
            (topo_id,),
        ).fetchone()
        if profile:
            try:
                regimes = json.loads(profile["regimes"] or "[]")
            except Exception:
                regimes = data.get("regimes", ["fisma_high", "stig"])
            classification = data.get("classification", profile["classification"] or classification)
        else:
            regimes = data.get("regimes", ["fisma_high", "stig"])

        try:
            graph = json.loads(topo["graph_json"])
        except Exception:
            conn.close()
            return jsonify({"error": "Bad graph JSON"}), 500

        # Load groups for region filtering
        groups = []
        if region_id:
            rows = conn.execute(f"SELECT * FROM nc_groups WHERE topology_id={_ph}", (topo_id,)).fetchall()
            groups = [_row_to_dict(r) for r in rows]

        # Check for as-built version
        as_built = conn.execute(
            f"SELECT id FROM nc_versions WHERE topology_id={_ph} AND label='As-Built' LIMIT 1",
            (topo_id,),
        ).fetchone()
        has_as_built = as_built is not None

        # Generate the ATO package
        try:
            package = generate_ato_package(
                topology_id=topo_id,
                graph=graph,
                system_name=system_name,
                classification=classification,
                regimes=regimes,
                groups=groups,
                region_id=region_id,
                has_as_built_version=has_as_built,
            )
        except Exception as exc:
            conn.close()
            return jsonify({"error": f"ATO generation failed: {exc}"}), 500

        # Persist to DB
        pkg_id = package["package_id"]
        now = _now()
        user_id = ""
        try:
            user_id = session.get("user_id", "")
        except RuntimeError:
            pass

        conn.execute(
            "INSERT INTO nc_ato_packages "
            "(id, topology_id, region_id, system_name, classification, regimes, "
            "package_json, summary_json, overall_readiness, stig_pass_rate, "
            "compliance_score, created_by, created_at) "
            f"VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph})",
            (
                pkg_id,
                topo_id,
                region_id,
                system_name,
                classification,
                json.dumps(regimes),
                json.dumps(package),
                json.dumps(package["summary"]),
                package["summary"]["overall_readiness"],
                package["summary"]["stig_pass_rate"],
                package["summary"]["compliance_score"],
                user_id,
                now,
            ),
        )
        conn.commit()
        conn.close()

        _audit(
            "ATO_GENERATE",
            "topology",
            topo_id,
            f"ATO package {pkg_id[:8]} | readiness={package['summary']['overall_readiness']} "
            f"| region={region_id or 'full'}",
        )

        return jsonify(package), 201

    @bp.route("/api/ato/<topo_id>/packages", methods=["GET"])
    @nc_login_required
    def nc_api_ato_list(topo_id):
        """List all generated ATO packages for a topology."""
        conn = get_connection()
        _ph = sql_placeholder(conn)
        rows = conn.execute(
            "SELECT id, topology_id, region_id, system_name, classification, "
            "regimes, summary_json, overall_readiness, stig_pass_rate, "
            "compliance_score, created_by, created_at "
            f"FROM nc_ato_packages WHERE topology_id={_ph} ORDER BY created_at DESC",
            (topo_id,),
        ).fetchall()
        conn.close()
        results = []
        for r in rows:
            d = _row_to_dict(r)
            try:
                d["summary"] = json.loads(d.pop("summary_json", "{}"))
            except Exception:
                d["summary"] = {}
            try:
                d["regimes"] = json.loads(d.get("regimes") or "[]")
            except Exception:
                pass
            results.append(d)
        return jsonify(results)

    @bp.route("/api/ato/<topo_id>/packages/<pkg_id>", methods=["GET"])
    @nc_login_required
    def nc_api_ato_detail(topo_id, pkg_id):
        """Get the full ATO package (all artifacts) by package ID."""
        conn = get_connection()
        _ph = sql_placeholder(conn)
        row = conn.execute(
            f"SELECT package_json FROM nc_ato_packages WHERE id={_ph} AND topology_id={_ph}",
            (pkg_id, topo_id),
        ).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Package not found"}), 404
        try:
            return jsonify(json.loads(row["package_json"]))
        except Exception:
            return jsonify({"error": "Corrupt package data"}), 500

    @bp.route("/api/ato/<topo_id>/packages/<pkg_id>", methods=["DELETE"])
    @nc_login_required
    def nc_api_ato_delete(topo_id, pkg_id):
        """Delete an ATO package."""
        conn = get_connection()
        _ph = sql_placeholder(conn)
        conn.execute(
            f"DELETE FROM nc_ato_packages WHERE id={_ph} AND topology_id={_ph}",
            (pkg_id, topo_id),
        )
        conn.commit()
        conn.close()
        _audit("ATO_DELETE", "ato_package", pkg_id, f"topology={topo_id}")
        return jsonify({"ok": True})

    # ══════════════════════════════════════════════════════════════════════
    # Heatmap Overlay — device/link metric data
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/heatmap/<topo_id>", methods=["GET"])
    @nc_login_required
    def nc_api_heatmap(topo_id):
        """Return per-node and per-link metric values for heatmap overlay.

        Query param ``metric`` selects which metric:
          - bandwidth   — link utilisation %  (from configData.utilization)
          - vuln        — vulnerability severity (from STIG imports / compliance)
          - stig        — STIG compliance %  (from compliance findings)
          - age         — equipment age in years (from configData.install_date)
        """
        metric = request.args.get("metric", "bandwidth")
        conn = get_connection()
        _ph = sql_placeholder(conn)
        topo = conn.execute(
            f"SELECT id, graph_json FROM topologies WHERE id={_ph}",
            (topo_id,),
        ).fetchone()
        if not topo:
            conn.close()
            return jsonify({"error": "Topology not found"}), 404

        try:
            graph = json.loads(topo["graph_json"])
        except Exception:
            graph = {"nodes": [], "edges": []}

        nodes = graph.get("nodes", [])
        edges = graph.get("edges", [])

        node_values = {}  # nodeId -> 0..1 normalised score
        link_values = {}  # edgeId -> 0..1 normalised score

        if metric == "bandwidth":
            for e in edges:
                cfg = e.get("configData") or {}
                util = cfg.get("utilization")
                if util is not None:
                    try:
                        link_values[e["id"]] = max(0.0, min(1.0, float(util) / 100.0))
                    except (ValueError, TypeError):
                        link_values[e["id"]] = 0.0
                else:
                    link_values[e["id"]] = 0.0

        elif metric == "vuln":
            # Pull latest compliance findings for this topology
            rows = conn.execute(
                "SELECT affected_entity, severity, status FROM nc_compliance_findings "
                f"WHERE topology_id={_ph} AND status='open' "
                "ORDER BY created_at DESC",
                (topo_id,),
            ).fetchall()
            severity_weight = {"CAT1": 1.0, "CAT2": 0.6, "CAT3": 0.25}
            entity_max = {}
            for r in rows:
                eid = r["affected_entity"]
                w = severity_weight.get(r["severity"], 0.1)
                if eid not in entity_max or w > entity_max[eid]:
                    entity_max[eid] = w
            for n in nodes:
                nid = n["id"]
                label = n.get("label", "")
                val = entity_max.get(nid, entity_max.get(label, 0.0))
                node_values[nid] = val
            for e in edges:
                eid = e["id"]
                label = e.get("label", "")
                val = entity_max.get(eid, entity_max.get(label, 0.0))
                link_values[eid] = val

        elif metric == "stig":
            # STIG import results — per-host pass rate
            stig_rows = conn.execute(
                f"SELECT result_json FROM nc_stig_imports WHERE topology_id={_ph} ORDER BY imported_at DESC LIMIT 1",
                (topo_id,),
            ).fetchone()
            host_compliance = {}
            if stig_rows and stig_rows["result_json"]:
                try:
                    stig_data = json.loads(stig_rows["result_json"])
                    hosts = stig_data.get("hosts", {})
                    for hostname, hdata in hosts.items():
                        s = hdata.get("summary", {})
                        total = s.get("pass", 0) + s.get("fail", 0) + s.get("nr", 0)
                        if total > 0:
                            host_compliance[hostname.lower()] = s.get("pass", 0) / total
                        else:
                            host_compliance[hostname.lower()] = 1.0
                except (json.JSONDecodeError, AttributeError):
                    pass
            for n in nodes:
                nid = n["id"]
                label = (n.get("label") or "").lower()
                # 1.0 = fully compliant (green), 0.0 = no compliance (red)
                # Invert so red=bad: heatmap value 1.0 = worst
                compliance = host_compliance.get(label, 1.0)
                node_values[nid] = 1.0 - compliance

        elif metric == "age":
            now = datetime.now(timezone.utc)
            for n in nodes:
                cfg = n.get("config") or n.get("configData") or {}
                score = 0.0

                # Check EOL/EOS/EoSup dates first (most impactful)
                for date_key, weight in [
                    ("eol_date", 1.0),
                    ("eosup_date", 0.9),
                    ("eos_date", 0.7),
                ]:
                    dval = cfg.get(date_key)
                    if dval:
                        try:
                            dt = datetime.fromisoformat(
                                dval + "T00:00:00+00:00" if "T" not in dval else dval.replace("Z", "+00:00")
                            )
                            if now >= dt:
                                score = max(score, weight)
                                pass  # past EOL/EOS/EoSup
                            else:
                                months_left = (dt - now).days / 30.44
                                if months_left < 6:
                                    score = max(score, weight * 0.8)
                                elif months_left < 12:
                                    score = max(score, weight * 0.5)
                                elif months_left < 24:
                                    score = max(score, weight * 0.2)
                        except (ValueError, TypeError):
                            pass

                # Fall back to install_date age
                if score == 0.0:
                    install_date = cfg.get("install_date") or cfg.get("installDate")
                    if install_date:
                        try:
                            dt = datetime.fromisoformat(
                                install_date + "T00:00:00+00:00"
                                if "T" not in install_date
                                else install_date.replace("Z", "+00:00")
                            )
                            age_years = (now - dt).days / 365.25
                            score = max(0.0, min(1.0, age_years / 10.0))
                            pass  # age computed
                        except (ValueError, TypeError):
                            pass

                node_values[n["id"]] = round(score, 3)

        conn.close()
        return jsonify(
            {
                "metric": metric,
                "node_values": node_values,
                "link_values": link_values,
            }
        )

    # ── Tech Debt Analysis ──────────────────────────────────────────────
    @bp.route("/api/tech-debt/<topo_id>", methods=["GET"])
    @nc_login_required
    def nc_api_tech_debt(topo_id):
        """Analyze lifecycle/tech debt across all devices in a topology."""
        conn = get_connection()
        _ph = sql_placeholder(conn)
        row = conn.execute(f"SELECT graph_json, name FROM topologies WHERE id={_ph}", (topo_id,)).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Not found"}), 404
        try:
            graph = json.loads(row["graph_json"])
        except Exception:
            graph = {"nodes": [], "edges": []}

        now = datetime.now(timezone.utc)
        devices = []
        past_eol = 0
        past_eos = 0
        past_eosup = 0
        approaching_eol = 0
        no_lifecycle = 0

        for n in graph.get("nodes", []):
            cfg = n.get("config") or n.get("configData") or {}
            ntype = n.get("type", "")
            # Skip drawing shapes and text
            if ntype in (
                "rect",
                "circle",
                "text",
                "heading",
                "badge",
                "hline",
                "vline",
                "arrow",
                "diamond",
                "ellipse",
                "triangle",
                "hexagon",
                "star",
                "roundedrect",
            ):
                continue

            device = {
                "id": n.get("id"),
                "label": n.get("label", ""),
                "type": ntype,
                "model": cfg.get("model", ""),
                "serial": cfg.get("serial", ""),
                "sw_version": cfg.get("sw_version", ""),
                "install_date": cfg.get("install_date", ""),
                "eos_date": cfg.get("eos_date", ""),
                "eol_date": cfg.get("eol_date", ""),
                "eosup_date": cfg.get("eosup_date", ""),
                "risk_level": "unknown",
                "issues": [],
            }

            has_lifecycle = False
            for dkey, label in [
                ("eol_date", "End of Life"),
                ("eosup_date", "End of Support"),
                ("eos_date", "End of Sale"),
            ]:
                dval = cfg.get(dkey)
                if dval:
                    has_lifecycle = True
                    try:
                        dt = datetime.fromisoformat(
                            dval + "T00:00:00+00:00" if "T" not in dval else dval.replace("Z", "+00:00")
                        )
                        if now >= dt:
                            device["issues"].append(f"PAST {label}: {dval}")
                            if dkey == "eol_date":
                                past_eol += 1
                            elif dkey == "eos_date":
                                past_eos += 1
                            elif dkey == "eosup_date":
                                past_eosup += 1
                        else:
                            months = (dt - now).days / 30.44
                            if months < 12:
                                device["issues"].append(f"{label} in {int(months)} months: {dval}")
                                if dkey == "eol_date":
                                    approaching_eol += 1
                    except (ValueError, TypeError):
                        pass

            install = cfg.get("install_date")
            if install:
                has_lifecycle = True
                try:
                    dt = datetime.fromisoformat(
                        install + "T00:00:00+00:00" if "T" not in install else install.replace("Z", "+00:00")
                    )
                    age = (now - dt).days / 365.25
                    if age > 7:
                        device["issues"].append(f"Equipment age: {age:.1f} years (>7yr)")
                except (ValueError, TypeError):
                    pass

            if not has_lifecycle:
                no_lifecycle += 1
                device["issues"].append("No lifecycle data configured")

            # Risk level
            if any("PAST End of Life" in i for i in device["issues"]):
                device["risk_level"] = "critical"
            elif any("PAST" in i for i in device["issues"]):
                device["risk_level"] = "high"
            elif any("months" in i for i in device["issues"]):
                device["risk_level"] = "medium"
            elif device["issues"]:
                device["risk_level"] = "low"
            else:
                device["risk_level"] = "healthy"

            devices.append(device)

        total = len(devices)
        critical = sum(1 for d in devices if d["risk_level"] == "critical")
        high = sum(1 for d in devices if d["risk_level"] == "high")

        return jsonify(
            {
                "topology": row["name"],
                "total_devices": total,
                "devices": devices,
                "summary": {
                    "past_eol": past_eol,
                    "past_eos": past_eos,
                    "past_eosup": past_eosup,
                    "approaching_eol_12mo": approaching_eol,
                    "no_lifecycle_data": no_lifecycle,
                    "critical_risk": critical,
                    "high_risk": high,
                    "tech_debt_score": round((critical * 4 + high * 2 + approaching_eol) / max(total, 1) * 25, 1),
                },
            }
        )

    # ── IPv6 Readiness Assessment ───────────────────────────────────────
    @bp.route("/api/ipv6-readiness/<topo_id>", methods=["GET"])
    @nc_login_required
    def nc_api_ipv6_readiness(topo_id):
        """Assess IPv6 readiness of a topology: device capability,
        addressing gaps, migration status."""
        conn = get_connection()
        _ph = sql_placeholder(conn)
        row = conn.execute(f"SELECT graph_json, name FROM topologies WHERE id={_ph}", (topo_id,)).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Not found"}), 404
        try:
            graph = json.loads(row["graph_json"])
        except Exception:
            graph = {"nodes": [], "edges": []}

        nodes = graph.get("nodes", [])
        devices = []
        capable_yes = capable_no = capable_partial = capable_unknown = 0
        has_v6_addr = 0
        has_v4_only = 0
        dual_stack = 0
        infra_types = {
            "router",
            "switch-l3",
            "switch-l2",
            "firewall",
            "mpls-pe",
            "mpls-p",
            "route-reflector",
            "load-balancer",
            "sdwan-edge",
            "wlc",
        }

        for n in nodes:
            ntype = n.get("type", "")
            if ntype not in infra_types:
                continue
            cfg = n.get("config") or n.get("configData") or {}
            cap = cfg.get("ipv6_capable", "")
            af = cfg.get("address_family", "")
            has_v6 = bool(cfg.get("ipv6"))
            has_v4 = bool(cfg.get("ip"))

            if cap == "yes":
                capable_yes += 1
            elif cap == "no":
                capable_no += 1
            elif cap == "partial":
                capable_partial += 1
            else:
                capable_unknown += 1

            if has_v6 and has_v4:
                dual_stack += 1
            elif has_v6:
                has_v6_addr += 1
            elif has_v4:
                has_v4_only += 1

            issues = []
            if cap == "no":
                issues.append("Device does NOT support IPv6 — requires hardware/software upgrade")
            elif cap == "partial":
                issues.append("Limited IPv6 support — verify feature set")
            elif cap == "":
                issues.append("IPv6 capability unknown — verify and update device properties")
            if has_v4 and not has_v6 and af != "ipv4":
                issues.append("IPv4 configured but no IPv6 address — add IPv6 for dual-stack")

            devices.append(
                {
                    "id": n.get("id"),
                    "label": n.get("label", ""),
                    "type": ntype,
                    "ipv6_capable": cap or "unknown",
                    "address_family": af
                    or ("dual-stack" if has_v6 and has_v4 else "ipv6" if has_v6 else "ipv4" if has_v4 else "none"),
                    "has_ipv4": has_v4,
                    "has_ipv6": has_v6,
                    "issues": issues,
                }
            )

        total = len(devices)
        ready_pct = round(capable_yes * 100 / max(total, 1))
        dual_pct = round(dual_stack * 100 / max(total, 1))

        # Migration recommendation
        if capable_no > 0:
            recommendation = (
                f"{capable_no} device(s) do not support IPv6 and "
                f"must be upgraded/replaced before migration. "
                f"Start with Phase 1: Assessment & Inventory."
            )
        elif capable_unknown > total / 2:
            recommendation = (
                f"{capable_unknown} device(s) have unknown IPv6 capability. "
                f"Audit all equipment and update the 'IPv6 Capable' "
                f"property before planning migration."
            )
        elif dual_stack < total:
            recommendation = (
                f"{total - dual_stack} device(s) not yet dual-stack. "
                f"Proceed with Phase 2: Enable dual-stack on core/distribution first."
            )
        else:
            recommendation = (
                "All infrastructure devices are dual-stack. Proceed to Phase 4: Access layer and endpoint rollout."
            )

        # Load IPv6 rules from design rules
        _dr = {}
        try:
            import yaml as _yaml_v6

            _v6_path = _ICDEV_ROOT / "args" / "network_design_rules.yaml"
            if _v6_path.exists():
                with open(_v6_path, encoding="utf-8") as _v6f:
                    _dr = _yaml_v6.safe_load(_v6f) or {}
        except Exception:
            pass
        ipv6_rules = _dr.get("ipv6_rules", {})

        return jsonify(
            {
                "topology": row["name"],
                "total_devices": total,
                "devices": devices,
                "summary": {
                    "capable_yes": capable_yes,
                    "capable_no": capable_no,
                    "capable_partial": capable_partial,
                    "capable_unknown": capable_unknown,
                    "readiness_pct": ready_pct,
                    "dual_stack_count": dual_stack,
                    "dual_stack_pct": dual_pct,
                    "ipv4_only_count": has_v4_only,
                    "ipv6_only_count": has_v6_addr,
                },
                "recommendation": recommendation,
                "migration_phases": ipv6_rules.get("migration_phases", {}),
                "transition_mechanisms": ipv6_rules.get("transition_mechanisms", {}),
                "security_checklist": ipv6_rules.get("security", []),
            }
        )

    # ══════════════════════════════════════════════════════════════════════
    # PPS Matrix Generator — Enclave / Device Pair
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/pps/<topo_id>")
    @nc_login_required
    def nc_pps_page(topo_id):
        """PPS Matrix Generator page — select two enclaves or device pair."""
        conn = get_connection()
        _ph = sql_placeholder(conn)
        topo = conn.execute(
            f"SELECT id, name, graph_json, classification FROM topologies WHERE id={_ph}",
            (topo_id,),
        ).fetchone()
        if not topo:
            conn.close()
            abort(404)

        try:
            graph = json.loads(topo["graph_json"])
        except Exception:
            graph = {"nodes": [], "edges": []}

        groups = [
            _row_to_dict(r) for r in conn.execute(f"SELECT * FROM nc_groups WHERE topology_id={_ph}", (topo_id,)).fetchall()
        ]
        conn.close()

        meta = get_topology_enclaves(graph, groups)
        classification_banner = (topo["classification"] or "").upper()

        return render_template(
            "network/pps_matrix.html",
            topology=_row_to_dict(topo),
            enclaves=meta["enclaves"],
            nodes=meta["nodes"],
            classification_banner=classification_banner,
        )

    @bp.route("/api/pps/<topo_id>/enclaves", methods=["GET"])
    @nc_login_required
    def nc_api_pps_enclaves(topo_id):
        """Return enclaves and nodes available for pair selection."""
        conn = get_connection()
        _ph = sql_placeholder(conn)
        topo = conn.execute(f"SELECT graph_json FROM topologies WHERE id={_ph}", (topo_id,)).fetchone()
        if not topo:
            conn.close()
            return jsonify({"error": "Topology not found"}), 404

        try:
            graph = json.loads(topo["graph_json"])
        except Exception:
            graph = {"nodes": [], "edges": []}

        groups = [
            _row_to_dict(r) for r in conn.execute(f"SELECT * FROM nc_groups WHERE topology_id={_ph}", (topo_id,)).fetchall()
        ]
        conn.close()

        return jsonify(get_topology_enclaves(graph, groups))

    @bp.route("/api/pps/<topo_id>/generate", methods=["POST"])
    @nc_login_required
    def nc_api_pps_generate(topo_id):
        """Generate PPS matrix for a selected enclave or device pair.

        Body JSON:
          {
            "source": "<zone_name | node_id>",
            "dest": "<zone_name | node_id>",
            "selector_type": "zone" | "node"   // default "zone"
          }
        """
        conn = get_connection()
        _ph = sql_placeholder(conn)
        topo = conn.execute(f"SELECT graph_json FROM topologies WHERE id={_ph}", (topo_id,)).fetchone()
        if not topo:
            conn.close()
            return jsonify({"error": "Topology not found"}), 404

        data = request.get_json(force=True, silent=True) or {}
        source = (data.get("source") or "").strip()
        dest = (data.get("dest") or "").strip()
        selector_type = data.get("selector_type", "zone")

        if not source or not dest:
            conn.close()
            return jsonify({"error": "source and dest are required"}), 400
        if source == dest:
            conn.close()
            return jsonify({"error": "source and dest must be different"}), 400
        if selector_type not in ("zone", "node"):
            selector_type = "zone"

        try:
            graph = json.loads(topo["graph_json"])
        except Exception:
            conn.close()
            return jsonify({"error": "Bad graph JSON"}), 500

        groups = [
            _row_to_dict(r) for r in conn.execute(f"SELECT * FROM nc_groups WHERE topology_id={_ph}", (topo_id,)).fetchall()
        ]
        conn.close()

        result = generate_pps_matrix_for_pair(
            graph=graph,
            source_selector=source,
            dest_selector=dest,
            selector_type=selector_type,
            groups=groups,
        )
        _audit(
            "PPS_GENERATE",
            "topology",
            topo_id,
            f"pair={source}<->{dest} type={selector_type} protocols={result['total_protocols']}",
        )
        return jsonify(result)

    @bp.route("/api/pps/<topo_id>/export", methods=["POST"])
    @nc_login_required
    def nc_api_pps_export(topo_id):
        """Export a PPS matrix as SSP table (CSV or Markdown).

        Body JSON: same as /api/pps/<topo_id>/generate plus:
          {
            "format": "csv" | "markdown"   // default "csv"
          }
        """
        conn = get_connection()
        _ph = sql_placeholder(conn)
        topo = conn.execute(f"SELECT graph_json FROM topologies WHERE id={_ph}", (topo_id,)).fetchone()
        if not topo:
            conn.close()
            return jsonify({"error": "Topology not found"}), 404

        data = request.get_json(force=True, silent=True) or {}
        source = (data.get("source") or "").strip()
        dest = (data.get("dest") or "").strip()
        selector_type = data.get("selector_type", "zone")
        fmt = data.get("format", "csv")

        if not source or not dest:
            conn.close()
            return jsonify({"error": "source and dest are required"}), 400
        if selector_type not in ("zone", "node"):
            selector_type = "zone"
        if fmt not in ("csv", "markdown"):
            fmt = "csv"

        try:
            graph = json.loads(topo["graph_json"])
        except Exception:
            conn.close()
            return jsonify({"error": "Bad graph JSON"}), 500

        groups = [
            _row_to_dict(r) for r in conn.execute(f"SELECT * FROM nc_groups WHERE topology_id={_ph}", (topo_id,)).fetchall()
        ]
        conn.close()

        result = generate_pps_matrix_for_pair(
            graph=graph,
            source_selector=source,
            dest_selector=dest,
            selector_type=selector_type,
            groups=groups,
        )
        content = export_pps_as_ssp_table(result, fmt=fmt)
        src_slug = source.replace(" ", "_").replace("/", "-")
        dst_slug = dest.replace(" ", "_").replace("/", "-")
        filename = f"pps_ssp_{src_slug}_to_{dst_slug}.{'csv' if fmt == 'csv' else 'md'}"

        from flask import Response

        mime = "text/csv" if fmt == "csv" else "text/markdown"
        return Response(
            content,
            mimetype=mime,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    # ── Missing page routes (nav links that need wiring) ────────────────

    @bp.route("/what-if")
    @nc_login_required
    def nc_whatif():
        return render_template("network/whatif.html")

    @bp.route("/replacements")
    @nc_login_required
    def nc_replacements():
        return render_template("network/replacement_map.html")

    @bp.route("/budget")
    @nc_login_required
    def nc_budget():
        return render_template("network/budget_forecast.html")

    @bp.route("/executive-dashboard")
    @nc_login_required
    def nc_executive_dashboard():
        return render_template("network/executive_dashboard.html")

    @bp.route("/api/executive-summary", methods=["GET"])
    @nc_login_required
    def nc_api_executive_summary():
        """Return portfolio-level executive summary data for the dashboard."""
        try:
            from tools.ndc.executive_summary_generator import generate_executive_summary
            result = generate_executive_summary()
            return jsonify(result)
        except Exception as exc:
            logger.warning("nc_api_executive_summary failed: %s", exc)
            return jsonify({"error": str(exc)}), 500

    @bp.route("/cloud-topology")
    @nc_login_required
    def nc_cloud_topology():
        return render_template("network/cloud_topology.html")

    @bp.route("/api/cloud-overlay/<topology_id>", methods=["GET"])
    @nc_login_required
    def nc_api_cloud_overlay(topology_id: str):
        """Return enriched topology JSON with cloud-provider overlay nodes."""
        try:
            from tools.ndc.cloud_topology_overlay import generate_cloud_overlay
            result = generate_cloud_overlay(topology_id)
            if result.get("error"):
                return jsonify(result), 404
            return jsonify(result)
        except Exception as exc:
            logger.warning("nc_api_cloud_overlay failed: %s", exc)
            return jsonify({"error": str(exc)}), 500

    def _showcase_conn():
        """Return a MAIN platform-DB connection for the showcase_demo_runs table.

        Uses the platform storage layer (``get_connection``, no db_path) so a
        PostgreSQL-primary deployment writes to the real database instead of a
        stray local SQLite file. ``showcase_demo_runs`` is an unscoped platform
        table with no ``tenant_id``/``classification`` columns, so the RLS
        predicate that ``get_connection`` auto-attaches inside a Flask request
        would raise ``UndefinedColumn`` on every SELECT — clear the security
        context to disable predicate injection for this table.
        """
        from tools.db.storage import get_connection as _get_platform_connection
        conn = _get_platform_connection()
        conn.set_security_context(None)  # rls-bypass: showcase_demo_runs is an unscoped platform table with no tenant_id/classification columns; RLS predicate would raise UndefinedColumn (ndc program)
        return conn

    def _ensure_showcase_table():
        """Create showcase_demo_runs if missing, via the platform storage layer.

        The DDL uses only portable TEXT/INTEGER column types so it runs on both
        SQLite and PostgreSQL through the storage layer's translate path.
        """
        conn = _showcase_conn()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS showcase_demo_runs (
                    run_id      TEXT PRIMARY KEY,
                    audience    TEXT NOT NULL DEFAULT 'exec',
                    scenarios_json TEXT NOT NULL DEFAULT '[]',
                    status      TEXT NOT NULL DEFAULT 'running',
                    result_json TEXT,
                    scenarios_passed INTEGER DEFAULT 0,
                    scenarios_total  INTEGER DEFAULT 0,
                    elapsed_ms  INTEGER DEFAULT 0,
                    created_at  TEXT NOT NULL
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

    @bp.route("/demo-runner")
    @nc_login_required
    def nc_demo_runner():
        """NDC Demo Runner control panel."""
        runs = []
        last_run = None
        last_result = {}
        try:
            _ensure_showcase_table()
            conn = _showcase_conn()
            try:
                rows = conn.execute(
                    "SELECT run_id, audience, scenarios_json, status, result_json, scenarios_passed, "
                    "scenarios_total, elapsed_ms, created_at "
                    "FROM showcase_demo_runs WHERE audience IN ('exec','tech','engineer') "
                    "ORDER BY created_at DESC LIMIT 15"
                ).fetchall()
            finally:
                conn.close()
            for row in rows:
                d = dict(row) if hasattr(row, "keys") else {
                    "run_id": row[0], "audience": row[1], "scenarios_json": row[2],
                    "status": row[3], "result_json": row[4], "scenarios_passed": row[5],
                    "scenarios_total": row[6], "elapsed_ms": row[7], "created_at": row[8],
                }
                try:
                    d["scenarios_list"] = json.loads(d.get("scenarios_json") or "[]")
                except (ValueError, TypeError):
                    d["scenarios_list"] = []
                runs.append(d)
            if runs:
                last_run = runs[0]
                if last_run.get("result_json"):
                    try:
                        last_result = json.loads(last_run["result_json"])
                    except (ValueError, TypeError):
                        pass
        except Exception as exc:
            logger.warning("nc_demo_runner history error: %s", exc)

        scenario_meta = {
            "A": {"title": "EOL Fire Drill",       "short": "EOL",  "color": "#e74c3c", "hook": "Risk → Replace → Runbook"},
            "B": {"title": "Multi-Cloud Expansion", "short": "Cloud", "color": "#3498db", "hook": "Hybrid connectivity overlay"},
            "C": {"title": "Compliance Audit",      "short": "Audit", "color": "#f39c12", "hook": "STIG → Remediation → cATO"},
        }
        return render_template(
            "network/demo_runner.html",
            runs=runs,
            last_run=last_run,
            last_result=last_result,
            scenario_meta=scenario_meta,
            iqe_canvas="network",
            iqe_api_route="/network/api/demo-iqe-query",
            iqe_title="NDC Demo Runner IQE",
            iqe_examples=[
                {"label": "Recent runs", "query": "show recent demo runs"},
                {"label": "Exec audience", "query": "show exec audience runs"},
                {"label": "Failed runs",   "query": "show failed runs"},
            ],
        )

    @bp.route("/api/demo-run", methods=["POST"])
    @nc_login_required
    def nc_api_demo_run():
        """Execute NDC demo scenarios and store run."""
        import time
        import uuid
        from datetime import datetime, timezone
        data = request.get_json(force=True, silent=True) or {}
        audience = (data.get("audience") or "exec").lower()
        raw = data.get("scenarios")
        if not raw or raw == "all":
            scenarios = None
        else:
            scenarios = [s.strip().upper() for s in raw if isinstance(s, str) and s.strip()]

        run_id = str(uuid.uuid4())
        t0 = time.monotonic()
        result: dict = {}
        status = "error"

        try:
            from tools.ndc.demo_runner import run_ndc_demo
            result = run_ndc_demo(scenarios=scenarios, audience=audience)
            elapsed_ms = result.get("elapsed_ms", int((time.monotonic() - t0) * 1000))
            passed = result.get("scenarios_passed", 0)
            total = result.get("scenarios_total", 0)
            status = result.get("status", "error")
            result_payload = result.get("results", {})
        except Exception as exc:
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            result_payload = {"error": str(exc)}
            passed = 0
            total = 0
            status = "error"
            logger.exception("nc_api_demo_run error: %s", exc)

        # Persist the run via the platform storage layer (main icdev DB).
        # showcase_demo_runs is an unscoped platform table; _showcase_conn()
        # disables RLS so the INSERT/SELECT do not fail on the missing
        # tenant_id/classification columns.
        persisted = False
        store_error = None
        try:
            _ensure_showcase_table()
            conn = _showcase_conn()
            try:
                conn.execute(
                    "INSERT INTO showcase_demo_runs "
                    "(run_id, audience, scenarios_json, status, result_json, "
                    "scenarios_passed, scenarios_total, elapsed_ms, created_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (run_id, audience, json.dumps(scenarios or "all"),
                     status, json.dumps(result_payload, default=str),
                     passed, total, elapsed_ms,
                     datetime.now(timezone.utc).isoformat()),
                )
                conn.commit()
                persisted = True
            finally:
                conn.close()
        except Exception as exc:
            store_error = str(exc)
            logger.warning("nc_api_demo_run store error: %s", exc)

        payload = {
            "ok": persisted,
            "run_id": run_id,
            "status": status,
            "results": result_payload,
            "scenarios_passed": passed,
            "scenarios_total": total,
            "elapsed_ms": elapsed_ms,
            "scenario_meta": {
                "A": {"title": "EOL Fire Drill",       "short": "EOL",  "color": "#e74c3c", "hook": "Risk → Replace → Runbook"},
                "B": {"title": "Multi-Cloud Expansion", "short": "Cloud", "color": "#3498db", "hook": "Hybrid connectivity overlay"},
                "C": {"title": "Compliance Audit",      "short": "Audit", "color": "#f39c12", "hook": "STIG → Remediation → cATO"},
            },
        }
        if not persisted:
            # Surface the persistence failure instead of pretending success —
            # a silent warning here previously masked total data loss.
            payload["error"] = store_error or "failed to persist demo run"
            return jsonify(payload), 500
        return jsonify(payload)

    @bp.route("/api/demo-runs")
    @nc_login_required
    def nc_api_demo_runs():
        """Return NDC demo run history."""
        limit = min(int(request.args.get("limit", 20)), 100)
        try:
            _ensure_showcase_table()
            conn = _showcase_conn()
            try:
                rows = conn.execute(
                    "SELECT run_id, audience, scenarios_json, status, result_json, scenarios_passed, "
                    "scenarios_total, elapsed_ms, created_at "
                    "FROM showcase_demo_runs WHERE audience IN ('exec','tech','engineer') "
                    "ORDER BY created_at DESC LIMIT %s",
                    (limit,),
                ).fetchall()
            finally:
                conn.close()
            result = []
            for row in rows:
                d = dict(row) if hasattr(row, "keys") else {
                    "run_id": row[0], "audience": row[1], "scenarios_json": row[2],
                    "status": row[3], "result_json": row[4], "scenarios_passed": row[5],
                    "scenarios_total": row[6], "elapsed_ms": row[7], "created_at": row[8],
                }
                try:
                    d["scenarios_list"] = json.loads(d.get("scenarios_json") or "[]")
                except (ValueError, TypeError):
                    d["scenarios_list"] = []
                result.append(d)
            return jsonify(result)
        except Exception as exc:
            # Surface the read failure explicitly rather than returning an
            # empty history that is indistinguishable from "no runs yet".
            logger.warning("nc_api_demo_runs error: %s", exc)
            return jsonify({"ok": False, "error": str(exc)}), 500

    @bp.route("/design-patterns")
    @nc_login_required
    def nc_design_patterns():
        return render_template("network/design_patterns.html")

    @bp.route("/design-rules")
    @nc_login_required
    def nc_design_rules():
        return render_template("network/design_rules.html")

    @bp.route("/api/design-rules", methods=["GET"])
    @nc_login_required
    def nc_api_design_rules():
        """Return args/network_design_rules.yaml as JSON for the design-rules page.

        The page template at templates/network/design_rules.html expects a dict
        with on_node_add, best_practices, and ipv6_rules top-level keys. The
        YAML already contains those exact keys plus on_edge_add. Deterministic,
        air-gap-safe, no LLM.
        """
        try:
            import yaml as _yaml_dr
            _dr_path = _ICDEV_ROOT / "args" / "network_design_rules.yaml"
            if not _dr_path.exists():
                return jsonify({
                    "on_node_add": {},
                    "best_practices": {},
                    "ipv6_rules": {},
                    "on_edge_add": {},
                    "error": "network_design_rules.yaml not found",
                }), 200
            with open(_dr_path, encoding="utf-8") as _drf:
                data = _yaml_dr.safe_load(_drf) or {}
            return jsonify({
                "on_node_add": data.get("on_node_add", {}),
                "best_practices": data.get("best_practices", {}),
                "ipv6_rules": data.get("ipv6_rules", {}),
                "on_edge_add": data.get("on_edge_add", {}),
            })
        except Exception as exc:
            logger.warning("nc_api_design_rules failed: %s", exc)
            return jsonify({
                "on_node_add": {},
                "best_practices": {},
                "ipv6_rules": {},
                "on_edge_add": {},
                "error": str(exc),
            }), 500

    @bp.route("/device-profiles")
    @nc_login_required
    def nc_device_profiles_page():
        return render_template("network/device_profiles.html")

    # ── Hardware Profiles (datasheet-level specs) ────────────────────────
    @bp.route("/hardware-profiles")
    @nc_login_required
    def nc_hardware_profiles_page():
        return render_template("network/hardware_profiles.html")

    @bp.route("/api/hardware-profiles", methods=["GET"])
    @nc_login_required
    def nc_api_list_hardware_profiles():
        conn = get_connection()
        rows = conn.execute(
            "SELECT id, vendor, model, model_family, device_type, form_factor, "
            "rack_units, throughput_gbps, power_max_w, replacement_cost, "
            "eol_date, eos_date, ports_json, tags, is_builtin "
            "FROM nc_hardware_profiles ORDER BY vendor, model"
        ).fetchall()
        conn.close()
        result = []
        for r in rows:
            d = dict(r)
            try:
                d["ports"] = json.loads(d.pop("ports_json", "[]"))
            except Exception:
                d["ports"] = []
            try:
                d["tags"] = json.loads(d.pop("tags", "[]"))
            except Exception:
                d["tags"] = []
            result.append(d)
        return jsonify(result)

    @bp.route("/api/hardware-profiles/<pid>", methods=["GET"])
    @nc_login_required
    def nc_api_get_hardware_profile(pid):
        conn = get_connection()
        _ph = sql_placeholder(conn)
        row = conn.execute(f"SELECT * FROM nc_hardware_profiles WHERE id={_ph}", (pid,)).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Not found"}), 404
        d = dict(row)
        for jcol in ("ports_json", "components_json", "mgmt_ports_json", "os_options", "tags"):
            try:
                d[jcol.replace("_json", "")] = json.loads(d.pop(jcol, "[]"))
            except Exception:
                d[jcol.replace("_json", "")] = []
        return jsonify(d)

    @bp.route("/api/hardware-profiles", methods=["POST"])
    @nc_login_required
    def nc_api_create_hardware_profile():
        data = request.get_json(force=True, silent=True) or {}
        pid = "hw-" + str(_uuid.uuid4())[:8]
        conn = get_connection()
        _ph = sql_placeholder(conn)
        conn.execute(
            "INSERT INTO nc_hardware_profiles (id, vendor, model, model_family, device_type, "
            "form_factor, rack_units, weight_kg, power_typical_w, power_max_w, throughput_gbps, "
            "ports_json, replacement_cost, eol_date, eos_date, tags, is_builtin, created_by) "
            f"VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},0,{_ph})",
            (pid, data.get("vendor", ""), data.get("model", ""), data.get("model_family", ""),
             data.get("device_type", "router"), data.get("form_factor", "rack"),
             data.get("rack_units", 1), data.get("weight_kg"),
             data.get("power_typical_w"), data.get("power_max_w"), data.get("throughput_gbps"),
             json.dumps(data.get("ports", [])), data.get("replacement_cost"),
             data.get("eol_date"), data.get("eos_date"),
             json.dumps(data.get("tags", [])), data.get("created_by", "")),
        )
        conn.commit()
        conn.close()
        return jsonify({"id": pid}), 201

    @bp.route("/api/hardware-profiles/<pid>", methods=["DELETE"])
    @nc_login_required
    def nc_api_delete_hardware_profile(pid):
        conn = get_connection()
        _ph = sql_placeholder(conn)
        row = conn.execute(f"SELECT is_builtin FROM nc_hardware_profiles WHERE id={_ph}", (pid,)).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "Not found"}), 404
        if row["is_builtin"]:
            conn.close()
            return jsonify({"error": "Cannot delete built-in profile"}), 403
        conn.execute(f"DELETE FROM nc_hardware_profiles WHERE id={_ph}", (pid,))
        conn.commit()
        conn.close()
        return jsonify({"deleted": pid})

    # ── Naming Conventions ─────────────────────────────────────────────
    @bp.route("/api/naming-conventions", methods=["GET"])
    @nc_login_required
    def nc_api_list_naming_conventions():
        from tools.network.naming_engine import list_conventions
        return jsonify(list_conventions())

    @bp.route("/api/naming-conventions/<cid>", methods=["GET"])
    @nc_login_required
    def nc_api_get_naming_convention(cid):
        from tools.network.naming_engine import _load_convention
        conv = _load_convention(cid)
        if not conv:
            return jsonify({"error": "Not found"}), 404
        return jsonify(conv)

    @bp.route("/api/naming-conventions/<cid>/generate", methods=["POST"])
    @nc_login_required
    def nc_api_generate_name(cid):
        from tools.network.naming_engine import generate_name, bulk_generate
        data = request.get_json(force=True, silent=True) or {}
        count = data.pop("count", 1)
        topology_id = data.pop("topology_id", "")
        if count > 1:
            result = bulk_generate(cid, count, data, topology_id)
        else:
            result = generate_name(cid, data, topology_id)
        return jsonify(result)

    @bp.route("/api/naming-conventions/<cid>/validate", methods=["POST"])
    @nc_login_required
    def nc_api_validate_name(cid):
        from tools.network.naming_engine import validate_name
        data = request.get_json(force=True, silent=True) or {}
        name = data.get("name", "")
        return jsonify(validate_name(name, cid))

    @bp.route("/api/naming-conventions", methods=["POST"])
    @nc_login_required
    def nc_api_create_naming_convention():
        data = request.get_json(force=True, silent=True) or {}
        cid = "nc-" + str(_uuid.uuid4())[:8]
        conn = get_connection()
        _ph = sql_placeholder(conn)
        conn.execute(
            "INSERT INTO nc_naming_conventions (id, name, description, pattern, "
            "fields_json, separator, max_length, case_rule, example, is_builtin) "
            f"VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},0)",
            (cid, data.get("name", ""), data.get("description", ""),
             data.get("pattern", ""), data.get("fields_json", "[]"),
             data.get("separator", ""), data.get("max_length", 63),
             data.get("case_rule", "upper"), data.get("example", "")),
        )
        conn.commit()
        conn.close()
        return jsonify({"id": cid}), 201

    @bp.route("/api/naming-conventions/<cid>", methods=["PUT"])
    @nc_login_required
    def nc_api_update_naming_convention(cid):
        data = request.get_json(force=True, silent=True) or {}
        conn = get_connection()
        _ph = sql_placeholder(conn)
        row = conn.execute(f"SELECT id FROM nc_naming_conventions WHERE id={_ph}", (cid,)).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "Not found"}), 404
        updates = []
        params = []
        for col in ("name", "description", "pattern", "fields_json", "separator", "max_length", "case_rule", "example"):
            if col in data:
                updates.append(f"{col} = {_ph}")
                params.append(data[col])
        if updates:
            params.append(cid)
            conn.execute(f"UPDATE nc_naming_conventions SET {', '.join(updates)} WHERE id = {_ph}", params)  # nosec B608 — columns from hardcoded whitelist
            conn.commit()
        conn.close()
        return jsonify({"updated": cid})

    @bp.route("/api/naming-conventions/<cid>", methods=["DELETE"])
    @nc_login_required
    def nc_api_delete_naming_convention(cid):
        conn = get_connection()
        _ph = sql_placeholder(conn)
        row = conn.execute(f"SELECT is_builtin FROM nc_naming_conventions WHERE id={_ph}", (cid,)).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "Not found"}), 404
        if row["is_builtin"]:
            conn.close()
            return jsonify({"error": "Cannot delete built-in convention"}), 403
        conn.execute(f"DELETE FROM nc_naming_conventions WHERE id={_ph}", (cid,))
        conn.execute(f"DELETE FROM nc_naming_sequences WHERE convention_id={_ph}", (cid,))
        conn.commit()
        conn.close()
        return jsonify({"deleted": cid})

    # ── Import Wizard: combined ingest + enrich + validate ──────────
    @bp.route("/api/import-wizard", methods=["POST"])
    @nc_login_required
    def nc_api_import_wizard():
        """One-shot import: ingest diagram + enrich + validate."""
        from tools.network.network_ingester import ingest_diagram
        import tempfile as _tmpmod

        if "file" not in request.files:
            return jsonify({"error": "No file provided"}), 400

        f = request.files["file"]
        project_id = request.form.get("project_id", "ndc-network-intelligence")
        name = request.form.get("name", f.filename or "uploaded")
        do_enrich = request.form.get("enrich", "true").lower() in ("true", "1", "yes")
        do_template = request.form.get("save_template", "false").lower() in ("true", "1", "yes")

        # Save to temp
        tmp_path = Path(os.path.join(_tmpmod.gettempdir(), f"ndc_import_{_uuid.uuid4().hex[:8]}_{f.filename}"))
        f.save(str(tmp_path))

        try:
            # Step 1: Ingest
            ingest_result = ingest_diagram(str(tmp_path), project_id, name)
            if ingest_result.get("error"):
                return jsonify({"step": "ingest", "error": ingest_result["error"]}), 400

            topo_id = ingest_result["topology_id"]
            response = {
                "topology_id": topo_id,
                "topology_name": name,
                "node_count": ingest_result.get("node_count", 0),
                "edge_count": ingest_result.get("edge_count", 0),
                "device_count": ingest_result.get("device_count", 0),
            }

            # Step 2: Enrich
            if do_enrich:
                try:
                    from tools.network.topology_enricher import enrich_topology
                    enrich_result = enrich_topology(topo_id, add_infra=False, add_groups=True)
                    response["enrichment"] = {
                        "groups_added": enrich_result.get("groups_added", 0),
                        "facilities_created": enrich_result.get("facilities_created", 0),
                        "racks_created": enrich_result.get("racks_created", 0),
                        "validation": enrich_result.get("validation", {}),
                    }
                except Exception as e:
                    response["enrichment"] = {"error": str(e)}

            # Step 3: Validate
            try:
                from tools.network.topology_validator import validate_topology
                val_result = validate_topology(topo_id, fix=True)
                response["validation"] = {
                    "issues_found": val_result.get("issues_found", 0),
                    "fixes_applied": val_result.get("fixes_applied", 0),
                    "passed": val_result.get("passed", True),
                }
            except Exception as e:
                response["validation"] = {"error": str(e)}

            # Step 4: Save template
            if do_template:
                try:
                    from tools.network.topology_enricher import save_as_template
                    tpl = save_as_template(topo_id, name + " (Template)")
                    response["template"] = tpl
                except Exception:
                    pass

            return jsonify(response)
        finally:
            tmp_path.unlink(missing_ok=True)

    # ── Topology Enrichment API ─────────────────────────────────────
    @bp.route("/api/topology/<tid>/enrich", methods=["POST"])
    @nc_login_required
    def nc_api_enrich_topology(tid):
        try:
            from tools.network.topology_enricher import enrich_topology
            data = request.get_json(force=True, silent=True) or {}
            result = enrich_topology(
                tid,
                add_infra=data.get("add_infra", True),
                add_groups=data.get("add_groups", True),
            )
            return jsonify(result)
        except ImportError:
            return jsonify({"error": "topology_enricher not available"}), 503
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @bp.route("/api/topology/<tid>/validate", methods=["POST"])
    @nc_login_required
    def nc_api_validate_topology(tid):
        try:
            from tools.network.topology_validator import validate_topology
            data = request.get_json(force=True, silent=True) or {}
            result = validate_topology(tid, fix=data.get("fix", True))
            return jsonify(result)
        except ImportError:
            return jsonify({"error": "topology_validator not available"}), 503
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # Renamed from nc_api_save_as_template to nc_api_save_template_enricher to
    # disambiguate from the older nc_api_save_as_template at line 7644 (which
    # handles /api/topologies/<topo_id>/save-as-template with direct SQL). This
    # newer wrapper delegates to tools.network.topology_enricher and has its own
    # URL path (/api/topology/<tid>/save-template). The Python name collision
    # was aborting the network blueprint registration mid-flight, hiding all
    # subsequent routes (/discovery, /intelligence, /runbooks, /ingestion).
    @bp.route("/api/topology/<tid>/save-template", methods=["POST"])
    @nc_login_required
    def nc_api_save_template_enricher(tid):
        try:
            from tools.network.topology_enricher import save_as_template
            data = request.get_json(force=True, silent=True) or {}
            name = data.get("name", f"Template from {tid}")
            result = save_as_template(tid, name, data.get("description", ""))
            return jsonify(result)
        except ImportError:
            return jsonify({"error": "topology_enricher not available"}), 503
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @bp.route("/discovery")
    @nc_login_required
    def nc_discovery_page():
        return render_template("network/discovery.html") if os.path.exists(
            os.path.join(os.path.dirname(__file__), "..", "dashboard", "templates", "network", "discovery.html")
        ) else ("Discovery page coming soon", 200)

    @bp.route("/logout")
    def nc_logout():
        return redirect("/network/")

    # ── Analysis routes (extracted to routes/analysis.py) ────────────────


    # ── Network Intelligence page + API routes (NII) ───────────────────
    @bp.route("/intelligence")
    @nc_login_required
    def nc_intelligence():
        """Network Infrastructure Intelligence dashboard page."""
        return render_template("network/intelligence.html")



    # ── Enterprise Summary (missing route fix) ───────────────────────────────
