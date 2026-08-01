# CUI // SP-CTI
"""ICDEV LLM proxy per-key budget accounting + enforcement (lpx-keys-02).

Wires spend budgets onto ICDEV's EXISTING grouping units rather than inventing a
parallel notion of "student". A virtual key (lpx-keys-01) carries a scope
(``tenant`` / ``team`` / ``guild`` / ``user``) and an optional ``max_budget_usd``
within a ``budget_window``; this module records spend attributed to a key and
decides allow / warn / block against that key's budget.

Three non-uniform grouping units, deliberately NOT forced into one table
(they are distinguished only by ``scope_type`` on the key + this ledger):

* ``/gameday`` — ``ttx_teams(team_id, session_id)``. Players join by ``join_code``
  and have NO ``dashboard_users`` row, so per-USER budgets are impossible; per-TEAM
  is the only available granularity (``scope_type='team'``,
  ``scope_ref=team_id``, ``session_id`` set). The gameday per-EXERCISE wiring
  lives in ``lpx-teams-02``.
* ``/academy`` — ``fa_guilds`` / ``fa_guild_members`` over ``fa_users``
  (``scope_type='guild'`` or ``'user'``). Whether academy budgets are per-guild
  or per-user is decided at issuance time, not assumed to mirror gameday.
* local canvas copies — individual dashboard users (``scope_type='user'``; see
  lpx-keys-04).

Defense in depth — this is an ADDITIONAL layer, not a replacement:

* ``tools/llm/gateway.py`` keeps its process/agent cost cap
  (``tools/agent/token_tracker.check_budget``). That layer is per-AGENT and
  process-global; it is untouched here.
* This module is per-KEY (i.e. per team / guild / user), so one cohort unit
  exhausting its budget degrades only that unit, not the whole process.

Interaction with ``tools/llm/rate_gate.py`` — NO double-throttle. ``rate_gate`` is
a process/host/cluster **concurrency** gate + inter-call pause measured in
requests-in-flight; it protects the shared provider account from bursts and has
no scope dimension. Budgets here are measured in **dollars per scope**. The two
gate on orthogonal axes and compose cleanly: a call may pass the concurrency gate
and still be blocked for budget, or vice versa. Neither reads the other's state.
Per-team RPM/TPM **rate** ceilings (a third, scope-aware axis) are built in
``lpx-teams-01`` and are likewise orthogonal to both.

PG-primary: aggregation is plain ``SUM`` (portable); no SQLite-dialect JSON SQL.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from tools.db.storage import get_connection

# Warn once utilisation crosses this fraction of budget (mirrors the warn/block
# vocabulary of tools/agent/token_tracker.check_budget).
DEFAULT_WARN_PCT = 0.8

_DDL = """
CREATE TABLE IF NOT EXISTS llm_proxy_spend (
    spend_id       TEXT PRIMARY KEY,
    key_id         TEXT NOT NULL,
    scope_type     TEXT NOT NULL DEFAULT 'tenant',
    scope_ref      TEXT,
    session_id     TEXT,
    window_key     TEXT NOT NULL DEFAULT 'none',
    input_tokens   INTEGER NOT NULL DEFAULT 0,
    output_tokens  INTEGER NOT NULL DEFAULT 0,
    cost_usd       REAL NOT NULL DEFAULT 0.0,
    tenant_id      TEXT,
    classification TEXT,
    recorded_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_llm_proxy_spend_key ON llm_proxy_spend(key_id, window_key)",
    "CREATE INDEX IF NOT EXISTS idx_llm_proxy_spend_scope ON llm_proxy_spend(scope_type, scope_ref)",
)

_migrated = False


def ensure_schema(conn=None) -> None:
    """Idempotently create the spend ledger. Depends on proxy_keys' table too."""
    global _migrated
    if _migrated and conn is None:
        return
    own = conn is None
    c = conn or get_connection()
    # The keys table must exist for lookups; create both idempotently.
    from tools.llm.proxy_keys import ensure_schema as ensure_keys_schema

    ensure_keys_schema(c)
    c.execute(_DDL.strip())
    for stmt in _INDEXES:
        try:
            c.execute(stmt)
        except Exception:
            pass
    c.commit()
    if own:
        _migrated = True


def _now() -> datetime:
    return datetime.now(timezone.utc)


def window_key_for(budget_window: str, session_id: Optional[str], now: Optional[datetime] = None) -> str:
    """Derive the accounting window key from a key's budget_window.

    * ``exercise`` — one bounded gameday session (resets per exercise).
    * ``day`` / ``month`` — calendar windows (academy/tenant may be month-shaped).
    * ``none`` — a single lifetime bucket (unlimited windows collapse here).
    """
    now = now or _now()
    if budget_window == "exercise":
        return f"exercise:{session_id or 'unscoped'}"
    if budget_window == "day":
        return f"day:{now.strftime('%Y-%m-%d')}"
    if budget_window == "month":
        return f"month:{now.strftime('%Y-%m')}"
    return "none"


def _key_row(key_id: str, conn) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        "SELECT key_id, scope_type, scope_ref, session_id, max_budget_usd, "
        "budget_window, status, tenant_id, classification "
        "FROM llm_proxy_keys WHERE key_id = %s",
        (key_id,),
    ).fetchone()
    return dict(row) if row else None


def record_spend(
    key_id: str,
    *,
    cost_usd: float = 0.0,
    input_tokens: int = 0,
    output_tokens: int = 0,
    now: Optional[datetime] = None,
    conn=None,
) -> Dict[str, Any]:
    """Append a spend record attributed to *key_id* in its current window."""
    own = conn is None
    c = conn or get_connection()
    ensure_schema(c)
    key = _key_row(key_id, c)
    if key is None:
        if own:
            _safe_close(c)
        raise ValueError(f"unknown key_id: {key_id}")

    now = now or _now()
    wkey = window_key_for(key.get("budget_window") or "none", key.get("session_id"), now)
    spend_id = uuid.uuid4().hex
    c.execute(
        """
        INSERT INTO llm_proxy_spend (
            spend_id, key_id, scope_type, scope_ref, session_id, window_key,
            input_tokens, output_tokens, cost_usd, tenant_id, classification, recorded_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            spend_id, key_id, key.get("scope_type"), key.get("scope_ref"),
            key.get("session_id"), wkey, int(input_tokens), int(output_tokens),
            float(cost_usd), key.get("tenant_id"), key.get("classification"),
            now.isoformat(),
        ),
    )
    c.commit()
    if own:
        _safe_close(c)
    return {"spend_id": spend_id, "key_id": key_id, "window_key": wkey, "cost_usd": float(cost_usd)}


def get_spend(key_id: str, *, window_key: Optional[str] = None, now: Optional[datetime] = None, conn=None) -> Dict[str, Any]:
    """Sum spend for *key_id* in the given (or current) window."""
    own = conn is None
    c = conn or get_connection()
    ensure_schema(c)
    key = _key_row(key_id, c)
    if key is None:
        if own:
            _safe_close(c)
        raise ValueError(f"unknown key_id: {key_id}")
    if window_key is None:
        window_key = window_key_for(key.get("budget_window") or "none", key.get("session_id"), now)
    row = c.execute(
        "SELECT COALESCE(SUM(cost_usd), 0.0) AS cost, "
        "COALESCE(SUM(input_tokens), 0) AS itok, "
        "COALESCE(SUM(output_tokens), 0) AS otok "
        "FROM llm_proxy_spend WHERE key_id = %s AND window_key = %s",
        (key_id, window_key),
    ).fetchone()
    d = dict(row)
    if own:
        _safe_close(c)
    return {
        "key_id": key_id,
        "window_key": window_key,
        "spent_usd": round(float(d["cost"]), 6),
        "input_tokens": int(d["itok"]),
        "output_tokens": int(d["otok"]),
    }


def check_budget(
    key_id: str,
    *,
    projected_cost_usd: float = 0.0,
    warn_pct: float = DEFAULT_WARN_PCT,
    now: Optional[datetime] = None,
    conn=None,
) -> Dict[str, Any]:
    """Decide allow / warn / block for *key_id* against its per-key budget.

    Return shape mirrors ``tools/agent/token_tracker.check_budget`` so callers can
    treat the two layers uniformly:
        {action, key_id, scope_type, scope_ref, window_key, budget_usd,
         spent_usd, projected_usd, remaining_usd, utilization_pct, message}

    A revoked/expired key blocks. A key with no ``max_budget_usd`` is unlimited
    (always allow). Block fires when spent + projected would meet or exceed the
    budget — the deny case is asserted in the tests.
    """
    own = conn is None
    c = conn or get_connection()
    ensure_schema(c)
    key = _key_row(key_id, c)
    if key is None:
        if own:
            _safe_close(c)
        raise ValueError(f"unknown key_id: {key_id}")

    status = key.get("status")
    scope_type = key.get("scope_type")
    scope_ref = key.get("scope_ref")
    budget = key.get("max_budget_usd")
    wkey = window_key_for(key.get("budget_window") or "none", key.get("session_id"), now)

    def _result(action: str, spent: float, message: str) -> Dict[str, Any]:
        if own:
            _safe_close(c)
        remaining = None if budget is None else round(float(budget) - spent, 6)
        util = None if not budget else round((spent / float(budget)) * 100.0, 2)
        return {
            "action": action,
            "key_id": key_id,
            "scope_type": scope_type,
            "scope_ref": scope_ref,
            "window_key": wkey,
            "budget_usd": budget,
            "spent_usd": round(spent, 6),
            "projected_usd": round(float(projected_cost_usd), 6),
            "remaining_usd": remaining,
            "utilization_pct": util,
            "message": message,
        }

    if status in ("revoked", "expired"):
        return _result("block", 0.0, f"Key {key_id} is {status}")

    if budget is None:
        return _result("allow", 0.0, "No budget set (unlimited)")

    spent = get_spend(key_id, window_key=wkey, now=now, conn=c)["spent_usd"]
    prospective = spent + float(projected_cost_usd)
    if prospective >= float(budget):
        return _result(
            "block",
            spent,
            f"Budget exhausted for {scope_type}:{scope_ref} — "
            f"${spent:.4f}+${float(projected_cost_usd):.4f} of ${float(budget):.2f} ({wkey})",
        )
    if spent >= float(budget) * warn_pct:
        return _result(
            "warn",
            spent,
            f"Budget {int(warn_pct * 100)}%+ used for {scope_type}:{scope_ref} "
            f"(${spent:.4f} of ${float(budget):.2f}, {wkey})",
        )
    return _result("allow", spent, "Within budget")


def resolve_active_key(
    scope_type: str,
    scope_ref: Optional[str],
    *,
    session_id: Optional[str] = None,
    conn=None,
) -> Optional[Dict[str, Any]]:
    """Find the active key for a grouping unit (most recent), or None.

    Enforcement callers hold a scope (a gameday team, an academy guild/user);
    this resolves it to the key whose budget applies. Never returns the key/hash.
    """
    own = conn is None
    c = conn or get_connection()
    ensure_schema(c)
    clauses = ["scope_type = %s", "status = 'active'"]
    params: List[Any] = [scope_type]
    if scope_ref is not None:
        clauses.append("scope_ref = %s")
        params.append(scope_ref)
    if session_id is not None:
        clauses.append("session_id = %s")
        params.append(session_id)
    row = c.execute(
        "SELECT key_id, scope_type, scope_ref, session_id, max_budget_usd, "
        "budget_window, rpm_limit, tpm_limit, status "
        "FROM llm_proxy_keys WHERE " + " AND ".join(clauses) + " ORDER BY created_at DESC",
        tuple(params),
    ).fetchone()
    if own:
        _safe_close(c)
    return dict(row) if row else None


def _safe_close(conn) -> None:
    try:
        conn.close()
    except Exception:
        pass


# --- CLI --------------------------------------------------------------------

def _print(obj: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(obj, indent=2, default=str))
    else:
        print(obj)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="ICDEV LLM proxy per-key budgets")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_check = sub.add_parser("check", help="Check budget for a key", parents=[common])
    p_check.add_argument("key_id")
    p_check.add_argument("--projected", type=float, default=0.0)

    p_spend = sub.add_parser("spend", help="Show spend for a key", parents=[common])
    p_spend.add_argument("key_id")

    p_record = sub.add_parser("record", help="Record spend for a key", parents=[common])
    p_record.add_argument("key_id")
    p_record.add_argument("--cost", type=float, required=True)
    p_record.add_argument("--input-tokens", type=int, default=0)
    p_record.add_argument("--output-tokens", type=int, default=0)

    args = parser.parse_args(argv)
    if args.cmd == "check":
        _print(check_budget(args.key_id, projected_cost_usd=args.projected), args.json)
        return 0
    if args.cmd == "spend":
        _print(get_spend(args.key_id), args.json)
        return 0
    if args.cmd == "record":
        _print(
            record_spend(
                args.key_id,
                cost_usd=args.cost,
                input_tokens=args.input_tokens,
                output_tokens=args.output_tokens,
            ),
            args.json,
        )
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
