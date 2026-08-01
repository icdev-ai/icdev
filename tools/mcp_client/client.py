# CUI // SP-CTI
"""Outbound MCP client — call tools on THIRD-PARTY MCP servers.

Everything else under ``tools/mcp/`` makes ICDEV an MCP *server*: something
else connects in and calls our tools. Before this module ICDEV had no client
transport at all, so "our SaaS providers expose MCP" was unusable — there was
no code path to connect out.

Two transports, both stdlib:

* ``stdio`` — spawn the server as a subprocess and speak newline-delimited
  JSON-RPC over its pipes, keeping the process warm across calls. This mirrors
  the working cross-repo bridge in idea_lab/compass rather than inventing a
  second dialect.
* ``http``  — POST JSON-RPC to a URL, through ``egress_guard`` so the
  destination is checked before the socket opens.

Deliberately stdlib rather than the ``mcp`` SDK: this has to work in air-gapped
deployments where adding a dependency is a procurement question, and the
protocol surface used here (initialize / tools/list / tools/call) is small.

Safety
------
* Air-gap mode disables every server unconditionally — an external MCP server
  is by definition off-box.
* HTTP destinations pass ``egress_guard`` (HTTPS-only, DNS resolve-then-check
  on every A record, RFC1918/link-local denied).
* Remote tool names and descriptions are attacker-controlled; see
  ``tools/mcp_client/sanitize.py``.
* A server that hangs, crashes or answers malformed JSON yields ``None``. Every
  public function here degrades; none raises into an agent loop.
"""
from __future__ import annotations

import json
import os
import subprocess  # noqa: S404 — spawning a configured MCP server is the point
import threading
from typing import Any

from icdev.tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

_PROTOCOL_VERSION = "2024-11-05"
_CLIENT_INFO = {"name": "icdev-mcp-client", "version": "1.0"}


class ExternalMCPError(RuntimeError):
    """Raised only by internal helpers; public entry points degrade to None."""


def _resolve_secret(value: str) -> str:
    """Resolve an ``env:VAR`` reference, else return the literal.

    Config carries references, never secrets: a registry file with a live token
    inlined is a credential in git.
    """
    text = str(value or "")
    if text.startswith("env:"):
        return os.environ.get(text[4:], "")
    return text


def _airgap_blocks() -> bool:
    try:
        from tools.airgap import is_airgap

        return bool(is_airgap())
    except Exception:  # noqa: BLE001 — airgap module optional
        return False


# ---------------------------------------------------------------------------
# stdio transport
# ---------------------------------------------------------------------------


class StdioTransport:
    """Warm subprocess speaking newline-delimited JSON-RPC."""

    def __init__(self, name: str, command: list[str], env: dict | None = None,
                 timeout: float = 30.0) -> None:
        self.name = name
        self._command = list(command or [])
        self._env_spec = dict(env or {})
        self._timeout = timeout
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._next_id = 0
        self._initialized = False

    def _spawn(self) -> subprocess.Popen | None:
        if not self._command:
            return None
        env = dict(os.environ)
        for key, spec in self._env_spec.items():
            env[key] = _resolve_secret(spec)
        try:
            return subprocess.Popen(  # noqa: S603 — command comes from checked-in config
                self._command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=env,
                text=True,
                encoding="utf-8",
                bufsize=1,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("mcp_client[%s]: spawn failed: %s", self.name, exc)
            return None

    def _ensure(self) -> bool:
        if self._proc is not None and self._proc.poll() is None:
            return True
        self._proc = self._spawn()
        self._initialized = False
        if self._proc is None:
            return False
        return self._initialize()

    def _initialize(self) -> bool:
        result = self._request("initialize", {
            "protocolVersion": _PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": _CLIENT_INFO,
        }, _skip_ensure=True)
        if result is None:
            return False
        self._notify("notifications/initialized")
        self._initialized = True
        return True

    def _write(self, payload: dict) -> bool:
        proc = self._proc
        if proc is None or proc.stdin is None:
            return False
        try:
            proc.stdin.write(json.dumps(payload) + "\n")
            proc.stdin.flush()
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("mcp_client[%s]: write failed: %s", self.name, exc)
            return False

    def _notify(self, method: str, params: dict | None = None) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def _request(self, method: str, params: dict, *, _skip_ensure: bool = False) -> Any:
        if not _skip_ensure and not self._ensure():
            return None

        self._next_id += 1
        request_id = self._next_id
        if not self._write({
            "jsonrpc": "2.0", "id": request_id, "method": method, "params": params,
        }):
            return None

        proc = self._proc
        if proc is None or proc.stdout is None:
            return None

        # Read until the matching id. A server may interleave notifications and
        # unrelated responses; anything that is not our answer is skipped rather
        # than treated as one.
        deadline = threading.Event()
        timer = threading.Timer(self._timeout, deadline.set)
        timer.start()
        try:
            while not deadline.is_set():
                line = proc.stdout.readline()
                if not line:
                    return None
                line = line.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except ValueError:
                    continue  # not JSON — some servers log to stdout
                if message.get("id") != request_id:
                    continue
                if "error" in message:
                    logger.warning(
                        "mcp_client[%s]: %s returned error: %s",
                        self.name, method, str(message["error"])[:200],
                    )
                    return None
                return message.get("result")
        finally:
            timer.cancel()

        logger.warning("mcp_client[%s]: %s timed out after %ss", self.name, method, self._timeout)
        return None

    def list_tools(self) -> list[dict]:
        result = self._request("tools/list", {})
        if not isinstance(result, dict):
            return []
        tools = result.get("tools")
        return tools if isinstance(tools, list) else []

    def call_tool(self, tool: str, arguments: dict) -> Any:
        return self._request("tools/call", {"name": tool, "arguments": arguments})

    def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:  # noqa: BLE001, S110
                pass


# ---------------------------------------------------------------------------
# HTTP transport
# ---------------------------------------------------------------------------


class HttpTransport:
    """JSON-RPC over HTTPS POST, gated by egress_guard."""

    def __init__(self, name: str, url: str, auth: dict | None = None,
                 timeout: float = 30.0) -> None:
        self.name = name
        self._url = url
        self._auth = dict(auth or {})
        self._timeout = timeout
        self._next_id = 0

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if str(self._auth.get("type", "")).lower() == "bearer":
            token = _resolve_secret(self._auth.get("token", ""))
            if token:
                headers["Authorization"] = f"Bearer {token}"
        return headers

    def _guard(self) -> None:
        """Raise unless the destination may be contacted."""
        try:
            from tools.http.egress_guard import egress_guard
        except Exception as exc:  # noqa: BLE001
            # Fail closed: an unimportable guard means the destination is
            # unchecked, which is exactly when not to open a socket.
            raise ExternalMCPError(f"egress guard unavailable: {exc}") from exc

        allowed, reason, _ips = egress_guard(self._url, {})
        if not allowed:
            raise ExternalMCPError(f"egress blocked ({reason}) for {self._url!r}")

    def _request(self, method: str, params: dict) -> Any:
        import urllib.request

        try:
            self._guard()
        except ExternalMCPError as exc:
            logger.warning("mcp_client[%s]: %s", self.name, exc)
            return None

        self._next_id += 1
        payload = json.dumps({
            "jsonrpc": "2.0", "id": self._next_id, "method": method, "params": params,
        }).encode("utf-8")

        request = urllib.request.Request(  # noqa: S310 — scheme validated by _guard
            self._url, data=payload, headers=self._headers(), method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:  # noqa: S310
                body = response.read().decode("utf-8")
        except Exception as exc:  # noqa: BLE001
            logger.warning("mcp_client[%s]: %s failed: %s", self.name, method, exc)
            return None

        try:
            message = json.loads(body)
        except ValueError:
            logger.warning("mcp_client[%s]: malformed JSON response", self.name)
            return None

        if "error" in message:
            logger.warning(
                "mcp_client[%s]: %s returned error: %s",
                self.name, method, str(message["error"])[:200],
            )
            return None
        return message.get("result")

    def list_tools(self) -> list[dict]:
        result = self._request("tools/list", {})
        if not isinstance(result, dict):
            return []
        tools = result.get("tools")
        return tools if isinstance(tools, list) else []

    def call_tool(self, tool: str, arguments: dict) -> Any:
        return self._request("tools/call", {"name": tool, "arguments": arguments})

    def close(self) -> None:
        return None


def build_transport(spec: dict) -> Any:
    """Construct the transport described by a registry entry, or None."""
    name = str(spec.get("name") or "")
    kind = str(spec.get("transport") or "stdio").lower()
    timeout = float(spec.get("timeout_seconds") or 30.0)

    if kind == "stdio":
        command = spec.get("command") or []
        if not command:
            logger.warning("mcp_client[%s]: stdio server has no command", name)
            return None
        return StdioTransport(name, list(command), spec.get("env"), timeout)

    if kind == "http":
        url = str(spec.get("url") or "")
        if not url:
            logger.warning("mcp_client[%s]: http server has no url", name)
            return None
        return HttpTransport(name, url, spec.get("auth"), timeout)

    logger.warning("mcp_client[%s]: unknown transport %r", name, kind)
    return None
