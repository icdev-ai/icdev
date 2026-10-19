# CUI // SP-CTI — `floci-oci` moves 0.4.2 → 0.5.0 (artifact-fresh-6717a5a069)

Same pin, same two literal sites as the last two moves
(`docs/audits/artifact-fresh-f2a465a983-floci-oci-pin.md`): `docker-compose.yml`
and `tools/cloud/emulator_oci.py::IMAGE` (mirrored to `icdev/`), kept equal by
`test_the_compose_image_and_the_seam_image_are_the_same_literal`.

## What the card said

> This tree pins `floci/floci-oci:0.4.2`. Upstream publishes `0.5.0`.
> Decided on `version_tag`.

## Measured 2026-10-19

Both versions driven live on this host, the same probe script against each.
Full tables: `docs/spikes/flx-oci-parity.md` §11.

* **Inventory contract: identical.** Health shape (only `version` differs),
  namespace, every create status, `compartmentId` handling, all eleven list
  lanes and their row keys, the queue envelope, the container-local endpoint
  quirk, the 404 controls, the no-socket OKE 500, storage-mode honouring, the
  image healthcheck.
* **OKE: a create now reports `CREATING`, not `ACTIVE`.** The privileged k3s
  sidecar and its `0.0.0.0`/`[::]` 6443 bind reproduce.
* **Not measured: whether 0.5.0 ever re-checks a dead sidecar.** The Docker
  engine on the measuring host restarted about 15 s after k3s started. Every
  container exited 255 at once, Postgres and the CI runners included. The
  probe was not retried. This host's memory pressure is a known hazard, and
  ICDEV does not depend on OKE.

## Decision: move the pin, keep the guard

* The ten inventory lanes ICDEV reads (seam, DataBridge connector, twin) are
  byte-for-byte the same contract. Keeping 0.4.2 would buy nothing.
* `FABRICATED_ACTIVE_WITH_DOCKER = {"oke"}` and
  `OKE_LIFECYCLE_IS_UNVERIFIED = True` are KEPT. The guard is fail-closed.
  An unmeasured re-check leaves it in place, and only a measurement can
  retire it.
* Every present-tense claim that "floci-oci sets ACTIVE once at create" is
  reworded, because 0.5.0 contradicts it. The new wording is "has never been
  observed re-checking", plus a 0.5.0 clause. That applies to the seam
  docstring, the connector note, the twin `unverified_reason`, the twin
  `simulate_delta` finding (rule id and severity unchanged), the DataBridge
  access note, sandbox-coverage and the compose comment.

## What moved

* `docker-compose.yml`: image `0.4.2` → `0.5.0`, digest comment, OKE comment.
* `tools/cloud/emulator_oci.py` (+ mirror): `IMAGE_TAG`, `IMAGE_DIGEST`,
  hazard docstrings.
* `tools/databridge/connectors/floci_oci_connector.py` (+ mirror) and
  `tools/twin_core/adapters/floci_oci.py` (+ mirror): reason strings only.
* `args/pinned_artifacts.yaml`: `pinned: "0.5.0"`, decision note.
* `args/databridge_agent_access.yaml`, `docs/security/sandbox-coverage.md`:
  wording.
* `docs/spikes/flx-oci-parity.md`: §11 appended, title updated.

```
python -m tools.airgap.artifact_freshness --artifact floci-oci --json
# status: "current"
```
