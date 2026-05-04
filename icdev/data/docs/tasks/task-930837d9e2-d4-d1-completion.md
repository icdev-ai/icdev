# Task Completion: task-930837d9e2-d4-d1

**Type:** research
**Title:** Investigate coherence gate failure root cause

## Findings

### Root Cause
The coherence gate was failing with exit code 1 due to **unresolved git conflict markers in `tools/manifest.md`** introduced during task `task-930837d9e2-d2` (Add narrative_generator.py to manifest).

The conflict markers (`<<<<<<< Updated upstream`, `=======`, `>>>>>>> Stashed changes`) in the auto-registered section of `tools/manifest.md` caused the manifest coherence check (`check_id: manifest`) to fail, setting `failed_checks > 0` and triggering `sys.exit(1)`.

### Fix Already Applied
Commit `88596ca7` (`docs(manifest): add narrative_generator.py entry and resolve conflict markers`) removed the stale conflict markers and added the narrative_generator.py entry. This was applied at 2026-04-23 19:07 EDT.

### Current Coherence State
- **overall_pass: True** (exit code 0)
- 16 checks pass, 0 failures, 1 warning
- Warning: `tools/db/migrations/023_ad_news_patterns/up.py:10` cites `'derived from NEWS_PATTERN_SEVERITIES'` which is not in `_ATTRIBUTION_REGISTRY`. This is a warning-only, does **not** set exit code 1.

### Coherence Gate Exit Logic
From `tools/workflow/coherence_checker.py:2683`:
```python
overall_pass = failed == 0   # warnings do NOT count as failures
sys.exit(0 if report.overall_pass else 1)
```

## Status
Root cause identified and already resolved. Coherence gate passes in both main branch and task-930837d9e2-d4 worktree.
