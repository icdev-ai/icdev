# CUI // SP-CTI
"""Pillar 2 — Documentation: README, CHANGELOG, inline docs, contributing guide."""
from __future__ import annotations

import pathlib
from functools import lru_cache
from typing import Any

from tools.ai_augmentation.agent_readiness.pillars._base import (
    Criterion,
    CriterionResult,
    Pillar,
    _exists,
    _glob_files,
    _read,
    _search,
)

# ---------------------------------------------------------------------------
# Anomaly-detection threshold loader
# ---------------------------------------------------------------------------
_ARGS_PATH = pathlib.Path(__file__).parents[4] / "args" / "agent_readiness_config.yaml"
_DEFAULTS: dict[str, Any] = {
    "readme_min_content_length": 80,
    "inline_docs_sample_size": 20,
    "min_jsdoc_files": 2,
    "docstring_ratio_denominator": 3,
}


@lru_cache(maxsize=1)
def _load_thresholds() -> dict[str, Any]:
    """Load documentation-pillar anomaly-detection thresholds from args/agent_readiness_config.yaml.

    Falls back to hard-coded defaults if the config file is absent or malformed.
    """
    try:
        import yaml  # optional dep — present in all ICDEV environments
        raw = _ARGS_PATH.read_text(encoding="utf-8")
        data = yaml.safe_load(raw) or {}
        cfg = data.get("pillars", {}).get("documentation", {})
        readme = cfg.get("readme", {})
        ds = cfg.get("inline_docs", {})
        return {
            "readme_min_content_length": int(readme.get("min_content_length", _DEFAULTS["readme_min_content_length"])),
            "inline_docs_sample_size": int(ds.get("sample_size", _DEFAULTS["inline_docs_sample_size"])),
            "min_jsdoc_files": int(ds.get("min_jsdoc_files", _DEFAULTS["min_jsdoc_files"])),
            "docstring_ratio_denominator": int(ds.get("docstring_ratio_denominator", _DEFAULTS["docstring_ratio_denominator"])),
        }
    except Exception:  # noqa: BLE001
        return dict(_DEFAULTS)


def _check_readme(repo: pathlib.Path) -> CriterionResult:
    cid = "readme-present"
    thresholds = _load_thresholds()
    min_length = thresholds["readme_min_content_length"]
    found = _exists(repo, "README.md", "README.rst", "README.txt", "README")
    if found:
        content = _read(repo, found) or ""
        if len(content) > min_length:
            return CriterionResult(cid, True, f"README found with content: {found}")
        return CriterionResult(cid, False, f"README found but very short ({len(content)} chars, min {min_length}).",
                               "Expand README with installation, usage, and contribution sections.")
    return CriterionResult(cid, False, "No README file found.", "Add a README.md to document the project.")


def _check_changelog(repo: pathlib.Path) -> CriterionResult:
    cid = "changelog-present"
    found = _exists(repo, "CHANGELOG.md", "CHANGELOG.rst", "CHANGELOG.txt", "HISTORY.md",
                    "CHANGES.md", "RELEASES.md")
    if found:
        return CriterionResult(cid, True, f"Changelog found: {found}")
    return CriterionResult(cid, False, "No CHANGELOG file found.",
                           "Add a CHANGELOG.md to track changes per release.")


def _check_contributing_guide(repo: pathlib.Path) -> CriterionResult:
    cid = "contributing-guide"
    found = _exists(repo, "CONTRIBUTING.md", "CONTRIBUTING.rst", ".github/CONTRIBUTING.md")
    if found:
        return CriterionResult(cid, True, f"Contributing guide found: {found}")
    return CriterionResult(cid, False, "No CONTRIBUTING guide found.",
                           "Add a CONTRIBUTING.md to guide new contributors.")


def _check_api_docs(repo: pathlib.Path) -> CriterionResult:
    cid = "api-docs"
    docs_dir = _exists(repo, "docs", "doc", "documentation", "wiki")
    if docs_dir:
        return CriterionResult(cid, True, f"Documentation directory found: {docs_dir}")
    # Check for mkdocs or sphinx config
    if _exists(repo, "mkdocs.yml", "mkdocs.yaml", "docs/conf.py", "sphinx-build.cfg"):
        return CriterionResult(cid, True, "Documentation generator configured (mkdocs/sphinx)")
    pyproject = _read(repo, "pyproject.toml")
    if pyproject and _search(pyproject, r"\[tool\.(mkdocs|sphinx)\]"):
        return CriterionResult(cid, True, "Docs generator configured in pyproject.toml")
    return CriterionResult(cid, False, "No API documentation directory or generator found.",
                           "Add a docs/ directory or configure mkdocs/sphinx.")


def _check_inline_docstrings(repo: pathlib.Path) -> CriterionResult:
    cid = "inline-docstrings"
    thresholds = _load_thresholds()
    sample_size = thresholds["inline_docs_sample_size"]
    min_jsdoc = thresholds["min_jsdoc_files"]
    ratio_denominator = thresholds["docstring_ratio_denominator"]
    py_files = _glob_files(repo, "**/*.py")
    if not py_files:
        ts_files = _glob_files(repo, "**/*.ts") + _glob_files(repo, "**/*.js")
        if not ts_files:
            return CriterionResult(cid, True, "No Python/JS/TS source files found; check skipped.", skipped=True)
        # Check for JSDoc
        sample = ts_files[:sample_size]
        jsdoc_count = sum(1 for f in sample if _search(f.read_text(encoding="utf-8", errors="replace"), r"/\*\*"))
        if jsdoc_count >= min_jsdoc:
            return CriterionResult(cid, True, f"JSDoc comments found in {jsdoc_count}/{len(sample)} sampled files")
        return CriterionResult(cid, False, f"Few JSDoc comments detected ({jsdoc_count}/{len(sample)} sampled files, min {min_jsdoc}).",
                               "Add JSDoc to public functions and classes.")
    sample = py_files[:sample_size]
    docstring_count = sum(
        1 for f in sample
        if _search(f.read_text(encoding="utf-8", errors="replace"), r'""".*?"""', flags=0)
        or _search(f.read_text(encoding="utf-8", errors="replace"), r"'''.*?'''", flags=0)
    )
    min_count = max(1, len(sample) // ratio_denominator)
    if docstring_count >= min_count:
        return CriterionResult(cid, True, f"Docstrings found in {docstring_count}/{len(sample)} sampled Python files")
    return CriterionResult(cid, False, f"Low docstring coverage ({docstring_count}/{len(sample)} sampled files, min {min_count}).",
                           "Add module and function docstrings to improve maintainability.")


PILLAR = Pillar(
    id="documentation",
    name="Documentation",
    description="README, changelog, contributing guide, API docs, and inline docstrings.",
    criteria=[
        Criterion("readme-present", "README present", "A README file exists with meaningful content.", "documentation", 1, _check_readme),
        Criterion("changelog-present", "Changelog present", "A CHANGELOG tracks changes per release.", "documentation", 2, _check_changelog),
        Criterion("contributing-guide", "Contributing guide", "A CONTRIBUTING.md exists for contributors.", "documentation", 2, _check_contributing_guide),
        Criterion("api-docs", "API documentation", "A documentation directory or generator is configured.", "documentation", 3, _check_api_docs),
        Criterion("inline-docstrings", "Inline docstrings", "Source files have docstrings or JSDoc comments.", "documentation", 3, _check_inline_docstrings),
    ],
)
