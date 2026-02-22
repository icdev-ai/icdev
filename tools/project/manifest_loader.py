#!/usr/bin/env python3
# CUI // SP-CTI
"""Parse and validate icdev.yaml project manifests.

Reads icdev.yaml from a project directory, validates structure and
cross-field constraints, applies defaults based on impact level, and
returns a normalized configuration dict.

The manifest is advisory (D189) — it declares intent but the DB remains
the source of truth.  Env var overrides use the ``ICDEV_`` prefix and
take precedence over yaml values (D193).

Usage:
    python tools/project/manifest_loader.py --dir /path/to/project --json
    python tools/project/manifest_loader.py --file /path/to/icdev.yaml --validate
"""

import argparse
import json
import os
import re
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent

MANIFEST_FILENAME = "icdev.yaml"
MANIFEST_VERSION = 1

# ── Impact-level default mappings ────────────────────────────────────────

IL_DEFAULTS = {
    "IL2": {
        "classification_level": "UNCLASSIFIED",
        "cui_markings": False,
        "frameworks": [],
        "profile_template": "startup",
        "cloud": "aws",
        "deployment_platform": "docker",
    },
    "IL4": {
        "classification_level": "CUI",
        "cui_markings": True,
        "frameworks": ["fedramp_moderate"],
        "profile_template": "dod_baseline",
        "cloud": "aws_govcloud",
        "deployment_platform": "k8s",
    },
    "IL5": {
        "classification_level": "CUI",
        "cui_markings": True,
        "frameworks": ["fedramp_high", "cmmc_l2"],
        "profile_template": "dod_baseline",
        "cloud": "aws_govcloud",
        "deployment_platform": "k8s",
    },
    "IL6": {
        "classification_level": "SECRET",
        "cui_markings": True,
        "frameworks": ["fedramp_high", "cmmc_l3"],
        "profile_template": "dod_baseline",
        "cloud": "aws_govcloud",
        "deployment_platform": "k8s",
    },
}

VALID_IMPACT_LEVELS = list(IL_DEFAULTS.keys())
VALID_PROJECT_TYPES = [
    "api", "microservice", "monolith", "cli", "data-pipeline",
    "frontend", "library", "webapp", "data_pipeline", "iac",
]
VALID_LANGUAGES = ["python", "java", "go", "rust", "csharp", "typescript"]
VALID_FRAMEWORKS = [
    "fedramp_moderate", "fedramp_high", "cmmc_l2", "cmmc_l3",
    "nist_800_171", "cjis", "hipaa", "hitrust", "soc2", "pci_dss",
    "iso27001", "mosa",
]
VALID_ATO_STATUSES = [
    "none", "in_progress", "iato", "ato", "cato", "dato", "denied", "active",
]

# ── Env-var override mapping ─────────────────────────────────────────────

_ENV_MAP = {
    "ICDEV_IMPACT_LEVEL": ("impact_level",),
    "ICDEV_DEPLOYMENT_CLOUD": ("deployment", "cloud"),
    "ICDEV_DEPLOYMENT_REGION": ("deployment", "region"),
    "ICDEV_DEPLOYMENT_PLATFORM": ("deployment", "platform"),
    "ICDEV_GATE_MIN_COVERAGE": ("pipeline", "gates", "min_coverage"),
    "ICDEV_GATE_STIG_MAX_CAT1": ("pipeline", "gates", "stig_max_cat1"),
    "ICDEV_GATE_STIG_MAX_CAT2": ("pipeline", "gates", "stig_max_cat2"),
    "ICDEV_GATE_MAX_CRITICAL_VULNS": ("pipeline", "gates", "max_critical_vulns"),
}


# ── Helpers ──────────────────────────────────────────────────────────────

def _deep_get(d: dict, keys: tuple, default=None):
    """Get a nested dict value by key path."""
    for k in keys:
        if not isinstance(d, dict):
            return default
        d = d.get(k, default)
    return d


def _deep_set(d: dict, keys: tuple, value):
    """Set a nested dict value by key path, creating intermediate dicts."""
    for k in keys[:-1]:
        d = d.setdefault(k, {})
    d[keys[-1]] = value


def _try_int(val):
    """Attempt to parse as int, return original on failure."""
    try:
        return int(val)
    except (ValueError, TypeError):
        return val


# ── Core functions ───────────────────────────────────────────────────────

def load_manifest(directory: str = None, file_path: str = None) -> dict:
    """Load and parse icdev.yaml from a directory or explicit path.

    Args:
        directory: Directory containing icdev.yaml (defaults to cwd).
        file_path: Explicit path to an icdev.yaml file (overrides directory).

    Returns:
        dict with keys:
            raw (dict): Original yaml content.
            normalized (dict): Config with defaults and env overrides applied.
            file_path (str): Resolved file path.
            valid (bool): True if no errors.
            errors (list[str]): Validation errors.
            warnings (list[str]): Validation warnings.
    """
    if file_path:
        manifest_path = Path(file_path)
    else:
        base = Path(directory) if directory else Path.cwd()
        manifest_path = base / MANIFEST_FILENAME

    result = {
        "raw": {},
        "normalized": {},
        "file_path": str(manifest_path),
        "valid": False,
        "errors": [],
        "warnings": [],
    }

    if not manifest_path.exists():
        result["errors"].append(f"Manifest not found: {manifest_path}")
        return result

    # Parse YAML
    try:
        import yaml
        raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except ImportError:
        # Fallback: attempt JSON parse (for testing / air-gap edge case)
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:
            result["errors"].append(f"Cannot parse manifest (pyyaml not installed): {exc}")
            return result
    except Exception as exc:
        result["errors"].append(f"YAML parse error: {exc}")
        return result

    if not isinstance(raw, dict):
        result["errors"].append("Manifest root must be a YAML mapping")
        return result

    result["raw"] = raw

    # Version check
    version = raw.get("version")
    if version is not None and version != MANIFEST_VERSION:
        result["warnings"].append(
            f"Manifest version {version} differs from expected {MANIFEST_VERSION}"
        )

    # Apply defaults then env overrides
    normalized = _apply_defaults(deepcopy(raw))
    normalized = _apply_env_overrides(normalized)
    result["normalized"] = normalized

    # Validate
    errors, warnings = validate_manifest(normalized)
    result["errors"] = errors
    result["warnings"] += warnings
    result["valid"] = len(errors) == 0

    return result


def _apply_defaults(raw: dict) -> dict:
    """Apply impact-level-based defaults to raw yaml config."""
    config = deepcopy(raw)

    # Normalize impact_level
    il = config.get("impact_level", "IL4")
    if isinstance(il, str) and not il.startswith("IL"):
        il = f"IL{il}"
    config["impact_level"] = il

    defaults = IL_DEFAULTS.get(il, IL_DEFAULTS["IL4"])

    # Project defaults
    project = config.setdefault("project", {})
    if not project.get("id"):
        name = project.get("name", "unnamed")
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        project["id"] = f"proj-{slug}"

    # Classification defaults
    classification = config.setdefault("classification", {})
    classification.setdefault("level", defaults["classification_level"])
    classification.setdefault("cui_markings", defaults["cui_markings"])

    # Compliance defaults
    compliance = config.setdefault("compliance", {})
    compliance.setdefault("frameworks", list(defaults["frameworks"]))
    compliance.setdefault("ato", {}).setdefault("status", "none")

    # Profile defaults
    profile = config.setdefault("profile", {})
    profile.setdefault("scope", "project")
    profile.setdefault("template", defaults["profile_template"])

    # Pipeline defaults
    pipeline = config.setdefault("pipeline", {})
    pipeline.setdefault("platform", "auto")
    pipeline.setdefault("on_pr", [
        "sast", "dependency_audit", "secret_detection", "cui_check",
        "stig_check", "unit_tests",
    ])
    pipeline.setdefault("on_merge", ["ssp_generate", "sbom_generate"])
    pipeline.setdefault("gates", {})
    gates = pipeline["gates"]
    gates.setdefault("stig_max_cat1", 0)
    gates.setdefault("stig_max_cat2", 0)
    gates.setdefault("min_coverage", 80)
    gates.setdefault("max_critical_vulns", 0)

    # Deployment defaults
    deployment = config.setdefault("deployment", {})
    deployment.setdefault("cloud", defaults["cloud"])
    deployment.setdefault("platform", defaults["deployment_platform"])

    # Companion defaults (D194)
    companion = config.setdefault("companion", {})
    companion.setdefault("tools", [])
    companion.setdefault("auto_sync", False)
    companion.setdefault("instruction_style", "full")

    return config


def _apply_env_overrides(config: dict) -> dict:
    """Override config values from ICDEV_* environment variables (D193)."""
    for env_var, key_path in _ENV_MAP.items():
        val = os.environ.get(env_var)
        if val is not None:
            _deep_set(config, key_path, _try_int(val))
    return config


def validate_manifest(config: dict) -> tuple:
    """Validate cross-field constraints.

    Returns:
        (errors: list[str], warnings: list[str])
    """
    errors = []
    warnings = []

    il = config.get("impact_level", "IL4")
    classification_level = _deep_get(config, ("classification", "level"), "")
    cui_markings = _deep_get(config, ("classification", "cui_markings"), True)
    frameworks = _deep_get(config, ("compliance", "frameworks"), [])
    cloud = _deep_get(config, ("deployment", "cloud"), "")

    # Project basics
    project = config.get("project", {})
    if not project.get("name"):
        errors.append("project.name is required")

    # IL6 requires SECRET
    if il == "IL6" and classification_level != "SECRET":
        errors.append(
            f"impact_level IL6 requires classification.level = SECRET "
            f"(got '{classification_level}')"
        )

    # CJIS requires IL4+
    if "cjis" in frameworks and il == "IL2":
        errors.append("cjis framework requires impact_level >= IL4")

    # FedRAMP High on non-GovCloud
    if "fedramp_high" in frameworks and il in ("IL4", "IL5", "IL6"):
        if cloud == "aws" and cloud != "aws_govcloud":
            errors.append(
                "fedramp_high with impact_level IL4+ requires "
                "deployment.cloud = aws_govcloud (got 'aws')"
            )

    # Warnings
    if il in ("IL4", "IL5", "IL6") and not frameworks:
        warnings.append(
            f"No compliance frameworks specified for {il} project "
            f"(consider adding fedramp_moderate or fedramp_high)"
        )

    if il in ("IL5", "IL6") and not cui_markings:
        warnings.append(
            f"CUI markings disabled but impact_level = {il} "
            f"(CUI markings are required for {il})"
        )

    if il == "IL2" and cui_markings:
        warnings.append(
            "CUI markings enabled for IL2 project (typically unnecessary)"
        )

    # Companion tool validation (D194)
    companion_tools = _deep_get(config, ("companion", "tools"), [])
    valid_companions = [
        "claude_code", "codex", "gemini", "copilot", "cursor",
        "windsurf", "amazon_q", "junie", "cline", "aider",
    ]
    for ct in companion_tools:
        if ct not in valid_companions:
            warnings.append(
                f"Unknown companion tool '{ct}' in companion.tools "
                f"(valid: {', '.join(valid_companions)})"
            )

    return errors, warnings


def detect_vcs_platform(directory: str = None) -> str:
    """Auto-detect GitHub vs GitLab from git remote URL.

    Args:
        directory: Git repo directory (defaults to cwd).

    Returns:
        'github', 'gitlab', or 'unknown'.
    """
    try:
        cwd = directory or str(Path.cwd())
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=10,
            cwd=cwd,
        )
        if result.returncode != 0:
            return "unknown"

        url = result.stdout.strip().lower()
        if "github" in url:
            return "github"
        elif "gitlab" in url:
            return "gitlab"
        else:
            return "gitlab"  # Default to GitLab for non-GitHub (self-hosted)
    except Exception:
        return "unknown"


# ── CLI ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Parse and validate icdev.yaml project manifests"
    )
    parser.add_argument("--dir", help="Project directory containing icdev.yaml")
    parser.add_argument("--file", help="Explicit path to icdev.yaml")
    parser.add_argument("--validate", action="store_true",
                        help="Validate only, print errors/warnings")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    result = load_manifest(directory=args.dir, file_path=args.file)

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    elif args.validate:
        if result["errors"]:
            for err in result["errors"]:
                print(f"ERROR: {err}")
        for warn in result["warnings"]:
            print(f"WARNING: {warn}")
        if result["valid"]:
            print("Manifest is valid.")
        sys.exit(0 if result["valid"] else 1)
    else:
        if result["errors"]:
            for err in result["errors"]:
                print(f"ERROR: {err}")
        for warn in result["warnings"]:
            print(f"WARNING: {warn}")
        if result["valid"]:
            print(json.dumps(result["normalized"], indent=2, default=str))
        sys.exit(0 if result["valid"] else 1)

    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
