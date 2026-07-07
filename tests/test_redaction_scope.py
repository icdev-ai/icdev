#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for CMMC L3 RFI redaction enforcement gap-fix.

Verifies the 5 rfi_* LLM functions (rfi_writer_drafting, rfi_editor_drafting,
rfi_reviewer_review, rfi_researcher_knowledge, rfi_compliance_assessment) are
covered by args/redaction_config.yaml's scope.enforced_modules, so raw
uploaded RFI/RFP CUI text is never skipped by GovConSanitizer.sanitize_for_llm
even when routed to local-only Ollama chains (see
tools/redaction/govcon_sanitizer.py:256).
"""

from pathlib import Path

import yaml

BASE_DIR = Path(__file__).resolve().parent.parent

RFI_FUNCTIONS = [
    "rfi_writer_drafting",
    "rfi_editor_drafting",
    "rfi_reviewer_review",
    "rfi_researcher_knowledge",
    "rfi_compliance_assessment",
]

# Content shaped to reliably trip the regex/deny-list detectors even without
# the Ollama NER backend available in CI (see tools/redaction/detector.py).
SAMPLE_CUI_TEXT = (
    "Please contact John Smith at john.smith@example-agency.gov "
    "regarding Contract W91CRB-24-C-0001."
)


def _load_enforced_modules():
    config_path = BASE_DIR / "args" / "redaction_config.yaml"
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)
    return config["scope"]["enforced_modules"]


def test_rfi_functions_in_enforced_modules():
    enforced = _load_enforced_modules()
    for fn in RFI_FUNCTIONS:
        assert fn in enforced, f"{fn} missing from redaction_config.yaml scope.enforced_modules"


def test_proposal_and_specialist_consult_still_enforced():
    """Regression guard: don't accidentally drop pre-existing entries."""
    enforced = _load_enforced_modules()
    for fn in ("proposal_drafting", "requirement_extraction", "bid_scoring", "color_review", "specialist_consult"):
        assert fn in enforced, f"{fn} missing from redaction_config.yaml scope.enforced_modules"


def test_sanitize_for_llm_not_skipped_for_local_only_rfi_functions():
    """Load-bearing check: sanitize_for_llm() must not skip these functions
    when is_local_only=True, since their chains have no cloud fallback today
    (args/llm_config.yaml) and were previously unprotected by content
    sanitization (see tools/redaction/govcon_sanitizer.py:256)."""
    from tools.redaction.govcon_sanitizer import GovConSanitizer

    sanitizer = GovConSanitizer()
    for fn in RFI_FUNCTIONS:
        sanitized, meta = sanitizer.sanitize_for_llm(
            SAMPLE_CUI_TEXT, function_name=fn, impact_level="IL4", is_local_only=True
        )
        assert meta.get("skipped") is not True, f"{fn} was skipped: {meta}"
        assert sanitized != SAMPLE_CUI_TEXT, f"{fn}: no sanitization applied to sample CUI text"
