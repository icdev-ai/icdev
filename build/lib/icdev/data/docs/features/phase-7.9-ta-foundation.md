# CUI // SP-CTI
# FathomDesk Phase 7.9 — TA Foundation (Swing Pivots, Volume Profile, S/R, Patterns)

**Shipped:** 2026-04-19. **Project:** `args/projects.yaml → fathomdesk-ta`.
Prefix `ad79-` (swing detection, S/R clustering, pattern detectors, chart overlay).

## Why

FathomDesk had OHLCV bars and options data, but no raw technical analysis
primitives. Phase 7.9 lays the deterministic foundation that every higher-level
TA signal will build on: swing-pivot detection, volume profile bucketing, S/R
strength scoring, and pattern recognition — all rendered as a layered SVG chart.

---

## (a) Swing-Pivot Method Rationale

**File:** `tools/trading/ta/swings.py`  
**Function:** `find_swings(bars, threshold_pct=1.5) -> list[dict]`

Swing pivots are the structural "zigzag" of price: the sequence of meaningful
highs and lows that define market structure. The algorithm uses a
**two-phase percentage-retracement approach**, not a fixed N-bar lookback.

### Why percentage retracement instead of N-bar window?

A fixed lookback (e.g., "highest high in 5 bars") is window-dependent: too
narrow misses major structure; too wide misses short-term S/R. A percentage
threshold instead says "this reversal is only meaningful if price reversed
at least X% from the extreme." That scales naturally across both trending
and ranging markets, and across instruments with different volatilities.

### Algorithm

**Phase 1 — Direction seeding:**  
Scan from bar 0 tracking a running maximum (`run_high`) and running minimum
(`run_low`). The first swing is detected when price retraces ≥ `threshold_pct`
from the running extreme, seeding the initial direction (up or down).

```python
if run_high > 0 and (run_high - close) / run_high >= threshold_pct:
    # Initial direction was up; first swing is a high
    first_swing = {"type": "high", "price": run_high, "bar_index": ...}
```

**Phase 2 — Alternating recording:**  
From the seed swing onward, the algorithm strictly alternates between
recording highs and lows. A new high swing is confirmed when price retraces
≥ `threshold_pct` below the running-high candidate; a new low swing when
price rallies ≥ `threshold_pct` above the running-low candidate.

**Invariant guaranteed:** swings always alternate `high → low → high …`  
No two consecutive swings of the same type can exist.

### Output

```python
[
  {"type": "high" | "low", "price": float, "bar_index": int},
  ...
]
```

### Key parameter

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `threshold_pct` | `1.5` | Minimum % retracement to confirm a new swing |

Lower values surface minor structure; higher values show only major pivots.
The default 1.5% is suitable for daily charts on mid/large-cap equities.

---

## (b) Volume Profile — Bucket Semantics, POC, and Value Area

**File:** `tools/trading/ta/volume_profile.py`  
**Function:** `volume_profile(bars, bucket_count=40) -> dict`

### Bucket construction

The price range across the entire window is divided into `bucket_count`
(default 40) equal-width buckets:

```
bucket_width = (max(bar.high) − min(bar.low)) / bucket_count
bucket[i].price_low  = min(bar.low) + i * bucket_width
bucket[i].price_high = bucket[i].price_low + bucket_width
```

Each bar's volume is distributed **uniformly** across the buckets it spans
by price (proportional to the fraction of `[bar.low, bar.high]` in each bucket).
This avoids falsely concentrating all volume at a single price level.

40 buckets balances resolution against noise: 20 would merge important
price clusters; 100 would overfit to individual bars.

### Point of Control (POC)

```
POC = price midpoint of the bucket with the highest cumulative volume
```

The POC is the price where the most trading activity occurred in the window.
It acts as a price magnet — the market tends to rotate around it.

### Value Area (VA)

```
VA = the contiguous range of buckets around POC whose total volume ≥ 70%
     of total window volume
```

Starting from the POC bucket, the algorithm expands outward (adding the
higher-volume neighboring bucket on each step) until the running sum
crosses 70%. The result is `value_area.low` and `value_area.high`.

The 70% target matches the standard market profile convention (analogous
to one standard deviation in a normal distribution). Due to discrete bucket
boundaries, the actual captured volume lands in the 65–75% range.

### High/Low Volume Nodes (HVN / LVN)

After computing the full profile, buckets are ranked by volume:

- **HVN** — top 20% by volume. Price accepts time here; expect S/R,
  consolidation, and slowed trends.
- **LVN** — bottom 20% by volume. Price moves quickly through here;
  expect fast breakouts and limited acceptance.

### Return shape

```python
{
  "buckets": [{"price_low": float, "price_high": float, "volume": float}, ...],
  "poc":         float,           # price of highest-volume bucket
  "value_area":  {"low": float, "high": float},
  "hvns":        [{"price": float, "volume": float}, ...],
  "lvns":        [{"price": float, "volume": float}, ...]
}
```

---

## (c) S/R Strength Scoring

**File:** `tools/trading/ta/support_resistance.py`  
**Function:** `compute_sr(bars, swings=None, cluster_pct=0.5) -> list[dict]`

### Clustering

Swing pivots within `cluster_pct` (default 0.5%) of each other in price
are merged into a single S/R level. Cluster price is the running mean of
all merged swings; the touch count increments on each merge.

```python
for swing in swings:
    for cluster in clusters:
        if abs(swing.price − cluster.price) / cluster.price <= cluster_pct:
            cluster.price = (cluster.price * n + swing.price) / (n + 1)
            cluster.touches += 1
            break
    else:
        clusters.append({"price": swing.price, "touches": 1, ...})
```

A cluster formed by high swings is labelled `resistance`; by low swings,
`support`.

### Strength normalization

```python
max_touches = max(c.touches for c in clusters)
cluster.strength = cluster.touches / max_touches   # → [0.0, 1.0]
```

This relative score avoids hard-coding "5 touches = strong" — it adapts
to the data window. A level touched 4/4 times scores 1.0; one touched 1/4
times scores 0.25.

### Visual encoding (fathomdesk.html lines 391–417)

| Property | Formula | Range |
|----------|---------|-------|
| Opacity | `0.15 + strength × 0.55` | 0.15 → 0.70 |
| Stroke width | `0.8 + strength × 1.8` | 0.8 → 2.6 px |
| Line style | Resistance = dashed · Support = solid | — |
| Color | Resistance = red · Support = green | — |

### Return shape

```python
[
  {"price": float, "strength": float, "touches": int,
   "type": "support" | "resistance"},
  ...   # sorted by strength descending
]
```

---

## (d) Pattern Detectors — Geometric Criteria

**Orchestrator:** `tools/trading/ta/patterns/__init__.py`  
**Function:** `detect_patterns(bars) -> list[dict]`

The orchestrator runs all detectors, deduplicates by type + bar-range
overlap (keeping the widest-span instance), and returns patterns sorted
by `start_bar`.

---

### Double Top / Double Bottom

**File:** `tools/trading/ta/patterns/double.py`

**Geometry:** Three consecutive alternating swings `[s1, s_mid, s2]`
where `s1` and `s2` are the same kind (both highs for double top; both lows
for double bottom) and `s_mid` is the opposite kind (the neckline).

**Tolerance test:**

```python
avg = (s1.price + s2.price) / 2
|s1.price − avg| / avg ≤ tolerance_pct   # default 3.0%
|s2.price − avg| / avg ≤ tolerance_pct
```

Both peaks (or troughs) must land within 3% of their mean. This allows
for natural market noise without classifying every three-swing sequence
as a double top.

**Key fields returned:**

| Field | Meaning |
|-------|---------|
| `type` | `"double_top"` or `"double_bottom"` |
| `high_1` / `low_1` | First swing dict |
| `high_2` / `low_2` | Second swing dict |
| `neckline` | Opposite swing (the middle pivot) |
| `avg_price` | Mean of the two matching swings |
| `start_bar`, `end_bar` | Bar index span |

---

### Triple Top / Triple Bottom

**File:** `tools/trading/ta/patterns/triple.py`

**Geometry:** Three consecutive same-kind swings `[s1, s2, s3]` (all highs
or all lows) all within `tolerance_pct` of their group mean.

```python
avg = (s1.price + s2.price + s3.price) / 3
for p in [s1.price, s2.price, s3.price]:
    |p − avg| / avg * 100 ≤ tolerance_pct   # default 3.0%
```

Three peaks at similar prices indicate strong supply / demand zones.
The pattern is rarer than the double but carries higher conviction because
price attempted the level three times and failed.

**Key fields returned:**

| Field | Meaning |
|-------|---------|
| `type` | `"triple_top"` or `"triple_bottom"` |
| `swing_1/2/3` | The three swing dicts |
| `avg_price` | Group mean price |
| `start_bar`, `end_bar` | Bar index span |

---

### Rising Wedge / Falling Wedge

**File:** `tools/trading/ta/patterns/wedge.py`

**Geometry:** Fit independent OLS (ordinary least squares) regression lines
through all swing-highs (resistance trendline) and all swing-lows (support
trendline). Minimum 2 swings of each kind are required.

**OLS implementation:**

```python
def _ols(xs, ys):
    n = len(xs)
    x_mean, y_mean = mean(xs), mean(ys)
    slope = Σ((x − x_mean)(y − y_mean)) / Σ((x − x_mean)²)
    intercept = y_mean − slope * x_mean
    return slope, intercept
```

**Classification rules:**

| Pattern | Resistance slope | Support slope | Relationship |
|---------|-----------------|---------------|--------------|
| Rising wedge | > 0 | > 0 | `slope_low > slope_high` (support rising faster → converges up) |
| Falling wedge | < 0 | < 0 | `slope_high > slope_low` (resistance falling slower → converges down) |

A rising wedge is bearish: price compresses into tighter highs while
support rises steeply, signalling eroding buying pressure. A falling wedge
is bullish: support holds better than resistance, typically resolving upward.

**Key fields returned:**

| Field | Meaning |
|-------|---------|
| `type` | `"rising_wedge"` or `"falling_wedge"` |
| `slope_high`, `intercept_high` | OLS fit through swing-highs |
| `slope_low`, `intercept_low` | OLS fit through swing-lows |
| `start_bar`, `end_bar` | Earliest to latest swing bar index |

---

## (e) Chart Layout

**Template:** `tools/dashboard/templates/fathomdesk.html`  
**Render function:** `drawChart(bars, vp, patterns, srLevels, ticker, tf)`

### Panel dimensions

```
Total width W  = container width (responsive)
Height H       = 420 px (fixed)
Padding        = L:56 R:12 T:16 B:40
VP fraction    = 15% of plot width
Candle area    = 85% of plot width
```

The VP histogram sits to the right of the candlestick panel, sharing the
same Y axis (price scale) so VP bars and candles align precisely.

### Layer rendering order (back → front)

| # | Layer | Rendering |
|---|-------|-----------|
| 1 | Grid | Dashed horizontal Y-axis lines (6 price ticks) |
| 2 | Value Area | Semi-transparent blue rectangle (opacity 0.07) spanning candle + VP panels |
| 3 | S/R Lines | Horizontal lines; opacity + thickness ∝ strength; resistance = dashed red, support = solid green |
| 4 | HVN Lines | Thin dashed orange horizontals at each HVN price |
| 5 | POC Line | Thick yellow dashed line labelled "POC" |
| 6 | Wedge lines | Two trendlines per wedge (resistance solid, support dashed), colored by type |
| 7 | Candlesticks | OHLC bars + wicks; bull = green, bear = red |
| 8 | Pattern markers | Colored ▼/▲ badge at breakout bar; click opens detail modal |
| 9 | VP histogram | Horizontal bars in VP panel; POC = yellow 0.9, HVN = orange 0.65, other = blue 0.45 |

### Data flow

```
Browser: GET /api/trading/chart/{ticker}?tf={tf}&limit=120
    │
    └─► app.py
          bars         = fetch_bars(ticker, tf, limit)
          vp           = volume_profile(bars, bucket_count=40)
          swings       = find_swings(bars)
          patterns     = detect_patterns(bars)
          sr_levels    = compute_sr(bars, swings=swings)
          patterns     = _enrich_chart_patterns(patterns)
          return {bars, volume_profile, patterns, sr_levels}
    │
    └─► drawChart(bars, vp, patterns, srLevels, ...)
          → SVG rendered in-browser (no canvas, no external charting lib)
```

### Interactive elements

- **S/R lines** — hover shows tooltip: price, touch count, strength %
- **VP buckets** — hover shows tooltip: price range, volume, POC/HVN flag
- **Pattern badges** — click opens modal: pattern type, confidence bar,
  bar/date span, pattern-specific geometry fields (neckline, slopes, avg price)
- **Candlesticks** — hover shows OHLCV tooltip

### Screenshots

| File | Contents |
|------|---------|
| `playwright/screenshots/ta_sr_lines.png` | S/R line overlay with strength-coded opacity |
| `playwright/screenshots/ta_vp_histogram.png` | VP histogram with POC/HVN/VA highlighted |
| `playwright/screenshots/ta_patterns_double.png` | Double-top badge + modal |
| `playwright/screenshots/ta_patterns_wedge.png` | Rising/falling wedge trendlines |
| `playwright/screenshots/ta_full_chart.png` | All layers combined |

---

## Configuration

All thresholds live in `args/ta_config.yaml`:

```yaml
swing_threshold_pct: 1.5   # % retracement to confirm a new pivot
vp_bucket_count: 40        # price buckets in the volume profile
sr_proximity_pct: 0.5      # % tolerance to merge swings into one S/R cluster
pattern_tolerance_pct: 3.0 # % tolerance for double/triple top/bottom matching
```

---

## Tests

- `tests/test_ta_primitives.py` — unit tests for `find_swings`, `volume_profile`,
  `compute_sr`: zigzag alternation invariant, V-shape/W-shape swing counts,
  VA covers 65–75% of volume, POC is max-volume bucket, S/R strength in [0,1].
- `tests/test_ta_patterns.py` — double/triple top/bottom detection on
  synthetic swing arrays; rising/falling wedge OLS slope conditions;
  deduplication of overlapping patterns.

---

## Out of scope (deferred)

- **Fibonacci retracements** — requires two user-selected pivots; UI not yet designed.
- **VWAP / anchored VWAP** — needs intraday tick data; Alpaca daily bars only.
- **Elliott Wave counting** — probabilistic labelling; backlog after VP ships.
- **Pattern confidence scoring** — current confidence is heuristic (`avg_price`
  proximity); a regression-trained model is a Phase 8 candidate.
