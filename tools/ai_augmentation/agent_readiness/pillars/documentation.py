# CUI // SP-CTI
"""Pillar 2 — Documentation: README, CHANGELOG, inline docs, contributing guide."""
from __future__ import annotations

import json
import os
import pathlib
import re
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
    "nlp_analyzer_enabled": True,
    "nlp_analyzer_model": "claude-haiku-4-5-20251001",
    "nlp_analyzer_max_tokens": 256,
    "nlp_analyzer_confidence_threshold": 0.7,
    "nlp_analyzer_text_sample_chars": 2000,
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
        inline = cfg.get("inline_docs", {})
        nlp = cfg.get("nlp_analyzer", {})
        return {
            "readme_min_content_length": int(
                readme.get("min_content_length", _DEFAULTS["readme_min_content_length"])
            ),
            "inline_docs_sample_size": int(
                inline.get("sample_size", _DEFAULTS["inline_docs_sample_size"])
            ),
            "min_jsdoc_files": int(
                inline.get("min_jsdoc_files", _DEFAULTS["min_jsdoc_files"])
            ),
            "docstring_ratio_denominator": int(
                inline.get("docstring_ratio_denominator", _DEFAULTS["docstring_ratio_denominator"])
            ),
            "nlp_analyzer_enabled": bool(nlp.get("enabled", _DEFAULTS["nlp_analyzer_enabled"])),
            "nlp_analyzer_model": str(nlp.get("model", _DEFAULTS["nlp_analyzer_model"])),
            "nlp_analyzer_max_tokens": int(nlp.get("max_tokens", _DEFAULTS["nlp_analyzer_max_tokens"])),
            "nlp_analyzer_confidence_threshold": float(
                nlp.get("confidence_threshold", _DEFAULTS["nlp_analyzer_confidence_threshold"])
            ),
            "nlp_analyzer_text_sample_chars": int(
                nlp.get("text_sample_chars", _DEFAULTS["nlp_analyzer_text_sample_chars"])
            ),
        }
    except Exception:  # noqa: BLE001
        return dict(_DEFAULTS)


# ---------------------------------------------------------------------------
# NLP anomaly analyzer — Claude Haiku for documentation quality assessment
# ---------------------------------------------------------------------------


def _nlp_analyze_doc_quality(text: str, task: str) -> "dict | None":
    """Assess documentation quality anomalies using Claude Haiku NLP.

    Returns dict with keys: anomaly (bool), assessment (str), confidence (float).
    Returns None when LLM is unavailable so callers fall back to static thresholds.
    """
    thresholds = _load_thresholds()
    if not thresholds["nlp_analyzer_enabled"]:
        return None
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return None
    try:
        from tools.llm.anthropic_provider import AnthropicLLMProvider
        from tools.llm.provider import LLMRequest
    except ImportError:
        return None

    sample = text[:thresholds["nlp_analyzer_text_sample_chars"]]
    prompt = (
        f"You are a documentation quality analyst. {task}\n\n"
        f"Text:\n{sample}\n\n"
        "Respond ONLY with valid JSON in this exact format: "
        '{"anomaly": false, "assessment": "brief reason", "confidence": 0.85}\n'
        'Set anomaly to true when documentation quality is anomalously low (placeholder text, '
        'missing critical sections, or content that does not actually explain the project). '
        'Set anomaly to false when documentation is adequate or better.'
    )
    try:
        provider = AnthropicLLMProvider(api_key=api_key)
        request = LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=thresholds["nlp_analyzer_max_tokens"],
        )
        model_id = thresholds["nlp_analyzer_model"]
        model_cfg = {"max_output_tokens": thresholds["nlp_analyzer_max_tokens"]}
        response = provider.invoke(request, model_id, model_cfg)
        result_text = response.content.strip()
        json_match = re.search(r'\{.*\}', result_text, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
    except Exception:  # noqa: BLE001
        pass
    return None


def _check_readme(repo: pathlib.Path) -> CriterionResult:
    cid = "readme-present"
    thresholds = _load_thresholds()
    min_length = thresholds["readme_min_content_length"]
    min_confidence = thresholds["nlp_analyzer_confidence_threshold"]
    found = _exists(repo, "README.md", "README.rst", "README.txt", "README")
    if found:
        content = _read(repo, found) or ""
        if len(content) > min_length:
            # NLP secondary pass: detect anomalously low quality despite passing length threshold
            result = _nlp_analyze_doc_quality(
                content,
                "Assess whether this README has anomalously low quality — e.g., placeholder text, "
                "template scaffolding with no real content, or a title/badge-only README that "
                "provides no actual installation or usage guidance.",
            )
            if result and result.get("anomaly") and result.get("confidence", 0) >= min_confidence:
                assessment = result.get("assessment", "low-quality content detected")
                return CriterionResult(
                    cid, False,
                    f"README exists but NLP anomaly detected: {assessment}",
                    "Expand README with installation, usage, and contribution sections.",
                )
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
    ratio_denom = thresholds["docstring_ratio_denominator"]
    min_confidence = thresholds["nlp_analyzer_confidence_threshold"]
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
        # NLP secondary pass: assess whether the lack of JSDoc is an anomaly for this project type
        for f in sample[:3]:
            content = f.read_text(encoding="utf-8", errors="replace")
            result = _nlp_analyze_doc_quality(
                content,
                "Assess whether the absence of JSDoc comments in this TypeScript/JavaScript file "
                "is anomalously low for a project of this complexity — i.e., whether public APIs, "
                "classes, or functions exported here clearly need documentation.",
            )
            if result and result.get("anomaly") and result.get("confidence", 0) >= min_confidence:
                assessment = result.get("assessment", "undocumented public API detected")
                return CriterionResult(
                    cid, False,
                    f"JSDoc anomaly detected via NLP in {f.name}: {assessment}",
                    "Add JSDoc to public functions and classes.",
                )
        return CriterionResult(cid, False, f"Few JSDoc comments detected ({jsdoc_count}/{len(sample)} sampled files, min {min_jsdoc}).",
                               "Add JSDoc to public functions and classes.")
    sample = py_files[:sample_size]
    file_contents = [(f, f.read_text(encoding="utf-8", errors="replace")) for f in sample]
    docstring_count = sum(
        1 for _, content in file_contents
        if _search(content, r'""".*?"""', flags=0) or _search(content, r"'''.*?'''", flags=0)
    )
    if docstring_count >= max(1, len(sample) // ratio_denom):
        return CriterionResult(cid, True, f"Docstrings found in {docstring_count}/{len(sample)} sampled Python files")
    # NLP secondary pass: check whether undocumented files contain complex public APIs
    for f, content in file_contents[:3]:
        if _search(content, r'""".*?"""', flags=0) or _search(content, r"'''.*?'''", flags=0):
            continue  # already has docstrings
        result = _nlp_analyze_doc_quality(
            content,
            "Assess whether the absence of docstrings in this Python file is anomalously low — "
            "i.e., whether it defines public functions, classes, or module-level logic that "
            "clearly require documentation for maintainability.",
        )
        if result and result.get("anomaly") and result.get("confidence", 0) >= min_confidence:
            assessment = result.get("assessment", "undocumented public API detected")
            return CriterionResult(
                cid, False,
                f"Docstring anomaly detected via NLP in {f.name}: {assessment}",
                "Add module and function docstrings to improve maintainability.",
            )
    return CriterionResult(cid, False, f"Low docstring coverage ({docstring_count}/{len(sample)} sampled files).",
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
