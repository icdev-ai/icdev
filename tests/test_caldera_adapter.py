# CUI // SP-CTI
"""Unit tests for tools/security_canvas/caldera_adapter.py — 4 cases (mocked HTTP)."""
from __future__ import annotations

import json
import pathlib
import socket
import urllib.error
import urllib.request
from typing import Any
from unittest.mock import MagicMock, patch

from tools.security_canvas.caldera_adapter import CalderaAdapter

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_ABILITIES = [
    {
        "ability_id": "abc-001",
        "name": "PowerShell Exec",
        "technique_id": "T1059.001",
        "technique_name": "PowerShell",
    },
    {
        "ability_id": "abc-002",
        "name": "Valid Accounts",
        "technique_id": "T1078",
        "technique_name": "Valid Accounts",
    },
    {
        "ability_id": "abc-003",
        "name": "No technique ability",
        # technique_id intentionally absent
    },
]

_ADVERSARIES = [
    {
        "adversary_id": "adv-001",
        "name": "Ransomware Sim",
        "description": "Simulates ransomware TTPs",
        "atomic_ordering": ["abc-001", "abc-002"],
    },
    {
        "adversary_id": "adv-002",
        "name": "Lateral Mover",
        "description": "Credential harvesting + lateral movement",
        "atomic_ordering": ["abc-002"],
    },
]


def _mock_urlopen(payload: Any):
    """Return a context manager whose read() yields *payload* JSON-encoded."""
    raw = json.dumps(payload).encode("utf-8")
    mock_resp = MagicMock()
    mock_resp.read.return_value = raw
    mock_resp.__enter__ = lambda s: mock_resp
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


def _make_adapter(tmp_path: pathlib.Path) -> CalderaAdapter:
    return CalderaAdapter(
        "http://localhost:8888",
        api_key="TEST_KEY",
        cache_dir=tmp_path / "caldera_cache",
    )


# ---------------------------------------------------------------------------
# Case 1 — fetch_scenarios() returns list of adversary dicts
# ---------------------------------------------------------------------------

def test_fetch_scenarios_returns_list(tmp_path: pathlib.Path) -> None:
    adapter = _make_adapter(tmp_path)

    with patch.object(urllib.request, "urlopen", return_value=_mock_urlopen(_ADVERSARIES)):
        scenarios = adapter.fetch_scenarios()

    assert isinstance(scenarios, list)
    assert len(scenarios) == 2
    assert scenarios[0]["adversary_id"] == "adv-001"
    assert scenarios[1]["name"] == "Lateral Mover"


# ---------------------------------------------------------------------------
# Case 2 — ability_technique_map cached locally after first fetch
# ---------------------------------------------------------------------------

def test_ability_technique_map_cached_locally(tmp_path: pathlib.Path) -> None:
    adapter = _make_adapter(tmp_path)

    with patch.object(urllib.request, "urlopen", return_value=_mock_urlopen(_ABILITIES)):
        mapping = adapter.ability_technique_map

    # abc-001 and abc-002 have technique_id; abc-003 does not
    assert mapping == {"abc-001": "T1059.001", "abc-002": "T1078"}

    # Cache file must have been written to disk
    cache_file = tmp_path / "caldera_cache" / "ability_technique_map.json"
    assert cache_file.exists()
    persisted = json.loads(cache_file.read_text("utf-8"))
    assert isinstance(persisted, list)
    assert len(persisted) == 3


# ---------------------------------------------------------------------------
# Case 3 — graceful failure when Caldera is unreachable
# ---------------------------------------------------------------------------

def test_graceful_failure_when_unreachable(tmp_path: pathlib.Path) -> None:
    adapter = _make_adapter(tmp_path)

    def _raise(*args: Any, **kwargs: Any) -> None:
        raise urllib.error.URLError("Connection refused")

    with patch.object(urllib.request, "urlopen", side_effect=_raise):
        scenarios = adapter.fetch_scenarios()
        abilities = adapter.fetch_abilities()
        mapping = adapter.ability_technique_map
        health = adapter.health()

    assert scenarios == []
    assert abilities == []
    assert mapping == {}
    assert health["status"] == "unreachable"
    assert "error" in health


# ---------------------------------------------------------------------------
# Case 4 — on-disk cache served when Caldera subsequently unreachable
# ---------------------------------------------------------------------------

def test_disk_cache_fallback_when_unreachable(tmp_path: pathlib.Path) -> None:
    adapter = _make_adapter(tmp_path)

    # First call: Caldera reachable — populates disk cache
    with patch.object(urllib.request, "urlopen", return_value=_mock_urlopen(_ABILITIES)):
        adapter.fetch_abilities()

    # Verify disk cache was written
    cache_file = tmp_path / "caldera_cache" / "ability_technique_map.json"
    assert cache_file.exists()

    # Invalidate in-memory cache so next call hits disk
    adapter._ability_cache = None
    adapter._ability_cache_ts = 0.0

    def _raise(*args: Any, **kwargs: Any) -> None:
        raise socket.timeout()

    # Second call: Caldera times out — should fall back to disk cache
    with patch.object(urllib.request, "urlopen", side_effect=_raise):
        abilities = adapter.fetch_abilities()

    assert len(abilities) == 3
    ids = {a["ability_id"] for a in abilities}
    assert ids == {"abc-001", "abc-002", "abc-003"}
