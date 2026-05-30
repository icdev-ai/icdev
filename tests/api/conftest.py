# CUI // SP-CTI
"""Fixtures for API contract tests."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))


@pytest.fixture(scope="session")
def api_gateway_app():
    """Return the SaaS API gateway Flask app in test mode."""
    from tools.saas.api_gateway import create_app
    app = create_app()
    app.config["TESTING"] = True
    return app
