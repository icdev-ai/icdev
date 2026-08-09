#!/usr/bin/env python3
# CUI // SP-CTI
"""Adversarial Data Validation Pipeline — bias, deepfake, and manipulation detection.

Screens unstructured OSINT sources (social media posts, satellite imagery) for
adversarial corruption before they enter the predictive analysis engine.

NIST 800-53 mappings:
- SA-11: Developer Security Testing and Evaluation
- SI-10: Information Input Validation
- AU-9:  Audit Log Management (append-only audit table)
"""

from __future__ import annotations
import sys
from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.logging.icdev_logger import get_logger

import argparse
import json
import logging
import re
import sys
import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = get_logger("icdev.strategos.adversarial_validator")
logging.basicConfig(level=logging.WARNING, format="%(levelname)s  %(message)s")


# ---------------------------------------------------------------------------
# Detection heuristics (deterministic — no cloud dependency)
# ---------------------------------------------------------------------------

# Bias indicators: weighted regex patterns for sentiment skew and demographic bias
_BIAS_PATTERNS = {
    "sentiment_skew": [
        (r"\b(all|every|none|always|never)\b.*\b(are|is|were|was)\b.*\b(bad|evil|criminals?|terrorists?|lazy|stupid|inferior|superior)\b", 0.8),
        (r"\b(should be deported|should be banned|should not be allowed|do not belong)\b", 0.9),
        (r"\b(destroy|hate|kill|eliminate)\b.*\b(them|they|those people)\b", 0.85),
    ],
    "demographic_bias": [
        (r"\b(women|men|blacks|whites|asians|hispanics|muslims|jews|christians)\b.*\b(are inherently|are naturally|are genetically|are biologically)\b", 0.9),
        (r"\b(women|men|blacks|whites|asians|hispanics|muslims|jews|christians)\b.*\b(less capable|less intelligent|weaker|inferior|superior)\b.*\b(than)\b", 0.9),
        (r"\b(less capable|less intelligent|weaker|inferior|superior)\b.*\b(than)\b.*\b(women|men|blacks|whites|asians|hispanics|muslims|jews|christians)\b", 0.85),
    ],
    "political_bias": [
        (r"\b(all politicians from|every member of|the entire)\b.*\b(party|group|movement)\b.*\b(are corrupt|are thieves|are liars|are frauds)\b", 0.8),
    ],
    "explicit_bias_marker": [
        (r"\b(biased text|bias confirmed|targeted harassment|hate speech)\b", 0.7),
    ],
}

# Deepfake / imagery manipulation heuristics
_DEEPFAKE_INDICATORS = {
    "metadata_inconsistency": {
        "suspicious_software": [r"Photoshop", r"GIMP", r"Paint", r"Canva", r"Figma"],
        "suspicious_makes": [r"Unknown", r"Generic", r""],
    },
    "compression_anomaly": {
        "min_sensible_size_bytes": 10_000,
        "min_sensible_bitrate_4k_kbps": 5000,
        "min_sensible_bitrate_hd_kbps": 2000,
    },
}

# Adversarial text manipulation
_INVISIBLE_CHARS = {"​", "‌", "‍", "﻿", "⁠"}

# Common homoglyph mappings (Latin vs Cyrillic/Greek lookalikes)
_HOMOGLYPH_RANGES = [
    (0x0430, 0x0430, "a"),  # Cyrillic а (U+0430) looks like Latin a
    (0x043E, 0x043E, "o"),  # Cyrillic о (U+043E) looks like Latin o
    (0x0435, 0x0435, "e"),  # Cyrillic е (U+0435) looks like Latin e
    (0x0440, 0x0440, "p"),  # Cyrillic р (U+0440) looks like Latin p
    (0x0456, 0x0456, "i"),  # Cyrillic і (U+0456) looks like Latin i
    (0x0457, 0x0457, "i"),  # Cyrillic ї (U+0457) looks like Latin i
]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ValidationResult:
    signal_id: str
    source_type: str
    flagged: bool
    status: str  # "clean", "flagged", "blocked"
    issues: List[str] = field(default_factory=list)
    bias_score: float = 0.0
    deepfake_score: float = 0.0
    manipulation_score: float = 0.0
    details: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_audit_event(self) -> Dict[str, Any]:
        return {
            "id": str(uuid.uuid4()),
            "timestamp": self.timestamp,
            "signal_id": self.signal_id,
            "source_type": self.source_type,
            "status": self.status,
            "issues": self.issues,
            "bias_score": self.bias_score,
            "deepfake_score": self.deepfake_score,
            "manipulation_score": self.manipulation_score,
            "details": self.details,
        }


# ---------------------------------------------------------------------------
# Detectors
# ---------------------------------------------------------------------------

class BiasDetector:
    """Deterministic bias detection via weighted regex heuristics."""

    def detect(self, text: str) -> Dict[str, Any]:
        if not text:
            return {"bias_detected": False, "categories": [], "bias_score": 0.0}

        categories: List[str] = []
        max_score = 0.0
        lower_text = text.lower()

        for category, patterns in _BIAS_PATTERNS.items():
            for pattern, weight in patterns:
                if re.search(pattern, lower_text, re.IGNORECASE):
                    if category not in categories:
                        categories.append(category)
                    max_score = max(max_score, weight)

        # Boost score if multiple categories hit
        if len(categories) >= 2:
            max_score = min(1.0, max_score + 0.1)

        return {
            "bias_detected": bool(categories),
            "categories": categories,
            "bias_score": round(max_score, 3),
        }


class DeepfakeDetector:
    """Deterministic deepfake / imagery-synthesis detection via metadata heuristics."""

    def detect(self, image_meta: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not image_meta:
            return {"deepfake_detected": False, "indicators": [], "deepfake_score": 0.0}

        indicators: List[str] = []
        score = 0.0

        software = (image_meta.get("exif_software") or "").strip()
        make = (image_meta.get("exif_make") or "").strip()
        file_size = image_meta.get("file_size_bytes") or 0
        filename = (image_meta.get("filename") or "").lower()
        resolution = image_meta.get("resolution") or ""
        bitrate = image_meta.get("bitrate_kbps") or 0

        # Check suspicious software
        for susp in _DEEPFAKE_INDICATORS["metadata_inconsistency"]["suspicious_software"]:
            if re.search(susp, software, re.IGNORECASE):
                indicators.append("metadata_inconsistency")
                score = max(score, 0.85)
                break

        # Check suspicious make
        if make in _DEEPFAKE_INDICATORS["metadata_inconsistency"]["suspicious_makes"]:
            indicators.append("metadata_inconsistency")
            score = max(score, 0.7)

        # Check file size anomalies
        min_size = _DEEPFAKE_INDICATORS["compression_anomaly"]["min_sensible_size_bytes"]
        if 0 < file_size < min_size:
            indicators.append("compression_anomaly")
            score = max(score, 0.75)

        # Video bitrate anomaly
        if "4k" in resolution.lower() or "2160" in resolution.lower():
            if 0 < bitrate < _DEEPFAKE_INDICATORS["compression_anomaly"]["min_sensible_bitrate_4k_kbps"]:
                indicators.append("compression_anomaly")
                score = max(score, 0.8)
        elif "hd" in resolution.lower() or "1080" in resolution.lower():
            if 0 < bitrate < _DEEPFAKE_INDICATORS["compression_anomaly"]["min_sensible_bitrate_hd_kbps"]:
                indicators.append("compression_anomaly")
                score = max(score, 0.75)

        # Filename heuristics: known benign patterns reduce suspicion,
        # but only if file size is at least remotely plausible.
        benign_patterns = [r"sentinel", r"landsat", r"copernicus", r"maxar", r"worldview", r"\.tif", r"\.tiff"]
        is_benign_name = any(re.search(p, filename, re.IGNORECASE) for p in benign_patterns)
        if is_benign_name and file_size >= min_size:
            if score > 0:
                score = max(0.0, score - 0.3)
                if score < 0.5:
                    indicators = [i for i in indicators if i != "metadata_inconsistency"]

        return {
            "deepfake_detected": bool(indicators) and score >= 0.5,
            "indicators": list(set(indicators)),
            "deepfake_score": round(score, 3),
        }


class ManipulationDetector:
    """Deterministic adversarial text manipulation detection."""

    def detect(self, text: str) -> Dict[str, Any]:
        if not text:
            return {"manipulation_detected": False, "indicators": [], "manipulation_score": 0.0}

        indicators: List[str] = []
        score = 0.0

        # Invisible characters
        found_invisible = [ch for ch in text if ch in _INVISIBLE_CHARS]
        if found_invisible:
            indicators.append("invisible_characters")
            score = max(score, min(0.9, 0.5 + 0.1 * len(found_invisible)))

        # Homoglyphs
        found_homoglyphs = False
        for codepoint, _, latin_equiv in _HOMOGLYPH_RANGES:
            if chr(codepoint) in text:
                found_homoglyphs = True
                break
        if found_homoglyphs:
            indicators.append("homoglyphs")
            score = max(score, 0.85)

        # Confusable Unicode normalization difference
        normalized = unicodedata.normalize("NFKC", text)
        if normalized != text:
            # Only flag if normalization actually changes visible characters (not just combining forms)
            if self._has_confusable_change(text, normalized):
                indicators.append("unicode_confusables")
                score = max(score, 0.7)

        return {
            "manipulation_detected": bool(indicators) and score >= 0.5,
            "indicators": list(set(indicators)),
            "manipulation_score": round(score, 3),
        }

    @staticmethod
    def _has_confusable_change(original: str, normalized: str) -> bool:
        """Return True if normalization changed confusable characters (not just composition)."""
        for o, n in zip(original, normalized):
            if o != n and unicodedata.category(o).startswith("L") and unicodedata.category(n).startswith("L"):
                return True
        return False


# ---------------------------------------------------------------------------
# Validator orchestrator
# ---------------------------------------------------------------------------

class AdversarialValidator:
    """Pipeline orchestrator: ingest → classify → detect → flag → audit."""

    def __init__(
        self,
        bias_detector: Optional[BiasDetector] = None,
        deepfake_detector: Optional[DeepfakeDetector] = None,
        manipulation_detector: Optional[ManipulationDetector] = None,
    ):
        self._bias = bias_detector or BiasDetector()
        self._deepfake = deepfake_detector or DeepfakeDetector()
        self._manip = manipulation_detector or ManipulationDetector()

    def validate(self, signal: Dict[str, Any]) -> ValidationResult:
        signal_id = signal.get("id", str(uuid.uuid4()))
        source_type = signal.get("source_type", "unknown")
        issues: List[str] = []
        details: Dict[str, Any] = {}
        bias_score = 0.0
        deepfake_score = 0.0
        manipulation_score = 0.0

        # Text-based checks
        content = signal.get("content") or ""
        if content:
            bias_result = self._bias.detect(content)
            if bias_result["bias_detected"]:
                issues.append("bias")
                bias_score = bias_result["bias_score"]
                details["bias"] = bias_result

            manip_result = self._manip.detect(content)
            if manip_result["manipulation_detected"]:
                issues.append("manipulation")
                manipulation_score = manip_result["manipulation_score"]
                details["manipulation"] = manip_result

        # Imagery / metadata checks
        metadata = signal.get("metadata")
        if metadata or source_type in ("satellite_imagery", "drone_imagery", "video"):
            df_result = self._deepfake.detect(metadata or {})
            if df_result["deepfake_detected"]:
                issues.append("deepfake")
                deepfake_score = df_result["deepfake_score"]
                details["deepfake"] = df_result

        # Determine status: blocked if any score >= 0.8, else flagged if any issue found
        max_score = max(bias_score, deepfake_score, manipulation_score)
        if issues:
            status = "blocked" if max_score >= 0.8 else "flagged"
        else:
            status = "clean"

        return ValidationResult(
            signal_id=signal_id,
            source_type=source_type,
            flagged=bool(issues),
            status=status,
            issues=issues,
            bias_score=bias_score,
            deepfake_score=deepfake_score,
            manipulation_score=manipulation_score,
            details=details,
        )

    def validate_batch(self, signals: List[Dict[str, Any]]) -> Dict[str, Any]:
        results: List[ValidationResult] = []
        flagged = 0
        clean = 0
        for sig in signals:
            r = self.validate(sig)
            results.append(r)
            if r.flagged:
                flagged += 1
            else:
                clean += 1
        return {
            "total": len(signals),
            "flagged": flagged,
            "clean": clean,
            "results": [self._result_to_dict(r) for r in results],
        }

    @staticmethod
    def _result_to_dict(result: ValidationResult) -> Dict[str, Any]:
        return {
            "signal_id": result.signal_id,
            "source_type": result.source_type,
            "flagged": result.flagged,
            "status": result.status,
            "issues": result.issues,
            "bias_score": result.bias_score,
            "deepfake_score": result.deepfake_score,
            "manipulation_score": result.manipulation_score,
            "timestamp": result.timestamp,
        }

    def health(self) -> Dict[str, Any]:
        return {
            "status": "ok",
            "detectors": {
                "bias": "ready",
                "deepfake": "ready",
                "manipulation": "ready",
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


# ---------------------------------------------------------------------------
# Batch convenience function
# ---------------------------------------------------------------------------

def validate_signals(signals: List[Dict[str, Any]]) -> Dict[str, Any]:
    validator = AdversarialValidator()
    return validator.validate_batch(signals)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Adversarial Data Validation Pipeline")
    p.add_argument("--signal", type=str, help="JSON signal object to validate")
    p.add_argument("--signals-file", type=str, help="JSON file with signals array")
    p.add_argument("--json", action="store_true", help="Emit JSON output")
    p.add_argument("--health", action="store_true", help="Health check")
    p.add_argument("--gate", action="store_true", help="Exit non-zero if any signal is flagged/blocked")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    validator = AdversarialValidator()

    if args.health:
        result = validator.health()
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Status: {result['status']}")
        return 0

    signals: List[Dict[str, Any]] = []
    if args.signal:
        signals = [json.loads(args.signal)]
    elif args.signals_file:
        raw = Path(args.signals_file).read_text(encoding="utf-8")
        data = json.loads(raw)
        signals = data if isinstance(data, list) else data.get("signals", [])

    if not signals:
        print("No signals provided.", file=sys.stderr)
        return 1

    batch = validator.validate_batch(signals)

    if args.json:
        print(json.dumps(batch, indent=2))
    else:
        print(
            f"Validated {batch['total']} signal(s): "
            f"{batch['flagged']} flagged, {batch['clean']} clean"
        )

    if args.gate and batch["flagged"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
