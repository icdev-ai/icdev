# CONTEXT.md — tools/genesis/

Consumed by the `icdev-improve` skill and `/ask-icdev` RAG index.
NIST controls: SA-15 (development process), CM-7 (least functionality).

---

## 1. Reflex YELLOW / GREEN tiers

**Locations:** `tools/daemon/base.py:63` (constants), `args/genesis_config.yaml` (config)

Every reflex declares a `risk_tier`. The tier controls sandbox, approval, and
allowed actions. Tiers are defined in `args/genesis_config.yaml` under
`trust_kernel.risk_tiers`:

| Tier | Description | Approval | Sandbox | Rollback window |
|------|-------------|----------|---------|-----------------|
| `green` | Non-destructive, read-only operations | auto | no | — |
| `yellow` | Reversible writes (worktree sandbox) | auto | yes | 72 h |
| `orange` | Code mutation — worktree + test gate | **human** | yes | — |

Constants in `tools/daemon/base.py`:

```python
RISK_GREEN  = "green"
RISK_YELLOW = "yellow"
RISK_ORANGE = "orange"
```

**Default:** `RISK_GREEN`. Any reflex that does not set `risk_tier` in
`genesis_config.yaml` runs as GREEN.

**Allowed actions per tier** (from `genesis_config.yaml`):

- GREEN: `read_file`, `query_database`, `fetch_rss`, `fetch_url`, `write_data_genesis`
- YELLOW: same as GREEN plus writable-data operations in a worktree sandbox
- ORANGE: adds `create_worktree`, `edit_source_file`; requires passing tests

**Trust Kernel enforcement** (`tools/daemon/base.py:TrustKernelBase`):

```python
def requires_human_approval(self, risk_tier: str) -> bool:
    tier_config = self.risk_tiers.get(risk_tier, {})
    return tier_config.get("approval") == "human"

def requires_sandbox(self, risk_tier: str) -> bool:
    tier_config = self.risk_tiers.get(risk_tier, {})
    return tier_config.get("sandbox", False)
```

ORANGE reflexes **run in proposal mode** (hgx-obs-02). `_run_reflex_impl_inner`
delegates to `GenesisDaemon._run_orange_proposal`, which imports the module,
executes it under a config overlay (`proposal_only`, `require_human_merge`,
`auto_apply: false`, `dry_run` defaulted on) and stages the outcome as an
`orange_proposal` GKP at `pending_review` — reviewable at `/genesis` through the
existing `genesis_gkp` surface.

Until hgx-obs-02 this method returned `{"status": "awaiting_human_approval"}`
**before importlib**, so `evolve` and `experiment` never executed a line of
their mutation code. They produced nothing, which on the dashboard reads
identically to "ran and found nothing", and the operator was told to "approve
and re-trigger" with no artifact naming what they were approving. Both reflexes
already end by exporting a GKP and never merge on their own, so the early return
was suppressing exactly the artifact the ORANGE tier exists to produce.

`orange_proposal` is listed under `promoter.human_approve` and deliberately NOT
under `promoter.auto_promote`, so `auto_promote_eligible()` can never match it.
Set `ICDEV_GENESIS_ORANGE_PROPOSALS=0` to restore the old early return.

---

## 2. REFLEX_NAMES — daemon.py registration

**Location:** `tools/genesis/daemon.py:64`

The `REFLEX_NAMES` list is the **single source of truth** for which reflexes
the Genesis daemon dispatches. A reflex module that exists under
`tools/genesis/reflexes/` but is NOT in `REFLEX_NAMES` will never be called.

```python
REFLEX_NAMES = [
    "research", "scout", "audit", "comply", "ingest", "market",
    "report", "publish", "test", "learn", "heal", "evolve",
    "docs", "experiment", "synthesize", "kanban", "oracle",
    "goal_learner", "remediation_lens", "awareness", "canvas_indexer",
    "self_monitor", "fathomdesk_trap_scenarios", "migration_canvas",
    "academy_reflex", "e2e_runner", "log_triage", "inspect_adapt",
]
```

`GenesisDaemon` inherits from `DaemonBase` and sets:

```python
class GenesisDaemon(DaemonBase):
    reflex_names = REFLEX_NAMES   # line 136
```

**Dispatch path** (`daemon.py:368–388`):

```python
module = importlib.import_module(f"tools.genesis.reflexes.{name}")
if hasattr(module, "run"):
    result = self._observe(name, module.run, config, trust)
```

**Checklist for adding a new reflex:**

1. Add the name string to `REFLEX_NAMES` in `tools/genesis/daemon.py`.
2. Create `tools/genesis/reflexes/<name>.py` with a top-level `run(config, trust)` function.
3. Add `<name>:` entry under `reflexes:` in `args/genesis_config.yaml` with at
   minimum `enabled: true` and `risk_tier: green`.
4. If the reflex should only run on a schedule, add `schedule: "every Xh"` in
   the config entry.

Omitting step 1 means the daemon silently never calls the module. Omitting step
3 means the reflex is disabled by default and the daemon skips it.

---

## 3. Per-reflex watchdog timeout

**Location:** `tools/genesis/daemon.py:59, 316–357`

```python
DEFAULT_REFLEX_TIMEOUT_SECONDS = 300
```

The daemon runs each reflex in a **daemon thread** and calls
`thread.join(timeout)`. If the thread is still alive after the timeout, the
daemon logs an error and returns a failure tuple — it does **not** kill the
thread (Python cannot forcibly terminate threads). The thread is leaked.

```python
worker = threading.Thread(target=_target, name=f"reflex-{name}", daemon=True)
worker.start()
worker.join(timeout)

if worker.is_alive():
    logger.error(
        "Reflex '%s' exceeded %.0fs watchdog timeout — abandoning (thread leaked) "
        "so the daemon loop can continue. Repeated timeouts will trip its circuit breaker.",
        name, timeout,
    )
    return False, 0.0, {"error": f"watchdog_timeout_{int(timeout)}s", "timeout": True}
```

**Why this matters:** A reflex that blocks indefinitely (e.g. an HTTPS fetch
with no socket timeout) would freeze the entire sequential reflex loop without
this mechanism. Historically this caused multi-day stalls.

**Consequences of a timeout:**
- The failure is recorded on the reflex state; after `max_consecutive_failures`
  (default 3), the circuit breaker trips and the reflex is skipped until reset.
- The leaked thread keeps running until the process exits or the operation
  finally unblocks.

**Override per reflex** in `args/genesis_config.yaml`:

```yaml
reflexes:
  research:
    enabled: true
    risk_tier: green
    timeout_seconds: 120   # override the 300s default
```

**Best practice:** any reflex that makes network calls MUST set an explicit
socket/request timeout (e.g. `requests.get(url, timeout=30)`) so the watchdog
is a last-resort backstop, not the primary mechanism.

---

## 4. IPv6 penalty — always use `127.0.0.1`, not `localhost`

**Location:** `tools/awareness/health_prober.py:69`

```python
# Use the IPv4 loopback explicitly. On Windows, "localhost" resolves to ::1
# (IPv6) first, but the dashboard listens on IPv4 only — so every probe pays a
# ~2s failed-IPv6-connect penalty before falling back. Across dozens of
# http_head probes per cycle that added minutes, pushing the awareness cycle
# past its watchdog and stalling self_monitor's probe refresh. 127.0.0.1 is ~30x
# faster (0.07s vs 2.17s measured).
DASHBOARD_BASE = "http://127.0.0.1:5050"
```

**Root cause:** On Windows, the DNS resolver tries IPv6 (`::1`) first for
`localhost`. Flask's development server binds to `0.0.0.0` (IPv4) only by
default. The IPv6 connection attempt fails after ~2 s, then falls back to IPv4.
With dozens of `http_head` probes per awareness cycle, this accumulated to
several minutes of added latency — enough to exceed the awareness watchdog and
prevent `self_monitor` from refreshing its probe data.

**Rule:** Any code in `tools/genesis/` or `tools/awareness/` that probes the
local dashboard MUST use `http://127.0.0.1:5050`, not `http://localhost:5050`.

This applies to:
- Health probes in `self_monitor` reflex
- Smoke checks in `kanban` reflex (post-merge route verification)
- Any new reflex that checks whether the dashboard is up before taking action

**Non-issue on Linux/macOS:** those platforms resolve `localhost` → `127.0.0.1`
(IPv4) directly. The `127.0.0.1` form is safe on all platforms.
