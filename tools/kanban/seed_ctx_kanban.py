# CUI // SP-CTI
"""Seed the CTX card — Cortex Activation & Hardening.

Successor to ``cxo`` (21/21 done). The finding is ACTIVATION and ENFORCEMENT,
not performance: Cortex is ~11k lines, well-architected, and almost entirely
switched off. Governance ships fail-open, the response cache ships disabled, the
canvas ships disabled, the 535-line client has zero in-repo consumers, and 56 of
its test modules — every tenant-isolation, redaction and provenance test among
them — have never gated a merge.

This card is **MANUAL-ONLY**. ``ctx-gate-00`` is held ``in_progress`` and every
task declares ``depends_on_task_id`` on it, because a held gate alone does not
actually hold — ``depends_on_task_id`` is the real gate. Release with::

    python tools/kanban/cli.py --set-status ctx-gate-00 done

Run it as a MODULE, from the repo (or worktree) root::

    python -m tools.kanban.seed_ctx_kanban            # seed
    python -m tools.kanban.seed_ctx_kanban --json     # machine-readable report
    python -m tools.kanban.seed_ctx_kanban --dry-run  # print, insert nothing

Invoking it by path (``python tools/kanban/seed_ctx_kanban.py``) puts the
script's own directory on ``sys.path`` instead of the repo root, so ``tools.*``
either fails to import or — worse, from inside a worktree — resolves to the
SHARED checkout and seeds against the wrong database.
"""

from __future__ import annotations

import argparse
import json
import sys

GATE = "ctx-gate-00"

# Mirrors the live CHECK constraint `kanban_tasks_task_type_check`. Note there is
# no "bug" — use "fix". SQLite does not enforce the constraint, so a bad value
# seeds cleanly against a fallback DB and only blows up on PostgreSQL part-way
# through the insert loop; asserting here fails it at --dry-run instead.
VALID_TASK_TYPES = frozenset(
    {"build", "run", "fix", "research", "deploy", "test", "chore"}
)


def _t(
    task_id: str,
    title: str,
    description: str,
    *,
    depends_on: str | None = GATE,
    priority: str = "medium",
    task_type: str = "build",
    status: str = "backlog",
    acceptance: str | None = None,
) -> dict:
    if task_type not in VALID_TASK_TYPES:
        raise ValueError(
            f"{task_id}: task_type {task_type!r} violates "
            f"kanban_tasks_task_type_check; allowed: {sorted(VALID_TASK_TYPES)}"
        )
    spec: dict = {
        "id": task_id,
        "title": title,
        "description": description.strip(),
        "task_type": task_type,
        "priority": priority,
        "status": status,
    }
    if depends_on:
        spec["depends_on_task_id"] = depends_on
    if acceptance:
        spec["acceptance_criteria"] = acceptance.strip()
    return spec


_CONTEXT = """
Card: CTX — Cortex Activation & Hardening. Gated on ctx-gate-00.

GROUND RULE: Cortex is already well-built. This card ACTIVATES and ENFORCES what
exists. Do NOT build a second facade, a second cache, or a second governance
pipeline. Extend tools/cortex/.

BINDING ACCEPTANCE CRITERIA on every task in this card:
  LLM-agnostic  — no model IDs in Python. Route by llm_function through
                  LLMRouter, and DECLARE that function under `routing:` in
                  args/llm_config.yaml. A function declared only under
                  `task_categories:` silently falls back to routing.default,
                  which is cloud-first — that is defect D-A on this very card.
  OS-agnostic   — encoding="utf-8" AND newline="" on every file write; pathlib;
                  repo root from __file__, never os.getcwd(); no shell.
  PG-primary    — author PostgreSQL. Compute JSON in Python rather than relying
                  on translate_sql. Every INSERT column must exist in the LIVE
                  schema; adding one means a migration.
  Mirror        — tools/cortex/ and icdev/tools/cortex/ are byte-identical and
                  pinned by args/mirror_parity.yaml. Mirror every change.
  Gate the test — a new test file is added to args/ci_test_files/core.txt IN THE
                  PR THAT MAKES IT PASS. Never bulk-widen.
"""


TASKS: list[dict] = [
    # ══════════════════════════════════════════════════════════════════
    # GATE
    # ══════════════════════════════════════════════════════════════════
    _t(
        GATE,
        "CTX HOLD GATE — do not dispatch until released",
        """
This card changes governance enforcement, the air-gap guarantee and the CI test
gate. It must not be built unattended.

Every task declares depends_on_task_id on this task, because a held gate alone
does not hold — the runner reads depends_on_task_id, not gate status.

ctx-reach-01 additionally touches the PRIVATE compass and idea_lab repos, which
the runner cannot check out or build in. That task stays manual even after this
gate is released.

Release, once a human is driving:
    python tools/kanban/cli.py --set-status ctx-gate-00 done
""",
        depends_on=None,
        priority="critical",
        task_type="chore",
        status="in_progress",
    ),

    # ══════════════════════════════════════════════════════════════════
    # TRUST — stop governance reporting more than it does
    # ══════════════════════════════════════════════════════════════════
    _t(
        "ctx-trust-01",
        "cortex.ask(summarize=True) violates air-gap via an undeclared routing function",
        """
tools/cortex/analyst.py:646 calls LLMRouter().invoke("summarization", ...).

"summarization" is declared ONLY under `task_categories:` in
args/llm_config.yaml — NOT under `routing:` (verified by parsing the YAML).
LLMRouter.get_provider_for_function does
routing.get(function, routing.get("default", {})), so it silently falls back to
routing.default, whose chain is CLOUD-FIRST:
[kimi-cloud, qwen3-local, claude-sonnet, gpt-4o, gemini-2.5-flash, llama-local].

Three compounding problems, all verified:
  1. No exclude_model_ids is passed, unlike every other Cortex LLM call
     (api.py:464). A caller with ICDEV_AIRGAP=1 will attempt kimi-cloud FIRST.
  2. CORTEX_ROUTING_FUNCTIONS (config.py:56) omits it, so assert_airgap_ready()
     (api.py:1000) never validates it.
  3. The failure is swallowed at analyst.py:670-672 (return None) and
     _finalize_result degrades to the deterministic row summary — which carries
     grounding="rows_by_construction", a STRONGER trust label. A broken and
     policy-violating path looks like it worked, and looks better than it is.

docs/features/cortex-unified-ai-layer.md:140-142 asserts the whole facade is
air-gap safe, "a guard, not a hope". This contradicts it.

FIX: declare a `cortex_summarize` chain under `routing:`; add it to
CORTEX_ROUTING_FUNCTIONS; use the get_router() singleton instead of constructing
LLMRouter() per call (which re-parses the ~2000-line config and resets the
availability cache every time); thread exclude_model_ids. Narrow the swallow so
a failed summary is reported rather than silently relabelled.
""",
        priority="critical",
        task_type="fix",
        acceptance="""
- `summarization`/`cortex_summarize` resolves through `routing:`, NOT routing.default.
- With an air-gapped context, ask(summarize=True) never selects a cloud model —
  assert on the resolved chain, not on a mock.
- assert_airgap_ready() covers the function (it is in CORTEX_ROUTING_FUNCTIONS).
- A failed summary does NOT return grounding="rows_by_construction" as if it had
  succeeded; the degradation is visible to the caller.
- Test gated in args/ci_test_files/core.txt in this PR.
""",
    ),
    _t(
        "ctx-trust-02",
        "REST v1 runs the TRUST chain twice on four endpoints",
        """
tools/cortex/rest_v1.py:40 imports the GOVERNED facades from .api (correct — the
comment explains importing from .analyst/.search_service would bypass governance
entirely). It then re-wraps four of them in a SECOND GovernancePipeline via
_governed():

  api_v1_complete  rest_v1.py:267
  api_v1_reason    rest_v1.py:288
  api_v1_classify  rest_v1.py:304
  api_v1_extract   rest_v1.py:320

Each inner facade is already @_governed_facade (api.py:557, 622, 682, 739).

Per REST call that means: two gateway check_text calls, two input-redaction
passes, two output-redaction passes, two register_citation provenance rows, and
two cortex_audit rows. Roughly 2x the fixed gate latency, and /cortex/metrics
DOUBLE-COUNTS every REST-origin complete/classify/extract/reason.

The codebase already knows. rest_v1.py:378-383 explains that api_v1_agent is
deliberately NOT wrapped because doing so "would run the chain twice over one
launch and write two audit rows for it" — and names complete/reason/classify/
extract as the ones that do. search/ask are also called bare (rest_v1.py:235,
246). These four were simply missed.

FIX: call the governed facades directly, matching agent/search/ask. A blocked
pre-check still raises GovernanceBlockedError from inside the facade and the
endpoint decorator still maps it to a 403.
""",
        priority="critical",
        task_type="fix",
        acceptance="""
- One REST POST to /cortex/api/v1/complete writes EXACTLY ONE cortex_audit row
  and ONE source_citation_registry row (count before/after — do not assert on a
  mock).
- Same for reason, classify, extract.
- A governance-blocked request still returns 403 with the governance envelope.
- Test gated in args/ci_test_files/core.txt in this PR.
""",
    ),
    _t(
        "ctx-trust-03",
        "IQE Cortex adapters close a caller-owned connection and swallow silently",
        """
tools/iqe/adapters/cortex.py:19-23 returns the caller's conn unchanged when one
is supplied, then UNCONDITIONALLY closes it in finally: (lines 38-39, 54-55,
70-71). All three adapters also swallow with a bare `except Exception: return []`
and NO logging.

analyst._ask_iqe (analyst.py:936-941) opens one connection and hands it to
execute_query(ast, conn). Executor._fetch_union / _fetch_join
(tools/iqe/executor.py:66-76) fetch multiple collections IN PARALLEL over that
one shared connection. So for
  foreach s in union("cortex.audit","cortex.search_history")
adapter #1 closes the connection while adapter #2 is still executing, adapter #2
raises, the bare except returns [], and HALF THE UNION SILENTLY RETURNS ZERO
ROWS — reported as a complete answer. Concurrent use of a single psycopg2
connection from two threads is independently unsafe.

FIX: an adapter must not close a connection it did not open. Replace the bare
swallow with a log plus a re-raise (or a sentinel the executor can distinguish),
so a failed collection can never masquerade as "no rows".
""",
        priority="high",
        task_type="fix",
        acceptance="""
- A caller-supplied connection is still OPEN after every adapter returns.
- An IQE union over two cortex collections returns rows from BOTH.
- An adapter failure is distinguishable from an empty result set by the caller,
  and is logged at warning or above (not debug).
- Test gated in args/ci_test_files/core.txt in this PR.
""",
    ),
    _t(
        "ctx-trust-04",
        "IQE cortex adapters cap at 500 rows BEFORE filtering, producing confidently wrong answers",
        """
tools/iqe/adapters/cortex.py:32, 48, 64 all issue
  ORDER BY created_at DESC LIMIT 500
with NO WHERE clause. tools/iqe/executor.py:35-37 then applies the query's where
clauses IN PYTHON, after the cap.

So cortex.ask("how many blocked calls in the last 30 days?") counts only within
the newest 500 audit rows — and analyst.py:580-587 returns that answer with
grounding="rows_by_construction", confidence: include, confidence_score: 1.0.
The answer is wrong and is labelled maximally trustworthy.

This is the worst class of defect on this card: not a crash, but a confident
falsehood on the exact surface an operator uses to audit Cortex itself.

FIX (either, argue for one in the PR):
  (a) push the WHERE / time-window predicates into the adapter SQL so the cap
      applies after filtering; or
  (b) surface truncation in the result so a capped scan cannot be reported as
      confidence_score 1.0 — the executor already has a `truncated` concept in
      metrics.py:407-411 worth reusing.
Related: tools/iqe/executor.py:62 falls back to `SELECT * FROM {table}` with NO
LIMIT for unregistered collections — an unbounded scan into Python memory.
""",
        priority="high",
        task_type="fix",
        acceptance="""
- A question over a window containing >500 matching rows returns either the
  correct count, or an explicitly truncated result — never a wrong count at
  confidence_score 1.0.
- Assert against a seeded table with >500 rows; do not mock the executor.
- Test gated in args/ci_test_files/core.txt in this PR.
""",
    ),
    _t(
        "ctx-trust-05",
        "api_iqe_query executes with no explicit security context and unbounded rows",
        """
tools/cortex/blueprint.py:593 calls execute_query(ast, conn=None).

The analyst path threads tenant/classification explicitly via
_apply_security_context (analyst.py:470-493), whose docstring warns: "Never
silently fall through to an unscoped connection." This route instead relies
entirely on get_connection() picking up flask.g.security_context. Rows are also
returned unbounded (blueprint.py:600-601).

Cortex is the component that enforces tenant isolation and Bell-LaPadula
read-down, so the one route that does not thread context explicitly is worth
closing even if g.security_context happens to be populated today.

FIX: thread an explicit security context, matching the analyst path. Bound the
result set.
""",
        priority="high",
        task_type="fix",
        acceptance="""
- The route passes an explicit security context rather than relying on ambient
  flask.g state.
- A cross-tenant query returns no foreign rows — assert the DENY case, not just
  the allow case.
- Results are bounded.
- Test gated in args/ci_test_files/core.txt in this PR.
""",
    ),

    # ══════════════════════════════════════════════════════════════════
    # ENF — make the gates able to fire
    # ══════════════════════════════════════════════════════════════════
    _t(
        "ctx-enf-01",
        "check_vendor_parity cannot fire in CI — make vendored drift detectable without the consumer repos",
        """
The vendored Cortex clients are 2 public methods behind canonical:
CortexClient.reason() and .agent(), added ce78c1aeb (#1447, 2026-08-09).
args/vendor_parity.yaml still records last_synced: "2026-08-02".

A check exists specifically to catch this — coherence_checker.py:8234
check_vendor_parity, shipped BY cxo-doc-03, running in the FAST tier on every
task gate. It computes the drift correctly. It cannot block:

  CI (consumer repos not checked out) : PASS — verified by simulating an absent
                                        root WITH --gate AND --changed-files
                                        tools/cortex/client.py
  Local full-repo sweep               : warn, overall_pass true, exit 0
                                        (coherence_checker.py:8269)
  Local + --changed-files naming it   : fail — the ONLY blocking configuration

Mechanism: coherence_checker.py:8313-8318 SKIPS a consumer whose path is absent
rather than failing, so `drift` stays empty.

ROOT CAUSE IS REPO TOPOLOGY, NOT OS. ICDEV is OS-agnostic; what matters is that
compass and idea_lab are separate PRIVATE repos ICDEV CI never checks out.
/home/me/standalone would skip exactly as C:/AI/standalone does. Making the path
portable does NOT fix this.

FIX: commit a generated public-API manifest for tools/cortex/client.py into
ICDEV, and fail when the client changes without the manifest being regenerated.
That is CI-verifiable with no external checkout, and makes re-vendoring a
deliberate step. Reuse _public_api() (coherence_checker.py) so the manifest and
the check cannot disagree. Complement — do not replace — by running the existing
parity check in compass/idea_lab CI, which DO have the vendored copy.

Also drop the machine-specific `ICDEV_STANDALONE_ROOT: C:/AI/standalone` default
from args/vendor_parity.yaml — hygiene for an OS-agnostic repo, but note it does
not fix the gate on its own.
""",
        priority="critical",
        acceptance="""
- Changing tools/cortex/client.py WITHOUT regenerating the manifest fails CI on
  a runner with no standalone checkout. This is the exact case that passes today
  — prove it fails now.
- The check still SKIPS (never falsely fails) when a consumer repo is genuinely
  absent; the manifest is what carries enforcement.
- args/vendor_parity.yaml no longer hardcodes a Windows path as a default.
- Test gated in args/ci_test_files/core.txt in this PR.
""",
    ),
    _t(
        "ctx-enf-02",
        "Gate the 10 security-critical Cortex test files, one per PR",
        """
tests/cortex/ holds 50 test modules. 56 cortex-named modules sit in
args/ci_test_backlog.txt — which that file's own header defines as tests "CI has
NEVER run, so [they have] never gated a merge." Exactly ONE cortex-related file
is gated (tests/test_cxo_adopt_04_mcp_cortex_adapters.py), and it is not even in
tests/cortex/.

Ungated, on the component that enforces tenant isolation, Bell-LaPadula
read-down, egress redaction and an append-only NIST-AU trail:

  test_tenant_isolation.py      test_service_keys.py
  test_egress_redaction.py      test_rest_scopes.py
  test_provenance_gate.py       test_security_lens.py
  test_provenance_swallow.py    test_governance_hardening.py
  test_governance_pipeline.py   test_governance_profiles.py

DO NOT BULK-WIDEN. These are ungated AND an unknown number are red; adding them
wholesale turns main red and the gate gets disabled, which is strictly worse
than the debt. Add ONE FILE PER PR, in the PR that makes it pass, removing it
from args/ci_test_backlog.txt at the same time. The census only ever shrinks.

Sequence by blast radius: tenant_isolation, then rest_scopes, service_keys,
egress_redaction, provenance_gate, provenance_swallow, then the governance_*
trio, then security_lens.
""",
        priority="critical",
        task_type="test",
        acceptance="""
- Each PR moves exactly ONE file from args/ci_test_backlog.txt into
  args/ci_test_files/core.txt, and that file passes in CI.
- `python tools/ci/gated_test_list.py --check --list core` reports no duplicates
  (union merge creates them — --check-coverage alone does NOT catch a duplicate).
- backlog_max in args/test_gating_gate.yaml only ever decreases.
- No file is added that has not been run green first.
""",
    ),
    _t(
        "ctx-enf-03",
        "Three Cortex config keys are read by nobody — wire them or delete them",
        """
All three are referenced ONLY by the defaults dict in tools/cortex/config.py.
Nothing reads them at runtime:

  search.strategy_weights (cortex_config.yaml:27)
      _rrf_fuse (search_service.py:169) computes contrib = 1.0/(k+rank) with NO
      weight term. The YAML comment documents `fused = weight/(rrf_k+rank)`,
      which is not what the code does. Tuning rag:1.0 / kb:0.6 has zero effect.

  analyst.nlq_fallback_enabled (cortex_config.yaml:236)
      analyst.ask()'s fallback eligibility (analyst.py:866-885) never consults
      it. An operator disabling the LLM NL->SQL path FOR POLICY REASONS has no
      effect — ask() still falls back. This is the one with real consequences.

  governance.skip_grounding_for_plain_complete (cortex_config.yaml:169)
      Behaviour is realized structurally (governance.py:706-708, 768-769), so
      flipping it to false will NOT start grounding plain completions.

They survived because the tests assert the keys LOAD
(test_airgap_assertion.py:332-336, 354, 402; test_search_router.py:386), never
that they change behaviour.

FIX: for each, wire it or delete it — both are acceptable, argue the choice.
Whichever is chosen, the test must assert BEHAVIOUR CHANGES, not that the value
round-trips through the loader. A config key that cannot change behaviour is a
lie told to an operator.
""",
        priority="high",
        task_type="fix",
        acceptance="""
- For each of the 3 keys: either it demonstrably changes behaviour, or it is
  gone from the YAML, the defaults dict and the docs.
- Every new test asserts an OBSERVABLE DIFFERENCE between two values of the key.
  A test that only asserts the key loads is not acceptable.
- Test gated in args/ci_test_files/core.txt in this PR.
""",
    ),

    # ══════════════════════════════════════════════════════════════════
    # OBS — measure before optimizing
    # ══════════════════════════════════════════════════════════════════
    _t(
        "ctx-obs-01",
        "cortex.search and cortex.govern record zero cost and latency",
        """
GovernancePipeline._audit (governance.py:559-565) only populates the accounting
fields (cost_usd, latency_ms, tokens, model) when isinstance(result,
CortexResult). search returns a list (api.py:800-802) and govern returns a str
(api.py:839). Both appear in `calls` but contribute NOTHING to cost_usd,
avg_latency_ms or by_model.

cortex.search is the MOST EXPENSIVE facade — multi-backend fan-out, optional
CRAG re-retrieval, plus a rewrite LLM call — and it is completely invisible in
the spend and latency panels. Any optimization decision made from /cortex/metrics
today is made with the biggest consumer missing.

This lands BEFORE ctx-perf-* on purpose: optimize what you can measure.

FIX: normalize the accounting capture so it does not depend on the return type —
either wrap the non-CortexResult returns, or capture timing/cost in the pipeline
independent of what the operation returns.
""",
        priority="high",
        acceptance="""
- /cortex/metrics reports non-zero avg_latency_ms for cortex.search after a real
  search.
- cortex.govern likewise records latency.
- by_function counts are unchanged (they were already exact) — prove no
  regression.
- Test gated in args/ci_test_files/core.txt in this PR.
""",
    ),
    _t(
        "ctx-obs-02",
        "The 7-gate governance chain's own cost is never measured",
        """
latency_ms comes from CortexResult.latency_ms, set from response.duration_ms or
the time.perf_counter() around _invoke (api.py:616-619, 493) — i.e. the LLM call
ONLY. No timer wraps GovernancePipeline.wrap.

So the question "how much of Cortex latency is governance?" cannot be answered,
which is exactly the question that decides whether the TRUST chain is worth its
cost and whether ctx-perf work should target the gates or the model call.

Note metrics latency/cost are also SAMPLED, not complete — only the newest
_DETAIL_ROW_LIMIT = 5000 rows in the window have gates_json parsed
(metrics.py:65, 214-219). Counts stay exact. This is at least honest:
detail.truncated is surfaced (metrics.py:407-411). Do not silently widen it.

FIX: time the chain, and surface gate cost distinctly from LLM latency. gates_json
already carries per-gate outcomes (db/init_db.py:307-330) — per-gate timing is a
natural extension of a field that already exists.
""",
        priority="high",
        acceptance="""
- A governed call records total wall time AND the LLM-call time, so gate
  overhead is derivable.
- The existing detail.truncated honesty is preserved, not widened silently.
- Test gated in args/ci_test_files/core.txt in this PR.
""",
    ),

    # ══════════════════════════════════════════════════════════════════
    # PERF — reduce the cost of being governed (after OBS)
    # ══════════════════════════════════════════════════════════════════
    _t(
        "ctx-perf-01",
        "Config path resolution costs ~40 filesystem syscalls per Cortex call",
        """
load_cortex_config() does path.stat() UNCONDITIONALLY before consulting its
mtime memo (config.py:155), and resolve_cortex_config_path() ->
resolve_llm_config_path() -> _walk_up_for_config() is_file()-probes every parent
directory (tools/llm/config_path.py:44-53) with no memoization.

Callers per governed retrieval call: is_enabled(), cacheable(), _ttl_for(),
resolve_fail_closed() (up to 3 sites — governance.py:508, 692, 754),
_content_grounding_floor() (x2 — governance.py:727, 749), _gate_ground_content
(governance.py:314). That is roughly 8-12 load_cortex_config() calls and 40+
filesystem syscalls PER CORTEX CALL, paid whether or not the cache is on.

Also: api.py:1000 assert_airgap_ready() runs at MODULE IMPORT, reading and
parsing llm_config.yaml via _load_yaml (config.py:125-134) directly — not through
the mtime memo — on every process that imports tools.cortex.api.

FIX: memoize the resolved path; consult the memo before stat(); or pass config
once through the pipeline rather than re-loading per gate.
""",
        acceptance="""
- Measured reduction in load_cortex_config() calls per governed call (count them
  in a test, do not eyeball).
- Config changes are still picked up — the mtime invalidation must survive.
- Test gated in args/ci_test_files/core.txt in this PR.
""",
    ),
    _t(
        "ctx-perf-02",
        "_allowed_tables() is recomputed once per table inside a comprehension",
        """
tools/cortex/analyst.py:348:

    off_allowlist = [t for t in tables if t not in _allowed_tables()]

The call is in the comprehension's CONDITION, so Python re-evaluates it on every
iteration. _allowed_tables() (analyst.py:274-291) calls list_collections() AND
_canvas_iqe_mapping() -> get_registry().get_iqe_mapping(), which loads the
component registry. A 5-table SQL query therefore does 5 full registry+executor
scans, on the SQL-safety path of every analyst query.

FIX: hoist it. One line.
""",
        priority="high",
        acceptance="""
- _allowed_tables() is evaluated once per validation call regardless of table
  count — assert with a call counter, not a timing measurement.
- SQL safety behaviour is unchanged (the allowlist still rejects what it did).
""",
    ),
    _t(
        "ctx-perf-03",
        "Five to six DB connections per Cortex chat turn",
        """
One POST /cortex/api/chat opens, commits and closes SEPARATE connections in:
  chat_session.ensure_session   (chat_session.py:73)
  chat_session.record_turn x2   (chat_session.py:122)
  blueprint._record_history     (blueprint.py:98)
  the governance audit          (db/init_db.py:381)

record_governed_call (db/init_db.py:362-395) was written specifically to collapse
this for the audit pair (cxo-perf-03). The chat store never got the same
treatment.

FIX: extend the same one-connection pattern to the chat-turn writes.
""",
        acceptance="""
- A chat turn opens materially fewer connections — assert with a connection
  counter.
- Chat persistence still works: a turn is readable after the request (see
  ctx-perf-06's note that these writes are currently swallowed at debug).
- Test gated in args/ci_test_files/core.txt in this PR.
""",
    ),
    _t(
        "ctx-perf-04",
        "RAG config is re-read and re-parsed from disk on every Cortex search",
        """
tools/rag/retriever_common.py:73 constructs a brand-new RAGRetriever per Cortex
search. RAGRetriever.__init__ (tools/rag/retriever.py:291-293) calls
_load_rag_config() (retriever.py:62-80), which reads and yaml.safe_load()s the
RAG config from disk WITH NO MTIME CACHE — unlike cortex/config.py:147-162,
which memoizes. Every cortex.search with the rag backend pays a disk read plus a
full YAML parse.

search_service.py:334 likewise constructs a new DICSearchEngine per call.

Two adjacent defects on the same hot path, worth fixing here:
  - tools/rag/retriever.py:364 hardcodes model="nomic-embed-text" in the fallback
    embedding path. Not in Cortex, but on Cortex's hot path, and a violation of
    the LLM-agnostic rule the Cortex modules obey.
  - tools/rag/retriever.py:366-367 swallows an embedding failure with
    `except Exception: return []` and NO log, which surfaces to the user as
    "No matching results were found across the Cortex backends"
    (blueprint.py:436). A dead embedding provider is reported as "nothing
    matched".

DO NOT regress the good work already there: the fan-out uses a single
process-wide bounded ThreadPoolExecutor (search_service.py:68-100) that was
explicitly fixed for a thread leak.
""",
        acceptance="""
- RAG config is memoized (mtime-keyed, matching cortex/config.py) — assert the
  file is not re-parsed on a second search.
- The hardcoded embed model id is routed by function instead.
- An embedding failure is distinguishable from a genuine zero-result, both in
  logs and in what the user is told.
- Test gated in args/ci_test_files/core.txt in this PR.
""",
    ),
    _t(
        "ctx-perf-05",
        "Missing indexes on the Cortex tables the RLS predicate makes hot",
        """
get_connection() injects a tenant RLS predicate, so effectively EVERY query is
`tenant_id = ? AND ...`. The indexes do not match that shape:

  cortex_audit          — separate indexes on session_id, tenant_id, function,
                          created_at (262_cortex_tables.sql:57-60), but NO
                          composite (tenant_id, created_at). Every metrics._scan
                          window query (metrics.py:208-219) is exactly
                          tenant_id + created_at and can use only one.
                          metrics.py:41-43 says the composite is not needed until
                          a per-tenant windowed rollup exists — the RLS predicate
                          means that is already every query.
  cortex_search_history — session_id ONLY (263:55). No tenant_id, no created_at,
                          no user_id. Any usage-over-time query is a full scan
                          under an RLS tenant predicate.
  cortex_messages       — (session_id, turn_number); no tenant_id.
  cortex_chat_sessions  — user_id, tenant_id; no created_at, yet
                          iqe/adapters/cortex.py:32 sorts by it.

FIX: add the composite indexes via a MIGRATION. Never hand-number it:
    python tools/db/migrate.py --create "<name>"
""",
        acceptance="""
- Migration scaffolded with tools/db/migrate.py (14-digit UTC id, not a
  hand-picked number), with an up.sql.
- EXPLAIN shows the composite index used for the metrics window query on PG.
- Migration is idempotent and runs clean on a fresh DB.
""",
    ),
    _t(
        "ctx-perf-06",
        "Enable the response cache — deepcopy on hit and an invalidation story first",
        """
tools/cortex/cache.py is 187 lines of correct, well-reasoned cache, wired at
api.py:225-262, and `enabled: false` in both the YAML (cortex_config.yaml:248)
and the code defaults. No consumer has ever flipped it.

The security model is already sound and should NOT be redesigned: the key folds
tenant_id + classification + domain + air_gap (cache.py:129-148), only the FINAL
post-redaction result is stored, and every hit still writes a cortex_audit row.
cache.operations deliberately omits reason/govern/agent.

TWO THINGS MUST LAND BEFORE THE FLIP:

1. Cached objects are shared BY REFERENCE. cache.py:156 stores the live result
   and api.py:234 returns it verbatim, so every hit hands out the SAME
   CortexResult instance. Any caller mutating result.text or result.metadata
   poisons every subsequent hit. Contrast metrics.py:262, 301, which deepcopies
   its memo for exactly this reason.

2. There is NO invalidation — only TTL expiry and LRU eviction. No corpus-change,
   ingest or write-through hook exists. cortex.ask (live NL->SQL over mutating DB
   state) is in the default operations list with a 30s TTL, so an ask answer can
   be up to 30s stale with no way to purge it. Either add an invalidation hook or
   drop ask from the default operations list — argue which.

Also note cache.max_entries is read ONCE (cache.py:118, singleton built on first
use); changing it later does nothing until cache.reset().

THEN flip cache.enabled: true.
""",
        priority="high",
        acceptance="""
- A cache hit returns a value the caller cannot mutate into the cached entry —
  assert by mutating a hit and re-reading.
- Either an invalidation path exists, or cortex.ask is removed from the default
  cacheable operations, with the reasoning recorded.
- cache.enabled: true, and a hit still writes its cortex_audit row (the NIST-AU
  trail must stay complete).
- Test gated in args/ci_test_files/core.txt in this PR.
""",
    ),

    # ══════════════════════════════════════════════════════════════════
    # REACH — vendored parity and adoption
    # ══════════════════════════════════════════════════════════════════
    _t(
        "ctx-reach-01",
        "MANUAL — re-vendor client.py into compass and idea_lab",
        """
*** MANUAL-ONLY. The kanban runner CANNOT build this task. ***
compass and idea_lab are SEPARATE PRIVATE repos under the icdev-ai org
(C:/AI/standalone/{compass,idea_lab} on the current workstation). The runner
cannot check them out. Do not dispatch; a human drives this one.

Both vendored copies are 2 public methods behind canonical: CortexClient.reason()
and .agent(), added ce78c1aeb (#1447, 2026-08-09). args/vendor_parity.yaml still
says last_synced: "2026-08-02".

The copies are governed, not ad-hoc: their header reads "VENDORED from icdev
tools/cortex/client.py (canonical source) ... Keep byte-identical apart from this
header ... Stdlib-only by design - never add idea_lab/icdev imports here."

Consumers that will benefit: compass is the heavy user (17 non-test modules
including tools/pm/msr/*, tools/pricing/*, tools/reporting/*); idea_lab is light
(tools/research/cortex_research.py).

DO THIS AFTER ctx-enf-01, so the manifest exists and the re-vendor is verifiable
rather than a promise.

DIRECTION MATTERS: icdev -> standalone is the sanctioned vendoring direction.
NEVER copy code from the proprietary repos back into the public ICDEV repo.

Steps: re-copy tools/cortex/client.py into each consumer keeping ONLY its
provenance header; separate branch and PR in EACH private repo; then update
last_synced in args/vendor_parity.yaml via a normal ICDEV PR.
""",
        priority="high",
        task_type="chore",
        acceptance="""
- `python tools/workflow/coherence_checker.py --check vendor_parity` reports both
  consumers in sync (run it on a machine that HAS the standalone tree — this is
  the configuration where the check actually works).
- Each consumer keeps ONLY the provenance header as its diff from canonical.
- args/vendor_parity.yaml last_synced updated.
- No proprietary code moved into ICDEV.
""",
    ),
    _t(
        "ctx-reach-02",
        "Decide CortexClient's in-repo fate — 535 lines with zero in-repo consumers",
        """
tools/cortex/client.py is 535 lines and has ZERO production callers inside
ICDEV. The only references are tests (tests/cortex/test_client.py,
test_rest_*.py) and kanban SEED TASK DESCRIPTIONS (seed_bom_concord.py:538,
seed_hgx_kanban.py:1046). It is a client library with no in-repo client.

That is defensible — it exists to be vendored by out-of-repo consumers, and it
is deliberately stdlib-only for exactly that reason. But it is currently
indistinguishable from dead code, which is this codebase's signature defect, and
coherence_checker.py:check_capability_liveness exists to catch precisely this
shape.

DECIDE AND RECORD, do not just leave it:
  (a) give it a real in-repo consumer (e.g. the dashboard calling Cortex over
      REST rather than in-process), or
  (b) declare it explicitly external-only surface, documented as such, so it
      reads as intentional rather than abandoned.

Either is acceptable. An undocumented third state is not.
""",
        priority="medium",
        task_type="research",
        acceptance="""
- A written decision with reasoning, in docs/ and referenced from the module
  docstring.
- If (a): a real consumer exists and is exercised by a gated test.
- If (b): the external-only status is documented where a reader of client.py
  will see it, and any liveness gate is taught that this is intentional rather
  than having its budget raised.
""",
    ),
    _t(
        "ctx-reach-03",
        "Decide govern() and agent() — zero consumers, and agent(mode=) is unvalidated",
        """
Both facades have ZERO production Python consumers.

govern() (api.py:820) is reachable only via MCP cortex_govern and REST
/api/v1/govern. Its docstring says it exists "for external / non-Cortex
callers ... lets other tools adopt the TRUST chain incrementally" — and no
internal tool ever has.

agent() (api.py:846) is reachable only via MCP cortex_agent_launch, REST
/api/v1/agent, and the canvas confirm-then-launch path (blueprint.py:298). Its
scope cortex:agent is deliberately NEVER granted by default
(service_keys.py:103), which is correct and should stay.

KNOWN LATENT BUG: args/projects.yaml:7732 records "cortex.agent mode graph. Note
the latent bug - mode is unvalidated." validators.py:257 declares
AGENT_MODES = ("auto","team","single","graph"); confirm whether the facade
enforces it and fix if not. Fix that regardless of the adoption decision.

DECIDE: adopt each in at least one real path, or mark it explicitly
external-only surface. Record the reasoning.

Related dead surface to sweep while here:
  - cortex_server.py::main()/build_server() — a standalone `icdev-cortex` stdio
    server that .mcp.json never launches (it configures only icdev-unified). The
    TOOLS are reachable via unified_server.py:148, so this is dead weight, not a
    functional gap.
  - the `compliance` domain lens is declared (constants.py:57) with no
    search.domains.compliance profile.
  - `sources:` row-scoping is inert for document/proposal/network (all empty);
    only `security` is populated.
  - gap_handlers.py:1773 _REASON_IGNORED_PARAMS — 4 MCP schema params accepted
    and silently ignored.
""",
        priority="medium",
        acceptance="""
- agent(mode=...) rejects a mode outside AGENT_MODES; assert the reject case.
- A written decision for govern() and agent(): adopted (with the consumer named
  and tested) or explicitly external-only (documented).
- Each item in the "related dead surface" list is either wired, removed, or
  documented as intentional — none left in the undocumented third state.
- Test gated in args/ci_test_files/core.txt in this PR.
""",
    ),
    _t(
        "ctx-reach-04",
        "Document the child-app access pattern — Cortex is reached over REST, never inherited",
        """
Verified: child_app_generator.py's DIRECTORY_TREE is an ALLOWLIST, and the string
`tools/cortex` appears in it ZERO times. It is in no CONDITIONAL_DIRS bucket and
no TRUST_REFRESH_DIRS entry. NO generated child app inherits Cortex, and because
the tree is an allowlist it does not even need to be in PARENT_ONLY_DIRS to be
excluded.

That is the correct architecture — Cortex is a parent-hosted governed service
reached over REST with an icdev_ctx_ service key, not a library to copy. But it
is nowhere stated, which is why the question keeps coming back.

Evidence it is not understood today: ZERO in-repo apps/ consume Cortex.
apps/forge_academy references it as CURRICULUM CONTENT
(m-cortex-01-unified-ai-layer) and apps/ai_gameday/constants.py:91-95 lists
cortex_ask/extract/classify/govern as CATALOG METADATA. Neither calls it. The
only real consumers are out-of-repo (compass, idea_lab) over the network.

FIX: document the pattern — service key issuance (service_keys.py), the
/cortex/api/v1 surface, the degradation contract (parsed JSON on success, None
when unreachable, NEVER raises), and the reason the client is stdlib-only. State
plainly that Cortex is NOT copied into descendants.

Then decide whether any in-repo app SHOULD consume it, rather than leaving the
answer implicit.
""",
        priority="medium",
        task_type="chore",
        acceptance="""
- A doc under docs/features/ stating the access pattern, with the reason the
  client is stdlib-only and the degradation contract spelled out.
- Referenced from tools/cortex/client.py and from the child-app generator docs so
  the next reader finds it.
- Never document a command whose file does not exist — verify every path.
""",
    ),
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--json", action="store_true", help="machine-readable report")
    ap.add_argument("--dry-run", action="store_true", help="print, insert nothing")
    args = ap.parse_args()

    ids = [t["id"] for t in TASKS]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        print(f"ERROR: duplicate task ids: {dupes}", file=sys.stderr)
        return 1

    ungated = [t["id"] for t in TASKS
               if t["id"] != GATE and t.get("depends_on_task_id") != GATE]
    if ungated:
        print(f"ERROR: tasks not gated on {GATE}: {ungated}", file=sys.stderr)
        return 1

    if args.dry_run:
        report = {"would_create": ids, "count": len(ids), "gate": GATE}
        print(json.dumps(report, indent=2) if args.json
              else "\n".join(f"  {i}" for i in ids))
        return 0

    from tools.kanban.task_factory import create_tasks

    created = create_tasks(TASKS)
    report = {
        "created": created,
        "created_count": len(created),
        "skipped_existing": [i for i in ids if i not in created],
        "gate": GATE,
        "context": _CONTEXT.strip(),
    }
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"Created {len(created)} of {len(ids)} CTX tasks (gate: {GATE})")
        for i in created:
            print(f"  + {i}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
