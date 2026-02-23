---
name: icdev-mbse
description: MBSE integration — import SysML/DOORS, build digital thread, generate code, sync, assess DES compliance
allowed-tools: Bash, Read, Write, Edit, Glob, Grep
---

# $icdev-mbse

## What This Does
Integrates MBSE into the ICDEV SDLC workflow:
1. **Import SysML models** from Cameo Systems Modeler (XMI format)
2. **Import requirements** from IBM DOORS NG (ReqIF format)
3. **Build digital thread** — end-to-end traceability from requirements to NIST controls
4. **Generate code** from model elements (blocks → classes, activities → functions)
5. **Sync model and code** — detect drift and synchronize in either direction
6. **Assess DES compliance** — DoDI 5000.87 Digital Engineering Strategy conformance
7. **Create PI snapshots** — version model state per SAFe Program Increment

All operations produce CUI-marked output and record audit trail entries.

## Error Handling
- If XMI parse fails: report validation errors, continue with existing model data
- If ReqIF parse fails: report validation errors, continue with existing requirements
- If no model data exists: skip code generation and sync, report warning
- If DES assessment fails: report partial results, continue with other steps