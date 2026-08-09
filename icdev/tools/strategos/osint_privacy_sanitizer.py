#!/usr/bin/env python3
"""OSINT Privacy Sanitizer — commercial-use PII detection and redaction for OSINT signals.

Scans OSINT signal title/body for PII/sensitive data and applies commercial-grade
redaction before storage. Operates under commercial privacy guidelines with no
specific federal compliance boundaries.

Internal controls enforced:
- PII detection via RedactionDetector (regex + NER, no cloud dependency)
- Automatic redaction/masking of detected entities
- Audit logging to osint_privacy_audit
- Metadata tracking (pii_detected, entity_types) on the signal

Usage:
    python tools/strategos/osint_privacy_sanitizer.py --signal '{"title":"...","body":"..."}' --json
    python tools/strategos/osint_privacy_sanitizer.py --health --json
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

from tools.redaction.detector import RedactionDetector  # noqa: E402
from tools.redaction.anonymizer import RedactionAnonymizer  # noqa: E402

logger = get_logger("icdev.strategos.osint_privacy")
logging.basicConfig(level=logging.WARNING, format="%(levelname)s  %(message)s")

# Commercial privacy: default to "redact" for PII, "mask" for contact info.
# No IL-based overrides — this is commercial use, not federal.
_COMMERCIAL_PRIVACY_DEFAULTS = {
    "US_SSN": "mask",
    "EMAIL_ADDRESS": "redact",
    "PHONE_NUMBER": "mask",
    "CREDIT_CARD": "redact",
    "IP_ADDRESS": "redact",
    "US_PASSPORT": "redact",
    "PERSON": "redact",
    "ORGANIZATION": "keep",  # Organizations are generally public in OSINT
    "LOCATION": "keep",      # Geo data is the point of OSINT
}


class OSINTPrivacySanitizer:
    """Commercial privacy sanitizer for OSINT signals."""

    def __init__(
        self,
        detector: Optional[RedactionDetector] = None,
        anonymizer: Optional[RedactionAnonymizer] = None,
        entity_treatments: Optional[Dict[str, str]] = None,
    ):
        self._detector = detector or RedactionDetector()
        self._anonymizer = anonymizer or RedactionAnonymizer()
        self._entity_treatments = entity_treatments or _COMMERCIAL_PRIVACY_DEFAULTS.copy()

    def sanitize_signal(self, signal: Dict[str, Any]) -> Dict[str, Any]:
        """Scan and sanitize a single OSINT signal.

        Returns a dict with:
            - sanitized_signal: the signal with title/body redacted
            - pii_detected: bool
            - entity_types: list of entity types found
            - detection_count: int
            - audit_event: dict ready for osint_privacy_audit
        """
        title = signal.get("title") or ""
        body = signal.get("body") or ""
        full_text = f"{title}\n{body}"

        detections = self._detector.detect(full_text)

        if not detections:
            return {
                "sanitized_signal": signal,
                "pii_detected": False,
                "entity_types": [],
                "detection_count": 0,
                "audit_event": self._build_audit_event(signal, [], False),
            }

        # Build per-entity treatment overrides for the anonymizer
        # We override the config via a temporary config patch on the anonymizer
        sanitized_title = self._anonymize_text(title, detections)
        sanitized_body = self._anonymize_text(body, detections)

        entity_types = list(set(d.entity_type for d in detections))

        sanitized_signal = dict(signal)
        sanitized_signal["title"] = sanitized_title
        sanitized_signal["body"] = sanitized_body

        audit_event = self._build_audit_event(signal, detections, True)

        return {
            "sanitized_signal": sanitized_signal,
            "pii_detected": True,
            "entity_types": entity_types,
            "detection_count": len(detections),
            "audit_event": audit_event,
        }

    def _anonymize_text(self, text: str, detections: List[Any]) -> str:
        """Apply commercial privacy treatments to text."""
        if not text or not detections:
            return text

        # Sort detections by position (end to start) so replacements don't shift indices
        sorted_dets = sorted(detections, key=lambda d: -d.start)
        result = text

        for det in sorted_dets:
            treatment = self._entity_treatments.get(det.entity_type, "redact")
            replacement = self._apply_treatment(treatment, det)
            result = result[: det.start] + replacement + result[det.end :]

        return result

    def _apply_treatment(self, treatment: str, detection: Any) -> str:
        """Apply a single treatment to a detection result."""
        original = detection.text

        if treatment == "redact":
            return f"[{detection.entity_type}]"

        if treatment == "mask":
            if len(original) <= 4:
                return "*" * len(original)
            return "*" * (len(original) - 4) + original[-4:]

        if treatment == "hash":
            import hashlib
            return hashlib.sha256(original.encode("utf-8")).hexdigest()[:16]

        if treatment == "keep":
            return original

        return f"[{detection.entity_type}]"

    def _build_audit_event(
        self, signal: Dict[str, Any], detections: List[Any], pii_found: bool
    ) -> Dict[str, Any]:
        """Build an audit event dict for osint_privacy_audit."""
        return {
            "id": str(uuid.uuid4()),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source": signal.get("source", "unknown")[:255],
            "url_hash": self._url_hash(signal),
            "pii_found": pii_found,
            "entity_types": json.dumps(list(set(d.entity_type for d in detections))) if detections else "[]",
            "detection_count": len(detections),
        }

    @staticmethod
    def _url_hash(signal: Dict[str, Any]) -> str:
        """Stable hash matching osint_harvester logic."""
        import hashlib
        url = (signal.get("url") or "").strip()
        title = (signal.get("title") or "").strip()
        date = (signal.get("date") or "").strip()
        text = url if url else (title + date)
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def log_audit(self, audit_event: Dict[str, Any]) -> None:
        """Write audit event to osint_privacy_audit table."""
        try:
            from tools.db.storage import get_connection  # noqa: PLC0415

            conn = get_connection()
            conn.execute(
                """
                INSERT INTO osint_privacy_audit
                (id, timestamp, source, url_hash, pii_found, entity_types, detection_count)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    audit_event["id"],
                    audit_event["timestamp"],
                    audit_event["source"],
                    audit_event["url_hash"],
                    audit_event["pii_found"],
                    audit_event["entity_types"],
                    audit_event["detection_count"],
                ),
            )
            conn.commit()
            conn.close()
        except Exception as exc:
            logger.warning("Privacy audit logging failed: %s", exc)

    def health(self) -> Dict[str, Any]:
        """Health check."""
        detector_health = self._detector.health()
        return {
            "status": "ok" if detector_health["status"] == "ok" else "degraded",
            "detector": detector_health,
            "entity_treatments": self._entity_treatments,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


def sanitize_signals(signals: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Batch-sanitize a list of OSINT signals.

    Returns envelope with sanitized_signals, summary counts, and audit events.
    """
    sanitizer = OSINTPrivacySanitizer()
    sanitized_signals: List[Dict[str, Any]] = []
    audit_events: List[Dict[str, Any]] = []
    pii_found_count = 0
    total_detections = 0

    for sig in signals:
        result = sanitizer.sanitize_signal(sig)
        sanitized_signals.append(result["sanitized_signal"])
        if result["pii_detected"]:
            pii_found_count += 1
            total_detections += result["detection_count"]
            audit_events.append(result["audit_event"])

    # Batch log audits
    for event in audit_events:
        try:
            sanitizer.log_audit(event)
        except Exception:
            pass

    return {
        "status": "ok",
        "signal_count": len(signals),
        "pii_found_count": pii_found_count,
        "total_detections": total_detections,
        "sanitized_signals": sanitized_signals,
    }


# ── CLI ────────────────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="OSINT Privacy Sanitizer")
    p.add_argument("--signal", type=str, help="JSON signal object to sanitize")
    p.add_argument("--signals-file", type=str, help="JSON file with signals array")
    p.add_argument("--json", action="store_true", help="Emit JSON output")
    p.add_argument("--health", action="store_true", help="Health check")
    p.add_argument("--gate", action="store_true", help="Exit non-zero if unhealthy")
    return p


def main(argv: List[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    sanitizer = OSINTPrivacySanitizer()

    if args.health:
        result = sanitizer.health()
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Status: {result['status']}")
        if args.gate and result["status"] != "ok":
            return 1
        return 0

    signals: List[Dict[str, Any]] = []
    if args.signal:
        signals = [json.loads(args.signal)]
    elif args.signals_file:
        data = json.loads(Path(args.signals_file).read_text(encoding="utf-8"))
        signals = data if isinstance(data, list) else data.get("signals", [])

    if not signals:
        print("No signals provided.", file=sys.stderr)
        return 1

    result = sanitize_signals(signals)

    if args.json:
        # Don't emit full sanitized signals in JSON by default (privacy)
        output = {
            "status": result["status"],
            "signal_count": result["signal_count"],
            "pii_found_count": result["pii_found_count"],
            "total_detections": result["total_detections"],
        }
        print(json.dumps(output, indent=2))
    else:
        print(
            f"Sanitized {result['signal_count']} signal(s), "
            f"PII found in {result['pii_found_count']}, "
            f"total detections: {result['total_detections']}"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
