#!/usr/bin/env python3
# CUI // SP-CTI
"""Compliance requirement count verifier."""
from __future__ import annotations

REQUIRED_REQUIREMENT_COUNT: int = 2


def count_and_verify_requirements(requirements: list) -> dict:
    count = len(requirements)
    success = count == REQUIRED_REQUIREMENT_COUNT
    if success:
        message = f"Verified: {count} requirement(s) present as expected."
    else:
        message = f"Count mismatch: expected {REQUIRED_REQUIREMENT_COUNT}, got {count}."
    return {
        "success": success,
        "count": count,
        "expected": REQUIRED_REQUIREMENT_COUNT,
        "message": message,
    }
