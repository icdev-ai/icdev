#!/usr/bin/env python3
"""Write to daily log and/or memory database."""

import argparse
import sqlite3
import os
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).resolve().parent.parent.parent
MEMORY_FILE = BASE_DIR / "memory" / "MEMORY.md"
LOGS_DIR = BASE_DIR / "memory" / "logs"
DB_PATH = BASE_DIR / "data" / "memory.db"

VALID_TYPES = ("fact", "preference", "event", "insight", "task", "relationship")
MEMORY_SECTIONS = (
    "user_preferences",
    "key_facts",
    "active_projects",
    "important_decisions",
    "relationships",
    "lessons_learned",
)


def write_to_db(content, entry_type, importance):
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute(
        "INSERT INTO memory_entries (content, type, importance) VALUES (?, ?, ?)",
        (content, entry_type, importance),
    )
    conn.commit()
    entry_id = c.lastrowid
    conn.close()
    return entry_id


def write_to_daily_log(content):
    today = datetime.now().date().isoformat()
    log_file = LOGS_DIR / f"{today}.md"

    if not log_file.exists():
        log_file.write_text(
            f"# Daily Log — {today}\n\n## Session Notes\n\n",
            encoding="utf-8",
        )

    with open(log_file, "a", encoding="utf-8") as f:
        timestamp = datetime.now().strftime("%H:%M")
        f.write(f"- [{timestamp}] {content}\n")

    # Also log to DB
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute(
        "INSERT INTO daily_logs (date, content) VALUES (?, ?)",
        (today, content),
    )
    conn.commit()
    conn.close()


def update_memory_md(content, section):
    section_map = {
        "user_preferences": "## User Preferences",
        "key_facts": "## Key Facts",
        "active_projects": "## Active Projects",
        "important_decisions": "## Important Decisions",
        "relationships": "## Relationships / Contacts",
        "lessons_learned": "## Lessons Learned",
    }

    header = section_map.get(section)
    if not header:
        print(f"Error: Unknown section '{section}'. Valid: {', '.join(MEMORY_SECTIONS)}")
        return False

    if not MEMORY_FILE.exists():
        print("Error: MEMORY.md not found.")
        return False

    text = MEMORY_FILE.read_text(encoding="utf-8")

    # Find the section and append after the header line
    if header in text:
        lines = text.split("\n")
        new_lines = []
        inserted = False
        for i, line in enumerate(lines):
            new_lines.append(line)
            if line.strip() == header and not inserted:
                # Insert after any blank line following the header
                # Find next non-empty content line or section
                new_lines.append("")
                new_lines.append(f"- {content}")
                inserted = True
        if inserted:
            MEMORY_FILE.write_text("\n".join(new_lines), encoding="utf-8")
            print(f"Updated MEMORY.md section: {header}")
            return True

    print(f"Warning: Section '{header}' not found in MEMORY.md")
    return False


def main():
    parser = argparse.ArgumentParser(description="Write to memory system")
    parser.add_argument("--content", required=True, help="Content to write")
    parser.add_argument(
        "--type",
        choices=VALID_TYPES,
        default="event",
        help="Memory entry type",
    )
    parser.add_argument(
        "--importance",
        type=int,
        default=5,
        choices=range(1, 11),
        help="Importance 1-10",
    )
    parser.add_argument(
        "--update-memory",
        action="store_true",
        help="Update MEMORY.md instead of daily log",
    )
    parser.add_argument(
        "--section",
        choices=MEMORY_SECTIONS,
        help="MEMORY.md section to update (with --update-memory)",
    )
    args = parser.parse_args()

    if args.update_memory:
        if not args.section:
            print("Error: --section required when using --update-memory")
            return
        update_memory_md(args.content, args.section)
        # Also store in DB
        write_to_db(args.content, args.type, args.importance)
    else:
        # Write to daily log + DB
        write_to_daily_log(args.content)
        entry_id = write_to_db(args.content, args.type, args.importance)
        print(f"Written to daily log and DB (entry #{entry_id})")


if __name__ == "__main__":
    main()
