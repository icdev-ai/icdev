# CUI // SP-CTI
# FORGE Academy — Phase 6: Auto-Currency

**Date:** 2026-05-09  
**Roadmap phase:** Phase 6 of ICDEV™ AI Upskilling & Innovation Platform  
**Status:** COMPLETE

---

## What Was Built

### 1. Oracle Lens — Staleness Detector

**File:** `apps/forge_academy/oracle/lens_staleness_detector.py`  
**Lens ID:** `staleness_detector`  
**Registered in:** `apps/forge_academy/oracle/runner.py` (5 total lenses now)

Two detection signals:

| Signal | Logic | Severity |
|--------|-------|----------|
| `stale_content` | Mission created >180 days ago + <3 completions in last 90 days | `critical` if >365 days old, `warning` otherwise |
| `draft_limbo` | Mission with `status='draft'` sitting >30 days without activation | `warning` |

Confidence formula:
- Staleness: `min(0.92, 0.55 + age_bonus)` dampened by recent completion count
- Draft limbo: `min(0.88, 0.65 + age_bonus)`

Predictions feed into `fa_oracle_predictions` via the existing Oracle runner pipeline. Convergence detection with other lenses still applies.

---

### 2. fa_missions Schema Extension

Two new columns added via safe `ALTER TABLE` migration in `migrate()`:

| Column | Type | Default | Purpose |
|--------|------|---------|---------|
| `status` | `TEXT` | `'active'` | Lifecycle state: `active`, `draft`, `archived` |
| `updated_at` | `TEXT` | `NULL` | Last content refresh timestamp |

The `ALTER TABLE` runs silently on existing installs (catches "duplicate column" exceptions).

---

### 3. Genesis Reflex — academy_reflex

**File:** `tools/genesis/reflexes/academy_reflex.py`  
**Registered in:** `tools/genesis/daemon.py` REFLEX_NAMES (24 reflexes total)  
**Config:** `args/genesis_config.yaml` → `reflexes.academy_reflex`  
**Schedule:** every 6h | cooldown 360 min | max_drafts: 5

**Phase 1 — Pattern → Draft:**
- Queries `genesis_gkp` for `artifact_type='proven_pattern'`, `confidence >= 0.70`, `promotion_status='promoted'`
- For each pattern without an existing mission slug match: inserts a `fa_missions` row with `status='draft'`, `is_active=0`, `tier=2`
- Slug format: `draft-{pattern-name-slugified}`
- Human must review and set `status='active'` + `is_active=1` before the mission appears in the Academy

**Phase 2 — Staleness → Kanban:**
- Runs `LensStalenesssDetector` directly
- Promotes predictions with `confidence >= 0.70` to `kanban_tasks` (`status='suggested'`, `source='academy_reflex'`)
- Task title: `[Academy] {prediction_title}`
- Priority: `high` for critical severity, `medium` for warning

**Return dict:**
```python
{
  "success": True,
  "metric_value": float(drafts_created + kanban_tasks_created),
  "details": {
    "patterns_found": int,
    "drafts_created": int,
    "draft_slugs": list[str],
    "stale_predictions": int,
    "kanban_tasks_created": int,
  }
}
```

---

## Verification

```bash
# Smoke test all Phase 6 components
python -c "
from apps.forge_academy.oracle.lens_staleness_detector import LensStalenesssDetector
from apps.forge_academy.oracle.runner import _LENSES
from tools.genesis.daemon import REFLEX_NAMES
import importlib
mod = importlib.import_module('tools.genesis.reflexes.academy_reflex')
assert any(l.name == 'staleness_detector' for l in _LENSES)
assert 'academy_reflex' in REFLEX_NAMES
assert hasattr(mod, 'run')
print('Phase 6 smoke test PASSED')
"

# CLI dry-run (no DB required)
python tools/genesis/reflexes/academy_reflex.py --dry-run --json
```

---

## Flywheel Closure

Phase 6 closes the auto-currency loop described in the roadmap:

```
FORGE ACADEMY → teaches patterns → 
GENESIS discovers new patterns → 
academy_reflex creates draft missions → 
Human reviews and activates → 
New missions teach the new patterns → 
Curriculum stays current without manual editorial cycles
```

The staleness detector ensures that missions which haven't been touched in 180+ days and are no longer being used surface in the Kanban backlog for review before they mislead learners with outdated API references.

---

## Files Changed

| File | Change |
|------|--------|
| `apps/forge_academy/db.py` | Added `status`, `updated_at` to `fa_missions` DDL + safe ALTER TABLE migration |
| `apps/forge_academy/oracle/lens_staleness_detector.py` | New Oracle lens (staleness_detector) |
| `apps/forge_academy/oracle/runner.py` | Registered LensStalenesssDetector (5th lens) |
| `tools/genesis/reflexes/academy_reflex.py` | New Genesis reflex (Phase 1 + Phase 2) |
| `tools/genesis/daemon.py` | Added `academy_reflex` to REFLEX_NAMES (24 total) |
| `args/genesis_config.yaml` | Added `academy_reflex` reflex config block |
