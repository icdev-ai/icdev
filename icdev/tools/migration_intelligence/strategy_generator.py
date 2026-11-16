#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration Intelligence — Strategy Generator + Roadmap Builder.

Generates migration strategy options for scored opportunities and assembles
wave-based roadmaps aligned to enterprise goals.

It does not score 7R (icx-mdz-02). For an opportunity raised from a legacy application (``source_canvas`` ``mdc``,
``source_entity_id`` the application's id), the strategy marked ``recommended`` is the one THE 7R engine,
``tools/modernization/seven_r_assessor.py``, recommended in its latest assessment (``strategy_for_app``), with its
confidence. With no assessment, or one that recommends nothing for lack of evidence, no option is marked
recommended and the result says why: the first option in a list is never a recommendation.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _get_mi_conn(db_path: str | None = None):
    from tools.migration_intelligence.db.init_db import get_connection
    return get_connection(db_path)


# =========================================================================
# SCORING
# =========================================================================

def score_all_opportunities(config: dict | None = None, db_path: str | None = None) -> dict[str, Any]:
    """Compute composite scores for all identified opportunities."""
    cfg = (config or {}).get("scoring", {})
    weights = cfg.get("weights", {
        "eol_urgency": 0.25, "goal_alignment": 0.25, "business_value": 0.20,
        "risk": 0.10, "effort": 0.10, "compliance": 0.10,
    })

    conn = _get_mi_conn(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM mi_opportunities WHERE status IN ('identified','scored')"
        ).fetchall()
        updated = 0
        for r in rows:
            r = dict(r)
            # Get best goal alignment score
            aln = conn.execute(
                "SELECT MAX(alignment_score) as best FROM mi_goal_alignments WHERE opportunity_id = %s",
                (r["id"],),
            ).fetchone()
            goal_score = (aln["best"] or 0.0) if aln else 0.0

            composite = (
                r["eol_urgency_score"] * weights.get("eol_urgency", 0.25)
                + goal_score * weights.get("goal_alignment", 0.25)
                + r["business_value_score"] * weights.get("business_value", 0.20)
                + (1.0 - r["risk_score"]) * weights.get("risk", 0.10)
                + (1.0 - r["effort_score"]) * weights.get("effort", 0.10)
                + r["compliance_score"] * weights.get("compliance", 0.10)
            )
            composite = round(min(1.0, max(0.0, composite)), 4)

            conn.execute(
                """UPDATE mi_opportunities
                   SET composite_score=%s, goal_alignment_score=%s, status='scored', updated_at=%s
                   WHERE id=%s""",
                (composite, goal_score, _now(), r["id"]),
            )
            updated += 1
        conn.commit()
        return {"ok": True, "scored": updated}
    finally:
        conn.close()


# =========================================================================
# STRATEGY GENERATION
# =========================================================================

_STRATEGY_TEMPLATES: dict[str, dict] = {
    "rehost": {
        "title": "Rehost (Lift & Shift)",
        "rationale": "Move workload as-is to target environment with minimal change.",
        "risk_level": "low",
        "pros": ["Fastest path", "Low risk", "Preserves existing functionality"],
        "cons": ["No modernization benefit", "May carry technical debt"],
        "prerequisites": ["Target environment provisioned", "Network connectivity validated"],
    },
    "replatform": {
        "title": "Replatform (Lift & Reshape)",
        "rationale": "Minor optimizations during migration to leverage target platform capabilities.",
        "risk_level": "medium",
        "pros": ["Some modernization benefit", "Moderate effort", "Platform optimization"],
        "cons": ["Requires config changes", "Moderate testing"],
        "prerequisites": ["Platform compatibility assessed", "Config delta documented"],
    },
    "refactor": {
        "title": "Refactor (Modernise the Code)",
        "rationale": "Upgrade the language level and frameworks in place, keeping the architecture.",
        "risk_level": "medium",
        "pros": ["Removes end-of-life dependencies", "Keeps the domain logic", "Incremental"],
        "cons": ["Code changes need regression tests", "Architecture limits remain"],
        "prerequisites": ["Upgrade path identified", "Regression tests in place"],
    },
    "rebuild": {
        "title": "Rebuild (Rearchitect Cloud-Native)",
        "rationale": "Redesign the application into cloud-native services.",
        "risk_level": "high",
        "pros": ["Full cloud-native benefits", "Scales independently"],
        "cons": ["Highest effort", "New ATO boundary"],
        "prerequisites": ["Target architecture agreed", "Service boundaries identified"],
    },
    "replace": {
        "title": "Replace (Buy vs Build)",
        "rationale": "Retire current system and replace with commercial off-the-shelf or SaaS equivalent.",
        "risk_level": "medium",
        "pros": ["Eliminates legacy debt", "Vendor support", "Modern feature set"],
        "cons": ["Data migration required", "User retraining", "Vendor lock-in risk"],
        "prerequisites": ["Replacement product evaluated", "Data migration plan"],
    },
    "retire": {
        "title": "Retire (Decommission)",
        "rationale": "System is no longer needed — decommission and remove from inventory.",
        "risk_level": "low",
        "pros": ["Eliminates cost", "Reduces attack surface"],
        "cons": ["Must confirm no hidden dependencies"],
        "prerequisites": ["Dependency analysis complete", "Data archived or deleted per policy"],
    },
    "retain": {
        "title": "Retain (Keep As-Is)",
        "rationale": "System is not ready for migration — defer to future cycle.",
        "risk_level": "low",
        "pros": ["No disruption", "No immediate cost"],
        "cons": ["Delays modernization", "EOL risk if deferred too long"],
        "prerequisites": ["EOL extension negotiated if needed"],
    },
    "rip_and_replace": {
        "title": "Rip & Replace (Full Forklift)",
        "rationale": "Complete hardware/software replacement — used for EOL network equipment.",
        "risk_level": "high",
        "pros": ["Clean slate", "Modern platform", "Eliminates EOL risk"],
        "cons": ["High effort", "Maintenance window required", "Traffic impact"],
        "prerequisites": ["Replacement equipment procured", "Config conversion complete", "Test plan ready"],
    },
    "phased_cutover": {
        "title": "Phased Cutover",
        "rationale": "Migrate circuits/services in phases to minimize blast radius.",
        "risk_level": "medium",
        "pros": ["Controlled rollout", "Smaller blast radius per phase", "Easier rollback"],
        "cons": ["Dual-running cost", "Longer project duration"],
        "prerequisites": ["Circuit prioritization complete", "Traffic baselining done"],
    },
    "parallel_run": {
        "title": "Parallel Run",
        "rationale": "Run old and new systems simultaneously during validation window.",
        "risk_level": "medium",
        "pros": ["Zero-risk validation", "Instant rollback", "Side-by-side comparison"],
        "cons": ["2x resource cost during window", "Complex traffic management"],
        "prerequisites": ["Target system fully provisioned", "Monitoring in place for both"],
    },
}

_NETWORK_EXTRA = {"rip_and_replace", "phased_cutover", "parallel_run"}
_NETWORK_TYPES = {"eol_replacement", "network_redesign"}

#: The 7R engine's strategy names in this canvas's vocabulary (mi_strategies.strategy_type).
SEVEN_R_TO_MI = {"rehost": "rehost", "replatform": "replatform", "refactor": "refactor", "rearchitect": "rebuild",
                 "repurchase": "replace", "retire": "retire", "retain": "retain"}
#: Opportunities raised from a legacy application carry its id here (the modernisation canvas).
APP_SOURCE_CANVAS = "mdc"


def seven_r_for_opportunity(opp: dict) -> dict:
    """What THE 7R engine says for an opportunity's application (icx-mdz-02): never a score of its own.

    ``status`` is ``recommended`` (with ``strategy`` in this canvas's vocabulary and the engine's ``confidence``),
    ``insufficient_evidence`` (assessed, but no strategy reached the minimum confidence), ``not_assessed`` (the
    application has no assessment), ``not_an_application`` (the opportunity was not raised from one) or
    ``unreadable``.
    """
    app_id = (opp.get("source_entity_id") or "").strip()
    if (opp.get("source_canvas") or "").strip().lower() != APP_SOURCE_CANVAS or not app_id:
        return {"status": "not_an_application", "strategy": None,
                "reason": "not raised from a legacy application (source_canvas mdc): the 7R engine has nothing to assess"}
    try:
        from tools.modernization.seven_r_assessor import strategy_for_app
        summary = strategy_for_app(app_id)
    except Exception as exc:  # noqa: BLE001 -- unreadable is said, never a recommendation
        return {"status": "unreadable", "strategy": None, "app_id": app_id, "reason": f"the 7R assessment cannot be read: {exc}"}
    if summary is None:
        return {"status": "not_assessed", "strategy": None, "app_id": app_id,
                "reason": f"{app_id} has no 7R assessment: assess it on /migration first"}
    rec = summary["recommended_strategy"]
    out = {"app_id": app_id, "assessment_id": summary["assessment_id"], "seven_r_strategy": rec,
           "confidence": summary["confidence"], "min_confidence": summary["min_confidence"],
           "not_assessable": summary["not_assessable"],
           "reason": (summary.get("recommendation") or {}).get("reason")}
    if not rec:
        return {**out, "status": "insufficient_evidence", "strategy": None}
    return {**out, "status": "recommended", "strategy": SEVEN_R_TO_MI[rec]}


def generate_strategies_for_opportunity(
    opportunity_id: str,
    config: dict | None = None,
    db_path: str | None = None,
) -> dict[str, Any]:
    """Generate strategy options for a scored opportunity."""
    cfg = (config or {}).get("strategy", {})
    default_types = cfg.get("default_strategies", ["rehost", "replatform", "replace"])
    network_extra = cfg.get("network_extra_strategies", ["rip_and_replace", "phased_cutover"])
    max_strats = cfg.get("max_strategies_per_opportunity", 4)

    conn = _get_mi_conn(db_path)
    try:
        opp = conn.execute("SELECT * FROM mi_opportunities WHERE id = %s", (opportunity_id,)).fetchone()
        if not opp:
            return {"ok": False, "error": "Opportunity not found"}
        opp = dict(opp)

        # Choose strategy types based on opportunity type
        strategy_types = list(default_types)
        if opp["opportunity_type"] in _NETWORK_TYPES:
            for s in network_extra:
                if s not in strategy_types:
                    strategy_types.append(s)
        strategy_types = strategy_types[:max_strats]
        # The recommendation is THE 7R engine's (icx-mdz-02); its strategy is always among the options
        seven_r = seven_r_for_opportunity(opp)
        recommended_type = seven_r.get("strategy")
        if recommended_type and recommended_type not in strategy_types:
            strategy_types = [recommended_type, *strategy_types][:max(max_strats, 1)]

        created = []
        for st in strategy_types:
            tmpl = _STRATEGY_TEMPLATES.get(st, {})
            strat_id = f"strat-{uuid.uuid4().hex[:8]}"
            rationale = tmpl.get("rationale", "")
            if st == recommended_type:
                rationale = (f"{rationale} Recommended by the 7R assessment {seven_r['assessment_id']} "
                             f"(confidence {seven_r['confidence']:.2f}).").strip()
            effort_est = _estimate_effort(opp, st)
            cost_est = effort_est * 1500  # rough $/day

            conn.execute(
                """INSERT OR IGNORE INTO mi_strategies
                   (id, opportunity_id, strategy_type, title, rationale,
                    target_platform, estimated_effort_days, estimated_cost_usd,
                    risk_level, pros, cons, prerequisites,
                    recommended, status, created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    strat_id, opportunity_id, st,
                    tmpl.get("title", st.replace("_", " ").title()),
                    rationale,
                    opp.get("target_platform", ""),
                    effort_est, cost_est,
                    tmpl.get("risk_level", "medium"),
                    json.dumps(tmpl.get("pros", [])),
                    json.dumps(tmpl.get("cons", [])),
                    json.dumps(tmpl.get("prerequisites", [])),
                    1 if st == recommended_type else 0,
                    "generated", _now(),
                ),
            )
            created.append({"id": strat_id, "type": st, "recommended": st == recommended_type})

        conn.execute(
            "UPDATE mi_opportunities SET status='strategy_generated', updated_at=%s WHERE id=%s",
            (_now(), opportunity_id),
        )
        conn.commit()
        return {"ok": True, "opportunity_id": opportunity_id, "strategies_created": len(created), "strategies": created,
                "recommended": recommended_type, "seven_r": seven_r}
    finally:
        conn.close()


def generate_all_strategies(config: dict | None = None, db_path: str | None = None) -> dict[str, Any]:
    """Generate strategies for all scored opportunities."""
    conn = _get_mi_conn(db_path)
    try:
        rows = conn.execute(
            "SELECT id FROM mi_opportunities WHERE status = 'scored' ORDER BY composite_score DESC"
        ).fetchall()
        opp_ids = [r["id"] for r in rows]
    finally:
        conn.close()

    results = []
    for oid in opp_ids:
        r = generate_strategies_for_opportunity(oid, config=config, db_path=db_path)
        results.append(r)
    return {"ok": True, "processed": len(results), "results": results}


def _estimate_effort(opp: dict, strategy_type: str) -> int:
    """Rough effort estimate in days."""
    _BASE = {
        "rehost": 5, "replatform": 15, "replace": 30,
        "retire": 3, "retain": 1,
        "rip_and_replace": 20, "phased_cutover": 25, "parallel_run": 20,
        "refactor": 45, "rebuild": 90, "consolidate": 20,
    }
    base = _BASE.get(strategy_type, 15)
    urgency = opp.get("eol_urgency_score", 0.5)
    # More urgent → assume simpler approach already chosen → no multiplier
    return max(1, int(base * (1.0 + (1.0 - urgency) * 0.5)))


# =========================================================================
# ROADMAP BUILDER
# =========================================================================

def build_roadmap(
    name: str = "Migration Roadmap",
    horizon_years: int = 5,
    config: dict | None = None,
    db_path: str | None = None,
) -> dict[str, Any]:
    """Build a wave-based migration roadmap from scored opportunities."""
    cfg = (config or {}).get("roadmap", {})
    wave_max = cfg.get("wave_size_max", 8)

    conn = _get_mi_conn(db_path)
    try:
        # Get all scored/strategy_generated opps ordered by composite score
        rows = conn.execute(
            """SELECT id, title, priority, composite_score,
                      estimated_effort_days, estimated_cost_usd
               FROM mi_opportunities
               WHERE status IN ('scored','strategy_generated','planned')
               ORDER BY composite_score DESC, priority DESC"""
        ).fetchall()
        opps = [dict(r) for r in rows]
    finally:
        conn.close()

    if not opps:
        return {"ok": False, "error": "No scored opportunities to roadmap"}

    # Assign waves based on priority + score
    waves: dict[int, list] = {}
    for opp in opps:
        wave_num = _assign_wave(opp, cfg)
        waves.setdefault(wave_num, []).append(opp)

    # Trim waves to max size
    for wn in waves:
        waves[wn] = waves[wn][:wave_max]

    # Build structured payload
    wave_list = []
    total_cost = 0.0
    total_months = 0
    for wn in sorted(waves.keys()):
        w_opps = waves[wn]
        w_cost = sum(o.get("estimated_cost_usd") or 0 for o in w_opps)
        w_effort = max((o.get("estimated_effort_days") or 0) for o in w_opps)  # parallel
        w_months = max(1, round(w_effort / 20))
        total_cost += w_cost
        total_months += w_months
        wave_list.append({
            "wave": wn,
            "opportunities": [{"id": o["id"], "title": o["title"], "priority": o["priority"]}
                               for o in w_opps],
            "estimated_duration_months": w_months,
            "estimated_cost_usd": w_cost,
        })

    roadmap_id = f"rm-{uuid.uuid4().hex[:10]}"
    payload = json.dumps({"waves": wave_list, "total_cost_usd": total_cost})

    conn = _get_mi_conn(db_path)
    try:
        conn.execute(
            """INSERT INTO mi_roadmaps
               (id, name, description, horizon_years, wave_count,
                opportunities_count, estimated_duration_months,
                estimated_total_cost_usd, payload, status, generated_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                roadmap_id, name,
                f"Auto-generated {horizon_years}-year migration roadmap — {len(opps)} opportunities",
                horizon_years, len(wave_list), len(opps),
                total_months, total_cost, payload, "draft", _now(),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "ok": True,
        "roadmap_id": roadmap_id,
        "wave_count": len(wave_list),
        "opportunities_count": len(opps),
        "estimated_duration_months": total_months,
        "estimated_total_cost_usd": total_cost,
        "waves": wave_list,
    }


def _assign_wave(opp: dict, cfg: dict) -> int:
    priority = opp.get("priority", "medium")
    score = opp.get("composite_score", 0.0)
    if priority == "critical" or score >= 0.85:
        return cfg.get("critical_priority_wave", 1)
    if priority == "high" or score >= 0.65:
        return cfg.get("high_priority_wave", 2)
    if score >= 0.45:
        return 3
    return 4
