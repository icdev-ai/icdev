# CUI // SP-CTI
"""tools.integrity — Software Integrity & Provenance Assessment (SIPA) engine.

Provenance-aware capability assessment for code/artifacts ingested into ICDEV.
Scans declared vs. exercised capabilities (network egress, filesystem, process
exec, dynamic code, crypto, secrets, serialization, obfuscation), reconciles
disclosed vs. undisclosed behavior, and renders an allow / review / quarantine
verdict with a weighted risk score.

Gated by the ``ICDEV_INTEGRITY_ENABLED`` feature flag (see constants).
"""
from __future__ import annotations

__all__ = [
    "constants",
]
