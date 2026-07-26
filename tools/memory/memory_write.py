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
from tools.db.storage import get_connection
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Overridable in tests to redirect to a temp SQLite DB.
DB_PATH = None


def _get_conn():
    if DB_PATH is not None:
        return sqlite3.connect(str(DB_PATH))
    return get_connection()
MEMORY_FILE = BASE_DIR / "memory" / "MEMORY.md"
LOGS_DIR = BASE_DIR / "memory" / "logs"
VALID_TYPES = ("fact", "preference", "event", "insight", "task", "relationship", "thinking")
VALID_SOURCES = ("manual", "hook", "thinking", "auto")
VALID_TIERS = ("procedural", "episodic", "semantic")
MEMORY_SECTIONS = (
    "user_preferences",
    "key_facts",
    "active_projects",
    "important_decisions",
    "relationships",
    "lessons_learned",
)


def _normalize(content: str) -> str:
    """Normalize content for fingerprinting: lowercase, strip, collapse whitespace."""
    return " ".join(content.lower().strip().split())


def compute_content_hash(content: str) -> str:
    """Compute SHA-256 hash of normalized content for deduplication (D179)."""
    return hashlib.sha256(_normalize(content).encode("utf-8")).hexdigest()


def write_to_db(
    content, entry_type, importance=5, user_id=None, tenant_id=None, source="manual",
    classification="CUI", compartment="", tier="episodic", session_ref=None,
    trace_id=None,
):
    """Write memory entry with SHA-256 dedup upsert (D179).

    Normalizes content before hashing so case/whitespace variants deduplicate.

    Args:
        tier: Memory tier — 'episodic' (dated events), 'semantic' (durable facts),
              'procedural' (instructions/skills). Added by migration 226.
        session_ref: agent_loop_sessions.session_id that produced this entry.
        trace_id: OTel span / agent_loop correlation ID (migration 229).

    Returns:
        dict: {id, status ('inserted'|'duplicate_merged'), fingerprint}
    """
    fingerprint = compute_content_hash(content)
    conn = _get_conn()
    c = conn.cursor()

    # Check for duplicate by normalized fingerprint (D179)
    if user_id:
        c.execute(
            "SELECT id FROM memory_entries WHERE content_hash = %s AND user_id = %s",
            (fingerprint, user_id),
        )
    else:
        c.execute(
            "SELECT id FROM memory_entries WHERE content_hash = %s AND user_id IS NULL",
            (fingerprint,),
        )
    existing = c.fetchone()
    if existing:
        # Merge: bump updated_at to record the re-encounter.
        # D7: decay_weight is NOT actively managed — there is no "hybrid_search decay
        # pass" (grep confirms none in hybrid_search.py / time_decay.py). Effective
        # relevance decay is computed on the fly from created_at (tools/memory/
        # time_decay.py), so this column stays at its insert-time 1.0 and is not read
        # by retrieval. Its only writer is reset_decay() below, which has no callers.
        c.execute(
            "UPDATE memory_entries SET updated_at = datetime('now') WHERE id = %s",
            (existing[0],),
        )
        conn.commit()
        conn.close()
        return {"id": existing[0], "status": "duplicate_merged", "fingerprint": fingerprint}

    _tier = tier if tier in VALID_TIERS else "episodic"
    # tier, session_ref columns added by migration 226; trace_id by migration 229.
    # Fall back gracefully when migration is not yet applied.
    try:
        c.execute(
            "INSERT INTO memory_entries "
            "(content, type, importance, content_hash, user_id, tenant_id, source, "
            "decay_weight, classification, compartment, tier, session_ref, trace_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (content, entry_type, importance, fingerprint, user_id, tenant_id, source,
             1.0, classification, compartment, _tier, session_ref, trace_id),
        )
    except Exception:
        try:
            # trace_id column missing (migration 229 not yet applied)
            c.execute(
                "INSERT INTO memory_entries "
                "(content, type, importance, content_hash, user_id, tenant_id, source, "
                "decay_weight, classification, compartment, tier, session_ref) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (content, entry_type, importance, fingerprint, user_id, tenant_id, source,
                 1.0, classification, compartment, _tier, session_ref),
            )
        except Exception:
            # Migration 226 also not yet applied
            c.execute(
                "INSERT INTO memory_entries "
                "(content, type, importance, content_hash, user_id, tenant_id, source, "
                "decay_weight, classification, compartment) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (content, entry_type, importance, fingerprint, user_id, tenant_id, source,
                 1.0, classification, compartment),
            )
    returning = c.fetchone()
    entry_id = returning[0] if returning else None
    conn.commit()
    conn.close()
    return {"id": entry_id, "status": "inserted", "fingerprint": fingerprint}


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
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        "INSERT INTO daily_logs (date, content) VALUES (%s, %s)",
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


def update_crossrefs(new_slug: str, new_content: str, memory_dir: Path | None = None) -> list[str]:
    """Add [[new_slug]] back-links to existing topic files that share keywords.

    Implements the Karpathy LLM Wiki "update 10-15 related pages" ingest step:
    when a new memory file is written, scan existing files for topic overlap and
    append a cross-reference link so the wiki graph stays connected.

    Args:
        new_slug: The slug (filename without .md) of the newly written file.
        new_content: Full text of the new file (used to extract keywords).
        memory_dir: Directory to scan. Defaults to the Claude Code auto-memory
                    derived from USERPROFILE/.claude/projects/<slug>/memory.

    Returns:
        List of file paths that were updated with a back-link.
    """
    import os
    import re

    if memory_dir is None:
        userprofile = Path(os.environ.get("USERPROFILE", Path.home()))
        project_slug = (
            str(BASE_DIR)
            .replace("\\", "-")
            .replace("/", "-")
            .replace(":", "-")
            .lstrip("-")
        )
        memory_dir = userprofile / ".claude" / "projects" / project_slug / "memory"

    if not memory_dir.is_dir():
        return []

    # Extract keywords: lowercase words ≥5 chars from the new file's title + body
    _stop = {"about", "their", "which", "would", "could", "should", "there", "where",
              "after", "before", "while", "since", "these", "those", "other", "using",
              "added", "fixed", "built", "ships", "every", "still", "never", "always"}
    kw_raw = re.findall(r"[a-zA-Z_]{5,}", new_content)
    keywords = {w.lower() for w in kw_raw if w.lower() not in _stop} - _stop

    if not keywords:
        return []

    link_line = f"\n[[{new_slug}]]"
    link_pattern = re.compile(r"\[\[" + re.escape(new_slug) + r"\]\]")
    updated: list[str] = []

    for f in sorted(memory_dir.glob("*.md")):
        if f.stem == new_slug or f.name == "MEMORY.md":
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        # Skip if already cross-linked
        if link_pattern.search(text):
            continue

        # Check keyword overlap: ≥2 shared keywords with the existing file's text
        existing_words = {w.lower() for w in re.findall(r"[a-zA-Z_]{5,}", text)}
        if len(keywords & existing_words) < 2:
            continue

        # Append back-link before the final newline
        f.write_text(text.rstrip() + link_line + "\n", encoding="utf-8")
        updated.append(str(f))

    return updated


def reset_decay(memory_id: str, conn=None) -> None:
    """Reset a memory's decay weight to 1.0.

    D7: currently has NO callers — retrieval strengthening is not wired, and actual
    decay is computed on the fly from created_at (time_decay.py), so decay_weight is
    a dead column. Retained (not dropped) to avoid a migration; wire this from the
    retrieval path if strengthening is ever implemented, or drop the column then.
    """
    close = conn is None
    if conn is None:
        from tools.db.storage import get_connection
        conn = get_connection().__enter__()
    conn.execute("UPDATE memory_entries SET decay_weight=1.0 WHERE id=%s", (memory_id,))
    if close:
        conn.commit()


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
    parser.add_argument(
        "--classification",
        default="CUI",
        help="Data classification (PUBLIC, CUI, SECRET, TOP SECRET, TOP SECRET//SCI)",
    )
    parser.add_argument(
        "--compartment",
        default="",
        help="Compartment / SCI tag (comma-separated for multiple)",
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
        result = write_to_db(
            args.content,
            args.type,
            args.importance,
            user_id=args.user_id,
            tenant_id=args.tenant_id,
            source=args.source,
            classification=args.classification,
            compartment=args.compartment,
        )
        if args.json:
            print(
                json.dumps(
                    {
                        "classification": "CUI // SP-CTI",
                        "entry_id": result["id"],
                        "status": result["status"],
                        "fingerprint": result["fingerprint"],
                        "target": "memory_md",
                        "section": args.section,
                    }
                )
            )
    else:
        write_to_daily_log(args.content)
        result = write_to_db(
            args.content,
            args.type,
            args.importance,
            user_id=args.user_id,
            tenant_id=args.tenant_id,
            source=args.source,
            classification=args.classification,
            compartment=args.compartment,
        )
        if args.json:
            print(
                json.dumps(
                    {
                        "classification": "CUI // SP-CTI",
                        "entry_id": result["id"],
                        "status": result["status"],
                        "fingerprint": result["fingerprint"],
                        "target": "daily_log",
                    }
                )
            )
        else:
            if result["status"] == "duplicate_merged":
                print(f"Duplicate detected (existing entry #{result['id']})")
            else:
                print(f"Written to daily log and DB (entry #{result['id']})")


if __name__ == "__main__":
    main()
