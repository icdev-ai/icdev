#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""Cross-artifact consistency analyzer.

Validates internal consistency of spec markdown files: acceptance criteria
vs testing strategy, implementation phases vs tasks, file references,
NIST controls vs ATO assessment, user story vs acceptance criteria, and
sibling document alignment.

Usage:
    python tools/requirements/consistency_analyzer.py --spec-file specs/feat.md --json
    python tools/requirements/consistency_analyzer.py --spec-dir specs/ --json
    python tools/requirements/consistency_analyzer.py --spec-file specs/feat.md --fix-suggestions --human
"""

import argparse
import dataclasses
import json
import re
import sqlite3
import uuid
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

# Graceful audit import (air-gap safe)
try:
    from tools.audit.audit_logger import log_event
    _HAS_AUDIT = True
except ImportError:
    _HAS_AUDIT = False

    def log_event(**kwargs):
        return -1


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


def _generate_id(prefix="cst"):
    """Generate a unique ID with prefix."""
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class ConsistencyResult:
    """Result of a single consistency check."""
    check_id: str
    source_section: str
    target_section: str
    status: str       # "consistent", "inconsistent", "warn"
    message: str
    suggestion: str = ""

    def to_dict(self):
        return dataclasses.asdict(self)


# ---------------------------------------------------------------------------
# Markdown parsing (shared logic with spec_quality_checker)
# ---------------------------------------------------------------------------

def _parse_spec_sections(spec_path: Path) -> dict:
    """Parse markdown by ``## Header`` into a dict.

    Returns ``{section_name_lower: content_string}``.
    """
    content = spec_path.read_text(encoding="utf-8")
    sections: dict = {}
    current_key = "_preamble"
    buffer = []

    for line in content.splitlines():
        h2 = re.match(r"^##\s+(.+)$", line)
        h3 = re.match(r"^###\s+(.+)$", line)
        if h2 and not h3:
            sections[current_key] = "\n".join(buffer)
            current_key = h2.group(1).strip().lower()
            buffer = []
        else:
            buffer.append(line)

    sections[current_key] = "\n".join(buffer)
    return sections


def _find_section(sections: dict, *keywords) -> str:
    """Find section content matching all keywords (case-insensitive)."""
    for key, content in sections.items():
        if all(kw in key for kw in keywords):
            return content
    return ""


def _extract_list_items(text: str) -> list:
    """Extract markdown list items from text."""
    items = []
    for line in text.splitlines():
        stripped = line.strip()
        m = re.match(r"^[-*]\s+(.+)$", stripped) or re.match(r"^\d+\.\s+(.+)$", stripped)
        if m:
            items.append(m.group(1).strip())
    return items


def _extract_keywords(text: str, min_length: int = 4) -> set:
    """Extract meaningful keywords from text (lowercase, min length)."""
    words = re.findall(r"\b[a-zA-Z_][\w-]*\b", text.lower())
    # Filter stop words and short words
    stop_words = {
        "that", "this", "with", "from", "have", "will", "been", "they",
        "their", "which", "when", "where", "what", "there", "about",
        "into", "more", "other", "some", "than", "them", "then", "these",
        "could", "would", "should", "each", "make", "like", "just",
        "over", "such", "take", "only", "come", "also", "after", "before",
        "want", "because", "does", "must", "shall",
    }
    return {
        w for w in words
        if len(w) >= min_length and w not in stop_words
    }


# ---------------------------------------------------------------------------
# Consistency check functions
# ---------------------------------------------------------------------------

def _check_acceptance_vs_testing(sections: dict) -> list:
    """Check that each acceptance criterion has a corresponding test mention."""
    results = []

    ac_content = _find_section(sections, "acceptance", "criteria")
    ts_content = _find_section(sections, "testing", "strategy")

    if not ac_content.strip() or not ts_content.strip():
        if not ac_content.strip():
            results.append(ConsistencyResult(
                check_id=_generate_id(),
                source_section="acceptance criteria",
                target_section="testing strategy",
                status="warn",
                message="Acceptance criteria section is missing; cannot cross-check against testing strategy.",
                suggestion="Add '## Acceptance Criteria' section.",
            ))
        if not ts_content.strip():
            results.append(ConsistencyResult(
                check_id=_generate_id(),
                source_section="testing strategy",
                target_section="acceptance criteria",
                status="warn",
                message="Testing strategy section is missing; cannot verify test coverage for acceptance criteria.",
                suggestion="Add '## Testing Strategy' section.",
            ))
        return results

    ac_items = _extract_list_items(ac_content)
    ts_lower = ts_content.lower()

    untested = []
    for item in ac_items:
        keywords = _extract_keywords(item, min_length=4)
        # Require at least one non-trivial keyword match in testing strategy
        if not any(kw in ts_lower for kw in keywords):
            untested.append(item[:60])

    if untested:
        results.append(ConsistencyResult(
            check_id=_generate_id(),
            source_section="acceptance criteria",
            target_section="testing strategy",
            status="warn",
            message=(
                f"{len(untested)} acceptance criteria have no apparent corresponding test mention: "
                f"'{untested[0]}'{'...' if len(untested) > 1 else ''}."
            ),
            suggestion="Ensure testing strategy covers each acceptance criterion explicitly.",
        ))
    else:
        results.append(ConsistencyResult(
            check_id=_generate_id(),
            source_section="acceptance criteria",
            target_section="testing strategy",
            status="consistent",
            message=f"All {len(ac_items)} acceptance criteria appear referenced in testing strategy.",
        ))

    return results


def _check_phases_vs_tasks(sections: dict) -> list:
    """Check that each implementation phase has corresponding tasks."""
    results = []

    plan_content = _find_section(sections, "implementation", "plan")
    tasks_content = _find_section(sections, "step", "task")

    if not plan_content.strip() or not tasks_content.strip():
        if not plan_content.strip():
            results.append(ConsistencyResult(
                check_id=_generate_id(),
                source_section="implementation plan",
                target_section="step by step tasks",
                status="warn",
                message="Implementation plan section is missing; cannot verify task coverage.",
                suggestion="Add '## Implementation Plan' with phased approach.",
            ))
        if not tasks_content.strip():
            results.append(ConsistencyResult(
                check_id=_generate_id(),
                source_section="step by step tasks",
                target_section="implementation plan",
                status="warn",
                message="Step by step tasks section is missing; cannot verify phase coverage.",
                suggestion="Add '## Step by Step Tasks' section.",
            ))
        return results

    # Extract phases: ### Phase N: Name  or  N. Phase Name
    phase_pattern = re.compile(r"###?\s*Phase\s+(\d+)[:\s]*(.+)", re.IGNORECASE)
    phases = phase_pattern.findall(plan_content)

    if not phases:
        # Fallback: numbered list items
        numbered = re.compile(r"^\s*(\d+)\.\s*(.+)", re.MULTILINE)
        phases = numbered.findall(plan_content)

    if not phases:
        results.append(ConsistencyResult(
            check_id=_generate_id(),
            source_section="implementation plan",
            target_section="step by step tasks",
            status="warn",
            message="Could not extract phases from implementation plan.",
            suggestion="Use '### Phase N: Name' format.",
        ))
        return results

    tasks_lower = tasks_content.lower()
    uncovered = []
    covered = []

    for num, name in phases:
        name_clean = name.strip()
        phase_ref = f"phase {num}"
        name_words = [w for w in name_clean.lower().split() if len(w) > 3]

        found = phase_ref in tasks_lower
        if not found and name_words:
            found = any(w in tasks_lower for w in name_words[:3])

        if found:
            covered.append(f"Phase {num}: {name_clean}")
        else:
            uncovered.append(f"Phase {num}: {name_clean}")

    if uncovered:
        results.append(ConsistencyResult(
            check_id=_generate_id(),
            source_section="implementation plan",
            target_section="step by step tasks",
            status="inconsistent",
            message=f"{len(uncovered)} phase(s) missing from tasks: {'; '.join(uncovered[:3])}.",
            suggestion="Add task entries for each implementation phase.",
        ))
    else:
        results.append(ConsistencyResult(
            check_id=_generate_id(),
            source_section="implementation plan",
            target_section="step by step tasks",
            status="consistent",
            message=f"All {len(phases)} phases have corresponding tasks.",
        ))

    return results


def _check_files_exist(sections: dict, spec_base: Path) -> list:
    """Check that file paths referenced in 'Relevant Files' actually exist."""
    results = []

    rf_content = _find_section(sections, "relevant", "file")
    if not rf_content.strip():
        return results  # No relevant files section, nothing to check

    # Split content into "New Files" and existing (everything else)
    new_files_section = False
    existing_paths = []
    new_paths = []

    for line in rf_content.splitlines():
        stripped = line.strip().lower()
        if "new file" in stripped:
            new_files_section = True
            continue
        if re.match(r"^###?\s+", line) and "new" not in stripped:
            new_files_section = False

        # Extract backtick-wrapped paths
        path_matches = re.findall(r"`([^`]+)`", line)
        for p in path_matches:
            # Heuristic: looks like a file path (has extension or slash)
            if "/" in p or "\\" in p or "." in p.split("/")[-1] if "/" in p else "." in p:
                if new_files_section:
                    new_paths.append(p)
                else:
                    existing_paths.append(p)

    # Check existing files
    for fpath in existing_paths:
        resolved = BASE_DIR / fpath.lstrip("/")
        if resolved.exists():
            results.append(ConsistencyResult(
                check_id=_generate_id(),
                source_section="relevant files",
                target_section="filesystem",
                status="consistent",
                message=f"Referenced file exists: {fpath}",
            ))
        else:
            results.append(ConsistencyResult(
                check_id=_generate_id(),
                source_section="relevant files",
                target_section="filesystem",
                status="inconsistent",
                message=f"Referenced file does not exist: {fpath}",
                suggestion=f"Verify path or move to 'New Files' subsection if it will be created.",
            ))

    # Warn about new files that already exist
    for fpath in new_paths:
        resolved = BASE_DIR / fpath.lstrip("/")
        if resolved.exists():
            results.append(ConsistencyResult(
                check_id=_generate_id(),
                source_section="relevant files (new)",
                target_section="filesystem",
                status="warn",
                message=f"File listed as 'new' already exists: {fpath}",
                suggestion="Move to existing files section or verify this is intentional.",
            ))

    return results


def _check_nist_vs_ato(sections: dict) -> list:
    """Check NIST control IDs are consistent with ATO impact assessment."""
    results = []

    nist_content = _find_section(sections, "nist", "800-53") or _find_section(sections, "nist", "control")
    ato_content = _find_section(sections, "ato", "impact")

    if not nist_content.strip() and not ato_content.strip():
        return results

    nist_pattern = re.compile(r"\b[A-Z]{2}-\d+(?:\(\d+\))?\b")
    nist_controls = set(nist_pattern.findall(nist_content)) if nist_content else set()
    ato_controls = set(nist_pattern.findall(ato_content)) if ato_content else set()

    # Check: NIST section lists controls but ATO says "None"
    ato_lower = ato_content.lower() if ato_content else ""
    nist_lower = nist_content.lower() if nist_content else ""

    if nist_controls and re.search(r"\bnone\b", ato_lower):
        results.append(ConsistencyResult(
            check_id=_generate_id(),
            source_section="nist 800-53 controls",
            target_section="ato impact assessment",
            status="inconsistent",
            message=(
                f"NIST section lists controls ({', '.join(sorted(nist_controls)[:5])}) "
                f"but ATO section indicates 'None'."
            ),
            suggestion="Reconcile: either remove controls from NIST section or update ATO impact.",
        ))
    elif not nist_controls and ato_controls:
        results.append(ConsistencyResult(
            check_id=_generate_id(),
            source_section="ato impact assessment",
            target_section="nist 800-53 controls",
            status="warn",
            message=(
                f"ATO section references controls ({', '.join(sorted(ato_controls)[:5])}) "
                f"but NIST section is empty."
            ),
            suggestion="Add referenced controls to the NIST 800-53 Controls section.",
        ))
    elif nist_controls and ato_controls:
        # Check for controls in one but not the other
        only_nist = nist_controls - ato_controls
        only_ato = ato_controls - nist_controls
        if only_nist or only_ato:
            parts = []
            if only_nist:
                parts.append(f"in NIST only: {', '.join(sorted(only_nist)[:3])}")
            if only_ato:
                parts.append(f"in ATO only: {', '.join(sorted(only_ato)[:3])}")
            results.append(ConsistencyResult(
                check_id=_generate_id(),
                source_section="nist 800-53 controls",
                target_section="ato impact assessment",
                status="warn",
                message=f"Control ID mismatch between sections: {'; '.join(parts)}.",
                suggestion="Ensure both sections reference the same set of applicable controls.",
            ))
        else:
            results.append(ConsistencyResult(
                check_id=_generate_id(),
                source_section="nist 800-53 controls",
                target_section="ato impact assessment",
                status="consistent",
                message=f"NIST and ATO sections reference consistent controls ({len(nist_controls)}).",
            ))
    elif nist_controls and not ato_content.strip():
        results.append(ConsistencyResult(
            check_id=_generate_id(),
            source_section="nist 800-53 controls",
            target_section="ato impact assessment",
            status="warn",
            message="NIST controls listed but no ATO Impact Assessment section found.",
            suggestion="Add '## ATO Impact Assessment' with boundary tier and SSP impact.",
        ))

    return results


def _check_user_story_vs_acceptance(sections: dict) -> list:
    """Check user story alignment with acceptance criteria."""
    results = []

    us_content = _find_section(sections, "user", "story")
    ac_content = _find_section(sections, "acceptance", "criteria")

    if not us_content.strip() or not ac_content.strip():
        return results

    # Extract "I want" clause
    want_match = re.search(r"i want\s+(.+?)(?:so that|$)", us_content, re.IGNORECASE | re.DOTALL)
    if not want_match:
        results.append(ConsistencyResult(
            check_id=_generate_id(),
            source_section="user story",
            target_section="acceptance criteria",
            status="warn",
            message="Could not extract 'I want' clause from user story.",
            suggestion="Use format: As a <role> I want <goal> So that <benefit>.",
        ))
        return results

    want_text = want_match.group(1).strip()
    want_keywords = _extract_keywords(want_text, min_length=4)

    if not want_keywords:
        return results

    # Check if at least one acceptance criterion references those keywords
    ac_lower = ac_content.lower()
    matched = [kw for kw in want_keywords if kw in ac_lower]

    if matched:
        results.append(ConsistencyResult(
            check_id=_generate_id(),
            source_section="user story",
            target_section="acceptance criteria",
            status="consistent",
            message=(
                f"User story keywords found in acceptance criteria: "
                f"{', '.join(sorted(matched)[:5])}."
            ),
        ))
    else:
        results.append(ConsistencyResult(
            check_id=_generate_id(),
            source_section="user story",
            target_section="acceptance criteria",
            status="warn",
            message=(
                f"User story 'I want' keywords ({', '.join(sorted(want_keywords)[:5])}) "
                f"not found in acceptance criteria."
            ),
            suggestion="Ensure acceptance criteria validate the user story goal.",
        ))

    return results


def _check_spec_directory_consistency(spec_path: Path) -> list:
    """Check consistency with sibling plan.md and tasks.md if they exist."""
    results = []
    spec_dir = spec_path.parent

    # Only check if this looks like a per-feature directory
    plan_path = spec_dir / "plan.md"
    tasks_path = spec_dir / "tasks.md"

    if not plan_path.exists() and not tasks_path.exists():
        return results  # Not a per-feature directory structure

    spec_sections = _parse_spec_sections(spec_path)

    # Check plan.md vs Implementation Plan section
    if plan_path.exists():
        plan_content = plan_path.read_text(encoding="utf-8")
        impl_content = _find_section(spec_sections, "implementation", "plan")

        if impl_content.strip() and plan_content.strip():
            plan_keywords = _extract_keywords(plan_content, min_length=5)
            impl_keywords = _extract_keywords(impl_content, min_length=5)

            if plan_keywords and impl_keywords:
                overlap = plan_keywords & impl_keywords
                total_unique = plan_keywords | impl_keywords
                similarity = len(overlap) / max(len(total_unique), 1)

                if similarity < 0.2:
                    results.append(ConsistencyResult(
                        check_id=_generate_id(),
                        source_section="plan.md",
                        target_section="implementation plan",
                        status="inconsistent",
                        message=(
                            f"Sibling plan.md and spec Implementation Plan section diverge "
                            f"significantly (keyword overlap: {similarity:.0%})."
                        ),
                        suggestion="Synchronize plan.md with the spec's Implementation Plan section.",
                    ))
                else:
                    results.append(ConsistencyResult(
                        check_id=_generate_id(),
                        source_section="plan.md",
                        target_section="implementation plan",
                        status="consistent",
                        message=f"plan.md aligns with Implementation Plan (keyword overlap: {similarity:.0%}).",
                    ))

    # Check tasks.md vs Step by Step Tasks section
    if tasks_path.exists():
        tasks_file_content = tasks_path.read_text(encoding="utf-8")
        tasks_section = _find_section(spec_sections, "step", "task")

        if tasks_section.strip() and tasks_file_content.strip():
            file_keywords = _extract_keywords(tasks_file_content, min_length=5)
            section_keywords = _extract_keywords(tasks_section, min_length=5)

            if file_keywords and section_keywords:
                overlap = file_keywords & section_keywords
                total_unique = file_keywords | section_keywords
                similarity = len(overlap) / max(len(total_unique), 1)

                if similarity < 0.2:
                    results.append(ConsistencyResult(
                        check_id=_generate_id(),
                        source_section="tasks.md",
                        target_section="step by step tasks",
                        status="inconsistent",
                        message=(
                            f"Sibling tasks.md and spec Step by Step Tasks section diverge "
                            f"significantly (keyword overlap: {similarity:.0%})."
                        ),
                        suggestion="Synchronize tasks.md with the spec's Step by Step Tasks section.",
                    ))
                else:
                    results.append(ConsistencyResult(
                        check_id=_generate_id(),
                        source_section="tasks.md",
                        target_section="step by step tasks",
                        status="consistent",
                        message=f"tasks.md aligns with Step by Step Tasks (keyword overlap: {similarity:.0%}).",
                    ))

    return results


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

def analyze_spec_consistency(spec_path: Path, db_path=None) -> dict:
    """Run all consistency checks on a spec file.

    Returns a summary dict with consistency score and detailed results.
    """
    spec_path = Path(spec_path)
    if not spec_path.exists():
        return {"status": "error", "error": f"Spec file not found: {spec_path}"}

    sections = _parse_spec_sections(spec_path)

    all_results = []
    all_results.extend(_check_acceptance_vs_testing(sections))
    all_results.extend(_check_phases_vs_tasks(sections))
    all_results.extend(_check_files_exist(sections, spec_path))
    all_results.extend(_check_nist_vs_ato(sections))
    all_results.extend(_check_user_story_vs_acceptance(sections))
    all_results.extend(_check_spec_directory_consistency(spec_path))

    consistent = sum(1 for r in all_results if r.status == "consistent")
    inconsistent = sum(1 for r in all_results if r.status == "inconsistent")
    warnings = sum(1 for r in all_results if r.status == "warn")
    total = len(all_results)

    consistency_score = round((consistent / max(total, 1)) * 100.0, 1)

    inconsistencies = [
        r.to_dict() for r in all_results if r.status == "inconsistent"
    ]
    suggestions = [
        r.suggestion for r in all_results
        if r.suggestion and r.status in ("inconsistent", "warn")
    ]

    if _HAS_AUDIT:
        log_event(
            event_type="spec_consistency_check",
            actor="icdev-requirements-analyst",
            action=f"Consistency check on {spec_path.name}: {consistency_score}%",
            details={
                "spec_file": str(spec_path),
                "consistency_score": consistency_score,
                "consistent": consistent,
                "inconsistent": inconsistent,
            },
        )

    return {
        "status": "ok",
        "spec_file": str(spec_path),
        "consistency_score": consistency_score,
        "total_checks": total,
        "consistent": consistent,
        "inconsistent": inconsistent,
        "warnings": warnings,
        "results": [r.to_dict() for r in all_results],
        "inconsistencies": inconsistencies,
        "suggestions": suggestions,
    }


# ---------------------------------------------------------------------------
# Human-readable output
# ---------------------------------------------------------------------------

def _format_human(result: dict, fix_suggestions: bool = False) -> str:
    """Format consistency results for terminal display."""
    lines = []
    score = result.get("consistency_score", 0)
    spec = result.get("spec_file", "unknown")

    if score >= 80:
        indicator = "[CONSISTENT]"
    elif score >= 50:
        indicator = "[PARTIAL]"
    else:
        indicator = "[INCONSISTENT]"

    lines.append(f"{'=' * 60}")
    lines.append(f"Consistency Report: {spec}")
    lines.append(f"{'=' * 60}")
    lines.append(f"  Score: {score:.1f}% {indicator}")
    lines.append(
        f"  Consistent: {result.get('consistent', 0)} | "
        f"Inconsistent: {result.get('inconsistent', 0)} | "
        f"Warnings: {result.get('warnings', 0)}"
    )
    lines.append("")

    for r in result.get("results", []):
        status = r.get("status", "?").upper()
        src = r.get("source_section", "?")
        tgt = r.get("target_section", "?")
        msg = r.get("message", "")

        if status == "CONSISTENT":
            tag = "[OK]"
        elif status == "INCONSISTENT":
            tag = "[FAIL]"
        else:
            tag = "[WARN]"

        lines.append(f"  {tag:8s} {src} <-> {tgt}")
        lines.append(f"  {'':8s}   {msg}")
        if fix_suggestions and r.get("suggestion"):
            lines.append(f"  {'':8s}   -> {r['suggestion']}")

    if result.get("inconsistencies"):
        lines.append("")
        lines.append(f"INCONSISTENCIES ({len(result['inconsistencies'])}):")
        for inc in result["inconsistencies"]:
            lines.append(f"  * {inc.get('source_section', '?')} <-> {inc.get('target_section', '?')}")
            lines.append(f"    {inc.get('message', '')}")

    lines.append(f"{'=' * 60}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="ICDEV Cross-Artifact Consistency Analyzer"
    )
    parser.add_argument("--spec-file", type=str, help="Check a single spec markdown file")
    parser.add_argument("--spec-dir", type=str, help="Batch check all .md files in directory (recursive)")
    parser.add_argument("--fix-suggestions", action="store_true", help="Include detailed fix suggestions")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--human", action="store_true", help="Colored terminal output")
    args = parser.parse_args()

    try:
        # --- Single file mode ---
        if args.spec_file:
            result = analyze_spec_consistency(Path(args.spec_file))

            if args.json:
                print(json.dumps(result, indent=2, default=str))
            elif args.human:
                print(_format_human(result, fix_suggestions=args.fix_suggestions))
            else:
                print(json.dumps(result, indent=2, default=str))
            return

        # --- Batch mode ---
        if args.spec_dir:
            spec_dir = Path(args.spec_dir)
            if not spec_dir.is_dir():
                raise ValueError(f"Not a directory: {spec_dir}")

            all_results = []
            for md_file in sorted(spec_dir.rglob("*.md")):
                r = analyze_spec_consistency(md_file)
                all_results.append(r)

            batch_result = {
                "status": "ok",
                "spec_dir": str(spec_dir),
                "total_specs": len(all_results),
                "average_score": round(
                    sum(r.get("consistency_score", 0) for r in all_results) / max(len(all_results), 1),
                    1,
                ),
                "specs_consistent": sum(
                    1 for r in all_results if r.get("consistency_score", 0) >= 70
                ),
                "specs_inconsistent": sum(
                    1 for r in all_results if r.get("consistency_score", 0) < 70
                ),
                "total_inconsistencies": sum(
                    len(r.get("inconsistencies", [])) for r in all_results
                ),
                "results": all_results,
            }

            if args.json:
                print(json.dumps(batch_result, indent=2, default=str))
            elif args.human:
                print(f"Batch Consistency Report: {spec_dir}")
                print(
                    f"  Specs: {batch_result['total_specs']} | "
                    f"Avg Score: {batch_result['average_score']}% | "
                    f"Consistent: {batch_result['specs_consistent']} | "
                    f"Inconsistent: {batch_result['specs_inconsistent']}"
                )
                print()
                for r in all_results:
                    print(_format_human(r, fix_suggestions=args.fix_suggestions))
                    print()
            else:
                print(json.dumps(batch_result, indent=2, default=str))
            return

        # No action specified
        parser.print_help()

    except (ValueError, FileNotFoundError) as exc:
        if args.json:
            print(json.dumps({"status": "error", "error": str(exc)}, indent=2))
        else:
            print(f"Error: {exc}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
