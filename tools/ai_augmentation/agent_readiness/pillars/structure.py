# CUI // SP-CTI
"""Pillar 4 — Structure: project layout, src/ layout, monorepo detection, gitignore."""
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


def _check_gitignore(repo: pathlib.Path) -> CriterionResult:
    cid = "gitignore-present"
    found = _exists(repo, ".gitignore")
    if found:
        content = _read(repo, ".gitignore") or ""
        if len(content.splitlines()) >= 5:
            return CriterionResult(cid, True, ".gitignore found with meaningful rules")
        return CriterionResult(cid, False, ".gitignore found but very sparse.",
                               "Expand .gitignore to cover build artifacts, secrets, and IDE files.")
    return CriterionResult(cid, False, "No .gitignore file found.",
                           "Add a .gitignore to exclude build artifacts and secrets from version control.")


def _check_src_layout(repo: pathlib.Path) -> CriterionResult:
    cid = "src-layout"
    # Python src/ layout
    if (repo / "src").is_dir():
        return CriterionResult(cid, True, "Python src/ layout detected")
    # Java/Kotlin src/main/
    if (repo / "src" / "main").is_dir():
        return CriterionResult(cid, True, "Maven/Gradle src/main/ layout detected")
    # Go cmd/ or pkg/ layout
    if (repo / "cmd").is_dir() or (repo / "pkg").is_dir():
        return CriterionResult(cid, True, "Go cmd/pkg layout detected")
    # Rust src/
    if _exists(repo, "Cargo.toml") and (repo / "src").is_dir():
        return CriterionResult(cid, True, "Rust src/ layout detected")
    # Node.js lib/ or dist/
    pkg = _read(repo, "package.json")
    if pkg and ((repo / "lib").is_dir() or (repo / "packages").is_dir()):
        return CriterionResult(cid, True, "Node.js lib/packages layout detected")
    # Generic: look for any coherent source directory
    for d in ["lib", "app", "api", "core", "service"]:
        if (repo / d).is_dir():
            return CriterionResult(cid, True, f"Structured source directory found: {d}/")
    return CriterionResult(cid, False, "No recognizable source layout detected.",
                           "Organize source code under src/, lib/, or language-idiomatic directories.")


def _check_monorepo_tools(repo: pathlib.Path) -> CriterionResult:
    cid = "monorepo-tools"
    pkg = _read(repo, "package.json")
    if pkg and _search(pkg, r'"workspaces"'):
        return CriterionResult(cid, True, "npm/yarn workspaces (monorepo) configured")
    if _exists(repo, "pnpm-workspace.yaml", "pnpm-workspace.yml"):
        return CriterionResult(cid, True, "pnpm workspace (monorepo) configured")
    if _exists(repo, "nx.json", "lerna.json", "turbo.json", "rush.json"):
        found = _exists(repo, "nx.json", "lerna.json", "turbo.json", "rush.json")
        return CriterionResult(cid, True, f"Monorepo tool configured: {found}")
    settings = _read(repo, "settings.gradle") or _read(repo, "settings.gradle.kts") or ""
    if _search(settings, r"include\s*\("):
        return CriterionResult(cid, True, "Gradle multi-project build (monorepo)")
    return CriterionResult(cid, True, "Single-repo project (monorepo tools not required)", skipped=True)


def _check_env_template(repo: pathlib.Path) -> CriterionResult:
    cid = "env-template"
    found = _exists(repo, ".env.example", ".env.template", ".env.sample", ".env.dist")
    if found:
        return CriterionResult(cid, True, f"Environment variable template found: {found}")
    return CriterionResult(cid, False, "No .env.example or .env.template found.",
                           "Add a .env.example to document required environment variables.")


def _check_docker_support(repo: pathlib.Path) -> CriterionResult:
    cid = "docker-support"
    if _exists(repo, "Dockerfile", "Containerfile"):
        return CriterionResult(cid, True, "Dockerfile/Containerfile found")
    if _exists(repo, "docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"):
        return CriterionResult(cid, True, "docker-compose file found")
    return CriterionResult(cid, False, "No container configuration found.",
                           "Add a Dockerfile or docker-compose.yml for reproducible environments.")


PILLAR = Pillar(
    id="structure",
    name="Structure",
    description="Project layout, source directories, monorepo tools, environment templates, container support.",
    criteria=[
        Criterion("gitignore-present", ".gitignore present", "A .gitignore exists with meaningful rules.", "structure", 1, _check_gitignore),
        Criterion("src-layout", "Source layout", "Source code is organized under a structured directory.", "structure", 2, _check_src_layout),
        Criterion("monorepo-tools", "Monorepo tools", "Monorepo tooling is configured if multi-package.", "structure", 3, _check_monorepo_tools),
        Criterion("env-template", "Env var template", "A .env.example template documents required env vars.", "structure", 2, _check_env_template),
        Criterion("docker-support", "Container support", "A Dockerfile or docker-compose file is present.", "structure", 3, _check_docker_support),
    ],
)
