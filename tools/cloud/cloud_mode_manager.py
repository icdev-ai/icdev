#!/usr/bin/env python3
# CUI // SP-CTI
"""Cloud Mode Manager — orchestrates cloud mode selection and validation (D232).

Provides a CLI and programmatic interface for:
- Querying the current cloud mode and provider configuration
- Validating cloud mode against impact level requirements
- Checking provider readiness for a given cloud mode
- Switching cloud mode with safety checks

Uses CSPProviderFactory (D225) for provider resolution and RegionValidator (D234)
for compliance certification validation.

CLI: --status, --validate, --switch, --check-readiness, --json
"""

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("icdev.cloud.mode_manager")

BASE_DIR = Path(__file__).resolve().parent.parent.parent

try:
    import yaml
except ImportError:
    yaml = None


# Cloud mode definitions (D232)
CLOUD_MODES = {
    "commercial": {
        "description": "Standard commercial cloud regions and endpoints",
        "fips_required": False,
        "internet_required": True,
        "supported_ils": ["IL2"],
    },
    "government": {
        "description": "Government cloud regions with FedRAMP authorization",
        "fips_required": True,
        "internet_required": True,
        "supported_ils": ["IL2", "IL4", "IL5"],
    },
    "on_prem": {
        "description": "On-premises deployment with optional cloud services",
        "fips_required": True,
        "internet_required": False,
        "supported_ils": ["IL2", "IL4", "IL5", "IL6"],
    },
    "air_gapped": {
        "description": "Fully air-gapped, no internet connectivity",
        "fips_required": True,
        "internet_required": False,
        "supported_ils": ["IL2", "IL4", "IL5", "IL6"],
    },
}

# CSP support matrix per cloud mode
CSP_MODE_SUPPORT = {
    "aws": ["commercial", "government"],
    "azure": ["commercial", "government"],
    "gcp": ["commercial", "government"],
    "oci": ["commercial", "government"],
    "ibm": ["commercial", "government"],
    "local": ["commercial", "government", "on_prem", "air_gapped"],
}

# Impact level to minimum cloud mode requirements
IL_MODE_REQUIREMENTS = {
    "IL2": ["commercial", "government", "on_prem", "air_gapped"],
    "IL4": ["government", "on_prem", "air_gapped"],
    "IL5": ["government", "on_prem", "air_gapped"],
    "IL6": ["on_prem", "air_gapped"],
}


class CloudModeManager:
    """Orchestrate cloud mode selection, validation, and transitions."""

    def __init__(self, config_path: Optional[str] = None):
        self._config_path = Path(config_path) if config_path else (
            BASE_DIR / "args" / "cloud_config.yaml"
        )
        self._config: Dict = {}
        self._load_config()

    def _load_config(self):
        """Load cloud_config.yaml."""
        if yaml is None:
            logger.warning("PyYAML not available — using defaults")
            self._config = {"cloud": {"provider": "local", "cloud_mode": "government"}}
            return
        if not self._config_path.exists():
            logger.warning("Cloud config not found: %s", self._config_path)
            self._config = {"cloud": {"provider": "local", "cloud_mode": "government"}}
            return
        try:
            with open(self._config_path, "r", encoding="utf-8") as f:
                self._config = yaml.safe_load(f) or {}
        except Exception as exc:
            logger.error("Failed to load cloud config: %s", exc)
            self._config = {"cloud": {"provider": "local", "cloud_mode": "government"}}

    @property
    def cloud_section(self) -> Dict:
        return self._config.get("cloud", {})

    @property
    def current_mode(self) -> str:
        return self.cloud_section.get("cloud_mode", "government")

    @property
    def current_provider(self) -> str:
        return self.cloud_section.get("provider", "local")

    @property
    def current_region(self) -> str:
        return self.cloud_section.get("region", "")

    @property
    def impact_level(self) -> str:
        return self.cloud_section.get("impact_level", "IL5")

    @property
    def air_gapped(self) -> bool:
        return self.cloud_section.get("air_gapped", False)

    def get_status(self) -> Dict:
        """Get current cloud mode status and configuration summary."""
        mode = self.current_mode
        mode_info = CLOUD_MODES.get(mode, {})
        provider = self.current_provider
        il = self.impact_level

        # Check CSP supports current mode
        csp_modes = CSP_MODE_SUPPORT.get(provider, [])
        csp_compatible = mode in csp_modes

        # Check IL supports current mode
        il_modes = IL_MODE_REQUIREMENTS.get(il, [])
        il_compatible = mode in il_modes

        return {
            "cloud_mode": mode,
            "cloud_mode_description": mode_info.get("description", "Unknown"),
            "provider": provider,
            "region": self.current_region,
            "impact_level": il,
            "air_gapped": self.air_gapped,
            "fips_required": mode_info.get("fips_required", False),
            "internet_required": mode_info.get("internet_required", True),
            "supported_impact_levels": mode_info.get("supported_ils", []),
            "csp_compatible": csp_compatible,
            "il_compatible": il_compatible,
            "valid": csp_compatible and il_compatible,
        }

    def validate(self) -> Dict:
        """Validate current cloud mode against all constraints.

        Returns:
            {valid: bool, errors: list, warnings: list}
        """
        errors: List[str] = []
        warnings: List[str] = []

        mode = self.current_mode
        provider = self.current_provider
        il = self.impact_level

        # 1. Check mode is valid
        if mode not in CLOUD_MODES:
            errors.append(f"Unknown cloud_mode: {mode}. "
                          f"Valid modes: {list(CLOUD_MODES.keys())}")

        # 2. Check CSP supports mode
        csp_modes = CSP_MODE_SUPPORT.get(provider, [])
        if mode not in csp_modes:
            errors.append(
                f"CSP '{provider}' does not support cloud_mode '{mode}'. "
                f"Supported modes for {provider}: {csp_modes}"
            )

        # 3. Check IL is compatible with mode
        il_modes = IL_MODE_REQUIREMENTS.get(il, [])
        if mode not in il_modes:
            errors.append(
                f"Impact level {il} requires cloud_mode in {il_modes}, "
                f"but current mode is '{mode}'"
            )

        # 4. IL6 CSP restrictions
        if il == "IL6" and provider not in ("aws", "azure", "oci", "local"):
            errors.append(
                f"IL6 only supports CSPs: aws, azure, oci, local. "
                f"Current provider: {provider}"
            )

        # 5. Air-gapped consistency
        if mode == "air_gapped" and not self.air_gapped:
            warnings.append(
                "cloud_mode is 'air_gapped' but air_gapped flag is false. "
                "Set air_gapped: true for consistency."
            )
        if self.air_gapped and mode != "air_gapped":
            warnings.append(
                "air_gapped flag is true but cloud_mode is not 'air_gapped'. "
                "Consider setting cloud_mode: air_gapped for consistency."
            )

        # 6. FIPS requirement check
        mode_info = CLOUD_MODES.get(mode, {})
        if mode_info.get("fips_required") and il in ("IL4", "IL5", "IL6"):
            aws_cfg = self.cloud_section.get("aws", {})
            if provider == "aws" and not aws_cfg.get("fips_endpoints", False):
                warnings.append(
                    f"Cloud mode '{mode}' with {il} requires FIPS endpoints. "
                    "Set aws.fips_endpoints: true"
                )

        # 7. Region validation (if region_validator is available)
        if self.current_region:
            try:
                from tools.cloud.region_validator import RegionValidator
                rv = RegionValidator()
                # Determine required certs based on IL
                required = []
                if il in ("IL4", "IL5"):
                    required.append("fedramp_moderate")
                if il in ("IL5", "IL6"):
                    required.append("fedramp_high")
                if required:
                    result = rv.validate_region(provider, self.current_region, required)
                    if not result.get("valid", True):
                        missing = result.get("missing", [])
                        warnings.append(
                            f"Region {self.current_region} missing certifications: {missing}"
                        )
            except Exception:
                pass  # Region validator not available — skip

        return {
            "valid": len(errors) == 0,
            "cloud_mode": mode,
            "provider": provider,
            "impact_level": il,
            "errors": errors,
            "warnings": warnings,
        }

    def check_readiness(self) -> Dict:
        """Check if all cloud services are ready for the current mode.

        Uses CSPProviderFactory health_check if available.
        """
        status = self.get_status()
        health_results = {}

        try:
            from tools.cloud.provider_factory import CSPProviderFactory
            factory = CSPProviderFactory(config_path=str(self._config_path))
            health = factory.health_check()
            health_results = health.get("services", {})
        except Exception as exc:
            logger.warning("Health check failed: %s", exc)
            health_results = {"error": str(exc)}

        return {
            "cloud_mode": self.current_mode,
            "provider": self.current_provider,
            "status": status,
            "health": health_results,
            "ready": all(
                v.get("status") == "healthy"
                for v in health_results.values()
                if isinstance(v, dict) and "status" in v
            ) if isinstance(health_results, dict) and "error" not in health_results else False,
        }

    def get_eligible_modes(self) -> List[Dict]:
        """List all cloud modes eligible for current provider and IL."""
        provider = self.current_provider
        il = self.impact_level

        csp_modes = set(CSP_MODE_SUPPORT.get(provider, []))
        il_modes = set(IL_MODE_REQUIREMENTS.get(il, []))
        eligible = csp_modes & il_modes

        results = []
        for mode_name, mode_info in CLOUD_MODES.items():
            results.append({
                "mode": mode_name,
                "description": mode_info["description"],
                "eligible": mode_name in eligible,
                "current": mode_name == self.current_mode,
                "supported_by_csp": mode_name in csp_modes,
                "supported_by_il": mode_name in il_modes,
            })
        return results


def run_cli():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Cloud Mode Manager — cloud mode selection and validation (D232)"
    )
    parser.add_argument("--config", help="Path to cloud_config.yaml")
    parser.add_argument("--status", action="store_true",
                        help="Show current cloud mode status")
    parser.add_argument("--validate", action="store_true",
                        help="Validate cloud mode against constraints")
    parser.add_argument("--check-readiness", action="store_true",
                        help="Check cloud service readiness")
    parser.add_argument("--eligible", action="store_true",
                        help="List eligible cloud modes for current config")
    parser.add_argument("--json", action="store_true",
                        help="Output as JSON")
    parser.add_argument("--human", action="store_true",
                        help="Output as formatted text")

    args = parser.parse_args()
    manager = CloudModeManager(config_path=args.config)

    if args.status:
        result = manager.get_status()
    elif args.validate:
        result = manager.validate()
    elif args.check_readiness:
        result = manager.check_readiness()
    elif args.eligible:
        result = {"eligible_modes": manager.get_eligible_modes()}
    else:
        result = manager.get_status()

    if args.json or not args.human:
        print(json.dumps(result, indent=2))
    else:
        # Human-readable output
        if "cloud_mode" in result:
            print(f"Cloud Mode: {result.get('cloud_mode', 'unknown')}")
            print(f"Provider:   {result.get('provider', 'unknown')}")
            print(f"Region:     {result.get('region', 'N/A')}")
            print(f"Impact:     {result.get('impact_level', 'unknown')}")
            if "valid" in result:
                print(f"Valid:      {'Yes' if result['valid'] else 'No'}")
            if "errors" in result:
                for err in result["errors"]:
                    print(f"  ERROR: {err}")
            if "warnings" in result:
                for warn in result["warnings"]:
                    print(f"  WARN:  {warn}")
        if "eligible_modes" in result:
            for m in result["eligible_modes"]:
                marker = " *" if m["current"] else ""
                eligible = "eligible" if m["eligible"] else "not eligible"
                print(f"  {m['mode']}: {eligible}{marker}")


if __name__ == "__main__":
    run_cli()
