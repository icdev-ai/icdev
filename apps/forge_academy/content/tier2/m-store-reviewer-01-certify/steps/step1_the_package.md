---
ontology_id: icdev:mission:m-store-reviewer-01-certify:step:1
step_class: icdev:Lesson
---

# What a Store Package Is

You will learn what an agent-store package carries, and what of it is verified and what is only claimed.

> Facts in this lesson are current as of November 2026. The A2A Agent Card signature format is the A2A 1.0 specification's.

## The package

ICDEV[IT]'s store publisher prepares a signed `icos-store-package/v1` for an agent, a crew, a skill, an MCP server or a plugin. It carries:

- the **publisher identity**;
- a **JWS-signed A2A Agent Card**, whose signature binds the package's content digest, so it covers the whole package;
- an **SBOM** (or AI-BOM) and a dependency report;
- the **declared tools**, egress and marking ceilings and budget;
- the publisher's **red-team report**.

## Prepared by one person, published by another

One of the publisher's people prepares the package. A **different** person of the same organisation publishes it. ICDEV[OS] refuses a package that is unpublished or self-published, and refuses any agent-signed publication.

## Verified and claimed

On import, ICDEV[OS] verifies the Agent Card against the publisher's **pinned** key and compares its claims with what the package declares. What the card claims, and what the publisher's own scanner says, are shown as claims.
