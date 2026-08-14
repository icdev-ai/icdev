# CUI // SP-CTI

# ctx-reach-03 — Reach decisions for `cortex.govern` and `cortex.agent`

**Date:** 2026-08-14
**Files:** `tools/cortex/api.py`, `tools/mcp/cortex_server.py`, `args/cortex_config.yaml`,
`tests/cortex/test_cortex_reach_decisions.py` (+ `icdev/` mirrors)

Two Cortex facades were flagged as declared-but-unconsumed, ICDEV's signature
defect. This card decides each of them explicitly, fixes the one real bug found
alongside, and closes out four related surfaces so none is left in the
undocumented third state (neither wired nor knowingly inert).

---

## 1. `cortex.agent` — **ADOPTED**

The card's premise ("ZERO production Python consumers") was wrong. `agent()` has
three live consumers, and each is now asserted to genuinely reach the facade
rather than merely be able to:

| Consumer | Path | Assertion |
|---|---|---|
| Cortex canvas chat, confirm-then-launch | `tools/cortex/blueprint.py::_launch_confirmed_agent` | `test_the_canvas_confirm_then_launch_path_reaches_the_facade` |
| `cortex_agent_launch` MCP tool | `tools/mcp/cortex_server.py::handle_cortex_agent_launch` | `test_mcp_agent_launch_goes_through_the_governed_facade` |
| `POST /cortex/api/v1/agent` | `tools/cortex/rest_v1.py::api_v1_agent` | `test_the_rest_agent_endpoint_reaches_the_facade` |

The first is an ordinary first-party Python module importing `tools.cortex.api`
and calling it — which is exactly what "a production Python consumer" means.

**What stays deliberately un-adopted.** An internal tool that already holds a
trusted, structured task calls `ACEController.launch` or the agent loop directly;
Studio nodes do the same. `agent()` is the governed entry for a goal arriving
from *outside* a trusted call path — a chat turn, an MCP client, a remote key —
i.e. text that must be injection-screened before it is allowed to authorise
action. Routing every internal ACE launch through it would put a governance
screen on first-party control flow and buy nothing.

**`cortex:agent` stays out of the default grant.** `service_keys.DEFAULT_SCOPES`
is `CORTEX_SCOPES`; `AGENT_SCOPES` is granted explicitly. This is the one scope
that makes the platform *act* rather than answer. Pinned by
`test_the_remote_agent_scope_is_still_never_granted_by_default`.

---

## 2. `cortex.govern` — **EXTERNAL-ONLY SURFACE** (documented, not adopted)

Zero in-repo Python callers, and that is the correct steady state.

* **Every in-process Cortex path is already governed.** `@_governed_facade` wraps
  complete / reason / classify / extract / search / ask / agent. An internal
  caller that wants TRUST calls a facade. Calling `govern()` on top of a facade
  result would run the chain a *second* time over one operation: two gateway
  screens, two redaction passes, two `source_citation_registry` rows, two
  `cortex_audit` rows, double latency, and a double count in `/cortex/metrics`.
  That is precisely the defect **ctx-trust-02** removed from four REST endpoints.
  "Adopting" `govern` internally would reintroduce it under a new name.
* **`POST /cortex/api/v1/govern` does not call it, and must not be "fixed" to.**
  `api_v1_govern` runs a single pipeline over its own identity lambda for two
  concrete reasons: (a) it returns the governed/**redacted text**, and
  `GovernanceReport` has no field to carry that — adopting the facade would
  silently drop a published response field; (b) it honours the caller's
  `retrieval` flag, which the facade fixes at `True`. Already pinned by
  `tests/cortex/test_rest_single_governance.py`; now also pinned from the other
  direction (`rest_v1` must not import `govern`).
* **Its one entry point is live.** `cortex_govern` (MCP) calls the facade
  directly. An "external adoption surface" no external caller can reach would be
  the declared-but-unconsumed defect wearing a justification, so that call is
  asserted, not assumed.

**How to reverse this.** The genuine adoption target is a **non-Cortex drafting
surface** — proposal / RFI / DIC / Tech Writer — that today calls
`tools/quality/citation_grounding.py` directly and would gain the full chain
(screen → redact → cite → ground → provenance → audit) from `govern()`.
Migrating one changes what blocks a promote or an export, so it is its own card,
not a drive-by here. Reversing this decision means naming that consumer and
showing it is not already governed.

---

## 3. The latent bug: `agent(mode=...)`

**Status: the membership check was already in place; the second implementation
was not.**

`api.agent` (api.py) normalises `mode` and raises `ValueError` on anything
outside `_AGENT_MODES`, and `validators.validate_agent` refuses it at the REST
door — both landed with hgx-cx-01/02. The `args/projects.yaml` note predated
those.

What was still live is the thing the note was really about. `cortex_server.py`
carried `_agent_launch_fallback`, guarded by
`getattr(cortex_api, "agent", None)`:

```python
use_team = mode == "team" or (mode == "auto" and bool(roles))
```

That is the exact dispatch `api.agent` replaced. Reached through it, an
unrecognised `mode` — a typo, or `"graph"` before graph mode existed — did not
error: it fell through to `run_agent_loop` and ran a **real, billed, ungated-by-
its-own-intent single agent**. The fallback also reached ACEController and the
agent loop directly, with **no TRUST chain at all**.

The probe could never fail, because the facade has existed since ctx-govern-04.
A dead branch guarding an ungoverned duplicate of a governed operation is worse
than no branch, so `_agent_launch_fallback` and both `getattr` probes were
**deleted**. A missing facade is now an `ImportError` the handler reports.

`handle_cortex_agent_launch` passes `mode` through unvalidated **on purpose**:
the facade owns the vocabulary, and screening it in the handler too would fork
the accepted set into two places that can disagree. The refusal reaching the MCP
caller as a tool error is asserted.

The reject case is asserted at every layer, and — importantly — with all three
backends stubbed to `pytest.fail`, so the tests distinguish "the mode was
rejected" from "the mode was accepted and something ran". A membership assertion
alone would have passed against the buggy dispatch.

---

## 4. Related dead surface — swept

### 4a. `cortex_server.py::main()` / `build_server()` — **kept, documented**

`.mcp.json` configures only `icdev-unified`, and all eight `cortex_*` tools are
served there from `TOOL_REGISTRY`, so this stdio server is never launched in this
repo. It is **not** removed: it is a *bounded* surface for an external or
air-gapped MCP client that must see only the Cortex family and not the full
unified registry, it is a documented command
(`docs/reference/commands.md`), and `build_server()` is exercised by
`tests/cortex/test_mcp_tools.py`.

The actual defect was that nothing said `.mcp.json` skips it on purpose. Fixed in
the module docstring and the feature doc. The risk of two entry points for one
tool set is **drift**, so that is what is now gated:
`test_the_standalone_cortex_stdio_server_cannot_drift_from_the_unified_one`
fails if any tool in `CORTEX_TOOLS` is absent from `TOOL_REGISTRY` (i.e. would be
reachable only via the server nobody launches).

### 4b. The `compliance` domain lens — **WIRED**

`CORTEX_DOMAIN_LENSES` declared `📋 Compliance — NIST 800-53, FedRAMP, CMMC, STIG
controls and crosswalks` and offered it in the canvas picker, but there was no
`search.domains.compliance` block, so `load_domain_profile("compliance")`
returned `None`: no backend narrowing, no persona, no intents. Selecting it
changed **nothing** except the `ctx.domain` tag on the audit row — worse than
inert, because the picker advertised scoping that did not happen.

`args/cortex_config.yaml` now defines it (backends `rag/graph/kb`, collections,
intents, and a control-literate persona that separates *implemented* from merely
*documented*). `triage: false`, matching `network` — only `security` has a
bespoke formatter, and claiming `triage: true` without one advertises a summary
`summarize()` returns `None` for.

`test_every_declared_domain_lens_resolves_to_a_profile` now walks
`CORTEX_DOMAIN_KEYS` and fails on any declared lens with no profile, backends, or
persona — so the next lens added to the picker cannot ship inert.

### 4c. Empty `sources:` on document / proposal / network / compliance — **documented as intentional**

Row-level scoping (`domains.filter_by_sources`) **drops** every hit whose source
prefix does not match. `security` populates it because its backing table families
are known exactly (`pvm_`, `dsoc_`, `incident_`, `cve`…). The other four lenses
retrieve from federated corpora — RAG collections, KG nodes, DIC documents, KB
entries — whose source ids are not a closed prefix set, so a guessed list would
**silently delete legitimate evidence**. A retrieval lens that quietly returns
less is strictly worse than one that does not row-scope at all.

This is a standing decision, not a TODO. `args/cortex_config.yaml` states it
once for all four lenses, and
`test_empty_domain_sources_are_a_decision_and_a_documented_no_op` asserts both
halves: empty → true no-op, and `security` → still drops two of three rows (the
behaviour the empty lists avoid).

### 4d. `gap_handlers._REASON_IGNORED_PARAMS` — **already intentional, now asserted**

Four params (`max_rounds`, `self_consistency_runs`, `num_debaters`,
`debate_rounds`) that the pre-Cortex `cot_invoke` / `cod_invoke` schemas
advertised and the governed `cortex.reason` facade has no seam for. They stay in
the `input_schema` because MCP tool schemas are a public contract, and the
handlers they were declared for never existed, so no caller can be depending on
them. They are echoed back in `ignored_params` so the no-op is **visible in the
response**, which is the part that makes this acceptable rather than silent
breakage. Now asserted end-to-end.

---

## Observed, out of scope

`domain_cfg["collections"]` is carried into `router_record["domain_scope"]` as
observability metadata and is not used to narrow retrieval on any lens,
`security` included. That is pre-existing across all five lenses and is a
separate question from this card's; noted here so it is not rediscovered as new.

---

## Verification

```bash
pytest tests/cortex/test_cortex_reach_decisions.py -q   # 25 passed
pytest tests/cortex/test_mcp_tools.py -q                # 33 passed
pytest tests/cortex/test_api_governed.py tests/cortex/test_validators.py \
       tests/cortex/test_rest_api.py tests/cortex/test_rest_single_governance.py \
       tests/cortex/test_domain_lenses.py -q
```

Gated in `args/ci_test_files/core.txt`.
