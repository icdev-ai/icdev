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
  user_file_upload           users may upload files into Open WebUI's own store
                             (CUI at rest outside ICDEV; owui-rag-02)
  builtin_retrieval          Open WebUI's own retrieval is on: it embeds and stores content
                             itself (owui-rag-03)
  embedding_direct           its embedding engine reaches something other than ICDEV /v1
                             -- by default Ollama, directly; /process/text embeds even with
                             retrieval bypassed, so the engine must fail closed (owui-rag-03)
  no_icdev_connection        nothing points at ICDEV at all (not a bypass, but broken)
  native_tool_calling        the default model params are not ``function_calling: legacy``.
                             Open WebUI 0.13 defaults to native calling, which sends its OWN
                             built-in tools (chat search, memory, notes over its store) and
                             which ICDEV /v1 refuses -- so every chat fails (owui-ship-02)
  shared_identity            (--identity oidc) a connection to ICDEV presents one shared
                             key, so every user reaches ICDEV as the same principal
                             (owui-idp-01)

Identity (--identity):
  key   (default) Open WebUI presents one scoped ``icdev_`` key for everyone.
  oidc  Open WebUI and ICDEV trust the same IdP; Open WebUI forwards each user's
        own access token (``auth_type: system_oauth``), so ICDEV RBAC, workflow
        roles and tenant RLS apply per person. Open WebUI must itself log users
        in through that IdP (the OAUTH_* / OPENID_* env from ``--env --identity oidc``).

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
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# kax-conflict-04: run by path, sys.path[0] is this file's own directory -- bootstrap
# the import root before the first first-party import.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from icdev.core.paths import repo_root  # noqa: E402

BASE_DIR = repo_root(__file__)

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
DEFAULT_PERMISSIONS = "/api/v1/users/default/permissions"
MODELS_CONFIG = "/api/v1/configs/models"
RETRIEVAL_CONFIG = "/api/v1/retrieval/config"           # routers/retrieval.py, admin only
RETRIEVAL_EMBEDDING = "/api/v1/retrieval/embedding"
GOVERNED_MODEL_PARAMS = {"function_calling": "legacy"}

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
                      ("connections", CONNECTIONS_CONFIG), ("tool_servers", TOOL_SERVERS_CONFIG),
                      ("permissions", DEFAULT_PERMISSIONS), ("models", MODELS_CONFIG),
                      ("retrieval", RETRIEVAL_CONFIG), ("embedding", RETRIEVAL_EMBEDDING)):
        try:
            state[key] = http("GET", base + path, None) or {}
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise ConfigReadError(f"GET {path} failed: {exc}") from exc
    return state


def _enabled(configs: dict, idx: int) -> bool:
    cfg = (configs or {}).get(str(idx)) or (configs or {}).get(idx) or {}
    return cfg.get("enable", True) is not False


def find_bypasses(state: Dict[str, Any], icdev_url: str, identity: str = "key") -> List[Dict[str, str]]:
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
                auth = ((configs.get(str(idx)) or configs.get(idx) or {}).get("auth_type") or "bearer")
                if identity == "oidc" and auth != "system_oauth":
                    findings.append({"rule": "shared_identity", "target": url})
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
        elif identity == "oidc" and conn.get("auth_type") != "system_oauth":
            findings.append({"rule": "shared_identity", "target": conn.get("url", "")})

    # owui-rag-02. Open WebUI 0.13 enforces this permission in the UI only -- its
    # upload API does not check it -- so it narrows the path, it is not a wall:
    # its data volume must still be counted inside the ATO boundary.
    if ((state.get("permissions") or {}).get("chat") or {}).get("file_upload", True):
        findings.append({"rule": "user_file_upload", "target": "permissions.chat.file_upload"})

    # owui-rag-03. Open WebUI's built-in retrieval embeds content itself -- through
    # Ollama directly on a default install -- and keeps the vectors in its own store.
    # The bypass flag stops file processing from embedding; it does NOT stop
    # /api/v1/retrieval/process/text, which any signed-in user may call. So the
    # embedding engine must point at ICDEV /v1, which refuses embeddings: any
    # attempt then fails closed instead of reaching a model outside ICDEV.
    if not (state.get("retrieval") or {}).get("BYPASS_EMBEDDING_AND_RETRIEVAL"):
        findings.append({"rule": "builtin_retrieval", "target": "BYPASS_EMBEDDING_AND_RETRIEVAL"})
    emb = state.get("embedding") or {}
    engine = emb.get("RAG_EMBEDDING_ENGINE") or ""
    if engine != "openai" or _norm((emb.get("openai_config") or {}).get("url", "")) != v1:
        where = ((emb.get(f"{engine}_config") or {}).get("url") if engine else None) \
            or f"{engine or 'local'}:{emb.get('RAG_EMBEDDING_MODEL') or ''}"
        findings.append({"rule": "embedding_direct", "target": where})

    params = (state.get("models") or {}).get("DEFAULT_MODEL_PARAMS") or {}
    if params.get("function_calling") != "legacy":
        findings.append({"rule": "native_tool_calling", "target": "DEFAULT_MODEL_PARAMS.function_calling"})

    if not icdev_connected:
        findings.append({"rule": "no_icdev_connection", "target": v1})
    return findings


# ---------------------------------------------------------------------------
# Desired state
# ---------------------------------------------------------------------------
def desired_writes(state: Dict[str, Any], icdev_url: str, icdev_key: str,
                   with_tools: bool = False, toolset: str = DEFAULT_TOOLSET,
                   identity: str = "key") -> List[Dict[str, Any]]:
    """The admin-API writes that make ICDEV the only backend. Keys are passed through, never logged.

    With ``identity="oidc"`` no key is written: each connection forwards the
    signed-in user's own IdP token instead.
    """
    oidc = identity == "oidc"
    auth = {"auth_type": "system_oauth"} if oidc else {}
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
            "OPENAI_API_KEYS": ["" if oidc else icdev_key],
            "OPENAI_API_CONFIGS": {"0": {"enable": True, "tags": [{"name": "icdev"}], **auth}}}},
        {"path": CONNECTIONS_CONFIG, "body": {
            "ENABLE_DIRECT_CONNECTIONS": False,
            "ENABLE_DIRECT_INTEGRATIONS": False,
            "ENABLE_BASE_MODELS_CACHE": bool(connections.get("ENABLE_BASE_MODELS_CACHE", False))}},
    ]
    mcp = icdev_mcp(icdev_url)
    kept = [c for c in (state.get("tool_servers") or {}).get("TOOL_SERVER_CONNECTIONS") or []
            if _norm(c.get("url", "")).startswith(mcp)]
    if oidc:
        kept = [{**c, "auth_type": "system_oauth", "key": ""} for c in kept]
    if with_tools and not kept:
        kept = [{"url": f"{mcp}/?toolset={toolset}", "path": "", "type": "mcp",
                 "auth_type": "system_oauth" if oidc else "bearer",
                 "key": "" if oidc else icdev_key, "config": {"enable": True},
                 # owui-mcp-02: tags every call so mcp_authz_survey can measure this client.
                 "headers": {"X-ICDEV-Client": "openwebui"},
                 "info": {"id": "icdev", "name": "ICDEV", "description": f"ICDEV tools ({toolset})"}}]
    writes.append({"path": TOOL_SERVERS_CONFIG, "body": {"TOOL_SERVER_CONNECTIONS": kept}})
    permissions = json.loads(json.dumps(state.get("permissions") or {}))
    permissions.setdefault("chat", {})["file_upload"] = False
    writes.append({"path": DEFAULT_PERMISSIONS, "body": permissions})
    models = json.loads(json.dumps(state.get("models") or {}))
    models["DEFAULT_MODEL_PARAMS"] = {**(models.get("DEFAULT_MODEL_PARAMS") or {}), **GOVERNED_MODEL_PARAMS}
    writes.append({"path": MODELS_CONFIG, "body": models})
    # owui-rag-03: no built-in retrieval, and an embedding engine that can only reach
    # ICDEV -- which refuses embeddings -- so nothing is embedded outside ICDEV. No key
    # is sent: the refusal is the point. config/update takes partial bodies (exclude_none).
    writes.append({"path": RETRIEVAL_CONFIG + "/update", "body": {"BYPASS_EMBEDDING_AND_RETRIEVAL": True}})
    emb = state.get("embedding") or {}
    writes.append({"path": RETRIEVAL_EMBEDDING + "/update", "body": {
        "RAG_EMBEDDING_ENGINE": "openai",
        "RAG_EMBEDDING_MODEL": emb.get("RAG_EMBEDDING_MODEL") or "icdev-refuses-embeddings",
        "openai_config": {"url": icdev_v1(icdev_url), "key": ""}}})
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


# Open WebUI's own OIDC login against the shared IdP (owui-idp-01). The values are
# the operator's; only their names are fixed here.
OIDC_ENV = {
    "ENABLE_OAUTH_SIGNUP": "true",
    "ENABLE_LOGIN_FORM": "false",
    "OAUTH_PROVIDER_NAME": "${OPENWEBUI_OIDC_PROVIDER_NAME}",
    "OPENID_PROVIDER_URL": "${OPENWEBUI_OIDC_DISCOVERY_URL}",
    "OAUTH_CLIENT_ID": "${OPENWEBUI_OIDC_CLIENT_ID}",
    "OAUTH_CLIENT_SECRET": "${OPENWEBUI_OIDC_CLIENT_SECRET}",
    "OAUTH_SCOPES": "openid email profile",
    "OAUTH_GROUPS_CLAIM": "groups",
    "ENABLE_OAUTH_GROUP_MANAGEMENT": "true",
    # Open WebUI shows a model with no access entry to admins only. Under OIDC the
    # authority is ICDEV, per user, from the forwarded token (a role-gated model is
    # refused there), so Open WebUI must not hold a second, divergent model ACL.
    "BYPASS_MODEL_ACCESS_CONTROL": "true",
}


def env_block(icdev_url: str, identity: str = "key") -> Dict[str, str]:
    """Environment for a container deployment that starts governed (owui-ship-01).

    Open WebUI has no env for a connection's ``auth_type``, so an OIDC deployment
    still needs one ``--apply --identity oidc`` after first start; ``--check
    --identity oidc`` reports ``shared_identity`` until it has run.
    """
    if identity == "oidc":
        return {**_common_env(icdev_url), **OIDC_ENV}
    return {**_common_env(icdev_url), "OPENAI_API_KEY": f"${{{ICDEV_KEY_ENV}}}"}


def _common_env(icdev_url: str) -> Dict[str, str]:
    return {
        "ENABLE_OLLAMA_API": "false",
        "ENABLE_OPENAI_API": "true",
        "OPENAI_API_BASE_URL": icdev_v1(icdev_url),
        "ENABLE_DIRECT_CONNECTIONS": "false",
        "BYPASS_EMBEDDING_AND_RETRIEVAL": "true",
        "USER_PERMISSIONS_CHAT_FILE_UPLOAD": "false",
        "DEFAULT_MODEL_PARAMS": json.dumps(GOVERNED_MODEL_PARAMS),
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
    ap.add_argument("--identity", choices=("key", "oidc"), default="key",
                    help="how users reach ICDEV: one shared icdev_ key, or each user's own IdP token")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.env:
        print(json.dumps(env_block(args.icdev_url, args.identity), indent=2))
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
    findings = find_bypasses(state, args.icdev_url, args.identity)

    if args.check:
        bypasses = [f for f in findings if f["rule"] != "no_icdev_connection"]
        report = {"bypasses": bypasses, "findings": findings, "governed": not findings}
        print(json.dumps(report, indent=2) if args.json else
              "\n".join(f"{f['rule']}: {f['target']}" for f in findings) or "governed: no bypass found")
        return 1 if findings else 0

    icdev_key = os.environ.get(ICDEV_KEY_ENV, "")
    if args.apply and not icdev_key and args.identity == "key":
        print(f"error: set {ICDEV_KEY_ENV} to the icdev_ key Open WebUI should present", file=sys.stderr)
        return 2
    writes = desired_writes(state, args.icdev_url, icdev_key or "<unset>", with_tools=args.with_tools,
                            identity=args.identity)

    if args.apply:
        apply(args.owui_url, writes, http)
        after = find_bypasses(read_state(args.owui_url, http), args.icdev_url, args.identity)
        print(json.dumps({"applied": len(writes), "remaining_findings": after}, indent=2))
        return 1 if after else 0

    print(json.dumps({"findings": findings, "writes": redact(writes)}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
