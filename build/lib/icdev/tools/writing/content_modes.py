#!/usr/bin/env python3
# CUI // SP-CTI
"""Content-type rule overrides for WriteGuard.

Each content mode applies targeted rule adjustments to the standard WriteGuard
analysis pipeline — suppressing irrelevant dimensions, adding structural checks,
and injecting mode-specific findings.

Modes
-----
blameless_postmortem : Allow passive voice; flag personal blame language.
policy_language      : Require shall/must; flag weak modals (may/might/should).
adr                  : Require Context/Decision/Consequences/Alternatives sections.
bluf_exec            : First para must contain ask + recommendation + quantified impact.
runbook              : Require numbered steps and rollback section.
rca                  : Require 5 Whys + timeline + impact + root cause + corrective + preventive.

Usage
-----
    from tools.writing.content_modes import apply_mode_overrides, CONTENT_MODES

    result = run_full_quality_check(text)
    result = apply_mode_overrides(result, mode="blameless_postmortem", text=text)
"""

from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# Finding constructors
# ---------------------------------------------------------------------------


def _finding(severity: str, message: str, suggestion: str = "") -> dict[str, Any]:
    return {"severity": severity, "message": message, "suggestion": suggestion}


# ---------------------------------------------------------------------------
# Per-mode structural checks
# ---------------------------------------------------------------------------


def _check_blameless_postmortem(text: str) -> list[dict[str, Any]]:
    """Flag personal blame language. Passive voice is explicitly allowed."""
    findings: list[dict[str, Any]] = []

    # Personal blame patterns: name/pronoun + blame verb
    blame_verbs = (
        r"(?:broke|caused|created|introduced|forgot|failed|made|screwed|messed|"
        r"left|deployed|pushed|merged|ignored|didn't|did not)"
    )
    personal_blame_re = re.compile(
        r"\b(?:[A-Z][a-z]+|he|she|they|you|I)\s+" + blame_verbs,
        re.IGNORECASE,
    )
    for m in personal_blame_re.finditer(text):
        snippet = text[max(0, m.start() - 20) : m.end() + 40].strip()
        findings.append(
            _finding(
                "high",
                f'Personal blame detected: "…{snippet}…"',
                "Reframe as a system/process failure rather than individual blame.",
            )
        )

    # "Fault" attribution
    fault_re = re.compile(r"\b(?:his|her|their|your|my)\s+fault\b", re.IGNORECASE)
    for m in fault_re.finditer(text):
        snippet = text[max(0, m.start() - 20) : m.end() + 30].strip()
        findings.append(
            _finding(
                "high",
                f'Blame attribution detected: "…{snippet}…"',
                'Use "contributing factor" or describe the process gap instead.',
            )
        )

    # Missing timeline section
    if not re.search(r"(?:##?\s*timeline|##?\s*chronolog|##?\s*sequence)", text, re.IGNORECASE):
        findings.append(
            _finding(
                "medium",
                "Missing Timeline section.",
                "Add a ## Timeline section with a chronological list of events.",
            )
        )

    # Missing action items section
    if not re.search(r"(?:##?\s*action\s*items?|##?\s*follow.?up|##?\s*remediation)", text, re.IGNORECASE):
        findings.append(
            _finding(
                "medium",
                "Missing Action Items section.",
                "Add a ## Action Items section with owners and due dates.",
            )
        )

    return findings


def _check_policy_language(text: str) -> list[dict[str, Any]]:
    """Require normative shall/must; flag weak modals."""
    findings: list[dict[str, Any]] = []

    # Must have at least one normative keyword
    has_normative = bool(re.search(r"\b(?:shall|must)\b", text, re.IGNORECASE))
    if not has_normative:
        findings.append(
            _finding(
                "critical",
                "No normative language (shall/must) found.",
                "Policy documents must use 'shall' for mandatory requirements and 'must' for absolute prohibitions.",
            )
        )

    # Flag weak modals in sentence context
    weak_modal_re = re.compile(
        r"(?<![Ss]hall\s)(?<![Mm]ust\s)\b(may|might|should|could|would)\b(?!\s+not\b)",
        re.IGNORECASE,
    )
    weak_seen: set[str] = set()
    for m in weak_modal_re.finditer(text):
        word = m.group(1).lower()
        if word in weak_seen:
            continue
        sentence_start = text.rfind(".", 0, m.start())
        sentence_end = text.find(".", m.end())
        sentence = text[sentence_start + 1 : sentence_end if sentence_end != -1 else m.end() + 80].strip()
        findings.append(
            _finding(
                "medium",
                f'Weak modal "{word}" in: "…{sentence[:120]}…"',
                f'Replace "{word}" with "shall" (mandatory) or "must" (absolute) if this is a requirement.',
            )
        )
        weak_seen.add(word)
        if len(weak_seen) >= 5:
            break  # Cap at 5 unique weak-modal findings

    # Check for section structure
    if not re.search(r"(?:##?\s*purpose|##?\s*scope|##?\s*policy|##?\s*applicability)", text, re.IGNORECASE):
        findings.append(
            _finding(
                "low",
                "Standard policy sections (Purpose, Scope, Policy) not detected.",
                "Consider adding ## Purpose, ## Scope, and ## Policy sections.",
            )
        )

    return findings


def _check_adr(text: str) -> list[dict[str, Any]]:
    """Require Context, Decision, Consequences, Alternatives sections."""
    findings: list[dict[str, Any]] = []

    required_sections = {
        "Context": r"(?:##?\s*context\b)",
        "Decision": r"(?:##?\s*decision\b)",
        "Consequences": r"(?:##?\s*consequences?\b|##?\s*outcomes?\b|##?\s*results?\b)",
        "Alternatives": r"(?:##?\s*alternatives?\b|##?\s*options?\b|##?\s*considered\b)",
    }
    for name, pattern in required_sections.items():
        if not re.search(pattern, text, re.IGNORECASE):
            findings.append(
                _finding(
                    "high",
                    f"Missing required ADR section: {name}.",
                    f"Add a '## {name}' section. ADRs must document all four: Context, Decision, Consequences, Alternatives.",
                )
            )

    # Recommend Status field
    if not re.search(r"(?:##?\s*status\b|status\s*:\s*(?:accepted|proposed|deprecated|superseded))", text, re.IGNORECASE):
        findings.append(
            _finding(
                "low",
                "Missing Status field.",
                "Add 'Status: Accepted | Proposed | Deprecated | Superseded' near the top.",
            )
        )

    return findings


def _check_bluf_exec(text: str) -> list[dict[str, Any]]:
    """First paragraph must contain ask + recommendation + quantified impact."""
    findings: list[dict[str, Any]] = []

    # Extract first non-empty, non-heading paragraph
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    # Skip heading lines
    first_para = ""
    for para in paragraphs:
        if not para.startswith("#"):
            first_para = para
            break

    if not first_para:
        findings.append(_finding("critical", "No opening paragraph detected.", "Start with a BLUF paragraph."))
        return findings

    # 1. Ask or recommendation signal
    ask_re = re.compile(
        r"\b(?:request|recommend|approve|authorize|endorse|direct|require|propose|"
        r"need|ask|seek|urge|advise|suggest)\b",
        re.IGNORECASE,
    )
    has_ask = bool(ask_re.search(first_para))
    if not has_ask:
        findings.append(
            _finding(
                "high",
                "First paragraph missing explicit ask or recommendation.",
                "Open with: 'I recommend…', 'Request approval…', or 'We propose…'",
            )
        )

    # 2. Quantified impact (number, %, $, or time expression)
    quant_re = re.compile(
        r"(?:\$[\d,]+|\d+\s*%|\d+\s*(?:days?|weeks?|months?|hours?|FTE|seats?|users?|nodes?|"
        r"TB|GB|requests?|calls?|incidents?)|\b\d{4,}\b)",
        re.IGNORECASE,
    )
    has_quant = bool(quant_re.search(first_para))
    if not has_quant:
        findings.append(
            _finding(
                "medium",
                "First paragraph lacks quantified impact.",
                "Include a number, percentage, dollar amount, or timeframe (e.g., '30% reduction', '$50K savings').",
            )
        )

    # 3. BLUF label check (optional but strongly recommended)
    if not re.search(r"\bBLUF\b", first_para):
        findings.append(
            _finding(
                "low",
                "Consider labeling the opening paragraph 'BLUF:' for clarity.",
                "Prefix with 'BLUF:' to signal executive-summary intent.",
            )
        )

    return findings


def _check_runbook(text: str) -> list[dict[str, Any]]:
    """Require numbered steps and a rollback/recovery section."""
    findings: list[dict[str, Any]] = []

    # Numbered steps — at least 3 consecutive
    step_re = re.compile(r"^\s*\d+[\.\)]\s+\S", re.MULTILINE)
    steps = step_re.findall(text)
    if len(steps) < 3:
        findings.append(
            _finding(
                "high",
                f"Only {len(steps)} numbered step(s) found — runbooks require at least 3.",
                "Use '1. Step description' format for each action.",
            )
        )

    # Rollback / recovery section
    rollback_re = re.compile(
        r"(?:##?\s*rollback|##?\s*revert|##?\s*recovery|##?\s*undo|##?\s*restore|"
        r"##?\s*contingency|rollback\s*steps?|revert\s*steps?)",
        re.IGNORECASE,
    )
    if not rollback_re.search(text):
        findings.append(
            _finding(
                "high",
                "Missing Rollback / Recovery section.",
                "Add a '## Rollback' section describing how to undo each step.",
            )
        )

    # Prerequisites check
    if not re.search(r"(?:##?\s*prerequisites?|##?\s*requirements?|##?\s*before\s+you\s+begin)", text, re.IGNORECASE):
        findings.append(
            _finding(
                "low",
                "Missing Prerequisites section.",
                "Add '## Prerequisites' listing required access, tools, and environment.",
            )
        )

    # Verification step
    if not re.search(r"(?:verify|validation|confirm|check|test|validate)\s", text, re.IGNORECASE):
        findings.append(
            _finding(
                "medium",
                "No verification step detected.",
                "Include a verification command or health check after the procedure.",
            )
        )

    return findings


def _check_rca(text: str) -> list[dict[str, Any]]:
    """Require 5 Whys + timeline + impact + root cause + corrective + preventive."""
    findings: list[dict[str, Any]] = []

    required = {
        "Timeline": r"(?:##?\s*timeline|##?\s*chronolog|##?\s*sequence\s*of\s*events?)",
        "Impact": r"(?:##?\s*impact|##?\s*affected|##?\s*business\s*impact|##?\s*scope)",
        "Root Cause": r"(?:##?\s*root\s*cause|##?\s*cause\s*analysis|##?\s*contributing\s*factors?)",
        "Corrective Action": r"(?:##?\s*corrective\s*actions?|##?\s*immediate\s*fix|##?\s*short.?term\s*fix)",
        "Preventive Action": r"(?:##?\s*preventive\s*actions?|##?\s*long.?term\s*fix|##?\s*prevention|##?\s*recurrence)",
    }
    for name, pattern in required.items():
        if not re.search(pattern, text, re.IGNORECASE):
            findings.append(
                _finding(
                    "high",
                    f"Missing required RCA section: {name}.",
                    f"Add a '## {name}' section to complete the RCA structure.",
                )
            )

    # 5 Whys — look for "why" repeated ≥ 3 times or explicit "5 Whys" label
    why_count = len(re.findall(r"\bwhy\b", text, re.IGNORECASE))
    has_five_whys_label = bool(re.search(r"\b5\s*whys?\b", text, re.IGNORECASE))
    if not has_five_whys_label and why_count < 3:
        findings.append(
            _finding(
                "medium",
                f"5 Whys analysis not detected (found {why_count} 'why' occurrence(s)).",
                "Add a '## 5 Whys' section or ask 'Why?' at least 5 times to reach root cause.",
            )
        )

    # Detection gap
    if not re.search(r"(?:##?\s*detection|how\s+(?:was\s+it\s+)?detected|alert|monitor|notif)", text, re.IGNORECASE):
        findings.append(
            _finding(
                "low",
                "Detection method not described.",
                "Describe how the issue was detected and what alert/monitor fired.",
            )
        )

    return findings


# ---------------------------------------------------------------------------
# Mode registry
# ---------------------------------------------------------------------------

CONTENT_MODES: dict[str, dict[str, Any]] = {
    "default": {
        "label": "Default",
        "description": "Standard WriteGuard rules — no overrides.",
        "suppressed_dims": [],
        "check": lambda text: [],
    },
    "blameless_postmortem": {
        "label": "Blameless Post-Mortem",
        "description": "Allows passive voice. Flags personal blame language and checks for Timeline and Action Items sections.",
        "suppressed_dims": ["passive_voice"],
        "check": _check_blameless_postmortem,
    },
    "policy_language": {
        "label": "Policy Language",
        "description": "Requires shall/must normative language. Flags weak modals (may/might/should/could).",
        "suppressed_dims": ["cliches"],
        "check": _check_policy_language,
    },
    "adr": {
        "label": "ADR (Architecture Decision Record)",
        "description": "Requires Context, Decision, Consequences, and Alternatives sections. Tone and readability checks relaxed.",
        "suppressed_dims": ["readability", "ai_detection"],
        "check": _check_adr,
    },
    "bluf_exec": {
        "label": "BLUF / Executive Summary",
        "description": "Verifies first paragraph contains an explicit ask, recommendation, and quantified impact.",
        "suppressed_dims": ["cliches", "consistency"],
        "check": _check_bluf_exec,
    },
    "runbook": {
        "label": "Runbook / SOP",
        "description": "Requires numbered steps and a Rollback/Recovery section. Tone checks relaxed.",
        "suppressed_dims": ["tone", "cliches"],
        "check": _check_runbook,
    },
    "rca": {
        "label": "Root Cause Analysis (RCA)",
        "description": "Requires Timeline, Impact, Root Cause, Corrective Action, and Preventive Action sections plus 5 Whys.",
        "suppressed_dims": ["tone", "ai_detection"],
        "check": _check_rca,
    },
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def list_modes() -> list[dict[str, str]]:
    """Return a list of available content modes for UI display."""
    return [
        {"key": key, "label": meta["label"], "description": meta["description"]}
        for key, meta in CONTENT_MODES.items()
    ]


def apply_mode_overrides(result: dict[str, Any], mode: str, text: str) -> dict[str, Any]:
    """Apply content-mode rule overrides to a WriteGuard result dict.

    Modifies *result* in-place and returns it.

    Parameters
    ----------
    result : dict
        Output of ``run_full_quality_check(text)`` with keys:
        ``overall_score``, ``passed``, ``details``, ``recommendations``.
    mode : str
        One of the keys in :data:`CONTENT_MODES`.  ``"default"`` is a no-op.
    text : str
        Original input text (needed for structural checks).

    Returns
    -------
    dict
        Modified result dict with:
        - Suppressed dimensions marked ``status="n/a"`` with findings cleared.
        - ``details["content_mode"]`` dimension added with structural findings.
        - ``overall_score`` and ``passed`` recomputed from active dimensions.
        - ``content_mode`` and ``content_mode_label`` keys added.
    """
    if mode == "default" or mode not in CONTENT_MODES:
        return result

    mode_def = CONTENT_MODES[mode]
    details: dict[str, Any] = result.setdefault("details", {})

    # 1. Suppress specified dimensions
    suppressed: set[str] = set(mode_def.get("suppressed_dims", []))
    for dim in suppressed:
        if dim in details:
            details[dim] = {
                **details[dim],
                "findings": [],
                "status": "n/a",
                "suppressed": True,
                "suppressed_by_mode": mode,
            }

    # 2. Run mode-specific structural check
    mode_findings = mode_def["check"](text)
    critical_count = sum(1 for f in mode_findings if f.get("severity") == "critical")
    high_count = sum(1 for f in mode_findings if f.get("severity") == "high")
    medium_count = sum(1 for f in mode_findings if f.get("severity") == "medium")
    # Score: 100 base − deduction per severity tier
    structural_score = max(
        20.0,
        100.0 - critical_count * 30.0 - high_count * 15.0 - medium_count * 5.0,
    )
    details["content_mode"] = {
        "label": mode_def["label"],
        "score": round(structural_score, 1),
        "status": "ok" if structural_score >= 70 else "needs_review",
        "findings": mode_findings,
        "suppressed": False,
    }

    # 3. Recompute overall_score from active (non-suppressed) dimensions
    active_scores: list[float] = []
    for key, dim in details.items():
        if dim.get("suppressed") or dim.get("status") == "unavailable":
            continue
        if key == "portion_marking" and not dim.get("has_marks", False):
            continue
        score_val = dim.get("score", dim.get("overall_score"))
        if isinstance(score_val, (int, float)):
            active_scores.append(float(score_val))

    new_overall = round(sum(active_scores) / len(active_scores), 1) if active_scores else 50.0
    plagiarism_quality = details.get("plagiarism", {}).get("score", 100)
    new_passed = new_overall >= 60 and (
        plagiarism_quality > 15 if isinstance(plagiarism_quality, (int, float)) else True
    )

    result["overall_score"] = new_overall
    result["passed"] = new_passed
    result["content_mode"] = mode
    result["content_mode_label"] = mode_def["label"]

    # 4. Prepend mode-specific recommendations
    mode_recs = []
    if critical_count:
        mode_recs.append(f"[{mode_def['label']}] {critical_count} critical structural issue(s) must be resolved.")
    if high_count:
        mode_recs.append(f"[{mode_def['label']}] {high_count} high-priority structural gap(s) detected.")
    if mode_recs:
        result["recommendations"] = mode_recs + result.get("recommendations", [])

    return result
