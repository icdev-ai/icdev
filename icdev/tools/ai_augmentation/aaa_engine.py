# CUI // SP-CTI
"""AAC engine entry point — re-exports run_scan from the icdev.tools namespace."""
from icdev.tools.ai_augmentation.engine import run_scan  # noqa: F401

__all__ = ["run_scan"]
