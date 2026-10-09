# Nine external repos reviewed; eight gaps were OUR OWN unconsumed capabilities (xrv)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

```bash
python -m tools.cost.session_cost --survey --by-verdict --json   # spend that SHIPPED: shipped|reverted|abandoned|in_flight|unmeasurable
python -m tools.cost.waste_survey --since-days 30 --project ICDev --json   # one-shot rate, ghost definitions, CLAUDE.md tokens/day
python -m tools.cache_savings.spend --json                       # the /cache-savings "Spend by Card" panel payload
python -m tools.kanban.should_run --survey --json                # ONE pre-dispatch verdict incl. the BUDGET rung
python -m tools.hooks.session_context --json                     # the SessionStart block (injected automatically)
python -m tools.hooks.observation_capture --survey --since-days 7 --json
python tools/memory/hybrid_search.py --query "q" --layer index --json    # index -> timeline -> detail, approx_tokens on each
python -m tools.security.agent_config_shield --json              # .claude/ .agents/ .cursor/ .mcp.json + the 10 companion files
python -m tools.dx.tool_index --refresh --json                   # the ~36 external binaries; which() REFUSES an undeclared name
python -m tools.routing.corpus_survey --json                     # 195 cases over the FOUR routers; adds no fifth
python tools/ci/pin_census.py --check                            # a CI reference that does not name its bytes (69 sites, shrink-only)
python -m tools.analyzers.binary_triage <path> --json            # pure-Python; executes NOTHING (AST-asserted)
python -m tools.analyzers.ghidra_headless <path> --json           # OPTIONAL; `unavailable` on a default install IS the answer
python -m tools.airgap.artifact_freshness --survey --json         # current|behind|unmeasurable; air-gap is NEVER current
```

Feature doc: docs/features/phase-xrv-external-review.md   ADRs: D402-D408
Surveys: docs/audits/xrv-{cost-03,mem-02,route-03,run-01,shield-01}-*.md
THREE RULES THIS PHASE ADDS, each measured before it shipped:
 1. AN ABSENT PRICE IS NEVER $0.00, AND AN UNMEASURED OUTCOME IS NEVER A VERDICT.
    A claude_cli dispatch bypasses router.invoke, so its cost lives only in the
    transcript; the CLI's own total_cost_usd is recorded AS REPORTED and never
    re-priced (args/llm_config.yaml prices no Claude Code model). `unpriced` is
    counted APART from shipped/abandoned -- a dispatch that reported no dollars
    still shipped. record_task_cost once defaulted an absent price to 0.0 and
    understated the bill in the direction that makes work look cheap. The two MCP
    usage counts (transcripts 3/472, Studio audit 1/472 on this host) measure
    DIFFERENT CALLERS and are never merged; nothing under tools/mcp/ writes a
    dispatch row, so Claude Code MCP calls are invisible to
    capability_consumption --class mcp_dispatch_tool (xrv-cost-05).
 2. DO NOT RAISE A BUDGET TO QUIETEN THE RUNG IT REFUSES. should_run ships
    KANBAN_SHOULD_RUN=report and changes no dispatch outcome. Replayed over 7,072
    scheduler dispatches: 28.11% `wait` lifetime, 100.00% in 2026-09 -- and EVERY
    fire is the module TOKEN cap, not one is USD (spent_usd 0.0 every month; every
    call routes to a $0/1k provider). So the rule is right about the ledger and the
    ledger is wrong about the cost: arming `enforce` today parks the whole board
    until 2026-10-01. The repair is the token cap moved after routing, then
    re-survey. Survey: docs/audits/xrv-run-01-should-run-survey.md
 3. ASK UPSTREAM; NEVER ACT ON THE PIN. artifact_freshness never pulls and never
    writes a pin (subprocess/os/shutil unimportable, AST-pinned) -- it files ONE
    `suggested` card per `behind` artifact, because moving a digest pin is a
    supply-chain act that belongs in a reviewed diff. AGREEMENT IS NOT CURRENCY: a
    test that two pin files agree with each other passes forever for a version that
    shipped a year ago. An air-gapped host reports every artifact `unmeasurable`,
    NEVER `current`. Measured 2026-09-12: 16 declared, 10 current, 6 behind, 3 of
    those also digest-drifted. Same rail everywhere in this phase: None never [],
    `unavailable` is not an empty result, the version TOKEN is not the version
    (OpenSSL 1.1.1k -> `1.1` is a WRONG version), and binary corroboration is
    reported BESIDE the signature verdict and can never downgrade it.
