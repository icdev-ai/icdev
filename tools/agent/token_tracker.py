
from tools.logging.icdev_logger import get_logger
# [TEMPLATE: CUI // SP-CTI]
"""Token usage, cost tracking, and budget enforcement per agent/project/task.

Logs LLM token consumption to data/icdev.db, provides aggregated summaries
and cost estimates, and enforces per-agent monthly budget hard-stops.

Budget enforcement (inspired by Paperclip AI's per-agent budget model):
    - Per-agent monthly USD caps configured in args/llm_config.yaml
    - Warning at configurable threshold (default 80%)
    - Hard-stop blocks invocations when budget exhausted
    - check_budget() returns allow/warn/block decision before each invoke()
"""

import argparse
import json
import sqlite3
from tools.db.storage import get_connection
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

logger = get_logger("icdev.agent.token_tracker")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

# ---------------------------------------------------------------------------
# Schema bootstrap — ensures the table exists even on a fresh DB
# ---------------------------------------------------------------------------
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS agent_token_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    model_id TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    thinking_tokens INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    task_id TEXT,
    cost_estimate_usd REAL NOT NULL DEFAULT 0.0,
    user_id TEXT DEFAULT NULL,
    api_key_source TEXT DEFAULT 'config',
    created_at TEXT NOT NULL
);
"""

# nav-sec-09: attribution columns added to older tables by DASHBOARD_AUTH_ALTER_SQL
# (init_icdev_db.py). Kept here so a table bootstrapped by _ensure_table alone —
# without ever running init/migrations — still carries them.
_ATTRIBUTION_COLUMNS = (
    ("user_id", "TEXT DEFAULT NULL"),
    ("api_key_source", "TEXT DEFAULT 'config'"),
)

CREATE_BUDGETS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS agent_token_budgets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT NOT NULL,
    month TEXT NOT NULL,
    budget_usd REAL NOT NULL DEFAULT 0.0,
    spent_usd REAL NOT NULL DEFAULT 0.0,
    warning_threshold REAL NOT NULL DEFAULT 0.8,
    hard_stop INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(agent_id, month)
);
"""


def _ensure_table(conn: sqlite3.Connection) -> None:
    """Create agent_token_usage and agent_token_budgets tables if they do not exist."""
    conn.execute(CREATE_TABLE_SQL)
    conn.execute(CREATE_BUDGETS_TABLE_SQL)
    # nav-sec-09: backfill attribution columns on a pre-existing table that was
    # created before these columns were introduced and never migrated. Guarded by
    # column_exists so we never issue a duplicate-column ALTER (which would abort a
    # PostgreSQL transaction). Best-effort — the read side tolerates their absence.
    try:
        from tools.db.storage import column_exists

        for col, ddl in _ATTRIBUTION_COLUMNS:
            if not column_exists(conn, "agent_token_usage", col):
                conn.execute(f"ALTER TABLE agent_token_usage ADD COLUMN {col} {ddl}")  # nosec B608 — fixed literals
    except Exception:
        pass
    conn.commit()


def _resolve_request_user_id():
    """Resolve the current dashboard user id from Flask's request context.

    Returns the authenticated user's id when ``log_usage`` runs while serving an
    HTTP request (i.e. a dashboard-originated LLM call), otherwise ``None``.
    Userless contexts — daemons, reflexes, cron, CLI — legitimately return
    ``None`` so those rows stay unattributed rather than fabricating a user.
    """
    try:
        from flask import g, has_request_context

        if not has_request_context():
            return None
        user = getattr(g, "current_user", None)
        if not user:
            return None
        if isinstance(user, dict):
            return user.get("id")
        try:
            return user["id"]
        except (KeyError, IndexError, TypeError):
            return getattr(user, "id", None)
    except Exception:
        return None


def _connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Open a connection and guarantee the table is present."""
    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = get_connection(db_path=str(path))
    _ensure_table(conn)
    return conn


# ---------------------------------------------------------------------------
# Model pricing loader
# ---------------------------------------------------------------------------
def _load_model_pricing() -> Dict:
    """Load pricing from llm_config.yaml (multi-provider) or bedrock_models.yaml."""
    # Try LLM router first (covers all providers: Bedrock, OpenAI, Ollama, etc.)
    try:
        from tools.llm.router import LLMRouter

        router = LLMRouter()
        all_pricing = router.get_all_model_pricing()
        if all_pricing:
            result = {}
            for mid, pricing in all_pricing.items():
                result[mid] = {
                    "model_id": mid,
                    "cost_per_1k_input": pricing.get("input_per_1k", 0.0),
                    "cost_per_1k_output": pricing.get("output_per_1k", 0.0),
                }
            return result
    except Exception:
        pass

    # Fallback to bedrock_models.yaml
    yaml_path = BASE_DIR / "args" / "bedrock_models.yaml"
    if not yaml_path.exists():
        return {}
    try:
        import yaml  # noqa: E401 — optional dependency

        with open(yaml_path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        return data.get("models", {})
    except ImportError:
        return {}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def log_usage(
    agent_id: str,
    project_id: str,
    model_id: str,
    input_tokens: int,
    output_tokens: int,
    thinking_tokens: int = 0,
    duration_ms: int = 0,
    task_id: str = None,
    cost_estimate_usd: float = 0.0,
    user_id: str = None,
    api_key_source: str = None,
    db_path: Path = None,
) -> int:
    """Insert token usage record into agent_token_usage. Returns row ID.

    ``user_id`` attributes the row to a dashboard user so the per-user usage API
    (tools/dashboard/api/usage.py) can scope it. When the caller does not pass one
    explicitly (the common case — bedrock_client / router track transparently), it
    is resolved from Flask's ``g.current_user`` while a request context is active.
    Userless contexts (daemons, reflexes, cron) resolve to ``None`` and stay
    legitimately unattributed — attribution is never fabricated (nav-sec-09).
    """
    # Only auto-resolve when the caller did not supply an explicit user.
    if user_id is None:
        user_id = _resolve_request_user_id()
    # Match the schema default ('config') rather than writing NULL when unknown.
    if api_key_source is None:
        api_key_source = "config"
    conn = _connect(db_path)
    try:
        cursor = conn.execute(
            """
            INSERT INTO agent_token_usage
                (agent_id, project_id, model_id, input_tokens, output_tokens,
                 thinking_tokens, duration_ms, task_id, cost_estimate_usd,
                 user_id, api_key_source, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                agent_id,
                project_id,
                model_id,
                input_tokens,
                output_tokens,
                thinking_tokens,
                duration_ms,
                task_id,
                cost_estimate_usd,
                user_id,
                api_key_source,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def get_usage_summary(
    project_id: str = None,
    agent_id: str = None,
    since: str = None,
    db_path: Path = None,
) -> Dict:
    """Get aggregated usage: total_input, total_output, total_thinking, total_cost, count.

    Filters by project_id, agent_id, and/or since (ISO timestamp).
    """
    conn = _connect(db_path)
    try:
        query = """
            SELECT
                COALESCE(SUM(input_tokens), 0)       AS total_input,
                COALESCE(SUM(output_tokens), 0)      AS total_output,
                COALESCE(SUM(thinking_tokens), 0)     AS total_thinking,
                COALESCE(SUM(cost_estimate_usd), 0.0) AS total_cost,
                COUNT(*)                              AS count
            FROM agent_token_usage
            WHERE 1=1
        """
        params = []

        if project_id:
            query += " AND project_id = ?"
            params.append(project_id)
        if agent_id:
            query += " AND agent_id = ?"
            params.append(agent_id)
        if since:
            query += " AND timestamp >= ?"
            params.append(since)

        row = conn.execute(query, params).fetchone()
        return {
            "total_input": row["total_input"],
            "total_output": row["total_output"],
            "total_thinking": row["total_thinking"],
            "total_cost": round(row["total_cost"], 6),
            "count": row["count"],
        }
    finally:
        conn.close()


def get_cost_estimate(project_id: str, db_path: Path = None) -> Dict:
    """Get cost breakdown by model_id for a project."""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT
                model_id,
                COALESCE(SUM(input_tokens), 0)       AS total_input,
                COALESCE(SUM(output_tokens), 0)      AS total_output,
                COALESCE(SUM(thinking_tokens), 0)     AS total_thinking,
                COALESCE(SUM(cost_estimate_usd), 0.0) AS total_cost,
                COUNT(*)                              AS count
            FROM agent_token_usage
            WHERE project_id = %s
            GROUP BY model_id
            ORDER BY total_cost DESC
            """,
            (project_id,),
        ).fetchall()

        breakdown = {}
        for r in rows:
            breakdown[r["model_id"]] = {
                "total_input": r["total_input"],
                "total_output": r["total_output"],
                "total_thinking": r["total_thinking"],
                "total_cost": round(r["total_cost"], 6),
                "count": r["count"],
            }

        grand_total = sum(m["total_cost"] for m in breakdown.values())
        return {
            "project_id": project_id,
            "by_model": breakdown,
            "grand_total_usd": round(grand_total, 6),
        }
    finally:
        conn.close()


def estimate_cost(model_id: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate cost in USD based on model pricing from bedrock_models.yaml."""
    models = _load_model_pricing()

    # Try to match by full model_id or by short key
    pricing = None
    for _key, cfg in models.items():
        if cfg.get("model_id") == model_id or _key == model_id:
            pricing = cfg
            break

    if pricing is None:
        return 0.0

    cost_in = (input_tokens / 1000.0) * pricing.get("cost_per_1k_input", 0.0)
    cost_out = (output_tokens / 1000.0) * pricing.get("cost_per_1k_output", 0.0)
    return round(cost_in + cost_out, 6)


# ---------------------------------------------------------------------------
# Budget Enforcement (Paperclip-inspired per-agent hard-stops)
# ---------------------------------------------------------------------------

# Default budgets loaded from args/llm_config.yaml token_budgets section
_budget_config_cache: Optional[Dict] = None


def _load_budget_config() -> Dict:
    """Load token budget config from llm_config.yaml."""
    global _budget_config_cache
    if _budget_config_cache is not None:
        return _budget_config_cache

    config_path = BASE_DIR / "args" / "llm_config.yaml"
    if not config_path.exists():
        _budget_config_cache = {}
        return _budget_config_cache

    try:
        import yaml

        with open(config_path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        _budget_config_cache = data.get("token_budgets", {})
        return _budget_config_cache
    except (ImportError, Exception):
        _budget_config_cache = {}
        return _budget_config_cache


def _current_month() -> str:
    """Return current month as YYYY-MM string."""
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _month_range(month: str) -> tuple:
    """Return (start, end) ISO timestamps for a YYYY-MM month.

    Start is first day of the month, end is first day of next month.
    Works with both PostgreSQL and SQLite date comparisons.
    """
    year, mon = int(month[:4]), int(month[5:7])
    start = f"{year:04d}-{mon:02d}-01T00:00:00"
    # Roll to next month
    if mon == 12:
        end = f"{year + 1:04d}-01-01T00:00:00"
    else:
        end = f"{year:04d}-{mon + 1:02d}-01T00:00:00"
    return start, end


def _get_agent_budget(agent_id: str) -> Dict:
    """Get budget config for an agent, falling back to defaults."""
    cfg = _load_budget_config()
    if not cfg.get("enabled", False):
        return {"enabled": False}

    default_usd = cfg.get("default_monthly_usd", 0.0)
    warning_pct = cfg.get("warning_threshold", 0.8)
    hard_stop = cfg.get("hard_stop", True)

    # Per-agent overrides
    per_agent = cfg.get("per_agent", {})
    agent_cfg = per_agent.get(agent_id, {})

    return {
        "enabled": True,
        "budget_usd": agent_cfg.get("monthly_usd", default_usd),
        "warning_threshold": agent_cfg.get("warning_threshold", warning_pct),
        "hard_stop": agent_cfg.get("hard_stop", hard_stop),
    }


def _get_monthly_spend(agent_id: str, month: str = None, db_path: Path = None) -> float:
    """Get total USD spent by agent in the given month."""
    month = month or _current_month()
    start, end = _month_range(month)
    conn = _connect(db_path)
    try:
        row = conn.execute(
            """SELECT COALESCE(SUM(cost_estimate_usd), 0.0) AS total
               FROM agent_token_usage
               WHERE agent_id = %s
                 AND created_at >= %s
                 AND created_at < %s""",
            (agent_id, start, end),
        ).fetchone()
        return round(row["total"], 6) if row else 0.0
    finally:
        conn.close()


def check_budget(agent_id: str, db_path: Path = None) -> Dict:
    """Check if agent is within budget. Returns decision dict.

    Returns:
        {
            "action": "allow" | "warn" | "block",
            "agent_id": str,
            "month": str,
            "budget_usd": float,
            "spent_usd": float,
            "remaining_usd": float,
            "utilization_pct": float,
            "message": str,
        }
    """
    budget_cfg = _get_agent_budget(agent_id)

    # Budget enforcement disabled — always allow
    if not budget_cfg.get("enabled"):
        return {
            "action": "allow",
            "agent_id": agent_id,
            "month": _current_month(),
            "budget_usd": 0.0,
            "spent_usd": 0.0,
            "remaining_usd": 0.0,
            "utilization_pct": 0.0,
            "message": "Budget enforcement disabled",
        }

    budget_usd = budget_cfg["budget_usd"]
    warning_threshold = budget_cfg["warning_threshold"]
    hard_stop = budget_cfg["hard_stop"]
    month = _current_month()

    # Zero budget means unlimited
    if budget_usd <= 0:
        return {
            "action": "allow",
            "agent_id": agent_id,
            "month": month,
            "budget_usd": 0.0,
            "spent_usd": 0.0,
            "remaining_usd": 0.0,
            "utilization_pct": 0.0,
            "message": "No budget cap set (unlimited)",
        }

    spent_usd = _get_monthly_spend(agent_id, month, db_path)
    remaining = round(budget_usd - spent_usd, 6)
    utilization = round(spent_usd / budget_usd, 4) if budget_usd > 0 else 0.0

    result = {
        "agent_id": agent_id,
        "month": month,
        "budget_usd": budget_usd,
        "spent_usd": spent_usd,
        "remaining_usd": max(remaining, 0.0),
        "utilization_pct": round(utilization * 100, 2),
    }

    if spent_usd >= budget_usd and hard_stop:
        result["action"] = "block"
        result["message"] = (
            f"Agent '{agent_id}' budget exhausted: "
            f"${spent_usd:.4f} spent of ${budget_usd:.2f} monthly cap. "
            f"Hard-stop active — invocation blocked."
        )
        logger.warning(result["message"])
    elif utilization >= warning_threshold:
        result["action"] = "warn"
        result["message"] = (
            f"Agent '{agent_id}' approaching budget: "
            f"${spent_usd:.4f} of ${budget_usd:.2f} "
            f"({result['utilization_pct']}% used)."
        )
        logger.info(result["message"])
    else:
        result["action"] = "allow"
        result["message"] = (
            f"Agent '{agent_id}' within budget: "
            f"${spent_usd:.4f} of ${budget_usd:.2f} "
            f"({result['utilization_pct']}% used)."
        )

    return result


def get_all_budgets(db_path: Path = None) -> Dict:
    """Get budget status for all agents with recorded usage this month."""
    month = _current_month()
    start, end = _month_range(month)
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            """SELECT agent_id, COALESCE(SUM(cost_estimate_usd), 0.0) AS total
               FROM agent_token_usage
               WHERE created_at >= %s
                 AND created_at < %s
               GROUP BY agent_id
               ORDER BY total DESC""",
            (start, end),
        ).fetchall()

        agents = {}
        for r in rows:
            aid = r["agent_id"]
            budget_status = check_budget(aid, db_path)
            agents[aid] = budget_status

        return {
            "month": month,
            "agent_count": len(agents),
            "agents": agents,
        }
    finally:
        conn.close()


class BudgetExceededError(Exception):
    """Raised when an agent's token budget has been exhausted."""

    def __init__(self, agent_id: str, budget_status: Dict):
        self.agent_id = agent_id
        self.budget_status = budget_status
        super().__init__(budget_status.get("message", f"Budget exceeded for {agent_id}"))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Token usage, cost tracking, and budget enforcement for ICDEV™ agents")
    parser.add_argument(
        "--action",
        choices=["summary", "cost", "check-budget", "budgets"],
        required=True,
        help="Action: summary, cost, check-budget (single agent), budgets (all agents)",
    )
    parser.add_argument("--project-id", default=None, help="Filter by project ID")
    parser.add_argument("--agent-id", default=None, help="Filter by agent ID")
    parser.add_argument("--since", default=None, help="Filter by ISO timestamp (>=)")
    parser.add_argument("--json", action="store_true", dest="json_output", help="Output as JSON")
    parser.add_argument("--gate", action="store_true", help="Exit 1 if any budget is blocked")
    args = parser.parse_args()

    if args.action == "summary":
        result = get_usage_summary(
            project_id=args.project_id,
            agent_id=args.agent_id,
            since=args.since,
        )
    elif args.action == "cost":
        if not args.project_id:
            parser.error("--project-id is required for 'cost' action")
        result = get_cost_estimate(project_id=args.project_id)
    elif args.action == "check-budget":
        if not args.agent_id:
            parser.error("--agent-id is required for 'check-budget' action")
        result = check_budget(agent_id=args.agent_id)
    elif args.action == "budgets":
        result = get_all_budgets()
    else:
        parser.error(f"Unknown action: {args.action}")
        return

    if args.json_output:
        print(json.dumps(result, indent=2))
    else:
        for key, value in result.items():
            if isinstance(value, dict):
                print(f"\n{key}:")
                for k2, v2 in value.items():
                    print(f"  {k2}: {v2}")
            else:
                print(f"{key}: {value}")

    # Gate mode: exit 1 if any budget blocked
    if args.gate:
        if args.action == "check-budget" and result.get("action") == "block":
            raise SystemExit(1)
        if args.action == "budgets":
            for agent_data in result.get("agents", {}).values():
                if agent_data.get("action") == "block":
                    raise SystemExit(1)


if __name__ == "__main__":
    main()
