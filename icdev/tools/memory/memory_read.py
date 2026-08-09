#!/usr/bin/env python3
# CUI // SP-CTI
"""Read all memory: MEMORY.md + recent daily logs + DB entries."""

import argparse
import sqlite3
import sys
from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.db.storage import StorageConnection, get_connection
from pathlib import Path
from datetime import datetime, timedelta

BASE_DIR = Path(__file__).resolve().parent.parent.parent
MEMORY_FILE = BASE_DIR / "memory" / "MEMORY.md"
LOGS_DIR = BASE_DIR / "memory" / "logs"

# Overridable in tests via monkeypatch
DB_PATH = None

# Ordinal classification levels (lowest to highest)
_CLASSIFICATION_ORDER = {
    "PUBLIC": 0,
    "CUI": 1,
    "SECRET": 2,
    "TOP SECRET": 3,
    "TOP SECRET//SCI": 4,
}


def _classification_level(label: str) -> int:
    return _CLASSIFICATION_ORDER.get((label or "CUI").upper().strip(), 1)


def _compartments_allowed(entry_compartments: str, user_compartments: list[str] | None) -> bool:
    """Return True if entry's compartments are a subset of user's compartments."""
    if not entry_compartments or entry_compartments.strip() == "":
        return True
    if user_compartments is None:
        return False
    entry_set = {c.strip().upper() for c in entry_compartments.split(",") if c.strip()}
    user_set = {c.strip().upper() for c in user_compartments if c.strip()}
    return entry_set <= user_set


def _connect():
    if DB_PATH is not None:
        # DB_PATH is a stand-in for get_connection(), which returns a
        # StorageConnection that rewrites PostgreSQL ``%s`` placeholders to
        # ``?`` for SQLite. Returning the bare sqlite3 connection made the seam
        # lie: every parameterised statement raised ``near "%": syntax error``,
        # and callers that swallow write failures reported a no-op as success.
        return StorageConnection(sqlite3.connect(str(DB_PATH)), "sqlite")
    return get_connection()


def read_memory_file():
    if MEMORY_FILE.exists():
        return MEMORY_FILE.read_text(encoding="utf-8")
    return "*(MEMORY.md not found)*"


def read_recent_logs(days=2):
    logs = []
    today = datetime.now().date()
    for i in range(days):
        date = today - timedelta(days=i)
        log_file = LOGS_DIR / f"{date.isoformat()}.md"
        if log_file.exists():
            logs.append(log_file.read_text(encoding="utf-8"))
    return logs


def read_db_recent(limit=10, user_id=None, tenant_id=None, clearance=None, compartments=None):
    conn = _connect()
    c = conn.cursor()

    # D6: author placeholders as %s (PostgreSQL — the production backend via
    # get_connection); translate_sql rewrites %s -> ? for the SQLite fallback. The
    # previous bare ? tripped translate_sql's "use %%s" warning on every read (this
    # is the Session Start Protocol command).
    sql = "SELECT content, type, importance, created_at, classification, compartment FROM memory_entries WHERE 1=1"
    params = []
    if user_id:
        sql += " AND (user_id = %s OR user_id IS NULL)"
        params.append(user_id)
    if tenant_id:
        sql += " AND (tenant_id = %s OR tenant_id IS NULL)"
        params.append(tenant_id)
    sql += " ORDER BY created_at DESC LIMIT %s"
    params.append(limit)

    c.execute(sql, params)
    rows = c.fetchall()
    conn.close()

    # Security-context filtering in Python (compartments require parsing)
    if clearance is not None:
        user_level = _classification_level(clearance)
        filtered = []
        for row in rows:
            entry_class = row[4] if len(row) > 4 else "CUI"
            entry_compartment = row[5] if len(row) > 5 else ""
            if _classification_level(entry_class) <= user_level and _compartments_allowed(entry_compartment, compartments):
                filtered.append(row)
        rows = filtered

    return rows


def format_markdown(memory_text, logs, db_entries):
    output = []
    output.append("# Memory Context\n")
    output.append("## Long-Term Memory\n")
    output.append(memory_text)
    output.append("\n---\n")

    if logs:
        output.append("## Recent Logs\n")
        for log in logs:
            output.append(log)
            output.append("\n---\n")

    if db_entries:
        output.append("## Recent DB Entries\n")
        # D6: read_db_recent selects SIX columns (content, type, importance,
        # created_at, classification, compartment); unpacking four raised
        # ValueError: too many values to unpack. Index the display columns and
        # ignore the trailing security-context columns.
        for row in db_entries:
            content, type_, importance, created_at = row[0], row[1], row[2], row[3]
            output.append(f"- **[{type_}]** (importance: {importance}) {content} — {created_at}")
        output.append("")

    return "\n".join(output)


def main():
    parser = argparse.ArgumentParser(description="Read all memory context")
    parser.add_argument("--format", choices=["markdown", "raw"], default="markdown")
    parser.add_argument("--days", type=int, default=2, help="Number of days of logs to include")
    parser.add_argument("--db-limit", type=int, default=10, help="Number of recent DB entries")
    parser.add_argument("--user-id", help="Filter by user ID (D180)")
    parser.add_argument("--tenant-id", help="Filter by tenant ID (D180)")
    parser.add_argument(
        "--clearance",
        default=None,
        help="User security clearance for classification filtering",
    )
    parser.add_argument(
        "--compartments",
        default=None,
        help="Comma-separated list of user compartments",
    )
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    args = parser.parse_args()

    comps = None
    if args.compartments:
        comps = [c.strip() for c in args.compartments.split(",") if c.strip()]

    memory_text = read_memory_file()
    logs = read_recent_logs(args.days)
    db_entries = read_db_recent(
        args.db_limit,
        user_id=args.user_id,
        tenant_id=args.tenant_id,
        clearance=args.clearance,
        compartments=comps,
    )

    if args.format == "markdown":
        print(format_markdown(memory_text, logs, db_entries))
    else:
        print(memory_text)
        for log in logs:
            print(log)


if __name__ == "__main__":
    main()
