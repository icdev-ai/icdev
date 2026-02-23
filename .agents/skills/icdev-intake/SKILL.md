---
name: icdev-intake
description: Start or resume an AI-driven requirements intake session with conversational guidance, gap detection, readiness scoring, and SAFe decomposition
allowed-tools: Bash, Read, Write, Edit, Glob, Grep
---

# $icdev-intake

## What This Does
Runs RICOAS Phase 1 — conversational requirements intake:
1. **Create or resume** an intake session with project and customer context
2. **Guide conversation** — ask structured questions about mission, users, security, data, integration, timeline
3. **Auto-extract requirements** from each customer response
4. **Detect ambiguities and gaps** in real-time using pattern matching
5. **Score readiness** across 5 dimensions (completeness, clarity, feasibility, compliance, testability)
6. **Decompose into SAFe hierarchy** — Epic > Capability > Feature > Story with BDD acceptance criteria
7. **Export decomposed items** for handoff to Architect agent (ATLAS workflow)

All operations produce classification-marked output per project settings and record audit trail entries.

## Error Handling
- If session creation fails: check project exists, report error, suggest --new flag
- If document extraction finds 0 requirements: suggest different document type or manual entry
- If readiness score stuck below 0.7: show trend, highlight weakest dimensions, suggest specific questions
- If gap detection finds critical gaps: block decomposition, list required resolutions
- If session resumed after long gap: summarize full context before continuing