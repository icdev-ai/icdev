---
mode: agent
description: ""Run Digital Program Twin simulations and generate COAs for requirements. Use when simulating program scenarios or generating courses of action.""
tools:
  - terminal
  - file_search
---

# icdev-simulate

## Example
```
#prompt:icdev-simulate proj-123 --session sess-abc --coas
```
This generates 3 COAs for the session's requirements, simulates each, and presents the comparison.