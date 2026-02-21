#!/usr/bin/env python3
# CUI // SP-CTI
"""Write to daily log and/or memory database.

Supports SHA-256 content-hash deduplication (D179), user-scoped memory (D180),
and thinking memory type (D182).
"""

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).resolve().parent.parent.parent
MEMORY_FILE = BASE_DIR / "memory" / "MEMORY.md"
LOGS_DIR = BASE_DIR / "memory" / "logs"
DB_PATH = BASE_DIR / "data" / "memory.db"

VALID_TYPES = ("fact", "preference", "event", "insight", "task", "relationship", "thinking")
VALID_SOURCES = ("manual", "hook", "thinking", "auto")
MEMORY_SECTIONS = (
    "user_preferences",
    "key_facts",
    "active_projects",
    "important_decisions",
    "relationships",
    "lessons_learned",
)


def compute_content_hash(content):
    """Compute SHA-256 hash of content for deduplication (D179)."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def write_to_db(content, entry_type, importance, user_id=None, tenant_id=None, source="manual"):
    """Write memory entry with dedup check (D179).

    Returns:
        tuple: (entry_id, is_duplicate)
    """
    content_hash = compute_content_hash(content)
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()

    # Check for duplicate (D179)
    if user_id:
        c.execute(
            "SELECT id FROM memory_entries WHERE content_hash = ? AND user_id = ?",
            (content_hash, user_id),
        )
    else:
        c.execute(
            "SELECT id FROM memory_entries WHERE content_hash = ? AND user_id IS NULL",
            (content_hash,),
        )
    existing = c.fetchone()
    if existing:
        conn.close()
        return existing[0], True

    c.execute(
        "INSERT INTO memory_entries (content, type, importance, content_hash, user_id, tenant_id, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (content, entry_type, importance, content_hash, user_id, tenant_id, source),
    )
    conn.commit()
    entry_id = c.lastrowid
    conn.close()
    return entry_id, False


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
    parser.add_argument("--user-id", help="User ID for scoped memory (D180)")
    parser.add_argument("--tenant-id", help="Tenant ID for scoped memory (D180)")
    parser.add_argument(
        "--source",
        choices=VALID_SOURCES,
        default="manual",
        help="Capture source (D181)",
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.update_memory:
        if not args.section:
            if args.json:
                print(json.dumps({"error": "--section required with --update-memory"}))
            else:
                print("Error: --section required when using --update-memory")
            return
        update_memory_md(args.content, args.section)
        entry_id, is_dup = write_to_db(
            args.content, args.type, args.importance,
            user_id=args.user_id, tenant_id=args.tenant_id, source=args.source,
        )
        if args.json:
            print(json.dumps({
                "classification": "CUI // SP-CTI",
                "entry_id": entry_id,
                "duplicate": is_dup,
                "target": "memory_md",
                "section": args.section,
            }))
    else:
        write_to_daily_log(args.content)
        entry_id, is_dup = write_to_db(
            args.content, args.type, args.importance,
            user_id=args.user_id, tenant_id=args.tenant_id, source=args.source,
        )
        if args.json:
            print(json.dumps({
                "classification": "CUI // SP-CTI",
                "entry_id": entry_id,
                "duplicate": is_dup,
                "target": "daily_log",
            }))
        else:
            if is_dup:
                print(f"Duplicate detected (existing entry #{entry_id})")
            else:
                print(f"Written to daily log and DB (entry #{entry_id})")


if __name__ == "__main__":
    main()
