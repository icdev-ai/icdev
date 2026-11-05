#!/usr/bin/env python3
# CUI // SP-CTI
"""Open WebUI configurator: make ICDEV the ONLY backend (owui-api-03, D-OWUI-1).

Open WebUI is admitted as a sidecar chat client on one condition: every model
call goes through ICDEV's governed ``/v1`` endpoint and every tool call through
``/mcp/v1``. Each Open WebUI setting below can quietly break that, and an admin
clicking through the UI is how it drifts back:

  ollama_direct              Ollama API enabled -> prompts skip ICDEV entirely
  openai_non_icdev           an OpenAI connection whose base URL is not ICDEV /v1
  user_direct_connections    users may add their own provider keys
  foreign_tool_server        a tool server that is not ICDEV /mcp/v1
  no_icdev_connection        nothing points at ICDEV at all (not a bypass, but broken)

Modes:
  --plan   (default) read the live config and print what --apply would change.
           Never writes.
  --check  exit 1 if any bypass is found; exit 2 if the config could not be read.
  --apply  write the governed config through Open WebUI's admin API.

Credentials come from the environment and are never printed:
  OPENWEBUI_ADMIN_TOKEN    an Open WebUI admin API key / JWT
  OPENWEBUI_ICDEV_API_KEY  the scoped ``icdev_`` key Open WebUI presents to ICDEV

Usage:
  python tools/openwebui/configure.py --icdev-url http://127.0.0.1:8443 --check --json
  python tools/openwebui/configure.py --icdev-url http://127.0.0.1:8443 --apply --with-tools
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional

from icdev.core.paths import repo_root

BASE_DIR = repo_root(__file__)
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("icdev.openwebui.configure")

ADMIN_TOKEN_ENV = "OPENWEBUI_ADMIN_TOKEN"
ICDEV_KEY_ENV = "OPENWEBUI_ICDEV_API_KEY"
DEFAULT_TOOLSET = "chat_readonly"

# Open WebUI 0.13 admin endpoints (routers/openai.py, ollama.py, configs.py).
OPENAI_CONFIG = "/openai/config"
OLLAMA_CONFIG = "/ollama/config"
CONNECTIONS_CONFIG = "/api/v1/configs/connections"
TOOL_SERVERS_CONFIG = "/api/v1/configs/tool_servers"

# (method, url, body) -> parsed JSON. Injectable so tests never touch a network.
Http = Callable[[str, str, Optional[dict]], Any]


class ConfigReadError(RuntimeError):
    """The live Open WebUI config could not be read."""


def make_http(token: str, timeout: float = 15.0) -> Http:
    def _http(method: str, url: str, body: Optional[dict] = None) -> Any:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - operator-supplied URL
            return json.loads(resp.read().decode("utf-8") or "null")
    return _http


def _norm(url: str) -> str:
    return (url or "").strip().rstrip("/")


def icdev_v1(icdev_url: str) -> str:
    return _norm(icdev_url) + "/v1"


def icdev_mcp(icdev_url: str) -> str:
    return _norm(icdev_url) + "/mcp/v1"


# ---------------------------------------------------------------------------
# Read + judge
# ---------------------------------------------------------------------------
def read_state(owui_url: str, http: Http) -> Dict[str, Any]:
    base = _norm(owui_url)
    state = {}
    for key, path in (("openai", OPENAI_CONFIG), ("ollama", OLLAMA_CONFIG),
                      ("connections", CONNECTIONS_CONFIG), ("tool_servers", TOOL_SERVERS_CONFIG)):
        try:
            state[key] = http("GET", base + path, None) or {}
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise ConfigReadError(f"GET {path} failed: {exc}") from exc
    return state


def _enabled(configs: dict, idx: int) -> bool:
    cfg = (configs or {}).get(str(idx)) or (configs or {}).get(idx) or {}
    return cfg.get("enable", True) is not False


def find_bypasses(state: Dict[str, Any], icdev_url: str) -> List[Dict[str, str]]:
    """Every setting that lets a chat turn or tool call reach something other than ICDEV."""
    findings: List[Dict[str, str]] = []
    v1, mcp = icdev_v1(icdev_url), icdev_mcp(icdev_url)

    ollama = state.get("ollama") or {}
    if ollama.get("ENABLE_OLLAMA_API") and ollama.get("OLLAMA_BASE_URLS"):
        for url in ollama["OLLAMA_BASE_URLS"]:
            findings.append({"rule": "ollama_direct", "target": url})

    openai = state.get("openai") or {}
    icdev_connected = False
    if openai.get("ENABLE_OPENAI_API"):
        configs = openai.get("OPENAI_API_CONFIGS") or {}
        for idx, url in enumerate(openai.get("OPENAI_API_BASE_URLS") or []):
            if not _enabled(configs, idx):
                continue
            if _norm(url) == v1:
                icdev_connected = True
            else:
                findings.append({"rule": "openai_non_icdev", "target": url})

    if (state.get("connections") or {}).get("ENABLE_DIRECT_CONNECTIONS"):
        findings.append({"rule": "user_direct_connections", "target": "ENABLE_DIRECT_CONNECTIONS"})

    for conn in (state.get("tool_servers") or {}).get("TOOL_SERVER_CONNECTIONS") or []:
        if (conn.get("config") or {}).get("enable", True) is False:
            continue
        url = _norm(conn.get("url", ""))
        if not url.startswith(mcp):
            findings.append({"rule": "foreign_tool_server", "target": conn.get("url", "")})

    if not icdev_connected:
        findings.append({"rule": "no_icdev_connection", "target": v1})
    return findings


# ---------------------------------------------------------------------------
# Desired state
# ---------------------------------------------------------------------------
def desired_writes(state: Dict[str, Any], icdev_url: str, icdev_key: str,
                   with_tools: bool = False, toolset: str = DEFAULT_TOOLSET) -> List[Dict[str, Any]]:
    """The admin-API writes that make ICDEV the only backend. Keys are passed through, never logged."""
    ollama = state.get("ollama") or {}
    connections = state.get("connections") or {}
    writes = [
        {"path": OLLAMA_CONFIG + "/update", "body": {
            "ENABLE_OLLAMA_API": False,
            "OLLAMA_BASE_URLS": ollama.get("OLLAMA_BASE_URLS") or [],
            "OLLAMA_API_CONFIGS": ollama.get("OLLAMA_API_CONFIGS") or {}}},
        {"path": OPENAI_CONFIG + "/update", "body": {
            "ENABLE_OPENAI_API": True,
            "OPENAI_API_BASE_URLS": [icdev_v1(icdev_url)],
            "OPENAI_API_KEYS": [icdev_key],
            "OPENAI_API_CONFIGS": {"0": {"enable": True, "tags": [{"name": "icdev"}]}}}},
        {"path": CONNECTIONS_CONFIG, "body": {
            "ENABLE_DIRECT_CONNECTIONS": False,
            "ENABLE_DIRECT_INTEGRATIONS": False,
            "ENABLE_BASE_MODELS_CACHE": bool(connections.get("ENABLE_BASE_MODELS_CACHE", False))}},
    ]
    mcp = icdev_mcp(icdev_url)
    kept = [c for c in (state.get("tool_servers") or {}).get("TOOL_SERVER_CONNECTIONS") or []
            if _norm(c.get("url", "")).startswith(mcp)]
    if with_tools and not kept:
        kept = [{"url": f"{mcp}/?toolset={toolset}", "path": "", "type": "mcp", "auth_type": "bearer",
                 "key": icdev_key, "config": {"enable": True},
                 "info": {"id": "icdev", "name": "ICDEV", "description": f"ICDEV tools ({toolset})"}}]
    writes.append({"path": TOOL_SERVERS_CONFIG, "body": {"TOOL_SERVER_CONNECTIONS": kept}})
    return writes


def redact(writes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """A printable copy of ``writes`` with every credential masked."""
    out = json.loads(json.dumps(writes))
    for w in out:
        body = w["body"]
        if "OPENAI_API_KEYS" in body:
            body["OPENAI_API_KEYS"] = ["***" if k else "" for k in body["OPENAI_API_KEYS"]]
        for c in body.get("TOOL_SERVER_CONNECTIONS") or []:
            if c.get("key"):
                c["key"] = "***"
    return out


def apply(owui_url: str, writes: List[Dict[str, Any]], http: Http) -> None:
    for w in writes:
        http("POST", _norm(owui_url) + w["path"], w["body"])


def env_block(icdev_url: str) -> Dict[str, str]:
    """Environment for a container deployment that starts governed (owui-ship-01)."""
    return {
        "ENABLE_OLLAMA_API": "false",
        "ENABLE_OPENAI_API": "true",
        "OPENAI_API_BASE_URL": icdev_v1(icdev_url),
        "OPENAI_API_KEY": f"${{{ICDEV_KEY_ENV}}}",
        "ENABLE_DIRECT_CONNECTIONS": "false",
        "BYPASS_EMBEDDING_AND_RETRIEVAL": "true",
        "SCARF_NO_ANALYTICS": "true",
        "DO_NOT_TRACK": "true",
        "ANONYMIZED_TELEMETRY": "false",
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: Optional[List[str]] = None, http: Optional[Http] = None) -> int:
    ap = argparse.ArgumentParser(description="Make ICDEV the only backend Open WebUI can reach (D-OWUI-1).")
    ap.add_argument("--owui-url", default="http://127.0.0.1:3000", help="Open WebUI base URL")
    ap.add_argument("--icdev-url", required=True, help="ICDEV SaaS gateway base URL, e.g. http://127.0.0.1:8443")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--plan", action="store_true", help="show intended changes (default; never writes)")
    mode.add_argument("--check", action="store_true", help="exit 1 on any bypass, 2 if unreadable")
    mode.add_argument("--apply", action="store_true", help="write the governed config")
    mode.add_argument("--env", action="store_true", help="print the container env block and exit")
    ap.add_argument("--with-tools", action="store_true", help=f"also register ICDEV /mcp/v1 ({DEFAULT_TOOLSET})")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.env:
        print(json.dumps(env_block(args.icdev_url), indent=2))
        return 0

    if http is None:
        token = os.environ.get(ADMIN_TOKEN_ENV, "")
        if not token:
            print(f"error: set {ADMIN_TOKEN_ENV} to an Open WebUI admin API key", file=sys.stderr)
            return 2
        http = make_http(token)

    try:
        state = read_state(args.owui_url, http)
    except ConfigReadError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    findings = find_bypasses(state, args.icdev_url)

    if args.check:
        bypasses = [f for f in findings if f["rule"] != "no_icdev_connection"]
        report = {"bypasses": bypasses, "findings": findings, "governed": not findings}
        print(json.dumps(report, indent=2) if args.json else
              "\n".join(f"{f['rule']}: {f['target']}" for f in findings) or "governed: no bypass found")
        return 1 if findings else 0

    icdev_key = os.environ.get(ICDEV_KEY_ENV, "")
    if args.apply and not icdev_key:
        print(f"error: set {ICDEV_KEY_ENV} to the icdev_ key Open WebUI should present", file=sys.stderr)
        return 2
    writes = desired_writes(state, args.icdev_url, icdev_key or "<unset>", with_tools=args.with_tools)

    if args.apply:
        apply(args.owui_url, writes, http)
        after = find_bypasses(read_state(args.owui_url, http), args.icdev_url)
        print(json.dumps({"applied": len(writes), "remaining_findings": after}, indent=2))
        return 1 if after else 0

    print(json.dumps({"findings": findings, "writes": redact(writes)}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
