# Goal: Promote Innovation Signals to Kanban

> Connect the Innovation Engine's signal queue to the Kanban board "Suggested" column.

## Context
The Innovation Engine (`goals/innovation_engine.md`) scans external sources, scores signals, and triages them into `approved`, `suggested`, `logged`, or `blocked`. Approved signals sit in `innovation_signals` with no downstream path to execution.

The Kanban board (`/kanban` dashboard page) has a **Suggested** column that was permanently empty because no producer ever wrote `status='suggested'` rows. This goal wires the two systems together.

## Workflow
1. Run `python tools/innovation/kanban_promoter.py --dry-run --json` to preview promotable signals.
2. Run `python tools/innovation/kanban_promoter.py --triage-result approved --limit 10` to insert up to 10 signals as `kanban_tasks(status='suggested')`.
3. Open `/kanban` — approved signals appear in the Suggested column with INNOV-\<prefix\> titles.
4. Reviewer promotes Suggested → Backlog (or rejects) via the UI.

## Provenance
Every promoted kanban row stamps `source_prediction_id = innovation_signals.id`, so the kanban card traces back to the originating signal.

## Idempotency
Re-running the promoter is safe — the query excludes signals whose id already appears in `kanban_tasks.source_prediction_id`.

## Configuration
`args/innovation_promoter.yaml`:
- `triage_results` — which triage states to promote (default: `[approved]`)
- `min_innovation_score` — score gate (default: 50)
- `max_per_run` — per-batch cap (default: 50)
- `priority_thresholds` — score → priority mapping (high=80, medium=60)

## Related
- `goals/innovation_engine.md` — upstream producer
- `tools/innovation/kanban_promoter.py` — this promoter
- `tools/innovation/innovation_manager.py` — orchestrator

## Acceptance
- `/kanban` Suggested column populates from approved innovation signals
- Re-running the promoter inserts zero rows
- Each kanban card links back to its source signal via `source_prediction_id`
