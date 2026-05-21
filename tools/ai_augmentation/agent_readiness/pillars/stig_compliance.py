# CUI // SP-CTI
"""Pillar 10 — STIG Compliance Markers (ICDEV): STIG V-IDs in config, code, or docs."""
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

# STIG vulnerability ID pattern: V-NNNNNN or V-NNNNNN/SV-NNNNNN
_STIG_VID_PATTERN = r"\bV-\d{5,6}\b|\bSV-\d{5,6}r\d+_rule\b"
_STIG_DOC_PATTERN = r"STIG|Security\s+Technical\s+Implementation\s+Guide|DISA\s+STIG"
_CAT_PATTERN = r"\bCAT\s*[I]{1,3}\b|\bCAT-[123]\b|\bCategory\s+[I]{1,3}\b"


def _check_stig_vids_in_code(repo: pathlib.Path) -> CriterionResult:
    cid = "stig-vids-in-code"
    py_files = _glob_files(repo, "**/*.py")
    yaml_files = _glob_files(repo, "**/*.yaml") + _glob_files(repo, "**/*.yml")
    all_files = py_files[:30] + yaml_files[:20]
    hits = []
    for f in all_files:
        content = f.read_text(encoding="utf-8", errors="replace")
        if re.search(_STIG_VID_PATTERN, content):
            hits.append(f.name)
    if hits:
        return CriterionResult(cid, True, f"STIG V-IDs found in {len(hits)} file(s): {', '.join(hits[:5])}")
    return CriterionResult(cid, False, "No STIG V-IDs found in source or config files.",
                           "Reference STIG V-IDs (e.g. # STIG: V-220938) in security-relevant code and config.")


def _check_stig_in_docs(repo: pathlib.Path) -> CriterionResult:
    cid = "stig-in-docs"
    doc_files = (
        _glob_files(repo, "docs/**/*.md") + _glob_files(repo, "*.md")
        + _glob_files(repo, "docs/**/*.txt")
    )
    for f in doc_files:
        content = f.read_text(encoding="utf-8", errors="replace")
        if _search(content, _STIG_DOC_PATTERN) or re.search(_STIG_VID_PATTERN, content):
            return CriterionResult(cid, True, f"STIG reference found in docs: {f.name}")
    return CriterionResult(cid, False, "No STIG references in documentation.",
                           "Add STIG checklist or V-ID references to compliance documentation.")


def _check_stig_checklist(repo: pathlib.Path) -> CriterionResult:
    cid = "stig-checklist"
    # XCCDF or CKL files are standard STIG checklist formats
    checklist_files = (
        _glob_files(repo, "**/*.ckl")
        + _glob_files(repo, "**/*.xml")
        + _glob_files(repo, "**/stig*.yaml")
        + _glob_files(repo, "**/stig*.json")
        + _glob_files(repo, "docs/**/*stig*")
        + _glob_files(repo, "docs/**/*checklist*")
    )
    for f in checklist_files:
        content = f.read_text(encoding="utf-8", errors="replace")
        if re.search(_STIG_VID_PATTERN, content) or _search(content, _STIG_DOC_PATTERN):
            return CriterionResult(cid, True, f"STIG checklist found: {f.name}")
    # Check icdev-comply output area
    if (repo / "docs" / "compliance").is_dir():
        for f in _glob_files(repo / "docs" / "compliance", "*.md"):
            content = f.read_text(encoding="utf-8", errors="replace")
            if re.search(_STIG_VID_PATTERN, content) or _search(content, _STIG_DOC_PATTERN):
                return CriterionResult(cid, True, f"STIG checklist in compliance docs: {f.name}")
    return CriterionResult(cid, False, "No STIG checklist (.ckl, XCCDF, or compliance doc) found.",
                           "Generate a STIG checklist with icdev-comply or store .ckl files in docs/compliance/.")


def _check_cat1_remediation(repo: pathlib.Path) -> CriterionResult:
    cid = "cat1-remediation"
    all_files = _glob_files(repo, "**/*.py")[:20] + _glob_files(repo, "docs/**/*.md")
    for f in all_files:
        content = f.read_text(encoding="utf-8", errors="replace")
        if re.search(_STIG_VID_PATTERN, content) and _search(content, _CAT_PATTERN):
            # Found explicit CAT references alongside V-IDs — indicates active STIG awareness
            return CriterionResult(cid, True, f"CAT severity markers + V-IDs found in {f.name}")
    # If STIG checklist exists with any content, assume CAT1 is being tracked
    checklist_files = _glob_files(repo, "**/*.ckl") + _glob_files(repo, "**/stig*.yaml")
    if checklist_files:
        return CriterionResult(cid, True, f"STIG checklist present; CAT I tracking assumed: {checklist_files[0].name}")
    return CriterionResult(cid, False, "No CAT I STIG remediation evidence found.",
                           "Document CAT I/II/III STIG finding severity in compliance artifacts.")


PILLAR = Pillar(
    id="stig-compliance",
    name="STIG Compliance Markers",
    description="STIG V-IDs in code/config, documentation references, checklist artifacts, and CAT I tracking.",
    criteria=[
        Criterion("stig-vids-in-code", "STIG V-IDs in code", "STIG V-IDs referenced in source or config files.", "stig-compliance", 3, _check_stig_vids_in_code),
        Criterion("stig-in-docs", "STIG in docs", "STIG references appear in project documentation.", "stig-compliance", 2, _check_stig_in_docs),
        Criterion("stig-checklist", "STIG checklist", "A STIG checklist (.ckl, XCCDF) or compliance artifact exists.", "stig-compliance", 4, _check_stig_checklist),
        Criterion("cat1-remediation", "CAT I remediation", "CAT severity markers show active STIG tracking.", "stig-compliance", 4, _check_cat1_remediation),
    ],
)
