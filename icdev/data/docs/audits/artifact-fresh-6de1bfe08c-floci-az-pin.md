# CUI // SP-CTI — `floci-az` moves 0.13.0 → 0.14.0 (artifact-fresh-6de1bfe08c)

Same class as `artifact-fresh-d3f3dd3e03-floci-az-pin.md`: `floci-az` is the
emulator **container itself**, not a runtime image floci pulls, so no
consumer-side knob chooses its tag. ICDEV names it in exactly two places
(`docker-compose.yml`, `tools/cloud/emulator_az.py::IMAGE`), kept equal by a
test, plus `args/pinned_artifacts.yaml`, kept equal to the seam by another.

## What the card said

> This tree pins `floci/floci-az:0.13.0`. Upstream publishes `0.14.0`.
> Decided on `version_tag`.

## Measured 2026-10-07

Both digests pulled and run side by side on this host (Docker Desktop,
`linux/amd64`, no docker socket mounted). The same estate was PUT into each
and the shipped `FlociAzConnector` read every table it declares from each.
Full table: `docs/spikes/flx-az-parity.md` §10.

| | 0.13.0 (pinned) | 0.14.0 (upstream) |
|---|---|---|
| digest | `sha256:3a71953f…d3521c5` (matches the tree's recorded digest) | `sha256:a35e74dfca9a5e811090d8ae98876044fc8990a8874ee525231f524ca56679db` |
| `/_floci/health` | `"version":"dev"` | **`"version":"0.14.0"`** |
| connector, all 9 tables | ok, identical row counts | ok, identical row counts |
| §1 subscription-scope trap (Network types) | reproduces | reproduces |
| §4 provider table + controls | — | identical type for type |

## Decision: move the pin

Nothing this seam reads moved except one thing it describes about itself:
the health body's `version` is now real. Keeping 0.13.0 would buy nothing —
there is no consumer whose evidence was measured against 0.13.0 specifically
(unlike `floci`'s 2.0.1, which six runtime-image measurements hang off).

## What moved

* `docker-compose.yml` — image `0.13.0` → `0.14.0`, digest comment.
* `tools/cloud/emulator_az.py` (+ `icdev/` mirror) — `IMAGE_TAG`,
  `IMAGE_DIGEST`, and `HEALTH_REPORTS_REAL_VERSION` False → True.
* `tools/databridge/connectors/floci_az_connector.py` (+ mirror) — comment
  only; it already reports `version_is_real` from the constant.
* `tests/cloud/test_floci_az_seam.py` — the health-version test now asserts
  True. It is RED at the merge base (where the constant is False), so it is
  a discriminating test for this change.
* `args/pinned_artifacts.yaml` — `floci-az.pinned` → `"0.14.0"`.
* `docs/spikes/flx-az-parity.md` — §10 appended, plus a correction to §1
  row 7 observed on both versions.
