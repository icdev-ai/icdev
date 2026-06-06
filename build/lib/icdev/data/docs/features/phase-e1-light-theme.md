# CUI // SP-CTI
# Phase E1 — Light Theme

**Shipped:** 2026-05-07

## What It Does

Adds a user-selectable light color theme across the entire ICDEV™ dashboard. Users can switch between the default dark interface and a professional light palette optimized for daytime use.

## How to Use

1. Navigate to `/settings` → Profile section
2. In the **Theme** dropdown, select **Light**
3. The page reloads with the light palette applied
4. Preference persists across sessions (stored on the user profile)
5. The `☀/🌙` toggle in the header provides quick switching without going to Settings

## Key Files

| File | Change |
|------|--------|
| `tools/dashboard/static/css/style.css` | `html[data-theme="light"]` block overriding 25 CSS variables (backgrounds, text, borders, shadows) |
| `tools/dashboard/templates/base.html` | `data-theme="{{ theme_pref or 'dark' }}"` on `<html>`; toggle JS function |
| `tools/trading/dashboard/app.py` | `POST /api/profile/theme` saves preference; `GET /api/profile` returns it |

## Implementation Notes

- All colors are driven by CSS custom properties (`--bg-primary`, `--text-primary`, etc.) — no hardcoded hex values in templates
- Theme is injected server-side to prevent flash-of-unstyled-content on load
- Light palette: `#f4f5f7` page background, `#ffffff` card surfaces, muted `#1a5fa8` accent blue
- Stored as `ad_profiles.theme = 'light' | 'dark'`
