---
name: icdev-boundary
description: "Assesses ATO boundary impact, generates COA alternatives, builds vendor dependency graphs, and runs SCRM assessments per NIST 800-161. Use when evaluating how a requirement affects an existing ATO boundary, triaging CVEs through the supply chain, checking Section 889 vendor compliance, or managing ISA/MOU lifecycle agreements."
allowed-tools: Bash, Read, Write, Edit, Glob, Grep
---

# $icdev-boundary

## What This Does
Runs RICOAS Phase 2 — ATO boundary impact and supply chain intelligence:
1. **Assess boundary impact** — evaluate how requirements affect existing ATO boundaries (GREEN/YELLOW/ORANGE/RED)
2. **Generate alternatives** — for RED-tier requirements, produce 3-5 alternative COAs within existing ATO
3. **Build dependency graph** — track vendor supply chain with upstream/downstream relationships
4. **SCRM assessment** — score vendors across 6 NIST 800-161 dimensions, check Section 889
5. **ISA/MOU lifecycle** — check expiring agreements, flag overdue reviews
6. **CVE triage** — triage vulnerabilities with blast radius propagation through dependency graph

All operations produce CUI-marked output and record audit trail entries.

## Error Handling
- If ATO system not registered: prompt to register before assessment
- If requirement not found: check intake session, report error
- If dependency graph empty: suggest adding vendors via add_vendor
- If SCRM assessment fails: check vendor records exist
- If CVE propagation finds circular dependency: break cycle, report warning