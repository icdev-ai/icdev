# A claim from a PLAIN SHELL now HOLDS -- `--claim` hands its lease to a keeper (mfx-own-02)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

```bash
python tools/kanban/cli.py --claim <task-id> --intent "repairing its PR by hand" [--ttl 7200]
python tools/kanban/cli.py --claim <task-id>                    # again: RENEWS the running keeper
python tools/kanban/cli.py --release <task-id>                  # ends the keeper session, frees the lease
python -m tools.kanban.interactive_claim --status <task-id>     # keeper pid, expiry, intent, log
```

MEASURED 2026-09-03: an operator ran `--claim rmf-ui-13` to hold the task while
repairing its PR by hand; the CLI answered that the lease was bound to a pid
that exits on the next line and an UNREGISTERED session id, so every reader
would read it as litter within seconds -- and a second session repaired the
same branch at 14:01. Only a registered SERVICE session could make the lease
hold; a shell is not one. Now `claim()` takes the lease synchronously under
the caller's identity (the same refusal every claimant gets), `leases.handover`
re-binds it -- no release/acquire window -- to a dedicated
`cli-claim-<task>-t<hex>` id, and a detached KEEPER registers that id in
agent_sessions (agent_type `cli`, your `--intent`), re-takes the lease under
its own live pid and heartbeats every 60s until the TTL, `--release`, a lost
lease or a terminal task status. Every reader then finds what it already
honours: `lease_liveness` reads `live` by pid and, to a reader that cannot see
the pid, by registered session; the dispatch window yields; startup recovery
keeps the row; `restore_acts --plan` refuses "the holder process is running".
NEVER THE INHERITED IDENTITY (claim-verif-33c9f4cd11): a shell inside a kanban
worker carries the scheduler's `ICDEV_SESSION_ID`; the keeper's env is
rewritten before the registry is touched, and the `t<hex>` suffix can never
parse as a `<name>-<pid>` service id. A dead keeper is OUR litter and the next
`--claim` replaces it. `create_tasks(claim=True)` from a non-service process
(`service_identity._OWNED` empty) hands its seed claims to the same keeper.
State and log per task: .tmp/coordination/claims/<task>.{json,log}.
