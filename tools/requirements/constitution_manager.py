#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""Project constitution manager -- immutable principles that gate specs and plans.

Constitutions are per-project principles (security, compliance, architecture, quality,
operations) that every specification must satisfy.  Default DoD principles can be
loaded from context/requirements/default_constitutions.json.

ADR D158: Constitutions stored in DB per-project with defaults from JSON.

Usage:
    python tools/requirements/constitution_manager.py --project-id proj-123 --add \
        --principle "All APIs require CAC" --category security --json
    python tools/requirements/constitution_manager.py --project-id proj-123 --list --json
    python tools/requirements/constitution_manager.py --project-id proj-123 --load-defaults --json
    python tools/requirements/constitution_manager.py --project-id proj-123 --validate \
        --spec-file specs/foo.md --json
    python tools/requirements/constitution_manager.py --remove --principle-id con-abc123 --json
"""

import argparse
import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

VALID_CATEGORIES = frozenset({
    "security", "compliance", "architecture", "quality", "operations", "general",
})

# Graceful import of audit logger
try:
    from tools.audit.audit_logger import log_event
    _HAS_AUDIT = True
except ImportError:
    _HAS_AUDIT = False

    def log_event(**kwargs) -> int:  # type: ignore[misc]
        return -1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_connection(db_path=None):
    """Get database connection with dict-like row access."""
    path = db_path or DB_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"Database not found: {path}\nRun: python tools/db/init_icdev_db.py"
        )
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _generate_id(prefix="con"):
    """Generate a unique ID with prefix."""
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _load_defaults_file() -> list:
    """Load default principles from context/requirements/default_constitutions.json.

    Returns the ``default_principles`` list, or an empty list on any failure.
    """
    path = BASE_DIR / "context" / "requirements" / "default_constitutions.json"
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data.get("default_principles", [])
    except (json.JSONDecodeError, OSError):
        return []


def _load_config() -> dict:
    """Load constitution config from args/spec_config.yaml (optional)."""
    config_path = BASE_DIR / "args" / "spec_config.yaml"
    if config_path.exists():
        try:
            import yaml  # optional -- air-gap safe fallback below
            with open(config_path, "r", encoding="utf-8") as fh:
                cfg = yaml.safe_load(fh)
            return cfg.get("constitution", {})
        except ImportError:
            pass
    return {}


def _parse_spec_sections(spec_path: Path) -> dict:
    """Parse a Markdown spec into {heading: body_text} pairs.

    Splits on ``## `` headers.  The text before the first ``## `` is stored
    under the key ``"_preamble"``.
    """
    content = spec_path.read_text(encoding="utf-8")
    sections: dict = {}
    current_heading = "_preamble"
    current_lines: list = []

    for line in content.splitlines():
        if line.startswith("## "):
            # Flush previous section
            sections[current_heading] = "\n".join(current_lines).strip()
            current_heading = line[3:].strip()
            current_lines = []
        else:
            current_lines.append(line)

    # Flush last section
    sections[current_heading] = "\n".join(current_lines).strip()
    return sections


# ---------------------------------------------------------------------------
# Core API
# ---------------------------------------------------------------------------

def add_principle(
    project_id: str,
    principle_text: str,
    category: str = "general",
    priority: int = 2,
    created_by: str = "system",
    db_path=None,
) -> dict:
    """Insert a new constitution principle for a project.

    Args:
        project_id: Target project identifier.
        principle_text: The principle statement.
        category: One of VALID_CATEGORIES.
        priority: 1 (critical) - 3 (advisory).  Default 2.
        created_by: Actor creating the principle.
        db_path: Optional DB path override.

    Returns:
        Dict describing the created principle.
    """
    if category not in VALID_CATEGORIES:
        raise ValueError(
            f"Invalid category '{category}'. Must be one of: {sorted(VALID_CATEGORIES)}"
        )
    if priority not in (1, 2, 3):
        raise ValueError("Priority must be 1 (critical), 2 (important), or 3 (advisory).")

    conn = _get_connection(db_path)
    principle_id = _generate_id("con")
    now = datetime.utcnow().isoformat()

    conn.execute(
        """INSERT INTO project_constitutions
               (id, project_id, principle_text, category, priority, is_active, created_by, created_at)
           VALUES (?, ?, ?, ?, ?, 1, ?, ?)""",
        (principle_id, project_id, principle_text, category, priority, created_by, now),
    )
    conn.commit()
    conn.close()

    if _HAS_AUDIT:
        log_event(
            event_type="constitution_added",
            actor=created_by,
            action=f"Added constitution principle '{principle_id}' to project {project_id}",
            project_id=project_id,
            details={"principle_id": principle_id, "category": category, "priority": priority},
        )

    return {
        "status": "ok",
        "principle_id": principle_id,
        "project_id": project_id,
        "principle_text": principle_text,
        "category": category,
        "priority": priority,
        "is_active": True,
        "created_by": created_by,
        "created_at": now,
    }


def list_principles(
    project_id: str,
    category: str = None,
    active_only: bool = True,
    db_path=None,
) -> list:
    """Return all constitution principles for a project.

    Args:
        project_id: Target project identifier.
        category: Optional filter by category.
        active_only: If True, only return active (non-deleted) principles.
        db_path: Optional DB path override.

    Returns:
        List of principle dicts.
    """
    conn = _get_connection(db_path)
    query = "SELECT * FROM project_constitutions WHERE project_id = ?"
    params: list = [project_id]

    if active_only:
        query += " AND is_active = 1"
    if category:
        if category not in VALID_CATEGORIES:
            conn.close()
            raise ValueError(
                f"Invalid category '{category}'. Must be one of: {sorted(VALID_CATEGORIES)}"
            )
        query += " AND category = ?"
        params.append(category)

    query += " ORDER BY priority ASC, created_at ASC"
    rows = conn.execute(query, params).fetchall()
    conn.close()

    return [dict(r) for r in rows]


def remove_principle(principle_id: str, db_path=None) -> dict:
    """Soft-delete a constitution principle (set is_active = 0).

    Per the append-only audit pattern, we never hard-delete rows.

    Args:
        principle_id: ID of the principle to deactivate.
        db_path: Optional DB path override.

    Returns:
        Confirmation dict.
    """
    conn = _get_connection(db_path)
    row = conn.execute(
        "SELECT * FROM project_constitutions WHERE id = ?", (principle_id,)
    ).fetchone()

    if not row:
        conn.close()
        raise ValueError(f"Principle '{principle_id}' not found.")

    row_data = dict(row)

    if not row_data.get("is_active", 1):
        conn.close()
        return {
            "status": "ok",
            "principle_id": principle_id,
            "message": "Already deactivated.",
        }

    conn.execute(
        "UPDATE project_constitutions SET is_active = 0 WHERE id = ?",
        (principle_id,),
    )
    conn.commit()
    conn.close()

    if _HAS_AUDIT:
        log_event(
            event_type="constitution_removed",
            actor="system",
            action=f"Deactivated constitution principle '{principle_id}'",
            project_id=row_data.get("project_id"),
            details={"principle_id": principle_id},
        )

    return {
        "status": "ok",
        "principle_id": principle_id,
        "project_id": row_data.get("project_id"),
        "message": "Principle deactivated (soft-deleted).",
    }


def load_defaults(project_id: str, db_path=None) -> dict:
    """Load default DoD constitution principles from JSON into the project.

    Skips principles whose ``principle_text`` already exists for the project
    to avoid duplicates.

    Args:
        project_id: Target project identifier.
        db_path: Optional DB path override.

    Returns:
        Summary dict with loaded / skipped counts.
    """
    defaults = _load_defaults_file()
    if not defaults:
        return {
            "status": "ok",
            "project_id": project_id,
            "loaded": 0,
            "skipped": 0,
            "message": "No default principles file found or file is empty.",
        }

    conn = _get_connection(db_path)

    # Fetch existing principle texts to skip duplicates
    existing_rows = conn.execute(
        "SELECT principle_text FROM project_constitutions WHERE project_id = ? AND is_active = 1",
        (project_id,),
    ).fetchall()
    existing_texts = {r["principle_text"] for r in existing_rows}

    loaded = 0
    skipped = 0

    for item in defaults:
        text = item.get("text", "")
        if not text:
            continue
        if text in existing_texts:
            skipped += 1
            continue

        principle_id = _generate_id("con")
        category = item.get("category", "general")
        if category not in VALID_CATEGORIES:
            category = "general"
        priority = item.get("priority", 2)
        if priority not in (1, 2, 3):
            priority = 2

        conn.execute(
            """INSERT INTO project_constitutions
                   (id, project_id, principle_text, category, priority, is_active, created_by, created_at)
               VALUES (?, ?, ?, ?, ?, 1, 'system', ?)""",
            (principle_id, project_id, text, category, priority, datetime.utcnow().isoformat()),
        )
        loaded += 1

    conn.commit()
    conn.close()

    if _HAS_AUDIT:
        log_event(
            event_type="constitution_defaults_loaded",
            actor="system",
            action=f"Loaded {loaded} default constitution principles for project {project_id}",
            project_id=project_id,
            details={"loaded": loaded, "skipped": skipped},
        )

    return {
        "status": "ok",
        "project_id": project_id,
        "loaded": loaded,
        "skipped": skipped,
        "total_defaults": len(defaults),
    }


def validate_spec(spec_path: Path, project_id: str, db_path=None) -> dict:
    """Validate a specification file against all active constitution principles.

    For each principle the engine loads associated keywords from the defaults
    JSON (falling back to simple word extraction from the principle text).
    Each spec section is checked for keyword coverage.

    Priority mapping:
        1 + keywords not found -> fail
        2 + keywords not found -> warn
        3 + keywords not found -> info

    Args:
        spec_path: Path to the Markdown spec file.
        project_id: Project whose constitution to validate against.
        db_path: Optional DB path override.

    Returns:
        Validation result dict.
    """
    spec_path = Path(spec_path)
    if not spec_path.exists():
        raise FileNotFoundError(f"Spec file not found: {spec_path}")

    # Load principles
    principles = list_principles(project_id, active_only=True, db_path=db_path)
    if not principles:
        return {
            "status": "ok",
            "project_id": project_id,
            "spec_file": str(spec_path),
            "total_principles": 0,
            "passed": 0,
            "failed": 0,
            "warnings": 0,
            "results": [],
            "message": "No active constitution principles found for this project.",
        }

    # Build keyword map from defaults file
    defaults = _load_defaults_file()
    keyword_map: dict = {}
    for item in defaults:
        text = item.get("text", "")
        keywords = item.get("keywords", [])
        if text and keywords:
            keyword_map[text] = [kw.lower() for kw in keywords]

    # Parse spec
    sections = _parse_spec_sections(spec_path)
    full_text = " ".join(sections.values()).lower()

    results = []
    passed = 0
    failed = 0
    warnings = 0

    for p in principles:
        p_text = p["principle_text"]
        priority = p.get("priority", 2)

        # Resolve keywords: prefer JSON mapping, fall back to extracting words
        keywords = keyword_map.get(p_text, [])
        if not keywords:
            # Extract non-trivial words from principle text as fallback
            words = p_text.split()
            keywords = [
                w.strip(".,;:!?()\"'").lower()
                for w in words
                if len(w.strip(".,;:!?()\"'")) > 3
            ]

        # Check coverage
        matched_keywords = [kw for kw in keywords if kw in full_text]
        coverage = len(matched_keywords) / max(len(keywords), 1)

        # Determine result based on coverage threshold
        threshold = 0.3  # at least 30% keyword coverage required
        covered = coverage >= threshold

        if covered:
            level = "pass"
            passed += 1
        elif priority == 1:
            level = "fail"
            failed += 1
        elif priority == 2:
            level = "warn"
            warnings += 1
        else:
            level = "info"
            passed += 1  # priority-3 info does not block

        results.append({
            "principle_id": p["id"],
            "principle_text": p_text,
            "category": p.get("category", "general"),
            "priority": priority,
            "level": level,
            "keyword_coverage": round(coverage, 4),
            "matched_keywords": matched_keywords,
            "total_keywords": len(keywords),
        })

    overall = "fail" if failed > 0 else ("warn" if warnings > 0 else "pass")

    return {
        "status": "ok",
        "project_id": project_id,
        "spec_file": str(spec_path),
        "overall": overall,
        "total_principles": len(principles),
        "passed": passed,
        "failed": failed,
        "warnings": warnings,
        "results": results,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="ICDEV Constitution Manager (ADR D158)"
    )
    parser.add_argument("--project-id", help="Target project ID")
    parser.add_argument("--add", action="store_true", help="Add a new principle")
    parser.add_argument("--principle", help="Principle text (for --add)")
    parser.add_argument("--category", default="general", help="Principle category")
    parser.add_argument("--priority", type=int, default=2, help="Priority 1-3")
    parser.add_argument("--list", dest="list_cmd", action="store_true", help="List principles")
    parser.add_argument("--remove", action="store_true", help="Remove (deactivate) a principle")
    parser.add_argument("--principle-id", help="Principle ID (for --remove)")
    parser.add_argument("--load-defaults", action="store_true", help="Load default DoD principles")
    parser.add_argument("--validate", action="store_true", help="Validate a spec file")
    parser.add_argument("--spec-file", help="Path to spec file (for --validate)")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    try:
        if args.add:
            if not args.project_id:
                raise ValueError("--project-id is required for --add")
            if not args.principle:
                raise ValueError("--principle is required for --add")
            result = add_principle(
                project_id=args.project_id,
                principle_text=args.principle,
                category=args.category,
                priority=args.priority,
            )
            if args.json:
                print(json.dumps(result, indent=2, default=str))
            else:
                print(f"Added principle {result['principle_id']} [{result['category']}] "
                      f"(priority {result['priority']})")
                print(f"  {result['principle_text']}")

        elif args.list_cmd:
            if not args.project_id:
                raise ValueError("--project-id is required for --list")
            cat_filter = args.category if args.category != "general" else None
            principles = list_principles(
                project_id=args.project_id,
                category=cat_filter,
            )
            if args.json:
                print(json.dumps({"status": "ok", "project_id": args.project_id,
                                  "count": len(principles), "principles": principles},
                                 indent=2, default=str))
            else:
                print(f"Constitution for project {args.project_id} ({len(principles)} principles):")
                for p in principles:
                    marker = "!" if p["priority"] == 1 else ("~" if p["priority"] == 2 else ".")
                    print(f"  [{marker}] [{p['category']}] {p['principle_text']}")
                    print(f"      id={p['id']}  priority={p['priority']}")

        elif args.remove:
            if not args.principle_id:
                raise ValueError("--principle-id is required for --remove")
            result = remove_principle(principle_id=args.principle_id)
            if args.json:
                print(json.dumps(result, indent=2, default=str))
            else:
                print(f"Deactivated: {result['principle_id']} -- {result['message']}")

        elif args.load_defaults:
            if not args.project_id:
                raise ValueError("--project-id is required for --load-defaults")
            result = load_defaults(project_id=args.project_id)
            if args.json:
                print(json.dumps(result, indent=2, default=str))
            else:
                print(f"Loaded {result['loaded']} default principles "
                      f"(skipped {result['skipped']} duplicates) "
                      f"for project {args.project_id}")

        elif args.validate:
            if not args.project_id:
                raise ValueError("--project-id is required for --validate")
            if not args.spec_file:
                raise ValueError("--spec-file is required for --validate")
            result = validate_spec(
                spec_path=Path(args.spec_file),
                project_id=args.project_id,
            )
            if args.json:
                print(json.dumps(result, indent=2, default=str))
            else:
                print(f"Constitution validation: {result['overall'].upper()}")
                print(f"  Principles: {result['total_principles']}  "
                      f"Passed: {result['passed']}  "
                      f"Failed: {result['failed']}  "
                      f"Warnings: {result['warnings']}")
                for r in result["results"]:
                    icon = {"pass": "OK", "fail": "FAIL", "warn": "WARN", "info": "INFO"}.get(
                        r["level"], "??"
                    )
                    print(f"  [{icon}] [{r['category']}] {r['principle_text']}")
                    if r["level"] in ("fail", "warn"):
                        print(f"         coverage={r['keyword_coverage']:.0%}  "
                              f"matched={r['matched_keywords']}")

        else:
            parser.print_help()

    except (ValueError, FileNotFoundError) as exc:
        if args.json:
            print(json.dumps({"error": str(exc)}, indent=2))
        else:
            print(f"Error: {exc}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
