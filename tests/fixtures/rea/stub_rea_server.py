# CUI // SP-CTI
"""Stub REA stdio MCP server for CI (rea-wire-01). Needs no Node and no Ghidra.

Replays ``rea_6.4.0_recorded.json``: the recorded ``tools/list`` subset and a
recorded ``tools/call`` result. A tool with no recorded response answers with
REA's structured error shape, so a test can never mistake a replay gap for an
analysis result. Every ``tools/call`` is appended to the file named by
``STUB_REA_CALL_LOG`` (when set), which lets a test prove a refused call never
reached the server.

``STUB_REA_RESPONSES`` (rea-wire-02) names a JSON file mapping tool name to a
recorded MCP result; it OVERRIDES the default replay for those tools, so a test
can compose a scenario (a PE that opens, a WASM REA cannot read) from the
recordings in ``rea_6.4.0_adapter_responses.json``.
"""
import json
import os
import sys
from pathlib import Path

RECORDED = json.loads(
    (Path(__file__).resolve().parent / "rea_6.4.0_recorded.json").read_text(encoding="utf-8")
)
if os.environ.get("STUB_REA_RESPONSES"):
    RECORDED["responses"] = {
        **RECORDED["responses"],
        **json.loads(Path(os.environ["STUB_REA_RESPONSES"]).read_text(encoding="utf-8")),
    }


def _answer(method, params):
    if method == "initialize":
        return {"protocolVersion": "2024-11-05", "serverInfo": RECORDED["server_info"],
                "capabilities": {"tools": {}}}
    if method == "tools/list":
        return {"tools": RECORDED["tools"]}
    if method == "tools/call":
        name = params.get("name")
        log = os.environ.get("STUB_REA_CALL_LOG")
        if log:
            with open(log, "a", encoding="utf-8") as handle:
                handle.write(json.dumps({"name": name, "arguments": params.get("arguments")}) + "\n")
        recorded = RECORDED["responses"].get(name)
        if recorded is not None:
            return recorded
        error = {"error": {"code": "stub_not_recorded", "category": "unavailable",
                           "retryable": False, "message": f"no recorded response for {name}"}}
        return {"content": [{"type": "text", "text": json.dumps(error)}], "isError": True}
    return {}


for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    message = json.loads(line)
    if message.get("id") is None:
        continue
    result = _answer(message.get("method"), message.get("params") or {})
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}) + "\n")
    sys.stdout.flush()
