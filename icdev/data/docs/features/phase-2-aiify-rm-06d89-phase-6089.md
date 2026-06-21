# Phase 2 — AI-ify Opportunity 6089 (hardcoded_threshold → anomaly_detection)

**Disposition: Duplicate of opportunity 6090 — closed without new code.**

| Field | Value |
|-------|-------|
| Kanban ID | `aiify-rm-06d89-phase-6089` |
| Roadmap | `rm-06d89040cf` |
| Scan ID | 43 |
| Opportunity ID | 6089 |
| Pattern | `hardcoded_threshold` → `anomaly_detection` |
| External module | `…/aiify_git_zwu66zfu/src/documents/views.py` (paperless-ngx clone) |
| Model rec. | `claude-haiku-4-5-20251001` |

## Why this is a duplicate

The `module_path` points at a temporary shallow-clone of the external
paperless-ngx repository (`aiify_git_*`), which the AI-ify engine clones, scans,
and deletes. The file is external and unmodifiable, so the AI-ification lands in
the **analogous ICDEV internal subsystem** — the Document Intelligence Canvas
(DIC), specifically the structural-anomaly severity grading in
`tools/document_intelligence/analytics_engine.py::detect_anomalies`.

The legacy grader used inline magic-number thresholds
(`contradictions > 5`, `stale_docs > 2`, `orphans > 20`, `single_source > 10`)
that ignore corpus size — the canonical `hardcoded_threshold` smell. The
canonical AI-ification of this exact pattern + paradigm was already authored as
sibling **opportunity 6090** in the same scan: a deterministic
`_heuristic_severity` baseline (always available) refined by an optional
`_ai_anomaly_severity` LLM pass routed through the new `dic_anomaly_severity`
function, grounded on the real counts and degrading silently to the heuristic on
any failure.

Opportunities 6089 and 6090 are the same `(pattern_type, ai_paradigm)` pair on
paperless `src/documents/*.py` — the scanner routinely emits such duplicate opps
for the same pattern on sibling files; filename is irrelevant, pattern +
paradigm decide the analog. Authoring a competing copy of `detect_anomalies`
severity grading would collide with the 6090 work.

## Verification (main checkout, branch `irad/feature`)

The 6090 implementation is present and complete in the working tree:

- `tools/document_intelligence/analytics_engine.py`
  - `_heuristic_severity(summary)` — pure deterministic baseline (L174).
  - `_ai_anomaly_severity(summary, samples)` — LLM refinement, returns `None`
    on no-data / blank / malformed / out-of-range / error (L190).
  - `detect_anomalies()` wires both: AI grade when available, else heuristic;
    returns `severity`, `severity_source`, `severity_rationale`,
    `severity_top_concern`, `heuristic_severity` (L260).
- `args/llm_config.yaml` — `dic_anomaly_severity` routing
  `[claude-haiku, qwen3-local, gpt-4o-mini, llama-local]`, effort low (L447).
- `tools/dashboard/templates/document_intelligence/analytics.html` — severity
  source/rationale surfaced in the analytics UI.
- `tests/test_dic_anomaly_severity.py` — pins the heuristic baseline, grounding,
  and silent-fallback paths.

No competing copy authored. Card moved to done with `bypass_verification: true`.
