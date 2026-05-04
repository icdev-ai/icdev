# FathomDesk Auto-Trading Runbook

Last updated: 2026-04-12

## Phase Map

| Phase | Tier | Capital | Per-pos | Halt | SHORT | Promotion gate |
|---|---|---|---|---|---|---|
| 0 | paper | sim only | n/a | n/a | n/a | 30 days clean signal flow on Alpaca paper |
| 1 | paper | sim only | n/a | n/a | n/a | 100 paper orders, <2% rejection |
| 2 | risk_hardening | (still paper) | n/a | n/a | n/a | PDT + kill-switch + drawdown + audit + approval verified |
| 3 | micro_live | $5,000 | 0.5% | -1.0% | OFF | 1 week clean live ops |
| 4a | scale_10k | $10,000 | 1.0% | -1.5% | OFF | ≥2 weeks scale_10k clean |
| 4b | scale_25k | $25,000 | 2.0% | -2.0% | OFF | ≥1 month scale_25k clean + Sharpe > 1.0 |
| 4c | scale_100k | $100,000 | 3.0% | -2.0% | ON (locate-gated) | board approval |

## Operator pre-flight (before each session)

```bash
# 1. Verify creds + endpoint
python tools/trading/brokers/alpaca_adapter.py --account --json

# 2. Check kill-switch is clear
python tools/trading/risk/kill_switch.py --status --json

# 3. Verify PDT eligibility
python tools/trading/risk/pdt_tracker.py --json

# 4. Snapshot drawdown baseline
python tools/trading/risk/drawdown_monitor.py --json

# 5. Confirm tier preset matches operator intent
python tools/trading/rollout/preset_loader.py --tier $ICDEV_TRADING_TIER --json
```

## Promoting between tiers

Tier promotion requires changing `ICDEV_TRADING_TIER` in env AND the loader will refuse mismatches. There is no auto-promotion.

```bash
# Promote from micro_live to scale_10k
export ICDEV_TRADING_TIER=scale_10k
python tools/trading/rollout/preset_loader.py --tier scale_10k --json
# verify preset reflects new caps before enabling daemon
```

## Emergency halt

Three independent halt sources — ANY one stops auto-trading immediately:

```bash
# Source 1 — env (current shell only)
export ICDEV_TRADING_KILLED=1

# Source 2 — file flag (persists across processes)
echo "manual halt" > data/.kill_trading

# Source 3 — DB (toggleable from dashboard /orders page or CLI)
python tools/trading/risk/kill_switch.py --trip "investigating PnL anomaly" --by oncall --json
```

Recovery:
```bash
unset ICDEV_TRADING_KILLED
rm -f data/.kill_trading
python tools/trading/risk/kill_switch.py --clear --by oncall --json
```

## Audit query

```bash
# Recent events for a ticker
python tools/trading/audit/trade_audit.py --query --ticker AAPL --limit 50 --json

# All recent kill-switch trips
python tools/trading/audit/trade_audit.py --query --event killswitch_tripped --limit 20 --json

# All drawdown halts in audit
python tools/trading/audit/trade_audit.py --query --event drawdown_halt --limit 20 --json
```

## SHORT enable (tier 3 only)

Even at scale_100k, SHORT requires:
1. Preset has `shorting.enabled: true` (scale_100k only)
2. Broker confirms borrow `locate` for the symbol
3. Kill-switch clear

```bash
python tools/trading/rollout/preset_loader.py --tier scale_100k --check-short --locate-ok --allow-unsafe --json
```

## Daily close-out

Daemon will not auto-close positions. Operator runs the EOD checklist:
1. Snapshot positions: `python tools/trading/brokers/alpaca_adapter.py --account --json`
2. Save audit dump: `python tools/trading/audit/trade_audit.py --query --limit 1000 --json > data/audit-$(date +%F).json`
3. Verify drawdown ended within tolerance
4. If anomaly: trip kill-switch, investigate before next session

## Out of scope (NOT implemented)

- Tax-lot tracking
- Wash-sale detection
- Options / futures
- Multi-broker execution (Alpaca only for now)
- Backtester (Phase 4+ enhancement)
