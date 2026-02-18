#!/usr/bin/env python3
# CUI // SP-CTI
"""Attestation Manager — Image signing and SBOM attestation for DevSecOps.

Generates cosign/notation signing configs and SLSA Level 3 attestation
pipeline jobs. Supports verification of existing image attestations.

ADR D119: Attestation is profile-driven — only active when image_signing
or sbom_attestation stages are enabled in DevSecOps profile.

Usage:
    python tools/devsecops/attestation_manager.py --project-id "proj-123" --generate --json
    python tools/devsecops/attestation_manager.py --project-id "proj-123" --verify --image "registry/app:v1.0" --json
    python tools/devsecops/attestation_manager.py --project-id "proj-123" --pipeline --json
    python tools/devsecops/attestation_manager.py --project-id "proj-123" --status --json
"""

import argparse
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

try:
    import yaml

    def _to_yaml(data: dict) -> str:
        return yaml.dump(data, default_flow_style=False, sort_keys=False)
except ImportError:
    yaml = None

    def _to_yaml(data: dict) -> str:
        return json.dumps(data, indent=2)


# ---------------------------------------------------------------------------
# Config & DB
# ---------------------------------------------------------------------------

def _load_config() -> dict:
    config_path = BASE_DIR / "args" / "devsecops_config.yaml"
    if yaml and config_path.exists():
        with open(config_path) as f:
            return yaml.safe_load(f) or {}
    return {}


def _get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _get_profile(project_id: str) -> dict:
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT * FROM devsecops_profiles WHERE project_id = ?",
            (project_id,)
        ).fetchone()
        if not row:
            return {}
        return {
            "maturity_level": row["maturity_level"],
            "active_stages": json.loads(row["active_stages"] or "[]"),
            "stage_configs": json.loads(row["stage_configs"] or "{}"),
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Signing configuration generation
# ---------------------------------------------------------------------------

def generate_signing_config(project_id: str, profile: dict = None) -> dict:
    """Generate cosign/notation signing configuration.

    Args:
        project_id: Project identifier.
        profile: Optional profile dict.

    Returns:
        Dict with signing_config, key_config, verification_policy.
    """
    if profile is None:
        profile = _get_profile(project_id)
        if not profile:
            return {"error": f"No DevSecOps profile for project {project_id}"}

    active = profile.get("active_stages", [])
    stage_cfg = profile.get("stage_configs", {}).get("image_signing", {})
    signing_tool = stage_cfg.get("tool", "cosign")

    has_signing = "image_signing" in active
    has_sbom = "sbom_attestation" in active

    if not has_signing and not has_sbom:
        return {
            "project_id": project_id,
            "status": "not_required",
            "message": "Neither image_signing nor sbom_attestation is active in profile",
        }

    signing_config = {
        "project_id": project_id,
        "signing_tool": signing_tool,
        "key_management": {
            "provider": "aws_kms",
            "kms_key_alias": f"alias/icdev-{project_id}-signing",
            "region": "us-gov-west-1",
            "rotation_days": 365,
        },
        "signing_policy": {
            "sign_on_build": has_signing,
            "attest_sbom": has_sbom,
            "verify_on_deploy": True,
            "require_signature_for_prod": True,
        },
    }

    verification_policy = {
        "apiVersion": "policy.sigstore.dev/v1beta1",
        "kind": "ClusterImagePolicy",
        "metadata": {
            "name": f"devsecops-{project_id}-verify",
            "labels": {
                "icdev.mil/project": project_id,
                "icdev.mil/component": "devsecops-attestation",
            },
        },
        "spec": {
            "images": [{"glob": "**"}],
            "authorities": [{
                "key": {
                    "kms": f"awskms:///alias/icdev-{project_id}-signing",
                },
            }],
        },
    }

    return {
        "project_id": project_id,
        "signing_tool": signing_tool,
        "image_signing_active": has_signing,
        "sbom_attestation_active": has_sbom,
        "signing_config": signing_config,
        "verification_policy_yaml": _to_yaml(verification_policy),
        "status": "configured",
    }


# ---------------------------------------------------------------------------
# Attestation pipeline generation
# ---------------------------------------------------------------------------

def generate_attestation_pipeline(project_id: str, profile: dict = None) -> dict:
    """Generate SLSA Level 3 attestation pipeline jobs.

    Returns:
        Dict with yaml_content for GitLab CI attestation jobs.
    """
    if profile is None:
        profile = _get_profile(project_id)
        if not profile:
            return {"error": f"No DevSecOps profile for project {project_id}"}

    active = profile.get("active_stages", [])
    now = datetime.now(timezone.utc).isoformat()

    jobs = []

    jobs.append(f"""
# =============================================================================
# DevSecOps Attestation Pipeline (SLSA Level 3)
# Project: {project_id}
# Generated: {now}
# =============================================================================
""")

    if "image_signing" in active:
        jobs.append("""
# --- Image Signing (cosign + KMS) ---
attestation:sign-image:
  stage: devsecops-check
  image: gcr.io/projectsigstore/cosign:latest
  script:
    - echo "Signing image with KMS key..."
    - cosign sign --key awskms:///alias/${COSIGN_KMS_KEY} ${IMAGE_NAME}:${IMAGE_TAG}
    - echo "Recording signing event in attestation log..."
    - cosign verify --key awskms:///alias/${COSIGN_KMS_KEY} ${IMAGE_NAME}:${IMAGE_TAG}
    - echo "Image signed and verified successfully"
  needs:
    - job: build:docker
  rules:
    - if: '$CI_COMMIT_BRANCH == "main"'
  variables:
    COSIGN_KMS_KEY: "icdev-${CI_PROJECT_NAME}-signing"
""")

    if "sbom_attestation" in active:
        jobs.append("""
# --- SBOM Generation + Attestation ---
attestation:sbom:
  stage: devsecops-check
  image: python:3.11-slim
  script:
    - pip install cyclonedx-bom
    - echo "Generating CycloneDX SBOM..."
    - cyclonedx-py environment --output sbom-cyclonedx.json --format json
    - echo "SBOM generated with $(python3 -c 'import json; print(len(json.load(open(\"sbom-cyclonedx.json\")).get(\"components\",[])))'  ) components"
  artifacts:
    when: always
    paths:
      - sbom-cyclonedx.json
    expire_in: 90 days
  needs:
    - job: build:docker
      optional: true
  rules:
    - if: '$CI_COMMIT_BRANCH == "main"'
    - if: '$CI_COMMIT_BRANCH == "develop"'

attestation:attest-sbom:
  stage: devsecops-check
  image: gcr.io/projectsigstore/cosign:latest
  script:
    - echo "Attesting SBOM to container image..."
    - cosign attest --key awskms:///alias/${COSIGN_KMS_KEY} --predicate sbom-cyclonedx.json --type cyclonedx ${IMAGE_NAME}:${IMAGE_TAG}
    - echo "SBOM attestation attached to image"
  needs:
    - job: attestation:sbom
    - job: build:docker
  rules:
    - if: '$CI_COMMIT_BRANCH == "main"'
  variables:
    COSIGN_KMS_KEY: "icdev-${CI_PROJECT_NAME}-signing"
""")

    # SLSA provenance (always when either is active)
    if "image_signing" in active or "sbom_attestation" in active:
        jobs.append("""
# --- SLSA Provenance Attestation ---
attestation:provenance:
  stage: devsecops-check
  image: gcr.io/projectsigstore/cosign:latest
  script:
    - |
      cat > provenance.json <<PROV
      {
        "_type": "https://in-toto.io/Statement/v0.1",
        "predicateType": "https://slsa.dev/provenance/v1",
        "predicate": {
          "buildDefinition": {
            "buildType": "https://gitlab.com/gitlab-ci",
            "externalParameters": {
              "repository": "${CI_PROJECT_URL}",
              "ref": "${CI_COMMIT_SHA}"
            }
          },
          "runDetails": {
            "builder": {
              "id": "https://gitlab.com/gitlab-ci"
            },
            "metadata": {
              "invocationId": "${CI_PIPELINE_ID}",
              "startedOn": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
            }
          }
        }
      }
      PROV
    - cosign attest --key awskms:///alias/${COSIGN_KMS_KEY} --predicate provenance.json --type slsaprovenance ${IMAGE_NAME}:${IMAGE_TAG}
    - echo "SLSA provenance attestation attached"
  artifacts:
    when: always
    paths:
      - provenance.json
    expire_in: 90 days
  needs:
    - job: build:docker
  rules:
    - if: '$CI_COMMIT_BRANCH == "main"'
  variables:
    COSIGN_KMS_KEY: "icdev-${CI_PROJECT_NAME}-signing"
""")

    yaml_content = "\n".join(jobs)

    return {
        "project_id": project_id,
        "image_signing": "image_signing" in active,
        "sbom_attestation": "sbom_attestation" in active,
        "slsa_level": 3 if ("image_signing" in active and "sbom_attestation" in active) else 2,
        "yaml_content": yaml_content,
        "jobs_generated": len([j for j in jobs if "stage:" in j]),
    }


# ---------------------------------------------------------------------------
# Attestation verification
# ---------------------------------------------------------------------------

def verify_attestation(project_id: str, image_ref: str,
                       expected_policies: list = None) -> dict:
    """Verify image attestation (dry-run check — actual verification requires cosign CLI).

    Args:
        project_id: Project identifier.
        image_ref: Image reference (e.g., "registry/app:v1.0").
        expected_policies: List of expected attestation types.

    Returns:
        Dict with verification instructions and expected checks.
    """
    if expected_policies is None:
        profile = _get_profile(project_id)
        active = profile.get("active_stages", [])
        expected_policies = []
        if "image_signing" in active:
            expected_policies.append("image_signature")
        if "sbom_attestation" in active:
            expected_policies.append("sbom_cyclonedx")
            expected_policies.append("slsa_provenance")

    verification_commands = []
    for policy in expected_policies:
        if policy == "image_signature":
            verification_commands.append(
                f"cosign verify --key awskms:///alias/icdev-{project_id}-signing {image_ref}"
            )
        elif policy == "sbom_cyclonedx":
            verification_commands.append(
                f"cosign verify-attestation --key awskms:///alias/icdev-{project_id}-signing "
                f"--type cyclonedx {image_ref}"
            )
        elif policy == "slsa_provenance":
            verification_commands.append(
                f"cosign verify-attestation --key awskms:///alias/icdev-{project_id}-signing "
                f"--type slsaprovenance {image_ref}"
            )

    return {
        "project_id": project_id,
        "image_ref": image_ref,
        "expected_attestations": expected_policies,
        "verification_commands": verification_commands,
        "note": "Run these commands in an environment with cosign CLI and KMS access",
    }


# ---------------------------------------------------------------------------
# Attestation status
# ---------------------------------------------------------------------------

def get_attestation_status(project_id: str) -> dict:
    """Get attestation status for a project from pipeline audit trail.

    Returns:
        Dict with latest attestation events and overall status.
    """
    conn = _get_db()
    try:
        rows = conn.execute(
            """SELECT stage, tool, status, created_at
               FROM devsecops_pipeline_audit
               WHERE project_id = ? AND stage IN ('image_signing', 'sbom_attestation')
               ORDER BY created_at DESC LIMIT 10""",
            (project_id,)
        ).fetchall()

        events = [dict(r) for r in rows]

        profile = _get_profile(project_id)
        active = profile.get("active_stages", [])

        return {
            "project_id": project_id,
            "image_signing_active": "image_signing" in active,
            "sbom_attestation_active": "sbom_attestation" in active,
            "recent_events": events,
            "event_count": len(events),
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Attestation Manager")
    parser.add_argument("--project-id", required=True, help="Project identifier")
    parser.add_argument("--generate", action="store_true",
                        help="Generate signing configuration")
    parser.add_argument("--pipeline", action="store_true",
                        help="Generate attestation pipeline jobs")
    parser.add_argument("--verify", action="store_true",
                        help="Verify image attestation")
    parser.add_argument("--image", help="Image reference for --verify")
    parser.add_argument("--status", action="store_true",
                        help="Get attestation status")
    parser.add_argument("--output", help="Write output to file")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--human", action="store_true", help="Human-readable output")
    args = parser.parse_args()

    if args.generate:
        result = generate_signing_config(args.project_id)
    elif args.pipeline:
        result = generate_attestation_pipeline(args.project_id)
    elif args.verify:
        if not args.image:
            result = {"error": "--verify requires --image"}
        else:
            result = verify_attestation(args.project_id, args.image)
    elif args.status:
        result = get_attestation_status(args.project_id)
    else:
        result = generate_signing_config(args.project_id)

    if args.output and "yaml_content" in result:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(result["yaml_content"], encoding="utf-8")
        result["output_file"] = str(out_path)

    if args.json or not args.human:
        print(json.dumps(result, indent=2))
    else:
        if "error" in result:
            print(f"ERROR: {result['error']}")
        elif "signing_config" in result:
            print(f"Project: {result['project_id']}")
            print(f"Signing tool: {result['signing_tool']}")
            print(f"Image signing: {'Active' if result['image_signing_active'] else 'Inactive'}")
            print(f"SBOM attestation: {'Active' if result['sbom_attestation_active'] else 'Inactive'}")
            print(f"Status: {result['status']}")
        elif "yaml_content" in result:
            print(f"Project: {result['project_id']}")
            print(f"SLSA Level: {result.get('slsa_level', 'N/A')}")
            print(f"Jobs generated: {result.get('jobs_generated', 0)}")
        elif "verification_commands" in result:
            print(f"Image: {result['image_ref']}")
            print("Verification commands:")
            for cmd in result["verification_commands"]:
                print(f"  $ {cmd}")


if __name__ == "__main__":
    main()
