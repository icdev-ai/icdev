# ICDEV[OS] application-twin bundle contract (vendored)

`apptwin.schema.json` is ICDEV[OS]'s `icos-apptwin/v1` bundle schema (`urn:icdev-os:contract:apptwin:1`), copied
verbatim from the ICDEV[OS] published contract (`contracts/schemas/apptwin.schema.json`, contract version 1.192.0,
SHA-256 `e30b9e42e8d02c8d1137c65bc22b4a7cc009e28f5665fa889e5de1a87c49ce5e`; 1.186.0-1.189.0 add a feature's
`provenance: both` and capability hints, a process's source, signature and review, a step's verb and object, the
bundle's `sections` map and `derived_waves`, a strategy's confidence and unmeasured criteria, and a wave's
`estimated_hours`; the application's `environment` carries its security domain; 1.190.0 adds a plan's `schedule`,
a wave's `depends_on` and `undated_reason`, and an IV&V's `area_status`, `findings`, `evidence`, `tool_version` and
`signoff`; 1.191.0 and 1.192.0 change nothing in this schema but its version). It is licensed Apache-2.0
(`LICENSE`, `NOTICE` here), unlike the rest of this repository.

`tools/modernization/apptwin_export.py` validates every bundle it writes against it and refuses to run if the
file's digest is not the pinned one. To take a newer contract: copy the new schema, update the digest in
`apptwin_export.SCHEMA_SHA256` and this file, and run `tests/test_icx_apptwin_export.py`.

## The conformance bundle (icx-coe-03; adopted by ICDEV[OS] in osx-coe-01)

`conformance.schema.json` is ICDEV[OS]'s `icos-conformance/v1` bundle schema (`urn:icdev-os:contract:conformance:1`), copied
verbatim from the ICDEV[OS] published contract (`contracts/schemas/conformance.schema.json`, contract version 1.193.0,
SHA-256 `e90251a5def88fd538ca5e465f0073a0bb0db9d2c3fee5395863000b768210e2`). ICDEV[IT] drafted it in icx-coe-03; ICDEV[OS]
adopted it byte for byte but two lines: its `description` says ICDEV[OS] owns it (no DRAFT note), and `x-icos-contract` is
the contract version, not `draft`. One bundle per repo of the ecosystem (ICDEV[IT], ICDEV[OS], ICDEV[FT]): the engineering
standards and their status, the scorecard results with evidence, the exemptions with expiry, the delivery metrics (DORA four
keys and AI-assisted rework, each with its window, sample size and source digests), the linked ADRs, and the producer,
version and time. It is licensed Apache-2.0 (`LICENSE`, `NOTICE` here), unlike the rest of this repository.

`tools/idp/conformance.py` validates every bundle against it and refuses to run if the file's digest is not the one pinned
in `conformance.SCHEMA_SHA256`. `content_digest` is SHA-256 over the canonical JSON of the bundle without `content_digest`,
`publication` and `produced_at`; `publication` is a person's declared step (`decl-wft-coe-conformance-publish`) bound to
it. ICDEV[OS] recomputes the digest on import and refuses a mismatch, a bundle nobody published, and one published by its
preparer. To take a newer contract: copy the new schema, update the digest in `conformance.SCHEMA_SHA256`,
`conformance.SCHEMA_CONTRACT` and this file, and run `tests/test_icx_coe_03_conformance.py`.

## The objective-evidence report (icx-ent-02; ICDEV[OS] osx-ent-02)

`objective-evidence.schema.json` is ICDEV[OS]'s `icos-objective-evidence/v1` schema
(`urn:icdev-os:contract:objective-evidence:1`), copied verbatim from ICDEV[OS] `main` after
osx-ent-02 merged (merge cfe0b91e, `contracts/schemas/objective-evidence.schema.json`, contract version 1.199.0, SHA-256
`adea74c3cc1e384f7fae9250098e6460c7966467e1ebc48b75bc651273c563d7`). It is NOT `icos-evidence/v1`, which is ICDEV[OS]'s
own CI evidence bundle. It is licensed Apache-2.0 (`LICENSE`,
`NOTICE` here), unlike the rest of this repository.

`tools/evidence_collectors/bundle.py` validates every report against it and refuses to run if the file's digest is not
the one pinned in `tools/evidence_collectors/constants.py` (`SCHEMA_SHA256`, `SCHEMA_CONTRACT`). `content_digest` is
ICDEV[OS]'s rule: SHA-256 over the canonical JSON of the report without `content_digest` and `publication`. The
publication is EMBEDDED in the report with exactly the contract's five fields (prepared_by, signed_by, step_id,
decided_at, content_digest) -- the placement ICDEV[OS]'s `icdev_os/publication.py` reads with `split(raw, "embedded")`
and checks with `check_publication` (refusing `unpublished`, `tampered`, a publication by its preparer or by an agent).
To take a newer contract: copy the new schema, update the digest and contract version in `constants.py` and this file,
and run `tests/test_icx_ent_02_evidence_collectors.py`.
