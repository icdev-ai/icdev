# //CUI
# CONTROLLED UNCLASSIFIED INFORMATION
# Authorized distribution limited to authorized personnel only.
# Handling: CUI Basic per 32 CFR Part 2002
# //CUI

"""Pytest configuration for test-svc microservice."""

import pytest
from httpx import AsyncClient, ASGITransport

from src.test_svc.main import app


@pytest.fixture
async def async_client():
    """Async test client for FastAPI."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
