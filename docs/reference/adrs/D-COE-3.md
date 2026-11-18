---
id: D-COE-3
title: Repos are scored only against adopted standards that apply to them, delivery evidence is computed from declared sources with no data unverified, and a repo's conformance bundle leaves for ICDEV[OS] only when a person who did not prepare it publishes it
version: '1'
status: proposed
section: 'Phase 83 — COE (Engineering Centre of Excellence: the standards register)'
format: structured
supersedes: []
superseded_by: ''
links:
  requirements: [PM-COE-11, PM-COE-12, PM-COE-13, PM-COE-14, PM-COE-15]
  standards: [ENG-PAGE-COMPLETENESS, ENG-E2E-SPEC, ENG-FIVE-RULES]
context: >-
  icx-coe-03, the third card of the Engineering CoE programme. icx-coe-01 gave ICDEV a
  standards register with a lifecycle and icx-coe-02 exemptions that expire and ADRs as
  records, but nothing scored a repo against the standards in force, the DORA view on /sre
  read one database's tables for ICDEV[IT] alone, and ICDEV[OS] (osx-coe-01) needs one
  bundle per repo of the ecosystem -- ICDEV[IT], ICDEV[OS] and ICDEV[FT] -- against a
  contract it will own.
consequences: >-
  Only a standard in force that a repo's declaration names is scored; ICDEV[FT] has none
  declared yet, ICDEV[OS] only its two proposed ICOS-* references, so their scorecards list
  every standard as not applicable or not scored until the owner extends
  args/coe_conformance.yaml. A manual evidence step is never a pass, so ICDEV[IT]'s five
  manual standards always show needs_evidence. Change failure rate and time to restore are
  unverified for any repo without a CI-run snapshot passed in. The draft schema
  context/icos/conformance.schema.json is pinned by SHA-256 in tools/idp/conformance.py and
  is re-pinned when osx-coe-01 adopts it into the ICOS contract. A bundle is a draft until a
  person publishes it; the twin reports every repo with no published bundle as unverified.
recorded:
  by: agent:claude-icx-coe-03
  kind: agent
  step_id: ''
  note: drafted by the card's builder; a person who is not its author accepts it on /idp/adrs
---
(1) Scorecards are per repo (args/coe_conformance.yaml: it, os, ft) against the standards register: every standard is listed, and only one IN FORCE that the repo's declaration names (ids or prefixes) is scored. A scorecard-rule check is graded by machine with the existing IDP scorecard (tools/idp/scorecard.py) and the live exemption log applied; it passes only when every service its rule applies to passes or is waived by a live exemption, and an EXPIRED exemption fails it, named. A manual evidence step is needs_evidence, never pass; a standard not in force is not_scored; a check that measured nothing is unverified. (2) Delivery evidence is the DORA four keys plus the rework rate of AI-assisted work, computed purely (tools/idp/dora.py) from three declared sources: the git history of main (a merge on the first-parent line is a deployment; trunk-based delivery), a CI-run snapshot passed in (an outcome is the latest completed run on main for the merge's sha), and a board export (follow-up cards). Lead time is first authored commit to merge, the median; a failure is a failed main run or a revert within failure_window_days; time to restore is to the next good deployment or the revert; rework is a revert, a later merge carrying the same card id, or a board card depending on it, within rework_days, counted only for deployments at least rework_days old. AI-assisted means a Co-Authored-By trailer at a no-reply address. Each metric carries its window, sample size and source digests; a metric with no data is unverified, never zero. Outcomes, not adoption counts. (3) One icos-conformance/v1 bundle per repo carries the standards and their status, the scorecard results with evidence, the exemptions with expiry, the delivery metrics, the linked ADRs and the producer, version and time; content_digest is SHA-256 over its canonical JSON without content_digest, publication and produced_at, so ICDEV[OS] recomputes it and the export is deterministic given its inputs. The schema is drafted here in the style of the apptwin contract and adopted into the ICOS contract by osx-coe-01. (4) Publishing is a person's declared step (decl-wft-coe-conformance-publish): anyone or an agent prepares a bundle, recorded at its digest in the append-only, hash-chained coe_conformance_events; a person who did not prepare it publishes or refuses, with a reason, bound to that digest, the way an apptwin bundle carries an IV&V only when an independent person signed it at its digest. Rejected: scoring every standard against every repo (ICDEV[IT]'s scorecard rules grade ICDEV[IT]'s components only, and which standards bind another repo is the owner's decision); reading CI or the board live inside a test or a page (a live dependency, and the board is the owner's); a cryptographic key held by the exporter (a key is not a person's decision, and ICDEV's signed decisions are its declared steps); counting tool adoption as delivery evidence.
