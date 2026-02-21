#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""ICDEV Module Registry — tracks installed modules in data/installation.json.

Manages the installation state of ICDEV modules, validates dependencies,
and provides upgrade path information.  Uses the installation manifest
(args/installation_manifest.yaml) as the source of truth for available
modules and their dependency graphs.

Dependencies: Python stdlib only (json, pathlib, argparse, datetime).

CLI::

    python tools/installer/module_registry.py --status              # Show installed
    python tools/installer/module_registry.py --status --json       # JSON output
    python tools/installer/module_registry.py --available           # What can be added
    python tools/installer/module_registry.py --validate            # Check consistency
    python tools/installer/module_registry.py --install core        # Install a module
    python tools/installer/module_registry.py --upgrade-path        # Show upgrade path
    python tools/installer/module_registry.py --export --json       # Full installation state
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_MANIFEST_PATH = BASE_DIR / "args" / "installation_manifest.yaml"
DEFAULT_REGISTRY_PATH = BASE_DIR / "data" / "installation.json"

# ---------------------------------------------------------------------------
# Lightweight YAML loader (stdlib only — no PyYAML hard dependency)
# ---------------------------------------------------------------------------

def _load_yaml_simple(path: Path) -> Dict[str, Any]:
    """Minimal YAML-subset loader for installation manifests.

    Handles simple key-value pairs, lists, and nested mappings that appear
    in the installation manifest.  Falls back to PyYAML when available.
    """
    try:
        import yaml  # type: ignore
        with open(path, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except ImportError:
        pass

    # Fallback: extremely simple parser for flat/list YAML
    result: Dict[str, Any] = {}
    if not path.exists():
        return result
    with open(path, "r", encoding="utf-8") as fh:
        lines = fh.readlines()

    current_key: Optional[str] = None
    current_dict: Optional[Dict] = None
    indent_stack: list = []

    for line in lines:
        stripped = line.rstrip()
        if not stripped or stripped.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        content = stripped.lstrip()

        # Top-level key: value
        if indent == 0 and ":" in content:
            key, _, val = content.partition(":")
            key = key.strip()
            val = val.strip()
            if val:
                result[key] = val
            else:
                result[key] = {}
                current_key = key
                current_dict = result[key]
            continue

        # List item
        if content.startswith("- ") and current_key is not None:
            item = content[2:].strip()
            if not isinstance(result[current_key], list):
                result[current_key] = []
            result[current_key].append(item)
            continue

        # Nested key: value
        if ":" in content and current_key is not None:
            key, _, val = content.partition(":")
            key = key.strip()
            val = val.strip()
            if isinstance(result[current_key], dict):
                result[current_key][key] = val if val else {}

    return result


# ---------------------------------------------------------------------------
# Default module definitions (used when manifest YAML does not exist yet)
# ---------------------------------------------------------------------------

DEFAULT_MODULES: Dict[str, Dict[str, Any]] = {
    "core": {
        "name": "ICDEV Core",
        "version": "1.0.0",
        "description": "Core GOTCHA framework, project management, audit trail, memory system",
        "dependencies": [],
        "category": "foundation",
    },
    "llm": {
        "name": "LLM Router",
        "version": "1.0.0",
        "description": "Vendor-agnostic LLM routing (Bedrock, Anthropic, OpenAI-compat, Ollama)",
        "dependencies": ["core"],
        "category": "foundation",
    },
    "builder": {
        "name": "Builder Agent",
        "version": "1.0.0",
        "description": "TDD code generation (RED/GREEN/REFACTOR), scaffolding, 6-language support",
        "dependencies": ["core", "llm"],
        "category": "agents",
    },
    "compliance_base": {
        "name": "Compliance Base",
        "version": "1.0.0",
        "description": "NIST 800-53 control mapping, crosswalk engine, CUI markings, base assessor",
        "dependencies": ["core"],
        "category": "compliance",
    },
    "fedramp_moderate": {
        "name": "FedRAMP Moderate",
        "version": "1.0.0",
        "description": "FedRAMP Moderate baseline assessment and reporting",
        "dependencies": ["compliance_base"],
        "category": "compliance",
    },
    "fedramp_high": {
        "name": "FedRAMP High",
        "version": "1.0.0",
        "description": "FedRAMP High baseline assessment and reporting",
        "dependencies": ["compliance_base"],
        "category": "compliance",
    },
    "cmmc": {
        "name": "CMMC Level 2/3",
        "version": "1.0.0",
        "description": "CMMC Level 2 and Level 3 certification assessment",
        "dependencies": ["compliance_base"],
        "category": "compliance",
    },
    "cjis": {
        "name": "CJIS Security Policy",
        "version": "1.0.0",
        "description": "FBI CJIS Security Policy v5.9.4 assessment",
        "dependencies": ["compliance_base"],
        "category": "compliance",
    },
    "hipaa": {
        "name": "HIPAA Security Rule",
        "version": "1.0.0",
        "description": "HIPAA Security Rule (45 CFR 164) assessment",
        "dependencies": ["compliance_base"],
        "category": "compliance",
    },
    "hitrust": {
        "name": "HITRUST CSF v11",
        "version": "1.0.0",
        "description": "HITRUST CSF v11 certification assessment",
        "dependencies": ["compliance_base"],
        "category": "compliance",
    },
    "soc2": {
        "name": "SOC 2 Type II",
        "version": "1.0.0",
        "description": "SOC 2 Type II Trust Service Criteria assessment",
        "dependencies": ["compliance_base"],
        "category": "compliance",
    },
    "pci_dss": {
        "name": "PCI DSS v4.0",
        "version": "1.0.0",
        "description": "PCI DSS v4.0 assessment for payment card data",
        "dependencies": ["compliance_base"],
        "category": "compliance",
    },
    "iso27001": {
        "name": "ISO/IEC 27001:2022",
        "version": "1.0.0",
        "description": "ISO 27001 international hub assessment with NIST bridge",
        "dependencies": ["compliance_base"],
        "category": "compliance",
    },
    "fips_199_200": {
        "name": "FIPS 199/200 Categorization",
        "version": "1.0.0",
        "description": "Security categorization with SP 800-60 types and CNSSI 1253",
        "dependencies": ["compliance_base"],
        "category": "compliance",
    },
    "oscal": {
        "name": "OSCAL Generation",
        "version": "1.0.0",
        "description": "OSCAL SSP/component/assessment generation",
        "dependencies": ["compliance_base"],
        "category": "compliance",
    },
    "emass": {
        "name": "eMASS Integration",
        "version": "1.0.0",
        "description": "eMASS sync and export for DoD RMF workflow",
        "dependencies": ["compliance_base"],
        "category": "compliance",
    },
    "cato": {
        "name": "cATO Monitoring",
        "version": "1.0.0",
        "description": "Continuous ATO evidence monitoring and scheduling",
        "dependencies": ["compliance_base"],
        "category": "compliance",
    },
    "cssp": {
        "name": "DoD CSSP (DI 8530.01)",
        "version": "1.0.0",
        "description": "CSSP assessment, incident response plan, SIEM config, evidence collection",
        "dependencies": ["compliance_base"],
        "category": "compliance",
    },
    "sbd_ivv": {
        "name": "Secure by Design + IV&V",
        "version": "1.0.0",
        "description": "CISA Secure by Design assessment and IEEE 1012 IV&V certification",
        "dependencies": ["compliance_base"],
        "category": "compliance",
    },
    "security": {
        "name": "Security Agent",
        "version": "1.0.0",
        "description": "SAST, dependency audit, secret detection, container scanning",
        "dependencies": ["core"],
        "category": "agents",
    },
    "infra": {
        "name": "Infrastructure Agent",
        "version": "1.0.0",
        "description": "Terraform, Ansible, K8s deployment, pipeline generation",
        "dependencies": ["core"],
        "category": "agents",
    },
    "mbse": {
        "name": "MBSE Agent",
        "version": "1.0.0",
        "description": "SysML/DOORS NG integration, digital thread, model-code sync",
        "dependencies": ["core", "compliance_base"],
        "category": "agents",
    },
    "modernization": {
        "name": "Modernization Agent",
        "version": "1.0.0",
        "description": "Legacy analysis, 7R assessment, migration planning",
        "dependencies": ["core", "compliance_base"],
        "category": "agents",
    },
    "requirements": {
        "name": "Requirements Analyst Agent",
        "version": "1.0.0",
        "description": "RICOAS conversational intake, gap detection, SAFe decomposition",
        "dependencies": ["core", "llm"],
        "category": "agents",
    },
    "supply_chain": {
        "name": "Supply Chain Agent",
        "version": "1.0.0",
        "description": "Dependency graph, SBOM aggregation, ISA lifecycle, CVE triage",
        "dependencies": ["core", "compliance_base"],
        "category": "agents",
    },
    "simulation": {
        "name": "Simulation Agent",
        "version": "1.0.0",
        "description": "Digital Program Twin, Monte Carlo, COA generation",
        "dependencies": ["core", "llm"],
        "category": "agents",
    },
    "devsecops": {
        "name": "DevSecOps & ZTA Agent",
        "version": "1.0.0",
        "description": "DevSecOps profiles, ZTA maturity, policy-as-code, service mesh",
        "dependencies": ["core", "compliance_base", "security"],
        "category": "agents",
    },
    "gateway": {
        "name": "Remote Command Gateway",
        "version": "1.0.0",
        "description": "Messaging channel integration, 8-gate security chain, IL-aware filtering",
        "dependencies": ["core"],
        "category": "agents",
    },
    "dashboard": {
        "name": "Dashboard",
        "version": "1.0.0",
        "description": "Flask web UI with auth, RBAC, activity feed, usage tracking",
        "dependencies": ["core"],
        "category": "ui",
    },
    "saas": {
        "name": "SaaS Multi-Tenancy",
        "version": "1.0.0",
        "description": "API gateway, per-tenant DB isolation, subscription tiers, Helm chart",
        "dependencies": ["core", "dashboard"],
        "category": "platform",
    },
    "marketplace": {
        "name": "GOTCHA Marketplace",
        "version": "1.0.0",
        "description": "Federated asset marketplace with 7-gate security pipeline",
        "dependencies": ["core", "saas"],
        "category": "platform",
    },
    "mosa": {
        "name": "DoD MOSA",
        "version": "1.0.0",
        "description": "Modular Open Systems Approach assessment and enforcement",
        "dependencies": ["compliance_base"],
        "category": "compliance",
    },
}


# ---------------------------------------------------------------------------
# ModuleRegistry
# ---------------------------------------------------------------------------

class ModuleRegistry:
    """Tracks installed ICDEV modules in a JSON registry file.

    Args:
        manifest_path: Path to the installation manifest YAML.
        registry_path: Path to the installation registry JSON.
    """

    def __init__(
        self,
        manifest_path: Optional[Path] = None,
        registry_path: Optional[Path] = None,
    ) -> None:
        self.manifest_path = manifest_path or DEFAULT_MANIFEST_PATH
        self.registry_path = registry_path or DEFAULT_REGISTRY_PATH
        self._modules = self._load_manifest()
        self._registry = self._load_registry()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_manifest(self) -> Dict[str, Dict[str, Any]]:
        """Load module definitions from YAML manifest, falling back to defaults."""
        if self.manifest_path.exists():
            data = _load_yaml_simple(self.manifest_path)
            modules = data.get("modules", {})
            if modules:
                return modules
        return dict(DEFAULT_MODULES)

    def _load_registry(self) -> Dict[str, Any]:
        """Load the installation registry from JSON."""
        if self.registry_path.exists():
            with open(self.registry_path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        return self._default_registry()

    def _default_registry(self) -> Dict[str, Any]:
        """Return a fresh registry structure."""
        now = datetime.now(timezone.utc).isoformat()
        return {
            "installed_at": now,
            "last_updated": now,
            "profile": "default",
            "platform": platform.system().lower(),
            "os": platform.system().lower(),
            "modules": {},
            "compliance_posture": [],
            "cui_enabled": False,
        }

    def _save_registry(self) -> None:
        """Persist registry to disk."""
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        self._registry["last_updated"] = datetime.now(timezone.utc).isoformat()
        with open(self.registry_path, "w", encoding="utf-8") as fh:
            json.dump(self._registry, fh, indent=2, default=str)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_installed(self) -> Dict[str, Any]:
        """Return installed modules.

        Returns:
            Dict mapping module_id to installation metadata.
        """
        return dict(self._registry.get("modules", {}))

    def check_dependencies(self, module_id: str) -> Dict[str, Any]:
        """Check whether all dependencies for *module_id* are satisfied.

        Returns:
            Dict with ``satisfied`` (bool), ``missing`` (list), and ``module_id``.
        """
        module_def = self._modules.get(module_id)
        if module_def is None:
            return {
                "module_id": module_id,
                "satisfied": False,
                "missing": [],
                "error": f"Unknown module: {module_id}",
            }

        deps = module_def.get("dependencies", [])
        installed = self._registry.get("modules", {})
        missing = [d for d in deps if d not in installed or not installed[d].get("installed")]

        return {
            "module_id": module_id,
            "satisfied": len(missing) == 0,
            "missing": missing,
            "required": deps,
        }

    def install_module(self, module_id: str) -> Dict[str, Any]:
        """Mark a module as installed after validating dependencies.

        Returns:
            Dict with installation result including ``success`` flag.
        """
        module_def = self._modules.get(module_id)
        if module_def is None:
            return {
                "success": False,
                "module_id": module_id,
                "error": f"Unknown module: {module_id}",
            }

        dep_check = self.check_dependencies(module_id)
        if not dep_check["satisfied"]:
            return {
                "success": False,
                "module_id": module_id,
                "error": "Unsatisfied dependencies",
                "missing_dependencies": dep_check["missing"],
            }

        now = datetime.now(timezone.utc).isoformat()
        version = module_def.get("version", "1.0.0")

        modules = self._registry.setdefault("modules", {})
        modules[module_id] = {
            "installed": True,
            "version": version,
            "installed_at": now,
            "name": module_def.get("name", module_id),
        }

        self._save_registry()

        return {
            "success": True,
            "module_id": module_id,
            "version": version,
            "installed_at": now,
            "name": module_def.get("name", module_id),
        }

    def get_available(self) -> List[Dict[str, Any]]:
        """Return modules whose dependencies are met but are not yet installed.

        Returns:
            List of dicts with module metadata.
        """
        installed = self._registry.get("modules", {})
        available: List[Dict[str, Any]] = []

        for mod_id, mod_def in self._modules.items():
            if mod_id in installed and installed[mod_id].get("installed"):
                continue
            deps = mod_def.get("dependencies", [])
            deps_met = all(
                d in installed and installed[d].get("installed")
                for d in deps
            )
            if deps_met:
                available.append({
                    "module_id": mod_id,
                    "name": mod_def.get("name", mod_id),
                    "version": mod_def.get("version", "1.0.0"),
                    "description": mod_def.get("description", ""),
                    "category": mod_def.get("category", ""),
                    "dependencies": deps,
                })

        return available

    def get_upgrade_path(self) -> List[Dict[str, Any]]:
        """Return modules in the manifest that are not yet installed.

        Returns:
            List of dicts ordered by dependency depth (install order).
        """
        installed = set(
            k for k, v in self._registry.get("modules", {}).items()
            if v.get("installed")
        )
        not_installed: List[Dict[str, Any]] = []

        for mod_id, mod_def in self._modules.items():
            if mod_id not in installed:
                not_installed.append({
                    "module_id": mod_id,
                    "name": mod_def.get("name", mod_id),
                    "version": mod_def.get("version", "1.0.0"),
                    "description": mod_def.get("description", ""),
                    "category": mod_def.get("category", ""),
                    "dependencies": mod_def.get("dependencies", []),
                    "deps_met": all(d in installed for d in mod_def.get("dependencies", [])),
                })

        # Sort: deps-met first, then by number of dependencies (simpler first)
        not_installed.sort(key=lambda m: (not m["deps_met"], len(m["dependencies"])))
        return not_installed

    def export_config(self) -> Dict[str, Any]:
        """Return the full installation state.

        Returns:
            Complete registry dict.
        """
        return dict(self._registry)

    def validate(self) -> Dict[str, Any]:
        """Check installed modules against manifest for consistency.

        Returns:
            Dict with ``valid`` flag, ``issues`` list, and ``summary``.
        """
        issues: List[str] = []
        installed = self._registry.get("modules", {})

        # Check for modules installed but not in manifest
        for mod_id in installed:
            if mod_id not in self._modules:
                issues.append(f"Installed module '{mod_id}' not found in manifest")

        # Check dependency integrity for installed modules
        for mod_id, mod_info in installed.items():
            if not mod_info.get("installed"):
                continue
            mod_def = self._modules.get(mod_id)
            if mod_def is None:
                continue
            for dep in mod_def.get("dependencies", []):
                if dep not in installed or not installed[dep].get("installed"):
                    issues.append(
                        f"Module '{mod_id}' depends on '{dep}' which is not installed"
                    )

        # Check for version mismatches
        for mod_id, mod_info in installed.items():
            if not mod_info.get("installed"):
                continue
            mod_def = self._modules.get(mod_id)
            if mod_def and mod_info.get("version") != mod_def.get("version"):
                issues.append(
                    f"Module '{mod_id}' version mismatch: installed={mod_info.get('version')}, "
                    f"manifest={mod_def.get('version')}"
                )

        total_installed = sum(1 for v in installed.values() if v.get("installed"))
        total_available = len(self._modules)

        return {
            "valid": len(issues) == 0,
            "issues": issues,
            "total_installed": total_installed,
            "total_available": total_available,
            "coverage": f"{total_installed}/{total_available}",
            "compliance_posture": self._registry.get("compliance_posture", []),
            "cui_enabled": self._registry.get("cui_enabled", False),
        }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _format_human_status(registry: ModuleRegistry) -> str:
    """Render installed modules as human-readable table."""
    installed = registry.get_installed()
    lines = [
        "=" * 65,
        "  ICDEV Module Registry — Installed Modules",
        "=" * 65,
        "",
    ]
    if not installed:
        lines.append("  (no modules installed)")
    else:
        lines.append(f"  {'Module':<25} {'Version':<10} {'Installed At'}")
        lines.append(f"  {'-' * 25} {'-' * 10} {'-' * 25}")
        for mod_id, info in sorted(installed.items()):
            if info.get("installed"):
                name = info.get("name", mod_id)
                ver = info.get("version", "?")
                ts = info.get("installed_at", "?")
                lines.append(f"  {name:<25} {ver:<10} {ts}")
    lines.append("")
    config = registry.export_config()
    lines.append(f"  Profile:    {config.get('profile', 'default')}")
    lines.append(f"  Platform:   {config.get('platform', '?')}")
    lines.append(f"  CUI:        {'enabled' if config.get('cui_enabled') else 'disabled'}")
    posture = config.get("compliance_posture", [])
    lines.append(f"  Posture:    {', '.join(posture) if posture else '(none)'}")
    return "\n".join(lines)


def _format_human_available(registry: ModuleRegistry) -> str:
    """Render available modules as human-readable list."""
    available = registry.get_available()
    lines = [
        "=" * 65,
        "  ICDEV Module Registry — Available Modules",
        "=" * 65,
        "",
    ]
    if not available:
        lines.append("  (all modules installed or dependencies unmet)")
    else:
        for mod in available:
            deps = ", ".join(mod["dependencies"]) if mod["dependencies"] else "(none)"
            lines.append(f"  {mod['module_id']:<25} {mod['name']}")
            lines.append(f"    {mod['description']}")
            lines.append(f"    deps: {deps}")
            lines.append("")
    return "\n".join(lines)


def _format_human_validate(result: Dict) -> str:
    """Render validation result as human-readable text."""
    lines = [
        "=" * 65,
        "  ICDEV Module Registry — Validation",
        "=" * 65,
        "",
        f"  Status:     {'VALID' if result['valid'] else 'ISSUES FOUND'}",
        f"  Installed:  {result['coverage']}",
        f"  CUI:        {'enabled' if result.get('cui_enabled') else 'disabled'}",
        "",
    ]
    if result["issues"]:
        lines.append("  Issues:")
        for issue in result["issues"]:
            lines.append(f"    - {issue}")
    else:
        lines.append("  No issues found.")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="ICDEV Module Registry — track and manage installed modules"
    )
    parser.add_argument(
        "--status", action="store_true",
        help="Show installed modules",
    )
    parser.add_argument(
        "--available", action="store_true",
        help="Show modules available to install (deps satisfied)",
    )
    parser.add_argument(
        "--validate", action="store_true",
        help="Check installed modules against manifest for consistency",
    )
    parser.add_argument(
        "--install", metavar="MODULE_ID",
        help="Install a module by ID",
    )
    parser.add_argument(
        "--upgrade-path", action="store_true",
        help="Show modules not yet installed (upgrade path)",
    )
    parser.add_argument(
        "--export", action="store_true",
        help="Export full installation state",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output as JSON",
    )
    parser.add_argument(
        "--human", action="store_true",
        help="Human-friendly colorized terminal output",
    )
    parser.add_argument(
        "--manifest-path", type=Path, default=None,
        help="Path to installation manifest YAML",
    )
    parser.add_argument(
        "--registry-path", type=Path, default=None,
        help="Path to installation registry JSON",
    )

    args = parser.parse_args()
    use_json = args.json

    registry = ModuleRegistry(
        manifest_path=args.manifest_path,
        registry_path=args.registry_path,
    )

    try:
        if args.install:
            result = registry.install_module(args.install)
            if use_json:
                print(json.dumps(result, indent=2, default=str))
            else:
                if result["success"]:
                    print(f"Installed: {result['name']} v{result['version']}")
                else:
                    print(f"ERROR: {result['error']}", file=sys.stderr)
                    if result.get("missing_dependencies"):
                        print(
                            f"  Missing deps: {', '.join(result['missing_dependencies'])}",
                            file=sys.stderr,
                        )
                    sys.exit(1)

        elif args.available:
            available = registry.get_available()
            if use_json:
                print(json.dumps(available, indent=2, default=str))
            else:
                print(_format_human_available(registry))

        elif args.validate:
            result = registry.validate()
            if use_json:
                print(json.dumps(result, indent=2, default=str))
            else:
                print(_format_human_validate(result))

        elif args.upgrade_path:
            path = registry.get_upgrade_path()
            if use_json:
                print(json.dumps(path, indent=2, default=str))
            else:
                print("=" * 65)
                print("  ICDEV Module Registry — Upgrade Path")
                print("=" * 65)
                print()
                for mod in path:
                    ready = "READY" if mod["deps_met"] else "BLOCKED"
                    print(f"  [{ready}] {mod['module_id']:<25} {mod['name']}")
                    if not mod["deps_met"]:
                        print(f"           needs: {', '.join(mod['dependencies'])}")

        elif args.export:
            config = registry.export_config()
            if use_json:
                print(json.dumps(config, indent=2, default=str))
            else:
                print(json.dumps(config, indent=2, default=str))

        else:
            # Default: --status
            installed = registry.get_installed()
            if use_json:
                print(json.dumps({
                    "installed": installed,
                    "total": sum(1 for v in installed.values() if v.get("installed")),
                    "profile": registry.export_config().get("profile", "default"),
                    "compliance_posture": registry.export_config().get("compliance_posture", []),
                    "cui_enabled": registry.export_config().get("cui_enabled", False),
                }, indent=2, default=str))
            else:
                print(_format_human_status(registry))

    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
