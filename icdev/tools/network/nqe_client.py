# [CUI // SP-CTI]
"""ICDEV™ NDC — NQE (Network Query Engine) client with local fallback.

Provides ``FallbackNQEClient``, which tries a live Forward Networks NQE
endpoint and falls back to translating the NQE path to an ICDEV IQE query
when the endpoint is unavailable or unconfigured.

Environment variables (all optional):
    NQE_BASE_URL      — Forward Networks NQE REST base URL
    NQE_API_KEY       — API key / Bearer token for NQE
    NQE_TIMEOUT       — HTTP timeout in seconds (default 30)
    ICDEV_LLM_NQE_TRANSLATE — set "true" to enable LLM fallback translation

Usage::

    from tools.network.nqe_client import FallbackNQEClient

    client = FallbackNQEClient()
    result = client.run_query("network.devices[platform.ostype]")
    result = client.run_query("network.interfaces[errors]", network_id="topo-abc")
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Mapping table (loaded from context file at import time)
# ---------------------------------------------------------------------------

_MAPPING_PATH = (
    Path(__file__).resolve().parents[2] / "context" / "nql" / "nql_to_iqe_mapping.md"
)

# { "network.devices[platform.ostype]": "foreach d in network.devices select ..." }
_NQL_TO_IQE: dict[str, str] = {}


def _load_mapping() -> None:
    """Parse the markdown mapping table into _NQL_TO_IQE."""
    try:
        text = _MAPPING_PATH.read_text(encoding="utf-8")
    except Exception as exc:
        logger.warning("Could not load NQL→IQE mapping: %s", exc)
        return

    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|") or line.startswith("| NQE") or line.startswith("|---"):
            continue
        parts = [p.strip() for p in line.strip("|").split("|")]
        if len(parts) >= 2:
            nql_key = parts[0].strip("`")
            iqe_val = parts[1].strip("`")
            if nql_key and iqe_val and nql_key.startswith("network."):
                _NQL_TO_IQE[nql_key] = iqe_val


_load_mapping()

# ---------------------------------------------------------------------------
# Bracket normalisation → canonical key
# ---------------------------------------------------------------------------

# Maps bracket content patterns to the suffix appended to the collection name
_BRACKET_NORM: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"platform\.ostype", re.I), "platform.ostype"),
    (re.compile(r"platform\.osversion", re.I), "platform.osversion"),
    (re.compile(r"platform\.vendor", re.I), "platform.vendor"),
    (re.compile(r"platform\.hardware", re.I), "platform.hardware"),
    (re.compile(r"\berrors?\b", re.I), "errors"),
    (re.compile(r"status\s*==\s*[\"']down[\"']", re.I), "down"),
    (re.compile(r"state\s*!=\s*[\"']Established[\"']", re.I), "down"),
    (re.compile(r"state\s*!=\s*[\"']Full[\"']", re.I), "down"),
    (re.compile(r"state\s*!=\s*[\"']Up[\"']", re.I), "down"),
    (re.compile(r"mpls_enabled", re.I), "mpls"),
    (re.compile(r"type\s*==\s*[\"']ibgp[\"']", re.I), "ibgp"),
    (re.compile(r"type\s*==\s*[\"']ebgp[\"']", re.I), "ebgp"),
    (re.compile(r"(valid|best)\s*==\s*false", re.I), "invalid"),
    (re.compile(r"action\s*==\s*[\"']deny[\"']", re.I), "deny"),
    (re.compile(r"action\s*==\s*[\"']permit[\"']", re.I), "permit"),
    (re.compile(r"(length|prefix_length)\s*[><=]+\s*3[12]", re.I), "host"),
    (re.compile(r"management\b", re.I), "management"),
    (re.compile(r"location\b", re.I), "location"),
    (re.compile(r"config\b", re.I), "config"),
    (re.compile(r"uptime\b", re.I), "uptime"),
    (re.compile(r"ip\b", re.I), "ip"),
    (re.compile(r"switchport\b", re.I), "switchport"),
    (re.compile(r"counters?\b", re.I), "counters"),
]


def _normalise_nql(nql: str) -> str:
    """Return a canonical mapping key from a raw NQE path expression.

    e.g. ``"network.devices[platform.ostype == 'ios-xe']"``
         → ``"network.devices[platform.ostype]"``
    """
    nql = nql.strip()
    m = re.match(r"^([a-z_.]+)(?:\[(.+)\])?$", nql, re.I)
    if not m:
        return nql
    collection = m.group(1).rstrip(".")
    bracket = (m.group(2) or "").strip()
    if not bracket:
        return collection
    for pattern, suffix in _BRACKET_NORM:
        if pattern.search(bracket):
            return f"{collection}[{suffix}]"
    # Unknown bracket — keep collection only (bare lookup)
    return collection


# ---------------------------------------------------------------------------
# Local IQE execution stub
# ---------------------------------------------------------------------------

def _run_iqe_local(iqe: str, network_id: str | None) -> list[dict[str, Any]]:
    """Execute an IQE query against the local network canvas DB.

    Returns a list of row dicts.  Returns an empty list on any error so the
    caller always gets a valid (possibly empty) result set.
    """
    try:
        from tools.db.storage import get_canvas_connection
        conn = get_canvas_connection("NC_STORAGE_BACKEND")
    except Exception as exc:
        logger.debug("Could not open canvas connection: %s", exc)
        return []

    # Determine which table to query based on the IQE collection keyword
    collection_map = {
        "network.devices": ("nc_nodes", ["id", "label", "object_type", "config"]),
        "network.interfaces": ("nc_nodes", ["id", "label", "object_type"]),
        "network.bgp.sessions": ("nc_nodes", ["id", "label", "config"]),
        "network.links": ("nc_edges", ["id", "source_id", "target_id"]),
        "network.acls": ("nc_nodes", ["id", "label", "config"]),
        "network.acls.rules": ("nc_nodes", ["id", "label", "config"]),
        "network.vlans": ("nc_nodes", ["id", "label", "config"]),
        "network.prefixes": ("nc_nodes", ["id", "label", "config"]),
        "network.ospf.neighbors": ("nc_nodes", ["id", "label", "config"]),
        "network.isis.adjacencies": ("nc_nodes", ["id", "label", "config"]),
        "network.mpls.lsps": ("nc_nodes", ["id", "label", "config"]),
    }

    # Extract collection name from IQE "foreach … in <collection> …"
    coll_match = re.search(r"\bin\s+(network\.[a-z_.]+)", iqe, re.I)
    collection = coll_match.group(1).rstrip(".") if coll_match else ""
    table, cols = collection_map.get(collection, ("nc_nodes", ["id", "label", "object_type"]))

    try:
        where_parts: list[str] = ["1=1"]
        params: list[Any] = []

        if network_id:
            where_parts.append("topology_id = %s")
            params.append(network_id)

        # Surface object_type filter for interface / BGP / OSPF sub-collections
        _OBJECT_TYPE_HINTS = {
            "network.interfaces": "interface",
            "network.bgp.sessions": "bgp_session",
            "network.bgp.routes": "bgp_route",
            "network.ospf.neighbors": "ospf_neighbor",
            "network.isis.adjacencies": "isis_adjacency",
            "network.mpls.lsps": "mpls_lsp",
            "network.acls": "acl",
            "network.acls.rules": "acl_rule",
            "network.vlans": "vlan",
            "network.prefixes": "prefix",
        }
        hint = _OBJECT_TYPE_HINTS.get(collection)
        if hint and table == "nc_nodes":
            where_parts.append("object_type = %s")
            params.append(hint)

        col_list = ", ".join(cols)
        sql = f"SELECT {col_list} FROM {table} WHERE {' AND '.join(where_parts)} LIMIT 500"

        import json as _json

        rows = conn.execute(sql, params).fetchall()

        col_names = cols
        result: list[dict[str, Any]] = []
        for row in rows:
            row_dict: dict[str, Any] = {}
            for i, col in enumerate(col_names):
                val = row[i] if isinstance(row, (list, tuple)) else row.get(col)
                if col == "config" and isinstance(val, str):
                    try:
                        val = _json.loads(val)
                    except Exception:
                        pass
                row_dict[col] = val
            result.append(row_dict)
        return result
    except Exception as exc:
        logger.debug("IQE local execution failed: %s", exc)
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# FallbackNQEClient
# ---------------------------------------------------------------------------

class FallbackNQEClient:
    """NQE client that tries a live endpoint then falls back to local IQE.

    Args:
        base_url:   Forward Networks NQE REST base URL.  Defaults to
                    ``NQE_BASE_URL`` env var or ``None`` (local-only).
        api_key:    Bearer token / API key.  Defaults to ``NQE_API_KEY``.
        timeout:    HTTP timeout in seconds.  Defaults to ``NQE_TIMEOUT`` or 30.
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: int | None = None,
    ) -> None:
        self._base_url = (base_url or os.environ.get("NQE_BASE_URL", "")).rstrip("/")
        self._api_key = api_key or os.environ.get("NQE_API_KEY", "")
        self._timeout = timeout or int(os.environ.get("NQE_TIMEOUT", "30"))
        self._llm_translate = os.environ.get("ICDEV_LLM_NQE_TRANSLATE", "").lower() == "true"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_query(self, nql: str, network_id: str | None = None) -> dict[str, Any]:
        """Run an NQE query, falling back to local IQE when needed.

        Args:
            nql:        NQE collection path expression, e.g.
                        ``"network.devices[platform.ostype]"`` or a full
                        IQE ``foreach …`` statement.
            network_id: Optional topology / network scope identifier passed
                        to the live endpoint as ``networkId`` and used to
                        filter local DB rows by ``topology_id``.

        Returns:
            ``{"rows": [...], "iqe": str|None, "source": str, "nql": str,
               "error": str|None}``

            *source* is one of ``"nqe_api"``, ``"local_mapping"``,
            ``"local_heuristic"``, ``"llm_translation"``, or ``"empty"``.
        """
        nql = (nql or "").strip()
        if not nql:
            return {"rows": [], "iqe": None, "source": "empty", "nql": nql, "error": "empty query"}

        # 1. Try the live NQE API
        if self._base_url:
            api_result = self._call_nqe_api(nql, network_id)
            if api_result is not None:
                return {"rows": api_result, "iqe": None, "source": "nqe_api", "nql": nql, "error": None}

        # 2. Resolve via mapping table
        canonical = _normalise_nql(nql)
        iqe = _NQL_TO_IQE.get(canonical)
        if iqe:
            rows = _run_iqe_local(iqe, network_id)
            return {"rows": rows, "iqe": iqe, "source": "local_mapping", "nql": nql, "error": None}

        # 3. Heuristic: strip brackets, query bare collection
        bare = re.sub(r"\[.*?\]", "", nql).strip().rstrip(".")
        iqe_bare = _NQL_TO_IQE.get(bare)
        if iqe_bare:
            rows = _run_iqe_local(iqe_bare, network_id)
            return {"rows": rows, "iqe": iqe_bare, "source": "local_heuristic", "nql": nql, "error": None}

        # Heuristic foreach passthrough (already IQE syntax)
        if nql.lower().startswith("foreach "):
            rows = _run_iqe_local(nql, network_id)
            return {"rows": rows, "iqe": nql, "source": "local_heuristic", "nql": nql, "error": None}

        # 4. LLM translation (opt-in)
        if self._llm_translate:
            iqe_llm = self._translate_via_llm(nql)
            if iqe_llm:
                rows = _run_iqe_local(iqe_llm, network_id)
                return {"rows": rows, "iqe": iqe_llm, "source": "llm_translation", "nql": nql, "error": None}

        logger.warning("NQL path not mappable: %s (canonical: %s)", nql, canonical)
        return {"rows": [], "iqe": None, "source": "empty", "nql": nql, "error": "unmapped"}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _call_nqe_api(self, nql: str, network_id: str | None) -> list[dict[str, Any]] | None:
        """POST to the NQE REST endpoint.  Returns rows list or None on failure."""
        try:
            from tools.http.client import request as _http_request
        except ImportError:
            try:
                import requests as _requests
                def _http_request(method: str, url: str, **kwargs: Any):  # type: ignore[misc]
                    return _requests.request(method, url, **kwargs)
            except ImportError:
                return None

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        payload: dict[str, Any] = {"query": nql}
        if network_id:
            payload["networkId"] = network_id

        try:
            resp = _http_request(
                "POST",
                f"{self._base_url}/api/nqe",
                json=payload,
                headers=headers,
                timeout=self._timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("rows") or data.get("items") or []
        except Exception as exc:
            logger.debug("NQE API call failed (%s): %s", self._base_url, exc)
            return None

    def _translate_via_llm(self, nql: str) -> str | None:
        """Use the LLM router to translate an NQE path to an IQE query."""
        try:
            from tools.llm.router import LLMRouter
            from tools.llm.provider import LLMRequest

            mapping_hint = (
                "Use the IQE grammar (foreach/where/select). "
                "Collections: network.devices, network.interfaces, network.links, "
                "network.bgp.sessions, network.bgp.routes, network.acls, network.acls.rules, "
                "network.vlans, network.prefixes, network.ospf.neighbors, "
                "network.isis.adjacencies, network.mpls.lsps."
            )
            prompt = (
                f"Translate this NQE collection path to an ICDEV IQE query.\n"
                f"NQE: {nql}\n"
                f"{mapping_hint}\n"
                f"Return only the IQE query string, nothing else."
            )
            router = LLMRouter()
            req = LLMRequest(messages=[{"role": "user", "content": prompt}])
            result = router.invoke("nql_translation", req)
            iqe = (result.content or "").strip()
            return iqe if iqe.lower().startswith("foreach ") else None
        except Exception as exc:
            logger.debug("LLM NQE translation failed: %s", exc)
            return None


# ---------------------------------------------------------------------------
# NQEClient — live Forward Networks API client
# ---------------------------------------------------------------------------

class NQEClient:
    """HTTP client for the Forward Networks NQE REST API.

    Args:
        api_key:   Bearer token for the FWD API.
        base_url:  Base URL of the Forward Networks platform
                   (e.g. ``"https://fwd.example.com"``).
    """

    def __init__(self, api_key: str, base_url: str) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")

    def run_query(self, nql: str, network_id: str | None = None) -> dict[str, Any]:
        """POST an NQL query to the Forward Networks NQE endpoint.

        Returns:
            ``{"rows": [...], "total": int, "source": "fwd-live"}``
            On error: ``{"rows": [], "total": 0, "source": "fwd-live", "error": str}``
        """
        try:
            from tools.http.client import request as _req
            resp = _req(
                "POST",
                f"{self._base_url}/api/networks/nqe",
                json=self._payload(nql, network_id),
                headers=self._headers(),
                timeout=30,
            )
        except ImportError:
            try:
                import requests as _requests
                resp = _requests.post(
                    f"{self._base_url}/api/networks/nqe",
                    json=self._payload(nql, network_id),
                    headers=self._headers(),
                    timeout=30,
                )
            except Exception as exc:
                logger.error("NQEClient.run_query error: %s", exc)
                return {"rows": [], "total": 0, "source": "fwd-live", "error": str(exc)}
        except Exception as exc:
            logger.error("NQEClient.run_query error: %s", exc)
            return {"rows": [], "total": 0, "source": "fwd-live", "error": str(exc)}

        try:
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.error("NQEClient response parse error: %s", exc)
            return {"rows": [], "total": 0, "source": "fwd-live", "error": str(exc)}

        rows = data.get("items", data.get("rows", []))
        return {"rows": rows, "total": data.get("total", len(rows)), "source": "fwd-live"}

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def _payload(self, nql: str, network_id: str | None) -> dict[str, Any]:
        payload: dict[str, Any] = {"query": nql}
        if network_id:
            payload["networkId"] = network_id
        return payload
