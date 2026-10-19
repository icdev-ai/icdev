# CUI // SP-CTI — `floci-gcp` moves `0.10.0` -> `0.11.0` (artifact-fresh-b20d238422)

## What the card said

The `artifact_freshness` reflex (xrv-pin-01) filed:

> This tree pins `floci/floci-gcp:0.10.0`. Upstream publishes `0.11.0`.
> Decided on `version_tag`.

Re-derived 2026-10-19 and confirmed: `status: behind`, `basis: version_tag`,
`newest: "0.11.0"`, `tags_seen: 313`.

## What I checked before deciding

I followed the same discipline as the two previous bumps
(`artifact-fresh-6a0d68cce8`, `artifact-fresh-6ba99af4d5`): drive a live
container rather than read a changelog. I pulled `floci/floci-gcp:0.11.0`
(`sha256:eae9683d806ceb643bcb36aee97cdb8cad13c3e2959cd053bf40e481994fde12`)
and ran three containers together: `0.11.0` with the docker socket, `0.11.0`
with the docker host pointed at a nonexistent path, and `0.10.0` as a live
reference.

Every fact that `tools/cloud/emulator_gcp.py` and
`tools/databridge/connectors/floci_gcp_connector.py` are designed against
reproduced:

- the `/health` path and the sibling 404s;
- the enablement-only service map (24 names, all `"running"`, byte-identical
  with and without a socket);
- the real `version`;
- the project body;
- the Firestore/Datastore REST 404s;
- the GKE/Kafka `/v1/.../clusters` collision;
- the cloudsql/kafka spawn images;
- the socket-less dockerjava 500s;
- the fabricated Cloud Run 200 (whose `GET` body includes `urls`);
- persistent storage;
- the GCS round-trip.

The full table is in `docs/spikes/flx-gcp-parity.md` §12.

**Deltas:**

1. `serviceusage` changed position in the service map. The set did not
   change, and nothing reads the order.
2. Cloud Run's revision container used to publish its ephemeral host port on
   `0.0.0.0`. It is now `127.0.0.1` only, so the move narrows the exposure.
   This also corrects §5's claim that no spawned service gets a host port;
   that claim was only ever true for Cloud SQL and Kafka. I amended the seam
   docstring. `SPAWNED_SERVICES_ARE_BRIDGE_ADDRESSED` and the absent
   `PROXY_PORT_RANGES` stand, because a per-revision port is not a range and
   nothing dials it.

## The decision

**Move `floci/floci-gcp` to `0.11.0`.** The pin has one dependent: the
read/inventory-only GCP seam and its connector. Its whole designed-against
surface was re-driven live and is unchanged. No runtime base-image
re-measurement is owed. `args/floci_runtime_images.yaml` covers only the AWS
emulator, no `decided_by: consumer` entry keys off `floci-gcp`, and the spawn
images observed here (`postgres:15.18-alpine`,
`redpandadata/redpanda:latest`, the user's Cloud Run image) are unchanged.

These files moved together:

- `docker-compose.yml` (tag and digest comment);
- `tools/cloud/emulator_gcp.py` and its `icdev/tools/` mirror (`IMAGE_TAG`,
  `IMAGE_DIGEST`, docstrings);
- the connector docstring (both copies);
- `args/pinned_artifacts.yaml` (`pinned:` and `note`);
- `docs/spikes/flx-gcp-parity.md` (§12);
- `tests/cloud/test_floci_gcp_seam.py` (pinned literals).

## Verify

```bash
python -m tools.airgap.artifact_freshness --artifact floci-gcp --json
# status: current
```
