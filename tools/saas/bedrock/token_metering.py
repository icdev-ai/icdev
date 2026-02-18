#!/usr/bin/env python3
# CUI // SP-CTI
"""ICDEV SaaS Phase 5 -- Bedrock Token Metering.

CUI // SP-CTI

Tracks Bedrock LLM token usage per tenant for billing, rate enforcement,
and cost allocation.  Records are stored in the platform.db
``usage_records`` table with endpoint='bedrock_proxy' and token counts
in the ``tokens_used`` column.

Budget enforcement reads from the tenant's subscription tier limits
(stored in ``subscriptions`` table) or from ``bedrock_config`` overrides.

Usage (library):
    from tools.saas.bedrock.token_metering import (
        record_token_usage, get_token_usage, check_token_budget
    )

    record_token_usage("tenant-abc", "user-1", "anthropic.claude-...", 500, 200, "bedrock_proxy")
    usage = get_token_usage("tenant-abc", period="month")
    budget = check_token_budget("tenant-abc")

Usage (CLI):
    python tools/saas/bedrock/token_metering.py --tenant-id tenant-abc --usage --period month
    python tools/saas/bedrock/token_metering.py --tenant-id tenant-abc --budget
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.saas.platform_db import get_platform_connection  # noqa: E402

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("saas.bedrock.metering")

# ---------------------------------------------------------------------------
# Default token budgets per tier (monthly)
# ---------------------------------------------------------------------------
DEFAULT_TOKEN_BUDGETS = {
    "starter": 100_000,
    "professional": 1_000_000,
    "enterprise": -1,  # unlimited
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _utcnow() -> str:
    """Return current UTC timestamp as ISO-8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _get_period_filter(period: str) -> str:
    """Return a SQL date prefix for filtering by period.

    Args:
        period: 'day' or 'month'.

    Returns:
        ISO date prefix string (e.g. '2026-02-17' or '2026-02').
    """
    now = datetime.now(timezone.utc)
    if period == "day":
        return now.strftime("%Y-%m-%d")
    else:
        return now.strftime("%Y-%m")


# ============================================================================
# Public API
# ============================================================================

def record_token_usage(tenant_id: str, user_id: str,
                       model_id: str, input_tokens: int,
                       output_tokens: int, endpoint: str = "bedrock_proxy"):
    """Record a Bedrock token usage event in platform.db.

    Writes to the ``usage_records`` table with total tokens (input + output)
    and model details in metadata.

    Args:
        tenant_id:     Platform tenant identifier.
        user_id:       User who initiated the call (may be None).
        model_id:      Bedrock model identifier.
        input_tokens:  Number of input/prompt tokens consumed.
        output_tokens: Number of output/completion tokens consumed.
        endpoint:      API endpoint label (default 'bedrock_proxy').
    """
    total_tokens = input_tokens + output_tokens
    metadata = json.dumps({
        "model_id": model_id,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    })

    conn = get_platform_connection()
    try:
        conn.execute(
            """INSERT INTO usage_records
               (tenant_id, user_id, endpoint, method, tokens_used,
                status_code, duration_ms, metadata, recorded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (tenant_id, user_id, endpoint, "BEDROCK",
             total_tokens, 200, 0, metadata, _utcnow()),
        )
        conn.commit()
        logger.debug(
            "Recorded %d tokens for tenant %s (model=%s)",
            total_tokens, tenant_id, model_id)
    except Exception as exc:
        conn.rollback()
        logger.error("Failed to record token usage: %s", exc)
        raise
    finally:
        conn.close()


def get_token_usage(tenant_id: str, period: str = "day") -> dict:
    """Get aggregated Bedrock token usage for a tenant.

    Args:
        tenant_id: Platform tenant identifier.
        period:    'day' for today or 'month' for current month.

    Returns:
        dict with total_tokens, total_requests, input_tokens,
        output_tokens, period, and period_start.
    """
    period_prefix = _get_period_filter(period)

    conn = get_platform_connection()
    try:
        # Get total tokens and request count
        row = conn.execute(
            """SELECT COALESCE(SUM(tokens_used), 0) as total_tokens,
                      COUNT(*) as total_requests
               FROM usage_records
               WHERE tenant_id = ?
                 AND endpoint = 'bedrock_proxy'
                 AND recorded_at LIKE ?""",
            (tenant_id, period_prefix + "%"),
        ).fetchone()

        total_tokens = row[0] if isinstance(row, (list, tuple)) else row["total_tokens"]
        total_requests = row[1] if isinstance(row, (list, tuple)) else row["total_requests"]

        # Get breakdown from metadata
        detail_rows = conn.execute(
            """SELECT metadata FROM usage_records
               WHERE tenant_id = ?
                 AND endpoint = 'bedrock_proxy'
                 AND recorded_at LIKE ?""",
            (tenant_id, period_prefix + "%"),
        ).fetchall()

        total_input = 0
        total_output = 0
        for dr in detail_rows:
            raw = dr[0] if isinstance(dr, (list, tuple)) else dr["metadata"]
            if raw and isinstance(raw, str):
                try:
                    meta = json.loads(raw)
                    total_input += meta.get("input_tokens", 0)
                    total_output += meta.get("output_tokens", 0)
                except json.JSONDecodeError:
                    pass

        return {
            "tenant_id": tenant_id,
            "period": period,
            "period_start": period_prefix,
            "total_tokens": total_tokens,
            "input_tokens": total_input,
            "output_tokens": total_output,
            "total_requests": total_requests,
        }
    finally:
        conn.close()


def check_token_budget(tenant_id: str) -> dict:
    """Check whether a tenant is within their monthly token budget.

    Budget is determined by:
      1. ``bedrock_config.token_budget`` override (if set).
      2. Subscription tier default from DEFAULT_TOKEN_BUDGETS.

    Args:
        tenant_id: Platform tenant identifier.

    Returns:
        dict with within_budget (bool), used, limit, remaining,
        and period.
    """
    conn = get_platform_connection()
    try:
        # Get tenant tier and bedrock_config
        row = conn.execute(
            """SELECT t.bedrock_config,
                      COALESCE(s.tier, 'starter') as tier
               FROM tenants t
               LEFT JOIN subscriptions s
                 ON s.tenant_id = t.id AND s.status = 'active'
               WHERE t.id = ?""",
            (tenant_id,),
        ).fetchone()

        if not row:
            raise ValueError(
                "Tenant not found: {}".format(tenant_id))

        raw_config = row[0] if isinstance(row, (list, tuple)) else row["bedrock_config"]
        tier = row[1] if isinstance(row, (list, tuple)) else row["tier"]

        # Check for override in bedrock_config
        budget_limit = DEFAULT_TOKEN_BUDGETS.get(tier, 100_000)
        if raw_config and isinstance(raw_config, str):
            try:
                config = json.loads(raw_config)
                if "token_budget" in config:
                    budget_limit = int(config["token_budget"])
            except (json.JSONDecodeError, ValueError):
                pass

        # Get current month usage
        usage = get_token_usage(tenant_id, period="month")
        used = usage.get("total_tokens", 0)

        # Unlimited budget
        if budget_limit == -1:
            return {
                "tenant_id": tenant_id,
                "within_budget": True,
                "used": used,
                "limit": -1,
                "remaining": -1,
                "period": "month",
                "tier": tier,
            }

        remaining = max(budget_limit - used, 0)
        within_budget = used < budget_limit

        return {
            "tenant_id": tenant_id,
            "within_budget": within_budget,
            "used": used,
            "limit": budget_limit,
            "remaining": remaining,
            "period": "month",
            "tier": tier,
        }
    finally:
        conn.close()


# ============================================================================
# CLI
# ============================================================================

def main():
    """CLI entry point for token metering queries."""
    parser = argparse.ArgumentParser(
        description="CUI // SP-CTI -- ICDEV Bedrock Token Metering",
    )
    parser.add_argument("--tenant-id", required=True,
                        help="Target tenant ID")

    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--usage", action="store_true",
                        help="Show token usage summary")
    action.add_argument("--budget", action="store_true",
                        help="Check token budget status")

    parser.add_argument("--period", type=str, default="day",
                        choices=["day", "month"],
                        help="Usage period (default: day)")
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="Output as JSON")

    args = parser.parse_args()

    try:
        if args.usage:
            result = get_token_usage(args.tenant_id, period=args.period)
            if args.as_json:
                print(json.dumps(result, indent=2))
            else:
                print("Token Usage -- {} ({})".format(
                    args.tenant_id, result["period"]))
                print("  Period start:    {}".format(
                    result["period_start"]))
                print("  Total tokens:    {:,}".format(
                    result["total_tokens"]))
                print("  Input tokens:    {:,}".format(
                    result["input_tokens"]))
                print("  Output tokens:   {:,}".format(
                    result["output_tokens"]))
                print("  Total requests:  {:,}".format(
                    result["total_requests"]))

        elif args.budget:
            result = check_token_budget(args.tenant_id)
            if args.as_json:
                print(json.dumps(result, indent=2))
            else:
                status = "WITHIN BUDGET" if result["within_budget"] else "OVER BUDGET"
                print("[{}] Token Budget -- {}".format(
                    status, args.tenant_id))
                print("  Tier:       {}".format(result["tier"]))
                print("  Used:       {:,}".format(result["used"]))
                if result["limit"] == -1:
                    print("  Limit:      unlimited")
                    print("  Remaining:  unlimited")
                else:
                    print("  Limit:      {:,}".format(result["limit"]))
                    print("  Remaining:  {:,}".format(result["remaining"]))
                print("  Period:     {}".format(result["period"]))
                if not result["within_budget"]:
                    sys.exit(1)

    except ValueError as exc:
        print("ERROR: {}".format(exc), file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print("FATAL: {}".format(exc), file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
