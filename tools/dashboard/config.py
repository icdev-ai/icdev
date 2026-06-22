# [TEMPLATE: CUI // SP-CTI]
"""
Dashboard configuration.
Loads settings from args/monitoring_config.yaml and args/cui_markings.yaml
with environment variable overrides.
"""

import os
from pathlib import Path

from tools.config.core_profile import apply_active_profile_env_defaults, profile_default

# Base directory: project root (3 levels up from this file)
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Load .env early so all os.environ reads below pick up .env values
try:
    from dotenv import load_dotenv

    load_dotenv(BASE_DIR / ".env")
except ImportError:
    _env_file = BASE_DIR / ".env"
    if _env_file.exists():
        for _line in _env_file.read_text(encoding="utf-8").splitlines():
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _, _v = _line.partition("=")
                _k, _v = _k.strip(), _v.strip().strip('"').strip("'")
                if _k and _k not in os.environ:
                    os.environ[_k] = _v

# ---------------------------------------------------------------------------
# YAML loading (pure-Python fallback if PyYAML is not installed)
# ---------------------------------------------------------------------------


def _load_yaml(filepath: Path) -> dict:
    """Load a YAML file. Uses PyYAML if available, otherwise a minimal parser."""
    try:
        import yaml

        with open(filepath, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except ImportError:
        return _simple_yaml_parse(filepath)


def _simple_yaml_parse(filepath: Path) -> dict:
    """Minimal YAML-subset parser for flat and one-level nested mappings."""
    data: dict = {}
    if not filepath.exists():
        return data
    current_section = None
    with open(filepath, "r", encoding="utf-8") as fh:
        for raw_line in fh:
            line = raw_line.rstrip()
            # Skip blanks / comments
            if not line or line.lstrip().startswith("#"):
                continue
            # Detect indentation
            stripped = line.lstrip()
            indent = len(line) - len(stripped)
            if ":" not in stripped:
                continue
            key, _, value = stripped.partition(":")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if indent == 0:
                if value:
                    data[key] = value
                else:
                    current_section = key
                    data[current_section] = {}
            elif current_section is not None:
                data[current_section][key] = value
    return data


# ---------------------------------------------------------------------------
# Load config files
# ---------------------------------------------------------------------------

_monitoring_path = BASE_DIR / "args" / "monitoring_config.yaml"
_cui_path = BASE_DIR / "args" / "cui_markings.yaml"

MONITORING_CONFIG = _load_yaml(_monitoring_path) if _monitoring_path.exists() else {}
CUI_CONFIG = _load_yaml(_cui_path) if _cui_path.exists() else {}

# ---------------------------------------------------------------------------
# Resolved settings (with env-var overrides)
# ---------------------------------------------------------------------------

# Database
DB_PATH = os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db"))

# Active core profile defaults (env var wins, then profile, then hardcoded default)
apply_active_profile_env_defaults()
_PROFILE_DEFAULTS_APPLIED = True

# CUI banner text
CUI_BANNER_TOP = os.environ.get(
    "ICDEV_CUI_BANNER_TOP",
    CUI_CONFIG.get("banner_top", ""),
)
CUI_BANNER_BOTTOM = os.environ.get(
    "ICDEV_CUI_BANNER_BOTTOM",
    CUI_CONFIG.get("banner_bottom", ""),
)
CUI_DESIGNATION = CUI_CONFIG.get("designation_indicator", {})
CUI_PORTION_MARKING = CUI_CONFIG.get("portion_marking", "(CUI)")

# Server
PORT = int(profile_default("ICDEV_DASHBOARD_PORT", "5050"))
HOST = profile_default("ICDEV_DASHBOARD_HOST", "0.0.0.0")  # nosec B104 — dashboard default; override via ICDEV_DASHBOARD_HOST
_DEBUG_VAL = str(profile_default("ICDEV_DASHBOARD_DEBUG", "false")).lower()
DEBUG = _DEBUG_VAL in ("1", "true", "yes")

# Monitoring thresholds (from monitoring_config.yaml)
SELF_HEALING = MONITORING_CONFIG.get("self_healing", {})
HEALTH_CHECK = MONITORING_CONFIG.get("health_check", {})
SLA = MONITORING_CONFIG.get("sla", {})

# CUI banner toggle (D173) — env var takes precedence, then active profile, then args/cui_markings.yaml
_CUI_BANNER_DEFAULT = profile_default(
    "ICDEV_CUI_BANNER_ENABLED",
    str(CUI_CONFIG.get("enabled", "false")),
)
CUI_BANNER_ENABLED = str(_CUI_BANNER_DEFAULT).lower() in ("1", "true", "yes")

# Dashboard auth (D169-D172)
DASHBOARD_SECRET = os.environ.get(
    "ICDEV_DASHBOARD_SECRET",
    "",  # Empty = auto-generate at app startup
)

# BYOK — Bring Your Own Key (D175-D178)
_BYOK_VAL = str(profile_default("ICDEV_BYOK_ENABLED", "false")).lower()
BYOK_ENABLED = _BYOK_VAL in ("1", "true", "yes")

BYOK_ENCRYPTION_KEY = os.environ.get("ICDEV_BYOK_ENCRYPTION_KEY", "")

# Classification
DEFAULT_CLASSIFICATION = profile_default("ICDEV_CLASSIFICATION", "")
