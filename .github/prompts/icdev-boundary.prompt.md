---
mode: agent
description: "Assess ATO boundary impact and manage supply chain risk for requirements"
tools:
  - terminal
  - file_search
---

# icdev-boundary

Runs RICOAS Phase 2 — ATO boundary impact and supply chain intelligence:
1. **Assess boundary impact** — evaluate how requirements affect existing ATO boundaries (GREEN/YELLOW/ORANGE/RED)
2. **Generate alternatives** — for RED-tier requirements, produce 3-5 alternative COAs within existing ATO
3. **Build dependency graph** — track vendor supply chain with upstream/downstream relationships
4. **SCRM assessment** — score vendors across 6 NIST 800-161 dimensions, check Section 889
5. **ISA/MOU lifecycle** — check expiring agreements, flag overdue reviews
6. **CVE triage** — triage vulnerabilities with blast radius propagation through dependency graph

All operations produce CUI-marked output and record audit trail entries.
