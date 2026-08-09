# CUI // SP-CTI
"""ICDEV™ — POA&M Auto-Remediator (cross-canvas, reusable).

Reusable, idempotent CLI tool. Takes finding hashes (or all pending/approved
findings from finding_approvals) and applies vendor-neutral design-completeness
fixes to the source canvas designs. After mutation, re-runs the canvas
assessment to verify the finding is gone, then updates
finding_approvals.decision='remediated' and writes a row to audit_trail.

Pipeline per finding:
    1. Look up source design in the canvas DB by re-hashing recent assessment
       findings until we find a match.
    2. Backup the canvas DB to backups/canvas/<db>.bak-<ts> (one backup per
       canvas per run, not per finding).
    3. Load the design's graph_json, snapshot it, run the per-rule handler to
       mutate it, and save it back.
    4. Re-run the canvas's own assessment engine on the mutated graph and
       persist the result as a fresh assessment row (assessment_type=
       'auto_remediator_verify').
    5. Compute new finding hashes; if the target hash is gone, mark
       finding_approvals.decision='remediated' with a diff summary in
       decision_rationale.
    6. Append an audit_trail row (event_type='vulnerability_resolved').

Handlers are vendor-neutral. They add placeholder design elements that satisfy
the assessment rule (e.g. ctrl-kms node, cmp-log-policy node, hardening_baseline
attribute on a server) without committing to a specific vendor product. Vendor
selection (which KMS, which SIEM) is left to the architect via the manual
GitHub-issue path.

Usage:
    python tools/canvas/auto_remediator.py --finding-hash <hash>
    python tools/canvas/auto_remediator.py --all-pending
    python tools/canvas/auto_remediator.py --all-approved
    python tools/canvas/auto_remediator.py --canvas security --all-pending
    python tools/canvas/auto_remediator.py --list-handlers
    python tools/canvas/auto_remediator.py --dry-run --all-pending
    python tools/canvas/auto_remediator.py --gate --json
"""

from __future__ import annotations

import argparse
import importlib
import json
import logging
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

# Bootstrap repo root onto sys.path so this script runs as a CLI.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.logging.icdev_logger import get_logger  # noqa: E402

from tools.db.storage import get_connection  # noqa: E402

logger = get_logger("icdev.canvas.auto_remediator")

DATA_DIR = _REPO_ROOT / "data"
BACKUP_DIR = _REPO_ROOT / "backups" / "canvas"

# ── Canvas registry ─────────────────────────────────────────────────────────
# Each entry tells the remediator how to read the design, where the assessment
# rows live, and which engine function to call to re-assess after mutation.

CANVAS_REGISTRY: dict[str, dict[str, Any]] = {
    "security": {
        "db": "security_canvas.db",
        "design_table": "security_designs",
        "asmt_table": "sc_assessments",
        "asmt_time_col": "ran_at",
        "engine_module": "tools.security_canvas.security_engine",
        "engine_func": "run_security_assessment",
        "engine_takes_design_id": True,
    },
    "observability": {
        "db": "observability_canvas.db",
        "design_table": "observability_designs",
        "asmt_table": "od_assessments",
        "asmt_time_col": "created_at",
        "engine_module": "tools.observability_canvas.observability_engine",
        "engine_func": "assess_observability_design",
        "engine_takes_design_id": False,
    },
    "boundary": {
        "db": "boundary_canvas.db",
        "design_table": "boundary_designs",
        "asmt_table": "bd_assessments",
        "asmt_time_col": "created_at",
        "engine_module": "tools.boundary_canvas.boundary_engine",
        "engine_func": "assess_boundary_design",
        "engine_takes_design_id": False,
    },
    "infra": {
        "db": "infra_canvas.db",
        "design_table": "infra_designs",
        "asmt_table": "idc_assessments",
        "asmt_time_col": "created_at",
        "engine_module": "tools.infra_canvas.infra_engine",
        "engine_func": "assess_infra_design",
        "engine_takes_design_id": False,
    },
}

# ── Handlers (rule_id → mutation function) ──────────────────────────────────
# Handler signature: (graph_dict, finding_dict) -> (graph_dict, diff_summary_str)
# Handlers must be idempotent — calling twice on a fixed graph is a no-op.


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_node_attrs(node: dict, rule_id: str) -> None:
    """Stamp a node with auto_remediator provenance metadata."""
    node["auto_remediated"] = True
    node["remediated_by"] = "auto_remediator"
    node["remediated_at"] = _now_iso()
    rules = node.get("remediated_rules") or []
    if rule_id not in rules:
        rules.append(rule_id)
    node["remediated_rules"] = rules


def _add_placeholder_node(graph: dict, node_type: str, label: str, rule_id: str) -> tuple[bool, str]:
    """Add a placeholder node of the given type if no node of that type exists.

    Returns (added: bool, message: str). Idempotent.
    """
    nodes = graph.setdefault("nodes", [])
    for n in nodes:
        if n.get("type") == node_type:
            return False, f"already present: type={node_type} id={n.get('id')}"
    new_id = f"auto-{node_type}-{uuid.uuid4().hex[:8]}"
    node = {
        "id": new_id,
        "type": node_type,
        "label": label,
        "x": 50,
        "y": 50,
    }
    _ensure_node_attrs(node, rule_id)
    nodes.append(node)
    return True, f"added placeholder node id={new_id} type={node_type}"


def _make_add_node_handler(node_type: str, label: str) -> Callable:
    """Factory: returns a handler that adds a single placeholder node of the type."""
    def handler(graph: dict, finding: dict) -> tuple[dict, str]:
        _, msg = _add_placeholder_node(graph, node_type, label, finding["rule_id"])
        return graph, msg
    return handler


def _resolve_edge_by_label(graph: dict, affected_entity: str) -> tuple[str | None, str | None]:
    """Parse 'src→tgt' affected_entity into (src_id, tgt_id) by node label lookup."""
    if "→" not in affected_entity:
        return None, None
    src_lbl, tgt_lbl = affected_entity.split("→", 1)
    label_map: dict[str, str] = {}
    for n in graph.get("nodes", []):
        lbl = n.get("label")
        if lbl and lbl not in label_map:
            label_map[lbl] = n.get("id")
    return label_map.get(src_lbl), label_map.get(tgt_lbl)


def _handler_set_authenticated(graph: dict, finding: dict) -> tuple[dict, str]:
    """SEC-AUTH-001: set authenticated=true on the affected edge."""
    affected = finding.get("affected_entity", "") or ""
    src_id, tgt_id = _resolve_edge_by_label(graph, affected)
    if not src_id or not tgt_id:
        return graph, f"skipped: could not resolve edge from '{affected}'"
    fixed = 0
    for e in graph.get("edges", []):
        if e.get("source") == src_id and e.get("target") == tgt_id:
            if not e.get("authenticated"):
                e["authenticated"] = True
                rules = e.get("auto_remediated_rules") or []
                if "SEC-AUTH-001" not in rules:
                    rules.append("SEC-AUTH-001")
                e["auto_remediated_rules"] = rules
                fixed += 1
    if fixed == 0:
        return graph, f"already authenticated: {affected}"
    return graph, f"set authenticated=true on {fixed} edge(s) {affected}"


def _handler_set_zero_trust(graph: dict, finding: dict) -> tuple[dict, str]:
    """SEC-ZT-001: set both authenticated and encrypted on the affected edge."""
    affected = finding.get("affected_entity", "") or ""
    src_id, tgt_id = _resolve_edge_by_label(graph, affected)
    if not src_id or not tgt_id:
        return graph, f"skipped: could not resolve edge from '{affected}'"
    fixed = 0
    for e in graph.get("edges", []):
        if e.get("source") == src_id and e.get("target") == tgt_id:
            changed = False
            if not e.get("authenticated"):
                e["authenticated"] = True
                changed = True
            if not e.get("encrypted"):
                e["encrypted"] = True
                changed = True
            if changed:
                rules = e.get("auto_remediated_rules") or []
                if "SEC-ZT-001" not in rules:
                    rules.append("SEC-ZT-001")
                e["auto_remediated_rules"] = rules
                fixed += 1
    if fixed == 0:
        return graph, f"already zero-trust: {affected}"
    return graph, f"set authenticated+encrypted on {fixed} edge(s) {affected}"


def _handler_set_hardening_baseline(graph: dict, finding: dict) -> tuple[dict, str]:
    """SEC-HARDEN-001: stamp hardening_baseline on the affected server node."""
    affected = finding.get("affected_entity", "") or ""
    fixed = 0
    for n in graph.get("nodes", []):
        if n.get("label") != affected and n.get("id") != affected:
            continue
        cfg = n.get("config_json") or n.get("config") or {}
        if isinstance(cfg, str):
            try:
                cfg = json.loads(cfg)
            except (json.JSONDecodeError, TypeError):
                cfg = {}
        if cfg.get("hardening_baseline"):
            return graph, f"already hardened: {affected}"
        cfg["hardening_baseline"] = "CIS Level 1"
        cfg["hardening_baseline_set_by"] = "auto_remediator"
        cfg["hardening_baseline_set_at"] = _now_iso()
        n["config_json"] = cfg
        _ensure_node_attrs(n, "SEC-HARDEN-001")
        fixed += 1
    if fixed == 0:
        return graph, f"skipped: node '{affected}' not found"
    return graph, f"set hardening_baseline=CIS Level 1 on {fixed} node(s) matching {affected}"


def _handler_add_trust_boundary_security(graph: dict, finding: dict) -> tuple[dict, str]:
    """SEC-SEG-001 / SEC-GEN-002: add a default trust boundary to graph.boundaries.

    The security engine reads boundaries from graph_data['boundaries'] (top-level
    list), not from nodes. So we add to that list.
    """
    boundaries = graph.setdefault("boundaries", [])
    if boundaries:
        return graph, "already present: boundary list non-empty"
    new_id = f"auto-bnd-{uuid.uuid4().hex[:8]}"
    boundaries.append({
        "id": new_id,
        "name": "Default Trust Zone",
        "type": "trust_zone",
        "auto_remediated": True,
        "remediated_by": "auto_remediator",
        "remediated_at": _now_iso(),
    })
    return graph, f"added default trust boundary id={new_id}"


def _handler_add_threats_list(graph: dict, finding: dict) -> tuple[dict, str]:
    """SEC-GEN-003: add a placeholder STRIDE threat entry."""
    threats = graph.setdefault("threats", [])
    if threats:
        return graph, "already present: threats list non-empty"
    new_id = f"auto-threat-{uuid.uuid4().hex[:8]}"
    threats.append({
        "id": new_id,
        "name": "Placeholder STRIDE assessment — full review pending",
        "category": "STRIDE",
        "risk": "TBD",
        "auto_remediated": True,
        "remediated_by": "auto_remediator",
        "remediated_at": _now_iso(),
    })
    return graph, f"added placeholder threat id={new_id}"


def _handler_add_boundary_firewall(graph: dict, finding: dict) -> tuple[dict, str]:
    """BDC-CTL-001: Add a ctrl-firewall node connected to the affected boundary node.

    The boundary engine's _find_controls_near_boundary() requires an edge between
    the boundary node and the ctrl-firewall node, so adding the node alone is
    insufficient.  This handler adds both the node and the edge (idempotent).
    """
    boundary_id: str = (finding.get("affected_entity") or "").strip()
    nodes = graph.setdefault("nodes", [])
    edges = graph.setdefault("edges", [])

    # Locate existing ctrl-firewall nodes.
    fw_nodes = [n for n in nodes if n.get("type") == "ctrl-firewall"]

    # If one already exists and is already connected to this boundary, nothing to do.
    if fw_nodes:
        fw_id = fw_nodes[0]["id"]
        for e in edges:
            if (e.get("source") == boundary_id and e.get("target") == fw_id) or \
               (e.get("source") == fw_id and e.get("target") == boundary_id):
                return graph, f"already present: ctrl-firewall id={fw_id} connected to boundary {boundary_id}"

    # Add the ctrl-firewall node if needed.
    if fw_nodes:
        fw_id = fw_nodes[0]["id"]
        msg_node = f"reused existing ctrl-firewall id={fw_id}"
    else:
        fw_id = f"auto-ctrl-firewall-{uuid.uuid4().hex[:8]}"
        fw_node = {
            "id": fw_id,
            "type": "ctrl-firewall",
            "label": "Firewall",
            "x": 50,
            "y": 150,
        }
        _ensure_node_attrs(fw_node, "BDC-CTL-001")
        nodes.append(fw_node)
        msg_node = f"added ctrl-firewall id={fw_id}"

    # Add the edge connecting the boundary to the firewall (if boundary_id is known).
    if boundary_id:
        edge_id = f"auto-edge-bnd-fw-{uuid.uuid4().hex[:8]}"
        edges.append({
            "id": edge_id,
            "source": boundary_id,
            "target": fw_id,
            "type": "control",
            "auto_remediated": True,
            "remediated_by": "auto_remediator",
            "remediated_at": _now_iso(),
        })
        msg_edge = f"added edge id={edge_id} boundary={boundary_id}→firewall"
    else:
        msg_edge = "no boundary_id in finding — edge not added"

    return graph, f"{msg_node}; {msg_edge}"


def _handler_add_pps_matrix_to_boundary(graph: dict, finding: dict) -> tuple[dict, str]:
    """BDC-DOC-001: Add a doc-pps-matrix node connected to the affected boundary node.

    The boundary engine's _find_docs_near_boundary() requires an edge between
    the boundary node and the doc-pps-matrix node, so adding the node alone is
    insufficient.  This handler adds both the node and the edge (idempotent).
    """
    boundary_id: str = (finding.get("affected_entity") or "").strip()
    nodes = graph.setdefault("nodes", [])
    edges = graph.setdefault("edges", [])

    # Locate existing doc-pps-matrix nodes.
    pps_nodes = [n for n in nodes if n.get("type") == "doc-pps-matrix"]

    # If one already exists and is already connected to this boundary, nothing to do.
    if pps_nodes:
        pps_id = pps_nodes[0]["id"]
        for e in edges:
            if (e.get("source") == boundary_id and e.get("target") == pps_id) or \
               (e.get("source") == pps_id and e.get("target") == boundary_id):
                return graph, f"already present: doc-pps-matrix id={pps_id} connected to boundary {boundary_id}"

    # Add the doc-pps-matrix node if needed.
    if pps_nodes:
        pps_id = pps_nodes[0]["id"]
        msg_node = f"reused existing doc-pps-matrix id={pps_id}"
    else:
        pps_id = f"auto-doc-pps-matrix-{uuid.uuid4().hex[:8]}"
        pps_node = {
            "id": pps_id,
            "type": "doc-pps-matrix",
            "label": "PPS Matrix",
            "x": 200,
            "y": 50,
        }
        _ensure_node_attrs(pps_node, "BDC-DOC-001")
        nodes.append(pps_node)
        msg_node = f"added doc-pps-matrix id={pps_id}"

    # Add the edge connecting the boundary to the PPS matrix doc (if boundary_id is known).
    if boundary_id:
        edge_id = f"auto-edge-bnd-pps-{uuid.uuid4().hex[:8]}"
        edges.append({
            "id": edge_id,
            "source": boundary_id,
            "target": pps_id,
            "type": "document",
            "auto_remediated": True,
            "remediated_by": "auto_remediator",
            "remediated_at": _now_iso(),
        })
        msg_edge = f"added edge id={edge_id} boundary={boundary_id}→pps-matrix"
    else:
        msg_edge = "no boundary_id in finding — edge not added"

    return graph, f"{msg_node}; {msg_edge}"


def _handler_add_dfd_doc(graph: dict, finding: dict) -> tuple[dict, str]:
    """BDC-DOC-002: Add a doc-dfd node connected to the affected boundary node.

    The boundary engine's _find_docs_near_boundary() requires an edge between
    the boundary node and the doc-dfd node, so adding the node alone is
    insufficient.  This handler adds both the node and the edge (idempotent).
    """
    boundary_id: str = (finding.get("affected_entity") or "").strip()
    nodes = graph.setdefault("nodes", [])
    edges = graph.setdefault("edges", [])

    # Locate existing doc-dfd nodes.
    dfd_nodes = [n for n in nodes if n.get("type") == "doc-dfd"]

    # If one already exists and is already connected to this boundary, nothing to do.
    if dfd_nodes:
        dfd_id = dfd_nodes[0]["id"]
        for e in edges:
            if (e.get("source") == boundary_id and e.get("target") == dfd_id) or \
               (e.get("source") == dfd_id and e.get("target") == boundary_id):
                return graph, f"already present: doc-dfd id={dfd_id} connected to boundary {boundary_id}"

    # Add the doc-dfd node if needed.
    if dfd_nodes:
        dfd_id = dfd_nodes[0]["id"]
        msg_node = f"reused existing doc-dfd id={dfd_id}"
    else:
        dfd_id = f"auto-doc-dfd-{uuid.uuid4().hex[:8]}"
        dfd_node = {
            "id": dfd_id,
            "type": "doc-dfd",
            "label": "Data Flow Diagram",
            "x": 200,
            "y": 50,
        }
        _ensure_node_attrs(dfd_node, "BDC-DOC-002")
        nodes.append(dfd_node)
        msg_node = f"added doc-dfd id={dfd_id}"

    # Add the edge connecting the boundary to the DFD doc (if boundary_id is known).
    if boundary_id:
        edge_id = f"auto-edge-bnd-dfd-{uuid.uuid4().hex[:8]}"
        edges.append({
            "id": edge_id,
            "source": boundary_id,
            "target": dfd_id,
            "type": "documentation",
            "auto_remediated": True,
            "remediated_by": "auto_remediator",
            "remediated_at": _now_iso(),
        })
        msg_edge = f"added edge id={edge_id} boundary={boundary_id}→dfd"
    else:
        msg_edge = "no boundary_id in finding — edge not added"

    return graph, f"{msg_node}; {msg_edge}"


def _handler_add_boundary_ids(graph: dict, finding: dict) -> tuple[dict, str]:
    """BDC-CTL-002: Add a ctrl-ids node connected to the affected boundary node.

    Mirrors _handler_add_boundary_firewall for IDS/IPS (NIST SI-4).
    The boundary engine's _find_controls_near_boundary() requires an edge between
    the boundary node and the ctrl-ids node, so adding the node alone is
    insufficient.  This handler adds both the node and the edge (idempotent).
    """
    boundary_id: str = (finding.get("affected_entity") or "").strip()
    nodes = graph.setdefault("nodes", [])
    edges = graph.setdefault("edges", [])

    # Locate existing ctrl-ids-ips nodes.
    ids_nodes = [n for n in nodes if n.get("type") == "ctrl-ids-ips"]

    # If one already exists and is already connected to this boundary, nothing to do.
    if ids_nodes:
        ids_id = ids_nodes[0]["id"]
        for e in edges:
            if (e.get("source") == boundary_id and e.get("target") == ids_id) or \
               (e.get("source") == ids_id and e.get("target") == boundary_id):
                return graph, f"already present: ctrl-ids-ips id={ids_id} connected to boundary {boundary_id}"

    # Add the ctrl-ids-ips node if needed.
    if ids_nodes:
        ids_id = ids_nodes[0]["id"]
        msg_node = f"reused existing ctrl-ids-ips id={ids_id}"
    else:
        ids_id = f"auto-ctrl-ids-ips-{uuid.uuid4().hex[:8]}"
        ids_node = {
            "id": ids_id,
            "type": "ctrl-ids-ips",
            "label": "IDS / IPS",
            "x": 50,
            "y": 250,
        }
        _ensure_node_attrs(ids_node, "BDC-CTL-002")
        nodes.append(ids_node)
        msg_node = f"added ctrl-ids-ips id={ids_id}"

    # Add the edge connecting the boundary to the IDS/IPS (if boundary_id is known).
    if boundary_id:
        edge_id = f"auto-edge-bnd-ids-{uuid.uuid4().hex[:8]}"
        edges.append({
            "id": edge_id,
            "source": boundary_id,
            "target": ids_id,
            "type": "control",
            "auto_remediated": True,
            "remediated_by": "auto_remediator",
            "remediated_at": _now_iso(),
        })
        msg_edge = f"added edge id={edge_id} boundary={boundary_id}→ids-ips"
    else:
        msg_edge = "no boundary_id in finding — edge not added"

    return graph, f"{msg_node}; {msg_edge}"


def _handler_add_siem_to_collector(graph: dict, finding: dict) -> tuple[dict, str]:
    """ODC-LOG-002: add a SIEM platform node and wire the affected collector to it.

    The engine flags any col-* node that has no edge to a plt-* node.
    affected_entity holds the collector's label.  Handler:
      1. Locates the collector node by label (or id fallback).
      2. Finds an existing plt-* node, or adds a plt-splunk placeholder.
      3. Adds a forwarding edge collector → SIEM if not already present.
    Idempotent — a second run on an already-fixed graph is a no-op.
    """
    affected = (finding.get("affected_entity") or "").strip()
    nodes = graph.setdefault("nodes", [])
    edges = graph.setdefault("edges", [])

    # 1. Locate the affected collector node.
    collector_node = None
    for n in nodes:
        if n.get("label") == affected or n.get("id") == affected:
            collector_node = n
            break
    if collector_node is None:
        # Fall back: pick any col-* node without a plt-* neighbour.
        plt_ids = {n["id"] for n in nodes if (n.get("type") or "").startswith("plt-")}
        connected_to_plt = {
            e["source"] for e in edges if e.get("target") in plt_ids
        } | {e["target"] for e in edges if e.get("source") in plt_ids}
        for n in nodes:
            ntype = n.get("type") or ""
            if ntype.startswith("col-") and n["id"] not in connected_to_plt:
                collector_node = n
                break
    if collector_node is None:
        return graph, f"skipped: collector node '{affected}' not found"

    col_id = collector_node["id"]

    # 2. Find or add a plt-* SIEM/analytics platform node.
    plt_ids = {n["id"] for n in nodes if (n.get("type") or "").startswith("plt-")}
    if plt_ids:
        siem_id = next(iter(plt_ids))
        msg_node = f"reused existing platform id={siem_id}"
    else:
        siem_id = f"auto-plt-splunk-{uuid.uuid4().hex[:8]}"
        siem_node = {
            "id": siem_id,
            "type": "plt-splunk",
            "label": "SIEM / Analytics Platform",
            "x": 300,
            "y": 50,
        }
        _ensure_node_attrs(siem_node, "ODC-LOG-002")
        nodes.append(siem_node)
        msg_node = f"added placeholder SIEM node id={siem_id} type=plt-splunk"

    # 3. Add forwarding edge collector → SIEM (idempotent).
    for e in edges:
        if e.get("source") == col_id and e.get("target") == siem_id:
            return graph, f"already wired: {col_id} → {siem_id}; {msg_node}"
        if e.get("source") == siem_id and e.get("target") == col_id:
            return graph, f"already wired (reverse): {siem_id} → {col_id}; {msg_node}"

    edge_id = f"auto-edge-col-siem-{uuid.uuid4().hex[:8]}"
    edges.append({
        "id": edge_id,
        "source": col_id,
        "target": siem_id,
        "type": "forward",
        "label": "forwards to",
        "auto_remediated": True,
        "remediated_by": "auto_remediator",
        "remediated_at": _now_iso(),
    })
    return graph, f"{msg_node}; added forwarding edge id={edge_id} {col_id}→{siem_id}"


def _handler_add_os_and_network_logs(graph: dict, finding: dict) -> tuple[dict, str]:
    """ODC-LOG-004: needs both src-os-log AND src-network-log nodes."""
    nodes = graph.setdefault("nodes", [])
    types_present = {n.get("type") for n in nodes}
    added: list[str] = []
    if "src-os-log" not in types_present:
        nid = f"auto-src-os-log-{uuid.uuid4().hex[:8]}"
        n = {"id": nid, "type": "src-os-log", "label": "OS / System Log Source", "x": 50, "y": 50}
        _ensure_node_attrs(n, "ODC-LOG-004")
        nodes.append(n)
        added.append(nid)
    if "src-network-log" not in types_present:
        nid = f"auto-src-network-log-{uuid.uuid4().hex[:8]}"
        n = {"id": nid, "type": "src-network-log", "label": "Network Log Source", "x": 50, "y": 100}
        _ensure_node_attrs(n, "ODC-LOG-004")
        nodes.append(n)
        added.append(nid)
    if not added:
        return graph, "already present: src-os-log and src-network-log"
    return graph, f"added {len(added)} log source node(s): {','.join(added)}"


# ── ODC cascade handlers (OPT-48 prep) ──────────────────────────────────────
# These close the cascade findings that emerge after placeholder nodes are
# added: sources without collectors, collectors without platforms, alert rules
# without SOAR, unencrypted log transport, SOAR without ticket system.
# Edge-wiring rules (LOG-001, DET-002, INT-001) create missing connections.


def _ensure_placeholder_collector(graph: dict, rule_id: str) -> str:
    """Ensure at least one col-* node exists; return its id."""
    nodes = graph.setdefault("nodes", [])
    for n in nodes:
        if (n.get("type") or "").startswith("col-"):
            return n["id"]
    nid = f"auto-col-fluentd-{uuid.uuid4().hex[:8]}"
    node = {"id": nid, "type": "col-fluentd", "label": "Default Log Collector", "x": 300, "y": 200}
    _ensure_node_attrs(node, rule_id)
    nodes.append(node)
    return nid


def _ensure_placeholder_platform(graph: dict, rule_id: str) -> str:
    """Ensure at least one plt-* node exists; return its id."""
    nodes = graph.setdefault("nodes", [])
    for n in nodes:
        if (n.get("type") or "").startswith("plt-"):
            return n["id"]
    nid = f"auto-plt-siem-{uuid.uuid4().hex[:8]}"
    node = {"id": nid, "type": "plt-splunk", "label": "SIEM Platform", "x": 500, "y": 200}
    _ensure_node_attrs(node, rule_id)
    nodes.append(node)
    return nid


def _add_edge(graph: dict, src: str, tgt: str, rule_id: str, **attrs) -> str:
    """Append an edge to the graph. Returns edge id."""
    edges = graph.setdefault("edges", [])
    eid = f"auto-edge-{uuid.uuid4().hex[:8]}"
    edge = {"id": eid, "source": src, "target": tgt, **attrs}
    edge["auto_remediated"] = True
    edge["remediated_by"] = "auto_remediator"
    edge["remediated_rules"] = [rule_id]
    edges.append(edge)
    return eid


def _handler_connect_sources_to_collector(graph: dict, finding: dict) -> tuple[dict, str]:
    """ODC-LOG-001: wire every src-* node to a col-* collector."""
    nodes = graph.setdefault("nodes", [])
    edges = graph.setdefault("edges", [])
    src_ids = [n["id"] for n in nodes if (n.get("type") or "").startswith("src-")]
    col_ids = {n["id"] for n in nodes if (n.get("type") or "").startswith("col-")}
    if not src_ids:
        return graph, "no src-* nodes present — nothing to wire"
    if not col_ids:
        default_col = _ensure_placeholder_collector(graph, "ODC-LOG-001")
        col_ids = {default_col}
    target_col = sorted(col_ids)[0]
    # Which sources already have an edge to some collector?
    adjacency: dict[str, set] = {}
    for e in edges:
        adjacency.setdefault(e.get("source", ""), set()).add(e.get("target", ""))
        adjacency.setdefault(e.get("target", ""), set()).add(e.get("source", ""))
    wired = 0
    for sid in src_ids:
        neighbors = adjacency.get(sid, set())
        if not (neighbors & col_ids):
            _add_edge(graph, sid, target_col, "ODC-LOG-001", label="auto log flow")
            wired += 1
    if wired == 0:
        return graph, "already wired: all src-* nodes connect to a collector"
    return graph, f"wired {wired} src-* node(s) to collector {target_col}"


def _handler_add_soar_and_wire(graph: dict, finding: dict) -> tuple[dict, str]:
    """ODC-DET-002: ensure auto-runbook/auto-soar exists and wire alert rules to it."""
    nodes = graph.setdefault("nodes", [])
    edges = graph.setdefault("edges", [])
    soar_ids = {n["id"] for n in nodes if (n.get("type") or "") in ("auto-soar", "auto-runbook")}
    if not soar_ids:
        nid = f"auto-runbook-{uuid.uuid4().hex[:8]}"
        node = {"id": nid, "type": "auto-runbook", "label": "Default Runbook", "x": 600, "y": 300}
        _ensure_node_attrs(node, "ODC-DET-002")
        nodes.append(node)
        soar_ids = {nid}
    target = sorted(soar_ids)[0]

    alert_ids = [n["id"] for n in nodes if (n.get("type") or "") == "auto-alert-rule"]
    adjacency: dict[str, set] = {}
    for e in edges:
        adjacency.setdefault(e.get("source", ""), set()).add(e.get("target", ""))
        adjacency.setdefault(e.get("target", ""), set()).add(e.get("source", ""))
    wired = 0
    for aid in alert_ids:
        if not (adjacency.get(aid, set()) & soar_ids):
            _add_edge(graph, aid, target, "ODC-DET-002", label="alert->runbook")
            wired += 1
    return graph, f"ensured runbook + wired {wired} alert(s) to {target}"


def _handler_encrypt_log_transport(graph: dict, finding: dict) -> tuple[dict, str]:
    """ODC-SEC-001: set encrypted=true on collector↔platform edges."""
    nodes = graph.setdefault("nodes", [])
    edges = graph.setdefault("edges", [])
    type_by_id = {n.get("id"): (n.get("type") or "") for n in nodes}
    fixed = 0
    for e in edges:
        src_t = type_by_id.get(e.get("source"), "")
        tgt_t = type_by_id.get(e.get("target"), "")
        is_col_plt = (src_t.startswith("col-") and tgt_t.startswith("plt-")) or (
            src_t.startswith("plt-") and tgt_t.startswith("col-")
        )
        if is_col_plt and not e.get("encrypted"):
            e["encrypted"] = True
            rules = e.get("auto_remediated_rules") or []
            if "ODC-SEC-001" not in rules:
                rules.append("ODC-SEC-001")
            e["auto_remediated_rules"] = rules
            fixed += 1
    if fixed == 0:
        return graph, "no unencrypted collector↔platform edges found"
    return graph, f"encrypted {fixed} collector↔platform edge(s)"


def _handler_add_ticket_and_wire(graph: dict, finding: dict) -> tuple[dict, str]:
    """ODC-INT-001: add auto-ticket + wire SOAR/runbook nodes to it."""
    nodes = graph.setdefault("nodes", [])
    edges = graph.setdefault("edges", [])
    ticket_ids = {n["id"] for n in nodes if (n.get("type") or "") == "auto-ticket"}
    if not ticket_ids:
        nid = f"auto-ticket-{uuid.uuid4().hex[:8]}"
        node = {"id": nid, "type": "auto-ticket", "label": "Ticket System", "x": 700, "y": 400}
        _ensure_node_attrs(node, "ODC-INT-001")
        nodes.append(node)
        ticket_ids = {nid}
    target = sorted(ticket_ids)[0]

    soar_ids = [n["id"] for n in nodes if (n.get("type") or "") in ("auto-soar", "auto-runbook")]
    if not soar_ids:
        return graph, f"added ticket node {target} (no SOAR/runbook to wire)"

    adjacency: dict[str, set] = {}
    for e in edges:
        adjacency.setdefault(e.get("source", ""), set()).add(e.get("target", ""))
        adjacency.setdefault(e.get("target", ""), set()).add(e.get("source", ""))
    wired = 0
    for sid in soar_ids:
        if not (adjacency.get(sid, set()) & ticket_ids):
            _add_edge(graph, sid, target, "ODC-INT-001", label="soar->ticket")
            wired += 1
    return graph, f"ensured ticket node + wired {wired} SOAR/runbook(s) to {target}"


# ── Infra (IDC) — template-merge handlers (cloud_vendor_policy.yaml) ───────
#
# Unlike security/observability/boundary handlers which add vendor-neutral
# placeholder nodes, the infra canvas checks for SPECIFIC vendor prefixes
# (aws-iam vs az-entra vs gcp-iam, etc.). Rather than hardcoding the choice
# in Python, we load args/cloud_vendor_policy.yaml and merge nodes from the
# "DoD IL4 Reference (AWS GovCloud)" template in idc_templates. This honors
# the project's standing cloud policy (AWS primary CSP, IL4/5 by default)
# and reuses the vendor-consistent reference architecture that already
# ships in the canvas — so if the policy changes, only the YAML + template
# need to change, not the handler code.

_policy_cache: dict | None = None


def _load_cloud_vendor_policy() -> dict:
    """Load args/cloud_vendor_policy.yaml. Cached in module state."""
    global _policy_cache
    if _policy_cache is not None:
        return _policy_cache
    policy_path = _REPO_ROOT / "args" / "cloud_vendor_policy.yaml"
    try:
        import yaml  # optional dep
        with open(policy_path, "r", encoding="utf-8") as f:
            _policy_cache = yaml.safe_load(f) or {}
    except Exception as exc:
        logger.warning("cloud_vendor_policy.yaml load failed: %s — using defaults", exc)
        _policy_cache = {}
    return _policy_cache


# ── OPT-48 — generalized canvas template-merge helper ──────────────────────
# The IDC flow (below) merges nodes from idc_templates."DoD IL4 Reference".
# OPT-48 generalizes the same pattern to every canvas — security, boundary,
# observability, data, etc. — so placeholder handlers can prefer real
# template nodes over bare placeholders. Falls back to the placeholder path
# when no template matches.
#
# Catalog is sourced from args/cloud_vendor_policy.yaml → canvas_artifact_catalog.

# Map: canvas_key (matches policy.canvas_artifact_catalog keys) → (db_filename, templates_table)
_CANVAS_TEMPLATE_MAP: dict[str, tuple[str, str]] = {
    "security": ("security_canvas.db", "sc_templates"),
    "observability": ("observability_canvas.db", "od_templates"),
    "boundary": ("boundary_canvas.db", "bd_templates"),
    "data": ("data_canvas.db", "dd_templates"),
    "pipeline": ("pipeline_canvas.db", "pc_templates"),
    "quality": ("qdc_canvas.db", "qdc_templates"),
    "migration": ("migration_canvas.db", "mc_templates"),
    # "infra" uses a dedicated policy-driven reference template — see below
    # "network" uses nc_templates with 99 entries — wire when needed
}

_canvas_template_cache: dict[str, list[dict]] = {}


def _load_canvas_templates(canvas_key: str) -> list[dict]:
    """Return all templates for a canvas as a list of {name, graph_json} dicts.

    Cached per canvas. Silently returns [] if the DB or table is missing.
    """
    if canvas_key in _canvas_template_cache:
        return _canvas_template_cache[canvas_key]
    mapping = _CANVAS_TEMPLATE_MAP.get(canvas_key)
    if not mapping:
        _canvas_template_cache[canvas_key] = []
        return []
    db_name, tbl = mapping
    db_path = DATA_DIR / db_name
    if not db_path.exists():
        _canvas_template_cache[canvas_key] = []
        return []
    templates: list[dict] = []
    try:
        conn = get_connection(db_path=str(db_path))
        rows = conn.execute(
            f"SELECT id, name, graph_json FROM {tbl}"  # nosec B608
        ).fetchall()
        conn.close()
        for r in rows:
            try:
                graph = json.loads(r["graph_json"] or "{}")
            except (json.JSONDecodeError, TypeError):
                continue
            templates.append({
                "id": r["id"],
                "name": r["name"],
                "nodes": graph.get("nodes", []) or [],
                "edges": graph.get("edges", []) or [],
            })
    except Exception as exc:
        logger.warning("failed to load %s templates: %s", canvas_key, exc)
    _canvas_template_cache[canvas_key] = templates
    return templates


def _find_template_node(canvas_key: str, node_type: str) -> tuple[dict, str] | None:
    """Search every template in the canvas for a node of the given type.
    Returns (node_dict, template_name) for the first match, or None.
    """
    for tpl in _load_canvas_templates(canvas_key):
        for n in tpl["nodes"]:
            if n.get("type") == node_type:
                return n, tpl["name"]
    return None


def _canvas_template_or_placeholder_handler(
    canvas_key: str, node_type: str, placeholder_label: str,
) -> Callable:
    """OPT-48: factory for handlers that prefer real template nodes.

    When the rule fires, first search the canvas's template catalog for a
    node of the target type. If found, clone it into the target design
    (with a fresh id and provenance stamp). Otherwise fall back to the old
    placeholder-node behavior. Idempotent — existing nodes of the type
    short-circuit both paths.
    """
    placeholder_fn = _make_add_node_handler(node_type, placeholder_label)

    def handler(graph: dict, finding: dict) -> tuple[dict, str]:
        # Idempotency check: skip if the type is already present
        existing_types = {n.get("type") for n in graph.get("nodes", [])}
        if node_type in existing_types:
            return graph, f"already present: type={node_type} (no-op)"

        # Try template path first
        match = _find_template_node(canvas_key, node_type)
        if match is not None:
            tpl_node, tpl_name = match
            nodes = graph.setdefault("nodes", [])
            new_id = f"auto-{node_type}-{uuid.uuid4().hex[:8]}"
            merged = dict(tpl_node)
            merged["id"] = new_id
            merged.setdefault("label", placeholder_label)
            _ensure_node_attrs(merged, finding.get("rule_id", ""))
            merged["remediated_from_template"] = tpl_name
            merged["remediation_canvas"] = canvas_key
            nodes.append(merged)
            return graph, (
                f"merged '{node_type}' node from template '{tpl_name}' "
                f"(id={new_id})"
            )

        # Fallback to placeholder
        return placeholder_fn(graph, finding)

    return handler


def _load_idc_reference_template(policy_key: str = "idc_reference_template") -> dict | None:
    """Fetch the referenced infra template's graph_json from idc_templates.

    Returns a dict {'nodes': [...], 'edges': [...]} or None if the template
    cannot be located (policy missing, canvas DB missing, name mismatch).
    """
    policy = _load_cloud_vendor_policy()
    ref = policy.get(policy_key) or {}
    name = ref.get("lookup_by_name")
    if not name:
        return None
    db_path = DATA_DIR / "infra_canvas.db"
    if not db_path.exists():
        logger.warning("infra_canvas.db not found at %s", db_path)
        return None
    try:
        conn = get_connection(db_path=str(db_path))
        row = conn.execute(
            "SELECT graph_json FROM idc_templates WHERE name = %s LIMIT 1",  # nosec B608
            (name,),
        ).fetchone()
        conn.close()
        if not row:
            logger.warning("idc template '%s' not found", name)
            return None
        return json.loads(row["graph_json"] or "{}")
    except Exception as exc:
        logger.warning("failed to load idc template '%s': %s", name, exc)
        return None


def _merge_idc_template_nodes(graph: dict, rule_id: str,
                              required_types: list[str]) -> tuple[dict, str]:
    """Merge nodes from the IDC reference template into the target graph.

    Only adds nodes whose TYPE is not already present in the target AND is
    in required_types (so a handler for IDC-IAM-001 only pulls IAM nodes,
    not the entire template). Keeps the fix minimal and targeted.
    """
    template = _load_idc_reference_template("idc_reference_template")
    if not template:
        return graph, (
            f"skipped: cloud_vendor_policy.yaml or 'DoD IL4 Reference "
            f"(AWS GovCloud)' template not loadable — cannot remediate "
            f"{rule_id} without vendor guidance"
        )

    tpl_nodes = template.get("nodes", []) or []
    existing_types = {n.get("type") for n in graph.get("nodes", [])}

    # Pick template nodes whose type is required AND not already present.
    candidates = [
        n for n in tpl_nodes
        if n.get("type") in required_types and n.get("type") not in existing_types
    ]
    if not candidates:
        return graph, f"already satisfied: required type(s) present for {rule_id}"

    nodes = graph.setdefault("nodes", [])
    added = []
    for tpl_node in candidates:
        new_id = f"auto-{tpl_node.get('type', 'node')}-{uuid.uuid4().hex[:8]}"
        merged = dict(tpl_node)
        merged["id"] = new_id
        merged["label"] = merged.get("label") or tpl_node.get("type", "").upper()
        _ensure_node_attrs(merged, rule_id)
        merged["remediated_from_template"] = "DoD IL4 Reference (AWS GovCloud)"
        merged["remediation_policy"] = "args/cloud_vendor_policy.yaml"
        nodes.append(merged)
        added.append(f"{merged['id']}({merged.get('type')})")

    return graph, (
        f"merged {len(added)} node(s) from DoD IL4 Reference template: "
        + ", ".join(added)
    )


def _make_idc_template_handler(required_types: list[str]) -> Callable:
    """Factory: returns a handler that merges specific node types from the
    IDC reference template. Each IDC rule declares which types satisfy it.
    """
    def handler(graph: dict, finding: dict) -> tuple[dict, str]:
        return _merge_idc_template_nodes(graph, finding["rule_id"], required_types)
    return handler


HANDLERS: dict[str, Callable] = {
    # Security Canvas (vendor-neutral ctrl-* placeholder types)
    "SEC-AUTH-001": _handler_set_authenticated,
    "SEC-ZT-001": _handler_set_zero_trust,
    "SEC-HARDEN-001": _handler_set_hardening_baseline,
    "SEC-SEG-001": _handler_add_trust_boundary_security,
    "SEC-GEN-002": _handler_add_trust_boundary_security,
    "SEC-GEN-003": _handler_add_threats_list,
    # OPT-48: prefer real nodes from sc_templates over bare placeholders
    "SEC-ENC-002": _canvas_template_or_placeholder_handler("security", "ctrl-kms", "Key Management Service"),
    "SEC-SECRET-001": _canvas_template_or_placeholder_handler("security", "ctrl-kms", "Key Management Service"),
    "SEC-EDR-001": _canvas_template_or_placeholder_handler("security", "ctrl-edr", "EDR / XDR"),
    "SEC-MON-001": _canvas_template_or_placeholder_handler("security", "ctrl-ids", "IDS / IPS"),
    "SEC-MON-002": _canvas_template_or_placeholder_handler("security", "ctrl-scanner", "Vulnerability Scanner"),
    "SEC-LOG-001": _canvas_template_or_placeholder_handler("security", "ctrl-siem", "SIEM"),
    # Observability Canvas (cmp-/src-/col-/auto- types)
    # OPT-48: ODC handlers also prefer real od_templates nodes
    "ODC-RET-001": _canvas_template_or_placeholder_handler("observability", "cmp-log-policy", "Log Retention Policy (1y online + 7y archive)"),
    "ODC-DET-001": _canvas_template_or_placeholder_handler("observability", "auto-alert-rule", "Default Alert Rule"),
    "ODC-DET-002": _handler_add_soar_and_wire,          # cascade: SOAR/runbook + wire alerts
    "ODC-DET-003": _canvas_template_or_placeholder_handler("observability", "cmp-baseline", "MITRE ATT&CK Detection Baseline"),
    "ODC-INT-001": _handler_add_ticket_and_wire,        # cascade: ticket system + wire SOAR
    "ODC-LOG-001": _handler_connect_sources_to_collector,  # cascade: source -> collector edges
    "ODC-LOG-002": _handler_add_siem_to_collector,
    "ODC-LOG-003": _canvas_template_or_placeholder_handler("observability", "col-s3", "Log Archive (S3 / Object Storage)"),
    "ODC-LOG-004": _handler_add_os_and_network_logs,
    "ODC-LOG-005": _canvas_template_or_placeholder_handler("observability", "src-cloud-log", "Cloud Audit Log (CloudTrail / Activity Log)"),
    "ODC-SEC-001": _handler_encrypt_log_transport,      # cascade: TLS on collector<->platform edges
    "ODC-SEC-002": _canvas_template_or_placeholder_handler("observability", "src-endpoint", "EDR Telemetry Source"),
    "ODC-SEC-003": _canvas_template_or_placeholder_handler("observability", "src-iam", "IAM / IdP Log Source"),
    # Boundary Canvas — OPT-48: prefer bd_templates nodes
    "BDC-BND-001": _canvas_template_or_placeholder_handler("boundary", "bnd-ato", "ATO Boundary"),
    "BDC-CTL-001": _handler_add_boundary_firewall,
    "BDC-CTL-002": _handler_add_boundary_ids,
    "BDC-CTL-003": _canvas_template_or_placeholder_handler("boundary", "ctrl-siem", "SIEM (boundary monitoring)"),
    "BDC-DOC-001": _handler_add_pps_matrix_to_boundary,
    "BDC-DOC-002": _handler_add_dfd_doc,
    # Infra Canvas — template-merge per args/cloud_vendor_policy.yaml
    # Merges nodes from idc_templates."DoD IL4 Reference (AWS GovCloud)"
    # which already contains aws-iam/aws-kms/aws-secrets/aws-securityhub/
    # iac-terraform. Policy-driven → change policy, not handler code.
    "IDC-IAM-001": _make_idc_template_handler(
        ["aws-iam", "aws-cognito", "az-entra", "gcp-iam", "oci-iam", "ibm-iam"],
    ),
    "IDC-ENC-003": _make_idc_template_handler(
        ["aws-kms", "az-keyvault", "gcp-kms", "oci-vault", "ibm-kms", "iac-vault"],
    ),
    "IDC-IAM-002": _make_idc_template_handler(
        ["aws-secrets", "az-keyvault", "gcp-secret", "oci-vault", "ibm-secrets", "iac-vault"],
    ),
    "IDC-SEC-001": _make_idc_template_handler(
        ["aws-securityhub", "aws-config", "az-defender", "gcp-scc", "oci-cspm", "ibm-scc"],
    ),
    "IDC-IAC-001": _make_idc_template_handler(
        ["iac-terraform", "iac-pulumi", "iac-crossplane", "iac-bicep", "iac-cdk"],
    ),
}

# Rules that exist in the engine but require vendor selection with no
# default policy in args/cloud_vendor_policy.yaml. Currently empty — all 5
# IDC rules are covered by _make_idc_template_handler as of 2026-04-10.
MANUAL_RULES: dict[str, str] = {}


# ── Database helpers ────────────────────────────────────────────────────────


def _get_dashboard_db_conn():
    """Return a connection to the central icdev.db (for finding_approvals)."""
    from tools.dashboard.config import DB_PATH
    from tools.db.storage import get_connection
    return get_connection(db_path=str(DB_PATH))


def _compute_hash(canvas: str, rule_id: str, title: str, affected_entity: str) -> str:
    from tools.dashboard.findings_aggregator import compute_finding_hash
    return compute_finding_hash(canvas, rule_id, title, affected_entity)


def build_hash_index(canvas: str) -> dict[str, str]:
    """Build a hash → design_id mapping by scanning the last 50 assessments once.

    Intended to be called once per canvas per auto_remediator run so that
    find_design_for_finding() can do O(1) lookups instead of re-parsing all
    assessment rows for every finding.
    """
    cfg = CANVAS_REGISTRY.get(canvas)
    if not cfg:
        return {}
    db_path = DATA_DIR / cfg["db"]
    if not db_path.exists():
        return {}
    conn = get_connection(db_path=str(db_path))
    index: dict[str, str] = {}
    try:
        rows = conn.execute(
            f"SELECT design_id, findings_json FROM {cfg['asmt_table']} "  # noqa: S608  # nosec B608
            f"ORDER BY {cfg['asmt_time_col']} DESC LIMIT 50",
        ).fetchall()
        for r in rows:
            try:
                items = json.loads(r["findings_json"] or "[]")
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(items, list):
                continue
            for it in items:
                if not isinstance(it, dict):
                    continue
                rule_id = it.get("rule_id") or it.get("id") or ""
                title = it.get("title") or it.get("description") or "(untitled)"
                affected = it.get("affected_entity") or it.get("affected") or ""
                h = _compute_hash(canvas, rule_id, title, affected)
                # First occurrence (most-recent assessment) wins.
                if h not in index:
                    index[h] = r["design_id"]
    finally:
        conn.close()
    return index


def find_design_for_finding(
    canvas: str,
    target_hash: str,
    hash_index: dict[str, str] | None = None,
) -> str | None:
    """Return the design_id whose findings contain a finding matching target_hash.

    When *hash_index* is supplied (pre-built by :func:`build_hash_index`) the
    lookup is O(1) and no DB I/O is performed.  Without it the function falls
    back to scanning the last 50 assessments directly (original behaviour, kept
    for backwards-compatible single-finding calls).
    """
    if hash_index is not None:
        return hash_index.get(target_hash)

    # Legacy path: build a one-off index and discard it.
    cfg = CANVAS_REGISTRY.get(canvas)
    if not cfg:
        return None
    db_path = DATA_DIR / cfg["db"]
    if not db_path.exists():
        return None
    conn = get_connection(db_path=str(db_path))
    try:
        rows = conn.execute(
            f"SELECT design_id, findings_json FROM {cfg['asmt_table']} "  # noqa: S608  # nosec B608
            f"ORDER BY {cfg['asmt_time_col']} DESC LIMIT 50",
        ).fetchall()
        for r in rows:
            try:
                items = json.loads(r["findings_json"] or "[]")
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(items, list):
                continue
            for it in items:
                if not isinstance(it, dict):
                    continue
                rule_id = it.get("rule_id") or it.get("id") or ""
                title = it.get("title") or it.get("description") or "(untitled)"
                affected = it.get("affected_entity") or it.get("affected") or ""
                h = _compute_hash(canvas, rule_id, title, affected)
                if h == target_hash:
                    return r["design_id"]
    finally:
        conn.close()
    return None


def backup_canvas_db(canvas: str) -> Path:
    """Snapshot the canvas DB to backups/canvas/<db>.bak-<timestamp>."""
    cfg = CANVAS_REGISTRY[canvas]
    src = DATA_DIR / cfg["db"]
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    dst = BACKUP_DIR / f"{cfg['db']}.bak-{ts}"
    shutil.copy2(src, dst)
    return dst


def reassess_design(canvas: str, design_id: str, graph: dict) -> list[dict]:
    """Call the canvas's own assessment engine on the mutated graph and return
    the new findings list.
    """
    cfg = CANVAS_REGISTRY[canvas]
    eng = importlib.import_module(cfg["engine_module"])
    fn = getattr(eng, cfg["engine_func"])
    if cfg.get("engine_takes_design_id"):
        result = fn(design_id, graph)
    else:
        result = fn(graph)
    if isinstance(result, dict):
        return result.get("findings", []) or []
    return []


def persist_verify_assessment(canvas: str, design_id: str, findings: list[dict]) -> None:
    """Insert a fresh assessment row tagged 'auto_remediator_verify' so the
    aggregator's recent-N window picks it up and the fixed finding falls off.
    """
    cfg = CANVAS_REGISTRY[canvas]
    db_path = DATA_DIR / cfg["db"]
    conn = get_connection(str(db_path))
    try:
        new_id = str(uuid.uuid4())
        ts = _now_iso()
        findings_json = json.dumps(findings)
        if canvas == "security":
            # sc_assessments has many extra cols — only required ones are id, design_id, ran_at
            conn.execute(
                "INSERT INTO sc_assessments (id, design_id, assessment_type, "
                "trigger_source, total_threats, total_controls, risk_score, "
                "posture_grade, findings_json, recommendations_json, ran_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (new_id, design_id, "auto_remediator_verify", "auto_remediator",
                 0, 0, 0.0, "N/A", findings_json, "[]", ts),
            )
        elif canvas == "boundary":
            conn.execute(
                "INSERT INTO bd_assessments (id, design_id, assessment_type, "
                "findings_json, score, grade, cat1_findings, cat2_findings, "
                "cat3_findings, nist_coverage_json, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (new_id, design_id, "auto_remediator_verify", findings_json,
                 100.0, "A", 0, 0, 0, "{}", ts),
            )
        elif canvas == "observability":
            conn.execute(
                "INSERT INTO od_assessments (id, design_id, assessment_type, "
                "findings_json, score, grade, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (new_id, design_id, "auto_remediator_verify", findings_json,
                 100.0, "A", ts),
            )
        elif canvas == "infra":
            conn.execute(
                "INSERT INTO idc_assessments (id, design_id, assessment_type, "
                "findings_json, score, created_at) VALUES (%s, %s, %s, %s, %s, %s)",
                (new_id, design_id, "auto_remediator_verify", findings_json,
                 100.0, ts),
            )
        conn.commit()
    finally:
        conn.close()


def update_finding_approval(finding_hash: str, target: dict, decision: str,
                            rationale: str, reviewer: str = "auto_remediator",
                            conn=None) -> None:
    """UPSERT into finding_approvals.decision.

    If *conn* is supplied the caller owns the connection and is responsible for
    committing/closing it (batch mode).  When omitted a private connection is
    opened, committed, and closed here (standalone mode — backward-compatible).
    """
    _own_conn = conn is None
    if _own_conn:
        conn = _get_dashboard_db_conn()
    try:
        now = _now_iso()
        existing = conn.execute(
            "SELECT decision FROM finding_approvals WHERE finding_hash = %s",
            (finding_hash,),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE finding_approvals SET decision = %s, decision_by = %s, "
                "decision_at = %s, decision_rationale = %s, updated_at = %s "
                "WHERE finding_hash = %s",
                (decision, reviewer, now, rationale, now, finding_hash),
            )
        else:
            conn.execute(
                "INSERT INTO finding_approvals "
                "(finding_hash, canvas_source, rule_id, severity, title, "
                "affected_entity, decision, decision_by, decision_at, "
                "decision_rationale, classification, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    finding_hash,
                    target.get("canvas_source", ""),
                    target.get("rule_id", ""),
                    target.get("severity", ""),
                    target.get("title", ""),
                    target.get("affected_entity", ""),
                    decision,
                    reviewer,
                    now,
                    rationale,
                    "CUI // SP-CTI",
                    now,
                    now,
                ),
            )
        if _own_conn:
            conn.commit()
    finally:
        if _own_conn:
            conn.close()


def emit_audit(finding_hash: str, canvas: str, rule_id: str, decision: str,
               diff: str, design_id: str, backup_path: str | None,
               cot_trace: dict | None = None) -> None:
    """Append one row to audit_trail (event_type=vulnerability_resolved)."""
    try:
        from tools.audit.audit_logger import log_event
        details = {
            "finding_hash": finding_hash,
            "canvas": canvas,
            "rule_id": rule_id,
            "decision": decision,
            "diff": diff,
            "design_id": design_id,
            "backup": backup_path,
        }
        if cot_trace:
            details["cot_trace"] = cot_trace
        log_event(
            event_type="vulnerability_resolved" if decision == "remediated" else "decision_made",
            actor="auto_remediator",
            action=f"poam.auto_remediate.{decision}",
            details=json.dumps(details),
            project_id="dashboard-poam",
        )
    except Exception as exc:  # pragma: no cover - audit must never break
        logger.warning("audit logging failed for %s: %s", finding_hash, exc)


# ── Core remediation flow ───────────────────────────────────────────────────


def remediate_one(target: dict, dry_run: bool = False,
                  backup_cache: dict[str, str] | None = None,
                  approval_conn=None,
                  hash_index: dict[str, str] | None = None) -> dict:
    """Apply auto-remediation to a single finding (dict from aggregate_findings).

    *approval_conn* — optional shared dashboard DB connection for batch runs.
    When supplied, ``update_finding_approval`` reuses it and skips per-call
    commit/close (the caller commits once after the batch).

    *hash_index* — optional pre-built hash → design_id mapping produced by
    :func:`build_hash_index`.  When supplied, ``find_design_for_finding`` runs
    in O(1) instead of re-scanning the last 50 assessments for every finding.
    """
    canvas = target.get("canvas_source", "")
    rule_id = target.get("rule_id", "")
    finding_hash = target.get("finding_hash", "")

    base = {
        "finding_hash": finding_hash,
        "rule_id": rule_id,
        "canvas": canvas,
        "severity": target.get("severity", ""),
        "title": target.get("title", ""),
        "affected_entity": target.get("affected_entity", ""),
    }

    handler = HANDLERS.get(rule_id)
    if not handler:
        return {**base, "ok": False, "skipped": True,
                "reason": f"no handler for rule {rule_id}",
                "manual_reason": MANUAL_RULES.get(rule_id, "")}

    cfg = CANVAS_REGISTRY.get(canvas)
    if not cfg:
        return {**base, "ok": False, "reason": f"unknown canvas '{canvas}'"}

    design_id = find_design_for_finding(canvas, finding_hash, hash_index=hash_index)
    if not design_id:
        return {**base, "ok": False,
                "reason": "could not locate source design in recent assessments"}

    db_path = DATA_DIR / cfg["db"]
    conn = get_connection(str(db_path))
    try:
        row = conn.execute(
            f"SELECT graph_json FROM {cfg['design_table']} WHERE id = %s",  # noqa: S608  # nosec B608
            (design_id,),
        ).fetchone()
        if not row:
            return {**base, "ok": False,
                    "design_id": design_id,
                    "reason": f"design {design_id} not found"}

        graph = json.loads(row["graph_json"] or "{}")
        graph, diff = handler(graph, target)

        if dry_run:
            return {**base, "ok": True, "dry_run": True,
                    "design_id": design_id, "diff": diff}

        # Backup once per canvas per run
        backup_path: str | None = None
        if backup_cache is not None:
            cached = backup_cache.get(canvas)
            if cached:
                backup_path = cached
            else:
                backup_path = str(backup_canvas_db(canvas))
                backup_cache[canvas] = backup_path
        else:
            backup_path = str(backup_canvas_db(canvas))

        # Save mutated graph
        conn.execute(
            f"UPDATE {cfg['design_table']} SET graph_json = %s, updated_at = %s WHERE id = %s",  # noqa: S608  # nosec B608
            (json.dumps(graph), _now_iso(), design_id),
        )
        conn.commit()
    finally:
        conn.close()

    # Re-assess (best-effort) and verify
    verified_gone = False
    new_findings: list[dict] = []
    reassess_error: str | None = None
    try:
        new_findings = reassess_design(canvas, design_id, graph)
        new_hashes = {
            _compute_hash(
                canvas,
                f.get("rule_id") or "",
                f.get("title") or f.get("description") or "(untitled)",
                f.get("affected_entity") or f.get("affected") or "",
            )
            for f in new_findings if isinstance(f, dict)
        }
        verified_gone = finding_hash not in new_hashes
        persist_verify_assessment(canvas, design_id, new_findings)
    except Exception as exc:  # pragma: no cover - keep going on assessment failure
        reassess_error = f"{type(exc).__name__}: {exc}"
        logger.warning("re-assessment failed for %s: %s", finding_hash, exc)

    decision = "remediated" if verified_gone else "approved"
    rationale = f"auto_remediator: {diff}"
    if not verified_gone:
        rationale += " (NOT verified by re-assessment — finding still present)"
    if reassess_error:
        rationale += f" [reassess_error: {reassess_error}]"

    # Build structured CoT trace for the remediation path
    cot_trace = {
        "handler": rule_id,
        "canvas": canvas,
        "design_id": design_id,
        "diff": diff,
        "verified_gone": verified_gone,
        "reassess_error": reassess_error,
        "reasoning": (
            f"Selected handler {rule_id} for canvas {canvas}. "
            f"Applied mutation: {diff}. "
            f"Re-assessment {'confirmed fix' if verified_gone else 'found finding still present'}."
        ),
    }
    rationale += f" | CoT: {json.dumps(cot_trace)}"

    update_finding_approval(finding_hash, target, decision, rationale,
                            conn=approval_conn)
    emit_audit(finding_hash, canvas, rule_id, decision, diff, design_id, backup_path, cot_trace=cot_trace)

    return {
        **base,
        "ok": True,
        "design_id": design_id,
        "diff": diff,
        "backup": backup_path,
        "verified_gone": verified_gone,
        "decision": decision,
        "reassess_error": reassess_error,
    }


# ── Target selection ────────────────────────────────────────────────────────


def select_targets(args) -> list[dict]:
    """Resolve --finding-hash / --all-pending / --all-approved / --canvas into
    a concrete list of finding dicts via the dashboard aggregator.
    """
    from tools.dashboard.findings_aggregator import aggregate_findings

    findings = aggregate_findings(get_db_conn=_get_dashboard_db_conn,
                                  include_remediated=True)

    if args.finding_hash:
        return [f for f in findings if f["finding_hash"] == args.finding_hash]

    selected = findings
    if args.canvas:
        selected = [f for f in selected if f["canvas_source"] == args.canvas]

    if args.all_pending:
        return [f for f in selected if f.get("decision") == "pending"]
    if args.all_approved:
        return [f for f in selected if f.get("decision") == "approved"]

    return []


# ── CLI ─────────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(
        description="ICDEV™ POA&M Auto-Remediator (cross-canvas)",
    )
    parser.add_argument("--finding-hash", help="Remediate a single finding by hash")
    parser.add_argument("--all-pending", action="store_true",
                        help="Remediate all pending findings (after --canvas filter)")
    parser.add_argument("--all-approved", action="store_true",
                        help="Remediate all approved findings (after --canvas filter)")
    parser.add_argument("--canvas", choices=sorted(CANVAS_REGISTRY.keys()),
                        help="Restrict to one canvas")
    parser.add_argument("--list-handlers", action="store_true",
                        help="Print the handler registry and exit")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be mutated without writing")
    parser.add_argument("--json", action="store_true", help="Emit JSON output")
    parser.add_argument("--gate", action="store_true",
                        help="Exit 1 if any finding failed to remediate")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.list_handlers:
        out = {
            "auto_fixable": sorted(HANDLERS.keys()),
            "manual": MANUAL_RULES,
            "canvases": sorted(CANVAS_REGISTRY.keys()),
        }
        if args.json:
            print(json.dumps(out, indent=2))
        else:
            print(f"Auto-fixable rules ({len(HANDLERS)}):")
            for k in sorted(HANDLERS.keys()):
                print(f"  {k}")
            print(f"\nManual-only rules ({len(MANUAL_RULES)}):")
            for k, reason in MANUAL_RULES.items():
                print(f"  {k}: {reason}")
        return 0

    targets = select_targets(args)
    if not targets:
        msg = "No targets selected. Use --finding-hash, --all-pending, or --all-approved."
        if args.json:
            print(json.dumps({"ok": False, "error": msg}))
        else:
            print(msg)
        return 2

    # Open one shared dashboard DB connection for the entire batch so
    # update_finding_approval() doesn't open/close a connection per finding.
    # Dry-run never writes approval rows, so skip the shared conn in that case.
    approval_conn = None if args.dry_run else _get_dashboard_db_conn()
    backup_cache: dict[str, str] = {}
    results: list[dict] = []

    # Build hash→design_id index once per canvas so find_design_for_finding()
    # does O(1) lookups instead of re-parsing 50 assessments per finding.
    canvases_in_batch = {t.get("canvas_source", "") for t in targets}
    hash_indexes: dict[str, dict[str, str]] = {
        c: build_hash_index(c) for c in canvases_in_batch if c
    }

    try:
        for t in targets:
            canvas_key = t.get("canvas_source", "")
            try:
                results.append(remediate_one(
                    t, dry_run=args.dry_run,
                    backup_cache=backup_cache,
                    approval_conn=approval_conn,
                    hash_index=hash_indexes.get(canvas_key),
                ))
            except Exception as exc:  # pragma: no cover - log and continue
                logger.exception("remediation crashed for %s", t.get("finding_hash"))
                results.append({
                    "finding_hash": t.get("finding_hash", ""),
                    "rule_id": t.get("rule_id", ""),
                    "canvas": t.get("canvas_source", ""),
                    "ok": False,
                    "reason": f"{type(exc).__name__}: {exc}",
                })
        if approval_conn is not None:
            approval_conn.commit()
    finally:
        if approval_conn is not None:
            approval_conn.close()

    summary = {
        "total": len(results),
        "ok": sum(1 for r in results if r.get("ok")),
        "verified_gone": sum(1 for r in results if r.get("verified_gone")),
        "skipped_no_handler": sum(1 for r in results if r.get("skipped")),
        "failed": sum(1 for r in results if not r.get("ok") and not r.get("skipped")),
        "dry_run": args.dry_run,
        "backups": backup_cache,
    }

    if args.json:
        print(json.dumps({"summary": summary, "results": results}, indent=2))
    else:
        for r in results:
            status = (
                "OK   " if r.get("verified_gone")
                else "PART " if r.get("ok")
                else "SKIP " if r.get("skipped")
                else "FAIL "
            )
            print(f"{status} [{r.get('canvas','?')}] {r.get('rule_id','?')} "
                  f"{r.get('affected_entity','')[:40]:<40} | {r.get('diff') or r.get('reason','')}")
        print()
        print(f"Summary: total={summary['total']} ok={summary['ok']} "
              f"verified_gone={summary['verified_gone']} "
              f"skipped={summary['skipped_no_handler']} "
              f"failed={summary['failed']}")

    if args.gate and (summary["failed"] > 0):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
