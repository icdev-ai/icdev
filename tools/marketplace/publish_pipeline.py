#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""Marketplace Publish Pipeline — Orchestrate scan, validate, sign, and publish.

Full pipeline for publishing a GOTCHA asset to the marketplace:
    1. Validate asset structure (SKILL.md / goal.md / etc.)
    2. Parse and validate metadata (YAML frontmatter)
    3. Register asset + version in catalog
    4. Run 7-gate security scanning pipeline
    5. Generate digital signature (RSA-SHA256)
    6. Submit for review (cross-tenant) or auto-publish (tenant-local)
    7. Record audit trail

Usage:
    # Publish a skill to tenant-local catalog
    python tools/marketplace/publish_pipeline.py \\
        --asset-path /path/to/my-skill \\
        --asset-type skill --tenant-id "tenant-abc" \\
        --publisher-user "john.doe@mil" --json

    # Publish to central registry (requires review)
    python tools/marketplace/publish_pipeline.py \\
        --asset-path /path/to/my-skill \\
        --asset-type skill --tenant-id "tenant-abc" \\
        --target-tier central_vetted --json

    # Publish a new version of an existing asset
    python tools/marketplace/publish_pipeline.py \\
        --asset-path /path/to/my-skill \\
        --asset-id "asset-abc" --new-version "1.1.0" \\
        --changelog "Added Oracle support" --json
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

from tools.marketplace.catalog_manager import (
    register_asset, add_version, update_status,
)
from tools.marketplace.asset_scanner import run_full_scan

# Graceful imports
try:
    from tools.audit.audit_logger import log_event as audit_log_event
    _HAS_AUDIT = True
except ImportError:
    _HAS_AUDIT = False
    def audit_log_event(**kwargs):
        return -1

try:
    from tools.saas.artifacts.signer import sign_artifact
    _HAS_SIGNER = True
except ImportError:
    _HAS_SIGNER = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
ASSET_TYPE_FILES = {
    "skill": "SKILL.md",
    "goal": "goal.md",
    "hardprompt": "prompt.md",
    "context": "context.json",
    "args": "config.yaml",
    "compliance": "controls.json",
}

# Alternative names accepted for each type
ASSET_TYPE_ALTERNATIVES = {
    "skill": ["SKILL.md"],
    "goal": ["goal.md", "workflow.md"],
    "hardprompt": ["prompt.md", "template.md"],
    "context": ["context.json", "context.yaml", "reference.json"],
    "args": ["config.yaml", "config.json", "settings.yaml"],
    "compliance": ["controls.json", "overlay.json", "framework.json"],
}


def _gen_id(prefix="pub"):
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _audit(event_type, actor, action, details=None):
    if _HAS_AUDIT:
        try:
            audit_log_event(
                event_type=event_type, actor=actor,
                action=action, details=details, db_path=DB_PATH,
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Metadata parsing
# ---------------------------------------------------------------------------

def parse_skill_md(file_path):
    """Parse SKILL.md YAML frontmatter + body."""
    content = Path(file_path).read_text(encoding="utf-8", errors="ignore")
    metadata = {}

    # Extract YAML frontmatter between --- markers
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            frontmatter = parts[1].strip()
            body = parts[2].strip()
            try:
                import yaml
                metadata = yaml.safe_load(frontmatter) or {}
            except ImportError:
                # Fallback: basic key:value parsing
                for line in frontmatter.split("\n"):
                    if ":" in line:
                        key, _, val = line.partition(":")
                        metadata[key.strip()] = val.strip().strip('"').strip("'")
            metadata["_body"] = body
    else:
        metadata["_body"] = content

    return metadata


def parse_json_metadata(file_path):
    """Parse JSON metadata file."""
    with open(file_path) as f:
        return json.load(f)


def parse_yaml_metadata(file_path):
    """Parse YAML metadata file."""
    try:
        import yaml
        with open(file_path) as f:
            return yaml.safe_load(f) or {}
    except ImportError:
        return {}


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_asset_structure(asset_path, asset_type):
    """Validate that the asset directory has the expected structure.

    Returns (is_valid, errors, metadata).
    """
    asset_path = Path(asset_path)
    errors = []
    metadata = {}

    if not asset_path.is_dir():
        return False, ["Asset path is not a directory"], {}

    # Find the main metadata file
    main_file = None
    for candidate in ASSET_TYPE_ALTERNATIVES.get(asset_type, []):
        if (asset_path / candidate).exists():
            main_file = asset_path / candidate
            break

    if not main_file:
        expected = ASSET_TYPE_FILES.get(asset_type, "metadata file")
        errors.append(f"Missing required file: {expected}")
        return False, errors, {}

    # Parse metadata
    if main_file.suffix == ".md":
        metadata = parse_skill_md(main_file)
    elif main_file.suffix == ".json":
        try:
            metadata = parse_json_metadata(main_file)
        except json.JSONDecodeError as e:
            errors.append(f"Invalid JSON in {main_file.name}: {e}")
    elif main_file.suffix in (".yaml", ".yml"):
        metadata = parse_yaml_metadata(main_file)

    # Validate required fields
    name = metadata.get("name") or asset_path.name
    if not name:
        errors.append("Missing required field: name")

    description = metadata.get("description")
    if not description:
        errors.append("Missing required field: description")

    # Validate name format
    if name and not re.match(r'^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$', name):
        errors.append(f"Invalid name format: '{name}'. Must be lowercase, hyphens, 3-64 chars")

    # Check for scripts directory (skills)
    if asset_type == "skill":
        scripts_dir = asset_path / "scripts"
        if scripts_dir.is_dir():
            metadata["has_scripts"] = True
            metadata["script_count"] = len(list(scripts_dir.rglob("*")))
        else:
            metadata["has_scripts"] = False

    metadata["_main_file"] = str(main_file.relative_to(asset_path))
    return len(errors) == 0, errors, metadata


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def publish_asset(asset_path, asset_type, tenant_id, publisher_user,
                  publisher_org=None, target_tier="tenant_local",
                  asset_id=None, new_version=None, changelog=None,
                  signing_key_path=None, db_path=None):
    """Execute the full publish pipeline.

    Steps:
        1. Validate structure
        2. Parse metadata
        3. Register/update asset in catalog
        4. Run security scanning
        5. Sign artifact
        6. Submit for review or auto-publish
    """
    asset_path = Path(asset_path)
    pipeline_id = _gen_id("pipeline")
    steps = []

    # Step 1: Validate structure
    is_valid, errors, metadata = validate_asset_structure(asset_path, asset_type)
    steps.append({
        "step": "validate_structure",
        "status": "pass" if is_valid else "fail",
        "errors": errors,
    })
    if not is_valid:
        return {
            "pipeline_id": pipeline_id,
            "status": "failed",
            "failed_at": "validate_structure",
            "steps": steps,
            "errors": errors,
        }

    # Extract key metadata
    name = metadata.get("name") or asset_path.name
    description = metadata.get("description", f"{asset_type}: {name}")
    version = new_version or metadata.get("version", "1.0.0")
    impact_level = metadata.get("impact_level", "IL4")
    classification = metadata.get("classification", "CUI // SP-CTI")
    compliance_controls = metadata.get("compliance_controls")
    tags = metadata.get("tags")

    steps.append({
        "step": "parse_metadata",
        "status": "pass",
        "metadata": {
            "name": name, "version": version,
            "impact_level": impact_level, "classification": classification,
        },
    })

    # Step 3: Register or add version
    if asset_id:
        # Adding new version to existing asset
        version_result = add_version(
            asset_id=asset_id, version=version,
            changelog=changelog, file_path=str(asset_path),
            published_by=publisher_user, db_path=db_path,
        )
        version_id = version_result["version_id"]
        steps.append({"step": "add_version", "status": "pass", "version_id": version_id})
    else:
        # New asset registration
        reg_result = register_asset(
            name=name, asset_type=asset_type,
            description=description, version=version,
            impact_level=impact_level, classification=classification,
            tenant_id=tenant_id, publisher_org=publisher_org,
            publisher_user=publisher_user, tags=tags,
            compliance_controls=compliance_controls, db_path=db_path,
        )
        asset_id = reg_result["asset_id"]
        version_result = add_version(
            asset_id=asset_id, version=version,
            changelog=changelog, file_path=str(asset_path),
            published_by=publisher_user, db_path=db_path,
        )
        version_id = version_result["version_id"]
        steps.append({
            "step": "register_asset",
            "status": "pass",
            "asset_id": asset_id,
            "version_id": version_id,
        })

    # Step 4: Security scanning
    update_status(asset_id, "scanning", db_path)
    scan_result = run_full_scan(
        asset_id=asset_id, version_id=version_id,
        asset_path=str(asset_path),
        expected_classification=classification,
        db_path=db_path,
    )
    steps.append({
        "step": "security_scan",
        "status": scan_result["overall_status"],
        "blocking_gates_pass": scan_result["blocking_gates_pass"],
        "gates_scanned": scan_result["gates_scanned"],
    })

    if not scan_result["blocking_gates_pass"]:
        update_status(asset_id, "draft", db_path)
        return {
            "pipeline_id": pipeline_id,
            "asset_id": asset_id,
            "version_id": version_id,
            "status": "failed",
            "failed_at": "security_scan",
            "steps": steps,
            "scan_result": scan_result,
        }

    # Step 5: Digital signature
    signature_info = {"signed": False}
    if _HAS_SIGNER and signing_key_path:
        try:
            # Create a tarball-like content hash for signing
            sig_data = sign_artifact(str(asset_path), private_key_path=signing_key_path)
            signature_info = {"signed": True, "signature": sig_data.get("signature", "")}
        except Exception as e:
            signature_info = {"signed": False, "error": str(e)}
    steps.append({"step": "sign_artifact", "status": "pass" if signature_info["signed"] else "skipped"})

    # Step 6: Publish or submit for review
    if target_tier == "central_vetted":
        # Submit for human review
        update_status(asset_id, "review", db_path)
        # Create review request
        conn = sqlite3.connect(str(db_path or DB_PATH))
        conn.row_factory = sqlite3.Row
        review_id = _gen_id("rev")
        conn.execute(
            """INSERT INTO marketplace_reviews
               (id, asset_id, version_id, decision, submitted_at)
               VALUES (?, ?, ?, 'pending', ?)""",
            (review_id, asset_id, version_id, _now()),
        )
        conn.commit()
        conn.close()
        steps.append({
            "step": "submit_review",
            "status": "pass",
            "review_id": review_id,
            "message": "Submitted for ISSO/security officer review",
        })
        final_status = "pending_review"
    else:
        # Auto-publish to tenant-local catalog
        update_status(asset_id, "published", db_path)
        # Update version status
        conn = sqlite3.connect(str(db_path or DB_PATH))
        conn.execute(
            "UPDATE marketplace_versions SET status = 'published' WHERE id = ?",
            (version_id,),
        )
        conn.commit()
        conn.close()
        steps.append({"step": "publish", "status": "pass"})
        final_status = "published"

    _audit(
        event_type="marketplace_asset_published",
        actor=publisher_user,
        action=f"Published {name} v{version} to {target_tier}",
        details={
            "pipeline_id": pipeline_id,
            "asset_id": asset_id,
            "version_id": version_id,
            "target_tier": target_tier,
            "scan_status": scan_result["overall_status"],
        },
    )

    return {
        "pipeline_id": pipeline_id,
        "asset_id": asset_id,
        "version_id": version_id,
        "slug": f"{tenant_id[:12] if tenant_id else 'central'}/{name}",
        "status": final_status,
        "version": version,
        "target_tier": target_tier,
        "scan_status": scan_result["overall_status"],
        "steps": steps,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="ICDEV Marketplace Publish Pipeline")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--db-path", type=Path, default=None)

    parser.add_argument("--asset-path", required=True, help="Path to asset directory")
    parser.add_argument("--asset-type", required=True,
                        choices=["skill", "goal", "hardprompt", "context", "args", "compliance"])
    parser.add_argument("--tenant-id", required=True, help="Publisher tenant ID")
    parser.add_argument("--publisher-user", required=True, help="Publisher identity")
    parser.add_argument("--publisher-org", help="Publisher organization")
    parser.add_argument("--target-tier", choices=["tenant_local", "central_vetted"],
                        default="tenant_local")
    parser.add_argument("--asset-id", help="Existing asset ID (for new version)")
    parser.add_argument("--new-version", help="Version string for update")
    parser.add_argument("--changelog", help="Version changelog")
    parser.add_argument("--signing-key", help="Path to RSA private key for signing")

    args = parser.parse_args()
    db_path = Path(args.db_path) if args.db_path else None

    try:
        result = publish_asset(
            asset_path=args.asset_path,
            asset_type=args.asset_type,
            tenant_id=args.tenant_id,
            publisher_user=args.publisher_user,
            publisher_org=args.publisher_org,
            target_tier=args.target_tier,
            asset_id=args.asset_id,
            new_version=args.new_version,
            changelog=args.changelog,
            signing_key_path=args.signing_key,
            db_path=db_path,
        )

        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            print(f"Pipeline: {result['status']}")
            for step in result.get("steps", []):
                print(f"  [{step['status']}] {step['step']}")

    except Exception as e:
        error = {"error": str(e)}
        if args.json:
            print(json.dumps(error, indent=2))
        else:
            print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
