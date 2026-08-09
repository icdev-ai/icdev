# [CUI // SP-CTI]
"""ICDEV™ Network Canvas — OCR-Based Diagram Extraction Fallback.

Provides traditional OCR extraction for network diagram images when vision
LLMs (vLLM, Ollama LLaVA, cloud APIs) are unavailable. Uses ensemble or
single-provider mode with spatial proximity inference to reconstruct
network topology from extracted text labels and bounding boxes.

Providers:
  - pytesseract (primary): Requires system Tesseract binary
  - rapidocr-onnxruntime (fallback): Pure Python ONNX, no system deps

Usage::

    python tools/network/ocr_fallback.py --image diagram.png --json
    python tools/network/ocr_fallback.py --check --json

Air-gap safe: both providers work fully offline once installed.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("icdev.network.ocr_fallback")

# ── Data classes ────────────────────────────────────────────────────────────


@dataclass
class OCRResult:
    """Single OCR detection: text + bounding box + confidence."""

    text: str
    confidence: float
    bbox: tuple[int, int, int, int]  # (x1, y1, x2, y2) pixels

    @property
    def center_x(self) -> float:
        return (self.bbox[0] + self.bbox[2]) / 2.0

    @property
    def center_y(self) -> float:
        return (self.bbox[1] + self.bbox[3]) / 2.0

    @property
    def width(self) -> int:
        return self.bbox[2] - self.bbox[0]

    @property
    def height(self) -> int:
        return self.bbox[3] - self.bbox[1]

    @property
    def area(self) -> int:
        return self.width * self.height


# ── OCR Provider ABC ───────────────────────────────────────────────────────


class OCRProvider(ABC):
    """Abstract base class for OCR backends."""

    provider_name: str = "base"

    @abstractmethod
    def check_availability(self) -> bool:
        """Return True if this OCR provider is installed and functional."""

    @abstractmethod
    def extract_text_with_boxes(self, image_path: Path) -> list[OCRResult]:
        """Run OCR on an image, return text detections with bounding boxes."""


class TesseractOCRProvider(OCRProvider):
    """pytesseract backend. Requires Tesseract binary installed on system.

    On Windows: choco install tesseract or download from GitHub.
    On Linux: apt-get install tesseract-ocr
    """

    provider_name = "tesseract"

    def __init__(
        self,
        tesseract_cmd: str = "",
        lang: str = "eng",
        psm: int = 3,
    ) -> None:
        self._tesseract_cmd = tesseract_cmd
        self._lang = lang
        self._psm = psm

    def check_availability(self) -> bool:
        try:
            import pytesseract  # type: ignore

            if self._tesseract_cmd:
                pytesseract.pytesseract.tesseract_cmd = self._tesseract_cmd
            pytesseract.get_tesseract_version()
            return True
        except Exception:
            return False

    def extract_text_with_boxes(self, image_path: Path) -> list[OCRResult]:
        import pytesseract  # type: ignore
        from PIL import Image

        if self._tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = self._tesseract_cmd

        img = Image.open(image_path)
        data = pytesseract.image_to_data(
            img,
            lang=self._lang,
            config=f"--psm {self._psm}",
            output_type=pytesseract.Output.DICT,
        )

        results: list[OCRResult] = []
        n_boxes = len(data["text"])
        for i in range(n_boxes):
            text = str(data["text"][i]).strip()
            conf = float(data["conf"][i])
            if not text or conf < 0:
                continue
            # pytesseract returns confidence 0-100, normalize to 0-1
            confidence = conf / 100.0
            x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
            results.append(OCRResult(
                text=text,
                confidence=confidence,
                bbox=(x, y, x + w, y + h),
            ))

        # Group adjacent words on the same line into phrases
        return _group_adjacent_words(results)


class RapidOCRProvider(OCRProvider):
    """rapidocr-onnxruntime backend. Pure Python + ONNX, no system deps."""

    provider_name = "rapidocr"

    def check_availability(self) -> bool:
        try:
            from rapidocr_onnxruntime import RapidOCR  # type: ignore  # noqa: F401

            return True
        except Exception:
            return False

    def extract_text_with_boxes(self, image_path: Path) -> list[OCRResult]:
        from rapidocr_onnxruntime import RapidOCR  # type: ignore

        engine = RapidOCR()
        result, _ = engine(str(image_path))

        results: list[OCRResult] = []
        if not result:
            return results

        for item in result:
            # RapidOCR returns: [[x1,y1],[x2,y2],[x3,y3],[x4,y4]], text, score
            box_points, text, score = item
            if not text or not text.strip():
                continue
            # Convert 4-point polygon to axis-aligned bbox
            xs = [p[0] for p in box_points]
            ys = [p[1] for p in box_points]
            bbox = (int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys)))
            results.append(OCRResult(
                text=text.strip(),
                confidence=float(score) if score else 0.5,
                bbox=bbox,
            ))

        return results


# ── Word grouping ──────────────────────────────────────────────────────────


def _group_adjacent_words(
    results: list[OCRResult],
    x_gap_threshold: int = 15,
    y_overlap_threshold: float = 0.5,
) -> list[OCRResult]:
    """Group horizontally adjacent words on the same line into phrases.

    Tesseract returns per-word detections. Network diagrams have multi-word
    labels like 'Core Router 1' that should be a single detection.
    """
    if not results:
        return results

    # Sort by y-center then x
    sorted_results = sorted(results, key=lambda r: (r.center_y, r.bbox[0]))
    groups: list[list[OCRResult]] = []
    current_group: list[OCRResult] = [sorted_results[0]]

    for i in range(1, len(sorted_results)):
        prev = current_group[-1]
        curr = sorted_results[i]

        # Check if on the same horizontal line (y-overlap)
        overlap_top = max(prev.bbox[1], curr.bbox[1])
        overlap_bot = min(prev.bbox[3], curr.bbox[3])
        overlap_height = max(0, overlap_bot - overlap_top)
        min_height = min(prev.height, curr.height) or 1
        y_overlap = overlap_height / min_height

        # Check horizontal gap
        x_gap = curr.bbox[0] - prev.bbox[2]

        if y_overlap >= y_overlap_threshold and x_gap <= x_gap_threshold:
            current_group.append(curr)
        else:
            groups.append(current_group)
            current_group = [curr]

    groups.append(current_group)

    # Merge each group into a single OCRResult
    merged: list[OCRResult] = []
    for group in groups:
        text = " ".join(r.text for r in group)
        confidence = sum(r.confidence for r in group) / len(group)
        x1 = min(r.bbox[0] for r in group)
        y1 = min(r.bbox[1] for r in group)
        x2 = max(r.bbox[2] for r in group)
        y2 = max(r.bbox[3] for r in group)
        merged.append(OCRResult(text=text, confidence=confidence, bbox=(x1, y1, x2, y2)))

    return merged


# ── Provider chain factory ─────────────────────────────────────────────────


def get_ocr_provider_chain(config: dict | None = None) -> list[OCRProvider]:
    """Build OCR provider chain. Returns list of available providers."""
    config = config or {}
    chain_names = config.get("provider_chain", ["tesseract", "rapidocr"])

    factory_map: dict[str, type[OCRProvider]] = {
        "tesseract": TesseractOCRProvider,
        "rapidocr": RapidOCRProvider,
    }

    providers: list[OCRProvider] = []
    for name in chain_names:
        cls = factory_map.get(name)
        if not cls:
            logger.debug("Unknown OCR provider: %s", name)
            continue

        if name == "tesseract":
            tess_cfg = config.get("tesseract", {})
            provider = cls(
                tesseract_cmd=tess_cfg.get("tesseract_cmd", ""),
                lang=tess_cfg.get("lang", "eng"),
                psm=tess_cfg.get("psm", 3),
            )
        else:
            provider = cls()

        try:
            if provider.check_availability():
                providers.append(provider)
                logger.debug("OCR provider available: %s", name)
            else:
                logger.debug("OCR provider not available: %s", name)
        except Exception as e:
            logger.debug("OCR provider %s check failed: %s", name, e)

    return providers


# ── Ensemble merging ───────────────────────────────────────────────────────


def _compute_iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    """Compute Intersection over Union of two bounding boxes."""
    x1 = max(a[0], b[0])
    y1 = max(a[1], b[1])
    x2 = min(a[2], b[2])
    y2 = min(a[3], b[3])
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def _merge_ocr_results(
    results_a: list[OCRResult],
    results_b: list[OCRResult],
    iou_threshold: float = 0.5,
    confidence_boost: float = 0.2,
) -> list[OCRResult]:
    """Merge OCR results from two providers using bbox IoU overlap.

    Strategy:
    1. For overlapping detections (IoU > threshold): keep higher confidence,
       boost by confidence_boost for cross-engine confirmation.
    2. Non-overlapping detections from either engine are added (union).
    """
    merged: list[OCRResult] = []
    used_b: set[int] = set()

    for a in results_a:
        best_match_idx: int | None = None
        best_iou = 0.0

        for j, b in enumerate(results_b):
            if j in used_b:
                continue
            iou = _compute_iou(a.bbox, b.bbox)
            if iou > best_iou:
                best_iou = iou
                best_match_idx = j

        if best_match_idx is not None and best_iou >= iou_threshold:
            # Cross-engine match — keep higher confidence, boost
            b = results_b[best_match_idx]
            used_b.add(best_match_idx)
            winner = a if a.confidence >= b.confidence else b
            boosted_conf = min(1.0, winner.confidence + confidence_boost)
            merged.append(OCRResult(
                text=winner.text,
                confidence=boosted_conf,
                bbox=winner.bbox,
            ))
        else:
            # Only in provider A
            merged.append(a)

    # Add unmatched from provider B (union)
    for j, b in enumerate(results_b):
        if j not in used_b:
            merged.append(b)

    return merged


# ── Image preprocessing ────────────────────────────────────────────────────


def _preprocess_for_ocr(image_path: Path, config: dict) -> Path:
    """Preprocess image for OCR: grayscale, contrast, denoise.

    Returns path to preprocessed image (may be a temp file).
    Uses Pillow (already in requirements.txt).
    """
    prep_cfg = config.get("preprocessing", {})
    if not any([prep_cfg.get("grayscale"), prep_cfg.get("contrast_enhance", 1.0) != 1.0, prep_cfg.get("denoise")]):
        return image_path  # No preprocessing needed

    import tempfile

    from PIL import Image, ImageEnhance, ImageFilter

    img = Image.open(image_path)

    if prep_cfg.get("grayscale", True):
        img = img.convert("L")

    contrast = prep_cfg.get("contrast_enhance", 1.5)
    if contrast != 1.0:
        img = ImageEnhance.Contrast(img).enhance(contrast)

    if prep_cfg.get("denoise", False):
        img = img.filter(ImageFilter.MedianFilter(3))

    # Save to temp file
    suffix = image_path.suffix or ".png"
    tmp_path = Path(tempfile.gettempdir()) / f"icdev_ocr_prep_{uuid.uuid4().hex[:8]}{suffix}"
    img.save(str(tmp_path))
    return tmp_path


# ── Spatial inference ──────────────────────────────────────────────────────


def _euclidean_distance(x1: float, y1: float, x2: float, y2: float) -> float:
    return math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)


def _point_on_segment_proximity(
    px: float, py: float,
    ax: float, ay: float,
    bx: float, by: float,
    tolerance: float,
) -> bool:
    """Check if point (px, py) is within tolerance distance of line segment AB."""
    # Project point onto line, clamp to segment
    dx, dy = bx - ax, by - ay
    seg_len_sq = dx * dx + dy * dy
    if seg_len_sq == 0:
        return _euclidean_distance(px, py, ax, ay) <= tolerance

    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg_len_sq))
    proj_x = ax + t * dx
    proj_y = ay + t * dy
    return _euclidean_distance(px, py, proj_x, proj_y) <= tolerance


def _infer_connections_from_spatial(
    device_boxes: list[dict],
    annotation_boxes: list[dict],
    proximity_threshold_px: int = 300,
    alignment_tolerance_px: int = 50,
) -> list[dict]:
    """Infer edges from bounding box spatial relationships.

    Args:
        device_boxes: List of dicts with id, label, type, center_x, center_y.
        annotation_boxes: List of dicts with text, center_x, center_y (non-device text).
        proximity_threshold_px: Max distance between device centers to create edge.
        alignment_tolerance_px: Tolerance for annotation-on-line check.

    Returns:
        List of edge dicts: {id, source, target, label, type}.
    """
    from tools.network.network_ingester import _classify_link_type

    edges: list[dict] = []
    seen_pairs: set[tuple[str, str]] = set()

    n = len(device_boxes)
    if n < 2:
        return edges

    # Compute all pairwise distances
    pairs: list[tuple[float, int, int]] = []
    for i in range(n):
        for j in range(i + 1, n):
            d = _euclidean_distance(
                device_boxes[i]["center_x"], device_boxes[i]["center_y"],
                device_boxes[j]["center_x"], device_boxes[j]["center_y"],
            )
            if d <= proximity_threshold_px:
                pairs.append((d, i, j))

    # Sort by distance (closest first)
    pairs.sort()

    # Compute median distance for outlier pruning
    if pairs:
        distances = [p[0] for p in pairs]
        median_dist = sorted(distances)[len(distances) // 2]
        max_allowed = median_dist * 2.0
    else:
        max_allowed = proximity_threshold_px

    # Track edge count per device for pruning
    edge_count: dict[str, int] = {}
    max_edges_per_device = 8

    for dist, i, j in pairs:
        if dist > max_allowed:
            continue

        dev_i = device_boxes[i]
        dev_j = device_boxes[j]
        pair_key = tuple(sorted([dev_i["id"], dev_j["id"]]))

        if pair_key in seen_pairs:
            continue
        if edge_count.get(dev_i["id"], 0) >= max_edges_per_device:
            continue
        if edge_count.get(dev_j["id"], 0) >= max_edges_per_device:
            continue

        # Check for annotation text on the line between devices
        edge_label = ""
        edge_type = "ethernet"
        for ann in annotation_boxes:
            if _point_on_segment_proximity(
                ann["center_x"], ann["center_y"],
                dev_i["center_x"], dev_i["center_y"],
                dev_j["center_x"], dev_j["center_y"],
                alignment_tolerance_px,
            ):
                edge_label = ann["text"]
                edge_type = _classify_link_type(ann["text"])
                break

        edges.append({
            "id": str(uuid.uuid4())[:8],
            "source": dev_i["id"],
            "target": dev_j["id"],
            "label": edge_label,
            "type": edge_type,
        })
        seen_pairs.add(pair_key)
        edge_count[dev_i["id"]] = edge_count.get(dev_i["id"], 0) + 1
        edge_count[dev_j["id"]] = edge_count.get(dev_j["id"], 0) + 1

    return edges


# ── Topology extraction ────────────────────────────────────────────────────


def _ocr_results_to_topology(
    ocr_results: list[OCRResult],
    config: dict,
) -> dict:
    """Convert OCR results to standard topology format via device classification + spatial inference."""
    from tools.network.network_ingester import _classify_device_type

    spatial_cfg = config.get("spatial_inference", {})
    min_confidence = spatial_cfg.get("min_confidence", 0.4)

    # Filter by confidence
    filtered = [r for r in ocr_results if r.confidence >= min_confidence]
    if not filtered:
        return {"nodes": [], "edges": [], "confidence": 0.0}

    # Classify each detection
    device_boxes: list[dict] = []
    annotation_boxes: list[dict] = []

    for r in filtered:
        device_type = _classify_device_type(r.text)
        entry = {
            "text": r.text,
            "confidence": r.confidence,
            "center_x": r.center_x,
            "center_y": r.center_y,
            "bbox": r.bbox,
        }
        if device_type != "unknown":
            node_id = str(uuid.uuid4())[:8]
            entry["id"] = node_id
            entry["label"] = r.text
            entry["type"] = device_type
            device_boxes.append(entry)
        else:
            annotation_boxes.append(entry)

    # Build nodes
    nodes = [
        {
            "id": d["id"],
            "label": d["label"],
            "type": d["type"],
            "x": int(d["center_x"]),
            "y": int(d["center_y"]),
        }
        for d in device_boxes
    ]

    # Infer edges
    edges = _infer_connections_from_spatial(
        device_boxes,
        annotation_boxes,
        proximity_threshold_px=spatial_cfg.get("proximity_threshold_px", 300),
        alignment_tolerance_px=spatial_cfg.get("alignment_tolerance_px", 50),
    )

    avg_confidence = sum(r.confidence for r in filtered) / len(filtered) if filtered else 0.0

    return {
        "nodes": nodes,
        "edges": edges,
        "confidence": round(avg_confidence, 3),
    }


def _ensemble_extract(
    providers: list[OCRProvider],
    image_path: Path,
    config: dict,
) -> dict:
    """Run all providers, merge results via IoU, then build topology."""
    ensemble_cfg = config.get("ensemble_settings", {})
    iou_threshold = ensemble_cfg.get("iou_threshold", 0.5)
    confidence_boost = ensemble_cfg.get("confidence_boost", 0.2)

    all_results: list[list[OCRResult]] = []
    provider_names: list[str] = []

    for provider in providers:
        try:
            results = provider.extract_text_with_boxes(image_path)
            if results:
                all_results.append(results)
                provider_names.append(provider.provider_name)
                logger.info("OCR provider %s: %d detections", provider.provider_name, len(results))
        except Exception as e:
            logger.warning("OCR provider %s failed: %s", provider.provider_name, e)

    if not all_results:
        return {"nodes": [], "edges": [], "error": "All OCR providers returned no results"}

    # Merge results pairwise
    merged = all_results[0]
    for i in range(1, len(all_results)):
        merged = _merge_ocr_results(merged, all_results[i], iou_threshold, confidence_boost)

    logger.info("Ensemble merged: %d detections from %d providers", len(merged), len(all_results))

    topology = _ocr_results_to_topology(merged, config)
    topology["method"] = "ocr_ensemble" if len(all_results) > 1 else f"ocr_{provider_names[0]}"
    return topology


def _single_extract(
    provider: OCRProvider,
    image_path: Path,
    config: dict,
) -> dict:
    """Run a single provider and build topology."""
    try:
        results = provider.extract_text_with_boxes(image_path)
    except Exception as e:
        return {"nodes": [], "edges": [], "error": f"OCR {provider.provider_name} failed: {e}"}

    if not results:
        return {"nodes": [], "edges": [], "error": f"OCR {provider.provider_name}: no text detected"}

    logger.info("OCR provider %s: %d detections", provider.provider_name, len(results))

    topology = _ocr_results_to_topology(results, config)
    topology["method"] = f"ocr_{provider.provider_name}"
    return topology


# ── Main entry point ───────────────────────────────────────────────────────


def _load_ocr_config() -> dict:
    """Load OCR fallback config from network_intelligence_config.yaml."""
    config_path = BASE_DIR / "args" / "network_intelligence_config.yaml"
    if not config_path.exists():
        return {}
    try:
        import yaml

        with open(config_path, encoding="utf-8") as f:
            full_config = yaml.safe_load(f) or {}
        return full_config.get("ingestion", {}).get("ocr_fallback", {})
    except Exception:
        return {}


def extract_topology_via_ocr(
    image_path: str,
    config: dict | None = None,
) -> dict:
    """Extract network topology from a diagram image using OCR.

    Falls back gracefully: ensemble (if 2+ providers) -> single -> empty.
    Returns same format as _extract_via_vision(): {"nodes": [...], "edges": [...]}.
    """
    config = config or _load_ocr_config()

    if not config.get("enabled", True):
        return {"nodes": [], "edges": [], "error": "OCR fallback disabled in config"}

    providers = get_ocr_provider_chain(config)
    if not providers:
        return {"nodes": [], "edges": [], "error": "No OCR providers available (pytesseract/rapidocr not installed)"}

    # Preprocess image
    img_path = Path(image_path)
    preprocessed = _preprocess_for_ocr(img_path, config)

    try:
        use_ensemble = config.get("ensemble", True) and len(providers) >= 2

        if use_ensemble:
            result = _ensemble_extract(providers, preprocessed, config)
        else:
            result = _single_extract(providers[0], preprocessed, config)

        return result
    finally:
        # Clean up temp preprocessed file
        if preprocessed != img_path and preprocessed.exists():
            try:
                preprocessed.unlink()
            except OSError:
                pass


def check_ocr_availability() -> dict[str, Any]:
    """Check which OCR providers are available. Returns status dict."""
    config = _load_ocr_config()
    providers = get_ocr_provider_chain(config)
    status: dict[str, Any] = {
        "available_providers": [p.provider_name for p in providers],
        "ensemble_possible": len(providers) >= 2,
        "config_enabled": config.get("enabled", True),
    }

    # Check each provider explicitly
    for name in ["tesseract", "rapidocr"]:
        try:
            if name == "tesseract":
                p = TesseractOCRProvider()
            else:
                p = RapidOCRProvider()
            status[f"{name}_available"] = p.check_availability()
        except Exception:
            status[f"{name}_available"] = False

    return status


# ── CLI ────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="ICDEV™ Network Diagram OCR Fallback")
    parser.add_argument("--image", help="Path to diagram image")
    parser.add_argument("--check", action="store_true", help="Check OCR provider availability")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--gate", action="store_true", help="Exit 1 if no providers available")
    args = parser.parse_args()

    if args.check:
        status = check_ocr_availability()
        if args.json:
            print(json.dumps(status, indent=2))
        else:
            for k, v in status.items():
                print(f"  {k}: {v}")
        if args.gate and not status["available_providers"]:
            sys.exit(1)
        return

    if args.image:
        result = extract_topology_via_ocr(args.image)
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            if result.get("error"):
                print(f"ERROR: {result['error']}")
                sys.exit(1)
            print(f"Method:  {result.get('method', 'unknown')}")
            print(f"Nodes:   {len(result.get('nodes', []))}")
            print(f"Edges:   {len(result.get('edges', []))}")
            print(f"Confidence: {result.get('confidence', 0)}")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
