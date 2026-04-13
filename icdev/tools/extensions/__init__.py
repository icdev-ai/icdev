#!/usr/bin/env python3
# CUI // SP-CTI
"""Active extension hook system (Phase 44 — D261-D264).

Independent ICDEV implementation of pluggable extension hooks. Extensions
can be behavioral (modify data flowing through) or observational (log/audit
only). Category-level concept is similar to Agent Zero's extension system
(MIT) but the architectures differ — ICDEV uses an enum-keyed dispatcher
with explicit register() API; Agent Zero uses decorator + base class
inheritance. See OPT-73 audit for the full structural comparison.
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
