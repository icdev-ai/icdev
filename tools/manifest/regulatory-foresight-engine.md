# Regulatory Foresight Engine (D352 — pint-regfore)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Regulatory Foresight Engine (D352 — pint-regfore)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Foresight Engine | tools/regulatory_foresight/foresight_engine.py | Main orchestration engine for the Regulatory Foresight pipeline (D352). Scan cycle: source_scanner → impact_scorer → deduplication → persist to regulatory_foresight_signals → cross-register high-score signals (≥0.70) to innovation_signals. Supports daemon mode with configurable scan_interval_hours and optional quiet hours. | --run, --status, --signals, --min-score SCORE, --daemon, --json | Scan results / stats / signal list (JSON or text) |
| Source Scanner | tools/regulatory_foresight/source_scanner.py | Regulatory signal source scanner with three public API integrations: Federal Register (no key), Congress.gov (CONGRESS_API_KEY or DEMO_KEY), Regulations.gov (REGULATIONS_GOV_API_KEY). Produces raw signal dicts matching regulatory_foresight_signals schema with deterministic 24-char SHA-256 signal IDs for deduplication. Every network call is wrapped with 10-second timeout — air-gap safe (failures return [] with warning). | --scan, --all, --source NAME (federal_register \| congress_bills \| regulations_gov), --json | List of raw signal dicts |
| Impact Scorer | tools/regulatory_foresight/impact_scorer.py | Deterministic composite impact scorer (no LLM, no DB). Three sub-scores: time_to_mandate (0–1, weight 0.40), icdev_impact via token overlap against icdev_capability_catalog.json (0–1, weight 0.35), blast_radius as proportion of ICDEV capabilities touched (0–1, weight 0.25). Weights loaded from args/regulatory_foresight_config.yaml; composite capped at 1.0 and rounded to 4 decimal places. | signal dict | Annotated signal dict with time_to_mandate_score, icdev_impact_score, blast_radius_score, composite_score |

### DB Tables
| Table | Owner | Notes |
|-------|-------|-------|
| `regulatory_foresight_signals` | foresight_engine.py, source_scanner.py | Append-only signal storage; migration 066; fields: source, doc_id, title, url, proposed_at, comment_deadline, estimated_mandate_date, affected_frameworks (JSON), icdev_impact_areas (JSON), time_to_mandate_days, icdev_impact_score, blast_radius_score, composite_score, status, innovation_signal_id, scanned_at, classification |
| `innovation_signals` | foresight_engine.py | Cross-registration target; high-score signals (composite ≥ auto_signal_threshold) written here by the engine |

### Configuration
| File | Purpose |
|------|---------|
| `args/regulatory_foresight_config.yaml` | scan_interval_hours, auto_signal_threshold (default 0.70), quiet hours, score_weights |
| `context/govcon/icdev_capability_catalog.json` | ICDEV capability/product keywords used by impact_scorer.py |

### Air-Gap Behavior
- `source_scanner.py`: all HTTP calls wrapped; returns `[]` on any network error — safe in air-gapped environments
- `impact_scorer.py`: fully offline (no network, no LLM)
- `foresight_engine.py`: degrades gracefully when scanner returns empty (no crash, logs warning)
