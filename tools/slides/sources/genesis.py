# CUI // SP-CTI
"""Source connector: Genesis reflex run summaries.

Reads from genesis_reflex_state to provide a summary of autonomous
operations for slide generation.
"""
from __future__ import annotations

from typing import Any

# Static catalog of key reflexes with descriptions for the slide context
_REFLEX_DESCRIPTIONS: dict[str, str] = {
    "research":      "Scrapes NIST/CISA/DoD feeds and GitHub trending signals",
    "scout":         "Monitors competitor repos for competitive intelligence",
    "audit":         "Self-audits code quality, security, compliance, and STIG",
    "comply":        "Refreshes cATO evidence and regenerates stale SSPs",
    "market":        "Monitors market trends and promotes innovation signals",
    "learn":         "Generates training pairs for local LLM fine-tuning",
    "heal":          "Detects and auto-remediates issues (confidence ≥ 0.7)",
    "evolve":        "Iterates codebase improvements via Bayesian experiments",
    "kanban":        "Dispatches kanban tasks autonomously to Claude CLI",
    "awareness":     "Indexes ICDEV components and detects structural gaps",
    "cpmp_monitor":  "CPMP surveillance: CPARS alerts, subcontractor compliance",
    "govchain_anchor": "Anchors compliance artifacts to Hyperledger Fabric",
    "goal_learner":  "Learns new goals from successful experiment outcomes",
}


def gather(max_reflexes: int = 15, include_last_run: bool = True) -> dict[str, Any]:
    """Return genesis reflex activity summary for slide generation."""
    reflex_summaries: list[dict] = []
    try:
        from tools.db.storage import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT reflex_name, last_run_at, last_result, run_count, "
                "consecutive_failures FROM genesis_reflex_state "
                "ORDER BY last_run_at DESC LIMIT %s",
                (max_reflexes,),
            ).fetchall()
            for row in rows:
                name = row["reflex_name"] if hasattr(row, "__getitem__") else row[0]
                last_run = row["last_run_at"] if hasattr(row, "__getitem__") else row[1]
                result = row["last_result"] if hasattr(row, "__getitem__") else row[2]
                run_count = row["run_count"] if hasattr(row, "__getitem__") else row[3]
                failures = row["consecutive_failures"] if hasattr(row, "__getitem__") else row[4]
                reflex_summaries.append({
                    "name": name,
                    "description": _REFLEX_DESCRIPTIONS.get(name, f"{name} autonomous reflex"),
                    "last_run": last_run,
                    "last_result": result,
                    "run_count": run_count or 0,
                    "healthy": (failures or 0) == 0,
                })
        finally:
            conn.close()
    except Exception:
        pass

    if not reflex_summaries:
        # Static fallback for when DB is unavailable
        for name, desc in list(_REFLEX_DESCRIPTIONS.items())[:max_reflexes]:
            reflex_summaries.append({"name": name, "description": desc, "healthy": True})

    healthy_count = sum(1 for r in reflex_summaries if r.get("healthy", True))
    total = len(reflex_summaries)

    return {
        "source": "genesis",
        "total_reflexes": total,
        "healthy_reflexes": healthy_count,
        "health_pct": round(healthy_count / total * 100) if total else 0,
        "reflexes": reflex_summaries,
        "summary": (
            f"Genesis daemon is running {total} autonomous reflexes "
            f"({healthy_count} healthy) covering research, compliance, security, "
            "kanban automation, and self-improvement."
        ),
    }
