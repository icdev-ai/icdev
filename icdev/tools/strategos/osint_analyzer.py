#!/usr/bin/env python3
# CUI // SP-CTI
"""
OSINT Analyzer — bias and deepfake detection for satellite imagery.

Implements heuristic checks for satellite imagery:
  1. Metadata integrity: timestamp consistency, sensor calibration, field completeness
  2. Pixel-level anomalies: block uniformity, noise consistency, histogram distribution

Each check returns a list of SatelliteFlag objects. The top-level analyze()
function combines all checks into an AnalysisResult.

NIST 800-53: SI-3 (Malicious Code Protection), SI-10 (Information Input Validation),
             AU-9 (Protection of Audit Information), SA-11 (Developer Security Testing)
Classification: CUI // SP-CTI

Usage:
  python -m tools.strategos.osint_analyzer --metadata metadata.json --json
  python -m tools.strategos.osint_analyzer --scene scene.json --json
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Severity constants
# ---------------------------------------------------------------------------

SEVERITY_LOW = "LOW"
SEVERITY_MEDIUM = "MEDIUM"
SEVERITY_HIGH = "HIGH"
SEVERITY_CRITICAL = "CRITICAL"

# ---------------------------------------------------------------------------
# Heuristic thresholds
# ---------------------------------------------------------------------------

# Landsat-1 launch date — first commercial EO satellite
_SATELLITE_ERA_EPOCH = date(1972, 7, 23)

REQUIRED_METADATA_FIELDS = frozenset({"scene_id", "satellite", "sensing_date"})
CLOUD_PCT_RANGE = (0.0, 100.0)
SCORE_RANGE = (0.0, 1.0)

# Pixel analysis
BLOCK_SIZE = 8
UNIFORM_STD_THRESHOLD = 2.0      # blocks below this std are suspiciously uniform
UNIFORM_BLOCK_RATIO = 0.50       # flag if >50% of blocks are unnaturally flat
NOISE_RATIO_THRESHOLD = 3.0      # max/min quadrant noise ratio before flagging
UNIQUE_VALUES_MIN = 16           # minimum unique pixel values expected in 8-bit imagery

# Confidence weights per severity level
_SEVERITY_WEIGHTS: dict[str, float] = {
    SEVERITY_LOW: 0.05,
    SEVERITY_MEDIUM: 0.15,
    SEVERITY_HIGH: 0.35,
    SEVERITY_CRITICAL: 0.75,
}

# manipulation_likely=True when any HIGH/CRITICAL flag exists OR confidence exceeds this
_MANIPULATION_THRESHOLD = 0.12


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class SatelliteFlag:
    rule: str
    severity: str
    detail: str


@dataclass
class AnalysisResult:
    manipulation_likely: bool
    confidence: float
    flags: list[SatelliteFlag] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "manipulation_likely": self.manipulation_likely,
            "confidence": round(self.confidence, 4),
            "flags": [
                {"rule": f.rule, "severity": f.severity, "detail": f.detail}
                for f in self.flags
            ],
        }


# ---------------------------------------------------------------------------
# Metadata checks
# ---------------------------------------------------------------------------

def check_metadata_completeness(metadata: dict[str, Any]) -> list[SatelliteFlag]:
    """Flag required fields that are absent or empty."""
    missing = sorted(k for k in REQUIRED_METADATA_FIELDS if not metadata.get(k))
    if not missing:
        return []
    return [SatelliteFlag(
        rule="metadata_missing_required_fields",
        severity=SEVERITY_HIGH,
        detail=f"Missing or empty required fields: {missing}",
    )]


def check_timestamp_consistency(metadata: dict[str, Any]) -> list[SatelliteFlag]:
    """
    Flag timestamp anomalies indicative of fabricated metadata.

    Checks performed:
    - sensing_date in the future          → CRITICAL
    - sensing_date before satellite era   → HIGH
    - processing timestamp before sensing → HIGH (impossible without time travel)
    """
    flags: list[SatelliteFlag] = []
    sensing_raw = metadata.get("sensing_date")
    if not sensing_raw:
        return flags

    try:
        if isinstance(sensing_raw, datetime):
            sensing = sensing_raw.date()
        elif isinstance(sensing_raw, date):
            sensing = sensing_raw
        else:
            sensing = date.fromisoformat(str(sensing_raw)[:10])
    except (ValueError, TypeError):
        return [SatelliteFlag(
            rule="timestamp_parse_error",
            severity=SEVERITY_HIGH,
            detail=f"sensing_date cannot be parsed as an ISO date: {sensing_raw!r}",
        )]

    today = datetime.now(timezone.utc).date()

    if sensing > today:
        flags.append(SatelliteFlag(
            rule="timestamp_future",
            severity=SEVERITY_CRITICAL,
            detail=f"sensing_date {sensing} is in the future (today={today}); indicates fabricated metadata",
        ))
    elif sensing < _SATELLITE_ERA_EPOCH:
        flags.append(SatelliteFlag(
            rule="timestamp_pre_satellite_era",
            severity=SEVERITY_HIGH,
            detail=f"sensing_date {sensing} predates first commercial EO satellite ({_SATELLITE_ERA_EPOCH})",
        ))

    for ts_field in ("created_at", "ingested_at", "processed_at"):
        other_raw = metadata.get(ts_field)
        if not other_raw:
            continue
        try:
            other = date.fromisoformat(str(other_raw)[:10])
            if other < sensing:
                flags.append(SatelliteFlag(
                    rule="timestamp_processing_before_sensing",
                    severity=SEVERITY_HIGH,
                    detail=(
                        f"{ts_field}={other} predates sensing_date={sensing}; "
                        "processing cannot precede acquisition — metadata may be fabricated"
                    ),
                ))
        except (ValueError, TypeError):
            pass

    return flags


def check_sensor_calibration(metadata: dict[str, Any]) -> list[SatelliteFlag]:
    """
    Flag sensor-derived values outside physically plausible ranges.

    Checked fields: cloud_pct, relevance_score, resolution_m, incidence_angle.
    Implausible values suggest injected or synthetically generated metadata.
    """
    flags: list[SatelliteFlag] = []

    cloud_pct = metadata.get("cloud_pct")
    if cloud_pct is not None:
        try:
            v = float(cloud_pct)
            if not (CLOUD_PCT_RANGE[0] <= v <= CLOUD_PCT_RANGE[1]):
                flags.append(SatelliteFlag(
                    rule="sensor_cloud_pct_out_of_range",
                    severity=SEVERITY_HIGH,
                    detail=f"cloud_pct={v} is outside [0, 100]; physically impossible",
                ))
        except (TypeError, ValueError):
            flags.append(SatelliteFlag(
                rule="sensor_cloud_pct_invalid",
                severity=SEVERITY_MEDIUM,
                detail=f"cloud_pct is not numeric: {cloud_pct!r}",
            ))

    relevance_score = metadata.get("relevance_score")
    if relevance_score is not None:
        try:
            v = float(relevance_score)
            if not (SCORE_RANGE[0] <= v <= SCORE_RANGE[1]):
                flags.append(SatelliteFlag(
                    rule="sensor_relevance_score_out_of_range",
                    severity=SEVERITY_MEDIUM,
                    detail=f"relevance_score={v} is outside [0, 1]",
                ))
        except (TypeError, ValueError):
            flags.append(SatelliteFlag(
                rule="sensor_relevance_score_invalid",
                severity=SEVERITY_LOW,
                detail=f"relevance_score is not numeric: {relevance_score!r}",
            ))

    resolution_m = metadata.get("resolution_m")
    if resolution_m is not None:
        try:
            v = float(resolution_m)
            if v <= 0:
                flags.append(SatelliteFlag(
                    rule="sensor_resolution_non_positive",
                    severity=SEVERITY_HIGH,
                    detail=f"resolution_m={v} is not positive; physically impossible",
                ))
            elif v < 0.1:
                flags.append(SatelliteFlag(
                    rule="sensor_resolution_implausibly_high",
                    severity=SEVERITY_MEDIUM,
                    detail=f"resolution_m={v} m is sub-decimeter; no current satellite achieves this",
                ))
        except (TypeError, ValueError):
            pass

    incidence_angle = metadata.get("incidence_angle")
    if incidence_angle is not None:
        try:
            v = float(incidence_angle)
            if not (0.0 <= v <= 90.0):
                flags.append(SatelliteFlag(
                    rule="sensor_incidence_angle_out_of_range",
                    severity=SEVERITY_HIGH,
                    detail=f"incidence_angle={v}° is outside [0, 90]; physically impossible",
                ))
        except (TypeError, ValueError):
            pass

    return flags


def analyze_metadata(metadata: dict[str, Any]) -> AnalysisResult:
    """Run all metadata integrity checks and return an AnalysisResult."""
    flags: list[SatelliteFlag] = []
    flags.extend(check_metadata_completeness(metadata))
    flags.extend(check_timestamp_consistency(metadata))
    flags.extend(check_sensor_calibration(metadata))
    return _build_result(flags)


# ---------------------------------------------------------------------------
# Pixel-level checks (numpy required; silently skipped if unavailable)
# ---------------------------------------------------------------------------

def _try_numpy():
    try:
        import numpy as np  # noqa: PLC0415
        return np
    except ImportError:
        return None


def check_block_uniformity(pixels, block_size: int = BLOCK_SIZE) -> list[SatelliteFlag]:
    """
    Detect blocks with unnaturally low variance.

    Real satellite sensor imagery always contains measurable photon shot noise
    and readout noise.  Regions with near-zero variance indicate synthetic
    generation, copy-fill operations, or aggressive post-processing.
    """
    np = _try_numpy()
    if np is None:
        return []

    arr = np.asarray(pixels, dtype=float)
    if arr.ndim != 2 or arr.shape[0] < block_size or arr.shape[1] < block_size:
        return []

    h, w = arr.shape
    uniform = total = 0
    for r in range(0, h - block_size + 1, block_size):
        for c in range(0, w - block_size + 1, block_size):
            total += 1
            if float(np.std(arr[r:r + block_size, c:c + block_size])) < UNIFORM_STD_THRESHOLD:
                uniform += 1

    if total == 0:
        return []

    ratio = uniform / total
    if ratio > UNIFORM_BLOCK_RATIO:
        return [SatelliteFlag(
            rule="pixel_block_uniformity_anomaly",
            severity=SEVERITY_HIGH,
            detail=(
                f"{uniform}/{total} image blocks ({ratio:.1%}) have std < {UNIFORM_STD_THRESHOLD}; "
                "unnaturally uniform for real sensor imagery — potential synthetic generation or inpainting"
            ),
        )]
    return []


def check_noise_consistency(pixels) -> list[SatelliteFlag]:
    """
    Detect abrupt noise level discontinuities between image quadrants.

    Spliced imagery (combining acquisitions from different sensors, orbits,
    or times) produces statistically distinct noise profiles in different
    regions.  A noise ratio > 3x between quadrants is a strong splice indicator.
    """
    np = _try_numpy()
    if np is None:
        return []

    arr = np.asarray(pixels, dtype=float)
    if arr.ndim != 2 or arr.shape[0] < 4 or arr.shape[1] < 4:
        return []

    mh, mw = arr.shape[0] // 2, arr.shape[1] // 2
    quadrants = {
        "top-left":     arr[:mh, :mw],
        "top-right":    arr[:mh, mw:],
        "bottom-left":  arr[mh:, :mw],
        "bottom-right": arr[mh:, mw:],
    }
    stds = {name: float(np.std(q)) for name, q in quadrants.items()}
    max_std = max(stds.values())
    min_std = min(stds.values())

    if min_std < 1e-9:
        if max_std > UNIFORM_STD_THRESHOLD:
            return [SatelliteFlag(
                rule="pixel_noise_region_mismatch",
                severity=SEVERITY_CRITICAL,
                detail=(
                    f"Flat quadrant (std≈0) alongside noisy quadrant (std={max_std:.2f}); "
                    "strong indicator of image splicing or synthetic region patching"
                ),
            )]
        return []

    ratio = max_std / min_std
    if ratio > NOISE_RATIO_THRESHOLD:
        noisy = max(stds, key=stds.__getitem__)
        flat = min(stds, key=stds.__getitem__)
        return [SatelliteFlag(
            rule="pixel_noise_consistency_anomaly",
            severity=SEVERITY_HIGH,
            detail=(
                f"Quadrant noise ratio {noisy}/{flat} = {ratio:.1f}x "
                f"(threshold {NOISE_RATIO_THRESHOLD}x); suggests image splicing from multiple sources"
            ),
        )]
    return []


def check_histogram_anomaly(pixels, bit_depth: int = 8) -> list[SatelliteFlag]:
    """
    Detect over-quantization: too few unique pixel values.

    AI-generated and heavily post-processed imagery loses the full dynamic
    range of real sensor data.  Very few unique values in an 8-bit image
    indicate synthetic generation, palette-quantized composites, or extreme
    lossy compression artifacts.
    """
    np = _try_numpy()
    if np is None:
        return []

    arr = np.asarray(pixels).flatten().astype(int)
    if len(arr) == 0:
        return []

    unique_count = int(np.unique(arr).size)
    if unique_count < UNIQUE_VALUES_MIN:
        return [SatelliteFlag(
            rule="pixel_histogram_over_quantized",
            severity=SEVERITY_HIGH,
            detail=(
                f"Only {unique_count} unique pixel values "
                f"(expected ≥{UNIQUE_VALUES_MIN} for {bit_depth}-bit imagery); "
                "indicates synthetic generation or extreme lossy compression"
            ),
        )]
    return []


def analyze_pixels(pixels, bit_depth: int = 8) -> AnalysisResult:
    """
    Run all pixel-level anomaly checks and return an AnalysisResult.

    pixels    — 2-D array-like (rows × cols) of integer grayscale pixel values.
    bit_depth — image bit depth (default 8 for uint8 imagery).
    """
    flags: list[SatelliteFlag] = []
    flags.extend(check_block_uniformity(pixels))
    flags.extend(check_noise_consistency(pixels))
    flags.extend(check_histogram_anomaly(pixels, bit_depth=bit_depth))
    return _build_result(flags)


# ---------------------------------------------------------------------------
# Combined entry point
# ---------------------------------------------------------------------------

def analyze(
    metadata: dict[str, Any] | None = None,
    pixels=None,
    bit_depth: int = 8,
) -> AnalysisResult:
    """
    Run the full satellite imagery analysis pipeline.

    Combines metadata integrity checks with pixel-level anomaly detection.
    At least one of metadata or pixels must be provided.

    Returns AnalysisResult with:
      manipulation_likely — True if any HIGH/CRITICAL flag is present
      confidence          — weighted sum of flag severities, capped at 1.0
      flags               — ordered list of SatelliteFlag findings
    """
    if metadata is None and pixels is None:
        raise ValueError("At least one of metadata or pixels must be provided")

    flags: list[SatelliteFlag] = []
    if metadata is not None:
        flags.extend(check_metadata_completeness(metadata))
        flags.extend(check_timestamp_consistency(metadata))
        flags.extend(check_sensor_calibration(metadata))
    if pixels is not None:
        flags.extend(check_block_uniformity(pixels))
        flags.extend(check_noise_consistency(pixels))
        flags.extend(check_histogram_anomaly(pixels, bit_depth=bit_depth))
    return _build_result(flags)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_result(flags: list[SatelliteFlag]) -> AnalysisResult:
    if not flags:
        return AnalysisResult(manipulation_likely=False, confidence=0.0, flags=[])

    total_weight = sum(_SEVERITY_WEIGHTS.get(f.severity, 0.05) for f in flags)
    confidence = min(1.0, total_weight)
    manipulation_likely = (
        any(f.severity in (SEVERITY_HIGH, SEVERITY_CRITICAL) for f in flags)
        or confidence >= _MANIPULATION_THRESHOLD
    )
    return AnalysisResult(
        manipulation_likely=manipulation_likely,
        confidence=round(confidence, 4),
        flags=flags,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None) -> None:
    ap = argparse.ArgumentParser(
        description="Satellite imagery bias and deepfake analyzer  [CUI // SP-CTI]"
    )
    ap.add_argument("--metadata", metavar="FILE",
                    help="JSON file containing image metadata dict")
    ap.add_argument("--scene", metavar="FILE",
                    help="JSON scene file with top-level 'metadata' key")
    ap.add_argument("--json", action="store_true", dest="as_json",
                    help="Output results as JSON")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="Enable debug logging")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
    )

    metadata = None
    path = args.metadata or args.scene
    if not path:
        ap.print_help()
        sys.exit(1)

    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    metadata = data.get("metadata", data) if args.scene else data

    result = analyze(metadata=metadata)

    if args.as_json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(f"manipulation_likely : {result.manipulation_likely}")
        print(f"confidence          : {result.confidence:.4f}")
        for f in result.flags:
            print(f"  [{f.severity}] {f.rule}: {f.detail}")

    sys.exit(1 if result.manipulation_likely else 0)


if __name__ == "__main__":
    main()
