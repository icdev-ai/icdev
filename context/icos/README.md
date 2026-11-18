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

## The conformance bundle (DRAFT, icx-coe-03)

`conformance.schema.json` is the `icos-conformance/v1` bundle schema (`urn:icdev-os:contract:conformance:1`), one
bundle per repo of the ecosystem (ICDEV[IT], ICDEV[OS], ICDEV[FT]): the engineering standards and their status, the
scorecard results with evidence, the exemptions with expiry, the delivery metrics (DORA four keys and AI-assisted
rework, each with its window, sample size and source digests), the linked ADRs, and the producer, version and time.
It is a DRAFT, written here in the style of `apptwin.schema.json` and licensed the same way: ICDEV[OS] adopts it into
its published contract in osx-coe-01, and then this copy is replaced by the vendored one and re-pinned.

`tools/idp/conformance.py` validates every bundle against it and refuses to run if the file's digest is not the one
pinned in `conformance.SCHEMA_SHA256`. `content_digest` is SHA-256 over the canonical JSON of the bundle without
`content_digest`, `publication` and `produced_at`; `publication` is a person's declared step
(`decl-wft-coe-conformance-publish`) bound to it. To change the draft: edit it, update the digest in
`conformance.SCHEMA_SHA256`, and run `tests/test_icx_coe_03_conformance.py`.
