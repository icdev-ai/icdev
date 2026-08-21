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

# An epic key that is a HEX TOKEN rather than a name (rem-hyg-08).
#
# THE BUG THIS EXISTS FOR. The tail test below is `parts[-1].isdigit()` with no
# bound on its length, and a hex token is all digits about 2% of the time. Of
# the 416 opaque `task-<hex>` rows on the live board, THREE ended in an
# all-digit hex segment:
#
#     task-0a4389596f-79141324
#     task-3bc9eb0918-12704769
#     task-3bc9eb0918-79410283
#
# Those three parsed as prefix=`task-`, epic=<hex parent id>, N=<hex tail>, so
# this module invented two "epics" named after hex parent ids and registered an
# entire "Task Project" card. Its epic LIKE patterns then claimed 83 rows while
# the other 333 matched nothing, producing a coverage warning nobody could
# resolve -- `task-<hex>` is what the dashboard's create-task API and
# `awareness/suggested_card_writer` generate, and was never card work.
#
# WHY NOT `task_identity.classify_shape`, which draws almost this line already
# (and whose own comment names the bug: "The 1-3 digit bound is what separates
# -01 from a 10-character hex token that happens to be all digits"). Because the
# two answer DIFFERENT questions. classify_shape asks "is this ROW card work",
# for enforcement. This asks "does this ID reveal a real project namespace", for
# registration. Gating here on classify_shape was measured and would have killed
# three LEGITIMATE epics -- `cdh-gap`, `ci-fix`, `mc-reflex` -- whose ids carry
# machine tails (`ci-fix-27889336050` is a GitHub Actions run id) but whose
# prefix+epic namespace is real and wanted. The discriminator is the EPIC
# SEGMENT, not the tail.
#
# MEASURED before adoption, against the live board and the committed registry:
# of 1,602 registered epics this rejects EXACTLY the two bogus ones, and `task-`
# is the only prefix that stops being auto-created.
#
# Requires a digit, so an all-letter word that happens to be hex-legal
# (`decade`, `facade`, `added`) is never mistaken for a token.
_HEX_TOKEN_RE = re.compile(r"^(?=.*\d)[0-9a-f]{8,}$")


def _is_hex_token(epic: str) -> bool:
    """Is this "epic key" actually a hex id fragment? See :data:`_HEX_TOKEN_RE`."""
    return bool(_HEX_TOKEN_RE.match(epic))


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
    # ...and must be a NAME, not a hex id fragment. A card whose epics are hex
    # tokens counts an arbitrary slice of an opaque namespace and warns forever
    # about the rest.
    if _is_hex_token(epic):
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


_HEADER = ("# CUI // SP-CTI\n#\n"
           "# Auto-managed by tools/project/kanban_project_sync.py\n"
           "# Human edits to name/description/briefs are preserved.\n\n")


def _split_project_blocks(text: str) -> tuple:
    """``(preamble, {key: block_text})`` — the file split at each ``- key:``.

    Every project entry starts with ``- key:`` at column 0, so the file is a
    preamble followed by one contiguous block per project. Splitting there lets
    an unchanged project be written back BYTE-FOR-BYTE.
    """
    preamble: list = []
    blocks: dict = {}
    current_key = None
    current: list = []
    for line in text.splitlines(keepends=True):
        if line.startswith("- key:"):
            if current_key is not None:
                blocks[current_key] = "".join(current)
            current_key = line.split(":", 1)[1].strip()
            current = [line]
        elif current_key is None:
            preamble.append(line)
        else:
            current.append(line)
    if current_key is not None:
        blocks[current_key] = "".join(current)
    return "".join(preamble), blocks


def _render_project(entry: dict) -> str:
    """One project entry, rendered in the file's own style."""
    import yaml

    return yaml.dump([entry], default_flow_style=False, allow_unicode=True,
                     sort_keys=False)


def compose(data: dict, original_text: str, changed_keys: set) -> str:
    """Rebuild the file, re-rendering ONLY the projects that changed.

    THE DEFECT THIS REPLACES. The writer used to ``yaml.dump`` the WHOLE
    document. That round-trip is not stable — it reflows block scalars, quoting
    and line wrapping — so a write that changed NOTHING semantically still
    produced +2,174 / -1,599 lines (measured 2026-08-21 on the live file), and
    adding one project rewrote all 165.

    That is not cosmetic. ``args/projects.yaml`` is TRACKED, and
    ``code_reload.pull_if_safe`` refuses to pull when an incoming file is also
    locally modified. Every project-card registration edits this file upstream,
    so a locally-rewritten copy clashes with essentially every merge — and this
    writer runs on task creation, so the local side is re-dirtied continuously.
    Measured the same day: the deployment had been frozen 22 commits behind
    origin/main, blocked by this one file, with every merged fix absent from the
    running services while every board and CI signal stayed green.

    Preserving unchanged blocks makes the diff proportional to the change, which
    is what makes it reviewable and committable — and a commit is what clears
    the block.
    """
    preamble, blocks = _split_project_blocks(original_text)
    if not preamble.strip():
        preamble = _HEADER + "projects:\n"
    out = [preamble]
    for entry in data.get("projects") or []:
        key = str(entry.get("key") or "").strip()
        if key and key not in changed_keys and key in blocks:
            out.append(blocks[key])          # verbatim — never re-rendered
        else:
            out.append(_render_project(entry))
    return "".join(out)


def _write_text(text: str) -> None:
    """Atomic write via temp-file rename."""
    tmp = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=_PROJECTS_YAML.parent,
        prefix=".projects_tmp_", suffix=".yaml", delete=False
    )
    try:
        tmp.write(text)
        tmp.flush()
        tmp.close()
        os.replace(tmp.name, _PROJECTS_YAML)
    except Exception:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass
        raise


def _write_yaml(data: dict) -> None:
    """Atomic write of projects.yaml — WHOLE-DOCUMENT render.

    Kept for a caller with no original text to preserve. Prefer :func:`compose`
    + :func:`_write_text`, which keeps unchanged blocks byte-for-byte; see that
    docstring for why the difference froze a deployment.
    """
    try:
        import yaml
    except ImportError:
        return
    text = yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False)
    header = _HEADER
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

    data, original_text = _load_yaml_raw()
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
        # Re-render ONLY the projects this run touched; every other block is
        # written back byte-for-byte. See compose() for why: a whole-document
        # yaml.dump reflows the file even when nothing changed, and this file is
        # tracked, so the churn clashes with every incoming merge and froze the
        # deployment 22 commits behind.
        changed_keys = {str(p.get("key") or "").strip() for p in new_projects}
        changed_keys |= {str(u.get("key") or "").strip() for u in updated_projects}
        _write_text(compose(data, original_text, changed_keys))
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
