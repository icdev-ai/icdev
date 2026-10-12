# [TEMPLATE: CUI // SP-CTI]
"""`icdev claude-bridge serve` -- harnesses without a subscription login reach Claude via `claude -p`.

OPERATOR DECISION (2026-10-11): every Claude call ICDEV or a harness it launches
makes is paid by the operator's claude.ai SUBSCRIPTION, never by a metered API
key. Claude Code logs in to the subscription natively; opencode and Pi cannot
(omx-spike-02 measured Pi's own Anthropic OAuth being refused with a 400 that
sends third-party apps to "extra usage"). This module is the route for them:
a loopback-only OpenAI chat-completions endpoint that turns each request into
ONE ``claude -p`` call -- mechanism B of omx-spike-02, measured GO for opencode
1.18.35 and pi 1.1.0 on the acceptance host.

How a tool call round-trips (stateless; the harness resends the history):

  1. the harness POSTs ``/v1/chat/completions`` with ``messages`` + ``tools``;
  2. the bridge renders the tool definitions and the conversation into one
     prompt and runs ``claude -p --tools "" --json-schema <TURN_SCHEMA> ...``
     -- claude's OWN tools are disabled, so it can only DESCRIBE a call;
  3. ``structured_output`` ``{"kind": "tool_calls", "calls": [...]}`` becomes
     ``message.tool_calls`` with ``finish_reason: tool_calls``; the harness runs
     the tools ITSELF (its own guard sees them) and POSTs again with
     ``role: tool`` results; ``{"kind": "final"}`` becomes ``content``.

Guarantees, each pinned by ``tests/llm/test_claude_http_bridge.py``:

* **No metered key.** The ``claude`` child is started through
  ``subprocess_backend.run_claude``, the one CLI wrapper, which scrubs
  ``ANTHROPIC_API_KEY`` / ``ANTHROPIC_AUTH_TOKEN`` / ``ANTHROPIC_BASE_URL``.
* **Loopback only.** :func:`resolve_bind` refuses any non-loopback address.
* **Per-host bearer token**, a 0600 file created on first use
  (:func:`token_path`); every route but ``/health`` requires it.
* **Bounded concurrency** (``ICDEV_CLAUDE_BRIDGE_MAX_CONCURRENT``, default the
  CLI bridge's own cap); a request that cannot get a slot is a 503, not a hang.
* **Usage recorded UNPRICED.** Each turn is a ``cli_llm_jobs`` row
  (``backend='claude_bridge'``) with the prompt-cache counters and no cost:
  ``args/cache_effectiveness.yaml`` ``usd_basis: unpriced``. Never price it at
  the API rate card.
* **Exhaustion is a distinct error.** A spent programmatic credit or a usage
  limit is HTTP 429 ``type: claude_quota_exhausted`` with header
  ``X-ICDEV-Failover: claude_quota_exhausted`` -- never a generic 500 and never
  an empty 200 -- so the provider failover (omx-adapt-06) can act on it. The
  exact CLI wording on exhaustion was NOT observed by the spike; the patterns
  in :data:`QUOTA_PATTERNS` are the known ones and an unmatched failure is a
  502 ``claude_cli_error`` carrying the CLI's own text.

Not built (YAGNI until a harness needs it): an Anthropic ``/v1/messages`` front,
and token-level streaming -- ``stream: true`` is served BUFFERED, one SSE delta
per turn, which both measured harnesses accept (schema-constrained output only
exists once the turn completes).

Usage:
    icdev claude-bridge serve [--port N] [--host 127.0.0.1]
    icdev claude-bridge status [--json]
    icdev claude-bridge unit            # print the systemd --user unit (Linux)
"""

from __future__ import annotations

import argparse
import hmac
import importlib
import json
import os
import secrets
import shutil
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.llm.cli_bridge.http_bridge")

DEFAULT_PORT = 8765
LOOPBACK_HOSTS = ("127.0.0.1", "::1", "localhost")
TOKEN_FILENAME = "claude-bridge.token"
UNIT_NAME = "icdev-claude-bridge.service"
#: The provider id every harness config this module writes uses.
PROVIDER_ID = "icdev-claude"
#: ``claude --model`` ALIASES (not model ids): the CLI resolves them itself.
DEFAULT_MODELS = ("sonnet", "opus", "haiku")
#: omx-spike-02: opencode offered 539 tool definitions (its global MCP config);
#: the measured run passed with the definitions cut at 60 KB.
DEFAULT_TOOL_DEFS_MAX_BYTES = 60_000
MAX_BODY_BYTES = 16 * 1024 * 1024
JOB_BACKEND = "claude_bridge"
USD_BASIS = "unpriced"
FAILOVER_HEADER = "X-ICDEV-Failover"

#: Adapters whose harness logs in to the claude.ai subscription itself.
#: omx-spike-02: `claude auth status` -> authMethod claude.ai on the acceptance host.
NATIVE_SUBSCRIPTION_ADAPTERS = ("claude_cli",)
#: Adapters this module can point at the bridge -> the harness whose config it writes.
BRIDGE_HARNESSES = {"opencode_cli": "opencode", "pi_cli": "pi"}
ROUTES = ("native_subscription", "cli_bridge", "none")

#: (code, lowercase substrings) -- matched against the CLI's error text, in order.
QUOTA_PATTERNS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("credit_exhausted", ("credit balance is too low", "extra usage", "out of credit",
                          "usage credits", "programmatic credit", "insufficient credit")),
    ("usage_limit", ("usage limit reached", "hit your limit", "rate limit", "rate_limit",
                     "limit will reset", "resets at", "too many requests")),
)
AUTH_PATTERNS = ("please run /login", "not logged in", "invalid api key", "authentication_error",
                 "oauth token has expired")

TURN_SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["final", "tool_calls"]},
        "text": {"type": "string"},
        "calls": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "arguments_json": {"type": "string"}},
                "required": ["name", "arguments_json"],
            },
        },
    },
    "required": ["kind"],
}

SYSTEM_PROMPT_TOOLS = (
    "You are the language model behind an OpenAI-compatible chat API, serving a coding "
    "harness. You have NO tools of your own and must never claim to have run anything. "
    "The caller offers the tools listed under [AVAILABLE TOOLS]; the CALLER executes them "
    "and sends their output back as [TOOL RESULT] turns. Read the whole conversation and "
    "choose the next step: EITHER request one or more tool calls (kind=\"tool_calls\", "
    "calls=[{name, arguments_json}], where arguments_json is a JSON object string that "
    "matches that tool's parameters), OR give the final answer to the user "
    "(kind=\"final\", text=...). Follow the caller's [SYSTEM] instructions."
)
SYSTEM_PROMPT_PLAIN = (
    "You are the language model behind a chat API. You have no tools. Follow the caller's "
    "[SYSTEM] instructions and reply to the conversation's last [USER] turn."
)


# --------------------------------------------------------------------------- #
# Host facts: bind address, port, token
# --------------------------------------------------------------------------- #

def resolve_bind(host: str) -> str:
    """The address to bind; anything that is not loopback is REFUSED."""
    h = (host or "127.0.0.1").strip()
    if h not in LOOPBACK_HOSTS:
        raise ValueError(f"claude-bridge binds loopback only ({', '.join(LOOPBACK_HOSTS)}); "
                         f"refusing {h!r}")
    return "127.0.0.1" if h == "localhost" else h


def configured_port(environ: Optional[Mapping[str, str]] = None) -> int:
    env = os.environ if environ is None else environ
    try:
        port = int(env.get("ICDEV_CLAUDE_BRIDGE_PORT") or DEFAULT_PORT)
    except (TypeError, ValueError):
        return DEFAULT_PORT
    return port if 0 < port < 65536 else DEFAULT_PORT


def bridge_url(port: Optional[int] = None) -> str:
    """The OpenAI base URL a harness is pointed at."""
    return f"http://127.0.0.1:{port or configured_port()}/v1"


def token_path(environ: Optional[Mapping[str, str]] = None, home: Optional[Path] = None) -> Path:
    """Where this host's bearer token lives (user-owned config dir)."""
    env = os.environ if environ is None else environ
    declared = (env.get("ICDEV_CLAUDE_BRIDGE_TOKEN_FILE") or "").strip()
    if declared:
        return Path(declared).expanduser()
    home = home or Path.home()
    if sys.platform.startswith("win") and env.get("APPDATA"):
        base = Path(env["APPDATA"])
    else:
        base = Path(env.get("XDG_CONFIG_HOME") or home / ".config")
    return base / "icdev" / TOKEN_FILENAME


def ensure_token(path: Path) -> str:
    """Read the token, creating it (mode 0600) on first use."""
    try:
        existing = path.read_text(encoding="utf-8").strip()
    except OSError:
        existing = ""
    if existing:
        return existing
    token = secrets.token_urlsafe(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(token + "\n")
    try:
        path.chmod(0o600)
    except OSError:  # pragma: no cover -- best effort on filesystems without modes
        pass
    return token


def configured_models(environ: Optional[Mapping[str, str]] = None) -> List[str]:
    env = os.environ if environ is None else environ
    raw = (env.get("ICDEV_CLAUDE_BRIDGE_MODELS") or "").strip()
    models = [m.strip() for m in raw.split(",") if m.strip()] if raw else list(DEFAULT_MODELS)
    return models or list(DEFAULT_MODELS)


def _max_concurrent(environ: Optional[Mapping[str, str]] = None) -> int:
    env = os.environ if environ is None else environ
    try:
        val = int(env.get("ICDEV_CLAUDE_BRIDGE_MAX_CONCURRENT") or 0)
    except (TypeError, ValueError):
        val = 0
    return val if val > 0 else _backend()._max_concurrent()


def _backend():
    # importlib, so a test's monkeypatch on the module object is what runs.
    return importlib.import_module("tools.llm.cli_bridge.subprocess_backend")


# --------------------------------------------------------------------------- #
# Request -> one claude -p prompt
# --------------------------------------------------------------------------- #

def _text_of(content: Any) -> str:
    """OpenAI message content (string or parts list) as plain text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for part in content:
            if isinstance(part, dict):
                if part.get("type") in ("text", "input_text"):
                    out.append(str(part.get("text") or ""))
                else:
                    out.append(f"[{part.get('type', 'part')} omitted]")
            else:
                out.append(str(part))
        return "\n".join(out)
    return str(content)


def render_tools(tools: List[dict], max_bytes: int = DEFAULT_TOOL_DEFS_MAX_BYTES) -> Tuple[str, int, int]:
    """(JSON of the kept tool definitions, kept, dropped) -- in the caller's order."""
    kept: List[dict] = []
    size = 2
    for t in tools:
        fn = (t or {}).get("function") if isinstance(t, dict) else None
        if not isinstance(fn, dict) or not fn.get("name"):
            continue
        entry = {"name": fn["name"], "description": fn.get("description") or "",
                 "parameters": fn.get("parameters") or {"type": "object"}}
        blob = len(json.dumps(entry, ensure_ascii=False)) + 2
        if kept and size + blob > max_bytes:
            break
        kept.append(entry)
        size += blob
    valid = sum(1 for t in tools if isinstance(t, dict) and isinstance(t.get("function"), dict)
                and t["function"].get("name"))
    return json.dumps(kept, ensure_ascii=False, indent=1), len(kept), valid - len(kept)


def render_conversation(messages: List[dict]) -> str:
    blocks: List[str] = []
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        if role in ("system", "developer"):
            blocks.append("[SYSTEM]\n" + _text_of(m.get("content")))
        elif role == "user":
            blocks.append("[USER]\n" + _text_of(m.get("content")))
        elif role == "assistant":
            text = _text_of(m.get("content"))
            if text:
                blocks.append("[ASSISTANT]\n" + text)
            calls = [{"id": c.get("id"), "name": (c.get("function") or {}).get("name"),
                      "arguments": (c.get("function") or {}).get("arguments")}
                     for c in (m.get("tool_calls") or []) if isinstance(c, dict)]
            if calls:
                blocks.append("[ASSISTANT TOOL CALLS]\n" + json.dumps(calls, ensure_ascii=False))
        elif role == "tool":
            blocks.append(f"[TOOL RESULT id={m.get('tool_call_id', '')}]\n" + _text_of(m.get("content")))
    return "\n\n".join(blocks)


def _model_arg(model: Any) -> Optional[str]:
    m = str(model or "").strip()
    if "/" in m:  # "icdev-claude/sonnet" -> "sonnet"
        m = m.rsplit("/", 1)[-1]
    return m or None


def build_turn(body: dict, *, tool_max_bytes: int = DEFAULT_TOOL_DEFS_MAX_BYTES) -> Dict[str, Any]:
    """Everything one ``claude -p`` run needs: ``args``, ``prompt``, tool stats."""
    tools = body.get("tools") or []
    choice = body.get("tool_choice")
    use_tools = bool(tools) and choice != "none"
    conversation = render_conversation(body.get("messages") or [])
    args = ["-p", "--tools", "", "--strict-mcp-config", "--setting-sources", "",
            "--no-session-persistence"]
    model = _model_arg(body.get("model"))
    if model:
        args += ["--model", model]
    kept = dropped = 0
    if use_tools:
        tool_json, kept, dropped = render_tools(tools, tool_max_bytes)
        prompt = f"[AVAILABLE TOOLS]\n{tool_json}\n\n[CONVERSATION]\n{conversation}"
        if choice == "required" or (isinstance(choice, dict) and choice.get("type") == "function"):
            prompt += "\n\n[NOTE] The caller requires a tool call this turn (kind=\"tool_calls\")."
        args += ["--system-prompt", SYSTEM_PROMPT_TOOLS, "--json-schema", json.dumps(TURN_SCHEMA)]
    else:
        prompt = conversation
        args += ["--system-prompt", SYSTEM_PROMPT_PLAIN]
    return {"args": args, "prompt": prompt, "use_tools": use_tools, "model": model,
            "tools_kept": kept, "tools_dropped": dropped,
            "turns": len(body.get("messages") or [])}


# --------------------------------------------------------------------------- #
# claude -p result -> OpenAI response / distinct error
# --------------------------------------------------------------------------- #

class BridgeError(Exception):
    """A failure with an HTTP status and a machine-readable ``type``/``code``."""

    def __init__(self, status: int, type_: str, message: str, code: str = "",
                 failover: bool = False):
        super().__init__(message)
        self.status, self.type, self.code, self.failover = status, type_, code or type_, failover

    def body(self) -> dict:
        return {"error": {"message": str(self), "type": self.type, "code": self.code,
                          "failover": self.failover}}


def classify_failure(run: Any) -> BridgeError:
    """A failed ClaudeRun as the error the harness (and omx-adapt-06) sees."""
    if getattr(run, "missing_binary", False):
        return BridgeError(503, "claude_cli_unavailable", run.error)
    text = (run.error or "").lower()
    for code, needles in QUOTA_PATTERNS:
        if any(n in text for n in needles):
            return BridgeError(429, "claude_quota_exhausted",
                               f"claude subscription quota exhausted ({code}): {run.error}",
                               code=code, failover=True)
    if any(n in text for n in AUTH_PATTERNS):
        return BridgeError(503, "claude_not_authenticated",
                           f"the claude CLI is not logged in to a claude.ai subscription: {run.error}")
    if "ceiling" in text:
        return BridgeError(504, "claude_timeout", run.error)
    return BridgeError(502, "claude_cli_error", run.error or "claude failed without a message")


def _structured(run: Any) -> Optional[dict]:
    so = (run.envelope or {}).get("structured_output")
    if isinstance(so, dict):
        return so
    try:
        parsed = json.loads(run.text)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) and "kind" in parsed else None


def _arguments(raw: Any) -> str:
    """Tool-call arguments as the JSON-object string OpenAI clients expect."""
    if isinstance(raw, dict):
        return json.dumps(raw, ensure_ascii=False)
    text = str(raw or "").strip() or "{}"
    try:
        parsed = json.loads(text)
    except ValueError:
        raise BridgeError(502, "claude_malformed_tool_call",
                          f"claude returned tool arguments that are not JSON: {text[:200]}")
    if not isinstance(parsed, dict):
        raise BridgeError(502, "claude_malformed_tool_call",
                          f"claude returned tool arguments that are not a JSON object: {text[:200]}")
    return json.dumps(parsed, ensure_ascii=False)


def usage_block(run: Any) -> dict:
    cache_read = int(run.cache_read_input_tokens or 0)
    cache_write = int(run.cache_creation_input_tokens or 0)
    prompt = int(run.input_tokens or 0) + cache_read + cache_write
    completion = int(run.output_tokens or 0)
    return {"prompt_tokens": prompt, "completion_tokens": completion,
            "total_tokens": prompt + completion,
            "prompt_tokens_details": {"cached_tokens": cache_read},
            "icdev": {"usd_basis": USD_BASIS, "route": "cli_bridge",
                      "input_tokens": int(run.input_tokens or 0),
                      "cache_read_input_tokens": cache_read,
                      "cache_creation_input_tokens": cache_write}}


def to_completion(run: Any, turn: Dict[str, Any], requested_model: str) -> dict:
    """A successful ClaudeRun as an OpenAI ``chat.completion``. Raises BridgeError."""
    message: Dict[str, Any] = {"role": "assistant", "content": None}
    finish = "stop"
    if turn["use_tools"]:
        out = _structured(run)
        if out is None:
            raise BridgeError(502, "claude_empty_response",
                              "claude returned no structured turn for a tool-enabled request")
        calls = [c for c in (out.get("calls") or []) if isinstance(c, dict) and c.get("name")]
        if out.get("kind") == "tool_calls" and calls:
            message["tool_calls"] = [
                {"id": f"call_{uuid.uuid4().hex[:24]}", "type": "function",
                 "function": {"name": str(c["name"]), "arguments": _arguments(c.get("arguments_json"))}}
                for c in calls]
            finish = "tool_calls"
            if out.get("text"):
                message["content"] = str(out["text"])
        else:
            message["content"] = str(out.get("text") or "")
    else:
        message["content"] = run.text
    if finish == "stop" and not (message["content"] or "").strip():
        raise BridgeError(502, "claude_empty_response", "claude returned an empty answer")
    return {"id": f"chatcmpl-{uuid.uuid4().hex}", "object": "chat.completion",
            "created": int(time.time()), "model": requested_model or (turn["model"] or "claude"),
            "choices": [{"index": 0, "message": message, "finish_reason": finish}],
            "usage": usage_block(run)}


def to_sse(completion: dict) -> List[bytes]:
    """A finished completion as buffered ``chat.completion.chunk`` SSE events."""
    choice = completion["choices"][0]
    msg = choice["message"]
    delta: Dict[str, Any] = {"role": "assistant"}
    if msg.get("content"):
        delta["content"] = msg["content"]
    if msg.get("tool_calls"):
        delta["tool_calls"] = [{"index": i, **c} for i, c in enumerate(msg["tool_calls"])]
    base = {"id": completion["id"], "object": "chat.completion.chunk",
            "created": completion["created"], "model": completion["model"]}
    events = [
        {**base, "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
        {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": choice["finish_reason"]}],
         "usage": completion["usage"]},
    ]
    return [f"data: {json.dumps(e, ensure_ascii=False)}\n\n".encode("utf-8") for e in events] + \
        [b"data: [DONE]\n\n"]


# --------------------------------------------------------------------------- #
# Usage record (unpriced)
# --------------------------------------------------------------------------- #

def record_usage(turn: Dict[str, Any], run: Any, error: str = "") -> None:
    """One ``cli_llm_jobs`` row per turn, best effort: a DB outage never fails a turn.

    Only a SUMMARY of the prompt is stored -- a harness resends its whole history
    and tool catalogue every turn. There is no cost column: the usage is unpriced.
    """
    try:
        js = importlib.import_module("tools.llm.cli_bridge.job_store")
        job_id = js.create_job(
            function="claude_bridge",
            prompt=(f"[claude-bridge] {turn['turns']} messages, {turn['tools_kept']} tools "
                    f"({turn['tools_dropped']} dropped)"),
            model_id=turn.get("model") or None, backend=JOB_BACKEND)
        if error:
            js.fail_job(job_id, error[:2000])
        else:
            js.complete_job(job_id, "",
                            input_tokens=run.input_tokens, output_tokens=run.output_tokens,
                            cache_read_input_tokens=run.cache_read_input_tokens,
                            cache_creation_input_tokens=run.cache_creation_input_tokens)
    except Exception as exc:  # noqa: BLE001 -- recording is advisory
        logger.debug("claude-bridge usage record failed: %s", exc)


# --------------------------------------------------------------------------- #
# HTTP server
# --------------------------------------------------------------------------- #

class BridgeState:
    """Per-server state: token, concurrency, the last quota failure (health)."""

    def __init__(self, token: str, *, max_concurrent: int, queue_wait: float = 600.0,
                 tool_max_bytes: int = DEFAULT_TOOL_DEFS_MAX_BYTES,
                 models: Optional[List[str]] = None,
                 recorder: Callable[..., None] = record_usage):
        self.token = token
        self.max_concurrent = max_concurrent
        self.slots = threading.BoundedSemaphore(max_concurrent)
        self.queue_wait = queue_wait
        self.tool_max_bytes = tool_max_bytes
        self.models = models or configured_models()
        self.recorder = recorder
        self.inflight = 0
        self.lock = threading.Lock()
        self.last_quota_error: Optional[dict] = None

    def authorized(self, headers: Mapping[str, str]) -> bool:
        auth = headers.get("Authorization") or ""
        presented = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        presented = presented or (headers.get("x-api-key") or "").strip()
        return bool(presented) and hmac.compare_digest(presented.encode(), self.token.encode())

    def complete(self, body: dict) -> dict:
        """One chat-completions request -> completion dict. Raises BridgeError."""
        if not isinstance(body.get("messages"), list) or not body["messages"]:
            raise BridgeError(400, "invalid_request_error", "messages must be a non-empty list")
        turn = build_turn(body, tool_max_bytes=self.tool_max_bytes)
        if not self.slots.acquire(timeout=self.queue_wait):
            raise BridgeError(503, "bridge_busy",
                              f"all {self.max_concurrent} claude slots busy for {self.queue_wait:.0f}s")
        try:
            with self.lock:
                self.inflight += 1
            run = _backend().run_claude(turn["args"], input_text=turn["prompt"])
        finally:
            with self.lock:
                self.inflight -= 1
            self.slots.release()
        if not run.ok:
            err = classify_failure(run)
            if err.failover:
                self.last_quota_error = {"at": time.time(), "code": err.code, "message": str(err)}
            self.recorder(turn, run, error=f"{err.type}: {err}")
            raise err
        try:
            completion = to_completion(run, turn, str(body.get("model") or ""))
        except BridgeError as err:
            self.recorder(turn, run, error=f"{err.type}: {err}")
            raise
        self.last_quota_error = None
        self.recorder(turn, run)
        logger.info("claude-bridge turn: model=%s tools=%d/%d finish=%s",
                    turn["model"], turn["tools_kept"], turn["tools_dropped"],
                    completion["choices"][0]["finish_reason"])
        return completion

    def health(self) -> dict:
        return {"ok": True, "claude_on_path": bool(shutil.which(_backend()._cli_binary())),
                "inflight": self.inflight, "max_concurrent": self.max_concurrent,
                "last_quota_error": self.last_quota_error, "usd_basis": USD_BASIS}


def make_handler(state: BridgeState):
    class Handler(BaseHTTPRequestHandler):
        server_version = "icdev-claude-bridge/1"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # route through the ICDEV logger
            logger.debug("claude-bridge %s - " + fmt, self.address_string(), *args)

        def _send(self, status: int, payload: dict, headers: Optional[Dict[str, str]] = None):
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def _error(self, err: BridgeError):
            headers = {FAILOVER_HEADER: err.type} if err.failover else None
            self._send(err.status, err.body(), headers)

        def _path(self) -> str:
            return self.path.split("?", 1)[0].rstrip("/") or "/"

        def do_GET(self):  # noqa: N802
            path = self._path()
            if path == "/health":
                return self._send(200, state.health())
            if not state.authorized(self.headers):
                return self._error(BridgeError(401, "authentication_error", "bad or missing bearer token"))
            if path == "/v1/models":
                return self._send(200, {"object": "list", "data": [
                    {"id": m, "object": "model", "owned_by": "claude-cli"} for m in state.models]})
            return self._error(BridgeError(404, "not_found", f"no route {path}"))

        def do_POST(self):  # noqa: N802
            if not state.authorized(self.headers):
                return self._error(BridgeError(401, "authentication_error", "bad or missing bearer token"))
            if self._path() != "/v1/chat/completions":
                return self._error(BridgeError(404, "not_found", f"no route {self._path()}"))
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if length <= 0 or length > MAX_BODY_BYTES:
                return self._error(BridgeError(400, "invalid_request_error", "missing or oversized body"))
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(body, dict):
                    raise ValueError("body is not an object")
            except ValueError as exc:
                return self._error(BridgeError(400, "invalid_request_error", f"bad JSON: {exc}"))
            try:
                completion = state.complete(body)
            except BridgeError as err:
                return self._error(err)
            except Exception as exc:  # noqa: BLE001 -- a bug is a 500 WITH its reason
                logger.exception("claude-bridge internal error")
                return self._error(BridgeError(500, "bridge_internal_error", f"{type(exc).__name__}: {exc}"))
            if not body.get("stream"):
                return self._send(200, completion)
            events = to_sse(completion)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(sum(len(e) for e in events)))
            self.end_headers()
            for e in events:
                self.wfile.write(e)

    return Handler


def make_server(host: str = "127.0.0.1", port: int = 0, *, state: BridgeState) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((resolve_bind(host), port), make_handler(state))
    server.daemon_threads = True
    return server


# --------------------------------------------------------------------------- #
# systemd --user unit (Linux) + harness configs (written by `icdev omarchy setup`)
# --------------------------------------------------------------------------- #

def render_unit(python: str, root: Path, *, source_checkout: bool, port: int = DEFAULT_PORT) -> str:
    """The ``systemd --user`` unit that keeps the bridge running.

    ``Environment=ANTHROPIC_API_KEY=`` blanks a key a user-level environment.d
    might export; ``run_claude`` scrubs it again for the child regardless.
    """
    module = "tools.cli" if source_checkout else "icdev.tools.cli"
    workdir = f"WorkingDirectory={root.as_posix()}\n" if source_checkout else ""
    return (
        "# CUI // SP-CTI -- generated by `icdev omarchy setup` (omx-adapt-04); re-run, do not edit\n"
        "[Unit]\n"
        "Description=ICDEV Claude bridge (loopback; claude.ai subscription via claude -p)\n"
        "After=network.target\n\n"
        "[Service]\n"
        "Type=simple\n"
        f"{workdir}"
        "Environment=ANTHROPIC_API_KEY=\n"
        "Environment=ANTHROPIC_AUTH_TOKEN=\n"
        f"ExecStart={python} -m {module} claude-bridge serve --port {port}\n"
        "Restart=on-failure\n"
        "RestartSec=5\n\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def render_opencode_provider(existing: Optional[dict], url: str, token_file: Path,
                             models: List[str]) -> dict:
    """``opencode.json`` with ``provider.icdev-claude``; every other key survives.

    The key is a ``{file:...}`` reference, so the token is never copied.
    """
    from tools.llm.harness_llm_config import OPENCODE_SCHEMA
    config = dict(existing) if isinstance(existing, dict) else {}
    config.setdefault("$schema", OPENCODE_SCHEMA)
    providers = dict(config.get("provider") or {})
    providers[PROVIDER_ID] = {
        "npm": "@ai-sdk/openai-compatible",
        "name": "Claude via ICDEV bridge (claude.ai subscription)",
        "options": {"baseURL": url, "apiKey": "{file:%s}" % token_file.as_posix()},
        "models": {m: {"name": f"Claude {m} (bridge)", "tool_call": True} for m in models},
    }
    config["provider"] = providers
    return config


def render_pi_provider(existing: Optional[dict], url: str, token: str, models: List[str]) -> dict:
    """Pi ``models.json`` with ``providers.icdev-claude``; other providers survive."""
    config = dict(existing) if isinstance(existing, dict) else {}
    providers = dict(config.get("providers") or {})
    providers[PROVIDER_ID] = {
        "api": "openai-completions",
        "apiKey": token,
        "baseUrl": url,
        "compat": {"supportsDeveloperRole": False, "supportsReasoningEffort": False},
        "models": [{"id": m, "input": ["text"]} for m in models],
    }
    config["providers"] = providers
    return config


def harness_config_path(harness: str, environ: Mapping[str, str], home: Path) -> Path:
    xdg = Path(environ.get("XDG_CONFIG_HOME") or home / ".config")
    if harness == "opencode":
        return xdg / "opencode" / "opencode.json"
    if harness == "pi":
        return Path(environ.get("PI_CODING_AGENT_DIR") or home / ".pi" / "agent") / "models.json"
    raise KeyError(harness)


def render_harness_config(harness: str, existing: Optional[dict], *, url: str, token_file: Path,
                          token: str, models: List[str]) -> dict:
    if harness == "opencode":
        return render_opencode_provider(existing, url, token_file, models)
    if harness == "pi":
        return render_pi_provider(existing, url, token, models)
    raise KeyError(harness)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _status(port: int) -> dict:
    import urllib.request
    path = token_path()
    out: Dict[str, Any] = {"url": bridge_url(port), "token_file": str(path),
                           "token_present": path.is_file(),
                           "claude_on_path": bool(shutil.which(_backend()._cli_binary()))}
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as r:  # nosec B310
            out["health"] = json.loads(r.read().decode("utf-8"))
        out["running"] = True
    except Exception as exc:  # noqa: BLE001
        out["running"] = False
        out["error"] = str(exc)
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="icdev claude-bridge", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("serve", help="Run the loopback endpoint in the foreground.")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=None)
    st = sub.add_parser("status", help="Token, claude binary, and a /health probe.")
    st.add_argument("--port", type=int, default=None)
    st.add_argument("--json", action="store_true")
    un = sub.add_parser("unit", help="Print the systemd --user unit (Linux only).")
    un.add_argument("--port", type=int, default=None)
    args = ap.parse_args(argv)
    port = args.port or configured_port()

    if args.cmd == "unit":
        if not sys.platform.startswith("linux"):
            print(f"unsupported on {sys.platform}: systemd --user units are Linux-only; "
                  "run `icdev claude-bridge serve` in a terminal instead", file=sys.stderr)
            return 2
        from icdev.core.paths import SOURCE_MARKERS, repo_root
        root = repo_root().resolve()
        print(render_unit(sys.executable, root, port=port,
                          source_checkout=any((root / m).exists() for m in SOURCE_MARKERS)), end="")
        return 0
    if args.cmd == "status":
        report = _status(port)
        print(json.dumps(report, indent=2) if args.json else
              "\n".join(f"{k}: {v}" for k, v in report.items()))
        return 0 if report["running"] else 1

    try:
        host = resolve_bind(args.host)
    except ValueError as exc:
        print(f"icdev claude-bridge: {exc}", file=sys.stderr)
        return 2
    token = ensure_token(token_path())
    state = BridgeState(token, max_concurrent=_max_concurrent())
    server = make_server(host, port, state=state)
    print(f"icdev claude-bridge: serving {bridge_url(server.server_address[1])} "
          f"(token: {token_path()}, max {state.max_concurrent} concurrent)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
