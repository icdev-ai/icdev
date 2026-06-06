# CUI // SP-CTI
"""ICDEV™ Structured Logging Package.

Central NDJSON logging standard for all ICDEV components.
All modules must use get_logger() rather than logging.getLogger() directly.
"""
from icdev.tools.logging.icdev_logger import get_logger

__all__ = ["get_logger"]
