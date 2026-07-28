# Redaction & Data Protection (Phase 70 — D-RDT-1)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Redaction & Data Protection (Phase 70 — D-RDT-1)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Redaction Detector | tools/redaction/detector.py | Presidio + custom recognizer PII/sensitive data detection engine | --detect, --detect-file, --list-entities, --health, --json, --gate | Detection results |
| Redaction Anonymizer | tools/redaction/anonymizer.py | Anonymization engine with IL-aware operators (surrogate/redact/mask/hash) | --anonymize, --anonymize-file, --il, --session, --show-text, --health, --json, --gate | Anonymized text + metadata |
| NER Recognizer | tools/redaction/ner_recognizer.py | Ollama gemma3 NER for PERSON/ORGANIZATION + regex fallback (air-gap safe) | --extract, --no-ollama, --health, --json, --gate | Named entities |
| GovCon Recognizers | tools/redaction/govcon_recognizers.py | Custom recognizers for contract#, CAGE, pricing, program names, orgs, custom terms | --list, --json | Recognizer definitions |
| Redaction Registry | tools/redaction/registry.py | Conversation-scoped real↔surrogate mapping with SQLite persistence | --session, --list, --cleanup, --health, --json | Mapping entries |
| GovCon Sanitizer | tools/redaction/govcon_sanitizer.py | Pre-LLM hook: sanitizes proposal content before cloud LLM invocation | --sanitize, --sanitize-file, --function, --il, --local-only, --show-text, --health, --json, --gate | Sanitized text + metadata |
| Pulse Sanitizer | tools/redaction/pulse_sanitizer.py | Pulse case study de-identification (agency, program, pricing, past perf) | --sanitize-article, --title, --body, --tags, --health, --json, --gate | Sanitized article |
| DB PII Scanner | tools/redaction/db_scanner.py | Scan proposal DB tables for PII density per column | --scan, --table, --sample-size, --health, --json, --gate | PII density report |
| Redaction Config | args/redaction_config.yaml | Global redaction config: entities, thresholds, operators, IL overrides, scope, audit | (data) | YAML config |
| GovCon Redaction Config | args/redaction_govcon.yaml | GovCon-specific: program deny-list, contract patterns, pricing patterns, past perf rules, Pulse sanitization | (data) | YAML config |

| Deny-list Seeder | tools/redaction/denylist_seeder.py | GovCon deny-list seeder (trust-mask-04). The program-name / protected-organization deny-lists in `args/redaction_govcon.yaml` ship empty, so an operator's own org, partner and customer names are not protected before cloud-LLM egress until populated. Discovers the derivable set (protected organizations) from the company profile and merges them non-destructively into the config. Program names stay operator-specific. Public API: `seed_from_profile(profile) -> {list_name: [names]}`, `merge_denylists(existing, seeds) -> dict`. | `--profile own_company [--dry-run\|--write] --json` | JSON {seeded, merged, written} |
| Redaction Scan Reflex | tools/genesis/reflexes/redaction_scan_reflex.py | Genesis reflex — scheduled at-rest PII/CUI sweep (trust-mask-03). Runs the DB PII scanner, builds a remediation plan for columns whose PII density meets the threshold, and files deduped `[PII-SCAN]` kanban remediation cards, extending the detect-only scanner into a detect-plan-remediate loop. GREEN tier (reads sampled DB rows, files kanban tasks). Public API: `run(config, state) -> dict`. | `run(config, state)` | JSON {success, metric_value, details} |
