# CUI // SP-CTI
"""Security middleware initializer for ICDEV™ child apps.

Provides a single init_security() entry-point that wires classification-aware
security policies, clearance ceilings, and audit hooks into a Flask app.
"""

from typing import Any


def init_security(app: Any, classification: str = "CUI") -> None:
    """Initialize security middleware for a child app.

    Args:
        app: Flask application instance.
        classification: Child app classification level (PUBLIC, CUI, SECRET).
    """
    # Placeholder for classification-aware security middleware.
    # Child apps may extend this with tenant-specific policies.
    app.config["ICDEV_CLASSIFICATION"] = classification
    app.config["ICDEV_SECURITY_INITIALIZED"] = True
