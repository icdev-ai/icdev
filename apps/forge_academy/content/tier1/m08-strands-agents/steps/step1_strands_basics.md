# Amazon Strands Agents

Amazon Strands is AWS's open-source agentic AI SDK — purpose-built for production deployment on AWS GovCloud. Where frameworks like LangChain focus on composing LLM chains, Strands focuses on the full agent execution loop: tool selection, multi-step reasoning, streaming, and AWS service integration out of the box.

## Why Strands for DoD/GovCon

- **Native Bedrock integration** — Routes to Claude, Titan, Llama on AWS GovCloud without config
- **Built-in tool use** — Structured tool definitions map directly to Claude's tool_use API
- **Streaming native** — Real-time token streaming for long-running compliance scans
- **Open source** — Auditable, air-gap deployable, no phone-home telemetry
- **MCP compatible** — Strands agents can expose tools as MCP servers

## Core concepts

### Agent definition
```python
from strands import Agent
from strands.models import BedrockModel

agent = Agent(
    model=BedrockModel(model_id="us.anthropic.claude-sonnet-4-5"),
    tools=[scan_stig, generate_report],
    system_prompt="You are a STIG compliance analyst...",
)
result = agent("Scan system XYZ for CAT I findings.")
```

### Tool definition
```python
from strands import tool

@tool
def scan_stig(control_id: str, system_name: str) -> str:
    """Scan a STIG control and return compliance status."""
    # your implementation
    return f"Control {control_id}: FAIL — PermitRootLogin enabled"
```

The `@tool` decorator works identically to FastMCP's `@mcp.tool()` — type hints become the schema, docstring becomes the description. Strands and MCP share this pattern deliberately.

## ICDEV on AWS GovCloud

ICDEV's IL4/IL5 deployment uses Strands agents on AWS GovCloud with:
- Bedrock as the LLM provider (Claude Sonnet 4 for reasoning, Haiku for classification)
- DynamoDB for agent state persistence across restarts
- Lambda for tool execution (each tool runs in an isolated function)
- EventBridge for event-driven agent triggers (new STIG finding → auto-triage agent)

## Your task

Implement the `@tool` decorator pattern (same mechanics as FastMCP step) and wire it into a Strands-style agent class. The pattern is identical — you're reinforcing the mental model while learning Strands-specific vocabulary.
