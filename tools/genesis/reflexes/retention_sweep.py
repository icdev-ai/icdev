# CUI // SP-CTI
"""Genesis Reflex — Retention Sweep (config-driven archival/prune, crx-db-03).

High-churn operational tables (agent run traces, kanban verification records,
inter-agent task history, hook events, ...) grow without bound and degrade the
hot query path. This reflex applies a **config-driven** retention policy from
``args/retention_policies.yaml`` mapping each table to
``{keep_days, strategy: prune|archive, timestamp_column, id_column, archive_dest}``.

HARD SAFETY RULES (enforced here, not merely by config)
-------------------------------------------------------
* **Append-only / audit tables are NEVER pruned.** The authoritative
  ``APPEND_ONLY_TABLES`` list lives in ``.claude/hooks/pre_tool_use.py``; this
  reflex parses it at runtime (single source of truth). A ``prune`` strategy
  declared against an append-only table is force-downgraded to ``archive``
  (copy-to-cold twin; the hot table is never reduced) so NIST AU retention
  minimums always hold. If the append-only set cannot be read, the reflex
  **fails closed** — every table is treated as archive-only and nothing is
  ever deleted.
* **dry_run is the DEFAULT.** With ``dry_run`` true no rows are touched and no
  log rows are written — the reflex only reports what it WOULD do.
* **Per-run caps.** ``max_rows_per_table`` bounds how many rows a single run
  drains, so a huge backlog can never wedge the daemon in one transaction.
* **Every action is logged** to the append-only ``retention_action_log`` table.

Excluded subsystems (self-managed retention) are listed in the config
``excluded`` block: RAG (``tools/rag/retention_manager.py``), the observability
archive-then-prune reflex, and PDC / digital-twin snapshot retention.
"""
from __future__ import annotations

IMPLEMENTATION_STATUS = "full"

import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Set

import yaml

from tools.db.storage import get_connection
from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

CADENCE_HOURS: int = 24

_DEFAULT_MAX_ROWS = 5000
_DEFAULT_KEEP_DAYS = 90
_DEFAULT_TS_COL = "created_at"
_DEFAULT_ID_COL = "id"
_ACTION_LOG = "retention_action_log"


# ---------------------------------------------------------------------------
# Path / config helpers (repo root resolved via __file__, never cwd)
# ---------------------------------------------------------------------------
def _repo_root() -> Path:
    # tools/genesis/reflexes/retention_sweep.py -> parents[3] == repo root
    return Path(__file__).resolve().parents[3]


def _config_path() -> Path:
    return _repo_root() / "args" / "retention_policies.yaml"


def _hook_path() -> Path:
    return _repo_root() / ".claude" / "hooks" / "pre_tool_use.py"


def load_config(path: Optional[str] = None) -> Dict[str, Any]:
    """Return the ``retention`` block from the policy YAML (tolerant of absence)."""
    p = Path(path) if path else _config_path()
    try:
        with open(p, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        return data.get("retention", {}) or {}
    except Exception:
        return {}


def load_append_only_tables(path: Optional[str] = None) -> Optional[Set[str]]:
    """Parse ``APPEND_ONLY_TABLES`` from the pre_tool_use hook (single source of truth).

    Returns a set of table names, or ``None`` if the list cannot be read. A
    ``None`` return is the fail-closed signal: callers must then treat EVERY
    table as append-only (archive-only, never delete).
    """
    p = Path(path) if path else _hook_path()
    try:
        src = p.read_text(encoding="utf-8")
        m = re.search(r"APPEND_ONLY_TABLES\s*=\s*\[(.*?)\n\s*\]", src, re.S)
        if not m:
            return None
        names = set(re.findall(r'"([A-Za-z0-9_]+)"', m.group(1)))
        return names or None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Low-level, cross-dialect (SQLite + PostgreSQL) primitives
# ---------------------------------------------------------------------------
def _count_eligible(conn, table: str, ts_col: str, cutoff_iso: str) -> int:
    row = conn.execute(
        f"SELECT COUNT(*) FROM {table} WHERE {ts_col} < ?",  # nosec B608
        (cutoff_iso,),
    ).fetchone()
    return int(row[0]) if row else 0


def _ensure_action_log(conn) -> None:
    """Create the append-only retention_action_log table if absent (idempotent)."""
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_ACTION_LOG} (
            id              TEXT PRIMARY KEY,
            table_name      TEXT NOT NULL,
            strategy        TEXT NOT NULL,
            forced_archive  INTEGER NOT NULL DEFAULT 0,
            keep_days       INTEGER NOT NULL,
            cutoff          TEXT NOT NULL,
            rows_matched    INTEGER NOT NULL DEFAULT 0,
            rows_affected   INTEGER NOT NULL DEFAULT 0,
            dry_run         INTEGER NOT NULL DEFAULT 0,
            archive_dest    TEXT,
            tenant_id       TEXT,
            classification  TEXT NOT NULL DEFAULT 'CUI',
            created_at      TEXT NOT NULL
        )
        """  # nosec B608
    )


def _log_action(conn, rec: Dict[str, Any]) -> None:
    conn.execute(
        f"""
        INSERT INTO {_ACTION_LOG}
            (id, table_name, strategy, forced_archive, keep_days, cutoff,
             rows_matched, rows_affected, dry_run, archive_dest,
             tenant_id, classification, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,  # nosec B608
        (
            uuid.uuid4().hex,
            rec["table_name"],
            rec["strategy"],
            1 if rec.get("forced_archive") else 0,
            int(rec["keep_days"]),
            rec["cutoff"],
            int(rec.get("rows_matched", 0)),
            int(rec.get("rows_affected", 0)),
            1 if rec.get("dry_run") else 0,
            rec.get("archive_dest"),
            rec.get("tenant_id"),
            rec.get("classification", "CUI"),
            datetime.now(timezone.utc).isoformat(),
        ),
    )


def _prune(conn, table: str, ts_col: str, id_col: str, cutoff_iso: str, cap: int) -> int:
    """Delete up to ``cap`` oldest rows older than cutoff. Returns rows deleted."""
    conn.execute(
        f"DELETE FROM {table} WHERE {id_col} IN ("  # nosec B608
        f"SELECT {id_col} FROM {table} WHERE {ts_col} < ? "  # nosec B608
        f"ORDER BY {ts_col} LIMIT {int(cap)})",  # nosec B608
        (cutoff_iso,),
    )
    row = conn.execute(
        f"SELECT COUNT(*) FROM {table} WHERE {ts_col} < ?",  # nosec B608
        (cutoff_iso,),
    ).fetchone()
    # rows_affected is inferred by re-counting remaining eligible vs. matched by
    # the caller; here we return the delete rowcount when the driver supplies it.
    return int(row[0]) if row else 0


def _archive_copy(
    conn, table: str, dest: str, ts_col: str, id_col: str, cutoff_iso: str, cap: int
) -> int:
    """Copy up to ``cap`` expired rows into the cold twin WITHOUT deleting them.

    Idempotent: only rows whose ``id_col`` is not already present in the archive
    are copied. The hot (append-only) table is NEVER reduced. Returns rows copied.
    """
    # Idempotent cold twin with the same schema (empty copy). Both SQLite and
    # PostgreSQL support CREATE TABLE ... AS SELECT ... WHERE 1=0.
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS {dest} AS SELECT * FROM {table} WHERE 1=0"  # nosec B608
    )
    before = conn.execute(f"SELECT COUNT(*) FROM {dest}").fetchone()[0]  # nosec B608
    conn.execute(
        f"INSERT INTO {dest} SELECT * FROM {table} WHERE {id_col} IN ("  # nosec B608
        f"SELECT {id_col} FROM {table} "  # nosec B608
        f"WHERE {ts_col} < ? AND {id_col} NOT IN (SELECT {id_col} FROM {dest}) "  # nosec B608
        f"ORDER BY {ts_col} LIMIT {int(cap)})",  # nosec B608
        (cutoff_iso,),
    )
    after = conn.execute(f"SELECT COUNT(*) FROM {dest}").fetchone()[0]  # nosec B608
    return int(after) - int(before)


# ---------------------------------------------------------------------------
# Per-table processing (pure — operates on any DBAPI connection)
# ---------------------------------------------------------------------------
def process_table(
    conn,
    table: str,
    policy: Dict[str, Any],
    append_only: Optional[Set[str]],
    max_rows: int,
    dry_run: bool,
    now: datetime,
    *,
    log: bool = True,
) -> Dict[str, Any]:
    """Apply one table's retention policy. Returns a per-table report dict."""
    strategy = str(policy.get("strategy", "prune")).lower()
    keep_days = int(policy.get("keep_days", _DEFAULT_KEEP_DAYS))
    ts_col = str(policy.get("timestamp_column", _DEFAULT_TS_COL))
    id_col = str(policy.get("id_column", _DEFAULT_ID_COL))
    archive_dest = policy.get("archive_dest") or f"{table}_archive"
    cutoff_iso = (now - timedelta(days=keep_days)).isoformat()

    report: Dict[str, Any] = {
        "strategy": strategy,
        "keep_days": keep_days,
        "cutoff": cutoff_iso,
        "forced_archive": False,
        "rows_matched": 0,
        "rows_affected": 0,
        "dry_run": dry_run,
        "missing": False,
        "error": None,
    }

    # HARD RULE: append-only tables are never pruned. Fail closed when the
    # append-only set is unknown (append_only is None) — treat as archive-only.
    is_append_only = (append_only is None) or (table in append_only)
    if strategy == "prune" and is_append_only:
        strategy = "archive"
        report["strategy"] = "archive"
        report["forced_archive"] = True

    try:
        report["rows_matched"] = _count_eligible(conn, table, ts_col, cutoff_iso)
    except Exception as exc:  # table absent / column missing — skip gracefully
        report["missing"] = True
        report["error"] = str(exc)
        return report

    if dry_run:
        # Report only. No rows touched, no log rows written.
        return report

    try:
        if strategy == "prune":
            matched = report["rows_matched"]
            remaining = _prune(conn, table, ts_col, id_col, cutoff_iso, max_rows)
            report["rows_affected"] = max(0, matched - remaining)
        else:  # archive
            report["archive_dest"] = archive_dest
            report["rows_affected"] = _archive_copy(
                conn, table, archive_dest, ts_col, id_col, cutoff_iso, max_rows
            )

        if log:
            _ensure_action_log(conn)
            _log_action(
                conn,
                {
                    "table_name": table,
                    "strategy": strategy,
                    "forced_archive": report["forced_archive"],
                    "keep_days": keep_days,
                    "cutoff": cutoff_iso,
                    "rows_matched": report["rows_matched"],
                    "rows_affected": report["rows_affected"],
                    "dry_run": False,
                    "archive_dest": report.get("archive_dest"),
                },
            )
    except Exception as exc:
        report["error"] = str(exc)
        logger.error("retention_sweep: %s (%s) failed: %s", table, strategy, exc)

    return report


def apply_retention(
    conn,
    policies: Dict[str, Any],
    append_only: Optional[Set[str]],
    *,
    excluded: Optional[Set[str]] = None,
    max_rows: int = _DEFAULT_MAX_ROWS,
    dry_run: bool = True,
    now: Optional[datetime] = None,
    log: bool = True,
) -> Dict[str, Any]:
    """Apply retention across all configured policies. Pure/testable engine.

    Commits once at the end (no-op for connections without commit()).
    """
    now = now or datetime.now(timezone.utc)
    excluded = excluded or set()
    result: Dict[str, Any] = {
        "dry_run": dry_run,
        "max_rows_per_table": max_rows,
        "append_only_loaded": append_only is not None,
        "tables": {},
        "total_affected": 0,
        "errors": [],
    }
    for table, policy in (policies or {}).items():
        if table in excluded:
            result["tables"][table] = {"skipped": "excluded"}
            continue
        rep = process_table(
            conn, table, policy or {}, append_only, max_rows, dry_run, now, log=log
        )
        result["tables"][table] = rep
        result["total_affected"] += int(rep.get("rows_affected", 0))
        if rep.get("error"):
            result["errors"].append(f"{table}: {rep['error']}")

    if not dry_run:
        try:
            conn.commit()
        except Exception:
            pass
    return result


# ---------------------------------------------------------------------------
# Genesis reflex entry point
# ---------------------------------------------------------------------------
def run(ctx: Dict[str, Any], conn=None) -> Dict[str, Any]:
    """Config-driven retention sweep across the configured operational tables.

    ``ctx`` honours ``dry_run`` (overrides the config default, which is TRUE).
    """
    cfg = load_config()
    policies = cfg.get("policies", {}) or {}
    excluded = set(cfg.get("excluded", []) or [])
    max_rows = int(cfg.get("max_rows_per_table", _DEFAULT_MAX_ROWS))
    # dry_run precedence: explicit ctx override > config > safe default (True)
    if "dry_run" in ctx:
        dry_run = bool(ctx["dry_run"])
    else:
        dry_run = bool(cfg.get("dry_run", True))

    result: Dict[str, Any] = {
        "cadence_hours": CADENCE_HOURS,
        "dry_run": dry_run,
        "enabled": bool(cfg.get("enabled", True)),
        "status": "ok",
        "total_affected": 0,
        "tables": {},
        "errors": [],
    }

    if not result["enabled"] or not policies:
        result["success"] = True
        result["metric_value"] = 0.0
        result["details"] = {"status": "ok", "reason": "disabled or no policies"}
        return result

    append_only = load_append_only_tables()

    db = None
    try:
        db = get_connection()
        # rls-bypass: retention must operate across ALL classifications/tenants.
        # The sweep runs in the Genesis daemon OUTSIDE any Flask request context,
        # so no security context auto-attaches; we clear it explicitly so an
        # in-request invocation cannot scope DELETE/INSERT to a subset of rows.
        if hasattr(db, "set_security_context"):
            db.set_security_context(None)  # rls-bypass: runs in the Genesis daemon outside any Flask request; a caller-scoped predicate would silently leave other-classification rows unprocessed

        engine = apply_retention(
            db,
            policies,
            append_only,
            excluded=excluded,
            max_rows=max_rows,
            dry_run=dry_run,
            log=True,
        )
        result["tables"] = engine["tables"]
        result["total_affected"] = engine["total_affected"]
        result["errors"] = engine["errors"]
        result["append_only_loaded"] = engine["append_only_loaded"]
        if engine["errors"]:
            result["status"] = "partial"
    except Exception as exc:
        logger.error("retention_sweep reflex error: %s", exc)
        result["status"] = "error"
        result["errors"].append(str(exc))
    finally:
        if db is not None:
            try:
                db.rollback()
            except Exception:
                pass
            try:
                db.close()
            except Exception:
                pass

    result["success"] = result["status"] in ("ok", "partial")
    result["metric_value"] = float(result["total_affected"])
    result["details"] = {
        "total_affected": result["total_affected"],
        "dry_run": dry_run,
        "append_only_loaded": result.get("append_only_loaded", False),
        "tables": {
            t: (r.get("rows_affected", 0) if isinstance(r, dict) else 0)
            for t, r in result["tables"].items()
        },
        "status": result["status"],
        "errors": result["errors"],
    }
    return result


if __name__ == "__main__":
    # Load THIS repo's .env so a direct CLI run uses the same board/PG config as the
    # GenesisDaemon. override=True: a pip-installed ICDEV in site-packages may have
    # already loaded a different checkout's .env at import. Repo root via __file__, not cwd.
    try:
        from pathlib import Path as _EnvPath
        from dotenv import load_dotenv as _load_dotenv
        _load_dotenv(_EnvPath(__file__).resolve().parents[3] / ".env", override=True)
    except ImportError:
        pass
    import json as _json

    print(_json.dumps(run({}), indent=2))
