# Goal: GovChain Blockchain Anchor Lifecycle

## Description

Anchors ICDEV™ audit trail entries and provenance citations to Hyperledger Fabric
(GovChain) for tamper-evident, cross-organizational attestation. Operates in two
modes: **connected** (submits Merkle roots to a live Fabric peer) and **air-gap**
(queues operations to `govchain_pending_operations` for deferred submission).

**Why this matters:** Federal audit trails must satisfy NIST AU-9 (protection of
audit information) and AU-10 (non-repudiation). A cryptographic Merkle chain in
SQLite provides integrity within a system boundary; blockchain anchoring extends
that guarantee across organizational boundaries and provides tamper-evidence that
survives DB compromise.

---

## Prerequisites

- [ ] Migration 149 applied (`audit_trail` has `hash`, `previous_hash`, `signature` columns)
- [ ] Migration 151 applied (`canvas_ai_decisions` has `decision_hash`, `previous_decision_hash`)
- [ ] `source_citation_registry` and `govchain_pending_operations` tables exist
- [ ] `args/blockchain_config.yaml` configured (or `ICDEV_BLOCKCHAIN_ENABLED=false` for air-gap)
- [ ] Chaincode packaged: `cd tools/blockchain/chaincode && make package` (connected mode only)
- [ ] `memory/MEMORY.md` loaded (session context)

---

## Quality Gates

| Gate | Threshold | Blocks Anchor? |
|------|-----------|---------------|
| Unanchored audit entries older than `max_anchor_age_hours` | 0 | YES |
| Pending ops in `govchain_pending_operations` older than 24h | 0 | WARNING |
| Chaincode security linter critical findings | 0 | YES (connected mode) |
| FIPS-compliant algorithm in use | AES-256-GCM, SHA-256+ | YES for IL5+ |

---

## Workflow

### Phase 1 — Configuration

```bash
# Verify blockchain config
python tools/blockchain/blockchain_config.py --test --json

# Check air-gap status
python -c "from tools.blockchain.blockchain_config import get_config; c = get_config(); print(c.is_enabled(), c.is_air_gapped())"

# Run chaincode security lint (connected mode only)
python tools/blockchain/chaincode_linter.py --scan --json
```

### Phase 2 — Batch Collection

Collect unanchored records from both audit trail and provenance registry.

```bash
# Check how many audit entries need anchoring
python -c "
from tools.db.storage import get_connection
conn = get_connection()
n = conn.execute('SELECT COUNT(*) FROM audit_trail WHERE hash IS NULL OR signature IS NULL').fetchone()[0]
print(f'{n} unanchored audit entries')
conn.close()
"

# Check unanchored registry entries
python -c "
from tools.db.storage import get_connection
conn = get_connection()
n = conn.execute('SELECT COUNT(*) FROM source_citation_registry WHERE merkle_root IS NULL').fetchone()[0]
print(f'{n} unanchored registry entries')
conn.close()
"
```

### Phase 3 — Merkle Tree Construction + Fabric Submission

```bash
# Anchor a specific batch of audit IDs
python tools/blockchain/chain_anchor.py --anchor-audit 1 2 3 4 5 --json

# Anchor provenance registry entries
python tools/blockchain/chain_anchor.py --anchor-provenance scr-abc123 scr-def456 --json

# Run full periodic sweep (scans for unanchored entries automatically)
python tools/blockchain/chain_anchor.py --periodic --json
```

In **connected mode**, this submits the Merkle root to `AuditContract.StoreMerkleRoot`
on the `govchain-channel` and records the TX ID in `source_citation_registry`.

In **air-gap mode**, operations are written to `govchain_pending_operations` for
deferred submission when the peer becomes reachable.

### Phase 4 — Registry Update

After anchoring, `source_citation_registry` is updated with `merkle_root` and
`blockchain_tx_id`. Trust scores increase by +0.3 for confirmed TX IDs.

```bash
# Re-score trust after anchoring
python tools/provenance/trust_scorer.py --rescore-all --json
```

### Phase 5 — Verification

```bash
# Verify a single audit entry's hash chain + blockchain anchor
python tools/blockchain/provenance_verifier.py --verify-audit 42 --json

# Verify a registry citation
python tools/blockchain/provenance_verifier.py --verify-citation scr-abc123 --json

# Generate full project verification report
python tools/blockchain/provenance_verifier.py --verify-project proj-abc123 --json

# Verify SLSA attestation for a project
python tools/blockchain/provenance_verifier.py --verify-slsa proj-abc123 --json
```

### Phase 6 — Pending Ops Flush (Air-Gap Recovery)

When reconnecting after an air-gap period:

```bash
# Flush queued operations to Fabric
python tools/blockchain/chain_anchor.py --flush-pending --json
```

This reads all rows in `govchain_pending_operations WHERE status='pending'`,
re-submits each to Fabric, and marks them `flushed` or `failed`.

### Phase 7 — Asset Tokenization (Optional)

For government property / IT asset lifecycle management:

```bash
# Register an asset on the ledger
python tools/blockchain/asset_ledger.py --register --asset-type it_equipment --nsn "7010-00-000-0000" --json

# Transition asset state
python tools/blockchain/asset_ledger.py --transition <asset-id> received --json

# List all assets
python tools/blockchain/asset_ledger.py --list --json
```

---

## Automated Scheduling

`chain_anchor.py --periodic` should run on a cadence (every 30 min) via the
Genesis daemon reflex or an OS cron job. See `args/awareness_config.yaml` for
the `govchain_periodic_anchor` reflex configuration.

---

## Key Tools

| Tool | File | Purpose |
|------|------|---------|
| Blockchain Config | `tools/blockchain/blockchain_config.py` | Config loader, NoOp client fallback |
| Chain Anchor | `tools/blockchain/chain_anchor.py` | Merkle batch + Fabric submission |
| Provenance Verifier | `tools/blockchain/provenance_verifier.py` | Integrity verification + reports |
| Channel Manager | `tools/blockchain/channel_manager.py` | Multi-org Fabric channel ops |
| Chaincode Linter | `tools/blockchain/chaincode_linter.py` | Security scan chaincode |
| Asset Ledger | `tools/blockchain/asset_ledger.py` | Asset state machine |
| Provenance Registry | `tools/provenance/registry.py` | `update_blockchain_anchor()` |
| Trust Scorer | `tools/provenance/trust_scorer.py` | `+0.3` for confirmed TX |

---

## Continuous Improvement

Every failed anchor → check `govchain_pending_operations` → investigate Fabric
connectivity → fix → re-run `--periodic`. Record Fabric downtime patterns in
`args/blockchain_config.yaml` `air_gapped.offline_queue_ttl_hours` to tune queue retention.
