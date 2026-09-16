# CUI // SP-CTI — `floci-oci` moves 0.4.0 → 0.4.1 (artifact-fresh-3d8a95dede)

**Same class as the `floci-az` move (artifact-fresh-d3f3dd3e03).** `floci-oci`
is the emulator container itself, compose-tagged rather than digest-pinned,
with no `vendor/images` topic and no "which deployment knob chooses the tag"
question — ICDEV is the only thing that ever names this image, in exactly two
places (`docker-compose.yml` and `tools/cloud/emulator_oci.py::IMAGE`, mirrored
to `icdev/tools/cloud/emulator_oci.py`), kept equal by
`test_the_compose_image_and_the_seam_image_are_the_same_literal`.

## What the card said

The `artifact_freshness` reflex (xrv-pin-01) filed:

> This tree pins `floci/floci-oci:0.4.0`. Upstream publishes `0.4.1`.
> Decided on `version_tag`.

## Measured 2026-09-16

Driven directly — `docker pull`, `docker run`, HTTP against both digests on
this host (Docker Desktop 28.5.1, `linux/amd64`):

| probe | 0.4.0 (pinned) | 0.4.1 (upstream) |
|---|---|---|
| `docker image inspect --format '{{index .RepoDigests 0}}'` | `sha256:584fd7f977077ab040063d7c2efaaaa1beabacccd903f5297eaa7bbe8f744a8b` (matches the tree's recorded digest exactly) | `sha256:58b4b17069508b30e48bdf5888fd2759a52c23de082bd758484e3b8e2592b1cb` |
| Quarkus version in banner | 3.37.4 | 3.38.3 |
| startup log, `ServiceRegistry` | 7 services (no `functions`) | identical — 7, same set |
| `GET /health` → `services` | 8 (+`functions`), all `"running"` | identical |
| `POST /20180222/clusters` (docker socket mounted) | 202; spawns `rancher/k3s:v1.30.1-k3s1`; container exits with `fatal msg="--token is required"`; API still reports `lifecycleState: ACTIVE` | **identical** — same fatal log line, same dead-container-reads-ACTIVE defect, same k3s tag |
| `FLOCI_OCI_STORAGE_MODE=persistent` | banner reads `Storage: persistent` | identical |
| vault `cryptoEndpoint` on host port 4601 (non-default mapping) | reports container-internal `http://localhost:4599` | identical |
| object storage bucket create + list | write reflects, `compartmentId` honoured | identical |

Full detail: `docs/spikes/flx-oci-parity.md` §9 (added alongside this audit).

## Why the pin moves here

Unlike the `floci` runtime-image rows in this same manifest (`ecr-registry`,
`rds-postgres`, `rds-mysql`, `elasticache-valkey`, `opensearch`), there is no
consumer-side reason to stay put: nothing in this repository requests a
version of floci-oci — the whole container is "the version." The
read/inventory-only seam's entire contract is: reach `/health`, get 200, then
talk OCI-shaped HTTP (`IAC_EXECUTION_SUPPORTED = False`, asserted by a test).
Both digests satisfy that contract identically down to the OKE hazard and the
container-local-endpoint quirk, so there is nothing this seam relies on that
the newer release changes.

Unlike the `floci-az` 0.12.0 → 0.13.0 move, this pass found **no** new surface
to record — no new ARM-shaped lanes, no service that changed from mocked to
docker-backed. This is a patch-shaped release: a Quarkus point bump and
otherwise byte-for-byte identical behavior, including the two hazards this
seam's docstring already names (`SERVICE_LIST_SELF_REPORTS_DISAGREE`,
`FABRICATED_ACTIVE_WITH_DOCKER = frozenset({"oke"})`).

## What moved

* `docker-compose.yml` — `floci-oci` service image `0.4.0` → `0.4.1`, digest
  comment updated; the OKE-hazard comment re-worded to say the defect was
  RE-CONFIRMED on 0.4.1, not merely observed on 0.4.0.
* `tools/cloud/emulator_oci.py` — `IMAGE_TAG` and `IMAGE_DIGEST` updated
  (mirrored byte-for-byte to `icdev/tools/cloud/emulator_oci.py`).
* `args/pinned_artifacts.yaml` — `floci-oci.pinned` → `"0.4.1"`, decision note
  added.
* `docs/spikes/flx-oci-parity.md` — title updated to point at §9; §9 appended.
  Nothing in §§1–8 was invalidated, so nothing there was rewritten.
* `tests/cloud/test_floci_oci_seam.py` — added
  `test_pinned_artifacts_manifest_agrees_with_the_seam`, closing the gap where
  the manifest's `pinned:` field and `emulator_oci.IMAGE_TAG` could drift
  without any test catching it (the same gap closed for `floci-az` in
  artifact-fresh-d3f3dd3e03).

Survey after the move:

```
python -m tools.airgap.artifact_freshness --artifact floci-oci --json
# status: "current", observed_digest matches the newly pinned digest
```

No other test needed a new assertion:
`test_the_compose_image_and_the_seam_image_are_the_same_literal` and
`test_the_digest_is_recorded_for_an_air_gap_bundle_check` check shape (pinned,
not `latest`, compose == seam, digest well-formed), not the literal version
string, so they hold across the bump unmodified.
`test_the_spike_exists_and_is_dated` asserts `emulator_oci.IMAGE_DIGEST` and
`emulator_oci.IMAGE` are literal substrings of the spike document — both are
present in the §9 table/fence added above, so that test holds too.
