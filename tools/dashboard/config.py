"""
Dashboard configuration.
Loads settings from args/monitoring_config.yaml and args/cui_markings.yaml
with environment variable overrides.
"""

import os
from pathlib import Path

# Base directory: project root (3 levels up from this file)
BASE_DIR = Path(__file__).resolve().parent.parent.parent

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

# CUI banner text
CUI_BANNER_TOP = os.environ.get(
    "ICDEV_CUI_BANNER_TOP",
    CUI_CONFIG.get("banner_top", "CUI // SP-CTI"),
)
CUI_BANNER_BOTTOM = os.environ.get(
    "ICDEV_CUI_BANNER_BOTTOM",
    CUI_CONFIG.get("banner_bottom", "CUI // SP-CTI"),
)
CUI_DESIGNATION = CUI_CONFIG.get("designation_indicator", {})
CUI_PORTION_MARKING = CUI_CONFIG.get("portion_marking", "(CUI)")

# Server
PORT = int(os.environ.get("ICDEV_DASHBOARD_PORT", "5000"))
DEBUG = os.environ.get("ICDEV_DASHBOARD_DEBUG", "false").lower() in ("1", "true", "yes")

# Monitoring thresholds (from monitoring_config.yaml)
SELF_HEALING = MONITORING_CONFIG.get("self_healing", {})
HEALTH_CHECK = MONITORING_CONFIG.get("health_check", {})
SLA = MONITORING_CONFIG.get("sla", {})

# Classification
DEFAULT_CLASSIFICATION = os.environ.get("ICDEV_CLASSIFICATION", "CUI")
