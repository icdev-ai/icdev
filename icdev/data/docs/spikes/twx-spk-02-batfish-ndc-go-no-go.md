# TWX Spike: Batfish for the NDC Twin — Go/No-Go (twx-spk-02)

> Spike deliverable — **no production code**. Feeds the TWX ADR (twx-xcut-01).
> Docs P1.4: replace/augment NDC's heuristic intent rules with Batfish formal
> reachability analysis.

## Summary decision

**GO — as an OPT-IN, self-hosted "deep reachability" validator that AUGMENTS
(never replaces) the heuristic fast path.** Keep the heuristic intent rules as
the default; Batfish is a slower, higher-fidelity second opinion behind a flag.

The spec's premise — *"NDC designs are graph JSON, not vendor configs; this
mismatch may kill the idea"* — is **only half true and NOT fatal** (see finding 1).

## Findings

### 1. Ingest mismatch is BRIDGEABLE via the existing config generator (key correction)
- Batfish's `bf_init_snapshot` wants **real vendor configs** (Cisco IOS, Arista
  EOS, Juniper JunOS, …) in a `configs/` directory — it will NOT ingest ICDEV's
  NDC graph JSON directly. So the raw-graph mismatch is real.
- **BUT** `tools/network/config_generator.py` already renders exactly those
  vendor configs from an NDC graph:
  > *"Generates vendor-specific configuration files (Cisco IOS, Arista EOS,
  > Juniper JunOS) from canvas topology data … return a dict of
  > {filename: config_text}."* (supports `ios_router`, `ios_switch`, `eos`,
  > `junos`).
- Therefore the pipeline **graph → `config_generator` → `configs/` dir →
  `bf_init_snapshot`** works today with no new modeling. The mismatch does not
  kill the idea; it just means Batfish consumes the *generated* configs, not the
  graph. (Fidelity caveat: Batfish is only as accurate as the generated config —
  if `config_generator` omits an ACL/route, Batfish can't see it.)

### 2. Accuracy delta vs the heuristic intent rules
NDC's current rules (`INTENT_RULES`): `reach-prod`, `no-direct-internet`,
`acl-compliance`, `il-boundary`, `no-unencrypted`, `redundancy` — all **string/
structure heuristics** over the delta graph.

Batfish adds **formal, data-plane reachability**:
- `reach-prod` / `no-direct-internet` / `il-boundary` — Batfish's
  `reachability()` / `reachability` queries compute *actual* forwarding paths
  across ACLs, routes, and NAT. This is a **material accuracy gain**: the
  heuristic can't tell whether a workload can *actually* reach the internet
  through multi-hop routing/NAT; Batfish can.
- `acl-compliance` — Batfish `searchFilters` / `testFilters` evaluate ACL
  semantics precisely (heuristic only pattern-matches `permit any any`).
- `redundancy` — Batfish `bidirectionalReachability` / edge failure analysis
  gives true N+1 proof vs the heuristic's "removed link with no replacement".
- `no-unencrypted` — **no Batfish advantage** (encryption is a link property,
  not a forwarding fact); heuristic stays authoritative.

Expected result on 3 reference topologies: Batfish catches **reachability /
boundary violations the heuristics miss** (multi-hop internet exposure, ACL
shadowing), at the cost of seconds-per-query and a config-generation step. It
does **not** replace `no-unencrypted` or `redundancy`-by-intent.

### 3. Air-gap deployment story (BETTER than LocalStack)
- Batfish is **Apache-2.0 OSS**, self-hostable as a Java service (the
  `batfish/allinone` image), with a `pybatfish` Python client. **No auth token,
  no phone-home, no per-seat license** — unlike LocalStack (twx-spk-01).
- So it **is** air-gap deployable: mirror the image into the internal registry
  (per the fed-01 air-gap rules) and run the service on the high side.
- Cost is **footprint**: a Java service + Docker (or a vendored JRE) — heavier
  than the repo's pure-Python/offline preference, but a one-time internal-mirror
  cost, not a recurring license.

## Go/No-Go per concern

| Concern | Verdict |
|---------|---------|
| Ingest NDC exports | **GO** — via `config_generator` (generated configs), not raw graph |
| Accuracy gain | **GO** — real reachability/ACL/redundancy for `reach-prod`, `no-direct-internet`, `il-boundary`, `acl-compliance` |
| `no-unencrypted` | keep heuristic (no Batfish benefit) |
| Air-gap | **GO** — OSS, self-hosted, no license (mirror image internally) |
| Footprint | **cost** — Java + Docker; acceptable as an opt-in high-fidelity path |
| Default path | **heuristics remain default** (fast, offline, zero-dep) |

## Recommended follow-ups (opt-in; heuristics stay default)
- `twx-bf-01` (gated): a `NdcBatfishValidator` behind `NDC_BATFISH_ENABLED`
  (default off) that (a) calls `config_generator` on the topology, (b) writes a
  Batfish snapshot, (c) runs `reachability`/`searchFilters` for the reachability/
  ACL/boundary intents, and (d) **merges** results into the canonical twin
  violation schema alongside the heuristic findings (method=`batfish-formal` vs
  `heuristic`) — never overriding the fast heuristic verdict when Batfish is
  unavailable.
- `twx-bf-02` (spike follow-on): quantify the accuracy delta on 3 saved
  reference topologies (`nc_versions`) to justify the footprint before GA.

**Decision:** GO for an opt-in augmentation; do not schedule `twx-bf-*` until the
Java/Docker footprint is accepted for the target deployment. Heuristics remain
the authoritative fast path in all environments.
