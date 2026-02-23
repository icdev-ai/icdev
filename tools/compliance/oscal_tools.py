# [TEMPLATE: CUI // SP-CTI]
"""OSCAL ecosystem integration — oscal-cli, oscal-pydantic, NIST OSCAL Content.

Composes three independently optional external tools behind a unified interface:
  1. oscal-cli (NIST, public domain) — Metaschema validation, profile resolution,
     format conversion (JSON/XML/YAML).  Requires Java 11+.
  2. oscal-pydantic (MIT) — Pydantic model validation for OSCAL artifacts
     (v1/v2 compatible via 3-strategy cascade: native → v1_compat → builtin_v2).
  3. NIST OSCAL Content (public domain) — Authoritative 800-53 Rev 5 catalog.

All three degrade gracefully when absent (air-gap safe, D134 compatible).

Architecture Decisions: D302 (oscal-cli subprocess), D303 (pydantic layer),
D304 (catalog adapter), D305 (single orchestrator), D306 (validation log).

Usage:
    python tools/compliance/oscal_tools.py --detect --json
    python tools/compliance/oscal_tools.py --validate /path/ssp.oscal.json --json
    python tools/compliance/oscal_tools.py --convert /path/ssp.json --format xml --json
    python tools/compliance/oscal_tools.py --resolve-profile /path/profile.json --json
    python tools/compliance/oscal_tools.py --catalog-lookup AC-2 --json
    python tools/compliance/oscal_tools.py --catalog-list --family AC --json
    python tools/compliance/oscal_tools.py --catalog-stats --json
"""

import argparse
import json
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"
CONFIG_PATH = BASE_DIR / "args" / "oscal_tools_config.yaml"

# ---------------------------------------------------------------------------
# Cached detection results (D302)
# ---------------------------------------------------------------------------
_JAVA_INFO = None       # {"available": bool, "version": str, "path": str}
_OSCAL_CLI_INFO = None  # {"available": bool, "version": str, "jar_path": str}
_PYDANTIC_INFO = None   # {"available": bool, "version": str}

# OSCAL JSON key → internal artifact type mapping (shared across validators)
_OSCAL_TYPE_MAP = {
    "system-security-plan": "ssp",
    "plan-of-action-and-milestones": "poam",
    "assessment-results": "assessment_results",
    "component-definition": "component_definition",
}
_OSCAL_JSON_KEY = {v: k for k, v in _OSCAL_TYPE_MAP.items()}

# oscal-pydantic module/class mapping
_OSCAL_PYDANTIC_MAP = {
    "ssp": ("oscal_pydantic.ssp", "SystemSecurityPlan"),
    "poam": ("oscal_pydantic.poam", "PlanOfActionAndMilestones"),
    "assessment_results": ("oscal_pydantic.assessment_results", "AssessmentResults"),
    "component_definition": ("oscal_pydantic.component_definition", "ComponentDefinition"),
}

# Model class cache: {artifact_type: (model_cls, compat_mode, is_document_model)}
_OSCAL_MODEL_CACHE = {}


def _get_pydantic_version():
    """Return installed pydantic major version (0 if not installed)."""
    try:
        import pydantic
        return int(pydantic.VERSION.split(".")[0])
    except Exception:
        return 0


def _load_config():
    """Load oscal_tools_config.yaml. Returns dict or defaults."""
    try:
        import yaml
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
    except ImportError:
        pass
    return {}


# ---------------------------------------------------------------------------
# Tool Detection (D302-D304)
# ---------------------------------------------------------------------------

def _detect_java():
    """Detect Java Runtime availability and version."""
    global _JAVA_INFO
    if _JAVA_INFO is not None:
        return _JAVA_INFO

    config = _load_config()
    java_cmd = config.get("oscal_cli", {}).get("java_cmd", "java")

    # Check JAVA_HOME first
    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        candidate = Path(java_home) / "bin" / "java"
        if candidate.exists():
            java_cmd = str(candidate)
        elif (candidate.parent / "java.exe").exists():
            java_cmd = str(candidate.parent / "java.exe")

    try:
        proc = subprocess.run(
            [java_cmd, "-version"],
            capture_output=True, text=True, timeout=10,
            stdin=subprocess.DEVNULL,
        )
        # Java outputs version to stderr
        output = proc.stderr or proc.stdout or ""
        # Extract version like "11.0.20" or "17.0.8"
        import re
        match = re.search(r'"?(\d+)[\._](\d+)', output)
        version = match.group(0).strip('"') if match else "unknown"
        major = int(match.group(1)) if match else 0

        _JAVA_INFO = {
            "available": major >= 11,
            "version": version,
            "path": shutil.which(java_cmd) or java_cmd,
            "error": None if major >= 11 else f"Java {major} found, need 11+",
        }
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        _JAVA_INFO = {"available": False, "version": None, "path": None, "error": str(exc)}

    return _JAVA_INFO


def _cli_env(java_info=None):
    """Build subprocess environment with JAVACMD set for wrapper scripts."""
    env = os.environ.copy()
    if java_info is None:
        java_info = _detect_java()
    java_path = java_info.get("path")
    if java_path:
        env["JAVACMD"] = java_path
        # Also ensure JAVA_HOME is set for scripts that use it
        java_bin = Path(java_path).parent
        if java_bin.name == "bin":
            env["JAVA_HOME"] = str(java_bin.parent)
    return env


def _find_oscal_cli():
    """Find oscal-cli executable or JAR.

    Returns:
        Tuple of (path, mode) where mode is 'wrapper' or 'jar', or (None, None).
    """
    # 1. Environment variable
    env_path = os.environ.get("OSCAL_CLI_PATH")
    if env_path and Path(env_path).exists():
        p = Path(env_path)
        if p.suffix == ".jar":
            return str(p), "jar"
        return str(p), "wrapper"

    config = _load_config()

    # 2. Vendor directory — standard distribution (bin/oscal-cli or bin/oscal-cli.bat)
    vendor_dir = BASE_DIR / "vendor" / "oscal-cli"
    if vendor_dir.exists():
        if os.name == "nt":
            # On Windows, prefer .bat (subprocess.run can't execute Unix shell scripts)
            bat = vendor_dir / "bin" / "oscal-cli.bat"
            if bat.exists():
                return str(bat), "wrapper"
        sh = vendor_dir / "bin" / "oscal-cli"
        if sh.exists():
            return str(sh), "wrapper"
        # Fallback: single fat JAR
        jar_rel = config.get("oscal_cli", {}).get("jar_path", "vendor/oscal-cli/oscal-cli.jar")
        jar_path = BASE_DIR / jar_rel
        if jar_path.exists():
            return str(jar_path), "jar"

    # 3. oscal-cli on system PATH
    on_path = shutil.which("oscal-cli")
    if on_path:
        return on_path, "wrapper"

    return None, None


def _detect_oscal_cli():
    """Detect oscal-cli availability."""
    global _OSCAL_CLI_INFO
    if _OSCAL_CLI_INFO is not None:
        return _OSCAL_CLI_INFO

    java = _detect_java()
    cli_path, cli_mode = _find_oscal_cli()

    if not java["available"]:
        _OSCAL_CLI_INFO = {
            "available": False, "version": None, "jar_path": cli_path,
            "mode": cli_mode,
            "error": f"Java not available: {java.get('error', 'not found')}",
        }
    elif not cli_path:
        _OSCAL_CLI_INFO = {
            "available": False, "version": None, "jar_path": None,
            "mode": None,
            "error": "oscal-cli not found (set OSCAL_CLI_PATH or place in vendor/oscal-cli/)",
        }
    else:
        # Try to get version
        version = None
        try:
            env = _cli_env(java)
            if cli_mode == "wrapper":
                cmd = [cli_path, "--version"]
            else:
                config = _load_config()
                java_cmd = java.get("path", "java")
                jvm_args = config.get("oscal_cli", {}).get("jvm_args", ["-Xmx512m"])
                cmd = [java_cmd] + jvm_args + ["-jar", cli_path, "--version"]
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=15,
                stdin=subprocess.DEVNULL, env=env,
            )
            version = (proc.stdout or proc.stderr or "").strip()
        except Exception:
            version = "unknown"

        _OSCAL_CLI_INFO = {
            "available": True, "version": version, "jar_path": cli_path,
            "mode": cli_mode, "error": None,
        }

    return _OSCAL_CLI_INFO


def _detect_oscal_pydantic():
    """Detect OSCAL Pydantic validation capability.

    Reports availability based on three possible sources:
      - oscal-pydantic package with compatible pydantic version (native)
      - oscal-pydantic package with pydantic v2 (v1_compat or builtin fallback)
      - Built-in v2 models when pydantic >= 2 (no oscal-pydantic needed)
    """
    global _PYDANTIC_INFO
    if _PYDANTIC_INFO is not None:
        return _PYDANTIC_INFO

    pv = _get_pydantic_version()
    oscal_pkg = None
    try:
        import importlib.metadata
        oscal_pkg = importlib.metadata.version("oscal-pydantic")
    except Exception:
        pass

    if oscal_pkg and pv >= 2:
        # oscal-pydantic installed + pydantic v2 → v1_compat or builtin fallback
        _PYDANTIC_INFO = {
            "available": True, "version": oscal_pkg,
            "pydantic_version": pv, "compat_mode": "v1_compat_or_builtin_v2",
            "error": None,
        }
    elif oscal_pkg and pv >= 1:
        # oscal-pydantic + pydantic v1 → native
        _PYDANTIC_INFO = {
            "available": True, "version": oscal_pkg,
            "pydantic_version": pv, "compat_mode": "native",
            "error": None,
        }
    elif pv >= 2:
        # No oscal-pydantic but pydantic v2 → builtin models
        _PYDANTIC_INFO = {
            "available": True, "version": f"builtin_v2 (pydantic {pv})",
            "pydantic_version": pv, "compat_mode": "builtin_v2",
            "error": None,
        }
    else:
        _PYDANTIC_INFO = {
            "available": pv > 0, "version": None,
            "pydantic_version": pv, "compat_mode": None,
            "error": ("pydantic not installed" if pv == 0
                      else "oscal-pydantic not installed (pip install oscal-pydantic)"),
        }

    return _PYDANTIC_INFO


def _detect_nist_catalog():
    """Detect whether official NIST OSCAL catalog is present."""
    config = _load_config()
    sources = config.get("nist_catalog", {}).get("catalog_sources", [])
    if not sources:
        sources = [
            "context/oscal/NIST_SP-800-53_rev5_catalog.json",
            "context/compliance/nist_800_53.json",
        ]

    for src in sources:
        path = BASE_DIR / src
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                is_official = "catalog" in data
                count = 0
                if is_official:
                    for g in data.get("catalog", {}).get("groups", []):
                        count += len(g.get("controls", []))
                        for c in g.get("controls", []):
                            count += len(c.get("controls", []))
                else:
                    count = len(data.get("controls", []))

                return {
                    "available": True,
                    "path": str(path),
                    "format": "nist_oscal" if is_official else "icdev_custom",
                    "controls_count": count,
                    "error": None,
                }
            except Exception as exc:
                return {"available": False, "path": str(path), "error": str(exc)}

    return {"available": False, "path": None, "error": "No catalog file found"}


def detect_oscal_tools():
    """Detect which OSCAL ecosystem tools are available.

    Returns:
        Dict with detection results for each tool.
    """
    return {
        "oscal_cli": _detect_oscal_cli(),
        "java": _detect_java(),
        "oscal_pydantic": _detect_oscal_pydantic(),
        "nist_catalog": _detect_nist_catalog(),
    }


# ---------------------------------------------------------------------------
# oscal-cli subprocess wrapper (D302)
# ---------------------------------------------------------------------------

def _run_oscal_cli(subcommand, cli_args, timeout=None):
    """Run oscal-cli via subprocess.

    Args:
        subcommand: e.g., "ssp validate", "ssp convert", "profile resolve"
        cli_args: List of additional CLI arguments.
        timeout: Seconds (default from config).

    Returns:
        Dict with stdout, stderr, returncode, success.
    """
    cli_info = _detect_oscal_cli()
    if not cli_info["available"]:
        return {"error": cli_info["error"], "available": False}

    config = _load_config()
    if timeout is None:
        timeout = config.get("oscal_cli", {}).get("timeout", 120)

    mode = cli_info.get("mode", "jar")
    if mode == "wrapper":
        cmd = [cli_info["jar_path"]]
    else:
        java_info = _detect_java()
        java_cmd = java_info.get("path", "java")
        jvm_args = config.get("oscal_cli", {}).get("jvm_args", ["-Xmx512m"])
        cmd = [java_cmd] + jvm_args + ["-jar", cli_info["jar_path"]]
    cmd.extend(subcommand.split())
    if cli_args:
        cmd.extend(cli_args)

    try:
        env = _cli_env()
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            cwd=str(BASE_DIR), stdin=subprocess.DEVNULL, env=env,
        )
        return {
            "success": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }
    except subprocess.TimeoutExpired:
        return {"error": f"oscal-cli timed out after {timeout}s", "success": False}
    except Exception as exc:
        return {"error": str(exc), "success": False}


# ---------------------------------------------------------------------------
# Multi-layer Validation (D302, D303, D306)
# ---------------------------------------------------------------------------

def _validate_structural(file_path, artifact_type=None):
    """Layer 1: ICDEV built-in structural validation (always available)."""
    start = time.monotonic()
    try:
        from tools.compliance.oscal_generator import validate_oscal
        result = validate_oscal(file_path, artifact_type)
    except ImportError:
        result = {"valid": False, "errors": ["oscal_generator not available"]}

    elapsed = int((time.monotonic() - start) * 1000)
    return {
        "validator": "icdev_structural",
        "valid": result.get("valid", False),
        "errors": result.get("errors", []),
        "duration_ms": elapsed,
    }


def _import_via_v1_compat(module_name, class_name):
    """Import oscal-pydantic model under pydantic v1 compatibility namespace.

    Pydantic v2 ships ``pydantic.v1`` for backward compatibility.  This
    temporarily redirects ``sys.modules["pydantic"]`` so oscal-pydantic's
    v1-style models (``__root__``, ``class Config``, ``regex=`` in Field)
    can load without modification.

    Note: NOT thread-safe — suitable for CLI/subprocess execution only.
    """
    import importlib
    import warnings

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            import pydantic.v1 as pydantic_v1  # noqa: F401
    except ImportError:
        return None

    # Save current pydantic modules
    saved_pydantic = {}
    for key in list(sys.modules.keys()):
        if key == "pydantic" or key.startswith("pydantic."):
            saved_pydantic[key] = sys.modules.pop(key)

    # Remove cached oscal_pydantic submodules so they re-import under v1
    for key in list(sys.modules.keys()):
        if key.startswith("oscal_pydantic."):
            del sys.modules[key]

    try:
        # Install v1 compat as 'pydantic'
        sys.modules["pydantic"] = pydantic_v1
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            mod = importlib.import_module(module_name)
        return getattr(mod, class_name, None)
    except Exception:
        return None
    finally:
        # Restore pydantic v2 modules
        for key in list(sys.modules.keys()):
            if key == "pydantic" or key.startswith("pydantic."):
                if key not in saved_pydantic:
                    del sys.modules[key]
        sys.modules.update(saved_pydantic)


def _get_builtin_v2_model(artifact_type):
    """Create lightweight Pydantic v2 OSCAL document model.

    Validates required OSCAL document structure (UUID, metadata, required
    sections) without the full schema depth of oscal-pydantic (~154 classes).
    Used as fallback when oscal-pydantic models are not v2-compatible.

    Returns a *document-level* model (wraps the full JSON, including the
    top-level ``system-security-plan`` / ``plan-of-action-and-milestones``
    key), unlike oscal-pydantic which provides content-level models.
    """
    try:
        from pydantic import BaseModel as PydanticBaseModel
        from pydantic import ConfigDict, Field
    except ImportError:
        return None

    class _Metadata(PydanticBaseModel):
        model_config = ConfigDict(extra="allow", populate_by_name=True)
        title: str
        last_modified: str = Field(alias="last-modified")
        version: str
        oscal_version: str = Field(alias="oscal-version")

    if artifact_type == "ssp":
        class _SSP(PydanticBaseModel):
            model_config = ConfigDict(extra="allow", populate_by_name=True)
            uuid: str
            metadata: _Metadata
            import_profile: dict = Field(alias="import-profile")
            system_characteristics: dict = Field(alias="system-characteristics")
            system_implementation: dict = Field(alias="system-implementation")
            control_implementation: dict = Field(alias="control-implementation")

        class _Doc(PydanticBaseModel):
            model_config = ConfigDict(extra="allow", populate_by_name=True)
            system_security_plan: _SSP = Field(alias="system-security-plan")
        return _Doc

    elif artifact_type == "poam":
        class _POAM(PydanticBaseModel):
            model_config = ConfigDict(extra="allow", populate_by_name=True)
            uuid: str
            metadata: _Metadata

        class _Doc(PydanticBaseModel):
            model_config = ConfigDict(extra="allow", populate_by_name=True)
            poam: _POAM = Field(alias="plan-of-action-and-milestones")
        return _Doc

    elif artifact_type == "assessment_results":
        class _AR(PydanticBaseModel):
            model_config = ConfigDict(extra="allow", populate_by_name=True)
            uuid: str
            metadata: _Metadata

        class _Doc(PydanticBaseModel):
            model_config = ConfigDict(extra="allow", populate_by_name=True)
            assessment_results: _AR = Field(alias="assessment-results")
        return _Doc

    elif artifact_type == "component_definition":
        class _CD(PydanticBaseModel):
            model_config = ConfigDict(extra="allow", populate_by_name=True)
            uuid: str
            metadata: _Metadata

        class _Doc(PydanticBaseModel):
            model_config = ConfigDict(extra="allow", populate_by_name=True)
            component_definition: _CD = Field(alias="component-definition")
        return _Doc

    return None


def _load_oscal_model(artifact_type):
    """Load OSCAL pydantic model class via cascading strategies.

    Strategies (in order):
      1. Direct import of oscal-pydantic (native v2 or v1 + pydantic v1)
      2. Import via pydantic.v1 compat namespace (v1 package under pydantic v2)
      3. Built-in lightweight Pydantic v2 models (always works with pydantic >= 2)

    Returns:
        Tuple of (model_class, compat_mode, is_document_model).
        compat_mode: ``"native"`` | ``"v1_compat"`` | ``"builtin_v2"`` | ``None``
        is_document_model: True if model wraps the full JSON document.
    """
    if artifact_type in _OSCAL_MODEL_CACHE:
        return _OSCAL_MODEL_CACHE[artifact_type]

    mapping = _OSCAL_PYDANTIC_MAP.get(artifact_type)
    if not mapping:
        result = (None, None, False)
        _OSCAL_MODEL_CACHE[artifact_type] = result
        return result

    module_name, class_name = mapping

    # Strategy 1: Direct import of oscal-pydantic
    try:
        import importlib
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            mod = importlib.import_module(module_name)
        cls = getattr(mod, class_name, None)
        if cls:
            result = (cls, "native", False)
            _OSCAL_MODEL_CACHE[artifact_type] = result
            return result
    except (ImportError, TypeError):
        # Clean up partial imports from failed attempt
        if module_name in sys.modules:
            del sys.modules[module_name]

    # Strategy 2: pydantic.v1 compat shim
    if _get_pydantic_version() >= 2:
        cls = _import_via_v1_compat(module_name, class_name)
        if cls:
            result = (cls, "v1_compat", False)
            _OSCAL_MODEL_CACHE[artifact_type] = result
            return result

    # Strategy 3: Built-in v2 models
    if _get_pydantic_version() >= 2:
        cls = _get_builtin_v2_model(artifact_type)
        if cls:
            result = (cls, "builtin_v2", True)
            _OSCAL_MODEL_CACHE[artifact_type] = result
            return result

    result = (None, None, False)
    _OSCAL_MODEL_CACHE[artifact_type] = result
    return result


def _validate_pydantic(file_path, artifact_type=None):
    """Layer 2: Pydantic model validation (optional, D303).

    Uses cascading strategy to load models:
      1. Native oscal-pydantic import (v2-compatible or v1 + pydantic v1)
      2. pydantic.v1 compat namespace (v1 package under pydantic v2)
      3. Built-in lightweight Pydantic v2 OSCAL models

    Skipped only when pydantic is not installed at all.
    """
    pv = _get_pydantic_version()
    if pv == 0:
        return {"validator": "oscal_pydantic", "skipped": True,
                "reason": "pydantic not installed"}

    start = time.monotonic()
    errors = []
    compat_mode = None

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Auto-detect artifact type from JSON keys
        if artifact_type is None:
            for key, at in _OSCAL_TYPE_MAP.items():
                if key in data:
                    artifact_type = at
                    break

        if artifact_type is None:
            errors.append("Cannot determine OSCAL artifact type for pydantic validation")
        else:
            model_cls, compat_mode, is_document = _load_oscal_model(artifact_type)

            if model_cls is None:
                errors.append(
                    f"No pydantic model available for artifact type: {artifact_type}")
            else:
                # For content models (oscal-pydantic), extract inner object;
                # for document models (builtin_v2), pass the full JSON.
                validate_data = data
                if not is_document:
                    json_key = _OSCAL_JSON_KEY.get(artifact_type)
                    if json_key and json_key in data:
                        validate_data = data[json_key]

                # Validate using the appropriate API (v2: model_validate, v1: parse_obj)
                if hasattr(model_cls, "model_validate"):
                    model_cls.model_validate(validate_data)
                elif hasattr(model_cls, "parse_obj"):
                    model_cls.parse_obj(validate_data)
                else:
                    model_cls(**validate_data)

    except (json.JSONDecodeError, OSError) as exc:
        errors.append(f"File read error: {exc}")
    except Exception as exc:
        errors.append(f"Pydantic validation error: {exc}")

    elapsed = int((time.monotonic() - start) * 1000)
    info = _detect_oscal_pydantic()
    result = {
        "validator": "oscal_pydantic",
        "valid": len(errors) == 0,
        "errors": errors,
        "duration_ms": elapsed,
    }
    if compat_mode:
        result["compat_mode"] = compat_mode
    if info.get("version"):
        result["version"] = info["version"]
    return result


def _validate_metaschema(file_path, artifact_type=None):
    """Layer 3: oscal-cli Metaschema validation (optional, D302)."""
    cli_info = _detect_oscal_cli()
    if not cli_info["available"]:
        return {"validator": "oscal_cli_metaschema", "skipped": True, "reason": cli_info["error"]}

    start = time.monotonic()

    # Map artifact type to oscal-cli subcommand
    subcmd_map = {
        "ssp": "ssp",
        "poam": "poam",
        "assessment_results": "assessment-results",
        "component_definition": "component-definition",
    }

    # Auto-detect from file content if needed
    if artifact_type is None:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for key, at in {"system-security-plan": "ssp", "plan-of-action-and-milestones": "poam",
                            "assessment-results": "assessment_results",
                            "component-definition": "component_definition"}.items():
                if key in data:
                    artifact_type = at
                    break
        except Exception:
            pass

    subcmd = subcmd_map.get(artifact_type, "ssp")
    result = _run_oscal_cli(f"{subcmd} validate", [str(file_path)])

    elapsed = int((time.monotonic() - start) * 1000)

    if "error" in result and not result.get("success", True):
        errors = [result["error"]]
    elif not result.get("success", True):
        # Parse validation errors from stderr
        raw = result.get("stderr", "") or result.get("stdout", "")
        errors = [line.strip() for line in raw.splitlines() if line.strip()]
    else:
        errors = []

    return {
        "validator": "oscal_cli_metaschema",
        "valid": result.get("success", False),
        "errors": errors,
        "duration_ms": elapsed,
        "version": cli_info.get("version"),
    }


def _log_validation(project_id, artifact_type, file_path, validator_result, db_path=None):
    """Record validation attempt in oscal_validation_log (D306, append-only)."""
    if db_path is None:
        db_path = DB_PATH
    db_path = Path(db_path)
    if not db_path.exists():
        return

    try:
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            """INSERT INTO oscal_validation_log
               (project_id, artifact_type, file_path, validator, valid,
                error_count, errors, duration_ms, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                project_id,
                artifact_type,
                str(file_path),
                validator_result.get("validator", "unknown"),
                1 if validator_result.get("valid") else 0,
                len(validator_result.get("errors", [])),
                json.dumps(validator_result.get("errors", [])),
                validator_result.get("duration_ms", 0),
                datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            ),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.debug("Failed to log validation: %s", exc)


def validate_oscal_deep(file_path, artifact_type=None, validators=None,
                        project_id=None, db_path=None):
    """Multi-layer OSCAL validation pipeline (D305).

    Runs up to 3 validation layers in order:
      1. ICDEV structural (always)
      2. oscal-pydantic model (if installed)
      3. oscal-cli Metaschema (if installed + Java present)

    Each layer is independent — skipped layers are reported as such.

    Args:
        file_path: Path to OSCAL file to validate.
        artifact_type: ssp|poam|assessment_results|component_definition (auto-detected).
        validators: List of specific validators to run. Default: all available.
        project_id: Optional project ID for audit logging.
        db_path: Override database path.

    Returns:
        Dict with overall valid status, per-validator results, and aggregated errors.
    """
    file_path = str(Path(file_path))
    results = []
    all_errors = []

    pipeline = validators or ["icdev_structural", "oscal_pydantic", "oscal_cli_metaschema"]

    dispatch = {
        "icdev_structural": lambda: _validate_structural(file_path, artifact_type),
        "oscal_pydantic": lambda: _validate_pydantic(file_path, artifact_type),
        "oscal_cli_metaschema": lambda: _validate_metaschema(file_path, artifact_type),
    }

    for layer in pipeline:
        if layer in dispatch:
            result = dispatch[layer]()
            results.append(result)

            if not result.get("skipped", False):
                _log_validation(project_id, artifact_type, file_path, result, db_path)
                all_errors.extend(result.get("errors", []))

    # Overall: valid only if all non-skipped layers pass
    active_results = [r for r in results if not r.get("skipped", False)]
    overall_valid = all(r.get("valid", False) for r in active_results) if active_results else False

    return {
        "valid": overall_valid,
        "errors": all_errors,
        "error_count": len(all_errors),
        "validators": results,
        "validators_run": len(active_results),
        "validators_skipped": len(results) - len(active_results),
        "file_path": file_path,
        "artifact_type": artifact_type,
    }


# ---------------------------------------------------------------------------
# Format Conversion (D302)
# ---------------------------------------------------------------------------

def convert_oscal_format(input_path, output_format, output_path=None):
    """Convert OSCAL artifact between JSON, XML, and YAML.

    Requires oscal-cli + Java. Returns error if unavailable.

    Args:
        input_path: Path to source OSCAL file.
        output_format: Target format — "json", "xml", or "yaml".
        output_path: Optional output path. Auto-generated if omitted.

    Returns:
        Dict with output_path on success, or error.
    """
    input_path = Path(input_path)
    if not input_path.exists():
        return {"error": f"File not found: {input_path}", "success": False}

    if output_format not in ("json", "xml", "yaml"):
        return {"error": f"Unsupported format: {output_format}", "success": False}

    if output_path is None:
        output_path = input_path.with_suffix(f".{output_format}")
    output_path = Path(output_path)

    # Auto-detect artifact type from file content
    subcmd = "ssp"
    try:
        with open(input_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for key, cmd in {"system-security-plan": "ssp",
                         "plan-of-action-and-milestones": "poam",
                         "assessment-results": "assessment-results",
                         "component-definition": "component-definition"}.items():
            if key in data:
                subcmd = cmd
                break
    except Exception:
        pass

    result = _run_oscal_cli(
        f"{subcmd} convert",
        ["--to", output_format, str(input_path), str(output_path)],
    )

    if result.get("error") and not result.get("success", True):
        return {"error": result["error"], "success": False}

    if result.get("success"):
        return {
            "success": True,
            "input_path": str(input_path),
            "output_path": str(output_path),
            "output_format": output_format,
        }

    return {
        "success": False,
        "error": result.get("stderr", "Unknown conversion error"),
    }


# ---------------------------------------------------------------------------
# Profile Resolution (D302)
# ---------------------------------------------------------------------------

def resolve_oscal_profile(profile_path, output_path=None):
    """Flatten an OSCAL Profile into a resolved Catalog.

    Requires oscal-cli + Java. Returns error if unavailable.

    Args:
        profile_path: Path to OSCAL Profile file.
        output_path: Optional output path for resolved catalog.

    Returns:
        Dict with output_path on success, or error.
    """
    profile_path = Path(profile_path)
    if not profile_path.exists():
        return {"error": f"Profile not found: {profile_path}", "success": False}

    if output_path is None:
        output_path = profile_path.with_name(
            profile_path.stem + "-resolved" + profile_path.suffix
        )
    output_path = Path(output_path)

    result = _run_oscal_cli(
        "profile resolve",
        [str(profile_path), str(output_path)],
    )

    if result.get("error") and not result.get("success", True):
        return {"error": result["error"], "success": False}

    if result.get("success"):
        return {
            "success": True,
            "profile_path": str(profile_path),
            "output_path": str(output_path),
        }

    return {
        "success": False,
        "error": result.get("stderr", "Unknown resolution error"),
    }


# ---------------------------------------------------------------------------
# Catalog Operations (D304)
# ---------------------------------------------------------------------------

def catalog_lookup(control_id, catalog_source="auto", catalog_path=None):
    """Look up a control from the NIST catalog.

    Args:
        control_id: Control ID (e.g., "AC-2").
        catalog_source: "auto" (official first), "official", or "icdev".
        catalog_path: Direct path to a catalog file (overrides catalog_source).

    Returns:
        Control dict or error dict.
    """
    from tools.compliance.oscal_catalog_adapter import OscalCatalogAdapter

    if catalog_path:
        adapter = OscalCatalogAdapter(catalog_path=catalog_path)
    else:
        sources = None
        if catalog_source == "official":
            sources = [str(BASE_DIR / "context" / "oscal" / "NIST_SP-800-53_rev5_catalog.json")]
        elif catalog_source == "icdev":
            sources = [str(BASE_DIR / "context" / "compliance" / "nist_800_53.json")]
        adapter = OscalCatalogAdapter(catalog_sources=sources)

    ctrl = adapter.get_control(control_id)

    if ctrl:
        return ctrl
    return {"error": f"Control '{control_id}' not found", "catalog_source": catalog_source}


def catalog_list(family=None, catalog_source="auto", catalog_path=None):
    """List controls from the NIST catalog.

    Args:
        family: Optional family filter (e.g., "AC").
        catalog_source: "auto", "official", or "icdev".
        catalog_path: Direct path to a catalog file (overrides catalog_source).

    Returns:
        Dict with controls list and count.
    """
    from tools.compliance.oscal_catalog_adapter import OscalCatalogAdapter

    if catalog_path:
        adapter = OscalCatalogAdapter(catalog_path=catalog_path)
    else:
        sources = None
        if catalog_source == "official":
            sources = [str(BASE_DIR / "context" / "oscal" / "NIST_SP-800-53_rev5_catalog.json")]
        elif catalog_source == "icdev":
            sources = [str(BASE_DIR / "context" / "compliance" / "nist_800_53.json")]
        adapter = OscalCatalogAdapter(catalog_sources=sources)
    controls = adapter.list_controls(family=family)

    return {
        "controls": controls,
        "count": len(controls),
        "catalog_stats": adapter.get_catalog_stats(),
    }


def catalog_stats(catalog_source="auto", catalog_path=None):
    """Get catalog statistics.

    Args:
        catalog_source: "auto", "official", or "icdev".
        catalog_path: Direct path to a catalog file (overrides catalog_source).

    Returns:
        Dict with catalog metadata and counts.
    """
    from tools.compliance.oscal_catalog_adapter import OscalCatalogAdapter

    if catalog_path:
        adapter = OscalCatalogAdapter(catalog_path=catalog_path)
    else:
        sources = None
        if catalog_source == "official":
            sources = [str(BASE_DIR / "context" / "oscal" / "NIST_SP-800-53_rev5_catalog.json")]
        elif catalog_source == "icdev":
            sources = [str(BASE_DIR / "context" / "compliance" / "nist_800_53.json")]
        adapter = OscalCatalogAdapter(catalog_sources=sources)
    return adapter.get_catalog_stats()


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------

def main():
    """CLI entry point for OSCAL ecosystem tools."""
    parser = argparse.ArgumentParser(
        description="OSCAL Ecosystem Tools — validation, conversion, catalog (D302-D306)"
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--detect", action="store_true",
                       help="Detect available OSCAL ecosystem tools")
    group.add_argument("--validate", metavar="FILE",
                       help="Deep-validate an OSCAL artifact (multi-layer pipeline)")
    group.add_argument("--convert", metavar="FILE",
                       help="Convert OSCAL artifact to another format")
    group.add_argument("--resolve-profile", metavar="FILE",
                       help="Resolve OSCAL Profile into flattened Catalog")
    group.add_argument("--catalog-lookup", metavar="CONTROL_ID",
                       help="Look up a control by ID (e.g., AC-2)")
    group.add_argument("--catalog-list", action="store_true",
                       help="List catalog controls")
    group.add_argument("--catalog-stats", action="store_true",
                       help="Show catalog statistics")

    parser.add_argument("--format", choices=["json", "xml", "yaml"],
                        help="Output format for --convert")
    parser.add_argument("--output", help="Output file path")
    parser.add_argument("--artifact-type",
                        choices=["ssp", "poam", "assessment_results", "component_definition"],
                        help="OSCAL artifact type (auto-detected if omitted)")
    parser.add_argument("--catalog-source", choices=["auto", "official", "icdev"],
                        default="auto", help="Catalog source preference")
    parser.add_argument("--family", help="Filter by control family (e.g., AC)")
    parser.add_argument("--project-id", help="Project ID for audit logging")
    parser.add_argument("--db-path", help="Override database path")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--human", action="store_true", help="Human-readable output")
    args = parser.parse_args()

    if args.detect:
        result = detect_oscal_tools()
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            for tool, info in result.items():
                avail = "AVAILABLE" if info.get("available") else "NOT AVAILABLE"
                ver = info.get("version", "")
                err = info.get("error", "")
                print(f"  {tool:20s} {avail:15s} {ver or ''}")
                if err:
                    print(f"  {'':20s} {err}")
        sys.exit(0)

    if args.validate:
        result = validate_oscal_deep(
            args.validate, artifact_type=args.artifact_type,
            project_id=args.project_id, db_path=args.db_path,
        )
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            status = "VALID" if result["valid"] else "INVALID"
            print(f"{status}: {args.validate}")
            print(f"  Validators run: {result['validators_run']}, "
                  f"skipped: {result['validators_skipped']}")
            for v in result["validators"]:
                name = v["validator"]
                if v.get("skipped"):
                    print(f"  [{name}] SKIPPED — {v.get('reason', '')}")
                else:
                    s = "PASS" if v.get("valid") else "FAIL"
                    print(f"  [{name}] {s} ({v.get('duration_ms', 0)}ms)")
                    for err in v.get("errors", [])[:5]:
                        print(f"    - {err}")
            if result["errors"]:
                print(f"\n{result['error_count']} total error(s)")
        sys.exit(0 if result["valid"] else 1)

    if args.convert:
        if not args.format:
            parser.error("--format is required with --convert")
        result = convert_oscal_format(args.convert, args.format, args.output)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            if result.get("success"):
                print(f"Converted: {result['output_path']}")
            else:
                print(f"ERROR: {result.get('error', 'unknown')}")
        sys.exit(0 if result.get("success") else 1)

    if args.resolve_profile:
        result = resolve_oscal_profile(args.resolve_profile, args.output)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            if result.get("success"):
                print(f"Resolved: {result['output_path']}")
            else:
                print(f"ERROR: {result.get('error', 'unknown')}")
        sys.exit(0 if result.get("success") else 1)

    if args.catalog_lookup:
        result = catalog_lookup(args.catalog_lookup, args.catalog_source)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            if "error" in result:
                print(result["error"])
            else:
                print(f"{result['id']}: {result['title']}")
                print(f"  Family: {result['family']}")
                desc = result.get("description", "")
                print(f"  Description: {desc[:200]}{'...' if len(desc) > 200 else ''}")
                if result.get("params"):
                    print(f"  Parameters: {len(result['params'])}")
        sys.exit(0 if "error" not in result else 1)

    if args.catalog_list:
        result = catalog_list(family=args.family, catalog_source=args.catalog_source)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            for ctrl in result["controls"]:
                enh = " (enhancement)" if ctrl.get("is_enhancement") else ""
                wd = " [WITHDRAWN]" if ctrl.get("withdrawn") else ""
                print(f"  {ctrl['id']}: {ctrl['title']}{enh}{wd}")
            print(f"\n{result['count']} controls")
        sys.exit(0)

    if args.catalog_stats:
        result = catalog_stats(catalog_source=args.catalog_source)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Source:   {result.get('source_path', 'none')}")
            print(f"Format:   {result.get('source_format', 'none')}")
            print(f"Controls: {result.get('total_controls', 0)} total "
                  f"({result.get('base_controls', 0)} base, "
                  f"{result.get('enhancements', 0)} enhancements)")
            print(f"Families: {result.get('family_count', 0)}")
        sys.exit(0)

    parser.print_help()


if __name__ == "__main__":
    main()
