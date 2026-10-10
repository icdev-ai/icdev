# CUI // SP-CTI — `floci-oci` moves 0.4.1 → 0.4.2 (artifact-fresh-f2a465a983)

Same pin, same two literal sites as the last move
(`docs/audits/artifact-fresh-3d8a95dede-floci-oci-pin.md`): `docker-compose.yml`
and `tools/cloud/emulator_oci.py::IMAGE` (mirrored to `icdev/`), kept equal by
`test_the_compose_image_and_the_seam_image_are_the_same_literal`.
**Unlike the last move, this release changes behaviour ICDEV wrote down.**

## What the card said

> This tree pins `floci/floci-oci:0.4.1`. Upstream publishes `0.4.2`.
> Decided on `version_tag`.

## Measured 2026-10-06

Both versions driven live on this host, the same probe script against each.
Full tables: `docs/spikes/flx-oci-parity.md` §10.

* **Inventory contract: identical.** Health shape, namespace, every create
  status, `compartmentId` handling, the queue envelope, the container-local
  endpoint quirk, the 404 controls, storage-mode honouring.
* **OKE: changed.** 0.4.2 starts k3s with a token-auth file; the apiserver
  answers 401 unauthenticated and 200 with the token the kubeconfig lane hands
  out. Through 0.4.1 k3s died on `--token is required`.
* **But `lifecycleState` is still never re-checked** — stop the k3s container
  and the API still reports `ACTIVE` with a dead endpoint.
* **New exposure:** the created cluster is a privileged container publishing
  6443 on all host interfaces.

## Decision: move the pin, keep the guard, change only its reason

* Keeping 0.4.1 buys nothing: it is the release where OKE cannot work at all,
  and the inventory lanes ICDEV reads are identical on both.
* `FABRICATED_ACTIVE_WITH_DOCKER = {"oke"}` and `OKE_LIFECYCLE_IS_UNVERIFIED`
  are KEPT — the field is still not evidence (measured, not assumed). Their
  reason text — seam, DataBridge connector note, twin snapshot
  `unverified_reason`, twin `simulate_delta` finding (rule id unchanged,
  severity unchanged at `high`) — is rewritten to the 0.4.2 truth, history
  included. A pin bump that left "k3s exits immediately" in every one of those
  strings would ship a claim nobody observed on the pinned digest.
* The off-host 6443 bind is recorded (compose comment, sandbox-coverage,
  spike) and NOT mitigated: the emulator chooses it, ICDEV never creates an
  OKE cluster, and gating cluster creation is a separate decision for whoever
  reviews this.

## What moved

* `docker-compose.yml` — image `0.4.1` → `0.4.2`, digest comment, OKE comment.
* `tools/cloud/emulator_oci.py` (+ mirror) — `IMAGE_TAG`, `IMAGE_DIGEST`,
  hazard docstrings.
* `tools/databridge/connectors/floci_oci_connector.py` (+ mirror),
  `tools/twin_core/adapters/floci_oci.py` (+ mirror) — reason strings only.
* `args/pinned_artifacts.yaml` — `pinned: "0.4.2"`, decision note.
* `args/databridge_agent_access.yaml`, `docs/security/sandbox-coverage.md`,
  `tests/cloud/test_floci_compose_profile.py` (comment only) — wording.
* `docs/spikes/flx-oci-parity.md` — §10 appended, title updated.

```
python -m tools.airgap.artifact_freshness --artifact floci-oci --json
# status: "current", observed_digest == the newly pinned digest
```
