# CUI // SP-CTI
"""Pillar 7 — Security: license, scanning, secrets detection, policy, dep updates."""
from __future__ import annotations

import pathlib

from tools.aiify.agent_readiness.pillars._base import (
    Criterion,
    CriterionResult,
    Pillar,
    _exists,
    _glob_files,
    _read,
    _search,
)


def _check_license(repo: pathlib.Path) -> CriterionResult:
    cid = "license"
    found = _exists(repo, "LICENSE", "LICENSE.md", "LICENSE.txt", "LICENCE", "LICENCE.md")
    if found:
        return CriterionResult(cid, True, f"License file found: {found}")
    return CriterionResult(cid, False, "No LICENSE file found.",
                           "Add a LICENSE file to clarify usage and distribution rights.")


def _check_security_scanning(repo: pathlib.Path) -> CriterionResult:
    cid = "security-scanning"
    ci_files = (
        _glob_files(repo, ".github/workflows/*.yml")
        + _glob_files(repo, ".github/workflows/*.yaml")
    )
    scan_patterns = r"\b(codeql|snyk|trivy|semgrep|sonarqube|sonarcloud|sast|bandit|safety|grype)\b"
    for f in ci_files:
        content = f.read_text(encoding="utf-8", errors="replace")
        if _search(content, scan_patterns):
            return CriterionResult(cid, True, f"Security scanner found in CI: {f.name}")
    gitlab = _read(repo, ".gitlab-ci.yml")
    if gitlab and _search(gitlab, scan_patterns):
        return CriterionResult(cid, True, "Security scanner found in .gitlab-ci.yml")
    if _exists(repo, ".snyk"):
        return CriterionResult(cid, True, "Snyk configuration found")
    pom = _read(repo, "pom.xml")
    if pom and _search(pom, r"dependency-check|owasp"):
        return CriterionResult(cid, True, "OWASP dependency-check found in pom.xml")
    cargo = _read(repo, "Cargo.toml")
    if cargo and "cargo-audit" in cargo:
        return CriterionResult(cid, True, "cargo-audit security scanning configured")
    pyproject = _read(repo, "pyproject.toml")
    if pyproject and _search(pyproject, r"bandit|safety"):
        return CriterionResult(cid, True, "Python security scanner configured in pyproject.toml")
    return CriterionResult(cid, False, "No security scanner configured in CI.",
                           "Add CodeQL, Semgrep, Bandit, Trivy, or Snyk to CI for vulnerability scanning.")


def _check_secrets_detection(repo: pathlib.Path) -> CriterionResult:
    cid = "secrets-detection"
    if _exists(repo, ".gitleaks.toml", ".gitleaks.yml"):
        return CriterionResult(cid, True, "Gitleaks config found")
    precommit = _read(repo, ".pre-commit-config.yaml") or ""
    if _search(precommit, r"detect-secrets|gitleaks|trufflehog"):
        return CriterionResult(cid, True, "Secrets detection hook in .pre-commit-config.yaml")
    ci_files = _glob_files(repo, ".github/workflows/*.yml") + _glob_files(repo, ".github/workflows/*.yaml")
    for f in ci_files:
        content = f.read_text(encoding="utf-8", errors="replace")
        if _search(content, r"gitleaks|detect-secrets|trufflehog|git-secrets"):
            return CriterionResult(cid, True, f"Secrets detection in CI: {f.name}")
    return CriterionResult(cid, False, "No secrets detection tool configured.",
                           "Add gitleaks, detect-secrets, or trufflehog to prevent secret commits.")


def _check_security_policy(repo: pathlib.Path) -> CriterionResult:
    cid = "security-policy"
    found = _exists(repo, "SECURITY.md", ".github/SECURITY.md")
    if found:
        return CriterionResult(cid, True, f"Security policy found: {found}")
    return CriterionResult(cid, False, "No SECURITY.md found.",
                           "Add SECURITY.md to document the vulnerability disclosure process.")


def _check_dep_update_automation(repo: pathlib.Path) -> CriterionResult:
    cid = "dep-update-automation"
    found = _exists(repo, ".github/dependabot.yml", ".github/dependabot.yaml",
                    "renovate.json", ".renovaterc", ".renovaterc.json")
    if found:
        return CriterionResult(cid, True, f"Dependency update automation found: {found}")
    return CriterionResult(cid, False, "No dependency update automation found.",
                           "Add Dependabot or Renovate to automatically update dependencies.")


PILLAR = Pillar(
    id="security",
    name="Security",
    description="License, SAST scanning, secrets detection, security policy, and dependency automation.",
    criteria=[
        Criterion("license", "License file", "A LICENSE file exists.", "security", 1, _check_license),
        Criterion("security-scanning", "Security scanning", "SAST/SCA scanning is configured in CI.", "security", 4, _check_security_scanning),
        Criterion("secrets-detection", "Secrets detection", "A secrets detection tool is configured.", "security", 4, _check_secrets_detection),
        Criterion("security-policy", "Security policy", "A SECURITY.md outlines vulnerability disclosure.", "security", 3, _check_security_policy),
        Criterion("dep-update-automation", "Dep update automation", "Automated dependency updates are configured.", "security", 3, _check_dep_update_automation),
    ],
)
