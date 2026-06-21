# CUI // SP-CTI
"""Pillar 9 — NIST 800-53 Control References (ICDEV): control IDs in comments or metadata."""
from __future__ import annotations

import json
import os
import pathlib
import re
from functools import lru_cache
from typing import Any, Optional

from tools.ai_augmentation.agent_readiness.pillars._base import (
    Criterion,
    CriterionResult,
    Pillar,
    _glob_files,
    _search,
)

# Matches NIST 800-53 control IDs like AC-1, AC-2(1), AU-12, SC-28, etc.
_NIST_CONTROL_PATTERN = r"\b(AC|AU|CA|CM|CP|IA|IR|MA|MP|PE|PL|PM|PS|RA|SA|SC|SI|SR)-\d+(?:\(\d+\))?\b"

# Matches NIST control family references in documentation
_NIST_DOC_PATTERN = r"NIST\s+(?:SP\s+)?800-53|NIST\s+800-171|control\s+(?:ID|reference|mapping)"

# ---------------------------------------------------------------------------
# Config loader for NLP extractor thresholds
# ---------------------------------------------------------------------------
_ARGS_PATH = pathlib.Path(__file__).parents[4] / "args" / "agent_readiness_config.yaml"
_DEFAULTS: dict[str, Any] = {
    "nlp_extractor_enabled": True,
    "nlp_extractor_model": "claude-haiku-4-5-20251001",
    "nlp_extractor_max_tokens": 256,
    "nlp_extractor_confidence_threshold": 0.7,
    "nlp_extractor_text_sample_chars": 2000,
}


@lru_cache(maxsize=1)
def _load_thresholds() -> dict[str, Any]:
    """Load NIST controls pillar NLP extractor config from args/agent_readiness_config.yaml.

    Falls back to hard-coded defaults if the config file is absent or malformed.
    """
    try:
        import yaml
        raw = _ARGS_PATH.read_text(encoding="utf-8")
        data = yaml.safe_load(raw) or {}
        cfg = data.get("pillars", {}).get("nist_controls", {}).get("nlp_extractor", {})
        return {
            "nlp_extractor_enabled": bool(cfg.get("enabled", _DEFAULTS["nlp_extractor_enabled"])),
            "nlp_extractor_model": str(cfg.get("model", _DEFAULTS["nlp_extractor_model"])),
            "nlp_extractor_max_tokens": int(cfg.get("max_tokens", _DEFAULTS["nlp_extractor_max_tokens"])),
            "nlp_extractor_confidence_threshold": float(
                cfg.get("confidence_threshold", _DEFAULTS["nlp_extractor_confidence_threshold"])
            ),
            "nlp_extractor_text_sample_chars": int(
                cfg.get("text_sample_chars", _DEFAULTS["nlp_extractor_text_sample_chars"])
            ),
        }
    except Exception:  # noqa: BLE001
        return dict(_DEFAULTS)


# ---------------------------------------------------------------------------
# NLP extractor — Claude Haiku for natural-language NIST control reference detection
# ---------------------------------------------------------------------------

def _nlp_extract_nist_refs(text: str, task: str) -> Optional[dict]:
    """Extract NIST 800-53 control references from text using Claude Haiku NLP.

    Returns dict with keys: found (bool), refs (list[str]), confidence (float).
    Returns None when LLM is unavailable so callers fall back to regex.
    """
    thresholds = _load_thresholds()
    if not thresholds["nlp_extractor_enabled"]:
        return None
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return None
    try:
        from tools.llm.anthropic_provider import AnthropicLLMProvider
        from tools.llm.provider import LLMRequest
    except ImportError:
        return None

    sample = text[:thresholds["nlp_extractor_text_sample_chars"]]
    prompt = (
        f"You are a NIST 800-53 compliance analyst. Analyze the following text and {task}.\n\n"
        f"Text:\n{sample}\n\n"
        "Respond ONLY with valid JSON in this exact format: "
        '{"found": true, "refs": ["AC-2", "AU-12", "SC-28"], "confidence": 0.9}\n'
        "Where refs contains NIST 800-53 control IDs (e.g. AC-2, AU-12(3)), control family names "
        "(e.g. Access Control, Audit), or NIST document citations found in the text. "
        'Set found to false and refs to [] when nothing is detected.'
    )
    try:
        provider = AnthropicLLMProvider(api_key=api_key)
        request = LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=thresholds["nlp_extractor_max_tokens"],
        )
        model_id = thresholds["nlp_extractor_model"]
        model_cfg = {"max_output_tokens": thresholds["nlp_extractor_max_tokens"]}
        response = provider.invoke(request, model_id, model_cfg)
        result_text = response.content.strip()
        json_match = re.search(r'\{.*\}', result_text, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
    except Exception:  # noqa: BLE001
        pass
    return None


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

    # Enhanced path: NLP for natural-language control references missed by regex
    thresholds = _load_thresholds()
    min_confidence = thresholds["nlp_extractor_confidence_threshold"]
    for f in py_files[:10]:
        content = f.read_text(encoding="utf-8", errors="replace")
        result = _nlp_extract_nist_refs(
            content,
            "identify any NIST 800-53 control IDs (e.g. AC-2, AU-12) or control family names "
            "referenced in code comments, docstrings, or inline annotations",
        )
        if result and result.get("found") and result.get("confidence", 0) >= min_confidence:
            refs = result.get("refs", [])
            ref_str = ", ".join(refs[:3]) if refs else "detected reference"
            return CriterionResult(cid, True, f"NIST 800-53 control IDs detected via NLP in {f.name}: {ref_str}")

    return CriterionResult(cid, False, "No NIST 800-53 control IDs found in Python source files.",
                           "Reference NIST control IDs (e.g. # NIST: AC-2, AU-12) in relevant code sections.")


def _check_nist_in_docs(repo: pathlib.Path) -> CriterionResult:
    cid = "nist-in-docs"
    doc_files = (
        _glob_files(repo, "docs/**/*.md")
        + _glob_files(repo, "*.md")
        + _glob_files(repo, "docs/**/*.rst")
    )

    # Fast path: regex detection
    for f in doc_files:
        content = f.read_text(encoding="utf-8", errors="replace")
        if _search(content, _NIST_DOC_PATTERN) or re.search(_NIST_CONTROL_PATTERN, content):
            return CriterionResult(cid, True, f"NIST 800-53 reference found in docs: {f.name}")

    # Enhanced path: NLP for natural-language NIST references missed by regex
    thresholds = _load_thresholds()
    min_confidence = thresholds["nlp_extractor_confidence_threshold"]
    for f in doc_files[:5]:
        content = f.read_text(encoding="utf-8", errors="replace")
        result = _nlp_extract_nist_refs(
            content,
            "identify any NIST 800-53 references, control family mentions, or NIST Special Publication "
            "citations written in natural language prose",
        )
        if result and result.get("found") and result.get("confidence", 0) >= min_confidence:
            refs = result.get("refs", [])
            ref_str = ", ".join(refs[:3]) if refs else "natural language reference"
            return CriterionResult(cid, True, f"NIST 800-53 reference detected via NLP in {f.name}: {ref_str}")

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

    # Fast path: regex for crosswalk engine references in Python
    py_files = _glob_files(repo, "**/*.py")
    for f in py_files[:30]:
        content = f.read_text(encoding="utf-8", errors="replace")
        if _search(content, r"crosswalk|CrosswalkEngine|fedramp|cmmc"):
            return CriterionResult(cid, True, f"Crosswalk engine referenced in {f.name}")

    # Enhanced path: NLP for natural-language crosswalk or mapping descriptions
    thresholds = _load_thresholds()
    min_confidence = thresholds["nlp_extractor_confidence_threshold"]
    doc_files = _glob_files(repo, "docs/**/*.md") + _glob_files(repo, "*.md")
    for f in doc_files[:5]:
        content = f.read_text(encoding="utf-8", errors="replace")
        result = _nlp_extract_nist_refs(
            content,
            "identify any NIST 800-53 to FedRAMP, CMMC, or other framework crosswalk mappings, "
            "control correlation tables, or compliance framework translation descriptions",
        )
        if result and result.get("found") and result.get("confidence", 0) >= min_confidence:
            refs = result.get("refs", [])
            ref_str = ", ".join(refs[:3]) if refs else "crosswalk reference"
            return CriterionResult(cid, True, f"NIST crosswalk reference detected via NLP in {f.name}: {ref_str}")

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
