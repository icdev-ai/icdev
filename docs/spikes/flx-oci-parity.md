# CUI // SP-CTI

# flx-oci-parity — what floci-oci 0.4.0 ACTUALLY answers, and what ICDEV can actually do with it

**Measured 2026-09-05** on this host (Windows 11, Docker Desktop 28.5.1,
`linux/amd64`), against:

```
floci/floci-oci:0.4.0
sha256:584fd7f977077ab040063d7c2efaaaa1beabacccd903f5297eaa7bbe8f744a8b
76.0 MB · native (Quarkus 3.37.4) · edition banner "OCI Local Emulator · Always Free"
```

Re-derive the whole document with:

```bash
docker run -d --name flx-oci-probe -p 4599:4599 \
  -v //var/run/docker.sock:/var/run/docker.sock floci/floci-oci:0.4.0
docker logs flx-oci-probe            # the Enabled services line
curl -s http://localhost:4599/health
```

> **Why this document exists before any code.** `flx-oci-01` says floci-oci is
> **the least proven of the four** siblings — created 2026-07-28, five weeks
> old at measurement — and that *"if measured parity does not cover what
> ICDEV's OCI paths actually need, the correct outcome is to record that and
> ship the compose profile WITHOUT wiring a canvas to it."*
>
> **That is the outcome.** But not for the reason the card anticipated, and the
> difference is the whole finding — see [§1](#1-the-finding-that-decides-the-card).

---

## 1. The finding that decides the card

### The emulator is not the weak half. ICDEV is.

The card expected the risk to sit in a five-week-old emulator. Measured, it
does not. floci-oci answers **eight services**, every REST lane returns real
data, every write reflects in its list, and `compartmentId` is honoured
([§4](#4-every-write-reflects--and-compartmentid-is-honoured)). It has no
analogue of the floci-az subscription-scope trap and no analogue of the
floci-gcp gRPC-only blind spot.

The gap is on **ICDEV's** side, and it is total:

| ICDEV OCI consumer | Can it be pointed at an emulator? | Measured evidence |
|---|---|---|
| `OCIObjectStorageProvider` | **No** | `upload` → `return False  # Requires full OCI config`; `download` → `None`; `list_objects` → `[]`; `delete` → `False`. **No network call in the class at all.** |
| `OCISecretsProvider` | **No** | `get_secret` → `None`, `put_secret` → `False`, `list_secrets` → `[]`, `delete_secret` → `False` |
| `OCIKMSProvider` | **No** | `encrypt` / `decrypt` / `generate_data_key` all → `None` |
| `OCIIAMProvider` | **No** | every method a stub; source comment: *"OCI uses dynamic groups + policies — simplified stub"* |
| `OCIMonitoringProvider` | **No** | `send_metric` has a body but constructs its client from `oci.config`; `send_log` is a stub |
| `OCIRegistryProvider` | **No** | `list_repositories` is a stub; no endpoint parameter |
| `oci_genai_provider` | **Endpoint yes — service NO** | passes `service_endpoint=`, but only ever `https://inference.generativeai.{region}.oci.oraclecloud.com` |
| `embedding_provider` (OCI Cohere) | **Endpoint yes — service NO** | same: `GenerativeAiInferenceClient(service_endpoint=…)` |

Two facts close it:

1. **Exactly two ICDEV sites accept a `service_endpoint`, and both target
   Generative AI inference — which this emulator does not have.** Measured:

   | Path | Result |
   |---|---|
   | `GET /20231130/actions/generateText` | **404** |
   | `GET /20231130/chat` | **404** |
   | `GET /generativeai` | **404** |
   | `GET /20190101/models` | **404** |

2. **The `oci` SDK is neither installed nor declared.** `import oci` raises
   `ModuleNotFoundError`; `oci` appears nowhere in `requirements.txt`. Every
   provider above guards on a `_HAS_OCI_*` flag that is therefore `False`, so
   even the two non-stub methods are unreachable on this deployment.

**Therefore: there are ZERO existing ICDEV OCI code paths that a running
floci-oci can serve.** Not "few", not "some need an endpoint parameter" —
zero. Pointing an endpoint at the provider layer would change nothing, because
`list_objects` returns `[]` before it would ever reach a socket.

This is the direct analogue of the AWS scope guard recorded for `flx-compose-01`
("67 boto3 sites exist tree-wide and only 3 honour `endpoint_url`") — except
that for OCI the equivalent number is **0 of 8**.

### What this licenses, and what it forbids

| Ships | Why it is honest |
|---|---|
| the compose profile | an operator can run the emulator; that claim is measured |
| the `emulator_oci` seam | a configuration seam that reads env and states the measured traps |
| the DataBridge connector | it reads **over plain `urllib` through the governed broker**, exactly as `floci_gcp_connector` does. It is a NEW consumer, not an existing OCI path, and every lane it declares was measured answering |
| the Twin Observatory adapter | reads through that same governed door |

| Does NOT ship, on purpose | Why |
|---|---|
| **any wiring of `tools/cloud/*_provider.py` to the emulator** | the stubs return constants; an endpoint cannot reach them. Wiring it would declare a capability whose first call returns `[]` — the platform's signature defect |
| **any canvas or page** | `floci-oci` is registered as a `core_extension`, like its three siblings. No page, so the 8-point page gate does not apply |
| **any IaC-execution claim** | ICDEV ships `aws_config_executor.py` and **no OCI analogue**. `IAC_EXECUTION_SUPPORTED = False`, asserted by a test |
| **`oci` added to `requirements.txt`** | adding an undeclared-import-census dependency to make stubs *look* reachable would not make them reachable |

**A follow-on card that wants ICDEV's OCI provider layer to work must implement
the providers first.** The emulator is not the blocker and adding more emulator
surface will not help.

---

## 2. Two self-reports of the service list, and they disagree

The container publishes its service set **twice**, and the two do not match:

| Source | Count | Set |
|---|---|---|
| startup log, `ServiceRegistry` | **7** | identity, kms, objectstorage, oke, queue, streaming, vault |
| `GET /health` → `services` | **8** | *the seven above* **+ functions** |

`functions` is absent from the log line and present in `/health` — and
**measured, `functions` works**: `GET /20181201/applications?compartmentId=…`
returns 200, and a create round-trips ([§4](#4-every-write-reflects--and-compartmentid-is-honoured)).
So the **startup log line is the incomplete one**, and a future card that
enumerated services by grepping the banner — the obvious move, and what the
floci-az seam had to do because that emulator has no map — would silently drop
a working service.

`emulator_oci.SERVICES` is therefore taken from the **measured lanes**, and
`SERVICE_LIST_SELF_REPORTS_DISAGREE = True` states the discrepancy once so
nobody re-derives it.

---

## 3. `/health` carries a service map, and the map is a CONFIGURATION ECHO

Third emulator, third time. Proof — two containers, one with the host docker
socket mounted, one with `FLOCI_OCI_DOCKER_DOCKER_HOST` **and** `DOCKER_HOST`
pointed at `unix:///nonexistent/does-not-exist.sock`:

```
socket-mounted services: 8 | socket-absent services: 8
services maps IDENTICAL: True
whole bodies IDENTICAL:  True
distinct status values across all 8: ['running']
```

Byte-identical for a deployment that **provably cannot start a container**
([§5](#5-oke-is-the-only-container-backed-service-and-it-is-broken)), and
`"running"` is the only value the vocabulary was ever observed to hold. It
reports enablement, never health — the `rem-hyg-17` shape, and the same defect
as floci-gcp's 23-service map and floci-az's `[enabled ]` banner.

So two constants, not one:

```python
HEALTH_HAS_SERVICE_MAP              = True   # the key exists and parses
HEALTH_SERVICE_MAP_IS_ENABLEMENT_ONLY = True # and it is NOT a health signal
```

The connector's `enabled_services` table strips the status for the same reason
its GCP sibling does: a constant must not be renderable as a health badge.

### Three emulators, three health paths

| Emulator | Health path | On the other two |
|---|---|---|
| floci (AWS) | `/_localstack/health` | — |
| floci-az | `/_floci/health` | — |
| **floci-oci** | **`/health`** | `/_floci/health` **404**, `/_localstack/health` **404** |

floci-oci shares floci-gcp's path but **is not a LocalStack drop-in**: it 404s
on `/_localstack/health`. `/q/health` and `/healthz` also 404 — the Quarkus
management surface is not exposed.

An unrouted path here **404s cleanly** (unlike floci-az, where the routing
filter answers 501 from the blob handler), so `urlopen` raising on non-2xx is
the correct probe.

---

## 4. Every write reflects — and `compartmentId` IS honoured

Measured as one write→list sequence per service, against tenancy
`ocid1.tenancy.oc1..flocilocaltenancy0000…`:

| Service | Create | Status | List afterwards |
|---|---|---|---|
| Object Storage | `POST /n/floci-local/b/` | 200 | the bucket |
| Identity | `POST /20160918/compartments` | 200 | the compartment |
| Vault/KMS | `POST /20180608/vaults` | 200 | the vault |
| Queue | `POST /20210201/queues` | **202** | the queue |
| Streaming | `POST /20180418/streams` | 200 | the stream |
| Functions | `POST /20181201/applications` | 200 | the application |
| OKE | `POST /20180222/clusters` | **202** | the cluster — **see §5** |

**No floci-az trap.** A list scoped to a *bogus* compartment returns **0 rows**
for vaults, streams, clusters and applications — so the filter is real, and a
populated estate cannot read as empty because of a scope mistake:

```
/20180608/vaults      under BOGUS compartment -> 0 rows
/20180418/streams     under BOGUS compartment -> 0 rows
/20180222/clusters    under BOGUS compartment -> 0 rows
/20181201/applications under BOGUS compartment -> 0 rows
```

**The namespace IS discoverable**, unlike floci-gcp's project id: `GET /n/`
returns the bare JSON string `"floci-local"`. So `emulator_oci` can offer
`namespace(probe=True)` where the GCP seam could only read configuration.

### The controls discriminate

Which is what makes the 200s above mean anything:

| Control | Result |
|---|---|
| `GET /20990101/nonsense` | 404 |
| `GET /totally/fake/path` | 404 |
| `GET /20160918/instances` (Compute) | 404 |
| `GET /20160918/vcns` (Networking) | 404 |
| `GET /20180418/streamPools` | 404 |

**There is no Compute, Networking, Database, ADW or Load Balancer surface at
all.** Eight services is the whole emulator.

### Required-parameter handling is inconsistent

Not a defect we can fix, but a caller must not generalise from one lane:

| Lane | `compartmentId` omitted |
|---|---|
| `/20180608/vaults` | **400** |
| `/20180418/streams` | **200** |
| `/n/{ns}/b/` | **400** |

### One lane wraps its rows; the other eight do not

| Lane | Envelope |
|---|---|
| `/20210201/queues` | **`{"items": [...]}`** |
| buckets, compartments, vaults, streams, applications, clusters, users, keys | **bare `[...]`** |

`queue` is the *only* wrapped lane. A `rows_from` that assumed either shape
would be wrong about the other, so `RESPONSE_ROW_KEY` records it per lane and
`rows_from` is the one place the distinction lives.

---

## 5. OKE is the only container-backed service, and it is BROKEN

This is the sharpest hazard on this emulator and it is **a different shape from
both siblings** — do not lend it either of their vocabularies.

### With a docker socket: it spawns, the spawn DIES, and the API says ACTIVE

```
POST /20180222/clusters  →  202
```

It really does spawn a container — `rancher/k3s:v1.30.1-k3s1`. That container
then **exits immediately**:

```
docker logs floci-oci-oke-ocid1.cluster…
  time="…" level=fatal msg="--token is required"
```

The emulator never passes k3s a `--token`, so **OKE cannot work at all in
0.4.0**. And the API never re-checks:

```json
{ "name": "probe-cluster",
  "lifecycleState": "ACTIVE",
  "endpoints": { "kubernetes": "https://127.0.0.1:6443",
                 "privateEndpoint": "10.0.0.10:6443" },
  "kubernetesVersion": "v1.29.1" }
```

`lifecycleState` reads **ACTIVE** for a cluster whose only container is dead,
and it advertises an endpoint with **no listener** (measured: `curl` to
`https://127.0.0.1:6443/version` returns `000`, connection refused).

`kubernetesVersion` is also an **echo of the request** — `v1.29.1` because that
is what was asked for, while the image is `v1.30.1-k3s1`. It is not a statement
about anything running.

### Without a docker socket: it fails HONESTLY

```
POST /20180222/clusters  →  500 Internal Server Error
GET  …/clusters          →  []          (nothing was recorded)
```

**So the sibling constant does not transfer.** floci-gcp needed
`FABRICATED_SUCCESS_WITHOUT_DOCKER = {"cloudrun"}` because Cloud Run returns a
fabricated 200 with no socket. Here the socket-absent case is the *honest* one,
and the fabrication happens **with** a socket:

```python
FABRICATED_SUCCESS_WITHOUT_DOCKER = frozenset()   # measured empty, not omitted
FABRICATED_ACTIVE_WITH_DOCKER     = frozenset({"oke"})
```

Two different constants because they send a reader to two different places: one
says *"you have no socket"*, the other says *"the emulator is broken and no
socket will fix it"*.

### Consequence for the twin

`oke` is declared container-backed, so a socket-absent deployment reports
`unsupported_without_docker` rather than an empty list. But an `oke` row
returned by a socket-**present** deployment is *also* not evidence of a working
cluster, and the twin must not score it as one. `OKE_LIFECYCLE_IS_UNVERIFIED`
carries that, and the connector never promotes `lifecycleState` to a health
verdict.

### The k3s tag IS pinned — one thing better than the GCP sibling

`rancher/k3s:v1.30.1-k3s1`, not `:latest`. floci-gcp spawns two `:latest` tags,
which an air-gap cache keyed on digest cannot complete from a list. floci-oci
spawns exactly one image and it is version-pinned, so the air-gap set is
enumerable. (It is also, today, an image there is no reason to cache: the
service it backs does not work.)

---

## 6. Configuration: what is honoured, and what the card asserted that is not

| Variable | Measured |
|---|---|
| `FLOCI_OCI_DEFAULT_REGION` | **honoured** — `us-phoenix-1` changes the banner *and* the OCID region code (`ocid1.vault.oc1.**phx**.…` vs `…oc1.**iad**.…` for `us-ashburn-1`) |
| `FLOCI_OCI_STORAGE_MODE` | **honoured** — `persistent` changes `Storage:` in the banner |
| `FLOCI_OCI_PERSISTENCE` | **NOT honoured** — banner still reads `memory` |
| `FLOCI_PERSISTENCE` | **NOT honoured** |
| `FLOCI_OCI_PERSISTENCE_MODE` | **NOT honoured** |

The card said floci-oci *"offers in-memory, persistent, hybrid and WAL
persistence like its siblings"* without naming the switch. The switch is
**`FLOCI_OCI_STORAGE_MODE`**; three plausible spellings are silently ignored,
which is the failure mode where an operator believes they enabled persistence
and did not. Default is `memory`.

### The advertised endpoint is the CONTAINER's, not the mapped one

A vault created on a container published at host port **4601** returns:

```json
"cryptoEndpoint": "http://localhost:4599"
```

— the container-internal port, hard-coded. So a client that follows an endpoint
out of a response body goes to the wrong place on any non-default mapping.
`emulator_oci` composes URLs from `endpoint()` and never from a response field;
`RESPONSE_ENDPOINTS_ARE_CONTAINER_LOCAL = True` records why.

---

## 7. What was NOT measured, and is therefore not claimed

Recorded because the card asserts them and this document may not silently
inherit an assertion it did not test.

| Claim | Status |
|---|---|
| *"the OCI CLI reaches it with `--endpoint`"* | **UNMEASURED.** No `oci` CLI is installed on this host. `emulator_oci` carries no CLI helper and no `--endpoint` claim |
| *"a `bin/ocilocal` wrapper injects that into every call"* | **UNMEASURED** — same reason. Nothing in this change depends on it |
| *"62 stars, MIT, GraalVM native"* | native runtime **confirmed** from the banner (`native (powered by Quarkus 3.37.4)`, 0.015s start); licence and stars not verified here and not load-bearing |
| *"hybrid and WAL persistence"* | **UNMEASURED.** Only `memory` (default) and `persistent` were exercised; the other two values were not, so no constant enumerates them |

Both siblings' standing guards carry forward unchanged and are **not**
re-litigated here:

* **Never source a performance, cost or capacity claim from this emulator** —
  `docs/spikes/twx-spk-01-localstack-go-no-go.md`. An emulator reproduces an
  API contract, not performance characteristics. The 0.015s start time above is
  quoted as evidence of a native binary, never as a latency figure.
* An emulated estate is **never** readable as an observed one — twin snapshots
  carry provenance `emulated`.

---

## 8. Corrections to the card

Four of the card's premises did not survive measurement. Recorded so the next
reader trusts the table above over the card text.

1. **"Measure before promising" was aimed at the wrong half.** The emulator
   over-delivered relative to the card's caution; ICDEV's OCI providers
   under-deliver completely ([§1](#1-the-finding-that-decides-the-card)). The
   `not yet` the card asked for is real, but it is a `not yet` about **ICDEV**.
2. **"Port 4599"** — correct.
3. **"in-memory, persistent, hybrid and WAL persistence"** — the switch is
   `FLOCI_OCI_STORAGE_MODE` and three plausible spellings are ignored; only two
   of the four modes were exercised ([§6](#6-configuration-what-is-honoured-and-what-the-card-asserted-that-is-not)).
4. **"the OCI CLI reaches it with `--endpoint` (a `bin/ocilocal` wrapper …)"** —
   unmeasured on this host and claimed nowhere in the shipped code
   ([§7](#7-what-was-not-measured-and-is-therefore-not-claimed)).

And one correction to an **earlier draft of this document**: the first pass
recorded `functions` as absent, having taken the service set from the startup
log line. `/health` lists it and it answers — [§2](#2-two-self-reports-of-the-service-list-and-they-disagree)
exists because of that mistake.
