# CUI // SP-CTI
"""tools/sharepoint/client — On-prem SharePoint REST client.

Thin, stdlib-adjacent wrapper around SharePoint Server's ``/_api/web/*``
REST surface. Handles NTLM / Kerberos / Basic auth selection, exponential
backoff on transient 5xx, and hard-fail on 401.

Intended for SharePoint Server 2016 / 2019 / Subscription Edition.
SharePoint Online / M365 would use Microsoft Graph + MSAL — that path is
NOT implemented here (deferred by Phase C follow-up plan).

Dependency posture
------------------
- `requests` is a hard dep (already on the runtime).
- `requests-ntlm` and `requests-kerberos` are OPTIONAL:
  * Imported lazily inside ``_build_auth``.
  * Missing package → ``ImportError`` with the pip-install hint; module
    still imports and ``auth_mode='basic'`` still works.
- This matches the air-gap guardrail from ``feedback_airgap_compat.md`` —
  verify dep compat before building, don't force NTLM/Kerberos on Python
  3.14 environments that can't install the Kerberos C extensions.

Typical usage
-------------
    from tools.sharepoint import SharePointClient
    c = SharePointClient(
        endpoint="https://sp.internal.mil",
        auth_mode="ntlm",
        username="DOMAIN\\alice",
        password=os.environ["ICDEV_SHAREPOINT_PASS"],
    )
    for site in c.list_sites():
        for lst in c.get_lists(site["Url"]):
            for item in c.get_list_items(site["Url"], lst["Id"]):
                ...
"""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import random
import time
from typing import Any, Iterable

import requests
from requests.auth import AuthBase, HTTPBasicAuth

logger = get_logger("icdev.sharepoint.client")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class SharePointError(Exception):
    """Base for all SharePoint client errors."""


class SharePointAuthError(SharePointError):
    """401/403 from SharePoint. Credentials or auth mode misconfigured."""


class SharePointTransientError(SharePointError):
    """Retried-exhausted 5xx or network failure. Caller may re-queue."""


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

_DEFAULT_HEADERS = {
    # verbose OData gives stable JSON shape across SP versions; nometadata
    # is smaller but field names shift between 2016 and SE.
    "Accept": "application/json;odata=verbose",
    "User-Agent": "ICDEV-dashboard-sharepoint-ingest/1.0",
}

_RETRY_STATUSES = (500, 502, 503, 504)
_MAX_RETRIES = 3
_BACKOFF_BASE = 0.5   # seconds; doubles each retry with jitter


class SharePointClient:
    """Minimal REST client for on-prem SharePoint Server.

    Constructor args map 1:1 to ``args/sharepoint.yaml``:

    endpoint:
        Root of the web application, e.g. ``"https://sp.internal.mil"``.
        No trailing slash stored internally.
    auth_mode:
        ``"ntlm"`` | ``"kerberos"`` | ``"basic"``. Anything else raises
        ``ValueError``. Lazy-imports the matching auth backend.
    username, password:
        Required for NTLM and Basic. Ignored by Kerberos (which uses the
        ambient TGT). ``None`` is allowed iff ``auth_mode='kerberos'``.
    timeout:
        Per-request wall-clock seconds (default 30.0).
    verify:
        TLS verify. ``True`` for default bundle, ``str`` for custom CA
        path, ``False`` to disable (dev only — emits a warning).

    The client does NOT pool connections across threads. Create one per
    worker; ``SharePointClient`` wraps a ``requests.Session`` that is not
    thread-safe by construction.
    """

    def __init__(
        self,
        endpoint: str,
        auth_mode: str = "ntlm",
        username: str | None = None,
        password: str | None = None,
        timeout: float = 30.0,
        verify: bool | str = True,
    ) -> None:
        if not endpoint:
            raise ValueError("endpoint is required")
        if auth_mode not in ("ntlm", "kerberos", "basic"):
            raise ValueError(
                f"auth_mode must be one of ntlm|kerberos|basic (got {auth_mode!r})"
            )
        if auth_mode in ("ntlm", "basic") and (not username or password is None):
            raise ValueError(f"username+password required for auth_mode={auth_mode}")
        if verify is False:
            logger.warning(
                "SharePointClient: TLS verification DISABLED. Only acceptable in dev."
            )

        self.endpoint: str = endpoint.rstrip("/")
        self.auth_mode: str = auth_mode
        self.timeout: float = timeout
        self.verify: bool | str = verify

        self._session: requests.Session = requests.Session()
        self._session.auth = self._build_auth(auth_mode, username, password)
        self._session.headers.update(_DEFAULT_HEADERS)

    # -------------------------------------------------------------- auth

    @staticmethod
    def _build_auth(
        auth_mode: str,
        username: str | None,
        password: str | None,
    ) -> AuthBase | None:
        """Resolve the requests ``AuthBase`` for the selected mode.

        NTLM and Kerberos are lazy-imported so the module stays importable
        in air-gap envs that didn't install those packages.
        """
        if auth_mode == "basic":
            return HTTPBasicAuth(username, password or "")
        if auth_mode == "ntlm":
            try:
                from requests_ntlm import HttpNtlmAuth  # noqa: PLC0415
            except ImportError as exc:
                raise ImportError(
                    "auth_mode='ntlm' requires requests-ntlm. "
                    "Install with: pip install requests-ntlm"
                ) from exc
            return HttpNtlmAuth(username, password or "")
        if auth_mode == "kerberos":
            try:
                from requests_kerberos import HTTPKerberosAuth, OPTIONAL  # noqa: PLC0415
            except ImportError as exc:
                raise ImportError(
                    "auth_mode='kerberos' requires requests-kerberos. "
                    "Install with: pip install requests-kerberos (needs pykerberos "
                    "on Linux/macOS, winkerberos on Windows)."
                ) from exc
            return HTTPKerberosAuth(mutual_authentication=OPTIONAL)
        # Unreachable — constructor already validated auth_mode.
        raise ValueError(f"unknown auth_mode={auth_mode!r}")

    # ---------------------------------------------------------- http core

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict | None = None,
        stream: bool = False,
    ) -> requests.Response:
        """HTTP request with hard-fail-on-401 + backoff on 5xx."""
        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                resp = self._session.request(
                    method,
                    url,
                    params=params,
                    timeout=self.timeout,
                    verify=self.verify,
                    stream=stream,
                )
            except requests.RequestException as exc:
                last_exc = exc
                if attempt >= _MAX_RETRIES:
                    raise SharePointTransientError(
                        f"network error after {attempt + 1} attempts: {exc}"
                    ) from exc
                self._sleep_backoff(attempt)
                continue

            # 401/403 — hard fail, no retry
            if resp.status_code in (401, 403):
                raise SharePointAuthError(
                    f"{resp.status_code} on {method} {url}: "
                    f"{(resp.text or '')[:200]} "
                    f"(auth_mode={self.auth_mode})"
                )
            # 5xx transient — retry with backoff
            if resp.status_code in _RETRY_STATUSES and attempt < _MAX_RETRIES:
                logger.warning(
                    "SharePoint %d on %s (attempt %d/%d), backing off",
                    resp.status_code, url, attempt + 1, _MAX_RETRIES + 1,
                )
                self._sleep_backoff(attempt)
                continue
            # Non-retryable failure → raise for status, surface the body
            if not resp.ok:
                raise SharePointError(
                    f"{resp.status_code} on {method} {url}: {(resp.text or '')[:200]}"
                )
            return resp
        # Should not reach here
        raise SharePointTransientError(
            f"exhausted retries on {method} {url}: {last_exc}"
        )

    @staticmethod
    def _sleep_backoff(attempt: int) -> None:
        """Exponential backoff with jitter: 0.5s, 1s, 2s (±20%)."""
        delay = _BACKOFF_BASE * (2 ** attempt)
        jitter = delay * 0.2 * (random.random() * 2 - 1)  # noqa: S311
        time.sleep(max(0.05, delay + jitter))

    def _get_json(self, url: str, params: dict | None = None) -> dict[str, Any]:
        resp = self._request("GET", url, params=params)
        try:
            return resp.json()
        except ValueError as exc:
            raise SharePointError(
                f"non-JSON response from {url}: {resp.text[:200]}"
            ) from exc

    @staticmethod
    def _unwrap(payload: dict[str, Any]) -> Any:
        """SharePoint verbose OData wraps results in ``d.results``/``d``.

        - ``d.results``: list endpoints (lists, items, webs, search rows)
        - ``d``:          single-object endpoints (file, web, etc.)
        - Unwrapped JSON (some /_api paths): returned as-is
        """
        if not isinstance(payload, dict):
            return payload
        if "d" in payload and isinstance(payload["d"], dict):
            d = payload["d"]
            if "results" in d and isinstance(d["results"], list):
                return d["results"]
            return d
        return payload

    # ----------------------------------------------------- public surface

    def list_sites(self) -> list[dict[str, Any]]:
        """Enumerate site collections visible to the authenticated user.

        Uses SharePoint Search (``/_api/search/query``) since there is no
        non-admin endpoint that enumerates site collections globally.
        Falls back to ``/_api/web/webs`` (sub-webs of the root web) if
        Search is unavailable.
        """
        url = f"{self.endpoint}/_api/search/query"
        params = {
            "querytext": "'contentclass:STS_Site'",
            "selectproperties": "'Path,Title,WebTemplate'",
            "rowlimit": 500,
        }
        try:
            payload = self._get_json(url, params=params)
        except SharePointError:
            # Fallback to sub-webs of root
            return self._get_subwebs(self.endpoint)

        # verbose-OData search response nesting: d.query.PrimaryQueryResult.RelevantResults.Table.Rows.results[].Cells.results[]{Key,Value}
        try:
            rows = (
                payload["d"]["query"]["PrimaryQueryResult"]
                ["RelevantResults"]["Table"]["Rows"]["results"]
            )
        except (KeyError, TypeError):
            return []
        sites: list[dict[str, Any]] = []
        for row in rows:
            cells = {
                c["Key"]: c["Value"]
                for c in (row.get("Cells", {}) or {}).get("results", [])
            }
            if cells.get("Path"):
                sites.append({
                    "Url": cells["Path"],
                    "Title": cells.get("Title", ""),
                    "Template": cells.get("WebTemplate", ""),
                })
        return sites

    def _get_subwebs(self, site_url: str) -> list[dict[str, Any]]:
        url = f"{site_url.rstrip('/')}/_api/web/webs"
        result = self._unwrap(self._get_json(url))
        return result if isinstance(result, list) else []

    def get_lists(self, site_url: str) -> list[dict[str, Any]]:
        """Return all visible lists + libraries on *site_url*."""
        url = f"{site_url.rstrip('/')}/_api/web/lists"
        result = self._unwrap(self._get_json(url))
        return result if isinstance(result, list) else []

    def get_list_items(
        self,
        site_url: str,
        list_id: str,
        *,
        top: int = 1000,
        select: Iterable[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Return items for a list identified by GUID *list_id*.

        top:
            Max items per page. SharePoint enforces its own cap (5000
            typical); we cap at 1000 for predictable response sizes.
        select:
            Optional list of field names to project. When provided, maps
            to ``$select`` for narrower payloads.
        """
        url = f"{site_url.rstrip('/')}/_api/web/lists(guid'{list_id}')/items"
        params: dict[str, Any] = {"$top": min(int(top), 5000)}
        if select:
            params["$select"] = ",".join(select)
        result = self._unwrap(self._get_json(url, params=params))
        return result if isinstance(result, list) else []

    def get_file(self, site_url: str, server_relative_url: str) -> bytes:
        """Download file bytes for a server-relative URL.

        ``server_relative_url`` must start with ``/`` and point to the
        file within the site (e.g. ``/sites/eng/Shared Documents/x.pdf``).
        """
        if not server_relative_url.startswith("/"):
            raise ValueError("server_relative_url must start with '/'")
        # Single-quote escaping per SharePoint REST — double any embedded "'"
        safe = server_relative_url.replace("'", "''")
        url = (
            f"{site_url.rstrip('/')}"
            f"/_api/web/GetFileByServerRelativeUrl('{safe}')/$value"
        )
        resp = self._request("GET", url, stream=True)
        return resp.content

    def search(self, query: str, *, row_limit: int = 50) -> list[dict[str, Any]]:
        """Free-text search via ``/_api/search/query``.

        Returns a list of dicts with ``{Path, Title, HitHighlightedSummary}``.
        """
        url = f"{self.endpoint}/_api/search/query"
        params = {
            "querytext": f"'{query}'",
            "rowlimit": max(1, min(row_limit, 500)),
        }
        payload = self._get_json(url, params=params)
        try:
            rows = (
                payload["d"]["query"]["PrimaryQueryResult"]
                ["RelevantResults"]["Table"]["Rows"]["results"]
            )
        except (KeyError, TypeError):
            return []
        hits: list[dict[str, Any]] = []
        for row in rows:
            cells = {
                c["Key"]: c["Value"]
                for c in (row.get("Cells", {}) or {}).get("results", [])
            }
            hits.append({
                "Path": cells.get("Path", ""),
                "Title": cells.get("Title", ""),
                "Summary": cells.get("HitHighlightedSummary", ""),
            })
        return hits

    def close(self) -> None:
        """Release the underlying connection pool. Safe to call twice."""
        try:
            self._session.close()
        except Exception:  # noqa: BLE001
            pass

    def __enter__(self) -> "SharePointClient":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()


__all__ = [
    "SharePointClient",
    "SharePointError",
    "SharePointAuthError",
    "SharePointTransientError",
]
