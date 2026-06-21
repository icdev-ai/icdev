# CUI // SP-CTI
"""Pillar 1 — Code Quality: style, linting, formatting, dead-code detection."""
from __future__ import annotations

import pathlib

from tools.aiify.agent_readiness.pillars._base import (
    Criterion,
    CriterionResult,
    Pillar,
    _exists,
    _read,
    _search,
)


def _check_linter_configured(repo: pathlib.Path) -> CriterionResult:
    cid = "linter-configured"
    # Dedicated ruff config files
    if _exists(repo, "ruff.toml", ".ruff.toml"):
        return CriterionResult(cid, True, "ruff linter config found (ruff.toml)")
    # pyproject.toml with linter section
    pyproject = _read(repo, "pyproject.toml")
    if pyproject and _search(pyproject, r"\[tool\.(ruff|flake8|pylint)\]"):
        return CriterionResult(cid, True, "Python linter configured in pyproject.toml")
    # .flake8 / setup.cfg / tox.ini with [flake8]
    for fn in [".flake8", "setup.cfg", "tox.ini"]:
        content = _read(repo, fn)
        if content and "[flake8]" in content:
            return CriterionResult(cid, True, f"flake8 linter configured in {fn}")
    # JS/TS
    eslint = _exists(repo, ".eslintrc", ".eslintrc.js", ".eslintrc.json", ".eslintrc.yml",
                     "eslint.config.js", "eslint.config.ts", "eslint.config.mjs")
    if eslint:
        return CriterionResult(cid, True, f"ESLint config found: {eslint}")
    # Other languages
    if _exists(repo, ".golangci.yml", ".golangci.yaml", ".golangci.toml"):
        return CriterionResult(cid, True, "golangci-lint config found")
    if _exists(repo, "detekt.yml", ".detekt.yml"):
        return CriterionResult(cid, True, "Kotlin detekt config found")
    if _exists(repo, ".rubocop.yml"):
        return CriterionResult(cid, True, "Ruby RuboCop config found")
    return CriterionResult(cid, False, "No linter configuration found.",
                           "Add ruff, ESLint, golangci-lint, or another linter config.")


def _check_formatter_configured(repo: pathlib.Path) -> CriterionResult:
    cid = "formatter-configured"
    if _exists(repo, ".prettierrc", ".prettierrc.js", ".prettierrc.json", ".prettierrc.yml",
               "prettier.config.js", "prettier.config.ts"):
        return CriterionResult(cid, True, "Prettier config found")
    pyproject = _read(repo, "pyproject.toml")
    if pyproject and _search(pyproject, r"\[tool\.(black|ruff\.format)\]"):
        return CriterionResult(cid, True, "Python formatter configured in pyproject.toml")
    # ruff.toml also controls formatting in newer ruff versions
    ruff_cfg = _read(repo, "ruff.toml") or _read(repo, ".ruff.toml")
    if ruff_cfg and _search(ruff_cfg, r"\[format\]|line.length"):
        return CriterionResult(cid, True, "Formatter configured in ruff.toml")
    if _exists(repo, ".editorconfig"):
        return CriterionResult(cid, True, "EditorConfig found (baseline formatting rules)")
    if _exists(repo, "rustfmt.toml", ".rustfmt.toml"):
        return CriterionResult(cid, True, "rustfmt config found")
    if _exists(repo, ".clang-format"):
        return CriterionResult(cid, True, "clang-format config found")
    return CriterionResult(cid, False, "No code formatter configured.",
                           "Add Prettier, black/ruff format, rustfmt, or EditorConfig.")


def _check_type_checking(repo: pathlib.Path) -> CriterionResult:
    cid = "type-checking"
    pyproject = _read(repo, "pyproject.toml")
    if pyproject and _search(pyproject, r"\[tool\.(mypy|pyright|pytype)\]"):
        return CriterionResult(cid, True, "Python type checker configured in pyproject.toml")
    if _exists(repo, "mypy.ini", ".mypy.ini", "pyrightconfig.json"):
        return CriterionResult(cid, True, "Python type checker config found")
    if _exists(repo, "tsconfig.json"):
        return CriterionResult(cid, True, "TypeScript tsconfig.json found (type checking enabled)")
    cargo = _read(repo, "Cargo.toml")
    if cargo:
        return CriterionResult(cid, True, "Rust (statically typed by compiler)")
    go_mod = _read(repo, "go.mod")
    if go_mod:
        return CriterionResult(cid, True, "Go (statically typed by compiler)")
    return CriterionResult(cid, False, "No type checking configured.",
                           "Add mypy, pyright, or tsconfig.json for type safety.")


def _check_dead_code_detection(repo: pathlib.Path) -> CriterionResult:
    cid = "dead-code-detection"
    if _exists(repo, "knip.json", "knip.ts", "knip.config.ts", ".knip.json"):
        return CriterionResult(cid, True, "knip dead code detection configured")
    pyproject = _read(repo, "pyproject.toml")
    if pyproject and "vulture" in pyproject:
        return CriterionResult(cid, True, "vulture dead code detection in pyproject.toml")
    if _exists(repo, ".vulture_whitelist.py", "vulture_whitelist.py"):
        return CriterionResult(cid, True, "vulture whitelist found")
    cargo = _read(repo, "Cargo.toml")
    if cargo and "cargo-udeps" in cargo:
        return CriterionResult(cid, True, "cargo-udeps configured")
    pom = _read(repo, "pom.xml")
    if pom and _search(pom, r"spotbugs|findbugs"):
        return CriterionResult(cid, True, "SpotBugs found in pom.xml (dead code detection)")
    return CriterionResult(cid, False, "No dead code detection tool found.",
                           "Add knip, vulture, or cargo-udeps to detect unused code.")


def _check_complexity_limits(repo: pathlib.Path) -> CriterionResult:
    cid = "complexity-limits"
    pyproject = _read(repo, "pyproject.toml")
    if pyproject and _search(pyproject, r"max.complexity|max_complexity"):
        return CriterionResult(cid, True, "Cyclomatic complexity limit set in pyproject.toml")
    for fn in [".flake8", "setup.cfg", "tox.ini"]:
        content = _read(repo, fn)
        if content and "max-complexity" in content:
            return CriterionResult(cid, True, f"Complexity limit configured in {fn}")
    return CriterionResult(cid, False, "No complexity limits configured.",
                           "Set max-complexity in flake8/ruff or eslint-plugin-complexity.")


def _check_pre_commit_hooks(repo: pathlib.Path) -> CriterionResult:
    cid = "pre-commit-hooks"
    if _exists(repo, ".pre-commit-config.yaml", ".pre-commit-config.yml"):
        return CriterionResult(cid, True, "pre-commit hooks configured")
    husky = _exists(repo, ".husky")
    if husky:
        return CriterionResult(cid, True, "Husky git hooks configured")
    lefthook = _exists(repo, "lefthook.yml", ".lefthook.yml")
    if lefthook:
        return CriterionResult(cid, True, "Lefthook git hooks configured")
    return CriterionResult(cid, False, "No pre-commit hooks configured.",
                           "Add .pre-commit-config.yaml or Husky to enforce code quality gates.")


PILLAR = Pillar(
    id="code-quality",
    name="Code Quality",
    description="Style, linting, formatting, type checking, and dead-code detection.",
    criteria=[
        Criterion("linter-configured", "Linter configured", "A linter is configured (ruff, ESLint, golangci-lint).", "code-quality", 1, _check_linter_configured),
        Criterion("formatter-configured", "Formatter configured", "A code formatter is configured.", "code-quality", 2, _check_formatter_configured),
        Criterion("type-checking", "Type checking enabled", "Static type checking is configured.", "code-quality", 3, _check_type_checking),
        Criterion("dead-code-detection", "Dead code detection", "A tool to detect unused code is configured.", "code-quality", 4, _check_dead_code_detection),
        Criterion("complexity-limits", "Complexity limits", "Cyclomatic complexity limits are enforced.", "code-quality", 4, _check_complexity_limits),
        Criterion("pre-commit-hooks", "Pre-commit hooks", "Pre-commit hooks enforce quality gates before commits.", "code-quality", 3, _check_pre_commit_hooks),
    ],
)
