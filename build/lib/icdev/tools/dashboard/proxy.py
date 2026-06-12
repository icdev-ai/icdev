from __future__ import annotations
# CUI // SP-CTI
"""
WSGI strangler-fig proxy middleware for the ICDEV™ dashboard.

Routing table
─────────────
  /next/*   → Next.js dev server (NEXT_DEV_URL, default http://localhost:3000)
              when dev_mode is True, or static files from frontend/out/ in prod
  /api/v1/* → Flask app (pass-through — already handled by Flask blueprints)
  all else  → Flask app (legacy routes, pass-through)

Mount via mount_proxy(app) inside create_app() after all routes are registered.
"""

import mimetypes
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Iterable


class StranglerProxyMiddleware:
    """Wrap Flask's wsgi_app to add strangler-fig routing for the Next.js frontend."""

    _NEXT_PREFIX = "/next"

    def __init__(
        self,
        flask_wsgi_app: Callable,
        *,
        next_dev_url: str = "http://localhost:3000",
        next_static_root: Path | None = None,
        dev_mode: bool = False,
    ) -> None:
        self._app = flask_wsgi_app
        self._next_dev_url = next_dev_url.rstrip("/")
        self._next_static_root = next_static_root
        self._dev_mode = dev_mode

    def __call__(self, environ: dict, start_response: Callable) -> Iterable[bytes]:
        path = environ.get("PATH_INFO", "")
        if path == self._NEXT_PREFIX or path.startswith(self._NEXT_PREFIX + "/"):
            return self._handle_next(environ, start_response, path)
        # /api/v1/* and all legacy paths fall through to Flask
        return self._app(environ, start_response)

    # ------------------------------------------------------------------
    # /next/* dispatch
    # ------------------------------------------------------------------

    def _handle_next(
        self, environ: dict, start_response: Callable, path: str
    ) -> Iterable[bytes]:
        if self._dev_mode:
            return self._proxy_to_dev_server(environ, start_response, path)
        return self._serve_static(environ, start_response, path)

    def _proxy_to_dev_server(
        self, environ: dict, start_response: Callable, path: str
    ) -> Iterable[bytes]:
        """Forward /next/* to the Next.js dev server, stripping the /next prefix."""
        upstream_path = path[len(self._NEXT_PREFIX):] or "/"
        qs = environ.get("QUERY_STRING", "")
        url = self._next_dev_url + upstream_path + (f"?{qs}" if qs else "")
        method = environ.get("REQUEST_METHOD", "GET")

        body = b""
        try:
            length = int(environ.get("CONTENT_LENGTH") or 0)
            if length > 0:
                body = environ["wsgi.input"].read(length)
        except (ValueError, KeyError):
            pass

        headers: dict[str, str] = {}
        for key, val in environ.items():
            if key.startswith("HTTP_"):
                headers[key[5:].replace("_", "-").title()] = val
        if environ.get("CONTENT_TYPE"):
            headers["Content-Type"] = environ["CONTENT_TYPE"]
        headers["X-Forwarded-For"] = environ.get("REMOTE_ADDR", "")

        req = urllib.request.Request(url, data=body or None, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:  # nosec B310
                resp_headers = [
                    (k, v)
                    for k, v in resp.headers.items()
                    if k.lower() not in ("transfer-encoding", "connection")
                ]
                start_response(f"{resp.status} {resp.reason}", resp_headers)
                return [resp.read()]
        except urllib.error.HTTPError as exc:
            start_response(f"{exc.code} {exc.reason}", list(exc.headers.items()))
            return [exc.read()]
        except Exception as exc:  # noqa: BLE001
            start_response("502 Bad Gateway", [("Content-Type", "text/plain")])
            return [f"Next.js dev server unavailable: {exc}".encode()]

    def _serve_static(
        self, environ: dict, start_response: Callable, path: str
    ) -> Iterable[bytes]:
        """Serve /next/* from the Next.js static export at frontend/out/."""
        if self._next_static_root is None:
            start_response("503 Service Unavailable", [("Content-Type", "text/plain")])
            return [b"Next.js static root not configured"]

        rel = path[len(self._NEXT_PREFIX):]
        if not rel or rel == "/":
            rel = "/index.html"
        elif not Path(rel).suffix:
            rel = rel.rstrip("/") + "/index.html"

        file_path = (self._next_static_root / rel.lstrip("/")).resolve()

        # Path traversal guard
        try:
            file_path.relative_to(self._next_static_root.resolve())
        except ValueError:
            start_response("403 Forbidden", [("Content-Type", "text/plain")])
            return [b"Forbidden"]

        if not file_path.is_file():
            start_response("404 Not Found", [("Content-Type", "text/plain")])
            return [b"Not found"]

        content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        data = file_path.read_bytes()
        start_response("200 OK", [
            ("Content-Type", content_type),
            ("Content-Length", str(len(data))),
        ])
        return [data]


def mount_proxy(app, *, base_dir: Path | None = None) -> None:
    """Wrap app.wsgi_app with StranglerProxyMiddleware.

    Call once inside create_app() after all routes are registered.
    """
    if base_dir is None:
        base_dir = Path(__file__).resolve().parent.parent.parent

    next_static_root = base_dir / "frontend" / "out"
    dev_mode = bool(app.debug) or os.environ.get("ICDEV_NEXT_DEV", "").lower() in ("1", "true")

    app.wsgi_app = StranglerProxyMiddleware(
        app.wsgi_app,
        next_dev_url=os.environ.get("NEXT_DEV_URL", "http://localhost:3000"),
        next_static_root=next_static_root,
        dev_mode=dev_mode,
    )
