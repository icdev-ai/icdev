# CUI // SP-CTI

# flx-gcp-parity — what floci-gcp 0.8.0 ACTUALLY answers

**Measured 2026-09-05** on this host (Windows 11, Docker Desktop 28.5.1,
`linux/amd64`), against:

```
floci/floci-gcp:0.8.0
sha256:5037d304aded5ab4ccf4697239131521fe66b8952f411f6c1781c9166d2ab01b
92.2 MB · native (Quarkus 3.37.4) · edition banner "GCP Local Emulator · Always Free"
```

Re-derive everything below with:

```bash
docker run -d --name flx-gcp-probe -p 4588:4588 \
  -v //var/run/docker.sock:/var/run/docker.sock \
  -e FLOCI_GCP_DEFAULT_REGION=us-central1 floci/floci-gcp:0.8.0
docker logs flx-gcp-probe            # the enabled-services line
curl -s http://localhost:4588/health
```

> **Why this document exists before any code.** `flx-gcp-01` says: *measure
> parity before claiming anything.* Every table here is a recorded HTTP or gRPC
> response, not a README reading. **Four premises did not survive the
> measurement, and two of those four were premises of an earlier draft of THIS
> document** — see [§8](#8-corrections-to-the-card-and-to-this-document).

---

## 1. The finding that changes the design

### Firestore and Datastore have NO REST lane — they answer gRPC ONLY

Both are in the emulator's own enabled-services list. Every REST path tried
returned 404 or 405; the same operations over **gRPC on the same port**
answered immediately.

| Lane | Firestore | Datastore |
|---|---|---|
| `GET /v1/projects/{p}/databases/(default)/documents` | 404 | — |
| `GET /v1/projects/{p}/databases` | 404 | — |
| `GET,POST /firestore/v1/...` | 404 | — |
| `POST /v1/projects/{p}:runQuery` / `:lookup` / `:beginTransaction` | — | 404 |
| `POST /datastore/v1/projects/{p}:runQuery` | — | **405** (POST *and* PATCH; GET/PUT 404) |
| **gRPC `google.firestore.v1.Firestore/ListCollectionIds`** | **OK** | — |
| **gRPC `google.firestore.v1.Firestore/ListDocuments`** | **OK** | — |
| **gRPC `google.datastore.v1.Datastore/RunQuery`** | — | **OK**, 20 bytes |
| **gRPC `google.datastore.v1.Datastore/AllocateIds`** | — | **OK** |

**Consequence, and it is binding on every consumer we ship:** ICDEV's connector
stack reads over HTTP (`urllib`). It therefore **cannot read Firestore or
Datastore from this emulator at all**, and the failure is silent-shaped — a
REST 404 is indistinguishable from "no such resource". So neither appears in
`floci_gcp_connector.TABLES`, and `emulator_gcp.GRPC_ONLY_SERVICES` states the
reason once, by name, so a later card adding a `firestore` table has to
confront it rather than rediscover it.

The gRPC probe **discriminates**, which is what makes those OKs meaningful:
`/nonsense.Fake/DoesNotExist` and `/google.pubsub.v1.Publisher/NoSuchMethod`
both returned `UNIMPLEMENTED — Method not found`. A registered method and an
unregistered one are distinguishable.

### The Azure trap does NOT apply here — measured, not assumed

`flx-az-parity.md` §1 found that a subscription-scoped ARM list returns
`200 {"value":[]}` for a populated estate. **The GCP analogue does not have
that defect.** Project-scoped lists reflect writes, measured in one sequence
per service:

| Service | Write | Project-scoped list afterwards |
|---|---|---|
| GCS | `POST /storage/v1/b?project=…` → 200 | `GET /storage/v1/b?project=…` → **the bucket** |
| Pub/Sub | `PUT /v1/projects/{p}/topics/probe-topic` → 200 | `GET …/topics` → **the topic** |
| Secret Manager | `POST …/secrets?secretId=…` → 200 | `GET …/secrets` → **the secret** |
| KMS | `POST …/keyRings?keyRingId=…` → 200 | `GET …/keyRings` → **the key ring** |

Recorded because a sibling seam inheriting the Azure fan-out "to be safe" would
add a per-scope loop that nothing here needs, and would then carry a comment
citing a trap this emulator does not have.

**What IS absent is enumeration one level up:** `GET /v1/projects` returns
**404**, while `GET /v1/projects/{id}` returns 200 with a real project body. A
project id cannot be discovered from this emulator — it is configuration
(`FLOCI_GCP_PROJECT_ID`, default `floci-local`, the id the emulator seeds).

---

## 2. `/health` carries a service map, and the map is a CONFIGURATION ECHO

This is the sharpest hazard on this emulator, and it is **worse than its Azure
sibling's** rather than better.

floci-az has no service map at all, so `flx-az-parity.md` could record
`HEALTH_HAS_SERVICE_MAP = False` and be done. floci-gcp **does** publish one,
23 services deep, every one reading `"running"` — so a consumer will believe
it.

Proof — two containers, one with the host docker socket mounted, one with
`FLOCI_GCP_DOCKER_DOCKER_HOST=unix:///nonexistent/does-not-exist.sock`:

```
socket-mounted: 23 services | socket-absent: 23 services
services maps IDENTICAL: True
whole bodies IDENTICAL:  True
differences: NONE
distinct status values across all 23: ['running']
```

The map is byte-identical for a deployment that **provably cannot start a
container** ([§5](#5-container-backed-services-measured-by-what-they-spawn)),
and `"running"` is the only value the vocabulary was ever observed to hold. It
is a registry listing wearing a measurement's name — the `rem-hyg-17` shape,
and the same defect as floci-az's `[enabled ]` banner one level along.

So two constants, not one, and keeping them apart is the point:

```python
HEALTH_HAS_SERVICE_MAP        = True   # the key exists and parses
HEALTH_SERVICE_MAP_IS_ENABLEMENT_ONLY = True   # ...and it is NOT a health signal
```

ICDEV decides docker-backing from **its own** seam
(`emulator_gcp.docker_backed()`, the tri-state that returns `None` rather than
guessing on Windows), never from this map.

### Health path — the Azure seam's constant does NOT carry over

| Path | Status | Body |
|---|---|---|
| **`/health`** | **200** | `{"services":{…23…},"version":"0.8.0"}` |
| `/_floci/health` | **404** | GCS-shaped `Object not found: health` |
| `/_localstack/health` | 404 | GCS-shaped `Object not found: health` |
| `/_floci_gcp/health` | 404 | GCS-shaped |

Three sibling emulators, three different health paths — `/_localstack/health`
(floci, the AWS drop-in), `/_floci/health` (floci-az), `/health` (floci-gcp).
There is no shared contract to inherit and no `LOCALSTACK_*` alias layer.

**`version` reports the REAL release here** (`0.8.0`, matching the image's own
`FLOCI_GCP_VERSION`), unlike floci-az which reports `"dev"`. So
`HEALTH_REPORTS_REAL_VERSION = True` — the one constant that inverts in the
*helpful* direction, and it is still not load-bearing for anything.

### An unknown path 404s in one of TWO shapes, and never 501

| Shape | When | Example |
|---|---|---|
| GCS-object JSON 404 | the first path segment parses as a bucket name | `/_floci/health` → `{"error":{"code":404,"message":"Object not found: health",…}}` |
| HTML `Resource not found` | it does not | `/`, `/v1`, `/computeMetadata/v1/project/project-id` |

Unlike floci-az — where an unrouted path falls into the blob handler and
returns **501** — an unrouted path here is a 404 either way. A reachability
probe that treats "not 404" as "route exists" is wrong on floci-az and right
here, which is exactly why neither seam may borrow the other's probe.

---

## 3. There is no metadata server

`GET /computeMetadata/v1/project/project-id` → **404** (HTML shape). floci-az
issues real signed JWTs from an IMDS endpoint; floci-gcp does not serve the GCE
metadata surface at all.

Consequence: nothing on this emulator hands out ambient credentials to a
process that merely runs next to it. It also means the standing IAM-sandbox
NO-GO is *less* strained here than on the Azure sibling — see
[§9](#9-standing-guards-carried-forward).

`iamcredentials` **does** mint a token when asked directly:
`POST /v1/projects/-/serviceAccounts/{sa}:generateAccessToken` → 200
`{"accessToken":"floci-gcp-impersonated-7f54139f-…"}`. That is an opaque
placeholder string, not a signed JWT — a *shaped* answer, not a credential.

---

## 4. Which lanes actually answer

`GET` unless noted. Every row is a recorded response.

### Answering — REST

| Service | Path | Result |
|---|---|---|
| resourcemanager | `/v1/projects/{p}` | **200** real project body, `projectNumber` |
| gcs | `/storage/v1/b?project={p}` (+ create, upload, download, list) | **200** — full round-trip, [§6](#6-round-trips-that-actually-round-trip) |
| pubsub | `/v1/projects/{p}/topics` (+ create, publish, subscribe, pull) | **200** — full round-trip |
| secretmanager | `/v1/projects/{p}/secrets` (+ create) | **200** |
| kms | `/v1/projects/{p}/locations/{l}/keyRings` (+ create) | **200** |
| bigquery | `/bigquery/v2/projects/{p}/datasets` | **200** `{"kind":"bigquery#datasetList"}` |
| cloudsql | `/sql/v1beta4/projects/{p}/instances` (+ create) | **200** `{"kind":"sql#instancesList"}` |
| logging | `/v2/projects/{p}/logs` | **200** `{"logNames":[]}` |
| iam | `/v1/projects/{p}/serviceAccounts` | **200** `{"accounts":[]}` |
| **gke** | **`/container/v1/projects/{p}/locations/{l}/clusters`** (+ create) | **200** — and create really spawns k3s |
| monitoring | `/v3/projects/{p}/metricDescriptors` | 200 `{}` |
| scheduler | `/v1/projects/{p}/locations/{l}/jobs` | 200 `{}` |
| cloudrun | `/v2/projects/{p}/locations/{l}/services` (+ create) | 200 `{}` |
| cloudfunctions | `/v2/projects/{p}/locations/{l}/functions` | 200 `{}` |
| eventarc | `/v1/projects/{p}/locations/{l}/triggers` | 200 `{}` |
| serviceusage | `/v1/projects/{p}/services` | 200 `{}` |
| kafka | `/v1/projects/{p}/locations/{l}/clusters` (+ create) | **200** — see the collision below |
| iamcredentials | `POST /v1/projects/-/serviceAccounts/{sa}:generateAccessToken` | **200** |
| firebaseauth | `POST /identitytoolkit.googleapis.com/v1/accounts:signUp?key=any` | **200**, returns an `idToken` |
| sts | `POST /v1/token` **form-encoded** | **400** with a *semantic* error — routed and validating |

**The empty-shape split is real and a reader must not merge it.** Nine lanes
answer a keyed empty (`{"clusters":[]}`, `{"logNames":[]}`, `{"accounts":[]}`)
and six answer a bare `{}` with no key at all. `body.get("services", [])` reads
both as empty; `body["services"]` raises on half of them.

### The two that needed a second verb — and this is why the table above is trustworthy

Both looked absent on the first pass and are not:

* **firebaseauth** answered **405** to a `GET`. 405 is *routed, wrong verb*.
  A `POST` to `:signUp` returns 200 and a real token. Recording it as
  unreachable would have been a fabricated absence.
* **sts** answered **415** to a JSON `POST`. 415 is *routed, wrong media type*.
  Form-encoded, it returns a 400 naming the offending field.

This is the floci-az `401-not-501` lesson generalised: **the status code says
which question you got wrong.** A probe that only records "not 200" invents
absences.

### Not reached by any route tried

| Service | Tried | Result |
|---|---|---|
| **cloudtasks** | `/v2/…/queues`, `/v2beta3/…`, `/cloudtasks/v2/…`, `/tasks/v2/…`, GET and POST | 404 everywhere |
| **firestore** (REST) | `/v1/…/documents`, `/v1/…/databases`, `/firestore/v1/…` | 404 — **gRPC only**, §1 |
| **datastore** (REST) | `/v1/{p}:runQuery|:lookup|:beginTransaction`, `/datastore/v1/{p}:runQuery` | 404 / **405** — **gRPC only**, §1 |

Recorded as `declared_unreachable` **by name**, deliberately not merged with
"absent": the repairs differ (find the route or file upstream, versus do not
design against it at all). The claim is bounded — *no route found*, not *no
route exists*; §8 records that this document already got that call wrong once.

### The path collision: a GKE request answered by Kafka

`/v1/projects/{p}/locations/{l}/clusters` — **Google's documented GKE path** —
is served here by the **Managed Kafka** handler. This is not an inference:

```
POST /v1/projects/floci-local/locations/us-central1/clusters?clusterId=probe-gke
  → 200
  → docker ps: floci-gcp-kafka-probe-gke   redpandadata/redpanda:latest
  → GET the same path: {"clusters":[{ …, "bootstrapAddress":"172.17.0.6:9092",
                                      "volumeName":"floci-gcp-kafka-c997e2"}]}
```

A container named `floci-gcp-kafka-*` running Redpanda, from a request that
asked for a GKE cluster, and a response body whose `{"clusters":[…]}` shape is
exactly what a GKE reader expects. GKE itself lives at `/container/v1/…` and
works there (create → `operationType: CREATE_CLUSTER`, spawns
`rancher/k3s:latest`).

**So a GKE client pointed at this emulator gets Kafka clusters and no error.**
`emulator_gcp.GKE_LIST_PATH_PREFIX` pins the `/container/v1` prefix and
`PATH_COLLISIONS` names the hazard, so no caller composes the `/v1` form.

---

## 5. Container-backed services, measured by what they SPAWN

The only sound way to answer "is this docker-backed" on this emulator, since
the health map cannot ([§2](#2-health-carries-a-service-map-and-the-map-is-a-configuration-echo)).
Each row is a create issued against the socket-mounted container and the
socket-absent one, in the same minute.

| Service | With socket | Container spawned | Without socket |
|---|---|---|---|
| **cloudsql** | 200 operation | `floci-gcp-cloudsql-…` `postgres:15.18-alpine` | **500** Internal Server Error |
| **kafka** | 200 | `floci-gcp-kafka-…` `redpandadata/redpanda:latest` | **500** |
| **gke** | 200 `CREATE_CLUSTER` | `floci-gcp-gke-…` `rancher/k3s:latest` | *(500, via the shared handler)* |
| **cloudrun** | 200 operation | `floci-gcp-cloudrun-…` `nginx:alpine` (the user's image) | **200 — a FABRICATED SUCCESS** |

**Cloud Run is the dangerous one.** On the socket-absent container the deploy
returned **200**, and a subsequent `GET` of the service returned a body
carrying `uid`, `generation`, `createTime`, `traffic` and even a `urls` entry —
structurally indistinguishable from the deployment that really has an nginx
container behind it. Nothing in the API tells the two apart.

That single row is the whole argument for deciding `unsupported_without_docker`
in ICDEV's seam rather than from any response this emulator gives.

The 500s carry a `com.github.dockerjava…ApacheDockerHttpClientImpl` stack trace
— an unhandled failure, not a designed refusal. Neither shape is something a
caller should be asked to interpret.

### Spawned services are addressed by BRIDGE IP, not by a host port

floci-az forwards ~1,100 host ports to the containers it spawns, and
`flx-az-parity.md` §6 spends a section on why those ranges are declared and
deliberately not published. **floci-gcp does not forward host ports at all.**

```
docker ps: floci-gcp-cloudsql-floci-local-pq  postgres:15.18-alpine  PORTS=[5432/tcp]
                                                                    ^ exposed, NOT published
GET /sql/v1beta4/…/instances → ipAddresses: [{"type":"PRIMARY",
                                              "ipAddress":"172.17.0.3","port":5432}]
```

`5432/tcp` with no `0.0.0.0:…->` mapping, and the API hands back a Docker
bridge address. This is consistent with the embedded DNS server the emulator
starts at boot (`172.17.0.2:53`, resolving `localhost.floci.io`).

Two consequences:

1. **The compose block declares no proxy port ranges**, because there are none
   to declare. `emulator_gcp` therefore has no `PROXY_PORT_RANGES` constant —
   the Azure seam's is not copied across empty.
2. **A process on the HOST cannot reach a spawned Cloud SQL instance.**
   `172.17.0.3:5432` is inside Docker's bridge network; only a container on
   that network can dial it. Nothing in this card needs to, and nothing here
   claims otherwise — but a later card planning to *use* the emulated Cloud SQL
   from an ICDEV process on the host will have to solve that, and it is a
   network-topology problem rather than a configuration one.

### Base images pulled AT RUN TIME — an air-gap finding

Enumerated from the native binary and corroborated by what actually started:

| Image | Pinned? |
|---|---|
| `postgres:15.18-alpine`, `postgres:16.14-alpine`, `postgres:17.10-alpine`, `postgres:18.4-alpine` | pinned |
| `redpandadata/redpanda:latest` | **`:latest`** |
| `rancher/k3s:latest` | **`:latest`** |

Two of the six are floating tags, so an air-gap cache keyed on a digest cannot
be completed by reading this list alone — the digest depends on when the
mirror pulled. This is the same shape as the AWS sibling's runtime pulls
(`flx-airgap-01`/`02`) and is recorded here for whichever card bundles GCP;
**this card ships no bundler** and makes no air-gap claim for floci-gcp.

---

## 6. Round-trips that actually round-trip

Content, not status codes:

```
GCS      PUT object "hello-from-flx-gcp-01" → GET ?alt=media → hello-from-flx-gcp-01
Pub/Sub  publish {"data":"aGVsbG8="} → pull → receivedMessages[0].message.data == "aGVsbG8="
KMS      create keyRing probe-ring → list → projects/floci-local/locations/us-central1/keyRings/probe-ring
```

**The REST and gRPC lanes share one state.** Every resource created over REST
came back over gRPC in the same session — the bucket (`ListBuckets` →
`projects/_/buckets/probe-bucket`), the topic (`ListTopics` →
`projects/floci-local/topics/probe-topic`) and the secret (`ListSecrets` →
`…/secrets/probe-secret`). They are two doors onto one store, which is what
makes the split in §7 a question of *client transport* only.

---

## 7. The env contract — and it is NOT one shape

**This is what the card is about, and the card's own list is uniform where the
truth is not.**

GCP client libraries take no endpoint-override parameter the way boto3 does.
They read standard `*_EMULATOR_HOST` variables. All of them point at the same
place on this emulator — **one port, 4588, serving both transports** (only
`4588/tcp` listens inside the container; the gRPC server is multiplexed onto it
by `GrpcServerManager`, which logs every request's `content-type`).

But **the FORM differs by transport**, and getting it wrong fails at the client:

| Variable | Transport | Form | Value here |
|---|---|---|---|
| `PUBSUB_EMULATOR_HOST` | gRPC | `host:port` | `localhost:4588` |
| `FIRESTORE_EMULATOR_HOST` | gRPC | `host:port` | `localhost:4588` |
| `DATASTORE_EMULATOR_HOST` | gRPC | `host:port` | `localhost:4588` |
| `FIREBASE_AUTH_EMULATOR_HOST` | REST | `host:port` | `localhost:4588` |
| **`STORAGE_EMULATOR_HOST`** | REST | **URL, with scheme** | **`http://localhost:4588`** |
| `SECRET_MANAGER_EMULATOR_HOST` | gRPC | `host:port` | `localhost:4588` |

**Both halves of that split are supported by measurement on this host:**

* the gRPC lane is addressed **without a scheme** — `grpc.insecure_channel(
  "localhost:4588")` connected and served eight real methods;
* the REST lane is addressed **with one** — the emulator's own `selfLink`
  fields come back as `http://localhost:4588/storage/v1/b/probe-bucket`. It is
  the emulator stating its own REST base, scheme included.

**What is NOT measured here, stated plainly:** no `google-cloud-*` client
library is installed on this host, so *the client-side reading of these
variables was not exercised*. Installing one to check would add an undeclared
dependency to the very environment the `tsg-iso-03` census governs. The mapping
above is therefore **declared** — from each variable's transport, which *is*
measured — and `emulator_gcp.EMULATOR_HOST_VARS` carries that provenance per
entry rather than presenting six equally-verified facts.

Corroborating negative: `grep -a "EMULATOR_HOST" /app/application` over the
native binary returns **nothing**. The emulator neither reads nor validates any
of these names. The contract lives entirely on the client side, which means
**nothing on this emulator will ever tell us we got it wrong** — hence a test
over the exported set, which is the one half we control.

---

## 8. Corrections to the card, and to this document

Recorded because the card asked for measurement rather than a README reading.

### To the card

| Card said | Measured |
|---|---|
| the six `*_EMULATOR_HOST` vars, "all pointing at localhost:4588" | Right about the **target** — one port serves both transports. Wrong about the **form**: `STORAGE_EMULATOR_HOST` is a URL *with scheme*, the rest are bare `host:port` (§7) |
| "GCP clients … read the STANDARD emulator-host variables" | True of the clients, and the emulator contains **no such string at all** — it never validates them (§7) |
| `FLOCI_GCP_STORAGE_MODE` mirrors the AWS storage modes | **Confirmed.** `FLOCI_GCP_STORAGE_MODE=persistent` → banner `Storage: persistent`; default is `memory` |
| port 4588, release 0.8.0, MIT, Java | **All confirmed** — and `/health` reports the real `0.8.0`, unlike floci-az's `"dev"` |
| *(unstated)* | The card names six services to export for. **Firestore and Datastore cannot be read by ICDEV's HTTP connector at all** (§1), and that changes what ships |

### To an earlier draft of this document

Both were caught by re-probing with a different verb, and both would have
shipped a **fabricated absence** — the failure mode this whole spike series
exists to refuse:

1. **"firebaseauth and sts are unreachable."** False. They answered 405 and 415
   — *routed, wrong verb* and *routed, wrong media type*. Both work.
2. **"GKE is declared_unreachable; the `/clusters` path is Kafka's."** Half
   false, and the dangerous half. Kafka does own `/v1/…/clusters`, but GKE is
   fully functional at `/container/v1/…` and really spawns k3s. Recording GKE
   as unreachable would have hidden a working service *and* left the collision
   undescribed.

`FLOCI_GCP_TLS_ENABLED` / `_TLS_CERT_PATH` / `_TLS_KEY_PATH` /
`_TLS_SELF_SIGNED` exist in the binary and were **not exercised**. Recorded as
unmeasured, not as working.

---

## 9. What ICDEV ships on this evidence

**Read and inventory only. No IaC execution.** ICDEV has
`tools/cloud/aws_config_executor.py` and **no GCP analogue**, so:

* `FlociGcpConnector.capabilities.supports_write` is `False`, and `write()`
  returns a refusal naming the missing executor rather than a generic error;
* `emulator_gcp.IAC_EXECUTION_SUPPORTED` is `False` and is asserted by a test;
* the twin adapter reads, and simulates a delta against the `gcp` preset — it
  applies nothing.

Declaring execution support no executor backs is the declared-but-unconsumed
defect this platform ships most, and it is refused here explicitly.

Tables the connector serves are exactly the lanes measured to answer **over
REST** and to reflect writes: `health`, `project`, `buckets`, `topics`,
`secrets`, `key_rings`, `service_accounts`, `sql_instances`, `datasets`,
`gke_clusters` (at the `/container/v1` prefix). Firestore, Datastore and Cloud
Tasks are absent by name and for stated reasons.

### Standing guards, carried forward unchanged

Both from `docs/spikes/twx-spk-01-localstack-go-no-go.md`, and neither is
weakened by anything measured above:

* **Never source a performance, cost or capacity claim from emulator timings.**
  An emulator reproduces the API contract, not its performance characteristics.
  The 92 MB image and the sub-second native start are provisioning facts, not
  performance evidence about Google Cloud.
* **The IAM-sandbox NO-GO stands.** floci-gcp serves no metadata endpoint (§3)
  and its `generateAccessToken` returns an opaque placeholder rather than a
  signed token, so there is less here to mistake for an authorization decision
  than on the Azure sibling — but "less" is not "none", and the ABAC engine
  already answers identity questions offline. A partial emulation is a second
  opinion with no rule for choosing between them.

---

*Provenance: every figure above was produced by `docker run` plus HTTP and gRPC
against the pinned digest on 2026-09-05. An emulated estate is never evidence
about a real one — snapshots taken through this emulator carry provenance
`emulated` (`twin_core.schema.PROVENANCE_EMULATED`).*
