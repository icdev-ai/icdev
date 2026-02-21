#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""Scenario Manager for the ICDEV RICOAS Digital Program Twin.

Save, load, fork, compare, and archive simulation scenarios.  Supports
exporting/importing scenarios as JSON files for sharing between environments.

Usage:
    # Fork an existing scenario with additional modifications
    python tools/simulation/scenario_manager.py --scenario-id <id> --fork \\
        --new-name "Scenario B" --json

    # Archive a scenario
    python tools/simulation/scenario_manager.py --scenario-id <id> --archive --json

    # Delete a scenario (soft delete)
    python tools/simulation/scenario_manager.py --scenario-id <id> --delete --json

    # Export scenario to JSON file
    python tools/simulation/scenario_manager.py --scenario-id <id> --export \\
        --output-path /tmp/scenario.json --json

    # Import scenario from JSON file
    python tools/simulation/scenario_manager.py --project-id <id> --import \\
        --input-path /tmp/scenario.json --json

    # List scenarios for a project
    python tools/simulation/scenario_manager.py --project-id <id> --list --json

    # List including archived
    python tools/simulation/scenario_manager.py --project-id <id> --list \\
        --include-archived --json

    # Get scenario summary
    python tools/simulation/scenario_manager.py --scenario-id <id> --summary --json

    # Compare multiple scenarios
    python tools/simulation/scenario_manager.py --compare \\
        --scenario-ids <id1>,<id2>,<id3> --json

Databases:
    - data/icdev.db: simulation_scenarios, simulation_results, monte_carlo_runs
"""

import argparse
import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

# Graceful import of audit logger
try:
    from tools.audit.audit_logger import log_event
    _HAS_AUDIT = True
except ImportError:
    _HAS_AUDIT = False
    def log_event(**kwargs) -> int:  # type: ignore[misc]
        return -1


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def _get_connection(db_path=None):
    """Get database connection with dict-like row access."""
    path = db_path or DB_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"Database not found: {path}\nRun: python tools/db/init_icdev_db.py"
        )
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _generate_id(prefix="sim"):
    """Generate a unique ID with prefix."""
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _now_iso():
    """Return current UTC timestamp in ISO format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_json_field(value):
    """Safely parse a JSON string field, returning the value as-is if not a string."""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _load_scenario(conn, scenario_id):
    """Load a scenario record or raise ValueError."""
    row = conn.execute(
        "SELECT * FROM simulation_scenarios WHERE id = ?",
        (scenario_id,),
    ).fetchone()
    if not row:
        raise ValueError(f"Scenario not found: {scenario_id}")
    return dict(row)


def _load_scenario_results(conn, scenario_id):
    """Load all simulation results for a scenario."""
    rows = conn.execute(
        "SELECT * FROM simulation_results WHERE scenario_id = ? ORDER BY dimension, metric_name",
        (scenario_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _load_monte_carlo_runs(conn, scenario_id):
    """Load all Monte Carlo runs for a scenario."""
    rows = conn.execute(
        "SELECT * FROM monte_carlo_runs WHERE scenario_id = ? ORDER BY dimension",
        (scenario_id,),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def fork_scenario(scenario_id, new_name, additional_modifications=None, db_path=None):
    """Create a new scenario based on an existing one with additional modifications.

    Copies the base state and merges any additional modifications on top of
    the original modifications.

    Args:
        scenario_id: The source scenario ID to fork from.
        new_name: Name for the forked scenario.
        additional_modifications: Optional dict of extra modifications.
        db_path: Optional database path override.

    Returns:
        dict with new_scenario_id, forked_from, and new_name.
    """
    conn = _get_connection(db_path)
    try:
        source = _load_scenario(conn, scenario_id)
        now = _now_iso()
        new_id = _generate_id("sim")

        # Parse base state and modifications from source
        base_state = _parse_json_field(source.get("base_state")) or {}
        source_mods = _parse_json_field(source.get("modifications")) or {}

        # Merge additional modifications
        merged_mods = dict(source_mods)
        if additional_modifications and isinstance(additional_modifications, dict):
            merged_mods.update(additional_modifications)
        merged_mods["forked_from"] = scenario_id
        merged_mods["forked_at"] = now

        conn.execute(
            """INSERT INTO simulation_scenarios
               (id, project_id, session_id, scenario_name, scenario_type,
                base_state, modifications, status, classification,
                created_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                new_id,
                source.get("project_id"),
                source.get("session_id"),
                new_name,
                source.get("scenario_type", "what_if"),
                json.dumps(base_state),
                json.dumps(merged_mods),
                "pending",
                source.get("classification", "CUI"),
                "icdev-simulation-engine",
                now,
            ),
        )

        conn.commit()

        # Audit
        if _HAS_AUDIT:
            log_event(
                event_type="simulation_created",
                actor="icdev-simulation-engine",
                action=f"Forked scenario {scenario_id} as {new_id} ({new_name})",
                project_id=source.get("project_id"),
                details=json.dumps({
                    "new_scenario_id": new_id,
                    "forked_from": scenario_id,
                    "new_name": new_name,
                    "has_additional_mods": additional_modifications is not None,
                }),
            )

        return {
            "new_scenario_id": new_id,
            "forked_from": scenario_id,
            "new_name": new_name,
            "project_id": source.get("project_id"),
            "status": "pending",
        }

    finally:
        conn.close()


def archive_scenario(scenario_id, db_path=None):
    """Mark a scenario as archived.

    Args:
        scenario_id: The scenario ID to archive.
        db_path: Optional database path override.

    Returns:
        dict with scenario_id and status.
    """
    conn = _get_connection(db_path)
    try:
        scenario = _load_scenario(conn, scenario_id)
        now = _now_iso()

        conn.execute(
            "UPDATE simulation_scenarios SET status = 'archived', completed_at = ? WHERE id = ?",
            (now, scenario_id),
        )
        conn.commit()

        if _HAS_AUDIT:
            log_event(
                event_type="simulation_completed",
                actor="icdev-simulation-engine",
                action=f"Archived scenario {scenario_id}",
                project_id=scenario.get("project_id"),
                details=json.dumps({"scenario_id": scenario_id, "action": "archive"}),
            )

        return {
            "scenario_id": scenario_id,
            "scenario_name": scenario.get("scenario_name"),
            "status": "archived",
        }

    finally:
        conn.close()


def delete_scenario(scenario_id, db_path=None):
    """Soft-delete a scenario by setting its status to 'deleted'.

    Note: This is a soft delete.  The record remains in the database for
    audit trail purposes but is excluded from normal queries.  The status
    value 'deleted' is not in the original CHECK constraint, so we use
    'failed' as a proxy status to stay within schema constraints while
    recording the intent in the modifications field.

    Args:
        scenario_id: The scenario ID to delete.
        db_path: Optional database path override.

    Returns:
        dict confirming the soft deletion.
    """
    conn = _get_connection(db_path)
    try:
        scenario = _load_scenario(conn, scenario_id)
        now = _now_iso()

        # Record deletion intent in modifications
        mods = _parse_json_field(scenario.get("modifications")) or {}
        mods["soft_deleted"] = True
        mods["deleted_at"] = now

        # Use 'archived' status (within CHECK constraint) to mark as removed
        conn.execute(
            """UPDATE simulation_scenarios
               SET status = 'archived', modifications = ?, completed_at = ?
               WHERE id = ?""",
            (json.dumps(mods), now, scenario_id),
        )
        conn.commit()

        if _HAS_AUDIT:
            log_event(
                event_type="simulation_completed",
                actor="icdev-simulation-engine",
                action=f"Soft-deleted scenario {scenario_id}",
                project_id=scenario.get("project_id"),
                details=json.dumps({"scenario_id": scenario_id, "action": "soft_delete"}),
            )

        return {
            "scenario_id": scenario_id,
            "scenario_name": scenario.get("scenario_name"),
            "status": "deleted",
            "message": "Scenario soft-deleted (marked as archived with deletion flag)",
        }

    finally:
        conn.close()


def export_scenario(scenario_id, output_path, db_path=None):
    """Export a scenario and its results as a JSON file.

    Includes the scenario record, all simulation results, and any Monte
    Carlo runs.

    Args:
        scenario_id: The scenario ID to export.
        output_path: File path to write the JSON export.
        db_path: Optional database path override.

    Returns:
        dict with scenario_id, output_path, and size_bytes.
    """
    conn = _get_connection(db_path)
    try:
        scenario = _load_scenario(conn, scenario_id)
        results = _load_scenario_results(conn, scenario_id)
        mc_runs = _load_monte_carlo_runs(conn, scenario_id)

        # Parse JSON fields in scenario
        for field in ("base_state", "modifications"):
            scenario[field] = _parse_json_field(scenario.get(field))

        # Parse JSON fields in results
        for r in results:
            r["details"] = _parse_json_field(r.get("details"))
            r["visualizations"] = _parse_json_field(r.get("visualizations"))

        # Parse JSON fields in MC runs
        for mc in mc_runs:
            for field in ("input_parameters", "histogram_data", "cdf_data", "confidence_intervals"):
                mc[field] = _parse_json_field(mc.get(field))

        export_data = {
            "export_version": "1.0",
            "exported_at": _now_iso(),
            "classification": "CUI // SP-CTI",
            "scenario": scenario,
            "results": results,
            "monte_carlo_runs": mc_runs,
        }

        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(export_data, f, indent=2, default=str)

        size_bytes = out.stat().st_size

        if _HAS_AUDIT:
            log_event(
                event_type="simulation_completed",
                actor="icdev-simulation-engine",
                action=f"Exported scenario {scenario_id} to {output_path}",
                project_id=scenario.get("project_id"),
                details=json.dumps({
                    "scenario_id": scenario_id,
                    "output_path": str(output_path),
                    "size_bytes": size_bytes,
                    "results_count": len(results),
                    "mc_runs_count": len(mc_runs),
                }),
            )

        return {
            "scenario_id": scenario_id,
            "output_path": str(out),
            "size_bytes": size_bytes,
            "results_exported": len(results),
            "monte_carlo_runs_exported": len(mc_runs),
        }

    finally:
        conn.close()


def import_scenario(project_id, input_path, db_path=None):
    """Import a scenario from a JSON file.

    Creates a new scenario record with a fresh ID (to avoid collisions)
    and inserts all associated results and Monte Carlo runs.

    Args:
        project_id: The project ID to associate the imported scenario with.
        input_path: File path of the JSON export to import.
        db_path: Optional database path override.

    Returns:
        dict with new_scenario_id.
    """
    inp = Path(input_path)
    if not inp.exists():
        raise FileNotFoundError(f"Import file not found: {input_path}")

    with open(inp, "r", encoding="utf-8") as f:
        data = json.load(f)

    scenario = data.get("scenario")
    if not scenario:
        raise ValueError("Import file does not contain a 'scenario' object")

    results = data.get("results", [])
    mc_runs = data.get("monte_carlo_runs", [])

    conn = _get_connection(db_path)
    try:
        now = _now_iso()
        new_id = _generate_id("sim")
        old_id = scenario.get("id", "unknown")

        # Build base_state and modifications
        base_state = scenario.get("base_state")
        if isinstance(base_state, dict):
            base_state = json.dumps(base_state)
        elif base_state is None:
            base_state = json.dumps({})

        modifications = scenario.get("modifications")
        if isinstance(modifications, dict):
            modifications["imported_from"] = old_id
            modifications["imported_at"] = now
            modifications["source_file"] = str(input_path)
            modifications = json.dumps(modifications)
        elif modifications is None:
            modifications = json.dumps({
                "imported_from": old_id,
                "imported_at": now,
                "source_file": str(input_path),
            })
        else:
            # String — try parse, augment, re-serialize
            try:
                mods = json.loads(modifications)
                mods["imported_from"] = old_id
                mods["imported_at"] = now
                mods["source_file"] = str(input_path)
                modifications = json.dumps(mods)
            except json.JSONDecodeError:
                modifications = json.dumps({
                    "imported_from": old_id,
                    "imported_at": now,
                    "source_file": str(input_path),
                    "original_modifications": modifications,
                })

        conn.execute(
            """INSERT INTO simulation_scenarios
               (id, project_id, session_id, scenario_name, scenario_type,
                base_state, modifications, status, classification,
                created_by, created_at, completed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                new_id,
                project_id,
                scenario.get("session_id"),
                scenario.get("scenario_name", "Imported Scenario"),
                scenario.get("scenario_type", "what_if"),
                base_state,
                modifications,
                scenario.get("status", "completed"),
                scenario.get("classification", "CUI"),
                "icdev-simulation-engine",
                now,
                scenario.get("completed_at"),
            ),
        )

        # Import results
        for r in results:
            details = r.get("details")
            if isinstance(details, dict):
                details = json.dumps(details)
            visualizations = r.get("visualizations")
            if isinstance(visualizations, dict):
                visualizations = json.dumps(visualizations)

            conn.execute(
                """INSERT INTO simulation_results
                   (scenario_id, dimension, metric_name, baseline_value,
                    simulated_value, delta, delta_pct, confidence, impact_tier,
                    details, visualizations, calculated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    new_id,
                    r.get("dimension"),
                    r.get("metric_name"),
                    r.get("baseline_value"),
                    r.get("simulated_value"),
                    r.get("delta"),
                    r.get("delta_pct"),
                    r.get("confidence", 0.0),
                    r.get("impact_tier"),
                    details,
                    visualizations,
                    r.get("calculated_at", now),
                ),
            )

        # Import Monte Carlo runs
        for mc in mc_runs:
            mc_new_id = _generate_id("mc")
            input_params = mc.get("input_parameters")
            if isinstance(input_params, dict):
                input_params = json.dumps(input_params)
            elif input_params is None:
                input_params = json.dumps({})

            histogram = mc.get("histogram_data")
            if isinstance(histogram, (dict, list)):
                histogram = json.dumps(histogram)
            cdf = mc.get("cdf_data")
            if isinstance(cdf, (dict, list)):
                cdf = json.dumps(cdf)
            ci = mc.get("confidence_intervals")
            if isinstance(ci, (dict, list)):
                ci = json.dumps(ci)

            conn.execute(
                """INSERT INTO monte_carlo_runs
                   (id, scenario_id, iterations, dimension, distribution_type,
                    input_parameters, p10_value, p50_value, p80_value, p90_value,
                    mean_value, std_deviation, histogram_data, cdf_data,
                    confidence_intervals, run_duration_ms, completed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    mc_new_id, new_id,
                    mc.get("iterations", 10000),
                    mc.get("dimension", "schedule"),
                    mc.get("distribution_type", "pert"),
                    input_params,
                    mc.get("p10_value"),
                    mc.get("p50_value"),
                    mc.get("p80_value"),
                    mc.get("p90_value"),
                    mc.get("mean_value"),
                    mc.get("std_deviation"),
                    histogram, cdf, ci,
                    mc.get("run_duration_ms"),
                    mc.get("completed_at", now),
                ),
            )

        conn.commit()

        if _HAS_AUDIT:
            log_event(
                event_type="simulation_created",
                actor="icdev-simulation-engine",
                action=f"Imported scenario from {input_path} as {new_id}",
                project_id=project_id,
                details=json.dumps({
                    "new_scenario_id": new_id,
                    "imported_from_file": str(input_path),
                    "original_id": old_id,
                    "results_imported": len(results),
                    "mc_runs_imported": len(mc_runs),
                }),
            )

        return {
            "new_scenario_id": new_id,
            "imported_from": str(input_path),
            "original_scenario_id": old_id,
            "results_imported": len(results),
            "monte_carlo_runs_imported": len(mc_runs),
        }

    finally:
        conn.close()


def list_scenarios(project_id, include_archived=False, db_path=None):
    """List scenarios for a project.

    Args:
        project_id: The project ID.
        include_archived: If True, include archived scenarios.
        db_path: Optional database path override.

    Returns:
        dict with project_id and scenarios list.
    """
    conn = _get_connection(db_path)
    try:
        if include_archived:
            rows = conn.execute(
                """SELECT id, project_id, session_id, scenario_name, scenario_type,
                          status, classification, created_by, created_at, completed_at
                   FROM simulation_scenarios
                   WHERE project_id = ?
                   ORDER BY created_at DESC""",
                (project_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT id, project_id, session_id, scenario_name, scenario_type,
                          status, classification, created_by, created_at, completed_at
                   FROM simulation_scenarios
                   WHERE project_id = ? AND status != 'archived'
                   ORDER BY created_at DESC""",
                (project_id,),
            ).fetchall()

        scenarios = []
        for row in rows:
            s = dict(row)
            # Check for soft-delete flag
            # (We need modifications to detect soft-deleted scenarios)
            full_row = conn.execute(
                "SELECT modifications FROM simulation_scenarios WHERE id = ?",
                (s["id"],),
            ).fetchone()
            if full_row:
                mods = _parse_json_field(full_row["modifications"])
                if isinstance(mods, dict) and mods.get("soft_deleted"):
                    if not include_archived:
                        continue
                    s["soft_deleted"] = True

            scenarios.append(s)

        return {
            "project_id": project_id,
            "count": len(scenarios),
            "include_archived": include_archived,
            "scenarios": scenarios,
        }

    finally:
        conn.close()


def get_scenario_summary(scenario_id, db_path=None):
    """Get a compact summary of a scenario with all dimension results.

    Args:
        scenario_id: The scenario ID.
        db_path: Optional database path override.

    Returns:
        dict with scenario metadata and summarized results.
    """
    conn = _get_connection(db_path)
    try:
        scenario = _load_scenario(conn, scenario_id)
        results = _load_scenario_results(conn, scenario_id)
        mc_runs = _load_monte_carlo_runs(conn, scenario_id)

        # Parse JSON fields
        scenario["base_state"] = _parse_json_field(scenario.get("base_state"))
        scenario["modifications"] = _parse_json_field(scenario.get("modifications"))

        # Group results by dimension
        by_dimension = {}
        for r in results:
            dim = r.get("dimension", "unknown")
            if dim not in by_dimension:
                by_dimension[dim] = []
            by_dimension[dim].append({
                "metric": r.get("metric_name"),
                "baseline": r.get("baseline_value"),
                "simulated": r.get("simulated_value"),
                "delta": r.get("delta"),
                "delta_pct": r.get("delta_pct"),
                "confidence": r.get("confidence"),
                "impact_tier": r.get("impact_tier"),
            })

        # Summarize Monte Carlo runs
        mc_summary = []
        for mc in mc_runs:
            mc_summary.append({
                "dimension": mc.get("dimension"),
                "iterations": mc.get("iterations"),
                "distribution": mc.get("distribution_type"),
                "p10": mc.get("p10_value"),
                "p50": mc.get("p50_value"),
                "p80": mc.get("p80_value"),
                "p90": mc.get("p90_value"),
                "mean": mc.get("mean_value"),
                "std_dev": mc.get("std_deviation"),
            })

        # Overall impact assessment
        tiers_found = [r.get("impact_tier") for r in results if r.get("impact_tier")]
        tier_rank = {"GREEN": 1, "YELLOW": 2, "ORANGE": 3, "RED": 4}
        worst_tier = "GREEN"
        if tiers_found:
            worst_rank = max(tier_rank.get(t, 1) for t in tiers_found)
            rank_to_tier = {v: k for k, v in tier_rank.items()}
            worst_tier = rank_to_tier.get(worst_rank, "GREEN")

        return {
            "scenario_id": scenario_id,
            "scenario_name": scenario.get("scenario_name"),
            "scenario_type": scenario.get("scenario_type"),
            "project_id": scenario.get("project_id"),
            "session_id": scenario.get("session_id"),
            "status": scenario.get("status"),
            "created_at": scenario.get("created_at"),
            "completed_at": scenario.get("completed_at"),
            "base_state": scenario.get("base_state"),
            "modifications": scenario.get("modifications"),
            "dimensions": by_dimension,
            "monte_carlo": mc_summary,
            "result_count": len(results),
            "overall_impact_tier": worst_tier,
        }

    finally:
        conn.close()


def compare_multiple(scenario_ids, db_path=None):
    """Compare N scenarios across all dimensions.

    Builds a matrix of metric values for each scenario in each dimension.

    Args:
        scenario_ids: List of scenario IDs to compare.
        db_path: Optional database path override.

    Returns:
        dict with comparison matrix in a structured format.
    """
    if not scenario_ids or len(scenario_ids) < 2:
        raise ValueError("At least 2 scenario IDs are required for comparison")

    conn = _get_connection(db_path)
    try:
        scenarios = {}
        all_results = {}

        for sid in scenario_ids:
            scenario = _load_scenario(conn, sid)
            scenarios[sid] = scenario
            results = _load_scenario_results(conn, sid)
            all_results[sid] = results

        # Collect all unique (dimension, metric_name) pairs
        all_metrics = set()
        for sid, results in all_results.items():
            for r in results:
                all_metrics.add((r.get("dimension"), r.get("metric_name")))

        all_metrics = sorted(all_metrics)

        # Build comparison matrix
        matrix = []
        for dim, metric in all_metrics:
            row = {
                "dimension": dim,
                "metric_name": metric,
                "values": {},
            }

            best_sid = None
            best_val = None

            for sid in scenario_ids:
                # Find matching result
                matching = [
                    r for r in all_results[sid]
                    if r.get("dimension") == dim and r.get("metric_name") == metric
                ]
                if matching:
                    r = matching[0]
                    val = r.get("simulated_value")
                    row["values"][sid] = {
                        "simulated_value": val,
                        "baseline_value": r.get("baseline_value"),
                        "delta": r.get("delta"),
                        "delta_pct": r.get("delta_pct"),
                        "impact_tier": r.get("impact_tier"),
                        "confidence": r.get("confidence"),
                    }

                    # Track best value per metric (context-dependent)
                    # For risk/cost: lower is better; for compliance: higher is better
                    if val is not None:
                        if dim in ("risk", "cost", "schedule"):
                            # Lower is better
                            if best_val is None or val < best_val:
                                best_val = val
                                best_sid = sid
                        else:
                            # Higher is better
                            if best_val is None or val > best_val:
                                best_val = val
                                best_sid = sid
                else:
                    row["values"][sid] = None

            row["best_scenario_id"] = best_sid
            matrix.append(row)

        # Compute per-scenario win counts
        win_counts = {sid: 0 for sid in scenario_ids}
        for row in matrix:
            best = row.get("best_scenario_id")
            if best:
                win_counts[best] = win_counts.get(best, 0) + 1

        # Determine overall winner
        overall_winner = max(win_counts, key=win_counts.get) if win_counts else None

        # Build scenario summaries
        scenario_summaries = {}
        for sid in scenario_ids:
            sc = scenarios[sid]
            scenario_summaries[sid] = {
                "scenario_name": sc.get("scenario_name"),
                "scenario_type": sc.get("scenario_type"),
                "status": sc.get("status"),
                "wins": win_counts.get(sid, 0),
            }

        return {
            "scenario_ids": scenario_ids,
            "scenario_count": len(scenario_ids),
            "metric_count": len(matrix),
            "scenarios": scenario_summaries,
            "comparison_matrix": matrix,
            "win_counts": win_counts,
            "overall_winner": overall_winner,
        }

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="RICOAS Scenario Manager — Save, load, fork, compare, and archive simulation scenarios"
    )
    parser.add_argument("--scenario-id", help="Scenario ID")
    parser.add_argument("--project-id", help="Project ID")
    parser.add_argument("--db", help="Database path override")

    # Actions
    parser.add_argument("--fork", action="store_true",
                        help="Fork an existing scenario")
    parser.add_argument("--new-name", help="Name for forked scenario")
    parser.add_argument("--modifications", help="JSON string of additional modifications")

    parser.add_argument("--archive", action="store_true",
                        help="Archive a scenario")

    parser.add_argument("--delete", action="store_true",
                        help="Soft-delete a scenario")

    parser.add_argument("--export", action="store_true",
                        help="Export scenario to JSON file")
    parser.add_argument("--output-path", help="Output path for export")

    # Use dest to avoid conflict with Python's import keyword
    parser.add_argument("--import", dest="do_import", action="store_true",
                        help="Import scenario from JSON file")
    parser.add_argument("--input-path", help="Input path for import")

    parser.add_argument("--list", action="store_true",
                        help="List scenarios for a project")
    parser.add_argument("--include-archived", action="store_true",
                        help="Include archived scenarios in list")

    parser.add_argument("--summary", action="store_true",
                        help="Get scenario summary")

    parser.add_argument("--compare", action="store_true",
                        help="Compare multiple scenarios")
    parser.add_argument("--scenario-ids", help="Comma-separated scenario IDs for comparison")

    # Output format
    parser.add_argument("--json", action="store_true",
                        help="Output as JSON")

    args = parser.parse_args()
    db_path = Path(args.db) if args.db else None

    try:
        if args.fork:
            if not args.scenario_id:
                parser.error("--scenario-id is required for --fork")
            if not args.new_name:
                parser.error("--new-name is required for --fork")
            add_mods = None
            if args.modifications:
                try:
                    add_mods = json.loads(args.modifications)
                except json.JSONDecodeError as e:
                    print(f"ERROR: Invalid JSON in --modifications: {e}", file=sys.stderr)
                    sys.exit(1)
            result = fork_scenario(
                scenario_id=args.scenario_id,
                new_name=args.new_name,
                additional_modifications=add_mods,
                db_path=db_path,
            )
            if args.json:
                print(json.dumps(result, indent=2, default=str))
            else:
                print("Scenario forked successfully:")
                print(f"  New ID: {result['new_scenario_id']}")
                print(f"  Forked From: {result['forked_from']}")
                print(f"  Name: {result['new_name']}")

        elif args.archive:
            if not args.scenario_id:
                parser.error("--scenario-id is required for --archive")
            result = archive_scenario(
                scenario_id=args.scenario_id,
                db_path=db_path,
            )
            if args.json:
                print(json.dumps(result, indent=2, default=str))
            else:
                print(f"Scenario archived: {result['scenario_id']}")
                print(f"  Name: {result['scenario_name']}")

        elif args.delete:
            if not args.scenario_id:
                parser.error("--scenario-id is required for --delete")
            result = delete_scenario(
                scenario_id=args.scenario_id,
                db_path=db_path,
            )
            if args.json:
                print(json.dumps(result, indent=2, default=str))
            else:
                print(f"Scenario deleted (soft): {result['scenario_id']}")
                print(f"  {result['message']}")

        elif args.export:
            if not args.scenario_id:
                parser.error("--scenario-id is required for --export")
            if not args.output_path:
                parser.error("--output-path is required for --export")
            result = export_scenario(
                scenario_id=args.scenario_id,
                output_path=args.output_path,
                db_path=db_path,
            )
            if args.json:
                print(json.dumps(result, indent=2, default=str))
            else:
                print("Scenario exported:")
                print(f"  Scenario ID: {result['scenario_id']}")
                print(f"  Output Path: {result['output_path']}")
                print(f"  Size: {result['size_bytes']:,} bytes")
                print(f"  Results: {result['results_exported']}")
                print(f"  Monte Carlo Runs: {result['monte_carlo_runs_exported']}")

        elif args.do_import:
            if not args.project_id:
                parser.error("--project-id is required for --import")
            if not args.input_path:
                parser.error("--input-path is required for --import")
            result = import_scenario(
                project_id=args.project_id,
                input_path=args.input_path,
                db_path=db_path,
            )
            if args.json:
                print(json.dumps(result, indent=2, default=str))
            else:
                print("Scenario imported:")
                print(f"  New ID: {result['new_scenario_id']}")
                print(f"  From: {result['imported_from']}")
                print(f"  Results: {result['results_imported']}")
                print(f"  Monte Carlo Runs: {result['monte_carlo_runs_imported']}")

        elif args.list:
            if not args.project_id:
                parser.error("--project-id is required for --list")
            result = list_scenarios(
                project_id=args.project_id,
                include_archived=args.include_archived,
                db_path=db_path,
            )
            if args.json:
                print(json.dumps(result, indent=2, default=str))
            else:
                print(f"Scenarios for project {args.project_id} ({result['count']} total):")
                for s in result["scenarios"]:
                    deleted = " [DELETED]" if s.get("soft_deleted") else ""
                    print(f"\n  [{s['status'].upper()}]{deleted} {s['scenario_name']}")
                    print(f"    ID: {s['id']}")
                    print(f"    Type: {s['scenario_type']}")
                    print(f"    Created: {s['created_at']}")

        elif args.summary:
            if not args.scenario_id:
                parser.error("--scenario-id is required for --summary")
            result = get_scenario_summary(
                scenario_id=args.scenario_id,
                db_path=db_path,
            )
            if args.json:
                print(json.dumps(result, indent=2, default=str))
            else:
                print(f"Scenario Summary: {result['scenario_name']}")
                print(f"  ID: {result['scenario_id']}")
                print(f"  Type: {result['scenario_type']}")
                print(f"  Status: {result['status']}")
                print(f"  Overall Impact: {result['overall_impact_tier']}")
                print(f"  Results: {result['result_count']}")
                if result.get("dimensions"):
                    print("\n  Dimensions:")
                    for dim, metrics in result["dimensions"].items():
                        print(f"    {dim}:")
                        for m in metrics:
                            tier_label = f" [{m['impact_tier']}]" if m.get("impact_tier") else ""
                            print(f"      {m['metric']}: {m['simulated']}{tier_label}")
                if result.get("monte_carlo"):
                    print("\n  Monte Carlo Runs:")
                    for mc in result["monte_carlo"]:
                        print(f"    {mc['dimension']}: P50={mc['p50']}, P90={mc['p90']} ({mc['iterations']} iterations)")

        elif args.compare:
            if not args.scenario_ids:
                parser.error("--scenario-ids is required for --compare")
            ids = [s.strip() for s in args.scenario_ids.split(",") if s.strip()]
            result = compare_multiple(
                scenario_ids=ids,
                db_path=db_path,
            )
            if args.json:
                print(json.dumps(result, indent=2, default=str))
            else:
                print(f"Comparison of {result['scenario_count']} scenarios:")
                print(f"  Metrics compared: {result['metric_count']}")
                print("\n  Scenario Summary:")
                for sid, info in result["scenarios"].items():
                    winner_marker = " <-- WINNER" if sid == result.get("overall_winner") else ""
                    print(f"    {info['scenario_name']} ({sid}): {info['wins']} wins{winner_marker}")
                print(f"\n  Overall Winner: {result.get('overall_winner')}")

        else:
            parser.print_help()
            sys.exit(1)

    except (FileNotFoundError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
# CUI // SP-CTI
