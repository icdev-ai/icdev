# CUI // SP-CTI
"""ICDEV™ security exceptions."""
from __future__ import annotations


class TierAccessDenied(Exception):
    """Raised when the active license tier is below a canvas's min_tier."""

    def __init__(self, canvas_key: str, required_tier: str) -> None:
        self.canvas_key = canvas_key
        self.required_tier = required_tier
        super().__init__(
            f"License tier too low: canvas '{canvas_key}' requires '{required_tier}'"
        )
