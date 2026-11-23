---
ontology_id: icdev:mission:m-store-reviewer-01-certify:step:3
step_class: icdev:Lesson
---

# Certify, Install, Revoke

You will learn who may certify a package, what certifying does and does not do, and how revocation works.

> Facts in this lesson are current as of November 2026.

## Who certifies

The `store-review` workflow's certify step is signed with a **passkey** by a reviewer who:

- did not import it;
- is none of the publisher's people, even one holding an ICDEV[OS] account with the reviewer's duty;
- is of the catalogue's organisation.

No agent certifies, and no model judges a package.

## Certifying is not installing

A certified package installs for a tenant only by a `config-change` of that tenant's own installs file. The tenant's own system owner approves it with a passkey, and a merge request adopts the signed bytes. An install never exceeds what its review certified.

## Revocation is immediate

Revoking a package needs no signature. Every install of it is refused at its next call, and the twin shows the install as a finding until it is removed. Lowering trust never waits; raising it always does.
