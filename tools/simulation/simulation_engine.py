#!/usr/bin/env python3
# CUI // SP-CTI
"""RICOAS Digital Program Twin — 6-dimension what-if simulation engine.

Coordinates architecture, compliance, supply chain, schedule, cost, and risk
dimensions for scenario-based analysis. Supports what-if, COA comparison, and
risk analysis scenario types.

Part of the ICDEV RICOAS Phase 20C simulation subsystem.
"""

import argparse
import json
import math
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
import os

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

ALL_DIMENSIONS = ["architecture", "compliance", "supply_chain", "schedule", "cost", "risk"]

# T-shirt size to hours mapping for cost estimation
TSHIRT_HOURS = {
    "XS": 8,
    "S": 40,
    "M": 80,
    "L": 200,
    "XL": 400,
    "XXL": 800,
}

DEFAULT_HOURLY_RATE = 150
INFRA_COST_PER_COMPONENT = 5000
STORIES_PER_SPRINT = 10
SPRINTS_PER_PI = 5


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def _get_connection(db_path=None):
    """Get a database connection."""
    path = db_path or DB_PATH
    if not Path(path).exists():
        raise FileNotFoundError(
            f"Database not found: {path}\n"
            "Run: python tools/db/init_icdev_db.py"
        )
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _log_audit(conn, project_id, event_type, action, details):
    """Append an audit trail entry (immutable)."""
    try:
        conn.execute(
            """INSERT INTO audit_trail
               (project_id, event_type, actor, action, details, classification)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                project_id,
                event_type,
                "icdev-simulation-engine",
                action,
                json.dumps(details),
                "CUI",
            ),
        )
        conn.commit()
    except Exception as exc:
        print(f"Warning: audit log failed: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# State snapshot helpers
# ---------------------------------------------------------------------------

def _snapshot_project_state(conn, project_id):
    """Capture a lightweight JSON summary of current project state."""
    state = {"project_id": project_id, "captured_at": datetime.now(timezone.utc).isoformat()}

    # Project metadata
    row = conn.execute(
        "SELECT name, type, status, impact_level, ato_status FROM projects WHERE id = ?",
        (project_id,),
    ).fetchone()
    if row:
        state["project"] = dict(row)

    # Counts for each dimension
    state["sysml_element_count"] = _safe_count(conn, "sysml_elements", project_id)
    state["sysml_relationship_count"] = _safe_count(conn, "sysml_relationships", project_id)
    state["control_count"] = _safe_count(conn, "project_controls", project_id)
    state["vendor_count"] = _safe_count(conn, "supply_chain_vendors", project_id)
    state["dependency_count"] = _safe_count(conn, "supply_chain_dependencies", project_id)
    state["safe_item_count"] = _safe_count(conn, "safe_decomposition", project_id)

    return state


def _safe_count(conn, table, project_id):
    """Return row count for a table filtered by project_id, 0 if table missing."""
    try:
        row = conn.execute(
            f"SELECT COUNT(*) AS cnt FROM {table} WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        return row["cnt"] if row else 0
    except sqlite3.OperationalError:
        return 0


def _safe_query_val(conn, sql, params=()):
    """Execute a query and return the first column of the first row, or 0."""
    try:
        row = conn.execute(sql, params).fetchone()
        if row is None:
            return 0
        val = row[0]
        return val if val is not None else 0
    except sqlite3.OperationalError:
        return 0


# ---------------------------------------------------------------------------
# Dimension simulators
# ---------------------------------------------------------------------------

def _simulate_architecture(conn, project_id, modifications):
    """Architecture dimension: components, API surface, complexity, coupling."""
    baseline_components = _safe_count(conn, "sysml_elements", project_id)
    baseline_api = _safe_query_val(
        conn,
        "SELECT COUNT(*) FROM sysml_elements WHERE project_id = ? AND stereotype LIKE '%interface%'",
        (project_id,),
    )
    baseline_relationships = _safe_count(conn, "sysml_relationships", project_id)
    baseline_coupling = (
        baseline_relationships / baseline_components if baseline_components > 0 else 0.0
    )

    # Apply modifications
    added_req = modifications.get("add_requirements", 0)
    removed_req = modifications.get("remove_requirements", 0)
    arch_change = modifications.get("change_architecture", {})
    component_delta = arch_change.get("add_components", 0) - arch_change.get("remove_components", 0)

    sim_components = max(0, baseline_components + added_req + component_delta - removed_req)
    sim_api = max(0, baseline_api + arch_change.get("add_interfaces", 0))
    sim_relationships = max(0, baseline_relationships + int(added_req * 1.5) + component_delta)
    sim_coupling = sim_relationships / sim_components if sim_components > 0 else 0.0

    baseline = {
        "component_count": baseline_components,
        "api_surface": baseline_api,
        "data_flow_complexity": baseline_relationships,
        "coupling_score": round(baseline_coupling, 4),
    }
    simulated = {
        "component_count": sim_components,
        "api_surface": sim_api,
        "data_flow_complexity": sim_relationships,
        "coupling_score": round(sim_coupling, 4),
    }
    delta = {k: simulated[k] - baseline[k] for k in baseline}
    delta_pct = _pct(baseline.get("component_count", 0), simulated.get("component_count", 0))

    chart_data = {
        "type": "bar",
        "labels": list(baseline.keys()),
        "baseline": list(baseline.values()),
        "simulated": list(simulated.values()),
    }

    return baseline, simulated, delta, delta_pct, chart_data


def _simulate_compliance(conn, project_id, modifications):
    """Compliance dimension: coverage, POAMs, boundary tier, frameworks."""
    total_controls = _safe_query_val(
        conn, "SELECT COUNT(*) FROM project_controls WHERE project_id = ?", (project_id,),
    )
    implemented = _safe_query_val(
        conn,
        "SELECT COUNT(*) FROM project_controls WHERE project_id = ? AND implementation_status = 'implemented'",
        (project_id,),
    )
    not_implemented = max(0, total_controls - implemented)
    coverage = implemented / total_controls if total_controls > 0 else 0.0

    # Worst-case boundary tier
    tier_order = {"GREEN": 0, "YELLOW": 1, "ORANGE": 2, "RED": 3}
    worst_tier = "GREEN"
    try:
        rows = conn.execute(
            "SELECT impact_tier FROM boundary_impact_assessments WHERE project_id = ?",
            (project_id,),
        ).fetchall()
        for r in rows:
            if tier_order.get(r["impact_tier"], 0) > tier_order.get(worst_tier, 0):
                worst_tier = r["impact_tier"]
    except sqlite3.OperationalError:
        pass

    frameworks_affected = _safe_query_val(
        conn,
        "SELECT COUNT(DISTINCT framework_id) FROM project_framework_status WHERE project_id = ?",
        (project_id,),
    )

    # Modifications
    added_req = modifications.get("add_requirements", 0)
    change_class = modifications.get("change_classification", None)

    sim_total = total_controls + int(added_req * 0.6)  # new reqs trigger ~60% new controls
    sim_implemented = implemented  # existing stays
    sim_coverage = sim_implemented / sim_total if sim_total > 0 else 0.0
    sim_poam = max(0, sim_total - sim_implemented)

    sim_tier = worst_tier
    if change_class:
        sim_tier = "RED" if change_class == "IL6" else "ORANGE"
    elif added_req > 10:
        if tier_order.get(sim_tier, 0) < tier_order.get("YELLOW", 1):
            sim_tier = "YELLOW"

    sim_frameworks = frameworks_affected + (1 if change_class else 0)

    baseline = {
        "control_coverage": round(coverage, 4),
        "poam_projection": not_implemented,
        "boundary_tier": worst_tier,
        "frameworks_affected": frameworks_affected,
    }
    simulated = {
        "control_coverage": round(sim_coverage, 4),
        "poam_projection": sim_poam,
        "boundary_tier": sim_tier,
        "frameworks_affected": sim_frameworks,
    }
    delta = {
        "control_coverage": round(sim_coverage - coverage, 4),
        "poam_projection": sim_poam - not_implemented,
        "boundary_tier": f"{worst_tier} -> {sim_tier}",
        "frameworks_affected": sim_frameworks - frameworks_affected,
    }
    delta_pct = _pct(coverage, sim_coverage)

    chart_data = {
        "type": "radar",
        "labels": ["Coverage", "POAMs", "Frameworks"],
        "baseline": [coverage, not_implemented, frameworks_affected],
        "simulated": [sim_coverage, sim_poam, sim_frameworks],
    }

    return baseline, simulated, delta, delta_pct, chart_data


def _simulate_supply_chain(conn, project_id, modifications):
    """Supply chain dimension: vendors, dependencies, critical vendors, ISAs."""
    vendor_count = _safe_count(conn, "supply_chain_vendors", project_id)
    dep_count = _safe_count(conn, "supply_chain_dependencies", project_id)
    critical_vendors = _safe_query_val(
        conn,
        "SELECT COUNT(*) FROM supply_chain_vendors WHERE project_id = ? AND scrm_risk_tier = 'critical'",
        (project_id,),
    )
    isa_count = _safe_query_val(
        conn,
        "SELECT COUNT(*) FROM isa_agreements WHERE project_id = ? AND status IN ('active', 'expiring')",
        (project_id,),
    )

    add_vendor = modifications.get("add_vendor", 0)
    if isinstance(add_vendor, dict):
        add_vendor_count = 1
        is_critical = add_vendor.get("scrm_risk_tier") == "critical"
    elif isinstance(add_vendor, int):
        add_vendor_count = add_vendor
        is_critical = False
    else:
        add_vendor_count = 0
        is_critical = False

    sim_vendors = vendor_count + add_vendor_count
    sim_deps = dep_count + add_vendor_count * 2  # each vendor adds ~2 dependencies
    sim_critical = critical_vendors + (1 if is_critical else 0)
    sim_isa = isa_count + add_vendor_count  # each new vendor may need ISA review

    baseline = {
        "vendor_count": vendor_count,
        "dependency_count": dep_count,
        "critical_vendors": critical_vendors,
        "isa_changes": isa_count,
    }
    simulated = {
        "vendor_count": sim_vendors,
        "dependency_count": sim_deps,
        "critical_vendors": sim_critical,
        "isa_changes": sim_isa,
    }
    delta = {k: simulated[k] - baseline[k] for k in baseline}
    delta_pct = _pct(vendor_count, sim_vendors)

    chart_data = {
        "type": "bar",
        "labels": list(baseline.keys()),
        "baseline": list(baseline.values()),
        "simulated": list(simulated.values()),
    }

    return baseline, simulated, delta, delta_pct, chart_data


def _simulate_schedule(conn, project_id, modifications):
    """Schedule dimension: sprints, PIs, critical path items."""
    story_count = _safe_query_val(
        conn,
        "SELECT COUNT(*) FROM safe_decomposition WHERE project_id = ? AND level = 'story'",
        (project_id,),
    )
    critical_path = _safe_query_val(
        conn,
        "SELECT COUNT(*) FROM safe_decomposition WHERE project_id = ? AND wsjf_score > 5",
        (project_id,),
    )

    sprints = max(1, math.ceil(story_count / STORIES_PER_SPRINT)) if story_count > 0 else 0
    pi_count = max(1, math.ceil(sprints / SPRINTS_PER_PI)) if sprints > 0 else 0

    added_req = modifications.get("add_requirements", 0)
    removed_req = modifications.get("remove_requirements", 0)
    net_stories = max(0, story_count + added_req - removed_req)

    sim_sprints = max(1, math.ceil(net_stories / STORIES_PER_SPRINT)) if net_stories > 0 else 0
    sim_pi = max(1, math.ceil(sim_sprints / SPRINTS_PER_PI)) if sim_sprints > 0 else 0
    sim_critical = max(0, critical_path + int(added_req * 0.3))

    baseline = {
        "estimated_sprints": sprints,
        "pi_count": pi_count,
        "critical_path_items": critical_path,
        "story_count": story_count,
    }
    simulated = {
        "estimated_sprints": sim_sprints,
        "pi_count": sim_pi,
        "critical_path_items": sim_critical,
        "story_count": net_stories,
    }
    delta = {k: simulated[k] - baseline[k] for k in baseline}
    delta_pct = _pct(sprints, sim_sprints)

    chart_data = {
        "type": "bar",
        "labels": ["Sprints", "PIs", "Critical Path", "Stories"],
        "baseline": [sprints, pi_count, critical_path, story_count],
        "simulated": [sim_sprints, sim_pi, sim_critical, net_stories],
    }

    return baseline, simulated, delta, delta_pct, chart_data


def _simulate_cost(conn, project_id, modifications):
    """Cost dimension: total hours, total cost, infrastructure delta."""
    # Sum hours from t-shirt sizes in safe_decomposition
    total_hours = 0
    try:
        rows = conn.execute(
            "SELECT t_shirt_size, COUNT(*) AS cnt FROM safe_decomposition "
            "WHERE project_id = ? AND t_shirt_size IS NOT NULL GROUP BY t_shirt_size",
            (project_id,),
        ).fetchall()
        for r in rows:
            size = r["t_shirt_size"]
            total_hours += TSHIRT_HOURS.get(size, 80) * r["cnt"]
    except sqlite3.OperationalError:
        pass

    component_count = _safe_count(conn, "sysml_elements", project_id)
    infra_cost = component_count * INFRA_COST_PER_COMPONENT
    total_cost = total_hours * DEFAULT_HOURLY_RATE + infra_cost

    # Modifications
    added_req = modifications.get("add_requirements", 0)
    removed_req = modifications.get("remove_requirements", 0)
    arch_change = modifications.get("change_architecture", {})
    new_components = arch_change.get("add_components", 0)

    # New requirements are estimated as M (80h) by default
    sim_total_hours = max(0, total_hours + added_req * 80 - removed_req * 40)
    sim_component_count = max(0, component_count + added_req + new_components - removed_req)
    sim_infra = sim_component_count * INFRA_COST_PER_COMPONENT
    sim_total_cost = sim_total_hours * DEFAULT_HOURLY_RATE + sim_infra

    baseline = {
        "total_hours": total_hours,
        "total_cost": total_cost,
        "infrastructure_cost": infra_cost,
        "hourly_rate": DEFAULT_HOURLY_RATE,
    }
    simulated = {
        "total_hours": sim_total_hours,
        "total_cost": sim_total_cost,
        "infrastructure_cost": sim_infra,
        "hourly_rate": DEFAULT_HOURLY_RATE,
    }
    delta = {
        "total_hours": sim_total_hours - total_hours,
        "total_cost": sim_total_cost - total_cost,
        "infrastructure_delta": sim_infra - infra_cost,
        "hourly_rate": 0,
    }
    delta_pct = _pct(total_cost, sim_total_cost)

    chart_data = {
        "type": "stacked_bar",
        "labels": ["Labor ($)", "Infrastructure ($)"],
        "baseline": [total_hours * DEFAULT_HOURLY_RATE, infra_cost],
        "simulated": [sim_total_hours * DEFAULT_HOURLY_RATE, sim_infra],
    }

    return baseline, simulated, delta, delta_pct, chart_data


def _simulate_risk(conn, project_id, modifications):
    """Risk dimension: compound risk, risk count, mitigation effectiveness."""
    # Gather risk indicators
    risk_items = []

    # RED boundary items
    red_count = _safe_query_val(
        conn,
        "SELECT COUNT(*) FROM boundary_impact_assessments WHERE project_id = ? AND impact_tier = 'RED'",
        (project_id,),
    )
    if red_count > 0:
        risk_items.extend([0.3] * red_count)

    # Critical CVEs
    crit_cve = _safe_query_val(
        conn,
        "SELECT COUNT(*) FROM cve_triage WHERE project_id = ? AND severity = 'critical' AND triage_decision NOT IN ('false_positive', 'not_applicable')",
        (project_id,),
    )
    if crit_cve > 0:
        risk_items.extend([0.4] * crit_cve)

    # Expired ISAs
    expired_isa = _safe_query_val(
        conn,
        "SELECT COUNT(*) FROM isa_agreements WHERE project_id = ? AND status = 'expired'",
        (project_id,),
    )
    if expired_isa > 0:
        risk_items.extend([0.2] * expired_isa)

    # Critical STIG findings
    cat1 = _safe_query_val(
        conn,
        "SELECT COUNT(*) FROM stig_findings WHERE project_id = ? AND severity = 'CAT1' AND status = 'Open'",
        (project_id,),
    )
    if cat1 > 0:
        risk_items.extend([0.35] * cat1)

    total_risks = len(risk_items)
    if total_risks == 0:
        risk_items = [0.05]  # baseline low risk
        total_risks = 1

    compound = 1.0
    for p in risk_items:
        compound *= (1.0 - p)
    compound_risk = round(1.0 - compound, 4)

    # Count mitigated (we consider non-open as mitigated)
    mitigated = _safe_query_val(
        conn,
        "SELECT COUNT(*) FROM cve_triage WHERE project_id = ? AND triage_decision IN ('remediate', 'mitigate', 'false_positive', 'not_applicable')",
        (project_id,),
    )
    mitigation_eff = mitigated / total_risks if total_risks > 0 else 1.0

    # Modifications
    added_req = modifications.get("add_requirements", 0)
    add_vendor = modifications.get("add_vendor", 0)
    vendor_count = 1 if isinstance(add_vendor, dict) else (add_vendor if isinstance(add_vendor, int) else 0)

    new_risk_items = list(risk_items)
    if added_req > 5:
        new_risk_items.extend([0.15] * int(added_req / 5))
    if vendor_count > 0:
        new_risk_items.extend([0.1] * vendor_count)

    sim_total_risks = len(new_risk_items)
    sim_compound = 1.0
    for p in new_risk_items:
        sim_compound *= (1.0 - p)
    sim_compound_risk = round(1.0 - sim_compound, 4)

    sim_total_risks - mitigated
    sim_mitigation_eff = mitigated / sim_total_risks if sim_total_risks > 0 else 1.0

    baseline = {
        "compound_risk_score": compound_risk,
        "risk_count": total_risks,
        "mitigation_effectiveness": round(mitigation_eff, 4),
    }
    simulated = {
        "compound_risk_score": sim_compound_risk,
        "risk_count": sim_total_risks,
        "mitigation_effectiveness": round(sim_mitigation_eff, 4),
    }
    delta = {
        "compound_risk_score": round(sim_compound_risk - compound_risk, 4),
        "risk_count": sim_total_risks - total_risks,
        "mitigation_effectiveness": round(sim_mitigation_eff - mitigation_eff, 4),
    }
    delta_pct = _pct(compound_risk, sim_compound_risk)

    chart_data = {
        "type": "gauge",
        "labels": ["Compound Risk", "Risk Count", "Mitigation %"],
        "baseline": [compound_risk, total_risks, mitigation_eff],
        "simulated": [sim_compound_risk, sim_total_risks, sim_mitigation_eff],
    }

    return baseline, simulated, delta, delta_pct, chart_data


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _pct(baseline_val, simulated_val):
    """Compute percentage change, safe for zero baseline."""
    if isinstance(baseline_val, str) or isinstance(simulated_val, str):
        return 0.0
    if baseline_val == 0:
        return 100.0 if simulated_val != 0 else 0.0
    return round(((simulated_val - baseline_val) / abs(baseline_val)) * 100.0, 2)


def _impact_score(dimension_results):
    """Compute an overall impact score (0-100) from all dimension deltas."""
    if not dimension_results:
        return 0.0
    weights = {
        "architecture": 0.15,
        "compliance": 0.25,
        "supply_chain": 0.10,
        "schedule": 0.15,
        "cost": 0.20,
        "risk": 0.15,
    }
    score = 0.0
    for dim, data in dimension_results.items():
        pct = abs(data.get("delta_pct", 0.0))
        w = weights.get(dim, 0.1)
        score += min(pct, 100.0) * w
    return round(min(score, 100.0), 2)


def _generate_recommendations(dimension_results):
    """Generate simple recommendations based on simulation deltas."""
    recs = []
    for dim, data in dimension_results.items():
        pct = data.get("delta_pct", 0.0)
        if dim == "compliance" and pct < -5:
            recs.append("Compliance coverage decreased significantly. Plan additional control implementations before proceeding.")
        if dim == "cost" and pct > 20:
            recs.append(f"Cost increased by {pct:.1f}%. Consider phased delivery to spread budget impact across PIs.")
        if dim == "schedule" and pct > 15:
            recs.append(f"Schedule extended by {pct:.1f}%. Evaluate scope reduction or parallel work streams.")
        if dim == "risk" and pct > 10:
            recs.append("Risk profile increased. Conduct targeted risk mitigation before accepting scenario.")
        if dim == "supply_chain":
            sc_delta = data.get("delta", {})
            if isinstance(sc_delta, dict) and sc_delta.get("critical_vendors", 0) > 0:
                recs.append("New critical vendor added. Initiate SCRM assessment and ISA review immediately.")
        if dim == "architecture":
            arch_delta = data.get("delta", {})
            if isinstance(arch_delta, dict) and arch_delta.get("coupling_score", 0) > 0.5:
                recs.append("Coupling score increasing. Review architecture for modularity opportunities.")
    if not recs:
        recs.append("No significant concerns detected. Scenario impact is within acceptable thresholds.")
    return recs


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

DIMENSION_SIMULATORS = {
    "architecture": _simulate_architecture,
    "compliance": _simulate_compliance,
    "supply_chain": _simulate_supply_chain,
    "schedule": _simulate_schedule,
    "cost": _simulate_cost,
    "risk": _simulate_risk,
}


def create_scenario(project_id, scenario_name, scenario_type, modifications,
                    base_session_id=None, db_path=None):
    """Create a new simulation scenario.

    Args:
        project_id: Project identifier.
        scenario_name: Human-readable name.
        scenario_type: One of 'what_if', 'coa_comparison', 'risk_analysis'.
        modifications: Dict describing changes to simulate.
        base_session_id: Optional intake session to anchor the scenario.
        db_path: Optional database path override.

    Returns:
        dict with scenario_id, scenario_name, scenario_type, status.
    """
    conn = _get_connection(db_path)
    try:
        scenario_id = str(uuid4())
        now = datetime.now(timezone.utc).isoformat()
        base_state = json.dumps(_snapshot_project_state(conn, project_id))

        # Map scenario_type to DB-compatible value
        db_type_map = {
            "what_if": "what_if",
            "coa_comparison": "coa_comparison",
            "risk_analysis": "risk_monte_carlo",
        }
        db_scenario_type = db_type_map.get(scenario_type, scenario_type)

        conn.execute(
            """INSERT INTO simulation_scenarios
               (id, project_id, session_id, scenario_name, scenario_type,
                base_state, modifications, status, classification,
                created_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                scenario_id,
                project_id,
                base_session_id,
                scenario_name,
                db_scenario_type,
                base_state,
                json.dumps(modifications),
                "pending",
                "CUI",
                "icdev-simulation-engine",
                now,
            ),
        )
        conn.commit()

        _log_audit(conn, project_id, "simulation_created",
                   f"Scenario '{scenario_name}' created",
                   {"scenario_id": scenario_id, "scenario_type": scenario_type})

        return {
            "scenario_id": scenario_id,
            "scenario_name": scenario_name,
            "scenario_type": scenario_type,
            "status": "pending",
        }
    finally:
        conn.close()


def run_simulation(scenario_id, dimensions=None, db_path=None):
    """Execute a simulation across specified dimensions.

    Args:
        scenario_id: UUID of the scenario to run.
        dimensions: List of dimension names, or None for all 6.
        db_path: Optional database path override.

    Returns:
        dict with scenario_id, dimensions results, overall_impact_score,
        and recommendations.
    """
    if dimensions is None or dimensions == ["all"] or dimensions == "all":
        dimensions = list(ALL_DIMENSIONS)

    conn = _get_connection(db_path)
    try:
        # Load scenario
        row = conn.execute(
            "SELECT * FROM simulation_scenarios WHERE id = ?", (scenario_id,),
        ).fetchone()
        if not row:
            raise ValueError(f"Scenario not found: {scenario_id}")

        scenario = dict(row)
        project_id = scenario["project_id"]
        modifications = json.loads(scenario["modifications"])

        # Update status to running
        conn.execute(
            "UPDATE simulation_scenarios SET status = 'running' WHERE id = ?",
            (scenario_id,),
        )
        conn.commit()

        dimension_results = {}
        for dim in dimensions:
            if dim not in DIMENSION_SIMULATORS:
                print(f"Warning: unknown dimension '{dim}', skipping.", file=sys.stderr)
                continue

            simulator = DIMENSION_SIMULATORS[dim]
            baseline, simulated, delta, delta_pct, chart_data = simulator(
                conn, project_id, modifications
            )

            # Persist each dimension result
            str(uuid4())
            conn.execute(
                """INSERT INTO simulation_results
                   (scenario_id, dimension, metric_name,
                    baseline_value, simulated_value, delta, delta_pct,
                    details, visualizations, calculated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    scenario_id,
                    dim,
                    dim,
                    json.dumps(baseline) if isinstance(baseline, dict) else baseline,
                    json.dumps(simulated) if isinstance(simulated, dict) else simulated,
                    json.dumps(delta) if isinstance(delta, dict) else delta,
                    delta_pct,
                    json.dumps({"baseline": baseline, "simulated": simulated, "delta": delta}),
                    json.dumps(chart_data),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

            dimension_results[dim] = {
                "baseline": baseline,
                "simulated": simulated,
                "delta": delta,
                "delta_pct": delta_pct,
                "chart_data": chart_data,
            }

        # Compute overall impact
        overall_impact = _impact_score(dimension_results)
        recommendations = _generate_recommendations(dimension_results)

        # Update scenario to completed
        conn.execute(
            "UPDATE simulation_scenarios SET status = 'completed', completed_at = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), scenario_id),
        )
        conn.commit()

        _log_audit(conn, project_id, "simulation_completed",
                   f"Simulation completed for scenario {scenario_id}",
                   {"scenario_id": scenario_id, "dimensions": dimensions,
                    "overall_impact": overall_impact})

        return {
            "scenario_id": scenario_id,
            "dimensions": dimension_results,
            "overall_impact_score": overall_impact,
            "recommendations": recommendations,
        }
    except Exception as exc:
        # Mark failed
        try:
            conn.execute(
                "UPDATE simulation_scenarios SET status = 'failed' WHERE id = ?",
                (scenario_id,),
            )
            conn.commit()
        except Exception:
            pass
        raise exc
    finally:
        conn.close()


def get_scenario(scenario_id, db_path=None):
    """Get scenario details with results.

    Args:
        scenario_id: UUID of the scenario.
        db_path: Optional database path override.

    Returns:
        dict with scenario metadata and results per dimension.
    """
    conn = _get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM simulation_scenarios WHERE id = ?", (scenario_id,),
        ).fetchone()
        if not row:
            raise ValueError(f"Scenario not found: {scenario_id}")

        scenario = dict(row)
        # Parse JSON fields
        for field in ("base_state", "modifications"):
            if scenario.get(field):
                try:
                    scenario[field] = json.loads(scenario[field])
                except (json.JSONDecodeError, TypeError):
                    pass

        # Fetch results
        results = conn.execute(
            "SELECT * FROM simulation_results WHERE scenario_id = ? ORDER BY dimension",
            (scenario_id,),
        ).fetchall()

        dim_results = {}
        for r in results:
            rd = dict(r)
            for field in ("details", "visualizations"):
                if rd.get(field):
                    try:
                        rd[field] = json.loads(rd[field])
                    except (json.JSONDecodeError, TypeError):
                        pass
            dim_results[rd["dimension"]] = rd

        scenario["results"] = dim_results
        return scenario
    finally:
        conn.close()


def list_scenarios(project_id, db_path=None):
    """List all scenarios for a project.

    Args:
        project_id: Project identifier.
        db_path: Optional database path override.

    Returns:
        dict with project_id and list of scenarios.
    """
    conn = _get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT id, scenario_name, scenario_type, status, created_at, completed_at "
            "FROM simulation_scenarios WHERE project_id = ? ORDER BY created_at DESC",
            (project_id,),
        ).fetchall()

        scenarios = [dict(r) for r in rows]
        return {"project_id": project_id, "scenarios": scenarios, "count": len(scenarios)}
    finally:
        conn.close()


def compare_scenarios(scenario_id_1, scenario_id_2, db_path=None):
    """Side-by-side comparison of two scenarios across all dimensions.

    Args:
        scenario_id_1: UUID of first scenario.
        scenario_id_2: UUID of second scenario.
        db_path: Optional database path override.

    Returns:
        dict with scenario_1, scenario_2, and comparison per dimension.
    """
    conn = _get_connection(db_path)
    try:
        s1 = conn.execute(
            "SELECT * FROM simulation_scenarios WHERE id = ?", (scenario_id_1,),
        ).fetchone()
        s2 = conn.execute(
            "SELECT * FROM simulation_scenarios WHERE id = ?", (scenario_id_2,),
        ).fetchone()
        if not s1:
            raise ValueError(f"Scenario not found: {scenario_id_1}")
        if not s2:
            raise ValueError(f"Scenario not found: {scenario_id_2}")

        # Fetch results for both
        def _get_results(sid):
            rows = conn.execute(
                "SELECT * FROM simulation_results WHERE scenario_id = ?", (sid,),
            ).fetchall()
            by_dim = {}
            for r in rows:
                rd = dict(r)
                for field in ("details", "visualizations"):
                    if rd.get(field):
                        try:
                            rd[field] = json.loads(rd[field])
                        except (json.JSONDecodeError, TypeError):
                            pass
                by_dim[rd["dimension"]] = rd
            return by_dim

        r1 = _get_results(scenario_id_1)
        r2 = _get_results(scenario_id_2)

        all_dims = set(list(r1.keys()) + list(r2.keys()))
        comparison = {}
        for dim in sorted(all_dims):
            d1 = r1.get(dim, {})
            d2 = r2.get(dim, {})
            pct1 = d1.get("delta_pct", 0.0) or 0.0
            pct2 = d2.get("delta_pct", 0.0) or 0.0
            # Lower absolute delta_pct is better (less disruption)
            if abs(pct1) < abs(pct2):
                winner = "scenario_1"
            elif abs(pct2) < abs(pct1):
                winner = "scenario_2"
            else:
                winner = "tie"
            comparison[dim] = {
                "scenario_1_value": d1.get("delta_pct", 0.0),
                "scenario_2_value": d2.get("delta_pct", 0.0),
                "winner": winner,
            }

        # Log audit
        project_id = dict(s1).get("project_id", "unknown")
        _log_audit(conn, project_id, "coa_compared",
                   f"Compared scenarios {scenario_id_1} vs {scenario_id_2}",
                   {"scenario_1": scenario_id_1, "scenario_2": scenario_id_2})

        return {
            "scenario_1": {"id": scenario_id_1, "name": dict(s1).get("scenario_name")},
            "scenario_2": {"id": scenario_id_2, "name": dict(s2).get("scenario_name")},
            "comparison": comparison,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="RICOAS Digital Program Twin — 6-dimension simulation engine"
    )
    parser.add_argument("--project-id", help="Project ID")
    parser.add_argument("--scenario-id", help="Scenario UUID")
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    # Actions
    parser.add_argument("--create-scenario", action="store_true", help="Create a new scenario")
    parser.add_argument("--run", action="store_true", help="Run simulation on a scenario")
    parser.add_argument("--get", action="store_true", help="Get scenario details and results")
    parser.add_argument("--list", action="store_true", help="List all scenarios for a project")
    parser.add_argument("--compare", action="store_true", help="Compare two scenarios")

    # Create args
    parser.add_argument("--scenario-name", help="Scenario name (for --create-scenario)")
    parser.add_argument("--scenario-type", default="what_if",
                        choices=["what_if", "coa_comparison", "risk_analysis"],
                        help="Scenario type")
    parser.add_argument("--modifications", help="JSON string of modifications")
    parser.add_argument("--base-session-id", help="Base intake session ID")

    # Run args
    parser.add_argument("--dimensions", default="all",
                        help="Comma-separated dimensions or 'all'")

    # Compare args
    parser.add_argument("--scenario-1", help="First scenario ID for comparison")
    parser.add_argument("--scenario-2", help="Second scenario ID for comparison")

    # DB override
    parser.add_argument("--db", help="Database path override")

    args = parser.parse_args()
    db_path = Path(args.db) if args.db else None

    try:
        if args.create_scenario:
            if not args.project_id:
                parser.error("--project-id is required for --create-scenario")
            if not args.scenario_name:
                parser.error("--scenario-name is required for --create-scenario")
            mods = json.loads(args.modifications) if args.modifications else {}
            result = create_scenario(
                project_id=args.project_id,
                scenario_name=args.scenario_name,
                scenario_type=args.scenario_type,
                modifications=mods,
                base_session_id=args.base_session_id,
                db_path=db_path,
            )

        elif args.run:
            if not args.scenario_id:
                parser.error("--scenario-id is required for --run")
            dims = None if args.dimensions == "all" else args.dimensions.split(",")
            result = run_simulation(
                scenario_id=args.scenario_id,
                dimensions=dims,
                db_path=db_path,
            )

        elif args.get:
            if not args.scenario_id:
                parser.error("--scenario-id is required for --get")
            result = get_scenario(
                scenario_id=args.scenario_id,
                db_path=db_path,
            )

        elif args.list:
            if not args.project_id:
                parser.error("--project-id is required for --list")
            result = list_scenarios(
                project_id=args.project_id,
                db_path=db_path,
            )

        elif args.compare:
            if not args.scenario_1 or not args.scenario_2:
                parser.error("--scenario-1 and --scenario-2 are required for --compare")
            result = compare_scenarios(
                scenario_id_1=args.scenario_1,
                scenario_id_2=args.scenario_2,
                db_path=db_path,
            )

        else:
            parser.print_help()
            sys.exit(0)

        # Output
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            _print_human(result)

    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as exc:
        print(f"ERROR: Invalid JSON: {exc}", file=sys.stderr)
        sys.exit(1)


def _print_human(result):
    """Pretty-print results for human consumption."""
    if "scenario_id" in result and "dimensions" in result:
        # run_simulation result
        print(f"Scenario: {result['scenario_id']}")
        print(f"Overall Impact Score: {result.get('overall_impact_score', 'N/A')}")
        print()
        for dim, data in result["dimensions"].items():
            print(f"--- {dim.upper()} ---")
            print(f"  Delta %: {data.get('delta_pct', 0.0):.2f}%")
            if isinstance(data.get("baseline"), dict):
                for k, v in data["baseline"].items():
                    sim_v = data.get("simulated", {}).get(k, "?")
                    print(f"  {k}: {v} -> {sim_v}")
            print()
        print("Recommendations:")
        for rec in result.get("recommendations", []):
            print(f"  - {rec}")

    elif "scenarios" in result:
        # list_scenarios result
        print(f"Project: {result['project_id']}  |  Scenarios: {result['count']}")
        for s in result["scenarios"]:
            print(f"  [{s['status']}] {s['id'][:8]}... {s['scenario_name']} ({s['scenario_type']})")

    elif "comparison" in result:
        # compare_scenarios result
        s1 = result["scenario_1"]
        s2 = result["scenario_2"]
        print(f"Scenario 1: {s1['name']} ({s1['id'][:8]}...)")
        print(f"Scenario 2: {s2['name']} ({s2['id'][:8]}...)")
        print()
        for dim, comp in result["comparison"].items():
            print(f"  {dim}: S1={comp['scenario_1_value']:.2f}%  S2={comp['scenario_2_value']:.2f}%  Winner={comp['winner']}")

    elif "scenario_id" in result and "status" in result:
        # create_scenario or get_scenario result
        for k, v in result.items():
            if k == "results" and isinstance(v, dict):
                print(f"  results: {len(v)} dimensions")
            elif isinstance(v, dict):
                print(f"  {k}: {json.dumps(v, default=str)[:120]}...")
            else:
                print(f"  {k}: {v}")
    else:
        print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
# [TEMPLATE: CUI // SP-CTI]
