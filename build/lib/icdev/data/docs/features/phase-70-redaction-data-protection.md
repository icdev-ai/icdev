# CUI // SP-CTI
# Phase 70: Redaction & Data Protection (D-RDT-1)

## Overview

Sensitive data protection system for ALL ICDEV™ modules and child apps.
Protects PII, program names, past performance, pricing, and personnel
across every LLM invocation — not just GovCon/Proposal Genesis.

**Architecture:** Three-layer detection (regex + Ollama NER + deny-lists) +
conversation-scoped surrogate registry + IL-aware anonymization operators.
Central hook in `router.invoke()` protects all 50+ LLM callers automatically.

**Key principles:**
- Proposal content routes to local Ollama only. Cloud LLMs never see raw proposal data.
- WriteGuard rewrite stays on Claude (rewrites style, not content).
- Zero external NLP dependencies (no spaCy, no Presidio — works on Python 3.14+).
- Air-gap safe: NER via Ollama gemma3 (pre-loaded local model, no downloads).
- Child apps inherit redaction automatically (`tools/redaction/` not in PARENT_ONLY_DIRS).

## Components

| Component | File | Purpose |
|-----------|------|---------|
| Detector | `tools/redaction/detector.py` | Three-layer PII detection (regex + NER + deny-list) |
| NER Recognizer | `tools/redaction/ner_recognizer.py` | Ollama gemma3 NER for PERSON/ORGANIZATION + regex fallback |
| Anonymizer | `tools/redaction/anonymizer.py` | IL-aware anonymization (surrogate/redact/mask/hash) |
| GovCon Recognizers | `tools/redaction/govcon_recognizers.py` | Contract#, CAGE, pricing, program name, org recognizers |
| Registry | `tools/redaction/registry.py` | Conversation-scoped real<->surrogate mapping with SQLite persistence |
| GovCon Sanitizer | `tools/redaction/govcon_sanitizer.py` | Pre-LLM hook for all modules (central in router.invoke) |
| Pulse Sanitizer | `tools/redaction/pulse_sanitizer.py` | Case study de-identification for Pulse publication |
| DB Scanner | `tools/redaction/db_scanner.py` | Scan proposal tables for PII density |
| Redaction Config | `args/redaction_config.yaml` | Global entity config, thresholds, IL overrides, scope |
| GovCon Config | `args/redaction_govcon.yaml` | Program deny-list, contract patterns, pricing, past perf rules |

## Integration Points

### Central Hook: router.invoke() (ALL modules)

**`_pre_invoke_redaction()`** in `tools/llm/router.py` runs before EVERY LLM call.
Inserted after injection scan, before two-tier routing. This single hook protects
all 50+ LLM callers (code_generation, narrative_generation, pulse_draft, rag_rerank,
proposal_drafting, etc.) without per-module integration.

Scope control via `args/redaction_config.yaml`:
- `mode: all` — redact for every LLM call (default)
- `mode: enforced_only` — only redact for explicitly listed functions
- `skip_for_local_only: true` — skip when all models in chain are Ollama
- `exempt_modules` — vision functions, deterministic tools

### LLM Routing (args/llm_config.yaml)

Four proposal functions forced to local Ollama only:
- `proposal_drafting` → `[qwen3-local, llama-local]`
- `requirement_extraction` → `[qwen3-local, llama-local]`
- `bid_scoring` → `[qwen3-local, llama-local]`
- `color_review` → `[qwen3-local, llama-local]`

WriteGuard rewrite (`wg_rewrite`) stays on `claude-sonnet-api` — rewrites style/grammar,
not sensitive content.

### R7 Draft Reflex (response_drafter.py)

Defense-in-depth: `GovConSanitizer.sanitize_for_llm()` wraps the prompt before
`router.invoke()`. Even though routing is local-only, the sanitizer catches PII
as an additional protection layer.

### R12 Publish Reflex (publish.py)

`PulseSanitizer.sanitize_article()` replaces the minimal `_sanitize_title()` regex.
Now strips: agency names from tags, program names, contract numbers, NAICS codes.
Generalizes: dates to quarters, dollar amounts to ranges, team counts to rounded values.

### Child App Inheritance

`tools/redaction/` is NOT in `PARENT_ONLY_DIRS` — child apps automatically inherit:
- All detection capabilities (regex, NER, deny-lists)
- The `router.invoke()` hook (since `tools/llm/` is also inherited)
- Config files in `args/` (redaction_config.yaml, redaction_govcon.yaml)
- Database tables (created by init_icdev_db.py)

### Database (init_icdev_db.py)

Two new tables:
- `redaction_registry` — session-scoped real<->surrogate mappings (unique constraint on session+entity+hash)
- `redaction_audit` — append-only detection/anonymization audit trail (NIST AU)

`redaction_audit` added to `APPEND_ONLY_TABLES` in `.claude/hooks/pre_tool_use.py`.

### MCP Gateway (tool_registry.py + gap_handlers.py)

Four MCP tools registered:
- `redaction_detect` — detect PII in text
- `redaction_anonymize` — anonymize text with IL-aware operators
- `redaction_sanitize_proposal` — sanitize proposal content for LLM
- `redaction_scan_db` — scan database tables for PII

### Security Gate (security_gates.yaml)

`redaction_data_protection` gate blocks on:
- PII detected in cloud-bound prompts
- CUI data in unprotected channels
- Program names or pricing in cloud prompts

## Detection Capabilities

### Standard PII (Presidio built-in, 50+ entity types)
PERSON, EMAIL_ADDRESS, PHONE_NUMBER, US_SSN, CREDIT_CARD, US_PASSPORT,
US_DRIVER_LICENSE, US_BANK_NUMBER, IP_ADDRESS, LOCATION, etc.

### GovCon-Specific (custom recognizers)
- DoD contract numbers (W91CRB-20-D-0001, FA8750-21-C-0502)
- Solicitation numbers (W91CRB-24-R-0001)
- CAGE codes (5-char with context boosting)
- UEI numbers (12-char with context)
- Dollar amounts ($1.2M, $450/hr)
- Labor rates ($125/hr, $150.00 per hour)
- Indirect rate percentages (12.5% fringe, 150% overhead)
- Program names (deny-list from config)
- Protected organizations (deny-list from config)
- Agency names (deny-list with codename surrogates)

### Past Performance Generalization
- Dollar amounts → ranges ("$12.5M" → "a multi-million dollar")
- Specific dates → quarters ("March 2023" → "Q1 2023")
- Team counts → rounded ("47 engineers" → "50+ engineers")
- Triggered by context phrases ("past performance", "prior contract", etc.)

## Anonymization Operators

| Operator | When Used | Reversible | Example |
|----------|-----------|------------|---------|
| surrogate | Names, locations, orgs | Yes (via registry) | "John Smith" → "Alex Alpha" |
| redact | SSN, credit card, email | No | "123-45-6789" → "[US_SSN]" |
| mask | Phone, IP | No | "703-555-1234" → "********1234" |
| hash | Referential integrity | No | "value" → "a1b2c3d4..." |
| keep | Entities to preserve | N/A | Unchanged |

## IL-Aware Override

| Level | Treatment Override |
|-------|-------------------|
| IL2 (Public) | PERSON→mask, EMAIL→mask |
| IL4 (CUI/GovCloud) | SSN→redact, CREDIT_CARD→redact, PERSON→surrogate |
| IL5 (CUI/Dedicated) | All PII→redact or surrogate |
| IL6 (SECRET/SIPR) | All PII→hard redact |

## Configuration

### Adding Program Names (args/redaction_govcon.yaml)

```yaml
program_names:
  - "Eagle Vision 2.0"
  - "JRDC Phase III"
  - "Project Thunderdome"
```

### Adding Protected Organizations

```yaml
protected_organizations:
  - "Acme Federal Solutions"
  - "NovaTech Consulting"
```

### Agency Codename Mapping

```yaml
agency_surrogates:
  "Missile Defense Agency": "Agency ALPHA"
  "Defense Health Agency": "Agency BRAVO"
```

## Dependencies

### Required (already in ICDEV™)
- pyyaml, pathlib, sqlite3, hashlib, json, re (stdlib)
- requests (for Ollama NER API calls)
- faker (for realistic surrogate generation)

### NER Backend
- **Ollama gemma3** — local NER for PERSON/ORGANIZATION (air-gap safe, pre-loaded)
- No spaCy, no Presidio, no model downloads required
- Presidio supported as optional enhancement (requires Python < 3.14)

### Graceful Degradation
- Ollama available → full NER (PERSON, ORGANIZATION) + regex + deny-lists
- Ollama unavailable → regex heuristics (title-prefixed names, email-derived names, 60 federal agencies) + deny-lists
- All environments → standard PII regex (SSN, email, phone, credit card, contract#, pricing)

## Decision Records

- **D-RDT-1:** Proposal functions route to local Ollama only (args/llm_config.yaml)
- **D-RDT-2:** WriteGuard rewrite stays on Claude (style, not content)
- **D-RDT-3:** Chat-time anonymization (adapted from ep3 design), not ingestion-time
- **D-RDT-4:** Surrogate vs hard-redact split by entity risk level
- **D-RDT-5:** Conversation-scoped registries with 72-hour TTL
- **D-RDT-8:** Ollama gemma3 for NER (replaces spaCy — Python 3.14 compatible, air-gap safe)
- **D-RDT-9:** Central hook in router.invoke() protects all modules and child apps
- **D-RDT-10:** mode: all (default) — every LLM call redacted unless exempt
- **D-RDT-6:** Past performance generalization via deterministic rules (no LLM)
- **D-RDT-7:** Deny-list config in YAML (FORGE args/ pattern)

## Source Attribution

Architecture patterns adapted from:
- [Microsoft Presidio](https://microsoft.github.io/presidio/) — NLP-based PII detection engine
- [AI Automators ep3 PRD](https://github.com/theaiautomators/claude-code-agentic-rag-series/tree/main/ep3-redaction-anonymization-video) — Chat-time anonymization, surrogate registries, two-pass detection
