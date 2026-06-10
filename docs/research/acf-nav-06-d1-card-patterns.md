# ACE / Foundry Card Pattern Audit — `acf-nav-06-d1`

> Research artifact: exact HTML blocks, class names, metric placeholders, and linking logic from `tools/dashboard/templates/index.html` so that any new Foundry (or ACE) dashboard content can mirror the existing design without manual recreation.

---

## 1. Monitor Tile Card (Primary pattern for summary / metric cards)

**Location in `index.html`:** lines 948–996 (Monitor Cards Row)

### Parent grid container
```html
<div class="chart-grid" style="display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 20px; margin-bottom: 24px;">
```

### Individual card block
```html
<div class="card" style="padding: 20px;">
    <div class="card-label">
        Card Title
        <span class="help-icon" title="Tooltip description">?</span>
    </div>
    <div id="tile-{canvas}" style="min-height: 140px; font-size: 13px; color: var(--text-dim);">
        Loading…
    </div>
</div>
```

### Key class names & elements
| Element | Class / Attribute | Purpose |
|---------|-------------------|---------|
| Grid wrapper | `.chart-grid` | `display: grid;` auto-fit columns, `minmax(280px, 1fr)` |
| Card container | `.card` | Standard dashboard card shell (border, radius, bg from `base.html` CSS) |
| Card title | `.card-label` | Label text; may include `.help-icon` for tooltip |
| Content area | `id="tile-{canvas}"` | Dynamic content injected by JS tile renderer |
| Loading state | Inline text "Loading…" | Placeholder before fetch completes |

### JS tile renderer pattern (consistent across all monitor tiles)
```javascript
(function () {
    function renderXxxTile() {
        fetch('/api/{canvas}/summary')
            .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
            .then(function (d) {
                var el = document.getElementById('tile-{canvas}');
                if (!el) return;
                // --- stat row (big numbers) ---
                var stat = function (label, val) {
                    return '<div style="flex:1; min-width:60px;">'
                        + '<div style="font-size:18px; font-weight:600; color:var(--text-primary);">' + (val == null ? '—' : val) + '</div>'
                        + '<div style="font-size:11px; color:var(--text-dim);">' + label + '</div>'
                        + '</div>';
                };
                el.innerHTML =
                    '<div style="display:flex; gap:8px; margin-bottom:12px;">'
                    + stat('Metric A', d.metric_a)
                    + stat('Metric B', d.metric_b)
                    + '</div>'
                    // optional mini-bar, list rows, etc.
                    + '<div style="margin-top:8px;"><a href="/{canvas}" class="btn" style="font-size:11px; padding:3px 10px;">View all</a></div>';
            })
            .catch(function () {
                var el = document.getElementById('tile-{canvas}');
                if (el) el.innerHTML = '<div style="color:var(--text-dim);">Summary unavailable</div>';
            });
    }
    document.addEventListener('DOMContentLoaded', renderXxxTile);
    window.renderXxxTile = renderXxxTile;   // exposed for auto-refresh loop
})();
```

### Linking logic
- Drill-through link: `<a href="/{canvas}" class="btn" style="font-size:11px; padding:3px 10px;">View all</a>`
- The host page’s `refreshDashboard()` calls `window.renderXxxTile()` for each tile.

---

## 2. Stat Bar (Compact inline stats — top-of-page pattern)

**Location in `index.html`:** lines 21–41

```html
<div class="stat-bar">
    <div class="stat-bar-item">
        <div class="stat-bar-value blue">{{ total_projects }}</div>
        <div class="stat-bar-label">Total Projects</div>
    </div>
    <a href="/poam?severity=CAT1" class="stat-bar-item" style="text-decoration:none; color:inherit; cursor:pointer;" title="...">
        <div class="stat-bar-value {% if firing_alerts > 0 %}red{% else %}green{% endif %}">{{ firing_alerts }}</div>
        <div class="stat-bar-label">CAT1 Findings</div>
    </a>
</div>
```

### Key class names
| Element | Class | Notes |
|---------|-------|-------|
| Row | `.stat-bar` | Flex row of compact stats |
| Item | `.stat-bar-item` | Can be a plain `<div>` or an `<a>` for linking |
| Value | `.stat-bar-value` | Color variants: `.blue`, `.green`, `.red`, `.yellow` |
| Label | `.stat-bar-label` | Text below the value |

---

## 3. Full-width Section Card (e.g. Compliance Posture)

**Location in `index.html`:** lines 936–946

```html
<div class="card" style="padding: 20px; margin-bottom: 24px; overflow: visible;">
    <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:10px;">
        <div class="card-label" style="margin:0;">
            Compliance Posture
            <span class="help-icon" title="...">?</span>
        </div>
        <a href="/security/posture" style="font-size:11px; color:var(--text-dim); text-decoration:none;">View all →</a>
    </div>
    <div id="chart-compliance" style="overflow: visible;"></div>
</div>
```

---

## 4. What is NOT in `index.html` (ACF/Foundry custom styles)

The ACF Foundry pipeline board (`foundry/index.html`) uses its own custom class namespace (`.acf-hero`, `.acf-rcard`, `.acf-board`, `.acf-card`, `.scorebadge`, `.rbadge`).
These are **not** reused on the dashboard home page. If a Foundry summary tile is to be added to the **home page**, it should follow the **Monitor Tile Card** pattern (Pattern 1 above), not the ACF custom styles.

---

## 5. Recommendations for new Foundry home-page content

1. **Wrap the tile** in the standard `.chart-grid` → `.card` structure.
2. **Title** uses `.card-label`; add `.help-icon` if the metric needs explanation.
3. **Content** renders into a `div` with a unique `id` (e.g. `id="tile-foundry"`).
4. **Metrics** use the `stat()` helper pattern: big number (`font-size:18px; font-weight:600`) + small label (`font-size:11px; color:var(--text-dim)`).
5. **Linking** ends with a small `.btn` anchor pointing to `/foundry`.
6. **Register** the tile renderer in `index.html`:
   - Add `if (window.renderFoundryTile) window.renderFoundryTile();` inside `refreshDashboard()`.
   - Expose `window.renderFoundryTile = renderFoundryTile;` so auto-refresh can re-trigger it.
7. **Color coding** — reuse existing palette:
   - `#22c55e` green (healthy / approved)
   - `#f59e0b` amber (warning / scored)
   - `#ef4444` red (failed / rejected)
   - `#b388ff` purple (ACF brand — only if the tile is ACF-specific and needs to visually group with the ACF page).

---

## Source files examined

- `tools/dashboard/templates/index.html` (root) — lines 1–1520
- `icdev/tools/dashboard/templates/index.html` (mirror) — identical
- `tools/dashboard/templates/foundry/index.html` — ACF custom styles (reference only)
- `tools/dashboard/templates/coworker/index.html` — ACE page styles (reference only)
- `tools/dashboard/templates/_projects_in_flight.html` — live task / project cards (reference only)
- `tools/dashboard/templates/_autonomy_status.html` — autonomy recovery cards (reference only)
