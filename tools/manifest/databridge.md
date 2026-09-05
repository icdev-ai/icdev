# DataBridge

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## DataBridge
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Connector ABC | tools/databridge/connector.py | DataConnector ABC, request/response dataclasses, connector capabilities, and schema definitions for all connector implementations | (library) | DataConnector ABC |
| Connector Registry | tools/databridge/registry.py | Central registration and lookup for DataBridge connectors via decorator pattern; bulk-loads FORGE connectors from database | (library) | register_connector() decorator, get_connector_instance() |
| Connection Manager | tools/databridge/connection_manager.py | DataBridge connection lifecycle management | --create, --list, --test, --json | Connection records |
| Schema Engine | tools/databridge/schema_engine.py | DataBridge schema discovery and mapping | --discover, --map, --json | Schema maps |
| Health Base | tools/databridge/connectors/health_base.py | Base class for health-check connectors (D-CF-5) | (library) | HealthConnector ABC |
| SOAP Base | tools/databridge/connectors/soap_base.py | Base class for SOAP/XML-RPC connectors (D-CF-5) | (library) | SoapConnector ABC |
| Forge Agent | tools/databridge/forge/forge_agent.py | Generate connector from OpenAPI spec — template + optional LLM (D-CF-2) | (library) — `forge_from_spec()` | Generated connector |
| Forge Spec Parser | tools/databridge/forge/spec_parser.py | Parse OpenAPI/Swagger specs into normalized schema | (library) | Parsed spec |
| Forge Static Validator | tools/databridge/forge/static_validator.py | Validate generated connector against ABC contract | (library) | Validation results |
| Forge Base Selector | tools/databridge/forge/base_selector.py | Select appropriate connector base class from spec | (library) | Base class selection |
| Forge Integration Tester | tools/databridge/forge/integration_tester.py | Docker/subprocess sandbox testing for generated connectors (D-CF-4) | (library) | Test results |
| Forge Import Handler | tools/databridge/forge/import_handler.py | Import and register generated connectors | (library) | Registration result |
| Forge Marketplace Publisher | tools/databridge/forge/marketplace_publisher.py | Publish forge connectors to marketplace (D-CF-8) | (library) | Published asset |
| Forge Community Hub | tools/databridge/forge/community_hub.py | Browse, rate, and manage community connectors (F10) | --browse, --featured, --json | Connector listings |
| Scale Worker Pool | tools/databridge/scale/worker_pool.py | ThreadPoolExecutor wrapper for concurrent sync (D-SC-1) | (library) | WorkerPool |
| Scale Write Batcher | tools/databridge/scale/write_batcher.py | WAL + batch flush for sync log/audit writes (D-SC-3) | (library) | WriteBatcher |
| Scale Backpressure | tools/databridge/scale/backpressure.py | Backpressure monitoring with optional psutil (D-SC-6) | (library) | Pressure metrics |
| Scale Chunked Pipeline | tools/databridge/scale/chunked_pipeline.py | Chunked data pipeline for large sync operations | (library) | Pipeline results |


## DataBridge Secret Resolvers
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Env Resolver | *(built-in)* | Direct environment-variable lookup; no external dependency; default backend when `secret_backend: env`; also serves as universal fallback for all other backends | ENV_VAR_NAME | Plaintext secret |
| Vault Resolver | tools/databridge/resolvers/vault_resolver.py | HashiCorp Vault KV secret resolver; resolves `vault:path/to/secret#field` refs; 5-min TTL in-process cache (thread-safe); reads VAULT_ADDR + VAULT_TOKEN from env; KV v1 and v2 supported | vault:path/to/secret#field | Plaintext secret |
| AWS Resolver | tools/databridge/resolvers/aws_resolver.py | AWS Secrets Manager resolver; resolves `aws:secret-name[#json-key]` refs; GovCloud endpoint (us-gov-west-1 default); creds from env vars or EC2/ECS instance profile; JSON secrets support key extraction | aws:secret-name[#json-key] | Plaintext secret |
| File Resolver | tools/databridge/resolvers/file_resolver.py | Air-gap file resolver; reads plaintext from `{secret_files_root}/{secret_id}`; path-traversal blocked (resolved path must be under root); root from DATABRIDGE_SECRET_FILES_ROOT env or args/databridge_config.yaml | file:secret_id | Plaintext secret |

### Resolver Chain

Configured via `args/databridge_config.yaml` key **`secret_backend`** (default: `env`).

**Step 1 — Primary backend** (selected by `secret_backend` value or ref prefix):

| Backend | Config value | Ref prefix | Behavior |
|---------|-------------|------------|----------|
| `env` | `env` | *(no prefix)* | Reads the key directly as an environment variable name |
| `vault` | `vault` | `vault:` | Calls HashiCorp Vault KV API via `hvac`; requires `VAULT_ADDR` + `VAULT_TOKEN` in env |
| `aws` | `aws` | `aws:` | Calls AWS Secrets Manager via `boto3`; GovCloud region by default; creds from env or instance profile |
| `file` | `file` | `file:` | Reads a plaintext file from `{secret_files_root}/{secret_id}`; path-traversal blocked; air-gap safe |

**Step 2 — Env fallback**: If the primary backend raises any error, the chain retries by looking up the normalized key as an environment variable (uppercase, non-alphanumeric characters replaced with underscores).

**Step 3 — `SecretNotFoundError`**: Raised if both the primary backend and the env fallback fail. Never silently returns an empty string.

**Config reference** (`args/databridge_config.yaml`):
```yaml
secret_backend: env           # env | vault | aws | file  (default: env)
vault_addr: ""                # convenience ref; resolver reads VAULT_ADDR env var
aws_region: us-gov-west-1     # GovCloud default; override with AWS_REGION env var
secret_files_root: /etc/strategos/secrets  # override with DATABRIDGE_SECRET_FILES_ROOT env var
```

## DataBridge (Additional)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Alpaca Connector | tools/databridge/connectors/alpaca_connector.py | Alpaca Markets trading API connector (equities + crypto) | --json | Connection status |
| ACLED Connector | tools/databridge/connectors/acled_connector.py | Armed Conflict Location & Event Data API connector; incremental by event_date; normalizes headline/geo_hint/signal_date | --table events --since DATE --json | Conflict event records |
| GDELT Connector | tools/databridge/connectors/gdelt_connector.py | GDELT Project API connector (events + GKG); no auth; incremental by SQLDATE; normalizes headline/geo_hint/signal_date | --table events|gkg --query TEXT --since SQLDATE --json | Event/GKG records |
| SaaS Base Connector | tools/databridge/connectors/saas_base.py | REST/SaaS API base connector class for Connector Forge | (library) | SaaSBaseConnector class |
| RSS Connector | tools/databridge/connectors/rss_connector.py | RSS 2.0 and Atom feed connector; no auth; egress-guarded (https-only, allowlist, SSRF range check) and stopped in air-gap; a non-2xx HTTP status is an error, never zero rows; normalizes headline/body_excerpt/signal_date/source; max_items default 50. Module form only — the file uses package-relative imports | python -m tools.databridge.connectors.rss_connector --url URL --feed-id ID --limit N --json | Feed entry records |
| Connection Seeder | tools/databridge/seed_connections.py | Seeds db_connections from args/databridge_connections.yaml; refuses a literal secret (auth_secret_ref must be an env:/vault:/aws:/file: reference), refuses a banner as a classification, all-or-nothing so a grant is never half-wired | --seed / --dry-run / --verify / --list [--json] | Created/updated connection ids |
| Sandbox Adapter | tools/databridge/forge/sandbox_adapter.py | Sandbox environment adapter for connector testing | --json | Adapter status |
| Sandbox Manager | tools/databridge/forge/sandbox_manager.py | Sandbox lifecycle manager for generated connectors | --json | Sandbox status |


## DataBridge (External Feeds)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| DataBridge Feeds API | tools/dashboard/api/databridge_feeds.py | Service-to-service HTTP surface GET/POST /api/databridge/v1/<connector>/<table> (ctx-expose-05). Connector allowlist (v1: icdev_demand, icdev_cpmp — internal connectors stay unreachable regardless of scopes); auth via g.cortex_binding set by the central icdev_ctx_ branch in tools/dashboard/auth.py; per-direction scopes databridge:<connector>:read|write. Dashboard session users are denied (401) — service keys only. Both exposed connectors read the local platform DB and take no endpoint or secret. Mirrored to icdev/tools/dashboard/api/. | HTTP + service key | JSON {ok, data, row_count, metadata, classification} |
| ICDEV Demand Connector | tools/databridge/connectors/icdev_demand_connector.py | Read-only local-DB connector exposing the RFI/proposals demand pipeline's aggregated capability gaps (rfi_capability_gaps via tools/govcon/rfi_demand.list_demand_signals) through the feeds surface — external workforce tools (compass supply-vs-demand, prem-lcatq-04) consume opportunity demand with a databridge:icdev_demand:read scoped service key; no direct DB access. Table: demand_signals (status filter + limit). Mirrored to icdev/. | ConnectorRequest(table_name='demand_signals', limit, filters.status) | ConnectorResponse rows |
| ICDEV CPMP Connector | tools/databridge/connectors/icdev_cpmp_connector.py | Contract-portfolio bridge for external delivery tools (compass prem-cpmp): READ contracts/clins/milestones/deliverables (Bell-LaPadula read-down against the service key's ceiling via _caller_classification injected by the feeds blueprint; compartmented rows never leave); WRITE cpmp_evm_periods (PV/EV/AC -> CPI/SPI/CV derived) + deliverable submission status (government-side accept/reject excluded). Scopes databridge:icdev_cpmp:read|write. Mirrored to icdev/. | ConnectorRequest(table, filters.contract_id) / write payloads | ConnectorResponse rows / {id, cpi, spi} |
| DataBridge Agent Broker | `tools/databridge/broker.py` | The ONLY route from an agent to an external SaaS connector. `fetch(agent_id, connector, table, *, filters, query, limit, classification) -> FetchOutcome` and `list_available(agent_id) -> list[dict]`. Chain, in order: air-gap interlock; per-agent authorization of the (agent, connector, table) triple against `args/databridge_agent_access.yaml` (deny-all by default, and a missing file also denies); read-only enforcement (no write path exists); fail-closed outbound redaction of free-text filters via GovConSanitizer; the egress guard in saas_base; and one audit row per call including denials. Never raises -- a denial is a result an agent can reason about. Exposed as MCP `databridge_fetch`/`databridge_sources` and the `external_data` toolset bundle; deliberately NOT a ToolRunner entry, which matches command strings exactly and cannot allowlist a parameterised fetch. |
| Floci Emulator Connector | tools/databridge/connectors/floci_connector.py | DataBridge connector over the LOCAL floci AWS emulator (a LocalStack drop-in, port 4566, OFF by default). Registry key `floci`; the old `localstack` key is GONE, not aliased -- the registry answers None for it, which is a loud failure, where two live names for one connector are two things to keep in step. Reads the ONE seam `tools/cloud/emulator.py` (flx-seam-01) rather than the environment. Seven logical tables -- health, services, s3_buckets, dynamodb_tables, lambda_functions, sqs_queues, ecr_repositories -- and EACH DECLARES `docker_backed`: a container-backed table on a socket-less host returns status `unsupported_without_docker`, NEVER `[]`, because an unanswerable question is not an empty answer (the rmf-disc-02 defect). Disabled returns `disabled` and raises nothing. Reachable through `broker.fetch` under the flx-bridge-02 grant (`twin_observatory_analyst`); a direct import is the ungoverned side channel cef-fnd-03 exists to close. `auth_method: none` -- credentials() is hard-wired to the dummy pair, so there is no credential to reference (flx-bridge-01, flx-bridge-02) | ConnectorRequest(table_name, filters, limit) | ConnectorResponse rows + status ok \| disabled \| unsupported_without_docker \| error |
| Floci-AZ Emulator Connector | tools/databridge/connectors/floci_az_connector.py | DataBridge connector over the LOCAL floci-az Azure emulator (port 4577, OFF by default). Registry key `floci_az`; reads the ONE seam `tools/cloud/emulator_az.py`. **READ ONLY** — `supports_write=False` and `write()` returns status `unsupported` NAMING the absent Azure IaC executor, because a capability gap and a failed call are different findings. Nine logical tables in three SCOPES (`table_scope()`): `probe` (health), `subscription` (subscriptions, resource_groups — the two lanes measured to answer truthfully), and `resource_group` (resources, virtual_networks, network_security_groups, storage_accounts, key_vaults, managed_identities), each of which FANS OUT one ARM list per resource group. THE FAN-OUT IS THE POINT: a subscription-scoped list returns an empty `200` for a populated estate, so a failed resource-group enumeration returns `error` — NEVER `ok` with `row_count: 0` — and a partly-failed sweep returns `partial` with the failing groups NAMED. No `services` table: floci-az's health body carries no service map, and one returning `[]` would be a fabrication. Reachable through `broker.fetch` under the flx-az-01 grant (`twin_observatory_analyst`) (flx-az-01) | ConnectorRequest(table_name, limit) | ConnectorResponse rows + status ok \| partial \| disabled \| unsupported \| error |
| Floci-GCP Emulator Connector | tools/databridge/connectors/floci_gcp_connector.py | DataBridge connector over the LOCAL floci-gcp GCP emulator (port 4588, OFF by default). Registry key `floci_gcp`; reads the ONE seam `tools/cloud/emulator_gcp.py`. **READ ONLY** — `supports_write=False` and `write()` returns status `unsupported` NAMING the absent GCP IaC executor. Eleven logical tables in two SCOPES (`table_scope()`): `emulator` (health, enabled_services) and `project` (project, buckets, topics, secrets, key_rings, service_accounts, sql_instances, datasets, gke_clusters). NO FAN-OUT, and that is a measurement rather than an omission: project-scoped lists reflect writes here, unlike the Azure sibling's subscription lane, so each table is ONE request and an empty result IS a real answer. `enabled_services` is named for what it is and returns NAMES ONLY — the always-`running` status is stripped so nobody can render a constant as a health badge. ABSENT by name: firestore/datastore (gRPC only — this connector reads HTTP, so a table would be a fabricated empty) and cloudtasks (no route found). Reachable through `broker.fetch` under the flx-gcp-01 grant (`twin_observatory_analyst`) (flx-gcp-01) | ConnectorRequest(table_name, limit) | ConnectorResponse rows + status ok \| disabled \| unsupported \| error |
| Floci-OCI Emulator Connector | tools/databridge/connectors/floci_oci_connector.py | DataBridge connector over the LOCAL floci-oci OCI emulator (port 4599, OFF by default). Registry key `floci_oci`; reads the ONE seam `tools/cloud/emulator_oci.py`. **READ ONLY** — `supports_write=False` and `write()` returns status `unsupported` naming TWO reasons: the absent OCI IaC executor AND the stubbed OCI provider layer. Thirteen logical tables in two SCOPES (`table_scope()`): `emulator` (health, enabled_services) and `compartment` (buckets, compartments, users, groups, policies, vaults, keys, queues, streams, applications, clusters). NO FAN-OUT, and that is a measurement rather than an omission: `compartmentId` is honoured here — a bogus compartment returns zero rows — unlike the Azure sibling's subscription lane, so each table is ONE request and an empty result IS a real answer. `enabled_services` returns NAMES ONLY (the always-`running` status is stripped) and carries `self_reports_disagree`, because the emulator's startup log names 7 services and its health map names 8. `clusters` rows carry `lifecycle_is_unverified`: floci-oci 0.4.0 reports OKE ACTIVE for clusters whose k3s container died on start. Row extraction goes through the seam because `queues` wraps its rows and the other ten do not. Reachable through `broker.fetch` under the flx-oci-01 grant (`twin_observatory_analyst`) (flx-oci-01) | ConnectorRequest(table_name, limit) | ConnectorResponse rows + status ok \| disabled \| unsupported \| error |
