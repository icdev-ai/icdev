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


def _handle_stig_triage(config: dict) -> dict:
    stig_id = config.get("stig_id", "V-XXXXX")
    severity = config.get("severity", "CAT II")
    return {
        "status": "ok",
        "action": "stig_triage",
        "result": {
            "stig_id": stig_id,
            "severity": severity,
            "recommendation": "Apply patch via ansible playbook. Estimated remediation: 2 hours.",
            "auto_poam": True,
            "evidence_collected": True,
        },
    }


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
        return {"status": "ok", "action": "rag_search",
                "results": [{"text": f"RAG demo: searched for '{query}'", "score": 0.92}],
                "note": "Live RAG unavailable — demo mode"}


def _handle_poam_draft(config: dict) -> dict:
    system_id = config.get("system_id", "SYS-001")
    findings = config.get("findings", [])
    return {
        "status": "ok",
        "action": "poam_draft",
        "poam_items": len(findings) or 3,
        "system_id": system_id,
        "draft_url": f"/compliance/poam?system={system_id}",
        "auto_populated": True,
    }


def _handle_ato_timeline(config: dict) -> dict:
    impact_level = config.get("impact_level", "IL4")
    controls = int(config.get("control_count", 110))
    weeks_est = max(4, controls // 8)
    return {
        "status": "ok",
        "action": "ato_timeline",
        "impact_level": impact_level,
        "estimated_weeks": weeks_est,
        "evidence_tasks": controls,
        "automation_coverage": "68%",
        "risk_score": "Medium",
    }


def _handle_ai_inventory(config: dict) -> dict:
    return {
        "status": "ok",
        "action": "ai_inventory",
        "systems_found": 7,
        "omb_compliant": 5,
        "gaps": ["Missing risk assessment for 2 systems", "No incident response plan documented"],
        "report_url": "/compliance/ai-transparency",
    }


def _handle_govcon_scan(config: dict) -> dict:
    keywords = config.get("keywords", [])
    naics = config.get("naics", "")
    return {
        "status": "ok",
        "action": "govcon_scan",
        "opportunities": 12,
        "keywords": keywords,
        "naics": naics,
        "top_match": "Advanced AI Integration Services — DoD — $4.2M — closes in 18 days",
        "report_url": "/govcon",
    }
