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

