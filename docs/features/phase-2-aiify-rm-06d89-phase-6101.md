<!-- CUI // SP-CTI -->
# Phase 2 — AI-ify Opportunity 6101 (Determination: Duplicate)

- **Kanban task:** `aiify-rm-06d89-phase-6101`
- **Roadmap:** `rm-06d89040cf` (scan_id 43)
- **Opportunity:** 6101
- **Pattern → paradigm:** `hardcoded_threshold` → `anomaly_detection`
- **External module:** `src/paperless/parsers/registry.py` (paperless-ngx shallow clone `aiify_git_zwu66zfu`, since reaped/GONE — external, unmodifiable)

## Determination

**Duplicate of `dfb671f09`** — the MONITOR `log_analyzer` anomaly-detection
refactor.

`src/paperless/parsers/registry.py` is paperless-ngx's **parser registry** — a
generic dispatch/lookup table mapping MIME types to parser classes. It carries
no match-confidence (`matching.py` → DIC `_is_confident_match`), date-parsing
(`plugins/date_parsing/*` → DIC `assess_document_dates`), or search-relevance
(`search/*` → DIC `detect_search_anomalies`) semantics. Per the established
mapping, a paperless `hardcoded_threshold` → `anomaly_detection` opp with no
strong filename semantics maps to the generic internal analog: the MONITOR
`tools/monitor/log_analyzer.py` anomaly-detection layer.

That analog already exists and is the faithful AI-ification of the
`hardcoded_threshold` → `anomaly_detection` pattern: inline z-score / error-rate
constants were lifted into the config-driven `anomaly_detection` block in
`args/monitoring_config.yaml`, with both a standard z-score method and a robust
MAD (modified z-score) method.

## Verification (HEAD `6ff5bd957`, branch kanban/aiify-rm-06d89-phase-6101)

- External clone `aiify_git_zwu66zfu/src/paperless/parsers/registry.py`: **GONE** (reaped by engine).
- `dfb671f09` is an **ancestor of HEAD**.
- `_load_anomaly_cfg` + z-score/MAD present in both mirrors:
  - `tools/monitor/log_analyzer.py` (def L477)
  - `icdev/tools/monitor/log_analyzer.py` (def L300)
- `anomaly_detection` block present in `args/monitoring_config.yaml` (L91).
- `tests/test_log_analyzer_anomaly.py`: **23/23 pass**.

No new code required — closing as a duplicate with `bypass_verification`.
