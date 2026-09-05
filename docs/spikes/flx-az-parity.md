# CUI // SP-CTI

# flx-az-parity — what floci-az 0.12.0 ACTUALLY answers

**Measured 2026-09-05** on this host (Windows 11, Docker Desktop 28.5.1,
`linux/amd64`), against:

```
floci/floci-az:0.12.0
sha256:0c673d49bb75b502ea0750f1c1347777483ffc33945539e1d9254438cb441a03
305 MB · native (Quarkus 3.37.4) · edition "floci-az-always-free"
```

Re-derive everything below with:

```bash
docker run -d --name flx-az-probe -p 4577:4577 \
  -v //var/run/docker.sock:/var/run/docker.sock \
  -e FLOCI_AZ_DEFAULT_REGION=usgovvirginia floci/floci-az:0.12.0
docker logs flx-az-probe            # the enabled-services banner
curl -s http://localhost:4577/_floci/health
```

> **Why this document exists before any code.** `flx-az-01` says: *measure
> parity before claiming anything; 0.12.0 is a young emulator and its parity is
> unknown to us until we look.* Every table here is a recorded HTTP response,
> not a README reading. Three of the card's own premises did not survive the
> measurement — see **[Corrections to the card](#corrections-to-the-card)**.

---

## 1. The finding that changes the design

### A subscription-scoped ARM list returns EMPTY for a populated estate

This is the headline, and it is a fabricated-empty of exactly the
`rmf-disc-02` shape — a surface reporting a clean zero for a question it did
not answer.

Measured, in one uninterrupted sequence against the same container:

| # | Call | Result |
|---|------|--------|
| 1 | `PUT /subscriptions/{sub}/resourcegroups/probe-rg` | **201**, resource group created |
| 2 | `PUT .../resourcegroups/probe-rg/providers/Microsoft.Network/virtualNetworks/probe-vnet` | **200**, returns the vnet with a real resource id |
| 3 | `GET .../resourcegroups/probe-rg/providers/Microsoft.Network/virtualNetworks/probe-vnet` | **200**, the vnet |
| 4 | `GET .../resourcegroups/probe-rg/providers/Microsoft.Network/virtualNetworks` | **200**, `{"value":[ the vnet ]}` |
| 5 | `GET .../resourcegroups/probe-rg/resources` | **200**, `{"value":[ the vnet ]}` |
| 6 | `GET /subscriptions/{sub}/providers/Microsoft.Network/virtualNetworks` | **200, `{"value":[]}`** |
| 7 | `GET /subscriptions/{sub}/resources` | **200, `{"value":[]}`** |

Rows 6 and 7 are `200 OK` with a well-formed empty list, for an estate that
demonstrably holds a virtual network. Nothing in the status code, the body
shape or the headers distinguishes that from a genuinely empty subscription.

**Consequence, and it is binding on every consumer we ship:** an inventory
reader MUST enumerate resource groups first and list **per resource group**.
`tools/cloud/emulator_az.py::SUBSCRIPTION_SCOPED_LIST_IS_EMPTY` states this
once and `floci_az_connector` reads it; a reader that lists at subscription
scope will report ZERO for a full estate and look completely healthy doing it.

The resource-group lane itself is subscription-scoped and **does** work
(`GET /subscriptions/{sub}/resourcegroups` returned `probe-rg`), which is
what makes the per-RG fan-out possible at all.

---

## 2. The startup banner is a CONFIGURATION ECHO, not a health signal

floci-az prints an `Enabled Services:` banner naming 24 services, each marked
`[enabled ]`. It cannot be used to decide what a deployment can serve.

Proof — two containers, one with the host docker socket mounted, one with
`FLOCI_AZ_DOCKER_DOCKER_HOST=unix:///nonexistent/does-not-exist.sock`:

```
  socket mounted:  functions [enabled ]  docker: unix:///var/run/docker.sock
  socket ABSENT:   functions [enabled ]  docker: unix:///nonexistent/does-not-exist.sock
```

**The two banners are otherwise byte-identical**, both reporting all 24
services `[enabled ]`. The banner echoes the configured value back; it never
probed it. A consumer deciding `unsupported_without_docker` from this banner
would call a provably socket-less deployment fully capable.

ICDEV therefore decides docker-backing from **its own** seam
(`emulator_az.docker_backed()`, the tri-state that returns `None` rather than
guessing on Windows), never from the emulator's self-report.

---

## 3. The health endpoint carries NO service map

| Path | Status | Body |
|------|--------|------|
| `/_floci/health` | **200** | `{"status":"UP","edition":"floci-az-always-free","version":"dev"}` |
| `/health` | 200 | same |
| `/_localstack/health` | **501** | blob-handler `NotImplemented` XML |
| `/_floci_az/health` | 501 | blob-handler `NotImplemented` XML |

Three consequences:

1. **floci-az is NOT a LocalStack drop-in.** The AWS emulator (`floci/floci`)
   keeps `/_localstack/health`; this one answers **501** there. The AWS seam's
   `HEALTH_PATH` cannot be reused, and neither can the `LOCALSTACK_*` alias
   layer — there is no compatibility contract to inherit.
2. **There is no `services` map**, so `health` cannot enumerate what is up.
   `FlociAzConnector` therefore has **no `services` table** — the AWS
   connector's `services` table has no counterpart here, and inventing one
   that returned `[]` would be a fabrication.
3. **`version` reads `"dev"`, not `0.12.0`.** The image's own
   `FLOCI_AZ_VERSION=0.12.0` env var carries the real version; the health body
   does not. Do not source a version claim from `/_floci/health`.

### An unknown path never 404s — it becomes a blob request

`AzureRoutingFilter` resolves the first path segment as a **storage account
name** and defaults `serviceType` to `blob`. From the container log for
`GET /_localstack/health`:

```
Resolved accountName: _localstack, serviceType: blob, resourcePath: health
Dispatching to handler: BlobServiceHandler_ClientProxy
```

So an unrouted path returns the blob handler's **501 `NotImplemented`**, not a
404. A reachability probe that treats "not 404" as "route exists" will
misread every unimplemented surface on this emulator.

---

## 4. Which ARM resource providers actually answer

`GET /subscriptions/{sub}/providers/{rp}?api-version=…`, measured on **both**
the socket-mounted and socket-absent containers. **The two columns were
identical for all 25 probes**, so the ARM management plane does not degrade
without docker.

**The control discriminates**, which is what makes the 200s meaningful:
`Microsoft.Nonsense/widgets`, `Contoso.Fake/things` and
`Microsoft.Quantum/workspaces` all returned **404 `Resource not found`** — a
registered provider and an unregistered one are distinguishable.

| Resource provider | Result |
|---|---|
| `Microsoft.Network/virtualNetworks` | **200** |
| `Microsoft.Network/networkSecurityGroups` | **200** |
| `Microsoft.Network/publicIPAddresses` | **200** |
| `Microsoft.Network/networkInterfaces` | **200** |
| `Microsoft.Compute/virtualMachines` | **200** |
| `Microsoft.Storage/storageAccounts` | **200** |
| `Microsoft.KeyVault/vaults` | **200** |
| `Microsoft.Sql/servers` | **200** |
| `Microsoft.Cache/redis` | **200** |
| `Microsoft.ContainerService/managedClusters` | **200** |
| `Microsoft.ContainerInstance/containerGroups` | **200** |
| `Microsoft.ContainerRegistry/registries` | **200** |
| `Microsoft.ManagedIdentity/userAssignedIdentities` | **200** |
| `Microsoft.Communication/communicationServices` | **200** |
| `Microsoft.Web/sites` | **404** |
| `Microsoft.EventHub/namespaces` | **404** |
| `Microsoft.ServiceBus/namespaces` | **404** |
| `Microsoft.EventGrid/topics` | **404** |
| `Microsoft.Insights/components` | **404** |
| `Microsoft.OperationalInsights/workspaces` | **404** |
| `Microsoft.ApiManagement/service` | **404** |
| `Microsoft.AppConfiguration/configurationStores` | **404** |
| `Microsoft.DocumentDB/databaseAccounts` | **404** |
| `Microsoft.Nonsense/widgets` *(control)* | 404 |
| `Contoso.Fake/things` *(control)* | 404 |

**A declared ARM lane that does not answer.** At startup floci-az logs:

```
ARM provider lane: 4 namespaces → [Microsoft.OperationalInsights,
                                   Microsoft.EventGrid, Microsoft.Insights,
                                   Microsoft.ApiManagement]
```

All four of those namespaces returned **404** for the resource types probed
above. The namespace is registered; the resource type is not. This is the
banner problem again one level down — a registration log is not a measurement.

---

## 5. Data planes, measured

| Service | How reached | Result |
|---|---|---|
| **blob** | `PUT /{account}/{container}?restype=container`, `PUT`/`GET` blob, `GET /{account}?comp=list` | **works** — 201 / 201 / 200, content round-trips, XML `EnumerationResults` |
| **queue** | `PUT /{account}-queue/{q}`, `GET /{account}-queue?comp=list` | **works** — 201 / 200 XML |
| **table** | `POST /{account}-table/Tables`, `GET .../Tables` | **works** — 201, and the created table is listed back |
| **cosmos** | `GET /dbs` with `x-ms-version` | **works** — 200 `{"_rid":"","_count":0,"Databases":[]}`; the log confirms a `Java-SDK cosmos root route` |
| **keyvault** | `Host: {vault}.vault.azure.net` + **bearer token** | **works** — 401 without a token; **200** with one, and a `PUT` secret is listed back |
| **entra** | `GET /{tenant}/v2.0/.well-known/openid-configuration` | **works** — 200 OIDC document |
| **managedid / IMDS** | `GET /metadata/identity/oauth2/token` + `Metadata: true` | **works** — 200, issues a real signed JWT (used to satisfy Key Vault above) |
| **graph** | `GET /v1.0/servicePrincipals` | **works** — 200, seeded service principals |
| **ACS email** | `POST /emails:send?api-version=2023-03-31` | **works** — 202 with an operation id (captured in memory; **nothing is delivered**) |
| **appconfig** | every path- and Host-style route tried | **NOT REACHED** — 501 blob handler |
| **eventgrid** | `POST /api/events`, path- and Host-style | **NOT REACHED** — 501 blob handler |
| **monitor** | `Microsoft.Insights/metrics` | 404 |
| **functions** | `Microsoft.Web/sites`, `/admin/functions`, `/_floci/functions` | **NOT REACHED** — 404 / 501 |
| **sql** *(data plane)* | — | banner itself says `data-plane: none` |

`appconfig`, `eventgrid` and `functions` are the sharp cases: all three are
`[enabled ]` in the banner and **none is reachable by any route found**. They
are recorded as `declared_unreachable` — deliberately not merged with "absent",
because the repairs differ (find the route / file upstream, versus do not
design against it at all).

Key Vault's **401-not-501** is the tell that separates "unrouted" from
"routed but unauthenticated": a 501 means the request fell through to the blob
handler, a 401 means a real handler took it.

---

## 6. Ports — measured, and the card was incomplete

The image exposes **only `4577/tcp`**. The ranges below are ports floci-az
forwards to containers it *spawns*, read from the startup banner:

| Service | Banner | Card said |
|---|---|---|
| eventhub | `docker: amqp:5672  ns:emulatorNs1` | AMQP 5672 ✓ |
| **servicebus** | `docker: amqp:5673  (on-demand)` | **omitted — and it is 5673, not 5672** |
| aks | `docker: k3s:rancher/k3s:latest  ports:6443-7443` | 6443-7443 ✓ |
| redis | `docker: image:valkey/valkey:8-alpine  ports:6379-6399` | 6379-6399 ✓ |
| **acr** | `docker: registry:registry:2  ports:5000-5099` | **omitted entirely** |
| Kafka 9093 | **not present in the banner** | claimed "optional Kafka 9093" — not enabled by default |

Per the AWS seam's standing rule these ranges are **declared and deliberately
not published** by every caller: publishing ~1,100 host ports the deployment
cannot serve through declares a capability nothing consumes, and collides with
any local Redis on 6379 and any local registry on 5000.

`aci` and `vm` report `docker: mocked  (no docker)` **even with the socket
mounted** — they are mocked unconditionally, so they are not container-backed
in practice.

---

## 7. Corrections to the card

Recorded because the card asked for measurement rather than a README reading,
and three of its premises did not survive it.

| Card said | Measured |
|---|---|
| "AMQP 5672 and optional Kafka 9093 (Event Hubs)" | Event Hubs AMQP **5672**; Service Bus AMQP **5673**, which the card omits; **no Kafka 9093** in the default banner |
| extra ports are AMQP / k3s / Redis | plus **ACR registry 5000-5099**, omitted by the card |
| "Azure Functions REQUIRES the Docker socket to spawn runtime containers" | Not contradicted, but **unverifiable here**: no Functions route was reachable at all (`Microsoft.Web/sites` 404), so the socket requirement could not be exercised |
| release 0.12.0 | image env says `FLOCI_AZ_VERSION=0.12.0`; **`/_floci/health` reports `"version":"dev"`** |

`FLOCI_AZ_TLS_ENABLED` (the card's protocol-sniffing HTTPS-on-4577 claim) was
**not exercised**. It is recorded as unmeasured, not as working.

---

## 8. What ICDEV ships on this evidence

**Read and inventory only. No IaC execution.** ICDEV has
`tools/cloud/aws_config_executor.py` and **no Azure analogue**, so:

* `FlociAzConnector.capabilities.supports_write` is `False`, and `write()`
  returns a refusal naming the missing executor rather than a generic error;
* `emulator_az.IAC_EXECUTION_SUPPORTED` is `False` and is asserted by a test;
* the twin adapter reads, and simulates a delta against the `azure_gov`
  preset — it applies nothing.

Declaring execution support no executor backs is the declared-but-unconsumed
defect this platform ships most, and it is refused here explicitly.

### Standing guards, carried forward unchanged

Both from `docs/spikes/twx-spk-01-localstack-go-no-go.md`, and neither is
weakened by anything measured above:

* **Never source a performance, cost or capacity claim from emulator timings.**
  An emulator reproduces the API contract, not its performance characteristics.
  The 305 MB / ~13 s startup figures here are provisioning facts, not
  performance evidence about Azure.
* **The IAM-sandbox NO-GO stands, and now covers Entra.** floci-az issues real
  signed JWTs from its IMDS endpoint and serves an OIDC discovery document with
  `validate-tokens:false`. That is a token *issuer*, not an authorization
  decision, and the ABAC engine already answers identity questions offline. A
  partial emulation is a second opinion with no rule for choosing between them.

### Not designed against, on purpose

`appconfig`, `eventgrid`, `functions`, `monitor`, the SQL data plane, Event
Hubs and Service Bus. Every one is either `declared_unreachable` or 404 on the
management plane. A capability declared here that nothing can consume would be
the exact defect this project exists to stop shipping.

---

*Provenance: every figure above was produced by `docker run` + HTTP against
the pinned digest on 2026-09-05. An emulated estate is never evidence about a
real one — snapshots taken through this emulator carry provenance `emulated`
(`twin_core.schema.PROVENANCE_EMULATED`).*
