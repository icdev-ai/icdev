# CUI // SP-CTI

# Decision Record — `tools/cortex/client.py` is an external-only surface (`ctx-reach-02`)

**Status:** DECIDED — option (b), external-only, with enforced obligations.
**Date:** 2026-08-14
**Task:** `ctx-reach-02` — "Decide CortexClient's in-repo fate — 535 lines with zero in-repo consumers"
**Decision owner:** ICDEV platform
**Enforced by:** `tools/workflow/coherence_checker.py::check_external_only_surfaces`,
declared in `args/external_only_surfaces.yaml`.

---

## 1. TL;DR

`tools/cortex/client.py` keeps its zero in-repo consumers. It is **declared
external-only**, and that declaration now carries four obligations a gate
checks on every commit.

Option (a) — give it an in-repo consumer — was rejected because the only ICDEV
process that could call it **is the server it calls**. Every in-repo consumer
available to us is a loopback HTTP round trip into the process already holding
the callee, reached in-process today through `tools/cortex/api.py`. That is a
caller manufactured to satisfy a metric: it adds a service key, an auth hop and
network latency, and it adds no coverage the REST tests do not already have.

The card is right that the status quo was indefensible, but the defect was not
the missing consumer. It was that **nothing distinguished this file from dead
code**, and that the one thing standing between it and silent rot — its test
suite — was in `args/ci_test_backlog.txt` and had never gated a merge.

---

## 2. Premises checked

| # | Card claim | Verdict | Evidence |
|---|---|---|---|
| 1 | 535 lines | **REVISED → 542** | `wc -l tools/cortex/client.py` (and its `icdev/` twin, identical) |
| 2 | Zero production callers inside ICDEV | **CONFIRMED** | AST import census over every `*.py` in the repo: 7 importers, **all** under `tests/` (`tests/cortex/test_client.py`, `test_rest_agent.py` ×2, `test_rest_cost_volume.py` ×2, `test_rest_dashboard.py`, `test_rest_staffing_matrix.py`). Zero elsewhere. |
| 3 | Other references are tests and kanban seed descriptions | **CONFIRMED, plus two** | also `docs/reference/commands.md` (usage examples) and `args/vendor_api_manifest.json` (the committed API snapshot) |
| 4 | It is a client library with no in-repo client | **CONFIRMED** | 23 public methods on `CortexClient`, none reached from `tools/` |
| 5 | Stdlib-only, deliberately, so it can be vendored | **CONFIRMED** | imports are `json`, `os`, `typing`, `urllib` only; contract stated in the module docstring and `args/vendor_parity.yaml` |
| 6 | `check_capability_liveness` exists to catch this shape | **CONFIRMED, but it does not cover this** | `capability_consumption.py` measures seven telemetry-backed classes (reflex, mcp_dispatch_tool, agent_approval_rule, mcp_tool_authorization, prompt_template, audit_chain, skill_optimizer). A vendored SDK is not one of them, so no gate saw this file at all. |

Two corrections worth recording. The line count is 542, not 535 — small, but the
card's number is what a future session will grep for. And the premise "the only
references are tests and seed descriptions" understated the position: the public
API is already pinned by `args/vendor_api_manifest.json`, which means the
external contract was *half* institutionalised before this task. That materially
favours (b).

---

## 3. Why not (a): the in-repo consumer would be manufactured

The suggested consumer was "the dashboard calling Cortex over REST rather than
in-process". Examined concretely:

1. **The dashboard is the server.** `tools/cortex/blueprint.py` and
   `rest_v1.py` mount `POST /cortex/api/v1/{search,ask,complete,reason,classify,extract,govern}`
   on the same Flask app at `localhost:5050`. A dashboard-side `CortexClient`
   would open a TCP connection to itself.

2. **The in-process path already exists and is the governed one.**
   `tools/cortex/api.py` exposes `complete`, `reason`, `classify`, `extract`,
   `govern` and `agent` as functions, behind `_governed_facade` — the same
   governance chain the REST layer calls. Routing an in-repo caller through
   HTTP would *bypass nothing and add* a service key (`icdev_ctx_`), a bearer
   auth hop, JSON serialisation both ways, and a timeout budget.

3. **It would add no coverage.** The client's behaviour against a live socket is
   already exercised by `tests/cortex/test_client.py` (against a stub server)
   and `tests/cortex/test_rest_agent.py` (against a real socket, including the
   unreachable-host degradation path). A production loopback caller would
   re-test the same edges, more slowly and less deterministically.

4. **It would make the vendoring contract harder to hold.** The file is
   stdlib-only *by contract* so compass and idea_lab can copy it verbatim. An
   in-repo consumer creates constant pressure to reach for a platform helper —
   a logger, `get_connection`, the config loader — and the first one that lands
   silently breaks every vendored copy.

Point 4 is the decisive one. Option (a) does not merely add a useless caller; it
actively erodes the property that makes the file worth keeping.

**Karpathy #3 and #4 apply directly:** the simpler change is the honest label,
and manufacturing a caller is scope the task does not require.

---

## 4. Why (b) needed more than a comment

The card's own framing is the reason a documentation-only answer is not enough:
*"it is currently indistinguishable from dead code, which is this codebase's
signature defect."* A prose declaration is still indistinguishable from dead
code to everything except a human who happens to read it.

So the declaration was given obligations. `args/external_only_surfaces.yaml`
holds no counts and no budget — every key only **adds** a requirement:

| Obligation | Why it exists | State before this task |
|---|---|---|
| A decision doc, named **in the module docstring** | A reader opening `client.py` to ask "why does nothing call this?" gets the answer in the file, not in a config they have never heard of | absent |
| **Zero** production importers, checked in **both** directions | If a real importer appears, the declaration is stale and the FIX IS TO DELETE IT — the module became an ordinary capability and `capability_liveness` should govern it | unmeasured |
| Declared as a `vendor_parity.yaml` source | ICDEV CI never checks out compass/idea_lab, so without `args/vendor_api_manifest.json` "external-only" is an unfalsifiable claim about repos nobody here can see | already satisfied |
| Covered by a test in `args/ci_test_files/core.txt` | An external-only surface whose only tests are ungated is a module **no merge has ever verified** | **not satisfied** — `tests/cortex/test_client.py` was in `args/ci_test_backlog.txt` |

The bidirectional importer check is the part that keeps this from being a
suppression list. The usual failure mode of an exemption file is that it
outlives its justification; here, the justification becoming false is itself a
hard failure with a named remedy.

---

## 5. What changed

1. **`args/external_only_surfaces.yaml`** — new. Declares `tools/cortex/client.py`
   external-only with its reasoning and its four obligations.
2. **`check_external_only_surfaces`** — new coherence check, registered in
   `CHECK_REGISTRY`. Runs in **both** tiers (it is pure file reads plus an AST
   walk, machine-independent, and cannot skip), autofix tier `skip` — satisfying
   an obligation or dropping the declaration are both decisions.
3. **`tools/cortex/client.py` + `icdev/tools/cortex/client.py`** — docstring now
   opens with the external-only status and points at this document.
4. **`tests/cortex/test_client.py`** — moved from `args/ci_test_backlog.txt` into
   `args/ci_test_files/core.txt`. It passes today (9 tests, ~5s), so this is the
   sanctioned direction of travel: the census shrinks by one.
5. **`tests/workflow/test_external_only_surfaces.py`** — new, gated in the same
   PR, pinning the check's behaviour in both directions.

Note what did **not** change: no budget was raised anywhere, and
`args/liveness_gate.yaml` was not touched. A vendored SDK is not one of the
classes `capability_consumption.py` measures, so there was no budget to raise —
which is precisely why this needed a check of its own rather than an entry in
someone else's exemption list.

---

## 6. How to revisit this

Delete the entry from `args/external_only_surfaces.yaml` the moment a genuine
in-repo consumer exists — a separate ICDEV *process* (a sidecar, a CLI on a
different host, an agent runner talking to a remote Cortex) is a real consumer,
because for it the HTTP hop is not loopback. The check will fail until the entry
is removed, which is the intended prompt.

Retire the file outright if `args/vendor_parity.yaml` ever lists no consumers:
an SDK with no vendored copies and no in-repo caller is dead code again, and at
that point deletion — not declaration — is the answer.
