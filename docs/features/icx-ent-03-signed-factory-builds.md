# Signed factory builds (icx-ent-03)

ICDEV[IT] builds; ICDEV[OS] admits. For each target in `args/factory_builds.yaml` the factory builds **one signed
`icos-artefact/v1`** that ICDEV[OS]'s release register (osx-ent-06) verifies with its one verifier and imports through
its one shared publication import. A build is evidence for that admission, never an approval: ICDEV[OS] admits, and
its people approve the release.

## What one build is

| File | What it is | Standard (checked 2026-11-21) |
|---|---|---|
| `artefact.json` | `icos-artefact/v1`, valid against ICDEV[OS]'s pinned schema; its SHA-256 is the artefact digest | ICDEV[OS] contract 1.205.0 (osx-ent-06 branch) |
| `sbom.cdx.json` | the dependencies ICDEV's own SBOM generator resolves (`sbom_generator.cyclonedx_document`) for software; for a model the AI-BOM below | [CycloneDX 1.7](https://github.com/CycloneDX/specification/tree/1.7/schema), schema vendored and pinned |
| `provenance.slsa.json` | an in-toto Statement v1: build type, external parameters (commit, version, target, kind, system), builder id, start/finish time, the SBOM as a byproduct | [SLSA v1.2 build provenance](https://slsa.dev/spec/v1.2/build-provenance); its predicate type is still `https://slsa.dev/provenance/v1` |
| `vex.openvex.json` | one statement per factory-scan finding; `under_investigation` unless the reviewed config states it | [OpenVEX 0.2.0](https://github.com/openvex/spec), schema vendored and pinned |
| `bundles/*.sigstore.json` | the signature, SBOM, provenance and VEX attestations, Sigstore bundle v0.3 DSSE, over the artefact's digest | [sigstore_bundle.proto](https://github.com/sigstore/protobuf-specs/blob/main/protos/sigstore_bundle.proto) |
| `bundles/model.sigstore.json` | a model's weights, OpenSSF model-signing (files, sha256, no symlinks) | [model-transparency](https://github.com/sigstore/model-transparency), as ICDEV[OS] osx-ent-06 verifies it |
| `oci/` | an OCI image layout with one referrer per bundle | OCI image-spec v1.1.1, as cosign v3 writes referrers |

**Checked, not assumed.** On 2026-11-21 the synthetic image and model builds were verified by ICDEV[OS] osx-ent-06's
own verifier (`artefact.verify_files`: 17 checks for the image, 19 for the model, all passing, model-signing and AI-BOM
included) and passed its shared publication import (`admission._publication`) with the template pinned; a
self-published copy was refused `bad_publication`. All four bundles of the image build were verified by **cosign
v3.1.4** (`verify-blob-attestation --key --insecure-ignore-tlog`; the binary checked against `cosign_checksums.txt`,
not a dependency), and refused under another key.

## The AI-BOM and the fine-tuning registry

A model is built only from the fine-tuning registry: the newest version of the target's `model` whose status is
`promoted` and whose newest `ft_promotion_log` row is a promotion. The AI-BOM's root component is the
`machine-learning-model` (its hash the model-signing root digest of its weights) with a `modelCard` (the dataset by
reference, the registry's own evaluation scores) and properties naming the model version, the training job and the
promotion row: who promoted it, for which function, when, and whether thresholds were bypassed (`override_promoted`).
A version of another tenant, or registry rows above the target's marking, are refused. Publishing re-checks the
promotion: a model demoted since it was built is `promotion_withdrawn`, and the twin reports a published one as a
finding.

## The key: KMS, the keyring, or keyless with a configured issuer

`tools/crypto/artefact_signer.py` is ICDEV[IT]'s side of ICDEV[OS]'s one machine-key story: the same spec syntax, the
same ECDSA P-256 / SHA-256, the same key id.
- `kms:<key>?region=<region>`: an `ECC_NIST_P256` key used for `ECDSA_SHA_256` through AWS KMS's FIPS endpoint.
- `keyring:<service>/<name>`: the OS keyring holds the PKCS#8 PEM.
- `file:` is a throwaway test key in the temporary directory only.
- **keyless**: an ephemeral key certified by a **configured** Fulcio (`POST /api/v2/signingCert`, proof of possession a
  signature over the token's `sub`; fulcio.proto) for a **configured** OIDC issuer. There is no default, so an
  air-gapped factory never reaches Sigstore's public instance by accident. The leaf must carry the ephemeral key and
  name the issuer (OID 1.3.6.1.4.1.57264.1.8). Bundles carry the leaf as `certificate`, the form v0.3 requires for
  keyless. **ICDEV[OS] does not admit a keyless build yet**: its register verifies key-based bundles against keys a
  system's owner pinned. The build record and the twin say so.

No key configured is refused (`no_key`), never a MAC or an unsigned build. Every signature is verified before it is
used, and every build verifies under its own key before it is recorded.

**The transparency log is off by default** and every build records `off`. With `transparency_log: {mode: rekor, url}`,
each bundle's envelope is submitted as a `dsse` 0.0.1 entry (`POST /api/v1/log/entries`, HTTP 201; Rekor
openapi.yaml) and the answer is kept in the bundle's `tlogEntries` and the build record, **as a receipt, not verified**:
no log key is pinned. Outbound calls are https (http only to a loopback literal, for tests), never follow a redirect,
cap the answer and hold one deadline over the whole exchange. Rekor's verifier encoding (base64 of the PEM) follows
Sigstore clients; the schema says only "base64-encoded strings" (not verified against a live log).

## Who decides
- **Prepare** (`/factory-builds/`, a person in `builder_roles`, `args/factory_builds.yaml`): builds and signs, records
  the whole artefact at its digest in the append-only, hash-chained `fb_events`. An agent or a service key never
  prepares: preparing makes the factory's key sign, and an agent never signs. A person outside `builder_roles` is
  refused (`role`).
- **Publish** (`decl-wft-fb-build-publish`): a person of the target's org, cleared for its marking, never the preparer,
  with a reason, bound to the digest read. The build is verified again first.
- **Export**: `{"bundle": icos-release-transfer/v1, "publication": icdev-build-publication/v1}`, only for a published
  build whose declared step is recorded that way by that person.

**"A person" includes that person's dashboard API key** (review M3). ICDEV resolves a dashboard API key to the
person who owns it, with that person's role, org and clearance, and every declared step on the platform treats it the
same way: a person's key is that person acting. A service key (role `service`) is never a person and is refused.
ICDEV has no marker today that separates an interactive session from a person's key at the route, so prepare and
publish do not require a session; requiring one is a platform-wide change to every declared step, not this card's.

**Only the factory's current key publishes** (review L5): at publish, the build's key id must be the configured
signer's (a keyless build must name the configured OIDC issuer). A key rotated or withdrawn since stops its builds
leaving (`key_mismatch`, `no_key`); ICDEV[OS] verifies again against the keys a system's owner pinned. The page's
self-check is re-run on every read under the build's recorded key, and says "verified now" or "fails now".

**A registry row with no tenant** binds only to a target the config declares `platform_scoped: true` (review M2);
`ft_model_versions.tenant_id` defaults to empty, and an unowned row is never taken as an organisation's. A promoted
version whose job or dataset row is missing is refused (`no_provenance`), and one archived since no longer stands.

**The SBOM is resolved from a confined copy** (review M1): only the named dependency manifests and lockfiles at the
project's top level (and `gradle/dependency-locks/*.lockfile`, `obj/project.assets.json`) are copied, each through the
confined reader (under the root, opened component by component with no symlink followed, a regular file with one
link, O_NONBLOCK, size-capped), into a fresh per-run directory that the SBOM generator reads. No installed
environment, source or include is read. The provenance names each copied manifest with its SHA-256.

**The e2e's key.** The shipped configuration names no key. The e2e dashboard signs with a throwaway key the seed writes
to `tests/e2e/fixtures/.ent03_factory_key.pem` (git-ignored), named by `ICDEV_FACTORY_BUILDS_E2E_KEY`, which is
honoured only for a file under `tests/e2e/fixtures/` and only while the config names no key (the precedent is
`ICDEV_MODERNISATION_CAPACITY_E2E`, honoured only under `tests/`). An installed deployment has no `tests/`.

A target the reader may not see is "no such target", byte for byte; lists count what they hold back. MCP
(`factory_builds_status`) only reads, and an agent sees synthetic targets only.

## Not superseded here
None of ICDEV's deprecated compliance generators (cmmc_assessor, fedramp_assessor, ssp_generator, poam_generator,
stig_checker, emass_sync, cve_triager, marketplace publish/review, the simulation engine) is replaced by signed
builds, so none is retired by this card. `sbom_generator.py` (not deprecated) is reused through a new public seam,
and `slsa_attestation_generator.py` (project-level SLSA v1.0 evidence, not deprecated) is left as it is.

## Owner and site steps
1. Set the factory key (`signing.key` in `args/factory_builds.yaml`, through review): a KMS key or a keyring entry the
   site holds. Never the owner's operator or release key.
2. Each customer system's owner pins the factory's public key in the system's ICDEV[OS] record (`release_keys`, the
   `system-change` step).
3. ICDEV[OS] pins the step (`artefacts.icdev.publication_template: decl-wft-fb-build-publish`) and adopts the
   publication draft, and re-pins `context/icos/artefact.schema.json` here once osx-ent-06 merges.
4. Optionally configure keyless (an issuer, a Fulcio, a token file) and a Rekor URL; both stay off until a site does.
