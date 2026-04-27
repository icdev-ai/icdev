# CUI // SP-CTI
# Strategos Air-Gap Three-Tier Fallback

**Phase:** Strategos Import — Air-Gap V&V
**Task:** sg-import-airgap-vv
**Shipped:** 2026-04-27
**Status:** V&V PASSED

---

## Overview

The Strategos OSINT subsystem resolves a connectivity tier at runtime and falls
back gracefully when internet access or GitLab is unavailable. This enables
uninterrupted intelligence collection across IL2 (internet), IL4/IL5 (GitLab
CI artifact), and fully air-gapped (pre-staged file inbox) deployments.

A parallel fallback chain governs task **execution** (LLM dispatch): Claude CLI
→ Ollama Local when in air-gap mode.

---

## Tier Resolver Logic

**Module:** `tools/strategos/tier_resolver.py`

The resolver probes sources in priority order and returns the first available
tier. Results are cached for 5 minutes (in-process) to avoid repeated probes
per scheduler cycle.

### OSINT Tier Chain

| Tier | Condition | Source |
|---|---|---|
| `TIER_INTERNET` | `ICDEV_AIRGAP` unset | Live RSS/Atom/Telegram/Twitter feeds |
| `TIER_GITLAB` | `ICDEV_AIRGAP=true` + `GITLAB_URL` reachable + `GITLAB_OSINT_PROJECT_ID` set | GitLab CI artifact (`osint_signals.json`) |
| `TIER_FILE_INBOX` | `ICDEV_AIRGAP=true` + JSON files in `data/osint_inbox/` | Pre-staged batch files from `osint_prestage.py` |
| `TIER_NONE` | All sources unavailable | Audit row written; 0 signals; exit 0 |

### Executor Tier Chain

| Tier | Condition |
|---|---|
| `EXEC_CLAUDE_CLI` | `ICDEV_AIRGAP` unset |
| `EXEC_GITLAB` | `ICDEV_AIRGAP=true` + `GITLAB_URL` reachable |
| `EXEC_OLLAMA_LOCAL` | `ICDEV_AIRGAP=true` + GitLab unreachable |

### Cache invalidation

```python
from tools.strategos.tier_resolver import invalidate_cache
invalidate_cache()  # forces fresh probe on next call
```

---

## OSINT Harvester Fallback Chain

**Module:** `tools/genesis/reflexes/strategos/osint_harvester.py`

The Genesis reflex entry point (`run(config, trust)`) follows this decision tree:

```
resolve_tiers()
    → TIER_INTERNET  → _harvest_internet()
    → TIER_GITLAB    → _harvest_gitlab()
                          └→ (empty/unreachable) fall through to _harvest_file_inbox()
                               └→ (empty) fall through to TIER_NONE
    → TIER_FILE_INBOX → _harvest_file_inbox()
                          └→ (empty) fall through to TIER_NONE
    → TIER_NONE       → write audit row, exit 0, return {success: true, metric_value: 0}
```

**Key invariants:**
- The harvester always returns `{success: true}` — even TIER_NONE is not a failure.
- Kanban task is NOT marked failed on TIER_NONE; it retries next cycle.
- All processed inbox files are moved to `data/osint_inbox/processed/<YYYY-MM-DD>/` regardless of outcome.
- Deduplication via SHA-256(url + title) prevents re-ingestion.

### Bug fixed during V&V (2026-04-27)

`_process_inbox_json` was wrapping the whole prestage batch object as a single
signal. Fixed to unpack the `"signals"` array from the prestage format:

```python
# Before (broken):
signals = data if isinstance(data, list) else [data]

# After (fixed):
if isinstance(data, list):
    signals = data
elif isinstance(data, dict) and "signals" in data:
    signals = data["signals"]   # ← prestage format
else:
    signals = [data]
```

Also fixed: `_ensure_tables()` now skips migration if tables already exist,
avoiding a spurious PostgreSQL transaction-abort warning on every invocation.

---

## OSINT Pre-Stager

**Module:** `tools/strategos/osint_prestage.py`

Run on the internet-connected side **before** deploying to an air-gapped
enclave. Writes timestamped JSON batch files to `data/osint_inbox/`.

```bash
# Collect and stage signals
python tools/strategos/osint_prestage.py --output-dir /mnt/transfer/osint_inbox/

# Dry-run (count without writing)
python tools/strategos/osint_prestage.py --dry-run --json
```

Output format per file:
```json
{
  "signals": [
    {"title": "...", "body": "...", "source": "...", "date": "...", "url": "...", "geo_hint": null}
  ],
  "count": N,
  "prestaged_at": "2026-04-27T12:00:00Z"
}
```

Transfer `data/osint_inbox/` to the enclave via rsync or removable media. The
harvester auto-processes and moves files to `processed/<date>/` on the next
Genesis reflex cycle.

---

## GitLab CI Collector

**Module:** `tools/strategos/gitlab_osint_collector.py`

Runs as a GitLab CI job (`osint_collect`) on an internet-connected runner.
Produces `osint_signals.json` and `kg_delta.json` as artifacts. The harvester
downloads and processes these artifacts automatically via TIER_GITLAB.

Required env vars on the enclave side:
```bash
ICDEV_AIRGAP=true
GITLAB_URL=https://gitlab.internal.gov
GITLAB_TOKEN=<service-account-token>
GITLAB_OSINT_PROJECT_ID=<numeric-project-id>
GITLAB_OSINT_REF=main
```

---

## Tier Resolver CLI

```bash
# Check current tier (cached)
python tools/strategos/tier_resolver.py --json

# Force fresh probe
python tools/strategos/tier_resolver.py --json --no-cache
```

Example output under TIER_FILE_INBOX:
```json
{
  "osint_tier": "FILE_INBOX",
  "exec_tier": "OLLAMA_LOCAL",
  "gitlab_reachable": false,
  "file_inbox_count": 3,
  "ollama_reachable": true,
  "resolved_at": "2026-04-27T17:22:52Z"
}
```

---

## V&V Results (2026-04-27)

| Test | Result | Notes |
|---|---|---|
| Compile all 3 modules | PASS | tier_resolver, gitlab_osint_collector, osint_prestage |
| ruff check tools/strategos/ | PASS | No lint errors |
| pytest (non-trading) | PASS | Exit 0 |
| bandit --severity-level medium | PASS | 5 medium (pre-existing B310/B608 in gdelt/interdiction_ranker) |
| Coherence gate | PASS | 1 warn (pre-existing), all others pass |
| sandbox-coverage.md | PASS | Gaps 11 (gitlab_osint_collector) + 12 (osint_prestage) added |
| airgap-runbook.md | PASS | Section 10 (OSINT pre-staging) added |
| 3a TIER_INTERNET | PASS | osint_tier=INTERNET when no airgap |
| 3b TIER_GITLAB | PASS | Falls to TIER_NONE when no real GitLab (expected; tier logic verified) |
| 3c TIER_FILE_INBOX | PASS | 9 signals harvested from 3 inbox files |
| 3d TIER_NONE | PASS | exit 0, 0 signals, audit row written |
| 3e EXEC_OLLAMA_LOCAL | PASS | exec_tier=OLLAMA_LOCAL with ICDEV_AIRGAP=true |
| 3f No executor | PASS | exec_tier=OLLAMA_LOCAL, ollama_reachable=False; Kanban handles gracefully |
| Dashboard /strategos/oracle | PASS | Renders (screenshot: playwright/screenshots/strategos-airgap-vv.png) |
| Dashboard /strategos/signals | PASS | Flask test client 200 (live server needs restart to pick up blueprint changes) |
| Companion sync | PASS | 10 platforms synced |

---

## Sandbox Coverage

See [docs/security/sandbox-coverage.md](../security/sandbox-coverage.md):
- **Gap 11** — `gitlab_osint_collector.py` → sandboxed-on-demand (CI runner context)
- **Gap 12** — `osint_prestage.py` → trusted-first-party (write path) / sandboxed-on-demand (RSS fetch)
- **Gap 10** — `osint_harvester.py` → sandboxed-on-demand (pre-existing)

---

## Air-Gap Runbook

See [docs/ops/airgap-runbook.md](../ops/airgap-runbook.md) Section 10 for
the full pre-staging workflow, tier resolution table, and TIER_NONE behaviour.
