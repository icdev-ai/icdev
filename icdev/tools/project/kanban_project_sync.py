# CUI // SP-CTI
"""Kanban Project Auto-Sync — tools/project/kanban_project_sync.py

Scans kanban_tasks for IDs that follow the <prefix><epic>-<N> naming convention
(e.g. sim-l0-01, dt-iqe-03, ad710-macro-01) and auto-upserts matching project
entries into args/simulation_canvas_registry.yaml... wait, wrong file.
Upserts into args/projects.yaml so the "Projects in Flight" card on the home
page always reflects every active project without manual YAML edits.

Rules:
  - Only tasks with IDs matching ^[a-z][a-z0-9]*(-[a-z][a-z0-9]*)*-\\d+$ are
    considered (UUID-style IDs are ignored).
  - Prefix  = everything up to (but not including) the last two dash-segments.
  - Epic key = second-to-last dash-segment.
  - New projects get a minimal entry (key, task_prefix, auto-titled epics).
  - Existing projects: only missing epics are added; name/description/briefs
    set by humans are never overwritten.
  - If nothing changed the YAML file is not written (no spurious git noise).
  - Thread-safe: atomic write via temp-file rename.

CLI:
    python tools/project/kanban_project_sync.py            # sync + print report
    python tools/project/kanban_project_sync.py --dry-run  # print only, no write
    python tools/project/kanban_project_sync.py --json     # JSON report to stdout

API hook (called automatically after every POST /api/kanban/tasks):
    from tools.project.kanban_project_sync import sync_projects
    sync_projects()   # best-effort, never raises
"""

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

import argparse
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Optional

logger = get_logger(__name__)

_PROJECTS_YAML = Path(__file__).resolve().parent.parent.parent / "args" / "projects.yaml"

# Pattern: lowercase prefix + at least one epic segment + numeric suffix
# e.g. sim-l0-01  dt-iqe-03  ad710-macro-01  og-data-02
_TASK_ID_RE = re.compile(
    r"^([a-z][a-z0-9]*(?:-[a-z][a-z0-9]*)*)-([\d]+)$"
)

# A valid id has at least 3 dash-segments so we can split prefix + epic + num
# e.g. "sim-l0-01" → parts=['sim','l0','01'] ✓
#      "task-abc"  → parts=['task','abc']    ✗ (only 2)
_MIN_PARTS = 3


def _parse_task_id(task_id: str) -> Optional[tuple[str, str]]:
    """Return (prefix_with_dash, epic_key) or None if id doesn't match."""
    parts = task_id.split("-")
    if len(parts) < _MIN_PARTS:
        return None
    # Last part must be all digits
    if not parts[-1].isdigit():
        return None
    # Second-to-last is the epic key (must be alphanumeric)
    epic = parts[-2]
    if not re.match(r"^[a-z0-9]+$", epic):
        return None
    prefix = "-".join(parts[:-2]) + "-"  # e.g. "sim-" or "ad710-"
    return prefix, epic


def _load_yaml_raw() -> tuple[dict, str]:
    """Load projects.yaml, return (parsed_dict, original_text)."""
    try:
        import yaml
    except ImportError:
        return {}, ""
    if not _PROJECTS_YAML.exists():
        return {"projects": []}, ""
    text = _PROJECTS_YAML.read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    if "projects" not in data:
        data["projects"] = []
    return data, text


def _write_yaml(data: dict) -> None:
    """Atomic write of projects.yaml via temp-file rename."""
    try:
        import yaml
    except ImportError:
        return
    text = yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False)
    # Preserve the CUI header comment
    header = "# CUI // SP-CTI\n#\n# Auto-managed by tools/project/kanban_project_sync.py\n# Human edits to name/description/briefs are preserved.\n\n"
    tmp = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=_PROJECTS_YAML.parent,
        prefix=".projects_tmp_", suffix=".yaml", delete=False
    )
    try:
        tmp.write(header + text)
        tmp.flush()
        tmp.close()
        os.replace(tmp.name, _PROJECTS_YAML)
    except Exception:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass
        raise


def _scan_db() -> dict[str, set[str]]:
    """Scan kanban_tasks and return {prefix: {epic_key, ...}} for matched IDs."""
    try:
        from tools.db.storage import get_connection
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT id FROM kanban_tasks WHERE status != 'deleted'"
            ).fetchall()
    except Exception as exc:
        logger.warning("kanban_project_sync: DB scan failed: %s", exc)
        return {}

    result: dict[str, set[str]] = {}
    for row in rows:
        tid = dict(row).get("id", "")
        parsed = _parse_task_id(tid)
        if parsed:
            prefix, epic = parsed
            result.setdefault(prefix, set()).add(epic)
    return result


def _epic_title(prefix: str, epic: str) -> str:
    """Generate a readable default title for a new auto-detected epic."""
    # Known layer patterns
    layer_map = {
        "l0": "L0 — Foundation",
        "l1": "L1 — Parsers / Ingestion",
        "l2": "L2 — Router / Dispatch",
        "l3": "L3 — Core Agent / Engine",
        "l4": "L4 — Artifacts / Output",
        "l5": "L5 — Dashboard UI",
        "l6": "L6 — Integrations",
        "l7": "L7 — Validation / Gate",
    }
    if epic in layer_map:
        return layer_map[epic]
    return epic.upper()


def _project_name(prefix: str) -> str:
    """Generate a readable default name for a new auto-detected project."""
    slug = prefix.rstrip("-").replace("-", " ").title()
    return f"{slug} Project"


def sync_projects(dry_run: bool = False) -> dict:
    """
    Scan kanban DB and upsert project entries in projects.yaml.

    Returns a report dict:
      {
        "new_projects": [{"prefix": ..., "key": ...}],
        "updated_projects": [{"key": ..., "added_epics": [...]}],
        "unchanged": int,
        "written": bool,
      }
    """
    db_map = _scan_db()
    if not db_map:
        return {"new_projects": [], "updated_projects": [], "unchanged": 0, "written": False}

    data, _ = _load_yaml_raw()
    projects: list = data.get("projects", [])

    # Build lookup: prefix -> project entry (by REFERENCE, not index). Indices
    # become stale the moment we projects.insert(0, ...) a new project — every
    # existing entry shifts by one — which previously caused epics to be added
    # to the wrong project. Holding the dict reference is shift-safe.
    existing: dict[str, dict] = {}
    for p in projects:
        pfx = (p.get("task_prefix") or "").strip()
        if pfx:
            existing[pfx] = p

    new_projects = []
    updated_projects = []
    changed = False

    # Build a set of existing prefixes for sub-prefix collision detection.
    # A new auto-detected prefix is skipped if any existing project prefix is
    # a strict prefix of it (e.g. "dt-" covers "dt-idc-" — don't create phantom).
    existing_prefixes = set(existing.keys())

    for prefix, epics_found in sorted(db_map.items()):
        if prefix in existing:
            # Project exists — add missing epics only
            proj = existing[prefix]
            current_epic_keys = {(e.get("key") or "").strip() for e in (proj.get("epics") or [])}
            added = []
            for epic in sorted(epics_found - current_epic_keys):
                if epic:
                    proj.setdefault("epics", []).append({
                        "key": epic,
                        "title": _epic_title(prefix, epic),
                        "priority": "medium",
                    })
                    added.append(epic)
                    changed = True
            if added:
                updated_projects.append({"key": proj.get("key"), "added_epics": added})
        else:
            # Skip if an existing project's prefix is a strict leading prefix
            # of this candidate (prevents phantom sub-projects like "dt-idc-"
            # when "dt-" already covers those tasks).
            if any(prefix.startswith(ep) and prefix != ep for ep in existing_prefixes):
                continue

            # New project — create minimal entry
            key = prefix.rstrip("-").replace("-", "_")
            # Avoid key collisions
            existing_keys = {p.get("key") for p in projects}
            base_key = key
            suffix = 2
            while key in existing_keys:
                key = f"{base_key}_{suffix}"
                suffix += 1

            new_entry = {
                "key": key,
                "name": _project_name(prefix),
                "description": (
                    f"Auto-registered project for tasks with prefix '{prefix}'. "
                    "Update name and description to reflect the project purpose."
                ),
                "task_prefix": prefix,
                "default_open": True,
                "briefs": [],
                "epics": [
                    {
                        "key": epic,
                        "title": _epic_title(prefix, epic),
                        "priority": "medium",
                    }
                    for epic in sorted(epics_found)
                    if epic
                ],
            }
            # Insert at top of projects list (most recently registered first)
            projects.insert(0, new_entry)
            existing[prefix] = new_entry  # store by reference (shift-safe)
            new_projects.append({"prefix": prefix, "key": key})
            changed = True

    written = False
    if changed and not dry_run:
        data["projects"] = projects
        _write_yaml(data)
        written = True

    return {
        "new_projects": new_projects,
        "updated_projects": updated_projects,
        "unchanged": len(db_map) - len(new_projects) - len(updated_projects),
        "written": written,
        "dry_run": dry_run,
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Sync kanban task prefixes → projects.yaml")
    parser.add_argument("--dry-run", action="store_true", help="Print changes without writing")
    parser.add_argument("--json", action="store_true", dest="as_json", help="JSON output")
    args = parser.parse_args()

    report = sync_projects(dry_run=args.dry_run)

    if args.as_json:
        print(json.dumps(report, indent=2))
        return

    if report["new_projects"]:
        print(f"New projects registered ({len(report['new_projects'])}):")
        for p in report["new_projects"]:
            print(f"  + {p['key']}  (prefix={p['prefix']})")
    if report["updated_projects"]:
        print(f"Projects updated ({len(report['updated_projects'])}):")
        for p in report["updated_projects"]:
            print(f"  ~ {p['key']}  added epics: {p['added_epics']}")
    if not report["new_projects"] and not report["updated_projects"]:
        print("projects.yaml already up to date.")
    if report.get("dry_run"):
        print("(dry-run — no file written)")
    elif report["written"]:
        print(f"Written: {_PROJECTS_YAML}")


if __name__ == "__main__":
    main()
