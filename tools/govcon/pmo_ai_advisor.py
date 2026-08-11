# CUI // SP-CTI
"""
PMO AI Advisor — LLM-backed contract health analysis for CPMP.

Functions:
    auto_detect_issues(contract_id)          Scan for schedule/cost/compliance issues
    get_pmo_recommendations(contract_id)     Priority-sorted improvement actions
    predict_award_fee_narrative(contract_id) CPARS prediction with narrative
    generate_corrective_action(contract_id, issue_type, context_data) Action plan
"""

import json
from datetime import date, timedelta

from tools.db.storage import get_connection
from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

# ── Config ────────────────────────────────────────────────────────────

_SEVERITY_MAP = {
    "critical": {"threshold": 16, "label": "critical"},
    "high": {"threshold": 11, "label": "high"},
    "medium": {"threshold": 6, "label": "medium"},
    "low": {"threshold": 1, "label": "low"},
}

_RATING_LABELS = {
    "exceptional": "Exceptional",
    "very_good": "Very Good",
    "satisfactory": "Satisfactory",
    "marginal": "Marginal",
    "unsatisfactory": "Unsatisfactory",
}


def _get_db():
    conn = get_connection()
    conn.set_security_context(None)  # rls-bypass: govcon service-layer; govcon tables lack tenant_id/classification columns
    return conn


def _try_llm(function_name, messages, system_prompt=""):
    """Invoke LLMRouter with graceful degradation. Returns text or None."""
    try:
        from tools.llm.router import LLMRouter
        router = LLMRouter()
        if not router.has_any_llm():
            return None
        from tools.llm.provider import LLMRequest
        req = LLMRequest(
            messages=messages,
            system_prompt=system_prompt or "You are a DoD Program Management Office AI advisor. Be concise, specific, and actionable. Use plain text, no markdown.",
            classification="CUI",
            effort="medium",
        )
        resp = router.invoke(function_name, req)
        return resp.content if resp else None
    except Exception as e:
        logger.warning("LLM unavailable for %s: %s", function_name, e)
        return None


def _gather_contract_context(contract_id):
    """Pull EVM, deliverables, risks, subcontractors, CPARS into a context dict."""
    conn = _get_db()
    ctx = {}
    try:
        row = conn.execute("SELECT * FROM cpmp_contracts WHERE id = %s", (contract_id,)).fetchone()
        if not row:
            return ctx
        ctx["contract"] = dict(row)

        evm = conn.execute(
            "SELECT cpi, spi, eac, vac, tcpi FROM cpmp_evm_periods "
            "WHERE contract_id = %s ORDER BY period_date DESC LIMIT 1",
            (contract_id,)
        ).fetchone()
        ctx["evm"] = dict(evm) if evm else {}

        overdue = conn.execute(
            "SELECT COUNT(*) as cnt FROM cpmp_deliverables "
            "WHERE contract_id = %s AND status NOT IN ('accepted','rejected') AND due_date < %s",
            (contract_id, date.today().isoformat())
        ).fetchone()
        ctx["overdue_deliverables"] = overdue["cnt"] if overdue else 0

        due_soon = conn.execute(
            "SELECT COUNT(*) as cnt FROM cpmp_deliverables "
            "WHERE contract_id = %s AND status NOT IN ('accepted','rejected') "
            "AND due_date >= %s AND due_date <= %s",
            (contract_id, date.today().isoformat(), (date.today() + timedelta(days=30)).isoformat())
        ).fetchone()
        ctx["due_in_30_days"] = due_soon["cnt"] if due_soon else 0

        open_risks = conn.execute(
            "SELECT COUNT(*) as cnt, MAX(exposure) as max_exp FROM cpmp_risks "
            "WHERE contract_id = %s AND status IN ('open','mitigating')",
            (contract_id,)
        ).fetchone()
        ctx["open_risks"] = open_risks["cnt"] if open_risks else 0
        ctx["max_risk_exposure"] = open_risks["max_exp"] if open_risks else 0

        noncompliant_subs = conn.execute(
            # status = 'active' must match subcontractor_tracker.check_flowdown /
            # check_cybersecurity. Without it the advisor counts inactive and
            # terminated subs, so it reported gaps the noncompliance API said were
            # clear and told the PM to issue a cure notice to a sub they had
            # already deactivated.
            "SELECT COUNT(*) as cnt FROM cpmp_subcontractors "
            "WHERE contract_id = %s AND status = 'active' "
            "AND (flow_down_complete = 0 OR cybersecurity_compliant = 0)",
            (contract_id,)
        ).fetchone()
        ctx["noncompliant_subs"] = noncompliant_subs["cnt"] if noncompliant_subs else 0

        latest_cpars = conn.execute(
            "SELECT overall_rating, overall_score FROM cpmp_cpars_assessments "
            "WHERE contract_id = %s ORDER BY period_end DESC LIMIT 1",
            (contract_id,)
        ).fetchone()
        ctx["latest_cpars"] = dict(latest_cpars) if latest_cpars else {}

        open_mods = conn.execute(
            "SELECT COUNT(*) as cnt FROM cpmp_contract_mods "
            "WHERE contract_id = %s AND status IN ('requested','in_review')",
            (contract_id,)
        ).fetchone()
        ctx["open_mods"] = open_mods["cnt"] if open_mods else 0

    except Exception as e:
        logger.warning("Context gather error: %s", e)
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return ctx


# ── Public Functions ──────────────────────────────────────────────────

def auto_detect_issues(contract_id):
    """
    Scan the contract for schedule, cost, risk, and compliance issues.
    Returns deterministic findings enriched with LLM suggested actions.
    """
    ctx = _gather_contract_context(contract_id)
    if not ctx.get("contract"):
        return {"status": "error", "message": "Contract not found", "issues": [], "total": 0}

    issues = []
    evm = ctx.get("evm", {})
    contract = ctx["contract"]

    cpi = evm.get("cpi")
    spi = evm.get("spi")

    if cpi and cpi < 0.85:
        issues.append({
            "type": "cost_overrun_critical",
            "severity": "critical",
            "source": "evm",
            "description": f"CPI = {cpi:.3f} — contract is significantly over budget. EAC indicates overrun of ${abs(evm.get('vac', 0)):,.0f}.",
            "suggested_action": "Convene cost recovery review; submit EAC reconciliation to CO within 30 days.",
        })
    elif cpi and cpi < 0.95:
        issues.append({
            "type": "cost_overrun_warning",
            "severity": "medium",
            "source": "evm",
            "description": f"CPI = {cpi:.3f} — cost performance trending below threshold.",
            "suggested_action": "Review labor hours and ODC spend; identify root cause WBS elements.",
        })

    if spi and spi < 0.85:
        issues.append({
            "type": "schedule_slip_critical",
            "severity": "critical",
            "source": "evm",
            "description": f"SPI = {spi:.3f} — contract is significantly behind schedule.",
            "suggested_action": "Submit schedule recovery plan to COR within 15 days; update IMS forecast dates.",
        })
    elif spi and spi < 0.95:
        issues.append({
            "type": "schedule_slip_warning",
            "severity": "medium",
            "source": "evm",
            "description": f"SPI = {spi:.3f} — schedule slipping. Review milestone forecast dates.",
            "suggested_action": "Update IMS; identify critical path impacts; brief COR at next status meeting.",
        })

    if ctx["overdue_deliverables"] > 0:
        issues.append({
            "type": "overdue_deliverables",
            "severity": "high",
            "source": "deliverables",
            "description": f"{ctx['overdue_deliverables']} CDRL(s) are past due and not yet accepted.",
            "suggested_action": "Submit or escalate overdue CDRLs immediately; document delays with rationale for COR.",
        })

    if ctx["noncompliant_subs"] > 0:
        issues.append({
            "type": "subcontractor_compliance",
            "severity": "high",
            "source": "subcontractors",
            "description": f"{ctx['noncompliant_subs']} subcontractor(s) have incomplete flow-down or cybersecurity gaps.",
            "suggested_action": "Issue cure notice to non-compliant subs; complete CMMC assessment within 60 days.",
        })

    if ctx.get("max_risk_exposure", 0) >= 16:
        issues.append({
            "type": "critical_risk",
            "severity": "critical",
            "source": "risk_register",
            "description": f"Critical risk (exposure ≥ 16) identified in risk register with {ctx['open_risks']} open risks.",
            "suggested_action": "Escalate critical risks to program manager; activate contingency plan if applicable.",
        })

    if ctx["open_mods"] > 0:
        issues.append({
            "type": "pending_modifications",
            "severity": "low",
            "source": "modifications",
            "description": f"{ctx['open_mods']} contract modification(s) pending approval.",
            "suggested_action": "Follow up with CO to expedite MOD processing; ensure funding is not blocked.",
        })

    return {
        "status": "ok",
        "issues": issues,
        "total": len(issues),
        "contract_number": contract.get("contract_number"),
    }


def get_pmo_recommendations(contract_id):
    """
    Generate priority-sorted PMO improvement recommendations.
    Deterministic analysis + optional LLM narrative.
    """
    ctx = _gather_contract_context(contract_id)
    if not ctx.get("contract"):
        return {"status": "error", "message": "Contract not found", "recommendations": []}

    contract = ctx["contract"]
    evm = ctx.get("evm", {})
    cpi = evm.get("cpi", 1.0) or 1.0
    spi = evm.get("spi", 1.0) or 1.0

    recs = []

    if cpi < 0.95:
        recs.append({
            "category": "Cost Control",
            "finding": f"CPI of {cpi:.3f} indicates costs exceeding planned budget.",
            "action": "Conduct Estimate at Completion (EAC) scrub; renegotiate labor rates if CPFF; tighten ODC approvals.",
            "urgency": "high" if cpi < 0.85 else "medium",
            "impact": "Prevent cost overrun from eroding fixed fee and triggering CO intervention.",
            "timeline": "30 days",
        })

    if spi < 0.95:
        recs.append({
            "category": "Schedule Recovery",
            "finding": f"SPI of {spi:.3f} — schedule slipping vs. baseline.",
            "action": "Update IMS with realistic forecast dates; identify critical path; consider overtime or surge staffing.",
            "urgency": "high" if spi < 0.85 else "medium",
            "impact": "Prevent schedule slip from impacting CPARS schedule rating.",
            "timeline": "15 days",
        })

    if ctx["overdue_deliverables"] > 0:
        recs.append({
            "category": "Deliverable Management",
            "finding": f"{ctx['overdue_deliverables']} CDRLs are overdue.",
            "action": "Prioritize overdue CDRL submission; assign dedicated writer/reviewer; track daily until accepted.",
            "urgency": "high",
            "impact": "Overdue CDRLs directly degrade CPARS Quality and Schedule ratings.",
            "timeline": "5–10 days",
        })

    if ctx["noncompliant_subs"] > 0:
        recs.append({
            "category": "Subcontractor Compliance",
            "finding": f"{ctx['noncompliant_subs']} subs have FAR flow-down or CMMC gaps.",
            "action": "Issue written cure notice; schedule CMMC gap assessment; update subcontract agreements.",
            "urgency": "medium",
            "impact": "Non-compliant subs create DFARS violation risk and can block award of future options.",
            "timeline": "45 days",
        })

    if ctx["due_in_30_days"] > 0 and ctx["overdue_deliverables"] == 0:
        recs.append({
            "category": "Upcoming Deliverables",
            "finding": f"{ctx['due_in_30_days']} CDRL(s) due within 30 days.",
            "action": "Confirm draft completion status; schedule internal review board; brief COR on timeline.",
            "urgency": "low",
            "impact": "Proactive management prevents deliverables from becoming overdue.",
            "timeline": "Now",
        })

    # Determine overall health
    if cpi < 0.85 or spi < 0.85 or ctx["overdue_deliverables"] > 0:
        overall = "red"
    elif cpi < 0.95 or spi < 0.95 or ctx["noncompliant_subs"] > 0:
        overall = "yellow"
    else:
        overall = "green"

    # Optional LLM enrichment
    summary = None
    if recs:
        context_str = (
            f"Contract {contract.get('contract_number')} ({contract.get('agency', 'DoD')}) — "
            f"CPI={cpi:.3f}, SPI={spi:.3f}, "
            f"overdue CDRLs={ctx['overdue_deliverables']}, "
            f"open risks={ctx['open_risks']}, "
            f"noncompliant subs={ctx['noncompliant_subs']}."
        )
        llm_text = _try_llm(
            "pmo_recommendations",
            [{"role": "user", "content": f"Summarize the PMO health in 2 sentences and the single most important action: {context_str}"}]
        )
        if llm_text:
            summary = llm_text.strip()

    recs.sort(key=lambda r: {"high": 0, "medium": 1, "low": 2}.get(r["urgency"], 3))

    return {
        "status": "ok",
        "overall_health": overall,
        "summary": summary,
        "recommendations": recs,
        "contract_number": contract.get("contract_number"),
    }


def predict_award_fee_narrative(contract_id):
    """
    Predict CPARS rating with LLM-generated narrative explaining strengths and risks.
    """
    try:
        from tools.govcon.cpars_predictor import predict_cpars
        pred = predict_cpars(contract_id)
    except Exception as e:
        logger.warning("CPARS predictor error: %s", e)
        pred = {}

    ctx = _gather_contract_context(contract_id)
    contract = ctx.get("contract", {})
    evm = ctx.get("evm", {})

    predicted_rating = pred.get("predicted_rating", "satisfactory")
    score = pred.get("predicted_score", 0.7)
    dims = pred.get("dimension_scores", {})

    # Deterministic strengths and risk factors
    strengths = []
    risk_factors = []

    if dims.get("quality", 0) >= 0.8:
        strengths.append("Consistent technical quality — high CDRL acceptance rate")
    if dims.get("schedule", 0) >= 0.8:
        strengths.append("On-time delivery performance above government threshold")
    if evm.get("cpi", 0) >= 0.95:
        strengths.append(f"Cost discipline — CPI of {evm.get('cpi', 1.0):.3f} within green threshold")
    if dims.get("small_business", 0) >= 0.8:
        strengths.append("Small business subcontracting goals on track")

    if dims.get("schedule", 1) < 0.7:
        risk_factors.append(f"Schedule performance below threshold (SPI={evm.get('spi','N/A')})")
    if dims.get("cost", 1) < 0.7:
        risk_factors.append("Cost overrun risk — CPI trending below 0.90")
    if ctx.get("overdue_deliverables", 0) > 0:
        risk_factors.append(f"{ctx['overdue_deliverables']} overdue CDRL(s) will impact Quality rating")
    if ctx.get("noncompliant_subs", 0) > 0:
        risk_factors.append("Subcontractor compliance gaps affect Small Business dimension")

    # LLM narrative
    narrative = None
    context_prompt = (
        f"Contract {contract.get('contract_number', 'N/A')} — predicted CPARS: {predicted_rating} (score={score:.2f}). "
        f"Dimension scores: {json.dumps({k: round(v, 2) for k, v in dims.items()})}. "
        f"EVM: CPI={evm.get('cpi','N/A')}, SPI={evm.get('spi','N/A')}. "
        f"Strengths: {strengths}. Risk factors: {risk_factors}. "
        "Write a 3-sentence CPARS narrative for the program manager explaining the predicted rating, "
        "what is driving it, and the single most impactful thing they can do to improve it."
    )
    llm_text = _try_llm(
        "pmo_award_fee_narrative",
        [{"role": "user", "content": context_prompt}]
    )
    if llm_text:
        narrative = llm_text.strip()
    else:
        narrative = (
            f"This contract is tracking toward a {_RATING_LABELS.get(predicted_rating, predicted_rating)} rating "
            f"based on current EVM performance (CPI={evm.get('cpi', 'N/A')}, SPI={evm.get('spi', 'N/A')}) "
            f"and deliverable acceptance rates. "
            + (f"The primary risk is {risk_factors[0].lower()}." if risk_factors else "Performance is within acceptable thresholds.")
        )

    return {
        "status": "ok",
        "predicted_rating": predicted_rating,
        "score": score,
        "narrative": narrative,
        "strengths": strengths,
        "risk_factors": risk_factors,
        "contract_number": contract.get("contract_number"),
    }


def generate_corrective_action(contract_id, issue_type, context_data=None):
    """
    Generate an AI corrective action plan for a specific issue type.
    Falls back to deterministic templates when LLM is unavailable.
    """
    ctx = _gather_contract_context(contract_id)
    contract = ctx.get("contract", {})
    evm = ctx.get("evm", {})
    context_data = context_data or {}

    _TEMPLATES = {
        "cost_overrun_critical": {
            "priority": "critical", "timeline_days": 30, "owner_role": "PM + CO",
            "action_plan": (
                "1. Within 5 days: Convene EAC scrub with finance lead. Identify top 3 WBS elements driving overrun.\n"
                "2. Within 10 days: Submit Formal EAC to Contracting Officer with root cause analysis.\n"
                "3. Within 15 days: Implement cost reduction measures (reduce non-critical ODC, renegotiate labor mix).\n"
                "4. Within 30 days: Establish weekly cost burn review cadence with COR. Track CPI recovery target ≥ 0.90."
            ),
        },
        "schedule_slip_critical": {
            "priority": "high", "timeline_days": 15, "owner_role": "PM + COR",
            "action_plan": (
                "1. Within 3 days: Update IMS with revised forecast dates. Identify critical path milestones at risk.\n"
                "2. Within 5 days: Brief COR on schedule status and recovery plan.\n"
                "3. Within 10 days: Evaluate surge staffing or overtime for critical path tasks.\n"
                "4. Within 15 days: Submit Schedule Recovery Plan to CO if slip exceeds 30 days."
            ),
        },
        "overdue_deliverables": {
            "priority": "high", "timeline_days": 10, "owner_role": "Program Manager",
            "action_plan": (
                "1. Immediately: Assign dedicated writer to each overdue CDRL. Set daily completion checkpoints.\n"
                "2. Within 2 days: Notify COR of delay status with expected submission date.\n"
                "3. Within 5 days: Complete internal review and submit overdue CDRLs.\n"
                "4. Prevent recurrence: Update CDRL tracker; set internal due dates 5 days before contract due dates."
            ),
        },
        "subcontractor_compliance": {
            "priority": "medium", "timeline_days": 45, "owner_role": "Subcontract Manager",
            "action_plan": (
                "1. Within 5 days: Issue written cure notice to non-compliant subcontractors.\n"
                "2. Within 14 days: Schedule CMMC gap assessment with each subcontractor's ISSO.\n"
                "3. Within 30 days: Update subcontract agreements to include missing FAR flow-down clauses.\n"
                "4. Within 45 days: Obtain signed certifications of compliance or initiate replacement sourcing."
            ),
        },
    }

    template = _TEMPLATES.get(issue_type, {
        "priority": "medium", "timeline_days": 30, "owner_role": "Program Manager",
        "action_plan": "Review the identified issue with your team and develop a tailored corrective action plan.",
    })

    # Try LLM for richer narrative
    contract_num = contract.get("contract_number", "N/A")
    prompt = (
        f"Contract {contract_num}: issue type '{issue_type}'. "
        f"EVM: CPI={evm.get('cpi','N/A')}, SPI={evm.get('spi','N/A')}. "
        f"Additional context: {json.dumps(context_data)}. "
        "Generate a 4-step corrective action plan (numbered list) that a DoD program manager can execute immediately. "
        "Each step should include: who acts, what to do, by when. Be specific."
    )
    llm_plan = _try_llm(
        "pmo_corrective_action",
        [{"role": "user", "content": prompt}]
    )

    return {
        "status": "ok",
        "issue_type": issue_type,
        "priority": template["priority"],
        "timeline_days": template["timeline_days"],
        "owner_role": template["owner_role"],
        "action_plan": llm_plan.strip() if llm_plan else template["action_plan"],
        "contract_number": contract_num,
    }
