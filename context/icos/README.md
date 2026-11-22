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

## The customer artefact (icx-ent-03; ICDEV[OS] osx-ent-06)

`artefact.schema.json` is ICDEV[OS]'s `icos-artefact/v1` manifest schema (`contracts/schemas/artefact.schema.json`,
contract version 1.208.0, SHA-256 `cdc80df7a7565c23aa005342f6b615eda249788bf6de946f41487ef1458a04aa`), copied verbatim
from ICDEV[OS] main (commit 9040bf4c, osx-ent-06 merged). One signed customer artefact (an image, a model, an agent or an IaC bundle) bound to one
registered ICDEV[OS] system, listing every file of the artefact with its SHA-256; the artefact digest is the SHA-256
of `artefact.json`. It is licensed Apache-2.0 (`LICENSE`, `NOTICE` here), unlike the rest of this repository.

`build-publication.schema.json` is a **DRAFT** by ICDEV[IT] (`icdev-build-publication/v1`,
`urn:icdev-it:contract:build-publication:1:draft`, SHA-256
`18e0056f111d0ac03d9de8cb7100fed8c46844d6c33d6e4b78f7a8873c834985`) for ICDEV[OS] to adopt, in the shape of the
findings publication: a person who did not prepare the build took `decl-wft-fb-build-publish`, bound to the artefact
digest (`sha256:` form, the digest ICDEV[OS]'s admission recomputes), with the ledger event and its row hash. The
artefact travels as an `icos-release-transfer/v1` document with the publication BESIDE it
(`{"bundle": ..., "publication": ...}`), exactly what `icdev_os/release/admission.py` reads through the one shared import
(`icdev_os/publication.py` `split(raw, "beside")`, `check_publication`). ICDEV[OS] pins the step by setting
`artefacts.icdev.publication_template: decl-wft-fb-build-publish` in its `args/artefacts.yaml`.

The CycloneDX 1.7 and OpenVEX 0.2.0 JSON schemas the factory validates against are vendored verbatim, with their pins,
from ICDEV[OS] main under `context/standards/` (Apache-2.0 and CC0-1.0, each with its SOURCE.yaml).

`tools/factory_builds/build.py` validates every manifest, SBOM and VEX against them and refuses to run if a file's
digest is not its pin (`constants.SCHEMA_SHA256`, `constants.PUBLICATION_SHA256`, each SOURCE.yaml). To take a newer
contract: copy the schema, update the digest in `tools/factory_builds/constants.py` and this file, and run
`tests/test_icx_ent_03_factory_builds.py`.

