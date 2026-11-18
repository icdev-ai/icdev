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

## The FOCUS cost bundle (DRAFT, osx-cop-05)

`focus-cost.schema.json` is ICDEV[OS]'s `icos-focus-cost/v1` bundle schema (`urn:icdev-os:contract:focus-cost:1`),
copied verbatim from ICDEV[OS]'s published contract (`contracts/schemas/focus-cost.schema.json`, contract version
1.195.0). It is a **DRAFT**: ICDEV[OS] has not merged that contract yet, and this copy is re-pinned when it does. It
is licensed Apache-2.0 (`LICENSE`, `NOTICE` here), unlike the rest of this repository. Its rows are the FOCUS 1.4
Cost and Usage columns (`$defs/FocusRow`); a bundle carries the rows, each declared workload's total and the
unattributed rows, its pin, its `content_digest` and, once a person published it, its `publication`.

It is pinned twice, and `tools/finops/focus_bundle.py` refuses to run if either does not match:

* the file's SHA-256, `focus_bundle.SCHEMA_SHA256`:
  `f79e7a232253856fbf6c504685dd19f3cfda4811ce80c376a68e2278c7b05af9`;
* its **schema digest**, `focus_bundle.SCHEMA_DIGEST`, which every bundle carries as `pin.sha256` and ICDEV[OS]
  compares with its own: SHA-256 of the canonical JSON (keys sorted, separators `,` `:`) of the schema without its
  top-level `x-icos-contract` version stamp:
  `09a2ea740fcbcd9107b79a0665a48728cc61370911fb5bb2647683421ae6ec58`.

To take a newer contract: copy the new schema, update both digests in `focus_bundle.py` and this file, and run
`tests/test_osx_cop_05_finops.py` (it recomputes both).
