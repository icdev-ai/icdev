# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""Vision-based screenshot validator using LLM vision models.

Validates E2E test screenshots against assertions using multimodal LLMs
(Ollama LLaVA for air-gapped, Claude/GPT-4o for cloud deployments).

Usage:
    # Validate a single screenshot
    python tools/testing/screenshot_validator.py \\
        --image screenshot.png --assert "CUI banner is visible at top" --json

    # Multiple assertions on one screenshot
    python tools/testing/screenshot_validator.py \\
        --image screenshot.png \\
        --assert "CUI banner is visible" \\
        --assert "No error dialogs present" --json

    # Batch validate all screenshots in a directory
    python tools/testing/screenshot_validator.py \\
        --batch-dir .tmp/test_runs/abc123/screenshots/ \\
        --default-assertions "CUI banner visible,No error dialogs" --json

    # Check if vision model is available
    python tools/testing/screenshot_validator.py --check --json
"""

import argparse
import base64
import json
import logging
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

logger = logging.getLogger("icdev.testing.screenshot_validator")

# Supported image formats and their media types
IMAGE_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}

# Default assertions applied when none specified
DEFAULT_ASSERTIONS = [
    "CUI banner or classification marking is visible at the top of the page",
    "No error dialogs, crash messages, or stack traces are visible",
    "Page content has loaded successfully (not a blank or loading screen)",
]

# System prompt for vision validation
VISION_SYSTEM_PROMPT = """You are a visual QA validator for a Gov/DoD application testing pipeline.
You will be shown a screenshot from an E2E test and asked to verify a specific assertion.

Respond with EXACTLY this JSON format (no markdown, no extra text):
{
    "passed": true or false,
    "confidence": 0.0 to 1.0,
    "explanation": "Brief explanation of what you see and why the assertion passes or fails"
}

Rules:
- Be precise and factual about what you observe in the screenshot
- If the assertion is about something not visible in the screenshot, set passed to false
- Confidence should reflect how certain you are (0.9+ for clear cases, 0.5-0.8 for ambiguous)
- Keep explanations under 100 words"""


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------
@dataclass
class VisionValidationResult:
    """Result of a single screenshot assertion validation."""
    image_path: str
    assertion: str
    passed: Optional[bool] = None  # None = skipped (no model available)
    confidence: float = 0.0
    explanation: str = ""
    model_used: str = ""
    duration_ms: int = 0
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Image encoding
# ---------------------------------------------------------------------------
def encode_image(image_path: str) -> tuple:
    """Load an image file and encode to base64.

    Args:
        image_path: Path to the image file.

    Returns:
        Tuple of (base64_string, media_type).

    Raises:
        FileNotFoundError: If the image file doesn't exist.
        ValueError: If the image format is not supported.
    """
    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(f"Image not found: {image_path}")

    ext = path.suffix.lower()
    media_type = IMAGE_MEDIA_TYPES.get(ext)
    if not media_type:
        raise ValueError(
            f"Unsupported image format: {ext}. "
            f"Supported: {', '.join(IMAGE_MEDIA_TYPES.keys())}"
        )

    with open(path, "rb") as f:
        b64_data = base64.b64encode(f.read()).decode("utf-8")

    return b64_data, media_type


# ---------------------------------------------------------------------------
# Vision model availability
# ---------------------------------------------------------------------------
def check_vision_available() -> dict:
    """Check if any vision-capable LLM model is available.

    Returns:
        Dict with keys: available (bool), model (str), provider (str), error (str|None)
    """
    try:
        from tools.llm import get_router
        router = get_router()
        provider, model_id, model_cfg = router.get_provider_for_function("screenshot_validation")

        if provider is None:
            return {
                "available": False,
                "model": "",
                "provider": "",
                "error": "No provider available for screenshot_validation function",
            }

        supports_vision = model_cfg.get("supports_vision", False)
        return {
            "available": supports_vision,
            "model": model_id,
            "provider": provider.provider_name,
            "supports_vision": supports_vision,
            "error": None if supports_vision else f"Model {model_id} does not have supports_vision: true",
        }

    except Exception as e:
        return {
            "available": False,
            "model": "",
            "provider": "",
            "error": str(e),
        }


# ---------------------------------------------------------------------------
# Core validation
# ---------------------------------------------------------------------------
def validate_screenshot(
    image_path: str,
    assertion: str,
    system_prompt: str = VISION_SYSTEM_PROMPT,
) -> VisionValidationResult:
    """Validate a screenshot against an assertion using a vision LLM.

    Encodes the image, builds a multimodal request, sends to the
    vision model via the LLM router, and parses the pass/fail result.

    Args:
        image_path: Path to the screenshot file.
        assertion: Text assertion to verify (e.g., "CUI banner is visible").
        system_prompt: System prompt for the vision model.

    Returns:
        VisionValidationResult with pass/fail, confidence, explanation.
    """
    start_time = time.time()

    # Check vision availability
    avail = check_vision_available()
    if not avail["available"]:
        return VisionValidationResult(
            image_path=image_path,
            assertion=assertion,
            passed=None,
            explanation=f"Vision model not available: {avail.get('error', 'unknown')}",
            error=avail.get("error"),
        )

    # Encode image
    try:
        b64_data, media_type = encode_image(image_path)
    except (FileNotFoundError, ValueError) as e:
        return VisionValidationResult(
            image_path=image_path,
            assertion=assertion,
            passed=None,
            explanation=str(e),
            error=str(e),
        )

    # Build multimodal message with image + assertion
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
            "text": f"Verify this assertion about the screenshot: {assertion}",
        },
    ]

    try:
        from tools.llm import get_router
        from tools.llm.provider import LLMRequest

        router = get_router()
        request = LLMRequest(
            messages=[{"role": "user", "content": user_content}],
            system_prompt=system_prompt,
            max_tokens=512,
            temperature=0.1,
        )

        response = router.invoke("screenshot_validation", request)
        duration_ms = int((time.time() - start_time) * 1000)

        # Parse structured response
        result_data = _parse_vision_response(response.content)

        return VisionValidationResult(
            image_path=image_path,
            assertion=assertion,
            passed=result_data.get("passed"),
            confidence=result_data.get("confidence", 0.0),
            explanation=result_data.get("explanation", response.content[:200]),
            model_used=response.model_id or avail.get("model", ""),
            duration_ms=duration_ms,
        )

    except Exception as e:
        duration_ms = int((time.time() - start_time) * 1000)
        logger.error("Vision validation failed: %s", e)
        return VisionValidationResult(
            image_path=image_path,
            assertion=assertion,
            passed=None,
            explanation=f"Vision model invocation failed: {e}",
            model_used=avail.get("model", ""),
            duration_ms=duration_ms,
            error=str(e),
        )


def _parse_vision_response(content: str) -> dict:
    """Parse the vision model's JSON response.

    Handles both clean JSON and markdown-wrapped JSON responses.
    """
    text = content.strip()

    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.splitlines()
        # Remove first and last fence lines
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        data = json.loads(text)
        return {
            "passed": bool(data.get("passed", False)),
            "confidence": float(data.get("confidence", 0.0)),
            "explanation": str(data.get("explanation", "")),
        }
    except (json.JSONDecodeError, ValueError):
        # Fallback: heuristic parsing from free-text response
        lower = text.lower()
        if any(w in lower for w in ("pass", "yes", "confirmed", "visible", "present")):
            passed = True
            confidence = 0.6
        elif any(w in lower for w in ("fail", "no", "not visible", "absent", "missing")):
            passed = False
            confidence = 0.6
        else:
            passed = None
            confidence = 0.3

        return {
            "passed": passed,
            "confidence": confidence,
            "explanation": text[:200],
        }


# ---------------------------------------------------------------------------
# Batch validation
# ---------------------------------------------------------------------------
def validate_batch(
    e2e_results: list,
    assertions: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Validate screenshots from E2E test results against assertions.

    For each E2ETestResult that has screenshots, runs each assertion
    against each screenshot and returns aggregated results.

    Args:
        e2e_results: List of E2ETestResult objects (or dicts with 'screenshots' key).
        assertions: Assertions to check. Defaults to DEFAULT_ASSERTIONS.

    Returns:
        List of dicts, each containing test_name, screenshot validations.
    """
    if assertions is None:
        assertions = DEFAULT_ASSERTIONS

    avail = check_vision_available()
    if not avail["available"]:
        logger.warning("Vision model not available — skipping batch validation: %s", avail.get("error"))
        return []

    all_results = []

    for e2e_result in e2e_results:
        # Support both objects and dicts
        if hasattr(e2e_result, "screenshots"):
            screenshots = e2e_result.screenshots or []
            test_name = getattr(e2e_result, "test_name", "unknown")
        elif isinstance(e2e_result, dict):
            screenshots = e2e_result.get("screenshots", [])
            test_name = e2e_result.get("test_name", "unknown")
        else:
            continue

        if not screenshots:
            continue

        test_validations = {
            "test_name": test_name,
            "validations": [],
        }

        for screenshot_path in screenshots:
            if not Path(screenshot_path).exists():
                logger.warning("Screenshot not found: %s", screenshot_path)
                continue

            for assertion in assertions:
                result = validate_screenshot(screenshot_path, assertion)
                test_validations["validations"].append(result.to_dict())

        all_results.append(test_validations)

    return all_results


def validate_directory(
    directory: str,
    assertions: Optional[List[str]] = None,
) -> List[VisionValidationResult]:
    """Validate all images in a directory against assertions.

    Args:
        directory: Path to directory containing screenshot images.
        assertions: Assertions to check. Defaults to DEFAULT_ASSERTIONS.

    Returns:
        List of VisionValidationResult objects.
    """
    if assertions is None:
        assertions = DEFAULT_ASSERTIONS

    dir_path = Path(directory)
    if not dir_path.is_dir():
        logger.error("Not a directory: %s", directory)
        return []

    results = []
    for ext in IMAGE_MEDIA_TYPES:
        for img_path in sorted(dir_path.rglob(f"*{ext}")):
            for assertion in assertions:
                result = validate_screenshot(str(img_path), assertion)
                results.append(result)

    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="ICDEV Vision-Based Screenshot Validator"
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--image", help="Path to screenshot image to validate")
    group.add_argument("--batch-dir", help="Directory of screenshots to validate")
    group.add_argument("--check", action="store_true", help="Check if vision model is available")

    parser.add_argument(
        "--assert", dest="assertions", action="append",
        help="Assertion to verify (can be specified multiple times)",
    )
    parser.add_argument(
        "--default-assertions",
        help="Comma-separated default assertions for batch mode",
    )

    args = parser.parse_args()

    if args.check:
        result = check_vision_available()
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            status = "AVAILABLE" if result["available"] else "NOT AVAILABLE"
            print(f"Vision model: {status}")
            if result.get("model"):
                print(f"  Model: {result['model']}")
                print(f"  Provider: {result['provider']}")
            if result.get("error"):
                print(f"  Error: {result['error']}")
        return

    # Resolve assertions
    assertions = args.assertions or []
    if args.default_assertions:
        assertions.extend(a.strip() for a in args.default_assertions.split(",") if a.strip())
    if not assertions:
        assertions = DEFAULT_ASSERTIONS

    if args.image:
        # Single image validation
        results = []
        for assertion in assertions:
            result = validate_screenshot(args.image, assertion)
            results.append(result)

        if args.json:
            print(json.dumps([r.to_dict() for r in results], indent=2, default=str))
        else:
            for r in results:
                status = "PASS" if r.passed is True else ("FAIL" if r.passed is False else "SKIP")
                print(f"[{status}] {r.assertion}")
                print(f"  Confidence: {r.confidence:.2f}")
                print(f"  Explanation: {r.explanation}")
                if r.model_used:
                    print(f"  Model: {r.model_used}")
                if r.error:
                    print(f"  Error: {r.error}")
                print()

        # Exit with failure if any assertion failed
        any_failed = any(r.passed is False for r in results)
        sys.exit(1 if any_failed else 0)

    elif args.batch_dir:
        # Batch directory validation
        results = validate_directory(args.batch_dir, assertions)

        if args.json:
            print(json.dumps([r.to_dict() for r in results], indent=2, default=str))
        else:
            passed = sum(1 for r in results if r.passed is True)
            failed = sum(1 for r in results if r.passed is False)
            skipped = sum(1 for r in results if r.passed is None)
            print(f"Vision Validation: {passed} passed, {failed} failed, {skipped} skipped")
            for r in results:
                status = "PASS" if r.passed is True else ("FAIL" if r.passed is False else "SKIP")
                img_name = Path(r.image_path).name
                print(f"  [{status}] {img_name}: {r.assertion}")
                if r.passed is False:
                    print(f"         {r.explanation}")

        any_failed = any(r.passed is False for r in results)
        sys.exit(1 if any_failed else 0)


if __name__ == "__main__":
    main()
# CUI // SP-CTI
