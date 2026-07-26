# CUI // SP-CTI
"""OHC — LLMOps Engine.

Delegates to Phase 70 tools/llm/* ops tools and surfaces a unified API
for the /ops/llm dashboard page.

All Phase 70 tools remain in-place — this is an orchestration/view layer.
"""

from __future__ import annotations

from typing import Any


# ── Gateway ───────────────────────────────────────────────────────────────────

def get_gateway_stats() -> dict:
    """Return LLM gateway audit stats (last 24h)."""
    try:
        from tools.llm.gateway import LLMGateway
        gw = LLMGateway()
        return gw.get_stats()
    except Exception as exc:
        return {"error": str(exc), "available": False}


def get_gateway_audit(limit: int = 50) -> list[dict]:
    """Return recent gateway audit log entries."""
    try:
        from tools.llm.gateway import LLMGateway
        gw = LLMGateway()
        return gw.get_audit_log(limit=limit)
    except Exception as exc:
        return [{"error": str(exc)}]


# ── Cost Intelligence ─────────────────────────────────────────────────────────

def get_cost_report() -> dict:
    """Return cost dashboard — per-agent/model spend summary."""
    try:
        from tools.llm.cost_intelligence import CostIntelligence
        ci = CostIntelligence()
        return ci.get_dashboard()
    except Exception as exc:
        return {"error": str(exc), "available": False}


def get_cost_anomalies() -> list[dict]:
    """Return active cost anomalies (spike, overspend, projection)."""
    try:
        from tools.llm.cost_intelligence import CostIntelligence
        ci = CostIntelligence()
        return ci.get_anomalies()
    except Exception as exc:
        return [{"error": str(exc)}]


def get_cost_recommendations() -> list[dict]:
    """Return cost optimization recommendations."""
    try:
        from tools.llm.cost_intelligence import CostIntelligence
        ci = CostIntelligence()
        return ci.get_recommendations()
    except Exception as exc:
        return [{"error": str(exc)}]


# ── LLM Proxy Metrics (lpx-obs-01) ─────────────────────────────────────────────

def get_proxy_metrics(window_hours: int = 24) -> dict:
    """Return LLM proxy spend + rate metrics for the /ops/llm page.

    Combines ICDEV's own spend/rate ledgers (always present) with a best-effort
    scrape of the proxy's Prometheus endpoint (only when the proxy is enabled and
    reachable). Never raises — returns an ``available``/``error`` shape on failure.
    """
    try:
        from tools.llm.proxy_metrics import collect_proxy_metrics
        return collect_proxy_metrics(window_hours=window_hours)
    except Exception as exc:
        return {"error": str(exc), "proxy_enabled": False}


# ── Model Monitor / Drift ─────────────────────────────────────────────────────

def get_model_health() -> dict:
    """Return model quality/latency health summary across all monitored models."""
    try:
        from tools.llm.model_monitor import ModelMonitor
        mm = ModelMonitor()
        return mm.get_health()
    except Exception as exc:
        return {"error": str(exc), "available": False}


def get_drift_events(model_name: str = "", limit: int = 20) -> list[dict]:
    """Return recent statistical drift events."""
    try:
        from tools.llm.model_monitor import ModelMonitor
        mm = ModelMonitor()
        return mm.get_drift_events(model_name=model_name, limit=limit)
    except Exception as exc:
        return [{"error": str(exc)}]


# ── Prompt Registry ───────────────────────────────────────────────────────────

def get_prompt_registry(status: str = "active") -> list[dict]:
    """Return registered prompt templates."""
    try:
        from tools.llm.prompt_registry import PromptRegistry
        pr = PromptRegistry()
        return pr.list_prompts(status=status)
    except Exception as exc:
        return [{"error": str(exc)}]


def get_ab_tests(active_only: bool = True) -> list[dict]:
    """Return prompt A/B tests."""
    try:
        from tools.llm.prompt_registry import PromptRegistry
        pr = PromptRegistry()
        return pr.list_ab_tests(active_only=active_only)
    except Exception as exc:
        return [{"error": str(exc)}]


# ── Eval Runner ───────────────────────────────────────────────────────────────

def get_eval_results(limit: int = 10) -> list[dict]:
    """Return recent eval run results."""
    try:
        from tools.llm.eval_runner import EvalRunner
        er = EvalRunner()
        return er.list_results(limit=limit)
    except Exception as exc:
        return [{"error": str(exc)}]


# ── Langfuse Adapter (if available) ──────────────────────────────────────────

def get_langfuse_traces(limit: int = 20) -> list[dict]:
    """Return LLM traces from Langfuse if configured."""
    try:
        from tools.ops_hub.adapter_registry import get_adapter
        adapter = get_adapter("langfuse")
        if adapter and adapter.available():
            resources = adapter.list_resources("traces", limit=limit)
            return [r.to_dict() for r in resources]
    except Exception:
        pass
    return []


def get_langfuse_cost_by_model() -> list[dict]:
    """Return cost-per-model breakdown from Langfuse trace data."""
    try:
        from tools.ops_hub.adapter_registry import get_adapter
        adapter = get_adapter("langfuse")
        if adapter and adapter.available():
            metrics = adapter.get_metrics("cost_per_model")
            return metrics.values
    except Exception:
        pass
    return []


def get_langfuse_latency_p95() -> dict:
    """Return p95 latency (ms) across recent Langfuse traces."""
    try:
        from tools.ops_hub.adapter_registry import get_adapter
        adapter = get_adapter("langfuse")
        if adapter and adapter.available():
            metrics = adapter.get_metrics("latency_p95")
            if metrics.values:
                return metrics.values[0]
    except Exception:
        pass
    return {}


# ── Unified Summary ───────────────────────────────────────────────────────────

def get_llmops_summary() -> dict[str, Any]:
    """Roll-up summary for /ops/llm overview card."""
    gateway = get_gateway_stats()
    cost = get_cost_report()
    health = get_model_health()
    langfuse_traces = get_langfuse_traces(limit=5)
    langfuse_cost = get_langfuse_cost_by_model()
    langfuse_latency = get_langfuse_latency_p95()

    # Derive a simple health status
    has_drift = bool(get_drift_events(limit=1))
    has_anomaly = bool(get_cost_anomalies())

    status = "healthy"
    if has_drift or has_anomaly:
        status = "degraded"
    if gateway.get("injection_blocks_24h", 0) > 10:
        status = "critical"

    return {
        "status": status,
        "gateway": gateway,
        "cost": cost,
        "model_health": health,
        "has_drift": has_drift,
        "has_cost_anomaly": has_anomaly,
        "langfuse": {
            "trace_count": len(langfuse_traces),
            "recent_traces": langfuse_traces,
            "cost_by_model": langfuse_cost,
            "latency_p95": langfuse_latency,
        },
        "reasoned_codegen": get_reasoned_codegen_config(),
        "proxy": get_proxy_metrics(),
        "domain": "llmops",
    }


# ── Reasoned Codegen (tools/llm/reasoned_codegen*.py) ──────────────────────────

def get_reasoned_codegen_config() -> dict:
    """Return the reasoned_codegen config block from args/llm_config.yaml."""
    try:
        from pathlib import Path
        import yaml

        base = Path(__file__).resolve().parents[2]
        cfg_path = base / "args" / "llm_config.yaml"
        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        rc = raw.get("reasoned_codegen", {}) or {}
        per_fn = rc.get("per_function", {}) or {}
        functions = []
        for name, fn in per_fn.items():
            functions.append({
                "function": name,
                "enabled": bool(fn.get("enabled", False)),
                "mode": fn.get("mode", rc.get("default_mode", "off")),
                "critique": bool(fn.get("critique", rc.get("default_critique", False))),
                "max_repair_rounds": fn.get("max_repair_rounds", rc.get("max_repair_rounds", 2)),
            })
        return {
            "section_enabled": bool(rc.get("enabled", True)),
            "default_mode": rc.get("default_mode", "off"),
            "cost_cap_usd": rc.get("cost_cap_usd"),
            "token_cap": rc.get("token_cap"),
            "functions": functions,
        }
    except Exception as exc:
        return {"error": str(exc), "section_enabled": None, "functions": []}


def get_recent_chain_runs(limit: int = 25, function: str = "") -> list[dict]:
    """Recent CoT/CoD chain telemetry (from llm_chain_telemetry)."""
    try:
        from tools.db.storage import get_connection

        conn = get_connection()
        conn.set_security_context(None)  # rls-bypass: llm_chain_telemetry lacks tenant_id/classification
        try:
            where = "WHERE function = ?" if function else ""
            params: tuple = (function,) if function else ()
            rows = conn.execute(
                f"""
                SELECT function, chain_mode, models_used, final_model_id,
                       input_tokens, output_tokens, cost_usd, duration_ms,
                       stop_reason, created_at
                FROM llm_chain_telemetry
                {where}
                ORDER BY created_at DESC
                LIMIT %s
                """,
                params + (int(limit),),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            try:
                conn.close()
            except Exception:
                pass
    except Exception as exc:
        return [{"error": str(exc)}]


def run_reasoned_codegen_advisor(
    function: str, spec: str, file_count: int = 0, past_failures: int = 0,
    use_llm: bool = False,
) -> dict:
    """Run the reasoned-codegen advisor on a task spec (dashboard 'try it')."""
    try:
        from tools.llm.reasoned_codegen_advisor import recommend

        return recommend(
            function or "code_generation", spec or "",
            context={"file_count": int(file_count or 0), "past_failures": int(past_failures or 0)},
            use_llm=bool(use_llm),
        )
    except Exception as exc:
        return {"error": str(exc), "recommended": False, "mode": "off"}
