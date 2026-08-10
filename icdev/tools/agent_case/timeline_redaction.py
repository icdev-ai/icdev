#!/usr/bin/env python3
# CUI // SP-CTI
"""AGOV CASE — redact what an operator SEES, never what the evidence IS.

A case timeline is read by a human and, via ``case_bundler``, carried to
another machine. Both are disclosures: a command line captured in
``hook_events.payload`` can hold a bearer token, an SSN typed into a CLI, or a
protected program name. This module is the seam that masks them, using the same
stack that backs the ``redaction_detect`` / ``redaction_anonymize`` tools —
``tools/redaction/detector.py`` plus ``tools/redaction/anonymizer.py``.

**The redaction is a projection, not a mutation.** ``session_timeline`` keeps
each source row verbatim under ``entry["record"]`` and puts the masked strings
in ``entry["display"]``. That split is load-bearing rather than stylistic:
``bundle_verifier`` re-computes the ``hook_events`` HMACs and the migration-149
audit hash chain over those exact bytes, so a redacted record would verify as
TAMPERED. Masking the record would not protect a secret either — it would only
destroy the operator's ability to prove the record is authentic. Which fields
reach a disclosure is the bundler's decision (agov-case-02), not this module's.

Two properties this module has to guarantee, because
:func:`session_timeline.build_timeline` promises a byte-identical result across
runs and redaction sits inside that promise:

1. **No LLM in the path.** The detector's Ollama NER layer is switched off
   here. It is a network call to a generative model; asking it twice can return
   two different answers, and one non-reproducible field makes the whole
   timeline unusable as the basis of a bundle manifest.
2. **A private surrogate registry.** Surrogate numbering (``[PROGRAM_NAME_1]``)
   is assigned in encounter order from a counter. Each redactor gets its own
   registry session, so the counter starts at zero every run rather than
   continuing from whatever another caller left in ``redaction_registry``.

Credential patterns are opt-in platform-wide (see
``detection.secret_patterns`` in args/redaction_config.yaml) and this module
opts in: the whole point of rendering a command is that the operator can read
it, and a command is exactly where a token ends up.

Usage:
    from tools.agent_case.timeline_redaction import TimelineRedactor
    redactor = TimelineRedactor()
    masked, entities = redactor.redact("curl -H 'Bearer sk-live-...'")
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

# Run by path, sys.path[0] is this file's own directory — never the import root.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.redaction.anonymizer import RedactionAnonymizer  # noqa: E402
from tools.redaction.detector import RedactionDetector, load_config  # noqa: E402

# Impact level used when the record carries no usable classification. IL4 is
# ICDEV's CUI default and is what the rest of the redaction stack assumes.
DEFAULT_IMPACT_LEVEL = "IL4"

# Classification marking -> impact level. A SECRET record is redacted harder
# than a CUI one because IL6 in args/redaction_config.yaml redacts outright
# where IL4 would surrogate or mask.
_CLASSIFICATION_IMPACT = {
    "UNCLASSIFIED": "IL2",
    "U": "IL2",
    "CUI": "IL4",
    "SECRET": "IL6",
    "TOP SECRET": "IL6",
}


def impact_level_for(classification) -> str:
    """Impact level for a record's classification marking.

    An unrecognized or missing marking gets the CUI default rather than the
    most permissive one: guessing "probably public" about a forensic record is
    the wrong direction to be wrong in.
    """
    if not classification:
        return DEFAULT_IMPACT_LEVEL
    key = str(classification).strip().upper()
    for marking, level in _CLASSIFICATION_IMPACT.items():
        if key == marking or key.startswith(marking + " ") or key.startswith(marking + "/"):
            return level
    return DEFAULT_IMPACT_LEVEL


class TimelineRedactor:
    """Reproducible, cached redaction of timeline display strings.

    Args:
        audit: Write a ``redaction_audit`` row per anonymized string. Off by
            default — building a timeline is a read, and one audit INSERT per
            rendered field would make reading a session write hundreds of rows.
            The bundler turns it on when it exports, which is the moment a
            disclosure actually happens.
        detect_secrets: Load the credential patterns. On by default here.
    """

    def __init__(self, audit: bool = False, detect_secrets: bool = True):
        config = copy.deepcopy(load_config())
        config.setdefault("audit", {})["enabled"] = bool(audit)
        detector = RedactionDetector(
            config=config,
            use_ollama_ner=False,
            detect_secrets=detect_secrets,
        )
        self._anonymizer = RedactionAnonymizer(config=config, detector=detector)
        # (text, impact_level) -> (masked, entity_types). Repeated strings are
        # the norm in a timeline (the same tool name on every row), and a cache
        # keeps surrogate numbering stable for them as well as saving the work.
        self._cache: dict = {}

    def redact(self, text, impact_level: str = DEFAULT_IMPACT_LEVEL):
        """Mask one string. Returns ``(masked_text, sorted_entity_types)``.

        Non-strings and empty strings pass through untouched with no entities:
        a record id or a NULL is not text to scan, and coercing it to ``str``
        just to run regexes over it would invent findings.
        """
        if not isinstance(text, str) or not text.strip():
            return text, []
        key = (text, impact_level)
        if key in self._cache:
            return self._cache[key]

        result = self._anonymizer.anonymize(text, impact_level=impact_level)
        entities = sorted({d.entity_type for d in result.detections})
        masked = (result.anonymized_text, entities)
        self._cache[key] = masked
        return masked

    def redact_mapping(self, mapping: dict, impact_level: str = DEFAULT_IMPACT_LEVEL):
        """Mask every value of a flat dict. Returns ``(masked, entity_types)``.

        Keys are never scanned. A key is a schema name chosen by ICDEV, not
        user content, and redacting it would rename the field.
        """
        masked = {}
        entities = set()
        for key in sorted(mapping):
            value, found = self.redact(mapping[key], impact_level=impact_level)
            masked[key] = value
            entities.update(found)
        return masked, sorted(entities)
