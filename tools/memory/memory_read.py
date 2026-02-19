#!/usr/bin/env python3
# CUI // SP-CTI
"""Read all memory: MEMORY.md + recent daily logs + DB entries."""

import argparse
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta

BASE_DIR = Path(__file__).resolve().parent.parent.parent
MEMORY_FILE = BASE_DIR / "memory" / "MEMORY.md"
LOGS_DIR = BASE_DIR / "memory" / "logs"
DB_PATH = BASE_DIR / "data" / "memory.db"


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


def read_db_recent(limit=10):
    if not DB_PATH.exists():
        return []
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute(
        "SELECT content, type, importance, created_at FROM memory_entries ORDER BY created_at DESC LIMIT ?",
        (limit,),
    )
    rows = c.fetchall()
    conn.close()
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
        for content, type_, importance, created_at in db_entries:
            output.append(f"- **[{type_}]** (importance: {importance}) {content} — {created_at}")
        output.append("")

    return "\n".join(output)


def main():
    parser = argparse.ArgumentParser(description="Read all memory context")
    parser.add_argument("--format", choices=["markdown", "raw"], default="markdown")
    parser.add_argument("--days", type=int, default=2, help="Number of days of logs to include")
    parser.add_argument("--db-limit", type=int, default=10, help="Number of recent DB entries")
    args = parser.parse_args()

    memory_text = read_memory_file()
    logs = read_recent_logs(args.days)
    db_entries = read_db_recent(args.db_limit)

    if args.format == "markdown":
        print(format_markdown(memory_text, logs, db_entries))
    else:
        print(memory_text)
        for log in logs:
            print(log)


if __name__ == "__main__":
    main()
