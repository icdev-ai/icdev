# CUI // SP-CTI
"""Migration Intelligence — 3-Tier App SLA Enforcer.

Rule: Migration executor for a standard 3-tier app must complete in under 8 hours.
"Complete" means the cutover execution window (maintenance window), not total project effort.

Standard 3-tier app profile:
  - Presentation layer (web/UI) + Application layer (app server) + Data layer (database)
  - Detected from opportunity title/description/type keywords

Cutover hours by strategy type (execution window, not total effort):
  - rehost / forklift:       4 h  — lift-and-shift, minimal change
  - replatform:              6 h  — minor re-config + cutover
  - parallel_run:            2 h  — blue/green switchover only
  - phased_cutover:          8 h  — exactly at the limit (multi-step)
  - graceful_migration:      4 h  — rolling update window
  - consolidate:             6 h  — merge + cutover
  - replace:                 8 h  — new-system swap at the limit
  - retain / retire:         0-1 h — no active migration
  - refactor / rebuild:     16-24 h — code-change work, exceeds limit
  - rip_and_replace:        12 h  — full swap, exceeds limit

CLI:
    python tools/migration_intelligence/sla_enforcer.py --check --json
    python tools/migration_intelligence/sla_enforcer.py --annotate --json
    python tools/migration_intelligence/sla_enforcer.py --report --json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

_DB_PATH = BASE_DIR / "data" / "migration_intel.db"

THREE_TIER_SLA_HOURS: float = 8.0

# Estimated execution/cutover window in hours, by strategy type.
# Values > THREE_TIER_SLA_HOURS will be flagged as SLA violations.
CUTOVER_HOURS_BY_STRATEGY: dict[str, float] = {
    "rehost":            4.0,
    "forklift":          4.0,
    "replatform":        6.0,
    "parallel_run":      2.0,
    "phased_cutover":    8.0,
    "graceful_migration": 4.0,
    "consolidate":       6.0,
    "replace":           8.0,
    "retain":            0.0,
    "retire":            1.0,
    "refactor":         16.0,
    "rebuild":          24.0,
    "rip_and_replace":  12.0,
}

# Keywords that identify a standard 3-tier application opportunity.
THREE_TIER_KEYWORDS: frozenset[str] = frozenset({
    "three-tier", "3-tier", "3tier", "web application", "webapp",
    "presentation layer", "application layer", "data layer",
    "frontend", "backend", "web app", "standard app",
    "tier 1", "tier 2", "tier 3", "three tier",
    "application migration",
})

# Opportunity types that commonly map to 3-tier app migrations.
THREE_TIER_OPPORTUNITY_TYPES: frozenset[str] = frozenset({
    "application_migration",
    "platform_modernization",
    "cloud_migration",
})

# Exclude large/complex patterns from the 3-tier profile.
EXCLUDE_KEYWORDS: frozenset[str] = frozenset({
    "enterprise", "legacy mainframe", "monolith", "microservices mesh",
    "data warehouse", "big data", "ml pipeline", "machine learning",
})


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _get_conn(db_path=None):
    from tools.migration_intelligence.db.init_db import get_connection, init_db
    path = db_path or str(_DB_PATH)
    init_db(path)
    return get_connection(path)


# ─────────────────────────────────────────────────────────────────────────────
# PROFILE DETECTION
# ─────────────────────────────────────────────────────────────────────────────

def is_three_tier_app(opportunity: dict) -> bool:
    """Return True if the opportunity looks like a standard 3-tier application."""
    text = " ".join(filter(None, [
        str(opportunity.get("title", "")),
        str(opportunity.get("description", "")),
        str(opportunity.get("opportunity_type", "")),
        str(opportunity.get("source_entity_name", "")),
    ])).lower()

    # Hard exclude: large/complex systems are not standard 3-tier
    if any(kw in text for kw in EXCLUDE_KEYWORDS):
        return False

    # Explicit keyword match
    if any(kw in text for kw in THREE_TIER_KEYWORDS):
        return True

    # Opportunity type match
    if opportunity.get("opportunity_type") in THREE_TIER_OPPORTUNITY_TYPES:
        return True

    return False


# ─────────────────────────────────────────────────────────────────────────────
# SLA COMPUTATION
# ─────────────────────────────────────────────────────────────────────────────

def compute_cutover_hours(strategy_type: str) -> float:
    """Return estimated cutover execution window (hours) for a given strategy type."""
    return CUTOVER_HOURS_BY_STRATEGY.get(strategy_type, THREE_TIER_SLA_HOURS)


def is_sla_compliant(strategy_type: str, sla_hours: float = THREE_TIER_SLA_HOURS) -> bool:
    """Return True if the strategy's cutover window is within the SLA limit."""
    return compute_cutover_hours(strategy_type) <= sla_hours


# ─────────────────────────────────────────────────────────────────────────────
# DB ANNOTATION
# ─────────────────────────────────────────────────────────────────────────────

def annotate_strategies(db_path=None) -> dict:
    """Annotate all strategies with cutover_hours_estimate and sla_compliant flags.

    Also marks mi_opportunities.app_profile for detected 3-tier apps.
    Returns a summary dict.
    """
    conn = _get_conn(db_path)
    try:
        # Fetch all opportunities
        opps = conn.execute(
            "SELECT id, title, description, opportunity_type, source_entity_name FROM mi_opportunities"
        ).fetchall()
        opps = [dict(r) for r in opps]

        three_tier_ids: set[str] = set()
        for opp in opps:
            if is_three_tier_app(opp):
                three_tier_ids.add(opp["id"])
                try:
                    conn.execute(
                        "UPDATE mi_opportunities SET app_profile='standard_three_tier', updated_at=? WHERE id=?",
                        (_now(), opp["id"]),
                    )
                except Exception:
                    pass
            else:
                try:
                    existing_profile = conn.execute(
                        "SELECT app_profile FROM mi_opportunities WHERE id=?", (opp["id"],)
                    ).fetchone()
                    if existing_profile and not existing_profile["app_profile"]:
                        conn.execute(
                            "UPDATE mi_opportunities SET app_profile='other', updated_at=? WHERE id=?",
                            (_now(), opp["id"]),
                        )
                except Exception:
                    pass

        # Fetch all strategies
        strategies = conn.execute(
            "SELECT id, opportunity_id, strategy_type FROM mi_strategies"
        ).fetchall()
        strategies = [dict(r) for r in strategies]

        annotated = 0
        violations = 0
        for strat in strategies:
            cutover_h = compute_cutover_hours(strat["strategy_type"])
            compliant = 1 if cutover_h <= THREE_TIER_SLA_HOURS else 0
            # Only flag SLA violation if opportunity is a 3-tier app
            if strat["opportunity_id"] not in three_tier_ids:
                compliant = 1  # SLA only applies to 3-tier apps
            try:
                conn.execute(
                    """UPDATE mi_strategies
                       SET cutover_hours_estimate=?, sla_compliant=?
                       WHERE id=?""",
                    (cutover_h, compliant, strat["id"]),
                )
                annotated += 1
                if not compliant:
                    violations += 1
            except Exception:
                pass

        conn.commit()
        return {
            "ok": True,
            "stage": "sla_check",
            "opportunities_scanned": len(opps),
            "three_tier_apps_detected": len(three_tier_ids),
            "strategies_annotated": annotated,
            "sla_violations": violations,
            "sla_hours": THREE_TIER_SLA_HOURS,
        }
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# REPORT
# ─────────────────────────────────────────────────────────────────────────────

def get_sla_report(db_path=None) -> dict:
    """Return a summary of 3-tier SLA compliance across all opportunities/strategies."""
    conn = _get_conn(db_path)
    try:
        three_tier_opps = conn.execute(
            "SELECT id, title FROM mi_opportunities WHERE app_profile='standard_three_tier'"
        ).fetchall()
        three_tier_opps = [dict(r) for r in three_tier_opps]

        violations = []
        compliant = []
        for opp in three_tier_opps:
            strats = conn.execute(
                """SELECT id, strategy_type, cutover_hours_estimate, sla_compliant, recommended
                   FROM mi_strategies WHERE opportunity_id=?""",
                (opp["id"],),
            ).fetchall()
            for s in strats:
                s = dict(s)
                entry = {
                    "opportunity_id": opp["id"],
                    "opportunity_title": opp["title"],
                    "strategy_id": s["id"],
                    "strategy_type": s["strategy_type"],
                    "cutover_hours": s.get("cutover_hours_estimate"),
                    "sla_compliant": bool(s.get("sla_compliant", 1)),
                    "recommended": bool(s.get("recommended", 0)),
                }
                if s.get("sla_compliant", 1):
                    compliant.append(entry)
                else:
                    violations.append(entry)

        total = len(compliant) + len(violations)
        return {
            "ok": True,
            "sla_hours": THREE_TIER_SLA_HOURS,
            "three_tier_apps": len(three_tier_opps),
            "strategies_evaluated": total,
            "compliant": len(compliant),
            "violations": len(violations),
            "compliance_rate": round(len(compliant) / total, 3) if total else 1.0,
            "violation_details": violations,
            "timestamp": _now(),
        }
    finally:
        conn.close()


def check_sla_for_opportunity(opportunity_id: str, db_path=None) -> dict:
    """Check SLA compliance for a single opportunity."""
    conn = _get_conn(db_path)
    try:
        opp = conn.execute(
            "SELECT id, title, description, opportunity_type, source_entity_name, app_profile FROM mi_opportunities WHERE id=?",
            (opportunity_id,),
        ).fetchone()
        if not opp:
            return {"error": f"Opportunity {opportunity_id} not found"}
        opp = dict(opp)

        profile = opp.get("app_profile") or ("standard_three_tier" if is_three_tier_app(opp) else "other")
        strats = conn.execute(
            "SELECT id, strategy_type, cutover_hours_estimate, sla_compliant, recommended FROM mi_strategies WHERE opportunity_id=?",
            (opportunity_id,),
        ).fetchall()
        strats = [dict(s) for s in strats]

        results = []
        for s in strats:
            cutover_h = s.get("cutover_hours_estimate") or compute_cutover_hours(s["strategy_type"])
            within_sla = cutover_h <= THREE_TIER_SLA_HOURS if profile == "standard_three_tier" else True
            results.append({
                "strategy_id": s["id"],
                "strategy_type": s["strategy_type"],
                "cutover_hours": cutover_h,
                "sla_compliant": within_sla,
                "recommended": bool(s.get("recommended", 0)),
            })

        any_compliant = any(r["sla_compliant"] for r in results) if results else True
        return {
            "opportunity_id": opportunity_id,
            "opportunity_title": opp["title"],
            "app_profile": profile,
            "sla_hours": THREE_TIER_SLA_HOURS,
            "strategies": results,
            "has_compliant_strategy": any_compliant,
            "sla_satisfied": any_compliant,
        }
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Migration Intelligence SLA Enforcer — 3-tier 8h execution window"
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--db-path", type=Path, default=None)
    grp = parser.add_mutually_exclusive_group(required=True)
    grp.add_argument("--annotate", action="store_true", help="Annotate strategies with SLA data")
    grp.add_argument("--report", action="store_true", help="SLA compliance report")
    grp.add_argument("--check", type=str, metavar="OPPORTUNITY_ID",
                     help="Check SLA for a specific opportunity")

    args = parser.parse_args()
    db = str(args.db_path) if args.db_path else None

    if args.annotate:
        result = annotate_strategies(db_path=db)
    elif args.report:
        result = get_sla_report(db_path=db)
    else:
        result = check_sla_for_opportunity(args.check, db_path=db)

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
