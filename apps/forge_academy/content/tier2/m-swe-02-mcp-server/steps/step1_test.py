# Auto-grader for SWE/Arch M2: MCP Server Design

# ── Test: generate_schema ─────────────────────────────────────────────────────

def sample_func(control_id: str, system: str = "ICDEV-Prod", limit: int = 10) -> dict:
    return {}

schema = generate_schema(sample_func)
assert schema is not None, "generate_schema() returned None"
assert isinstance(schema, dict), f"generate_schema() must return dict"
assert "type" in schema and schema["type"] == "object", "Schema type must be 'object'"
assert "properties" in schema, "Schema must have 'properties'"
assert "required" in schema, "Schema must have 'required'"

props = schema["properties"]
assert "control_id" in props, "control_id should be in properties"
assert "system" in props, "system should be in properties"
assert "limit" in props, "limit should be in properties"
assert props["control_id"].get("type") == "string", \
    f"control_id is str -> type 'string', got {props['control_id'].get('type')}"
assert props["limit"].get("type") == "integer", \
    f"limit is int -> type 'integer', got {props['limit'].get('type')}"

required = schema["required"]
assert "control_id" in required, "control_id has no default -> required"
assert "system" not in required, "system has default -> not required"
assert "limit" not in required, "limit has default -> not required"

# Function with no annotations
def no_annotations(param1, param2="default"):
    pass
schema_na = generate_schema(no_annotations)
assert schema_na["properties"]["param1"].get("type") == "string", \
    "No annotation defaults to 'string'"
assert "param1" in schema_na["required"], "param1 has no default -> required"
assert "param2" not in schema_na["required"], "param2 has default -> not required"

# ── Setup registry and tools ──────────────────────────────────────────────────

registry = MCPToolRegistry()

@registry.tool(name="get_compliance_status", description="Check control compliance status")
def get_compliance_status(control_id: str, system: str = "ICDEV-Prod") -> dict:
    return {"control": control_id, "status": "compliant", "system": system}

@registry.tool(name="list_findings", description="List open findings for a system")
def list_findings(system: str, severity: str = "all") -> list:
    return [{"id": "V-220706", "severity": "medium", "system": system}]

@registry.tool(name="error_tool", description="Always raises an error")
def error_tool(msg: str) -> dict:
    raise RuntimeError(f"Tool error: {msg}")

# ── Test: list_tools ──────────────────────────────────────────────────────────

tools = registry.list_tools()
assert tools is not None, "list_tools() returned None"
assert isinstance(tools, list), f"list_tools() must return list"
assert len(tools) == 3, f"Should have 3 tools registered, got {len(tools)}"

tool_names = [t["name"] for t in tools]
assert "get_compliance_status" in tool_names, "get_compliance_status should be registered"
assert "list_findings" in tool_names, "list_findings should be registered"

for t in tools:
    assert "name" in t, f"Each tool must have 'name'"
    assert "description" in t, f"Each tool must have 'description'"
    assert "parameters" in t, f"Each tool must have 'parameters'"

# ── Test: call — successful call ──────────────────────────────────────────────

result = registry.call("get_compliance_status", {"control_id": "IA-2"})
assert result is not None, "call() returned None"
assert isinstance(result, dict), f"call() must return dict"
assert "result" in result, "Call result must have 'result'"
assert "error" in result, "Call result must have 'error'"
assert result["error"] is None, f"Successful call should have error=None, got '{result['error']}'"
assert isinstance(result["result"], dict), "Result should be dict"
assert result["result"]["control"] == "IA-2", \
    f"control should be 'IA-2', got '{result['result']['control']}'"
assert result["result"]["system"] == "ICDEV-Prod", \
    "Default system should be ICDEV-Prod"

# With explicit system
result2 = registry.call("get_compliance_status", {"control_id": "AC-2", "system": "ICDEV-Dev"})
assert result2["result"]["system"] == "ICDEV-Dev", "Explicit system should be used"

# ── Test: call — unknown tool ─────────────────────────────────────────────────

err_result = registry.call("nonexistent_tool", {})
assert err_result["result"] is None, "Unknown tool -> result=None"
assert err_result["error"] is not None, "Unknown tool -> error message"
assert "nonexistent_tool" in err_result["error"] or "not found" in err_result["error"].lower(), \
    f"Error should mention tool name, got '{err_result['error']}'"

# ── Test: call — tool raises exception ───────────────────────────────────────

exc_result = registry.call("error_tool", {"msg": "test error"})
assert exc_result["result"] is None, "Erroring tool -> result=None"
assert exc_result["error"] is not None, "Erroring tool -> error message"
assert "Tool error" in exc_result["error"] or "error" in exc_result["error"].lower(), \
    f"Error should propagate exception message, got '{exc_result['error']}'"

# ── Test: call — None arguments defaults to {} ────────────────────────────────

result_none_args = registry.call("list_findings", None)
# list_findings requires 'system' arg — this should error gracefully, not crash
assert "result" in result_none_args and "error" in result_none_args, \
    "call(tool, None) must return result dict, not crash"

# ── Test: get_schema ──────────────────────────────────────────────────────────

s = registry.get_schema("get_compliance_status")
assert s is not None, "get_schema() for known tool should not return None"
assert isinstance(s, dict), "get_schema() must return dict"
assert "properties" in s, "Schema must have 'properties'"

missing_schema = registry.get_schema("nonexistent")
assert missing_schema is None, "get_schema() for unknown tool should return None"

print("PASS: MCPToolRegistry complete. generate_schema + tool decorator + list_tools + call + get_schema all verified.")
