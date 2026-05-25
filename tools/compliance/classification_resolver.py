#!/usr/bin/env python3

from tools.logging.icdev_logger import get_logger
"""Classification Resolver — single-source API for classification-aware behavior.

All ICDEV™ tools that need to know the project's classification level should call
this module instead of hardcoding CUI assumptions. The resolver reads the profile
from args/classification_profiles.yaml and answers questions like:

    - Should I add CUI banners to generated files?
    - Which compliance frameworks should auto-activate?
    - Should security gates enforce CUI markings?

Usage:
    from tools.compliance.classification_resolver import ClassificationResolver

    resolver = ClassificationResolver("public")
    resolver.banner_enabled        # False
    resolver.file_header_text      # ""
    resolver.auto_frameworks       # []
    resolver.should_include_tool("cui_marker")  # False

CLI:
    python tools/compliance/classification_resolver.py --level public --json
    python tools/compliance/classification_resolver.py --list --json
    python tools/compliance/classification_resolver.py --level cui --check banner_enabled
"""

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = get_logger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
PROFILES_PATH = BASE_DIR / "args" / "classification_profiles.yaml"

# Default profile used when no classification is specified
DEFAULT_LEVEL = "public"


def _load_profiles() -> Dict[str, Any]:
    """Load classification profiles from YAML config."""
    try:
        import yaml
    except ImportError:
        yaml = None

    if yaml and PROFILES_PATH.exists():
        with open(PROFILES_PATH, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    # Fallback: minimal built-in profiles (no yaml dependency)
    return {
        "default_level": "public",
        "levels": ["public", "cui", "cui_sp_cti", "secret"],
        "profiles": {
            "public": {
                "display_name": "Public / Unclassified",
                "banner": {"enabled": False, "text": ""},
                "file_header": {"enabled": False, "text": ""},
                "docker_labels": {"enabled": False},
                "k8s_labels": {"enabled": False},
                "compliance": {
                    "auto_frameworks": [],
                    "cui_markings_required": False,
                    "sbom_required": True,
                    "stig_required": False,
                },
                "security_gates": {
                    "enforce_cui_marking_gate": False,
                    "enforce_classification_gate": False,
                    "enforce_nist_800_171": False,
                    "enforce_cmmc": False,
                },
                "tools": {
                    "include_classification_manager": False,
                    "include_cui_marker": False,
                    "include_fips199_categorizer": False,
                },
                "generation": {
                    "strip_cui_headers": True,
                    "strip_cui_docker_labels": True,
                    "strip_cui_k8s_labels": True,
                    "strip_classification_tools": True,
                },
            },
            "cui_sp_cti": {
                "display_name": "CUI // SP-CTI",
                "banner": {"enabled": True, "text": "CUI // SP-CTI"},
                "file_header": {"enabled": True, "text": "# CUI // SP-CTI"},
                "docker_labels": {"enabled": True},
                "k8s_labels": {"enabled": True},
                "compliance": {
                    "auto_frameworks": ["nist_800_171", "fedramp_moderate", "cmmc_l2"],
                    "cui_markings_required": True,
                    "sbom_required": True,
                    "stig_required": True,
                },
                "security_gates": {
                    "enforce_cui_marking_gate": True,
                    "enforce_classification_gate": True,
                    "enforce_nist_800_171": True,
                    "enforce_cmmc": True,
                },
                "tools": {
                    "include_classification_manager": True,
                    "include_cui_marker": True,
                    "include_fips199_categorizer": True,
                },
                "generation": {
                    "strip_cui_headers": False,
                    "strip_cui_docker_labels": False,
                    "strip_cui_k8s_labels": False,
                    "strip_classification_tools": False,
                },
            },
        },
    }


class ClassificationResolver:
    """Resolves classification-level questions for a given project.

    This is the SINGLE SOURCE OF TRUTH for classification behavior across ICDEV™.
    All tools should use this instead of hardcoding CUI/classification checks.
    """

    def __init__(self, level: Optional[str] = None):
        """Initialize resolver for a classification level.

        Args:
            level: Classification level (e.g., 'public', 'cui', 'cui_sp_cti', 'secret').
                   If None, uses the default from config.
        """
        self._config = _load_profiles()
        self._level = (level or self._config.get("default_level", DEFAULT_LEVEL)).lower()
        self._profile = self._config.get("profiles", {}).get(self._level, {})

        if not self._profile:
            logger.warning(
                "Classification level '%s' not found in profiles; falling back to '%s'",
                self._level,
                DEFAULT_LEVEL,
            )
            self._level = DEFAULT_LEVEL
            self._profile = self._config.get("profiles", {}).get(DEFAULT_LEVEL, {})

    # ----- Properties: Banner & Headers -----

    @property
    def level(self) -> str:
        """Current classification level."""
        return self._level

    @property
    def display_name(self) -> str:
        """Human-readable classification name."""
        return self._profile.get("display_name", self._level.upper())

    @property
    def banner_enabled(self) -> bool:
        """Whether to show classification banners in UI/documents."""
        return self._profile.get("banner", {}).get("enabled", False)

    @property
    def banner_text(self) -> str:
        """Banner text (e.g., 'CUI // SP-CTI')."""
        return self._profile.get("banner", {}).get("text", "")

    @property
    def file_header_enabled(self) -> bool:
        """Whether to add classification headers to generated code files."""
        return self._profile.get("file_header", {}).get("enabled", False)

    @property
    def file_header_text(self) -> str:
        """Classification header text for code files."""
        return self._profile.get("file_header", {}).get("text", "")

    # ----- Properties: Infrastructure -----

    @property
    def docker_labels_enabled(self) -> bool:
        """Whether to add classification labels to Dockerfiles."""
        return self._profile.get("docker_labels", {}).get("enabled", False)

    @property
    def k8s_labels_enabled(self) -> bool:
        """Whether to add classification labels to K8s manifests."""
        return self._profile.get("k8s_labels", {}).get("enabled", False)

    # ----- Properties: Compliance -----

    @property
    def auto_frameworks(self) -> List[str]:
        """Compliance frameworks to auto-activate for this classification."""
        return self._profile.get("compliance", {}).get("auto_frameworks", [])

    @property
    def cui_markings_required(self) -> bool:
        """Whether CUI markings are required on generated artifacts."""
        return self._profile.get("compliance", {}).get("cui_markings_required", False)

    @property
    def sbom_required(self) -> bool:
        """Whether SBOM generation is required."""
        return self._profile.get("compliance", {}).get("sbom_required", True)

    @property
    def stig_required(self) -> bool:
        """Whether STIG compliance is required."""
        return self._profile.get("compliance", {}).get("stig_required", False)

    # ----- Properties: Security Gates -----

    @property
    def enforce_cui_marking_gate(self) -> bool:
        """Whether the CUI marking gate blocks merges."""
        return self._profile.get("security_gates", {}).get("enforce_cui_marking_gate", False)

    @property
    def enforce_classification_gate(self) -> bool:
        """Whether the classification gate is enforced."""
        return self._profile.get("security_gates", {}).get("enforce_classification_gate", False)

    def is_gate_enforced(self, gate_name: str) -> bool:
        """Check if a specific security gate is enforced for this classification."""
        gates = self._profile.get("security_gates", {})
        return gates.get(gate_name, False)

    # ----- Properties: Tool Inclusion -----

    def should_include_tool(self, tool_name: str) -> bool:
        """Check if a tool should be included in generated child apps.

        Args:
            tool_name: Tool identifier (e.g., 'cui_marker',
                       'classification_manager', 'fips199_categorizer').
        """
        tools = self._profile.get("tools", {})
        key = f"include_{tool_name}"
        return tools.get(key, False)

    # ----- Properties: Generation Behavior -----

    @property
    def strip_cui_headers(self) -> bool:
        """Whether to strip CUI headers from generated files."""
        return self._profile.get("generation", {}).get("strip_cui_headers", True)

    @property
    def strip_cui_docker_labels(self) -> bool:
        """Whether to strip CUI labels from generated Dockerfiles."""
        return self._profile.get("generation", {}).get("strip_cui_docker_labels", True)

    @property
    def strip_cui_k8s_labels(self) -> bool:
        """Whether to strip CUI labels from generated K8s manifests."""
        return self._profile.get("generation", {}).get("strip_cui_k8s_labels", True)

    @property
    def strip_classification_tools(self) -> bool:
        """Whether to strip classification-related tools from child apps."""
        return self._profile.get("generation", {}).get("strip_classification_tools", True)

    # ----- Helpers -----

    def to_dict(self) -> Dict[str, Any]:
        """Export resolved settings as a dictionary."""
        return {
            "level": self._level,
            "display_name": self.display_name,
            "banner_enabled": self.banner_enabled,
            "banner_text": self.banner_text,
            "file_header_enabled": self.file_header_enabled,
            "file_header_text": self.file_header_text,
            "docker_labels_enabled": self.docker_labels_enabled,
            "k8s_labels_enabled": self.k8s_labels_enabled,
            "auto_frameworks": self.auto_frameworks,
            "cui_markings_required": self.cui_markings_required,
            "sbom_required": self.sbom_required,
            "stig_required": self.stig_required,
            "enforce_cui_marking_gate": self.enforce_cui_marking_gate,
            "strip_cui_headers": self.strip_cui_headers,
            "strip_classification_tools": self.strip_classification_tools,
        }

    @staticmethod
    def list_levels() -> List[Dict[str, str]]:
        """List all available classification levels with descriptions."""
        config = _load_profiles()
        result = []
        for level_name in config.get("levels", []):
            profile = config.get("profiles", {}).get(level_name, {})
            result.append(
                {
                    "level": level_name,
                    "display_name": profile.get("display_name", level_name),
                    "description": profile.get("description", ""),
                }
            )
        return result

    @staticmethod
    def get_default_level() -> str:
        """Get the default classification level."""
        config = _load_profiles()
        return config.get("default_level", DEFAULT_LEVEL)


def resolve_classification(level: Optional[str] = None) -> Dict[str, Any]:
    """Convenience function: resolve classification and return as dict."""
    resolver = ClassificationResolver(level)
    return resolver.to_dict()


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="ICDEV™ Classification Resolver — resolve classification settings")
    parser.add_argument("--level", help="Classification level to resolve (e.g., public, cui, secret)")
    parser.add_argument("--list", action="store_true", help="List all available classification levels")
    parser.add_argument("--check", help="Check a specific setting (e.g., banner_enabled)")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    if args.list:
        levels = ClassificationResolver.list_levels()
        if args.json:
            print(json.dumps({"levels": levels, "default": ClassificationResolver.get_default_level()}, indent=2))
        else:
            for lvl in levels:
                print(f"  {lvl['level']:<15} {lvl['display_name']:<35} {lvl['description']}")
        return

    resolver = ClassificationResolver(args.level)

    if args.check:
        value = getattr(resolver, args.check, None)
        if value is None:
            value = resolver.is_gate_enforced(args.check)
        if args.json:
            print(json.dumps({args.check: value}))
        else:
            print(f"{args.check}: {value}")
        return

    result = resolver.to_dict()
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        for key, value in result.items():
            print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
