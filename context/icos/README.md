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

## The Academy's xAPI batch and its publication (icx-cop-01; ICDEV[OS] drafts, osx-cop-01 and osx-cop-07)

`cop-academy-statements.schema.json` is ICDEV[OS]'s `icos-cop-academy/v1` batch schema
(`urn:icdev-os:contract:cop-academy-statements:1`), copied verbatim from ICDEV[OS]'s osx-cop-07 work as DRAFTED there
(`contracts/schemas/cop-academy-statements.schema.json`, contract version 1.195.0, not yet merged on ICDEV[OS]'s main
when this was vendored), SHA-256 `89966bdae51a33aef68347b539e43b6b33a3dad8e72c8b8560e1842824729028`. One batch of xAPI
statements from the FORGE Academy's Community of Practice tracks: the producer and the time, the learning paths it
reports on, the statements (a pseudonymous `account` actor, an ADL verb, the path's activity id
`urn:icdev:academy:path:<path id>`, `result.completion`/`success`, the timestamp) and `content_digest`, SHA-256 over the
canonical JSON of the batch without `content_digest` (ICDEV[OS] recomputes it). It is licensed Apache-2.0 (`LICENSE`,
`NOTICE` here), unlike the rest of this repository.

`cop-academy-publication.schema.json` is ICDEV[OS]'s `icos-cop-academy-publication/v1` schema
(`urn:icdev-os:contract:cop-academy-publication:1`), copied verbatim from ICDEV[OS]'s osx-cop-07 work as DRAFTED there
(contract version 1.195.0, not yet merged), SHA-256 `9fef09ec0630f0ffbac52f14ae24bd7dc698cb84ceed34a17eb0c094ed08654b`.
ICDEV[IT] proposed it here (icx-cop-01, as `icdev-cop-academy-publication/v1`); ICDEV[OS] adopted it with the same
fields and constraints under its own name. The batch schema admits no extra field, so a batch's publication (a person
who did not prepare it took `decl-wft-cop-academy-xapi-publish`, bound to the batch's `content_digest`) travels BESIDE
it, as `icos-cop-academy.publication.json` next to `icos-cop-academy.json`
(`tools/idp/cop_xapi_export.py --published --out <dir>`). ICDEV[OS] imports the pair and refuses a batch with no
publication, a publication at another digest, one signed by its preparer, and one that fails this schema.

`tools/idp/cop_tracks.py` validates every batch against the first and every publication against the second, and refuses
to run if either file's digest is not the one pinned (`cop_tracks.SCHEMA_SHA256`, `cop_tracks.PUBLICATION_SHA256`).
When ICDEV[OS] publishes its contract: copy the schemas it publishes, update the digests in `cop_tracks.SCHEMA_SHA256`,
`cop_tracks.PUBLICATION_SHA256`, `cop_tracks.SCHEMA_CONTRACT` and this file, and run `tests/test_icx_cop_01_tracks.py`.
