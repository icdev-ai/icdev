# MEMORY.md

> Curated long-term facts and preferences. Read at session start.

---

## User Preferences

- Prefers direct, no-nonsense communication
- Wants comprehensive implementation (not stubs)
- Air-gapped Gov/DoD environment — PyPi + AWS Bedrock only
- CUI markings required on ALL artifacts

## Key Facts

- **Project initialized:** 2026-02-14
- **Framework:** GOTCHA (Goals, Orchestration, Tools, Args, Context, Hard Prompts)
- **System:** ICDEV — Intelligent Coding Development meta-builder for Gov/DoD (IL4+)
- **Database:** SQLite (data/icdev.db) with 32 tables across 7 domains
- **MCP Servers:** 5 stdio servers (core, compliance, builder, infra, knowledge) — all operational
- **A2A Agents:** 8 agents across 3 tiers (Core, Domain, Support) — ports 8443-8450
- **Skills:** 10 Claude Code custom commands (/icdev-init through /icdev-knowledge)
- **Goals:** 12 goal workflow documents
- **Hard Prompts:** 19 reusable LLM templates across 6 categories
- **Dashboard:** Flask SSR web app with CUI banners
- **K8s:** 14 manifests for full deployment stack
- **Docker:** 2 STIG-hardened Dockerfiles (agent-base, dashboard)
- **Total files:** ~175

## Active Projects

- **ICDEV System** — Phase 0-12 complete. Phase 13 (integration testing) pending.

## Important Decisions

- **D1:** SQLite for ICDEV internals; PostgreSQL for apps ICDEV builds
- **D2:** Stdio transport for MCP (Claude Code); HTTPS+mTLS for A2A (K8s inter-agent)
- **D3:** Flask over FastAPI (simpler, fewer deps, auditable SSR, smaller STIG surface)
- **D4:** Statistical methods for pattern detection; Bedrock LLM for root cause analysis
- **D5:** CUI markings applied at generation time (inline, not post-processing)
- **D6:** Audit trail is append-only/immutable (no UPDATE/DELETE — NIST AU compliance)

## Relationships / Contacts

- *(None recorded yet)*

## Lessons Learned

- Background agents may be denied Write/Bash permissions by user settings — create files directly when agents fail
- Large parallel agent spawns can hit rate limits — stagger if possible
- MCP servers should use lazy imports (_import_tool pattern) so they work even when underlying tools are still being built
- Plan output files can exceed token limits — chunk reads for large outputs
