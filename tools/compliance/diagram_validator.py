# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""Compliance diagram validator using vision LLMs.

Validates SSP architecture diagrams, network zone diagrams, data flow
diagrams, and ATO boundary diagrams against DoD/Gov compliance
requirements (NIST 800-53, RMF, FedRAMP) using multimodal LLM vision
analysis.

Diagram types supported:
- network_zone: DMZ, enclave boundaries, CDS markers, firewalls
- architecture: Component labels, cloud/on-prem boundaries, protocols
- data_flow: CUI markings on flows, encryption indicators, classifications
- ato_boundary: Authorization boundary lines, interconnection points

Usage:
    # Validate a network zone diagram
    python tools/compliance/diagram_validator.py \\
        --image network_diagram.png --type network_zone --json

    # Validate with expected components
    python tools/compliance/diagram_validator.py \\
        --image architecture.png --type architecture \\
        --expected-components "Web Server,API Gateway,Database" --json

    # Validate ATO boundary for a project
    python tools/compliance/diagram_validator.py \\
        --image boundary.png --type ato_boundary --project-id proj-123 --json

    # Validate data flow with classification level
    python tools/compliance/diagram_validator.py \\
        --image dataflow.png --type data_flow --classification SECRET --json

    # Validate network zones with expected zone list
    python tools/compliance/diagram_validator.py \\
        --image network.png --type network_zone \\
        --expected-zones "DMZ,Enclave A,Management Zone" --json
"""

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

logger = logging.getLogger("icdev.compliance.diagram_validator")

# ---------------------------------------------------------------------------
# Graceful imports for optional dependencies
# ---------------------------------------------------------------------------
try:
    from tools.testing.screenshot_validator import encode_image
except ImportError:
    encode_image = None  # type: ignore[assignment]
    logger.debug("screenshot_validator not available; encode_image unavailable")

try:
    from tools.llm import get_router
    from tools.llm.provider import LLMRequest
except ImportError:
    get_router = None  # type: ignore[assignment]
    LLMRequest = None  # type: ignore[assignment,misc]
    logger.debug("tools.llm not available; LLM invocation disabled")

try:
    from tools.audit.audit_logger import log_event as _audit_log_event
except ImportError:
    _audit_log_event = None  # type: ignore[assignment]
    logger.debug("audit_logger not available; audit logging disabled")


# ---------------------------------------------------------------------------
# Validation checks per diagram type
# ---------------------------------------------------------------------------
DIAGRAM_VALIDATIONS: Dict[str, List[str]] = {
    "network_zone": [
        "DMZ boundary is clearly marked and labeled",
        "Enclave boundaries are labeled with classification level",
        "Cross-domain solution (CDS) markers are present where required",
        "Firewall icons or indicators present at zone boundaries",
        "All network zones have clear labels",
    ],
    "architecture": [
        "All system components are labeled",
        "Cloud vs on-premises boundaries are clearly marked",
        "External interfaces are identified and labeled",
        "Data stores are identified with classification markings",
        "Communication protocols are labeled on connections",
    ],
    "data_flow": [
        "CUI markings are present on data flow arrows",
        "Encryption indicators shown at trust boundaries",
        "Data classification level labeled on each flow",
        "External data sources/sinks are identified",
        "Data at rest and in transit protections indicated",
    ],
    "ato_boundary": [
        "Authorization boundary is clearly drawn as a distinct line",
        "All system components are inside the boundary",
        "External interfaces crossing the boundary are labeled",
        "Interconnection points are documented",
        "System name and classification are labeled on the boundary",
    ],
}

VALID_DIAGRAM_TYPES = list(DIAGRAM_VALIDATIONS.keys())


# ---------------------------------------------------------------------------
# Vision system prompt
# ---------------------------------------------------------------------------
_SYSTEM_PROMPT_TEMPLATE = (
    "You are a DoD compliance validator analyzing a {diagram_type} diagram "
    "for ATO/RMF compliance. For EACH of the following checks, determine if "
    "it passes or fails based on what you see in the diagram. Respond with "
    "EXACTLY this JSON format (no markdown): "
    '{{"validations": [{{"check": string, "passed": bool, "confidence": '
    'float 0.0-1.0, "explanation": string}}], "overall_assessment": string, '
    '"recommendations": [string]}}'
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _parse_vision_response(content: str) -> dict:
    """Parse the vision model's JSON response.

    Handles both clean JSON and markdown-wrapped JSON responses.
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
        logger.warning("Failed to parse vision response as JSON, returning raw text")
        return {
            "validations": [],
            "overall_assessment": text[:500],
            "recommendations": [],
        }


def _build_no_vision_result(
    image_path: str,
    diagram_type: str,
    reason: str,
) -> Dict[str, Any]:
    """Build a graceful-degradation result when no vision model is available."""
    checks = DIAGRAM_VALIDATIONS.get(diagram_type, [])
    return {
        "diagram_path": str(image_path),
        "diagram_type": diagram_type,
        "validations": [
            {
                "check": check,
                "passed": None,
                "confidence": 0.0,
                "explanation": reason,
            }
            for check in checks
        ],
        "overall_passed": None,
        "overall_assessment": reason,
        "recommendations": [],
        "model_used": "",
        "duration_ms": 0,
    }


def _log_audit(
    project_id: Optional[str],
    diagram_type: str,
    image_path: str,
    overall_passed: Optional[bool],
) -> None:
    """Log a compliance_check audit event if the audit logger is available."""
    if _audit_log_event is None:
        return
    try:
        _audit_log_event(
            event_type="compliance_check",
            actor="compliance-agent",
            action=f"Validated {diagram_type} diagram: {Path(image_path).name}",
            project_id=project_id,
            details={
                "diagram_type": diagram_type,
                "diagram_path": str(image_path),
                "overall_passed": overall_passed,
            },
        )
    except Exception as exc:
        logger.debug("Audit logging failed (non-fatal): %s", exc)


# ---------------------------------------------------------------------------
# Core validation
# ---------------------------------------------------------------------------
def validate_diagram(
    image_path: str,
    diagram_type: str,
    project_id: Optional[str] = None,
    expected_components: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Validate a compliance diagram against its type-specific checks.

    Encodes the diagram image, builds a multimodal LLM request with the
    appropriate checks for the diagram type, invokes the vision model,
    and returns structured pass/fail results.

    Args:
        image_path: Path to the diagram image file (PNG, JPEG, etc.).
        diagram_type: One of ``network_zone``, ``architecture``,
            ``data_flow``, or ``ato_boundary``.
        project_id: Optional ICDEV project identifier for audit logging.
        expected_components: Optional list of component names that should
            appear in the diagram.

    Returns:
        Dict with keys: diagram_path, diagram_type, validations,
        overall_passed, overall_assessment, recommendations, model_used,
        duration_ms.
    """
    start_time = time.time()

    # --- Validate diagram_type ---
    if diagram_type not in DIAGRAM_VALIDATIONS:
        raise ValueError(
            f"Unknown diagram_type '{diagram_type}'. "
            f"Valid types: {VALID_DIAGRAM_TYPES}"
        )

    # --- Check required dependencies ---
    if encode_image is None:
        return _build_no_vision_result(
            image_path, diagram_type,
            "Vision model not available: encode_image dependency missing",
        )

    if get_router is None or LLMRequest is None:
        return _build_no_vision_result(
            image_path, diagram_type,
            "Vision model not available: tools.llm dependency missing",
        )

    # --- Check vision model availability ---
    try:
        router = get_router()
        provider, model_id, model_cfg = router.get_provider_for_function(
            "compliance_diagram"
        )
        if provider is None:
            return _build_no_vision_result(
                image_path, diagram_type,
                "Vision model not available: no provider for compliance_diagram function",
            )
        supports_vision = model_cfg.get("supports_vision", False)
        if not supports_vision:
            return _build_no_vision_result(
                image_path, diagram_type,
                f"Vision model not available: model {model_id} does not support vision",
            )
    except Exception as exc:
        return _build_no_vision_result(
            image_path, diagram_type,
            f"Vision model not available: {exc}",
        )

    # --- Encode image ---
    try:
        b64_data, media_type = encode_image(image_path)
    except (FileNotFoundError, ValueError) as exc:
        return _build_no_vision_result(
            image_path, diagram_type, f"Image encoding failed: {exc}",
        )

    # --- Build checks list ---
    checks = list(DIAGRAM_VALIDATIONS[diagram_type])
    if expected_components:
        components_str = ", ".join(expected_components)
        checks.append(
            f"The following components should be present: {components_str}"
        )

    checks_text = "\n".join(f"- {c}" for c in checks)

    # --- Build multimodal request ---
    system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(diagram_type=diagram_type)

    user_content: List[Dict[str, Any]] = [
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
                f"Validate this {diagram_type} diagram against these "
                f"compliance checks:\n{checks_text}"
            ),
        },
    ]

    request = LLMRequest(
        messages=[{"role": "user", "content": user_content}],
        system_prompt=system_prompt,
        max_tokens=2048,
        temperature=0.1,
    )

    # --- Invoke vision model ---
    try:
        response = router.invoke("compliance_diagram", request)
    except Exception as exc:
        duration_ms = int((time.time() - start_time) * 1000)
        logger.error("Vision model invocation failed: %s", exc)
        result = _build_no_vision_result(
            image_path, diagram_type,
            f"Vision model invocation failed: {exc}",
        )
        result["duration_ms"] = duration_ms
        return result

    duration_ms = int((time.time() - start_time) * 1000)

    # --- Parse response ---
    parsed = _parse_vision_response(response.content)

    validations = parsed.get("validations", [])
    overall_assessment = parsed.get("overall_assessment", "")
    recommendations = parsed.get("recommendations", [])

    # Compute overall_passed: True only if ALL validations passed
    if validations:
        overall_passed = all(
            v.get("passed") is True for v in validations
        )
    else:
        overall_passed = None

    actual_model = response.model_id or model_id or ""

    result = {
        "diagram_path": str(image_path),
        "diagram_type": diagram_type,
        "validations": validations,
        "overall_passed": overall_passed,
        "overall_assessment": overall_assessment,
        "recommendations": recommendations,
        "model_used": actual_model,
        "duration_ms": duration_ms,
    }

    # --- Audit logging ---
    _log_audit(project_id, diagram_type, image_path, overall_passed)

    logger.info(
        "Diagram validation complete: type=%s passed=%s model=%s duration=%dms",
        diagram_type, overall_passed, actual_model, duration_ms,
    )

    return result


# ---------------------------------------------------------------------------
# Convenience wrappers
# ---------------------------------------------------------------------------
def check_network_zones(
    image_path: str,
    expected_zones: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Validate a network zone diagram.

    Convenience wrapper around :func:`validate_diagram` for network zone
    diagrams, with optional expected zone names.

    Args:
        image_path: Path to the network zone diagram image.
        expected_zones: Optional list of zone names that should be present
            (e.g. ``["DMZ", "Enclave A", "Management Zone"]``).

    Returns:
        Validation result dict.
    """
    expected_components = None
    if expected_zones:
        expected_components = [f"Network zone: {z}" for z in expected_zones]
    return validate_diagram(
        image_path=image_path,
        diagram_type="network_zone",
        expected_components=expected_components,
    )


def check_boundary_diagram(
    image_path: str,
    project_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Validate an ATO boundary diagram.

    Convenience wrapper around :func:`validate_diagram` for authorization
    boundary diagrams.

    Args:
        image_path: Path to the ATO boundary diagram image.
        project_id: Optional ICDEV project identifier.

    Returns:
        Validation result dict.
    """
    return validate_diagram(
        image_path=image_path,
        diagram_type="ato_boundary",
        project_id=project_id,
    )


def check_data_flow(
    image_path: str,
    classification_level: str = "CUI",
) -> Dict[str, Any]:
    """Validate a data flow diagram.

    Convenience wrapper around :func:`validate_diagram` for data flow
    diagrams, adding classification context.

    Args:
        image_path: Path to the data flow diagram image.
        classification_level: Expected classification level on flows
            (default ``"CUI"``).

    Returns:
        Validation result dict.
    """
    expected_components = [
        f"Classification level marking: {classification_level}",
    ]
    return validate_diagram(
        image_path=image_path,
        diagram_type="data_flow",
        expected_components=expected_components,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    """Command-line interface for compliance diagram validation."""
    parser = argparse.ArgumentParser(
        description="ICDEV Compliance Diagram Validator (Vision LLM)",
    )
    parser.add_argument(
        "--image", required=True,
        help="Path to the diagram image to validate",
    )
    parser.add_argument(
        "--type", required=True, dest="diagram_type",
        choices=VALID_DIAGRAM_TYPES,
        help="Type of compliance diagram",
    )
    parser.add_argument(
        "--project-id",
        help="ICDEV project identifier (for audit logging)",
    )
    parser.add_argument(
        "--expected-components",
        help="Comma-separated list of expected components in the diagram",
    )
    parser.add_argument(
        "--expected-zones",
        help="Comma-separated list of expected network zones (network_zone type only)",
    )
    parser.add_argument(
        "--classification", default="CUI",
        help="Classification level for data flow diagrams (default: CUI)",
    )
    parser.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output results as JSON",
    )

    args = parser.parse_args()

    # --- Route to the appropriate function ---
    if args.diagram_type == "network_zone" and args.expected_zones:
        zones = [z.strip() for z in args.expected_zones.split(",") if z.strip()]
        result = check_network_zones(args.image, expected_zones=zones)
    elif args.diagram_type == "data_flow":
        expected = None
        if args.expected_components:
            expected = [c.strip() for c in args.expected_components.split(",") if c.strip()]
        # Merge classification context and any explicit expected components
        classification_component = f"Classification level marking: {args.classification}"
        if expected:
            expected.append(classification_component)
        else:
            expected = [classification_component]
        result = validate_diagram(
            image_path=args.image,
            diagram_type="data_flow",
            project_id=args.project_id,
            expected_components=expected,
        )
    elif args.diagram_type == "ato_boundary":
        expected = None
        if args.expected_components:
            expected = [c.strip() for c in args.expected_components.split(",") if c.strip()]
        result = validate_diagram(
            image_path=args.image,
            diagram_type="ato_boundary",
            project_id=args.project_id,
            expected_components=expected,
        )
    else:
        expected = None
        if args.expected_components:
            expected = [c.strip() for c in args.expected_components.split(",") if c.strip()]
        result = validate_diagram(
            image_path=args.image,
            diagram_type=args.diagram_type,
            project_id=args.project_id,
            expected_components=expected,
        )

    # --- Output ---
    if args.json_output:
        print(json.dumps(result, indent=2, default=str))
    else:
        overall = result.get("overall_passed")
        if overall is True:
            status_label = "PASSED"
        elif overall is False:
            status_label = "FAILED"
        else:
            status_label = "SKIPPED"

        print(f"Diagram Validation: {status_label}")
        print(f"  Type: {result['diagram_type']}")
        print(f"  Image: {result['diagram_path']}")
        if result.get("model_used"):
            print(f"  Model: {result['model_used']}")
        print(f"  Duration: {result['duration_ms']}ms")
        print()

        for v in result.get("validations", []):
            passed = v.get("passed")
            if passed is True:
                mark = "PASS"
            elif passed is False:
                mark = "FAIL"
            else:
                mark = "SKIP"
            confidence = v.get("confidence", 0.0)
            print(f"  [{mark}] {v.get('check', 'unknown')} (confidence: {confidence:.2f})")
            if v.get("explanation"):
                print(f"         {v['explanation']}")

        if result.get("overall_assessment"):
            print(f"\n  Assessment: {result['overall_assessment']}")

        if result.get("recommendations"):
            print("\n  Recommendations:")
            for rec in result["recommendations"]:
                print(f"    - {rec}")

    # Exit code reflects validation outcome
    if result.get("overall_passed") is False:
        sys.exit(1)


if __name__ == "__main__":
    main()
# CUI // SP-CTI
