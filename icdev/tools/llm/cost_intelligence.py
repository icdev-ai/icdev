# [TEMPLATE: CUI // SP-CTI]
"""LLM Cost Intelligence Module.

Analyzes token usage data to detect anomalies, project spend, and recommend
optimizations.  100% air-gapped — reads ``agent_token_usage`` /
``agent_token_budgets`` tables and model pricing from ``args/llm_config.yaml``.
No external API calls.

Usage::

    python tools/llm/cost_intelligence.py --dashboard --json
    python tools/llm/cost_intelligence.py --anomalies --json
    python tools/llm/cost_intelligence.py --project --json
    python tools/llm/cost_intelligence.py --project --agent orchestrator --json
    python tools/llm/cost_intelligence.py --recommend --json
    python tools/llm/cost_intelligence.py --edge-vs-cloud --function code_generation --json
    python tools/llm/cost_intelligence.py --alerts --json
    python tools/llm/cost_intelligence.py --gate
"""

from __future__ import annotations
import sys
from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.logging.icdev_logger import get_logger

import argparse
import json
import logging
import statistics
import sys
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = BASE_DIR / "args" / "llm_config.yaml"

logger = get_logger("cost_intelligence")

# Alert / recommendation type enums (mirrored in CHECK constraints)
ALERT_TYPES = ("spike", "overspend", "projection", "optimization")
SEVERITY_LEVELS = ("info", "warning", "critical")
RECOMMENDATION_TYPES = (
    "downgrade_model",
    "switch_to_local",
    "batch_requests",
    "reduce_tokens",
    "cache_responses",
    "enable_cot",
    "enable_cod",
)
RECOMMENDATION_STATUSES = ("pending", "accepted", "rejected", "implemented")


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------


def _get_conn():
    """Return a connection to the main ICDEV™ database."""
    from tools.db.storage import get_connection  # noqa: E402

    return get_connection()


def init_tables(conn=None):
    """Create cost-intelligence tables if they do not exist."""
    own_conn = conn is None
    if own_conn:
        conn = _get_conn()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS llm_cost_alerts (
            id TEXT PRIMARY KEY,
            alert_type TEXT NOT NULL CHECK(alert_type IN ('spike','overspend','projection','optimization')),
            agent_id TEXT,
            model_id TEXT,
            function_name TEXT,
            severity TEXT NOT NULL CHECK(severity IN ('info','warning','critical')),
            message TEXT NOT NULL,
            details_json TEXT,
            acknowledged INTEGER DEFAULT 0,
            acknowledged_by TEXT,
            created_at TEXT NOT NULL,
            acknowledged_at TEXT
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_cost_alerts_ack
        ON llm_cost_alerts(acknowledged)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_cost_alerts_severity
        ON llm_cost_alerts(severity)
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS llm_cost_recommendations (
            id TEXT PRIMARY KEY,
            recommendation_type TEXT NOT NULL CHECK(
                recommendation_type IN (
                    'downgrade_model','switch_to_local','batch_requests',
                    'reduce_tokens','cache_responses',
                    'enable_cot','enable_cod'
                )
            ),
            function_name TEXT,
            current_model TEXT,
            recommended_model TEXT,
            estimated_savings_usd REAL DEFAULT 0.0,
            confidence REAL DEFAULT 0.0,
            status TEXT NOT NULL DEFAULT 'pending' CHECK(
                status IN ('pending','accepted','rejected','implemented')
            ),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_cost_rec_status
        ON llm_cost_recommendations(status)
    """)

    conn.commit()
    if own_conn:
        conn.close()


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


def _load_config() -> dict:
    """Load llm_config.yaml and return the full dict."""
    if not CONFIG_PATH.exists():
        logger.warning("llm_config.yaml not found at %s", CONFIG_PATH)
        return {}
    with open(CONFIG_PATH, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _model_pricing(config: dict) -> dict[str, dict]:
    """Return {model_name: {input_per_1k, output_per_1k}} from config."""
    models = config.get("models", {})
    pricing: dict[str, dict] = {}
    for name, info in models.items():
        p = info.get("pricing", {})
        pricing[name] = {
            "input_per_1k": float(p.get("input_per_1k", 0.0)),
            "output_per_1k": float(p.get("output_per_1k", 0.0)),
        }
    return pricing


def _is_local_model(config: dict, model_name: str) -> bool:
    """Return True if *model_name* runs on Ollama (local, zero-cost)."""
    models = config.get("models", {})
    info = models.get(model_name, {})
    return info.get("provider") in ("ollama", "mistral_vllm")


#: Why a cost figure is what it is. A bare 0.0 is ambiguous in the one way that
#: matters: local inference really is free, and a cloud model with no price in
#: the table is simply UNMEASURED. Recording both as 0.0 is how
#: module_budget_usage accumulated 1,391 rows summing to $0.00 - including 557
#: calls on kimi-k2.6:cloud - while the $150 monthly USD cap sat at 0% and could
#: never fire. Enforcement then ran entirely on the token proxy.
COST_BASIS_PRICED = "priced"          # computed from a real per-1k price
COST_BASIS_LOCAL_ZERO = "local_zero"  # local provider: $0 is the true cost
COST_BASIS_UNPRICED = "unpriced"      # NO price for a non-local model: unknown


def compute_cost_usd(
    model_name: str,
    input_tokens: int,
    output_tokens: int,
    config: dict | None = None,
) -> tuple[float, str]:
    """Return ``(cost_usd, basis)`` for one call.

    ``LLMResponse.cost_usd`` is documented as "when provider computes it" and NO
    provider computes it - all nine adapters leave the 0.0 default - so the
    field was dead from the start. Cost is therefore derived centrally here from
    the per-model ``pricing`` block, which one place can do correctly for every
    provider.

    The basis is the load-bearing half of the return value. ``unpriced`` is NOT
    folded into ``local_zero``: 30 of 39 configured models carry
    ``{input_per_1k: 0.0, output_per_1k: 0.0}``, and for the local ones that is
    the truth while for claude-opus it plainly is not. A caller that cannot tell
    them apart is back to the bug this function exists to fix, so the cost is
    still 0.0 for an unpriced model - inventing a number would be worse - but it
    is labelled, and therefore countable.
    """
    cfg = config if config is not None else _load_config()
    if _is_local_model(cfg, model_name):
        return 0.0, COST_BASIS_LOCAL_ZERO

    pricing = (cfg.get("models", {}).get(model_name, {}) or {}).get("pricing", {}) or {}
    in_per_1k = float(pricing.get("input_per_1k", 0.0) or 0.0)
    out_per_1k = float(pricing.get("output_per_1k", 0.0) or 0.0)
    if in_per_1k <= 0.0 and out_per_1k <= 0.0:
        return 0.0, COST_BASIS_UNPRICED

    cost = (max(0, int(input_tokens or 0)) / 1000.0) * in_per_1k
    cost += (max(0, int(output_tokens or 0)) / 1000.0) * out_per_1k
    return round(cost, 6), COST_BASIS_PRICED


def _scanner_functions(config: dict) -> list[str]:
    """Return the list of scanner-tier functions from two_tier config."""
    return config.get("two_tier", {}).get("scanner_functions", [])


def _model_id_to_name(config: dict) -> dict[str, str]:
    """Map model_id values back to the config model name."""
    mapping: dict[str, str] = {}
    for name, info in config.get("models", {}).items():
        mid = info.get("model_id", "")
        # Strip env-var wrappers like ${OLLAMA_MODEL:-...}
        if mid.startswith("${") and ":-" in mid:
            mid = mid.split(":-")[1].rstrip("}")
        mapping[mid] = name
        mapping[name] = name  # allow lookup by name too
    return mapping


def _gen_id(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _month_boundaries(now: datetime | None = None) -> tuple[str, str, str]:
    """Return (month_str, month_start_iso, next_month_start_iso)."""
    if now is None:
        now = datetime.now(timezone.utc)
    month_str = now.strftime("%Y-%m")
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if now.month == 12:
        next_month_start = now.replace(year=now.year + 1, month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        next_month_start = now.replace(month=now.month + 1, day=1, hour=0, minute=0, second=0, microsecond=0)
    return month_str, month_start.isoformat(), next_month_start.isoformat()


# ---------------------------------------------------------------------------
# Alert creation helper
# ---------------------------------------------------------------------------


def _create_alert(
    conn,
    alert_type: str,
    severity: str,
    message: str,
    agent_id: str | None = None,
    model_id: str | None = None,
    function_name: str | None = None,
    details: dict | None = None,
) -> str:
    alert_id = _gen_id("calrt-")
    conn.execute(
        """INSERT INTO llm_cost_alerts
           (id, alert_type, agent_id, model_id, function_name,
            severity, message, details_json, created_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (
            alert_id,
            alert_type,
            agent_id,
            model_id,
            function_name,
            severity,
            message,
            json.dumps(details) if details else None,
            _now_iso(),
        ),
    )
    return alert_id


# ---------------------------------------------------------------------------
# Core analysis functions
# ---------------------------------------------------------------------------


def detect_cost_anomalies(lookback_hours: int = 24) -> dict:
    """Compare recent spend rate to 7-day rolling average.

    Returns dict with anomalies found and any new alerts created.
    """
    conn = _get_conn()
    init_tables(conn)
    now = datetime.now(timezone.utc)
    cutoff_recent = (now - timedelta(hours=lookback_hours)).isoformat()
    cutoff_7d = (now - timedelta(days=7)).isoformat()

    # Per-agent hourly spend for last 7 days
    rows_7d = conn.execute(
        """SELECT agent_id,
                  SUM(cost_estimate_usd) as total_cost,
                  COUNT(*) as call_count
           FROM agent_token_usage
           WHERE created_at >= %s
           GROUP BY agent_id""",
        (cutoff_7d,),
    ).fetchall()

    # Per-agent recent spend
    rows_recent = conn.execute(
        """SELECT agent_id,
                  SUM(cost_estimate_usd) as total_cost,
                  COUNT(*) as call_count
           FROM agent_token_usage
           WHERE created_at >= %s
           GROUP BY agent_id""",
        (cutoff_recent,),
    ).fetchall()

    # Build 7-day per-hour averages
    avg_hourly: dict[str, float] = {}
    for row in rows_7d:
        agent = row[0] if isinstance(row, (tuple, list)) else row["agent_id"]
        total = float(row[1] if isinstance(row, (tuple, list)) else row["total_cost"])
        avg_hourly[agent] = total / (7 * 24)

    # Compute per-hour recent rate
    anomalies: list[dict] = []
    alerts_created: list[str] = []
    for row in rows_recent:
        agent = row[0] if isinstance(row, (tuple, list)) else row["agent_id"]
        recent_total = float(row[1] if isinstance(row, (tuple, list)) else row["total_cost"])
        recent_hourly = recent_total / max(lookback_hours, 1)
        baseline = avg_hourly.get(agent, 0.0)

        if baseline > 0:
            ratio = recent_hourly / baseline
        elif recent_hourly > 0:
            ratio = float("inf")
        else:
            ratio = 0.0

        # Also compute stddev from daily buckets for the agent
        daily_rows = conn.execute(
            """SELECT DATE(created_at) as day, SUM(cost_estimate_usd) as daily_cost
               FROM agent_token_usage
               WHERE agent_id = %s AND created_at >= %s
               GROUP BY DATE(created_at)""",
            (agent, cutoff_7d),
        ).fetchall()
        daily_costs = [float(r[1] if isinstance(r, (tuple, list)) else r["daily_cost"]) for r in daily_rows]
        stddev = statistics.stdev(daily_costs) if len(daily_costs) >= 2 else 0.0
        daily_mean = statistics.mean(daily_costs) if daily_costs else 0.0

        # Alert if recent daily-equivalent > mean + 2*stddev
        recent_daily_equiv = recent_hourly * 24
        threshold = daily_mean + 2 * stddev if stddev > 0 else daily_mean * 2

        is_anomaly = (threshold > 0 and recent_daily_equiv > threshold) or ratio > 3.0

        if is_anomaly:
            severity = "critical" if ratio > 5.0 else "warning"
            detail = {
                "agent_id": agent,
                "recent_hourly_usd": round(recent_hourly, 6),
                "baseline_hourly_usd": round(baseline, 6),
                "ratio": round(ratio, 2) if ratio != float("inf") else "inf",
                "daily_mean_usd": round(daily_mean, 4),
                "daily_stddev_usd": round(stddev, 4),
                "recent_daily_equiv_usd": round(recent_daily_equiv, 4),
                "threshold_usd": round(threshold, 4),
            }
            msg = (
                f"Cost spike detected for agent '{agent}': "
                f"recent rate {recent_hourly:.4f}/hr vs baseline {baseline:.4f}/hr "
                f"({ratio:.1f}x)"
                if ratio != float("inf")
                else f"Cost spike detected for agent '{agent}': new spend {recent_hourly:.4f}/hr with no prior baseline"
            )
            aid = _create_alert(conn, "spike", severity, msg, agent_id=agent, details=detail)
            alerts_created.append(aid)
            anomalies.append(detail)

    conn.commit()
    conn.close()

    return {
        "status": "ok",
        "lookback_hours": lookback_hours,
        "anomalies_found": len(anomalies),
        "anomalies": anomalies,
        "alerts_created": alerts_created,
    }


def project_monthly_spend(agent_id: str | None = None) -> dict:
    """Linear extrapolation of current-month spend to end of month.

    Warns if projected spend exceeds budget in ``agent_token_budgets``.
    """
    conn = _get_conn()
    init_tables(conn)
    now = datetime.now(timezone.utc)
    month_str, month_start_iso, next_month_iso = _month_boundaries(now)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    # Days elapsed (at least 1 to avoid div-by-zero)
    days_elapsed = max((now - month_start).days + 1, 1)

    # Days in month
    if now.month == 12:
        next_month = now.replace(year=now.year + 1, month=1, day=1)
    else:
        next_month = now.replace(month=now.month + 1, day=1)
    days_in_month = (next_month - month_start).days

    # Query spend
    agent_filter = ""
    params: list = [month_start_iso, next_month_iso]
    if agent_id:
        agent_filter = " AND agent_id = ?"
        params.append(agent_id)

    rows = conn.execute(
        f"""SELECT agent_id,
                   SUM(cost_estimate_usd) as spent,
                   COUNT(*) as calls
            FROM agent_token_usage
            WHERE created_at >= %s AND created_at < %s{agent_filter}
            GROUP BY agent_id""",  # nosec B608 — agent_filter is hardcoded
        params,
    ).fetchall()

    # Budgets
    budget_params: list = [month_str]
    budget_filter = ""
    if agent_id:
        budget_filter = " AND agent_id = ?"
        budget_params.append(agent_id)

    budget_rows = conn.execute(
        f"""SELECT agent_id, budget_usd, spent_usd, warning_threshold
            FROM agent_token_budgets
            WHERE month = %s{budget_filter}""",  # nosec B608 — budget_filter is hardcoded
        budget_params,
    ).fetchall()
    budgets: dict[str, dict] = {}
    for b in budget_rows:
        aid = b[0] if isinstance(b, (tuple, list)) else b["agent_id"]
        budgets[aid] = {
            "budget_usd": float(b[1] if isinstance(b, (tuple, list)) else b["budget_usd"]),
            "spent_usd": float(b[2] if isinstance(b, (tuple, list)) else b["spent_usd"]),
            "warning_threshold": float(b[3] if isinstance(b, (tuple, list)) else b["warning_threshold"]),
        }

    projections: list[dict] = []
    alerts_created: list[str] = []
    total_spent = 0.0
    total_projected = 0.0

    for row in rows:
        aid = row[0] if isinstance(row, (tuple, list)) else row["agent_id"]
        spent = float(row[1] if isinstance(row, (tuple, list)) else row["spent"])
        calls = int(row[2] if isinstance(row, (tuple, list)) else row["calls"])
        daily_rate = spent / days_elapsed
        projected = daily_rate * days_in_month

        total_spent += spent
        total_projected += projected

        proj = {
            "agent_id": aid,
            "spent_usd": round(spent, 4),
            "daily_rate_usd": round(daily_rate, 4),
            "projected_eom_usd": round(projected, 4),
            "days_elapsed": days_elapsed,
            "days_in_month": days_in_month,
            "calls": calls,
        }

        budget_info = budgets.get(aid)
        if budget_info:
            budget = budget_info["budget_usd"]
            proj["budget_usd"] = budget
            proj["utilization_pct"] = round((spent / budget) * 100, 1) if budget > 0 else 0.0
            proj["projected_utilization_pct"] = round((projected / budget) * 100, 1) if budget > 0 else 0.0

            if budget > 0 and projected > budget:
                severity = "critical" if projected > budget * 1.5 else "warning"
                msg = (
                    f"Agent '{aid}' projected to spend ${projected:.2f} "
                    f"by end of {month_str}, exceeding budget ${budget:.2f} "
                    f"({proj['projected_utilization_pct']}% utilization)"
                )
                alert_id = _create_alert(
                    conn,
                    "projection",
                    severity,
                    msg,
                    agent_id=aid,
                    details=proj,
                )
                alerts_created.append(alert_id)
                proj["over_budget"] = True
            else:
                proj["over_budget"] = False

        projections.append(proj)

    conn.commit()
    conn.close()

    return {
        "status": "ok",
        "month": month_str,
        "total_spent_usd": round(total_spent, 4),
        "total_projected_usd": round(total_projected, 4),
        "projections": projections,
        "alerts_created": alerts_created,
    }


def recommend_optimizations() -> list[dict]:
    """Analyze per-function cost/quality and generate recommendations.

    Checks:
    1. Scanner functions using cloud models -> recommend local
    2. Functions with high repeated-prompt ratio -> recommend caching
    3. Functions with avg tokens > 2x median -> flag for prompt optimization
    """
    conn = _get_conn()
    init_tables(conn)
    config = _load_config()
    model_id_map = _model_id_to_name(config)
    now = datetime.now(timezone.utc)
    cutoff_30d = (now - timedelta(days=30)).isoformat()
    cutoff_7d = (now - timedelta(days=7)).isoformat()

    now_iso = _now_iso()
    recommendations: list[dict] = []

    # ---------------------------------------------------------------
    # 1. Scanner functions using cloud models
    # ---------------------------------------------------------------
    rows = conn.execute(
        """SELECT model_id,
                  COUNT(*) as calls,
                  SUM(cost_estimate_usd) as total_cost
           FROM agent_token_usage
           WHERE created_at >= %s
           GROUP BY model_id""",
        (cutoff_30d,),
    ).fetchall()

    # Also check per-function model usage if we can infer function from task_id
    # For now, check if any cloud model is used that could be replaced
    for row in rows:
        model_raw = row[0] if isinstance(row, (tuple, list)) else row["model_id"]
        total_cost = float(row[1] if isinstance(row, (tuple, list)) else row["total_cost"])
        model_name = model_id_map.get(model_raw, model_raw)
        model_info = config.get("models", {}).get(model_name, {})
        provider = model_info.get("provider", "")

        # If it's a cloud model with non-zero cost, suggest local alternative
        if provider not in ("ollama", "mistral_vllm", "") and total_cost > 0.01:
            rec_id = _gen_id("crec-")
            rec = {
                "id": rec_id,
                "recommendation_type": "switch_to_local",
                "function_name": None,
                "current_model": model_name,
                "recommended_model": "qwen3-local",
                "estimated_savings_usd": round(total_cost * 0.8, 4),
                "confidence": 0.6,
                "status": "pending",
            }
            conn.execute(
                """INSERT OR IGNORE INTO llm_cost_recommendations
                   (id, recommendation_type, function_name, current_model,
                    recommended_model, estimated_savings_usd, confidence,
                    status, created_at, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    rec_id,
                    rec["recommendation_type"],
                    rec["function_name"],
                    rec["current_model"],
                    rec["recommended_model"],
                    rec["estimated_savings_usd"],
                    rec["confidence"],
                    rec["status"],
                    now_iso,
                    now_iso,
                ),
            )
            recommendations.append(rec)

    # ---------------------------------------------------------------
    # 2. Prompt-length outliers (avg tokens > 2x median across agents)
    # ---------------------------------------------------------------
    token_rows = conn.execute(
        """SELECT agent_id,
                  AVG(input_tokens + output_tokens) as avg_tokens,
                  COUNT(*) as calls
           FROM agent_token_usage
           WHERE created_at >= %s
           GROUP BY agent_id
           HAVING COUNT(*) >= 5""",
        (cutoff_7d,),
    ).fetchall()

    if token_rows:
        avg_values = [float(r[1] if isinstance(r, (tuple, list)) else r["avg_tokens"]) for r in token_rows]
        if avg_values:
            median_tokens = statistics.median(avg_values)
            for row in token_rows:
                agent = row[0] if isinstance(row, (tuple, list)) else row["agent_id"]
                avg_tok = float(row[1] if isinstance(row, (tuple, list)) else row["avg_tokens"])
                if median_tokens > 0 and avg_tok > 2 * median_tokens:
                    rec_id = _gen_id("crec-")
                    rec = {
                        "id": rec_id,
                        "recommendation_type": "reduce_tokens",
                        "function_name": agent,
                        "current_model": None,
                        "recommended_model": None,
                        "estimated_savings_usd": 0.0,
                        "confidence": 0.7,
                        "status": "pending",
                    }
                    conn.execute(
                        """INSERT OR IGNORE INTO llm_cost_recommendations
                           (id, recommendation_type, function_name, current_model,
                            recommended_model, estimated_savings_usd, confidence,
                            status, created_at, updated_at)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                        (
                            rec_id,
                            rec["recommendation_type"],
                            rec["function_name"],
                            rec["current_model"],
                            rec["recommended_model"],
                            rec["estimated_savings_usd"],
                            rec["confidence"],
                            rec["status"],
                            now_iso,
                            now_iso,
                        ),
                    )
                    recommendations.append(rec)

    # ---------------------------------------------------------------
    # 3. Repeated identical prompts (cache opportunity)
    #    Approximated by looking for same agent + model + similar token counts
    # ---------------------------------------------------------------
    repeat_rows = conn.execute(
        """SELECT agent_id, model_id, input_tokens, COUNT(*) as cnt
           FROM agent_token_usage
           WHERE created_at >= %s
           GROUP BY agent_id, model_id, input_tokens
           HAVING COUNT(*) >= 5""",
        (cutoff_7d,),
    ).fetchall()

    if repeat_rows:
        # Group by agent
        agent_repeats: dict[str, int] = {}
        agent_totals: dict[str, int] = {}
        for row in repeat_rows:
            agent = row[0] if isinstance(row, (tuple, list)) else row["agent_id"]
            cnt = int(row[3] if isinstance(row, (tuple, list)) else row["cnt"])
            agent_repeats[agent] = agent_repeats.get(agent, 0) + cnt

        total_rows = conn.execute(
            """SELECT agent_id, COUNT(*) as total
               FROM agent_token_usage
               WHERE created_at >= %s
               GROUP BY agent_id""",
            (cutoff_7d,),
        ).fetchall()
        for row in total_rows:
            agent = row[0] if isinstance(row, (tuple, list)) else row["agent_id"]
            agent_totals[agent] = int(row[1] if isinstance(row, (tuple, list)) else row["total"])

        for agent, repeat_count in agent_repeats.items():
            total = agent_totals.get(agent, 1)
            ratio = repeat_count / total
            if ratio >= 0.8:
                rec_id = _gen_id("crec-")
                rec = {
                    "id": rec_id,
                    "recommendation_type": "cache_responses",
                    "function_name": agent,
                    "current_model": None,
                    "recommended_model": None,
                    "estimated_savings_usd": 0.0,
                    "confidence": round(ratio, 2),
                    "status": "pending",
                }
                conn.execute(
                    """INSERT OR IGNORE INTO llm_cost_recommendations
                       (id, recommendation_type, function_name, current_model,
                        recommended_model, estimated_savings_usd, confidence,
                        status, created_at, updated_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (
                        rec_id,
                        rec["recommendation_type"],
                        rec["function_name"],
                        rec["current_model"],
                        rec["recommended_model"],
                        rec["estimated_savings_usd"],
                        rec["confidence"],
                        rec["status"],
                        now_iso,
                        now_iso,
                    ),
                )
                recommendations.append(rec)

    # ---------------------------------------------------------------
    # 4. High-variance functions → recommend CoT or CoD
    #    Query llm_chain_telemetry for functions with high cost variance.
    #    If NO chain telemetry yet, recommend enabling CoT for costly functions.
    # ---------------------------------------------------------------
    try:
        # Check if llm_chain_telemetry table exists
        has_table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='llm_chain_telemetry'"
        ).fetchone()

        if has_table:
            variance_rows = conn.execute(
                """SELECT function,
                          AVG(cost_usd) as avg_cost,
                          COUNT(*) as calls
                   FROM llm_chain_telemetry
                   WHERE created_at >= %s
                   GROUP BY function
                   HAVING COUNT(*) >= 3""",
                (cutoff_30d,),
            ).fetchall()

            # For functions with non-trivial cost but no CoT/CoD yet, suggest enabling
            # For functions already using CoT with high debate potential, suggest CoD
            for row in variance_rows:
                fn = row[0] if isinstance(row, (tuple, list)) else row["function"]
                avg_cost = float(row[1] if isinstance(row, (tuple, list)) else row["avg_cost"])
                calls = int(row[2] if isinstance(row, (tuple, list)) else row["calls"])

                # Detect functions with avg cost > $0.01/call that aren't using CoD yet
                if avg_cost > 0.01 and calls >= 5:
                    mode_row = conn.execute(
                        "SELECT DISTINCT chain_mode FROM llm_chain_telemetry WHERE function=%s",
                        (fn,),
                    ).fetchall()
                    modes = {r[0] for r in mode_row}
                    if "cod" not in modes:
                        rec_id = _gen_id("crec-")
                        rec = {
                            "id": rec_id,
                            "recommendation_type": "enable_cod",
                            "function_name": fn,
                            "current_model": None,
                            "recommended_model": None,
                            "estimated_savings_usd": 0.0,
                            "confidence": 0.65,
                            "status": "pending",
                        }
                        conn.execute(
                            """INSERT OR IGNORE INTO llm_cost_recommendations
                               (id, recommendation_type, function_name, current_model,
                                recommended_model, estimated_savings_usd, confidence,
                                status, created_at, updated_at)
                               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                            (rec_id, rec["recommendation_type"], rec["function_name"],
                             rec["current_model"], rec["recommended_model"],
                             rec["estimated_savings_usd"], rec["confidence"],
                             rec["status"], now_iso, now_iso),
                        )
                        recommendations.append(rec)

        # Recommend CoT for high-cost cloud functions with no chain usage at all
        high_cost_rows = conn.execute(
            """SELECT function_name,
                      SUM(cost_estimate_usd) as total_cost
               FROM agent_token_usage
               WHERE created_at >= %s
               GROUP BY function_name
               HAVING SUM(cost_estimate_usd) > 0.10 AND function_name IS NOT NULL""",
            (cutoff_30d,),
        ).fetchall() if conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='agent_token_usage'"
        ).fetchone() else []

        for row in high_cost_rows:
            fn = row[0] if isinstance(row, (tuple, list)) else row["function_name"]
            if not fn:
                continue
            # Only recommend if no CoT telemetry exists for this function
            if has_table:
                existing = conn.execute(
                    "SELECT 1 FROM llm_chain_telemetry WHERE function=%s LIMIT 1", (fn,)
                ).fetchone()
                if existing:
                    continue
            rec_id = _gen_id("crec-")
            rec = {
                "id": rec_id,
                "recommendation_type": "enable_cot",
                "function_name": fn,
                "current_model": None,
                "recommended_model": None,
                "estimated_savings_usd": 0.0,
                "confidence": 0.70,
                "status": "pending",
            }
            conn.execute(
                """INSERT OR IGNORE INTO llm_cost_recommendations
                   (id, recommendation_type, function_name, current_model,
                    recommended_model, estimated_savings_usd, confidence,
                    status, created_at, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (rec_id, rec["recommendation_type"], rec["function_name"],
                 rec["current_model"], rec["recommended_model"],
                 rec["estimated_savings_usd"], rec["confidence"],
                 rec["status"], now_iso, now_iso),
            )
            recommendations.append(rec)
    except Exception as exc:
        logger.debug("CoT/CoD recommendation detection skipped: %s", exc)

    conn.commit()
    conn.close()
    return recommendations


def compare_edge_vs_cloud(function_name: str) -> dict:
    """Cost comparison between Ollama local and cloud API for a function.

    Returns per-1k-call cost estimates for local vs cloud models in
    the function's routing chain.
    """
    config = _load_config()
    pricing = _model_pricing(config)
    routing = config.get("routing", {})
    chain = routing.get(function_name, routing.get("default", {})).get("chain", [])

    conn = _get_conn()
    init_tables(conn)
    cutoff_30d = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()

    # Get average token counts for this function's agents
    avg_row = conn.execute(
        """SELECT AVG(input_tokens) as avg_in,
                  AVG(output_tokens) as avg_out,
                  COUNT(*) as calls
           FROM agent_token_usage
           WHERE created_at >= %s""",
        (cutoff_30d,),
    ).fetchone()

    avg_input = float(avg_row[0] or 500) if avg_row else 500.0
    avg_output = float(avg_row[1] or 200) if avg_row else 200.0
    conn.close()

    comparisons: list[dict] = []
    for model_name in chain:
        p = pricing.get(model_name, {"input_per_1k": 0.0, "output_per_1k": 0.0})
        is_local = _is_local_model(config, model_name)
        cost_per_call = (avg_input / 1000) * p["input_per_1k"] + (avg_output / 1000) * p["output_per_1k"]
        comparisons.append(
            {
                "model": model_name,
                "is_local": is_local,
                "input_per_1k_tokens": p["input_per_1k"],
                "output_per_1k_tokens": p["output_per_1k"],
                "estimated_cost_per_call": round(cost_per_call, 6),
                "estimated_cost_per_1k_calls": round(cost_per_call * 1000, 4),
            }
        )

    # Separate local vs cloud
    local_models = [c for c in comparisons if c["is_local"]]
    cloud_models = [c for c in comparisons if not c["is_local"]]

    cheapest_cloud = min(cloud_models, key=lambda x: x["estimated_cost_per_call"]) if cloud_models else None
    cheapest_local = min(local_models, key=lambda x: x["estimated_cost_per_call"]) if local_models else None

    savings_per_1k = 0.0
    if cheapest_cloud and cheapest_local:
        savings_per_1k = round(
            cheapest_cloud["estimated_cost_per_1k_calls"] - cheapest_local["estimated_cost_per_1k_calls"],
            4,
        )

    return {
        "status": "ok",
        "function_name": function_name,
        "avg_input_tokens": round(avg_input, 0),
        "avg_output_tokens": round(avg_output, 0),
        "chain": chain,
        "comparisons": comparisons,
        "cheapest_cloud": cheapest_cloud,
        "cheapest_local": cheapest_local,
        "savings_per_1k_calls_usd": savings_per_1k,
    }


def get_cost_dashboard() -> dict:
    """Summary dashboard: total spend, by agent, by model, trends."""
    conn = _get_conn()
    init_tables(conn)
    now = datetime.now(timezone.utc)
    month_str, month_start_iso, next_month_iso = _month_boundaries(now)

    # Total spend (all time)
    total_row = conn.execute("SELECT SUM(cost_estimate_usd), COUNT(*) FROM agent_token_usage").fetchone()
    total_spend = float(total_row[0] or 0) if total_row else 0.0
    total_calls = int(total_row[1] or 0) if total_row else 0

    # This month
    month_row = conn.execute(
        "SELECT SUM(cost_estimate_usd), COUNT(*) FROM agent_token_usage WHERE created_at >= %s AND created_at < %s",
        (month_start_iso, next_month_iso),
    ).fetchone()
    month_spend = float(month_row[0] or 0) if month_row else 0.0
    month_calls = int(month_row[1] or 0) if month_row else 0

    # By agent (this month)
    by_agent = conn.execute(
        """SELECT agent_id,
                  SUM(cost_estimate_usd) as cost,
                  SUM(input_tokens) as input_tok,
                  SUM(output_tokens) as output_tok,
                  SUM(thinking_tokens) as think_tok,
                  COUNT(*) as calls
           FROM agent_token_usage
           WHERE created_at >= %s AND created_at < %s
           GROUP BY agent_id
           ORDER BY cost DESC""",
        (month_start_iso, next_month_iso),
    ).fetchall()

    agents = []
    for row in by_agent:
        agents.append(
            {
                "agent_id": row[0] if isinstance(row, (tuple, list)) else row["agent_id"],
                "cost_usd": round(float(row[1] if isinstance(row, (tuple, list)) else row["cost"]), 4),
                "input_tokens": int(row[2] if isinstance(row, (tuple, list)) else row["input_tok"]),
                "output_tokens": int(row[3] if isinstance(row, (tuple, list)) else row["output_tok"]),
                "thinking_tokens": int(row[4] if isinstance(row, (tuple, list)) else row["think_tok"]),
                "calls": int(row[5] if isinstance(row, (tuple, list)) else row["calls"]),
            }
        )

    # By model (this month)
    by_model = conn.execute(
        """SELECT model_id,
                  SUM(cost_estimate_usd) as cost,
                  SUM(input_tokens + output_tokens) as total_tokens,
                  COUNT(*) as calls
           FROM agent_token_usage
           WHERE created_at >= %s AND created_at < %s
           GROUP BY model_id
           ORDER BY cost DESC""",
        (month_start_iso, next_month_iso),
    ).fetchall()

    models = []
    for row in by_model:
        models.append(
            {
                "model_id": row[0] if isinstance(row, (tuple, list)) else row["model_id"],
                "cost_usd": round(float(row[1] if isinstance(row, (tuple, list)) else row["cost"]), 4),
                "total_tokens": int(row[2] if isinstance(row, (tuple, list)) else row["total_tokens"]),
                "calls": int(row[3] if isinstance(row, (tuple, list)) else row["calls"]),
            }
        )

    # Daily trend (last 14 days)
    trend_rows = conn.execute(
        """SELECT DATE(created_at) as day,
                  SUM(cost_estimate_usd) as cost,
                  COUNT(*) as calls
           FROM agent_token_usage
           WHERE created_at >= %s
           GROUP BY DATE(created_at)
           ORDER BY day""",
        ((now - timedelta(days=14)).isoformat(),),
    ).fetchall()

    trends = []
    for row in trend_rows:
        trends.append(
            {
                "date": row[0] if isinstance(row, (tuple, list)) else row["day"],
                "cost_usd": round(float(row[1] if isinstance(row, (tuple, list)) else row["cost"]), 4),
                "calls": int(row[2] if isinstance(row, (tuple, list)) else row["calls"]),
            }
        )

    # Budget status
    budget_rows = conn.execute(
        "SELECT agent_id, budget_usd, spent_usd FROM agent_token_budgets WHERE month = %s",
        (month_str,),
    ).fetchall()

    budget_status = []
    for row in budget_rows:
        budget = float(row[1] if isinstance(row, (tuple, list)) else row["budget_usd"])
        spent = float(row[2] if isinstance(row, (tuple, list)) else row["spent_usd"])
        budget_status.append(
            {
                "agent_id": row[0] if isinstance(row, (tuple, list)) else row["agent_id"],
                "budget_usd": budget,
                "spent_usd": spent,
                "utilization_pct": round((spent / budget) * 100, 1) if budget > 0 else 0.0,
            }
        )

    # Unacknowledged alert count
    alert_count = conn.execute("SELECT COUNT(*) FROM llm_cost_alerts WHERE acknowledged = 0").fetchone()

    conn.close()

    return {
        "status": "ok",
        "total_spend_usd": round(total_spend, 4),
        "total_calls": total_calls,
        "month": month_str,
        "month_spend_usd": round(month_spend, 4),
        "month_calls": month_calls,
        "by_agent": agents,
        "by_model": models,
        "daily_trend": trends,
        "budget_status": budget_status,
        "unacknowledged_alerts": int(alert_count[0]) if alert_count else 0,
    }


def get_cost_alerts() -> dict:
    """Return unacknowledged alerts."""
    conn = _get_conn()
    init_tables(conn)
    rows = conn.execute(
        """SELECT id, alert_type, agent_id, model_id, function_name,
                  severity, message, details_json, created_at
           FROM llm_cost_alerts
           WHERE acknowledged = 0
           ORDER BY
               CASE severity
                   WHEN 'critical' THEN 0
                   WHEN 'warning' THEN 1
                   WHEN 'info' THEN 2
               END,
               created_at DESC"""
    ).fetchall()
    conn.close()

    alerts = []
    for row in rows:
        alerts.append(
            {
                "id": row[0] if isinstance(row, (tuple, list)) else row["id"],
                "alert_type": row[1] if isinstance(row, (tuple, list)) else row["alert_type"],
                "agent_id": row[2] if isinstance(row, (tuple, list)) else row["agent_id"],
                "model_id": row[3] if isinstance(row, (tuple, list)) else row["model_id"],
                "function_name": row[4] if isinstance(row, (tuple, list)) else row["function_name"],
                "severity": row[5] if isinstance(row, (tuple, list)) else row["severity"],
                "message": row[6] if isinstance(row, (tuple, list)) else row["message"],
                "details": json.loads(row[7])
                if (row[7] if isinstance(row, (tuple, list)) else row["details_json"])
                else None,
                "created_at": row[8] if isinstance(row, (tuple, list)) else row["created_at"],
            }
        )

    return {
        "status": "ok",
        "unacknowledged_count": len(alerts),
        "alerts": alerts,
    }


def acknowledge_alert(alert_id: str, acknowledged_by: str = "system") -> dict:
    """Mark an alert as acknowledged."""
    conn = _get_conn()
    init_tables(conn)
    now_iso = _now_iso()
    cursor = conn.execute(
        """UPDATE llm_cost_alerts
           SET acknowledged = 1, acknowledged_by = %s, acknowledged_at = %s
           WHERE id = %s AND acknowledged = 0""",
        (acknowledged_by, now_iso, alert_id),
    )
    affected = cursor.rowcount
    conn.commit()
    conn.close()

    if affected == 0:
        return {
            "status": "error",
            "message": f"Alert '{alert_id}' not found or already acknowledged",
        }
    return {
        "status": "ok",
        "alert_id": alert_id,
        "acknowledged_by": acknowledged_by,
        "acknowledged_at": now_iso,
    }


def run_gate() -> dict:
    """Gate check: exit 1 if any critical unacknowledged alerts exist."""
    conn = _get_conn()
    init_tables(conn)
    critical_row = conn.execute(
        "SELECT COUNT(*) FROM llm_cost_alerts WHERE acknowledged = 0 AND severity = 'critical'"
    ).fetchone()
    critical_count = int(critical_row[0]) if critical_row else 0

    warning_row = conn.execute(
        "SELECT COUNT(*) FROM llm_cost_alerts WHERE acknowledged = 0 AND severity = 'warning'"
    ).fetchone()
    warning_count = int(warning_row[0]) if warning_row else 0
    conn.close()

    passed = critical_count == 0
    return {
        "status": "pass" if passed else "fail",
        "gate": "cost_intelligence",
        "critical_alerts": critical_count,
        "warning_alerts": warning_count,
        "message": (
            "No critical cost alerts" if passed else f"{critical_count} critical cost alert(s) require acknowledgement"
        ),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="LLM Cost Intelligence — anomaly detection, projections, recommendations"
    )
    parser.add_argument("--dashboard", action="store_true", help="Cost dashboard summary")
    parser.add_argument("--anomalies", action="store_true", help="Detect cost anomalies")
    parser.add_argument("--project", action="store_true", help="Project monthly spend")
    parser.add_argument("--recommend", action="store_true", help="Generate optimization recommendations")
    parser.add_argument("--edge-vs-cloud", action="store_true", help="Compare edge vs cloud cost")
    parser.add_argument("--alerts", action="store_true", help="List unacknowledged alerts")
    parser.add_argument("--acknowledge", type=str, metavar="ALERT_ID", help="Acknowledge an alert")
    parser.add_argument("--gate", action="store_true", help="Gate check (exit 1 on critical alerts)")
    parser.add_argument("--agent", type=str, help="Filter by agent ID (for --project)")
    parser.add_argument("--function", type=str, help="Function name (for --edge-vs-cloud)")
    parser.add_argument("--lookback", type=int, default=24, help="Lookback hours (for --anomalies)")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    parser.add_argument("--init-tables", action="store_true", help="Initialize tables only")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    result: dict | list | None = None

    if args.init_tables:
        init_tables()
        result = {"status": "ok", "message": "Cost intelligence tables initialized"}

    elif args.dashboard:
        result = get_cost_dashboard()

    elif args.anomalies:
        result = detect_cost_anomalies(lookback_hours=args.lookback)

    elif args.project:
        result = project_monthly_spend(agent_id=args.agent)

    elif args.recommend:
        recs = recommend_optimizations()
        result = {"status": "ok", "recommendations": recs, "count": len(recs)}

    elif args.edge_vs_cloud:
        fn = args.function
        if not fn:
            print("ERROR: --function required with --edge-vs-cloud", file=sys.stderr)
            sys.exit(1)
        result = compare_edge_vs_cloud(fn)

    elif args.alerts:
        result = get_cost_alerts()

    elif args.acknowledge:
        result = acknowledge_alert(args.acknowledge)

    elif args.gate:
        result = run_gate()
        if args.json_output:
            print(json.dumps(result, indent=2))
        else:
            status = result.get("status", "fail")
            print(f"Gate: cost_intelligence — {status.upper()}")
            print(f"  Critical alerts: {result.get('critical_alerts', 0)}")
            print(f"  Warning alerts: {result.get('warning_alerts', 0)}")
            print(f"  {result.get('message', '')}")
        sys.exit(0 if result.get("status") == "pass" else 1)

    else:
        parser.print_help()
        sys.exit(0)

    if result is not None:
        if args.json_output:
            print(json.dumps(result, indent=2, default=str))
        else:
            # Human-readable output
            if isinstance(result, dict):
                for key, val in result.items():
                    if isinstance(val, list):
                        print(f"\n{key}:")
                        for item in val:
                            print(f"  - {item}")
                    else:
                        print(f"{key}: {val}")
            else:
                print(result)


if __name__ == "__main__":
    main()
