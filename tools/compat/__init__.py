# CUI // SP-CTI
"""ICDEV cross-platform compatibility module.

Centralizes OS detection and platform-specific behavior (D145).
Uses only Python stdlib — air-gap safe.
"""
from tools.compat.platform_utils import (  # noqa: F401
    IS_WINDOWS,
    IS_MACOS,
    IS_LINUX,
    PLATFORM_NAME,
    get_temp_dir,
    get_home_dir,
    get_npx_cmd,
    get_python_cmd,
    get_project_root,
    get_data_dir,
    get_config_dir,
    normalize_path,
    ensure_utf8_console,
)
