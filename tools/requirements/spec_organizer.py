#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""Per-feature spec directory organizer with parallel task markers.

Organizes flat spec files into structured directories:
    specs/{issue_number}-{slug}/
        spec.md              -- Feature specification
        plan.md              -- Implementation plan (extracted)
        tasks.md             -- Step-by-step tasks with checkboxes + [P] markers
        checklist.md         -- Quality checklist results
        constitution_check.md -- Constitution validation results

ADR D160: Per-feature directories are optional/additive.
ADR D161: Parallel markers use [P] prefix on independent tasks.

Usage:
    python tools/requirements/spec_organizer.py --init --issue 3 --slug "dashboard-kanban" --json
    python tools/requirements/spec_organizer.py --migrate --spec-file specs/issue-3-foo.md --json
    python tools/requirements/spec_organizer.py --migrate-all --json
    python tools/requirements/spec_organizer.py --status --spec-dir specs/3-dashboard-kanban/ --json
    python tools/requirements/spec_organizer.py --list --json
    python tools/requirements/spec_organizer.py --register --spec-dir specs/3-foo/ --project-id proj-123 --json
"""

import argparse
import json
import re
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

try:
    from tools.audit.audit_logger import log_event
    _HAS_AUDIT = True
except ImportError:
    _HAS_AUDIT = False
    def log_event(**kwargs):
        return -1


# ---------------------------------------------------------------------------
# Dependency keywords used to detect steps that cannot run in parallel.
# A step whose text contains any of these (case-insensitive) likely depends
# on a prior step and therefore must NOT receive the [P] marker.
# ---------------------------------------------------------------------------
_DEPENDENCY_KEYWORDS = [
    "after step",
    "after completing",
    "depends on",
    "requires step",
    "once step",
    "from step",
    "building on",
    "result of step",
    "output of step",
    "following step",
]

# The set of sub-files that compose a spec directory.
_SPEC_DIR_FILES = [
    "spec.md",
    "plan.md",
    "tasks.md",
    "checklist.md",
    "constitution_check.md",
]

# CUI header block reused across generated files.
_CUI_HEADER = "# CUI // SP-CTI"


def _get_connection(db_path=None):
    """Open a connection to the ICDEV database."""
    path = db_path or DB_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"Database not found: {path}\nRun: python tools/db/init_icdev_db.py"
        )
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _generate_id(prefix="spec"):
    """Generate a unique ID with prefix."""
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def _load_config() -> dict:
    """Load spec_directory section from args/spec_config.yaml.

    Returns a dict of configuration values with sensible fallback defaults
    when the config file is missing or cannot be parsed.
    """
    config_path = BASE_DIR / "args" / "spec_config.yaml"
    defaults = {
        "enabled": True,
        "auto_migrate": False,
        "directory_pattern": "{issue_number}-{slug}",
        "files": list(_SPEC_DIR_FILES),
        "parallel_markers": {
            "enabled": True,
            "marker_prefix": "[P]",
            "auto_detect": True,
        },
    }
    if config_path.exists():
        try:
            import yaml  # optional dependency
            with open(config_path, "r", encoding="utf-8") as fh:
                cfg = yaml.safe_load(fh) or {}
            sd = cfg.get("spec_directory", {})
            pm = cfg.get("parallel_markers", {})
            return {
                "enabled": sd.get("enabled", defaults["enabled"]),
                "auto_migrate": sd.get("auto_migrate", defaults["auto_migrate"]),
                "directory_pattern": sd.get(
                    "directory_pattern", defaults["directory_pattern"]
                ),
                "files": sd.get("files", defaults["files"]),
                "parallel_markers": {
                    "enabled": pm.get(
                        "enabled", defaults["parallel_markers"]["enabled"]
                    ),
                    "marker_prefix": pm.get(
                        "marker_prefix", defaults["parallel_markers"]["marker_prefix"]
                    ),
                    "auto_detect": pm.get(
                        "auto_detect", defaults["parallel_markers"]["auto_detect"]
                    ),
                },
            }
        except ImportError:
            pass
        except Exception:
            pass
    return defaults


# ---------------------------------------------------------------------------
# Metadata / section parsing
# ---------------------------------------------------------------------------

def _slugify(text: str) -> str:
    """Convert free-form text into a URL/path-safe slug."""
    slug = text.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-{2,}", "-", slug)
    return slug.strip("-")


def _parse_spec_metadata(content: str) -> dict:
    """Extract metadata from spec content.

    Returns a dict with keys: issue_number, run_id, title, slug.
    """
    metadata = {
        "issue_number": None,
        "run_id": None,
        "title": None,
        "slug": None,
    }

    # issue_number: `N`
    m = re.search(r"issue_number:\s*`([^`]+)`", content)
    if m:
        metadata["issue_number"] = m.group(1).strip()

    # run_id: `xxx`
    m = re.search(r"run_id:\s*`([^`]+)`", content)
    if m:
        metadata["run_id"] = m.group(1).strip()

    # title: first # heading after CUI marking, OR first line of Feature Description
    title = None
    lines = content.splitlines()
    past_cui = False
    for line in lines:
        stripped = line.strip()
        # Skip the CUI marking header itself
        if stripped.startswith("# CUI"):
            past_cui = True
            continue
        if past_cui and stripped.startswith("# ") and not stripped.startswith("## "):
            # Remove leading "# " and optional "Feature: " prefix
            title = re.sub(r"^#\s+", "", stripped)
            title = re.sub(r"^Feature:\s*", "", title, flags=re.IGNORECASE)
            break

    if not title:
        # Fallback: first non-empty line under ## Feature Description
        in_feature_desc = False
        for line in lines:
            stripped = line.strip()
            if stripped.lower().startswith("## feature description"):
                in_feature_desc = True
                continue
            if in_feature_desc and stripped and not stripped.startswith("#"):
                title = stripped
                break
            if in_feature_desc and stripped.startswith("##"):
                break

    metadata["title"] = title or "Untitled"
    metadata["slug"] = _slugify(metadata["title"])

    return metadata


def _parse_spec_sections(content: str) -> dict:
    """Parse spec content into sections keyed by ## header name.

    Returns {section_name_lower: section_content} where section_content
    includes everything between the header and the next ## header (or EOF).
    """
    sections = {}
    current_key = None
    current_lines = []

    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            # Save previous section
            if current_key is not None:
                sections[current_key] = "\n".join(current_lines)
            current_key = stripped[3:].strip().lower()
            current_lines = []
        else:
            current_lines.append(line)

    # Save last section
    if current_key is not None:
        sections[current_key] = "\n".join(current_lines)

    return sections


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------

def extract_plan(spec_content: str) -> str:
    """Extract the Implementation Plan section and format as standalone plan.md."""
    metadata = _parse_spec_metadata(spec_content)
    title = metadata.get("title", "Untitled")
    sections = _parse_spec_sections(spec_content)

    plan_body = sections.get("implementation plan", "")
    if not plan_body.strip():
        # Return template
        return (
            f"{_CUI_HEADER}\n"
            f"# Plan: {title}\n"
            "\n"
            "## Phases\n"
            "### Phase 1: Foundation\n"
            "- TODO\n"
            "\n"
            f"{_CUI_HEADER}\n"
        )

    lines = []
    lines.append(_CUI_HEADER)
    lines.append(f"# Plan: {title}")
    lines.append("")
    lines.append("## Phases")
    lines.append(plan_body.strip())
    lines.append("")
    lines.append(_CUI_HEADER)
    return "\n".join(lines) + "\n"


def _has_dependency(text: str) -> bool:
    """Check whether *text* contains any dependency keywords."""
    lower = text.lower()
    return any(kw in lower for kw in _DEPENDENCY_KEYWORDS)


def extract_tasks(spec_content: str) -> str:
    """Extract Step by Step Tasks section and format with checkboxes + [P] markers.

    Rules:
      - ``### Step N: Title`` lines are preserved as headings.
      - Sub-items under each step get ``- [ ] `` checkbox prefixes.
      - Steps that do NOT reference prior steps (no dependency keywords) are
        marked with ``### [P] Step N: Title`` (except Step 1 which is always
        sequential).
      - A ``<!-- Parallel group: steps X, Y -->`` comment is emitted above
        contiguous groups of parallel steps.
    """
    metadata = _parse_spec_metadata(spec_content)
    title = metadata.get("title", "Untitled")
    sections = _parse_spec_sections(spec_content)

    raw = sections.get("step by step tasks", "")
    if not raw.strip():
        return (
            f"{_CUI_HEADER}\n"
            f"# Tasks: {title}\n"
            "\n"
            "## Status\n"
            "- [ ] Not started\n"
            "\n"
            "## Steps\n"
            "### Step 1: TODO\n"
            "- [ ] TODO\n"
            "\n"
            f"{_CUI_HEADER}\n"
        )

    config = _load_config()
    parallel_enabled = config.get("parallel_markers", {}).get("enabled", True)
    marker_prefix = config.get("parallel_markers", {}).get("marker_prefix", "[P]")

    # ---- First pass: collect steps as structured blocks ----
    steps = []  # list of {"heading": str, "number": int, "body_lines": [...]}
    current_step = None

    for line in raw.splitlines():
        stripped = line.strip()

        # Detect step headings: ### Step N: ...
        step_match = re.match(r"^###\s+Step\s+(\d+)\s*:\s*(.*)", stripped)
        if step_match:
            if current_step is not None:
                steps.append(current_step)
            step_num = int(step_match.group(1))
            step_title = step_match.group(2).strip()
            current_step = {
                "number": step_num,
                "title": step_title,
                "body_lines": [],
            }
            continue

        # Non-heading line while inside a step
        if current_step is not None:
            current_step["body_lines"].append(line)
        # Lines before the first step (preamble like "IMPORTANT: ...") are
        # skipped in the tasks file — the spec.md retains them.

    if current_step is not None:
        steps.append(current_step)

    # ---- Second pass: determine parallelism ----
    for step in steps:
        full_text = step["title"] + " " + " ".join(step["body_lines"])
        # Step 1 is never parallel.
        if step["number"] == 1:
            step["parallel"] = False
        elif parallel_enabled and not _has_dependency(full_text):
            step["parallel"] = True
        else:
            step["parallel"] = False

    # ---- Third pass: render output ----
    out = []
    out.append(_CUI_HEADER)
    out.append(f"# Tasks: {title}")
    out.append("")
    out.append("## Status")
    out.append("- [ ] Not started")
    out.append("")
    out.append("## Steps")

    # Group consecutive parallel steps for comment emission.
    idx = 0
    while idx < len(steps):
        step = steps[idx]
        if step["parallel"]:
            # Collect contiguous parallel group
            group_start = idx
            group_nums = []
            while idx < len(steps) and steps[idx]["parallel"]:
                group_nums.append(str(steps[idx]["number"]))
                idx += 1
            # Emit group comment if more than one step in the group
            if len(group_nums) > 1:
                out.append(
                    f"<!-- Parallel group: steps {', '.join(group_nums)} -->"
                )
            # Emit each step in the group
            for gi in range(group_start, group_start + len(group_nums)):
                _emit_step(steps[gi], out, marker_prefix, is_parallel=True)
        else:
            _emit_step(step, out, marker_prefix, is_parallel=False)
            idx += 1

    out.append("")
    out.append(_CUI_HEADER)
    return "\n".join(out) + "\n"


def _emit_step(step: dict, out: list, marker_prefix: str, is_parallel: bool):
    """Append a single step block to *out* list."""
    prefix = f"{marker_prefix} " if is_parallel else ""
    out.append(f"### {prefix}Step {step['number']}: {step['title']}")

    for line in step["body_lines"]:
        stripped = line.strip()
        if not stripped:
            out.append("")
            continue
        # Convert list items to checkboxes (- item or * item or numbered)
        list_match = re.match(r"^(\s*)[-*]\s+(.*)", line)
        numbered_match = re.match(r"^(\s*)\d+[.)]\s+(.*)", line)
        if list_match:
            indent = list_match.group(1)
            text = list_match.group(2)
            out.append(f"{indent}- [ ] {text}")
        elif numbered_match:
            indent = numbered_match.group(1)
            text = numbered_match.group(2)
            out.append(f"{indent}- [ ] {text}")
        else:
            out.append(line)


# ---------------------------------------------------------------------------
# Directory operations
# ---------------------------------------------------------------------------

def init_spec_dir(
    issue_number: str,
    slug: str,
    spec_content: str = None,
    specs_dir: Path = None,
) -> Path:
    """Create a per-feature spec directory and populate it.

    If *spec_content* is provided the directory is populated by extracting
    plan and tasks from the content.  Otherwise template files are written.

    Returns the created directory path.
    """
    specs_dir = specs_dir or (BASE_DIR / "specs")
    config = _load_config()
    dir_name = config["directory_pattern"].format(
        issue_number=issue_number, slug=slug
    )
    target_dir = specs_dir / dir_name
    target_dir.mkdir(parents=True, exist_ok=True)

    files_created = []

    if spec_content:
        # Write the full spec
        spec_path = target_dir / "spec.md"
        spec_path.write_text(spec_content, encoding="utf-8")
        files_created.append("spec.md")

        # Extract and write plan
        plan_text = extract_plan(spec_content)
        plan_path = target_dir / "plan.md"
        plan_path.write_text(plan_text, encoding="utf-8")
        files_created.append("plan.md")

        # Extract and write tasks
        tasks_text = extract_tasks(spec_content)
        tasks_path = target_dir / "tasks.md"
        tasks_path.write_text(tasks_text, encoding="utf-8")
        files_created.append("tasks.md")
    else:
        # Write template files
        title = slug.replace("-", " ").title()
        run_id = uuid.uuid4().hex[:8]

        spec_template = (
            f"{_CUI_HEADER}\n"
            f"# Feature: {title}\n"
            "\n"
            "## Metadata\n"
            f"issue_number: `{issue_number}`\n"
            f"run_id: `{run_id}`\n"
            "\n"
            "## Feature Description\n"
            "TODO: Describe the feature.\n"
            "\n"
            "## User Story\n"
            "As a [role]\n"
            "I want [goal]\n"
            "So that [benefit]\n"
            "\n"
            "## Solution Statement\n"
            "TODO: Describe the solution approach.\n"
            "\n"
            "## ATO Impact Assessment\n"
            "- **Boundary Impact**: GREEN\n"
            "- **New NIST Controls**: None\n"
            "- **SSP Impact**: None\n"
            "\n"
            "## Relevant Files\n"
            "- TODO\n"
            "\n"
            "## Implementation Plan\n"
            "### Phase 1: Foundation\n"
            "- TODO\n"
            "\n"
            "## Step by Step Tasks\n"
            "### Step 1: TODO\n"
            "- TODO\n"
            "\n"
            "## Testing Strategy\n"
            "### Unit Tests\n"
            "- TODO\n"
            "\n"
            "## Acceptance Criteria\n"
            "- TODO\n"
            "- TODO\n"
            "- TODO\n"
            "\n"
            "## Validation Commands\n"
            "- `python -m py_compile <file>` - Syntax check\n"
            "- `ruff check .` - Lint check\n"
            "- `python -m pytest tests/ -v` - Unit tests\n"
            "\n"
            "## NIST 800-53 Controls\n"
            "- TODO\n"
            "\n"
            f"{_CUI_HEADER}\n"
        )
        (target_dir / "spec.md").write_text(spec_template, encoding="utf-8")
        files_created.append("spec.md")

        plan_template = (
            f"{_CUI_HEADER}\n"
            f"# Plan: {title}\n"
            "\n"
            "## Phases\n"
            "### Phase 1: Foundation\n"
            "- TODO\n"
            "\n"
            f"{_CUI_HEADER}\n"
        )
        (target_dir / "plan.md").write_text(plan_template, encoding="utf-8")
        files_created.append("plan.md")

        tasks_template = (
            f"{_CUI_HEADER}\n"
            f"# Tasks: {title}\n"
            "\n"
            "## Status\n"
            "- [ ] Not started\n"
            "\n"
            "## Steps\n"
            "### Step 1: TODO\n"
            "- [ ] TODO\n"
            "\n"
            f"{_CUI_HEADER}\n"
        )
        (target_dir / "tasks.md").write_text(tasks_template, encoding="utf-8")
        files_created.append("tasks.md")

    if _HAS_AUDIT:
        log_event(
            event_type="spec.init",
            actor="icdev-requirements-analyst",
            action=f"Initialized spec directory {dir_name}",
            details={
                "issue_number": issue_number,
                "slug": slug,
                "files_created": files_created,
            },
        )

    return target_dir


def migrate_flat_spec(spec_path: Path, specs_dir: Path = None) -> dict:
    """Migrate a flat spec file into a structured directory.

    Returns a result dict with status, source, target_dir, files_created.
    """
    spec_path = Path(spec_path)
    if not spec_path.exists():
        raise FileNotFoundError(f"Spec file not found: {spec_path}")

    content = spec_path.read_text(encoding="utf-8")
    metadata = _parse_spec_metadata(content)

    issue_number = metadata.get("issue_number")
    slug = metadata.get("slug")
    if not issue_number:
        # Try to extract from filename pattern: issue-N-...
        m = re.match(r"issue-(\d+)", spec_path.stem)
        if m:
            issue_number = m.group(1)
        else:
            issue_number = "0"
    if not slug:
        slug = _slugify(spec_path.stem)

    specs_dir = specs_dir or spec_path.parent
    target_dir = init_spec_dir(
        issue_number=issue_number,
        slug=slug,
        spec_content=content,
        specs_dir=specs_dir,
    )

    files_created = [
        f.name for f in target_dir.iterdir() if f.is_file()
    ]

    return {
        "status": "ok",
        "source": str(spec_path),
        "target_dir": str(target_dir),
        "issue_number": issue_number,
        "slug": slug,
        "files_created": sorted(files_created),
    }


def migrate_all(specs_dir: Path = None) -> list:
    """Migrate all flat .md files in the specs directory.

    Only considers files directly in *specs_dir* (not in subdirectories).
    Returns a list of migration result dicts.
    """
    specs_dir = specs_dir or (BASE_DIR / "specs")
    if not specs_dir.exists():
        return []

    results = []
    for md_file in sorted(specs_dir.glob("*.md")):
        if not md_file.is_file():
            continue
        try:
            result = migrate_flat_spec(md_file, specs_dir=specs_dir)
            results.append(result)
        except Exception as exc:
            results.append({
                "status": "error",
                "source": str(md_file),
                "error": str(exc),
            })

    return results


# ---------------------------------------------------------------------------
# Status / listing
# ---------------------------------------------------------------------------

def get_status(spec_dir: Path) -> dict:
    """Check which expected files exist in a spec directory.

    Returns a dict with the directory path, a file-existence map, and a
    ``complete`` flag that is True when all expected files are present.
    """
    spec_dir = Path(spec_dir)
    files_map = {}
    for fname in _SPEC_DIR_FILES:
        files_map[fname] = (spec_dir / fname).exists()

    return {
        "spec_dir": str(spec_dir),
        "files": files_map,
        "complete": all(files_map.values()),
    }


def list_all_specs(specs_dir: Path = None) -> list:
    """List all spec directories and flat spec files.

    For directories the status is included.  Flat files are listed with a
    ``type`` of ``"flat"``.
    """
    specs_dir = specs_dir or (BASE_DIR / "specs")
    if not specs_dir.exists():
        return []

    items = []

    # Directories first
    for entry in sorted(specs_dir.iterdir()):
        if entry.is_dir() and (entry / "spec.md").exists():
            status = get_status(entry)
            items.append({
                "type": "directory",
                "name": entry.name,
                "path": str(entry),
                **status,
            })

    # Then flat markdown files
    for md_file in sorted(specs_dir.glob("*.md")):
        if md_file.is_file():
            items.append({
                "type": "flat",
                "name": md_file.name,
                "path": str(md_file),
            })

    return items


# ---------------------------------------------------------------------------
# DB registration
# ---------------------------------------------------------------------------

def register_spec(spec_dir: Path, project_id: str = None, db_path=None) -> dict:
    """Insert or update an entry in the spec_registry table.

    Parses metadata from spec.md inside *spec_dir*.
    Returns the registered entry as a dict.
    """
    spec_dir = Path(spec_dir)
    spec_file = spec_dir / "spec.md"
    if not spec_file.exists():
        raise FileNotFoundError(f"spec.md not found in {spec_dir}")

    content = spec_file.read_text(encoding="utf-8")
    metadata = _parse_spec_metadata(content)

    conn = _get_connection(db_path)
    now = datetime.utcnow().isoformat()

    # Check for existing entry by spec_dir
    existing = conn.execute(
        "SELECT id FROM spec_registry WHERE spec_dir = ?",
        (str(spec_dir),),
    ).fetchone()

    if existing:
        entry_id = existing["id"]
        conn.execute(
            """UPDATE spec_registry
               SET project_id = ?,
                   spec_path = ?,
                   issue_number = ?,
                   run_id = ?,
                   title = ?,
                   updated_at = ?
               WHERE id = ?""",
            (
                project_id,
                str(spec_file),
                metadata.get("issue_number"),
                metadata.get("run_id"),
                metadata.get("title"),
                now,
                entry_id,
            ),
        )
    else:
        entry_id = _generate_id("spec")
        conn.execute(
            """INSERT INTO spec_registry
               (id, project_id, spec_path, spec_dir, issue_number, run_id,
                title, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                entry_id,
                project_id,
                str(spec_file),
                str(spec_dir),
                metadata.get("issue_number"),
                metadata.get("run_id"),
                metadata.get("title"),
                now,
                now,
            ),
        )

    conn.commit()

    row = conn.execute(
        "SELECT * FROM spec_registry WHERE id = ?", (entry_id,)
    ).fetchone()
    conn.close()

    result = dict(row) if row else {}

    if _HAS_AUDIT:
        log_event(
            event_type="spec.register",
            actor="icdev-requirements-analyst",
            action=f"Registered spec {entry_id} from {spec_dir.name}",
            project_id=project_id,
            details={"spec_id": entry_id, "spec_dir": str(spec_dir)},
        )

    return {"status": "ok", "entry": result}


# ---------------------------------------------------------------------------
# Checklist / constitution updates
# ---------------------------------------------------------------------------

def update_checklist(spec_dir: Path, check_results: dict) -> Path:
    """Write quality checklist results to checklist.md inside *spec_dir*.

    *check_results* should contain at least ``score`` (float 0-1) and
    ``checks`` (list of dicts with ``name``, ``passed``, ``detail``).
    Returns the path to the written file.
    """
    spec_dir = Path(spec_dir)
    spec_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.utcnow().isoformat()
    score = check_results.get("score", 0.0)
    checks = check_results.get("checks", [])

    lines = [
        _CUI_HEADER,
        "# Quality Checklist Results",
        "",
        f"**Generated:** {now}",
        f"**Score:** {score:.2f}",
        "",
        "## Checks",
    ]

    for chk in checks:
        icon = "PASS" if chk.get("passed") else "FAIL"
        name = chk.get("name", "Unknown")
        detail = chk.get("detail", "")
        lines.append(f"- [{icon}] **{name}**")
        if detail:
            lines.append(f"  - {detail}")

    lines.append("")
    lines.append(_CUI_HEADER)

    out_path = spec_dir / "checklist.md"
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path


def update_constitution_check(spec_dir: Path, validation_results: dict) -> Path:
    """Write constitution validation results to constitution_check.md.

    *validation_results* should contain ``passed`` (bool), ``violations``
    (list of dicts), and optionally ``summary``.
    Returns the path to the written file.
    """
    spec_dir = Path(spec_dir)
    spec_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.utcnow().isoformat()
    passed = validation_results.get("passed", False)
    violations = validation_results.get("violations", [])
    summary = validation_results.get("summary", "")

    verdict = "PASS" if passed else "FAIL"

    lines = [
        _CUI_HEADER,
        "# Constitution Validation Results",
        "",
        f"**Generated:** {now}",
        f"**Verdict:** {verdict}",
        "",
    ]

    if summary:
        lines.append(f"## Summary")
        lines.append(summary)
        lines.append("")

    if violations:
        lines.append("## Violations")
        for v in violations:
            category = v.get("category", "general")
            rule = v.get("rule", "unknown")
            message = v.get("message", "")
            lines.append(f"- **[{category.upper()}] {rule}**: {message}")
        lines.append("")
    else:
        lines.append("## Violations")
        lines.append("None.")
        lines.append("")

    lines.append(_CUI_HEADER)

    out_path = spec_dir / "constitution_check.md"
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="ICDEV Per-Feature Spec Directory Organizer"
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--init", action="store_true", help="Initialize a new spec directory")
    group.add_argument("--migrate", action="store_true", help="Migrate a flat spec file")
    group.add_argument("--migrate-all", action="store_true", help="Migrate all flat specs")
    group.add_argument("--status", action="store_true", help="Check spec directory status")
    group.add_argument("--list", action="store_true", help="List all specs")
    group.add_argument("--register", action="store_true", help="Register spec in DB")

    parser.add_argument("--issue", type=str, help="Issue number (for --init)")
    parser.add_argument("--slug", type=str, help="Feature slug (for --init)")
    parser.add_argument("--spec-content", type=str, help="Spec content string (for --init)")
    parser.add_argument("--spec-file", type=str, help="Path to flat spec file (for --migrate)")
    parser.add_argument("--spec-dir", type=str, help="Path to spec directory (for --status/--register)")
    parser.add_argument("--specs-dir", type=str, help="Override base specs directory")
    parser.add_argument("--project-id", type=str, help="Project ID (for --register)")
    parser.add_argument("--json", action="store_true", help="JSON output")

    args = parser.parse_args()

    specs_dir = Path(args.specs_dir) if args.specs_dir else None

    try:
        if args.init:
            if not args.issue or not args.slug:
                parser.error("--init requires --issue and --slug")
            target_dir = init_spec_dir(
                issue_number=args.issue,
                slug=args.slug,
                spec_content=args.spec_content,
                specs_dir=specs_dir,
            )
            files = sorted(f.name for f in target_dir.iterdir() if f.is_file())
            result = {
                "status": "ok",
                "spec_dir": str(target_dir),
                "files_created": files,
            }
            if args.json:
                print(json.dumps(result, indent=2, default=str))
            else:
                print(f"Created: {target_dir}")
                for f in files:
                    print(f"  - {f}")

        elif args.migrate:
            if not args.spec_file:
                parser.error("--migrate requires --spec-file")
            result = migrate_flat_spec(
                Path(args.spec_file), specs_dir=specs_dir
            )
            if args.json:
                print(json.dumps(result, indent=2, default=str))
            else:
                print(f"Migrated: {result['source']}")
                print(f"  -> {result['target_dir']}")
                for f in result["files_created"]:
                    print(f"     - {f}")

        elif args.migrate_all:
            results = migrate_all(specs_dir=specs_dir)
            if args.json:
                print(json.dumps(results, indent=2, default=str))
            else:
                for r in results:
                    if r["status"] == "ok":
                        print(f"OK: {r['source']} -> {r['target_dir']}")
                    else:
                        print(f"ERR: {r['source']}: {r.get('error', 'unknown')}")
                print(f"\nTotal: {len(results)} file(s) processed")

        elif args.status:
            if not args.spec_dir:
                parser.error("--status requires --spec-dir")
            result = get_status(Path(args.spec_dir))
            if args.json:
                print(json.dumps(result, indent=2, default=str))
            else:
                print(f"Directory: {result['spec_dir']}")
                for fname, exists in result["files"].items():
                    icon = "+" if exists else "-"
                    print(f"  [{icon}] {fname}")
                print(f"  Complete: {result['complete']}")

        elif args.list:
            items = list_all_specs(specs_dir=specs_dir)
            if args.json:
                print(json.dumps(items, indent=2, default=str))
            else:
                for item in items:
                    kind = item["type"]
                    name = item["name"]
                    if kind == "directory":
                        complete = "complete" if item.get("complete") else "partial"
                        print(f"  [DIR]  {name}  ({complete})")
                    else:
                        print(f"  [FILE] {name}")
                if not items:
                    print("No specs found.")

        elif args.register:
            if not args.spec_dir:
                parser.error("--register requires --spec-dir")
            result = register_spec(
                Path(args.spec_dir),
                project_id=args.project_id,
            )
            if args.json:
                print(json.dumps(result, indent=2, default=str))
            else:
                entry = result.get("entry", {})
                print(f"Registered: {entry.get('id', 'N/A')}")
                print(f"  Title: {entry.get('title', 'N/A')}")
                print(f"  Issue: {entry.get('issue_number', 'N/A')}")

    except (ValueError, FileNotFoundError) as exc:
        if args.json:
            print(json.dumps({"status": "error", "error": str(exc)}, indent=2))
        else:
            print(f"Error: {exc}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
