# CUI // SP-CTI
"""ICDEV™ Security Design Canvas — MITRE Caldera REST API Adapter (read-only).

Wraps the Caldera v2 API (https://caldera.readthedocs.io/en/latest/Rest-API.html)
using stdlib urllib only (no external deps). Fetches scenarios (adversaries) and
abilities, then exposes a cached ability-ID → ATT&CK technique-ID map.

All public methods fail gracefully: connection/HTTP errors return empty
collections or a structured error dict rather than raising, so the SDC
orchestrator can degrade cleanly when Caldera is offline or not deployed.

Usage::

    from tools.security_canvas.caldera_adapter import CalderaAdapter
    adapter = CalderaAdapter("http://localhost:8888", api_key="ADMIN123")
    scenarios = adapter.fetch_scenarios()        # list of adversary dicts
    ability_map = adapter.ability_technique_map  # {"ability-id": "T1059", ...}
"""

from __future__ import annotations

import json
import pathlib
import socket
import tempfile
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

DEFAULT_TIMEOUT = 8.0
_CACHE_TTL = 300  # seconds


class CalderaAdapter:
    """MITRE Caldera REST API adapter (read-only, graceful degradation).

    Args:
        url: Base URL of the Caldera server (e.g. ``http://localhost:8888``).
        api_key: Caldera REST API key (``RED_API_KEY`` in Caldera config).
        timeout: Per-request socket timeout in seconds.
        cache_dir: Directory for the on-disk ability→technique cache. Defaults
            to a ``caldera_cache`` sub-directory inside the system temp dir.
    """

    def __init__(
        self,
        url: str,
        api_key: str = "ADMIN123",
        timeout: float = DEFAULT_TIMEOUT,
        cache_dir: Optional[pathlib.Path] = None,
    ) -> None:
        self._url = url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout
        self._cache_dir = cache_dir or pathlib.Path(
            tempfile.gettempdir(), "caldera_cache"
        )
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._cache_file = self._cache_dir / "ability_technique_map.json"

        # In-memory caches
        self._ability_cache: Optional[List[Dict[str, Any]]] = None
        self._ability_cache_ts: float = 0.0
        self._scenario_cache: Optional[List[Dict[str, Any]]] = None
        self._scenario_cache_ts: float = 0.0

    # ------------------------------------------------------------------ #
    # Internal HTTP helpers
    # ------------------------------------------------------------------ #

    def _request(self, path: str) -> Any:
        """GET *path* and return parsed JSON; return error dict on failure."""
        full_url = f"{self._url}{path}"
        req = urllib.request.Request(  # noqa: S310 — internal management network
            full_url,
            headers={
                "KEY": self._api_key,
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:  # noqa: S310  # nosec B310
                raw = resp.read()
                if not raw:
                    return []
                return json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return {"status": "error", "error": f"HTTP {exc.code}: {exc.reason}"}
        except urllib.error.URLError as exc:
            return {"status": "unreachable", "error": str(exc.reason)}
        except socket.timeout:
            return {"status": "unreachable", "error": "timeout"}
        except (OSError, ConnectionError) as exc:
            return {"status": "unreachable", "error": str(exc)}
        except json.JSONDecodeError as exc:
            return {"status": "error", "error": f"JSON decode: {exc}"}

    @staticmethod
    def _is_error(data: Any) -> bool:
        return isinstance(data, dict) and data.get("status") in (
            "error",
            "unreachable",
        )

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def health(self) -> Dict[str, Any]:
        """GET /api/v2/health — reachability probe.

        Returns ``{"status": "ok", "version": "..."}`` when Caldera is up, or
        ``{"status": "unreachable", "error": "..."}`` when it is not.
        """
        data = self._request("/api/v2/health")
        if self._is_error(data):
            return {"status": data.get("status", "error"), "error": data.get("error")}
        if isinstance(data, dict):
            return {"status": "ok", "version": data.get("version", "unknown")}
        return {"status": "ok"}

    def fetch_scenarios(self) -> List[Dict[str, Any]]:
        """Fetch all adversary profiles (scenarios) from Caldera.

        Caldera adversaries are the top-level "scenario" objects: each contains
        an ``adversary_id``, ``name``, ``description``, and an ``atomic_ordering``
        list of ability IDs.

        Returns an empty list when Caldera is unreachable or returns an error.
        The result is cached for :attr:`_CACHE_TTL` seconds.
        """
        now = time.monotonic()
        if (
            self._scenario_cache is not None
            and now - self._scenario_cache_ts < _CACHE_TTL
        ):
            return self._scenario_cache

        data = self._request("/api/v2/adversaries")
        if self._is_error(data) or not isinstance(data, list):
            return []

        self._scenario_cache = data
        self._scenario_cache_ts = now
        return data

    def fetch_abilities(self) -> List[Dict[str, Any]]:
        """Fetch all abilities from Caldera.

        Each ability contains ``ability_id``, ``name``, ``description``, and
        ``technique_id`` / ``technique_name`` fields (ATT&CK technique mapping).

        Returns an empty list when Caldera is unreachable. Result is cached for
        :attr:`_CACHE_TTL` seconds (in-memory + on-disk).
        """
        now = time.monotonic()
        if (
            self._ability_cache is not None
            and now - self._ability_cache_ts < _CACHE_TTL
        ):
            return self._ability_cache

        data = self._request("/api/v2/abilities")
        if self._is_error(data) or not isinstance(data, list):
            # Try loading from on-disk cache before returning empty
            if self._cache_file.exists():
                try:
                    cached = json.loads(self._cache_file.read_text("utf-8"))
                    if isinstance(cached, list):
                        self._ability_cache = cached
                        self._ability_cache_ts = now
                        return cached
                except (json.JSONDecodeError, OSError):
                    pass
            return []

        self._ability_cache = data
        self._ability_cache_ts = now
        # Persist to disk so future cold-starts can degrade gracefully
        self._persist_ability_cache(data)
        return data

    def _persist_ability_cache(self, abilities: List[Dict[str, Any]]) -> None:
        """Write raw ability list to disk as the fallback cache."""
        try:
            self._cache_file.write_text(
                json.dumps(abilities, ensure_ascii=False), encoding="utf-8", newline=""
            )
        except OSError:
            pass

    @property
    def ability_technique_map(self) -> Dict[str, str]:
        """Return a dict mapping ``ability_id`` → ATT&CK ``technique_id``.

        The map is derived from :meth:`fetch_abilities`. Abilities that have no
        ``technique_id`` field are omitted. If Caldera is unreachable the method
        returns whatever was previously persisted to the local cache file, or an
        empty dict.

        Example::

            {
                "9a30740d-3aa8-4c23-8efa-d51215e8b09c": "T1059.001",
                "3b2e3b2e-1234-5678-abcd-ef1234567890": "T1078",
            }
        """
        abilities = self.fetch_abilities()
        result: Dict[str, str] = {}
        for ability in abilities:
            ability_id = ability.get("ability_id") or ability.get("id", "")
            technique_id = ability.get("technique_id", "")
            if ability_id and technique_id:
                result[ability_id] = technique_id
        return result

    def fetch_operations(self) -> List[Dict[str, Any]]:
        """Fetch all completed/running operations from Caldera.

        Returns an empty list when Caldera is unreachable.
        """
        data = self._request("/api/v2/operations")
        if self._is_error(data) or not isinstance(data, list):
            return []
        return data


def get_adapter(url: str, **kwargs: Any) -> CalderaAdapter:
    """Factory helper for the SDC adapter registry pattern."""
    return CalderaAdapter(url, **kwargs)
