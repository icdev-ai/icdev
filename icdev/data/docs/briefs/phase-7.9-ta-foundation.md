# CUI // SP-CTI
# FathomDesk Phase 7.9 — TA Foundation

**Project:** `args/projects.yaml → fathomdesk-7-9`. Prefix `ad79-`.
19 tasks / 4 epics. No external deps (ships first).

## Why

7.6–7.8 gave us AI Assist, POP, and Greeks. The next natural unlock — traps
(7.10) and news-on-chart (7.11) — both need an **algorithmic chart layer**
that doesn't exist yet: volume profile, S/R clusters, swing pivots, and
reversal patterns. This phase builds that layer as standalone deterministic
math, useful on /analysis immediately, and reusable downstream.

## Epics + tasks

### PRIMITIVES (4 tasks + gate)

- **ad79-primitives-01** — `tools/trading/ta/swings.py`: swing-pivot detector
  (fractal or percentage-threshold method). Public: `find_swings(bars,
  threshold_pct=1.5) → [{index, time, price, kind}]` where kind ∈ {'high',
  'low'}. Used by S/R + all pattern detectors.
- **ad79-primitives-02** — `tools/trading/ta/volume_profile.py`: given bars,
  compute `{buckets:[{price_low, price_high, volume}], poc, value_area,
  hvns:[price,...], lvns:[price,...]}`. Bucket size tunable via
  `args/ta_config.yaml::vp_bucket_count` (default 40). Value area = the
  contiguous buckets containing 70% of total volume.
- **ad79-primitives-03** — `tools/trading/ta/sr.py`: cluster swing pivots +
  VP HVNs into significant levels. Clustering by price proximity (tolerance
  pct), weighted by touch count + HVN overlap. Returns
  `[{price, strength, touch_count, source: 'swing'|'vp'|'both'}]`.
- **ad79-primitives-04** — pytests: swings monotone (every high > prior
  low), VP volume sum equals input volume within rounding, S/R prices are
  within a tolerance of true touches.
- **ad79-primitives-gate**.

### PATTERNS (4 tasks + gate)

- **ad79-patterns-01** — `tools/trading/ta/patterns/double.py`: detect
  double top + double bottom. Input: swings + bars. Rule: two peaks within
  X% of each other, with a valley between at least Y% below, neckline =
  valley low; confirmed when price breaks neckline. Return list of
  `{pattern: 'double_top'|'double_bottom', peaks, neckline, confirmed,
  breakout_bar}`.
- **ad79-patterns-02** — `tools/trading/ta/patterns/triple.py`: triple top
  + triple bottom. Similar shape; three peaks within tolerance.
- **ad79-patterns-03** — `tools/trading/ta/patterns/wedge.py`: rising wedge
  + falling wedge detection via linear regression over last-N swings.
  Rising wedge = both trend lines positive-slope with upper slope <
  lower slope (converging, typically bearish reversal). Falling wedge =
  mirror.
- **ad79-patterns-04** — `tools/trading/ta/patterns/__init__.py`:
  `detect_patterns(bars) → [patterns]` orchestrator that runs all
  detectors + dedupes overlapping results. Full pytest suite.
- **ad79-patterns-gate**.

### UI (4 tasks + gate)

- **ad79-ui-01** — `GET /api/ta/chart/<ticker>?timeframe=1D&limit=120` —
  returns `{bars, volume_profile, sr_levels, patterns, swings}`. Reuses
  `fetch_bars` from `tools/trading/data/market_data.py`.
- **ad79-ui-02** — Candlestick chart on `/analysis` page. If a Chart.js
  candlestick plugin is available, use it; else render bars via custom
  canvas (two bars per candle: a thin line for range, a rectangle for
  body). 1D timeframe, default 120 bars.
- **ad79-ui-03** — Volume profile overlay — horizontal histogram on the
  right side of the chart; VP buckets shaded by volume intensity; POC
  highlighted; HVN lines extended across chart as faint horizontals.
- **ad79-ui-04** — S/R overlay (blue lines at cluster prices, thickness ∝
  strength) + pattern annotations (label pinned to breakout bar with
  pattern name + arrow).
- **ad79-ui-gate**.

### WRAP (4 tasks)

- **ad79-wrap-01** — manifest + coherence + companion.
- **ad79-wrap-02** — feature doc.
- **ad79-wrap-03** — Selenium E2E (chart renders, VP on right, S/R lines,
  at least one pattern detected for a ticker with a known historical
  setup).
- **ad79-wrap-04** — backlog + memory.

## Out of scope (downstream phases handle)

- Trap detection (that's 7.10; it uses these primitives).
- News-on-chart overlay (7.11 plumbs news_id → chart vertical).
- Intraday timeframes (1m/5m/15m) — defer until a need arises.
- Multi-ticker charting, drawing tools, custom indicators.
