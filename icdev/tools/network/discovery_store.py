# CUI // SP-CTI — ICDEV Network Canvas Auto-Discovery Persistence
"""ICDEV™ Network Canvas — discovery scan persistence and inventory seam.

``tools/network/discovery.py`` has always known how to SCAN a network and how
to DIFF the result against a design. Nothing persisted either, and nothing
turned a discovered device into an inventory row — so the engine was a CLI with
no consumers, ``/network/discovery`` called five endpoints that were defined
nowhere, ``nc_discovery_scans`` and ``nc_discovery_diffs`` held 0 rows, and
``ni_devices`` held 0 rows while four surfaces read it (rmf-disc-02).

This module is the seam between them, and it is the ONLY writer of
``ni_devices`` on the discovery path — the routes and the ``asset_discovery``
genesis reflex both call it rather than writing their own INSERT.

PROVENANCE IS PART OF THE WRITE, NEVER AN AFTERTHOUGHT
------------------------------------------------------
Every device row records ``ni_devices.source`` (migration 20260902210030).
That table is declared ``evidence_kind: inventory`` at the best precedence in
``args/docmod/inventory_feeds.yaml`` — an OBSERVED DEPLOYED ESTATE, which
outranks every design topology. A row this module writes is therefore one of:

    discovery   a scan reached the host and the host ANSWERED. Real evidence.
    synthetic   fabricated demo data from SyntheticDataEngine. NOT evidence,
                and excluded by name from that feed.

Those two never share a code path that could let one wear the other's label:
``import_scan_devices`` can only write ``discovery`` and
``seed_synthetic_devices`` can only write ``synthetic``, because each passes its
own literal.

WHAT AN EMPTY RESULT MEANS
--------------------------
A scan that reaches nothing returns ``devices_discovered: 0`` and is recorded
``completed`` with that number, NOT ``failed``. Those are different facts: the
first says the targets did not answer, the second says the scanner broke. A
scan that raised is ``failed`` and carries the exception in ``error``.
"""
from __future__ import annotations

import json
import uuid as _uuid
from datetime import datetime, timezone
from typing import Any

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.network.discovery_store")

#: Provenance labels this module writes into ``ni_devices.source``. The column
#: carries no CHECK constraint (a new writer must be able to name itself
#: without a migration), so these are the repo-side statement of what the
#: DISCOVERY path may write — never a claim about what the column can hold.
SOURCE_DISCOVERY = "discovery"
SOURCE_SYNTHETIC = "synthetic"

#: Scan lifecycle. `completed` with zero devices is a real, successful answer.
VALID_STATUSES = ("pending", "running", "completed", "failed")

#: A passive scan touches nothing on the wire beyond ICMP. `snmp` and `ssh`
#: both authenticate against live infrastructure, so the reflex refuses them
#: unless a deployment explicitly opts in — see
#: tools/genesis/reflexes/asset_discovery.py.
PASSIVE_METHODS = ("ping",)
ACTIVE_METHODS = ("snmp", "ssh")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _conn():
    """Open the network-canvas connection.

    ``tools.network.db.init_db.get_connection`` is the canvas-connection
    pattern: it clears the security context, so the global RLS predicate is not
    attached to tables that have no ``tenant_id``. Every other NDC route module
    uses it; do not substitute ``tools.db.storage.get_connection`` here.
    """
    from tools.network.db.init_db import get_connection
    return get_connection()


def _row_to_dict(row) -> dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    try:
        return dict(row)
    except Exception:
        return {}


def _loads(raw, default):
    """Parse a JSON column, returning *default* for anything unreadable.

    Never raises: a scan row whose payload is corrupt must still LIST, so a
    human can see it and delete it, rather than taking the whole page down.
    """
    if raw is None or raw == "":
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except Exception:
        return default


def _iso(value) -> str:
    """Render a timestamp column as a string.

    PostgreSQL hands back a ``datetime`` where SQLite hands back TEXT, and the
    page slices this value as a string. Formatting at the seam keeps that
    difference out of every caller.
    """
    if value is None:
        return ""
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


# ── Scan CRUD ────────────────────────────────────────────────────────────────

def create_scan(
    name: str,
    targets: list[str],
    method: str = "snmp",
    topology_id: str | None = None,
    config: dict[str, Any] | None = None,
    conn=None,
) -> str:
    """Insert a `running` scan row and return its id.

    The row exists BEFORE the scan runs, so a scan that hangs or whose process
    dies is visible as `running` rather than leaving no trace at all.
    ``config`` never carries a credential — see ``_safe_config``.
    """
    own = conn is None
    conn = conn or _conn()
    scan_id = str(_uuid.uuid4())
    try:
        conn.execute(
            "INSERT INTO nc_discovery_scans "
            "(id, topology_id, name, method, targets, config_json, status, "
            " devices_json, graph_json, stats_json, started_at, created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                scan_id,
                topology_id or None,
                name or "Discovery Scan",
                method,
                json.dumps(list(targets or [])),
                json.dumps(_safe_config(config or {})),
                "running",
                "[]",
                json.dumps({"nodes": [], "edges": []}),
                "{}",
                _now(),
                _now(),
            ),
        )
        conn.commit()
    finally:
        if own:
            conn.close()
    return scan_id


def _safe_config(config: dict[str, Any]) -> dict[str, Any]:
    """Strip secrets before a scan config is persisted.

    ``nc_discovery_scans.config_json`` is read back by the scan-detail endpoint
    and rendered. An SNMP community string and an SSH password are credentials
    for live infrastructure; storing either would put them in a database, in
    every backup of it, and on a page. The scan still records THAT it
    authenticated (``auth``) so a reader can tell an unauthenticated sweep from
    an authenticated one — the account name is operationally useful and is not
    the secret, so it is kept.
    """
    out = {
        k: v for k, v in config.items()
        if k not in ("community", "password", "username", "credential_ref")
    }
    if config.get("community"):
        out["auth"] = "community"
    if config.get("password"):
        out["auth"] = "password"
    if config.get("username"):
        out["username"] = config["username"]
    return out


def record_scan_result(scan_id: str, result: dict[str, Any], conn=None) -> None:
    """Mark a scan `completed` and store its devices, graph and stats."""
    own = conn is None
    conn = conn or _conn()
    try:
        conn.execute(
            "UPDATE nc_discovery_scans SET status=%s, devices_json=%s, "
            "graph_json=%s, stats_json=%s, completed_at=%s WHERE id=%s",
            (
                "completed",
                json.dumps(result.get("devices", [])),
                json.dumps(result.get("graph_json", {"nodes": [], "edges": []})),
                json.dumps(result.get("stats", {})),
                _now(),
                scan_id,
            ),
        )
        conn.commit()
    finally:
        if own:
            conn.close()


def record_scan_failure(scan_id: str, error: str, conn=None) -> None:
    """Mark a scan `failed`. Reserved for a scanner that RAISED.

    A scan that ran and found nothing is `completed` with 0 devices — calling
    that failure would make "the targets did not answer" indistinguishable from
    "the scanner is broken", which are opposite repairs.
    """
    own = conn is None
    conn = conn or _conn()
    try:
        conn.execute(
            "UPDATE nc_discovery_scans SET status=%s, error=%s, completed_at=%s "
            "WHERE id=%s",
            ("failed", str(error)[:2000], _now(), scan_id),
        )
        conn.commit()
    finally:
        if own:
            conn.close()


def list_scans(limit: int = 100, conn=None) -> list[dict[str, Any]]:
    """Return scan rows newest first, WITHOUT the heavy JSON payloads.

    ``devices_json``/``graph_json`` can each be megabytes on a large sweep and
    the history table renders neither — only the counts, which come from
    ``stats_json``. Selecting them here would make the page cost grow with the
    size of every scan ever run.
    """
    own = conn is None
    conn = conn or _conn()
    try:
        rows = conn.execute(
            "SELECT id, topology_id, name, method, targets, status, stats_json, "
            "error, started_at, completed_at, created_at "
            "FROM nc_discovery_scans ORDER BY created_at DESC LIMIT %s",
            (int(limit),),
        ).fetchall()
    except Exception as exc:
        logger.warning("discovery_store.list_scans failed: %s", exc)
        return []
    finally:
        if own:
            conn.close()

    out: list[dict[str, Any]] = []
    for row in rows:
        d = _row_to_dict(row)
        stats = _loads(d.get("stats_json"), {})
        d["targets"] = _loads(d.get("targets"), [])
        d["stats"] = stats
        # Surfaced flat so the history table need not re-parse JSON per row.
        # None, never 0, when the scan recorded no stats: a scan still running
        # has not discovered zero devices, it has not reported yet.
        d["devices_discovered"] = stats.get("devices_discovered")
        d["nodes_generated"] = stats.get("nodes_generated")
        d["edges_generated"] = stats.get("edges_generated")
        for key in ("started_at", "completed_at", "created_at"):
            d[key] = _iso(d.get(key))
        out.append(d)
    return out


def get_scan(scan_id: str, conn=None) -> dict[str, Any] | None:
    """Return one full scan row with its JSON columns parsed, or None."""
    own = conn is None
    conn = conn or _conn()
    try:
        row = conn.execute(
            "SELECT * FROM nc_discovery_scans WHERE id=%s", (scan_id,)
        ).fetchone()
    finally:
        if own:
            conn.close()
    if not row:
        return None
    d = _row_to_dict(row)
    d["targets"] = _loads(d.get("targets"), [])
    d["config"] = _loads(d.get("config_json"), {})
    d["devices_json"] = _loads(d.get("devices_json"), [])
    d["graph_json"] = _loads(d.get("graph_json"), {"nodes": [], "edges": []})
    d["stats_json"] = _loads(d.get("stats_json"), {})
    for key in ("started_at", "completed_at", "created_at"):
        d[key] = _iso(d.get(key))
    return d


def delete_scan(scan_id: str, conn=None) -> dict[str, Any]:
    """Delete a scan and the diffs computed from it.

    The diffs go first: ``nc_discovery_diffs.scan_id`` references the scan, and
    a diff whose scan is gone can no longer be re-derived or explained. Devices
    already imported into ``ni_devices`` are NOT deleted — they were imported by
    a separate, deliberate act, and deleting a scan record is not a statement
    that the estate stopped existing.
    """
    own = conn is None
    conn = conn or _conn()
    try:
        existing = conn.execute(
            "SELECT id FROM nc_discovery_scans WHERE id=%s", (scan_id,)
        ).fetchone()
        if not existing:
            return {"ok": False, "error": f"scan not found: {scan_id}"}
        conn.execute("DELETE FROM nc_discovery_diffs WHERE scan_id=%s", (scan_id,))
        conn.execute("DELETE FROM nc_discovery_scans WHERE id=%s", (scan_id,))
        conn.commit()
    finally:
        if own:
            conn.close()
    return {"ok": True, "id": scan_id}


# ── ni_devices: the inventory write ──────────────────────────────────────────

def _device_rows_from_graph(graph: dict[str, Any]) -> list[dict[str, Any]]:
    """Project discovery graph nodes onto the ni_devices field set.

    Reads the GRAPH rather than the raw device records because
    ``build_graph_json`` has already resolved neighbour stubs into nodes — a
    switch that only ever appeared as a CDP neighbour of something else is a
    real device on the estate and must reach the inventory, but it has no
    discovery record of its own.
    """
    rows: list[dict[str, Any]] = []
    for node in graph.get("nodes", []) or []:
        if not isinstance(node, dict):
            continue
        cfg = node.get("config") or {}
        rows.append({
            "node_id": node.get("id"),
            "label": (
                node.get("label") or cfg.get("hostname")
                or cfg.get("ip_address") or node.get("id")
            ),
            "device_type": node.get("type") or "unknown",
            "vendor": cfg.get("vendor") or None,
            # A discovery record carries sysDescr, not a catalogue model number.
            # `platform` is what CDP/LLDP reports and is the closest thing to a
            # model the wire actually offers; anything else would be invented.
            "model": cfg.get("platform") or None,
            "ip": cfg.get("ip_address") or "",
            "sys_descr": cfg.get("sys_descr") or "",
            "discovery_method": cfg.get("discovery_method") or "",
        })
    return [r for r in rows if r["node_id"]]


def import_scan_devices(
    scan_id: str,
    topology_id: str,
    graph: dict[str, Any] | None = None,
    conn=None,
) -> dict[str, Any]:
    """Upsert a scan's devices into ``ni_devices`` under *topology_id*.

    Writes ``source='discovery'`` and nothing else — this function CANNOT write
    any other provenance label, which is what keeps a synthetic seed from ever
    being mistaken for an observation.

    Idempotent: ``upsert_device`` keys on (topology_id, node_id), so re-running
    an import updates rather than duplicating.
    """
    from tools.network.device_manager import upsert_device

    own = conn is None
    conn = conn or _conn()
    created = updated = 0
    errors: list[str] = []
    try:
        for row in _device_rows_from_graph(graph or {}):
            try:
                res = upsert_device(
                    topology_id,
                    row["node_id"],
                    conn=conn,
                    label=row["label"],
                    device_type=row["device_type"],
                    vendor=row["vendor"],
                    model=row["model"],
                    source=SOURCE_DISCOVERY,
                    properties_json=json.dumps({
                        "ip_address": row["ip"],
                        "sys_descr": row["sys_descr"],
                        "discovery_method": row["discovery_method"],
                        "discovery_scan_id": scan_id,
                    }),
                )
                if res.get("action") == "created":
                    created += 1
                else:
                    updated += 1
            except Exception as exc:
                errors.append(f"{row['node_id']}: {exc}")
    finally:
        if own:
            conn.close()
    return {
        "ok": not errors,
        "scan_id": scan_id,
        "topology_id": topology_id,
        "devices_created": created,
        "devices_updated": updated,
        "errors": errors,
    }


def seed_synthetic_devices(
    topology_id: str,
    count: int = 24,
    seed: int | None = 42,
    conn=None,
) -> dict[str, Any]:
    """Seed ``ni_devices`` with fabricated demo devices, labelled as such.

    THE ROWS ARE NOT EVIDENCE. Each carries ``source='synthetic'``, which
    ``args/docmod/inventory_feeds.yaml`` excludes BY NAME from the de-facto
    standard learner's ``inventory`` feed — so an EOL table, an inventory page
    and an attack-surface map get something to render while the one engine that
    RANKS this table as an observed estate never sees them.

    Deterministic for a given ``seed`` and idempotent for a given
    ``topology_id``: node ids are derived from the record index, so re-running
    updates the same rows instead of doubling the fleet.
    """
    from icdev.tools.showcase.synthetic_data_engine import SyntheticDataEngine

    from tools.network.device_manager import upsert_device

    own = conn is None
    conn = conn or _conn()
    created = updated = 0
    errors: list[str] = []
    try:
        records = SyntheticDataEngine(seed=seed).generate("network_devices", int(count))
        for idx, dev in enumerate(records):
            node_id = f"synthetic-{idx:04d}"
            try:
                res = upsert_device(
                    topology_id,
                    node_id,
                    conn=conn,
                    label=dev["label"],
                    device_type=dev["device_type"],
                    vendor=dev["vendor"],
                    model=dev["model"],
                    firmware_version=dev["firmware_version"],
                    eol_date=dev["eol_date"],
                    eos_date=dev["eos_date"],
                    site=dev["site"],
                    rack_location=dev["rack"],
                    replacement_cost=dev["replacement_cost"],
                    source=SOURCE_SYNTHETIC,
                    notes="Synthetic demo device — fabricated, not an observed asset.",
                    properties_json=json.dumps({
                        "ip_address": dev["ip"],
                        "criticality": dev["criticality"],
                        "synthetic": True,
                    }),
                )
                if res.get("action") == "created":
                    created += 1
                else:
                    updated += 1
            except Exception as exc:
                errors.append(f"{node_id}: {exc}")
    finally:
        if own:
            conn.close()
    return {
        "ok": not errors,
        "topology_id": topology_id,
        "source": SOURCE_SYNTHETIC,
        "devices_created": created,
        "devices_updated": updated,
        "errors": errors,
    }


def ensure_demo_topology(
    topology_id: str = "topo-synthetic-demo-estate",
    name: str = "Synthetic Demo Estate (FABRICATED — not a real network)",
    conn=None,
) -> dict[str, Any]:
    """Create the topology synthetic devices hang off, if it does not exist.

    ``ni_devices.topology_id`` references ``topologies(id)``, so fabricated
    devices need a topology of their own. They must NEVER be attached to a real
    design: a demo fleet inside somebody's Campus LAN diagram is a corruption of
    that diagram, and every inventory query scoped to it would return invented
    hardware. The name says what the rows are in the one place a human sees them.

    Idempotent, and it never touches an existing row — a topology already at
    this id keeps whatever it holds.
    """
    own = conn is None
    conn = conn or _conn()
    try:
        existing = conn.execute(
            "SELECT id FROM topologies WHERE id=%s", (topology_id,)
        ).fetchone()
        if existing:
            return {"id": topology_id, "action": "exists"}
        conn.execute(
            "INSERT INTO topologies (id, name, description, graph_json, "
            "classification, created_at, updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (
                topology_id,
                name,
                "Holder for synthetic demo devices seeded by "
                "tools/network/discovery_store.py --seed-synthetic. Every device "
                "under it carries ni_devices.source='synthetic' and is excluded "
                "from the de-facto standard learner's inventory feed.",
                json.dumps({"nodes": [], "edges": []}),
                "CUI // SP-CTI",
                _now(),
                _now(),
            ),
        )
        conn.commit()
    finally:
        if own:
            conn.close()
    return {"id": topology_id, "action": "created"}


def device_inventory_stats(conn=None) -> dict[str, Any]:
    """Count ni_devices rows BY PROVENANCE.

    One number would answer "is the inventory populated" and hide the only
    question that matters about it — how much of it was OBSERVED. ``by_source``
    keys a NULL provenance as ``unknown`` rather than folding it into either
    side: rows written before migration 20260902210030 stated no origin, and
    guessing one for them would be the fabrication that column exists to stop.
    """
    own = conn is None
    conn = conn or _conn()
    try:
        rows = conn.execute(
            "SELECT source, COUNT(*) AS n FROM ni_devices GROUP BY source"
        ).fetchall()
    except Exception as exc:
        logger.warning("discovery_store.device_inventory_stats failed: %s", exc)
        # UNMEASURABLE, never a clean zero: an unreadable table says nothing
        # about how many devices exist.
        return {
            "total": None, "by_source": None, "observed": None,
            "synthetic": None, "measurable": False, "error": str(exc),
        }
    finally:
        if own:
            conn.close()

    by_source: dict[str, int] = {}
    for row in rows:
        d = _row_to_dict(row)
        key = d.get("source") or "unknown"
        by_source[key] = int(d.get("n") or 0)
    return {
        "total": sum(by_source.values()),
        "by_source": by_source,
        "observed": by_source.get(SOURCE_DISCOVERY, 0),
        "synthetic": by_source.get(SOURCE_SYNTHETIC, 0),
        "measurable": True,
    }


# ── Topology import + diff ───────────────────────────────────────────────────

def import_to_topology(
    scan_id: str,
    topology_id: str,
    mode: str = "merge",
    conn=None,
) -> dict[str, Any]:
    """Write a scan's graph into a topology, and its devices into ni_devices.

    ``mode``:
        merge    add discovered nodes/edges the topology does not already have,
                 matched on hostname/IP the same way ``diff_topologies`` matches
                 them. An existing node is LEFT ALONE — a merge that rewrote
                 designed nodes from live data would silently destroy the design
                 half of an as-designed-vs-as-built comparison.
        replace  the topology's graph BECOMES the discovered graph.

    ``replace`` is destructive and says so: the caller's own confirmation is the
    gate, and the prior graph is recoverable only from ``nc_versions`` if a
    version was saved.
    """
    from tools.network.discovery import diff_topologies

    own = conn is None
    conn = conn or _conn()
    try:
        scan = get_scan(scan_id, conn=conn)
        if not scan:
            return {"error": f"scan not found: {scan_id}"}
        topo_row = conn.execute(
            "SELECT id, name, graph_json FROM topologies WHERE id=%s", (topology_id,)
        ).fetchone()
        if not topo_row:
            return {"error": f"topology not found: {topology_id}"}

        discovered = scan.get("graph_json") or {"nodes": [], "edges": []}
        existing = _loads(
            _row_to_dict(topo_row).get("graph_json"), {"nodes": [], "edges": []}
        )

        if mode == "replace":
            merged = {
                "nodes": list(discovered.get("nodes", []) or []),
                "edges": list(discovered.get("edges", []) or []),
            }
            added_nodes = len(merged["nodes"])
            added_edges = len(merged["edges"])
        else:
            # Reuse the diff to decide what is genuinely new, so "already in the
            # design" means exactly what the diff panel says it means. A second
            # matching rule here would let the two disagree.
            diff = diff_topologies(existing, discovered)
            new_nodes = list(diff.get("discovered_only", []))
            existing_nodes = list(existing.get("nodes", []) or [])
            existing_edges = list(existing.get("edges", []) or [])
            reachable = {
                n.get("id") for n in existing_nodes if isinstance(n, dict)
            } | {n.get("id") for n in new_nodes if isinstance(n, dict)}

            merged_edges = list(existing_edges)
            seen = {
                tuple(sorted([str(e.get("source")), str(e.get("target"))]))
                for e in merged_edges if isinstance(e, dict)
            }
            for edge in discovered.get("edges", []) or []:
                if not isinstance(edge, dict):
                    continue
                key = tuple(sorted([str(edge.get("source")), str(edge.get("target"))]))
                # An edge whose endpoints are not BOTH in the merged node set
                # would render as a dangling link, so it is dropped rather than
                # silently creating a node the merge decided not to add.
                if (key in seen
                        or edge.get("source") not in reachable
                        or edge.get("target") not in reachable):
                    continue
                seen.add(key)
                merged_edges.append(edge)

            merged = {"nodes": existing_nodes + new_nodes, "edges": merged_edges}
            added_nodes = len(new_nodes)
            added_edges = len(merged_edges) - len(existing_edges)

        conn.execute(
            "UPDATE topologies SET graph_json=%s, updated_at=%s WHERE id=%s",
            (json.dumps(merged), _now(), topology_id),
        )
        conn.commit()

        inv = import_scan_devices(scan_id, topology_id, graph=discovered, conn=conn)
    finally:
        if own:
            conn.close()

    return {
        "ok": True,
        "mode": mode,
        "scan_id": scan_id,
        "topology_id": topology_id,
        # The page renders these two as "Imported N nodes, M edges".
        "nodes": added_nodes,
        "edges": added_edges,
        "total_nodes": len(merged.get("nodes", [])),
        "total_edges": len(merged.get("edges", [])),
        "inventory": inv,
    }


def run_diff(scan_id: str, topology_id: str, conn=None) -> dict[str, Any]:
    """Diff a scan against a topology and persist the result.

    Returns the full ``diff_topologies`` payload plus the persisted diff id.
    Persistence is best-effort AND REPORTED: a diff that could not be stored is
    still a valid answer to the question the caller asked, and refusing to
    return it would make a storage fault look like "no drift".
    """
    from tools.network.discovery import diff_topologies

    own = conn is None
    conn = conn or _conn()
    try:
        scan = get_scan(scan_id, conn=conn)
        if not scan:
            return {"error": f"scan not found: {scan_id}"}
        topo_row = conn.execute(
            "SELECT id, graph_json FROM topologies WHERE id=%s", (topology_id,)
        ).fetchone()
        if not topo_row:
            return {"error": f"topology not found: {topology_id}"}

        designed = _loads(
            _row_to_dict(topo_row).get("graph_json"), {"nodes": [], "edges": []}
        )
        discovered = scan.get("graph_json") or {"nodes": [], "edges": []}
        result = diff_topologies(designed, discovered)
        summary = result.get("summary", {})

        diff_id = str(_uuid.uuid4())
        try:
            conn.execute(
                "INSERT INTO nc_discovery_diffs "
                "(id, scan_id, topology_id, diff_json, drift_score, matched, "
                " designed_only, discovered_only, with_drift, created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    diff_id, scan_id, topology_id, json.dumps(result),
                    float(summary.get("drift_score", 0) or 0),
                    int(summary.get("matched", 0) or 0),
                    int(summary.get("designed_only", 0) or 0),
                    int(summary.get("discovered_only", 0) or 0),
                    int(summary.get("with_drift", 0) or 0),
                    _now(),
                ),
            )
            conn.commit()
            result["diff_id"] = diff_id
            result["persisted"] = True
        except Exception as exc:
            logger.warning("discovery_store.run_diff: persist failed: %s", exc)
            result["diff_id"] = None
            result["persisted"] = False
            result["persist_error"] = str(exc)
    finally:
        if own:
            conn.close()
    return result


def list_topologies(conn=None) -> list[dict[str, Any]]:
    """Topology id/name pairs for the page's two selects."""
    own = conn is None
    conn = conn or _conn()
    try:
        rows = conn.execute("SELECT id, name FROM topologies ORDER BY name").fetchall()
    except Exception as exc:
        logger.warning("discovery_store.list_topologies failed: %s", exc)
        return []
    finally:
        if own:
            conn.close()
    return [_row_to_dict(r) for r in rows]


# ── CLI ──────────────────────────────────────────────────────────────────────

def _cli() -> int:
    """Inspect the discovery store and seed the demo inventory.

    The synthetic seed is HERE and not in the ``asset_discovery`` reflex on
    purpose. One deliberate operator command is a fixture; a daemon doing the
    same thing every 24 hours is a data source, and this platform's de-facto
    standard learner ranks ``ni_devices`` as an observed deployed estate.
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="ICDEV Network Canvas — discovery scan store and inventory seam",
    )
    parser.add_argument("--stats", action="store_true",
                        help="ni_devices counts by provenance + scan history summary")
    parser.add_argument("--list-scans", action="store_true", help="List recorded scans")
    parser.add_argument("--scan", metavar="SCAN_ID", help="Show one scan in full")
    parser.add_argument("--delete-scan", metavar="SCAN_ID", help="Delete a scan and its diffs")
    parser.add_argument("--seed-synthetic", action="store_true",
                        help="Seed ni_devices with FABRICATED demo devices "
                             "(source='synthetic'; excluded from the de-facto learner)")
    parser.add_argument("--topology-id", help="Topology the seeded devices belong to")
    parser.add_argument("--count", type=int, default=24, help="How many synthetic devices")
    parser.add_argument("--seed", type=int, default=42, help="RNG seed (determinism)")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    def _emit(payload):
        print(json.dumps(payload, indent=2, default=str))

    if args.seed_synthetic:
        # Defaults to a topology of its own, created on demand. Passing
        # --topology-id explicitly is supported, and pointing it at a REAL
        # design is the caller's decision to own: fabricated devices inside a
        # real diagram corrupt every inventory query scoped to it.
        topology_id = args.topology_id or "topo-synthetic-demo-estate"
        topo = ensure_demo_topology(topology_id) if not args.topology_id else {
            "id": topology_id, "action": "caller_supplied"
        }
        result = seed_synthetic_devices(topology_id, count=args.count, seed=args.seed)
        result["topology"] = topo
        result["inventory"] = device_inventory_stats()
        _emit(result)
        return 0 if result.get("ok") else 1

    if args.delete_scan:
        _emit(delete_scan(args.delete_scan))
        return 0

    if args.scan:
        scan = get_scan(args.scan)
        if not scan:
            _emit({"error": f"scan not found: {args.scan}"})
            return 1
        _emit(scan)
        return 0

    if args.list_scans:
        _emit({"scans": list_scans()})
        return 0

    # Default view.
    scans = list_scans(limit=25)
    _emit({
        "inventory": device_inventory_stats(),
        "topologies": len(list_topologies()),
        "scans_recorded": len(scans),
        "scans_completed": sum(1 for s in scans if s.get("status") == "completed"),
        "scans_failed": sum(1 for s in scans if s.get("status") == "failed"),
    })
    return 0


if __name__ == "__main__":
    # Load THIS repo's .env so a direct CLI run reads the same database the
    # dashboard does. Repo root via __file__, never cwd.
    try:
        from pathlib import Path as _EnvPath

        from dotenv import load_dotenv as _load_dotenv
        _load_dotenv(_EnvPath(__file__).resolve().parents[2] / ".env", override=True)
    except ImportError:
        pass
    raise SystemExit(_cli())
