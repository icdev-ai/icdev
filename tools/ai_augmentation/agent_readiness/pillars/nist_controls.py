# CUI // SP-CTI
"""Pillar 9 — NIST 800-53 Control References (ICDEV): control IDs in comments or metadata."""
from __future__ import annotations

import pathlib
import re

from tools.ai_augmentation.agent_readiness.pillars._base import (
    Criterion,
    CriterionResult,
    Pillar,
    _glob_files,
    _read,
    _search,
)

# Matches NIST 800-53 control IDs like AC-1, AC-2(1), AU-12, SC-28, etc.
_NIST_CONTROL_PATTERN = r"\b(AC|AU|CA|CM|CP|IA|IR|MA|MP|PE|PL|PM|PS|RA|SA|SC|SI|SR)-\d+(?:\(\d+\))?\b"

# Matches NIST control family references in documentation
_NIST_DOC_PATTERN = r"NIST\s+(?:SP\s+)?800-53|NIST\s+800-171|control\s+(?:ID|reference|mapping)"


def _check_control_ids_in_code(repo: pathlib.Path) -> CriterionResult:
    cid = "nist-control-ids-in-code"
    py_files = _glob_files(repo, "**/*.py")
    hits = []
    for f in py_files[:50]:
        content = f.read_text(encoding="utf-8", errors="replace")
        if re.search(_NIST_CONTROL_PATTERN, content):
            hits.append(f.name)
    if hits:
        return CriterionResult(cid, True,
                               f"NIST 800-53 control IDs found in {len(hits)} file(s): {', '.join(hits[:5])}")
    return CriterionResult(cid, False, "No NIST 800-53 control IDs found in Python source files.",
                           "Reference NIST control IDs (e.g. # NIST: AC-2, AU-12) in relevant code sections.")


def _check_nist_in_docs(repo: pathlib.Path) -> CriterionResult:
    cid = "nist-in-docs"
    doc_files = (
        _glob_files(repo, "docs/**/*.md")
        + _glob_files(repo, "*.md")
        + _glob_files(repo, "docs/**/*.rst")
    )
    for f in doc_files:
        content = f.read_text(encoding="utf-8", errors="replace")
        if _search(content, _NIST_DOC_PATTERN) or re.search(_NIST_CONTROL_PATTERN, content):
            return CriterionResult(cid, True, f"NIST 800-53 reference found in docs: {f.name}")
    return CriterionResult(cid, False, "No NIST 800-53 references in documentation.",
                           "Add NIST 800-53 control mappings to architecture or compliance documentation.")


def _check_ssp_present(repo: pathlib.Path) -> CriterionResult:
    cid = "ssp-present"
    # System Security Plan documents
    ssp_files = (
        _glob_files(repo, "**/ssp*.md") + _glob_files(repo, "**/ssp*.yaml")
        + _glob_files(repo, "**/system-security-plan*")
        + _glob_files(repo, "docs/**/*ssp*")
        + _glob_files(repo, "docs/**/*compliance*")
    )
    if ssp_files:
        return CriterionResult(cid, True, f"SSP/compliance document found: {ssp_files[0].name}")
    # Check for compliance artifacts directory
    if (repo / "compliance").is_dir() or (repo / "docs" / "compliance").is_dir():
        return CriterionResult(cid, True, "Compliance artifacts directory found")
    return CriterionResult(cid, False, "No System Security Plan (SSP) or compliance artifact found.",
                           "Generate an SSP with ICDEV icdev-comply or store compliance docs in docs/compliance/.")


def _check_crosswalk_config(repo: pathlib.Path) -> CriterionResult:
    cid = "crosswalk-config"
    # ICDEV crosswalk engine config
    crosswalk_files = (
        _glob_files(repo, "**/crosswalk*.yaml") + _glob_files(repo, "**/crosswalk*.json")
        + _glob_files(repo, "args/*compliance*") + _glob_files(repo, "args/*crosswalk*")
    )
    if crosswalk_files:
        return CriterionResult(cid, True, f"NIST crosswalk config found: {crosswalk_files[0].name}")
    # Check for crosswalk engine usage in Python
    py_files = _glob_files(repo, "**/*.py")
    for f in py_files[:30]:
        content = f.read_text(encoding="utf-8", errors="replace")
        if _search(content, r"crosswalk|CrosswalkEngine|fedramp|cmmc"):
            return CriterionResult(cid, True, f"Crosswalk engine referenced in {f.name}")
    return CriterionResult(cid, False, "No NIST/FedRAMP crosswalk configuration found.",
                           "Configure the crosswalk engine (args/crosswalk.yaml) for NIST → FedRAMP/CMMC mapping.")


PILLAR = Pillar(
    id="nist-controls",
    name="NIST 800-53 Control References",
    description="NIST 800-53 control IDs in code comments, docs, SSP artifacts, and crosswalk configuration.",
    criteria=[
        Criterion("nist-control-ids-in-code", "Control IDs in code", "NIST 800-53 control IDs referenced in source code comments.", "nist-controls", 3, _check_control_ids_in_code),
        Criterion("nist-in-docs", "NIST in docs", "NIST 800-53 referenced in documentation.", "nist-controls", 2, _check_nist_in_docs),
        Criterion("ssp-present", "SSP present", "System Security Plan or compliance artifacts exist.", "nist-controls", 4, _check_ssp_present),
        Criterion("crosswalk-config", "Crosswalk config", "NIST → FedRAMP/CMMC crosswalk is configured.", "nist-controls", 4, _check_crosswalk_config),
    ],
)
