# CUI // SP-CTI — `floci-az` moves 0.12.0 → 0.13.0 (artifact-fresh-d3f3dd3e03)

**This entry is not one of the `floci` runtime-image rows.** The five prior
`artifact-fresh-*` audits in this directory (`ecr-registry`, `rds-postgres`,
`rds-mysql`, `elasticache-valkey`, `opensearch`) are all about an image floci
**pulls at runtime** to back an emulated service. `floci-az` is the emulator
**container itself** — the same class as the `floci` (AWS) and `floci-gcp`
entries in `args/pinned_artifacts.yaml`, compose-tagged rather than
digest-pinned, with no `vendor/images` topic. There is no "which deployment
knob chooses the tag" question here; ICDEV is the only thing that ever names
this image, in exactly two places (`docker-compose.yml` and
`tools/cloud/emulator_az.py::IMAGE`), kept equal by a test.

## What the card said

The `artifact_freshness` reflex (xrv-pin-01) filed:

> This tree pins `floci/floci-az:0.12.0`. Upstream publishes `0.13.0`.
> Decided on `version_tag`.

## Measured 2026-09-15

Driven directly — `docker pull`, `docker run`, HTTP against both digests on
this host (Docker Desktop 28.5.1, `linux/amd64`):

| probe | 0.12.0 (pinned) | 0.13.0 (upstream) |
|---|---|---|
| `docker manifest inspect` | — | exists, amd64+arm64 |
| `GET /_floci/health` | `200 {"status":"UP","edition":"floci-az-always-free","version":"dev"}` | same shape, same edition, same fake `version` |
| `GET /_localstack/health` | 501 (unchanged behavior per §3 of the parity spike) | 501 |
| Quarkus version in banner | 3.37.4 | 3.39.2 |
| Event Hubs | `mocked — skipping container startup` | `enabled — namespaces start on-demand via PUT .../namespaces/{ns}` (driven without the docker socket: 500, `ApacheDockerHttpClientImpl` connect failure) |
| New ARM lanes | none | `Microsoft.DBforPostgreSQL/flexibleServers`, `Microsoft.DBforMySQL/flexibleServers`, `Microsoft.App/containerApps` all **200** with a dedicated handler (postgres/mysql/mariadb/containerapps now run as real docker-backed services) |
| Banner-only, still unregistered | — | `signalr`, `apim` show `[enabled ]` but their ARM providers still 404 — the "banner is a configuration echo" finding from the parity spike reproduces unchanged |
| `docker image inspect --format '{{index .RepoDigests 0}}'` | `sha256:0c673d49bb75b502ea0750f1c1347777483ffc33945539e1d9254438cb441a03` (matches the tree's recorded digest exactly) | `sha256:3a71953fbc0940aa33bbc1c5e88211a320b66812c0840831a9f8558d3d3521c5` |

Full detail: `docs/spikes/flx-az-parity.md` §9 (added alongside this audit).

## Why the pin moves here, unlike the runtime-image siblings

Every prior card in this family stayed put because SOME consumer-side reason
kept the OLD tag load-bearing: a default nothing overrides (`registry:2`), a
constant nothing can override (`valkey`), a catalogue that refuses the new
value (`opensearch`), or a suffix mismatch (`rds-mysql`). None of that applies
to `floci-az`: nothing in this repository *requests* a version of floci-az —
there is no `EngineVersion`-shaped knob, because the whole container is the
"version." `tools/cloud/emulator_az.py` is a read/inventory-only seam
(`IAC_EXECUTION_SUPPORTED = False`) whose entire contract with the container
is: reach `/_floci/health`, get 200, then talk ARM/data-plane HTTP. Both
digests satisfy that contract identically, and none of the new surfaces
(Event Hubs going docker-backed, the three new database/Container-Apps ARM
lanes) are on any resource-list path this seam walks — they join `appconfig`,
`eventgrid`, `functions`, `monitor`, Service Bus and (now, in its new form)
Event Hubs on the "not designed against, on purpose" list in the parity spike
§8, unchanged in kind.

## What moved

* `docker-compose.yml` — `floci-az` service image `0.12.0` → `0.13.0`, digest
  comment updated.
* `tools/cloud/emulator_az.py` — `IMAGE_TAG` and `IMAGE_DIGEST` updated
  (mirrored byte-for-byte to `icdev/tools/cloud/emulator_az.py`).
* `args/pinned_artifacts.yaml` — `floci-az.pinned` → `"0.13.0"`, decision note
  added.
* `docs/spikes/flx-az-parity.md` — §9 appended; nothing in §§1–8 was
  invalidated, so nothing there was rewritten.

Survey after the move:

```
python -m tools.airgap.artifact_freshness --artifact floci-az --json
# status: "current", observed_digest matches the newly pinned digest
```

No test needed a new assertion: `test_compose_image_matches_the_seam` and
`test_image_tag_is_pinned_never_latest` in `tests/cloud/test_floci_az_seam.py`
check shape (pinned, not `latest`, compose == seam), not the literal version
string, so they hold across the bump without modification.
