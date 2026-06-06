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

