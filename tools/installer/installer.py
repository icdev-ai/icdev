#!/usr/bin/env python3
"""ICDEV Modular Installer — central CLI for configuring and installing ICDEV.

Supports three modes of operation:

1. **Interactive wizard** (``--interactive``):
   Guided 6-step terminal wizard that walks users through organization type,
   team size, compliance posture, deployment target, and optional modules.

2. **Profile-based** (``--profile <name>``):
   Non-interactive installation driven by a deployment profile from
   ``args/deployment_profiles.yaml``, with optional CLI overrides for
   compliance frameworks and platform.

3. **Upgrade / add-module** (``--upgrade``, ``--add-module``, ``--add-compliance``,
   ``--status``):
   Post-install operations to extend an existing installation with additional
   modules or compliance frameworks.

All modes support ``--dry-run`` to preview changes without executing them,
``--json`` for machine-readable output, and ``--human`` for colorized terminal
output.

Dependencies: Python stdlib only (json, pathlib, argparse, sqlite3, re,
datetime).  PyYAML used when available but not required.

CLI::

    # Interactive wizard
    python tools/installer/installer.py --interactive

    # Profile-based
    python tools/installer/installer.py --profile dod_team
    python tools/installer/installer.py --profile dod_team --compliance fedramp_high,cmmc --platform k8s

    # Upgrade / add
    python tools/installer/installer.py --upgrade
    python tools/installer/installer.py --add-module marketplace
    python tools/installer/installer.py --add-compliance hipaa
    python tools/installer/installer.py --status
    python tools/installer/installer.py --status --json

    # Dry run
    python tools/installer/installer.py --profile dod_team --dry-run
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "icdev.db"
MANIFEST_PATH = BASE_DIR / "args" / "installation_manifest.yaml"
PROFILES_PATH = BASE_DIR / "args" / "deployment_profiles.yaml"
CUI_MARKINGS_PATH = BASE_DIR / "args" / "cui_markings.yaml"
REGISTRY_PATH = DATA_DIR / "installation.json"

# Ensure BASE_DIR is on sys.path so that ``from tools.xxx import ...`` works
# when invoked directly (consistent with other ICDEV CLI tools).
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# ---------------------------------------------------------------------------
# Lazy imports — keep module-level import list minimal so that the installer
# can be imported even when sibling modules are not yet installed.
# ---------------------------------------------------------------------------


def _get_module_registry():
    """Import and return the ModuleRegistry class."""
    from tools.installer.module_registry import ModuleRegistry
    return ModuleRegistry


def _get_compliance_configurator():
    """Import and return the ComplianceConfigurator class."""
    from tools.installer.compliance_configurator import ComplianceConfigurator
    return ComplianceConfigurator


# ---------------------------------------------------------------------------
# Lightweight YAML loader (mirrors module_registry.py pattern)
# ---------------------------------------------------------------------------

def _load_yaml(path: Path) -> Dict[str, Any]:
    """Load a YAML file, falling back to a minimal parser if PyYAML is absent."""
    try:
        import yaml  # type: ignore
        with open(path, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except ImportError:
        pass

    # Minimal fallback — sufficient for the flat/nested structures in the
    # deployment_profiles and installation_manifest files.
    result: Dict[str, Any] = {}
    if not path.exists():
        return result
    with open(path, "r", encoding="utf-8") as fh:
        lines = fh.readlines()

    stack: List[Tuple[int, str, Dict]] = []  # (indent, key, dict_ref)
    current: Dict[str, Any] = result

    for line in lines:
        stripped = line.rstrip()
        if not stripped or stripped.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        content = stripped.lstrip()

        # Pop stack to correct nesting level
        while stack and stack[-1][0] >= indent:
            stack.pop()
        if stack:
            current = stack[-1][2]
        else:
            current = result

        # List item
        if content.startswith("- "):
            item = content[2:].strip()
            # Find parent key
            if stack:
                parent_key = stack[-1][1]
                parent_dict = stack[-2][2] if len(stack) > 1 else result
                existing = parent_dict.get(parent_key)
                if not isinstance(existing, list):
                    parent_dict[parent_key] = []
                parent_dict[parent_key].append(item)
            continue

        # Key: value pair
        if ":" in content:
            key, _, val = content.partition(":")
            key = key.strip().strip('"').strip("'")
            val = val.strip().strip('"').strip("'")

            if val.startswith("[") and val.endswith("]"):
                # Inline list: [a, b, c]
                items = [
                    v.strip().strip('"').strip("'")
                    for v in val[1:-1].split(",")
                    if v.strip()
                ]
                current[key] = items
            elif val:
                # Scalar value
                if val.lower() == "true":
                    current[key] = True
                elif val.lower() == "false":
                    current[key] = False
                else:
                    current[key] = val
            else:
                # Nested mapping — create empty dict and push to stack
                current[key] = {}
                stack.append((indent, key, current[key]))
                current = current[key]
                continue

            # Non-nested key — still push for potential list children
            stack.append((indent, key, current))

    return result


# ---------------------------------------------------------------------------
# ModularDBInitializer
# ---------------------------------------------------------------------------

class ModularDBInitializer:
    """Selectively initialize database tables based on installed modules.

    Instead of running the full 143-table init_icdev_db.py monolith, this
    class reads the ``db_table_groups`` mapping from the installation manifest
    and creates only the tables required by the selected modules.

    This is strictly additive — existing tables are never dropped.
    """

    def __init__(
        self,
        manifest_path: Optional[Path] = None,
        db_path: Optional[Path] = None,
    ) -> None:
        self.manifest_path = manifest_path or MANIFEST_PATH
        self.db_path = db_path or DB_PATH
        self._manifest = _load_yaml(self.manifest_path) if self.manifest_path.exists() else {}
        self._schema_sql: Optional[str] = None

    # ------------------------------------------------------------------
    # Schema SQL extraction
    # ------------------------------------------------------------------

    def _load_schema_sql(self) -> str:
        """Load the full SCHEMA_SQL string from init_icdev_db.py."""
        if self._schema_sql is not None:
            return self._schema_sql

        init_path = BASE_DIR / "tools" / "db" / "init_icdev_db.py"
        if not init_path.exists():
            self._schema_sql = ""
            return self._schema_sql

        # Import SCHEMA_SQL from the module
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location("init_icdev_db", str(init_path))
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)  # type: ignore[union-attr]
                self._schema_sql = getattr(mod, "SCHEMA_SQL", "")
            else:
                self._schema_sql = ""
        except Exception:
            self._schema_sql = ""

        return self._schema_sql

    def _extract_create_statements(self, table_names: List[str]) -> List[str]:
        """Extract CREATE TABLE IF NOT EXISTS statements for specific tables.

        Parses the full SCHEMA_SQL to find the complete CREATE TABLE statement
        for each requested table name.
        """
        schema = self._load_schema_sql()
        if not schema:
            return []

        statements: List[str] = []
        # Split on CREATE TABLE boundaries
        pattern = re.compile(
            r"(CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+(\w+)\s*\(.*?\);)",
            re.DOTALL | re.IGNORECASE,
        )

        for match in pattern.finditer(schema):
            full_stmt = match.group(1)
            tbl_name = match.group(2)
            if tbl_name in table_names:
                statements.append(full_stmt)

        # Also extract CREATE INDEX statements for matched tables
        idx_pattern = re.compile(
            r"(CREATE\s+INDEX\s+IF\s+NOT\s+EXISTS\s+\w+\s+ON\s+(\w+)\s*\(.*?\);)",
            re.DOTALL | re.IGNORECASE,
        )
        for match in idx_pattern.finditer(schema):
            idx_stmt = match.group(1)
            idx_table = match.group(2)
            if idx_table in table_names:
                statements.append(idx_stmt)

        return statements

    def _resolve_table_groups(self, module_ids: List[str]) -> List[str]:
        """Resolve module IDs to a deduplicated list of table names.

        Uses the ``db_table_groups`` section of the installation manifest to
        map module -> group IDs -> table names.
        """
        modules_cfg = self._manifest.get("modules", {})
        table_groups_cfg = self._manifest.get("db_table_groups", {})

        # Collect all group IDs needed
        group_ids: List[str] = []
        for mod_id in module_ids:
            mod_def = modules_cfg.get(mod_id, {})
            groups = mod_def.get("db_table_groups", [])
            if isinstance(groups, str):
                groups = [groups]
            for g in groups:
                if g and g not in group_ids:
                    group_ids.append(g)

        # Resolve group IDs to table names
        table_names: List[str] = []
        for gid in group_ids:
            group_def = table_groups_cfg.get(gid, {})
            tables = group_def.get("tables", [])
            if isinstance(tables, str):
                tables = [tables]
            for t in tables:
                if t and t not in table_names:
                    table_names.append(t)

        return table_names

    def initialize(
        self,
        module_ids: List[str],
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Create database tables required by the given modules.

        Args:
            module_ids: List of module IDs to initialize tables for.
            dry_run: If True, return the plan without executing.

        Returns:
            Dict with ``tables_created``, ``sql_statements`` (dry-run),
            and ``db_path``.
        """
        table_names = self._resolve_table_groups(module_ids)

        if not table_names:
            return {
                "tables_created": [],
                "table_count": 0,
                "db_path": str(self.db_path),
                "dry_run": dry_run,
            }

        statements = self._extract_create_statements(table_names)

        if dry_run:
            return {
                "tables_planned": table_names,
                "table_count": len(table_names),
                "sql_statement_count": len(statements),
                "db_path": str(self.db_path),
                "dry_run": True,
            }

        # Execute the SQL
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path))
        tables_created: List[str] = []
        try:
            for stmt in statements:
                try:
                    conn.execute(stmt)
                except sqlite3.OperationalError:
                    pass  # Table/index already exists or other benign error

            conn.commit()

            # Verify which tables now exist
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            existing_tables = {row[0] for row in cursor.fetchall()}
            tables_created = [t for t in table_names if t in existing_tables]
        finally:
            conn.close()

        return {
            "tables_created": tables_created,
            "table_count": len(tables_created),
            "db_path": str(self.db_path),
            "dry_run": False,
        }


# ---------------------------------------------------------------------------
# Profile and manifest loaders
# ---------------------------------------------------------------------------

def _load_profiles() -> Dict[str, Any]:
    """Load deployment profiles from YAML."""
    if not PROFILES_PATH.exists():
        return {}
    return _load_yaml(PROFILES_PATH)


def _load_manifest() -> Dict[str, Any]:
    """Load installation manifest from YAML."""
    if not MANIFEST_PATH.exists():
        return {}
    return _load_yaml(MANIFEST_PATH)


def _get_all_module_ids(manifest: Dict[str, Any]) -> List[str]:
    """Return all module IDs from the manifest."""
    modules = manifest.get("modules", {})
    return list(modules.keys())


def _get_all_compliance_ids(manifest: Dict[str, Any]) -> List[str]:
    """Return module IDs that are compliance posture modules."""
    modules = manifest.get("modules", {})
    return [
        mod_id for mod_id, mod_def in modules.items()
        if isinstance(mod_def, dict) and mod_def.get("compliance_posture")
    ]


def _get_required_module_ids(manifest: Dict[str, Any]) -> List[str]:
    """Return module IDs that are required (always installed)."""
    modules = manifest.get("modules", {})
    return [
        mod_id for mod_id, mod_def in modules.items()
        if isinstance(mod_def, dict) and mod_def.get("required")
    ]


# ---------------------------------------------------------------------------
# Topological sort for dependency resolution
# ---------------------------------------------------------------------------

def _topological_sort(
    module_ids: List[str],
    manifest: Dict[str, Any],
) -> List[str]:
    """Resolve and topologically sort modules by their dependencies.

    Automatically includes transitive dependencies.  Returns modules in
    install order (dependencies first).

    Raises:
        ValueError: If a circular dependency is detected.
    """
    modules_cfg = manifest.get("modules", {})

    # Build full set including transitive deps
    required: Set[str] = set()
    queue = list(module_ids)
    while queue:
        mod_id = queue.pop(0)
        if mod_id in required:
            continue
        required.add(mod_id)
        mod_def = modules_cfg.get(mod_id, {})
        deps = mod_def.get("depends_on", []) or mod_def.get("dependencies", [])
        if isinstance(deps, str):
            deps = [deps]
        for dep in deps:
            if dep not in required:
                queue.append(dep)

    # Kahn's algorithm for topological sort
    in_degree: Dict[str, int] = {m: 0 for m in required}
    adj: Dict[str, List[str]] = {m: [] for m in required}

    for mod_id in required:
        mod_def = modules_cfg.get(mod_id, {})
        deps = mod_def.get("depends_on", []) or mod_def.get("dependencies", [])
        if isinstance(deps, str):
            deps = [deps]
        for dep in deps:
            if dep in required:
                adj[dep].append(mod_id)
                in_degree[mod_id] += 1

    queue = [m for m in required if in_degree[m] == 0]
    queue.sort()  # deterministic ordering
    sorted_list: List[str] = []

    while queue:
        node = queue.pop(0)
        sorted_list.append(node)
        for neighbor in sorted(adj.get(node, [])):
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    if len(sorted_list) != len(required):
        missing = required - set(sorted_list)
        raise ValueError(
            f"Circular dependency detected among modules: {', '.join(sorted(missing))}"
        )

    return sorted_list


# ---------------------------------------------------------------------------
# CUI markings update
# ---------------------------------------------------------------------------

def _update_cui_markings(enabled: bool, dry_run: bool = False) -> Dict[str, Any]:
    """Update the CUI markings YAML to enable or disable CUI banners.

    This modifies the ``enabled`` field in ``args/cui_markings.yaml``.  If the
    file does not have an ``enabled`` field, one is prepended.
    """
    if dry_run:
        return {"cui_enabled": enabled, "dry_run": True, "path": str(CUI_MARKINGS_PATH)}

    if not CUI_MARKINGS_PATH.exists():
        return {"cui_enabled": enabled, "error": "cui_markings.yaml not found"}

    with open(CUI_MARKINGS_PATH, "r", encoding="utf-8") as fh:
        content = fh.read()

    # Check if an `enabled:` key already exists
    enabled_pattern = re.compile(r"^enabled:\s*(true|false)\s*$", re.MULTILINE | re.IGNORECASE)
    new_value = "true" if enabled else "false"

    if enabled_pattern.search(content):
        content = enabled_pattern.sub(f"enabled: {new_value}", content)
    else:
        # Prepend at the top (after any initial comments)
        lines = content.split("\n")
        insert_idx = 0
        for i, line in enumerate(lines):
            if line.strip() and not line.strip().startswith("#"):
                insert_idx = i
                break
        lines.insert(insert_idx, f"enabled: {new_value}\n")
        content = "\n".join(lines)

    with open(CUI_MARKINGS_PATH, "w", encoding="utf-8") as fh:
        fh.write(content)

    return {"cui_enabled": enabled, "dry_run": False, "path": str(CUI_MARKINGS_PATH)}


# ---------------------------------------------------------------------------
# Installation engine
# ---------------------------------------------------------------------------

def install(
    modules: List[str],
    compliance_frameworks: List[str],
    profile_name: str,
    platform_target: str,
    team_size: str = "",
    cui_enabled: bool = False,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Execute the full installation sequence.

    Steps:
        1. Resolve all module dependencies (topological sort).
        2. Create ``data/`` directory if needed.
        3. Initialize database with required table groups.
        4. Update ``data/installation.json`` via ModuleRegistry.
        5. Update ``args/cui_markings.yaml``.
        6. Return summary.

    Args:
        modules: List of module IDs to install.
        compliance_frameworks: Compliance framework IDs to activate.
        profile_name: Profile identifier used.
        platform_target: Deployment platform (docker, k8s, etc.).
        team_size: Team size range string.
        cui_enabled: Whether CUI markings should be enabled.
        dry_run: If True, preview without executing.

    Returns:
        Dict with full installation summary.
    """
    manifest = _load_manifest()
    now = datetime.now(timezone.utc).isoformat()

    # Merge compliance framework modules into the module list
    ComplianceConfiguratorCls = _get_compliance_configurator()
    configurator = ComplianceConfiguratorCls()
    compliance_config = configurator.configure_posture(compliance_frameworks)
    compliance_modules = compliance_config.get("required_modules", [])

    # Combine explicit modules + compliance modules + always-required modules
    required_modules = _get_required_module_ids(manifest)
    all_module_ids: List[str] = list(required_modules)
    for m in modules:
        if m not in all_module_ids:
            all_module_ids.append(m)
    for m in compliance_modules:
        if m not in all_module_ids:
            all_module_ids.append(m)

    # Resolve dependencies and topological sort
    try:
        sorted_modules = _topological_sort(all_module_ids, manifest)
    except ValueError as e:
        return {
            "success": False,
            "error": str(e),
            "timestamp": now,
        }

    # Validate that all modules exist in manifest
    modules_cfg = manifest.get("modules", {})
    unknown = [m for m in sorted_modules if m not in modules_cfg]
    if unknown:
        return {
            "success": False,
            "error": f"Unknown modules: {', '.join(unknown)}",
            "timestamp": now,
        }

    if dry_run:
        # Build table list for preview
        db_init = ModularDBInitializer()
        db_result = db_init.initialize(sorted_modules, dry_run=True)
        return {
            "success": True,
            "dry_run": True,
            "profile": profile_name,
            "platform": platform_target,
            "team_size": team_size,
            "modules": sorted_modules,
            "module_count": len(sorted_modules),
            "compliance_frameworks": compliance_frameworks,
            "cui_enabled": cui_enabled or compliance_config.get("cui_enabled", False),
            "impact_level": compliance_config.get("impact_level", "IL2"),
            "db_tables_planned": db_result.get("tables_planned", []),
            "db_table_count": db_result.get("table_count", 0),
            "security_gate_overrides": compliance_config.get("security_gate_overrides", {}),
            "timestamp": now,
        }

    # Step 1: Create data directory
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Step 2: Initialize database tables
    db_init = ModularDBInitializer()
    db_result = db_init.initialize(sorted_modules, dry_run=False)

    # Step 3: Update installation.json via ModuleRegistry
    ModuleRegistryCls = _get_module_registry()
    registry = ModuleRegistryCls()
    install_results: List[Dict[str, Any]] = []
    for mod_id in sorted_modules:
        result = registry.install_module(mod_id)
        install_results.append(result)

    # Update registry metadata
    registry_data = registry.export_config()
    registry_data["profile"] = profile_name
    registry_data["platform"] = platform_target
    registry_data["team_size"] = team_size
    registry_data["compliance_posture"] = compliance_frameworks
    effective_cui = cui_enabled or compliance_config.get("cui_enabled", False)
    registry_data["cui_enabled"] = effective_cui
    registry_data["impact_level"] = compliance_config.get("impact_level", "IL2")
    registry_data["security_gate_overrides"] = compliance_config.get(
        "security_gate_overrides", {}
    )
    registry_data["last_updated"] = now

    # Persist updated registry
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(REGISTRY_PATH, "w", encoding="utf-8") as fh:
        json.dump(registry_data, fh, indent=2, default=str)

    # Step 4: Update CUI markings
    cui_result = _update_cui_markings(effective_cui)

    # Build module summary
    module_summary: List[Dict[str, str]] = []
    for mod_id in sorted_modules:
        mod_def = modules_cfg.get(mod_id, {})
        module_summary.append({
            "id": mod_id,
            "name": mod_def.get("name", mod_id) if isinstance(mod_def, dict) else mod_id,
            "category": mod_def.get("category", "") if isinstance(mod_def, dict) else "",
        })

    failed = [r for r in install_results if not r.get("success")]

    return {
        "success": len(failed) == 0,
        "dry_run": False,
        "profile": profile_name,
        "platform": platform_target,
        "team_size": team_size,
        "modules_installed": sorted_modules,
        "module_count": len(sorted_modules),
        "module_summary": module_summary,
        "compliance_frameworks": compliance_frameworks,
        "cui_enabled": effective_cui,
        "impact_level": compliance_config.get("impact_level", "IL2"),
        "db_tables_created": db_result.get("tables_created", []),
        "db_table_count": db_result.get("table_count", 0),
        "security_gate_overrides": compliance_config.get("security_gate_overrides", {}),
        "failures": failed if failed else [],
        "registry_path": str(REGISTRY_PATH),
        "db_path": str(DB_PATH),
        "timestamp": now,
    }


# ---------------------------------------------------------------------------
# Interactive wizard
# ---------------------------------------------------------------------------

def _prompt_choice(prompt: str, options: List[str], default: int = 0) -> int:
    """Display a numbered list and prompt for a single selection.

    Args:
        prompt: Header text for the prompt.
        options: List of option display strings.
        default: Zero-based default selection index.

    Returns:
        Zero-based index of selected option.
    """
    print(f"\n{prompt}")
    print("-" * 50)
    for i, opt in enumerate(options):
        marker = " *" if i == default else ""
        print(f"  {i + 1}. {opt}{marker}")
    print()

    while True:
        raw = input(f"Enter choice [1-{len(options)}] (default={default + 1}): ").strip()
        if not raw:
            return default
        try:
            choice = int(raw) - 1
            if 0 <= choice < len(options):
                return choice
            print(f"  Please enter a number between 1 and {len(options)}.")
        except ValueError:
            print("  Please enter a valid number.")


def _prompt_multi_choice(
    prompt: str,
    options: List[str],
    defaults: Optional[List[int]] = None,
) -> List[int]:
    """Display a numbered list and prompt for multiple selections.

    Args:
        prompt: Header text.
        options: List of option display strings.
        defaults: Zero-based indices that are pre-selected.

    Returns:
        List of zero-based indices of selected options.
    """
    if defaults is None:
        defaults = []

    print(f"\n{prompt}")
    print("-" * 50)
    for i, opt in enumerate(options):
        check = "[x]" if i in defaults else "[ ]"
        print(f"  {i + 1}. {check} {opt}")
    print()
    print("  Enter numbers separated by commas (e.g., 1,3,5)")
    print("  Press Enter to accept defaults, or 'none' to clear all.")
    print()

    while True:
        raw = input("Your selection: ").strip()
        if not raw:
            return list(defaults)
        if raw.lower() == "none":
            return []
        try:
            indices = []
            for part in raw.split(","):
                part = part.strip()
                if not part:
                    continue
                # Support ranges like 1-3
                if "-" in part:
                    start, end = part.split("-", 1)
                    for n in range(int(start), int(end) + 1):
                        idx = n - 1
                        if 0 <= idx < len(options) and idx not in indices:
                            indices.append(idx)
                else:
                    idx = int(part) - 1
                    if 0 <= idx < len(options) and idx not in indices:
                        indices.append(idx)
            return indices
        except ValueError:
            print("  Please enter valid numbers separated by commas.")


def _prompt_confirm(prompt: str, default: bool = True) -> bool:
    """Prompt for yes/no confirmation.

    Args:
        prompt: Question text.
        default: Default answer.

    Returns:
        True for yes, False for no.
    """
    suffix = "[Y/n]" if default else "[y/N]"
    while True:
        raw = input(f"\n{prompt} {suffix}: ").strip().lower()
        if not raw:
            return default
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        print("  Please enter 'y' or 'n'.")


def run_interactive(dry_run: bool = False) -> Dict[str, Any]:
    """Run the 6-step interactive installation wizard.

    Steps:
        1. Organization type (profile selection)
        2. Team size
        3. Compliance posture (multi-select)
        4. Deployment target
        5. Additional capabilities (optional modules)
        6. Review and confirm

    Returns:
        Installation result dict.
    """
    profiles_data = _load_profiles()
    manifest = _load_manifest()
    profiles = profiles_data.get("profiles", {})
    platforms = profiles_data.get("platforms", {})

    print()
    print("=" * 60)
    print("  ICDEV Modular Installer — Interactive Wizard")
    print("=" * 60)

    # ------------------------------------------------------------------
    # Step 1: Organization Type (profile selection)
    # ------------------------------------------------------------------
    profile_ids = [pid for pid in profiles.keys() if pid != "custom"]
    profile_ids.append("custom")
    profile_options = []
    for pid in profile_ids:
        pdef = profiles.get(pid, {})
        name = pdef.get("name", pid) if isinstance(pdef, dict) else pid
        desc = pdef.get("description", "") if isinstance(pdef, dict) else ""
        profile_options.append(f"{name} — {desc}" if desc else name)

    choice_idx = _prompt_choice(
        "Step 1/6: What type of organization are you?",
        profile_options,
        default=0,
    )
    selected_profile_id = profile_ids[choice_idx]
    selected_profile = profiles.get(selected_profile_id, {})
    if not isinstance(selected_profile, dict):
        selected_profile = {}

    # ------------------------------------------------------------------
    # Step 2: Team Size
    # ------------------------------------------------------------------
    team_sizes = ["1-5", "5-15", "15-50", "50-200", "200+"]
    default_team_size_idx = 0
    profile_team = selected_profile.get("team_size", "")
    for i, ts in enumerate(team_sizes):
        if ts in str(profile_team):
            default_team_size_idx = i
            break

    team_choice = _prompt_choice(
        "Step 2/6: What is your team size?",
        [
            "1-5     (solo / small team)",
            "5-15    (small team)",
            "15-50   (medium team)",
            "50-200  (large team)",
            "200+    (enterprise)",
        ],
        default=default_team_size_idx,
    )
    team_size = team_sizes[team_choice]

    # ------------------------------------------------------------------
    # Step 3: Compliance Posture
    # ------------------------------------------------------------------
    all_compliance = _get_all_compliance_ids(manifest)
    modules_cfg = manifest.get("modules", {})
    compliance_options = []
    for cid in all_compliance:
        cdef = modules_cfg.get(cid, {})
        name = cdef.get("name", cid) if isinstance(cdef, dict) else cid
        compliance_options.append(f"{cid}: {name}")

    # Pre-select from profile
    profile_compliance = selected_profile.get("default_compliance", [])
    if isinstance(profile_compliance, str):
        if profile_compliance.upper() == "ALL":
            profile_compliance = list(all_compliance)
        else:
            profile_compliance = [profile_compliance]

    default_indices = []
    for i, cid in enumerate(all_compliance):
        if cid in profile_compliance:
            default_indices.append(i)

    compliance_indices = _prompt_multi_choice(
        "Step 3/6: Which compliance frameworks do you need?",
        compliance_options,
        defaults=default_indices,
    )
    selected_compliance = [all_compliance[i] for i in compliance_indices]

    # ------------------------------------------------------------------
    # Step 4: Deployment Target
    # ------------------------------------------------------------------
    platform_ids = list(platforms.keys()) if isinstance(platforms, dict) else []
    platform_options = []
    for pid in platform_ids:
        pdef = platforms.get(pid, {})
        name = pdef.get("name", pid) if isinstance(pdef, dict) else pid
        desc = pdef.get("description", "") if isinstance(pdef, dict) else ""
        platform_options.append(f"{name} — {desc}" if desc else name)

    default_platform_idx = 0
    profile_platform = selected_profile.get("default_platform", "docker")
    for i, pid in enumerate(platform_ids):
        if pid == profile_platform:
            default_platform_idx = i
            break

    if platform_options:
        platform_choice = _prompt_choice(
            "Step 4/6: Where will you deploy?",
            platform_options,
            default=default_platform_idx,
        )
        selected_platform = platform_ids[platform_choice]
    else:
        selected_platform = "docker"

    # ------------------------------------------------------------------
    # Step 5: Additional Capabilities
    # ------------------------------------------------------------------
    # Gather modules already included from profile + compliance
    profile_modules = selected_profile.get("modules", [])
    if isinstance(profile_modules, str):
        if profile_modules.upper() == "ALL":
            profile_modules = _get_all_module_ids(manifest)
        else:
            profile_modules = [profile_modules]

    required_ids = set(_get_required_module_ids(manifest))
    already_included = set(profile_modules) | required_ids

    # Compliance modules
    ComplianceConfiguratorCls = _get_compliance_configurator()
    configurator = ComplianceConfiguratorCls()
    comp_config = configurator.configure_posture(selected_compliance)
    comp_modules = set(comp_config.get("required_modules", []))
    already_included |= comp_modules

    # Optional modules not yet selected
    all_module_ids = _get_all_module_ids(manifest)
    optional_ids = [
        m for m in all_module_ids
        if m not in already_included and not modules_cfg.get(m, {}).get("compliance_posture")
    ]

    optional_options = []
    for mid in optional_ids:
        mdef = modules_cfg.get(mid, {})
        name = mdef.get("name", mid) if isinstance(mdef, dict) else mid
        desc = mdef.get("description", "") if isinstance(mdef, dict) else ""
        short_desc = desc[:60] + "..." if len(desc) > 60 else desc
        optional_options.append(f"{mid}: {name} — {short_desc}")

    # Pre-check recommended addons from profile
    recommended = selected_profile.get("recommended_addons", [])
    if isinstance(recommended, str):
        recommended = [recommended]
    optional_defaults = [i for i, mid in enumerate(optional_ids) if mid in recommended]

    selected_addons: List[str] = []
    if optional_options:
        addon_indices = _prompt_multi_choice(
            "Step 5/6: Select additional capabilities (optional):",
            optional_options,
            defaults=optional_defaults,
        )
        selected_addons = [optional_ids[i] for i in addon_indices]

    # ------------------------------------------------------------------
    # Build final module list
    # ------------------------------------------------------------------
    final_modules = list(profile_modules)
    for m in selected_addons:
        if m not in final_modules:
            final_modules.append(m)

    # Determine CUI from profile + compliance
    cui_enabled = selected_profile.get("cui_enabled", False)
    if comp_config.get("cui_enabled"):
        cui_enabled = True

    # ------------------------------------------------------------------
    # Step 6: Review & Confirm
    # ------------------------------------------------------------------
    print()
    print("=" * 60)
    print("  Step 6/6: Review Your Installation")
    print("=" * 60)
    print()
    print(f"  Profile:     {selected_profile.get('name', selected_profile_id)}")
    print(f"  Team Size:   {team_size}")
    print(f"  Platform:    {selected_platform}")
    print(f"  CUI Markings: {'enabled' if cui_enabled else 'disabled'}")
    print(f"  Impact Level: {comp_config.get('impact_level', 'IL2')}")
    print()
    print("  Compliance Frameworks:")
    if selected_compliance:
        for fw in selected_compliance:
            print(f"    - {fw}")
    else:
        print("    (none)")
    print()
    print("  Modules to install:")
    try:
        sorted_preview = _topological_sort(final_modules, manifest)
    except ValueError:
        sorted_preview = final_modules
    for mod_id in sorted_preview:
        mdef = modules_cfg.get(mod_id, {})
        name = mdef.get("name", mod_id) if isinstance(mdef, dict) else mod_id
        print(f"    - {mod_id}: {name}")
    print()

    if dry_run:
        print("  [DRY RUN] No changes will be made.")
        print()

    if not _prompt_confirm("Proceed with installation?"):
        return {"success": False, "error": "Installation cancelled by user."}

    return install(
        modules=final_modules,
        compliance_frameworks=selected_compliance,
        profile_name=selected_profile_id,
        platform_target=selected_platform,
        team_size=team_size,
        cui_enabled=cui_enabled,
        dry_run=dry_run,
    )


# ---------------------------------------------------------------------------
# Profile-based (non-interactive) installation
# ---------------------------------------------------------------------------

def run_profile(
    profile_name: str,
    compliance_override: Optional[List[str]] = None,
    platform_override: Optional[str] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Run installation from a named deployment profile.

    Args:
        profile_name: Profile ID from deployment_profiles.yaml.
        compliance_override: Override the profile's default_compliance.
        platform_override: Override the profile's default_platform.
        dry_run: If True, preview without executing.

    Returns:
        Installation result dict.
    """
    profiles_data = _load_profiles()
    manifest = _load_manifest()
    profiles = profiles_data.get("profiles", {})

    if profile_name not in profiles:
        available = ", ".join(sorted(profiles.keys()))
        return {
            "success": False,
            "error": f"Unknown profile: '{profile_name}'. Available: {available}",
        }

    profile = profiles[profile_name]
    if not isinstance(profile, dict):
        profile = {}

    # Resolve modules
    profile_modules = profile.get("modules", [])
    if isinstance(profile_modules, str):
        if profile_modules.upper() == "ALL":
            profile_modules = _get_all_module_ids(manifest)
        else:
            profile_modules = [profile_modules]

    # Resolve compliance
    compliance = compliance_override
    if compliance is None:
        compliance = profile.get("default_compliance", [])
        if isinstance(compliance, str):
            if compliance.upper() == "ALL":
                compliance = _get_all_compliance_ids(manifest)
            else:
                compliance = [compliance]

    # Resolve platform
    platform = platform_override or profile.get("default_platform", "docker")

    # CUI
    cui_enabled = profile.get("cui_enabled", False)

    # Team size
    team_size = profile.get("team_size", "")

    return install(
        modules=profile_modules,
        compliance_frameworks=compliance,
        profile_name=profile_name,
        platform_target=platform,
        team_size=team_size,
        cui_enabled=cui_enabled,
        dry_run=dry_run,
    )


# ---------------------------------------------------------------------------
# Upgrade / add-module operations
# ---------------------------------------------------------------------------

def show_upgrade_options() -> Dict[str, Any]:
    """Show modules that can be added to the current installation.

    Returns:
        Dict with available modules grouped by category.
    """
    ModuleRegistryCls = _get_module_registry()
    registry = ModuleRegistryCls()
    available = registry.get_available()
    upgrade_path = registry.get_upgrade_path()
    installed = registry.get_installed()

    installed_count = sum(1 for v in installed.values() if v.get("installed"))

    return {
        "installed_count": installed_count,
        "available_now": available,
        "available_now_count": len(available),
        "full_upgrade_path": upgrade_path,
        "full_upgrade_count": len(upgrade_path),
    }


def add_module(
    module_id: str,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Add a single module to an existing installation.

    Resolves dependencies and initializes required DB tables.

    Args:
        module_id: The module to add.
        dry_run: Preview without executing.

    Returns:
        Dict with result summary.
    """
    manifest = _load_manifest()
    modules_cfg = manifest.get("modules", {})

    if module_id not in modules_cfg:
        return {
            "success": False,
            "error": f"Unknown module: '{module_id}'",
            "available": list(modules_cfg.keys()),
        }

    # Load current installation
    ModuleRegistryCls = _get_module_registry()
    registry = ModuleRegistryCls()
    installed = registry.get_installed()
    installed_ids = [k for k, v in installed.items() if v.get("installed")]

    if module_id in installed_ids:
        return {
            "success": True,
            "module_id": module_id,
            "note": "Module is already installed.",
        }

    # Resolve what needs to be installed (module + deps)
    try:
        sorted_new = _topological_sort([module_id], manifest)
    except ValueError as e:
        return {"success": False, "error": str(e)}

    # Filter out already installed
    to_install = [m for m in sorted_new if m not in installed_ids]

    if dry_run:
        db_init = ModularDBInitializer()
        db_result = db_init.initialize(to_install, dry_run=True)
        return {
            "success": True,
            "dry_run": True,
            "module_id": module_id,
            "modules_to_install": to_install,
            "db_tables_planned": db_result.get("tables_planned", []),
        }

    # Initialize DB tables for new modules
    db_init = ModularDBInitializer()
    db_result = db_init.initialize(to_install, dry_run=False)

    # Register modules
    results = []
    for mid in to_install:
        r = registry.install_module(mid)
        results.append(r)

    failed = [r for r in results if not r.get("success")]

    return {
        "success": len(failed) == 0,
        "dry_run": False,
        "module_id": module_id,
        "modules_installed": to_install,
        "db_tables_created": db_result.get("tables_created", []),
        "failures": failed if failed else [],
    }


def add_compliance(
    framework_id: str,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Add a compliance framework to an existing installation.

    Resolves required modules, initializes DB tables, and updates the
    compliance posture in the registry.

    Args:
        framework_id: Compliance framework ID (e.g., hipaa, cmmc).
        dry_run: Preview without executing.

    Returns:
        Dict with result summary.
    """
    ComplianceConfiguratorCls = _get_compliance_configurator()
    configurator = ComplianceConfiguratorCls()

    # Validate framework
    posture_config = configurator.configure_posture([framework_id])
    if posture_config.get("unknown_frameworks"):
        available_postures = configurator.list_postures()
        return {
            "success": False,
            "error": f"Unknown framework: '{framework_id}'",
            "available": [p["id"] for p in available_postures],
        }

    required_modules = posture_config.get("required_modules", [])
    cui_needed = posture_config.get("cui_enabled", False)

    # Load current state
    ModuleRegistryCls = _get_module_registry()
    registry = ModuleRegistryCls()
    installed = registry.get_installed()
    installed_ids = [k for k, v in installed.items() if v.get("installed")]
    current_config = registry.export_config()
    current_posture = current_config.get("compliance_posture", [])

    if framework_id in current_posture:
        return {
            "success": True,
            "framework_id": framework_id,
            "note": "Framework is already in the compliance posture.",
        }

    # Determine new modules needed
    to_install = [m for m in required_modules if m not in installed_ids]

    if dry_run:
        db_init = ModularDBInitializer()
        db_result = db_init.initialize(to_install, dry_run=True) if to_install else {}
        return {
            "success": True,
            "dry_run": True,
            "framework_id": framework_id,
            "modules_to_install": to_install,
            "cui_enabled": cui_needed,
            "db_tables_planned": db_result.get("tables_planned", []) if db_result else [],
            "security_gate_overrides": posture_config.get("security_gate_overrides", {}),
        }

    # Install new modules
    manifest = _load_manifest()
    if to_install:
        try:
            sorted_new = _topological_sort(to_install, manifest)
        except ValueError as e:
            return {"success": False, "error": str(e)}
        to_install = [m for m in sorted_new if m not in installed_ids]

        db_init = ModularDBInitializer()
        db_init.initialize(to_install, dry_run=False)

        for mid in to_install:
            registry.install_module(mid)

    # Update compliance posture in registry
    updated_posture = list(current_posture)
    if framework_id not in updated_posture:
        updated_posture.append(framework_id)

    # Update CUI if needed
    effective_cui = current_config.get("cui_enabled", False)
    if cui_needed:
        effective_cui = True
        _update_cui_markings(True)

    # Merge gate overrides
    existing_gates = current_config.get("security_gate_overrides", {})
    existing_gates.update(posture_config.get("security_gate_overrides", {}))

    # Persist registry
    current_config["compliance_posture"] = updated_posture
    current_config["cui_enabled"] = effective_cui
    current_config["security_gate_overrides"] = existing_gates
    current_config["last_updated"] = datetime.now(timezone.utc).isoformat()

    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(REGISTRY_PATH, "w", encoding="utf-8") as fh:
        json.dump(current_config, fh, indent=2, default=str)

    return {
        "success": True,
        "dry_run": False,
        "framework_id": framework_id,
        "modules_installed": to_install,
        "compliance_posture": updated_posture,
        "cui_enabled": effective_cui,
        "security_gate_overrides": existing_gates,
    }


def show_status() -> Dict[str, Any]:
    """Return the current installation status.

    Returns:
        Dict with installed modules, compliance posture, platform, etc.
    """
    ModuleRegistryCls = _get_module_registry()
    registry = ModuleRegistryCls()

    installed = registry.get_installed()
    config = registry.export_config()
    validation = registry.validate()

    installed_count = sum(1 for v in installed.values() if v.get("installed"))
    total_available = len(_get_all_module_ids(_load_manifest()))

    return {
        "profile": config.get("profile", "default"),
        "platform": config.get("platform", "unknown"),
        "team_size": config.get("team_size", ""),
        "installed_modules": installed,
        "installed_count": installed_count,
        "total_available": total_available,
        "coverage": f"{installed_count}/{total_available}",
        "compliance_posture": config.get("compliance_posture", []),
        "cui_enabled": config.get("cui_enabled", False),
        "impact_level": config.get("impact_level", "IL2"),
        "security_gate_overrides": config.get("security_gate_overrides", {}),
        "validation": validation,
        "installed_at": config.get("installed_at", ""),
        "last_updated": config.get("last_updated", ""),
        "registry_path": str(REGISTRY_PATH),
        "db_path": str(DB_PATH),
    }


# ---------------------------------------------------------------------------
# Human-readable output formatting
# ---------------------------------------------------------------------------

def _format_install_result_human(result: Dict[str, Any]) -> str:
    """Format installation result for terminal display."""
    lines = [
        "",
        "=" * 60,
        "  ICDEV Installation Summary",
        "=" * 60,
        "",
    ]

    if result.get("dry_run"):
        lines.append("  *** DRY RUN — no changes were made ***")
        lines.append("")

    success = result.get("success", False)
    lines.append(f"  Status:       {'SUCCESS' if success else 'FAILED'}")
    if result.get("error"):
        lines.append(f"  Error:        {result['error']}")
    lines.append(f"  Profile:      {result.get('profile', 'N/A')}")
    lines.append(f"  Platform:     {result.get('platform', 'N/A')}")
    lines.append(f"  Team Size:    {result.get('team_size', 'N/A')}")
    lines.append(f"  CUI Markings: {'enabled' if result.get('cui_enabled') else 'disabled'}")
    lines.append(f"  Impact Level: {result.get('impact_level', 'IL2')}")
    lines.append("")

    # Modules
    modules = result.get("modules_installed") or result.get("modules", [])
    lines.append(f"  Modules ({len(modules)}):")
    for mod_id in modules:
        lines.append(f"    - {mod_id}")
    lines.append("")

    # Compliance
    compliance = result.get("compliance_frameworks", [])
    lines.append(f"  Compliance Frameworks ({len(compliance)}):")
    if compliance:
        for fw in compliance:
            lines.append(f"    - {fw}")
    else:
        lines.append("    (none)")
    lines.append("")

    # DB tables
    tables_key = "db_tables_created" if not result.get("dry_run") else "db_tables_planned"
    tables = result.get(tables_key, [])
    table_count = result.get("db_table_count", len(tables))
    lines.append(f"  Database Tables: {table_count}")
    lines.append("")

    # Failures
    failures = result.get("failures", [])
    if failures:
        lines.append("  Failures:")
        for f in failures:
            lines.append(f"    - {f.get('module_id', '?')}: {f.get('error', '?')}")
        lines.append("")

    # Paths
    if result.get("registry_path"):
        lines.append(f"  Registry:     {result['registry_path']}")
    if result.get("db_path"):
        lines.append(f"  Database:     {result['db_path']}")
    lines.append("")

    return "\n".join(lines)


def _format_status_human(result: Dict[str, Any]) -> str:
    """Format status output for terminal display."""
    lines = [
        "",
        "=" * 60,
        "  ICDEV Installation Status",
        "=" * 60,
        "",
        f"  Profile:       {result.get('profile', 'default')}",
        f"  Platform:      {result.get('platform', 'unknown')}",
        f"  Team Size:     {result.get('team_size', 'N/A')}",
        f"  Coverage:      {result.get('coverage', '0/0')}",
        f"  CUI Markings:  {'enabled' if result.get('cui_enabled') else 'disabled'}",
        f"  Impact Level:  {result.get('impact_level', 'IL2')}",
        f"  Installed At:  {result.get('installed_at', 'N/A')}",
        f"  Last Updated:  {result.get('last_updated', 'N/A')}",
        "",
    ]

    # Installed modules
    installed = result.get("installed_modules", {})
    lines.append(f"  Installed Modules ({result.get('installed_count', 0)}):")
    if installed:
        lines.append(f"    {'Module':<25} {'Version':<10} {'Installed At'}")
        lines.append(f"    {'-' * 25} {'-' * 10} {'-' * 25}")
        for mod_id, info in sorted(installed.items()):
            if isinstance(info, dict) and info.get("installed"):
                name = info.get("name", mod_id)
                ver = info.get("version", "?")
                ts = info.get("installed_at", "?")
                lines.append(f"    {name:<25} {ver:<10} {ts}")
    else:
        lines.append("    (none)")
    lines.append("")

    # Compliance posture
    posture = result.get("compliance_posture", [])
    lines.append(f"  Compliance Posture ({len(posture)}):")
    if posture:
        for fw in posture:
            lines.append(f"    - {fw}")
    else:
        lines.append("    (none)")
    lines.append("")

    # Validation
    validation = result.get("validation", {})
    valid = validation.get("valid", True)
    lines.append(f"  Validation:    {'VALID' if valid else 'ISSUES FOUND'}")
    issues = validation.get("issues", [])
    if issues:
        for issue in issues:
            lines.append(f"    - {issue}")
    lines.append("")

    return "\n".join(lines)


def _format_upgrade_human(result: Dict[str, Any]) -> str:
    """Format upgrade options for terminal display."""
    lines = [
        "",
        "=" * 60,
        "  ICDEV — Available Upgrades",
        "=" * 60,
        "",
        f"  Currently installed: {result.get('installed_count', 0)} modules",
        "",
    ]

    available = result.get("available_now", [])
    lines.append(f"  Ready to install ({len(available)}):")
    if available:
        for mod in available:
            lines.append(f"    {mod['module_id']:<25} {mod.get('name', '')}")
            if mod.get("description"):
                lines.append(f"      {mod['description'][:70]}")
    else:
        lines.append("    (none — all dependencies must be satisfied first)")
    lines.append("")

    blocked = [m for m in result.get("full_upgrade_path", []) if not m.get("deps_met")]
    if blocked:
        lines.append(f"  Blocked (dependencies unmet): {len(blocked)}")
        for mod in blocked:
            deps = ", ".join(mod.get("dependencies", []))
            lines.append(f"    {mod['module_id']:<25} needs: {deps}")
        lines.append("")

    lines.append("  To add a module:")
    lines.append("    python tools/installer/installer.py --add-module <module_id>")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Parse CLI arguments and dispatch to the appropriate mode."""
    parser = argparse.ArgumentParser(
        description="ICDEV Modular Installer — configure and install ICDEV components",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  # Interactive wizard
  python tools/installer/installer.py --interactive

  # Install from profile
  python tools/installer/installer.py --profile dod_team
  python tools/installer/installer.py --profile healthcare --compliance hipaa,hitrust --platform k8s

  # Add modules after install
  python tools/installer/installer.py --add-module marketplace
  python tools/installer/installer.py --add-compliance hipaa

  # Check status
  python tools/installer/installer.py --status
  python tools/installer/installer.py --status --json

  # Dry run
  python tools/installer/installer.py --profile dod_team --dry-run
""",
    )

    # Mode selection
    mode_group = parser.add_argument_group("Installation Modes")
    mode_group.add_argument(
        "--interactive", action="store_true",
        help="Run the interactive 6-step installation wizard",
    )
    mode_group.add_argument(
        "--profile", metavar="NAME",
        help="Install using a named deployment profile (e.g., dod_team, healthcare)",
    )
    mode_group.add_argument(
        "--upgrade", action="store_true",
        help="Show available modules that can be added to the current installation",
    )
    mode_group.add_argument(
        "--add-module", metavar="MODULE_ID",
        help="Add a single module (with dependencies) to the current installation",
    )
    mode_group.add_argument(
        "--add-compliance", metavar="FRAMEWORK_ID",
        help="Add a compliance framework (with required modules) to the installation",
    )
    mode_group.add_argument(
        "--status", action="store_true",
        help="Show the current installation status",
    )

    # Overrides for profile mode
    override_group = parser.add_argument_group("Profile Overrides")
    override_group.add_argument(
        "--compliance", metavar="FRAMEWORKS",
        help="Comma-separated compliance framework IDs (overrides profile default)",
    )
    override_group.add_argument(
        "--platform", metavar="PLATFORM",
        help="Deployment platform: docker, k8s, helm, aws_govcloud, azure_gov, on_prem",
    )

    # Output options
    output_group = parser.add_argument_group("Output Options")
    output_group.add_argument(
        "--json", action="store_true",
        help="Output results as JSON",
    )
    output_group.add_argument(
        "--human", action="store_true",
        help="Human-friendly colorized terminal output",
    )
    output_group.add_argument(
        "--dry-run", action="store_true",
        help="Preview what would be installed without executing",
    )

    args = parser.parse_args()
    use_json = args.json

    def _output(result: Dict[str, Any], formatter=None) -> None:
        """Print result as JSON or human-readable."""
        if use_json:
            print(json.dumps(result, indent=2, default=str))
        elif formatter:
            print(formatter(result))
        else:
            # Fallback: try to use output_formatter if available
            try:
                from tools.cli.output_formatter import auto_format
                print(auto_format(result, title="ICDEV Installer"))
            except ImportError:
                print(json.dumps(result, indent=2, default=str))

    try:
        # ------- Interactive mode -------
        if args.interactive:
            result = run_interactive(dry_run=args.dry_run)
            _output(result, _format_install_result_human)

        # ------- Profile mode -------
        elif args.profile:
            compliance_override = None
            if args.compliance:
                compliance_override = [
                    f.strip() for f in args.compliance.split(",") if f.strip()
                ]

            result = run_profile(
                profile_name=args.profile,
                compliance_override=compliance_override,
                platform_override=args.platform,
                dry_run=args.dry_run,
            )
            _output(result, _format_install_result_human)

            if not use_json and result.get("success") and not args.dry_run:
                print("  Installation complete. Run --status to verify.")
                print()

        # ------- Upgrade -------
        elif args.upgrade:
            result = show_upgrade_options()
            _output(result, _format_upgrade_human)

        # ------- Add module -------
        elif args.add_module:
            result = add_module(args.add_module, dry_run=args.dry_run)
            _output(result, _format_install_result_human)

        # ------- Add compliance -------
        elif args.add_compliance:
            result = add_compliance(args.add_compliance, dry_run=args.dry_run)
            _output(result, _format_install_result_human)

        # ------- Status -------
        elif args.status:
            result = show_status()
            _output(result, _format_status_human)

        else:
            parser.print_help()
            sys.exit(0)

    except KeyboardInterrupt:
        print("\n\n  Installation cancelled.")
        sys.exit(130)
    except Exception as exc:
        error_result = {
            "success": False,
            "error": str(exc),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if use_json:
            print(json.dumps(error_result, indent=2, default=str))
        else:
            print(f"\nERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
