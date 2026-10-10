# This repo is PUBLIC: nothing from the trading domain comes back (xit-leak-01)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

```bash
python tools/ci/domain_leak_gate.py --check                   # CI security job; exit 1 on a violation
python tools/ci/domain_leak_gate.py --staged --check          # what .githooks/pre-commit runs
python tools/ci/domain_leak_gate.py --json
```

Refuses a BROKER CREDENTIAL (Alpaca key ids / APCA headers, Kraken private
keys, Tradier / Tastytrade / Schwab / IBKR tokens, Coinbase CDP key names,
exchange secrets -- patterns in tools/security/secret_detector.py, category
broker_credential, SURVEYED at 0 hits before arming), an ad_* table DUMP in a
data file, and -- once `paths.mode` flips to enforce in the removal PR -- any
file under a removed trading path. Allow entries in args/domain_leak_gate.yaml
need a written reason. ICDEV_DOMAIN_LEAK_GUARD=0 stands it down; never `|| true`.
