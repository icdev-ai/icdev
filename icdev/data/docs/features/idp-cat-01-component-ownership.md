# CUI // SP-CTI

# idp-cat-01 — Ownership on the Component Registry

**Epic:** IDP / CAT — Catalog, ownership and the dependency graph
**Status:** shipped

## Problem

An Internal Developer Portal's first question is *"who owns this?"* ICDEV could
not answer it. As measured on 2026-08-02, no owner, team, maintainer, steward or
on-call field existed for any of the 66 components in
`args/component_registry.yaml`, nor in the awareness graph, nor anywhere else. A
DB-wide scan found 48 owner-ish columns across 48 tables and every one is a
customer-domain payload (`dm_domains`, `cf_applications`, `mc_app_inventory`,
`ttx_*`) — none describes an ICDEV component.

`default_roles` is not ownership. It is an RBAC access list (who may *use* the
canvas), not who is accountable when it breaks.

## What shipped

Three optional fields on the registry schema and on the `Component` dataclass:

| Field | Meaning |
|-------|---------|
| `owner` | Team or individual **accountable** for the component |
| `owner_contact` | How to reach them — email, chat handle, list, URL |
| `on_call` | Rotation / escalation handle for production issues |

```yaml
- key: ndc
  kind: canvas
  display_name: Network Design Canvas
  owner: Platform Engineering
  owner_contact: platform-eng@example.mil
  on_call: platform-primary
```

### Why optional

A required field would fail the **entire** registry load for every entry without
an owner, taking all canvases and child apps down with it. All 66 existing
entries load unchanged.

### Unowned is reported, never defaulted

`_clean_ownership_field()` normalizes each field to a real value or `None`.
Blank strings, non-scalars (a nested mapping), and the placeholders in
`UNOWNED_SENTINELS` — `tbd`, `todo`, `unassigned`, `unknown`, `none`, `n/a`,
`null`, `-`, `unowned` — all collapse to `None`. A component carrying `owner:
TBD` is reported **unowned**, not owned-by-"TBD": a wrong owner routes an
incident to nobody while reading as answered.

There is no fallback owner. `Component.is_owned` is the authoritative predicate;
callers branch on it rather than truth-testing `owner`.

## API

```python
from tools.config.component_registry import get_registry
registry = get_registry()

registry.get_owner("ndc")                  # str | None (None for unknown keys too)
registry.get("ndc").is_owned               # bool
registry.get("ndc").ownership()            # one ownership record
registry.get_ownership_map()               # {key: record} for all components
registry.list_owned(kind="canvas")
registry.list_unowned(kind="canvas")       # the scorecard rule's input
registry.get_ownership_summary()           # coverage counts
```

`get_ownership_summary()` returns `total`, `owned`, `unowned`, `unowned_keys`,
`coverage_pct`, `with_contact`, `with_on_call` — optionally scoped by `kind`.
This is the input `idp-score-02` consumes to raise unowned components as a
scorecard rule; that rule itself is out of scope here.

## Backfill: deliberately zero

**0 of 66 components declare an owner**, and that is a measured baseline rather
than an oversight. The repository has no `CODEOWNERS` file, no team roster and
no maintainer metadata beyond a single `pyproject.toml` author, so there was
nothing to backfill from without guessing — and the task's own constraint is
that a wrong owner is worse than a blank one. The fields are populated as real
accountability is assigned; until then the scorecard grades an honest 0%.

## Files

- `args/component_registry.yaml` — schema documented in the header block
- `tools/config/component_registry.py` — `UNOWNED_SENTINELS`,
  `_clean_ownership_field()`, `Component.owner/owner_contact/on_call`,
  `Component.is_owned`, `Component.ownership()`, and the five registry methods
- `icdev/tools/config/component_registry.py`, `icdev/data/args/component_registry.yaml` — mirrors
- `tests/test_component_registry_ownership.py` — 24 tests

## Verification

```bash
pytest tests/test_component_registry_ownership.py tests/test_component_registry.py -q
```

64 passed. The ownership suite covers optional-ness against the real 66-entry
registry, readability for every component, placeholder rejection (parametrized
over every sentinel), contact-without-owner, malformed non-scalar owners,
summary arithmetic including the empty-registry divide, positional
back-compatibility of the dataclass, and `tools/` ↔ `icdev/` mirror parity.
