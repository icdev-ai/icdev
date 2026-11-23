---
ontology_id: icdev:mission:m-assessor-01-objective-evidence:step:2
step_class: icdev:Lesson
---

# Freshness, Claims and Inherited Controls

You will learn how to read evidence that is stale, claimed or inherited, without letting any of it pass for more than it is.

> Facts in this lesson are current as of November 2026.

## Freshness

Every report has a freshness budget. A report past it counts for nothing. A timestamp that is empty, cannot be parsed or lies in the future is unverified, never compared as a string.

## Claims are labelled as claims

- A provider's FedRAMP authorisation in a system record is as stated: a claim.
- A CRM row is a claim. It meets no objective by itself; it tells you where to look.
- A publisher's own scanner results on a store package are shown, never trusted.

## Inherited, properly

An objective is inherited only when the system's own evidence says so, citing the provider's evidence. The platform's CRM template marks a control **shared** with `icos-platform` when the platform's register has evidence for it, and the customer's otherwise. It is never the platform's alone, because the platform's statements are its own reading and no third party has checked them. Read the shared row's evidence reference against the platform's register and its Trust centre.

## What you write

Write what you saw, objective by objective. An evidence gap is unverified. Do not round it up.
