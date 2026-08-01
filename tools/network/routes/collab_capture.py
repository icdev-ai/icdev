# CUI // SP-CTI
"""ICDEV Network Design Canvas -- collab_capture route group.

Extracted verbatim from tools/network/blueprint.py (cvx-net-01 monolith split).
Registered on the shared NDC blueprint via register_collab_capture_routes(bp).
"""
from __future__ import annotations

import json
import uuid as _uuid
from datetime import datetime, timezone
from flask import g, jsonify, render_template, request
from tools.db.storage import sql_placeholder
from tools.network.blueprint_helpers import _audit, _now, _row_to_dict, nc_login_required
from tools.network.db.init_db import get_connection


def register_collab_capture_routes(bp):
    """Register collab_capture routes on the NDC blueprint."""

    @bp.route("/enterprise")
    @nc_login_required
    def nc_enterprise():
        """Enterprise summary page — aggregate metrics across all projects."""
        return render_template("network/enterprise.html")

    @bp.route("/api/enterprise-summary")
    @nc_login_required
    def nc_enterprise_summary_api():
        """Enterprise summary API — aggregate metrics."""
        conn = get_connection()
        _ph = sql_placeholder(conn)
        total_topos = conn.execute("SELECT COUNT(*) FROM topologies").fetchone()[0]
        total_devices = 0
        total_interconnects = 0
        total_cat1 = 0
        total_open_findings = 0
        compliance_pct = None

        # Count devices from all topology graphs
        rows = conn.execute("SELECT graph_json FROM topologies").fetchall()
        for r in rows:
            try:
                g = json.loads(r[0] if isinstance(r, tuple) else r["graph_json"])
                total_devices += len(g.get("nodes", []))
                total_interconnects += len(g.get("edges", []))
            except Exception:
                pass

        # Compliance findings
        try:
            findings = conn.execute(
                "SELECT COUNT(*) FROM nc_compliance_findings WHERE status = 'open'"
            ).fetchone()
            total_open_findings = findings[0] if findings else 0
            cat1 = conn.execute(
                "SELECT COUNT(*) FROM nc_compliance_findings WHERE severity = 'CAT1' AND status = 'open'"
            ).fetchone()
            total_cat1 = cat1[0] if cat1 else 0
        except Exception:
            pass

        # Projects
        total_projects = 0
        status_counts = {}
        try:
            proj_rows = conn.execute("SELECT status, COUNT(*) FROM nc_projects GROUP BY status").fetchall()
            for pr in proj_rows:
                s = pr[0] if isinstance(pr, tuple) else pr["status"]
                c = pr[1] if isinstance(pr, tuple) else pr[1]
                status_counts[s or "draft"] = c
                total_projects += c
        except Exception:
            pass

        # Cost
        total_capex = 0
        total_circuit_monthly = 0
        try:
            capex_row = conn.execute("SELECT SUM(purchase_cost) FROM ni_devices").fetchone()
            total_capex = float(capex_row[0] or 0) if capex_row else 0
            circ_row = conn.execute("SELECT SUM(monthly_cost_usd) FROM nc_circuits").fetchone()
            total_circuit_monthly = float(circ_row[0] or 0) if circ_row else 0
        except Exception:
            pass

        # Board reviews
        board_reviews = {"pending": 0, "approved": 0, "rejected": 0}
        try:
            for status in ("pending", "approved", "rejected"):
                cnt = conn.execute(
                    f"SELECT COUNT(*) FROM nc_governance_reviews WHERE status = {_ph}", (status,)
                ).fetchone()
                board_reviews[status] = cnt[0] if cnt else 0
        except Exception:
            pass

        conn.close()
        return jsonify({
            "total_projects": total_projects,
            "total_topologies": total_topos,
            "total_devices": total_devices,
            "total_interconnects": total_interconnects,
            "compliance_pct": compliance_pct,
            "total_cat1": total_cat1,
            "total_open_findings": total_open_findings,
            "status_counts": status_counts,
            "total_capex": total_capex,
            "total_circuit_cost_monthly": total_circuit_monthly,
            "board_reviews": board_reviews,
        })

    # ── Collaboration (Task 18) ───────────────────────────────────────────────
    import uuid as _uuid_mod
    from tools.canvas.collaboration import CanvasCollabManager as _NDCCollabMgr

    _ndc_collab = _NDCCollabMgr("nc")

    @bp.route("/api/collab/<design_id>/join", methods=["POST"])
    @nc_login_required
    def nc_collab_join(design_id):
        """Join a collaborative NDC editing session."""
        body = request.json or {}
        user_id = body.get("user_id", str(_uuid_mod.uuid4())[:8])
        user_name = body.get("user_name", "")
        return jsonify(_ndc_collab.join(design_id, user_id, user_name))

    @bp.route("/api/collab/<design_id>/leave", methods=["POST"])
    @nc_login_required
    def nc_collab_leave(design_id):
        """Leave an NDC collaborative session."""
        body = request.json or {}
        user_id = body.get("user_id", "")
        _ndc_collab.leave(design_id, user_id)
        return jsonify({"ok": True})

    @bp.route("/api/collab/<design_id>/push", methods=["POST"])
    @nc_login_required
    def nc_collab_push(design_id):
        """Push an operation into an NDC collaborative session."""
        body = request.json or {}
        user_id = body.get("user_id", "")
        op_type = body.get("op_type", "")
        data = body.get("data", {})
        seq = _ndc_collab.push(design_id, user_id, op_type, data)
        return jsonify({"seq": seq})

    @bp.route("/api/collab/<design_id>/poll", methods=["GET"])
    @nc_login_required
    def nc_collab_poll(design_id):
        """Poll for NDC collaborative operations since a sequence number."""
        since = int(request.args.get("since", 0))
        user_id = request.args.get("user_id", "")
        cx = request.args.get("cx")
        cy = request.args.get("cy")
        if user_id and cx is not None and cy is not None:
            _ndc_collab.update_cursor(design_id, user_id, float(cx), float(cy))
        ops, participants, latest_seq = _ndc_collab.poll(design_id, since)
        return jsonify({"operations": ops, "participants": participants, "latest_seq": latest_seq})

    @bp.route("/api/collab/<design_id>/participants", methods=["GET"])
    @nc_login_required
    def nc_collab_participants(design_id):
        """Return current participants in an NDC collaborative session."""
        return jsonify({"participants": _ndc_collab.get_participants(design_id)})

    # ── Runbooks ──────────────────────────────────────────────────────────────

    _NDC_RUNBOOK_TRIGGERS = [
        "link_failure",
        "routing_loop",
        "stig_remediation",
        "bgp_session_recovery",
        "circuit_outage_triage",
        "device_unreachable",
        "ipam_conflict",
        "ddos_mitigation",
    ]
    _NDC_RUNBOOK_SEVERITIES = ["critical", "high", "medium", "low"]

    @bp.route("/runbooks")
    @nc_login_required
    def nc_runbooks():
        """NDC Runbooks — network incident response playbooks."""
        conn = get_connection()
        runbooks = [
            _row_to_dict(r)
            for r in conn.execute(
                "SELECT * FROM ndc_runbooks ORDER BY updated_at DESC"
            ).fetchall()
        ]
        topologies = [
            _row_to_dict(r)
            for r in conn.execute(
                "SELECT id, name FROM topologies ORDER BY name"
            ).fetchall()
        ]
        conn.close()
        for rb in runbooks:
            try:
                rb["steps"] = json.loads(rb.get("steps_json") or "[]")
            except Exception:
                rb["steps"] = []
        return render_template(
            "network/runbooks.html",
            runbooks=runbooks,
            topologies=topologies,
            valid_triggers=_NDC_RUNBOOK_TRIGGERS,
            valid_severities=_NDC_RUNBOOK_SEVERITIES,
        )

    @bp.route("/api/runbooks", methods=["GET"])
    @nc_login_required
    def nc_api_runbooks_list():
        """List all NDC runbooks."""
        conn = get_connection()
        rows = [
            _row_to_dict(r)
            for r in conn.execute(
                "SELECT * FROM ndc_runbooks ORDER BY updated_at DESC"
            ).fetchall()
        ]
        conn.close()
        for r in rows:
            try:
                r["steps"] = json.loads(r.get("steps_json") or "[]")
            except Exception:
                r["steps"] = []
        return jsonify(rows)

    @bp.route("/api/runbooks", methods=["POST"])
    @nc_login_required
    def nc_api_runbooks_create():
        """Create a new NDC runbook."""
        body = request.json or {}
        title = (body.get("title") or "").strip()
        if not title:
            return jsonify({"error": "title is required"}), 400
        trigger = body.get("trigger_event", "link_failure")
        if trigger not in _NDC_RUNBOOK_TRIGGERS:
            trigger = "link_failure"
        severity = body.get("severity", "high")
        if severity not in _NDC_RUNBOOK_SEVERITIES:
            severity = "high"
        rid = str(_uuid.uuid4())
        now = _now()
        steps_json = json.dumps(body.get("steps") or [])
        conn = get_connection()
        _ph = sql_placeholder(conn)
        conn.execute(
            "INSERT INTO ndc_runbooks (id, title, trigger_event, severity, owner, "
            "topology_id, description, steps_json, classification, created_at, updated_at) "
            f"VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph})",
            (
                rid, title, trigger, severity,
                body.get("owner", ""), body.get("topology_id") or None,
                body.get("description", ""), steps_json,
                body.get("classification", "CUI"), now, now,
            ),
        )
        conn.commit()
        _audit(conn, "runbook_created", "ndc_runbook", rid, title)
        conn.close()
        return jsonify({"id": rid, "status": "created"})

    @bp.route("/api/runbooks/<rb_id>", methods=["GET"])
    @nc_login_required
    def nc_api_runbook_get(rb_id):
        """Get a single NDC runbook by ID."""
        conn = get_connection()
        _ph = sql_placeholder(conn)
        row = conn.execute(f"SELECT * FROM ndc_runbooks WHERE id={_ph}", (rb_id,)).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "not found"}), 404
        rb = _row_to_dict(row)
        try:
            rb["steps"] = json.loads(rb.get("steps_json") or "[]")
        except Exception:
            rb["steps"] = []
        return jsonify(rb)

    @bp.route("/api/runbooks/<rb_id>", methods=["PUT"])
    @nc_login_required
    def nc_api_runbook_update(rb_id):
        """Update an existing NDC runbook."""
        conn = get_connection()
        _ph = sql_placeholder(conn)
        row = conn.execute(f"SELECT id FROM ndc_runbooks WHERE id={_ph}", (rb_id,)).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "not found"}), 404
        body = request.json or {}
        now = _now()
        trigger = body.get("trigger_event", "link_failure")
        if trigger not in _NDC_RUNBOOK_TRIGGERS:
            trigger = "link_failure"
        severity = body.get("severity", "high")
        if severity not in _NDC_RUNBOOK_SEVERITIES:
            severity = "high"
        steps_json = json.dumps(body.get("steps") or [])
        conn.execute(
            f"UPDATE ndc_runbooks SET title={_ph}, trigger_event={_ph}, severity={_ph}, owner={_ph}, "
            f"topology_id={_ph}, description={_ph}, steps_json={_ph}, classification={_ph}, updated_at={_ph} WHERE id={_ph}",
            (
                (body.get("title") or "").strip() or "Untitled",
                trigger, severity,
                body.get("owner", ""), body.get("topology_id") or None,
                body.get("description", ""), steps_json,
                body.get("classification", "CUI"), now, rb_id,
            ),
        )
        conn.commit()
        _audit(conn, "runbook_updated", "ndc_runbook", rb_id, body.get("title", ""))
        conn.close()
        return jsonify({"status": "updated"})

    @bp.route("/api/runbooks/<rb_id>", methods=["DELETE"])
    @nc_login_required
    def nc_api_runbook_delete(rb_id):
        """Delete an NDC runbook."""
        conn = get_connection()
        _ph = sql_placeholder(conn)
        row = conn.execute(f"SELECT id FROM ndc_runbooks WHERE id={_ph}", (rb_id,)).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "not found"}), 404
        conn.execute(f"DELETE FROM ndc_runbooks WHERE id={_ph}", (rb_id,))
        conn.commit()
        _audit(conn, "runbook_deleted", "ndc_runbook", rb_id, rb_id)
        conn.close()
        return jsonify({"status": "deleted"})

    # ── SOP Library ───────────────────────────────────────────────────────

    @bp.route("/sops")
    @nc_login_required
    def nc_sops_page():
        from tools.network.sops import list_sops as _ls
        category = request.args.get("category", "")
        status_f = request.args.get("status", "approved")
        q = request.args.get("q", "")
        sops = _ls(category=category or None, status=status_f or None, limit=200)
        all_sops = _ls(limit=1000)
        categories = sorted({s["category"] for s in all_sops})
        csps = sorted({s.get("csp", "multi") for s in all_sops})
        return render_template(
            "network/sops.html",
            sops=sops, categories=categories, csps=csps,
            filter_category=category, filter_status=status_f, search_q=q,
            is_admin=(getattr(getattr(g, "current_user", None), "role", "") == "admin"),
        )

    @bp.route("/api/sops")
    def nc_api_sops_list():
        from tools.network.sops import list_sops as _ls
        return jsonify(_ls(
            category=request.args.get("category") or None,
            status=request.args.get("status") or None,
            limit=200,
        ))

    @bp.route("/api/sops/<sop_id>")
    def nc_api_sop_get(sop_id):
        from tools.network.sops import get_sop as _gs
        s = _gs(sop_id)
        return (jsonify(s), 200) if s else (jsonify({"error": "not found"}), 404)

    @bp.route("/api/sops/<sop_id>/history")
    def nc_api_sop_history(sop_id):
        from tools.network.sops import get_approval_history as _gah
        return jsonify(_gah(sop_id))

    # ── Connectivity Reference ────────────────────────────────────────────

    @bp.route("/connectivity")
    @nc_login_required
    def nc_connectivity_page():
        from tools.network.connectivity_ref import (
            get_connectivity_matrix, get_scca_flow, get_resiliency_tiers,
        )
        return render_template(
            "network/connectivity.html",
            matrix=get_connectivity_matrix(),
            csps=["aws", "azure", "gcp", "oci", "ibm"],
            scca_flow=get_scca_flow(),
            resiliency_tiers=get_resiliency_tiers(),
        )

    @bp.route("/api/connectivity/matrix")
    def nc_api_connectivity_matrix():
        from tools.network.connectivity_ref import get_connectivity_matrix
        return jsonify(get_connectivity_matrix())

    @bp.route("/api/connectivity/onprem-pattern")
    def nc_api_onprem_pattern():
        from tools.network.connectivity_ref import get_onprem_to_csp_patterns
        return jsonify(get_onprem_to_csp_patterns(
            csp=request.args.get("csp", "aws"),
            pattern_type=request.args.get("type", "ipsec_vpn"),
        ))

    @bp.route("/api/connectivity/c2c-patterns")
    def nc_api_c2c_patterns():
        from tools.network.connectivity_ref import get_csp_to_csp_patterns
        return jsonify(get_csp_to_csp_patterns(
            src_csp=request.args.get("src", "aws"),
            dst_csp=request.args.get("dst", "azure"),
        ))

    # ── Packet Capture (GNS3 / lab link capture) ─────────────────────────

    import hashlib
    import struct
    from datetime import timedelta

    def _gen_stub_pcap() -> bytes:
        """Return a minimal valid PCAP file (global header only, no packets).

        Magic 0xa1b2c3d4, Ethernet link type.  Wireshark opens this cleanly and
        reports "0 packets captured".  Real GNS3 captures replace this with the
        streamed .pcapng file.
        """
        return struct.pack(
            "<IHHiIII",
            0xA1B2C3D4,  # magic number
            2,            # version major
            4,            # version minor
            0,            # thiszone (UTC)
            0,            # sigfigs
            65535,        # snaplen
            1,            # network (LINKTYPE_ETHERNET)
        )

    def _finalize_capture(conn, cap_id: str):
        """Transition a 'running' capture to 'complete' with stub PCAP data."""
        pcap_bytes = _gen_stub_pcap()
        sha = hashlib.sha256(pcap_bytes).hexdigest()
        expiry = (datetime.now(timezone.utc) + timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        _ph = sql_placeholder(conn)
        conn.execute(
            f"""UPDATE nc_packet_captures
               SET status='complete', size_bytes={_ph}, sha256={_ph}, expiry_at={_ph},
                   stopped_at={_ph}, pcap_data={_ph}
               WHERE id={_ph}""",
            (len(pcap_bytes), sha, expiry, now, pcap_bytes, cap_id),
        )

    @bp.route("/api/captures", methods=["POST"])
    @nc_login_required
    def nc_api_capture_start():
        """Start a packet capture on a canvas link.

        Body: {link_id, topology_id, src_label?, dst_label?, protocol?, lab_run_id?}
        """
        body = request.get_json(force=True) or {}
        link_id = (body.get("link_id") or "").strip()
        topo_id = (body.get("topology_id") or "").strip()
        if not link_id or not topo_id:
            return jsonify({"error": "link_id and topology_id required"}), 400

        cap_id = str(_uuid.uuid4())
        lab_run_id = body.get("lab_run_id") or None
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # If no lab_run provided, auto-create a stub run for this topology
        conn = get_connection()
        _ph = sql_placeholder(conn)
        if not lab_run_id:
            lab_run_id = str(_uuid.uuid4())
            conn.execute(
                f"""INSERT INTO nc_lab_runs (id, topology_id, name, backend, status, started_at)
                   VALUES ({_ph}, {_ph}, {_ph}, 'stub', 'running', {_ph})""",
                (lab_run_id, topo_id, f"Auto-run {now[:10]}", now),
            )

        conn.execute(
            f"""INSERT INTO nc_packet_captures
               (id, link_id, lab_run_id, topology_id, src_label, dst_label,
                protocol, status, created_at)
               VALUES ({_ph}, {_ph}, {_ph}, {_ph}, {_ph}, {_ph}, {_ph}, 'running', {_ph})""",
            (
                cap_id,
                link_id,
                lab_run_id,
                topo_id,
                body.get("src_label", ""),
                body.get("dst_label", ""),
                body.get("protocol", ""),
                now,
            ),
        )
        conn.commit()
        _audit(conn, "capture_started", "nc_packet_captures", cap_id, link_id)
        conn.close()
        return jsonify({"id": cap_id, "status": "running", "link_id": link_id})

    @bp.route("/api/captures", methods=["GET"])
    @nc_login_required
    def nc_api_captures_list():
        """List captures.  Query params: link_id, topology_id."""
        link_id = request.args.get("link_id", "")
        topo_id = request.args.get("topology_id", "")
        conn = get_connection()
        _ph = sql_placeholder(conn)
        if link_id:
            rows = conn.execute(
                f"SELECT * FROM nc_packet_captures WHERE link_id={_ph} ORDER BY created_at DESC",
                (link_id,),
            ).fetchall()
        elif topo_id:
            rows = conn.execute(
                f"SELECT * FROM nc_packet_captures WHERE topology_id={_ph} ORDER BY created_at DESC",
                (topo_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM nc_packet_captures ORDER BY created_at DESC LIMIT 100"
            ).fetchall()
        conn.close()
        result = []
        for r in rows:
            d = _row_to_dict(r)
            d.pop("pcap_data", None)  # don't send binary over list endpoint
            result.append(d)
        return jsonify(result)

    @bp.route("/api/captures/<cap_id>", methods=["GET"])
    @nc_login_required
    def nc_api_capture_get(cap_id):
        """Poll a single capture; auto-finalizes stub captures after 5 s."""
        conn = get_connection()
        _ph = sql_placeholder(conn)
        row = conn.execute(
            f"SELECT * FROM nc_packet_captures WHERE id={_ph}", (cap_id,)
        ).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "not found"}), 404

        d = _row_to_dict(row)
        d.pop("pcap_data", None)

        # Auto-finalize stub captures that have been 'running' for ≥5 s
        if d.get("status") == "running":
            backend_ref = json.loads(d.get("backend_ref") or "{}")
            is_stub = not backend_ref.get("gns3_node_id")
            created = d.get("created_at", "")
            try:
                age = (
                    datetime.now(timezone.utc)
                    - datetime.fromisoformat(created.replace("Z", "+00:00"))
                ).total_seconds()
            except Exception:
                age = 99
            if is_stub and age >= 5:
                _finalize_capture(conn, cap_id)
                conn.commit()
                row2 = conn.execute(
                    f"SELECT * FROM nc_packet_captures WHERE id={_ph}", (cap_id,)
                ).fetchone()
                d = _row_to_dict(row2)
                d.pop("pcap_data", None)

        conn.close()
        return jsonify(d)

    @bp.route("/api/captures/<cap_id>/stop", methods=["POST"])
    @nc_login_required
    def nc_api_capture_stop(cap_id):
        """Stop a running capture."""
        conn = get_connection()
        _ph = sql_placeholder(conn)
        row = conn.execute(
            f"SELECT id, status FROM nc_packet_captures WHERE id={_ph}", (cap_id,)
        ).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "not found"}), 404
        if row["status"] == "running":
            _finalize_capture(conn, cap_id)
            conn.commit()
            _audit(conn, "capture_stopped", "nc_packet_captures", cap_id, cap_id)
        conn.close()
        return jsonify({"status": "complete"})

    @bp.route("/api/captures/<cap_id>/download", methods=["GET"])
    @nc_login_required
    def nc_api_capture_download(cap_id):
        """Download the .pcapng file for a completed capture."""
        from flask import Response

        conn = get_connection()
        _ph = sql_placeholder(conn)
        row = conn.execute(
            f"SELECT * FROM nc_packet_captures WHERE id={_ph}", (cap_id,)
        ).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "not found"}), 404
        d = _row_to_dict(row)

        # Ensure capture is finalized before download
        if d.get("status") == "running":
            _finalize_capture(conn, cap_id)
            conn.commit()
            row = conn.execute(
                f"SELECT * FROM nc_packet_captures WHERE id={_ph}", (cap_id,)
            ).fetchone()
            d = _row_to_dict(row)

        pcap_bytes = row["pcap_data"] if row["pcap_data"] else _gen_stub_pcap()
        conn.close()

        src = (d.get("src_label") or "src").replace(" ", "-")
        dst = (d.get("dst_label") or "dst").replace(" ", "-")
        ts = (d.get("created_at") or "").replace(":", "").replace("-", "")[:13]
        filename = f"capture-{src}-{dst}-{ts}.pcap"

        return Response(
            pcap_bytes,
            mimetype="application/vnd.tcpdump.pcap",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Length": str(len(pcap_bytes)),
            },
        )

    # ── Ingestion routes (file upload, config, NMS, folder watch) ────────
