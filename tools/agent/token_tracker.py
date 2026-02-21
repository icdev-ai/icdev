# CUI // SP-CTI
"""Token usage and cost tracking per agent/project/task.

Logs Bedrock API token consumption to data/icdev.db and provides
aggregated summaries and cost estimates by project, agent, and model.
"""

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

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
    timestamp TEXT NOT NULL
);
"""


def _ensure_table(conn: sqlite3.Connection) -> None:
    """Create agent_token_usage table if it does not exist."""
    conn.execute(CREATE_TABLE_SQL)
    conn.commit()


def _connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Open a connection and guarantee the table is present."""
    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
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
    db_path: Path = None,
) -> int:
    """Insert token usage record into agent_token_usage. Returns row ID."""
    conn = _connect(db_path)
    try:
        cursor = conn.execute(
            """
            INSERT INTO agent_token_usage
                (agent_id, project_id, model_id, input_tokens, output_tokens,
                 thinking_tokens, duration_ms, task_id, cost_estimate_usd, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            WHERE project_id = ?
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
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Token usage and cost tracking for ICDEV agents"
    )
    parser.add_argument(
        "--action",
        choices=["summary", "cost"],
        required=True,
        help="Action to perform",
    )
    parser.add_argument("--project-id", default=None, help="Filter by project ID")
    parser.add_argument("--agent-id", default=None, help="Filter by agent ID")
    parser.add_argument("--since", default=None, help="Filter by ISO timestamp (>=)")
    parser.add_argument(
        "--json", action="store_true", dest="json_output", help="Output as JSON"
    )
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


if __name__ == "__main__":
    main()
