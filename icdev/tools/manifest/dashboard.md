# Dashboard

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Dashboard
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Web Dashboard | tools/dashboard/app.py | Flask web dashboard with role-based views, wizard, quick paths | --port, --debug | Web UI on port 5000 |
| Dashboard Config | tools/dashboard/config.py | Loads dashboard settings from args/monitoring_config.yaml and args/cui_markings.yaml with env-var overrides; exposes DB_PATH, PORT, DEBUG, CUI banners, BYOK, auth secret, classification | Environment variables (ICDEV_DB_PATH, ICDEV_DASHBOARD_PORT, etc.) | Module-level constants |
| Platform Health | tools/dashboard/platform_health.py | Aggregate health scoring across 10 ICDEV™ domains (database, agents, compliance, security, infrastructure, canvases, LLM, monitoring, CI/CD, marketplace); 60s in-process cache; bands: ≥90 healthy, ≥70 degraded, <70 critical | get_platform_health(), get_domain_health(domain), _invalidate_cache() | Composite + per-domain score/status/findings JSON |
| Canvas Aggregator | tools/dashboard/canvas_aggregator.py | Queries across all 7 canvas SQLite DBs (security, infra, observability, boundary, data, network, pipeline) to surface compliance summaries, finding counts, recent findings, and activity trends; 60s module-level cache | get_canvas_compliance_summary(), get_canvas_finding_counts(), get_recent_canvas_findings(limit), get_canvas_activity_trend(days), invalidate_cache(), close_canvas_connections() | Aggregated canvas data JSON |
| UX Helpers | tools/dashboard/ux_helpers.py | Jinja2 filters (friendly_time, glossary), error recovery dict, quick paths, wizard steps | register_ux_filters(app) | Template filters + globals |
| UX JavaScript | tools/dashboard/static/js/ux.js | Client-side glossary tooltips, timestamp formatting, accessibility, notifications, progress pipeline | Auto-init on DOMContentLoaded | ICDEV™ namespace |
| UX Stylesheet | tools/dashboard/static/css/ux.css | Tooltip, pipeline, wizard, quick path, breadcrumb, notification, accessibility styles | — | CSS |
| Charts Library | tools/dashboard/static/js/charts.js | Zero-dependency SVG chart library: sparkline, line, bar, donut, gauge with tooltips and animation | ICDEV™.lineChart(), ICDEV™.barChart(), ICDEV™.donutChart(), ICDEV™.gaugeChart() | SVG charts |
| Table Interactivity | tools/dashboard/static/js/tables.js | Table search, column sort, column filter, CSV export, row counter | Auto-init on DOMContentLoaded | Enhanced tables |
| Onboarding Tour | tools/dashboard/static/js/tour.js | Interactive overlay walkthrough for first-visit users, 6-step spotlight tour | ICDEV™.startTour(), ICDEV™.resetTour() | Tour overlay |
| Live Dashboard | tools/dashboard/static/js/live.js | Real-time SSE auto-refresh: connection status, smart debounced updates, event toasts | ICDEV™.connectSSE(), ICDEV™.disconnectSSE() | Live updates |
| Batch Operations JS | tools/dashboard/static/js/batch.js | Batch workflow UI: catalog display, execution progress, step status polling | ICDEV™.batchStartBatch(id, projectId) | Batch progress UI |
| Batch Operations API | tools/dashboard/api/batch.py | Flask blueprint: batch execute/status/catalog endpoints, background subprocess runner | POST /api/batch/execute, GET /api/batch/status | JSON batch status |
| Keyboard Shortcuts | tools/dashboard/static/js/shortcuts.js | Chord-based navigation (g+key), direct shortcuts, help modal overlay | ICDEV™.showShortcutsHelp() | Navigation + help modal |
| Mermaid Integration | tools/dashboard/static/js/mermaid-icdev.js | ICDEV™ Mermaid module: dark theme, click handlers, editor, SVG export, auto-init | ICDEV™.renderMermaid(), ICDEV™.initMermaidEditor(), ICDEV™.exportMermaidSVG() | Rendered diagrams |
| Diagram Definitions | tools/dashboard/diagram_definitions.py | Centralized Mermaid diagram catalog: 18 diagrams across 4 categories with role filtering | get_catalog_for_role(), get_diagram() | Diagram data |
| Diagrams API | tools/dashboard/api/diagrams.py | Blueprint: list/get diagram definitions, role-filtered catalog | GET /api/diagrams/, GET /api/diagrams/<id> | JSON diagram data |
| Proxy Middleware | tools/dashboard/proxy.py | WSGI strangler-fig proxy — routes static assets to Next.js build dir (prod) or live dev server (NEXT_DEV_URL); falls through to Flask for all other requests | WSGI app + env (NEXT_DEV_URL, NEXT_STATIC_ROOT, dev_mode) | WSGI middleware instance |

