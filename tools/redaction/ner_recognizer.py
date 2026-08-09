#!/usr/bin/env python3
# CUI // SP-CTI
"""Ollama-Based NER Recognizer — air-gap safe PERSON/ORGANIZATION detection.

Replaces spaCy NER for environments where:
    - Python 3.14+ (spaCy incompatible with pydantic v1)
    - Air-gapped networks (no pip install / model downloads)
    - VRAM-constrained (spaCy transformer model requires GPU)

Uses the existing Ollama qwen3.5 (scanner-tier, zero Claude cost) to extract
named entities from text. Falls back to regex heuristics if Ollama unavailable.

The NER results are returned as DetectionResult objects, compatible with
the RedactionDetector pipeline.

CLI:
    python tools/redaction/ner_recognizer.py --extract "John Smith works at DISA" --json
    python tools/redaction/ner_recognizer.py --health --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("icdev.redaction.ner_recognizer")

# ---------------------------------------------------------------------------
# Regex-based NER heuristics (fallback when Ollama unavailable)
# ---------------------------------------------------------------------------

# Common person name patterns (Title Case sequences near context words)
_PERSON_CONTEXT = re.compile(
    r"\b(?:Mr|Mrs|Ms|Dr|Prof|Gen|Col|Maj|Capt|Lt|Sgt|COR|COTR|PM|KO|CO)\b"
    r"\.?\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})",
    re.MULTILINE,
)

# "Contact: First Last" or "POC: First Last" patterns
_POC_PATTERN = re.compile(
    r"(?:contact|poc|point of contact|contracting officer|program manager"
    r"|project lead|key personnel|team lead|principal investigator)"
    r"[:\s]+([A-Z][a-z]+(?:\s+[A-Z]\.?\s*)?[A-Z][a-z]+)",
    re.IGNORECASE,
)

# Email-derived names: john.smith@... → "John Smith"
_EMAIL_NAME_PATTERN = re.compile(
    r"([a-zA-Z]+)\.([a-zA-Z]+)@",
)

# Common federal agencies / organizations (heuristic deny-list)
_FEDERAL_AGENCIES = [
    "Department of Defense",
    "Department of Homeland Security",
    "Department of Veterans Affairs",
    "Department of Health and Human Services",
    "Department of Energy",
    "Department of Commerce",
    "Department of the Interior",
    "Department of Justice",
    "Department of Labor",
    "Department of State",
    "Department of Transportation",
    "Department of Treasury",
    "Defense Information Systems Agency",
    "Defense Intelligence Agency",
    "Defense Logistics Agency",
    "Defense Health Agency",
    "Defense Threat Reduction Agency",
    "Defense Advanced Research Projects Agency",
    "Missile Defense Agency",
    "National Security Agency",
    "National Geospatial-Intelligence Agency",
    "National Reconnaissance Office",
    "General Services Administration",
    "Office of Management and Budget",
    "Small Business Administration",
    "Federal Emergency Management Agency",
    "Centers for Disease Control",
    "Food and Drug Administration",
    "National Institutes of Health",
    "National Aeronautics and Space Administration",
    "Army",
    "Navy",
    "Air Force",
    "Marine Corps",
    "Space Force",
    "Coast Guard",
    "DISA",
    "DIA",
    "DLA",
    "DHA",
    "DTRA",
    "DARPA",
    "MDA",
    "NSA",
    "NGA",
    "NRO",
    "GSA",
    "OMB",
    "SBA",
    "FEMA",
    "CDC",
    "FDA",
    "NIH",
    "NASA",
    "USACE",
    "NAVFAC",
    "AFLCMC",
    "PEO",
    "SPAWAR",
    "NAVSEA",
]


class NERResult:
    """A named entity recognition result."""

    def __init__(self, entity_type: str, text: str, start: int, end: int, score: float, method: str = "regex"):
        self.entity_type = entity_type
        self.text = text
        self.start = start
        self.end = end
        self.score = score
        self.method = method

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entity_type": self.entity_type,
            "text": self.text,
            "start": self.start,
            "end": self.end,
            "score": round(self.score, 3),
            "method": self.method,
        }


# ---------------------------------------------------------------------------
# Ollama NER Extractor
# ---------------------------------------------------------------------------

_NER_PROMPT = """Extract all person names and organization names from this text.
Return ONLY a JSON array of objects. Each object must have:
- "text": the exact text span
- "type": either "PERSON" or "ORGANIZATION"

If no entities found, return [].
Do not include any explanation, just the JSON array.

Text:
{text}"""


def _extract_via_ollama(text: str, model: str = "gemma3:latest") -> List[NERResult]:
    """Use local Ollama to extract named entities.

    Scanner-tier: zero Claude cost, runs entirely on local GPU.
    """
    try:
        # Truncate to avoid overwhelming small models
        text_truncated = text[:3000] if len(text) > 3000 else text

        prompt = _NER_PROMPT.format(text=text_truncated)

        # Use low-level chat API (no thinking mode)
        from tools.http.client import request as _http_request

        resp = _http_request(
            "POST",
            "http://localhost:11434/api/chat",
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "options": {"num_predict": 512, "temperature": 0.1},
            },
            timeout=(2, 8),  # (connect_timeout, read_timeout)
        )

        if resp.status_code != 200:
            return []

        content = resp.json().get("message", {}).get("content", "")

        # Extract JSON from response (may be wrapped in markdown code block)
        json_match = re.search(r"\[.*\]", content, re.DOTALL)
        if not json_match:
            return []

        entities = json.loads(json_match.group())
        results = []
        for ent in entities:
            ent_text = ent.get("text", "")
            ent_type = ent.get("type", "")
            if not ent_text or ent_type not in ("PERSON", "ORGANIZATION"):
                continue

            # Find position in original text
            idx = text.find(ent_text)
            if idx == -1:
                # Try case-insensitive
                idx = text.lower().find(ent_text.lower())
            if idx == -1:
                continue

            results.append(
                NERResult(
                    entity_type=ent_type,
                    text=ent_text,
                    start=idx,
                    end=idx + len(ent_text),
                    score=0.75,  # Ollama NER confidence
                    method="ollama_ner",
                )
            )

        return results

    except Exception as e:
        logger.debug("Ollama NER extraction failed: %s", e)
        # Mark Ollama as temporarily unavailable to skip future attempts
        import requests as _req

        if isinstance(e, (_req.exceptions.Timeout, _req.exceptions.ConnectionError)):
            _OLLAMA_CACHE["available"] = False
        return []


# ---------------------------------------------------------------------------
# Regex NER Fallback
# ---------------------------------------------------------------------------


def _extract_via_regex(text: str) -> List[NERResult]:
    """Regex-based NER for person names and organizations."""
    results = []

    # Person names via title patterns
    for match in _PERSON_CONTEXT.finditer(text):
        name = match.group(1)
        full_start = match.start(1)
        results.append(
            NERResult(
                entity_type="PERSON",
                text=name,
                start=full_start,
                end=full_start + len(name),
                score=0.7,
                method="regex_title",
            )
        )

    # Person names via POC context
    for match in _POC_PATTERN.finditer(text):
        name = match.group(1)
        full_start = match.start(1)
        results.append(
            NERResult(
                entity_type="PERSON",
                text=name,
                start=full_start,
                end=full_start + len(name),
                score=0.65,
                method="regex_poc",
            )
        )

    # Person names from email addresses
    for match in _EMAIL_NAME_PATTERN.finditer(text):
        first = match.group(1).capitalize()
        last = match.group(2).capitalize()
        name = f"{first} {last}"
        # Don't add DetectionResult here — the email itself is caught by the detector
        # But record the derived name for surrogate mapping
        results.append(
            NERResult(
                entity_type="PERSON",
                text=name,
                start=match.start(),
                end=match.end() - 1,  # Exclude @
                score=0.5,
                method="regex_email_derived",
            )
        )

    # Federal agencies via deny-list
    text_lower = text.lower()
    for agency in _FEDERAL_AGENCIES:
        agency_lower = agency.lower()
        idx = text_lower.find(agency_lower)
        while idx != -1:
            results.append(
                NERResult(
                    entity_type="ORGANIZATION",
                    text=text[idx : idx + len(agency)],
                    start=idx,
                    end=idx + len(agency),
                    score=0.9 if len(agency) > 5 else 0.6,
                    method="regex_agency_list",
                )
            )
            idx = text_lower.find(agency_lower, idx + len(agency))

    return results


# ---------------------------------------------------------------------------
# Combined NER Extractor
# ---------------------------------------------------------------------------

# Module-level Ollama availability cache (avoids 3s health check per instance)
_OLLAMA_CACHE: Dict[str, Any] = {"available": None, "checked_at": 0.0}
_OLLAMA_CACHE_TTL = 60.0  # Re-check every 60 seconds


def _check_ollama_cached() -> bool:
    """Check Ollama availability with module-level TTL cache.

    Uses /api/tags to quickly verify Ollama is responding to HTTP requests.
    The inference call in _extract_via_ollama has its own short timeout (3s)
    so stalled inference attempts fail fast without blocking the pipeline.
    """
    import time as _t

    now = _t.time()
    if _OLLAMA_CACHE["available"] is not None and (now - _OLLAMA_CACHE["checked_at"]) < _OLLAMA_CACHE_TTL:
        return _OLLAMA_CACHE["available"]
    try:
        from tools.http.client import request

        resp = request("GET", "http://localhost:11434/api/tags", timeout=1.5)
        _OLLAMA_CACHE["available"] = resp.status_code == 200
    except Exception:
        _OLLAMA_CACHE["available"] = False
    _OLLAMA_CACHE["checked_at"] = now
    return _OLLAMA_CACHE["available"]


class NERRecognizer:
    """Combined Ollama + regex NER for PERSON and ORGANIZATION entities."""

    def __init__(self, use_ollama: bool = True, model: str = "gemma3:latest"):
        self._use_ollama = use_ollama
        self._model = model

    def extract(self, text: str) -> List[NERResult]:
        """Extract named entities from text.

        Tries Ollama first (higher accuracy), falls back to regex.
        Results from both methods are merged with deduplication.
        """
        if not text or not text.strip():
            return []

        results: List[NERResult] = []

        # Always run regex (fast, catches agencies and title-prefixed names)
        regex_results = _extract_via_regex(text)
        results.extend(regex_results)

        # Try Ollama for better PERSON detection (catches names without titles)
        if self._use_ollama and _check_ollama_cached():
            ollama_results = _extract_via_ollama(text, self._model)
            results.extend(ollama_results)

        # Deduplicate (prefer higher score on overlap)
        return self._deduplicate(results)

    @staticmethod
    def _deduplicate(results: List[NERResult]) -> List[NERResult]:
        """Remove overlapping detections, keeping highest-scoring."""
        if not results:
            return []
        sorted_results = sorted(results, key=lambda r: (-r.score, -(r.end - r.start)))
        kept: List[NERResult] = []
        used_ranges: List[tuple] = []
        for r in sorted_results:
            overlaps = any(r.start < end and r.end > start for start, end in used_ranges)
            if not overlaps:
                kept.append(r)
                used_ranges.append((r.start, r.end))
        return kept

    def health(self) -> Dict[str, Any]:
        """Health check."""
        return {
            "status": "ok",
            "ollama_available": _check_ollama_cached(),
            "model": self._model,
            "use_ollama": self._use_ollama,
            "federal_agencies": len(_FEDERAL_AGENCIES),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="ICDEV™ NER Recognizer (Ollama + regex)")
    parser.add_argument("--extract", type=str, help="Extract entities from text")
    parser.add_argument("--no-ollama", action="store_true", help="Regex only (skip Ollama)")
    parser.add_argument("--health", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--gate", action="store_true")
    args = parser.parse_args()

    recognizer = NERRecognizer(use_ollama=not args.no_ollama)

    if args.health:
        result = recognizer.health()
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            for k, v in result.items():
                print(f"{k}: {v}")
        if args.gate and result["status"] != "ok":
            sys.exit(1)
        return

    if args.extract:
        results = recognizer.extract(args.extract)
        if args.json:
            print(
                json.dumps(
                    {
                        "entities": [r.to_dict() for r in results],
                        "count": len(results),
                    },
                    indent=2,
                )
            )
        else:
            if not results:
                print("No entities found.")
            else:
                for r in results:
                    print(f"  {r.entity_type}: '{r.text}' [{r.start}:{r.end}] score={r.score:.2f} ({r.method})")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
