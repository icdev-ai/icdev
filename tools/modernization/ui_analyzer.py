# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""Legacy UI Screenshot Analysis Engine for ICDEV DoD Modernization.

Analyzes legacy application UI screenshots using vision-capable LLMs to assess
navigation complexity, form density, layout approach, accessibility indicators,
and technology era. Computes a deterministic UI complexity score that feeds into
the 7R migration strategy assessment (seven_r_assessor.py).

Supports graceful degradation: if no vision model is available, returns results
with complexity_score=null so downstream tools can still operate.

Usage:
    # Analyze a single screenshot
    python tools/modernization/ui_analyzer.py \\
        --image /path/to/screenshot.png --json

    # Analyze all screenshots in a directory
    python tools/modernization/ui_analyzer.py \\
        --image-dir /path/to/screenshots/ --json

    # Analyze and store results for a legacy app
    python tools/modernization/ui_analyzer.py \\
        --image /path/to/screenshot.png --app-id lapp-001 \\
        --project-id proj-123 --store --json

    # Only print the deterministic complexity score
    python tools/modernization/ui_analyzer.py \\
        --image /path/to/screenshot.png --score-only

Classification: CUI // SP-CTI
Environment:    AWS GovCloud (us-gov-west-1)
Compliance:     NIST 800-53 Rev 5 / RMF
"""

import argparse
import json
import logging
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

DB_PATH = BASE_DIR / "data" / "icdev.db"

logger = logging.getLogger("icdev.modernization.ui_analyzer")

# Supported image file extensions
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff"}

# ---------------------------------------------------------------------------
# Graceful imports
# ---------------------------------------------------------------------------
try:
    from tools.testing.screenshot_validator import encode_image
except ImportError:
    encode_image = None  # type: ignore[assignment]
    logger.warning("screenshot_validator not available; image encoding disabled")

try:
    from tools.audit.audit_logger import log_event as _audit_log_event
except ImportError:
    _audit_log_event = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# System prompt for vision-based UI analysis
# ---------------------------------------------------------------------------
UI_ANALYSIS_SYSTEM_PROMPT = (
    "You are analyzing a legacy application UI screenshot for a DoD "
    "modernization assessment. Evaluate navigation complexity, form density, "
    "layout approach (table-based vs grid vs responsive), accessibility "
    "indicators, and technology era indicators. Respond with EXACTLY this "
    "JSON format (no markdown, no extra text): "
    '{"navigation": {"depth": int, "menu_items": int, "breadcrumbs": bool}, '
    '"forms": {"count": int, "field_count": int, "custom_widgets": int}, '
    '"layout": {"responsive": bool, "grid_based": bool, "table_heavy": bool}, '
    '"accessibility": {"contrast_issues": bool, "text_readability": '
    '"high|medium|low"}, '
    '"technology_indicators": {"framework_hints": [string], "era": string}, '
    '"complexity_score": float 0.0-1.0, "modernization_notes": string}'
)


# ---------------------------------------------------------------------------
# Database helper
# ---------------------------------------------------------------------------
def _get_db(db_path: Optional[Path] = None):
    """Return a sqlite3 connection to the ICDEV operational database.

    Uses row_factory = sqlite3.Row for dict-like access.
    """
    path = db_path or DB_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"ICDEV database not found at {path}. "
            "Run 'python tools/db/init_icdev_db.py' first."
        )
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ---------------------------------------------------------------------------
# Vision response parser
# ---------------------------------------------------------------------------
def _parse_vision_response(content: str) -> dict:
    """Parse the vision model's JSON response.

    Handles both clean JSON and markdown-wrapped JSON responses.
    Falls back to heuristic parsing on failure.
    """
    text = content.strip()

    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        # Fallback: return a minimal structure indicating parse failure
        logger.warning("Failed to parse vision response as JSON; returning raw text")
        return {
            "navigation": {"depth": 0, "menu_items": 0, "breadcrumbs": False},
            "forms": {"count": 0, "field_count": 0, "custom_widgets": 0},
            "layout": {"responsive": False, "grid_based": False, "table_heavy": False},
            "accessibility": {"contrast_issues": False, "text_readability": "medium"},
            "technology_indicators": {"framework_hints": [], "era": "unknown"},
            "complexity_score": None,
            "modernization_notes": f"Vision model returned unparseable response: {text[:200]}",
            "_parse_error": True,
        }


# ---------------------------------------------------------------------------
# Vision availability check
# ---------------------------------------------------------------------------
def _check_vision_available() -> dict:
    """Check if a vision-capable LLM is available for ui_analysis.

    Returns:
        Dict with keys: available (bool), model (str), provider (str), error (str|None).
    """
    try:
        from tools.llm import get_router
        router = get_router()
        provider, model_id, model_cfg = router.get_provider_for_function("ui_analysis")

        if provider is None:
            return {
                "available": False,
                "model": "",
                "provider": "",
                "error": "No provider available for ui_analysis function",
            }

        supports_vision = model_cfg.get("supports_vision", False)
        return {
            "available": supports_vision,
            "model": model_id,
            "provider": provider.provider_name,
            "supports_vision": supports_vision,
            "error": None if supports_vision else (
                f"Model {model_id} does not have supports_vision: true"
            ),
        }
    except Exception as e:
        return {
            "available": False,
            "model": "",
            "provider": "",
            "error": str(e),
        }


# ---------------------------------------------------------------------------
# Core analysis
# ---------------------------------------------------------------------------
def analyze_screenshot(
    image_path: str,
    analysis_type: str = "full",
) -> Dict[str, Any]:
    """Analyze a legacy application UI screenshot using a vision LLM.

    Encodes the image, checks vision model availability, sends a multimodal
    request via the LLM router, and parses the structured JSON response.

    Args:
        image_path: Absolute or relative path to the screenshot file.
        analysis_type: Analysis mode — 'full' (default) or 'quick'.

    Returns:
        Dict containing analysis results, model_used, duration_ms, and
        any error information.  If no vision model is available, returns
        a result with complexity_score=None (graceful degradation).
    """
    start_time = time.time()
    result_base: Dict[str, Any] = {
        "image_path": str(image_path),
        "analysis_type": analysis_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "model_used": "",
        "duration_ms": 0,
        "error": None,
    }

    # ------------------------------------------------------------------
    # Validate encode_image availability
    # ------------------------------------------------------------------
    if encode_image is None:
        result_base["error"] = (
            "screenshot_validator.encode_image not available; "
            "cannot encode image"
        )
        result_base["complexity_score"] = None
        result_base["duration_ms"] = int((time.time() - start_time) * 1000)
        return result_base

    # ------------------------------------------------------------------
    # Check vision model availability
    # ------------------------------------------------------------------
    avail = _check_vision_available()
    if not avail["available"]:
        result_base["error"] = (
            f"Vision model not available: {avail.get('error', 'unknown')}"
        )
        result_base["complexity_score"] = None
        result_base["duration_ms"] = int((time.time() - start_time) * 1000)
        return result_base

    # ------------------------------------------------------------------
    # Encode image
    # ------------------------------------------------------------------
    try:
        b64_data, media_type = encode_image(image_path)
    except (FileNotFoundError, ValueError) as exc:
        result_base["error"] = str(exc)
        result_base["complexity_score"] = None
        result_base["duration_ms"] = int((time.time() - start_time) * 1000)
        return result_base

    # ------------------------------------------------------------------
    # Build multimodal request
    # ------------------------------------------------------------------
    user_content = [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": b64_data,
            },
        },
        {
            "type": "text",
            "text": (
                "Analyze this legacy application UI screenshot for a "
                "DoD modernization assessment. Provide a structured JSON "
                "evaluation of navigation, forms, layout, accessibility, "
                "and technology indicators."
            ),
        },
    ]

    try:
        from tools.llm import get_router
        from tools.llm.provider import LLMRequest

        router = get_router()
        request = LLMRequest(
            messages=[{"role": "user", "content": user_content}],
            system_prompt=UI_ANALYSIS_SYSTEM_PROMPT,
            max_tokens=1024,
            temperature=0.1,
        )

        response = router.invoke("ui_analysis", request)
        duration_ms = int((time.time() - start_time) * 1000)

        # Parse structured response
        analysis_data = _parse_vision_response(response.content)

        result_base.update(analysis_data)
        result_base["model_used"] = response.model_id or avail.get("model", "")
        result_base["duration_ms"] = duration_ms

        return result_base

    except Exception as exc:
        duration_ms = int((time.time() - start_time) * 1000)
        logger.error("UI analysis vision invocation failed: %s", exc)
        result_base["error"] = f"Vision model invocation failed: {exc}"
        result_base["complexity_score"] = None
        result_base["model_used"] = avail.get("model", "")
        result_base["duration_ms"] = duration_ms
        return result_base


# ---------------------------------------------------------------------------
# Batch analysis
# ---------------------------------------------------------------------------
def analyze_ui_batch(
    image_paths: List[str],
    app_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Analyze multiple legacy UI screenshots and aggregate results.

    Iterates over each image path, calls ``analyze_screenshot`` for each,
    then computes an aggregated summary across all screenshots.

    Args:
        image_paths: List of file paths to screenshot images.
        app_id: Optional legacy app identifier (for context only).

    Returns:
        Dict with keys:
          - individual: list of per-image analysis dicts
          - summary: aggregated summary with average complexity, merged
                     technology hints, and combined modernization notes
    """
    individual_results: List[Dict[str, Any]] = []
    for img_path in image_paths:
        result = analyze_screenshot(img_path)
        individual_results.append(result)

    # ------------------------------------------------------------------
    # Aggregate summary
    # ------------------------------------------------------------------
    scores = [
        r.get("complexity_score")
        for r in individual_results
        if r.get("complexity_score") is not None
    ]
    avg_score = sum(scores) / len(scores) if scores else None

    # Merge technology indicators
    all_hints: List[str] = []
    all_eras: List[str] = []
    all_notes: List[str] = []
    for r in individual_results:
        tech = r.get("technology_indicators", {})
        if isinstance(tech, dict):
            all_hints.extend(tech.get("framework_hints", []))
            era = tech.get("era", "")
            if era:
                all_eras.append(era)
        note = r.get("modernization_notes", "")
        if note:
            all_notes.append(note)

    # Deduplicate framework hints
    unique_hints = list(dict.fromkeys(all_hints))

    summary: Dict[str, Any] = {
        "app_id": app_id,
        "total_screenshots": len(image_paths),
        "analyzed_count": len(individual_results),
        "average_complexity_score": avg_score,
        "technology_indicators": {
            "framework_hints": unique_hints,
            "eras": list(dict.fromkeys(all_eras)),
        },
        "combined_modernization_notes": "; ".join(all_notes) if all_notes else "",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    return {
        "individual": individual_results,
        "summary": summary,
    }


# ---------------------------------------------------------------------------
# Deterministic UI complexity scoring
# ---------------------------------------------------------------------------
def compute_ui_complexity_score(analysis_results: Dict[str, Any]) -> float:
    """Compute a deterministic UI complexity score from analysis results.

    Weighting:
        - navigation: 0.25
        - forms:      0.25
        - layout:     0.20
        - accessibility: 0.15
        - technology: 0.15

    Scoring rules:
        navigation: depth * 0.1 + menu_items * 0.03, capped at 1.0
        forms:      field_count * 0.03 + custom_widgets * 0.1, capped at 1.0
        layout:     0.8 if table_heavy, 0.3 if responsive, 0.5 otherwise
        accessibility: 0.7 if contrast_issues, scaled by text_readability
        technology: 0.9 if era before 2010, 0.5 for 2010-2015, 0.2 after 2015

    Args:
        analysis_results: Dict from analyze_screenshot or a parsed vision
            response containing navigation, forms, layout, accessibility,
            and technology_indicators sub-dicts.

    Returns:
        Float 0.0-1.0 representing UI modernization complexity.
    """
    # --- Navigation ---
    nav = analysis_results.get("navigation", {})
    if not isinstance(nav, dict):
        nav = {}
    nav_depth = nav.get("depth", 0) or 0
    nav_items = nav.get("menu_items", 0) or 0
    nav_score = min(nav_depth * 0.1 + nav_items * 0.03, 1.0)

    # --- Forms ---
    forms = analysis_results.get("forms", {})
    if not isinstance(forms, dict):
        forms = {}
    field_count = forms.get("field_count", 0) or 0
    custom_widgets = forms.get("custom_widgets", 0) or 0
    form_score = min(field_count * 0.03 + custom_widgets * 0.1, 1.0)

    # --- Layout ---
    layout = analysis_results.get("layout", {})
    if not isinstance(layout, dict):
        layout = {}
    if layout.get("table_heavy", False):
        layout_score = 0.8
    elif layout.get("responsive", False):
        layout_score = 0.3
    else:
        layout_score = 0.5

    # --- Accessibility ---
    access = analysis_results.get("accessibility", {})
    if not isinstance(access, dict):
        access = {}
    contrast_issues = access.get("contrast_issues", False)
    readability = str(access.get("text_readability", "medium")).lower()
    readability_scale = {"high": 0.8, "medium": 1.0, "low": 1.2}
    scale_factor = readability_scale.get(readability, 1.0)
    access_base = 0.7 if contrast_issues else 0.3
    access_score = min(access_base * scale_factor, 1.0)

    # --- Technology ---
    tech = analysis_results.get("technology_indicators", {})
    if not isinstance(tech, dict):
        tech = {}
    era_str = str(tech.get("era", "")).strip().lower()
    tech_score = _era_to_score(era_str)

    # --- Weighted average ---
    score = (
        0.25 * nav_score
        + 0.25 * form_score
        + 0.20 * layout_score
        + 0.15 * access_score
        + 0.15 * tech_score
    )
    return round(min(max(score, 0.0), 1.0), 4)


def _era_to_score(era_str: str) -> float:
    """Map a technology era string to a complexity score.

    Returns 0.9 for pre-2010, 0.5 for 2010-2015, 0.2 for post-2015.
    Falls back to 0.5 if the era cannot be parsed.
    """
    if not era_str:
        return 0.5

    # Try to extract a 4-digit year from the era string
    import re
    match = re.search(r"(\d{4})", era_str)
    if match:
        year = int(match.group(1))
        if year < 2010:
            return 0.9
        elif year <= 2015:
            return 0.5
        else:
            return 0.2

    # Keyword-based fallback
    early_keywords = {
        "90s", "2000s", "early", "legacy", "classic", "ie6",
        "table-based", "frames", "activex", "flash", "silverlight",
    }
    modern_keywords = {
        "modern", "react", "angular", "vue", "responsive", "spa",
        "material", "bootstrap4", "bootstrap5", "tailwind",
    }
    if any(kw in era_str for kw in early_keywords):
        return 0.9
    if any(kw in era_str for kw in modern_keywords):
        return 0.2
    return 0.5


# ---------------------------------------------------------------------------
# Database storage
# ---------------------------------------------------------------------------
def store_ui_analysis(
    app_id: str,
    project_id: str,
    results: Dict[str, Any],
    db_path: Optional[Path] = None,
) -> bool:
    """Store UI analysis results in the legacy_apps table metadata column.

    Merges the new analysis results under the key ``ui_analysis`` in the
    existing metadata JSON blob for the given app.

    Args:
        app_id: Legacy application identifier.
        project_id: ICDEV project identifier.
        results: Analysis results dict to store.
        db_path: Optional override for the database path.

    Returns:
        True if the update succeeded, False otherwise.
    """
    try:
        conn = _get_db(db_path)
    except FileNotFoundError as exc:
        logger.error("Cannot store UI analysis: %s", exc)
        return False

    try:
        cursor = conn.cursor()
        # Fetch current metadata
        cursor.execute(
            "SELECT metadata FROM legacy_apps WHERE app_id = ? AND project_id = ?",
            (app_id, project_id),
        )
        row = cursor.fetchone()
        if row is None:
            logger.error(
                "Legacy app not found: app_id=%s, project_id=%s", app_id, project_id
            )
            conn.close()
            return False

        existing_meta = {}
        if row["metadata"]:
            try:
                existing_meta = json.loads(row["metadata"])
            except (json.JSONDecodeError, TypeError):
                existing_meta = {}

        # Merge UI analysis under a dedicated key
        existing_meta["ui_analysis"] = {
            "results": results,
            "analyzed_at": datetime.now(timezone.utc).isoformat(),
        }

        cursor.execute(
            "UPDATE legacy_apps SET metadata = ? WHERE app_id = ? AND project_id = ?",
            (json.dumps(existing_meta, default=str), app_id, project_id),
        )
        conn.commit()
        conn.close()

        # Audit log
        if _audit_log_event is not None:
            try:
                _audit_log_event(
                    event_type="compliance_check",
                    actor="ui-analyzer",
                    action=f"Stored UI analysis for app {app_id}",
                    project_id=project_id,
                    details={"app_id": app_id, "score": results.get("complexity_score")},
                )
            except Exception:
                pass  # audit failure should not break primary flow

        return True

    except Exception as exc:
        logger.error("Failed to store UI analysis: %s", exc)
        conn.close()
        return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    """CLI entry point for legacy UI screenshot analysis."""
    parser = argparse.ArgumentParser(
        description="ICDEV Legacy UI Screenshot Analyzer"
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--image", help="Path to a single screenshot image")
    group.add_argument("--image-dir", help="Directory containing screenshot images")

    parser.add_argument("--app-id", help="Legacy application ID (for DB storage / 7R integration)")
    parser.add_argument("--project-id", help="ICDEV project ID")
    parser.add_argument("--store", action="store_true", help="Store results in icdev.db")
    parser.add_argument("--score-only", action="store_true", help="Print only the complexity score")
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    args = parser.parse_args()

    # ------------------------------------------------------------------
    # Single image
    # ------------------------------------------------------------------
    if args.image:
        result = analyze_screenshot(args.image)

        # Compute deterministic score if vision produced raw analysis
        det_score = None
        if result.get("navigation") is not None and not result.get("error"):
            det_score = compute_ui_complexity_score(result)
            result["deterministic_complexity_score"] = det_score

        if args.score_only:
            score = det_score if det_score is not None else result.get("complexity_score")
            print(json.dumps({"complexity_score": score}) if args.json else str(score))
            return

        if args.store and args.app_id and args.project_id:
            stored = store_ui_analysis(args.app_id, args.project_id, result)
            result["stored"] = stored

        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            _print_human_readable(result)

    # ------------------------------------------------------------------
    # Directory of images
    # ------------------------------------------------------------------
    elif args.image_dir:
        dir_path = Path(args.image_dir)
        if not dir_path.is_dir():
            print(f"Error: not a directory: {args.image_dir}", file=sys.stderr)
            sys.exit(1)

        image_paths: List[str] = []
        for ext in IMAGE_EXTENSIONS:
            image_paths.extend(str(p) for p in sorted(dir_path.rglob(f"*{ext}")))

        if not image_paths:
            msg = f"No images found in {args.image_dir}"
            if args.json:
                print(json.dumps({"error": msg}))
            else:
                print(msg, file=sys.stderr)
            sys.exit(1)

        batch_result = analyze_ui_batch(image_paths, app_id=args.app_id)

        # Compute deterministic scores for each individual result
        for individual in batch_result.get("individual", []):
            if individual.get("navigation") is not None and not individual.get("error"):
                individual["deterministic_complexity_score"] = (
                    compute_ui_complexity_score(individual)
                )

        # Compute aggregate deterministic score
        det_scores = [
            r["deterministic_complexity_score"]
            for r in batch_result.get("individual", [])
            if r.get("deterministic_complexity_score") is not None
        ]
        if det_scores:
            batch_result["summary"]["average_deterministic_score"] = round(
                sum(det_scores) / len(det_scores), 4
            )

        if args.score_only:
            score = batch_result["summary"].get(
                "average_deterministic_score",
                batch_result["summary"].get("average_complexity_score"),
            )
            print(json.dumps({"complexity_score": score}) if args.json else str(score))
            return

        if args.store and args.app_id and args.project_id:
            stored = store_ui_analysis(
                args.app_id, args.project_id, batch_result
            )
            batch_result["stored"] = stored

        if args.json:
            print(json.dumps(batch_result, indent=2, default=str))
        else:
            _print_batch_human_readable(batch_result)


def _print_human_readable(result: Dict[str, Any]) -> None:
    """Print a single analysis result in human-readable format."""
    print(f"Image: {result.get('image_path', 'unknown')}")
    print(f"Model: {result.get('model_used', 'none')}")
    print(f"Duration: {result.get('duration_ms', 0)}ms")

    if result.get("error"):
        print(f"Error: {result['error']}")

    score = result.get("deterministic_complexity_score", result.get("complexity_score"))
    print(f"Complexity Score: {score}")

    nav = result.get("navigation", {})
    if isinstance(nav, dict) and nav:
        print(f"Navigation: depth={nav.get('depth', 0)}, "
              f"items={nav.get('menu_items', 0)}, "
              f"breadcrumbs={nav.get('breadcrumbs', False)}")

    forms = result.get("forms", {})
    if isinstance(forms, dict) and forms:
        print(f"Forms: count={forms.get('count', 0)}, "
              f"fields={forms.get('field_count', 0)}, "
              f"custom_widgets={forms.get('custom_widgets', 0)}")

    layout = result.get("layout", {})
    if isinstance(layout, dict) and layout:
        print(f"Layout: responsive={layout.get('responsive', False)}, "
              f"grid={layout.get('grid_based', False)}, "
              f"table_heavy={layout.get('table_heavy', False)}")

    tech = result.get("technology_indicators", {})
    if isinstance(tech, dict) and tech:
        print(f"Technology: era={tech.get('era', 'unknown')}, "
              f"hints={tech.get('framework_hints', [])}")

    notes = result.get("modernization_notes", "")
    if notes:
        print(f"Notes: {notes}")
    print()


def _print_batch_human_readable(batch: Dict[str, Any]) -> None:
    """Print batch analysis results in human-readable format."""
    summary = batch.get("summary", {})
    print("=== UI Analysis Batch Summary ===")
    print(f"Screenshots analyzed: {summary.get('analyzed_count', 0)}")
    score = summary.get("average_deterministic_score", summary.get("average_complexity_score"))
    print(f"Average Complexity Score: {score}")
    tech = summary.get("technology_indicators", {})
    if isinstance(tech, dict):
        print(f"Framework hints: {tech.get('framework_hints', [])}")
        print(f"Eras: {tech.get('eras', [])}")
    notes = summary.get("combined_modernization_notes", "")
    if notes:
        print(f"Notes: {notes}")
    print()

    for i, result in enumerate(batch.get("individual", []), 1):
        print(f"--- Screenshot {i} ---")
        _print_human_readable(result)


if __name__ == "__main__":
    main()
# CUI // SP-CTI
