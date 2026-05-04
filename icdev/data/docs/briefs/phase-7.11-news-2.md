# CUI // SP-CTI
# FathomDesk Phase 7.11 — News 2.0

**Project:** `args/projects.yaml → fathomdesk-7-11`. Prefix `ad711-`.
14 tasks / 4 epics. **Blocked on 7.9** — reuses chart layer for
news-on-price overlays.

## Why

`/news` today is a filter-chip firehose — items scroll past with no
per-category structure, no pattern rollup, and no autonomous action.
This phase redesigns it as category tabs with pattern analyzers, and
wires a Genesis reflex so the system can *react* to news patterns
(alerts, scenario auto-spawn, Pulse digest) without human-in-the-loop
for low-risk signals.

## Epics + tasks

### CAT (3 tasks + gate) — categorized view + chart overlay

- **ad711-cat-01** — Refactor `templates/news.html` from chip-filtered
  flat list → tab layout. Tabs: All, Macro, Geopolitical, Earnings,
  Regulatory, Sector, Corporate. Each tab is a pane with its own
  pattern summary card at the top followed by category-filtered items.
- **ad711-cat-02** — Per-tab summary card: 7d sentiment sparkline (net
  bullish vs bearish count per day), total item count, active pattern
  count (filled once 7.11-pattern lands).
- **ad711-cat-03** — "Show on chart" link on each news item → builds
  `/analysis?ticker=<first_mentioned>&highlight=<news_id>`. When
  `/analysis` receives a highlight param, it draws a vertical line on
  the chart at the news publish time with the headline as tooltip.
- **ad711-cat-gate**.

### PATTERN (4 tasks + gate)

- **ad711-pattern-01** — Migration 023 — `ad_news_patterns`
  (append-only): `id, pattern_type, category, severity (info/warn/
  critical), evidence_item_ids JSON, window_start, window_end,
  recommendation, created_at`. Add to `APPEND_ONLY_TABLES` + conftest
  schema.
- **ad711-pattern-02** — `tools/trading/news/pattern_db.py`: CRUD
  (`insert_pattern`, `list_patterns(category, since)`, `get_pattern`).
- **ad711-pattern-03** — `tools/trading/news/pattern_analyzer.py`: per-
  category detectors:
    - **macro**: hawkish/dovish skew ≥ 70% over 24h → regime_shift pattern
    - **earnings**: ≥ 5 items with bullish direction in 48h → broad_tailwind
    - **geopolitical**: spike (> 2× 7d baseline) + bearish skew → risk_off
    - **regulatory**: cluster ≥ 3 items same sector + bearish → crackdown
    - **sector**: rolling sentiment per sector → rotation flags
  Each detector returns a pattern dict; orchestrator `analyze_all()`
  runs them all.
- **ad711-pattern-04** — `/api/news/patterns?category=X` endpoint + UI
  rendering of active patterns inside each tab's summary card
  (severity-colored chip + recommendation text). Pytest for detectors.
- **ad711-pattern-gate**.

### GENESIS (3 tasks + gate)

- **ad711-genesis-01** — `tools/genesis/reflexes/fathomdesk_news_patterns.py`
  — conforms to Genesis contract (`run(config, trust) → {success,
  metric_value, details}`). Hourly cadence. Calls
  `pattern_analyzer.analyze_all()` + persists; skips duplicates per
  (pattern_type, category, window hash) within cooldown.
- **ad711-genesis-02** — autonomous action wiring. For each new pattern:
    - Write to `ad_alerts` at matching severity (existing alerts engine)
    - Feed evidence into Oracle `regime_lens`
    - On severity='critical': auto-spawn a matching scenario via
      `scenario_engine.run_scenario`; auto-publish a Pulse post via
      existing Pulse engine
    - NEVER places an order, NEVER modifies a position
- **ad711-genesis-03** — register in Genesis config
  (`tools/genesis/daemon.py` reflex list + matching config yaml).
- **ad711-genesis-gate**.

### WRAP (3 tasks)

- **ad711-wrap-01** — manifest + coherence + companion.
- **ad711-wrap-02** — feature doc + Selenium E2E (tab-switch renders
  pane; pattern card shows; "Show on chart" navigates + highlights).
- **ad711-wrap-03** — backlog + memory.

## Safety boundaries

- Genesis reflex is **signal-only** — every autonomous action is
  observable in its own audit table (`ad_alerts` rows,
  `scenario_runs` rows, Pulse posts). No order placement path.
- Pattern severity thresholds are operator-tunable in
  `args/news_pattern_thresholds.yaml`.
- Scenario auto-spawn is disabled by default when
  `ICDEV_GENESIS_AUTOSPAWN=false` — safe rollout flag.
