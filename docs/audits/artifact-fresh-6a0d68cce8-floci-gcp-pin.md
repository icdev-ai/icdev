# CUI // SP-CTI — `floci-gcp` moves `0.8.0` -> `0.9.0` (artifact-fresh-6a0d68cce8)

## What the card said

The `artifact_freshness` reflex (xrv-pin-01) filed:

> This tree pins `floci/floci-gcp:0.8.0`. Upstream publishes `0.9.0`. Decided
> on `version_tag`.

Re-derived 2026-09-17 — confirmed: `status: behind`, `basis: version_tag`,
`newest: "0.9.0"`, `tags_seen: 245`.

## What I checked before deciding

`docs/spikes/flx-gcp-parity.md` records an unusually large surface of measured
behaviour for `0.8.0` — a health map that is a configuration echo, a
GKE/Kafka path collision, a gRPC-only Firestore/Datastore split, and a
fabricated Cloud Run success without a docker socket. Every one of those is
the kind of fact a point release could silently break, so — following the
same discipline as the `floci-az` (`artifact-fresh-d3f3dd3e03`) and
`floci-oci` (`artifact-fresh-3d8a95dede`) bumps — I drove a live `0.9.0`
container rather than reading a changelog.

Pulled `floci/floci-gcp:0.9.0` (`sha256:ea29a53b34d04ba05240cdc6833608e43ae2b0a67849e5b97f01a7224e1138ea`,
Docker Desktop 28.5.1, `linux/amd64`) and re-ran the probes `flx-gcp-parity.md`
documents, on both a socket-mounted container and one with
`FLOCI_GCP_DOCKER_DOCKER_HOST` pointed at a nonexistent path:

* `/health` still 200 with the same 23-name, all-`"running"` service map
  (`version` now reports `"0.9.0"`); `/_floci/health` and
  `/_localstack/health` still 404.
* `GET /v1/projects/floci-local` still returns a real project body.
* GCS create + list still round-trips (`probe-bucket-09`).
* `POST /v1/projects/{p}/locations/{l}/clusters` is still answered by the
  Managed Kafka handler — spawned `floci-gcp-kafka-…` running
  `redpandadata/redpanda:latest`, same `bootstrapAddress` shape. Real GKE is
  still at `/container/v1/…`.
* Firestore/Datastore REST lanes (`/v1/…/documents`, `:runQuery`) still 404.
* Without a docker socket: Cloud SQL and Kafka still return 500 with a
  dockerjava stack trace; **Cloud Run still returns a fabricated 200** with a
  service body carrying `uid`/`createTime`/`traffic`.
* With a socket: Cloud SQL create still spawns `postgres:15.18-alpine`.
* `FLOCI_GCP_STORAGE_MODE=persistent` still produces banner `Storage:
  persistent`.

Full table in `docs/spikes/flx-gcp-parity.md` §10. Everything this platform's
seam (`tools/cloud/emulator_gcp.py`) and connector
(`tools/databridge/connectors/floci_gcp_connector.py`) design against
reproduced **byte-for-byte**. The only observed difference — image size
92.2 MB → 53.4 MB, Quarkus 3.37.4 → 3.38.3 — is a packaging/runtime change
that nothing here reads.

## The decision

**Move `floci/floci-gcp` to `0.9.0`.** Unlike the `floci` root pin
(`artifact-fresh-1f206ea0ad`, kept at `2.0.1`) and the `floci-gcp`
sibling-of-a-sibling reasoning that guards the six `decided_by: consumer`
entries, this pin has exactly one dependent — the read/inventory-only GCP
seam and connector — and that dependent's entire designed-against surface was
re-driven live and found unchanged. There is no multi-probe re-measurement
owed here the way there was for the AWS root pin: `floci-gcp` has no runtime
base-image manifest of its own (`args/floci_runtime_images.yaml` covers only
the AWS emulator) and no `decided_by: consumer` entries key off it.

Moved together, in this diff:
`docker-compose.yml` (image tag + digest comment), `tools/cloud/emulator_gcp.py`
and its `icdev/tools/` mirror (`IMAGE_TAG`, `IMAGE_DIGEST`, the two docstring
`"version"` literals), `args/pinned_artifacts.yaml` (`pinned:` + a `note`
recording this decision), `docs/spikes/flx-gcp-parity.md` (§10, the
re-measurement), and `tests/cloud/test_floci_gcp_seam.py` (updated the pinned
literal, added `test_pinned_artifacts_manifest_agrees_with_the_seam` so the
manifest and the seam cannot drift apart silently — the same test the
`floci-az` and `floci-oci` bumps added for their own manifests).

## Verify

```bash
python -m tools.airgap.artifact_freshness --artifact floci-gcp --json
# status: current
```

The reflex's idempotency key was `artifact-freshness:floci-gcp:0.9.0`; a
further upstream release is a new key and a new card, not a re-fire of this
one.
