# CUI // SP-CTI
"""Amendment seed — adds 5 tasks missed in the original fdt- plan.

Findings from direct code review of tauricresearch/tradingagents:
1. ResearchManager layer missing (synthesizes 4 reports → investment plan)
2. Trader layer missing (investment plan → transaction proposal)
3. Risk debate is 3-sided (Aggressive/Conservative/Neutral), not Bull/Bear
4. TradingMemoryLog deferred reflection loop — entirely missed
5. 5-tier signal scale (Buy/Overweight/Hold/Underweight/Sell)

Also corrects fdt-magent-06 dependency chain.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402
from tools.kanban.task_factory import create_tasks  # noqa: E402

NOW = datetime.now(timezone.utc).isoformat()

NEW_TASKS = [
    {
        "id": "fdt-magent-06b",
        "title": "Create research_manager.py — synthesize 4 analyst reports into investment plan",
        "description": (
            "Missing layer between the 4 analyst agents and the debate engine. "
            "TradingAgents ResearchManager takes the 4 analyst reports and synthesizes "
            "them into a single coherent investment plan using the quick_think_llm. "
            "Create tools/fathomdesk/agents/research_manager.py: "
            "run_synthesis(reports: dict[str, str]) -> str (the investment plan text). "
            "Use quick model from fathomdesk_config.yaml quick_think_llm. "
            "Output feeds directly into debate_engine.py as context. "
            "Reuse: llm_factory.get_llm('research_manager'). New file only."
        ),
        "task_type": "build",
        "priority": "critical",
        "status": "scheduled",
        "depends_on_task_id": "fdt-magent-05",
    },
    {
        "id": "fdt-magent-06c",
        "title": "Create trader.py — transaction proposal from investment plan",
        "description": (
            "Missing layer: after the investment debate, TradingAgents runs a Trader agent "
            "that translates the investment plan into a concrete transaction proposal "
            "(direction: BUY/SELL/HOLD, size modifier, entry rationale). "
            "This proposal feeds the RISK debate separately from the investment debate. "
            "Create tools/fathomdesk/agents/trader.py: "
            "propose(investment_plan: str, ticker: str, analyst_reports: dict) -> dict. "
            "Use deep_think_llm (Opus). "
            "Output schema: {direction, size_modifier, rationale}. "
            "Reuse llm_factory. New file only."
        ),
        "task_type": "build",
        "priority": "critical",
        "status": "scheduled",
        "depends_on_task_id": "fdt-magent-06b",
    },
    {
        "id": "fdt-magent-06d",
        "title": "Revise debate_engine.py — add 3-sided risk debate (Aggressive/Conservative/Neutral)",
        "description": (
            "Our original plan had one Bull/Bear debate. TradingAgents runs TWO separate debates: "
            "(1) Investment debate: Bull vs Bear on whether to invest (already in magent-06). "
            "(2) Risk debate: Aggressive vs Conservative vs Neutral on HOW to execute the trader plan. "
            "Add run_risk_debate(trader_proposal: dict, analyst_reports: dict) -> dict to debate_engine.py. "
            "3 roles: Aggressive (maximize upside), Conservative (minimize downside), Neutral (balance). "
            "N rounds from fathomdesk_config.yaml max_risk_discuss_rounds (default 1). "
            "Output: {aggressive_history, conservative_history, neutral_history, history}. "
            "Portfolio Manager in risk_manager.py arbitrates this output. "
            "Edit debate_engine.py only — no new file."
        ),
        "task_type": "build",
        "priority": "critical",
        "status": "scheduled",
        "depends_on_task_id": "fdt-magent-06c",
    },
    {
        "id": "fdt-magent-11",
        "title": "Create decision_memory.py — deferred reflection feedback loop",
        "description": (
            "Most valuable pattern from TradingAgents missed in the original plan. "
            "Append-only markdown log that: "
            "(1) Immediately stores each decision with [date|ticker|rating|pending] tag (no LLM). "
            "(2) On the next same-ticker run, fetches actual return via data_gateway.fetch_ohlcv(), "
            "calls quick_think_llm.invoke() for a 2-4 sentence reflection on the outcome. "
            "(3) Injects N past same-ticker decisions + M cross-ticker lessons into risk_manager.py prompt. "
            "Create tools/fathomdesk/decision_memory.py: "
            "store_decision(ticker, date, decision), get_pending(ticker), "
            "update_with_return(ticker, date, raw_return, alpha_return), "
            "get_past_context(ticker, n_same=5, n_cross=3) -> str. "
            "Use atomic temp-file replace for all writes. Log path: data/fathomdesk_decisions.md. "
            "Wire get_past_context() into risk_manager.py prompt context before Portfolio Manager call."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "scheduled",
        "depends_on_task_id": "fdt-magent-10",
    },
    {
        "id": "fdt-magent-12",
        "title": "Add 5-tier signal scale (Buy/Overweight/Hold/Underweight/Sell) to signal_generator.py",
        "description": (
            "TradingAgents uses a 5-tier scale deterministically parsed from PM structured output: "
            "Buy > Overweight > Hold > Underweight > Sell. "
            "Add parse_rating(text: str) -> str to tools/fathomdesk/signal_generator.py "
            "(keyword search in priority order, no LLM needed). "
            "Add RATING_TO_SIZE_MODIFIER dict: Buy=1.0, Overweight=0.75, Hold=0.5, "
            "Underweight=0.25, Sell=0.0. "
            "Update risk_manager.py to call parse_rating() on Portfolio Manager output "
            "and include size_modifier in final recommendation dict. "
            "Edit signal_generator.py + risk_manager.py only. No schema changes."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "scheduled",
        "depends_on_task_id": "fdt-magent-10",
    },
]

EXTRA_DEPS = [
    # research_manager also waits for agents 02, 03, 04 (already waits for 05 via linear dep)
    ("fdt-magent-06b", "fdt-magent-02"),
    ("fdt-magent-06b", "fdt-magent-03"),
    ("fdt-magent-06b", "fdt-magent-04"),
    # risk debate needs investment debate done (magent-06) before it can run
    ("fdt-magent-06d", "fdt-magent-06"),
]

DEP_UPDATES = [
    # magent-06 (investment debate) now depends on magent-06b (research manager)
    # because the debate needs the synthesized investment plan as context
    ("fdt-magent-06", "fdt-magent-06b"),
    # magent-07 (risk_manager / portfolio manager) now depends on magent-06d (risk debate done)
    ("fdt-magent-07", "fdt-magent-06d"),
]


def seed():
    # Fold the EXTRA_DEPS junction rows into each task's depends_on list so the
    # factory writes both the scalar parent and the multi-parent junction rows.
    extra_by_task: dict[str, list[str]] = {}
    for task_id, dep_id in EXTRA_DEPS:
        extra_by_task.setdefault(task_id, []).append(dep_id)
    # These tasks are genuinely time-deferred: status 'scheduled' WITH a real
    # scheduled_at (NOW). Stamp scheduled_at + extra deps on every task.
    for t in NEW_TASKS:
        t["scheduled_at"] = NOW
        if t["id"] in extra_by_task:
            t["depends_on"] = extra_by_task[t["id"]]

    report = create_tasks("fdt", NEW_TASKS, strict=False, register_project=False)
    print(report.summary())

    # DEP_UPDATES re-point the scalar parent of PRE-EXISTING tasks (not seeded
    # here), so they remain a direct UPDATE.
    conn = get_connection()
    c = conn.cursor()
    updated = 0
    for task_id, new_dep in DEP_UPDATES:
        c.execute(
            "UPDATE kanban_tasks SET depends_on_task_id=%s WHERE id=%s",
            (new_dep, task_id),
        )
        print(f"  UPDATE dep: {task_id} -> {new_dep}")
        updated += 1
    conn.commit()
    conn.close()

    print(
        f"\nDone — {len(report.seeded)} tasks inserted / {len(report.skipped)} skipped | "
        f"{updated} dep chain corrections"
    )


if __name__ == "__main__":
    seed()
