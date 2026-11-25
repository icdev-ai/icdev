# CUI // SP-CTI

# Twin threads held to their item (icx-sec-01)

A twin finding's `thread_id` names the record it rests on. Knowing the id is not a right to read the record.
`tools/twin_core/governance.py resolve_thread(thread_id, reader)` follows a thread only for a reader who may see the
item the record belongs to. For anyone else -- another org, a lower clearance, an agent in no org, no reader at all --
the answer is exactly what an id that does not exist gets, apart from the id the caller sent (no existence oracle).

A reader is `{id, role, clearance, org}`. The console routes pass the signed-in person (`thread_reader()`, the
person's row read fresh); MCP `twin_governance` passes an agent in no org; the CLI passes none.

## What each kind is held to

| Kind | Held to |
|---|---|
| `wf_step` | its workflow item: a factory build's target, a store package, a system's objective evidence (each module's own `visible`); CoE standard/ADR/golden-path/conformance and vulnerability discovery steps are as open as their registers; any other template resolves for no one |
| `factory_builds`, `evidence_collectors` | the module's `thread_record(event_id, reader)` |
| `cortex_audit`, `cortex_finding`, `idp_exemption`, `studio_workflow` | the row's own tenant and marking. A row with no tenant fails closed, unless identifiably the platform's (an exemption row of the CoE bootstrap migration, `actor_kind: bootstrap`) |
| `audit_trail`, `ace_audit` | the row's marking |

A person whose row has no tenant is in `default` on a single-tenant install (neither `ICDEV_SAAS_MODE` nor
`ICDEV_MULTI_TENANT` set, `args/re_engines.yaml shared_deployment_signals`) and in no org on a shared one
(`tools/saas/auth/middleware.py effective_org`).

## Writes carry their writer's org

Exemption requests (`/idp/api/exemptions/request`) and the five write paths into `studio_workflows` stamp
`tenant_id` from the signed-in person (`signed_in_org()`). Migration `20261124230000_studio_workflows_tenant` gives an
existing workflow with no tenant its creator's tenant. Exemption rows are append-only and hash-chained, with
`tenant_id` inside the hash, so old exemption rows are not backfilled: one with no tenant stays no one's (the CoE
bootstrap's excepted).

## Factory builds and ClawHub

A factory build's declared step and publication carry the target's org, and export re-checks the publisher's org,
clearance and status. Builds published before this card must be published again (409 `step_missing`). ClawHub promote
is a named ISSO's or administrator's act, never the request body's `promoted_by` or the shared bootstrap account, and
never approves its own review.

## Open (icx-sec-02)

These thread kinds still resolve for anyone holding an id, because their items have no visibility rule yet:
`ace_instance`, `ace_trust`, `ace_event`, `ace_grant` (the ACE engine) and `mdz_assessment`, `migration_plan`,
`legacy_app`, `mc_refactor_job`, `aiify_decision` (project-scoped modernisation records; no project-to-tenant rule).
`audit_trail` is held to its marking only, not to a project's tenant. `/decisions/api/steps` lists every step to every
signed-in person. ClawHub promote does not check the import's tenant.

Tests: `tests/test_icx_sec_01_thread_oracles.py`, `tests/test_icx_sec_01_ambient.py` (PG tier).
