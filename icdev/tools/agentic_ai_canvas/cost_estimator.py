# CUI // SP-CTI
"""Agentic AI Design Canvas — live cost estimator.

Walks a design graph, identifies model nodes, and computes token-based
inference cost projections. Fully deterministic — no LLM calls.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json

from tools.agentic_ai_canvas.constants import AADC_MODEL_COSTS

log = get_logger(__name__)

_DEFAULT_RUNS_PER_MONTH = 1000


def _resolve_model(node: dict) -> str | None:
    """Extract the model identifier from a node's properties or type."""
    props = node.get("properties") or node.get("attrs") or {}
    model = (
        props.get("model")
        or props.get("model_id")
        or props.get("llm_model")
        or ""
    ).lower().strip()
    if not model:
        # Fall back to node type slug
        ntype = node.get("type", "")
        if "ollama" in ntype or "local" in ntype:
            model = "ollama-local"
        elif "llm" in ntype:
            model = "gpt-4o"  # safe default for unspecified LLM
    return model or None


def _cost_for_model(model_id: str) -> dict:
    """Return cost entry for a model.

    Priority: (1) AADC local table, (2) AIMC FOUNDATION_MODELS catalog,
    (3) fuzzy prefix match, (4) gpt-4o-mini default.
    """
    if model_id in AADC_MODEL_COSTS:
        return AADC_MODEL_COSTS[model_id]
    # Try AIMC catalog — uses cost_per_1k_tokens (split 50/50 in/out heuristic)
    try:
        from tools.aiml_canvas.constants import FOUNDATION_MODELS as _AIMC_MODELS
        aimc_model = next((m for m in _AIMC_MODELS if m["id"] == model_id), None)
        if aimc_model and aimc_model.get("cost_per_1k_tokens", 0) >= 0:
            cost = aimc_model["cost_per_1k_tokens"]
            return {"input_per_1k": cost, "output_per_1k": cost * 2}
    except Exception:
        pass
    # Fuzzy match prefix against AADC table
    for key in AADC_MODEL_COSTS:
        if model_id.startswith(key) or key.startswith(model_id):
            return AADC_MODEL_COSTS[key]
    return AADC_MODEL_COSTS["gpt-4o-mini"]


def estimate_design_cost(graph: dict, runs_per_month: int = _DEFAULT_RUNS_PER_MONTH) -> dict:
    """Estimate token-based inference cost for a design graph.

    Args:
        graph: Design graph dict with 'nodes' and 'edges' lists.
        runs_per_month: Expected number of full pipeline invocations per month.

    Returns:
        {
            model_breakdown: {model_id: {tokens_per_run, cost_per_run, monthly_cost}},
            total_per_run: float,
            total_monthly: float,
            runs_per_month: int,
            optimization_hints: list[str],
            has_local_models: bool,
        }
    """
    nodes = graph.get("nodes", [])
    if isinstance(nodes, str):
        try:
            nodes = json.loads(nodes)
        except Exception:
            nodes = []

    # Collect model-bearing nodes
    model_nodes: list[tuple[str, str]] = []  # (node_id, model_id)
    for node in nodes:
        ntype = node.get("type", "")
        from tools.agentic_ai_canvas.constants import MODEL_NODES, AGENT_NODES
        if ntype in MODEL_NODES or any(ntype.startswith(a) for a in ("llm", "model-")):
            model_id = _resolve_model(node)
            if model_id:
                model_nodes.append((node.get("id", "?"), model_id))
        elif ntype in AGENT_NODES:
            # Agents may have an assigned model
            model_id = _resolve_model(node)
            if model_id:
                model_nodes.append((node.get("id", "?"), model_id))

    if not model_nodes:
        return {
            "model_breakdown": {},
            "total_per_run": 0.0,
            "total_monthly": 0.0,
            "runs_per_month": runs_per_month,
            "optimization_hints": ["No model nodes detected — add LLM or agent nodes to estimate cost."],
            "has_local_models": False,
        }

    breakdown: dict[str, dict] = {}
    for node_id, model_id in model_nodes:
        entry = _cost_for_model(model_id)
        tokens_per_run = entry["avg_in"] + entry["avg_out"]
        cost_per_run = (
            entry["avg_in"] / 1000.0 * entry["input"]
            + entry["avg_out"] / 1000.0 * entry["output"]
        )
        key = model_id
        if key not in breakdown:
            breakdown[key] = {
                "node_ids": [],
                "tokens_per_run": tokens_per_run,
                "cost_per_run": round(cost_per_run, 6),
                "monthly_cost": round(cost_per_run * runs_per_month, 4),
                "is_local": entry["input"] == 0.0 and entry["output"] == 0.0,
            }
        breakdown[key]["node_ids"].append(node_id)
        breakdown[key]["monthly_cost"] = round(
            breakdown[key]["cost_per_run"] * runs_per_month * len(breakdown[key]["node_ids"]), 4
        )

    total_per_run = sum(
        v["cost_per_run"] * len(v["node_ids"]) for v in breakdown.values()
    )
    total_monthly = total_per_run * runs_per_month
    has_local = any(v["is_local"] for v in breakdown.values())

    hints = _generate_hints(breakdown, total_per_run, total_monthly, runs_per_month)

    return {
        "model_breakdown": breakdown,
        "total_per_run": round(total_per_run, 6),
        "total_monthly": round(total_monthly, 4),
        "runs_per_month": runs_per_month,
        "optimization_hints": hints,
        "has_local_models": has_local,
    }


def _generate_hints(
    breakdown: dict,
    total_per_run: float,
    total_monthly: float,
    runs_per_month: int,
) -> list[str]:
    hints: list[str] = []

    # Hint: expensive model with a cheaper alternative
    expensive_swaps = {
        "claude-opus-4":  ("claude-sonnet-4",  0.003,  0.015),
        "gpt-4o":         ("gpt-4o-mini",       0.00015, 0.0006),
        "gemini-1.5-pro": ("gemini-1.5-flash",  0.000075, 0.0003),
        "mistral-large":  ("llama-3.3-70b",     0.00059, 0.00079),
    }
    for model_id, (alt, alt_in, alt_out) in expensive_swaps.items():
        if model_id in breakdown:
            entry = _cost_for_model(model_id)
            current_cost = breakdown[model_id]["cost_per_run"] * len(breakdown[model_id]["node_ids"])
            avg_in = entry["avg_in"]
            avg_out = entry["avg_out"]
            alt_cost = (avg_in / 1000.0 * alt_in + avg_out / 1000.0 * alt_out) * len(breakdown[model_id]["node_ids"])
            if current_cost > 0 and alt_cost < current_cost:
                savings_pct = round((1 - alt_cost / current_cost) * 100)
                savings_monthly = round((current_cost - alt_cost) * runs_per_month, 2)
                hints.append(
                    f"Swap {model_id} → {alt}: saves ~{savings_pct}% "
                    f"(${savings_monthly}/mo at {runs_per_month} runs)"
                )

    # Hint: multiple agents sharing the same model
    for model_id, info in breakdown.items():
        if len(info["node_ids"]) > 2 and not info["is_local"]:
            hints.append(
                f"{len(info['node_ids'])} agents use {model_id} — "
                "consider a shared model proxy to reduce per-call overhead."
            )

    # Hint: local model available
    local_monthly = sum(
        v["monthly_cost"] for v in breakdown.values() if v["is_local"]
    )
    cloud_monthly = total_monthly - local_monthly
    if has_local := any(v["is_local"] for v in breakdown.values()):
        if cloud_monthly > 0:
            hints.append(
                f"Local model handles some load. Routing more calls to Ollama "
                f"could save up to ${round(cloud_monthly, 2)}/mo."
            )

    # Hint: high monthly cost warning
    if total_monthly > 500:
        hints.append(
            f"Estimated ${round(total_monthly, 2)}/mo — consider batching requests "
            "or caching frequent queries with a semantic-cache node."
        )
    elif total_monthly == 0 and not has_local:
        hints.append("All models are local (Ollama) — no API cost at runtime.")

    return hints
