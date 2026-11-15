# CUI // SP-CTI
# icx-ace-01: the ACE event feed, recommend-by-default dispatch, auto-dispatch grants

## What was broken

`GET /api/ace/events` answered 500 on every call. Commit 36d611a31 (#64) rewrote
`tools/ace/event_bus.py` from a durable event store into the per-instance SSE queue
and dropped `emit`, `get_pending`, `mark_processed`, `store_result`,
`get_recent_results` and `pending_count`. Every caller swallowed the ImportError:
the route returned the error text, completion events were silently lost, the
dispatcher would have failed each cycle (nothing started it), `/events/topics`
answered 404, and `tests/test_nova_reflexion_trigger.py` sat in the CI backlog.

## What holds now

- `tools/ace/event_log.py` is the durable log. `event_bus.py` is the queue only.
- Topics are derived from the role YAMLs. An undeclared topic is refused.
- `/api/ace/events` answers 200, or 503 when the log cannot be read.
  `/events/topics` answers 200.

## Dispatch modes (owner, 2026-11-15)

**Recommend (default, HITL).** For each role that listens to an event, the
dispatcher records a pending `decl-wft-ace-run-launch` step. A holder decides it
at `/decisions` (launch, or decline with a reason). The dispatcher then spends
the step once, for exactly the launch it recommended, and launches. A declined,
withdrawn or changed recommendation launches nothing. One undecided for 72 hours
lapses.

**Auto (HOTL), only under a live grant.** All of these must hold:

- the role YAML declares the topic (`auto_dispatch: {eligible_topics, reason}`);
- the role's NOVA trust is `trusted` or above;
- a holder took `decl-wft-ace-auto-dispatch` with a reason, an expiry (at most
  30 days) and a daily limit;
- the grant is not revoked, expired or at today's limit, and stop-all is off.

An auto-launch shows in the Event feed as "auto, under grant X" and can be
aborted at once. If trust falls, the grant suspends itself and events are
recommendations again; a person grants anew.

**Stops.** Revoke (any holder) and stop-all (anyone signed in) take effect at
once, with a reason and no approval. Resume is the holder's step.

No shipped role declares auto-dispatch yet. Opting one in is a reviewed change to
its role YAML.

## The five rules

| Rule | Where |
|---|---|
| GUI | `/coworker`: Event feed and Auto-dispatch panels (`coworker/_dispatch_panels.html`) |
| Workflow | `decl-wft-ace-run-launch` (recommendations), `decl-wft-ace-auto-dispatch` (grant, resume) |
| Twin | `ace` adapter: `ace.event.unprocessed`, `ace.recommendation.undecided`, `ace.auto.failed`, `ace.grant.at_limit`, `ace.grant.expiring`, `ace.grant.suspended`; threads `ace_event:<id>`, `ace_grant:<id>` |
| Architecture | `args/platform_model/ace.yaml`: PM-ACE-14 to PM-ACE-18 |
| AI-native | MCP `ace_events`, `ace_auto_dispatch_grants` (read-only); no tool grants, revokes or launches |

## Recurrence guard

`tests/ace/test_ace_import_resolution.py` imports every name an ACE module imports
from another ACE module, including imports inside functions and `try` blocks.

## Config

`args/ace/auto_dispatch.yaml`. Schema: migration `20261115114944_ace_event_dispatch`
(four `ace_event_results` columns, append-only `ace_dispatch_grants`).

## Found on PostgreSQL: three tables the row predicate could not apply to

The manual check on a scratch PostgreSQL found that the decision path could not
work in a signed-in request. `wf_decision_steps` (the declared-decision ledger,
icx-cnf-02), `ace_trust_ledger` and `ace_coworkers` carry no `classification` or
`tenant_id` column, so the request's row predicate raised `UndefinedColumn`:
`/decisions` answered 503 ("the decision ledger cannot be read") and could not
decide anything, and NOVA trust fell back to the 0.5 default inside a request
while the dispatcher thread read the real score. The SQLite tests never attach a
real security context, so they did not see it.

The three tables are now `rls_exempt` in `args/schema_ownership_rules.yaml`, with
reasons (the manifest regenerated into `icdev/core/schema/tables.yaml`). The wf_
engine tables a decision opens (`wf_instances`, `wf_approvals`, `wf_feedback`)
keep their predicate. `tests/test_ace_event_dispatch_ambient.py` pins both halves.
