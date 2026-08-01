# CUI // SP-CTI
"""FORGE Academy guided step configurator — form data → ICDEV API dispatch."""

from __future__ import annotations

import logging

_log = logging.getLogger(__name__)


def dispatch_configure(data: dict) -> dict:
    """Route a guided step configure submission to the appropriate ICDEV API handler."""
    action = data.get("action", "")
    config = data.get("config", {})

    handlers = {
        "deploy_pattern": _handle_deploy_pattern,
        "stig_triage": _handle_stig_triage,
        "rag_search": _handle_rag_search,
        "poam_draft": _handle_poam_draft,
        "ato_timeline": _handle_ato_timeline,
        "ai_inventory": _handle_ai_inventory,
        "govcon_scan": _handle_govcon_scan,
    }

    handler = handlers.get(action)
    if not handler:
        return {"status": "error", "message": f"Unknown action: '{action}'",
                "available": list(handlers.keys())}
    try:
        return handler(config)
    except Exception as exc:
        _log.warning("configure dispatch '%s' failed: %s", action, exc)
        return {"status": "error", "message": str(exc)}


def _handle_deploy_pattern(config: dict) -> dict:
    pattern_id = config.get("pattern_id", "")
    from .integrations import deploy_pattern
    result = deploy_pattern(pattern_id)
    return {**result, "action": "deploy_pattern", "pattern_id": pattern_id}


def _simulated(action: str, real_tool: str, result: dict, why: str = "") -> dict:
    """Wrap a teaching-only result so it can never read as a real system response.

    aca-hon-01: five of the seven handlers returned hardcoded constants that ignored
    the learner's input and reported a bare status='ok'. The learner was shown fiction
    labelled as the output of the action they had just run, across 42 configure steps.
    That is the same defect fga-fix-02 removed from watch steps ("a fabricated example
    presented to a learner as this step's actual output"), and the honest pattern was
    already in this file: _handle_rag_search tags its fallback with a demo-mode note.

    Every synthetic response now carries simulated=True, a human-readable note, and
    the name of the real tool it stands in for — so a reader knows what wiring it up
    would actually mean. The UI renders the marker (_step_configure.html).
    """
    return {
        "status": "ok",
        "action": action,
        "simulated": True,
        "source": "teaching-simulation",
        "real_tool": real_tool,
        "note": (
            why
            or f"Illustrative values for teaching. Not a real result — the live "
               f"equivalent is {real_tool}."
        ),
        "result": result,
    }


def _handle_stig_triage(config: dict) -> dict:
    stig_id = config.get("stig_id", "V-XXXXX")
    severity = config.get("severity", "CAT II")
    return _simulated(
        "stig_triage",
        "tools/security STIG scanning (MCP: stig_check)",
        {
            "stig_id": stig_id,
            "severity": severity,
            "recommendation": (
                "Illustrative only: a real triage would come from a STIG scan of a "
                "specific target, not from the finding id alone."
            ),
        },
    )


def _handle_rag_search(config: dict) -> dict:
    query = config.get("query", "")
    try:
        from tools.rag.retriever import simple_search
        results = simple_search(query, top_k=3)
        return {"status": "ok", "action": "rag_search", "results": results}
    except Exception:
        pass
    try:
        from tools.rag.retriever import retrieve
        results = retrieve(query, top_k=3)
        return {"status": "ok", "action": "rag_search", "results": results}
    except Exception:
        # Already the honest pattern before aca-hon-01; now uses the shared marker so
        # the UI treats every simulation the same way. The 0.92 score is dropped — an
        # invented relevance number is exactly the kind of detail that reads as real.
        return _simulated(
            "rag_search",
            "tools/rag/retriever.py",
            {"query": query, "results": []},
            why="Live RAG is unavailable in this lab, so no retrieval results are shown.",
        )


def _handle_poam_draft(config: dict) -> dict:
    """Count real findings when given them; never invent a count.

    The old version returned ``len(findings) or 3`` — so an empty findings list
    silently became "3 POA&M items", a number with no origin at all.
    """
    system_id = config.get("system_id", "SYS-001")
    findings = config.get("findings", []) or []
    return _simulated(
        "poam_draft",
        "tools/compliance/poam_generator.py (MCP: poam_generate)",
        {
            "system_id": system_id,
            # Derived from the learner's own input, or honestly zero.
            "poam_items": len(findings),
            "findings_supplied": len(findings),
            "draft_url": f"/compliance/poam?system={system_id}",
        },
        why=(
            "Counts only the findings you supplied. A real draft is generated from a "
            "scan against a registered system by tools/compliance/poam_generator.py."
        ),
    )


def _handle_ato_timeline(config: dict) -> dict:
    """Show the arithmetic, and drop the two invented judgements.

    ``automation_coverage: "68%"`` and ``risk_score: "Medium"`` were pure invention —
    neither is derivable from a control count. The week estimate at least follows a
    stated rule, so it stays with the rule shown.
    """
    impact_level = config.get("impact_level", "IL4")
    controls = int(config.get("control_count", 110) or 0)
    weeks_est = max(4, controls // 8)
    return _simulated(
        "ato_timeline",
        "tools/dashboard/api/cato.py + the compliance crosswalk engine",
        {
            "impact_level": impact_level,
            "control_count": controls,
            "estimated_weeks": weeks_est,
            "estimate_rule": "max(4, control_count // 8) — a teaching heuristic",
        },
        why=(
            "The week estimate is the stated heuristic applied to your control count. "
            "Automation coverage and a risk score are NOT included: neither can be "
            "derived from a control count, and the previous fixed 68% / Medium were "
            "invented. Real figures come from a cATO assessment of a live system."
        ),
    )


def _handle_ai_inventory(config: dict) -> dict:
    """Read the real inventory when the table is reachable; otherwise say so.

    The old version reported systems_found 7 / omb_compliant 5 and two invented gap
    strings, ignoring config entirely — numbers about the learner's own estate that
    were simply made up.
    """
    try:
        from tools.db.storage import get_connection

        conn = get_connection()
        total = conn.execute(
            "SELECT COUNT(*) FROM ai_use_case_inventory"
        ).fetchone()[0]
        return {
            "status": "ok",
            "action": "ai_inventory",
            "simulated": False,
            "source": "ai_use_case_inventory",
            "result": {
                "systems_found": int(total),
                "report_url": "/compliance/ai-transparency",
            },
        }
    except Exception as exc:  # noqa: BLE001 — fall back to an HONEST simulation
        _log.debug("ai_inventory: live inventory unavailable: %s", exc)
        return _simulated(
            "ai_inventory",
            "the ai_use_case_inventory table (MCP: ai_inventory_register)",
            {"systems_found": None, "report_url": "/compliance/ai-transparency"},
            why=(
                "The live AI inventory is not reachable from this lab, so no counts "
                "are shown. Previously this reported invented totals for your estate."
            ),
        )


def _handle_govcon_scan(config: dict) -> dict:
    """Echo the query; never invent an opportunity.

    The old version returned "opportunities: 12" and a fabricated award — a named
    programme, a dollar value and a closing date — which is the single most
    misleading string in this file.
    """
    keywords = config.get("keywords", []) or []
    naics = config.get("naics", "")
    return _simulated(
        "govcon_scan",
        "tools/govcon/sam_scanner.py (SAM.gov)",
        {
            "keywords": keywords,
            "naics": naics,
            "opportunities": None,
            "report_url": "/govcon",
        },
        why=(
            "No opportunity data is shown: a real scan queries SAM.gov via "
            "tools/govcon/sam_scanner.py and needs credentials this lab does not "
            "have. The previous fixed count and named award were invented."
        ),
    )
