# CUI // SP-CTI — `floci-az` moves 0.14.0 → 0.15.0 (artifact-fresh-95d11e24a8)

Same class as `artifact-fresh-6de1bfe08c-floci-az-pin.md`: `floci-az` is the
emulator **container itself**, not a runtime image floci pulls, so no
consumer-side knob chooses its tag. ICDEV names it in `docker-compose.yml` and
`tools/cloud/emulator_az.py::IMAGE` (kept equal by a test), plus
`args/pinned_artifacts.yaml` (kept equal to the seam by another).

## What the card said

> This tree pins `floci/floci-az:0.14.0`. Upstream publishes `0.15.0`.
> Decided on `version_tag`.

## Measured 2026-10-18

Both digests pulled and run side by side on this host (Docker Desktop,
`linux/amd64`, no docker socket mounted), plus 0.15.0 a second time with
`FLOCI_AZ_PARITY=azure`. The same estate was PUT into each and the shipped
`FlociAzConnector` read every table it declares from each. Full table:
`docs/spikes/flx-az-parity.md` §11.

| | 0.14.0 (pinned) | 0.15.0 (upstream) |
|---|---|---|
| digest | `sha256:a35e74df…c56679db` (matches the tree's recorded digest) | `sha256:530635d603223e120adbf9221a97560cffc6991d65fa0c72d0f79d1097974c89` |
| `/_floci/health` | `"version":"0.14.0"` | `"version":"0.15.0"` |
| connector, all 9 tables | ok, identical row counts | ok, identical row counts (also under `PARITY=azure`) |
| §1 subscription-scope trap (Network types) | reproduces | reproduces (also under `PARITY=azure`) |
| §4 absent providers / §9 lanes | 404 / 200 | identical |

New in 0.15.0 and not consumed: a `FLOCI_AZ_PARITY` mode (default `compat`,
which ICDEV runs), an Azure Policy ARM lane, and a 176 MB → 46 MB image.

## Decision: move the pin

Nothing this seam reads moved, and no constant in `emulator_az` changes value
(`HEALTH_REPORTS_REAL_VERSION` re-measured True). As with 0.14.0, no evidence
in this tree was measured against 0.14.0 specifically, so keeping it buys
nothing. Floci-az does not pull runtime images on the seam's read paths, so
there is no `args/floci_runtime_images.yaml` table to re-derive for it.

## What moved

* `docker-compose.yml` — image `0.14.0` → `0.15.0`, digest comment.
* `tools/cloud/emulator_az.py` (+ `icdev/` mirror) — `IMAGE_TAG`,
  `IMAGE_DIGEST`, comments. No behavioural constant changed.
* `args/pinned_artifacts.yaml` (+ `icdev/data` copy) — `floci-az.pinned` →
  `"0.15.0"`.
* `docs/spikes/flx-az-parity.md` (+ copy) — §11 appended.

No test changes: the compose↔seam and manifest↔seam agreement tests already
pin the three names equal, and no measured property flipped, so there is no
new assertion to go RED.
