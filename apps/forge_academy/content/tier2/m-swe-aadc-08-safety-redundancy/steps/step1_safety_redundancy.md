---
ontology_id: icdev:mission:m-swe-aadc-08-safety-redundancy:step:1
step_class: icdev:Lesson
---

# Safety Redundancy Design — Defense in Depth for Agentic Systems

## Two different "safety" numbers

The AADC canvas measures safety in two places, and this mission uses both:

1. **Safety redundancy coverage**: `GET /agentic-ai/api/designs/<id>/safety-redundancy`
   (`analyze_safety_redundancy()` in `tools/agentic_ai_canvas/safety_redundancy.py`).
   It is the percentage of agent nodes that have at least one safety or governance
   node somewhere *upstream*. An agent nothing guards is "unprotected". A design
   with no agents returns `null`, not 100.
2. **Assessment score**: `POST /agentic-ai/api/designs/<id>/assess`. This weights
   NIST AI RMF and OWASP LLM Top 10 checks; the rules live in
   `tools/agentic_ai_canvas/constants.py`.

## Safety nodes and the OWASP checks they satisfy

These are the node `type` strings the assessor looks for. The OWASP IDs are the
ones in `OWASP_LLM_CHECKS` (OWASP LLM Top 10, 2023 numbering).

| Node type | Function | Check satisfied |
|-----------|----------|-----------------|
| `input-sanitizer` | Prompt injection defense | LLM01: an `input-sanitizer` directly upstream of each `llm` / `llm-local` node (passes trivially when the design has no LLM node) |
| `guardrail` | General input/output filter | Counts as a protective node for redundancy coverage, but is *not* what LLM01 checks for |
| `output-validator` | Output handling safety | LLM02: reachable downstream of each LLM node |
| `pii-detector` **and** `redaction-engine` | Sensitive data protection | LLM06 (both required) |
| `rate-limiter` or `token-budget` | Resource exhaustion | LLM04 Model DoS |
| `circuit-breaker` or `hitl-gate` downstream of each agent | Runaway agent termination | LLM08 Excessive Agency |
| `confidence-threshold` | Bounds hallucination / overreliance | LLM09, NIST MEA-1 |
| `audit-logger` | Action trail | LLM10 Model Theft |
| `hitl-gate` / `approval-workflow` | Human oversight | NIST GOV-1 |
| `alert-manager` | Incident response path | NIST MNG-1 |
| `trusted-monitor` | Independent oversight of autonomous agents | Phase 4 MNG-3 (required when an `autonomous-agent` is present) |

## The Triangle of Trust

```
          [Trusted Monitor]
               /     \
              /       \
[Input Guard]---[Agent]---[Output Validator]
              \       /
               \     /
          [Circuit Breaker]
```

## Your Mission

Build an AADC design where every agent is guarded upstream (redundancy coverage
>= 80%) and the assessment scores at least 70.

> **Where this runs.** This is a reading step: run the script from your own terminal
> against your own ICDEV instance (the Academy sandbox has no network). If auth is
> on, send a dashboard API key as `Authorization: Bearer icdev_dash_...`.

```python
import requests

BASE = "http://localhost:5050"
HEADERS = {}  # e.g. {"Authorization": "Bearer icdev_dash_..."} if auth is on

def node(i, t, label):
    return {"id": i, "type": t, "label": label}

def edge(i, s, t):
    return {"id": i, "source": s, "target": t, "type": "data-flow"}

graph = {
    "nodes": [
        node("n1",  "orchestrator",         "Primary Agent"),
        node("n2",  "analyst-agent",        "Analyst Sub-Agent"),
        # Input side: PII detection -> redaction -> guardrail -> agent
        node("n3",  "pii-detector",         "PII Detector"),
        node("n4",  "redaction-engine",     "Redaction Engine"),
        node("n5",  "guardrail",            "Input Guardrail (OWASP LLM01)"),
        node("n6",  "rate-limiter",         "Rate Limiter"),
        node("n7",  "trusted-monitor",      "Trusted Monitor"),
        # Output side: confidence -> circuit breaker -> validator -> HITL
        node("n8",  "confidence-threshold", "Confidence Threshold (>= 0.75)"),
        node("n9",  "circuit-breaker",      "Circuit Breaker"),
        node("n10", "output-validator",     "Output Validator (OWASP LLM02)"),
        node("n11", "hitl-gate",            "HITL Gate"),
        node("n12", "alert-manager",        "Alert Manager"),
        node("n13", "audit-logger",         "Audit Logger"),
    ],
    "edges": [
        edge("e1", "n3", "n4"), edge("e2", "n4", "n5"), edge("e3", "n5", "n1"),
        edge("e4", "n6", "n1"), edge("e5", "n7", "n1"), edge("e6", "n1", "n2"),
        edge("e7", "n2", "n8"), edge("e8", "n8", "n9"), edge("e9", "n9", "n10"),
        edge("e10", "n10", "n11"), edge("e11", "n12", "n11"), edge("e12", "n1", "n13"),
    ],
}

d = requests.post(f"{BASE}/agentic-ai/api/designs", headers=HEADERS, json={
    "name": "Safety Redundancy Mission", "classification": "CUI", "graph": graph,
}).json()
did = d["id"]

# 1. Redundancy coverage: % of agents with a safety/governance node upstream
red = requests.get(f"{BASE}/agentic-ai/api/designs/{did}/safety-redundancy", headers=HEADERS).json()
print(f"Safety coverage: {red['score']}%  unprotected: {[a['label'] for a in red['unprotected_agents']]}")

# 2. Assessment score + remaining findings
result = requests.post(f"{BASE}/agentic-ai/api/designs/{did}/assess", headers=HEADERS).json()
print(f"Assessment score: {result['score']}")
for f in result.get("findings", []):
    print(f"  [{f['severity']}] {f['framework']}: {f['title']}")

assert red["score"] >= 80, f"Every agent should be guarded upstream: got {red['score']}%"
assert result["score"] >= 70, f"Safety-redundant design should score >= 70: got {result['score']}"
print("PASSED: Safety redundancy architecture validated")
```

What is left after this design passes? Look at the remaining findings. They are not
safety gaps; they are design-completeness gaps: no `inference-input` node (system
boundary), no `drift-detector` or `baseline-snapshot`, and no AI use-case inventory
entry. Remove the `rate-limiter` or the `redaction-engine` and re-run to watch LLM04 or
LLM06 come back.
