#!/usr/bin/env python3
# CUI // SP-CTI
"""Adversarial data validation pipeline for OSINT sources.

Detects bias, deepfakes, and adversarial manipulation before data enters the
predictive analysis engine.

NIST 800-53 controls:
- SA-11 (Developer Security Testing)
- CM-3 (Configuration Change Control)
- SI-3 (Malicious Code Protection)
- SI-10 (Information Input Validation)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BIAS_THRESHOLD_DEFAULT = 0.75
DEEPFAKE_THRESHOLD_DEFAULT = 0.70
MANIPULATION_THRESHOLD_DEFAULT = 0.60

# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class BiasResult:
    bias_detected: bool = False
    source_concentration_score: float = 0.0
    sentiment_skew_score: float = 0.0
    dominant_source: Optional[str] = None


@dataclass
class DeepfakeResult:
    deepfake_detected: bool = False
    confidence: float = 0.0
    anomaly_flags: List[str] = field(default_factory=list)


@dataclass
class ManipulationResult:
    manipulation_detected: bool = False
    perturbation_score: float = 0.0
    injection_score: float = 0.0
    perturbation_types: List[str] = field(default_factory=list)
    injection_types: List[str] = field(default_factory=list)


@dataclass
class ValidationReport:
    bias_result: BiasResult = field(default_factory=BiasResult)
    deepfake_result: DeepfakeResult = field(default_factory=DeepfakeResult)
    manipulation_result: ManipulationResult = field(default_factory=ManipulationResult)


@dataclass
class PipelineResult:
    is_valid: bool = True
    block_reasons: List[str] = field(default_factory=list)
    report: ValidationReport = field(default_factory=ValidationReport)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "block_reasons": self.block_reasons,
            "report": {
                "bias_result": {
                    "bias_detected": self.report.bias_result.bias_detected,
                    "source_concentration_score": self.report.bias_result.source_concentration_score,
                    "sentiment_skew_score": self.report.bias_result.sentiment_skew_score,
                    "dominant_source": self.report.bias_result.dominant_source,
                },
                "deepfake_result": {
                    "deepfake_detected": self.report.deepfake_result.deepfake_detected,
                    "confidence": self.report.deepfake_result.confidence,
                    "anomaly_flags": self.report.deepfake_result.anomaly_flags,
                },
                "manipulation_result": {
                    "manipulation_detected": self.report.manipulation_result.manipulation_detected,
                    "perturbation_score": self.report.manipulation_result.perturbation_score,
                    "injection_score": self.report.manipulation_result.injection_score,
                    "perturbation_types": self.report.manipulation_result.perturbation_types,
                    "injection_types": self.report.manipulation_result.injection_types,
                },
            },
        }


# ---------------------------------------------------------------------------
# BiasDetector
# ---------------------------------------------------------------------------

class BiasDetector:
    """Detects statistical bias in OSINT signal batches."""

    # Simple negative word list for rudimentary sentiment scoring
    _NEGATIVE_WORDS = {
        "terrible", "disaster", "massive", "failure", "horrible", "catastrophe",
        "complete", "collapse", "worst", "outcome", "total", "defeat", "panic",
        "doom", "crisis", "tragedy", "devastating", "awful", "fatal",
    }

    def detect(self, signals: List[Dict[str, Any]]) -> BiasResult:
        if not signals:
            return BiasResult(
                bias_detected=False,
                source_concentration_score=0.0,
                sentiment_skew_score=0.0,
            )

        # Source concentration
        source_counts: Dict[str, int] = {}
        for s in signals:
            src = s.get("source", "unknown")
            source_counts[src] = source_counts.get(src, 0) + 1

        max_count = max(source_counts.values())
        concentration = max_count / len(signals)
        dominant = max(source_counts, key=source_counts.get)

        # Sentiment skew
        total_negative = 0
        total_words = 0
        for s in signals:
            text = s.get("text", "")
            words = re.findall(r"[A-Za-z]+", text.lower())
            total_words += len(words)
            total_negative += sum(1 for w in words if w in self._NEGATIVE_WORDS)

        if total_words > 0:
            sentiment_skew = total_negative / total_words
        else:
            sentiment_skew = 0.0

        # Source concentration — require minimum sample size before flagging
        min_signals_for_concentration_bias = 3
        concentration_bias = (
            len(signals) >= min_signals_for_concentration_bias
            and concentration >= BIAS_THRESHOLD_DEFAULT
        )
        sentiment_bias = sentiment_skew > 0.5

        bias_detected = concentration_bias or sentiment_bias

        return BiasResult(
            bias_detected=bias_detected,
            source_concentration_score=concentration,
            sentiment_skew_score=sentiment_skew,
            dominant_source=dominant if bias_detected else None,
        )


# ---------------------------------------------------------------------------
# DeepfakeDetector
# ---------------------------------------------------------------------------

class DeepfakeDetector:
    """Detects deepfake indicators in OSINT media metadata."""

    # Image mime types we can reason about
    _IMAGE_MIMES = {
        "image/jpeg", "image/png", "image/gif", "image/webp", "image/tiff",
    }

    def _fetch_reference_hash(self, source_url: str) -> Optional[str]:
        """Placeholder: in production, fetches reference hash from known-good index."""
        return None

    def detect(self, media: Dict[str, Any]) -> DeepfakeResult:
        flags: List[str] = []
        mime = media.get("mime_type", "")

        if mime not in self._IMAGE_MIMES:
            return DeepfakeResult(
                deepfake_detected=False,
                confidence=0.0,
                anomaly_flags=["unsupported_media_type"],
            )

        # Check metadata presence
        exif_make = media.get("exif_make")
        exif_model = media.get("exif_model")
        if not exif_make or not exif_model:
            flags.append("missing_camera_metadata")

        # Check file size
        size = media.get("file_size_bytes", 0)
        if size < 10240:
            flags.append("small_file_size")

        # Check hash against reference if URL provided
        source_url = media.get("source_url")
        if source_url:
            reference = self._fetch_reference_hash(source_url)
            if reference is not None:
                actual = media.get("hash_sha256", "")
                if actual != reference:
                    flags.append("hash_mismatch")

        # Confidence: each flag adds 0.35, capped at 1.0
        confidence = min(len(flags) * 0.35, 1.0)
        deepfake_detected = confidence >= DEEPFAKE_THRESHOLD_DEFAULT

        return DeepfakeResult(
            deepfake_detected=deepfake_detected,
            confidence=confidence,
            anomaly_flags=flags,
        )


# ---------------------------------------------------------------------------
# AdversarialManipulationDetector
# ---------------------------------------------------------------------------

class AdversarialManipulationDetector:
    """Detects adversarial text perturbations and injection patterns."""

    # Leet-style substitution regex: digits/symbols mixed into words
    _PERTURBATION_RE = re.compile(r"[a-zA-Z]*[0-9@$!%*?&][a-zA-Z0-9@$!%*?&]*")

    # Injection keywords
    _INJECTION_KEYWORDS = [
        "ignore previous instructions",
        "ignore all rules",
        "system override",
        "override all safeguards",
        "output '",
        "disregard",
        "bypass",
        "jailbreak",
    ]

    # Data poisoning patterns
    _POISONING_RE = re.compile(r"\{\{repeat\s+\d+\}\}.*\{\{/repeat\}\}", re.IGNORECASE)

    def detect(self, text: str) -> ManipulationResult:
        if not text:
            return ManipulationResult(
                manipulation_detected=False,
                perturbation_score=0.0,
                injection_score=0.0,
            )

        # Perturbation scoring
        pert_types: List[str] = []
        # Count words that contain substitutions vs total words
        words = re.findall(r"\S+", text)
        pert_words = [w for w in words if self._PERTURBATION_RE.fullmatch(w)]
        if pert_words:
            pert_ratio = len(pert_words) / len(words)
            pert_types.append("character_substitution")
        else:
            pert_ratio = 0.0

        # Poisoning pattern
        if self._POISONING_RE.search(text):
            pert_types.append("data_poisoning_pattern")
            pert_ratio = max(pert_ratio, 0.8)

        # Injection scoring
        inj_types: List[str] = []
        text_lower = text.lower()
        inj_hits = sum(1 for kw in self._INJECTION_KEYWORDS if kw in text_lower)
        inj_score = min(inj_hits * 0.35, 1.0)
        if inj_hits > 0:
            inj_types.append("prompt_injection")

        manipulation_detected = (
            pert_ratio > MANIPULATION_THRESHOLD_DEFAULT
            or inj_score > MANIPULATION_THRESHOLD_DEFAULT
        )

        return ManipulationResult(
            manipulation_detected=manipulation_detected,
            perturbation_score=pert_ratio,
            injection_score=inj_score,
            perturbation_types=pert_types,
            injection_types=inj_types,
        )


# ---------------------------------------------------------------------------
# AdversarialValidationPipeline
# ---------------------------------------------------------------------------

class AdversarialValidationPipeline:
    """Orchestrates bias, deepfake, and manipulation detection on OSINT batches."""

    def __init__(
        self,
        bias_detector: Optional[BiasDetector] = None,
        deepfake_detector: Optional[DeepfakeDetector] = None,
        manipulation_detector: Optional[AdversarialManipulationDetector] = None,
    ):
        self.bias_detector = bias_detector or BiasDetector()
        self.deepfake_detector = deepfake_detector or DeepfakeDetector()
        self.manipulation_detector = manipulation_detector or AdversarialManipulationDetector()

    def validate(self, data: Dict[str, Any]) -> PipelineResult:
        block_reasons: List[str] = []

        # Signals / bias + manipulation
        signals = data.get("signals", [])
        if signals:
            bias_result = self.bias_detector.detect(signals)
            if bias_result.bias_detected:
                block_reasons.append("bias")

            # Aggregate manipulation across all signal texts
            max_pert = 0.0
            max_inj = 0.0
            all_pert_types: set[str] = set()
            all_inj_types: set[str] = set()
            manip_detected = False
            for s in signals:
                text = s.get("text", "")
                mr = self.manipulation_detector.detect(text)
                if mr.manipulation_detected:
                    manip_detected = True
                max_pert = max(max_pert, mr.perturbation_score)
                max_inj = max(max_inj, mr.injection_score)
                all_pert_types.update(mr.perturbation_types)
                all_inj_types.update(mr.injection_types)

            manipulation_result = ManipulationResult(
                manipulation_detected=manip_detected,
                perturbation_score=max_pert,
                injection_score=max_inj,
                perturbation_types=list(all_pert_types),
                injection_types=list(all_inj_types),
            )
            if manipulation_result.manipulation_detected:
                block_reasons.append("manipulation")
        else:
            bias_result = BiasResult()
            manipulation_result = ManipulationResult()

        # Media / deepfake
        media = data.get("media")
        if media:
            deepfake_result = self.deepfake_detector.detect(media)
            if deepfake_result.deepfake_detected:
                block_reasons.append("deepfake")
        else:
            deepfake_result = DeepfakeResult()

        report = ValidationReport(
            bias_result=bias_result,
            deepfake_result=deepfake_result,
            manipulation_result=manipulation_result,
        )

        return PipelineResult(
            is_valid=len(block_reasons) == 0,
            block_reasons=block_reasons,
            report=report,
        )
