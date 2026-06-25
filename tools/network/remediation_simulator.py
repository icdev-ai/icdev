# CUI // SP-CTI — NDC Remediation Simulator
"""Network Design Canvas — Remediation Action Simulator.

Runs a multi-layer simulation for a pending nc_remediation_actions row:
  Layer 1 — NDC Digital Twin  : intent validation + blast-radius analysis
  Layer 2 — NQE Impact         : Forward Networks NQE reachability delta (if FWD
                                  API is reachable); degrades gracefully to stub.

Public API
----------
simulate_remediation(remediation_action_id: int) -> dict
    Returns:
        action_id          int
        device_name        str
        action_type        str
        twin_verdict       "pass" | "warn" | "fail"
        blast_radius       list[{device, impact_type, severity}]
        policy_violations  list[{rule_id, label, detail}]
        nqe_verdict        "pass" | "warn" | "fail" | "skipped"
        nqe_findings       list[{check_id, description, severity}]
        rollback_steps     list[str]
        simulation_id      str
        simulated_at       str
"""
from __future__ import annotations

from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _nc_conn():
    from tools.network.db.init_db import get_connection
    return get_connection()


# ---------------------------------------------------------------------------
# Layer 1 helpers
# ---------------------------------------------------------------------------

def _run_twin_layer(row: dict) -> dict:
    """Call twin.intent_validate and normalise the result."""
    from tools.network.twin import intent_validate

    delta = {
        "device": row["device_name"],
        "action": row["action_type"],
        "from_version": row.get("current_version") or "",
        "to_version": row.get("target_version") or "",
    }
    project_id = row.get("project_id") or None

    try:
        result = intent_validate(delta=delta, project_id=project_id)
    except Exception as exc:
        return {
            "verdict": "warn",
            "simulation_id": None,
            "intent_results": [],
            "compliance_findings": [],
            "impacted_systems": [],
            "_error": str(exc),
        }

    return result


def _parse_blast_radius(impacted_systems: list) -> list:
    """Normalise twin impacted_systems to {device, impact_type, severity}."""
    out = []
    for item in impacted_systems:
        out.append({
            "device": item.get("title") or item.get("id") or "",
            "impact_type": item.get("impact_type") or "connectivity",
            "severity": item.get("severity") or "medium",
        })
    return out


def _parse_policy_violations(intent_results: list) -> list:
    """Extract failed intent rules as policy violations."""
    return [
        {
            "rule_id": r.get("rule_id", ""),
            "label": r.get("label", r.get("rule_id", "")),
            "detail": r.get("detail") or "",
        }
        for r in intent_results
        if not r.get("passed", True)
    ]


# ---------------------------------------------------------------------------
# Layer 2 helpers
# ---------------------------------------------------------------------------

def _run_nqe_layer(row: dict) -> dict:
    """Run NQE reachability check if FWD API is reachable; stub otherwise."""
    try:
        from tools.network.advisory import _fwd_reachable, get_nqe_client
        if not _fwd_reachable():
            return {"verdict": "skipped", "findings": []}

        nqe = get_nqe_client()
        network_id = row.get("project_id") or ""
        device = row["device_name"]
        query = (
            f"SELECT devices WHERE name = '{device}' "
            f"AND softwareVersion = '{row.get('current_version', '')}'"
        )
        resp = nqe.run_query(network_id=network_id, query=query)
        items = resp.get("items", [])
        findings = []
        if items:
            findings.append({
                "check_id": "nqe-version-match",
                "description": f"Device '{device}' still reports current_version in NQE snapshot",
                "severity": "info",
            })
        return {"verdict": "pass" if not findings else "warn", "findings": findings}
    except Exception as exc:
        return {"verdict": "skipped", "findings": [], "_error": str(exc)}


# ---------------------------------------------------------------------------
# Rollback plan
# ---------------------------------------------------------------------------

_ROLLBACK_TEMPLATES: dict[str, list[str]] = {
    "patch": [
        "Halt patch deployment immediately",
        "Restore previous firmware from backup stored in nc_backups",
        "Verify device reachability on restoration",
        "Re-run intent validation after rollback completes",
    ],
    "firmware_update": [
        "Revert to prior firmware image",
        "Reload device from recovery partition if available",
        "Re-sync topology snapshot in NDC twin",
        "Notify NOC and update nc_remediation_actions status to rolled_back",
    ],
    "config_change": [
        "Push prior running-config from nc_collected_configs snapshot",
        "Verify ACL and routing tables match pre-change state",
        "Clear NQE cache for affected device",
    ],
    "replace": [
        "Re-install original device hardware",
        "Restore last config snapshot from nc_backups",
        "Validate topology connectivity in NDC twin",
        "Update IPAM/asset records in nc_objects",
    ],
    "decommission": [
        "Re-provision device from last known config",
        "Restore topology edge entries removed during decommission",
        "Alert change advisory board of rollback",
    ],
}


def _build_rollback_steps(row: dict) -> list[str]:
    action = row.get("action_type", "")
    steps = list(_ROLLBACK_TEMPLATES.get(action, ["Contact NOC to manually restore device state"]))
    device = row.get("device_name", "")
    if device:
        steps.insert(0, f"Isolate {device} from production traffic before rollback")
    return steps


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def simulate_remediation(remediation_action_id: int) -> dict:
    """Run a full twin + NQE simulation for a pending remediation action.

    Parameters
    ----------
    remediation_action_id:
        Primary key from nc_remediation_actions.

    Returns
    -------
    dict with keys:
        action_id, device_name, action_type,
        twin_verdict, blast_radius, policy_violations,
        nqe_verdict, nqe_findings,
        rollback_steps, simulation_id, simulated_at
    """
    conn = _nc_conn()

    try:
        row_obj = conn.execute(
            "SELECT * FROM nc_remediation_actions WHERE id = ?",
            (remediation_action_id,),
        ).fetchone()
    except Exception as exc:
        return {
            "error": "db_error",
            "remediation_action_id": remediation_action_id,
            "detail": str(exc),
        }

    if row_obj is None:
        return {
            "error": "not_found",
            "remediation_action_id": remediation_action_id,
        }

    # Normalise sqlite3.Row / psycopg2 RealDictRow to plain dict
    row: dict = dict(row_obj)

    # Layer 1 — NDC digital twin
    twin = _run_twin_layer(row)
    twin_verdict: str = twin.get("verdict", "warn")
    blast = _parse_blast_radius(twin.get("impacted_systems", []))
    violations = _parse_policy_violations(twin.get("intent_results", []))

    # Layer 2 — NQE impact
    nqe = _run_nqe_layer(row)

    # Stamp simulated_at on the row
    sim_at = _now()
    try:
        conn.execute(
            "UPDATE nc_remediation_actions SET status = 'simulated', updated_at = ? WHERE id = ?",
            (sim_at, remediation_action_id),
        )
        conn.commit()
    except Exception:
        pass

    return {
        "action_id": remediation_action_id,
        "device_name": row.get("device_name", ""),
        "action_type": row.get("action_type", ""),
        "twin_verdict": twin_verdict,
        "blast_radius": blast,
        "policy_violations": violations,
        "nqe_verdict": nqe.get("verdict", "skipped"),
        "nqe_findings": nqe.get("findings", []),
        "rollback_steps": _build_rollback_steps(row),
        "simulation_id": twin.get("simulation_id"),
        "simulated_at": sim_at,
    }
