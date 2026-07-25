# Insider-Risk UBA (lite)

**Card:** `crx-sec-01` · **Status:** config-gated, **default OFF** · **Module:** `tools/security/insider_risk.py`

A lightweight User-Behavior-Analytics (UBA) baseline built over telemetry ICDEV
**already** collects. It surfaces potential insider-risk signals using
**deterministic rules only** — no machine learning in phase 1.

## What it does

For each platform user (actor), over a configurable lookback window, it:

1. Derives a per-user **baseline** — typical active hours, endpoint/event mix,
   export/download volume, first/last seen.
2. Applies three **deterministic anomaly rules** and produces a 0..1 risk score:

   | Rule | Fires when | Weight |
   |------|-----------|--------|
   | `off_hours_bulk_export` | ≥ N export/download actions during the off-hours window | 0.40 |
   | `privilege_change_burst` | ≥ N privilege/config-change events within a sliding window | 0.35 |
   | `dormant_account_activity` | account reactivates after ≥ N days of dormancy | 0.25 |

   Score → band: `high ≥ 0.6`, `elevated ≥ 0.3`, else `normal`.

## Data sources (READ-ONLY)

The engine reads, and **never writes to**, existing telemetry:

- `audit_trail` — append-only audit records (Phase 30). Actor = platform user;
  `event_type` classifies export vs. privilege-change vs. plain activity.
- `hook_events` — tool/session activity feed (recency signal).
- `usage_events` — route/feature usage; export/download routes count as exports.

> The audit trail is immutable (NIST AU-9). This feature only SELECTs from it —
> it performs no UPDATE/DELETE on any audit table.

## Where results live

Derived data (recomputable — **not** audit records, intentionally not
append-only), each row carrying `tenant_id` + `classification` for RLS:

- `insider_risk_baselines` — one row per account.
- `insider_risk_scores` — one row per scan per account with a fired rule.

Migration `282_insider_risk_uba.sql` provisions both; the engine also
self-creates them at runtime (`CREATE TABLE IF NOT EXISTS`).

## Surfacing

Scores appear on the Security Design Canvas dashboard (`/security/`) in the
**Insider-Risk (UBA)** panel, backed by `GET /security/api/insider-risk` and
`POST /security/api/insider-risk/scan`. When the feature is disabled the panel
renders an informational "disabled" state.

## Privacy & governance

**This feature monitors platform users, so it ships DEFAULT OFF.** Enabling it:

1. Set `enabled: true` in `args/insider_risk_config.yaml`, or export
   `ICDEV_INSIDER_RISK_ENABLED=1`.
2. Before enabling in any environment with real users, complete a privacy
   review and ensure users are notified per your jurisdiction's requirements.
   Monitoring employees/users without notice is unlawful in some regions.

Only deterministic, explainable rules are used — every finding lists exactly
which rules fired and why. No behavioral profiling model or opaque scoring is
involved in phase 1. (ML scoring is explicitly out of scope.)

NIST 800-53 alignment: AU-6, AU-6(1), AU-6(3), AU-7, SI-4, AC-2(12).

## CLI

```bash
python tools/security/insider_risk.py --scan --json       # run a scan (respects enabled flag)
python tools/security/insider_risk.py --scan --force --json  # run even if disabled
python tools/security/insider_risk.py --summary --json    # latest findings
```

## Follow-ups

- `crx-not-01` — real alert dispatch. The engine has a clean soft-coupled hook
  (`alert.enabled`) that no-ops until that notification surface exists.
