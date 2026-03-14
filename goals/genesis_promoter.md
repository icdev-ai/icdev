# CUI // SP-CTI
# Goal: Genesis Knowledge Bridge (Promoter)

**Version:** 1.0
**Classification:** CUI // SP-CTI
**Decisions:** D-GEN-3, D-GEN-4, D-GEN-7

---

## Purpose

The Promoter is the **only authorized gateway** from v2.0 Genesis to v1.x
production.  It validates, deduplicates, and imports Genesis Knowledge Packets
(GKPs) into the appropriate v1.x tables and configs.

---

## GKP Format (Genesis Knowledge Packet)

```json
{
  "gkp_version": "1.0",
  "id": "gkp-a1b2c3d4e5",
  "artifact_type": "research_signal|compliance_knowledge|quality_baseline|proven_pattern|capability_update|code_patch|training_pair",
  "genesis_reflex": "research|scout|audit|comply|ingest|market|publish|test|learn|heal|evolve|report",
  "confidence": 0.85,
  "evidence": {},
  "payload": {},
  "sha256": "...",
  "promotion_status": "pending_review",
  "created_at": "2026-03-13T02:00:00Z"
}
```

---

## 7 Artifact Types → Import Destinations

| Artifact Type | v1.x Destination | Auto-Promote? | Gate |
|--------------|-----------------|---------------|------|
| `research_signal` | `innovation_signals` table | Yes (GREEN) | None |
| `compliance_knowledge` | `dh_enrichment_cache` table | Yes (GREEN) | None |
| `quality_baseline` | `code_quality_metrics` table | Yes (GREEN) | None |
| `proven_pattern` (>= 0.85) | `knowledge_patterns` table | Yes (YELLOW) | Confidence >= 0.85 |
| `proven_pattern` (0.7-0.85) | `knowledge_patterns` table | No | Human review |
| `capability_update` | `context/capabilities/*.yaml` | No | Human review |
| `code_patch` | Cherry-pick to `main` | No | Human review (D-GEN-7) |
| `training_pair` | `ft_training_pairs` (unapproved) | No | Human review |

---

## Workflow

### Export (v2.0 → GKP file)
1. Reflex generates result
2. Reflex calls `promoter.export_gkp()` with payload
3. Promoter checks dedup (SHA-256 hash match)
4. If unique: stores in `genesis_gkp` table + writes `data/genesis/exports/{id}.gkp.json`
5. Logs `genesis.promoter.exported` to audit

### Auto-Promote
1. `promoter.auto_promote_eligible()` scans all `pending_review` GKPs
2. Matches against auto-promote rules in `args/genesis_config.yaml`
3. For each match: calls `_import_to_v1x()` → writes to destination table
4. Updates GKP status to `auto_promoted`
5. Logs `genesis.promoter.auto_promoted` to audit

### Human Review
1. Human runs `python tools/genesis/promoter.py --list --json`
2. Reviews pending GKPs
3. Promotes: `python tools/genesis/promoter.py --promote <gkp_id>`
4. Rejects: `python tools/genesis/promoter.py --reject <gkp_id> --reason "..."`

---

## Operations

```bash
# List pending GKPs
python tools/genesis/promoter.py --list --status-filter pending_review --json

# Auto-promote all eligible
python tools/genesis/promoter.py --auto-promote --json

# Manually promote
python tools/genesis/promoter.py --promote gkp-a1b2c3d4e5 --json

# Reject with reason
python tools/genesis/promoter.py --reject gkp-a1b2c3d4e5 --reason "Not relevant" --json

# Statistics
python tools/genesis/promoter.py --stats --json
```

---

## Guardrails

- Promoter is the ONLY gateway (D-GEN-4) — no direct table writes from Genesis
- Deduplication via SHA-256 hash prevents duplicate knowledge
- Code patches NEVER auto-promote — always human cherry-pick (D-GEN-7)
- Training pairs stored as unapproved — human must approve before training
- All promotion decisions logged to append-only `genesis_audit` (NIST AU)
- Capability YAML updates staged for review — never auto-applied
