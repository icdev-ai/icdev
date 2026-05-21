# CUI // SP-CTI
"""Pillar 3 — Testing: framework, coverage, unit/integration tests, CI automation."""
from __future__ import annotations

import pathlib

from tools.ai_augmentation.agent_readiness.pillars._base import (
    Criterion,
    CriterionResult,
    Pillar,
    _exists,
    _glob_files,
    _read,
    _search,
)


def _check_test_framework(repo: pathlib.Path) -> CriterionResult:
    cid = "test-framework"
    if _exists(repo, "pytest.ini", "conftest.py"):
        return CriterionResult(cid, True, "pytest configured")
    pyproject = _read(repo, "pyproject.toml")
    if pyproject and _search(pyproject, r"\[tool\.pytest"):
        return CriterionResult(cid, True, "pytest configured in pyproject.toml")
    if _exists(repo, "jest.config.js", "jest.config.ts", "jest.config.mjs", "jest.config.cjs"):
        return CriterionResult(cid, True, "Jest configured")
    if _exists(repo, "vitest.config.ts", "vitest.config.js", "vitest.config.mjs"):
        return CriterionResult(cid, True, "Vitest configured")
    cargo = _read(repo, "Cargo.toml")
    if cargo:
        return CriterionResult(cid, True, "Rust (built-in #[test] framework)")
    go_tests = _glob_files(repo, "**/*_test.go")
    if go_tests:
        return CriterionResult(cid, True, f"Go test files found ({len(go_tests)} files)")
    pom = _read(repo, "pom.xml")
    if pom and _search(pom, r"junit|testng"):
        return CriterionResult(cid, True, "Java test framework in pom.xml")
    gradle = _read(repo, "build.gradle.kts") or _read(repo, "build.gradle") or ""
    if _search(gradle, r"junit|kotest|testImplementation"):
        return CriterionResult(cid, True, "Test framework found in Gradle build file")
    return CriterionResult(cid, False, "No test framework configuration found.",
                           "Add pytest, Jest, Vitest, or language-native test runner config.")


def _check_test_files_exist(repo: pathlib.Path) -> CriterionResult:
    cid = "test-files-exist"
    patterns = ["**/test_*.py", "**/*_test.py", "**/tests/**/*.py",
                "**/*.test.ts", "**/*.spec.ts", "**/*.test.js", "**/*.spec.js",
                "**/*_test.go", "**/src/test/**/*.java"]
    for pat in patterns:
        hits = _glob_files(repo, pat)
        if hits:
            return CriterionResult(cid, True, f"Test files found ({len(hits)} matching {pat})")
    return CriterionResult(cid, False, "No test files detected.",
                           "Create test files following naming conventions (test_*.py, *.test.ts, etc.).")


def _check_coverage_configured(repo: pathlib.Path) -> CriterionResult:
    cid = "coverage-configured"
    if _exists(repo, ".coveragerc", "coverage.ini"):
        return CriterionResult(cid, True, "Python coverage config found")
    pyproject = _read(repo, "pyproject.toml")
    if pyproject and _search(pyproject, r"\[tool\.coverage"):
        return CriterionResult(cid, True, "Coverage configured in pyproject.toml")
    pkg = _read(repo, "package.json")
    if pkg and _search(pkg, r'"coverage"|"c8"|"istanbul"'):
        return CriterionResult(cid, True, "Coverage tool found in package.json")
    cargo = _read(repo, "Cargo.toml")
    if cargo and "cargo-tarpaulin" in cargo:
        return CriterionResult(cid, True, "cargo-tarpaulin coverage configured")
    return CriterionResult(cid, False, "No coverage tool configured.",
                           "Add pytest-cov, c8, or cargo-tarpaulin to measure test coverage.")


def _check_coverage_threshold(repo: pathlib.Path) -> CriterionResult:
    cid = "coverage-threshold"
    for fn in [".coveragerc", "coverage.ini", "setup.cfg"]:
        content = _read(repo, fn)
        if content and _search(content, r"fail_under\s*=\s*\d+"):
            return CriterionResult(cid, True, f"Coverage threshold (fail_under) set in {fn}")
    pyproject = _read(repo, "pyproject.toml")
    if pyproject and _search(pyproject, r"fail.under\s*="):
        return CriterionResult(cid, True, "Coverage threshold set in pyproject.toml")
    pkg = _read(repo, "package.json")
    if pkg and _search(pkg, r'"branches"\s*:\s*\d+|"lines"\s*:\s*\d+'):
        return CriterionResult(cid, True, "Coverage threshold set in package.json")
    return CriterionResult(cid, False, "No coverage threshold enforced.",
                           "Set fail_under in .coveragerc or thresholds in package.json.")


def _check_bdd_scenarios(repo: pathlib.Path) -> CriterionResult:
    cid = "bdd-scenarios"
    feature_files = _glob_files(repo, "**/*.feature")
    if feature_files:
        return CriterionResult(cid, True, f"BDD feature files found ({len(feature_files)} files)")
    behave = _exists(repo, "features", "behave.ini")
    if behave:
        return CriterionResult(cid, True, f"Behave BDD setup found: {behave}")
    cucumber = _glob_files(repo, "**/cucumber.json") + _glob_files(repo, "**/cucumber.js")
    if cucumber:
        return CriterionResult(cid, True, "Cucumber BDD configured")
    return CriterionResult(cid, False, "No BDD test scenarios found.",
                           "Add .feature files with Gherkin scenarios for BDD coverage.")


def _check_ci_tests(repo: pathlib.Path) -> CriterionResult:
    cid = "ci-tests"
    ci_files = (
        _glob_files(repo, ".github/workflows/*.yml")
        + _glob_files(repo, ".github/workflows/*.yaml")
        + [repo / ".gitlab-ci.yml"]
        + [repo / ".circleci/config.yml"]
        + [repo / "Jenkinsfile"]
    )
    test_patterns = r"\bpytest\b|\bnpm test\b|\bnpm run test\b|\bgo test\b|\bcargo test\b|\bjunit\b|\bvitest\b|\bjest\b"
    for f in ci_files:
        p = pathlib.Path(f)
        if p.exists():
            content = p.read_text(encoding="utf-8", errors="replace")
            if _search(content, test_patterns):
                return CriterionResult(cid, True, f"Test step found in CI: {p.name}")
    return CriterionResult(cid, False, "No test step found in CI pipeline.",
                           "Add a test step to your CI workflow (GitHub Actions, GitLab CI, etc.).")


def _check_mutation_testing(repo: pathlib.Path) -> CriterionResult:
    cid = "mutation-testing"
    pyproject = _read(repo, "pyproject.toml")
    if pyproject and _search(pyproject, r"mutmut|cosmic.ray"):
        return CriterionResult(cid, True, "Python mutation testing configured")
    pkg = _read(repo, "package.json")
    if pkg and _search(pkg, r"stryker|mutant"):
        return CriterionResult(cid, True, "JS/TS mutation testing (Stryker) configured")
    if _exists(repo, "stryker.config.json", "stryker.config.js", ".stryker.json"):
        return CriterionResult(cid, True, "Stryker mutation testing config found")
    return CriterionResult(cid, False, "No mutation testing configured.",
                           "Add mutmut (Python) or Stryker (JS/TS) for mutation test coverage.")


def _check_integration_tests(repo: pathlib.Path) -> CriterionResult:
    cid = "integration-tests"
    for pat in ["**/test*integration*", "**/integration*test*", "**/e2e/**", "**/tests/integration/**"]:
        hits = _glob_files(repo, pat)
        if hits:
            return CriterionResult(cid, True, f"Integration/e2e test files found ({len(hits)} files)")
    return CriterionResult(cid, False, "No integration or e2e test directory found.",
                           "Add tests/integration/ or e2e/ directory for end-to-end coverage.")


PILLAR = Pillar(
    id="testing",
    name="Testing",
    description="Test framework, coverage, CI automation, BDD, mutation, and integration tests.",
    criteria=[
        Criterion("test-framework", "Test framework", "A test framework is configured.", "testing", 2, _check_test_framework),
        Criterion("test-files-exist", "Test files exist", "Test files following naming conventions are present.", "testing", 1, _check_test_files_exist),
        Criterion("coverage-configured", "Coverage configured", "A code coverage tool is configured.", "testing", 3, _check_coverage_configured),
        Criterion("coverage-threshold", "Coverage threshold", "A minimum coverage threshold is enforced.", "testing", 4, _check_coverage_threshold),
        Criterion("bdd-scenarios", "BDD scenarios", "Gherkin .feature files exist for BDD coverage.", "testing", 4, _check_bdd_scenarios),
        Criterion("ci-tests", "Tests in CI", "Test step is present in the CI pipeline.", "testing", 2, _check_ci_tests),
        Criterion("mutation-testing", "Mutation testing", "Mutation testing is configured.", "testing", 5, _check_mutation_testing),
        Criterion("integration-tests", "Integration tests", "Integration or e2e test directory exists.", "testing", 3, _check_integration_tests),
    ],
)
