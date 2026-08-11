#!/usr/bin/env python3
# CUI // SP-CTI
"""Redaction Detector — production-grade PII detection for ICDEV™.

Three detection layers (no external NLP dependencies required):
    1. Regex patterns — SSN, email, phone, credit card, contract#, pricing
    2. Ollama NER — PERSON, ORGANIZATION via local qwen3.5 (air-gap safe)
    3. Deny-list matching — program names, partners, agencies from config

Presidio is supported as optional enhancement but NOT required.
Falls back gracefully: Ollama unavailable → regex+deny-list only.

An OPT-IN fourth layer detects credentials (AWS keys, bearer tokens, private
keys, connection strings). It is off by default because this detector already
runs on every LLM egress and a new pattern family there changes what every
caller sends; turn it on per-caller with ``detect_secrets=True`` or globally
with ``detection.secret_patterns.enabled`` in args/redaction_config.yaml.
The patterns are NOT redefined here — they are read from
``tools.security.secret_detector.BUILTIN_PATTERNS``, which is the repo's
existing vetted list, so the two cannot drift apart.

CLI:
    python tools/redaction/detector.py --detect "John Smith SSN 123-45-6789" --json
    python tools/redaction/detector.py --detect-file /path/to/file.txt --json
    python tools/redaction/detector.py --list-entities --json
    python tools/redaction/detector.py --health --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("icdev.redaction.detector")

# ---------------------------------------------------------------------------
# Configuration loader
# ---------------------------------------------------------------------------

# Module-level config cache (avoids YAML file I/O on every instance)
_CONFIG_CACHE: Dict[str, Any] = {}
_GOVCON_CONFIG_CACHE: Dict[str, Any] = {}
_CONFIG_LOADED = False


def _load_config() -> Dict[str, Any]:
    """Load redaction config from args/redaction_config.yaml (cached)."""
    global _CONFIG_CACHE, _CONFIG_LOADED
    if _CONFIG_LOADED and _CONFIG_CACHE:
        return _CONFIG_CACHE
    config_path = BASE_DIR / "args" / "redaction_config.yaml"
    try:
        import yaml

        with open(config_path, encoding="utf-8") as f:
            _CONFIG_CACHE = yaml.safe_load(f) or {}
    except Exception:
        _CONFIG_CACHE = {}
    _CONFIG_LOADED = True
    return _CONFIG_CACHE


def _load_govcon_config() -> Dict[str, Any]:
    """Load GovCon-specific redaction config (cached)."""
    global _GOVCON_CONFIG_CACHE, _CONFIG_LOADED
    if _CONFIG_LOADED and _GOVCON_CONFIG_CACHE:
        return _GOVCON_CONFIG_CACHE
    config_path = BASE_DIR / "args" / "redaction_govcon.yaml"
    try:
        import yaml

        with open(config_path, encoding="utf-8") as f:
            _GOVCON_CONFIG_CACHE = yaml.safe_load(f) or {}
    except Exception:
        _GOVCON_CONFIG_CACHE = {}
    return _GOVCON_CONFIG_CACHE


# ---------------------------------------------------------------------------
# Detection result
# ---------------------------------------------------------------------------


def load_config() -> Dict[str, Any]:
    """Public accessor for the cached redaction config.

    Callers that need to run the stack with one setting changed (audit off for
    a read path, a different impact level) need the full config to copy from,
    and reaching for the private loader to get it is how a supported seam turns
    into an accident. The returned dict is the shared cache — deep-copy it
    before mutating.
    """
    return _load_config()


class DetectionResult:
    """A single PII detection."""

    def __init__(self, entity_type: str, start: int, end: int, score: float, text: str = "", recognizer: str = ""):
        self.entity_type = entity_type
        self.start = start
        self.end = end
        self.score = score
        self.text = text
        self.recognizer = recognizer

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entity_type": self.entity_type,
            "start": self.start,
            "end": self.end,
            "score": round(self.score, 3),
            "text_length": len(self.text),
            "recognizer": self.recognizer,
        }

    def __repr__(self) -> str:
        return f"DetectionResult({self.entity_type}, [{self.start}:{self.end}], score={self.score:.2f})"


# ---------------------------------------------------------------------------
# Regex fallback patterns (when Presidio not available)
# ---------------------------------------------------------------------------

_FALLBACK_PATTERNS: List[Dict[str, Any]] = [
    {"entity_type": "US_SSN", "pattern": r"\b\d{3}-\d{2}-\d{4}\b", "score": 0.95},
    {"entity_type": "EMAIL_ADDRESS", "pattern": r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", "score": 0.9},
    {"entity_type": "PHONE_NUMBER", "pattern": r"\b(\+?1[-.]?)?\(?\d{3}\)?[-.]?\d{3}[-.]?\d{4}\b", "score": 0.7},
    {
        "entity_type": "CREDIT_CARD",
        "pattern": r"\b(?:4\d{12}(?:\d{3})?|5[1-5]\d{14}|3[47]\d{13}|6(?:011|5\d{2})\d{12})\b",
        "score": 0.95,
    },
    {"entity_type": "IP_ADDRESS", "pattern": r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "score": 0.6},
    {"entity_type": "US_PASSPORT", "pattern": r"\b[A-Z]\d{8}\b", "score": 0.5},
]

# Credential severities mapped onto detector confidence. All three land above
# the default hard_redact threshold (0.3) — a credential is never a soft match.
_SECRET_SEVERITY_SCORE: Dict[str, float] = {"critical": 0.99, "high": 0.95, "medium": 0.9}


# ---------------------------------------------------------------------------
# Detector Engine
# ---------------------------------------------------------------------------


class RedactionDetector:
    """PII detection engine: regex + Ollama NER + deny-lists (+ optional Presidio)."""

    def __init__(
        self,
        config: Optional[Dict] = None,
        govcon_config: Optional[Dict] = None,
        use_ollama_ner: bool = True,
        detect_secrets: Optional[bool] = None,
    ):
        self._config = config or _load_config()
        self._govcon_config = govcon_config or _load_govcon_config()
        self._presidio_available = False
        self._analyzer = None
        self._ner_recognizer = None
        self._custom_patterns = []
        self._deny_lists: Dict[str, List[str]] = {}
        self._use_ollama_ner = use_ollama_ner
        if detect_secrets is None:
            detect_secrets = bool(
                self._config.get("detection", {}).get("secret_patterns", {}).get("enabled", False)
            )
        self._detect_secrets = detect_secrets
        self._init_engine()

    def _init_engine(self) -> None:
        """Initialize detection layers."""
        # Layer 1: Try Presidio (optional — works if installed + Python < 3.14)
        try:
            from presidio_analyzer import AnalyzerEngine
            from tools.redaction.govcon_recognizers import build_govcon_recognizers

            self._analyzer = AnalyzerEngine()
            govcon_recognizers = build_govcon_recognizers(self._govcon_config)
            for rec in govcon_recognizers:
                self._analyzer.registry.add_recognizer(rec)
            self._presidio_available = True
            logger.info("Presidio analyzer initialized with %d custom recognizers", len(govcon_recognizers))
        except (ImportError, Exception) as e:
            logger.info("Presidio not available (%s) — using built-in detection", type(e).__name__)
            self._presidio_available = False

        # Layer 2: Ollama NER for PERSON/ORGANIZATION (air-gap safe)
        try:
            from tools.redaction.ner_recognizer import NERRecognizer

            self._ner_recognizer = NERRecognizer(use_ollama=self._use_ollama_ner)
            logger.info("Ollama NER recognizer initialized (use_ollama=%s)", self._use_ollama_ner)
        except ImportError:
            logger.warning("NER recognizer not available")

        # Layer 3: Always load deny-lists and regex patterns
        self._load_deny_lists()
        self._load_govcon_patterns()

        # Layer 4: credentials — opt-in, see the module docstring
        if self._detect_secrets:
            self._load_secret_patterns()

    def _load_secret_patterns(self) -> None:
        """Compile the repo's existing credential patterns into this detector.

        Sourced from ``tools.security.secret_detector.BUILTIN_PATTERNS`` rather
        than restated in this module or in YAML: a second copy of a credential
        regex is a copy that goes stale, and the security scanner's list is the
        one already reviewed. A pattern that fails to compile is skipped, the
        same as every other pattern family here.
        """
        try:
            from tools.security.secret_detector import BUILTIN_PATTERNS
        except ImportError as exc:  # pragma: no cover - defensive
            logger.warning("Secret patterns requested but unavailable: %s", exc)
            return

        for pdef in BUILTIN_PATTERNS:
            try:
                compiled = re.compile(pdef["pattern"])
            except (re.error, KeyError):
                continue
            entity_type = "SECRET_" + re.sub(r"[^A-Z0-9]+", "_", pdef.get("name", "").upper()).strip("_")
            self._custom_patterns.append(
                {
                    "entity_type": entity_type or "SECRET",
                    "pattern": pdef["pattern"],
                    "compiled": compiled,
                    "score": _SECRET_SEVERITY_SCORE.get(pdef.get("severity", "high"), 0.9),
                    "context_words": [],
                    "treatment": "redact",
                }
            )

    def _load_deny_lists(self) -> None:
        """Load program names, protected organizations, and custom terms from config."""
        gc = self._govcon_config
        programs = gc.get("program_names", []) or []
        orgs = gc.get("protected_organizations", []) or []
        agencies = gc.get("agency_surrogates", {}) or {}
        custom_terms = gc.get("custom_terms", []) or []

        if programs:
            self._deny_lists["PROGRAM_NAME"] = [p.lower() for p in programs]
        if orgs:
            self._deny_lists["PROTECTED_ORG"] = [o.lower() for o in orgs]
        if agencies:
            self._deny_lists["AGENCY_NAME"] = [a.lower() for a in agencies.keys()]

        # Custom user-defined terms
        custom_list = []
        for entry in custom_terms:
            if isinstance(entry, str):
                custom_list.append(entry.lower())
            elif isinstance(entry, dict) and "term" in entry:
                custom_list.append(entry["term"].lower())
        if custom_list:
            self._deny_lists["CUSTOM_TERM"] = custom_list

    def _load_govcon_patterns(self) -> None:
        """Load and pre-compile contract/pricing patterns from govcon config."""
        gc = self._govcon_config

        for pdef in gc.get("contract_patterns", []) or []:
            try:
                compiled = re.compile(pdef["pattern"])
            except re.error:
                continue
            self._custom_patterns.append(
                {
                    "entity_type": pdef.get("name", "CONTRACT_ID").upper(),
                    "pattern": pdef["pattern"],
                    "compiled": compiled,
                    "score": pdef.get("confidence", 0.8),
                    "context_words": pdef.get("context_words", []),
                    "treatment": pdef.get("treatment", "redact"),
                }
            )

        for pdef in gc.get("pricing_patterns", []) or []:
            try:
                compiled = re.compile(pdef["pattern"])
            except re.error:
                continue
            self._custom_patterns.append(
                {
                    "entity_type": pdef.get("name", "PRICING").upper(),
                    "pattern": pdef["pattern"],
                    "compiled": compiled,
                    "score": pdef.get("confidence", 0.8),
                    "context_words": pdef.get("context_words", []),
                    "treatment": pdef.get("treatment", "redact"),
                    "placeholder": pdef.get("placeholder", ""),
                }
            )

    def detect(
        self, text: str, entities: Optional[List[str]] = None, impact_level: str = "IL4"
    ) -> List[DetectionResult]:
        """Detect PII/sensitive data in text.

        Args:
            text: Input text to scan.
            entities: Optional list of entity types to detect (None = all).
            impact_level: Classification level (IL2/IL4/IL5/IL6).

        Returns:
            List of DetectionResult objects, sorted by position.
        """
        if not text or not text.strip():
            return []

        results: List[DetectionResult] = []

        # 1. Presidio detection (if available — optional enhancement)
        if self._presidio_available and self._analyzer:
            results.extend(self._detect_presidio(text, entities))

        # 2. Regex patterns (standard PII + GovCon custom)
        results.extend(self._detect_regex(text))

        # 3. Deny-list matching (program names, orgs, agencies)
        results.extend(self._detect_deny_lists(text))

        # 4. Ollama NER for PERSON/ORGANIZATION (air-gap safe, no spaCy)
        if self._ner_recognizer and not self._presidio_available:
            results.extend(self._detect_ner(text))

        # 5. De-duplicate overlapping detections (higher score wins)
        results = self._deduplicate(results)

        # 5. Filter by configured entities
        detection_cfg = self._config.get("detection", {})
        std_entities = detection_cfg.get("standard_entities", {})
        filtered = []
        for r in results:
            entity_cfg = std_entities.get(r.entity_type, {})
            if entity_cfg.get("enabled", True) is False:
                continue
            threshold = entity_cfg.get("threshold", detection_cfg.get("thresholds", {}).get("hard_redact", 0.3))
            if r.score >= threshold:
                filtered.append(r)

        # Sort by position
        filtered.sort(key=lambda r: r.start)
        return filtered

    def _detect_presidio(self, text: str, entities: Optional[List[str]]) -> List[DetectionResult]:
        """Run Presidio analyzer."""
        results = []
        try:
            language = self._config.get("detection", {}).get("language", "en")
            presidio_results = self._analyzer.analyze(
                text=text,
                language=language,
                entities=entities,
            )
            for pr in presidio_results:
                results.append(
                    DetectionResult(
                        entity_type=pr.entity_type,
                        start=pr.start,
                        end=pr.end,
                        score=pr.score,
                        text=text[pr.start : pr.end],
                        recognizer="presidio",
                    )
                )
        except Exception as e:
            logger.error("Presidio detection failed: %s", e)
        return results

    def _detect_ner(self, text: str) -> List[DetectionResult]:
        """Run Ollama NER for PERSON/ORGANIZATION detection."""
        results = []
        try:
            ner_results = self._ner_recognizer.extract(text)
            for nr in ner_results:
                results.append(
                    DetectionResult(
                        entity_type=nr.entity_type,
                        start=nr.start,
                        end=nr.end,
                        score=nr.score,
                        text=nr.text,
                        recognizer=f"ner_{nr.method}",
                    )
                )
        except Exception as e:
            logger.debug("NER detection failed: %s", e)
        return results

    def _detect_regex(self, text: str) -> List[DetectionResult]:
        """Run regex-based detection (fallback + GovCon patterns)."""
        results = []

        # Standard fallback patterns (only if Presidio not available)
        patterns = self._custom_patterns[:]
        if not self._presidio_available:
            patterns.extend(_FALLBACK_PATTERNS)

        for pdef in patterns:
            try:
                # Use pre-compiled regex if available, otherwise compile
                regex = pdef.get("compiled") or re.compile(pdef["pattern"])
                context_words = pdef.get("context_words", [])

                for match in regex.finditer(text):
                    score = pdef["score"]

                    # Context boosting: check for nearby context words
                    if context_words:
                        window_start = max(0, match.start() - 100)
                        window_end = min(len(text), match.end() + 100)
                        window = text[window_start:window_end].lower()
                        has_context = any(cw.lower() in window for cw in context_words)
                        if not has_context:
                            score *= 0.3  # Reduce confidence without context
                        else:
                            score = min(1.0, score * 1.2)  # Boost with context

                    # Filter UUID-like segments (prevent false positives)
                    matched_text = match.group()
                    if self._looks_like_uuid(matched_text):
                        continue

                    results.append(
                        DetectionResult(
                            entity_type=pdef["entity_type"],
                            start=match.start(),
                            end=match.end(),
                            score=score,
                            text=matched_text,
                            recognizer="regex",
                        )
                    )
            except re.error:
                continue

        return results

    def _detect_deny_lists(self, text: str) -> List[DetectionResult]:
        """Match deny-list entries (program names, orgs, agencies)."""
        results = []

        for entity_type, terms in self._deny_lists.items():
            for term in terms:
                # Word boundary matching
                pattern = re.compile(r"\b" + re.escape(term) + r"\b", re.IGNORECASE)
                for match in pattern.finditer(text):
                    results.append(
                        DetectionResult(
                            entity_type=entity_type,
                            start=match.start(),
                            end=match.end(),
                            score=0.99,  # Deny-list = very high confidence
                            text=match.group(),
                            recognizer="deny_list",
                        )
                    )

        return results

    @staticmethod
    def _looks_like_uuid(text: str) -> bool:
        """Check if text looks like a UUID segment."""
        return bool(re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", text, re.IGNORECASE))

    @staticmethod
    def _deduplicate(results: List[DetectionResult]) -> List[DetectionResult]:
        """Remove overlapping detections, keeping highest-scoring."""
        if not results:
            return []

        # Sort by score descending, then by span length descending
        sorted_results = sorted(results, key=lambda r: (-r.score, -(r.end - r.start)))
        kept: List[DetectionResult] = []
        used_ranges: List[tuple] = []

        for r in sorted_results:
            overlaps = False
            for start, end in used_ranges:
                if r.start < end and r.end > start:
                    overlaps = True
                    break
            if not overlaps:
                kept.append(r)
                used_ranges.append((r.start, r.end))

        return kept

    def get_supported_entities(self) -> List[Dict[str, Any]]:
        """List all supported entity types."""
        entities = []

        # Standard entities from config
        detection_cfg = self._config.get("detection", {})
        for entity_type, cfg in detection_cfg.get("standard_entities", {}).items():
            entities.append(
                {
                    "entity_type": entity_type,
                    "enabled": cfg.get("enabled", True),
                    "treatment": cfg.get("treatment", "redact"),
                    "source": "presidio" if self._presidio_available else "regex_fallback",
                }
            )

        # Custom GovCon entities
        for pdef in self._custom_patterns:
            entities.append(
                {
                    "entity_type": pdef["entity_type"],
                    "enabled": True,
                    "treatment": pdef.get("treatment", "redact"),
                    "source": "govcon_regex",
                }
            )

        # Deny-list entities
        for entity_type, terms in self._deny_lists.items():
            entities.append(
                {
                    "entity_type": entity_type,
                    "enabled": True,
                    "treatment": "surrogate",
                    "source": "deny_list",
                    "term_count": len(terms),
                }
            )

        return entities

    def health(self) -> Dict[str, Any]:
        """Return health check info."""
        ner_health = self._ner_recognizer.health() if self._ner_recognizer else {"status": "unavailable"}
        return {
            "status": "ok",
            "presidio_available": self._presidio_available,
            "ner_recognizer": ner_health,
            "custom_patterns": len(self._custom_patterns),
            "deny_list_entities": {k: len(v) for k, v in self._deny_lists.items()},
            "supported_entities": len(self.get_supported_entities()),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="ICDEV™ Redaction Detector")
    parser.add_argument("--detect", type=str, help="Detect PII in text")
    parser.add_argument("--detect-file", type=str, help="Detect PII in file")
    parser.add_argument("--list-entities", action="store_true", help="List supported entities")
    parser.add_argument("--health", action="store_true", help="Health check")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--gate", action="store_true", help="Exit non-zero if unhealthy")
    args = parser.parse_args()

    detector = RedactionDetector()

    if args.health:
        result = detector.health()
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            for k, v in result.items():
                print(f"{k}: {v}")
        if args.gate and result["status"] != "ok":
            sys.exit(1)
        return

    if args.list_entities:
        entities = detector.get_supported_entities()
        if args.json:
            print(json.dumps(entities, indent=2))
        else:
            for e in entities:
                status = "ON" if e.get("enabled") else "OFF"
                print(f"  [{status}] {e['entity_type']} ({e['source']}) → {e['treatment']}")
        return

    text = args.detect
    if args.detect_file:
        text = Path(args.detect_file).read_text(encoding="utf-8")

    if text:
        results = detector.detect(text)
        if args.json:
            output = {
                "text_length": len(text),
                "detections": [r.to_dict() for r in results],
                "detection_count": len(results),
                "entity_types_found": list(set(r.entity_type for r in results)),
            }
            print(json.dumps(output, indent=2))
        else:
            if not results:
                print("No PII detected.")
            else:
                print(f"Found {len(results)} detection(s):")
                for r in results:
                    print(f"  {r.entity_type} [{r.start}:{r.end}] score={r.score:.2f} ({r.recognizer})")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
