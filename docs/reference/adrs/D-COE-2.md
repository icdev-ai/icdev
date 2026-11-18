---
id: D-COE-2
title: Exemptions always expire and are granted by a person's declared step; ADRs are signed, versioned records and the flat ADR log is generated from them
version: '1'
status: proposed
section: 'Phase 83 — COE (Engineering Centre of Excellence: the standards register)'
format: structured
supersedes: []
superseded_by: ''
links:
  requirements: [PM-COE-06, PM-COE-07, PM-COE-08, PM-COE-09, PM-COE-10]
  standards: [ENG-E2E-SPEC, ENG-FIVE-RULES]
context: >-
  icx-coe-02, the second card of the Engineering CoE programme. A scorecard exemption
  (tools/idp/exemptions.py) waived one rule for one component and could run for ever: the
  expiry was optional, a scorecard could auto-approve, the requester could approve their
  own request, and the log had no trigger and no chain. docs/reference/adrs.md held about
  675 decisions in one flat file that every branch appended to by hand, with no status,
  no author, no link to the requirements or standards a decision serves, and no way to
  supersede one except by prose.
consequences: >-
  An exemption past its expiry FAILS the scorecard (exemptions.gate fail, and the rule
  fails for that component with the lapse named) and the CI step python
  tools/idp/exemptions.py --check --gate, until a person renews or revokes it; a waiver in
  scorecard YAML or an autoApprove switch fails the same check. Approved exemptions with no
  expiry were given one, 30 days out, by bootstrap:icx-coe-02-exemption-migration (a
  migrated row, never a grant), and the twin reports each as coe.exemption.migrated for the
  owner. An ADR is added by writing its record (or proposing it on /idp/adrs and exporting
  it), never by editing adrs.md, and CI fails on a drifted doc (python tools/idp/adrs.py
  --check); every concurrent branch that appends to adrs.md must move its text into a
  record. The 676 migrated ADRs are accepted by a labelled bootstrap identity, which is
  never a person's signature; superseding one is a person's step.
recorded:
  by: agent:claude-icx-coe-02
  kind: agent
  step_id: ''
  note: drafted by the card's builder; a person who is not its author accepts it on /idp/adrs
---
(1) An exemption names a scorecard and one scope (rule: a rule on a component; service: every rule on a component; standard: the rule an engineering standard's check names; repo: a rule on every component of a repo), links to the standards it waives (derived from the register, and checked against it), and always expires: a request with no expiry, one not after today, or one beyond max_expiry_days (args/coe_records.yaml) is refused. (2) Granting and renewing are a person's declared steps (decl-wft-coe-exemption-grant, decl-wft-coe-exemption-renew), with a reason, bound to the exemption's digest, separate_from builders so the requester (and whoever asked for the renewal) never decides; there is no automatic renewal and no timer that decides, and expiry only removes the exemption. Revoking is a stop: immediate, with a stated reason. (3) idp_rule_exemptions gains its scope, digest, step, actor and a hash chain (UNIQUE(exemption_key, prev_hash)) by adding columns, never changing them, and gets append-only triggers on SQLite and PostgreSQL; rows from before the chain are reported as legacy, never verified. (4) ADRs become one record per ADR under docs/reference/adrs/ (YAML front matter: id, title, version, status, section, supersedes, superseded_by, links to platform-model requirements and engineering standards, context, consequences, who recorded it; the decision as the body) plus _layout.md for the doc's prose, and docs/reference/adrs.md is generated from them. The migration is lossless and proved by regenerating the flat doc as it stood (tests/fixtures/adrs_flat_20261117.md) byte for byte; only a one-line generated banner is added. (5) ADR status is the replay of the append-only, hash-chained coe_adr_events; propose (anyone, or an agent's draft), accept or return (decl-wft-coe-adr-accept) and supersede or deprecate (decl-wft-coe-adr-supersede) are a person's steps, never the author's. Rejected: status as a field anyone edits in the record (an edit is not a decision; the record carries the export of the ledger's state and the step that decided it); one YAML file holding every ADR (the same merge-collision surface the flat doc was); inferring superseded status for old ADRs from their prose (a judgement for a person, not a migration).
