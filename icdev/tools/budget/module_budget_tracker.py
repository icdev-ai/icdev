# [TEMPLATE: CUI // SP-CTI]
"""Module-level budget tracker for generative intelligence and predictive analysis.

Enforces computational resource limits across two high-cost modules:
  - generative_intelligence: LLM invocations, embeddings, chain orchestration
  - predictive_analysis: simulation runs, Monte Carlo iterations, scenario evaluations

Tracks usage in module_budget_usage table; config lives in args/llm_config.yaml
under module_budgets.  Hard-stops invocations when budget exhausted.

Usage::

    from tools.budget.module_budget_tracker import check_module_budget, record_module_usage
    from tools.budget.module_budget_tracker import ModuleBudgetExceededError

    # Before expensive operation
    status = check_module_budget("generative_intelligence", function="code_generation")
    if status["action"] == "block":
        raise ModuleBudgetExceededError("generative_intelligence", status)

    # After operation
    record_module_usage("generative_intelligence", cost_usd=0.05, tokens=1500)

CLI::

    python tools/budget/module_budget_tracker.py --check generative_intelligence --json
    python tools/budget/module_budget_tracker.py --check predictive_analysis --json
    python tools/budget/module_budget_tracker.py --dashboard --json
    python tools/budget/module_budget_tracker.py --gate
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import argparse
import json
from contextlib import contextmanager
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

try:
    import yaml
except ImportError:
    yaml = None

from tools.db.storage import get_connection

logger = get_logger("icdev.budget.module_budget")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = BASE_DIR / "args" / "llm_config.yaml"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_MODULES = ("generative_intelligence", "predictive_analysis")


def declared_modules() -> tuple:
    """Every module a budget may be enforced against: the two built-ins plus anything
    declared under ``module_budgets.per_module``.

    An unknown module is ALLOWED, not blocked, so this list is what makes a declared
    module actually enforceable rather than silently free.
    """
    try:
        per_module = (_load_module_budget_config().get("per_module") or {})
        return tuple(dict.fromkeys(VALID_MODULES + tuple(per_module)))
    except Exception:  # noqa: BLE001 -- an unreadable config must not disable enforcement
        return VALID_MODULES


def module_for_function(function: str) -> str:
    """Which budget module this LLM function is charged to.

    THE DEFECT THIS EXISTS FOR. ``router.invoke`` hardcoded ``"generative_intelligence"``
    for EVERY call, so one monthly pool covered the whole platform and a "module budget"
    was a global cap wearing a module's name. Measured 2026-08-28: FT's period row read
    407,934 / 400,000 tokens, and because the pool is shared the block was not specific
    to the workload that filled it -- ``code_generation`` was refused too, as was every
    other function in both parents.

    The mapping is DATA (``module_budgets.function_modules``), not a second hardcoded
    frozenset beside ``PREDICTIVE_ANALYSIS_FUNCTIONS``. An unmapped function falls to
    ``default_module``, which defaults to ``generative_intelligence`` -- so a deployment
    that declares no mapping behaves exactly as it did before this change.

    A call is charged to exactly ONE module. Charging one call to two pools -- which is
    what the predictive branch did, counting a simulation call against both -- is part of
    why neither number could be read as a budget.
    """
    try:
        cfg = _load_module_budget_config()
    except Exception:  # noqa: BLE001
        return "generative_intelligence"
    default = str(cfg.get("default_module") or "generative_intelligence")
    mapping = cfg.get("function_modules") or {}
    mod = mapping.get(function) if isinstance(mapping, dict) else None
    if not mod:
        # The legacy frozenset stays authoritative for the module it was written for, so
        # deleting it is a separate, reviewable change.
        if function in PREDICTIVE_ANALYSIS_FUNCTIONS:
            return "predictive_analysis"
        return default
    return str(mod) if str(mod) in declared_modules() else default
VALID_RESOURCE_TYPES = ("usd", "tokens", "operations")

# LLM function names that belong to the predictive_analysis module.
# When the router invokes these, tokens also count against predictive_analysis budget.
PREDICTIVE_ANALYSIS_FUNCTIONS = frozenset({
    "agent_simulation",
    "coa_generation",
    "scenario_evaluation",
    "monte_carlo_simulation",
    "predictive_model",
    "risk_assessment",
    "simulation_run",
    "digital_twin_analysis",
    "program_simulation",
})

# SQL DDL
CREATE_MODULE_BUDGET_USAGE_SQL = """
CREATE TABLE IF NOT EXISTS module_budget_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    module_name TEXT NOT NULL CHECK(module_name <> ''),  -- declared in args/llm_config.yaml; see declared_modules()
    function_name TEXT,
    resource_type TEXT NOT NULL DEFAULT 'usd' CHECK(resource_type IN ('usd', 'tokens', 'operations')),
    amount REAL NOT NULL DEFAULT 0.0,
    tokens INTEGER NOT NULL DEFAULT 0,
    operations INTEGER NOT NULL DEFAULT 0,
    project_id TEXT,
    model_id TEXT,
    details_json TEXT,
    -- How `amount` was arrived at: priced | local_zero | unpriced. NULL on rows
    -- written before the basis existed, which is a different fact from
    -- 'unpriced'. See migration 20260815003153.
    cost_basis TEXT,
    created_at TEXT NOT NULL DEFAULT ''
);
"""

CREATE_MODULE_BUDGET_PERIODS_SQL = """
CREATE TABLE IF NOT EXISTS module_budget_periods (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    module_name TEXT NOT NULL CHECK(module_name <> ''),  -- declared in args/llm_config.yaml; see declared_modules()
    month TEXT NOT NULL,
    budget_usd REAL NOT NULL DEFAULT 0.0,
    budget_tokens INTEGER NOT NULL DEFAULT 0,
    budget_operations INTEGER NOT NULL DEFAULT 0,
    spent_usd REAL NOT NULL DEFAULT 0.0,
    spent_tokens INTEGER NOT NULL DEFAULT 0,
    spent_operations INTEGER NOT NULL DEFAULT 0,
    warning_threshold REAL NOT NULL DEFAULT 0.8,
    hard_stop INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT '',
    UNIQUE(module_name, month)
);
"""

# ---------------------------------------------------------------------------
# Schema bootstrap
# ---------------------------------------------------------------------------


def _ensure_tables(conn) -> None:
    """Create module budget tables if missing."""
    conn.execute(CREATE_MODULE_BUDGET_USAGE_SQL)
    conn.execute(CREATE_MODULE_BUDGET_PERIODS_SQL)
    conn.commit()


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

_module_budget_config_cache: Optional[Dict] = None


def _load_module_budget_config() -> Dict:
    """Load module_budgets section from llm_config.yaml."""
    global _module_budget_config_cache
    if _module_budget_config_cache is not None:
        return _module_budget_config_cache

    if not CONFIG_PATH.exists() or yaml is None:
        _module_budget_config_cache = {"enabled": False}
        return _module_budget_config_cache

    try:
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        _module_budget_config_cache = data.get("module_budgets", {"enabled": False})
        return _module_budget_config_cache
    except Exception as exc:
        logger.warning("Failed to load module budget config: %s", exc)
        _module_budget_config_cache = {"enabled": False}
        return _module_budget_config_cache


def _current_month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _month_range(month: str) -> tuple:
    year, mon = int(month[:4]), int(month[5:7])
    start = f"{year:04d}-{mon:02d}-01T00:00:00"
    if mon == 12:
        end = f"{year + 1:04d}-01-01T00:00:00"
    else:
        end = f"{year:04d}-{mon + 1:02d}-01T00:00:00"
    return start, end


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()



# ---------------------------------------------------------------------------
# Budget config resolution
# ---------------------------------------------------------------------------


def _get_module_budget_config(module_name: str) -> Dict:
    """Resolve budget config for a module."""
    cfg = _load_module_budget_config()
    if not cfg.get("enabled", False):
        return {"enabled": False}

    defaults = {
        "enabled": True,
        "monthly_usd": cfg.get("default_monthly_usd", 0.0),
        "monthly_tokens": cfg.get("default_monthly_tokens", 0),
        "monthly_operations": cfg.get("default_monthly_operations", 0),
        "warning_threshold": cfg.get("warning_threshold", 0.8),
        "hard_stop": cfg.get("hard_stop", True),
    }

    per_module = cfg.get("per_module", {})
    mod_cfg = per_module.get(module_name, {})

    result = dict(defaults)
    result.update({k: v for k, v in mod_cfg.items() if v is not None})
    return result


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------


def _get_conn():
    return get_connection()


@contextmanager
def _conn_scope():
    """Yield a connection that is ALWAYS closed, with no transaction left open.

    Every call site here previously did ``conn = _get_conn()`` ... ``conn.close()``
    with no try/finally. On the happy path that is fine. On an exception path --
    a locked database, a schema mismatch, a failed INSERT -- the connection was
    never closed and any implicit write transaction stayed open.

    That is a production connection leak, and in tests it is worse: budget usage
    is recorded from ChatManager._agent_loop BACKGROUND THREADS, which outlive
    the test that started them. A transaction left open there is attributed to
    whichever unrelated test happens to be finishing when the leak guard looks,
    and it blocks any later DDL on another connection until the busy timeout
    expires. That is the mechanism behind the suite hang.

    Rolls back rather than commits on the way out: callers commit explicitly
    when they mean to, so anything still pending here is by definition
    unfinished work from a failed path.

    The rollback is unconditional. ``get_connection`` returns a
    ``StorageConnection`` wrapper, which exposes ``rollback``/``close`` but NOT
    ``in_transaction`` and defines no ``__getattr__`` passthrough -- so guarding
    on ``getattr(conn, "in_transaction", False)`` reads False on every call and
    never rolls anything back. Calling rollback outright is both correct and
    simpler: with nothing pending it is a no-op on sqlite3 and psycopg2 alike,
    and it needs no knowledge of which backend is underneath.
    """
    conn = _get_conn()
    try:
        yield conn
    finally:
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001 - never mask the original error
            pass
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def _get_or_create_period(conn, module_name: str, month: str) -> Dict:
    """Fetch or initialize a budget period row."""
    row = conn.execute(
        "SELECT * FROM module_budget_periods WHERE module_name = %s AND month = %s",
        (module_name, month),
    ).fetchone()

    if row:
        return dict(row)

    cfg = _get_module_budget_config(module_name)
    now = _now_iso()
    conn.execute(
        """INSERT INTO module_budget_periods
           (module_name, month, budget_usd, budget_tokens, budget_operations,
            spent_usd, spent_tokens, spent_operations, warning_threshold, hard_stop,
            created_at, updated_at)
           VALUES (%s, %s, %s, %s, %s, 0.0, 0, 0, %s, %s, %s, %s)""",
        (
            module_name,
            month,
            cfg.get("monthly_usd", 0.0),
            cfg.get("monthly_tokens", 0),
            cfg.get("monthly_operations", 0),
            cfg.get("warning_threshold", 0.8),
            1 if cfg.get("hard_stop", True) else 0,
            now,
            now,
        ),
    )
    conn.commit()

    row = conn.execute(
        "SELECT * FROM module_budget_periods WHERE module_name = %s AND month = %s",
        (module_name, month),
    ).fetchone()
    return dict(row) if row else {}


def _get_monthly_spend(conn, module_name: str, month: str) -> Dict:
    """Return aggregated spend for the module this month from usage table."""
    start, end = _month_range(month)
    row = conn.execute(
        """SELECT COALESCE(SUM(amount), 0.0) AS total_usd,
                   COALESCE(SUM(tokens), 0) AS total_tokens,
                   COALESCE(SUM(operations), 0) AS total_operations,
                   COUNT(*) AS call_count
           FROM module_budget_usage
           WHERE module_name = %s AND created_at >= %s AND created_at < %s""",
        (module_name, start, end),
    ).fetchone()
    return {
        "usd": float(row["total_usd"]) if row else 0.0,
        "tokens": int(row["total_tokens"]) if row else 0,
        "operations": int(row["total_operations"]) if row else 0,
        "calls": int(row["call_count"]) if row else 0,
    }


def _sync_period_spent(conn, module_name: str, month: str) -> None:
    """Recompute spent columns in module_budget_periods from usage table."""
    spend = _get_monthly_spend(conn, module_name, month)
    conn.execute(
        """UPDATE module_budget_periods
           SET spent_usd = %s, spent_tokens = %s, spent_operations = %s, updated_at = %s
           WHERE module_name = %s AND month = %s""",
        (spend["usd"], spend["tokens"], spend["operations"], _now_iso(), module_name, month),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class ModuleBudgetExceededError(RuntimeError):
    """Raised when a module-level budget has been exhausted."""

    def __init__(self, module_name: str, budget_status: Dict):
        self.module_name = module_name
        self.budget_status = budget_status
        super().__init__(
            budget_status.get(
                "message",
                f"Module '{module_name}' budget exceeded",
            )
        )


def check_module_budget(
    module_name: str,
    function: str = "",
    estimated_cost_usd: float = 0.0,
    estimated_tokens: int = 0,
    estimated_operations: int = 0,
    project_id: str = "",
) -> Dict:
    """Check whether a module operation is within budget.

    Args:
        module_name: 'generative_intelligence' or 'predictive_analysis'
        function: ICDEV function name (for logging/detail)
        estimated_cost_usd: Expected USD cost of this operation
        estimated_tokens: Expected token count (for generative intelligence)
        estimated_operations: Expected operation count (for predictive analysis)
        project_id: Optional project ID for attribution

    Returns:
        Dict with action ('allow'|'warn'|'block'), utilization, message.
    """
    if module_name not in declared_modules():
        return {
            "action": "allow",
            "module_name": module_name,
            "message": f"Unknown module '{module_name}' — no budget enforced",
        }

    cfg = _get_module_budget_config(module_name)
    if not cfg.get("enabled", False):
        return {
            "action": "allow",
            "module_name": module_name,
            "message": "Module budget enforcement disabled",
        }

    month = _current_month()
    with _conn_scope() as conn:
        _ensure_tables(conn)

        # Sync to ensure period table reflects latest usage
        _sync_period_spent(conn, module_name, month)
        period = _get_or_create_period(conn, module_name, month)

    budget_usd = float(period.get("budget_usd", 0.0))
    budget_tokens = int(period.get("budget_tokens", 0))
    budget_operations = int(period.get("budget_operations", 0))
    warning_threshold = float(period.get("warning_threshold", 0.8))
    hard_stop = bool(period.get("hard_stop", 1))

    spent_usd = float(period.get("spent_usd", 0.0))
    spent_tokens = int(period.get("spent_tokens", 0))
    spent_operations = int(period.get("spent_operations", 0))

    # Zero budget means unlimited for that resource type
    result = {
        "module_name": module_name,
        "month": month,
        "function": function,
        "project_id": project_id,
        "action": "allow",
        "budget_usd": budget_usd,
        "spent_usd": round(spent_usd, 6),
        "remaining_usd": round(max(budget_usd - spent_usd, 0.0), 6),
        "utilization_usd_pct": round((spent_usd / budget_usd) * 100, 2) if budget_usd > 0 else 0.0,
        "budget_tokens": budget_tokens,
        "spent_tokens": spent_tokens,
        "remaining_tokens": max(budget_tokens - spent_tokens, 0),
        "utilization_tokens_pct": round((spent_tokens / budget_tokens) * 100, 2) if budget_tokens > 0 else 0.0,
        "budget_operations": budget_operations,
        "spent_operations": spent_operations,
        "remaining_operations": max(budget_operations - spent_operations, 0),
        "utilization_operations_pct": round((spent_operations / budget_operations) * 100, 2) if budget_operations > 0 else 0.0,
        "warning_threshold": warning_threshold,
        "hard_stop": hard_stop,
    }

    # Check each constrained resource
    over_budget = False
    over_message = []

    if budget_usd > 0 and (spent_usd + estimated_cost_usd) > budget_usd:
        over_budget = True
        over_message.append(
            f"USD cap: ${spent_usd + estimated_cost_usd:.4f} would exceed ${budget_usd:.2f}"
        )

    if budget_tokens > 0 and (spent_tokens + estimated_tokens) > budget_tokens:
        # NOTE, measured 2026-08-28 and deliberately NOT acted on here: this cap is a cost
        # proxy, and on this deployment it fired at 407,934 of 400,000 tokens while the USD
        # cap beside it -- the one that tracks real money -- sat at $0.00 of $150.00,
        # because every call routed to a provider priced at $0/1k. Relaxing it pre-flight
        # is not implementable: the gate runs BEFORE the model is resolved, and every
        # declared chain here carries `claude-sonnet` and `gpt-4o` as fallbacks, so the
        # honest pre-flight answer is always "this could cost money". Moving the token cap
        # after routing, or giving it a short window so it guards runaways instead of
        # proxying cost, is its own card.
        over_budget = True
        over_message.append(
            f"Token cap: {spent_tokens + estimated_tokens} would exceed {budget_tokens}"
        )

    if budget_operations > 0 and (spent_operations + estimated_operations) > budget_operations:
        over_budget = True
        over_message.append(
            f"Operation cap: {spent_operations + estimated_operations} would exceed {budget_operations}"
        )

    if over_budget and hard_stop:
        result["action"] = "block"
        result["message"] = (
            f"Module '{module_name}' budget exceeded for function '{function}': "
            + "; ".join(over_message)
        )
        logger.warning(result["message"])
        return result

    # Warning threshold check (only for primary resource)
    primary_util = 0.0
    primary_budget = 0.0
    if budget_usd > 0:
        primary_util = spent_usd / budget_usd
        primary_budget = budget_usd
    elif budget_tokens > 0:
        primary_util = spent_tokens / budget_tokens
        primary_budget = budget_tokens
    elif budget_operations > 0:
        primary_util = spent_operations / budget_operations
        primary_budget = budget_operations

    if primary_budget > 0 and primary_util >= warning_threshold:
        result["action"] = "warn"
        result["message"] = (
            f"Module '{module_name}' approaching budget: "
            f"${spent_usd:.4f} of ${budget_usd:.2f} ({result['utilization_usd_pct']}%), "
            f"{spent_tokens} of {budget_tokens} tokens, "
            f"{spent_operations} of {budget_operations} ops."
        )
        logger.info(result["message"])
        return result

    result["message"] = (
        f"Module '{module_name}' within budget: "
        f"${spent_usd:.4f} of ${budget_usd:.2f} ({result['utilization_usd_pct']}%), "
        f"{spent_tokens} of {budget_tokens} tokens, "
        f"{spent_operations} of {budget_operations} ops."
    )
    return result


def record_module_usage(
    module_name: str,
    cost_usd: float = 0.0,
    tokens: int = 0,
    operations: int = 0,
    function: str = "",
    project_id: str = "",
    model_id: str = "",
    details: Optional[Dict] = None,
    cost_basis: str = "",
) -> str:
    """Record resource consumption against a module budget.

    Returns the inserted usage record ID as a string, or "" if the module is
    unknown.

    ``id`` is deliberately absent from the column list. It is
    ``INTEGER PRIMARY KEY AUTOINCREMENT`` (translated to ``SERIAL`` for
    PostgreSQL), so the database assigns it. This function used to pass an
    explicit ``_gen_id("mbu-")`` string into that integer column, which failed
    on EVERY call and on BOTH backends -- ``InvalidTextRepresentation`` on
    PostgreSQL, ``IntegrityError: datatype mismatch`` on SQLite. Every caller
    wraps the call in ``except Exception: pass``, so the failure was silent and
    ``module_budget_usage`` never received a single row; module budget
    enforcement read a permanently empty table. The sibling
    ``_get_or_create_period`` omits ``id`` and has always worked, which is why
    ``module_budget_periods`` has rows and this table does not.

    Fixed by omitting the column rather than by widening it to TEXT:
    ``CREATE TABLE IF NOT EXISTS`` never alters an existing table, so a DDL
    change would leave every deployed database broken until a migration ran.
    """
    if module_name not in VALID_MODULES:
        logger.debug("Skipping module usage for unknown module: %s", module_name)
        return ""

    record_id = ""
    with _conn_scope() as conn:
        _ensure_tables(conn)
        cursor = conn.execute(
            """INSERT INTO module_budget_usage
               (module_name, function_name, resource_type, amount, tokens,
                operations, project_id, model_id, details_json, cost_basis,
                created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               RETURNING id""",
            (
                module_name,
                function,
                "usd" if cost_usd > 0 else ("tokens" if tokens > 0 else "operations"),
                cost_usd,
                tokens,
                operations,
                project_id,
                model_id,
                json.dumps(details) if details else None,
                cost_basis or None,
                _now_iso(),
            ),
        )
        # RETURNING is supported by PostgreSQL and by SQLite 3.35+, so one
        # statement serves both backends without an is_pg branch. psycopg2's
        # lastrowid would not: it reports an OID, not the sequence value.
        row = cursor.fetchone()
        if row is not None:
            record_id = str(row["id"] if isinstance(row, dict) else row[0])
        conn.commit()

    # Update period aggregate on its own connection (_sync_period_spent commits).
    with _conn_scope() as conn:
        _sync_period_spent(conn, module_name, _current_month())

    return record_id


def get_module_budget_dashboard() -> Dict:
    """Return budget status for both modules."""
    month = _current_month()
    with _conn_scope() as conn:
        _ensure_tables(conn)

        results = {}
        for mod in VALID_MODULES:
            period = _get_or_create_period(conn, mod, month)
            results[mod] = {
                "month": month,
                "budget_usd": float(period.get("budget_usd", 0.0)),
                "spent_usd": float(period.get("spent_usd", 0.0)),
                "budget_tokens": int(period.get("budget_tokens", 0)),
                "spent_tokens": int(period.get("spent_tokens", 0)),
                "budget_operations": int(period.get("budget_operations", 0)),
                "spent_operations": int(period.get("spent_operations", 0)),
                "warning_threshold": float(period.get("warning_threshold", 0.8)),
                "hard_stop": bool(period.get("hard_stop", 1)),
            }

    return {"status": "ok", "month": month, "modules": results}


def run_gate() -> Dict:
    """Return fail if any module is over budget."""
    dashboard = get_module_budget_dashboard()
    blocked = []
    for mod, data in dashboard.get("modules", {}).items():
        if data.get("hard_stop") and data.get("budget_usd", 0) > 0:
            if data["spent_usd"] >= data["budget_usd"]:
                blocked.append(mod)
        if data.get("hard_stop") and data.get("budget_tokens", 0) > 0:
            if data["spent_tokens"] >= data["budget_tokens"]:
                blocked.append(mod)
        if data.get("hard_stop") and data.get("budget_operations", 0) > 0:
            if data["spent_operations"] >= data["budget_operations"]:
                blocked.append(mod)

    passed = len(blocked) == 0
    return {
        "status": "pass" if passed else "fail",
        "gate": "module_budget",
        "blocked_modules": list(set(blocked)),
        "message": "All modules within budget" if passed else f"Over budget: {', '.join(set(blocked))}",
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Module-level budget tracker for generative intelligence and predictive analysis"
    )
    parser.add_argument("--check", type=str, help="Check budget for module name")
    parser.add_argument("--function", type=str, default="", help="Function name")
    parser.add_argument("--cost", type=float, default=0.0, help="Estimated cost USD")
    parser.add_argument("--tokens", type=int, default=0, help="Estimated tokens")
    parser.add_argument("--operations", type=int, default=0, help="Estimated operations")
    parser.add_argument("--record", action="store_true", help="Record usage (requires --module)")
    parser.add_argument("--module", type=str, help="Module name for --record")
    parser.add_argument("--dashboard", action="store_true", help="Show dashboard")
    parser.add_argument("--gate", action="store_true", help="Gate check (exit 1 on over-budget)")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    parser.add_argument("--init-tables", action="store_true", help="Initialize tables only")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    result: Dict = {}

    if args.init_tables:
        with _conn_scope() as conn:
            _ensure_tables(conn)
        result = {"status": "ok", "message": "Module budget tables initialized"}

    elif args.check:
        result = check_module_budget(
            module_name=args.check,
            function=args.function,
            estimated_cost_usd=args.cost,
            estimated_tokens=args.tokens,
            estimated_operations=args.operations,
        )

    elif args.record:
        if not args.module:
            parser.error("--module required with --record")
        rid = record_module_usage(
            module_name=args.module,
            cost_usd=args.cost,
            tokens=args.tokens,
            operations=args.operations,
            function=args.function,
        )
        result = {"status": "ok", "record_id": rid}

    elif args.dashboard:
        result = get_module_budget_dashboard()

    elif args.gate:
        result = run_gate()
        if args.json_output:
            print(json.dumps(result, indent=2))
        else:
            print(f"Gate: module_budget — {result['status'].upper()}")
            print(f"  Blocked modules: {result['blocked_modules']}")
            print(f"  {result['message']}")
        sys.exit(0 if result["status"] == "pass" else 1)

    else:
        parser.print_help()
        sys.exit(0)

    if args.json_output:
        print(json.dumps(result, indent=2, default=str))
    else:
        for key, val in result.items():
            if isinstance(val, dict):
                print(f"\n{key}:")
                for k2, v2 in val.items():
                    print(f"  {k2}: {v2}")
            else:
                print(f"{key}: {val}")


if __name__ == "__main__":
    main()
