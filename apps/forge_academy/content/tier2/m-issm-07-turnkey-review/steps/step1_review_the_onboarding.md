---
ontology_id: icdev:mission:m-issm-07-turnkey-review:step:1
step_class: icdev:Lesson
---

# Review a Tenant's Onboarding

You will learn what an ISSM of the platform checks when a new tenant is onboarded, and why the review is a separate person's step.

> Facts in this lesson are current as of November 2026.

## The workflow

`tenant-onboarding` has three people:

1. a **platform admin** drafts the record;
2. an **ISSM of the platform's own organisation** reviews it;
3. **another** platform admin signs it with a passkey.

Every step is bound to the platform's organisation, and the server records that org when the item is created. It cannot be edited afterwards. The drafter can never sign, and neither can you.

## What you check

The gates check the record mechanically. You check that it is right:

- **the ceiling**: the highest marking the tenant's systems may carry. The twin flags any of its systems registered above it;
- **the identity**: ICDEV[OS] is a relying party of ONE identity provider. `oidc` and `pki` must name the site's own provider and an acr it requires, for a second factor or for a PKI card. `local` is ICDEV[OS]'s own MFA sign-in, and only where the site still allows it. A tenant that needs another provider gets its own release, in its own namespace;
- **the isolation**: `org` shares this install, held to its organisation by the one reader rule; `namespace` means nothing of the tenant lives here;
- **synthetic**: a demo or test tenant must say so.

## What signing does not do

Onboarding grants nobody anything. Each of the tenant's people is still created, and given the tenant's org and duties, by an admin's signed access change.
