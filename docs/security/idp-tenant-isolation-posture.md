# CUI // SP-CTI

# IDP Tenant Isolation Posture — Decision Record

Decision record for how tenant isolation is enforced on the Internal Developer
Portal's catalog and scorecard data, and whether that enforcement is sufficient
to offer the surface to an external tenant.

> **Scope note (public repo).** ICDEV is a public repository. This document
> records an engineering posture and the work required to change it. It is not
> an adversarial analysis of the current control, and it deliberately does not
> enumerate bypass techniques. Follows the same convention as
> [spike-crx-db-01](../reference/spike-crx-db-01-pg-native-rls.md).

- **Card:** `idp-mt-01` (IDP — tenant scoping for the outward-facing half)
- **Status:** **OPEN — awaiting a named human decision.** The machine-readable
  half of this record, [`args/idp_tenancy.yaml`](../../args/idp_tenancy.yaml),
  carries `external_offering_approved: false` and must stay false until
  §6 is signed.
- **Type:** decision record. The scoping code it describes ships with this card;
  the *decision* does not, by design.

---

## 1. Why this record exists

The IDP ships in two directions at once. Inward, it is ICDEV governing its own
registered components. Outward, it is the same catalog-plus-scorecard surface
offered to a customer over their services. Those two readings ask the same
question — *which components does this scorecard cover?* — and before `idp-mt-01`
there was only one answer available: all of them.

Scoping the data is an engineering problem and this card solves it. Whether the
*mechanism* used to scope it is strong enough to put a customer's data behind is
a different question, with a different kind of answer. Inheriting that second
answer silently — shipping an external surface because the internal one happened
to work — is the specific failure this record exists to prevent.

---

## 2. Measured state

Measured 2026-08-02, re-verified 2026-08-03 against the live PostgreSQL
instance.

| Fact | Value |
|------|-------|
| Tables in `public` schema | 1,758 |
| Tables with PostgreSQL RLS enabled (`pg_class.relrowsecurity`) | **0** |
| Rows in `pg_policies` | **0** |
| `tenant_component_overrides` present and readable | yes (migration 207) |
| Posture recorded in `args/idp_tenancy.yaml` | `isolation_posture: app_layer_only` |

Isolation today is enforced entirely in application code: the storage layer
rewrites each query's `WHERE` clause to append a tenant and classification
predicate ([`tools/security/row_security.py`](../../tools/security/row_security.py)
plus `conn.set_security_context()`). PostgreSQL-native RLS exists only as a
feasibility spike, [spike-crx-db-01](../reference/spike-crx-db-01-pg-native-rls.md),
which recommends a phased GO but has not been implemented.

Two internal service engines deliberately opt out of the app-layer predicate and
are annotated as doing so — `readiness_scorer.py` and
`canvas_compliance/posture.py`, both carrying
`# rls-bypass: internal service engine … tenant isolation enforced at API
boundary`. Those bypasses are consistent with the internal reading and are
**not** consistent with an external one, which is precisely the gap this record
asks a human to rule on.

### What none of the scoring tables carried

Checked against `information_schema`: `readiness_scores`, `production_audits`,
`developer_scorecards`, `kg_nodes`, `awareness_component_health` and
`kanban_tasks` — **none** carried a `tenant_id` column. Component-level scoring
was globally scoped by construction rather than by oversight.

---

## 3. What this card changed

Scoping now exists, and is applied at one choke point.

- **`idp_scorecard_history.tenant_id`** stamps every persisted score with the
  tenant it was evaluated for, or `NULL` for the platform's own series.
  Migration `20260803031229` guarantees the column on any database that
  predates it and indexes the tenant predicate that every read now carries.
- **Every read filters on it.** `tenant_id=None` reads `tenant_id IS NULL` —
  the platform's own series — rather than "everything". A nullable column
  nobody filters on would be decoration, and a default read that blended
  tenants would put a customer's scores into ICDEV's own trend line.
- **Scope comes from `tenant_component_overrides`.** A tenant's scorecard covers
  the components *that tenant* has enabled, resolved exactly as
  `ComponentRegistry.is_enabled_for_tenant` resolves them. Grading a tenant on
  canvases they cannot reach would make the grade a statement about somebody
  else's estate.
- **Filtering lives in the `idp.components` IQE adapter**, the single fact
  source behind every IDP read path — the scorecard universe, each rule's
  query, the evidence fact rows, the catalog, and the free-text IQE widget.
  Scoping the scorecard evaluator instead would have left
  `POST /idp/api/iqe-query` — arbitrary IQE over the same collection — serving
  the whole estate.
- **Failures fail closed.** Once a tenant is known, a failure to compute its
  scope yields the empty set, never the full estate.

What this card did **not** change: the isolation mechanism. Scoping is app-layer
row filtering, layered on an app-layer isolation boundary. It reduces what a
correctly-routed request returns; it does not add an independent boundary
underneath.

---

## 4. Options considered

**A. App-layer scoping only — implemented, pending approval for external use.**
The mechanism the rest of the platform already uses. Costs nothing further and
is sufficient for the internal reading, where every caller is ICDEV and the
blast radius of a scoping error is a wrong number on a dashboard. For an
external tenant the same error is a disclosure, and there is no second boundary
to catch it.

**B. PostgreSQL-native RLS as defense-in-depth.** Implement spike-crx-db-01 for
the IDP tables specifically: enable RLS, add per-tenant policies, and run the
application under a least-privilege role so isolation holds independently of
the connection path. Highest assurance, and the spike already recommends it.
Bounded here to a handful of tables rather than 300+, so the usual blast-radius
objection is much weaker for this surface than for the platform-wide change.

**C. Physical separation.** Per-tenant databases (`data/tenants/{slug}.db`)
already exist as a pattern. Strongest isolation, but it forfeits the
cross-tenant fleet reporting the scorecard history was built to support, and
duplicates the component registry per tenant.

**D. Do not offer the surface externally.** Keep the IDP internal. Zero risk,
zero value on the outward half of the card.

---

## 5. Recommendation

**Option A for the internal reading — in force now.** The platform's own
scorecard runs over the full estate under `tenant_id IS NULL`, and nothing about
that reading is externally exposed.

**Option B before the surface is offered to an external tenant.** The
recommendation is *not* that app-layer scoping is inadequate in general — it is
the platform's standard and it is what every other surface relies on. It is that
this specific surface changes the consequence of a scoping bug from a wrong
number to a cross-tenant disclosure, while the two annotated `rls-bypass`
engines in §2 sit directly upstream of the data being scoped. A second boundary
under the IDP tables is cheap relative to that, and the spike has already done
the design work.

Until §6 is signed, the portal and every tenant-scoped API payload carry the
notice from `args/idp_tenancy.yaml`, so the surface states its own posture
rather than leaving a reader to assume it.

---

## 6. Decision

**Unsigned.** This is the judgement call the card reserves for a human; it is
not a default to inherit.

To record a decision, set the fields in
[`args/idp_tenancy.yaml`](../../args/idp_tenancy.yaml) and fill this section in:

| Field | Value |
|-------|-------|
| Decision | _(A — accept app-layer-only for external use / B — require PG-native RLS first / C / D)_ |
| Approved by | _(name and role)_ |
| Approved on | _(YYYY-MM-DD)_ |
| Residual risk accepted | _(what the signer is accepting)_ |
| Re-review trigger | _(e.g. first external tenant onboarded, or crx-db-01 implemented)_ |

`external_offering_approved: true` requires a non-empty `approved_by`;
`tools/idp/tenancy.py::posture()` treats approval without a named approver as
unapproved, so this table and the config cannot drift apart silently.

---

## 7. Verification

```bash
# Posture as the code reads it
python -c "from tools.idp.tenancy import posture; print(posture())"

# What one tenant's scorecard covers vs the platform's
python tools/idp/scorecard.py --tenant acme --json
python tools/idp/scorecard.py --json

# Tenant-scoped history reads
python tools/idp/score_history.py --trend <component> --tenant acme --json
```

Tests: `tests/test_idp_tenancy.py`.

---

## References

- [spike-crx-db-01 — PostgreSQL Native RLS](../reference/spike-crx-db-01-pg-native-rls.md)
- [Enterprise Configurable Platform](../features/enterprise-configurable-platform.md) — `tenant_component_overrides`, `is_enabled_for_tenant`
- [`args/idp_tenancy.yaml`](../../args/idp_tenancy.yaml) — machine-readable half of this record
- [`tools/idp/tenancy.py`](../../tools/idp/tenancy.py) — scope resolution and posture reader
