"""gcpl-cdrl-10 — POST /api/cpmp/sam/sync-awards answers inside a request budget.

The route calls SAM.gov synchronously. It used a 60s outbound timeout, so a slow
SAM.gov held the request past the 10s E2E client timeout (qa-fail-b535d046a9341241).
The error message also echoed the request URL, api_key included, back to the client.
"""

import importlib

import pytest

sync = importlib.import_module("tools.govcon.sam_contract_sync")

FAKE_KEY = "SAM-00000000-test-key-do-not-leak"


@pytest.fixture
def captured(monkeypatch):
    seen = {}

    def fake_get(url, params=None, headers=None, timeout=30):
        seen["timeout"] = timeout
        return None, f"401 Client Error: Unauthorized for url: {url}?api_key={params['api_key']}&limit=100"

    monkeypatch.setenv(sync.API_KEY_ENV, FAKE_KEY)
    monkeypatch.setattr(sync, "_safe_get", fake_get)
    return seen


def test_outbound_call_fits_inside_the_request_budget(captured):
    sync.sync_awards()
    assert captured["timeout"] <= 8


def test_upstream_error_does_not_echo_the_api_key(captured):
    result = sync.sync_awards()
    assert result["status"] == "error"
    assert FAKE_KEY not in result["message"]
    assert "api_key=***" in result["message"]


def test_missing_key_still_degrades_gracefully(monkeypatch):
    monkeypatch.delenv(sync.API_KEY_ENV, raising=False)
    result = sync.sync_awards()
    assert result["status"] == "error"
    assert sync.API_KEY_ENV in result["message"]
