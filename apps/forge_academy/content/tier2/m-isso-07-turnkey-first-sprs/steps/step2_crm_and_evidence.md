---
ontology_id: icdev:mission:m-isso-07-turnkey-first-sprs:step:2
step_class: icdev:Lesson
---

# Start the CRM from the Template, Then Bring Evidence

You will learn where your system's CRM rows for the platform come from, why none of them meets an objective by itself, and how collected evidence reaches your system.

> Facts in this lesson are current as of November 2026.

## ICDEV[OS] is your external service provider

Your system runs on ICDEV[OS], which processes and stores your CUI and security protection data. To your system it is an **external service provider**. `GET /api/v1/tenants/crm-template` (also on the **Tenants** page) gives the matrix your record starts from, derived from the platform's own register as system `icos`:

- a control the platform states as **implemented** or **partial** is **shared** with `icos-platform`, citing the platform's evidence;
- anything else is **yours**, or another provider's that you name.

No row is ever the provider's alone, because the platform's statements are its own reading and no third party has checked them. You add the template's `icos-platform` provider and its shared rows to your record.

## A CRM row is a claim

A CRM row meets no objective. The control backbone resolves your objectives only from **your own** imported evidence reports. An objective with no evidence is unverified.

## Evidence from the collectors

ICDEV[IT]'s evidence collectors read your cloud configuration, identity provider, endpoints and SIEM, read-only. Credentials stay in your vault, named by reference. One person of your organisation prepares a report on `/evidence-collectors/`, and **another** person publishes it. Only a published report is exported, as `icos-objective-evidence/v1`, and imported on the **Controls** page.

The import is refused if the report is unpublished, self-published, or not your system's. A report past its freshness budget counts for nothing, so stale evidence never props up a score.
