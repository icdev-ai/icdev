# CUI // SP-CTI
"""ACE Canvas Role Generator — Phase D canvas-specific role expansion.

Deterministically generates role YAML files for ICDEV canvases that have no
representative ACE role (detected via canvas_role_gap.detect_gaps()).

Usage::

    python tools/ace/role_generator.py --dry-run --json   # preview only
    python tools/ace/role_generator.py --json             # write and report

Each generated role is written to args/ace/roles/<canvas_key>_analyst.yaml
with the ``canvas: <key>`` field so canvas_role_gap.detect_gaps() picks it up.
Only writes a file if no role with ``canvas: <key>`` already exists.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).parents[2].resolve()
_ROLES_DIR = _REPO_ROOT / "args" / "ace" / "roles"

# ---------------------------------------------------------------------------
# Capability mapping — deterministic, keyword-based
# ---------------------------------------------------------------------------

_CAPABILITY_MAP: list[tuple[list[str], list[str]]] = [
    (["network", "ndc", "nocc", "noc", "bgp", "routing", "topology", "circuit", "ccc", "pmc", "peering"],
     ["network_analysis", "topology_review", "traffic_flow_mapping"]),
    (["security", "sdc", "dsoc", "stig", "zig", "threat", "vulnerability", "incident"],
     ["security_assessment", "control_mapping", "vulnerability_triage"]),
    (["compliance", "fedramp", "cmmc", "nist", "ato", "poam", "audit"],
     ["compliance_mapping", "control_gap_analysis", "evidence_collection"]),
    (["migration", "mdc", "modernize", "7r", "lift"],
     ["migration_planning", "dependency_mapping", "risk_assessment"]),
    (["data", "ddc", "lineage", "schema", "synthetic", "pii"],
     ["data_analysis", "schema_review", "lineage_tracing"]),
    (["ai", "ml", "nova", "finetune", "model", "aimc", "aadc", "aisg", "agentic", "observatory"],
     ["model_analysis", "training_review", "inference_optimization"]),
    (["document", "dic", "docgen", "rag", "ingest", "content"],
     ["document_review", "content_generation", "citation_management"]),
    (["govlift", "govcon", "federal", "acquisition", "cpmp", "contract"],
     ["federal_compliance", "acquisition_review", "cdrl_management"]),
    (["slides", "viz", "presentation", "deck", "chart"],
     ["presentation_design", "data_visualization", "narrative_structuring"]),
    (["pipeline", "pdc", "cicd", "deploy", "build", "gitlab"],
     ["pipeline_review", "build_optimization", "deployment_readiness"]),
    (["observability", "odc", "monitor", "logging", "logs", "sre", "slo", "tracing"],
     ["log_analysis", "alert_triage", "slo_monitoring"]),
    (["quality", "qdc", "test", "qa", "coverage", "runbook"],
     ["test_strategy_review", "coverage_analysis", "quality_gate_enforcement"]),
    (["infra", "idc", "infrastructure", "cloud", "terraform", "k8s", "iac"],
     ["infrastructure_review", "iac_validation", "cost_optimization"]),
    (["boundary", "bdc", "supply.chain", "scrm", "isa", "ato.boundary"],
     ["boundary_analysis", "supply_chain_risk", "isa_lifecycle_management"]),
    (["ops", "ohc", "operations", "llmops", "mlops", "incident", "slos"],
     ["operations_monitoring", "incident_triage", "slo_tracking"]),
    (["info.ops", "iop", "information.ops", "influence", "psyop"],
     ["information_analysis", "influence_assessment", "narrative_tracking"]),
    (["mission", "digital.twin", "commander", "strategos", "wargame"],
     ["mission_planning", "operational_analysis", "decision_support"]),
    (["aiify", "modernization", "opportunity", "scan", "roadmap"],
     ["opportunity_detection", "modernization_assessment", "roi_analysis"]),
    (["integrity", "sipa", "provenance", "sbom", "supply"],
     ["integrity_assessment", "provenance_verification", "dependency_audit"]),
    (["ace", "coworker", "agent", "anvil", "orchestrat"],
     ["agent_coordination", "role_assignment", "task_dispatch"]),
    (["foundry", "innovation", "capability", "factory", "0.to.1"],
     ["capability_generation", "novelty_assessment", "ideation_support"]),
    (["gameday", "tabletop", "simulation", "threat.sim", "exercise"],
     ["scenario_design", "threat_simulation", "after_action_review"]),
    (["academy", "forge", "training", "learning", "gamif"],
     ["curriculum_design", "skill_assessment", "learning_path_guidance"]),
    (["wfc", "workflow", "form", "automation", "process"],
     ["workflow_analysis", "form_validation", "process_optimization"]),
    (["second.brain", "briefing", "persona", "assistant", "advisor"],
     ["context_retrieval", "briefing_generation", "preference_management"]),
    (["forecast", "timesfm", "time.series", "prediction"],
     ["forecast_generation", "trend_analysis", "anomaly_detection"]),
    (["hitl", "human.in.the.loop", "review.queue", "approval"],
     ["review_facilitation", "approval_routing", "hitl_escalation"]),
    (["canvas.health", "health", "registry", "component"],
     ["health_monitoring", "registry_validation", "component_status_tracking"]),
    (["demo", "runner", "showcase"],
     ["demo_orchestration", "scenario_execution", "result_capture"]),
    (["innovation", "lab", "r&d", "experiment"],
     ["innovation_tracking", "experiment_design", "feasibility_assessment"]),
]

_DEFAULT_CAPABILITIES = ["task_execution", "status_reporting", "context_retrieval"]


def _clip(text: str, limit: int = 120) -> str:
    """Shorten *text* to *limit* chars without cutting a word in half.

    A bare ``[:120]`` produced descriptions that stopped mid-word with an
    unclosed bracket — e.g. the Cortex registry entry ended
    "...(complete/classify/extract/search/ask". These strings are written into
    committed role YAML, so the damage is durable rather than cosmetic.
    """
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    if " " in cut:
        cut = cut[: cut.rindex(" ")]
    return cut.rstrip(" ,;:-(") + "…"


def _pick_capabilities(canvas_key: str, display_name: str, description: str) -> list[str]:
    """Return 2–3 capability strings based on canvas identity."""
    combined = f"{canvas_key} {display_name} {description}".lower()
    for keywords, caps in _CAPABILITY_MAP:
        if any(kw.replace(".", " ") in combined or kw.replace(".", "") in combined for kw in keywords):
            return caps[:3]
    return _DEFAULT_CAPABILITIES


def _make_listen_topics(canvas_key: str) -> list[str]:
    return [f"{canvas_key}.task.*", f"{canvas_key}.query.*", f"{canvas_key}.status.changed"]


def _make_emit_topics(canvas_key: str) -> list[str]:
    return [f"{canvas_key}.analysis.complete", f"{canvas_key}.report.ready", "status.updated"]


def _make_steps(capabilities: list[str]) -> list[str]:
    steps = ["gather_canvas_context"]
    for cap in capabilities:
        steps.append(cap)
    steps.append("report_findings")
    return steps


def _make_rules(canvas_key: str, display_name: str) -> list[str]:
    return [
        f"Never act outside the scope of the {display_name} canvas.",
        "Never access audit tables with UPDATE or DELETE — audit trail is append-only.",
        "Never produce findings without citing specific canvas data or configuration.",
        "Never skip the human review step when output will be published or acted upon.",
    ]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_role(
    canvas_key: str,
    display_name: str,
    description: str = "",
    canvas_type: str = "",
) -> dict[str, Any]:
    """Generate a role spec dict for a canvas.

    Args:
        canvas_key: Registry component key (e.g. ``"idc"``).
        display_name: Human-readable canvas name.
        description: Optional description from component registry.
        canvas_type: Optional type hint (unused internally; reserved for callers).

    Returns:
        Role spec dict (not yet written to disk).
    """
    role_id = f"{canvas_key}_analyst"
    capabilities = _pick_capabilities(canvas_key, display_name, description)
    short_desc = _clip(description or f"Canvas for {display_name} operations in ICDEV.")

    return {
        "role_id": role_id,
        "display_name": f"{display_name} Analyst",
        "description": (
            f"Co-worker specializing in {display_name.lower()} operations. "
            f"{short_desc}"
        ),
        "version": "1.0",
        "trust_tier": "yellow",
        "canvas": canvas_key,
        "source": "role_generator",
        "default_count": 1,
        "max_instances": 2,
        "steps": _make_steps(capabilities),
        "communication": {
            "protocol": "a2a",
            "listen_topics": _make_listen_topics(canvas_key),
            "emit_topics": _make_emit_topics(canvas_key),
        },
        "llm_function": "agent_knowledge",
        "tool_permissions": ["Read", "Grep", "Glob"],
        "genesis_reflex": "audit",
        "personality": {
            "domain": f"{display_name} Analysis",
            "capabilities": capabilities,
            "tags": ["canvas-specific", "context-aware", "deterministic-first"],
        },
        "rules": _make_rules(canvas_key, display_name),
    }


def _dump_yaml_str(spec: dict[str, Any]) -> str:
    """Serialize a role spec to YAML string without external deps if possible."""
    try:
        import yaml
        return yaml.dump(spec, default_flow_style=False, allow_unicode=True, sort_keys=False)
    except ImportError:
        # Minimal fallback serializer for simple dicts
        lines = ["# CUI // SP-CTI"]
        for k, v in spec.items():
            if isinstance(v, str):
                escaped = v.replace('"', '\\"')
                lines.append(f'{k}: "{escaped}"')
            elif isinstance(v, (int, float)):
                lines.append(f"{k}: {v}")
            elif isinstance(v, list):
                lines.append(f"{k}:")
                for item in v:
                    if isinstance(item, str):
                        lines.append(f"  - {item}")
                    elif isinstance(item, dict):
                        first = True
                        for dk, dv in item.items():
                            prefix = "  - " if first else "    "
                            first = False
                            lines.append(f"{prefix}{dk}: {dv}")
            elif isinstance(v, dict):
                lines.append(f"{k}:")
                for dk, dv in v.items():
                    if isinstance(dv, list):
                        lines.append(f"  {dk}:")
                        for item in dv:
                            lines.append(f"    - {item}")
                    elif isinstance(dv, str):
                        lines.append(f"  {dk}: {dv}")
                    elif isinstance(dv, dict):
                        lines.append(f"  {dk}:")
                        for ddk, ddv in dv.items():
                            lines.append(f"    {ddk}: {ddv}")
        return "\n".join(lines) + "\n"


def write_role(spec: dict[str, Any], out_dir: Path | None = None) -> Path:
    """Write a role spec dict to args/ace/roles/<role_id>.yaml.

    Args:
        spec: Role spec as returned by generate_role().
        out_dir: Override output directory (default: args/ace/roles/).

    Returns:
        Path of the written file.
    """
    dest = (out_dir or _ROLES_DIR) / f"{spec['role_id']}.yaml"
    header = "# CUI // SP-CTI\n"
    content = header + _dump_yaml_str(spec)
    dest.write_text(content, encoding="utf-8", newline="")
    return dest


def fill_gaps(dry_run: bool = False, out_dir: Path | None = None) -> dict[str, Any]:
    """Generate roles for all canvases detected as gaps by canvas_role_gap.

    Args:
        dry_run: If True, generate specs but do not write files.
        out_dir: Override output directory (for testing).

    Returns:
        Summary dict with generated, skipped, errors lists.
    """
    from tools.ace.canvas_role_gap import detect_gaps

    result = detect_gaps()
    gaps = result.get("gaps", [])

    generated: list[str] = []
    skipped: list[str] = []
    errors: list[dict[str, str]] = []

    # Build canvas descriptions from registry
    canvas_desc_map: dict[str, str] = {}
    try:
        import yaml
        reg_path = _REPO_ROOT / "args" / "component_registry.yaml"
        reg = yaml.safe_load(reg_path.read_text(encoding="utf-8")) or {}
        for comp in reg.get("components", []):
            if isinstance(comp, dict) and comp.get("key"):
                canvas_desc_map[comp["key"]] = comp.get("description", "")
    except Exception:
        pass

    for gap in gaps:
        canvas_key = gap.get("canvas_key", "")
        display_name = gap.get("display_name", canvas_key)
        if not canvas_key:
            continue
        role_id = f"{canvas_key}_analyst"
        dest = (out_dir or _ROLES_DIR) / f"{role_id}.yaml"
        if dest.exists():
            skipped.append(canvas_key)
            continue
        try:
            description = canvas_desc_map.get(canvas_key, "")
            spec = generate_role(canvas_key, display_name, description)
            if not dry_run:
                write_role(spec, out_dir)
            generated.append(canvas_key)
        except Exception as exc:
            errors.append({"canvas_key": canvas_key, "error": str(exc)})

    return {
        "generated": generated,
        "skipped": skipped,
        "errors": errors,
        "dry_run": dry_run,
        "total_gaps_before": len(gaps),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="ACE canvas role generator (Phase D)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing files")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    result = fill_gaps(dry_run=args.dry_run)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        action = "Would generate" if args.dry_run else "Generated"
        print("ACE Role Generator — Phase D")
        print(f"  {action}: {len(result['generated'])} roles")
        print(f"  Skipped (already exist): {len(result['skipped'])}")
        print(f"  Errors: {len(result['errors'])}")
        for canvas_key in result["generated"]:
            role_id = f"{canvas_key}_analyst"
            print(f"    + {canvas_key} -> {role_id}")
        for e in result["errors"]:
            print(f"    ! {e['canvas_key']}: {e['error']}")


if __name__ == "__main__":
    main()
