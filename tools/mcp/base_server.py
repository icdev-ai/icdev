#!/usr/bin/env python3
# CUI // SP-CTI
"""Base MCP (Model Context Protocol) server implementing JSON-RPC 2.0 over stdio.

Uses Content-Length framing (LSP-style):
    Content-Length: N\r\n\r\n{json_payload}

Reads requests from stdin, dispatches to registered handlers, writes responses to stdout.
Notifications (methods starting with "notifications/") receive no response.
"""

import json
import logging
import sys
import traceback
from typing import Any, Callable, Dict, List, Optional

# Configure logging to stderr so it does not interfere with the JSON-RPC stdio transport.
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("mcp.base")

PROTOCOL_VERSION = "2024-11-05"


class MCPServer:
    """Base MCP server with JSON-RPC 2.0 dispatch over stdio with Content-Length framing."""

    def __init__(self, name: str = "icdev-mcp", version: str = "1.0.0"):
        self.name = name
        self.version = version

        # Registered tools: name -> {description, input_schema, handler}
        self._tools: Dict[str, dict] = {}

        # Registered resources: uri -> {name, description, mime_type, handler}
        self._resources: Dict[str, dict] = {}

        # Registered prompts: name -> {description, arguments, handler}
        self._prompts: Dict[str, dict] = {}

        # Whether the client has completed initialization handshake
        self._initialized = False

    # ------------------------------------------------------------------
    # Registration helpers
    # ------------------------------------------------------------------

    def register_tool(
        self,
        name: str,
        description: str,
        input_schema: dict,
        handler: Callable[[dict], Any],
    ) -> None:
        """Register a tool that clients can invoke via tools/call.

        Args:
            name: Unique tool name (e.g. "project_create").
            description: Human-readable description of the tool.
            input_schema: JSON Schema object describing the tool's input parameters.
            handler: Callable that receives the arguments dict and returns a result.
        """
        self._tools[name] = {
            "description": description,
            "input_schema": input_schema,
            "handler": handler,
        }
        logger.info("Registered tool: %s", name)

    def register_resource(
        self,
        uri: str,
        name: str,
        description: str,
        handler: Callable[[str], Any],
        mime_type: str = "application/json",
    ) -> None:
        """Register a resource that clients can read via resources/read.

        Args:
            uri: Resource URI (e.g. "projects://list"). May contain {placeholders}.
            name: Human-readable resource name.
            description: Human-readable description.
            handler: Callable that receives the URI string and returns content.
            mime_type: MIME type of the resource content.
        """
        self._resources[uri] = {
            "name": name,
            "description": description,
            "mime_type": mime_type,
            "handler": handler,
        }
        logger.info("Registered resource: %s", uri)

    def register_prompt(
        self,
        name: str,
        description: str,
        arguments: List[dict],
        handler: Callable[[dict], Any],
    ) -> None:
        """Register a prompt template that clients can retrieve via prompts/get.

        Args:
            name: Unique prompt name.
            description: Human-readable description.
            arguments: List of argument descriptors, each with 'name', 'description', 'required'.
            handler: Callable that receives the arguments dict and returns prompt messages.
        """
        self._prompts[name] = {
            "description": description,
            "arguments": arguments,
            "handler": handler,
        }
        logger.info("Registered prompt: %s", name)

    # ------------------------------------------------------------------
    # JSON-RPC 2.0 transport (Content-Length framing)
    # ------------------------------------------------------------------

    def _read_message(self) -> Optional[dict]:
        """Read a single Content-Length-framed JSON-RPC message from stdin.

        Returns None on EOF.
        """
        # Read headers until we find Content-Length
        content_length = None
        while True:
            line = sys.stdin.buffer.readline()
            if not line:
                return None  # EOF
            line_str = line.decode("utf-8", errors="replace").strip()
            if line_str == "":
                # End of headers
                if content_length is not None:
                    break
                # Empty line without headers: try reading next line as raw JSON
                # (some clients send bare JSON lines without Content-Length)
                continue
            if line_str.lower().startswith("content-length:"):
                try:
                    content_length = int(line_str.split(":", 1)[1].strip())
                except ValueError:
                    logger.warning("Invalid Content-Length header: %s", line_str)
                    return None
            elif line_str.startswith("{"):
                # Bare JSON line (no Content-Length framing) - parse directly
                try:
                    return json.loads(line_str)
                except json.JSONDecodeError:
                    logger.warning("Malformed JSON line: %s", line_str[:200])
                    return None

        # Read exactly content_length bytes
        body = sys.stdin.buffer.read(content_length)
        if not body:
            return None
        try:
            return json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            logger.error("JSON decode error: %s", exc)
            return None

    def _write_message(self, obj: dict) -> None:
        """Write a Content-Length-framed JSON-RPC message to stdout."""
        body = json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
        body_bytes = body.encode("utf-8")
        header = f"Content-Length: {len(body_bytes)}\r\n\r\n"
        sys.stdout.buffer.write(header.encode("utf-8"))
        sys.stdout.buffer.write(body_bytes)
        sys.stdout.buffer.flush()

    # ------------------------------------------------------------------
    # JSON-RPC helpers
    # ------------------------------------------------------------------

    def _make_response(self, request_id: Any, result: Any) -> dict:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def _make_error(
        self, request_id: Any, code: int, message: str, data: Any = None
    ) -> dict:
        err: Dict[str, Any] = {"code": code, "message": message}
        if data is not None:
            err["data"] = data
        return {"jsonrpc": "2.0", "id": request_id, "error": err}

    # Standard JSON-RPC error codes
    PARSE_ERROR = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603

    # ------------------------------------------------------------------
    # Method dispatch
    # ------------------------------------------------------------------

    def _dispatch(self, msg: dict) -> Optional[dict]:
        """Dispatch a JSON-RPC request/notification and return the response dict.

        Returns None for notifications (no id field).
        """
        method = msg.get("method", "")
        params = msg.get("params", {})
        request_id = msg.get("id")
        is_notification = "id" not in msg

        logger.debug("Dispatch: method=%s, id=%s", method, request_id)

        try:
            result = self._handle_method(method, params)
        except Exception as exc:
            logger.error("Error handling %s: %s\n%s", method, exc, traceback.format_exc())
            if is_notification:
                return None
            return self._make_error(
                request_id, self.INTERNAL_ERROR, str(exc), traceback.format_exc()
            )

        if is_notification:
            return None

        return self._make_response(request_id, result)

    def _handle_method(self, method: str, params: dict) -> Any:
        """Route a method to its handler. Returns the result value."""

        # ----- MCP lifecycle -----
        if method == "initialize":
            return self._handle_initialize(params)

        if method == "notifications/initialized":
            self._initialized = True
            return None  # notification, no response needed

        # ----- Tools -----
        if method == "tools/list":
            return self._handle_tools_list(params)

        if method == "tools/call":
            return self._handle_tools_call(params)

        # ----- Resources -----
        if method == "resources/list":
            return self._handle_resources_list(params)

        if method == "resources/read":
            return self._handle_resources_read(params)

        # ----- Prompts -----
        if method == "prompts/list":
            return self._handle_prompts_list(params)

        if method == "prompts/get":
            return self._handle_prompts_get(params)

        # ----- Ping (keepalive) -----
        if method == "ping":
            return {}

        # ----- Unknown method -----
        raise _MethodNotFound(f"Unknown method: {method}")

    # ------------------------------------------------------------------
    # MCP handlers
    # ------------------------------------------------------------------

    def _handle_initialize(self, params: dict) -> dict:
        """Handle the initialize handshake. Return server info and capabilities."""
        capabilities: Dict[str, Any] = {}
        if self._tools:
            capabilities["tools"] = {"listChanged": False}
        if self._resources:
            capabilities["resources"] = {"subscribe": False, "listChanged": False}
        if self._prompts:
            capabilities["prompts"] = {"listChanged": False}

        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": capabilities,
            "serverInfo": {
                "name": self.name,
                "version": self.version,
            },
        }

    def _handle_tools_list(self, params: dict) -> dict:
        """Return all registered tools with their input schemas."""
        tools_list = []
        for name, info in self._tools.items():
            tools_list.append(
                {
                    "name": name,
                    "description": info["description"],
                    "inputSchema": info["input_schema"],
                }
            )
        return {"tools": tools_list}

    def _handle_tools_call(self, params: dict) -> dict:
        """Dispatch a tool call to the registered handler.

        D284: Auto-instruments all 15 MCP servers via trace span.
        """
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        if tool_name not in self._tools:
            raise _MethodNotFound(f"Unknown tool: {tool_name}")

        handler = self._tools[tool_name]["handler"]

        # D284: Create trace span wrapping tool execution
        try:
            from tools.observability import get_tracer
            from tools.observability.tracer import set_content_tag
            import hashlib as _hl
            tracer = get_tracer()
        except ImportError:
            tracer = None

        span = None
        if tracer:
            _args_hash = _hl.sha256(
                json.dumps(arguments, default=str, sort_keys=True).encode()
            ).hexdigest()[:16]
            span = tracer.start_span("mcp.tool_call", kind="SERVER", attributes={
                "gen_ai.operation.name": "execute_tool",
                "mcp.tool.name": tool_name,
                "mcp.server.name": getattr(self, "server_name", self.__class__.__name__),
                "mcp.tool.args_hash": _args_hash,
            })

        try:
            result = handler(arguments)
        except Exception as exc:
            logger.error("Tool %s raised: %s", tool_name, exc)
            if span:
                span.set_status("ERROR", str(exc))
                span.add_event("exception", {
                    "exception.type": type(exc).__name__,
                    "exception.message": str(exc),
                })
                span.end()
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {"error": str(exc), "tool": tool_name}, indent=2
                        ),
                    }
                ],
                "isError": True,
            }

        # Normalize result to MCP content format
        if isinstance(result, dict):
            text = json.dumps(result, indent=2, default=str)
        elif isinstance(result, str):
            text = result
        else:
            text = json.dumps(result, indent=2, default=str)

        if span:
            _result_hash = _hl.sha256(text.encode()).hexdigest()[:16]
            span.set_attribute("mcp.tool.result_hash", _result_hash)
            span.set_status("OK")
            span.end()

        return {
            "content": [{"type": "text", "text": text}],
            "isError": False,
        }

    def _handle_resources_list(self, params: dict) -> dict:
        """Return all registered resources."""
        resources_list = []
        for uri, info in self._resources.items():
            resources_list.append(
                {
                    "uri": uri,
                    "name": info["name"],
                    "description": info["description"],
                    "mimeType": info["mime_type"],
                }
            )
        return {"resources": resources_list}

    def _handle_resources_read(self, params: dict) -> dict:
        """Read a resource by URI."""
        uri = params.get("uri", "")

        # Try exact match first
        if uri in self._resources:
            handler = self._resources[uri]["handler"]
            mime_type = self._resources[uri]["mime_type"]
            content = handler(uri)
            if isinstance(content, dict) or isinstance(content, list):
                content = json.dumps(content, indent=2, default=str)
            return {
                "contents": [
                    {"uri": uri, "mimeType": mime_type, "text": str(content)}
                ]
            }

        # Try pattern match (simple {id} template matching)
        for registered_uri, info in self._resources.items():
            if "{" in registered_uri:
                # Build a simple regex from the template
                import re

                pattern = re.escape(registered_uri).replace(r"\{", "(?P<").replace(r"\}", ">[^/]+)")
                # Replace the named group markers properly
                pattern = registered_uri
                parts = pattern.split("{")
                regex_parts = [re.escape(parts[0])]
                for part in parts[1:]:
                    param_name, rest = part.split("}", 1)
                    regex_parts.append(f"(?P<{param_name}>[^/]+)")
                    regex_parts.append(re.escape(rest))
                full_regex = "^" + "".join(regex_parts) + "$"
                match = re.match(full_regex, uri)
                if match:
                    handler = info["handler"]
                    mime_type = info["mime_type"]
                    content = handler(uri)
                    if isinstance(content, dict) or isinstance(content, list):
                        content = json.dumps(content, indent=2, default=str)
                    return {
                        "contents": [
                            {"uri": uri, "mimeType": mime_type, "text": str(content)}
                        ]
                    }

        raise _MethodNotFound(f"Unknown resource URI: {uri}")

    def _handle_prompts_list(self, params: dict) -> dict:
        """Return all registered prompts."""
        prompts_list = []
        for name, info in self._prompts.items():
            prompts_list.append(
                {
                    "name": name,
                    "description": info["description"],
                    "arguments": info["arguments"],
                }
            )
        return {"prompts": prompts_list}

    def _handle_prompts_get(self, params: dict) -> dict:
        """Get a prompt by name with provided arguments."""
        prompt_name = params.get("name", "")
        arguments = params.get("arguments", {})

        if prompt_name not in self._prompts:
            raise _MethodNotFound(f"Unknown prompt: {prompt_name}")

        handler = self._prompts[prompt_name]["handler"]
        result = handler(arguments)

        # Result should be a dict with 'description' and 'messages'
        if isinstance(result, dict) and "messages" in result:
            return result

        # Wrap bare string or dict into messages format
        if isinstance(result, str):
            return {
                "description": self._prompts[prompt_name]["description"],
                "messages": [
                    {"role": "user", "content": {"type": "text", "text": result}}
                ],
            }

        return {
            "description": self._prompts[prompt_name]["description"],
            "messages": [
                {
                    "role": "user",
                    "content": {
                        "type": "text",
                        "text": json.dumps(result, indent=2, default=str),
                    },
                }
            ],
        }

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Main event loop. Reads JSON-RPC messages from stdin and dispatches them.

        Blocks until stdin is closed (EOF) or a keyboard interrupt is received.
        """
        logger.info("MCP server '%s' v%s starting (protocol %s)", self.name, self.version, PROTOCOL_VERSION)

        try:
            while True:
                msg = self._read_message()
                if msg is None:
                    logger.info("EOF on stdin, shutting down.")
                    break

                # Validate basic JSON-RPC shape
                if not isinstance(msg, dict) or "method" not in msg:
                    # If it has an 'id', send an error response
                    request_id = msg.get("id") if isinstance(msg, dict) else None
                    if request_id is not None:
                        response = self._make_error(
                            request_id, self.INVALID_REQUEST, "Missing 'method' field"
                        )
                        self._write_message(response)
                    continue

                response = self._dispatch(msg)
                if response is not None:
                    self._write_message(response)

        except KeyboardInterrupt:
            logger.info("Interrupted, shutting down.")
        except Exception as exc:
            logger.critical("Fatal error in main loop: %s\n%s", exc, traceback.format_exc())
            sys.exit(1)


class _MethodNotFound(Exception):
    """Raised when a JSON-RPC method is not found."""
    pass
