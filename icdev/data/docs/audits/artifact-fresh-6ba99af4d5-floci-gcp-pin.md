# CUI // SP-CTI — `floci-gcp` moves `0.9.0` -> `0.10.0` (artifact-fresh-6ba99af4d5)

## What the card said

The `artifact_freshness` reflex (xrv-pin-01) filed:

> This tree pins `floci/floci-gcp:0.9.0`. Upstream publishes `0.10.0`.
> Decided on `version_tag`.

Re-derived 2026-10-07 — confirmed: `status: behind`, `basis: version_tag`,
`newest: "0.10.0"`, `tags_seen: 287`.

## What I checked before deciding

The same discipline as the previous bump (`artifact-fresh-6a0d68cce8`):
drive a live container, not a changelog. Pulled `floci/floci-gcp:0.10.0`
(`sha256:405c128b685afbc461276820f2286defcc3a6fa478755067d3e3f740c5503c07`)
and ran three containers side by side — `0.10.0` with the docker socket,
`0.10.0` with `FLOCI_GCP_DOCKER_DOCKER_HOST` pointed at a nonexistent path,
and `0.9.0` as a live reference — so each probe was answered by both releases.

Every fact `tools/cloud/emulator_gcp.py` and
`tools/databridge/connectors/floci_gcp_connector.py` design against
reproduced: the `/health` path and sibling 404s, the enablement-only
service map (all `"running"`, byte-identical with and without a socket),
the real `version`, the project body, Firestore/Datastore REST 404s, the
GKE/Kafka `/v1/.../clusters` collision, the cloudsql/kafka spawn images, the
socket-less 500s, the fabricated Cloud Run 200, persistent storage, and the
GCS round-trip. Table: `docs/spikes/flx-gcp-parity.md` §11.

**One delta:** a 24th service, `compute`, appended to the map. Probed: it is
control-plane only — an instance reaches `RUNNING` with a `networkIP` and no
container is started, socket or not. It is therefore not container-backed,
and `CONTAINER_BACKED_SERVICES` / `FABRICATED_SUCCESS_WITHOUT_DOCKER` are
unchanged. Nothing reads the service-map length (it is read as names only),
so the only edits it needed were prose ("23" -> "24").

## The decision

**Move `floci/floci-gcp` to `0.10.0`.** One dependent (the read/inventory-only
GCP seam + connector); its whole designed-against surface was re-driven
live and unchanged. No runtime base-image re-measurement is owed:
`args/floci_runtime_images.yaml` covers only the AWS emulator, and no
`decided_by: consumer` entry keys off `floci-gcp`.

Moved together: `docker-compose.yml` (tag + digest comment),
`tools/cloud/emulator_gcp.py` and its `icdev/tools/` mirror (`IMAGE_TAG`,
`IMAGE_DIGEST`, docstrings), the connector docstring (both copies),
`args/pinned_artifacts.yaml` (`pinned:` + `note`),
`docs/spikes/flx-gcp-parity.md` (§11), and
`tests/cloud/test_floci_gcp_seam.py` (pinned literals).

## Verify

```bash
python -m tools.airgap.artifact_freshness --artifact floci-gcp --json
# status: current
```
