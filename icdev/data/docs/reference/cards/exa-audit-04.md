# Audit hash-chain integrity — is the audit_trail chain actually intact? (exa-audit-04)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

```bash
python tools/audit/chain_sweep.py --json           # whole-table sweep, four buckets
python tools/audit/chain_sweep.py --gate           # exit 1 if any chained link is broken
python tools/audit/chain_sweep.py --verify-signatures
python tools/audit/chain_sweep.py --db-path <db>   # sweep an evidence copy / tenant db
```

verified | pre_cutover (unverifiable, NOT tampered) | unchained (writer bypassed) | BROKEN
BROKEN is the only tamper signal. Signatures are reported separately and never counted
broken — an unsigned deployment must not read as 100% tampered.
UI: /provenance -> "Audit Chain Integrity"   API: /api/govchain-provenance/chain-health
Cadence: the genesis `audit` reflex (args/genesis_config.yaml -> reflexes.audit.checks)
