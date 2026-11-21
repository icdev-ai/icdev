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

## The findings bundle (icx-ent-01; adopted by ICDEV[OS] in osx-ent-07)

`findings.schema.json` is ICDEV[OS]'s `icos-findings/v1` bundle schema (`urn:icdev-os:contract:findings:1`), copied
verbatim from the ICDEV[OS] published contract (`contracts/schemas/findings.schema.json`, contract version 1.201.0,
SHA-256 `555fbdff56e74f27e60981c2275e5ba2cae60351e4d55e5d509ff703a7481697`). One bundle per vulnerability-discovery
target: the target at its tree digest (and, optionally, the ICDEV[OS] registered system it belongs to) and the person's
authorisation to test it, the newest discovery run and how it was isolated, every CONFIRMED finding with the digest and
replay outcomes of its sealed proof of vulnerability (never the PoV itself), its patch proposal and review, the dropped
candidates counted by reason, the dependency advisories carried as unproven with their KEV flag, and the pipeline's
measured false-positive and false-negative rates. ICDEV[IT] drafted it in icx-ent-01; ICDEV[OS] adopted it with three
differences from that draft: the run's isolation carries no `waiver` (the waiver stays in ICDEV[IT]'s run record and its
twin), the measurement carries no `status` or `why` (an unverified measurement reaches ICDEV[OS] as null rates), and
`target.system_id` is ICDEV[OS]'s system id (`^[a-z][a-z0-9-]{1,62}$`, optional). It is licensed Apache-2.0 (`LICENSE`,
`NOTICE` here), unlike the rest of this repository.

`findings-publication.schema.json` is ICDEV[OS]'s `icos-findings-publication/v1` (`urn:icdev-os:contract:findings-publication:1`,
contract version 1.201.0, SHA-256 `9f86fdc8656c67e57550c6a3a97b1e6af56afd6b1738680917e52747b456d8d9`), its publication, in
exactly the shape of the Academy publication (`icdev-cop-academy-publication/v1`, icx-cop-01): a person who did not prepare
the bundle took `decl-wft-vr-findings-publish`, bound to its `content_digest`, with the ledger event and its row hash.
The bundle admits no extra field, so the publication travels BESIDE it (`icos-findings.json` +
`icos-findings.publication.json`), and ICDEV[OS] refuses a bundle nobody published, one published by its preparer or by
an agent, or one whose digest does not recompute. ICDEV[IT] writes `icos-findings-publication/v1`; ICDEV[OS] also
still accepts the draft's name, `icdev-findings-publication/v1`.

`tools/vulnresearch/bundle.py` validates every bundle and publication against them and refuses to run if a file's
digest is not the one pinned in `bundle.SCHEMA_SHA256` / `bundle.PUBLICATION_SHA256`. `content_digest` is SHA-256
over the canonical JSON of the bundle without `content_digest` and `produced_at`. To take a newer contract: copy both
schemas, update the digests and `bundle.SCHEMA_CONTRACT` and this file, and run `tests/test_icx_ent_01_vulnresearch.py`.
