
from tools.logging.icdev_logger import get_logger
# CUI // SP-CTI
"""ICDEV ODC Digital Twin — MITRE ATT&CK Coverage Gap Engine.

Schema: detection technique × signal source × coverage_state.
Integrates OTel-style event ingest and closed-loop SDC attack path
verification. All operations are deterministic (no LLM).

Coverage states:
  covered  — all required signal sources for the technique are present
  partial  — some but not all required sources present
  gap      — no required source present

Gap score = fraction of techniques in 'gap' state (0.0 = fully covered, 1.0 = all gaps).
"""

import json
import uuid
from datetime import datetime, timezone

logger = get_logger("icdev.observability_canvas.mitre_coverage_twin")

# ── MITRE Technique Catalog ────────────────────────────────────────────────────
# Single source of truth lives in tools/observability_canvas/mitre_catalog.py.
# MITRE_TECHNIQUE_CATALOG is the coverage-scoring view derived from it: the
# subset of techniques carrying signal_sources, each with a scalar `tactic`
# (primary tactic, coverage-twin underscore vocabulary), `signal_sources` and
# `min_sources`. Kept as a public name so downstream consumers keep working.
from tools.observability_canvas.mitre_catalog import build_coverage_catalog

MITRE_TECHNIQUE_CATALOG: dict[str, dict] = build_coverage_catalog()


# ── Coverage Scoring ──────────────────────────────────────────────────────────


def score_technique_coverage(
    technique_id: str,
    technique_def: dict,
    present_source_types: set[str],
) -> tuple[str, list[str], list[str]]:
    """Score a single technique against present signal sources.

    Returns:
        (coverage_state, sources_present, sources_missing)
        coverage_state: 'covered' | 'partial' | 'gap'
    """
    required = set(technique_def["signal_sources"])
    present = required & present_source_types
    min_required = technique_def.get("min_sources", 1)

    if len(present) >= min_required and len(present) >= len(required):
        state = "covered"
    elif len(present) >= min_required:
        state = "partial"
    elif len(present) > 0:
        state = "partial"
    else:
        state = "gap"

    return state, sorted(present), sorted(required - present)


def compute_gap_score(design_id: str, graph_data: dict) -> dict:
    """Compute per-technique coverage and overall gap score for a design.

    Args:
        design_id: ODC design ID (used for DB persistence).
        graph_data: Dict with "nodes" list.

    Returns:
        Dict with coverage_by_technique, covered_count, partial_count, gap_count,
        total_techniques, gap_score (0.0=all covered, 1.0=all gaps),
        by_tactic, technique_gaps.
    """
    nodes = graph_data.get("nodes", [])
    present_source_types: set[str] = {n.get("type", "") for n in nodes}

    # Merge in techniques from cmp-baseline nodes (manual coverage overrides)
    baseline_covered: set[str] = set()
    for node in nodes:
        if node.get("type") == "cmp-baseline":
            cfg = node.get("config_json", {})
            if isinstance(cfg, str):
                try:
                    cfg = json.loads(cfg)
                except (ValueError, TypeError):
                    cfg = {}
            for tech in cfg.get("techniques", []):
                if tech.get("covered"):
                    baseline_covered.add(tech.get("id", ""))

    coverage_by_technique: dict[str, dict] = {}
    by_tactic: dict[str, dict] = {}
    technique_gaps: list[str] = []
    covered_count = 0
    partial_count = 0
    gap_count = 0

    for tid, tdef in MITRE_TECHNIQUE_CATALOG.items():
        # Manual baseline override
        if tid in baseline_covered:
            state = "covered"
            sources_present = tdef["signal_sources"]
            sources_missing: list[str] = []
        else:
            state, sources_present, sources_missing = score_technique_coverage(
                tid, tdef, present_source_types
            )

        # Gap score for this technique: 0.0 = covered, 0.5 = partial, 1.0 = gap
        tech_gap_score = 0.0 if state == "covered" else (0.5 if state == "partial" else 1.0)

        coverage_by_technique[tid] = {
            "name": tdef["name"],
            "tactic": tdef["tactic"],
            "coverage_state": state,
            "signal_sources_present": sources_present,
            "signal_sources_missing": sources_missing,
            "gap_score": tech_gap_score,
        }

        tactic = tdef["tactic"]
        by_tactic.setdefault(tactic, {"covered": 0, "partial": 0, "gap": 0})
        by_tactic[tactic][state] += 1

        if state == "covered":
            covered_count += 1
        elif state == "partial":
            partial_count += 1
        else:
            gap_count += 1
            technique_gaps.append(tid)

    total = len(MITRE_TECHNIQUE_CATALOG)
    # Weighted gap score: gap=1.0, partial=0.5, covered=0.0
    gap_score = round(
        (gap_count * 1.0 + partial_count * 0.5) / max(total, 1), 3
    )

    result = {
        "design_id": design_id,
        "total_techniques": total,
        "covered_count": covered_count,
        "partial_count": partial_count,
        "gap_count": gap_count,
        "gap_score": gap_score,
        "coverage_pct": round(100 * covered_count / max(total, 1), 1),
        "coverage_by_technique": coverage_by_technique,
        "by_tactic": by_tactic,
        "technique_gaps": sorted(technique_gaps),
        "assessed_at": datetime.now(timezone.utc).isoformat(),
    }

    _persist_gap_score(design_id, result)
    return result


def _persist_gap_score(design_id: str, result: dict) -> None:
    """Persist gap scores to odc_gap_scores and odc_technique_coverage tables."""
    try:
        from tools.observability_canvas.db.init_db import get_connection

        now = result["assessed_at"]
        gap_id = str(uuid.uuid4())

        with get_connection() as conn:
            conn.execute(
                "INSERT INTO odc_gap_scores "
                "(id, design_id, total_techniques, covered_count, partial_count, "
                "gap_count, overall_gap_score, by_tactic, assessed_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    gap_id,
                    design_id,
                    result["total_techniques"],
                    result["covered_count"],
                    result["partial_count"],
                    result["gap_count"],
                    result["gap_score"],
                    json.dumps(result["by_tactic"]),
                    now,
                ),
            )

            for tid, cov in result["coverage_by_technique"].items():
                cov_id = str(uuid.uuid4())
                conn.execute(
                    "INSERT INTO odc_technique_coverage "
                    "(id, design_id, technique_id, coverage_state, "
                    "signal_sources_present, signal_sources_missing, gap_score, assessed_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        cov_id,
                        design_id,
                        tid,
                        cov["coverage_state"],
                        json.dumps(cov["signal_sources_present"]),
                        json.dumps(cov["signal_sources_missing"]),
                        cov["gap_score"],
                        now,
                    ),
                )

    except Exception as exc:
        logger.warning("ODC gap score persistence failed: %s", exc)


# ── OTel Event Ingest ─────────────────────────────────────────────────────────


def ingest_otel_event(design_id: str, event: dict) -> dict:
    """Ingest an OTel-format detection event and map it to a MITRE technique.

    Accepts a simplified OTLP JSON span/event with optional 'mitre_technique'
    attribute. Persists to odc_otel_events for closed-loop gap verification.

    Args:
        design_id: ODC design ID.
        event: OTel span/event dict. Expected keys:
            name, trace_id, span_id, attributes (dict).
            Optionally: attributes.mitre_technique, attributes.signal_source.

    Returns:
        Dict with stored event summary.
    """
    attrs = event.get("attributes", {})
    technique_id = attrs.get("mitre_technique", attrs.get("technique_id", ""))
    signal_source = attrs.get("signal_source", attrs.get("source_type", ""))
    event_name = event.get("name", "unknown")

    # Validate technique_id against catalog
    if technique_id and technique_id not in MITRE_TECHNIQUE_CATALOG:
        technique_id = ""

    try:
        from tools.observability_canvas.db.init_db import get_connection

        with get_connection() as conn:
            conn.execute(
                "INSERT INTO odc_otel_events "
                "(design_id, trace_id, span_id, event_name, technique_id, "
                "signal_source, attributes, received_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    design_id,
                    event.get("trace_id", ""),
                    event.get("span_id", ""),
                    event_name,
                    technique_id,
                    signal_source,
                    json.dumps(attrs),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
    except Exception as exc:
        logger.warning("OTel event ingest failed: %s", exc)
        return {"status": "error", "error": str(exc)}

    return {
        "status": "ingested",
        "event_name": event_name,
        "technique_id": technique_id,
        "signal_source": signal_source,
    }


def ingest_otel_batch(design_id: str, events: list) -> dict:
    """Ingest a batch of OTel events (OTLP-over-HTTP JSON format subset).

    Accepts either:
      - List of span dicts
      - OTLP JSON envelope: {"resourceSpans": [...]}

    Returns:
        Dict with ingested count and per-technique event counts.
    """
    # Unwrap OTLP envelope if present
    if isinstance(events, dict):
        resource_spans = events.get("resourceSpans", [])
        flat: list[dict] = []
        for rs in resource_spans:
            for ss in rs.get("scopeSpans", rs.get("instrumentationLibrarySpans", [])):
                for span in ss.get("spans", []):
                    flat.append(span)
        events = flat

    ingested = 0
    technique_counts: dict[str, int] = {}
    for ev in events:
        result = ingest_otel_event(design_id, ev)
        if result.get("status") == "ingested":
            ingested += 1
            tid = result.get("technique_id", "")
            if tid:
                technique_counts[tid] = technique_counts.get(tid, 0) + 1

    return {
        "ingested": ingested,
        "total_submitted": len(events),
        "technique_counts": technique_counts,
    }


# ── SDC Closed-Loop Verification ─────────────────────────────────────────────


def verify_sdc_attack_path(design_id: str, ttp_list: list[str]) -> dict:
    """Verify detection coverage for an SDC attack path replay.

    Called by the SDC twin when it replays an attack path (list of TTP IDs).
    Checks each TTP against the ODC design's current coverage state.

    Args:
        design_id: ODC design ID to verify against.
        ttp_list: List of MITRE technique IDs (e.g. ['T1059', 'T1078']).

    Returns:
        Dict with per_ttp_result, covered_ttps, gap_ttps, coverage_pct, summary.
    """
    try:
        from tools.observability_canvas.db.init_db import get_connection

        with get_connection() as conn:
            row = conn.execute(
                "SELECT graph_json FROM observability_designs WHERE id=%s",
                (design_id,),
            ).fetchone()
    except Exception as exc:
        logger.warning("ODC verify: design load failed: %s", exc)
        return {"status": "error", "error": str(exc)}

    if not row:
        return {"status": "error", "error": "ODC design not found"}

    graph_raw = row[0] if isinstance(row, (list, tuple)) else row["graph_json"]
    try:
        graph_data = json.loads(graph_raw) if isinstance(graph_raw, str) else graph_raw
    except (ValueError, TypeError):
        return {"status": "error", "error": "Invalid graph data"}

    nodes = graph_data.get("nodes", [])
    present_source_types: set[str] = {n.get("type", "") for n in nodes}

    # Baseline overrides
    baseline_covered: set[str] = set()
    for node in nodes:
        if node.get("type") == "cmp-baseline":
            cfg = node.get("config_json", {})
            if isinstance(cfg, str):
                try:
                    cfg = json.loads(cfg)
                except (ValueError, TypeError):
                    cfg = {}
            for tech in cfg.get("techniques", []):
                if tech.get("covered"):
                    baseline_covered.add(tech.get("id", ""))

    per_ttp_result: list[dict] = []
    covered_ttps: list[str] = []
    partial_ttps: list[str] = []
    gap_ttps: list[str] = []

    for tid in ttp_list:
        tdef = MITRE_TECHNIQUE_CATALOG.get(tid)
        if tdef is None:
            per_ttp_result.append({
                "technique_id": tid,
                "coverage_state": "unknown",
                "detail": "Technique not in ODC catalog",
            })
            gap_ttps.append(tid)
            continue

        if tid in baseline_covered:
            state = "covered"
            missing: list[str] = []
        else:
            state, _present, missing = score_technique_coverage(
                tid, tdef, present_source_types
            )

        per_ttp_result.append({
            "technique_id": tid,
            "name": tdef["name"],
            "tactic": tdef["tactic"],
            "coverage_state": state,
            "signal_sources_missing": missing,
        })

        if state == "covered":
            covered_ttps.append(tid)
        elif state == "partial":
            partial_ttps.append(tid)
        else:
            gap_ttps.append(tid)

    total = len(ttp_list)
    coverage_pct = round(100 * len(covered_ttps) / max(total, 1), 1)

    # Persist verification result to odc_gap_scores as attack-path type
    try:
        from tools.observability_canvas.db.init_db import get_connection

        now = datetime.now(timezone.utc).isoformat()
        verify_id = str(uuid.uuid4())
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO odc_sdc_verifications "
                "(id, design_id, ttp_list, covered_ttps, partial_ttps, gap_ttps, "
                "coverage_pct, verified_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    verify_id,
                    design_id,
                    json.dumps(ttp_list),
                    json.dumps(covered_ttps),
                    json.dumps(partial_ttps),
                    json.dumps(gap_ttps),
                    coverage_pct,
                    now,
                ),
            )
    except Exception as exc:
        logger.warning("SDC verification persistence failed: %s", exc)

    return {
        "status": "verified",
        "design_id": design_id,
        "total_ttps": total,
        "covered_ttps": covered_ttps,
        "partial_ttps": partial_ttps,
        "gap_ttps": gap_ttps,
        "coverage_pct": coverage_pct,
        "per_ttp_result": per_ttp_result,
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }


# ── Gap Report ────────────────────────────────────────────────────────────────


def generate_gap_report(design_id: str, graph_data: dict) -> dict:
    """Full gap analysis: technique coverage + prioritized remediation steps.

    Args:
        design_id: ODC design ID.
        graph_data: Graph dict with nodes/edges.

    Returns:
        Dict with gap_score, coverage_by_technique, remediation_steps,
        quick_wins (sources that would close the most gaps if added).
    """
    score_result = compute_gap_score(design_id, graph_data)

    # Build remediation steps for gap + partial techniques
    remediation_steps: list[dict] = []
    source_gap_counts: dict[str, int] = {}

    for tid, cov in score_result["coverage_by_technique"].items():
        if cov["coverage_state"] in ("gap", "partial"):
            missing = cov["signal_sources_missing"]
            remediation_steps.append({
                "technique_id": tid,
                "technique_name": cov["name"],
                "tactic": cov["tactic"],
                "coverage_state": cov["coverage_state"],
                "add_sources": missing,
                "priority": "critical" if cov["coverage_state"] == "gap" else "recommended",
            })
            for src in missing:
                source_gap_counts[src] = source_gap_counts.get(src, 0) + 1

    # Sort: critical first, then by technique_id
    remediation_steps.sort(key=lambda x: (0 if x["priority"] == "critical" else 1, x["technique_id"]))

    # Quick wins: sources that would close the most gaps
    quick_wins = sorted(
        [{"source_type": src, "techniques_unlocked": count}
         for src, count in source_gap_counts.items()],
        key=lambda x: -x["techniques_unlocked"],
    )

    return {
        **score_result,
        "remediation_steps": remediation_steps,
        "quick_wins": quick_wins,
        "total_remediation_items": len(remediation_steps),
    }
