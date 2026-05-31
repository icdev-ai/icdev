# Plan: Verify AI-ify Scanner Fixes End-to-End

## Context
The AI-ify scanner semgrep rules were narrowed and simplified to fix:
1. **0 AI-ready results** for paperless-ngx — rules were too narrow (class wrappers) or background processes cached old module state.
2. **Noisy search rule** (`aac-search-broad-py`) — 262 hits from `$QS.filter($KW=$VAL)` matching every Django filter.
3. **FileField/consumer rules** — class-wrapped patterns failed on abstract base classes in paperless-ngx.
4. **UI overload** — no per-file hit cap.

Fixes applied:
- Removed `$QS.filter($KW=$VAL)` from search rule; kept `Q(...)`, `.annotate(...)`, and `search()`/`query()` functions only.
- Simplified `aac-django-filefield-py` to standalone `$FIELD = models.FileField(...)`.
- Removed `class $CLASS(...)` wrapper from consumer/ingest rules.
- Added `max_per_file=5` in `_map_semgrep_results()`.

## Steps

1. **In-process scan verification**
   - Run `detect_patterns()` + `run_scan()` on `https://github.com/paperless-ngx/paperless-ngx` in current Python process.
   - Confirm verdict = **AI-ready (wholly)** with framework-level opportunities (document ingestion, OCR, search, metadata, classification).
   - Target: ≥5 distinct framework pattern types, ≥50 total opportunities, top candidate score ≥65%.

2. **Playwright UI verification**
   - Navigate to AI-ify dashboard wizard (`/aiify`).
   - Advance to Panel 6 (PRD Review).
   - Screenshot to verify **Dry-run** button is visible next to **Send phase to Kanban**.
   - Click Dry-run, verify `dryRunBox` renders with PRD quality score and tasks preview.

3. **Health & coherence gates**
   - `python tools/testing/health_check.py --json`
   - `python tools/workflow/coherence_checker.py --all --gate`
   - Fix any new failures introduced by our changes (expect the 2 pre-existing RLS/append-only issues to remain).

4. **Companion sync**
   - `python tools/dx/companion.py --sync --write --json`
   - Ensures `icdev/tools/dashboard/templates/aiify/page.html` is mirrored.

5. **Commit**
   - Stage all modified files: semgrep rules, `pattern_classifier.py`, constants/scorer updates.
   - Commit with message referencing the fix.

## Success Criteria
- Scan of paperless-ngx returns AI-ready verdict.
- Dry-run button visible and functional in dashboard.
- Coherence gate passes (excluding pre-existing failures).
- Companion sync completes without error.
- All changes committed.
