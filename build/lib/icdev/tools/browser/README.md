# Selenium Call Site Inventory

Refactor inventory generated 2026-04-14 by task `efa-D1-grep-selenium`.
Source: `grep -rn 'from selenium' tools/ tests/` + `grep -rn 'webdriver\.(Chrome|Edge|Firefox)'`.

**Re-verified 2026-04-15 during Phase D handoff** \u2014 counts still match:
Chrome 51 + Edge 2 (canonical impl in `driver_manager.py`) + Firefox 0.
Excludes `icdev/` package-shadow copies (7 duplicate Chrome call sites) and
planning documents (`tools/scripts/schedule_enterprise_frontend_plan.py`).

---

## Summary

| Pattern | Count |
|---------|-------|
| `webdriver.Chrome(options=...)` | 51 |
| `webdriver.Edge(options=...)` | 2 |
| `webdriver.Firefox(...)` | 0 |
| **Total driver instantiations** | **53** |
| Import-only files (no instantiation) | 3 |

All call sites should be migrated to `from tools.browser.driver_manager import get_driver`.

---

## Flat List — All Driver Instantiation Call Sites

| # | File | Line | Call Pattern |
|---|------|------|--------------|
| 1 | tools/testing/e2e_devops_canvas.py | 30 | `webdriver.Chrome(options=opts)` |
| 2 | tools/testing/e2e_full_dashboard.py | 66 | `webdriver.Chrome(options=opts)` |
| 3 | tools/testing/e2e_new_canvases.py | 43 | `webdriver.Chrome(options=opts)` |
| 4 | tools/testing/e2e_qdc_canvas.py | 227 | `webdriver.Chrome(options=opts)` |
| 5 | tools/testing/e2e_diagram_validator.py | 420 | `webdriver.Chrome(options=opts)` |
| 6 | tools/appforge/reflexes/publish.py | 140 | `webdriver.Chrome(options=opts)` |
| 7 | tools/browser/driver_manager.py | 395 | `wd.Chrome(service=service, options=opts)` |
| 8 | tools/browser/driver_manager.py | 396 | `wd.Chrome(options=opts)` |
| 9 | tests/e2e_fathomdesk.py | 45 | `webdriver.Chrome(options=opts)` |
| 10 | tests/e2e_fathomdesk_modal.py | 26 | `webdriver.Chrome(options=opts)` |
| 11 | tests/e2e_cloud_migration_security.py | 189 | `webdriver.Chrome(options=opts)` |
| 12 | tests/e2e_components_map.py | 68 | `webdriver.Chrome(options=opts)` |
| 13 | tests/e2e_confluence_widget.py | 46 | `webdriver.Chrome(options=opts)` |
| 14 | tests/e2e_ddc_sops.py | 99 | `webdriver.Chrome(options=opts)` |
| 15 | tests/e2e_exec_quality_widget.py | 46 | `webdriver.Chrome(options=opts)` |
| 16 | tests/e2e_fedramp-20x.py | 46 | `webdriver.Chrome(options=opts)` |
| 17 | tests/e2e_kanban_bulk_promote.py | 191 | `webdriver.Chrome(options=opts)` |
| 18 | tests/e2e_kanban_depends_on.py | 97 | `webdriver.Chrome(options=opts)` |
| 19 | tests/e2e_kanban_depends_on_full_lifecycle.py | 206 | `webdriver.Chrome(options=opts)` |
| 20 | tests/e2e_killswitch_widget.py | 52 | `webdriver.Chrome(options=opts)` |
| 21 | tests/e2e_ndc_sops.py | 66 | `webdriver.Chrome(options=opts)` |
| 22 | tests/e2e_network_arb_erb.py | 72 | `webdriver.Chrome(options=opts)` |
| 23 | tests/e2e_network_collect.py | 69 | `webdriver.Chrome(options=opts)` |
| 24 | tests/e2e_network_extended.py | 70 | `webdriver.Chrome(options=opts)` |
| 25 | tests/e2e_network_geo.py | 61 | `webdriver.Chrome(options=opts)` |
| 26 | tests/e2e_network_import.py | 90 | `webdriver.Chrome(options=opts)` |
| 27 | tests/e2e_network_innovation.py | 61 | `webdriver.Chrome(options=opts)` |
| 28 | tests/e2e_network_p1.py | 66 | `webdriver.Chrome(options=opts)` |
| 29 | tests/e2e_network_p1_rulebook.py | 66 | `webdriver.Chrome(options=opts)` |
| 30 | tests/e2e_network_p2.py | 66 | `webdriver.Chrome(options=opts)` |
| 31 | tests/e2e_network_p2_patterns.py | 66 | `webdriver.Chrome(options=opts)` |
| 32 | tests/e2e_network_p3.py | 70 | `webdriver.Chrome(options=opts)` |
| 33 | tests/e2e_network_p345.py | 69 | `webdriver.Chrome(options=opts)` |
| 34 | tests/e2e_network_peering_capacity.py | 65 | `webdriver.Chrome(options=opts)` |
| 35 | tests/e2e_network_phase_a.py | 70 | `webdriver.Chrome(options=opts)` |
| 36 | tests/e2e_network_phase_b.py | 69 | `webdriver.Chrome(options=opts)` |
| 37 | tests/e2e_network_phase_c.py | 67 | `webdriver.Chrome(options=opts)` |
| 38 | tests/e2e_network_profiles.py | 67 | `webdriver.Chrome(options=opts)` |
| 39 | tests/e2e_network_projects.py | 66 | `webdriver.Chrome(options=opts)` |
| 40 | tests/e2e_network_refresh.py | 59 | `webdriver.Chrome(options=opts)` |
| 41 | tests/e2e_network_routing.py | 59 | `webdriver.Chrome(options=opts)` |
| 42 | tests/e2e_network_whatif.py | 57 | `webdriver.Chrome(options=opts)` |
| 43 | tests/e2e_oracle_insights_widget.py | 46 | `webdriver.Chrome(options=opts)` |
| 44 | tests/e2e_simulation.py | 70 | `webdriver.Chrome(options=opts)` |
| 45 | tests/e2e_writeguard.py | 77 | `webdriver.Chrome(options=opts)` |
| 46 | tests/e2e/e2e_migration_canvas.py | 43 | `webdriver.Chrome(options=opts)` |
| 47 | tests/security/e2e_sandbox_smoke.py | 100 | `webdriver.Chrome(options=opts)` |
| 48 | tests/dashboard/e2e_opt68_ux_patterns.py | 49 | `webdriver.Chrome(options=opts)` |
| 49 | tests/dashboard/e2e_oracle_history.py | 113 | `webdriver.Chrome(options=opts)` |
| 50 | tests/dashboard/e2e_poam_list.py | 107 | `webdriver.Chrome(options=opts)` |
| 51 | tests/innovation/e2e_kanban_promoter.py | 120 | `webdriver.Chrome(options=opts)` |
| 52 | tools/browser/driver_manager.py | 376 | `wd.Edge(service=service, options=opts)` |
| 53 | tools/browser/driver_manager.py | 377 | `wd.Edge(options=opts)` |

---

## Grouped by Pattern

### Pattern A — `webdriver.Chrome(options=...)` (51 call sites)

Direct Chrome instantiation. Each site manually constructs `Options`, adds `--headless=new`,
`--no-sandbox`, `--disable-gpu`, and `--window-size`. Identical boilerplate repeated in every file.

**tools/ (8 calls)**

| File | Line |
|------|------|
| tools/testing/e2e_devops_canvas.py | 30 |
| tools/testing/e2e_full_dashboard.py | 66 |
| tools/testing/e2e_new_canvases.py | 43 |
| tools/testing/e2e_qdc_canvas.py | 227 |
| tools/testing/e2e_diagram_validator.py | 420 |
| tools/appforge/reflexes/publish.py | 140 |
| tools/browser/driver_manager.py | 395 |
| tools/browser/driver_manager.py | 396 |

**tests/ (43 calls)**

| File | Line |
|------|------|
| tests/e2e_fathomdesk.py | 45 |
| tests/e2e_fathomdesk_modal.py | 26 |
| tests/e2e_cloud_migration_security.py | 189 |
| tests/e2e_components_map.py | 68 |
| tests/e2e_confluence_widget.py | 46 |
| tests/e2e_ddc_sops.py | 99 |
| tests/e2e_exec_quality_widget.py | 46 |
| tests/e2e_fedramp-20x.py | 46 |
| tests/e2e_kanban_bulk_promote.py | 191 |
| tests/e2e_kanban_depends_on.py | 97 |
| tests/e2e_kanban_depends_on_full_lifecycle.py | 206 |
| tests/e2e_killswitch_widget.py | 52 |
| tests/e2e_ndc_sops.py | 66 |
| tests/e2e_network_arb_erb.py | 72 |
| tests/e2e_network_collect.py | 69 |
| tests/e2e_network_extended.py | 70 |
| tests/e2e_network_geo.py | 61 |
| tests/e2e_network_import.py | 90 |
| tests/e2e_network_innovation.py | 61 |
| tests/e2e_network_p1.py | 66 |
| tests/e2e_network_p1_rulebook.py | 66 |
| tests/e2e_network_p2.py | 66 |
| tests/e2e_network_p2_patterns.py | 66 |
| tests/e2e_network_p3.py | 70 |
| tests/e2e_network_p345.py | 69 |
| tests/e2e_network_peering_capacity.py | 65 |
| tests/e2e_network_phase_a.py | 70 |
| tests/e2e_network_phase_b.py | 69 |
| tests/e2e_network_phase_c.py | 67 |
| tests/e2e_network_profiles.py | 67 |
| tests/e2e_network_projects.py | 66 |
| tests/e2e_network_refresh.py | 59 |
| tests/e2e_network_routing.py | 59 |
| tests/e2e_network_whatif.py | 57 |
| tests/e2e_oracle_insights_widget.py | 46 |
| tests/e2e_simulation.py | 70 |
| tests/e2e_writeguard.py | 77 |
| tests/e2e/e2e_migration_canvas.py | 43 |
| tests/security/e2e_sandbox_smoke.py | 100 |
| tests/dashboard/e2e_opt68_ux_patterns.py | 49 |
| tests/dashboard/e2e_oracle_history.py | 113 |
| tests/dashboard/e2e_poam_list.py | 107 |
| tests/innovation/e2e_kanban_promoter.py | 120 |

### Pattern B — `webdriver.Edge(options=...)` (2 call sites)

Edge instantiation lives exclusively inside `driver_manager.py`. The module-level
`get_driver()` function is already the right abstraction — these two lines are the
canonical implementation, not call sites to refactor away.

| File | Line | Call Pattern |
|------|------|--------------|
| tools/browser/driver_manager.py | 376 | `wd.Edge(service=service, options=opts)` |
| tools/browser/driver_manager.py | 377 | `wd.Edge(options=opts)` |

### Pattern C — `webdriver.Firefox(...)` (0 call sites)

Firefox is not used anywhere in `tools/` or `tests/`.

---

## Import-Only Files (no driver instantiation)

These files import selenium helpers (`By`, `WebDriverWait`, `expected_conditions`) but
receive a driver instance from a caller rather than constructing one themselves.

| File | Imported Symbols |
|------|-----------------|
| tools/testing/e2e_security_canvas.py:22,24 | `By`, `WebDriverWait` |
| tests/e2e_election_phase_widget.py:15–17 | `By`, `expected_conditions`, `WebDriverWait` |
| tests/e2e_network_canvas.py:15,17–18 | `By`, `expected_conditions`, `WebDriverWait` |

---

## Refactor Target

Replace every Pattern A call site with:

```python
from tools.browser.driver_manager import get_driver

driver = get_driver(headless=True)
```

`get_driver()` already handles Chrome/Edge selection, `--headless=new`, `--no-sandbox`,
`--disable-gpu`, window size, service path resolution, and thread-local caching.
No per-file boilerplate needed after migration.
