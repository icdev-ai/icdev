#!/usr/bin/env python3
"""Requirements Traceability Matrix (RTM) generator.

Discovers requirements, design artifacts, code modules, and tests in a project
directory, then builds forward and backward traceability links. Identifies gaps
where requirements lack tests or tests lack requirements."""

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _get_connection(db_path=None):
    """Get a database connection with Row factory."""
    path = db_path or DB_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"Database not found: {path}\n"
            "Run: python tools/db/init_icdev_db.py"
        )
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _get_project(conn, project_id):
    """Load project data from the projects table."""
    row = conn.execute(
        "SELECT * FROM projects WHERE id = ?", (project_id,)
    ).fetchone()
    if not row:
        raise ValueError(f"Project '{project_id}' not found.")
    return dict(row)


def _load_cui_config():
    """Load CUI marking configuration.

    Attempts to import from cui_marker module; falls back to defaults.
    """
    try:
        from tools.compliance.cui_marker import load_cui_config as _load
        return _load()
    except Exception:
        pass

    try:
        cui_marker_path = Path(__file__).resolve().parent / "cui_marker.py"
        if cui_marker_path.exists():
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                "cui_marker", cui_marker_path
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod.load_cui_config()
    except Exception:
        pass

    return {
        "banner_top": "CUI // SP-CTI",
        "banner_bottom": "CUI // SP-CTI",
        "document_header": (
            "////////////////////////////////////////////////////////////////////\n"
            "CONTROLLED UNCLASSIFIED INFORMATION (CUI) // SP-CTI\n"
            "Distribution: Distribution D -- Authorized DoD Personnel Only\n"
            "////////////////////////////////////////////////////////////////////"
        ),
        "document_footer": (
            "////////////////////////////////////////////////////////////////////\n"
            "CUI // SP-CTI | Department of Defense\n"
            "////////////////////////////////////////////////////////////////////"
        ),
    }


def _log_audit_event(conn, project_id, action, details, file_path):
    """Log an audit trail event for RTM generation.

    Uses 'compliance_check' as the event_type since RTM generation
    falls under compliance verification activities.
    """
    try:
        conn.execute(
            """INSERT INTO audit_trail
               (project_id, event_type, actor, action, details,
                affected_files, classification)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                project_id,
                "compliance_check",
                "icdev-ivv-engine",
                action,
                json.dumps(details),
                json.dumps([str(file_path)]),
                "CUI",
            ),
        )
        conn.commit()
    except Exception as e:
        print(
            f"Warning: Could not log audit event: {e}", file=sys.stderr
        )


def _normalize_name(name):
    """Normalize a name for fuzzy matching.

    Converts to lowercase, strips extension, replaces separators with spaces.
    """
    name = name.lower()
    # Remove common file extensions
    name = re.sub(r"\.(py|js|ts|tsx|jsx|feature|md|yaml|yml|json)$", "", name)
    # Replace separators with spaces
    name = re.sub(r"[-_./\\]", " ", name)
    # Collapse multiple spaces
    name = re.sub(r"\s+", " ", name).strip()
    return name


def _extract_keywords(text):
    """Extract meaningful keywords from a text string for matching.

    Filters out common stop words and short tokens.
    """
    stop_words = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been",
        "being", "have", "has", "had", "do", "does", "did", "will",
        "shall", "should", "may", "might", "must", "can", "could",
        "would", "and", "but", "or", "nor", "not", "so", "yet",
        "for", "of", "in", "on", "at", "to", "from", "by", "with",
        "as", "into", "through", "during", "before", "after", "above",
        "below", "between", "under", "over", "test", "tests", "spec",
        "src", "lib", "app", "init", "main", "index", "module",
    }
    normalized = _normalize_name(text)
    words = normalized.split()
    return [w for w in words if len(w) > 2 and w not in stop_words]


def _keyword_overlap(keywords_a, keywords_b):
    """Calculate keyword overlap ratio between two keyword lists.

    Returns a float between 0.0 and 1.0.
    """
    if not keywords_a or not keywords_b:
        return 0.0
    set_a = set(keywords_a)
    set_b = set(keywords_b)
    intersection = set_a & set_b
    # Jaccard-like similarity
    union = set_a | set_b
    if not union:
        return 0.0
    return len(intersection) / len(union)


# ---------------------------------------------------------------------------
# Discovery functions
# ---------------------------------------------------------------------------

def _discover_requirements(project_dir):
    """Discover requirements from the project directory.

    Scans for:
      - .feature files: extracts ``Feature:`` name from each
      - requirements.md / user-stories.md: extracts heading-level requirements

    Returns:
        list of dicts with keys: id, title, source_file, type
    """
    project_path = Path(project_dir)
    requirements = []
    req_counter = 1

    # Scan for .feature files
    feature_files = sorted(project_path.rglob("*.feature"))
    for fpath in feature_files:
        try:
            content = fpath.read_text(encoding="utf-8", errors="replace")
            # Extract Feature: line
            match = re.search(r"^\s*Feature:\s*(.+)$", content, re.MULTILINE)
            if match:
                title = match.group(1).strip()
            else:
                title = fpath.stem.replace("_", " ").replace("-", " ").title()

            req_id = f"REQ-{req_counter:03d}"
            requirements.append({
                "id": req_id,
                "title": title,
                "source_file": str(fpath.relative_to(project_path)),
                "type": "feature",
            })
            req_counter += 1
        except (OSError, UnicodeDecodeError):
            continue

    # Scan for requirements markdown files
    req_file_patterns = [
        "requirements.md",
        "REQUIREMENTS.md",
        "user-stories.md",
        "user_stories.md",
        "USER_STORIES.md",
        "docs/requirements.md",
        "docs/requirements/*.md",
        "docs/user-stories.md",
    ]
    seen_files = set()
    for pattern in req_file_patterns:
        for fpath in sorted(project_path.glob(pattern)):
            if fpath in seen_files:
                continue
            seen_files.add(fpath)
            try:
                content = fpath.read_text(
                    encoding="utf-8", errors="replace"
                )
                # Extract heading-level requirements (## or ### headings)
                headings = re.findall(
                    r"^#{2,4}\s+(.+)$", content, re.MULTILINE
                )
                for heading in headings:
                    title = heading.strip()
                    # Skip generic headings
                    if title.lower() in (
                        "overview", "introduction", "references",
                        "table of contents", "toc", "changelog",
                        "appendix", "glossary",
                    ):
                        continue

                    req_id = f"REQ-{req_counter:03d}"
                    requirements.append({
                        "id": req_id,
                        "title": title,
                        "source_file": str(
                            fpath.relative_to(project_path)
                        ),
                        "type": "markdown",
                    })
                    req_counter += 1
            except (OSError, UnicodeDecodeError):
                continue

    return requirements


def _discover_design_artifacts(project_dir):
    """Discover design artifacts from the project directory.

    Scans for: architecture.md, system_design.md, docs/design/,
    docs/architecture/, adr/ directories and files.

    Returns:
        list of dicts with keys: id, title, file_path, type
    """
    project_path = Path(project_dir)
    artifacts = []
    artifact_counter = 1

    # Individual design files
    design_file_patterns = [
        "architecture.md",
        "ARCHITECTURE.md",
        "system_design.md",
        "design.md",
        "DESIGN.md",
        "docs/architecture.md",
        "docs/design.md",
        "docs/system_design.md",
    ]
    seen_files = set()
    for pattern in design_file_patterns:
        for fpath in sorted(project_path.glob(pattern)):
            if fpath in seen_files:
                continue
            seen_files.add(fpath)
            title = fpath.stem.replace("_", " ").replace("-", " ").title()
            artifacts.append({
                "id": f"DES-{artifact_counter:03d}",
                "title": title,
                "file_path": str(fpath.relative_to(project_path)),
                "type": "document",
            })
            artifact_counter += 1

    # Design directories
    design_dirs = [
        "docs/design",
        "docs/architecture",
        "design",
        "architecture",
        "adr",
        "docs/adr",
    ]
    for dir_name in design_dirs:
        dir_path = project_path / dir_name
        if not dir_path.is_dir():
            continue
        for fpath in sorted(dir_path.rglob("*.md")):
            if fpath in seen_files:
                continue
            seen_files.add(fpath)
            title = fpath.stem.replace("_", " ").replace("-", " ").title()
            artifacts.append({
                "id": f"DES-{artifact_counter:03d}",
                "title": title,
                "file_path": str(fpath.relative_to(project_path)),
                "type": "adr" if "adr" in str(dir_name) else "design",
            })
            artifact_counter += 1

    return artifacts


def _discover_code_modules(project_dir):
    """Discover code modules from the project directory.

    Scans src/, lib/, app/ for Python/JS/TS modules. Also scans root for
    main application files.

    Returns:
        list of dicts with keys: id, name, file_path, language
    """
    project_path = Path(project_dir)
    modules = []
    module_counter = 1

    # Language extension mapping
    ext_lang = {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".jsx": "javascript",
        ".go": "go",
        ".rs": "rust",
        ".java": "java",
    }

    # Directories to scan for source code
    source_dirs = ["src", "lib", "app", "api", "services", "models"]

    seen_files = set()

    # Scan source directories
    for dir_name in source_dirs:
        dir_path = project_path / dir_name
        if not dir_path.is_dir():
            continue
        for ext, lang in ext_lang.items():
            for fpath in sorted(dir_path.rglob(f"*{ext}")):
                if fpath in seen_files:
                    continue
                # Skip test files in source directories
                fname = fpath.name.lower()
                if (
                    fname.startswith("test_")
                    or fname.endswith(f"_test{ext}")
                    or fname.endswith(f".test{ext}")
                    or fname.endswith(f".spec{ext}")
                    or "__pycache__" in str(fpath)
                    or "node_modules" in str(fpath)
                ):
                    continue
                # Skip __init__.py files that are empty or near-empty
                if fname == "__init__.py":
                    try:
                        size = fpath.stat().st_size
                        if size < 50:
                            continue
                    except OSError:
                        continue

                seen_files.add(fpath)
                name = fpath.stem
                modules.append({
                    "id": f"MOD-{module_counter:03d}",
                    "name": name,
                    "file_path": str(fpath.relative_to(project_path)),
                    "language": lang,
                })
                module_counter += 1

    # Scan root for main application files
    root_app_patterns = [
        "main.py", "app.py", "server.py", "index.py",
        "main.js", "app.js", "server.js", "index.js",
        "main.ts", "app.ts", "server.ts", "index.ts",
    ]
    for pattern in root_app_patterns:
        fpath = project_path / pattern
        if fpath.exists() and fpath not in seen_files:
            seen_files.add(fpath)
            ext = fpath.suffix
            lang = ext_lang.get(ext, "unknown")
            modules.append({
                "id": f"MOD-{module_counter:03d}",
                "name": fpath.stem,
                "file_path": str(fpath.relative_to(project_path)),
                "language": lang,
            })
            module_counter += 1

    return modules


def _discover_tests(project_dir):
    """Discover test files from the project directory.

    Scans tests/, test/, spec/ for test files matching common patterns:
    test_*.py, *_test.py, *.spec.ts, *.test.js, etc.
    Also scans features/ for .feature step definition files.

    Returns:
        list of dicts with keys: id, name, file_path, type
    """
    project_path = Path(project_dir)
    tests = []
    test_counter = 1
    seen_files = set()

    # Test directories to scan
    test_dirs = [
        "tests", "test", "spec", "specs",
        "e2e", "integration",
        "tests/unit", "tests/integration", "tests/e2e",
        "test/unit", "test/integration", "test/e2e",
    ]

    # Test file patterns
    test_patterns = [
        "test_*.py",
        "*_test.py",
        "*_test.go",
        "*.test.js",
        "*.test.ts",
        "*.test.tsx",
        "*.test.jsx",
        "*.spec.js",
        "*.spec.ts",
        "*.spec.tsx",
        "*.spec.jsx",
        "Test*.java",
        "*Test.java",
    ]

    for dir_name in test_dirs:
        dir_path = project_path / dir_name
        if not dir_path.is_dir():
            continue
        for pattern in test_patterns:
            for fpath in sorted(dir_path.rglob(pattern)):
                if fpath in seen_files:
                    continue
                if (
                    "__pycache__" in str(fpath)
                    or "node_modules" in str(fpath)
                ):
                    continue
                seen_files.add(fpath)
                name = fpath.stem
                # Determine test type from directory
                rel = str(fpath.relative_to(project_path)).lower()
                if "e2e" in rel:
                    test_type = "e2e"
                elif "integration" in rel:
                    test_type = "integration"
                elif "unit" in rel:
                    test_type = "unit"
                elif "spec" in rel:
                    test_type = "spec"
                else:
                    test_type = "unit"

                tests.append({
                    "id": f"TST-{test_counter:03d}",
                    "name": name,
                    "file_path": str(fpath.relative_to(project_path)),
                    "type": test_type,
                })
                test_counter += 1

    # Also scan root-level test files
    for pattern in test_patterns:
        for fpath in sorted(project_path.glob(pattern)):
            if fpath in seen_files:
                continue
            if fpath.is_file():
                seen_files.add(fpath)
                tests.append({
                    "id": f"TST-{test_counter:03d}",
                    "name": fpath.stem,
                    "file_path": str(fpath.relative_to(project_path)),
                    "type": "unit",
                })
                test_counter += 1

    # Scan features/ for step definition files
    step_dirs = ["features/steps", "features", "steps"]
    for dir_name in step_dirs:
        dir_path = project_path / dir_name
        if not dir_path.is_dir():
            continue
        for fpath in sorted(dir_path.rglob("*.py")):
            if fpath in seen_files:
                continue
            if "__pycache__" in str(fpath):
                continue
            # Only include step definition files (not __init__.py, etc.)
            if fpath.name.startswith("__"):
                continue
            seen_files.add(fpath)
            tests.append({
                "id": f"TST-{test_counter:03d}",
                "name": fpath.stem,
                "file_path": str(fpath.relative_to(project_path)),
                "type": "bdd_step",
            })
            test_counter += 1

    return tests


# ---------------------------------------------------------------------------
# Traceability functions
# ---------------------------------------------------------------------------

def _build_forward_trace(requirements, design, code, tests):
    """Build forward traceability from requirements to design/code/tests.

    For each requirement, fuzzy-match to design artifacts, code modules,
    and test files using keyword overlap on names.

    Returns:
        list of trace dicts with: requirement_id, requirement_title,
        design_artifacts, code_modules, test_files, status
    """
    trace = []
    match_threshold = 0.15  # Minimum keyword overlap to count as a match

    for req in requirements:
        req_keywords = _extract_keywords(req["title"])
        # Also use the source file name for matching
        source_keywords = _extract_keywords(req.get("source_file", ""))
        all_req_keywords = list(set(req_keywords + source_keywords))

        # Match to design artifacts
        matched_design = []
        for d in design:
            d_keywords = _extract_keywords(d["title"])
            d_path_keywords = _extract_keywords(d.get("file_path", ""))
            overlap = _keyword_overlap(
                all_req_keywords, d_keywords + d_path_keywords
            )
            if overlap >= match_threshold:
                matched_design.append({
                    "id": d["id"],
                    "title": d["title"],
                    "file_path": d.get("file_path", ""),
                    "confidence": round(overlap, 2),
                })

        # Match to code modules
        matched_code = []
        for m in code:
            m_keywords = _extract_keywords(m["name"])
            m_path_keywords = _extract_keywords(m.get("file_path", ""))
            overlap = _keyword_overlap(
                all_req_keywords, m_keywords + m_path_keywords
            )
            if overlap >= match_threshold:
                matched_code.append({
                    "id": m["id"],
                    "name": m["name"],
                    "file_path": m.get("file_path", ""),
                    "confidence": round(overlap, 2),
                })

        # Match to tests
        matched_tests = []
        for t in tests:
            t_keywords = _extract_keywords(t["name"])
            t_path_keywords = _extract_keywords(t.get("file_path", ""))
            overlap = _keyword_overlap(
                all_req_keywords, t_keywords + t_path_keywords
            )
            if overlap >= match_threshold:
                matched_tests.append({
                    "id": t["id"],
                    "name": t["name"],
                    "file_path": t.get("file_path", ""),
                    "type": t.get("type", "unknown"),
                    "confidence": round(overlap, 2),
                })

        # Determine trace status
        has_design = len(matched_design) > 0
        has_code = len(matched_code) > 0
        has_tests = len(matched_tests) > 0

        if has_design and has_code and has_tests:
            status = "Traced"
        elif has_tests:
            status = "Partial"
        elif has_design or has_code:
            status = "Partial"
        else:
            status = "Gap"

        trace.append({
            "requirement_id": req["id"],
            "requirement_title": req["title"],
            "source_file": req.get("source_file", ""),
            "requirement_type": req.get("type", "unknown"),
            "design_artifacts": matched_design,
            "code_modules": matched_code,
            "test_files": matched_tests,
            "status": status,
        })

    return trace


def _build_backward_trace(tests, code, design, requirements):
    """Build backward traceability from tests to requirements.

    For each test, try to find matching requirements using keyword overlap.
    Tests with no matching requirement are flagged as orphan tests.

    Returns:
        list of backward trace dicts with: test_id, test_name,
        matched_requirements, status
    """
    trace = []
    match_threshold = 0.15

    for t in tests:
        t_keywords = _extract_keywords(t["name"])
        t_path_keywords = _extract_keywords(t.get("file_path", ""))
        all_test_keywords = list(set(t_keywords + t_path_keywords))

        matched_reqs = []
        for req in requirements:
            req_keywords = _extract_keywords(req["title"])
            source_keywords = _extract_keywords(
                req.get("source_file", "")
            )
            overlap = _keyword_overlap(
                all_test_keywords, req_keywords + source_keywords
            )
            if overlap >= match_threshold:
                matched_reqs.append({
                    "id": req["id"],
                    "title": req["title"],
                    "confidence": round(overlap, 2),
                })

        status = "Traced" if matched_reqs else "Orphan"

        trace.append({
            "test_id": t["id"],
            "test_name": t["name"],
            "test_file": t.get("file_path", ""),
            "test_type": t.get("type", "unknown"),
            "matched_requirements": matched_reqs,
            "status": status,
        })

    return trace


def _identify_gaps(forward_trace, backward_trace):
    """Identify traceability gaps.

    Returns:
        dict with: untested_requirements, orphan_tests, gap_count
    """
    # Untested requirements: forward trace entries with no test_files
    untested = [
        {
            "requirement_id": ft["requirement_id"],
            "requirement_title": ft["requirement_title"],
            "source_file": ft.get("source_file", ""),
        }
        for ft in forward_trace
        if not ft["test_files"]
    ]

    # Orphan tests: backward trace entries with no matched_requirements
    orphans = [
        {
            "test_id": bt["test_id"],
            "test_name": bt["test_name"],
            "test_file": bt.get("test_file", ""),
        }
        for bt in backward_trace
        if bt["status"] == "Orphan"
    ]

    gap_count = len(untested) + len(orphans)

    return {
        "untested_requirements": untested,
        "orphan_tests": orphans,
        "gap_count": gap_count,
    }


def _calculate_coverage(forward_trace):
    """Calculate forward traceability coverage percentage.

    Coverage = (traced_count / total_requirements) * 100

    A requirement is considered "traced" if it has at least one test file
    matched in the forward trace.

    Returns:
        tuple of (coverage_float, traced_count, total_count)
    """
    total = len(forward_trace)
    if total == 0:
        return 0.0, 0, 0

    traced = sum(
        1 for ft in forward_trace if ft["test_files"]
    )
    coverage = 100.0 * traced / total
    return round(coverage, 1), traced, total


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def _generate_rtm_markdown(
    forward_trace, backward_trace, gaps, coverage,
    traced_count, total_count, project, cui_config,
    requirements, design, code, tests,
):
    """Generate the RTM markdown report.

    Includes CUI headers/footers, project info, coverage summary,
    forward/backward trace tables, and gap analysis.
    """
    lines = []

    # CUI header
    header = cui_config.get("document_header", "").strip()
    if header:
        lines.append(header)
        lines.append("")

    # Title
    lines.append("# Requirements Traceability Matrix (RTM)")
    lines.append("")
    lines.append(f"**Project:** {project.get('name', 'N/A')}")
    lines.append(f"**Project ID:** {project.get('id', 'N/A')}")
    lines.append(f"**Classification:** {project.get('classification', 'CUI')}")
    lines.append(
        f"**Generated:** {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}"
    )
    lines.append(f"**Generator:** ICDEV RTM Generator v1.0")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Discovery summary
    lines.append("## 1. Artifact Discovery Summary")
    lines.append("")
    lines.append(f"| Artifact Type | Count |")
    lines.append(f"|---------------|------:|")
    lines.append(f"| Requirements | {len(requirements)} |")
    lines.append(f"| Design Artifacts | {len(design)} |")
    lines.append(f"| Code Modules | {len(code)} |")
    lines.append(f"| Test Files | {len(tests)} |")
    lines.append("")

    # Coverage summary
    lines.append("## 2. Coverage Summary")
    lines.append("")
    lines.append(f"**Forward Traceability Coverage:** {coverage:.1f}%")
    lines.append(
        f"**Requirements with Full Trace:** {traced_count} / {total_count}"
    )

    gap_data = gaps
    untested_count = len(gap_data.get("untested_requirements", []))
    orphan_count = len(gap_data.get("orphan_tests", []))

    lines.append(f"**Untested Requirements:** {untested_count}")
    lines.append(f"**Orphan Tests (no requirement):** {orphan_count}")
    lines.append(f"**Total Gaps:** {gap_data.get('gap_count', 0)}")
    lines.append("")

    # Status breakdown
    status_counts = {"Traced": 0, "Partial": 0, "Gap": 0}
    for ft in forward_trace:
        st = ft.get("status", "Gap")
        if st in status_counts:
            status_counts[st] += 1

    lines.append("| Trace Status | Count | Percentage |")
    lines.append("|-------------|------:|-----------:|")
    for status in ["Traced", "Partial", "Gap"]:
        cnt = status_counts[status]
        pct = (
            f"{100.0 * cnt / total_count:.1f}%"
            if total_count > 0
            else "N/A"
        )
        lines.append(f"| {status} | {cnt} | {pct} |")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Forward trace table
    lines.append("## 3. Forward Traceability (Requirements -> Artifacts)")
    lines.append("")
    lines.append(
        "| Req ID | Title | Design | Code | Tests | Status |"
    )
    lines.append(
        "|--------|-------|--------|------|-------|--------|"
    )
    for ft in forward_trace:
        req_id = ft["requirement_id"]
        title = ft["requirement_title"]
        if len(title) > 40:
            title = title[:37] + "..."

        design_ids = ", ".join(
            d["id"] for d in ft["design_artifacts"]
        ) or "--"
        code_ids = ", ".join(
            m["id"] for m in ft["code_modules"]
        ) or "--"
        test_ids = ", ".join(
            t["id"] for t in ft["test_files"]
        ) or "--"

        # Truncate long ID lists
        if len(design_ids) > 30:
            design_ids = design_ids[:27] + "..."
        if len(code_ids) > 30:
            code_ids = code_ids[:27] + "..."
        if len(test_ids) > 30:
            test_ids = test_ids[:27] + "..."

        status = ft["status"]
        status_mark = {
            "Traced": "Traced",
            "Partial": "**Partial**",
            "Gap": "**GAP**",
        }.get(status, status)

        lines.append(
            f"| {req_id} | {title} | {design_ids} "
            f"| {code_ids} | {test_ids} | {status_mark} |"
        )
    lines.append("")
    lines.append("---")
    lines.append("")

    # Backward trace table
    lines.append("## 4. Backward Traceability (Tests -> Requirements)")
    lines.append("")
    lines.append(
        "| Test ID | Test Name | Type | Matched Requirements | Status |"
    )
    lines.append(
        "|---------|-----------|------|---------------------|--------|"
    )
    for bt in backward_trace:
        test_id = bt["test_id"]
        test_name = bt["test_name"]
        if len(test_name) > 35:
            test_name = test_name[:32] + "..."
        test_type = bt.get("test_type", "unknown")

        matched = ", ".join(
            r["id"] for r in bt["matched_requirements"]
        ) or "--"
        if len(matched) > 30:
            matched = matched[:27] + "..."

        status = bt["status"]
        status_mark = (
            "Traced" if status == "Traced" else "**ORPHAN**"
        )

        lines.append(
            f"| {test_id} | {test_name} | {test_type} "
            f"| {matched} | {status_mark} |"
        )
    lines.append("")
    lines.append("---")
    lines.append("")

    # Gap analysis
    lines.append("## 5. Gap Analysis")
    lines.append("")

    # Untested requirements
    lines.append("### 5.1 Untested Requirements")
    lines.append("")
    untested = gap_data.get("untested_requirements", [])
    if untested:
        lines.append(
            f"The following {len(untested)} requirement(s) have no "
            "matching test files:"
        )
        lines.append("")
        lines.append("| Req ID | Title | Source File |")
        lines.append("|--------|-------|-------------|")
        for ur in untested:
            title = ur["requirement_title"]
            if len(title) > 50:
                title = title[:47] + "..."
            lines.append(
                f"| {ur['requirement_id']} | {title} "
                f"| {ur.get('source_file', 'N/A')} |"
            )
        lines.append("")
    else:
        lines.append("*All requirements have at least one matching test.*")
        lines.append("")

    # Orphan tests
    lines.append("### 5.2 Orphan Tests")
    lines.append("")
    orphans = gap_data.get("orphan_tests", [])
    if orphans:
        lines.append(
            f"The following {len(orphans)} test(s) have no matching "
            "requirement:"
        )
        lines.append("")
        lines.append("| Test ID | Test Name | Test File |")
        lines.append("|---------|-----------|-----------|")
        for ot in orphans:
            lines.append(
                f"| {ot['test_id']} | {ot['test_name']} "
                f"| {ot.get('test_file', 'N/A')} |"
            )
        lines.append("")
    else:
        lines.append("*All tests trace to at least one requirement.*")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(
        "*Generated by ICDEV RTM Generator v1.0 on "
        f"{datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}*"
    )
    lines.append("")

    # CUI footer
    footer = cui_config.get("document_footer", "").strip()
    if footer:
        lines.append(footer)
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Core function
# ---------------------------------------------------------------------------

def generate_rtm(project_id, project_dir=None, output_path=None, db_path=None):
    """Generate a Requirements Traceability Matrix for a project.

    Workflow:
        1. Connect, load project
        2. Resolve project_dir
        3. Discover requirements, design, code, tests
        4. Build forward/backward traces
        5. Identify gaps, calculate coverage
        6. Generate markdown report
        7. Generate JSON data file (machine-readable RTM)
        8. Write both files to compliance/rtm/
        9. Audit: "compliance_check" (RTM generated)
       10. Return result dict

    Args:
        project_id: The project identifier.
        project_dir: Override project directory path.
        output_path: Override output directory.
        db_path: Override database path.

    Returns:
        dict with output_file, json_file, coverage, traced, gaps,
        total_requirements.
    """
    conn = _get_connection(db_path)
    try:
        # 1. Load project
        project = _get_project(conn, project_id)
        project_name = project.get("name", project_id)

        # 2. Resolve project directory
        if project_dir:
            proj_dir = Path(project_dir)
        else:
            dir_path = project.get("directory_path", "")
            if dir_path and Path(dir_path).is_dir():
                proj_dir = Path(dir_path)
            else:
                # Fallback to projects/{name}
                proj_dir = BASE_DIR / "projects" / project_name
                if not proj_dir.is_dir():
                    raise FileNotFoundError(
                        f"Project directory not found. Tried:\n"
                        f"  - {dir_path or '(no directory_path in DB)'}\n"
                        f"  - {proj_dir}\n"
                        "Use --project-dir to specify the project directory."
                    )

        if not proj_dir.is_dir():
            raise FileNotFoundError(
                f"Project directory does not exist: {proj_dir}"
            )

        # 3. Discover artifacts
        requirements = _discover_requirements(str(proj_dir))
        design = _discover_design_artifacts(str(proj_dir))
        code = _discover_code_modules(str(proj_dir))
        tests = _discover_tests(str(proj_dir))

        # 4. Build forward and backward traces
        forward_trace = _build_forward_trace(
            requirements, design, code, tests
        )
        backward_trace = _build_backward_trace(
            tests, code, design, requirements
        )

        # 5. Identify gaps and calculate coverage
        gaps = _identify_gaps(forward_trace, backward_trace)
        coverage, traced_count, total_count = _calculate_coverage(
            forward_trace
        )

        # 6. Load CUI config
        cui_config = _load_cui_config()

        # 7. Generate markdown report
        report_md = _generate_rtm_markdown(
            forward_trace, backward_trace, gaps, coverage,
            traced_count, total_count, project, cui_config,
            requirements, design, code, tests,
        )

        # 8. Generate JSON data (machine-readable RTM)
        rtm_json_data = {
            "project_id": project_id,
            "project_name": project_name,
            "generated_at": datetime.utcnow().isoformat(),
            "generator": "ICDEV RTM Generator v1.0",
            "coverage": coverage,
            "traced": traced_count,
            "total_requirements": total_count,
            "gaps": {
                "untested_requirements": gaps["untested_requirements"],
                "orphan_tests": gaps["orphan_tests"],
                "gap_count": gaps["gap_count"],
            },
            "discovery": {
                "requirements_count": len(requirements),
                "design_artifacts_count": len(design),
                "code_modules_count": len(code),
                "test_files_count": len(tests),
            },
            "requirements": requirements,
            "design_artifacts": design,
            "code_modules": code,
            "test_files": tests,
            "forward_trace": forward_trace,
            "backward_trace": backward_trace,
        }

        # 9. Determine output paths
        if output_path:
            out_dir = Path(output_path)
        else:
            dir_path = project.get("directory_path", "")
            if dir_path:
                out_dir = Path(dir_path) / "compliance" / "rtm"
            else:
                out_dir = (
                    BASE_DIR / "projects" / project_name
                    / "compliance" / "rtm"
                )

        out_dir.mkdir(parents=True, exist_ok=True)

        md_file = out_dir / "rtm-report.md"
        json_file = out_dir / "rtm-data.json"

        with open(md_file, "w", encoding="utf-8") as f:
            f.write(report_md)

        with open(json_file, "w", encoding="utf-8") as f:
            json.dump(rtm_json_data, f, indent=2, default=str)

        # 10. Log audit event
        audit_details = {
            "report_type": "Requirements Traceability Matrix",
            "coverage": coverage,
            "traced": traced_count,
            "total_requirements": total_count,
            "gap_count": gaps["gap_count"],
            "untested_requirements": len(
                gaps["untested_requirements"]
            ),
            "orphan_tests": len(gaps["orphan_tests"]),
            "requirements_discovered": len(requirements),
            "design_artifacts_discovered": len(design),
            "code_modules_discovered": len(code),
            "test_files_discovered": len(tests),
            "output_files": [str(md_file), str(json_file)],
        }
        _log_audit_event(
            conn,
            project_id,
            f"RTM generated — {coverage:.1f}% coverage, "
            f"{gaps['gap_count']} gap(s)",
            audit_details,
            md_file,
        )

        # 11. Print summary
        print("Requirements Traceability Matrix generated successfully:")
        print(f"  Report:            {md_file}")
        print(f"  Data (JSON):       {json_file}")
        print(f"  Project:           {project_name}")
        print(f"  Requirements:      {len(requirements)}")
        print(f"  Design Artifacts:  {len(design)}")
        print(f"  Code Modules:      {len(code)}")
        print(f"  Test Files:        {len(tests)}")
        print(f"  Coverage:          {coverage:.1f}%")
        print(f"  Traced:            {traced_count} / {total_count}")
        print(f"  Gaps:              {gaps['gap_count']}")

        # 12. Return result dict
        return {
            "output_file": str(md_file),
            "json_file": str(json_file),
            "coverage": coverage,
            "traced": traced_count,
            "gaps": gaps["gap_count"],
            "total_requirements": total_count,
            "untested_requirements": len(
                gaps["untested_requirements"]
            ),
            "orphan_tests": len(gaps["orphan_tests"]),
            "requirements_discovered": len(requirements),
            "design_artifacts_discovered": len(design),
            "code_modules_discovered": len(code),
            "test_files_discovered": len(tests),
            "generated_at": datetime.utcnow().isoformat(),
        }

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _format_json_output(result):
    """Format result as JSON for machine-readable output."""
    return json.dumps(result, indent=2, default=str)


def _format_text_output(result):
    """Format result as human-readable text."""
    lines = [
        "=" * 60,
        "REQUIREMENTS TRACEABILITY MATRIX SUMMARY",
        "=" * 60,
        "",
        f"  Report (MD):       {result['output_file']}",
        f"  Data (JSON):       {result['json_file']}",
        "",
        "  DISCOVERY:",
        f"    Requirements:    {result['requirements_discovered']}",
        f"    Design:          {result['design_artifacts_discovered']}",
        f"    Code Modules:    {result['code_modules_discovered']}",
        f"    Test Files:      {result['test_files_discovered']}",
        "",
        "  TRACEABILITY:",
        f"    Coverage:        {result['coverage']:.1f}%",
        f"    Traced:          {result['traced']} / {result['total_requirements']}",
        f"    Total Gaps:      {result['gaps']}",
        f"    Untested Reqs:   {result['untested_requirements']}",
        f"    Orphan Tests:    {result['orphan_tests']}",
        "",
        f"  Generated:         {result['generated_at']}",
        "",
        "=" * 60,
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=(
            "Generate Requirements Traceability Matrix (RTM) — "
            "a core IV&V artifact"
        )
    )
    parser.add_argument(
        "--project-id",
        required=True,
        help="Project ID to generate RTM for",
    )
    parser.add_argument(
        "--project-dir",
        help=(
            "Project directory to scan (default: from DB or "
            "projects/{name})"
        ),
    )
    parser.add_argument(
        "--output-dir",
        help=(
            "Output directory (default: {project_dir}/compliance/rtm/)"
        ),
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=DB_PATH,
        help="Database path (default: data/icdev.db)",
    )
    parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format for CLI summary (default: text)",
    )

    args = parser.parse_args()

    try:
        result = generate_rtm(
            project_id=args.project_id,
            project_dir=args.project_dir,
            output_path=args.output_dir,
            db_path=args.db_path,
        )
        if args.format == "json":
            print(_format_json_output(result))
        else:
            print(_format_text_output(result))
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
