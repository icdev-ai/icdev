<!-- CUI // SP-CTI -->
# Phase 2 — AI-ify Opportunity 5985 (Determination: Duplicate)

- **Kanban task:** `aiify-rm-06d89-phase-5985`
- **Roadmap:** `rm-06d89040cf` (scan_id 43)
- **Opportunity:** 5985
- **Pattern → paradigm:** `hardcoded_threshold` → `anomaly_detection`
- **External module:** `docker/rootfs/usr/local/bin/wait-for-redis.py` (paperless-ngx shallow clone `aiify_git_zwu66zfu`, since reaped/GONE — external, unmodifiable)

## Determination

**Duplicate of `dfb671f09`** — the MONITOR `log_analyzer` anomaly-detection
refactor.

`wait-for-redis.py` is paperless-ngx's **container bootstrap readiness loop** — a
small shell-style script that polls Redis on startup until the service answers,
using a connection retry/timeout constant. That "hardcoded threshold" is the
correct, intended design for an infrastructure wait-for-service script; it has no
match-confidence, date-parsing, or search-relevance semantics that would map to a
domain-specific AI-ification. Per the established mapping, a paperless
`hardcoded_threshold` → `anomaly_detection` opp with no strong domain semantics
maps to the generic internal analog: the MONITOR
`tools/monitor/log_analyzer.py` anomaly-detection layer.

This is the same determination reached for sibling opp 6101 (and matches the
14 duplicate siblings the scanner re-emits for this same file on every re-clone:
1728, 1729, 3360, 3361, 4213, 4214, 4635, 4636, 4793, 4794, 5024, 5025, 5986).

The analog already exists and is the faithful AI-ification of the
`hardcoded_threshold` → `anomaly_detection` pattern: inline z-score / error-rate
constants were lifted into the config-driven `anomaly_detection` block in
`args/monitoring_config.yaml`, with both a standard z-score method and a robust
MAD (modified z-score) method.

## Verification (branch kanban/aiify-rm-06d89-phase-5985)

- External clone `aiify_git_zwu66zfu/.../wait-for-redis.py`: **GONE** (reaped by engine).
- `dfb671f09` is an **ancestor of HEAD**.
- `_load_anomaly_cfg` + z-score/MAD present in both mirrors:
  - `tools/monitor/log_analyzer.py`
  - `icdev/tools/monitor/log_analyzer.py`
- `anomaly_detection` block present in `args/monitoring_config.yaml` (L91).
- `tests/test_log_analyzer_anomaly.py`: **23/23 pass**.

No new code required — closing as a duplicate with `bypass_verification`.
