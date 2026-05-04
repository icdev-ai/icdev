# CUI // SP-CTI
# FathomDesk Phase 7.11 — News Intelligence Dashboard

**Shipped:** 2026-04-19. **Project:** `args/projects.yaml → fathomdesk`.
Prefix `ad711-` (news ingestion, category tabs, sentiment reading, chart annotation).

## Why

FathomDesk needed a structured news layer that connects macro/geopolitical events
to the price chart. Phase 7.11 delivers a full-page News Intelligence dashboard
with RSS ingestion, category-tab filtering, sentiment aggregation, and a
one-click "Show on chart" flow that drops a vertical annotation on the FathomDesk
chart at the exact timestamp of the news event.

---

## Architecture

### Data Flow

```
RSS Feeds
  └─► tools/trading/news/rss_ingestor.py   (ingest + categorize)
        └─► ad_news_items (fathomdesk.db)
              └─► tools/dashboard/api/news.py  (REST endpoints)
                    └─► /news  (news.html — tabbed UI)
                          └─► /fathomdesk?ticker=…&highlight=<id>  (chart annotation)
```

### Database Tables (`data/fathomdesk.db`)

| Table | Purpose |
|-------|---------|
| `ad_news_items` | Raw ingested items: id, source, title, link, published_at, category, impact\_level, net\_direction, mentioned\_tickers (JSON) |
| `ad_news_scenario_links` | Item → scenario associations |
| `ad_news_clusters` | Multi-source clusters: scenario\_key, category, item\_ids, status (emerging/cluster/regime) |

Schema initialised by `tools/trading/news/db.py::ensure_schema()`.

### API Endpoints (`tools/dashboard/api/news.py`)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/news` | List items; `?category=`, `?limit=` (max 500) |
| GET | `/api/news/category-summary/<cat>` | 7-day sparkline + 24h count |
| GET | `/api/news/reading` | Aggregate sentiment (mood, counts, summary) |
| GET | `/api/news/clusters` | Active signal clusters (up to 50) |
| GET | `/api/news/divergences` | Cross-signal divergences |
| GET | `/api/news/export.csv` | CSV download; `?category=` |
| GET | `/api/news/<id>` | Single item detail (used by chart annotation) |
| POST | `/api/news/<id>/analyze` | INTaaS deep analysis (stub → 501) |

---

## UI — `/news`

**Template:** `tools/dashboard/templates/news.html`  
**Route:** `tools/dashboard/app.py` → `@app.route("/news")`

### Tab Bar (7 tabs)

The category tab bar renders all seven categories in a single Jinja2 loop:

```
all | macro | geopolitical | earnings | regulatory | sector | corporate
```

Each tab badge is updated in real-time by `updateBadges()`, which calls
`/api/news/category-summary/<cat>` and writes `count_24h` onto the badge element.
Clicking a tab filters the news list via `filterAndRender(category)`.

### Summary Cards (top panels)

| Panel | Data source |
|-------|-------------|
| News Reading | `/api/news/reading` — mood + sentiment counts |
| Cross-Signal Divergences | `/api/news/divergences` |
| Regime Watch | `/api/news/clusters` (status=regime) |
| Emerging Clusters | `/api/news/clusters` (status=emerging) |

### Macro Tab Detail

Selecting the **Macro** tab calls `filterAndRender('macro')`, which:
1. Requests `/api/news?category=macro&limit=200`
2. Re-renders the card list (title, source, time-ago, impact badge, net\_direction badge, ticker chips, summary snippet)
3. Shows the Macro category summary card (sparkline + 24h count) via `/api/news/category-summary/macro`

### "Show on Chart" Flow

Each news card exposes a **📈 Show on chart** button. Clicking it navigates to:

```
/fathomdesk?ticker=<first_mentioned_ticker>&highlight=<news_item_id>
```

On the FathomDesk page (`tools/dashboard/templates/fathomdesk.html`):
1. URL params are read: `ticker` → symbol selector; `highlight` → news ID
2. After chart geometry is ready, `maybeAnnotateNews()` fetches `/api/news/<id>`
3. A **vertical dashed annotation** is drawn on the SVG canvas:
   - Yellow (#f5c518) dashed vertical line spanning the full plot area
   - 7 px circle marker at the top labelled **"N"**
   - Hover tooltip shows headline + formatted publication timestamp
   - X position is the bar closest to `published_at`

---

## Files Changed / Added

| Path | Change |
|------|--------|
| `tools/trading/news/__init__.py` | Module init |
| `tools/trading/news/db.py` | DDL + CRUD helpers for news tables |
| `tools/trading/news/rss_ingestor.py` | RSS ingestion + categorization |
| `tools/trading/news/scenario_matcher.py` | Scenario-link matching |
| `tools/dashboard/api/news.py` | 8 REST endpoints (Flask blueprint) |
| `tools/dashboard/templates/news.html` | Full news dashboard template |
| `tools/dashboard/app.py` | `@app.route("/news")` route |
| `tools/dashboard/templates/fathomdesk.html` | `maybeAnnotateNews()` + `?highlight=` param handling |

---

## Acceptance Criteria

- [ ] `/news` loads without error and displays the 7-tab bar
- [ ] Macro tab filters list to macro-category items + shows summary card
- [ ] "Show on chart" redirects to `/fathomdesk?ticker=…&highlight=<id>`
- [ ] FathomDesk draws vertical annotation at the correct timestamp
- [ ] CSV export downloads a valid file
- [ ] CUI // SP-CTI banner present top and bottom
# CUI // SP-CTI
