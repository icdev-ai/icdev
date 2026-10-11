# CUI // SP-CTI
"""Registry of external MCP servers, and the gate in front of them.

Surfaces third-party MCP tools to ICDEV agents under a namespaced id, and is
the single place every outbound call passes through.

Four controls, none of which require trusting the remote server:

1. **Namespacing.** Remote tools are exposed as ``ext__<server>__<tool>``. A
   remote server cannot advertise a tool called ``sandbox_execute`` and have an
   agent reach ICDEV's real one; the names cannot collide because ICDEV tool
   ids never start with ``ext__``.
2. **Allowlist.** Only tools named in a server's ``tools:`` list are exposed.
   A server advertising a hundred tools contributes exactly the listed ones.
3. **Classification ceiling.** Arguments are checked against the server's
   ceiling before the call. A server at UNCLASSIFIED never receives CUI.
4. **Air-gap interlock.** Every server is disabled in air-gap mode, whatever
   the config says.

Three more, opt-in per server, for a server that analyses artifacts
(rea-wire-01):

5. **Exact pin.** A ``package:`` block names one exact version, and the
   installed ``package.json`` beside the launch path must carry it. A server
   launched through a registry resolver (``npx``, ``@latest``) has no installed
   path to check, so it is refused.
6. **Single-user only.** ``single_user_only: true`` drops the server on a shared
   / SaaS deployment (the ``re_engines.deployment_context`` signals).
7. **Authorized targets.** ``authorization: {required: true}`` routes every call
   through ``target_auth``: no operator grant for the target's sha256 means no
   call, and every call leaves one provenance row.

Descriptions are sanitised on the way in (``sanitize.py``) because they enter an
agent's prompt as attacker-controlled text.
"""
from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from icdev.tools.logging.icdev_logger import get_logger
from icdev.tools.mcp_client import client as transport_mod
from icdev.tools.mcp_client import target_auth
from icdev.tools.mcp_client.sanitize import (
    is_suspicious,
    sanitize_description,
    sanitize_tool_name,
)

logger = get_logger(__name__)

#: Prefix that makes a remote tool structurally distinguishable from an ICDEV
#: one. Nothing in TOOL_REGISTRY starts with this.
NAMESPACE_PREFIX = "ext__"

#: Ordered least- to most-sensitive. A server may receive content at or below
#: its ceiling.
_CLASSIFICATION_ORDER = ["UNCLASSIFIED", "CUI", "SECRET", "TOP SECRET"]


@dataclass
class ExternalTool:
    """One allowlisted tool on one external server."""

    server: str
    remote_name: str
    namespaced_name: str
    description: str
    input_schema: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.namespaced_name,
            "server": self.server,
            "remote_name": self.remote_name,
            "description": self.description,
            "input_schema": self.input_schema,
            "external": True,
        }


def _config_path():
    from icdev._paths import get_data_path

    return get_data_path("args") / "external_mcp_servers.yaml"


def load_config() -> dict:
    """Load the registry config, degrading to disabled."""
    try:
        raw = yaml.safe_load(_config_path().read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001 — absent config means "no servers"
        logger.debug("mcp_client: no external server config (%s)", exc)
        return {"enabled": False, "servers": [], "defaults": {}}
    raw.setdefault("enabled", False)
    raw.setdefault("servers", [])
    raw.setdefault("defaults", {})
    return raw


def _classification_rank(value: str) -> int:
    text = str(value or "UNCLASSIFIED").strip().upper()
    return _CLASSIFICATION_ORDER.index(text) if text in _CLASSIFICATION_ORDER else 0


def _shared_deployment() -> bool:
    """True on a shared / SaaS deployment. Unknown counts as shared (fail closed)."""
    try:
        from icdev.tools.analyzers.re_engines import deployment_context

        return bool(deployment_context().get("shared"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("mcp_client: deployment context unavailable (%s); treating as shared", exc)
        return True


#: An exact version: no range operator, no dist-tag, no ``latest``.
_EXACT_VERSION = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")


def pin_problem(spec: dict, command: list[str] | None = None) -> str | None:
    """Why the server's declared pin does not hold, or None when it does.

    A server with no ``package:`` block declares no pin, and nothing is checked.
    *command* is the RESOLVED launch command (``env:`` references expanded).
    """
    package = spec.get("package")
    if not package:
        return None
    name = str(package.get("name") or "")
    version = str(package.get("version") or "")
    if not name or not _EXACT_VERSION.match(version):
        return f"package pin {name!r}@{version!r} is not an exact version"

    elements = command if command is not None else (spec.get("command") or [])
    for element in elements:
        candidate = Path(str(element))
        if not str(element) or not candidate.is_file():
            continue
        for parent in list(candidate.resolve().parents)[:4]:
            manifest = parent / "package.json"
            if not manifest.is_file():
                continue
            try:
                installed = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if installed.get("name") != name:
                continue
            if installed.get("version") != version:
                return f"installed {name} is {installed.get('version')!r}, pinned {version!r}"
            return None
    return f"no installed {name} found beside the launch path"


def enabled_servers() -> list[dict]:
    """Server specs that are enabled, with defaults merged in.

    Returns empty in air-gap mode regardless of config: an external MCP server
    is off-box by definition, so this is not a policy decision the config gets
    to make.
    """
    if transport_mod._airgap_blocks():
        logger.info("mcp_client: air-gap active — no external MCP servers")
        return []

    config = load_config()
    if not config.get("enabled"):
        return []

    defaults = config.get("defaults") or {}
    out: list[dict] = []
    for spec in config.get("servers") or []:
        if not isinstance(spec, dict) or not spec.get("enabled"):
            continue
        if not spec.get("name"):
            logger.warning("mcp_client: skipping unnamed server entry")
            continue
        merged = {**defaults, **spec}
        if merged.get("single_user_only") and _shared_deployment():
            logger.info("mcp_client[%s]: single-user server skipped on a shared deployment",
                        merged["name"])
            continue
        out.append(merged)
    return out


class ExternalToolRegistry:
    """Discovers, namespaces and dispatches external MCP tools."""

    def __init__(self) -> None:
        self._transports: dict[str, Any] = {}
        self._tools: dict[str, ExternalTool] = {}
        self._specs: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._discovered = False
        #: Operator grants, for servers with ``authorization.required``.
        self.grants = target_auth.TargetGrants()
        #: Authorized targets opened on each server process; later calls cite them.
        self._session_targets: dict[str, list[dict]] = {}
        #: Provenance writer. Tests inject one.
        self.recorder: target_auth.Recorder = target_auth.audit_recorder

    # -- discovery ---------------------------------------------------------

    def discover(self, *, force: bool = False) -> list[ExternalTool]:
        """Connect to each enabled server and collect its allowlisted tools."""
        with self._lock:
            if self._discovered and not force:
                return list(self._tools.values())

            self._tools.clear()
            self._specs.clear()

            for spec in enabled_servers():
                name = str(spec["name"])
                allow = {sanitize_tool_name(t) for t in (spec.get("tools") or [])}
                if not allow:
                    logger.info("mcp_client[%s]: no tools allowlisted — contributing none", name)
                    continue

                transport = self._transports.get(name)
                if transport is None:
                    problem = pin_problem(spec, transport_mod.resolve_command(spec))
                    if problem:
                        logger.warning("mcp_client[%s]: refused: %s", name, problem)
                        continue
                    transport = transport_mod.build_transport(spec)
                    if transport is None:
                        continue
                    self._transports[name] = transport

                self._specs[name] = spec
                for remote in transport.list_tools():
                    if not isinstance(remote, dict):
                        continue
                    raw_name = remote.get("name") or ""
                    safe_name = sanitize_tool_name(raw_name)
                    if safe_name not in allow:
                        continue

                    raw_description = remote.get("description") or ""
                    if is_suspicious(raw_description):
                        logger.warning(
                            "mcp_client[%s]: tool %r description contained "
                            "injection-shaped content; sanitised", name, safe_name,
                        )

                    namespaced = f"{NAMESPACE_PREFIX}{sanitize_tool_name(name)}__{safe_name}"
                    self._tools[namespaced] = ExternalTool(
                        server=name,
                        remote_name=str(raw_name),
                        namespaced_name=namespaced,
                        description=sanitize_description(raw_description, server=name),
                        input_schema=remote.get("inputSchema") or {},
                    )

            self._discovered = True
            return list(self._tools.values())

    def list_tools(self) -> list[dict]:
        return [t.to_dict() for t in self.discover()]

    def grant_target(self, server: str, target: Any, *, basis: str,
                     operator: str | None = None, note: str = "") -> dict:
        """Operator authorization for the bytes at *target* on *server*.

        The server's ``authorization.bases`` may narrow the default bases and
        can never widen them. Raises ``ValueError`` on a basis outside the set.
        """
        self.discover()
        spec = self._specs.get(server) or {}
        declared = (spec.get("authorization") or {}).get("bases") or target_auth.DEFAULT_BASES
        bases = tuple(b for b in declared if b in target_auth.DEFAULT_BASES)
        return self.grants.grant(server, target, basis=basis, operator=operator,
                                 note=note, bases=bases)

    def get(self, namespaced_name: str) -> ExternalTool | None:
        self.discover()
        return self._tools.get(namespaced_name)

    # -- dispatch ----------------------------------------------------------

    def call(
        self,
        namespaced_name: str,
        arguments: dict | None = None,
        *,
        classification: str = "UNCLASSIFIED",
    ) -> dict:
        """Call an external tool. Always returns a dict; never raises.

        ``classification`` is the sensitivity of *arguments*. A server whose
        ceiling is below it is refused before any connection is made.
        """
        tool = self.get(namespaced_name)
        if tool is None:
            return {"ok": False, "error": f"unknown external tool {namespaced_name!r}"}

        spec = self._specs.get(tool.server) or {}
        ceiling = spec.get("classification_ceiling", "UNCLASSIFIED")
        if _classification_rank(classification) > _classification_rank(ceiling):
            logger.warning(
                "mcp_client[%s]: refused %s content to a %s server",
                tool.server, classification, ceiling,
            )
            return {
                "ok": False,
                "error": (
                    f"classification {classification} exceeds the ceiling "
                    f"({ceiling}) for server {tool.server!r}"
                ),
            }

        transport = self._transports.get(tool.server)
        if transport is None:
            return {"ok": False, "error": f"server {tool.server!r} is not connected"}

        args = dict(arguments or {})
        decision = None
        if (spec.get("authorization") or {}).get("required"):
            decision = target_auth.decide(
                spec, tool.remote_name, args, self.grants,
                self._session_targets.get(tool.server, []),
            )
            written = target_auth.record(decision, self.recorder, classification)
            if not decision["allowed"]:
                return {"ok": False, "refused": decision["reason"], "error": decision["message"]}
            if not written:
                return {
                    "ok": False,
                    "refused": "provenance_unwritable",
                    "error": (f"no provenance row could be written for {namespaced_name!r}; "
                              "an unrecorded RE run is refused"),
                }

        result = transport.call_tool(tool.remote_name, args)
        if result is None:
            return {"ok": False, "error": f"call to {namespaced_name!r} failed"}
        out = {"ok": True, "server": tool.server, "tool": tool.remote_name, "result": result}
        if decision is not None:
            if decision.get("opens_target"):
                opened = self._session_targets.setdefault(tool.server, [])
                known = {t["target_sha256"] for t in opened}
                opened.extend(t for t in decision["targets"] if t["target_sha256"] not in known)
            out["authorization"] = [
                {"target_sha256": t["target_sha256"], "basis": t["basis"]}
                for t in decision["targets"]
            ]
        return out

    def close(self) -> None:
        with self._lock:
            for transport in self._transports.values():
                try:
                    transport.close()
                except Exception:  # noqa: BLE001, S110
                    pass
            self._transports.clear()
            self._tools.clear()
            self._session_targets.clear()
            self._discovered = False


_registry: ExternalToolRegistry | None = None
_registry_lock = threading.Lock()


def get_external_registry() -> ExternalToolRegistry:
    """Process-wide registry."""
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = ExternalToolRegistry()
    return _registry


def reset_external_registry() -> None:
    """Drop the cached registry and close transports (tests)."""
    global _registry
    with _registry_lock:
        if _registry is not None:
            _registry.close()
        _registry = None
