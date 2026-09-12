# Raw board writers — does this INSERT bypass the canonical seeder? (rem-hyg-05)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

```bash
python tools/kanban/raw_insert_census.py --check          # the gate; exit 1 on a NEW raw INSERT
python tools/kanban/raw_insert_census.py --json           # full report
python tools/kanban/raw_insert_census.py --changed tools/foo.py --check
python tools/kanban/raw_insert_census.py --staged         # only what this commit touches
python tools/kanban/raw_insert_census.py --prune          # drop entries whose site is gone
python tools/workflow/coherence_checker.py --check board_writer_census --gate
```

219 raw `INSERT INTO kanban_tasks` sites bypass task_factory today (42 of them
in tools/genesis/reflexes/*, the autonomous path). They are grandfathered BY
NAME in args/kanban_raw_insert_census.txt; a NEW one fails. The fix is
`from tools.kanban.task_factory import create_tasks`. Converting the 219 is
rem-hyg-06. `raw_insert_max` in args/board_writer_gate.yaml may only go DOWN.
