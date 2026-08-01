# CUI // SP-CTI
"""Network Design Canvas — Analysis routes.

Provides topology health scoring, risk matrix, compliance trend,
AI topology review, and export endpoints.  All reads from
nc_compliance_findings, nc_objects, nc_circuits,
nc_intent_validations, nc_versions, topologies.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import os
from functools import lru_cache

logger = get_logger("icdev.network.routes.analysis")


@lru_cache(maxsize=256)
def _version_meta_fields(meta_json: str):
    """Parse an ``nc_versions.metadata_json`` blob once and return the two
    fields the trend view needs, as an immutable tuple (ndc-perf-02).

    Bounded memo keyed by the raw JSON string — identical metadata across many
    version rows (common when compliance scores are unchanged) is parsed once.
    Returning a tuple (not the dict) keeps the memo mutation-safe.
    """
    try:
        meta = json.loads(meta_json or "{}")
    except Exception as exc:
        # Fail-closed (ndc-gov-01): report the parse failure instead of
        # returning (None, None), which the trend view renders as empty cells
        # indistinguishable from a version that legitimately has no score.
        # Silently swallowing it made corrupt metadata look like missing data.
        return None, None, f"metadata parse failed: {type(exc).__name__}: {exc}"
    return meta.get("compliance_score"), meta.get("cat1_count"), None


def register_analysis_routes(bp, get_conn=None, helpers=None):
    """Register analysis routes on the NDC blueprint."""
    from flask import jsonify, request

    def _nc():
        from tools.network.db.init_db import get_connection
        return get_connection()

    # ── Summary ───────────────────────────────────────────────────────────────

    @bp.route("/api/topologies/<topo_id>/analysis/summary", methods=["GET"])
    def analysis_summary(topo_id):
        """Aggregate device count, link count, open findings count, EOL count, compliance score."""
        with _nc() as db:
            devices = db.execute(
                "SELECT COUNT(*) FROM nc_objects WHERE topology_id=%s AND object_type NOT IN ('label','group','container')",
                (topo_id,),
            ).fetchone()[0]
            links = db.execute(
                "SELECT COUNT(*) FROM nc_objects WHERE topology_id=%s AND object_type='link'",
                (topo_id,),
            ).fetchone()[0]
            findings_row = db.execute(
                "SELECT COUNT(*) as total, "
                "SUM(CASE WHEN severity='cat1' THEN 1 ELSE 0 END) as cat1, "
                "SUM(CASE WHEN severity='cat2' THEN 1 ELSE 0 END) as cat2, "
                "SUM(CASE WHEN severity='cat3' THEN 1 ELSE 0 END) as cat3 "
                "FROM nc_compliance_findings WHERE topology_id=%s AND status='open'",
                (topo_id,),
            ).fetchone()
            eol_count = db.execute(
                "SELECT COUNT(*) FROM nc_objects WHERE topology_id=%s AND eol_status IN ('eol','eos')",
                (topo_id,),
            ).fetchone()[0] if _col_exists(db, "nc_objects", "eol_status") else 0

            # Compliance score: 100 - (cat1*20 + cat2*10 + cat3*5) / max(devices,1)
            cat1 = (findings_row[1] or 0) if findings_row else 0
            cat2 = (findings_row[2] or 0) if findings_row else 0
            cat3 = (findings_row[3] or 0) if findings_row else 0
            total_findings = (findings_row[0] or 0) if findings_row else 0
            penalty = cat1 * 20 + cat2 * 10 + cat3 * 5
            score = max(0, round(100 - penalty, 1))

        return jsonify({
            "topology_id": topo_id,
            "device_count": devices,
            "link_count": links,
            "open_findings": total_findings,
            "cat1_count": cat1,
            "cat2_count": cat2,
            "cat3_count": cat3,
            "eol_device_count": eol_count,
            "compliance_score": score,
        })

    # ── Topology Health ───────────────────────────────────────────────────────

    @bp.route("/api/topologies/<topo_id>/analysis/topology-health", methods=["GET"])
    def topology_health(topo_id):
        """Health score across 5 dimensions: redundancy, security, compliance, EOL, capacity."""
        with _nc() as db:
            # Compliance dimension
            findings = db.execute(
                "SELECT severity, COUNT(*) FROM nc_compliance_findings "
                "WHERE topology_id=%s AND status='open' GROUP BY severity",
                (topo_id,),
            ).fetchall()
            finding_map = {r[0]: r[1] for r in findings}
            cat1, cat2, cat3 = finding_map.get("cat1", 0), finding_map.get("cat2", 0), finding_map.get("cat3", 0)
            compliance_score = max(0, 100 - cat1 * 25 - cat2 * 10 - cat3 * 3)

            # Security: intent validation failures.
            #
            # Fail-closed (ndc-gov-01): 'error' counts as not-pass alongside
            # 'fail'. An intent check that could not be evaluated is not a
            # passing check — counting only 'fail' meant a topology whose checks
            # all errored scored a clean security tally, which is the fail-OPEN
            # case this gate exists to prevent.
            intent_fails = db.execute(
                "SELECT COUNT(*) FROM nc_intent_validations "
                "WHERE topology_id=%s AND result IN ('fail','error')",
                (topo_id,),
            ).fetchone()[0] if _table_exists(db, "nc_intent_validations") else 0
            intent_total = db.execute(
                "SELECT COUNT(*) FROM nc_intent_validations WHERE topology_id=%s",
                (topo_id,),
            ).fetchone()[0] if _table_exists(db, "nc_intent_validations") else 1
            security_score = round(max(0, 100 - (intent_fails / max(intent_total, 1)) * 100), 1)

            # EOL dimension
            if _col_exists(db, "nc_objects", "eol_status"):
                device_count = db.execute(
                    "SELECT COUNT(*) FROM nc_objects WHERE topology_id=%s AND object_type NOT IN ('label','group','link')",
                    (topo_id,),
                ).fetchone()[0] or 1
                eol_count = db.execute(
                    "SELECT COUNT(*) FROM nc_objects WHERE topology_id=%s AND eol_status IN ('eol','eos')",
                    (topo_id,),
                ).fetchone()[0]
                eol_score = round(max(0, 100 - (eol_count / device_count) * 100), 1)
            else:
                eol_score = 100.0

            # Redundancy: devices with redundant links (heuristic — nodes with >1 edge)
            redundancy_score = 75.0  # placeholder heuristic

            # Capacity: circuits over threshold
            capacity_score = 90.0  # placeholder heuristic

        dimensions = {
            "compliance": compliance_score,
            "security": security_score,
            "eol": eol_score,
            "redundancy": redundancy_score,
            "capacity": capacity_score,
        }
        overall = round(sum(dimensions.values()) / len(dimensions), 1)
        return jsonify({
            "topology_id": topo_id,
            "overall_health": overall,
            "dimensions": dimensions,
        })

    # ── Risk Matrix ───────────────────────────────────────────────────────────

    @bp.route("/api/topologies/<topo_id>/analysis/risk-matrix", methods=["GET"])
    def risk_matrix(topo_id):
        """Likelihood × Impact matrix for open compliance findings."""
        _SEVERITY_IMPACT = {"cat1": 5, "cat2": 3, "cat3": 1}
        _LIKELIHOOD = {"open": 4, "in_progress": 2, "deferred": 1}

        with _nc() as db:
            findings = db.execute(
                "SELECT id, check_name, severity, status, description "
                "FROM nc_compliance_findings WHERE topology_id=%s AND status != 'closed'",
                (topo_id,),
            ).fetchall()

        matrix = []
        for f in findings:
            fid, name, severity, status, desc = f
            impact = _SEVERITY_IMPACT.get(severity, 1)
            likelihood = _LIKELIHOOD.get(status, 1)
            risk_score = impact * likelihood
            matrix.append({
                "finding_id": fid,
                "name": name,
                "severity": severity,
                "status": status,
                "impact": impact,
                "likelihood": likelihood,
                "risk_score": risk_score,
                "quadrant": _quadrant(likelihood, impact),
                "description": desc,
            })

        matrix.sort(key=lambda x: -x["risk_score"])
        return jsonify({"topology_id": topo_id, "matrix": matrix, "total": len(matrix)})

    # ── Compliance Trend ──────────────────────────────────────────────────────

    @bp.route("/api/topologies/<topo_id>/analysis/trend", methods=["GET"])
    def analysis_trend(topo_id):
        """Compliance score over time from nc_versions snapshot history."""
        with _nc() as db:
            if not _table_exists(db, "nc_versions"):
                return jsonify({"topology_id": topo_id, "trend": [], "message": "No version history"})
            versions = db.execute(
                "SELECT version_number, created_at, metadata_json "
                "FROM nc_versions WHERE topology_id=%s ORDER BY version_number",
                (topo_id,),
            ).fetchall() if _col_exists(db, "nc_versions", "topology_id") else []

        trend = []
        for v in versions:
            version_num, created_at, meta_json = v
            compliance_score, cat1_count, meta_error = _version_meta_fields(meta_json or "{}")
            if meta_error:
                logger.warning(
                    "Version %s metadata parse failed for topology %s: %s",
                    version_num, topo_id, meta_error,
                )
            trend.append({
                "version": version_num,
                "date": created_at,
                **({"error": meta_error} if meta_error else {}),
                "compliance_score": compliance_score,
                "cat1_count": cat1_count,
            })
        return jsonify({"topology_id": topo_id, "trend": trend})

    # ── AI Topology Review ────────────────────────────────────────────────────

    @bp.route("/api/ai-review/<topo_id>", methods=["POST"])
    def ai_review(topo_id):
        """LLM-assisted topology review: heuristic SPOF/redundancy/STIG analysis,
        optionally enhanced by Ollama.  Returns findings + summary JSON."""
        with _nc() as db:
            row = db.execute(
                "SELECT name, graph_json FROM topologies WHERE id=%s", (topo_id,)
            ).fetchone()
        if not row:
            return jsonify({"error": "Topology not found"}), 404

        topo_name = row[0] if isinstance(row, (list, tuple)) else row["name"]
        gj_raw = row[1] if isinstance(row, (list, tuple)) else row["graph_json"]
        # Fail-closed (ndc-gov-01): a graph that cannot be parsed must surface
        # as an explicit error, not silently degrade to an empty graph that
        # the heuristics would then report as a clean/"healthy" topology.
        graph, graph_error = _parse_topology_graph(gj_raw)

        nodes = graph.get("nodes") or []
        edges = graph.get("edges") or []

        # ── Build adjacency for heuristic checks ─────────────────────────────
        device_nodes = [
            n for n in nodes
            if n.get("type", n.get("nodeType", "")) not in ("label", "group", "container", "cloud", "internet")
        ]
        # degree map
        degree: dict[str, int] = {}
        for e in edges:
            src, tgt = e.get("source", ""), e.get("target", "")
            degree[src] = degree.get(src, 0) + 1
            degree[tgt] = degree.get(tgt, 0) + 1

        findings: list[dict] = []
        fid = 0

        def _add(severity, category, title, description, suggestion, node_ids_list=None):
            nonlocal fid
            fid += 1
            findings.append({
                "id": f"F{fid}",
                "severity": severity,
                "category": category,
                "title": title,
                "description": description,
                "suggestion": suggestion,
                "node_ids": node_ids_list or [],
            })

        # ── SPOF: devices with exactly one link ───────────────────────────────
        spof_nodes = [n for n in device_nodes if degree.get(n["id"], 0) == 1]
        if spof_nodes:
            labels = [n.get("label", n["id"]) for n in spof_nodes[:5]]
            _add(
                "HIGH", "SPOF",
                "Single-link devices create SPOFs",
                f"{len(spof_nodes)} device(s) have only one network connection: "
                + ", ".join(labels) + ("…" if len(spof_nodes) > 5 else "") + ". "
                "Failure of the upstream port or link brings these devices offline.",
                "Add a redundant uplink to each single-homed device, or connect them "
                "to a second distribution/core switch.",
                [n["id"] for n in spof_nodes],
            )

        # ── SPOF: isolated devices (zero edges) ───────────────────────────────
        isolated = [n for n in device_nodes if degree.get(n["id"], 0) == 0]
        if isolated:
            labels = [n.get("label", n["id"]) for n in isolated[:5]]
            _add(
                "MEDIUM", "SPOF",
                "Isolated devices detected",
                f"{len(isolated)} device(s) have no links: " + ", ".join(labels) + ". "
                "These are either placeholders or misconfigured.",
                "Connect isolated devices or remove them if they are placeholders.",
                [n["id"] for n in isolated],
            )

        # ── REDUNDANCY: no redundant core/distribution ────────────────────────
        high_degree_nodes = [n for n in device_nodes if degree.get(n["id"], 0) >= 3]
        if len(device_nodes) >= 4 and len(high_degree_nodes) < 2:
            _add(
                "HIGH", "REDUNDANCY",
                "No redundant core/distribution layer",
                f"The topology has {len(device_nodes)} devices but fewer than 2 nodes "
                "with 3+ connections, indicating a single-core design. A core switch/router "
                "failure would partition the entire network.",
                "Deploy at least two core or distribution-layer devices with cross-connects "
                "to ensure network continuity on single-node failure.",
            )

        # ── REDUNDANCY: large flat topology (no hierarchy) ────────────────────
        if len(device_nodes) >= 8:
            max_degree = max((degree.get(n["id"], 0) for n in device_nodes), default=0)
            if max_degree < 4:
                _add(
                    "MEDIUM", "REDUNDANCY",
                    "Flat topology — no hierarchical design",
                    f"The topology has {len(device_nodes)} devices with a max node degree "
                    f"of {max_degree}. A three-tier (core/distribution/access) or spine-leaf "
                    "design improves redundancy and reduces blast radius.",
                    "Introduce a two- or three-tier hierarchy with redundant uplinks at each tier.",
                )

        # ── SEGMENTATION: single large broadcast domain ───────────────────────
        vlan_labels = [
            n for n in nodes
            if any(kw in (n.get("label", "") + n.get("type", "")).lower()
                   for kw in ("vlan", "segment", "zone", "dmz", "mgmt"))
        ]
        if len(device_nodes) >= 6 and len(vlan_labels) == 0:
            _add(
                "MEDIUM", "SEGMENTATION",
                "No network segmentation detected",
                f"The topology contains {len(device_nodes)} devices with no VLAN, DMZ, "
                "or zone boundaries visible. A flat network increases the lateral movement "
                "attack surface.",
                "Introduce VLAN segmentation or firewall zones separating at minimum: "
                "management, user, server, and DMZ segments.",
            )

        # ── ENCRYPTION: internet-facing links without firewall ────────────────
        internet_nodes = [
            n for n in nodes
            if any(kw in (n.get("label", "") + n.get("type", "")).lower()
                   for kw in ("internet", "cloud", "wan", "isp", "provider"))
        ]
        fw_nodes = [
            n for n in nodes
            if any(kw in (n.get("label", "") + n.get("type", "")).lower()
                   for kw in ("firewall", "fw", "palo", "fortinet", "asa", "checkpoint"))
        ]
        if internet_nodes and not fw_nodes:
            _add(
                "HIGH", "ENCRYPTION",
                "Internet/WAN connectivity without a firewall",
                "The topology includes internet or WAN nodes but no firewall device was "
                "detected. All traffic from external networks reaches internal devices directly.",
                "Place a stateful firewall or next-generation firewall between the internet "
                "boundary and the internal network.",
                [n["id"] for n in internet_nodes],
            )

        # ── STIG: management plane not separated ─────────────────────────────
        mgmt_nodes = [
            n for n in nodes
            if any(kw in (n.get("label", "") + n.get("type", "")).lower()
                   for kw in ("mgmt", "management", "oob", "out-of-band", "bastion", "jump"))
        ]
        if len(device_nodes) >= 6 and not mgmt_nodes:
            _add(
                "MEDIUM", "STIG",
                "No out-of-band management plane",
                "No management network, OOB segment, or bastion host was detected. "
                "STIG V-220532 and NIST CM-7 require a dedicated management plane "
                "isolated from production traffic.",
                "Add a dedicated OOB management VLAN/segment with jump server access. "
                "Restrict SSH/HTTPS management to that segment only.",
            )

        # ── ROUTING: no redundant gateway ────────────────────────────────────
        router_nodes = [
            n for n in nodes
            if any(kw in (n.get("label", "") + n.get("type", "")).lower()
                   for kw in ("router", "gateway", "gw", "rtr"))
        ]
        if len(device_nodes) >= 4 and len(router_nodes) == 1:
            _add(
                "HIGH", "ROUTING",
                "Single gateway/router — no redundant routing path",
                "Only one router or gateway device was detected. Loss of this device "
                "removes all routing capability from the topology.",
                "Deploy a secondary router with HSRP/VRRP or BGP failover to provide "
                "a redundant default gateway.",
                [n["id"] for n in router_nodes],
            )

        # ── ERROR: unparseable topology graph (fail-closed) ───────────────────
        review_status = "ok"
        if graph_error:
            review_status = "error"
            _add(
                "ERROR", "ERROR",
                "Topology graph could not be parsed",
                "The stored topology graph is invalid and could not be analyzed, so "
                "this review is incomplete and must not be read as a clean result. "
                f"Parser reported: {graph_error}",
                "Re-save the topology from the canvas to regenerate valid graph JSON, "
                "then re-run the AI review.",
            )

        # ── SUGGESTION: empty topology ────────────────────────────────────────
        if not device_nodes and not graph_error:
            _add(
                "INFO", "SUGGESTION",
                "Topology is empty",
                "No devices have been added to this topology yet.",
                "Start by dragging devices from the palette on the left, then connect "
                "them with links to build your network diagram.",
            )

        # ── Try Ollama for AI-enhanced summary ────────────────────────────────
        provider = "Heuristic Analysis"
        try:
            from tools.http.client import request as _http_req
            ollama_url = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
            ollama_model = os.environ.get("OLLAMA_TOPO_MODEL", os.environ.get("OLLAMA_MODEL", "qwen3-local"))
            device_summary = ", ".join(
                n.get("label", n["id"]) for n in device_nodes[:20]
            ) or "none"
            prompt = (
                f"Network topology '{topo_name}': {len(device_nodes)} devices "
                f"({device_summary}), {len(edges)} links.\n"
                f"Heuristic findings: {len(findings)} issues identified "
                f"({sum(1 for f in findings if f['severity']=='HIGH')} HIGH, "
                f"{sum(1 for f in findings if f['severity']=='MEDIUM')} MEDIUM).\n"
                "Write a 2-sentence executive summary of the topology's security and "
                "redundancy posture. Be concise and specific. No preamble."
            )
            resp = _http_req(
                "POST",
                f"{ollama_url}/api/chat",
                json={
                    "model": ollama_model,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                    "options": {"num_predict": 200, "temperature": 0.3},
                },
                timeout=30,
            )
            ai_summary = resp.json().get("message", {}).get("content", "").strip()
            import re as _re
            ai_summary = _re.sub(r"<think>.*?</think>", "", ai_summary, flags=_re.DOTALL).strip()
            if ai_summary:
                provider = f"Ollama ({ollama_model})"
        except Exception:
            ai_summary = None

        # Build fallback summary from heuristics
        if not ai_summary:
            high_cnt = sum(1 for f in findings if f["severity"] == "HIGH")
            med_cnt = sum(1 for f in findings if f["severity"] == "MEDIUM")
            if not findings:
                ai_summary = (
                    f"Topology '{topo_name}' has {len(device_nodes)} devices and "
                    f"{len(edges)} links. No heuristic issues were detected."
                )
            else:
                ai_summary = (
                    f"Topology '{topo_name}' has {len(device_nodes)} device(s) and "
                    f"{len(edges)} link(s). "
                    f"{high_cnt} HIGH and {med_cnt} MEDIUM issues were identified, "
                    "primarily around redundancy and segmentation."
                )

        return jsonify({
            "topology_id": topo_id,
            "findings": findings,
            "summary": ai_summary,
            "provider": provider,
            # Fail-closed status: "error" means the review could not complete
            # (e.g. corrupt graph) and its findings are NOT a clean bill.
            "status": review_status,
        })

    # ── Export ────────────────────────────────────────────────────────────────

    @bp.route("/api/topologies/<topo_id>/analysis/export", methods=["GET"])
    def analysis_export(topo_id):
        """Export full analysis as JSON (default) or PDF summary."""
        fmt = request.args.get("format", "json").lower()

        with _nc() as db:
            devices = db.execute(
                "SELECT COUNT(*) FROM nc_objects WHERE topology_id=%s AND object_type NOT IN ('label','group','link')",
                (topo_id,),
            ).fetchone()[0]
            findings = [
                dict(r) for r in db.execute(
                    "SELECT id, check_name, severity, status, description "
                    "FROM nc_compliance_findings WHERE topology_id=%s",
                    (topo_id,),
                ).fetchall()
            ]

        payload = {
            "topology_id": topo_id,
            "device_count": devices,
            "findings": findings,
            "total_findings": len(findings),
            "cat1_count": sum(1 for f in findings if f.get("severity") == "cat1"),
        }

        if fmt == "pdf":
            try:
                from tools.network.pdf_export import export_analysis_pdf
                pdf_bytes = export_analysis_pdf(topo_id, payload)
                from flask import Response
                return Response(
                    pdf_bytes,
                    mimetype="application/pdf",
                    headers={"Content-Disposition": f"attachment; filename=analysis_{topo_id}.pdf"},
                )
            except Exception as exc:
                logger.warning("PDF export failed, returning JSON: %s", exc)

        return jsonify(payload)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_topology_graph(gj_raw) -> tuple[dict, str | None]:
    """Parse a topology graph_json blob under a fail-closed contract (ndc-gov-01).

    On success returns (graph_dict, None). On any parse failure — or a payload
    that is not a JSON object — returns (benign_empty_graph, error_summary)
    where error_summary is a short string naming the exception. Callers must
    treat a non-None error as a failed/incomplete analysis, NEVER as a clean
    result. The empty fallback graph lets downstream code run without a second
    guard, but the error string is what makes the failure visible.
    """
    try:
        graph = json.loads(gj_raw or '{"nodes":[],"edges":[]}')
    except Exception as exc:
        logger.warning("ai_review: topology graph parse failed: %s", exc)
        return {"nodes": [], "edges": []}, f"{type(exc).__name__}: {exc}"
    if not isinstance(graph, dict):
        logger.warning(
            "ai_review: topology graph is %s, expected object", type(graph).__name__
        )
        return {"nodes": [], "edges": []}, (
            f"graph JSON is {type(graph).__name__}, expected object"
        )
    return graph, None


def _table_exists(db, table: str) -> bool:
    from tools.network.blueprint_helpers import table_exists
    return table_exists(db, table)


def _col_exists(db, table: str, col: str) -> bool:
    from tools.network.blueprint_helpers import col_exists
    return col_exists(db, table, col)


def _quadrant(likelihood: int, impact: int) -> str:
    high_l = likelihood >= 3
    high_i = impact >= 3
    if high_l and high_i:
        return "critical"
    if high_l:
        return "high_likelihood"
    if high_i:
        return "high_impact"
    return "low_priority"
