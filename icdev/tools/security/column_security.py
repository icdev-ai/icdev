#!/usr/bin/env python3
# CUI // SP-CTI
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
"""Column-Level Security for ICDEV™.

SQLite fallback (default):
    mask_columns(row_dict, policies) -> dict
    apply_column_policy(table, role, row_dict) -> dict

PostgreSQL:
    grant_column_select(table, columns, role) -> DDL
    revoke_column_select(table, columns, role) -> DDL

Strategies:
    null     -> replace value with None
    redact   -> replace value with "[REDACTED]"
    hash     -> replace value with sha256 hex digest
    truncate -> replace string value with "..."
"""

import hashlib
import json
import threading
from pathlib import Path
from typing import Any, Dict, List

logger = get_logger("security.column")

# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

_BASE_DIR = Path(__file__).resolve().parent.parent.parent
_CONFIG_PATH = _BASE_DIR / "args" / "security_config.yaml"
# Installed layout: sync_package_tree.py ships args/ under <package>/data/args/,
# so the source-checkout path above does not exist inside the wheel. Without this
# fallback every column policy silently resolved to "no policy" (unmasked).
_PACKAGED_CONFIG_PATH = _BASE_DIR / "data" / "args" / "security_config.yaml"


def _resolve_config_path() -> Path:
    """Return the security config path for the current layout (source or wheel)."""
    if _CONFIG_PATH.exists():
        return _CONFIG_PATH
    return _PACKAGED_CONFIG_PATH


#: Memoized parse of the security config, keyed on the file's identity
#: (path, mtime, size). ``_apply_column_masking`` in tools/db/storage.py calls
#: ``get_column_policies_for_role`` once per fetched ROW, so an uncached parse
#: here re-read and re-parsed ~11KB of YAML thousands of times per request —
#: enough to turn a high-row-count dashboard route into an 86-second request.
#: The stat() below is microseconds against ~14ms for the parse, and keeping
#: mtime in the key preserves hot-reload: edit the YAML and the next call
#: re-parses without a restart.
_CONFIG_CACHE: dict[str, Any] = {}

#: Guards the compare-and-swap on ``_CONFIG_CACHE``. The dashboard is threaded;
#: without this, concurrent first-touch requests each parse the file.
_CONFIG_CACHE_LOCK = threading.Lock()


def _config_cache_key(path: Path) -> tuple:
    """Identity of the config file: re-parse only when one of these changes."""
    st = path.stat()
    return (str(path), st.st_mtime_ns, st.st_size)


def reset_config_cache() -> None:
    """Drop the memoized config. For tests and for explicit reload hooks."""
    with _CONFIG_CACHE_LOCK:
        _CONFIG_CACHE.clear()


def _load_config() -> dict:
    """Return the parsed security config.

    The returned dict is the shared cached object and MUST be treated as
    read-only. Callers that hand policy data outward copy it first (see
    ``get_column_policies_for_role``); keep it that way — mutating this dict
    would corrupt masking policy for every later request in the process.
    """
    path = _resolve_config_path()
    if not path.exists():
        logger.warning("column masking: security_config.yaml not found (looked in %s, %s)",
                       _CONFIG_PATH, _PACKAGED_CONFIG_PATH)
        return {}
    try:
        key = _config_cache_key(path)
    except OSError:
        logger.exception("column masking: failed to stat %s", path)
        return {}

    cached = _CONFIG_CACHE.get("entry")
    if cached is not None and cached[0] == key:
        return cached[1]

    try:
        import yaml  # type: ignore[import-untyped]
        with open(path, "r", encoding="utf-8") as fh:
            parsed = yaml.safe_load(fh) or {}
    except Exception:
        logger.exception("column masking: failed to load %s", path)
        # Deliberately not cached: a transient read error must not pin an
        # empty (fail-open, unmasked) policy set for the life of the process.
        return {}

    with _CONFIG_CACHE_LOCK:
        _CONFIG_CACHE["entry"] = (key, parsed)
    return parsed


# ---------------------------------------------------------------------------
# Masking
# ---------------------------------------------------------------------------

def _apply_mask(value: Any, strategy: str) -> Any:
    if strategy == "null":
        return None
    if strategy == "redact":
        return "[REDACTED]"
    if strategy == "hash":
        if value is None:
            return None
        return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:16]
    if strategy == "truncate":
        if value is None:
            return None
        return "..."
    return value


def mask_columns(row_dict: dict, policies: Dict[str, str]) -> dict:
    """Apply masking strategies to a row dict.

    Args:
        row_dict: dict of column_name -> value
        policies: dict of column_name -> strategy

    Returns:
        A new dict with masked values.
    """
    result = dict(row_dict)
    for col, strategy in policies.items():
        if col in result:
            result[col] = _apply_mask(result[col], strategy)
    return result


def apply_column_policy(table: str, role: str, row_dict: dict) -> dict:
    """Lookup policies from ``args/security_config.yaml`` and apply."""
    config = _load_config()
    policies = config.get("column_policies", [])
    for policy in policies:
        if policy.get("table") == table and policy.get("role") == role:
            return mask_columns(row_dict, policy.get("columns", {}))
    return dict(row_dict)


def get_column_policies_for_role(table: str, role: str) -> Dict[str, str]:
    """Return the raw column->strategy mapping for a table+role."""
    config = _load_config()
    for policy in config.get("column_policies", []):
        if policy.get("table") == table and policy.get("role") == role:
            return dict(policy.get("columns", {}))
    return {}


# ---------------------------------------------------------------------------
# Derived-figure masking (computed payloads DB-layer masking cannot protect)
# ---------------------------------------------------------------------------

#: Roles denied ``ptw_estimate_low/high`` and ``calc_benchmark_median`` on
#: ``pg_cost_volumes`` by ``column_policies``.
PTW_DENIED_ROLES = frozenset({"reviewer", "co"})


def mask_ptw_payload(result: dict, role: str) -> dict:
    """Withhold recomputed price-to-win figures from roles denied them at rest.

    ``rate_benchmarker.ptw_analysis`` aggregates raw ``pg_competitor_awards``
    rows server-side, so DB-layer column masking cannot protect the *output*
    without corrupting the computation (a NULLed ``award_amount`` would make the
    analysis silently report "no award amounts" with a bogus recommendation).
    The derived figures are stripped from the payload here instead, keeping the
    key shape stable (values nulled + ``*_masked`` flags) so clients don't break.
    """
    if not isinstance(result, dict) or result.get("status") != "ok":
        return result
    if str(role or "") not in PTW_DENIED_ROLES:
        return result
    out = dict(result)
    if isinstance(out.get("ptw_range"), dict):
        out["ptw_range"] = dict.fromkeys(out["ptw_range"])
        out["ptw_range_masked"] = True
    if isinstance(out.get("strategies"), dict):
        out["strategies"] = {
            name: ({**s, "target": None} if isinstance(s, dict) and "target" in s else s)
            for name, s in out["strategies"].items()
        }
        out["strategies_masked"] = True
    return out


def current_role() -> str:
    """Best-effort role of the caller from the Flask security context."""
    try:
        from flask import g
        ctx = getattr(g, "security_context", None)
    except Exception:
        return ""
    return str(getattr(ctx, "role", "") or "") if ctx else ""


# ---------------------------------------------------------------------------
# PostgreSQL DDL helpers
# ---------------------------------------------------------------------------

def grant_column_select(table: str, columns: List[str], role: str) -> str:
    """Emit ``GRANT SELECT(col1, col2) ON table TO role``."""
    cols = ", ".join(columns)
    return f"GRANT SELECT ({cols}) ON {table} TO {role};"


def revoke_column_select(table: str, columns: List[str], role: str) -> str:
    """Emit ``REVOKE SELECT(col1, col2) ON table FROM role``."""
    cols = ", ".join(columns)
    return f"REVOKE SELECT ({cols}) ON {table} FROM {role};"  # nosec B608 — DDL generator, not user input


def apply_column_grants(conn=None) -> dict:
    """Execute PostgreSQL column-level GRANTs for all policies in security_config.yaml (G-06).

    Reads ``column_policies`` from ``args/security_config.yaml``. For each policy,
    executes a ``GRANT SELECT (cols) ON table TO role`` statement via the provided
    connection or a new connection from ``tools.db.storage.get_connection()``.

    SQLite silently skips column GRANT DDL (not supported); this function is a
    no-op on SQLite backends, which is intentional — masking is applied
    application-side there.

    Args:
        conn: Optional existing DB connection. If None, opens one via get_connection().

    Returns:
        Dict with keys:
          - backend (str): 'postgresql' or 'sqlite'
          - grants_applied (int): number of GRANT statements executed
          - grants_skipped (int): number skipped (DDL error or no columns)
          - details (list[dict]): per-grant outcome
    """
    import os

    backend = os.environ.get("ICDEV_STORAGE_BACKEND", "sqlite").lower()
    result: dict = {
        "backend": backend,
        "grants_applied": 0,
        "grants_skipped": 0,
        "details": [],
    }

    config = _load_config()
    policies = config.get("column_policies", [])

    if not policies:
        logger.debug("No column_policies in security_config.yaml; nothing to grant")
        return result

    owned_conn = conn is None
    if owned_conn:
        try:
            from tools.db.storage import get_connection  # type: ignore[import-untyped]
            conn = get_connection()
        except ImportError as exc:
            logger.warning("Cannot import get_connection; skipping column grants: %s", exc)
            return result

    try:
        for policy in policies:
            table = policy.get("table", "")
            role = policy.get("role", "")
            columns_cfg = policy.get("columns", {})

            if not table or not role or not columns_cfg:
                result["grants_skipped"] += 1
                result["details"].append({"table": table, "role": role, "status": "skipped_empty"})
                continue

            # columns_cfg is a dict of {column_name: masking_strategy}
            permitted_cols = list(columns_cfg.keys()) if isinstance(columns_cfg, dict) else []
            if not permitted_cols:
                result["grants_skipped"] += 1
                result["details"].append({"table": table, "role": role, "status": "skipped_no_cols"})
                continue

            ddl = grant_column_select(table, permitted_cols, role)
            try:
                conn.execute(ddl)
                result["grants_applied"] += 1
                result["details"].append({"table": table, "role": role, "columns": permitted_cols, "status": "granted"})
                logger.info("Column GRANT applied: %s role=%s cols=%s", table, role, permitted_cols)
            except Exception as exc:
                err_msg = str(exc)
                if backend == "sqlite":
                    # SQLite doesn't support column-level GRANTs — expected, skip silently
                    result["grants_skipped"] += 1
                    result["details"].append({"table": table, "role": role, "status": "sqlite_skip"})
                else:
                    logger.warning("Column GRANT failed: table=%s role=%s err=%s", table, role, err_msg)
                    result["grants_skipped"] += 1
                    result["details"].append({"table": table, "role": role, "status": "error", "error": err_msg})

        if result["grants_applied"] > 0:
            try:
                conn.commit()
            except Exception:
                pass

    finally:
        if owned_conn:
            try:
                conn.close()
            except Exception:
                pass

    logger.info(
        "apply_column_grants: backend=%s applied=%d skipped=%d",
        backend, result["grants_applied"], result["grants_skipped"],
    )
    return result


# ---------------------------------------------------------------------------
# Audit logging
# ---------------------------------------------------------------------------

def log_column_mask(
    conn,
    table: str,
    role: str,
    masked_columns: List[str],
) -> None:
    """Log an append-only column masking audit event."""
    try:
        from datetime import datetime, timezone
        conn.execute(
            """
            INSERT INTO column_mask_audit (table_name, role, masked_columns, recorded_at)
            VALUES (%s, %s, %s, %s)
            """,
            (
                table,
                role,
                json.dumps(masked_columns),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
    except Exception as exc:
        logger.debug("Could not log column mask event: %s", exc)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Column Security CLI")
    parser.add_argument("--mask", action="store_true", help="Run masking demo")
    parser.add_argument("--table", type=str, default="dashboard_users")
    parser.add_argument("--role", type=str, default="viewer")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.mask:
        row = {"id": 1, "email": "alice@example.com", "api_key_hash": "abc123", "role": "admin"}
        masked = apply_column_policy(args.table, args.role, row)
        print(json.dumps(masked, indent=2) if args.json else str(masked))


if __name__ == "__main__":
    main()
