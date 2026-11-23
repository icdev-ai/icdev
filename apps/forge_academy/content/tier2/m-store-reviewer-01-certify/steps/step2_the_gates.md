---
ontology_id: icdev:mission:m-store-reviewer-01-certify:step:2
step_class: icdev:Lesson
---

# The Gates Run Here

You will learn which checks run on a package before you may certify it, and why a check that cannot run never passes.

> Facts in this lesson are current as of November 2026.

## Seven scanner gates, re-run

ICDEV[IT]'s seven scanner gates run again **in ICDEV[OS]**, on the package itself: SAST, secret detection, dependency audit, CUI marking validation, SBOM generation, supply-chain provenance and the digital signature. The publisher's own results are only shown.

ICDEV[OS] adds two of its own:
- **red team current**: the report answers every OWASP Agentic and LLM Top 10 id, and none is in the future. It is the publisher's attestation, not a re-run;
- **declared scope**: the ceilings, tools and budget are ones the store allows, and no package claims a ceiling that needs an accreditation.

## Fail closed

A gate that cannot be evaluated **blocks**. A dependency audit with no KEV file to check against is unverified, not passed. A package swapped after import blocks every gate, because every gate reads the package the item holds.

## Reading a red-team report

Failed and unverified ids are named. Only the ids the report actually passed count as passed.
