# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""Vision-based SysML diagram extractor using multimodal LLMs.

Extracts model elements and relationships from diagram screenshots when
XMI export from Cameo Systems Modeler / MagicDraw is unavailable.  Uses
vision-capable LLMs (Ollama LLaVA for air-gapped, Claude/GPT-4o for cloud)
to analyze SysML diagram images and produce structured element/relationship
data compatible with the ICDEV MBSE digital thread.

Usage:
    # Extract elements from a diagram screenshot
    python tools/mbse/diagram_extractor.py \\
        --image /path/to/bdd_screenshot.png --json

    # Extract with explicit diagram type
    python tools/mbse/diagram_extractor.py \\
        --image /path/to/activity_diagram.png --diagram-type activity --json

    # Extract and store in DB
    python tools/mbse/diagram_extractor.py \\
        --image /path/to/diagram.png --project-id proj-123 --store --json

    # Extract and validate against existing model
    python tools/mbse/diagram_extractor.py \\
        --image /path/to/diagram.png --project-id proj-123 --validate --json
"""

import argparse
import json
import logging
import sqlite3
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

logger = logging.getLogger("icdev.mbse.diagram_extractor")

# ---------------------------------------------------------------------------
# Graceful imports — optional dependencies
# ---------------------------------------------------------------------------
try:
    from tools.testing.screenshot_validator import encode_image  # type: ignore
    _HAS_ENCODE = True
except ImportError:
    _HAS_ENCODE = False

    def encode_image(image_path: str) -> tuple:  # noqa: D103 – stub
        """Fallback: inline base64 encoding when screenshot_validator unavailable."""
        import base64
        _MEDIA_TYPES = {
            ".png": "image/png", ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp",
        }
        p = Path(image_path)
        if not p.exists():
            raise FileNotFoundError(f"Image not found: {image_path}")
        ext = p.suffix.lower()
        media_type = _MEDIA_TYPES.get(ext)
        if not media_type:
            raise ValueError(f"Unsupported image format: {ext}")
        with open(p, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")
        return b64, media_type

try:
    from tools.audit.audit_logger import log_event  # type: ignore
    _HAS_AUDIT = True
except ImportError:
    _HAS_AUDIT = False

    def log_event(**kwargs) -> int:  # noqa: D103 – stub
        return -1

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DIAGRAM_TYPES = [
    "block_definition", "internal_block", "activity", "sequence",
    "state_machine", "use_case", "requirement", "parametric", "auto",
]

# Map diagram types to DB diagram_type codes used in sysml_elements
_DIAGRAM_TYPE_TO_DB = {
    "block_definition": "bdd",
    "internal_block": "ibd",
    "activity": "act",
    "sequence": "act",       # closest match in schema
    "state_machine": "stm",
    "use_case": "uc",
    "requirement": "req",
    "parametric": "bdd",     # closest match in schema
    "auto": None,
}

# Valid element_type values from sysml_elements CHECK constraint
_VALID_ELEMENT_TYPES = {
    "block", "interface_block", "value_type", "constraint_block",
    "activity", "action", "object_node", "control_flow", "object_flow",
    "requirement", "use_case", "actor", "state_machine", "state",
    "package", "profile", "stereotype", "port", "connector",
}

# Valid relationship_type values from sysml_relationships CHECK constraint
_VALID_RELATIONSHIP_TYPES = {
    "association", "composition", "aggregation", "generalization",
    "dependency", "realization", "usage", "allocate",
    "satisfy", "derive", "verify", "refine", "trace", "copy",
}

# System prompt for SysML diagram extraction
EXTRACTION_SYSTEM_PROMPT = (
    "You are analyzing a SysML diagram screenshot from a DoD systems "
    "engineering tool (Cameo Systems Modeler / MagicDraw). Extract all "
    "model elements and their relationships. Respond with EXACTLY this "
    "JSON format (no markdown, no extra text): "
    '{"diagram_type": string, "elements": [{"name": string, "type": string, '
    '"stereotype": string or null, "properties": {}}], "relationships": '
    '[{"source": string, "target": string, "type": string, "stereotype": '
    'string or null, "name": string or null}], "confidence": float 0.0-1.0, '
    '"notes": string}'
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _ts() -> str:
    """Current ISO-8601 timestamp."""
    return datetime.now().isoformat()


def _new_elem_id() -> str:
    """Generate a prefixed UUID for a diagram-extracted element."""
    return f"elem-{uuid.uuid4()}"


def _new_rel_id() -> str:
    """Generate a prefixed UUID for a diagram-extracted relationship."""
    return f"rel-{uuid.uuid4()}"


def _new_vision_import_id() -> str:
    """Generate a vision-specific import identifier."""
    return f"vision-{uuid.uuid4()}"


def _parse_llm_response(content: str) -> dict:
    """Parse the LLM's JSON response, stripping markdown fences if present."""
    text = content.strip()

    # Strip markdown code fences
    if text.startswith("```"):
        lines = text.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("Failed to parse LLM response as JSON: %s", exc)
        return {}


def _normalize_element_type(raw_type: str) -> str:
    """Map a free-form element type string to a valid DB element_type value."""
    lower = raw_type.lower().replace(" ", "_").replace("-", "_")
    # Direct match
    if lower in _VALID_ELEMENT_TYPES:
        return lower
    # Common synonyms
    synonyms = {
        "bdd_block": "block",
        "sysml_block": "block",
        "interfaceblock": "interface_block",
        "valuetype": "value_type",
        "constraintblock": "constraint_block",
        "statemachine": "state_machine",
        "usecase": "use_case",
        "objectnode": "object_node",
        "controlflow": "control_flow",
        "objectflow": "object_flow",
        "class": "block",
        "component": "block",
        "signal": "block",
        "interface": "interface_block",
    }
    if lower in synonyms:
        return synonyms[lower]
    # Substring match
    for valid in _VALID_ELEMENT_TYPES:
        if valid in lower or lower in valid:
            return valid
    # Default fallback
    return "block"


def _normalize_relationship_type(raw_type: str) -> str:
    """Map a free-form relationship type string to a valid DB type."""
    lower = raw_type.lower().replace(" ", "_").replace("-", "_")
    if lower in _VALID_RELATIONSHIP_TYPES:
        return lower
    synonyms = {
        "inheritance": "generalization",
        "extends": "generalization",
        "implements": "realization",
        "uses": "usage",
        "depends_on": "dependency",
        "include": "dependency",
        "extend": "dependency",
        "satisfies": "satisfy",
        "derives": "derive",
        "verifies": "verify",
        "refines": "refine",
        "traces": "trace",
        "allocates": "allocate",
        "containment": "composition",
        "directed_association": "association",
    }
    if lower in synonyms:
        return synonyms[lower]
    for valid in _VALID_RELATIONSHIP_TYPES:
        if valid in lower or lower in valid:
            return valid
    return "association"


def _get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Open a connection to the ICDEV database."""
    path = db_path or DB_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"Database not found: {path}\n"
            "Run: python tools/db/init_icdev_db.py"
        )
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ---------------------------------------------------------------------------
# Vision model availability check
# ---------------------------------------------------------------------------
def _check_vision_available() -> dict:
    """Check if a vision-capable LLM model is available for diagram extraction."""
    try:
        from tools.llm import get_router
        router = get_router()
        provider, model_id, model_cfg = router.get_provider_for_function("diagram_extraction")

        if provider is None:
            return {
                "available": False,
                "model": "",
                "provider": "",
                "error": "No provider available for diagram_extraction function",
            }

        supports_vision = model_cfg.get("supports_vision", False)
        return {
            "available": supports_vision,
            "model": model_id,
            "provider": provider.provider_name,
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
# Core extraction
# ---------------------------------------------------------------------------
def extract_diagram(
    image_path: str,
    diagram_type: str = "auto",
) -> Dict[str, Any]:
    """Extract SysML elements and relationships from a diagram screenshot.

    Encodes the image, sends it to a vision-capable LLM with a structured
    extraction prompt, and parses the returned JSON into elements and
    relationships.

    Args:
        image_path: Path to the diagram image file (PNG, JPEG, etc.).
        diagram_type: One of DIAGRAM_TYPES.  ``"auto"`` lets the LLM detect
            the diagram type.

    Returns:
        Dict with keys: elements, relationships, diagram_type, confidence,
        model_used, duration_ms, notes, error (if any).
    """
    start_time = time.time()

    # Validate diagram_type
    if diagram_type not in DIAGRAM_TYPES:
        return {
            "elements": [],
            "relationships": [],
            "diagram_type": diagram_type,
            "confidence": 0.0,
            "model_used": "",
            "duration_ms": 0,
            "notes": "",
            "error": (
                f"Invalid diagram_type '{diagram_type}'. "
                f"Valid: {', '.join(DIAGRAM_TYPES)}"
            ),
        }

    # Check vision availability
    avail = _check_vision_available()
    if not avail["available"]:
        return {
            "elements": [],
            "relationships": [],
            "diagram_type": diagram_type,
            "confidence": 0.0,
            "model_used": "",
            "duration_ms": int((time.time() - start_time) * 1000),
            "notes": "",
            "error": f"No vision model available: {avail.get('error', 'unknown')}",
        }

    # Encode image
    try:
        b64_data, media_type = encode_image(image_path)
    except (FileNotFoundError, ValueError) as e:
        return {
            "elements": [],
            "relationships": [],
            "diagram_type": diagram_type,
            "confidence": 0.0,
            "model_used": "",
            "duration_ms": int((time.time() - start_time) * 1000),
            "notes": "",
            "error": str(e),
        }

    # Build user prompt
    user_text = "Extract all model elements and relationships from this SysML diagram."
    if diagram_type != "auto":
        user_text += f" This is a {diagram_type.replace('_', ' ')} diagram."

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
            "text": user_text,
        },
    ]

    # Invoke LLM
    try:
        from tools.llm import get_router
        from tools.llm.provider import LLMRequest

        router = get_router()
        request = LLMRequest(
            messages=[{"role": "user", "content": user_content}],
            system_prompt=EXTRACTION_SYSTEM_PROMPT,
            max_tokens=4096,
            temperature=0.1,
        )

        response = router.invoke("diagram_extraction", request)
        duration_ms = int((time.time() - start_time) * 1000)

        # Parse structured response
        data = _parse_llm_response(response.content)

        if not data:
            return {
                "elements": [],
                "relationships": [],
                "diagram_type": diagram_type,
                "confidence": 0.0,
                "model_used": response.model_id or avail.get("model", ""),
                "duration_ms": duration_ms,
                "notes": "LLM response could not be parsed as JSON.",
                "error": "Failed to parse LLM response",
            }

        return {
            "elements": data.get("elements", []),
            "relationships": data.get("relationships", []),
            "diagram_type": data.get("diagram_type", diagram_type),
            "confidence": float(data.get("confidence", 0.0)),
            "model_used": response.model_id or avail.get("model", ""),
            "duration_ms": duration_ms,
            "notes": data.get("notes", ""),
            "error": None,
        }

    except Exception as e:
        duration_ms = int((time.time() - start_time) * 1000)
        logger.error("Diagram extraction failed: %s", e)
        return {
            "elements": [],
            "relationships": [],
            "diagram_type": diagram_type,
            "confidence": 0.0,
            "model_used": avail.get("model", ""),
            "duration_ms": duration_ms,
            "notes": "",
            "error": f"LLM invocation failed: {e}",
        }


# ---------------------------------------------------------------------------
# Store extracted elements in DB
# ---------------------------------------------------------------------------
def store_extracted_elements(
    project_id: str,
    extraction_result: Dict[str, Any],
    source_image: str,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Store vision-extracted elements and relationships in the ICDEV database.

    For each extracted element, inserts a row into ``sysml_elements``.
    For each extracted relationship, inserts a row into ``sysml_relationships``.

    Args:
        project_id: ICDEV project identifier.
        extraction_result: Output from :func:`extract_diagram`.
        source_image: Path to the source diagram image.
        db_path: Override database path (default: data/icdev.db).

    Returns:
        Dict with elements_stored, relationships_stored, import_id, errors.
    """
    timestamp = _ts()
    vision_import_id = _new_vision_import_id()
    errors: List[str] = []
    elements_stored = 0
    rels_stored = 0

    conn = _get_connection(db_path)
    cursor = conn.cursor()

    # Build element name -> generated id mapping for relationship resolution
    elem_id_map: Dict[str, str] = {}

    diagram_type = extraction_result.get("diagram_type", "auto")
    db_diagram_type = _DIAGRAM_TYPE_TO_DB.get(diagram_type)

    # Insert elements
    for elem in extraction_result.get("elements", []):
        elem_name = elem.get("name", "")
        if not elem_name:
            continue

        elem_id = _new_elem_id()
        elem_id_map[elem_name] = elem_id
        raw_type = elem.get("type", "block")
        element_type = _normalize_element_type(raw_type)
        stereotype = elem.get("stereotype") or ""
        properties = json.dumps(elem.get("properties", {}))

        try:
            cursor.execute(
                """INSERT OR REPLACE INTO sysml_elements
                   (id, project_id, xmi_id, element_type, name, qualified_name,
                    parent_id, stereotype, description, properties,
                    diagram_type, source_file, source_hash, imported_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    elem_id,
                    project_id,
                    vision_import_id,
                    element_type,
                    elem_name,
                    elem_name,      # qualified_name = name for vision extractions
                    None,           # parent_id
                    stereotype,
                    "",             # description
                    properties,
                    db_diagram_type,
                    Path(source_image).name,
                    "",             # source_hash (N/A for vision)
                    timestamp,
                    timestamp,
                ),
            )
            elements_stored += 1
        except sqlite3.Error as exc:
            errors.append(f"Element '{elem_name}': {exc}")

    # Insert relationships
    for rel in extraction_result.get("relationships", []):
        source_name = rel.get("source", "")
        target_name = rel.get("target", "")
        source_elem_id = elem_id_map.get(source_name)
        target_elem_id = elem_id_map.get(target_name)

        if not source_elem_id or not target_elem_id:
            errors.append(
                f"Relationship '{source_name}' -> '{target_name}': "
                "source or target element not found in extraction"
            )
            continue

        raw_rel_type = rel.get("type", "association")
        rel_type = _normalize_relationship_type(raw_rel_type)
        rel_name = rel.get("name") or ""

        try:
            cursor.execute(
                """INSERT OR REPLACE INTO sysml_relationships
                   (project_id, source_element_id, target_element_id,
                    relationship_type, name, properties, source_file)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    project_id,
                    source_elem_id,
                    target_elem_id,
                    rel_type,
                    rel_name,
                    json.dumps({"stereotype": rel.get("stereotype")}),
                    Path(source_image).name,
                ),
            )
            rels_stored += 1
        except sqlite3.Error as exc:
            errors.append(
                f"Relationship '{source_name}' -> '{target_name}': {exc}"
            )

    # Record in model_imports
    status = "completed" if not errors else "partial"
    try:
        cursor.execute(
            """INSERT INTO model_imports
               (project_id, import_type, source_file, source_hash,
                elements_imported, relationships_imported, errors,
                error_details, status, imported_by, imported_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                project_id,
                "json",  # closest import_type for vision extraction
                Path(source_image).name,
                "",
                elements_stored,
                rels_stored,
                len(errors),
                json.dumps(errors) if errors else None,
                status,
                "icdev-diagram-extractor",
                timestamp,
            ),
        )
        import_id = cursor.lastrowid
    except sqlite3.Error as exc:
        import_id = -1
        errors.append(f"model_imports insert failed: {exc}")

    conn.commit()
    conn.close()

    # Audit trail
    if _HAS_AUDIT:
        try:
            log_event(
                event_type="compliance_check",
                actor="icdev-diagram-extractor",
                action=(
                    f"Vision-extracted {elements_stored} elements and "
                    f"{rels_stored} relationships from '{Path(source_image).name}' "
                    f"for project {project_id}"
                ),
                project_id=project_id,
                details={
                    "import_id": import_id,
                    "vision_import_id": vision_import_id,
                    "elements_stored": elements_stored,
                    "relationships_stored": rels_stored,
                    "errors": len(errors),
                    "model_used": extraction_result.get("model_used", ""),
                },
                affected_files=[source_image],
                classification="CUI",
            )
        except Exception:
            pass  # Audit failure should not block storage

    return {
        "elements_stored": elements_stored,
        "relationships_stored": rels_stored,
        "import_id": import_id,
        "vision_import_id": vision_import_id,
        "errors": errors,
        "status": status,
    }


# ---------------------------------------------------------------------------
# Validate against existing model
# ---------------------------------------------------------------------------
def validate_against_model(
    project_id: str,
    extraction_result: Dict[str, Any],
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Compare vision-extracted elements against existing model elements in DB.

    Queries ``sysml_elements`` for the given project and compares element
    names and types with those in the extraction result.

    Args:
        project_id: ICDEV project identifier.
        extraction_result: Output from :func:`extract_diagram`.
        db_path: Override database path.

    Returns:
        Dict with matched, missing_in_model, missing_in_diagram, confidence.
    """
    conn = _get_connection(db_path)
    rows = conn.execute(
        "SELECT name, element_type FROM sysml_elements WHERE project_id = ?",
        (project_id,),
    ).fetchall()
    conn.close()

    existing_names = {row["name"] for row in rows}
    extracted_names = {
        e.get("name", "")
        for e in extraction_result.get("elements", [])
        if e.get("name")
    }

    matched = existing_names & extracted_names
    missing_in_model = sorted(extracted_names - existing_names)
    missing_in_diagram = sorted(existing_names - extracted_names)

    total = len(existing_names | extracted_names)
    confidence = len(matched) / total if total > 0 else 0.0

    return {
        "matched": len(matched),
        "matched_names": sorted(matched),
        "missing_in_model": missing_in_model,
        "missing_in_diagram": missing_in_diagram,
        "existing_count": len(existing_names),
        "extracted_count": len(extracted_names),
        "confidence": round(confidence, 3),
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
def main() -> None:
    """Command-line interface for SysML diagram extraction."""
    parser = argparse.ArgumentParser(
        description="ICDEV Vision-Based SysML Diagram Extractor"
    )
    parser.add_argument(
        "--image", required=True,
        help="Path to diagram image to analyze (PNG, JPEG, etc.)",
    )
    parser.add_argument(
        "--diagram-type", default="auto", choices=DIAGRAM_TYPES,
        help="Diagram type (default: auto-detect)",
    )
    parser.add_argument(
        "--project-id",
        help="ICDEV project identifier (required for --store and --validate)",
    )
    parser.add_argument(
        "--store", action="store_true",
        help="Store extracted elements in the ICDEV database (requires --project-id)",
    )
    parser.add_argument(
        "--validate", action="store_true",
        help="Compare against existing model in DB (requires --project-id)",
    )
    parser.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output results as JSON",
    )
    parser.add_argument(
        "--db-path", type=Path, default=None,
        help="Override database path (default: data/icdev.db)",
    )

    args = parser.parse_args()

    # Validate argument combinations
    if args.store and not args.project_id:
        parser.error("--store requires --project-id")
    if args.validate and not args.project_id:
        parser.error("--validate requires --project-id")

    image_path = str(Path(args.image).resolve())

    # Step 1: Extract
    result = extract_diagram(image_path, diagram_type=args.diagram_type)

    output: Dict[str, Any] = {"extraction": result}

    # Step 2: Store (optional)
    if args.store and result.get("error") is None:
        store_result = store_extracted_elements(
            project_id=args.project_id,
            extraction_result=result,
            source_image=image_path,
            db_path=args.db_path,
        )
        output["storage"] = store_result

    # Step 3: Validate (optional)
    if args.validate and result.get("error") is None:
        validate_result = validate_against_model(
            project_id=args.project_id,
            extraction_result=result,
            db_path=args.db_path,
        )
        output["validation"] = validate_result

    # Output
    if args.json_output:
        print("CUI // SP-CTI")
        print(json.dumps(output, indent=2, default=str))
        print("CUI // SP-CTI")
    else:
        print("CUI // SP-CTI")
        ext = result
        print("SysML Diagram Extraction")
        print(f"  Image:         {Path(image_path).name}")
        print(f"  Diagram Type:  {ext.get('diagram_type', 'unknown')}")
        print(f"  Elements:      {len(ext.get('elements', []))}")
        print(f"  Relationships: {len(ext.get('relationships', []))}")
        print(f"  Confidence:    {ext.get('confidence', 0.0):.2f}")
        print(f"  Model:         {ext.get('model_used', 'N/A')}")
        print(f"  Duration:      {ext.get('duration_ms', 0)}ms")
        if ext.get("error"):
            print(f"  Error:         {ext['error']}")
        if ext.get("notes"):
            print(f"  Notes:         {ext['notes']}")

        if "storage" in output:
            s = output["storage"]
            print("\n  Storage:")
            print(f"    Elements Stored:      {s['elements_stored']}")
            print(f"    Relationships Stored: {s['relationships_stored']}")
            print(f"    Import ID:            {s['import_id']}")
            print(f"    Status:               {s['status']}")
            if s.get("errors"):
                print(f"    Errors ({len(s['errors'])}):")
                for err in s["errors"]:
                    print(f"      - {err}")

        if "validation" in output:
            v = output["validation"]
            print("\n  Validation Against Model:")
            print(f"    Matched:              {v['matched']}")
            print(f"    Missing in Model:     {len(v['missing_in_model'])}")
            if v["missing_in_model"]:
                for name in v["missing_in_model"]:
                    print(f"      + {name}")
            print(f"    Missing in Diagram:   {len(v['missing_in_diagram'])}")
            if v["missing_in_diagram"]:
                for name in v["missing_in_diagram"]:
                    print(f"      - {name}")
            print(f"    Confidence:           {v['confidence']:.3f}")

        print("CUI // SP-CTI")

    # Exit code: 0 if extraction succeeded, 1 if error
    has_error = result.get("error") is not None
    sys.exit(1 if has_error else 0)


if __name__ == "__main__":
    main()
# CUI // SP-CTI
