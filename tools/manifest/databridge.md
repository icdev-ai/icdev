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


## DataBridge (Additional)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Alpaca Connector | tools/databridge/connectors/alpaca_connector.py | Alpaca Markets trading API connector (equities + crypto) | --json | Connection status |
| ACLED Connector | tools/databridge/connectors/acled_connector.py | Armed Conflict Location & Event Data API connector; incremental by event_date; normalizes headline/geo_hint/signal_date | --table events --since DATE --json | Conflict event records |
| GDELT Connector | tools/databridge/connectors/gdelt_connector.py | GDELT Project API connector (events + GKG); no auth; incremental by SQLDATE; normalizes headline/geo_hint/signal_date | --table events|gkg --query TEXT --since SQLDATE --json | Event/GKG records |
| SaaS Base Connector | tools/databridge/connectors/saas_base.py | REST/SaaS API base connector class for Connector Forge | (library) | SaaSBaseConnector class |
| Sandbox Adapter | tools/databridge/forge/sandbox_adapter.py | Sandbox environment adapter for connector testing | --json | Adapter status |
| Sandbox Manager | tools/databridge/forge/sandbox_manager.py | Sandbox lifecycle manager for generated connectors | --json | Sandbox status |

