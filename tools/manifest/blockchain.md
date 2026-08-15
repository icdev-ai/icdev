# Blockchain (GovChain — D-GC-1 through D-GC-11)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

Hyperledger Fabric integration for tamper-evident audit and provenance anchoring.
Hybrid architecture: SQLite remains the local source of truth; Fabric provides
cross-organizational attestation. Degrades gracefully to air-gap queue when Fabric
is unreachable.

## Core Python Modules

| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Blockchain Config | `tools/blockchain/blockchain_config.py` | Loads `args/blockchain_config.yaml`; provides `BlockchainConfig` and `NoOpFabricClient` (air-gap fallback). Environment override: `ICDEV_BLOCKCHAIN_ENABLED`. | `--test`, `--json` | JSON |
| Chain Anchor | `tools/blockchain/chain_anchor.py` | Batches unanchored audit entries and provenance hashes into Merkle trees; submits root to GovChain channel. Air-gap mode queues to `govchain_pending_operations`. | `--anchor-audit IDS`, `--anchor-provenance IDS`, `--periodic`, `--json` | JSON |
| Provenance Verifier | `tools/blockchain/provenance_verifier.py` | Verifies audit hash chains, Merkle inclusion, and blockchain TX anchoring. Generates comprehensive verification reports. | `--verify-audit ID`, `--verify-citation ID`, `--verify-project ID`, `--verify-slsa ID`, `--json` | JSON |
| Channel Manager | `tools/blockchain/channel_manager.py` | Cross-organization provenance channel management for federated Fabric deployments. Creates and joins channels across GovOrg1/GovOrg2. | `--create-channel`, `--join-channel`, `--list-channels`, `--json` | JSON |
| ZK Prover | `tools/blockchain/zk_prover.py` | Zero-Knowledge prover for CUI/SECRET provenance disclosure (research module). Pedersen commitments + Merkle membership proofs. **Not production-ready — pedagogical implementation only.** | `--prove`, `--verify`, `--json` | JSON |
| Chaincode Linter | `tools/blockchain/chaincode_linter.py` | Scans Go chaincode in `tools/blockchain/chaincode/` against vulnerability patterns in `args/chaincode_security_config.yaml`. Reports by severity with NIST 800-53 mappings. | `--scan`, `--gate`, `--json` | JSON |
| Asset Ledger | `tools/blockchain/asset_ledger.py` | Government asset tokenization state machine (`procured→received→operational→maintenance→surplus→disposed`). Stores assets in SQLite; anchors state transitions via ChainAnchor. | `--register`, `--transition`, `--get`, `--list`, `--json` | JSON |
| Transport Registry | `tools/blockchain/transport_registry.py` | Picks the best HEALTHY anchor transport by `priority` (D-GC-1), modelled on `platform_connectors/connector_registry.py`. Replaces the `HAS_FABRIC` hard branch that routed every anchor to a no-op. Registering one `PeerCliTransport` per endpoint IS peer failover. Health cached `transport_health_ttl_seconds` (60s). | `--doctor`, `--json` | JSON |
| Anchor Transport ABC | `tools/blockchain/transports/base.py` | `AnchorTransport` ABC (`chaincode_invoke` / `chaincode_query` / `health`) + `TransportHealth`. Result vocabulary: `anchored` \| `queued` \| `failed` — a returned dict is NOT success, callers must read `status`. | (library) | `from icdev.tools.blockchain.transports.base import AnchorTransport, TransportHealth` |
| Peer CLI Transport | `tools/blockchain/transports/peer_cli.py` | **The D-GC-1 transport that was declared in `args/blockchain_config.yaml` and never built.** Wraps the Fabric `peer` binary via `subprocess` (argv form, `shell=False`, bounded timeout). Needs no Python SDK. `health()` proves the binary answers `peer version`, not that an orderer will accept a tx. | (library) | `PeerCliTransport(cli_path, peer_address, orderer, tls, priority)` |
| Fabric SDK Transport | `tools/blockchain/transports/fabric_sdk.py` | Optional `hfc`/fabric-sdk-py transport. `hfc` is in NEITHER `requirements.txt` NOR `pyproject.toml`; the import is **inside** `health()`/`_client()` so this module never requires it. Absent SDK ⇒ `unavailable`, registry skips it. | (library) | `FabricSdkTransport(net_profile, org_name, user_name, peers)` |
| No-Op Transport | `tools/blockchain/transports/noop.py` | Queue-only last resort, **unhealthy by default** — a no-op is the absence of a backend, and saying so is what makes the `govchain_pending_operations` fall-through fire. Flip with `ICDEV_BLOCKCHAIN_NOOP_HEALTHY=1` for a simulation sink with synthetic `noop-` tx ids (never chain commitments). | (library) | `NoOpTransport(queue_to_db, healthy, priority)` |

## Chaincode (Go — Hyperledger Fabric)

| Contract | File | Functions |
|----------|------|-----------|
| ProvenanceContract | `tools/blockchain/chaincode/provenance/provenance.go` | `StoreHash`, `VerifyHash`, `GetHistory` |
| AuditContract | `tools/blockchain/chaincode/audit/audit.go` | `StoreMerkleRoot`, `VerifyMerkleRoot`, `GetAuditBatch` |
| EvidenceContract | `tools/blockchain/chaincode/evidence/evidence.go` | `StoreEvidence`, `VerifyEvidence`, `GetEvidenceHistory` |
| AccessContract | `tools/blockchain/chaincode/access/access.go` | `RecordAccess`, `GetAccessLog`, `RevokeAccess` |
| ComplianceGateContract | `tools/blockchain/chaincode/compliance_gate/compliance_gate.go` | `StoreComplianceResult`, `GetComplianceHistory`, `QueryByFramework` |

Package chaincode with: `cd tools/blockchain/chaincode && make package`

## Database Tables

| Table | Purpose |
|-------|---------|
| `source_citation_registry` | Unified provenance index — all subsystem citations with `source_hash`, `merkle_root`, `blockchain_tx_id`, `trust_score` |
| `govchain_pending_operations` | Air-gap queue for unsubmitted Fabric operations — flushed when peer becomes reachable |

## Configuration

| File | Purpose |
|------|---------|
| `args/blockchain_config.yaml` | Fabric topology, TLS, crypto (FIPS advisory), provenance intervals, asset tokenization, air-gap mode |
| `args/chaincode_security_config.yaml` | Vulnerability regex patterns (critical/high/medium/low) with NIST 800-53 mappings for Go/Java/Node chaincode audit |
| `args/asset_tokenization_config.yaml` | Asset types, NSN requirements, lifecycle state machine definitions |

## Key Design Decisions

- **D-GC-1:** Fabric CLI via subprocess (same pattern as bandit/SAST wrappers)
- **D-GC-3:** FIPS validator is advisory-only (not enforcing) — HSM required for IL5+
- **D-GC-5:** W3C PROV-AGENT extended with `blockchain_anchor` entity type
- **D-GC-6:** Assets in SQLite; blockchain holds integrity proofs only (hybrid)
- **D-GC-8:** TLS mutual_auth enabled, certs at `/etc/hyperledger/fabric/tls`
- **D-GC-10:** REST bridges only (air-gap compatible), not direct Fabric SDKs
- **D-GC-11:** HSM modeled as external reference; pluggable via PKCS#11
