#!/usr/bin/env python3
# CUI // SP-CTI
"""Active extension hook system (Phase 44 — D261-D264).

Adapted from Agent Zero's extension point architecture. Extensions can be
behavioral (modify data flowing through) or observational (log/audit only).
"""

from tools.extensions.extension_manager import (
    ExtensionPoint,
    ExtensionHandler,
    ExtensionManager,
    extension_manager,
)

__all__ = [
    "ExtensionPoint",
    "ExtensionHandler",
    "ExtensionManager",
    "extension_manager",
]
