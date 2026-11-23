---
ontology_id: icdev:mission:m-isso-07-turnkey-first-sprs:step:1
step_class: icdev:Lesson
---

# Register the Customer System

You will learn how an ISSO turns a customer's environment into a registered system under assessment in ICDEV[OS]: what the record must say, who signs it, and why nothing counts until it is signed.

> Facts in this lesson are current as of November 2026. CMMC rules are cited from 32 CFR Part 170; check the eCFR for the text in force before you rely on a detail.

## The tenant comes first

Your organisation is a **tenant** of ICDEV[OS]. A platform admin drafted its onboarding record, an ISSM of the platform reviewed it, and another admin signed it with a passkey. The record sets your **ceiling**, the highest marking any of your systems may carry, and how your people sign in. Onboarding grants nobody anything: you were given your org and your ISSO duty by an admin's own signed access change.

## What the record says

On the **Systems** page you draft an `icos-system/v1` record:

- the **boundary**: components, the interfaces between them, and the data flows, each saying whether it carries CUI;
- the **CUI scope**: every component's CMMC asset category from 32 CFR 170.19 (CUI Assets, Security Protection Assets, Contractor Risk Managed Assets, Specialized Assets, Out-of-Scope Assets). An out-of-scope component needs a justification, and a CUI flow to it is refused;
- the **external providers**: each cloud or external service provider, what it handles, and which controls it leaves for you to inherit;
- the **customer responsibility matrix (CRM)**: per control, yours, the provider's, or shared;
- the **people in each duty**: system owner, ISSO, ISSM, authorizing official and senior affirming official. Two duties that must be separate cannot name the same person.

## Who signs

You draft. The ISSM reviews. The **system owner** signs with a passkey over the item's exact digest, which holds the record. Only that step registers the system, and only someone of your organisation can take it. A record changed after review blocks the signature until it is reviewed again.

## Why it matters

The registered record is the subject of everything after it: the control backbone resolves objectives for this system id, the CMMC assessment scores it, and the twin observes it. A component nobody reports is **unverified**, never passed.
