#!/usr/bin/env python3
# CUI // SP-CTI
"""Zero-Trust stub gate — single source of truth for stub opt-in semantics.

Zero-trust device-trust and PDP integration adapters ship with *stubs* that
stand in for a live vendor API (CrowdStrike Falcon, DISA ICAM, Zscaler ZPA)
until real credentials are configured. Historically those stubs **failed
open** — fabricating a permissive result (healthy device / permit decision)
whenever the live API was unavailable. In production that is a security
defect: an unreachable PDP or EDR must default to *deny*, not *permit*.

This module centralises the one policy question those adapters need to ask:

    "Are stub results allowed to be honored in this environment?"

Production leaves ``ICDEV_ZT_ALLOW_STUB`` unset, so ``stub_allowed()`` returns
False and every stub path fails **closed** (deny / device status unknown).
Dev, CI, and e2e set ``ICDEV_ZT_ALLOW_STUB=1`` to opt back into the permissive
stub behavior so local workflows keep functioning without live vendor APIs.

Keeping the truthy-value semantics in exactly one place avoids each adapter
re-implementing (and subtly disagreeing about) what "enabled" means.

NIST 800-53: AC-3, IA-3, SA-9, ZTA Pillars 2 & 5
"""
from __future__ import annotations

import os

#: Env var that opts into honoring zero-trust stub results (fail-open).
STUB_ENV_VAR = "ICDEV_ZT_ALLOW_STUB"

_TRUTHY = ("1", "true", "yes")


def stub_allowed() -> bool:
    """Return True iff zero-trust stubs may be honored in this environment.

    Reads ``ICDEV_ZT_ALLOW_STUB`` and returns True only when it is set to a
    truthy value ("1", "true", "yes"; case-insensitive). Unset, empty, or any
    other value returns False — i.e. **fail closed** by default so that an
    unavailable live adapter denies rather than fabricating a permissive
    result.
    """
    val = os.environ.get(STUB_ENV_VAR, "").strip().lower()
    return val in _TRUTHY
