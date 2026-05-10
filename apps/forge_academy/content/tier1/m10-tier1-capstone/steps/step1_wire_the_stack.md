# Tier 1 Capstone — Wire the Stack

You've built the individual components. Now you wire them into a complete AI system: a RAG-augmented agent that exposes its capabilities as an MCP server.

This is the pattern powering every ICDEV mission from here on. The capstone test: can you assemble the stack from memory?

## The target architecture

```
MCP Client (Claude / ICDEV agent)
        │
        │  tools/call: compliance_qa(question)
        ▼
┌─────────────────────────────────────────────────────────────────┐
│                   MCP Server (your code)                        │
│                                                                  │
│  @tool compliance_qa(question: str) → str                       │
│         │                                                        │
│         ├── RAG: retrieve(question, COMPLIANCE_DOCS, top_k=2)   │
│         │         → [doc1, doc2]                                 │
│         │                                                        │
│         └── Agent: run(question + context)                       │
│                   → tool_stig_lookup / tool_calculate_risk       │
│                   → final_answer                                 │
└─────────────────────────────────────────────────────────────────┘
```

## What you need to implement

1. **A retrieval function** — from M03 (keyword scoring against a corpus)
2. **An agent run function** — from M04 (decide → act → observe loop)
3. **An MCP server** — from M05 (handle `tools/list` and `tools/call`)
4. **Wire them together** — the MCP tool calls the agent, the agent uses retrieval for context

## Success criteria

- `tools/list` returns at least 1 tool with a valid schema
- `tools/call compliance_qa({"question": "..."})` returns a grounded answer
- The answer references at least one compliance document source
- The agent loop executes ≥1 tool internally before returning
- No external imports required

This is the foundation every real ICDEV agent is built on. Once you pass this, you're ready for Tier 2 role tracks.
