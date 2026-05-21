# CUI // SP-CTI
"""Pillar 8 — IL Classification (ICDEV): CUI markings, classification headers, IL metadata."""
from __future__ import annotations

import pathlib

from tools.ai_augmentation.agent_readiness.pillars._base import (
    Criterion,
    CriterionResult,
    Pillar,
    _glob_files,
    _read,
    _search,
)

# Recognized classification banner patterns
_CUI_PATTERNS = r"CUI\s*//|CONTROLLED\s+UNCLASSIFIED|CUI\s+BASIC|CUI\s+SPECIFIED"
_CLASS_HEADER = r"#\s*CUI|#\s*CONTROLLED\s+UNCLASSIFIED|#\s*SECRET|#\s*TOP\s+SECRET"
_IL_PATTERN = r"\bIL[4-6]\b|\bimpact\s+level\s+[4-6]\b"


def _check_cui_file_headers(repo: pathlib.Path) -> CriterionResult:
    cid = "cui-file-headers"
    py_files = _glob_files(repo, "**/*.py")
    if not py_files:
        return CriterionResult(cid, True, "No Python source files; CUI header check skipped.", skipped=True)
    sample = py_files[:30]
    marked = []
    for f in sample:
        content = f.read_text(encoding="utf-8", errors="replace")
        first_lines = "\n".join(content.splitlines()[:5])
        if _search(first_lines, _CLASS_HEADER) or _search(first_lines, _CUI_PATTERNS):
            marked.append(f.name)
    ratio = len(marked) / len(sample)
    if ratio >= 0.5:
        return CriterionResult(cid, True, f"CUI/classification headers found in {len(marked)}/{len(sample)} sampled files")
    if marked:
        return CriterionResult(cid, False, f"Only {len(marked)}/{len(sample)} sampled files have CUI headers.",
                               "Add '# CUI // SP-CTI' or equivalent classification header to all source files.")
    return CriterionResult(cid, False, "No CUI classification headers found in sampled source files.",
                           "Add classification markings to all source files per NIST SP 800-171 requirements.")


def _check_claude_md_classification(repo: pathlib.Path) -> CriterionResult:
    cid = "claude-md-classification"
    claude_md = _read(repo, "CLAUDE.md")
    if not claude_md:
        return CriterionResult(cid, True, "No CLAUDE.md present; check skipped.", skipped=True)
    if _search(claude_md, _CUI_PATTERNS) or _search(claude_md, r"IL[4-6]|FedRAMP|CMMC"):
        return CriterionResult(cid, True, "CLAUDE.md contains classification/compliance context")
    return CriterionResult(cid, False, "CLAUDE.md lacks IL/classification context.",
                           "Add IL level, FedRAMP/CMMC applicability, and CUI handling guidance to CLAUDE.md.")


def _check_il_env_variable(repo: pathlib.Path) -> CriterionResult:
    cid = "il-env-variable"
    for fn in [".env", ".env.example", ".env.template", ".env.sample"]:
        content = _read(repo, fn)
        if content and _search(content, r"IL_LEVEL|IMPACT_LEVEL|ICDEV_IL|FedRAMP_IL"):
            return CriterionResult(cid, True, f"IL level env variable found in {fn}")
    # Check any config yaml for il_level
    for cfg in _glob_files(repo, "args/*.yaml") + _glob_files(repo, "args/*.yml"):
        content = cfg.read_text(encoding="utf-8", errors="replace")
        if _search(content, r"il_level|impact_level"):
            return CriterionResult(cid, True, f"IL level configured in {cfg.name}")
    return CriterionResult(cid, False, "No IL level environment variable or config found.",
                           "Set ICDEV_IL_LEVEL (il4/il5/il6) in .env.example to document the deployment IL.")


def _check_classification_manager_usage(repo: pathlib.Path) -> CriterionResult:
    cid = "classification-manager-usage"
    py_files = _glob_files(repo, "**/*.py")
    for f in py_files:
        content = f.read_text(encoding="utf-8", errors="replace")
        if _search(content, r"classification_manager|ClassificationManager"):
            return CriterionResult(cid, True, f"classification_manager used in {f.name}")
    # Check if the classification_manager tool exists
    if _glob_files(repo, "**/classification_manager.py"):
        return CriterionResult(cid, True, "classification_manager.py tool present in repo")
    return CriterionResult(cid, False, "No classification_manager usage detected.",
                           "Use tools.classification_manager for CUI markings instead of hardcoding banners.")


PILLAR = Pillar(
    id="il-classification",
    name="IL Classification",
    description="CUI markings, classification headers, IL metadata, and classification manager usage.",
    criteria=[
        Criterion("cui-file-headers", "CUI file headers", "Source files carry CUI/classification headers.", "il-classification", 3, _check_cui_file_headers),
        Criterion("claude-md-classification", "CLAUDE.md classification", "CLAUDE.md contains IL/classification context.", "il-classification", 2, _check_claude_md_classification),
        Criterion("il-env-variable", "IL env variable", "IL level is documented in .env or config YAML.", "il-classification", 2, _check_il_env_variable),
        Criterion("classification-manager-usage", "Classification manager", "classification_manager is used for CUI markings.", "il-classification", 3, _check_classification_manager_usage),
    ],
)
