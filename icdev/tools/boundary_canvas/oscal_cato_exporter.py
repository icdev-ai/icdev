# CUI // SP-CTI — BDC cATO OSCAL Exporter
"""OSCAL cATO exporter for the Boundary Design Canvas digital twin.

Thin delegation layer over ``tools/compliance/oscal_generator.py``. This module
contains NO OSCAL generation logic of its own — it maps a boundary-canvas
artifact-type request to the corresponding generator call, validates the produced
file, and returns a compact ``{path, valid, artifact_type, ...}`` payload for the
twin UI / API.

Public API (mirrors the generator so callers/tests can depend on stable names):
  * ``generate_oscal_ssp(project_id, output_dir=None, db_path=None)``
  * ``generate_oscal_poam(project_id, output_dir=None, db_path=None)``
  * ``generate_oscal_assessment_results(project_id, output_dir=None, db_path=None)``
  * ``generate_oscal_component_definition(project_id, output_dir=None, db_path=None)``
  * ``validate_oscal(file_path, artifact_type=None)``
  * ``export_oscal_artifact(project_id, artifact_type='ssp', output_dir=None, db_path=None)``

The generators read project/control/finding state from the ICDEV main database
(``data/icdev.db`` by default; overridable via ``db_path``). A ``project_id`` that
does not map to a real ``projects`` row raises inside the generator; this module
catches that and returns an honest error payload (``{"status": "error", ...}``)
rather than a fabricated artifact.
"""
from __future__ import annotations

import argparse
import json
import sys

from tools.compliance import oscal_generator as _oscal
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.boundary_canvas.oscal")

# Canonical artifact types the generator understands, plus twin-UI short aliases.
_ARTIFACT_ALIASES = {
    "ssp": "ssp",
    "poam": "poam",
    "poa&m": "poam",
    "ar": "assessment_results",
    "assessment_results": "assessment_results",
    "assessment-results": "assessment_results",
    "cd": "component_definition",
    "component_definition": "component_definition",
    "component-definition": "component_definition",
}

_GENERATORS = {
    "ssp": _oscal.generate_oscal_ssp,
    "poam": _oscal.generate_oscal_poam,
    "assessment_results": _oscal.generate_oscal_assessment_results,
    "component_definition": _oscal.generate_oscal_component_definition,
}


# --------------------------------------------------------------------------- #
# Thin delegations (stable public names, identical signatures to the generator)
# --------------------------------------------------------------------------- #
def generate_oscal_ssp(project_id, output_dir=None, db_path=None):
    """Delegate to oscal_generator.generate_oscal_ssp."""
    return _oscal.generate_oscal_ssp(project_id, output_dir=output_dir, db_path=db_path)


def generate_oscal_poam(project_id, output_dir=None, db_path=None):
    """Delegate to oscal_generator.generate_oscal_poam."""
    return _oscal.generate_oscal_poam(project_id, output_dir=output_dir, db_path=db_path)


def generate_oscal_assessment_results(project_id, output_dir=None, db_path=None):
    """Delegate to oscal_generator.generate_oscal_assessment_results."""
    return _oscal.generate_oscal_assessment_results(project_id, output_dir=output_dir, db_path=db_path)


def generate_oscal_component_definition(project_id, output_dir=None, db_path=None):
    """Delegate to oscal_generator.generate_oscal_component_definition."""
    return _oscal.generate_oscal_component_definition(project_id, output_dir=output_dir, db_path=db_path)


def validate_oscal(file_path, artifact_type=None):
    """Delegate to oscal_generator.validate_oscal -> {valid, errors}."""
    return _oscal.validate_oscal(file_path, artifact_type)


def normalize_artifact_type(artifact_type):
    """Map a UI/CLI artifact-type token to a canonical generator key, or None."""
    if not artifact_type:
        return None
    return _ARTIFACT_ALIASES.get(str(artifact_type).strip().lower())


# --------------------------------------------------------------------------- #
# Mapping entry point used by the twin facade / dashboard route
# --------------------------------------------------------------------------- #
def export_oscal_artifact(project_id, artifact_type="ssp", output_dir=None, db_path=None):
    """Generate one OSCAL artifact for a project and validate it.

    Maps ``artifact_type`` (ssp | poam | assessment_results | component_definition,
    plus ``ar`` / ``cd`` aliases) to the matching generator, runs ``validate_oscal``
    on the produced file, and returns a compact payload:

        {"status": "ok", "artifact_type": ..., "path": ..., "valid": bool,
         "errors": [...], "uuid": ...}

    On any failure (unknown type, project not found, DB missing, generation error)
    returns ``{"status": "error", "artifact_type": ..., "path": None,
    "valid": False, "error": <message>}`` — never raises to the caller.
    """
    canonical = normalize_artifact_type(artifact_type)
    if canonical is None:
        msg = f"Unknown OSCAL artifact type: {artifact_type!r}"
        logger.warning(msg)
        return {
            "status": "error",
            "artifact_type": artifact_type,
            "path": None,
            "valid": False,
            "error": msg,
        }

    generator = _GENERATORS[canonical]
    try:
        result = generator(project_id, output_dir=output_dir, db_path=db_path)
    except Exception as exc:  # noqa: BLE001 — surface an honest error payload, do not 500
        logger.warning(
            "OSCAL %s generation failed for project %s: %s", canonical, project_id, exc
        )
        return {
            "status": "error",
            "artifact_type": canonical,
            "path": None,
            "valid": False,
            "error": str(exc),
        }

    path = result.get("file_path")
    # Re-validate the produced file (thin delegation; the generator also self-validates).
    validation = validate_oscal(path, canonical) if path else {"valid": False, "errors": ["No file produced"]}

    return {
        "status": "ok",
        "artifact_type": canonical,
        "path": path,
        "valid": bool(validation.get("valid")),
        "errors": validation.get("errors", []),
        "uuid": result.get("uuid"),
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Generate an OSCAL artifact for a boundary-canvas project (delegates to oscal_generator)."
    )
    parser.add_argument("--project-id", required=True, help="Project identifier (must exist in projects table).")
    parser.add_argument(
        "--artifact-type",
        default="ssp",
        help="ssp | poam | assessment_results (ar) | component_definition (cd)",
    )
    parser.add_argument("--output-dir", default=None, help="Override output directory (default: generator's).")
    parser.add_argument("--json", action="store_true", help="Emit the result payload as JSON.")
    args = parser.parse_args(argv)

    payload = export_oscal_artifact(
        args.project_id, artifact_type=args.artifact_type, output_dir=args.output_dir
    )
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"artifact_type={payload.get('artifact_type')} status={payload.get('status')} "
              f"valid={payload.get('valid')} path={payload.get('path')}")
    return 0 if payload.get("status") == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
