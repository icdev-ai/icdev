#!/usr/bin/env python3

# CUI // SP-CTI
"""Redaction Anonymizer — applies anonymization operators to detected PII.

Takes detection results from detector.py and applies configured treatments:
    - surrogate: Replace with realistic fake (via registry.py)
    - redact: Replace with [ENTITY_TYPE] placeholder
    - mask: Partial character masking (e.g., ***-**-6789)
    - hash: SHA-256 hash with optional salt

IL-aware: treatment can be overridden per Impact Level.

CLI:
    python tools/redaction/anonymizer.py --anonymize "John Smith SSN 123-45-6789" --json
    python tools/redaction/anonymizer.py --anonymize "text" --il IL6 --json
    python tools/redaction/anonymizer.py --health --json
"""

import argparse
import hashlib
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

from tools.redaction.detector import RedactionDetector, DetectionResult  # noqa: E402
from tools.redaction.registry import RedactionRegistry  # noqa: E402

logger = get_logger("icdev.redaction.anonymizer")


def _load_config() -> Dict[str, Any]:
    config_path = BASE_DIR / "args" / "redaction_config.yaml"
    try:
        import yaml

        with open(config_path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _load_govcon_config() -> Dict[str, Any]:
    config_path = BASE_DIR / "args" / "redaction_govcon.yaml"
    try:
        import yaml

        with open(config_path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Anonymization result
# ---------------------------------------------------------------------------


class AnonymizationResult:
    """Result of anonymizing a text."""

    def __init__(
        self,
        original_length: int,
        anonymized_text: str,
        detections: List[DetectionResult],
        replacements: List[Dict[str, Any]],
        session_id: str,
    ):
        self.original_length = original_length
        self.anonymized_text = anonymized_text
        self.detections = detections
        self.replacements = replacements
        self.session_id = session_id

    def to_dict(self) -> Dict[str, Any]:
        return {
            "original_length": self.original_length,
            "anonymized_length": len(self.anonymized_text),
            "detection_count": len(self.detections),
            "replacement_count": len(self.replacements),
            "entity_types": list(set(r["entity_type"] for r in self.replacements)),
            "session_id": self.session_id,
        }


# ---------------------------------------------------------------------------
# Anonymizer Engine
# ---------------------------------------------------------------------------


class RedactionAnonymizer:
    """Anonymization engine with IL-aware operator selection."""

    def __init__(
        self,
        config: Optional[Dict] = None,
        govcon_config: Optional[Dict] = None,
        session_id: Optional[str] = None,
        detector: Optional[RedactionDetector] = None,
    ):
        """Args:
            detector: Pre-built detector to use instead of constructing one.
                Callers that need a specific detection profile — credentials on,
                Ollama NER off because the caller must be reproducible — cannot
                express it otherwise, since this class would build its own with
                the defaults and silently ignore them.
        """
        self._config = config or _load_config()
        self._govcon_config = govcon_config or _load_govcon_config()
        self._detector = detector or RedactionDetector(self._config, self._govcon_config)
        self._agency_map = self._govcon_config.get("agency_surrogates", {}) or {}
        self._registry = RedactionRegistry(
            session_id=session_id,
            agency_map=self._agency_map,
            ttl_hours=self._config.get("registry", {}).get("ttl_hours", 72),
        )
        self._audit_enabled = self._config.get("audit", {}).get("enabled", True)

    @property
    def session_id(self) -> str:
        return self._registry.session_id

    def anonymize(
        self, text: str, impact_level: str = "IL4", entities: Optional[List[str]] = None
    ) -> AnonymizationResult:
        """Detect and anonymize PII in text.

        Args:
            text: Input text.
            impact_level: Classification level for operator selection.
            entities: Optional entity filter.

        Returns:
            AnonymizationResult with anonymized text and metadata.
        """
        if not text or not text.strip():
            return AnonymizationResult(0, text, [], [], self.session_id)

        # Detect
        detections = self._detector.detect(text, entities=entities, impact_level=impact_level)

        if not detections:
            return AnonymizationResult(len(text), text, [], [], self.session_id)

        # Apply treatments (process from end to preserve positions)
        replacements = []
        anonymized = text
        for det in sorted(detections, key=lambda d: -d.start):
            treatment = self._get_treatment(det.entity_type, impact_level)
            replacement = self._apply_treatment(treatment, det)
            anonymized = anonymized[: det.start] + replacement + anonymized[det.end :]
            replacements.append(
                {
                    "entity_type": det.entity_type,
                    "treatment": treatment,
                    "original_length": det.end - det.start,
                    "replacement_length": len(replacement),
                }
            )

        # Audit
        if self._audit_enabled:
            self._log_audit(text, detections, impact_level)

        replacements.reverse()  # Restore original order
        return AnonymizationResult(
            original_length=len(text),
            anonymized_text=anonymized,
            detections=detections,
            replacements=replacements,
            session_id=self.session_id,
        )

    def de_anonymize(self, text: str) -> str:
        """Reverse anonymization using session registry."""
        return self._registry.de_anonymize(text)

    def _get_treatment(self, entity_type: str, impact_level: str) -> str:
        """Determine treatment for entity based on config and IL level."""
        # Check IL override first
        il_overrides = self._config.get("il_overrides", {}).get(impact_level, {})
        if entity_type in il_overrides:
            return il_overrides[entity_type]

        # Check standard entity config
        std_entities = self._config.get("detection", {}).get("standard_entities", {})
        if entity_type in std_entities:
            return std_entities[entity_type].get("treatment", "redact")

        # GovCon entities: check govcon config patterns
        gc = self._govcon_config
        for pdef in (gc.get("contract_patterns", []) or []) + (gc.get("pricing_patterns", []) or []):
            if pdef.get("name", "").upper() == entity_type.replace("GOVCON_", ""):
                return pdef.get("treatment", "redact")

        # Deny-list entities default to surrogate
        if entity_type in (
            "GOVCON_PROGRAM_NAME",
            "GOVCON_PROTECTED_ORG",
            "GOVCON_AGENCY_NAME",
            "PROGRAM_NAME",
            "PROTECTED_ORG",
            "AGENCY_NAME",
        ):
            return "surrogate"

        # Default
        return self._config.get("anonymization", {}).get("default_operator", "redact")

    def _apply_treatment(self, treatment: str, detection: DetectionResult) -> str:
        """Apply a specific treatment to a detection."""
        real_value = detection.text

        if treatment == "surrogate":
            return self._registry.get_or_create_surrogate(detection.entity_type, real_value)

        if treatment == "redact":
            # Check for custom placeholder
            placeholder = self._config.get("anonymization", {}).get("redact", {}).get("placeholder", "[{entity_type}]")
            return placeholder.format(entity_type=detection.entity_type)

        if treatment == "mask":
            mask_cfg = self._config.get("anonymization", {}).get("mask", {})
            char = mask_cfg.get("masking_char", "*")
            # Show last N chars
            std_cfg = self._config.get("detection", {}).get("standard_entities", {}).get(detection.entity_type, {})
            show_chars = std_cfg.get("mask_chars", 4)
            if len(real_value) <= show_chars:
                return char * len(real_value)
            masked_len = len(real_value) - show_chars
            return char * masked_len + real_value[-show_chars:]

        if treatment == "hash":
            hash_cfg = self._config.get("anonymization", {}).get("hash", {})
            algo = hash_cfg.get("algorithm", "sha256")
            salt = hash_cfg.get("salt", "")
            h = hashlib.new(algo, (salt + real_value).encode("utf-8"))
            return h.hexdigest()[:16]

        if treatment == "keep":
            return real_value

        # Unknown treatment — redact
        return f"[{detection.entity_type}]"

    def _log_audit(self, text: str, detections: List[DetectionResult], impact_level: str) -> None:
        """Log anonymization event to audit table."""
        try:
            from tools.db.storage import get_connection

            conn = get_connection()
            conn.execute("""
                CREATE TABLE IF NOT EXISTS redaction_audit (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    text_length INTEGER NOT NULL,
                    detection_count INTEGER NOT NULL,
                    entity_types TEXT NOT NULL,
                    impact_level TEXT NOT NULL,
                    module TEXT DEFAULT 'unknown'
                )
            """)
            conn.execute(
                "INSERT INTO redaction_audit "
                "(id, session_id, timestamp, text_length, detection_count, "
                "entity_types, impact_level) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (
                    str(uuid.uuid4()),
                    self.session_id,
                    datetime.now(timezone.utc).isoformat(),
                    len(text),
                    len(detections),
                    json.dumps(list(set(d.entity_type for d in detections))),
                    impact_level,
                ),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning("Audit logging failed: %s", e)

    def health(self) -> Dict[str, Any]:
        """Health check."""
        detector_health = self._detector.health()
        registry_health = self._registry.health()
        return {
            "status": "ok",
            "detector": detector_health,
            "registry": registry_health,
            "config_loaded": bool(self._config),
            "govcon_config_loaded": bool(self._govcon_config),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="ICDEV™ Redaction Anonymizer")
    parser.add_argument("--anonymize", type=str, help="Anonymize text")
    parser.add_argument("--anonymize-file", type=str, help="Anonymize file contents")
    parser.add_argument(
        "--il", type=str, default="IL4", choices=["IL2", "IL4", "IL5", "IL6"], help="Impact level (default: IL4)"
    )
    parser.add_argument("--session", type=str, default=None, help="Session ID")
    parser.add_argument("--show-text", action="store_true", help="Show anonymized text (default: metadata only)")
    parser.add_argument("--health", action="store_true", help="Health check")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--gate", action="store_true", help="Exit non-zero if unhealthy")
    args = parser.parse_args()

    anonymizer = RedactionAnonymizer(session_id=args.session)

    if args.health:
        result = anonymizer.health()
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Status: {result['status']}")
            print(f"Presidio: {result['detector']['presidio_available']}")
        if args.gate and result["status"] != "ok":
            sys.exit(1)
        return

    text = args.anonymize
    if args.anonymize_file:
        text = Path(args.anonymize_file).read_text(encoding="utf-8")

    if text:
        result = anonymizer.anonymize(text, impact_level=args.il)
        output = result.to_dict()
        if args.show_text:
            output["anonymized_text"] = result.anonymized_text
        if args.json:
            print(json.dumps(output, indent=2))
        else:
            print(f"Detections: {output['detection_count']}")
            print(f"Replacements: {output['replacement_count']}")
            print(f"Entity types: {output['entity_types']}")
            if args.show_text:
                print(f"\nAnonymized:\n{result.anonymized_text}")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
