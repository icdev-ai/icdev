# A service's session id is INHERITED by everything it spawns (claim-verif-33c9f4cd11)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

```bash
python tools/awareness/claim_verifier.py --claim scheduler_heartbeat_is_fresh
python -c "from tools.coordination.service_identity import is_inherited_identity as f; import os; print(f(os.environ.get('ICDEV_SESSION_ID','')))"
```

The claim above filed its first live card on 2026-09-03 for a scheduler that
was LOOPING NORMALLY: reported `[22508]` (the process table), derived
`[31872]` (the registry). TWO reductions were wrong and the data was not.
 * `claim_service_identity` writes `kanban-scheduler-<pid>` and
   `ICDEV_AGENT=kanban` into os.environ, and every kanban worker session --
   and every command run inside one -- inherits both. Any of them touching
   session_registry wrote THE SCHEDULER'S ROW: register() replaced its pid,
   heartbeat() refreshed it on the child's behalf, the Stop hook's
   end_session() could mark it ended; and the claim's derived side read every
   `agent_type='kanban'` row, so a worker's coordination-hook row (pid 31872)
   was "what the primary data says", and a board with NO scheduler read
   `agrees` against hook rows. Ownership is now PROCESS-LOCAL
   (service_identity._OWNED, a set, never an env var): an id embedding a pid
   that is not ours for a name we never claimed is a descendant's, and the
   registry writes it as `<parent-id>/child-<pid>`; `is_service_session`
   refuses child ids, and the claim reads scheduler rows through it. A
   service re-executed by code_reload (a NEW pid on Windows, the old id in
   the env) claims its name again in main() and keeps its row.
 * The scheduler's heartbeat was written ONCE per cycle, before the work, and
   a cycle with a dozen tasks in flight runs 9-37 minutes on this board --
   outside the claim's ten-minute window and session_registry's fifteen-minute
   reap. A pump thread now beats every minute WHILE a cycle works and
   WITHHOLDS the beat past ICDEV_SCHEDULER_CYCLE_CEILING (3600s, above every
   cycle measured), where "busy" becomes the alive-but-not-looping state the
   claim exists for; a cycle longer than the interval logs its duration.
NOT fixed here, and named: leases still key on the raw inherited id, so a
child's lease reads as the scheduler's; and DaemonBase.run_forever and
pr_watcher heartbeat once per loop the same way, unpumped.
