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

## The store package and its publication (icx-ent-04; ICDEV[OS] osx-ent-11)

`store-package.schema.json` is ICDEV[OS]'s `icos-store-package/v1` schema (`urn:icdev-os:contract:store-package:1`) and
`store-publication.schema.json` its `icos-store-publication/v1` (`urn:icdev-os:contract:store-publication:1`), copied
verbatim from the ICDEV[OS] published contract on its main (commit ba4e0a56, contract version 1.207.0; SHA-256
`07503b4e5468427b36c9582ccf6af6a3f555e74e3c966bbc8900231007293efa` and
`2ecf86c73ec41cff5a57707f71a2a67b3d52f97115ffa86bcfb9055096ae4795`). A store package is an agent, crew, skill, MCP
server or plugin offered to ICDEV[OS]'s agent store with its JWS-signed A2A Agent Card that binds the package's content
digest (`metadata["icos.store/package_digest"]`); the publication beside it (`{"bundle": ..., "publication": ...}`)
says a person who did not prepare it took the declared step `icos-store-publish` at its content digest, which is what
ICDEV[OS]'s one shared bundle import (`icdev_os/publication.py`) checks. They are licensed Apache-2.0 (`LICENSE`,
`NOTICE` here), unlike the rest of this repository.

`tools/store_publisher/package.py` validates every package against the first and `service.py` every publication
against the second, and each refuses to run if its file's digest is not the pin in `tools/store_publisher/constants.py`
(`SCHEMA_SHA256`, `PUBLICATION_SHA256`). To take a newer contract: copy the schema, update the digest in
`constants.py` and this file, and run `tests/test_icx_ent_04_store_publisher.py`.

