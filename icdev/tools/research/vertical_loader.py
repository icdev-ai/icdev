#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV™ System Administrator
"""
Vertical Loader — load and validate industry vertical configs from JSON files.

Architecture:
  D-RES-2:  Declarative JSON configs in context/research/verticals/*.json
  D26:      Add new verticals without code changes
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402

DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))
CONFIG_PATH = BASE_DIR / "args" / "research_config.yaml"
DEFAULT_VERTICALS_DIR = BASE_DIR / "context" / "research" / "verticals"

# --- Graceful imports ---
try:
    import yaml

    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

try:
    from tools.audit.audit_logger import log_event as audit_log_event

    _HAS_AUDIT = True
except ImportError:
    _HAS_AUDIT = False

    def audit_log_event(**kwargs):
        return -1


# --- Required fields for validation ---
REQUIRED_FIELDS = ["id", "name", "slug", "description", "keywords"]
OPTIONAL_FIELDS = [
    "regulatory_bodies",
    "academic_categories",
    "community_sources",
    "review_sites",
    "open_source",
    "news_sources",
    "patent_categories",
    "compliance_frameworks",
]


def _now():
    """ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _get_db(db_path=None):
    path = db_path or DB_PATH
    if not Path(str(path)).exists():
        raise FileNotFoundError(f"Database not found: {path}\nRun: python tools/db/init_icdev_db.py")
    conn = get_connection(db_path=str(path))
    return conn


def _audit(event_type, action, details=None):
    if _HAS_AUDIT:
        try:
            audit_log_event(
                event_type=event_type,
                actor="research-engine",
                action=action,
                details=json.dumps(details) if details else None,
                project_id="research-engine",
            )
        except Exception:
            pass


def _load_config():
    if not _HAS_YAML or not CONFIG_PATH.exists():
        return {}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _vertical_id():
    return f"rvert-{uuid.uuid4().hex[:12]}"


def validate_vertical(data):
    """Validate a vertical config dict. Returns (is_valid, errors)."""
    errors = []
    for field in REQUIRED_FIELDS:
        if field not in data:
            errors.append(f"Missing required field: {field}")
        elif not data[field]:
            errors.append(f"Empty required field: {field}")

    if "slug" in data and not isinstance(data["slug"], str):
        errors.append("'slug' must be a string")

    if "keywords" in data and not isinstance(data["keywords"], list):
        errors.append("'keywords' must be a list")

    return len(errors) == 0, errors


def load_vertical_file(file_path):
    """Load and validate a single vertical JSON file."""
    path = Path(file_path)
    if not path.exists():
        return None, f"File not found: {file_path}"

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        return None, f"Invalid JSON in {file_path}: {e}"

    is_valid, errors = validate_vertical(data)
    if not is_valid:
        return None, f"Validation errors in {file_path}: {'; '.join(errors)}"

    data["_config_path"] = str(path)
    return data, None


def discover_verticals(verticals_dir=None):
    """Discover all vertical config files in the verticals directory."""
    vdir = Path(verticals_dir) if verticals_dir else DEFAULT_VERTICALS_DIR
    if not vdir.exists():
        return [], f"Verticals directory not found: {vdir}"

    verticals = []
    errors = []
    for f in sorted(vdir.glob("*.json")):
        data, err = load_vertical_file(f)
        if err:
            errors.append(err)
        elif data:
            verticals.append(data)

    return verticals, errors if errors else None


def load_verticals_to_db(verticals_dir=None, db_path=None):
    """Load all vertical configs into the research_verticals table."""
    verticals, discovery_errors = discover_verticals(verticals_dir)

    conn = _get_db(db_path)
    loaded = 0
    skipped = 0

    try:
        for vert in verticals:
            slug = vert["slug"]
            existing = conn.execute("SELECT id FROM research_verticals WHERE slug = %s", (slug,)).fetchone()

            if existing:
                # Update existing vertical
                conn.execute(
                    """UPDATE research_verticals SET
                       name = %s, description = %s, config_path = %s,
                       keywords = %s, regulatory_bodies = %s,
                       academic_categories = %s, community_sources = %s,
                       updated_at = %s
                       WHERE slug = %s""",
                    (
                        vert["name"],
                        vert.get("description", ""),
                        vert.get("_config_path", ""),
                        json.dumps(vert.get("keywords", [])),
                        json.dumps(vert.get("regulatory_bodies", [])),
                        json.dumps(vert.get("academic_categories", {})),
                        json.dumps(vert.get("community_sources", {})),
                        _now(),
                        slug,
                    ),
                )
                skipped += 1
            else:
                vid = vert.get("id", _vertical_id())
                conn.execute(
                    """INSERT INTO research_verticals
                       (id, name, slug, description, config_path,
                        keywords, regulatory_bodies, academic_categories,
                        community_sources, active, loaded_at, updated_at, classification)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 1, %s, %s, 'CUI')""",
                    (
                        vid,
                        vert["name"],
                        slug,
                        vert.get("description", ""),
                        vert.get("_config_path", ""),
                        json.dumps(vert.get("keywords", [])),
                        json.dumps(vert.get("regulatory_bodies", [])),
                        json.dumps(vert.get("academic_categories", {})),
                        json.dumps(vert.get("community_sources", {})),
                        _now(),
                        _now(),
                    ),
                )
                loaded += 1

        conn.commit()
    finally:
        conn.close()

    result = {
        "loaded": loaded,
        "updated": skipped,
        "total_discovered": len(verticals),
        "errors": discovery_errors or [],
        "loaded_at": _now(),
    }
    _audit("research.verticals.loaded", f"Loaded {loaded} verticals, updated {skipped}", result)
    return result


def list_verticals(db_path=None):
    """List all verticals from the database."""
    conn = _get_db(db_path)
    try:
        rows = conn.execute("SELECT * FROM research_verticals ORDER BY name").fetchall()
        return {
            "verticals": [dict(r) for r in rows],
            "total": len(rows),
        }
    finally:
        conn.close()


def get_vertical(slug=None, vertical_id=None, db_path=None):
    """Get a specific vertical by slug or ID."""
    conn = _get_db(db_path)
    try:
        if slug:
            row = conn.execute("SELECT * FROM research_verticals WHERE slug = %s", (slug,)).fetchone()
        elif vertical_id:
            row = conn.execute("SELECT * FROM research_verticals WHERE id = %s", (vertical_id,)).fetchone()
        else:
            return {"error": "Provide --slug or --vertical-id"}

        if not row:
            return {"error": f"Vertical not found: {slug or vertical_id}"}
        return dict(row)
    finally:
        conn.close()


def get_vertical_config(slug, db_path=None):
    """Get the full JSON config for a vertical by loading from its config_path."""
    vert = get_vertical(slug=slug, db_path=db_path)
    if "error" in vert:
        return vert

    config_path = vert.get("config_path", "")
    if not config_path or not Path(config_path).exists():
        return {"error": f"Config file not found: {config_path}"}

    data, err = load_vertical_file(config_path)
    if err:
        return {"error": err}
    return data


def _print_human(args, result):
    print("=" * 70)
    print("  ICDEV™ Research Engine — Vertical Loader — CUI // SP-CTI")
    print("=" * 70)

    if isinstance(result, dict) and "error" in result:
        print(f"\n  ERROR: {result['error']}\n")
        print("=" * 70)
        return

    if args.load:
        print(f"\n  Loaded: {result.get('loaded', 0)}")
        print(f"  Updated: {result.get('updated', 0)}")
        print(f"  Discovered: {result.get('total_discovered', 0)}")
        errs = result.get("errors", [])
        if errs:
            print("\n  Errors:")
            for e in errs:
                print(f"    - {e}")

    elif args.list:
        verts = result.get("verticals", [])
        print(f"\n  Total Verticals: {result.get('total', 0)}\n")
        print(f"    {'Slug':20s} {'Name':35s} {'Active':8s}")
        print(f"    {'-' * 20} {'-' * 35} {'-' * 8}")
        for v in verts:
            active = "Yes" if v.get("active") else "No"
            print(f"    {v['slug']:20s} {v['name'][:35]:35s} {active:8s}")

    elif args.get:
        print(f"\n  ID: {result.get('id', '')}")
        print(f"  Name: {result.get('name', '')}")
        print(f"  Slug: {result.get('slug', '')}")
        print(f"  Active: {result.get('active', '')}")
        kw = (
            json.loads(result.get("keywords", "[]"))
            if isinstance(result.get("keywords"), str)
            else result.get("keywords", [])
        )
        print(f"  Keywords: {', '.join(kw[:10])}")

    print()
    print("=" * 70)


def main():
    parser = argparse.ArgumentParser(
        description="Research Engine Vertical Loader — CUI // SP-CTI",
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--human", action="store_true", help="Human-readable output")
    parser.add_argument("--db-path", type=Path, default=None, help="Database path override")

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--load", action="store_true", help="Load verticals from config dir to DB")
    group.add_argument("--list", action="store_true", help="List all verticals")
    group.add_argument("--get", action="store_true", help="Get a specific vertical")
    group.add_argument("--config", action="store_true", help="Get full JSON config for a vertical")
    group.add_argument("--validate", action="store_true", help="Validate all vertical configs")

    parser.add_argument("--slug", type=str, help="Vertical slug (with --get/--config)")
    parser.add_argument("--vertical-id", type=str, help="Vertical ID (with --get)")
    parser.add_argument("--verticals-dir", type=str, help="Override verticals directory")

    args = parser.parse_args()

    try:
        if args.load:
            result = load_verticals_to_db(verticals_dir=args.verticals_dir, db_path=args.db_path)
        elif args.list:
            result = list_verticals(db_path=args.db_path)
        elif args.get:
            result = get_vertical(slug=args.slug, vertical_id=args.vertical_id, db_path=args.db_path)
        elif args.config:
            result = get_vertical_config(slug=args.slug, db_path=args.db_path)
        elif args.validate:
            verts, errs = discover_verticals(args.verticals_dir)
            result = {
                "valid": len(verts),
                "errors": errs or [],
                "verticals": [v["slug"] for v in verts],
            }
        else:
            result = {"error": "No action specified"}

        if args.human:
            _print_human(args, result)
        else:
            print(json.dumps(result, indent=2, default=str))

    except FileNotFoundError as e:
        error = {"error": str(e), "hint": "Run: python tools/db/init_icdev_db.py"}
        if args.human:
            print(f"ERROR: {e}", file=sys.stderr)
        else:
            print(json.dumps(error, indent=2))
        sys.exit(1)
    except Exception as e:
        error = {"error": str(e)}
        if args.human:
            print(f"ERROR: {e}", file=sys.stderr)
        else:
            print(json.dumps(error, indent=2))
        sys.exit(1)


if __name__ == "__main__":
    main()
