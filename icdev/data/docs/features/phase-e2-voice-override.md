# CUI // SP-CTI
# Phase E2 — Per-Page Voice Override

**Shipped:** 2026-05-07

## What It Does

Lets users assign a different Reading voice (persona) to each dashboard page, overriding the global voice setting. A "Quant" voice on Signals, a "Rookie" voice on Portfolio, etc. — each page speaks in the style the user finds most useful for that context.

## How to Use

1. Navigate to `/settings` → Profile section → **Voice overrides per page** (collapsible panel)
2. A table lists 6 overridable pages: Signals, Portfolio, Rebalance, Options, News, Alerts
3. For each page, pick a voice from the dropdown or leave it at **Inherit global**
4. Changes save automatically; next data refresh on that page uses the selected voice

## Key Files

| File | Change |
|------|--------|
| `tools/trading/analytics/reading_voice.py` | `get_voice_for_page(page_key, profile)` — looks up page override, falls back to global voice |
| `tools/trading/dashboard/templates/settings.html` | Per-page voice override table with 6 page dropdowns |
| `tools/trading/dashboard/app.py` | `PATCH /api/profile` merge-patches `voice_overrides` JSON dict |

## Implementation Notes

- 7 voice presets: PM-style, Rookie, Standard, Technical, Quant, Long-Horizon, Advisor (from `args/persona_presets.yaml`)
- Stored as `ad_profiles.voice_overrides` JSON: `{"signals": "quant", "portfolio": "rookie"}`
- PATCH uses merge-patch semantics — sending `{"signals": "quant"}` merges into existing overrides rather than replacing the full dict
- `apply_page_voice()` deep-copies observation data before filtering/rephrasing to avoid mutating the source
