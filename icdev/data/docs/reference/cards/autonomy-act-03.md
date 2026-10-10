# The restore tier, ENUMERATED — four mechanical acts, and no fifth (autonomy-act-03, autonomy-dep-04)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

```bash
python tools/awareness/restore_acts.py --list
python tools/awareness/restore_acts.py --plan [--json]                      # candidates; ACTS NOTHING
python tools/awareness/restore_acts.py --apply reap_dead_lease --target <task-id>
python tools/awareness/restore_acts.py --apply prune_gone_census_entry --target <census entry>
python tools/awareness/restore_acts.py --apply restart_stale_daemon --target tools.genesis.daemon
python tools/awareness/restore_acts.py --apply restore_auto_managed_file --target args/projects.yaml --root <checkout>
python tools/awareness/restore_acts.py --apply <act> --target <t> --dry-run   # prove, audit nothing, act on nothing
```

A CLOSED SET IS THE CONTROL. `ACTS` is a frozen mapping of exactly four
names; an open-ended "the agent decides what to fix" is not self-healing, it
is an unaudited actuator with write access to its own guardrails. There is
deliberately NO act that edits a claim, threshold or assertion so a surface
agrees — the module cannot even import the claims registry, and a test reads
its AST to prove it.
EVERY ACT IS prove -> audit -> apply -> confirm, IN THAT ORDER.
  prove    re-derives the precondition from PRIMARY evidence now. `proven` is
           True | False | None, and None (cannot tell) REFUSES.
  audit    writes `awareness.restore_act` / restore.<act>.intent to
           audit_trail BEFORE acting, raise_on_error=True. No row, no act:
           `unaudited_refused` — an unaudited automatic repair is
           indistinguishable from drift. On a PG database that has not run
           migration 20260821045946 the CHECK refuses the type and EVERY act is
           refused; that is the correct reading, not an obstacle.
  apply    one lease file / one census line / one pid. Nothing else.
  confirm  re-reads the world; `applied_unconfirmed` is never `applied`.
THE FOUR:
  reap_dead_lease          kanban:task:<id> only. Holder pid PROVABLY dead AND
                           the task not heartbeating — rem-hyg-15's
                           `_task_is_heartbeating`, the same question every
                           reaper asks (autonomy-adm-03), not a third opinion.
                           rem-hyg-13 measured holder_is_alive() False FOUR
                           SECONDS after a heartbeat: the pid on the lease is
                           the dispatcher's, which exits while the worker runs
                           on. `service:` etc. are out of scope — nothing
                           heartbeats for them, so the two-signal proof cannot
                           be made. Undo: `kanban/cli.py --claim <id>`.
  prune_gone_census_entry  ONE line from ONE of five enumerated census files
                           (undeclared_import, perfect_score, kanban_raw_insert,
                           ci_test_backlog, ci_skip), and only when the FILE the
                           line names does not exist — what a Path.exists() can
                           verify. A fixed site whose file still exists is the
                           scanner's own --prune to decide. Only ever shrinks,
                           so cef-ci-02's census_growth never objects. Undo:
                           `git checkout -- <census>`.
  restart_stale_daemon     terminate ONE supervised child whose import closure
                           is `stale` per code_staleness (autonomy-id-02), only
                           while the supervisor is UP (autonomy-id-03) to
                           restart it from the current tree within 30s. No
                           supervisor => refused: that is a kill, not a
                           restart. The pid must be the identity row's pid AND
                           its command line must run the service's script
                           (supervisor_status.SERVICES, the same fragments the
                           launcher kills by) — a reused pid, a `bash -c` that
                           merely typed the name, and an unreadable process
                           table all refuse. Graceful terminate, never kill.
  restore_auto_managed_file  `git checkout --` ONE tracked file a reflex REGENERATES
                           (autonomy-dep-04; the set is AUTO_MANAGED_FILES, declared
                           by each WRITER's TRACKED_RELPATH: args/projects.yaml from
                           kanban_project_sync, today the only entry). Proven only
                           when deployment_freshness is `blocked` with the guard's
                           overlap naming EXACTLY enumerated files AND the writer,
                           re-run over HEAD:<file> against the CURRENT board,
                           reproduces the local diff (regenerable_diff: committed
                           projects unchanged apart from appended epics, every
                           addition one the writer would make; a board that grew
                           since is fine). A HUMAN EDIT to name/description/briefs/a
                           committed epic is False and NEVER reverted; an unreadable
                           board, HEAD or working copy, or an empty board, is None.
                           A second blocked file NO writer regenerates refuses too.
                           apply = checkout, then pull THROUGH pull_if_safe (never a
                           bare git pull), then re-run the writer on the pulled tree
                           so the board's cards are re-derived on top of what the
                           incoming commits registered by hand. confirm asks the
                           guard again, dry run. Undo: nothing to undo, by proof.
                           MEASURED live 2026-08-23 on C:/AI/ICDev (1,340 refusals
                           deep): proven True, 4 behind, applied, current.
CONSUMED by the `deployment_freshness` detector in tools/kanban/detector_findings.py
(detector_findings_reflex, 6h): on `blocked` it asks this act FIRST, re-measures,
and files a card ONLY for a freeze still standing, carrying the act's refusal.
`restore: false` under that detector's config files the card without asking.
`--plan` re-proves every candidate and prints the refusals too, and it states
what it MEASURED (leases / census files read / staleness state) beside the
verdict, because "no candidate" over an unmeasured fleet is the fabricated
clean bill this whole card series exists to refuse.
