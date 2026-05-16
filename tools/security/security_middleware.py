#!/usr/bin/env python3
# CUI // SP-CTI
from __future__ import annotations
"""Thin wrapper re-exporting ``tools.security.middleware``.

The manifest documents ``tools/security/security_middleware.py`` as the
middleware entry point.  This file ensures that import path works.
"""

from tools.security.middleware import init_security, DEFAULT_PUBLIC_ENDPOINTS, _is_public_endpoint

__all__ = ["init_security", "DEFAULT_PUBLIC_ENDPOINTS", "_is_public_endpoint"]
