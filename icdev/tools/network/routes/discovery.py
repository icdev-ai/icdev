# CUI // SP-CTI
"""Network Canvas — Auto-Discovery routes (rmf-disc-02).

THE DEFECT THIS MODULE CLOSES

``tools/dashboard/templates/network/discovery.html`` is a complete page: a scan
form, a scan-history table, a device-detail modal, an import-into-topology
control and an as-designed-vs-as-built diff panel. Its JavaScript calls five
endpoints, and all five were DEFINED NOWHERE:

    POST   /network/api/discovery/scan
    GET    /network/api/discovery/scans/<scan_id>
    DELETE /network/api/discovery/scans/<scan_id>
    POST   /network/api/discovery/scans/<scan_id>/import/<topology_id>
    POST   /network/api/discovery/diff

The page could not even reach that JavaScript. Its route in
``routes/analytics.py`` rendered the template only when a path existed at
``tools/network/dashboard/templates/network/discovery.html`` — a directory that
has never existed in this tree (the templates live under
``tools/dashboard/templates/``), so the guard was always false and
``/network/discovery`` returned the string ``Discovery page coming soon`` with
HTTP 200. Measured on the live dashboard 2026-09-02: exactly that. The route is
moved here and the guard removed — ``render_template`` already raises a
resolvable error when a template is genuinely missing, and a guard that
silently downgrades a missing page to a 200 is worse than the failure it hides.

The page route also passed NO context. ``scans``, ``topologies``,
``has_pysnmp`` and ``has_netmiko`` were all undefined, and Jinja renders an
undefined as empty — so even had the guard passed, the page would have shown an
empty scan list and an empty topology picker with nothing indicating that it
had not looked.

WHAT THESE ROUTES DO NOT DO

They do not scan in the background. ``run_discovery`` is synchronous and the
POST blocks until it finishes, which is what the page's JavaScript already
expects ("This may take a moment"). A background worker would need a job table,
a poller and a cancellation story; none of that exists, and faking it with a
thread would leave a scan stuck in `running` forever whenever the process
recycled. The bound that makes this safe is ``max_targets`` — see ``_expand``.
"""

from __future__ import annotations

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.network.routes.discovery")

#: A single request may not expand to more than this many target addresses.
#: The scan is synchronous, so this is the only thing standing between one
#: pasted `10.0.0.0/8` and a request that never returns. A refusal NAMES the
#: number it refused, so the operator can split the sweep rather than guess.
MAX_TARGETS = 1024

#: Per-host timeout ceiling. A caller-supplied timeout is clamped rather than
#: rejected: an operator asking for 60s per host on a /24 is asking for a
#: four-hour request, and silently honouring it is how a page becomes "broken".
MAX_TIMEOUT_SECONDS = 10.0

#: Neighbour-crawl depth ceiling. Each hop re-scans every newly found neighbour,
#: so cost is superlinear in this number.
MAX_HOP_LIMIT = 5


def register_discovery_routes(bp):
    """Register the auto-discovery page + API routes on the NDC blueprint."""
    from flask import jsonify, render_template, request

    from tools.network.blueprint_helpers import nc_login_required

    # ── Page ─────────────────────────────────────────────────────────────

    @bp.route("/discovery")
    @nc_login_required
    def nc_discovery_page():
        """Auto-Discovery page — scan history, protocol availability, topologies.

        Every value the template reads is supplied. ``scans`` and ``topologies``
        come from the store; the two protocol flags are import probes, and they
        are the honest answer to "can this deployment scan by SNMP/SSH at all",
        which is otherwise only discoverable by launching a scan and watching it
        find nothing.
        """
        from tools.network import discovery as _disc
        from tools.network import discovery_store as store

        return render_template(
            "network/discovery.html",
            scans=store.list_scans(),
            topologies=store.list_topologies(),
            has_pysnmp=_disc._HAS_PYSNMP,
            has_netmiko=_disc._HAS_NETMIKO,
            inventory=store.device_inventory_stats(),
        )

    # ── Helpers ──────────────────────────────────────────────────────────

    def _parse_targets(raw) -> list[str]:
        """Accept the comma-separated string the form posts, or a JSON list."""
        if isinstance(raw, list):
            items = raw
        else:
            items = str(raw or "").replace("\n", ",").split(",")
        return [t.strip() for t in items if str(t).strip()]

    def _expand_size(targets: list[str]) -> int:
        """Count how many addresses *targets* expands to, WITHOUT scanning.

        A CIDR is counted by its address space rather than by pinging it, so an
        oversized sweep is refused before it costs anything. A hostname counts
        as one. This deliberately over-counts a sparse subnet (a /24 counts 256
        even if 3 hosts answer) — the bound is on work REQUESTED, and the ping
        sweep has to try every address to find out which of them are alive.
        """
        import ipaddress

        total = 0
        for t in targets:
            try:
                total += ipaddress.ip_network(t, strict=False).num_addresses
            except ValueError:
                total += 1
        return total

    # ── POST /api/discovery/scan ─────────────────────────────────────────

    @bp.route("/api/discovery/scan", methods=["POST"])
    @nc_login_required
    def nc_api_discovery_scan():
        """Run a discovery scan synchronously and persist the result.

        The scan row is written BEFORE the scan runs, so a request that dies
        mid-sweep leaves a `running` row rather than no trace. A scan that
        completes having found nothing is `completed` with 0 devices — only a
        scanner that RAISED is `failed`.
        """
        from tools.network import discovery as _disc
        from tools.network import discovery_store as store

        data = request.get_json(force=True, silent=True) or {}
        targets = _parse_targets(data.get("targets"))
        if not targets:
            return jsonify({"error": "at least one target IP or CIDR is required"}), 400

        method = str(data.get("method") or "snmp").lower()
        if method not in (*store.PASSIVE_METHODS, *store.ACTIVE_METHODS):
            return jsonify({
                "error": f"unknown method {method!r}",
                "supported": [*store.PASSIVE_METHODS, *store.ACTIVE_METHODS],
            }), 400

        expanded = _expand_size(targets)
        if expanded > MAX_TARGETS:
            # Named, not silently truncated: a sweep that quietly scanned the
            # first 1024 of 65536 addresses would report a partial estate as a
            # complete one.
            return jsonify({
                "error": (
                    f"targets expand to {expanded} addresses, over the "
                    f"{MAX_TARGETS} limit for a synchronous scan. Split the sweep."
                ),
                "expanded": expanded,
                "max_targets": MAX_TARGETS,
            }), 400

        try:
            hop_limit = max(0, min(int(data.get("hop_limit") or 0), MAX_HOP_LIMIT))
        except (TypeError, ValueError):
            hop_limit = 2
        try:
            timeout = min(float(data.get("timeout") or 2.0), MAX_TIMEOUT_SECONDS)
        except (TypeError, ValueError):
            timeout = 2.0

        topology_id = data.get("topology_id") or None
        scan_id = store.create_scan(
            name=data.get("name") or f"{method.upper()} scan — {', '.join(targets)[:60]}",
            targets=targets,
            method=method,
            topology_id=topology_id,
            config={
                "hop_limit": hop_limit,
                "layout": data.get("layout") or "grid",
                "device_type": data.get("device_type") or "cisco_ios",
                "timeout": timeout,
                # _safe_config strips these two and records only THAT they were
                # supplied — a community string and a password are credentials
                # for live infrastructure and are never persisted.
                "community": data.get("community"),
                "password": data.get("password"),
                "username": data.get("username"),
            },
        )

        try:
            result = _disc.run_discovery(
                targets=targets,
                method=method,
                community=str(data.get("community") or "public"),
                username=str(data.get("username") or ""),
                password=str(data.get("password") or ""),
                device_type=str(data.get("device_type") or "cisco_ios"),
                timeout=timeout,
                layout=str(data.get("layout") or "grid"),
                hop_limit=hop_limit,
            )
        except Exception as exc:
            logger.exception("discovery scan %s failed", scan_id)
            store.record_scan_failure(scan_id, str(exc))
            return jsonify({"error": str(exc), "scan_id": scan_id}), 500

        store.record_scan_result(scan_id, result)

        inventory = None
        if topology_id:
            # A scan launched against a named topology writes its devices into
            # ni_devices immediately — that is what "compare with topology"
            # means operationally. A standalone scan does NOT: the operator
            # imports it deliberately from the detail modal, because there is no
            # topology to attribute the devices to.
            inventory = store.import_scan_devices(
                scan_id, topology_id, graph=result.get("graph_json"),
            )

        return jsonify({
            "ok": True,
            "scan_id": scan_id,
            "stats": result.get("stats", {}),
            "inventory": inventory,
        })

    # ── GET /api/discovery/scans/<id> ────────────────────────────────────

    @bp.route("/api/discovery/scans/<scan_id>", methods=["GET"])
    @nc_login_required
    def nc_api_discovery_scan_detail(scan_id):
        """Return one scan with its devices, graph and stats."""
        from tools.network import discovery_store as store

        scan = store.get_scan(scan_id)
        if not scan:
            return jsonify({"error": f"scan not found: {scan_id}"}), 404
        return jsonify(scan)

    # ── GET /api/discovery/scans ─────────────────────────────────────────

    @bp.route("/api/discovery/scans", methods=["GET"])
    @nc_login_required
    def nc_api_discovery_scans():
        """List scans (payload-free rows) — the machine-readable page view."""
        from tools.network import discovery_store as store

        try:
            limit = max(1, min(int(request.args.get("limit", 100)), 500))
        except (TypeError, ValueError):
            limit = 100
        return jsonify({
            "scans": store.list_scans(limit=limit),
            "inventory": store.device_inventory_stats(),
        })

    # ── DELETE /api/discovery/scans/<id> ─────────────────────────────────

    @bp.route("/api/discovery/scans/<scan_id>", methods=["DELETE"])
    @nc_login_required
    def nc_api_discovery_scan_delete(scan_id):
        """Delete a scan and the diffs derived from it.

        Devices already imported into ``ni_devices`` are deliberately kept:
        they were imported by a separate act, and deleting the scan record is
        not a claim that the estate stopped existing.
        """
        from tools.network import discovery_store as store

        result = store.delete_scan(scan_id)
        if not result.get("ok"):
            return jsonify(result), 404
        return jsonify(result)

    # ── POST /api/discovery/scans/<id>/import/<topology_id> ──────────────

    @bp.route("/api/discovery/scans/<scan_id>/import/<topology_id>", methods=["POST"])
    @nc_login_required
    def nc_api_discovery_import(scan_id, topology_id):
        """Import a scan into a topology (``merge`` default, or ``replace``)."""
        from tools.network import discovery_store as store

        data = request.get_json(force=True, silent=True) or {}
        mode = str(data.get("mode") or "merge").lower()
        if mode not in ("merge", "replace"):
            return jsonify({"error": f"unknown mode {mode!r}; expected merge|replace"}), 400

        result = store.import_to_topology(scan_id, topology_id, mode=mode)
        if result.get("error"):
            return jsonify(result), 404
        return jsonify(result)

    # ── POST /api/discovery/diff ─────────────────────────────────────────

    @bp.route("/api/discovery/diff", methods=["POST"])
    @nc_login_required
    def nc_api_discovery_diff():
        """As-designed vs as-built: diff a scan against a topology, and store it."""
        from tools.network import discovery_store as store

        data = request.get_json(force=True, silent=True) or {}
        scan_id = data.get("scan_id")
        topology_id = data.get("topology_id")
        if not scan_id or not topology_id:
            return jsonify({"error": "scan_id and topology_id are both required"}), 400

        result = store.run_diff(str(scan_id), str(topology_id))
        if result.get("error"):
            return jsonify(result), 404
        return jsonify(result)

    # ── GET /api/discovery/inventory ─────────────────────────────────────

    @bp.route("/api/discovery/inventory", methods=["GET"])
    @nc_login_required
    def nc_api_discovery_inventory():
        """ni_devices counts BY PROVENANCE.

        Reports `measurable: false` rather than a zero when the table cannot be
        read — "nothing is deployed" and "I could not look" justify opposite
        decisions and must never render the same.
        """
        from tools.network import discovery_store as store

        return jsonify(store.device_inventory_stats())
