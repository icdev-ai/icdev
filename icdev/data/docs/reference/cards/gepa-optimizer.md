# GEPA Optimizer — Genome Evolution Pressure Analyzer (MCP tool: gepa_optimizer)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

```bash
python tools/skills/gepa_optimizer.py --json           # Run optimization pass (prune low-fitness genome entries)
python tools/skills/gepa_optimizer.py --dry-run --json # Scan without writing changes
```

MCP tool: gepa_optimizer  params: dry_run (bool, default false)
  Returns: {applied: [...], declined: [...], skipped: [...], errors: [...]}
rem-cap-01: GEPA records a DECISION against every artifact it evaluates, not
only the ones it applies. `declined_no_delta` / `declined_low_score` /
`declined_unmappable_skill` are TERMINAL (nothing rescores an artifact after
insert, and a blank skill_used can never resolve) so the artifact leaves the
queue; `declined_skill_file_missing` / `declined_rubric` /
`declined_empty_patch` are retried next cycle. Consumption for the
`skill_optimizer` capability class is a recorded decision, applied OR
declined — counting applies alone made "GEPA ran and correctly declined
everything" read identically to "GEPA never ran", which is the exact defect
capability_consumption exists to catch. `status` is deliberately untouched:
skills_lifecycle.py and ace/blueprint.py read status='pending' as NOVA's
proposal queue.
