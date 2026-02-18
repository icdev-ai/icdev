#!/usr/bin/env python3
"""IV&V assessment tool per IEEE 1012 and DoD standards.

Loads IV&V requirements from ivv_requirements.json, performs automated checks
where possible, stores results in ivv_assessments table, generates findings in
ivv_findings table, evaluates IV&V gates, applies CUI markings, and logs audit events."""

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
IVV_REQUIREMENTS_PATH = BASE_DIR / "context" / "compliance" / "ivv_requirements.json"

PROCESS_AREAS = [
    "Requirements Verification",
    "Design Verification",
    "Code Verification",
    "Test Verification",
    "Integration Verification",
    "Traceability Analysis",
    "Security Verification",
    "Build/Deploy Verification",
    "Process Compliance",
]

# Process area codes used for scoring categories
PROCESS_AREA_CODES = {
    "Requirements Verification": "REQ",
    "Design Verification": "DES",
    "Code Verification": "CODE",
    "Test Verification": "TEST",
    "Integration Verification": "INT",
    "Traceability Analysis": "RTM",
    "Security Verification": "SEC",
    "Build/Deploy Verification": "BLD",
    "Process Compliance": "PROC",
}

# Verification areas contribute to verification_score
VERIFICATION_AREAS = ["REQ", "DES", "CODE", "RTM", "SEC", "BLD", "PROC"]

# Validation areas contribute to validation_score
VALIDATION_AREAS = ["TEST", "INT"]


# -----------------------------------------------------------------
# Database helpers
# -----------------------------------------------------------------

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


# -----------------------------------------------------------------
# Configuration helpers
# -----------------------------------------------------------------

def _load_cui_config():
    """Load CUI marking configuration."""
    try:
        sys.path.insert(0, str(BASE_DIR / "tools" / "compliance"))
        from cui_marker import load_cui_config
        return load_cui_config()
    except ImportError:
        return {
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


def _load_ivv_requirements():
    """Load IV&V requirements from the JSON catalog."""
    if not IVV_REQUIREMENTS_PATH.exists():
        raise FileNotFoundError(
            f"IV&V requirements file not found: {IVV_REQUIREMENTS_PATH}\n"
            "Expected: context/compliance/ivv_requirements.json"
        )
    with open(IVV_REQUIREMENTS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _log_audit_event(conn, project_id, action, details, file_path=None):
    """Log an audit trail event (append-only, NIST AU compliant)."""
    try:
        conn.execute(
            """INSERT INTO audit_trail
               (project_id, event_type, actor, action, details,
                affected_files, classification)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                project_id,
                "ivv_assessed",
                "icdev-ivv-engine",
                action,
                json.dumps(details),
                json.dumps([str(file_path)] if file_path else []),
                "CUI",
            ),
        )
        conn.commit()
    except Exception as e:
        print(f"Warning: Could not log audit event: {e}", file=sys.stderr)


# -----------------------------------------------------------------
# Auto-check helper: walk project files matching extensions
# -----------------------------------------------------------------

def _scan_files(project_dir, extensions, patterns, threshold=1):
    """Scan project files for regex patterns.

    Args:
        project_dir: Root directory to walk.
        extensions: Tuple of file extensions to include (e.g. ('.py', '.md')).
        patterns: List of regex patterns to search for.
        threshold: Minimum number of files with matches to consider satisfied.

    Returns:
        Tuple of (matched_files, total_scanned).
    """
    matched_files = []
    total_scanned = 0
    for root, _, files in os.walk(project_dir):
        for fname in files:
            if not fname.endswith(extensions):
                continue
            fpath = os.path.join(root, fname)
            total_scanned += 1
            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                for pattern in patterns:
                    if re.search(pattern, content, re.IGNORECASE):
                        matched_files.append(fpath)
                        break
            except Exception:
                continue
    return matched_files, total_scanned


def _dir_or_file_exists(project_dir, dir_names=None, glob_patterns=None):
    """Check if specific directories or file globs exist under project_dir.

    Args:
        project_dir: Root directory to search.
        dir_names: List of directory names to look for.
        glob_patterns: List of glob patterns to match files.

    Returns:
        List of found paths.
    """
    found = []
    project_path = Path(project_dir)

    if dir_names:
        for dname in dir_names:
            candidate = project_path / dname
            if candidate.is_dir():
                found.append(str(candidate))
            # Also check one level deeper (e.g. infra/terraform/)
            for child in project_path.rglob(dname):
                if child.is_dir() and str(child) not in found:
                    found.append(str(child))

    if glob_patterns:
        for gp in glob_patterns:
            for match in project_path.rglob(gp):
                if str(match) not in found:
                    found.append(str(match))

    return found


# -----------------------------------------------------------------
# Auto-check functions
# Each returns a dict:
#   {"status": "satisfied"|"not_satisfied"|"partially_satisfied"|"not_applicable",
#    "evidence": "description of what was found",
#    "details": "specifics"}
# -----------------------------------------------------------------

def _check_req_completeness(project_dir):
    """IVV-01: Requirements Completeness.

    Look for requirements.md, user-stories.md, *.feature files, docs/requirements/.
    Satisfied if at least 2 types of requirements docs found; partially if 1;
    not_satisfied if 0.
    """
    types_found = []
    project_path = Path(project_dir)

    # Type 1: requirements documents
    req_docs = _dir_or_file_exists(
        project_dir,
        glob_patterns=[
            "requirements.md", "REQUIREMENTS.md", "requirements.txt",
            "requirements.rst", "functional-requirements*", "non-functional-requirements*",
        ],
    )
    if req_docs:
        types_found.append(("requirements_docs", req_docs))

    # Type 2: user stories
    user_stories = _dir_or_file_exists(
        project_dir,
        glob_patterns=[
            "user-stories.md", "user_stories.md", "USER_STORIES.md",
            "user-stories*.md", "stories*.md",
        ],
    )
    if user_stories:
        types_found.append(("user_stories", user_stories))

    # Type 3: .feature files (BDD/Gherkin)
    feature_files = list(project_path.rglob("*.feature"))
    if feature_files:
        types_found.append(("feature_files", [str(f) for f in feature_files]))

    # Type 4: requirements directory
    req_dirs = _dir_or_file_exists(
        project_dir,
        dir_names=["requirements", "docs/requirements", "specs"],
    )
    if req_dirs:
        types_found.append(("requirements_directory", req_dirs))

    count = len(types_found)
    all_evidence = []
    for tname, tfiles in types_found:
        all_evidence.append(f"{tname}: {len(tfiles)} item(s)")

    if count >= 2:
        return {
            "status": "satisfied",
            "evidence": (
                f"Requirements completeness: {count} types of requirements "
                f"documentation found."
            ),
            "details": "; ".join(all_evidence),
        }
    elif count == 1:
        return {
            "status": "partially_satisfied",
            "evidence": (
                f"Only 1 type of requirements documentation found. "
                f"At least 2 types required for full completeness."
            ),
            "details": "; ".join(all_evidence),
        }
    else:
        return {
            "status": "not_satisfied",
            "evidence": "No requirements documentation found.",
            "details": (
                "Expected: requirements.md, user-stories.md, *.feature files, "
                "or docs/requirements/ directory."
            ),
        }


def _check_req_consistency(project_dir):
    """IVV-02: Requirements Consistency.

    Find .feature files, check if test file names match feature names
    (e.g., auth.feature -> test_auth.py or auth_steps.py).
    Satisfied if >80% features have matching tests; partially if >50%.
    """
    project_path = Path(project_dir)
    feature_files = list(project_path.rglob("*.feature"))

    if not feature_files:
        return {
            "status": "not_satisfied",
            "evidence": "No .feature files found for consistency analysis.",
            "details": "Cannot assess requirements consistency without feature files.",
        }

    # Collect all test file stems for matching
    test_files = set()
    for ext in ("*.py", "*.js", "*.ts"):
        for tf in project_path.rglob(ext):
            test_files.add(tf.stem.lower())

    matched = 0
    unmatched_features = []

    for feat in feature_files:
        feat_stem = feat.stem.lower()
        # Check for test_<feature>, <feature>_test, <feature>_steps, test<feature>
        candidates = [
            f"test_{feat_stem}",
            f"{feat_stem}_test",
            f"{feat_stem}_steps",
            f"test{feat_stem}",
            f"{feat_stem}_spec",
            f"steps_{feat_stem}",
        ]
        if any(c in test_files for c in candidates):
            matched += 1
        else:
            unmatched_features.append(feat.name)

    total = len(feature_files)
    ratio = matched / total if total > 0 else 0

    if ratio > 0.8:
        return {
            "status": "satisfied",
            "evidence": (
                f"Requirements consistency: {matched}/{total} features "
                f"({ratio:.0%}) have matching test files."
            ),
            "details": f"Threshold: >80%. All features are consistent with test naming.",
        }
    elif ratio > 0.5:
        return {
            "status": "partially_satisfied",
            "evidence": (
                f"Requirements consistency: {matched}/{total} features "
                f"({ratio:.0%}) have matching test files."
            ),
            "details": (
                f"Threshold: >80% for full compliance. "
                f"Unmatched: {', '.join(unmatched_features[:5])}"
            ),
        }
    else:
        return {
            "status": "not_satisfied",
            "evidence": (
                f"Requirements consistency: only {matched}/{total} features "
                f"({ratio:.0%}) have matching test files."
            ),
            "details": (
                f"Most feature files lack corresponding test files. "
                f"Unmatched: {', '.join(unmatched_features[:10])}"
            ),
        }


def _check_req_testability(project_dir):
    """IVV-03: Requirements Testability.

    Each .feature file should have at least one corresponding step file
    in a steps/ directory.
    Satisfied if all features have steps; partially if >70%.
    """
    project_path = Path(project_dir)
    feature_files = list(project_path.rglob("*.feature"))

    if not feature_files:
        return {
            "status": "not_satisfied",
            "evidence": "No .feature files found to assess testability.",
            "details": "Cannot verify requirements testability without BDD feature files.",
        }

    # Find all step definition files
    step_files = set()
    for steps_dir in project_path.rglob("steps"):
        if steps_dir.is_dir():
            for sf in steps_dir.iterdir():
                if sf.suffix in (".py", ".js", ".ts", ".rb"):
                    step_files.add(sf.stem.lower())

    # Also look for *_steps.py pattern outside steps/ directories
    for sf in project_path.rglob("*_steps.py"):
        step_files.add(sf.stem.lower())
    for sf in project_path.rglob("*_steps.js"):
        step_files.add(sf.stem.lower())

    matched = 0
    unmatched = []

    for feat in feature_files:
        feat_stem = feat.stem.lower()
        # Match: steps/<feature>.py, <feature>_steps.py, steps_<feature>.py
        candidates = [
            feat_stem,
            f"{feat_stem}_steps",
            f"steps_{feat_stem}",
            f"test_{feat_stem}",
            f"{feat_stem}_step_defs",
        ]
        if any(c in step_files for c in candidates):
            matched += 1
        else:
            unmatched.append(feat.name)

    total = len(feature_files)
    ratio = matched / total if total > 0 else 0

    if ratio >= 1.0:
        return {
            "status": "satisfied",
            "evidence": (
                f"All {total} feature file(s) have corresponding step "
                f"implementation files."
            ),
            "details": f"Step files found for every .feature file.",
        }
    elif ratio > 0.7:
        return {
            "status": "partially_satisfied",
            "evidence": (
                f"Testability: {matched}/{total} features ({ratio:.0%}) "
                f"have step implementations."
            ),
            "details": (
                f"Threshold: 100% for full compliance, >70% for partial. "
                f"Missing steps for: {', '.join(unmatched[:5])}"
            ),
        }
    else:
        return {
            "status": "not_satisfied",
            "evidence": (
                f"Testability: only {matched}/{total} features ({ratio:.0%}) "
                f"have step implementations."
            ),
            "details": (
                f"Most feature files lack corresponding step definitions. "
                f"Missing: {', '.join(unmatched[:10])}"
            ),
        }


def _check_design_architecture(project_dir):
    """IVV-05: Architecture Documentation.

    Look for architecture.md, system_design.md, ARCHITECTURE.md,
    docs/architecture/, docs/design/, adr/ (Architecture Decision Records).
    Satisfied if architecture docs exist.
    """
    found_docs = _dir_or_file_exists(
        project_dir,
        glob_patterns=[
            "architecture.md", "ARCHITECTURE.md", "system_design.md",
            "SYSTEM_DESIGN.md", "system-design.md", "design.md",
            "DESIGN.md", "architecture.rst", "system_architecture*",
        ],
    )
    found_dirs = _dir_or_file_exists(
        project_dir,
        dir_names=[
            "architecture", "docs/architecture", "docs/design",
            "design", "adr", "docs/adr", "doc/architecture",
        ],
    )
    all_found = list(set(found_docs + found_dirs))

    if all_found:
        return {
            "status": "satisfied",
            "evidence": (
                f"Architecture documentation found: {len(all_found)} artifact(s)."
            ),
            "details": "; ".join(
                os.path.basename(f) for f in all_found[:5]
            ),
        }
    return {
        "status": "not_satisfied",
        "evidence": "No architecture documentation found.",
        "details": (
            "Expected: architecture.md, system_design.md, ARCHITECTURE.md, "
            "docs/architecture/, docs/design/, or adr/ directory."
        ),
    }


def _check_independent_sast(project_dir):
    """IVV-08: Independent SAST.

    Check for SAST result files: *sast*report*, *bandit*report*, *.sarif.
    Also scan for known SAST tool output in JSON/XML files.
    Satisfied if SAST results found.
    """
    found_files = _dir_or_file_exists(
        project_dir,
        glob_patterns=[
            "*sast*report*", "*bandit*report*", "*.sarif",
            "*sast*result*", "*sonarqube*report*", "*semgrep*",
            "*codeql*", "*sast*.json", "*sast*.xml",
        ],
    )

    # Also scan JSON/XML files for SAST tool output signatures
    sast_patterns = [
        r'"tool".*"bandit"|"tool".*"semgrep"|"tool".*"codeql"',
        r"bandit.*results|severity.*confidence.*location",
        r"sonarqube|sonarlint|checkmarx|fortify|coverity",
    ]
    matched, _ = _scan_files(
        project_dir, (".json", ".xml", ".sarif"), sast_patterns
    )

    all_found = list(set(found_files + matched))
    if all_found:
        return {
            "status": "satisfied",
            "evidence": (
                f"SAST results found: {len(all_found)} artifact(s)."
            ),
            "details": "; ".join(
                os.path.basename(f) for f in all_found[:5]
            ),
        }
    return {
        "status": "not_satisfied",
        "evidence": "No SAST scan results detected in the project.",
        "details": (
            "Expected: *sast*report*, *bandit*report*, *.sarif files, "
            "or SAST tool output in JSON/XML."
        ),
    }


def _check_coding_standards(project_dir):
    """IVV-09: Coding Standards Compliance.

    Look for linter configs: .flake8, .pylintrc, .eslintrc*, tslint.json,
    pyproject.toml (with [tool.ruff] or [tool.black]), setup.cfg (with [flake8]).
    Satisfied if linter config found.
    """
    # Direct config file checks
    found = _dir_or_file_exists(
        project_dir,
        glob_patterns=[
            ".flake8", ".pylintrc", ".eslintrc*", "tslint.json",
            ".prettierrc*", ".stylelintrc*", ".editorconfig",
            "ruff.toml", ".ruff.toml", ".bandit",
        ],
    )
    if found:
        return {
            "status": "satisfied",
            "evidence": (
                f"Coding standards configuration found: {len(found)} config file(s)."
            ),
            "details": "; ".join(
                os.path.basename(f) for f in found[:5]
            ),
        }

    # Check pyproject.toml for linter sections
    pyproject_files = _dir_or_file_exists(
        project_dir, glob_patterns=["pyproject.toml"]
    )
    for pp_path in pyproject_files:
        try:
            with open(pp_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            if re.search(
                r"\[tool\.(ruff|black|flake8|pylint|isort|mypy)\]",
                content, re.IGNORECASE,
            ):
                return {
                    "status": "satisfied",
                    "evidence": "Linter configuration found in pyproject.toml.",
                    "details": f"File: {os.path.basename(pp_path)}",
                }
        except Exception:
            continue

    # Check setup.cfg for [flake8] section
    setup_cfg_files = _dir_or_file_exists(
        project_dir, glob_patterns=["setup.cfg"]
    )
    for sc_path in setup_cfg_files:
        try:
            with open(sc_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            if re.search(r"\[flake8\]|\[pylint\]|\[mypy\]", content):
                return {
                    "status": "satisfied",
                    "evidence": "Linter configuration found in setup.cfg.",
                    "details": f"File: {os.path.basename(sc_path)}",
                }
        except Exception:
            continue

    return {
        "status": "not_satisfied",
        "evidence": "No coding standards or linter configuration found.",
        "details": (
            "Expected: .flake8, .pylintrc, .eslintrc*, tslint.json, "
            "or [tool.ruff]/[tool.black] in pyproject.toml."
        ),
    }


def _check_code_review_completion(project_dir):
    """IVV-10: Code Review Completion.

    Look for code review evidence: CODEOWNERS, .github/CODEOWNERS,
    merge request templates, pull_request_template.md.
    Also scan for @approval_required, review_required patterns.
    Satisfied if code review infrastructure found.
    """
    found_files = _dir_or_file_exists(
        project_dir,
        glob_patterns=[
            "CODEOWNERS", ".github/CODEOWNERS", ".gitlab/CODEOWNERS",
            "pull_request_template.md", "PULL_REQUEST_TEMPLATE.md",
            "merge_request_template*", ".github/pull_request_template*",
            ".gitlab/merge_request_templates/*",
        ],
    )

    if found_files:
        return {
            "status": "satisfied",
            "evidence": (
                f"Code review infrastructure found: {len(found_files)} artifact(s)."
            ),
            "details": "; ".join(
                os.path.basename(f) for f in found_files[:5]
            ),
        }

    # Scan CI/pipeline files for review requirements
    review_patterns = [
        r"approval_required|required_approvals|approvals_required",
        r"review_required|code.review|peer.review",
        r"merge.*approval|approve.*merge|required_reviewers",
    ]
    extensions = (".yml", ".yaml", ".json", ".toml", ".py")
    matched, total = _scan_files(project_dir, extensions, review_patterns)

    if matched:
        return {
            "status": "satisfied",
            "evidence": (
                f"Code review enforcement patterns found in {len(matched)} file(s)."
            ),
            "details": "; ".join(
                os.path.basename(f) for f in matched[:5]
            ),
        }

    if total == 0:
        return {
            "status": "not_satisfied",
            "evidence": "No applicable files found to assess code review configuration.",
            "details": "Project directory lacks CI/CD or configuration files.",
        }

    return {
        "status": "not_satisfied",
        "evidence": "No code review infrastructure or enforcement detected.",
        "details": (
            "Expected: CODEOWNERS, pull_request_template.md, or "
            "approval_required patterns in CI configuration."
        ),
    }


def _check_complexity_metrics(project_dir):
    """IVV-11: Complexity Metrics.

    Look for radon config in pyproject.toml/setup.cfg, .complexity,
    complexity-report.*, McCabe config.
    Also check for max-complexity in linter config.
    Satisfied if complexity tooling configured.
    """
    # Direct complexity tool artifacts
    found = _dir_or_file_exists(
        project_dir,
        glob_patterns=[
            ".complexity", "complexity-report*", "*complexity*report*",
            "radon-report*", "*radon*",
        ],
    )
    if found:
        return {
            "status": "satisfied",
            "evidence": (
                f"Complexity measurement artifacts found: {len(found)} item(s)."
            ),
            "details": "; ".join(
                os.path.basename(f) for f in found[:5]
            ),
        }

    # Check pyproject.toml for radon/complexity config
    pyproject_files = _dir_or_file_exists(
        project_dir, glob_patterns=["pyproject.toml"]
    )
    for pp_path in pyproject_files:
        try:
            with open(pp_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            if re.search(
                r"\[tool\.radon\]|max.complexity|mccabe|cyclomatic",
                content, re.IGNORECASE,
            ):
                return {
                    "status": "satisfied",
                    "evidence": "Complexity tooling configured in pyproject.toml.",
                    "details": f"File: {os.path.basename(pp_path)}",
                }
        except Exception:
            continue

    # Check setup.cfg for complexity settings
    setup_cfg_files = _dir_or_file_exists(
        project_dir, glob_patterns=["setup.cfg"]
    )
    for sc_path in setup_cfg_files:
        try:
            with open(sc_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            if re.search(r"max.complexity|mccabe|radon", content, re.IGNORECASE):
                return {
                    "status": "satisfied",
                    "evidence": "Complexity metrics configured in setup.cfg.",
                    "details": f"File: {os.path.basename(sc_path)}",
                }
        except Exception:
            continue

    # Check linter config files for max-complexity
    linter_configs = _dir_or_file_exists(
        project_dir,
        glob_patterns=[".flake8", ".pylintrc", ".eslintrc*", "ruff.toml"],
    )
    for lc_path in linter_configs:
        try:
            with open(lc_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            if re.search(r"max.complexity|mccabe|cyclomatic", content, re.IGNORECASE):
                return {
                    "status": "satisfied",
                    "evidence": "Complexity threshold found in linter configuration.",
                    "details": f"File: {os.path.basename(lc_path)}",
                }
        except Exception:
            continue

    return {
        "status": "not_satisfied",
        "evidence": "No complexity measurement tooling or configuration detected.",
        "details": (
            "Expected: radon configuration, max-complexity in linter config, "
            "or complexity-report artifacts."
        ),
    }


def _check_test_coverage(project_dir):
    """IVV-12: Test Coverage Adequacy.

    Look for coverage artifacts: htmlcov/, coverage.xml, .coverage,
    coverage-report.*, lcov.info, cobertura.xml.
    Also check for pytest-cov in requirements, coverage config in pyproject.toml.
    Satisfied if coverage reports/config found.
    """
    # Coverage report artifacts
    found_dirs = _dir_or_file_exists(
        project_dir,
        dir_names=["htmlcov", "coverage", "coverage-report"],
    )
    found_files = _dir_or_file_exists(
        project_dir,
        glob_patterns=[
            "coverage.xml", ".coverage", "coverage-report*",
            "lcov.info", "cobertura.xml", "coverage.json",
            "*coverage*report*", ".coveragerc",
        ],
    )

    all_found = list(set(found_dirs + found_files))
    if all_found:
        return {
            "status": "satisfied",
            "evidence": (
                f"Test coverage artifacts found: {len(all_found)} item(s)."
            ),
            "details": "; ".join(
                os.path.basename(f) for f in all_found[:5]
            ),
        }

    # Check requirements for coverage tools
    req_files = _dir_or_file_exists(
        project_dir,
        glob_patterns=[
            "requirements*.txt", "requirements/*.txt",
            "Pipfile", "Pipfile.lock",
        ],
    )
    for rf_path in req_files:
        try:
            with open(rf_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            if re.search(r"pytest.cov|coverage|istanbul|nyc", content, re.IGNORECASE):
                return {
                    "status": "satisfied",
                    "evidence": "Coverage tooling found in project dependencies.",
                    "details": f"File: {os.path.basename(rf_path)}",
                }
        except Exception:
            continue

    # Check pyproject.toml for coverage config
    pyproject_files = _dir_or_file_exists(
        project_dir, glob_patterns=["pyproject.toml"]
    )
    for pp_path in pyproject_files:
        try:
            with open(pp_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            if re.search(
                r"\[tool\.coverage\]|\[tool\.pytest.*cov|--cov|coverage",
                content, re.IGNORECASE,
            ):
                return {
                    "status": "satisfied",
                    "evidence": "Coverage configuration found in pyproject.toml.",
                    "details": f"File: {os.path.basename(pp_path)}",
                }
        except Exception:
            continue

    return {
        "status": "not_satisfied",
        "evidence": "No test coverage reports or configuration detected.",
        "details": (
            "Expected: htmlcov/, coverage.xml, .coverage, lcov.info, "
            "or pytest-cov in requirements."
        ),
    }


def _check_test_plan(project_dir):
    """IVV-13: Test Plan Completeness.

    Look for test-plan.md, TEST_PLAN.md, docs/testing/,
    tests/ directory with organized structure.
    Check tests/ has subdirectories (unit/, integration/, e2e/) or at least
    3+ test files. Satisfied if test plan or structured tests/ directory exists.
    """
    # Check for explicit test plan docs
    plan_docs = _dir_or_file_exists(
        project_dir,
        glob_patterns=[
            "test-plan.md", "TEST_PLAN.md", "test_plan.md",
            "test-plan.rst", "testing-plan*", "test-strategy*",
            "TEST_STRATEGY*",
        ],
    )
    plan_dirs = _dir_or_file_exists(
        project_dir,
        dir_names=["docs/testing", "docs/test", "test-documentation"],
    )

    if plan_docs or plan_dirs:
        all_found = list(set(plan_docs + plan_dirs))
        return {
            "status": "satisfied",
            "evidence": (
                f"Test plan documentation found: {len(all_found)} artifact(s)."
            ),
            "details": "; ".join(
                os.path.basename(f) for f in all_found[:5]
            ),
        }

    # Check for structured tests/ directory
    project_path = Path(project_dir)
    tests_dirs = list(project_path.rglob("tests"))
    test_dirs = list(project_path.rglob("test"))
    all_test_dirs = [d for d in (tests_dirs + test_dirs) if d.is_dir()]

    for td in all_test_dirs:
        # Check for organized subdirectories
        subdirs = [
            child.name for child in td.iterdir()
            if child.is_dir() and child.name in (
                "unit", "integration", "e2e", "functional",
                "acceptance", "smoke", "performance", "security",
            )
        ]
        if subdirs:
            return {
                "status": "satisfied",
                "evidence": (
                    f"Structured test directory found at {td.name}/ with "
                    f"subdirectories: {', '.join(subdirs)}."
                ),
                "details": f"Path: {td}",
            }

        # Check for at least 3 test files
        test_file_count = sum(
            1 for f in td.iterdir()
            if f.is_file() and f.name.startswith("test_")
        )
        if test_file_count >= 3:
            return {
                "status": "satisfied",
                "evidence": (
                    f"Test directory found with {test_file_count} test file(s)."
                ),
                "details": f"Path: {td}",
            }

    if all_test_dirs:
        return {
            "status": "partially_satisfied",
            "evidence": (
                "Test directory exists but lacks structured organization."
            ),
            "details": (
                "Expected: tests/ with unit/, integration/, e2e/ subdirectories "
                "or at least 3 test files."
            ),
        }

    return {
        "status": "not_satisfied",
        "evidence": "No test plan documentation or structured tests/ directory found.",
        "details": (
            "Expected: test-plan.md, docs/testing/, or tests/ directory "
            "with organized subdirectories."
        ),
    }


def _check_security_tests(project_dir):
    """IVV-14: Security Test Cases.

    Scan test files for security patterns: test.*security, test.*auth,
    test.*injection, test.*xss, test.*csrf, test.*permission, test.*access.
    Satisfied if 2+ security test files found; partially if 1.
    """
    project_path = Path(project_dir)
    security_test_files = []

    security_name_patterns = re.compile(
        r"test.*(security|auth|injection|xss|csrf|permission|access|"
        r"sanitiz|encrypt|token|jwt|session|privilege|exploit|vuln)",
        re.IGNORECASE,
    )

    for ext in ("*.py", "*.js", "*.ts"):
        for tf in project_path.rglob(ext):
            if security_name_patterns.search(tf.stem):
                security_test_files.append(str(tf))

    # Also scan test file content for security test patterns
    if not security_test_files:
        content_patterns = [
            r"def\s+test_.*(?:security|auth|inject|xss|csrf|permission|access)",
            r"it\s*\(\s*['\"].*(?:security|auth|inject|xss|csrf|permission|access)",
            r"describe\s*\(\s*['\"].*(?:security|auth|inject|xss|csrf|permission|access)",
        ]
        matched, _ = _scan_files(
            project_dir, (".py", ".js", ".ts"), content_patterns
        )
        security_test_files = matched

    count = len(security_test_files)
    if count >= 2:
        return {
            "status": "satisfied",
            "evidence": (
                f"Security test coverage: {count} security-specific test file(s) found."
            ),
            "details": "; ".join(
                os.path.basename(f) for f in security_test_files[:5]
            ),
        }
    elif count == 1:
        return {
            "status": "partially_satisfied",
            "evidence": (
                f"Security test coverage: only 1 security-specific test file found. "
                f"At least 2 required for full compliance."
            ),
            "details": os.path.basename(security_test_files[0]),
        }
    else:
        return {
            "status": "not_satisfied",
            "evidence": "No security-specific test files detected.",
            "details": (
                "Expected: test files containing security, auth, injection, "
                "xss, csrf, permission, or access patterns."
            ),
        }


def _check_bdd_coverage(project_dir):
    """IVV-15: BDD Feature Coverage.

    Count .feature files and compare to step implementation files.
    Satisfied if all features have steps; partially if >70%.
    """
    project_path = Path(project_dir)
    feature_files = list(project_path.rglob("*.feature"))

    if not feature_files:
        return {
            "status": "not_satisfied",
            "evidence": "No .feature files found for BDD coverage assessment.",
            "details": "Cannot verify BDD coverage without Gherkin feature files.",
        }

    # Find all step definition files
    step_file_stems = set()
    for steps_dir in project_path.rglob("steps"):
        if steps_dir.is_dir():
            for sf in steps_dir.iterdir():
                if sf.suffix in (".py", ".js", ".ts", ".rb"):
                    step_file_stems.add(sf.stem.lower())

    for sf in project_path.rglob("*_steps.py"):
        step_file_stems.add(sf.stem.lower())
    for sf in project_path.rglob("*_steps.js"):
        step_file_stems.add(sf.stem.lower())
    for sf in project_path.rglob("*_steps.rb"):
        step_file_stems.add(sf.stem.lower())

    matched = 0
    unmatched = []

    for feat in feature_files:
        feat_stem = feat.stem.lower()
        candidates = [
            feat_stem,
            f"{feat_stem}_steps",
            f"steps_{feat_stem}",
            f"test_{feat_stem}",
            f"{feat_stem}_step_defs",
        ]
        if any(c in step_file_stems for c in candidates):
            matched += 1
        else:
            unmatched.append(feat.name)

    total = len(feature_files)
    ratio = matched / total if total > 0 else 0

    if ratio >= 1.0:
        return {
            "status": "satisfied",
            "evidence": (
                f"BDD coverage: all {total} feature file(s) have "
                f"corresponding step implementations."
            ),
            "details": "Full BDD coverage achieved.",
        }
    elif ratio > 0.7:
        return {
            "status": "partially_satisfied",
            "evidence": (
                f"BDD coverage: {matched}/{total} features ({ratio:.0%}) "
                f"have step implementations."
            ),
            "details": (
                f"Threshold: 100% for full compliance. "
                f"Missing steps for: {', '.join(unmatched[:5])}"
            ),
        }
    else:
        return {
            "status": "not_satisfied",
            "evidence": (
                f"BDD coverage: only {matched}/{total} features ({ratio:.0%}) "
                f"have step implementations."
            ),
            "details": (
                f"Majority of features lack step definitions. "
                f"Missing: {', '.join(unmatched[:10])}"
            ),
        }


def _check_e2e_tests(project_dir):
    """IVV-17: End-to-End Verification.

    Look for e2e/ directory, integration/ directory, playwright.config.*,
    cypress.config.*, selenium config.
    Satisfied if E2E test infrastructure found.
    """
    found_dirs = _dir_or_file_exists(
        project_dir,
        dir_names=[
            "e2e", "integration", "end-to-end", "end_to_end",
            "tests/e2e", "tests/integration", "test/e2e", "test/integration",
        ],
    )
    found_files = _dir_or_file_exists(
        project_dir,
        glob_patterns=[
            "playwright.config.*", "cypress.config.*", "cypress.json",
            "wdio.conf.*", "protractor.conf.*", "selenium.conf*",
            "nightwatch.conf.*", "testcafe*",
        ],
    )

    all_found = list(set(found_dirs + found_files))
    if all_found:
        return {
            "status": "satisfied",
            "evidence": (
                f"E2E test infrastructure found: {len(all_found)} artifact(s)."
            ),
            "details": "; ".join(
                os.path.basename(f) for f in all_found[:5]
            ),
        }

    # Check for E2E-flavored test files
    e2e_patterns = [
        r"class\s+\w*E2E\w*Test|class\s+\w*Integration\w*Test",
        r"describe.*['\"]e2e|describe.*['\"]integration|describe.*['\"]end.to.end",
        r"def\s+test_.*(?:e2e|integration|end_to_end|workflow|journey)",
    ]
    matched, _ = _scan_files(
        project_dir, (".py", ".js", ".ts"), e2e_patterns
    )
    if matched:
        return {
            "status": "satisfied",
            "evidence": (
                f"E2E test patterns found in {len(matched)} file(s)."
            ),
            "details": "; ".join(
                os.path.basename(f) for f in matched[:5]
            ),
        }

    return {
        "status": "not_satisfied",
        "evidence": "No E2E test infrastructure or integration test directories detected.",
        "details": (
            "Expected: e2e/ directory, integration/ directory, or "
            "E2E test framework configuration (Playwright, Cypress, Selenium)."
        ),
    }


def _check_rtm_exists(project_dir):
    """IVV-19: RTM Completeness.

    Look for traceability_matrix.md, rtm.md, RTM.*, requirements-traceability.*,
    trace-matrix.*. Also check compliance/rtm/ directory.
    Satisfied if RTM artifact exists.
    """
    found_files = _dir_or_file_exists(
        project_dir,
        glob_patterns=[
            "traceability_matrix*", "traceability-matrix*",
            "rtm.md", "RTM.md", "RTM.*", "rtm.*",
            "requirements-traceability*", "requirements_traceability*",
            "trace-matrix*", "trace_matrix*",
            "traceability*",
        ],
    )
    found_dirs = _dir_or_file_exists(
        project_dir,
        dir_names=[
            "rtm", "compliance/rtm", "docs/rtm",
            "traceability", "docs/traceability",
        ],
    )

    all_found = list(set(found_files + found_dirs))
    if all_found:
        return {
            "status": "satisfied",
            "evidence": (
                f"Requirements Traceability Matrix found: {len(all_found)} artifact(s)."
            ),
            "details": "; ".join(
                os.path.basename(f) for f in all_found[:5]
            ),
        }
    return {
        "status": "not_satisfied",
        "evidence": "No Requirements Traceability Matrix (RTM) detected.",
        "details": (
            "Expected: traceability_matrix.md, rtm.md, RTM.*, "
            "requirements-traceability.*, or compliance/rtm/ directory."
        ),
    }


def _check_pipeline_security(project_dir):
    """IVV-25: Pipeline Security.

    Look for CI/CD config files and scan for security stages.
    Satisfied if CI pipeline has security stages; partially if CI exists
    but no security stages.
    """
    ci_files = _dir_or_file_exists(
        project_dir,
        glob_patterns=[
            ".gitlab-ci.yml", ".github/workflows/*.yml",
            ".github/workflows/*.yaml", "Jenkinsfile",
            "azure-pipelines.yml", ".circleci/config.yml",
            "bitbucket-pipelines.yml", ".buildkite/pipeline.yml",
        ],
    )

    if not ci_files:
        return {
            "status": "not_satisfied",
            "evidence": "No CI/CD pipeline configuration files detected.",
            "details": (
                "Expected: .gitlab-ci.yml, .github/workflows/*.yml, "
                "Jenkinsfile, or azure-pipelines.yml."
            ),
        }

    # Scan CI files for security-related stages/jobs
    security_patterns = [
        r"\bsast\b|static.analysis|security.scan",
        r"\bbandit\b|\btrivy\b|\bsnyk\b|\bgrype\b|\bsemgrep\b",
        r"security|vulnerability|audit|secret.detect",
        r"container.scan|image.scan|dependency.check",
    ]
    security_found = []
    for ci_path in ci_files:
        try:
            with open(ci_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            for pattern in security_patterns:
                if re.search(pattern, content, re.IGNORECASE):
                    security_found.append(ci_path)
                    break
        except Exception:
            continue

    if security_found:
        return {
            "status": "satisfied",
            "evidence": (
                f"Pipeline security stages found in {len(security_found)} "
                f"CI/CD configuration file(s)."
            ),
            "details": "; ".join(
                os.path.basename(f) for f in security_found[:5]
            ),
        }

    return {
        "status": "partially_satisfied",
        "evidence": (
            f"CI/CD configuration found ({len(ci_files)} file(s)) but "
            f"no security stages detected."
        ),
        "details": (
            "Pipeline exists but lacks security stages. Expected: SAST, "
            "dependency audit, secret detection, or container scanning stages."
        ),
    }


def _check_artifact_integrity(project_dir):
    """IVV-26: Artifact Integrity.

    Look for SBOM files, checksums (SHA256SUMS, *.sha256, *.sig),
    signed artifacts. Also check for cosign, sigstore, GPG signature files.
    Satisfied if integrity artifacts found.
    """
    # SBOM files
    sbom_files = _dir_or_file_exists(
        project_dir,
        glob_patterns=[
            "*sbom*.json", "*bom*.xml", "*sbom*.xml",
            "*cyclonedx*", "*spdx*",
        ],
    )

    # Checksum and signature files
    integrity_files = _dir_or_file_exists(
        project_dir,
        glob_patterns=[
            "SHA256SUMS", "*.sha256", "*.sha512", "*.sig",
            "*.asc", "*.gpg", "checksums*", "CHECKSUMS*",
            "*cosign*", "*sigstore*", "*.intoto.jsonl",
            "*.provenance", "*provenance*.json",
        ],
    )

    all_found = list(set(sbom_files + integrity_files))
    if all_found:
        return {
            "status": "satisfied",
            "evidence": (
                f"Artifact integrity mechanisms found: {len(all_found)} artifact(s)."
            ),
            "details": "; ".join(
                os.path.basename(f) for f in all_found[:5]
            ),
        }
    return {
        "status": "not_satisfied",
        "evidence": "No artifact integrity mechanisms detected (SBOM, checksums, signatures).",
        "details": (
            "Expected: SBOM files, SHA256SUMS, *.sha256, *.sig, "
            "cosign signatures, or provenance attestations."
        ),
    }


def _check_config_hardening(project_dir):
    """IVV-27: Configuration Hardening.

    Check Dockerfiles for STIG hardening: non-root USER, drop ALL capabilities,
    read-only rootfs, minimal base image.
    Satisfied if all Dockerfiles hardened; partially if some; not_satisfied if none.
    """
    dockerfiles = _dir_or_file_exists(
        project_dir,
        glob_patterns=["Dockerfile*", "*.dockerfile"],
    )
    if not dockerfiles:
        return {
            "status": "not_satisfied",
            "evidence": "No Dockerfiles found in the project.",
            "details": "Cannot verify configuration hardening without container definitions.",
        }

    hardened_count = 0
    hardening_evidence = []

    for df_path in dockerfiles:
        try:
            with open(df_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except Exception:
            continue

        checks = {
            "non_root_user": bool(
                re.search(r"USER\s+(?!root)\S+", content)
            ),
            "drop_capabilities": bool(
                re.search(
                    r"drop.*ALL|securityContext.*drop|cap_drop",
                    content, re.IGNORECASE | re.DOTALL,
                )
            ),
            "read_only_rootfs": bool(
                re.search(
                    r"readOnlyRootFilesystem|read.only",
                    content, re.IGNORECASE,
                )
            ),
            "minimal_base": bool(
                re.search(
                    r"FROM.*(:slim|:alpine|-slim|-minimal|distroless|hardened)",
                    content, re.IGNORECASE,
                )
            ),
        }
        passed = sum(checks.values())
        if passed >= 2:
            hardened_count += 1
            hardening_evidence.append(
                f"{os.path.basename(df_path)}: {passed}/4 hardening checks passed"
            )

    if hardened_count == len(dockerfiles):
        return {
            "status": "satisfied",
            "evidence": (
                f"All {hardened_count} Dockerfile(s) show STIG hardening patterns."
            ),
            "details": "; ".join(hardening_evidence),
        }
    elif hardened_count > 0:
        return {
            "status": "partially_satisfied",
            "evidence": (
                f"{hardened_count}/{len(dockerfiles)} Dockerfile(s) show hardening."
            ),
            "details": "; ".join(hardening_evidence),
        }
    return {
        "status": "not_satisfied",
        "evidence": "Dockerfiles found but lack STIG hardening patterns.",
        "details": (
            "Expected: non-root USER, drop ALL capabilities, "
            "read-only rootfs, minimal base image."
        ),
    }


def _check_rollback_capability(project_dir):
    """IVV-28: Rollback Capability.

    Look for rollback scripts, K8s deployment with rollout strategy,
    blue-green/canary patterns. Scan for rollback, kubectl rollout undo,
    deployment strategy, revisionHistoryLimit.
    Satisfied if rollback mechanism found.
    """
    # Look for explicit rollback scripts/docs
    found_files = _dir_or_file_exists(
        project_dir,
        glob_patterns=[
            "*rollback*", "rollback.*", "rollback_*.py",
            "rollback_*.sh", "*rollback*.yml", "*rollback*.yaml",
        ],
    )
    if found_files:
        return {
            "status": "satisfied",
            "evidence": (
                f"Rollback artifacts found: {len(found_files)} item(s)."
            ),
            "details": "; ".join(
                os.path.basename(f) for f in found_files[:5]
            ),
        }

    # Scan K8s manifests and IaC for rollback patterns
    rollback_patterns = [
        r"rollback|roll.back",
        r"kubectl.*rollout.*undo|rollout.*restart",
        r"strategy:\s*(RollingUpdate|Recreate|BlueGreen|Canary)",
        r"revisionHistoryLimit",
        r"blue.green|canary|rolling.update",
        r"deployment.*strategy|maxUnavailable|maxSurge",
    ]
    extensions = (".yaml", ".yml", ".tf", ".py", ".sh", ".json")
    matched, total = _scan_files(project_dir, extensions, rollback_patterns)

    if matched:
        return {
            "status": "satisfied",
            "evidence": (
                f"Rollback patterns found in {len(matched)} file(s)."
            ),
            "details": "; ".join(
                os.path.basename(f) for f in matched[:5]
            ),
        }

    if total == 0:
        return {
            "status": "not_satisfied",
            "evidence": "No deployment or infrastructure files found to assess.",
            "details": "Project directory lacks YAML, Terraform, or deployment scripts.",
        }

    return {
        "status": "not_satisfied",
        "evidence": "No rollback mechanism detected in deployment configuration.",
        "details": (
            "Expected: rollback scripts, kubectl rollout undo, "
            "deployment strategy, or revisionHistoryLimit in K8s manifests."
        ),
    }


# -----------------------------------------------------------------
# Requirement-to-check mapping
# -----------------------------------------------------------------

AUTO_CHECKS = {
    "IVV-01": _check_req_completeness,
    "IVV-02": _check_req_consistency,
    "IVV-03": _check_req_testability,
    "IVV-05": _check_design_architecture,
    "IVV-08": _check_independent_sast,
    "IVV-09": _check_coding_standards,
    "IVV-10": _check_code_review_completion,
    "IVV-11": _check_complexity_metrics,
    "IVV-12": _check_test_coverage,
    "IVV-13": _check_test_plan,
    "IVV-14": _check_security_tests,
    "IVV-15": _check_bdd_coverage,
    "IVV-17": _check_e2e_tests,
    "IVV-19": _check_rtm_exists,
    "IVV-25": _check_pipeline_security,
    "IVV-26": _check_artifact_integrity,
    "IVV-27": _check_config_hardening,
    "IVV-28": _check_rollback_capability,
}


# -----------------------------------------------------------------
# Status mapping helpers
# -----------------------------------------------------------------

def _map_check_status(check_status):
    """Map auto-check result status to IV&V assessment status.

    IV&V assessment status values: pass, fail, partial, not_applicable,
    deferred, not_assessed.
    """
    mapping = {
        "satisfied": "pass",
        "partially_satisfied": "partial",
        "not_satisfied": "fail",
        "not_applicable": "not_applicable",
    }
    return mapping.get(check_status, "not_assessed")


def _map_priority_to_severity(priority):
    """Map requirement priority to finding severity.

    Priority: critical, high, medium, low
    Severity: critical, high, moderate, low
    """
    mapping = {
        "critical": "critical",
        "high": "high",
        "medium": "moderate",
        "low": "low",
    }
    return mapping.get(priority, "moderate")


# -----------------------------------------------------------------
# Core assessment function
# -----------------------------------------------------------------

def run_ivv_assessment(
    project_id,
    process_area="all",
    project_dir=None,
    gate=False,
    output_path=None,
    db_path=None,
):
    """Run IV&V assessment per IEEE 1012 and DoD standards.

    Args:
        project_id: The project identifier.
        process_area: Filter to a specific process area or "all".
        project_dir: Project directory for automated file-based checks.
        gate: If True, evaluate the IV&V gate (0 critical findings = pass).
        output_path: Override output directory for the assessment report.
        db_path: Override database path.

    Returns:
        Dict with assessment results, summary, scores, gate result,
        and output file path.
    """
    conn = _get_connection(db_path)
    try:
        project = _get_project(conn, project_id)

        # Load IV&V requirements catalog
        ivv_data = _load_ivv_requirements()
        metadata = ivv_data.get("metadata", {})
        requirements = ivv_data.get("requirements", [])

        # Filter by process area if specified
        if process_area != "all":
            requirements = [
                r for r in requirements
                if r["process_area"] == process_area
            ]
            if not requirements:
                raise ValueError(
                    f"No requirements found for process area '{process_area}'. "
                    f"Valid areas: {', '.join(PROCESS_AREAS)}."
                )

        # Resolve project directory for auto-checks
        if project_dir and Path(project_dir).is_dir():
            can_auto_check = True
        elif (
            project.get("directory_path")
            and Path(project["directory_path"]).is_dir()
        ):
            project_dir = project["directory_path"]
            can_auto_check = True
        else:
            can_auto_check = False

        now = datetime.utcnow()
        results = []
        findings = []

        # -- Assess each requirement --
        for req in requirements:
            req_id = req["id"]
            automation_level = req.get("automation_level", "manual")
            check_status = "not_assessed"
            ivv_status = "not_assessed"
            evidence = ""
            details = ""
            notes = ""

            if automation_level == "auto" and can_auto_check:
                if req_id in AUTO_CHECKS:
                    try:
                        check_result = AUTO_CHECKS[req_id](project_dir)
                        check_status = check_result["status"]
                        ivv_status = _map_check_status(check_status)
                        evidence = check_result["evidence"]
                        details = check_result.get("details", "")
                    except Exception as e:
                        ivv_status = "not_assessed"
                        evidence = f"Auto-check error: {e}"
                        notes = "Auto-check failed; manual review required."
                else:
                    ivv_status = "not_assessed"
                    evidence = (
                        "No automated check implemented for this requirement."
                    )
                    notes = "Manual review required."

            elif automation_level == "auto" and not can_auto_check:
                ivv_status = "not_assessed"
                evidence = (
                    "No project directory available for automated scanning."
                )
                notes = "Provide --project-dir to enable auto-checks."

            elif automation_level == "semi" and can_auto_check:
                if req_id in AUTO_CHECKS:
                    try:
                        check_result = AUTO_CHECKS[req_id](project_dir)
                        check_status = check_result["status"]
                        ivv_status = _map_check_status(check_status)
                        evidence = check_result["evidence"]
                        details = check_result.get("details", "")
                        notes = (
                            "Semi-automated check completed. "
                            "Manual review required to verify full compliance."
                        )
                    except Exception as e:
                        ivv_status = "not_assessed"
                        evidence = f"Partial auto-check error: {e}"
                        notes = (
                            "Semi-automated check failed; "
                            "full manual review required."
                        )
                else:
                    ivv_status = "not_assessed"
                    evidence = (
                        "Semi-automated: no automated component implemented."
                    )
                    notes = (
                        f"Manual review required. Evidence needed: "
                        f"{req.get('evidence_required', 'See requirement description.')}"
                    )

            elif automation_level == "semi" and not can_auto_check:
                ivv_status = "not_assessed"
                evidence = (
                    "Semi-automated check requires project directory."
                )
                notes = (
                    f"Manual review required. Evidence needed: "
                    f"{req.get('evidence_required', 'See requirement description.')}"
                )

            else:
                # manual automation_level
                ivv_status = "not_assessed"
                evidence = "Manual assessment required."
                notes = (
                    f"This requirement must be verified manually. "
                    f"Evidence needed: "
                    f"{req.get('evidence_required', 'See requirement description.')}"
                )

            result_entry = {
                "requirement_id": req_id,
                "process_area": req["process_area"],
                "process_area_code": req.get("process_area_code", ""),
                "title": req["title"],
                "description": req["description"],
                "verification_type": req.get("verification_type", "verification"),
                "priority": req.get("priority", "medium"),
                "automation_level": automation_level,
                "nist_controls": req.get("nist_controls", []),
                "status": ivv_status,
                "evidence": evidence,
                "details": details,
                "notes": notes,
            }
            results.append(result_entry)

            # -- Upsert into ivv_assessments table --
            try:
                conn.execute(
                    """INSERT OR REPLACE INTO ivv_assessments
                       (project_id, assessment_date, assessor, process_area,
                        verification_type, requirement_id, status,
                        evidence_description, evidence_path,
                        automation_result, notes, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        project_id,
                        now.isoformat(),
                        "icdev-ivv-engine",
                        req["process_area"],
                        req.get("verification_type", "verification"),
                        req_id,
                        ivv_status,
                        evidence,
                        details if details else None,
                        json.dumps({
                            "automation_level": automation_level,
                            "check_function": (
                                AUTO_CHECKS[req_id].__name__
                                if req_id in AUTO_CHECKS
                                else None
                            ),
                        }),
                        notes if notes else None,
                        now.isoformat(),
                    ),
                )
            except Exception as e:
                print(
                    f"Warning: Could not upsert assessment for {req_id}: {e}",
                    file=sys.stderr,
                )

            # -- Generate findings for failed checks --
            if ivv_status == "fail":
                finding_id = (
                    f"IVV-F-{project_id[:8]}-{req_id}-"
                    f"{now.strftime('%Y%m%d')}"
                )
                severity = _map_priority_to_severity(
                    req.get("priority", "medium")
                )
                finding = {
                    "finding_id": finding_id,
                    "severity": severity,
                    "process_area": req["process_area"],
                    "title": f"IV&V Finding: {req['title']}",
                    "description": (
                        f"Requirement {req_id} ({req['title']}) failed "
                        f"IV&V assessment. {evidence}"
                    ),
                    "recommendation": (
                        f"Address the following: "
                        f"{req.get('evidence_required', req['description'])}"
                    ),
                }
                findings.append(finding)

                try:
                    conn.execute(
                        """INSERT OR IGNORE INTO ivv_findings
                           (project_id, finding_id, severity, process_area,
                            title, description, recommendation, status)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            project_id,
                            finding_id,
                            severity,
                            req["process_area"],
                            finding["title"],
                            finding["description"],
                            finding["recommendation"],
                            "open",
                        ),
                    )
                except Exception as e:
                    print(
                        f"Warning: Could not insert finding {finding_id}: {e}",
                        file=sys.stderr,
                    )

        conn.commit()

        # -- Build summary by process area (9 areas) --
        summary = {}
        for area in PROCESS_AREAS:
            summary[area] = {
                "total": 0,
                "pass": 0,
                "partial": 0,
                "fail": 0,
                "not_assessed": 0,
                "not_applicable": 0,
                "deferred": 0,
            }

        for r in results:
            area = r["process_area"]
            if area not in summary:
                summary[area] = {
                    "total": 0, "pass": 0, "partial": 0,
                    "fail": 0, "not_assessed": 0,
                    "not_applicable": 0, "deferred": 0,
                }
            summary[area]["total"] += 1
            st = r["status"]
            if st in summary[area]:
                summary[area][st] += 1

        # -- Calculate scores per area --
        area_scores = {}
        for area in PROCESS_AREAS:
            s = summary.get(area, {})
            total = s.get("total", 0)
            na = s.get("not_applicable", 0)
            deferred = s.get("deferred", 0)
            assessable = total - na - deferred
            if assessable > 0:
                score = 100.0 * (
                    s.get("pass", 0) + s.get("partial", 0) * 0.5
                ) / assessable
            else:
                score = 0.0
            code = PROCESS_AREA_CODES.get(area, area[:4].upper())
            area_scores[code] = round(score, 1)

        # Verification score: average of REQ, DES, CODE, RTM, SEC, BLD, PROC
        verification_scores = [
            area_scores[code]
            for code in VERIFICATION_AREAS
            if code in area_scores
        ]
        verification_score = (
            round(sum(verification_scores) / len(verification_scores), 1)
            if verification_scores
            else 0.0
        )

        # Validation score: average of TEST, INT
        validation_scores = [
            area_scores[code]
            for code in VALIDATION_AREAS
            if code in area_scores
        ]
        validation_score = (
            round(sum(validation_scores) / len(validation_scores), 1)
            if validation_scores
            else 0.0
        )

        # Overall score: 0.6 * verification + 0.4 * validation
        overall_score = round(
            0.6 * verification_score + 0.4 * validation_score, 1
        )

        # -- Gate evaluation: 0 critical findings = PASS --
        critical_findings = [
            f for f in findings if f["severity"] == "critical"
        ]
        gate_passed = len(critical_findings) == 0
        gate_result = {
            "evaluated": gate,
            "passed": gate_passed,
            "critical_findings_count": len(critical_findings),
            "critical_findings": [
                f"{f['finding_id']}: {f['title']}"
                for f in critical_findings
            ],
            "reason": (
                "PASS: 0 critical IV&V findings"
                if gate_passed
                else (
                    f"FAIL: {len(critical_findings)} critical IV&V finding(s): "
                    f"{', '.join(f['finding_id'] for f in critical_findings)}"
                )
            ),
        }

        # -- Update ivv_certifications table --
        open_count = len(findings)
        critical_count = len(critical_findings)
        cert_status = "in_progress"
        if gate_passed and overall_score >= 80.0:
            cert_status = "submitted"
        elif not gate_passed:
            cert_status = "denied"

        try:
            conn.execute(
                """INSERT OR REPLACE INTO ivv_certifications
                   (project_id, certification_type, status,
                    verification_score, validation_score, overall_score,
                    ivv_authority, open_findings_count,
                    critical_findings_count, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    project_id,
                    "IV&V",
                    cert_status,
                    verification_score,
                    validation_score,
                    overall_score,
                    "icdev-ivv-engine",
                    open_count,
                    critical_count,
                    now.isoformat(),
                ),
            )
            conn.commit()
        except Exception as e:
            print(
                f"Warning: Could not update ivv_certifications: {e}",
                file=sys.stderr,
            )

        # -- Generate CUI-marked Markdown report --
        cui_config = _load_cui_config()
        doc_header = cui_config.get(
            "document_header", "CUI // SP-CTI"
        ).strip()
        doc_footer = cui_config.get(
            "document_footer", "CUI // SP-CTI"
        ).strip()

        lines = [
            doc_header,
            "",
            "# IV&V Assessment Report -- IEEE 1012",
            "",
            f"**Project:** {project.get('name', project_id)} ({project_id})",
            f"**Assessment Date:** {now.strftime('%Y-%m-%d %H:%M UTC')}",
            f"**Assessor:** ICDEV IV&V Engine (automated)",
            f"**Process Area Scope:** {process_area}",
            f"**IEEE 1012 Version:** IEEE 1012-2016",
            (
                f"**Source Standards:** "
                f"{metadata.get('source', 'IEEE 1012-2016, DoDI 5000.87, DoDI 8510.01')}"
            ),
            f"**Classification:** CUI // SP-CTI",
            "",
            "---",
            "",
            "## Executive Summary",
            "",
        ]

        # Summary table
        lines.append(
            "| Process Area | Total | Pass | Partial | Fail "
            "| Not Assessed | N/A | Deferred | Score |"
        )
        lines.append(
            "|--------------|-------|------|---------|------"
            "|--------------|-----|----------|-------|"
        )

        grand_total = {
            "total": 0, "pass": 0, "partial": 0,
            "fail": 0, "not_assessed": 0,
            "not_applicable": 0, "deferred": 0,
        }

        for area in PROCESS_AREAS:
            s = summary.get(area, {})
            if s.get("total", 0) == 0:
                continue
            code = PROCESS_AREA_CODES.get(area, "")
            score = area_scores.get(code, 0.0)
            lines.append(
                f"| {area} | {s['total']} | {s['pass']} | "
                f"{s['partial']} | {s['fail']} | "
                f"{s['not_assessed']} | {s['not_applicable']} | "
                f"{s['deferred']} | {score:.1f}% |"
            )
            for key in grand_total:
                grand_total[key] += s.get(key, 0)

        lines.append(
            f"| **Total** | **{grand_total['total']}** | "
            f"**{grand_total['pass']}** | "
            f"**{grand_total['partial']}** | "
            f"**{grand_total['fail']}** | "
            f"**{grand_total['not_assessed']}** | "
            f"**{grand_total['not_applicable']}** | "
            f"**{grand_total['deferred']}** | |"
        )
        lines.append("")

        # Scores section
        lines.extend([
            "## IV&V Scores",
            "",
            f"| Metric | Score |",
            f"|--------|-------|",
            f"| Verification Score | {verification_score:.1f}% |",
            f"| Validation Score | {validation_score:.1f}% |",
            f"| **Overall IV&V Score** | **{overall_score:.1f}%** |",
            "",
            (
                f"*Scoring: Overall = 0.6 x Verification + 0.4 x Validation. "
                f"Per-area = 100 x (pass + partial x 0.5) / assessable.*"
            ),
            "",
        ])

        # Area score breakdown
        lines.extend([
            "### Score Breakdown by Area",
            "",
            "| Area Code | Score |",
            "|-----------|-------|",
        ])
        for area in PROCESS_AREAS:
            code = PROCESS_AREA_CODES.get(area, "")
            if code in area_scores:
                category = (
                    "Verification" if code in VERIFICATION_AREAS
                    else "Validation"
                )
                lines.append(
                    f"| {code} ({area}) | {area_scores[code]:.1f}% "
                    f"[{category}] |"
                )
        lines.append("")

        # Gate evaluation section
        if gate:
            gate_label = "PASS" if gate_result["passed"] else "**FAIL**"
            lines.extend([
                "## IV&V Gate Evaluation",
                "",
                f"**Gate Result:** {gate_label}",
                f"**Criteria:** 0 critical IV&V findings",
                (
                    f"**Critical Findings:** "
                    f"{gate_result['critical_findings_count']}"
                ),
                "",
            ])
            if gate_result["critical_findings"]:
                lines.append("**Critical Findings:**")
                for cf in gate_result["critical_findings"]:
                    lines.append(f"- {cf}")
                lines.append("")

        # Certification status
        lines.extend([
            "## IV&V Certification Status",
            "",
            f"**Status:** {cert_status.replace('_', ' ').title()}",
            f"**Open Findings:** {open_count}",
            f"**Critical Findings:** {critical_count}",
            "",
        ])

        # Findings section
        if findings:
            lines.extend([
                "---",
                "",
                "## IV&V Findings",
                "",
            ])
            for f in findings:
                lines.extend([
                    f"### {f['finding_id']}",
                    "",
                    f"**Severity:** {f['severity'].upper()}  ",
                    f"**Process Area:** {f['process_area']}  ",
                    f"**Title:** {f['title']}",
                    "",
                    f"**Description:** {f['description']}",
                    "",
                    f"**Recommendation:** {f['recommendation']}",
                    "",
                    "---",
                    "",
                ])

        lines.extend(["---", ""])

        # -- Detailed findings per process area --
        lines.append("## Detailed Assessment Results")
        lines.append("")

        for area in PROCESS_AREAS:
            area_results = [
                r for r in results if r["process_area"] == area
            ]
            if not area_results:
                continue

            code = PROCESS_AREA_CODES.get(area, "")
            score = area_scores.get(code, 0.0)
            lines.append(f"### {area} ({code}) -- {score:.1f}%")
            lines.append("")

            for r in area_results:
                status_display = r["status"].replace("_", " ").title()
                priority_display = r["priority"].upper()
                vtype_display = r["verification_type"].title()
                nist_str = (
                    ", ".join(r["nist_controls"])
                    if r["nist_controls"]
                    else "N/A"
                )

                lines.extend([
                    f"#### {r['requirement_id']}: {r['title']}",
                    "",
                    f"**Type:** {vtype_display}  ",
                    f"**Priority:** {priority_display}  ",
                    f"**Status:** {status_display}  ",
                    f"**Automation Level:** {r['automation_level']}  ",
                    f"**NIST Controls:** {nist_str}",
                    "",
                    f"**Evidence:** {r['evidence']}",
                    "",
                ])
                if r["details"]:
                    lines.append(f"**Details:** {r['details']}")
                    lines.append("")
                if r["notes"]:
                    lines.append(f"**Notes:** {r['notes']}")
                    lines.append("")

                lines.extend(["---", ""])

        # Append CUI footer
        lines.extend([doc_footer, ""])
        content = "\n".join(lines)

        # -- Write output file --
        if output_path:
            out_dir = Path(output_path)
        else:
            dir_path = project.get("directory_path", "")
            if dir_path:
                out_dir = Path(dir_path) / "compliance"
            else:
                out_dir = BASE_DIR / ".tmp" / "compliance" / project_id
        out_dir.mkdir(parents=True, exist_ok=True)

        area_suffix = (
            process_area.lower().replace(" ", "_").replace("/", "_")
            if process_area != "all"
            else "all"
        )
        out_file = (
            out_dir
            / f"ivv_1012_{project_id}_{area_suffix}_"
            f"{now.strftime('%Y%m%d_%H%M%S')}.md"
        )

        with open(out_file, "w", encoding="utf-8") as f:
            f.write(content)

        # -- Log audit event --
        _log_audit_event(
            conn,
            project_id,
            f"IV&V assessment completed ({process_area})",
            {
                "process_area": process_area,
                "requirements_assessed": len(results),
                "findings_generated": len(findings),
                "verification_score": verification_score,
                "validation_score": validation_score,
                "overall_score": overall_score,
                "summary": {k: v for k, v in grand_total.items()},
                "gate_result": gate_result,
                "certification_status": cert_status,
                "output_file": str(out_file),
            },
            out_file,
        )

        # -- Console output --
        print(f"IV&V assessment completed:")
        print(f"  File: {out_file}")
        print(f"  Scope: {process_area}")
        print(f"  Requirements assessed: {len(results)}")
        print(f"  Findings generated: {len(findings)}")

        for area in PROCESS_AREAS:
            s = summary.get(area, {})
            if s.get("total", 0) == 0:
                continue
            code = PROCESS_AREA_CODES.get(area, "")
            score = area_scores.get(code, 0.0)
            print(
                f"  {area} ({code}): "
                f"PASS={s['pass']} "
                f"PARTIAL={s['partial']} "
                f"FAIL={s['fail']} "
                f"NOT_ASSESSED={s['not_assessed']} "
                f"Score={score:.1f}%"
            )

        print(f"\n  Verification Score: {verification_score:.1f}%")
        print(f"  Validation Score:   {validation_score:.1f}%")
        print(f"  Overall IV&V Score: {overall_score:.1f}%")
        print(f"  Certification:      {cert_status}")

        if gate:
            print(f"\n  Gate: {gate_result['reason']}")

        return {
            "output_file": str(out_file),
            "results": results,
            "findings": findings,
            "summary": summary,
            "scores": {
                "area_scores": area_scores,
                "verification_score": verification_score,
                "validation_score": validation_score,
                "overall_score": overall_score,
            },
            "gate_result": gate_result,
            "certification_status": cert_status,
        }

    finally:
        conn.close()


# -----------------------------------------------------------------
# Public alias
# -----------------------------------------------------------------

def assess_project(project_id, process_area="all", **kwargs):
    """Alias for run_ivv_assessment for convenient programmatic use."""
    return run_ivv_assessment(
        project_id, process_area=process_area, **kwargs
    )


# -----------------------------------------------------------------
# CLI entrypoint
# -----------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run IV&V assessment per IEEE 1012"
    )
    parser.add_argument(
        "--project-id", required=True, help="Project ID"
    )
    parser.add_argument(
        "--process-area",
        default="all",
        choices=["all"] + PROCESS_AREAS,
        help="Process area to assess (default: all)",
    )
    parser.add_argument(
        "--project-dir",
        help="Project directory for automated file-based checks",
    )
    parser.add_argument(
        "--gate",
        action="store_true",
        help="Evaluate IV&V gate (0 critical findings = pass)",
    )
    parser.add_argument(
        "--output-dir",
        help="Output directory for the assessment report",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=DB_PATH,
        help="Override database path",
    )
    args = parser.parse_args()

    try:
        result = run_ivv_assessment(
            project_id=args.project_id,
            process_area=args.process_area,
            project_dir=args.project_dir,
            gate=args.gate,
            output_path=args.output_dir,
            db_path=args.db_path,
        )
        print(json.dumps({
            "output_file": result.get("output_file"),
            "scores": result.get("scores"),
            "summary": result.get("summary"),
            "gate_result": result.get("gate_result"),
            "certification_status": result.get("certification_status"),
            "findings_count": len(result.get("findings", [])),
        }, indent=2))

        if args.gate and not result["gate_result"]["passed"]:
            sys.exit(1)

    except (FileNotFoundError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
