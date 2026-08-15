# ICDEV™ CLI Command Reference

Complete CLI command reference for all ICDEV™ modules. See [CLAUDE.md](../../CLAUDE.md) for behavioral instructions.

---

## Quick Reference

### Commands
```bash
# Initialize framework (first run)
/initialize                    # Custom slash command — sets up all dirs, manifests, memory, databases

# Memory system
python tools/memory/memory_read.py --format markdown          # Load all memory
python tools/memory/memory_write.py --content "text" --type event  # Write to daily log + DB
python tools/memory/memory_write.py --content "text" --type fact --importance 7  # Store a fact
python tools/memory/memory_write.py --update-memory --content "text" --section user_preferences  # Update MEMORY.md
python tools/memory/memory_db.py --action search --query "keyword"   # Keyword search
python tools/memory/semantic_search.py --query "concept"             # Semantic search (requires OpenAI key)
python tools/memory/hybrid_search.py --query "query"                 # Best: combined keyword + semantic
python tools/memory/embed_memory.py --all                            # Generate embeddings for all entries

# Agentic generation (Phase 19)
python tools/builder/agentic_fitness.py --spec "..." --json               # Assess fitness
python tools/builder/app_blueprint.py --fitness-scorecard sc.json \
  --user-decisions '{}' --app-name "my-app" --json                        # Generate blueprint
python tools/builder/child_app_generator.py --blueprint bp.json \
  --project-path /tmp --name "my-app" --json                              # Generate child app
python tools/builder/scaffolder.py --project-path /tmp --name "my-app" \
  --type api --agentic --fitness-scorecard sc.json                        # Scaffold + agentic

# LLM Provider (vendor-agnostic model routing)
python -c "from tools.llm.router import LLMRouter; r = LLMRouter(); print(r.get_provider_for_function('code_generation'))"  # Check routing
# Config: args/llm_config.yaml — providers, models, routing, embeddings
# Set OLLAMA_BASE_URL=http://localhost:11434/v1 for local model support
# Set prefer_local: true in llm_config.yaml for air-gapped environments

# Semantic loop detection for the agent loop (ars-loop-01)
# Library: tools/llm/loop_detector.py — detect_semantic_loop(records, config=) -> LoopDetection
#   Config: args/llm_config.yaml -> agent_loop.loop_detection (enabled, window, similarity_threshold,
#           min_cluster_size, min_distinct_turns, min_distinct_variants, coverage_ratio)
#   Wired: run_agent_loop control 6 -> ResultSubtype.error_semantic_loop, truncation_reason="semantic_loop"
python tools/llm/loop_detector_tune.py --transcripts <dir>                            # replay real transcripts
python tools/llm/loop_detector_tune.py --transcripts <dir> --threshold 0.75 --json    # tune / inspect flags
python tools/llm/loop_detector_tune.py --transcripts <dir> --max-flag-rate 0.0        # regression gate

# Reasoned Codegen (CoT/CoD + adversary critique + verify/repair)
# Library: tools/llm/reasoned_codegen.py — generate_reasoned_code(function=, request=, verifier=, mode=)
#   Config: args/llm_config.yaml -> reasoned_codegen (section kill-switch + per_function mode/critique)
#   Wired: translation (default ON); ANVIL agentic_runner --reasoned auto|on|off; chat per-session reasoning_mode
python tools/llm/reasoned_codegen_advisor.py --function code_generation --spec "..." --file-count 5 --json  # advise enable/mode
python tools/anvil/agentic_runner.py --task-id X --task-desc "..." --reasoned auto   # auto|on|off
# Dashboard panel: /ops/llm (config view + advisor runner + recent chain runs)
```

---

## CloudForge Runbooks & Metastore Commands
```bash
# Runbook Engine
python tools/cloudforge/runbooks/engine.py --list --json                    # List runbooks
python tools/cloudforge/runbooks/engine.py --get <id> --json                # Get runbook detail
python tools/cloudforge/runbooks/template_loader.py --list --json           # List YAML templates
python tools/cloudforge/runbooks/template_loader.py --load dr_failover --json  # Load template

# Application Metastore
python tools/cloudforge/metastore/registry.py --list --json                 # List applications
python tools/cloudforge/metastore/registry.py --get <id> --json             # Get app detail

# Ops MCP Server (18 tools: runbooks, metastore, cross-domain)
python tools/mcp/ops_server.py                                              # Start stdio MCP server
```

---

## Testing Commands
```bash
python tools/testing/health_check.py                 # Full system health check
python tools/testing/health_check.py --json           # JSON output
python tools/testing/test_orchestrator.py --project-dir /path/to/project
python tools/testing/e2e_runner.py --discover         # List available E2E test specs
python tools/testing/e2e_runner.py --run-all           # Execute all E2E tests
```

---

## Enterprise-Configurable Platform Commands
```bash
# Project scaffolding — ALWAYS run `icdev init` after `pip install icdev`
icdev init [target]               # Scaffold a project (CLAUDE.md + FORGE data + .claude/ + .env)
icdev init my-project --profile air-gap   # Non-interactive: apply a core profile
icdev init my-project --profile none      # Registry defaults, skip the profile prompt
icdev init --list                 # Dry-run: show what would be copied

# Interactive feature-toggle TUI (stdlib-only; primary browser-free on/off surface)
icdev setup                       # Browse every component + sub-pages, toggle, write .env
icdev setup --plain               # Force the plain numbered-menu mode (non-TTY)
icdev setup --json                # Dump current component state as JSON

# Component registry — single source of truth for canvases, child apps, features, core extensions
python -c "from tools.config.component_registry import get_registry; r=get_registry(); print([c.key for c in r.iter_canvases()])"
python -c "from tools.config.component_registry import get_registry; r=get_registry(); print(r.get_nav_context())"

# Canvas / feature toggles
icdev enable <name> [...]         # Turn on canvas / subsystem toggles in .env
icdev disable <name> [...]        # Turn off toggles
icdev status                      # Show active toggles
icdev status --json               # Machine-readable status
icdev list                        # List supported toggles

# Audit feed — read audit_trail + hook_events from the terminal.
# NOTE: `icdev status` above reports TOGGLES, not health. For health use
# `python tools/testing/health_check.py --json`.
icdev audit tail                          # Last 50 events (oldest-first on screen)
icdev audit tail --limit 200
icdev audit tail --follow                 # Poll for new events; Ctrl-C exits 0
icdev audit tail --json                   # One JSON object per line (jq-able)
icdev audit tail --list-types             # Event types this deployment emits, with counts
icdev audit tail --project <id> --event-type <type> --since <iso8601>
icdev audit tail --source hook_events     # Restrict source (repeatable)
icdev audit export --framework soc2 --tenant-id <tid> --output report.html

# Runtime invocations through the same reader — rows, not the `runtime top` rollup.
# Requested on its own: it is telemetry, not audit evidence, and merging hundreds of
# MCP calls per session into the feed would bury the audit rows you asked for.
# Columns map onto the feed: invocation name -> event type, surface -> actor.
icdev audit tail --source runtime_invocations --limit 5
icdev audit tail --source runtime_invocations --event-type rag_search   # one name
icdev audit tail --source runtime_invocations --actor mcp               # one surface
icdev audit tail --source runtime_invocations --follow --json

# Runtime invocation telemetry — what actually ran (runtime_invocations, migration 341).
# `audit tail` answers "what happened"; `runtime top` answers "what is slow / failing".
# --limit bounds the number of NAMES shown, not the invocations scanned.
icdev runtime top                         # Top 20 names by call count, all surfaces
icdev runtime top --limit 50
icdev runtime top --surface mcp           # One surface: mcp | agent | persona | role
icdev runtime top --surface agent         # SAG tool calls, recorded from dispatch.py
icdev runtime top --json                  # Machine-readable rollup

# One RUN rather than all runs. The correlation id is AgentLoopResult.trace_id;
# both the agent.turn spans and the gen_ai.invoke spans beneath them carry it.
icdev runtime trace <correlation-id>      # Every span of one agent run, oldest first
icdev runtime trace <correlation-id> --json

# Core enterprise profiles
icdev profile list                 # List available profiles
icdev profile show [<name>]        # Show profile details (active profile by default)
icdev profile apply <name>         # Append profile env overrides to .env
icdev profile apply <name> --dry-run  # Preview overrides

# Scaffolding
icdev scaffold canvas <key> --display-name "Name" [--flavor <flavor>] [--template <dir>] [--out <dir>]   # Generate a new canvas
icdev scaffold child-app <key> --display-name "Name" --flavor <flavor> [--canvases k1,k2] [--out <dir>]  # Generate a child app (minimal, compliance, ai-lab, govcon)

# Document-currency packs (docmod) — let any domain own its own coverage.
# Writes IN PLACE (packs are discovered by location under args/docmod/) and never
# overwrites an existing file.
icdev scaffold docmod-pack <key> --display-name "Name"                        # rulebook flavor: YAML only, NO Python
icdev scaffold docmod-pack <key> --display-name "Name" --entity-type standard --finding-type deprecated_tech
icdev scaffold docmod-pack <key> --display-name "Name" --flavor catalog --evidence-table <table>  # table-driven; generates a Python stub
icdev scaffold docmod-pack <key> --display-name "Name" --dry-run --json       # preview
# Then: write rules in args/docmod/rulebook_<key>.yaml, set enabled: true in
# args/docmod/packs/<key>.yaml — the next docmod sweep auto-discovers it.

# Validation
python tools/workflow/coherence_checker.py --all --gate      # Registry/profile/completeness coherence gate
python tools/builder/forge_validator.py --gate               # FORGE gate for child apps
```

---

## Compliance Commands
```bash
python tools/compliance/ssp_generator.py --project-id "sparkpilot"
python tools/compliance/poam_generator.py --project-id "sparkpilot"
python tools/compliance/stig_checker.py --project-id "sparkpilot"
python tools/compliance/sbom_generator.py --project-id "sparkpilot"                                # CycloneDX (default spec 1.7)
python tools/compliance/sbom_generator.py --project-id "sparkpilot" --format spdx                  # SPDX 2.3 — the other format the 2026 standard names
python tools/compliance/sbom_generator.py --project-id "sparkpilot" --spec-version 1.6             # 1.4-1.7 selectable for lagging consumers
python tools/compliance/sbom_generator.py --project-id "sparkpilot" --python-env /path/to/.venv   # resolve Python from the installed environment
python tools/compliance/sbom_generator.py --project-id "sparkpilot" --author "Defense Information Systems Agency"  # SBOM Author: the entity, not the tool ($ICDEV_SBOM_AUTHOR)
python tools/compliance/spdx_writer.py --convert "/path/to/sbom.cdx.json" --output "/path/to/sbom.spdx.json"
python tools/compliance/spdx_writer.py --validate "/path/to/sbom.spdx.json" --json                # against the official SPDX 2.3 schema, offline
python tools/compliance/spdx_writer.py --compare "/path/to/sbom.cdx.json" "/path/to/sbom.spdx.json" --json  # do both formats carry the same elements?
python tools/compliance/dependency_resolver.py --project-dir "/path/to/project" --json            # resolved transitive set + coverage report
python tools/compliance/component_producer.py --purl "pkg:golang/k8s.io/client-go@v0.29.0" --json  # Component Producer for one component
python tools/compliance/component_producer.py --name flask --version 3.0.0 --ecosystem python --project-dir "/path/to/project" --json
python tools/compliance/component_producer.py --validate "/path/to/sbom.cdx.json" --json          # every component states a producer or unknown provenance
python tools/compliance/component_producer.py --registry --json                                    # the namespace -> organization registry in force
python tools/compliance/component_hasher.py --registry --json                                      # the IANA Hash Function Textual Names, and which are emittable
python tools/compliance/component_hasher.py --validate "/path/to/sbom.cdx.json" --json             # every component states a digest or an explicit unknown, with an IANA-registered algorithm
python tools/compliance/component_hasher.py --file "/path/to/artifact.jar" --json                  # sha-256 of one artifact, the way recomputation does it
python tools/compliance/sbom_conformance_gate.py --sbom "/path/to/sbom.cdx.json" --json            # gate on the 2026 minimum elements, not on presence
python tools/compliance/sbom_conformance_gate.py --sbom "/path/to/sbom.cdx.json" --gate swft       # deployment_gates | swft | devsecops; exit 1 when it blocks

# SBOM Frequency + Accommodation of Updates (2026 Minimum Elements). A correction is a
# successor row; the SBOM it corrects is never rewritten.
python tools/compliance/sbom_generator.py --project-id "sparkpilot" --build-id "$CI_PIPELINE_ID"   # record which build this SBOM describes
python tools/compliance/sbom_revision.py --project-id "sparkpilot" --chain --json                 # the revision chain, each row marked superseded/head
python tools/compliance/sbom_revision.py --project-id "sparkpilot" --frequency --json             # per-build first, 30-day age as the backstop
python tools/compliance/sbom_revision.py --project-id "sparkpilot" --correct --sbom "/path/to/corrected.cdx.json" --reason "producer was wrong" --json
python tools/compliance/sbom_revision.py --project-id "sparkpilot" --correct --sbom "/path/to/fixed.cdx.json" --reason "upstream published the hash" --reason-code detail_discovered --json

# SBOM Author Signature (2026 Minimum Elements). Offline on both paths — no sigstore/Fulcio.
python tools/crypto/key_manager.py --generate-keys --key-type ecdsa-p256 --json                   # one-time: create the signing key
export ICDEV_SBOM_SIGNING_KEY_PATH=data/keys/icdev_audit_ecdsa-p256.pem                           # generator signs every SBOM from here on
python tools/compliance/sbom_signer.py --list-algorithms                                          # approved algorithms + the authority for each
python tools/compliance/sbom_signer.py --sign "compliance/sbom.cdx.json" --json                   # writes detached compliance/sbom.cdx.json.sig.json
python tools/compliance/sbom_signer.py --verify "compliance/sbom.cdx.json" --json                 # integrity; exit 1 if tampered or unsigned
python tools/compliance/sbom_signer.py --verify "compliance/sbom.cdx.json" --expect-fp "<fp>"     # + authorship, fingerprint pinned out of band
python tools/compliance/unknown_information.py --validate "/path/to/sbom.cdx.json" --json         # unknown vs withheld conformance; withheld is never counted as unknown
python tools/compliance/unknown_information.py --policy --json                                     # the disclosure policy: enquiry route + declared withholdings (exit 1 on dropped rules)
python tools/compliance/unknown_information.py --vocabulary --json                                 # the 17 fields and the two disjoint reason vocabularies

# SBOM 2026 Minimum Elements conformance — grades ICDEV output AND vendor-supplied SBOMs
python tools/compliance/sbom_minimum_elements_validator.py --sbom compliance/sbom.cdx.json
python tools/compliance/sbom_minimum_elements_validator.py --sbom vendor.spdx.json --json         # CycloneDX 1.x or SPDX 2.2/2.3
python tools/compliance/sbom_minimum_elements_validator.py --sbom sbom.cdx.json --min-score 80    # exit 1 below threshold, 2 if unreadable
python tools/compliance/sbom_minimum_elements_validator.py --sbom sbom.cdx.json --require-conformant
python tools/compliance/sbom_minimum_elements_validator.py --sbom sbom.cdx.json --record --project-id "sparkpilot"
python tools/compliance/sbom_identifiers.py --validate "/path/to/sbom.cdx.json" --json           # Component Identifiers conformance; exit 1 on a component with none, or a malformed one
python tools/compliance/sbom_identifiers.py --component "pkg:pypi/flask@3.0.0" --json             # every identifier derivable for one component, CPE included
python tools/compliance/component_names.py --validate "/path/to/sbom.cdx.json" --json            # Component Name conformance; exit 1 on an alternate that repeats the primary or carries an unknown kind
python tools/compliance/component_names.py --name core --group "@babel" --purl "pkg:npm/%40babel%2Fcore@7.23.9" --json   # every name one component is known by
python tools/compliance/dependency_graph.py --validate "/path/to/sbom.cdx.json" --json           # Component Dependency Relationship; exit 1 on a flat list, a dangling dependsOn, an unrooted graph or a declared cycle count that disagrees

# SBOM Distribution and Delivery (2026 Minimum Elements) — version-specific retrieval.
# Served over HTTP at $ICDEV_BASE_URL/api/supply_chain/sbom/<project_id>/<version>,
# with /record/<id> as a permalink and /versions/<project_id> as the index. RBAC and
# classification are enforced on every one of them.
python tools/compliance/sbom_distribution.py --list --json                                         # every SBOM record + its retrieval URL
python tools/compliance/sbom_distribution.py --list --project-id "sparkpilot"                      # one project's versions
python tools/compliance/sbom_distribution.py --project-id "sparkpilot" --version 2.0 --json        # resolve one version: sha256, markings, conformance
python tools/compliance/sbom_distribution.py --record-id 7 --out "./sbom.cdx.json"                 # the artifact's exact bytes, unmodified
python tools/compliance/cui_marker.py --file "/path/to/file" --marking "CUI // SP-CTI"
python tools/compliance/nist_lookup.py --control "AC-2"
python tools/compliance/control_mapper.py --activity "code.commit" --project-id "sparkpilot"
python tools/compliance/crosswalk_engine.py --control AC-2
python tools/compliance/crosswalk_engine.py --project-id "sparkpilot" --coverage
python tools/compliance/fedramp_assessor.py --project-id "sparkpilot" --baseline moderate
python tools/compliance/cmmc_assessor.py --project-id "sparkpilot" --level 2
python tools/compliance/oscal_generator.py --project-id "sparkpilot" --artifact ssp
python tools/compliance/classification_manager.py --impact-level IL4

# Secure by Design (CISA SbD + Cloudyrion 8-Pillar)
python tools/compliance/sbd_assessor.py --project-id "sparkpilot" --project-dir . --gate --json
python tools/compliance/sbd_assessor.py --project-id "sparkpilot" --list-exceptions --json
python tools/compliance/sbd_assessor.py --project-id "sparkpilot" --register-exception --requirement-id "SBD-04" --title "Title" --owner "owner@org" --duration-days 90 --json
python tools/compliance/sbd_assessor.py --project-id "sparkpilot" --renew-exception --requirement-id "SBD-04" --json
python tools/compliance/sbd_report_generator.py --project-id "sparkpilot" --json
python tools/compliance/crosswalk_engine.py --framework cisa_sbd --project-id "sparkpilot" --coverage
```

---

## Module Encryption Commands
```bash
# Encrypt a module directory (used by marketplace SaaS at publish time)
python tools/marketplace/module_crypto.py encrypt --module-dir tools/writing --seed "<hex>" --slug writeguard
python tools/marketplace/module_crypto.py encrypt --module-dir tools/writing --seed "<hex>" --slug writeguard --keep-originals

# Verify a .py.enc file can be decrypted
python tools/marketplace/module_crypto.py verify --file tools/writing/analysis_engine.py.enc --seed "<hex>" --slug writeguard
```

---

## Storage Layer Commands
```bash
python tools/db/storage.py --health --json        # Check backend connectivity
python tools/db/storage.py --info --json           # Show backend configuration
# Connection API (in Python):
# from tools.db.storage import get_connection
# with get_connection() as conn:
#     rows = conn.execute("SELECT * FROM projects WHERE id = ?", (pid,)).fetchall()
```

---

## Untrusted HTML Extraction (oss-filter-01)
```bash
# Two-pass fit_markdown filter: prune site chrome, then BM25-rank against a query.
python tools/http/page_extract.py --file page.html                      # prune only
python tools/http/page_extract.py --file page.html --query "rate limits"  # + relevance
python tools/http/page_extract.py --file page.html --query "rate limits" --json
cat page.html | python tools/http/page_extract.py --query "rate limits"

# Thresholds (prune threshold/type, min_word_threshold, BM25 threshold, stopwords,
# stemming, section propagation, markdown rendering): args/page_extract.yaml
```

## Untrusted URL Fetch → Extract → Scan (oss-filter-02)
```bash
# One hardened path: central HTTP client (mTLS/proxy/retry) → two-pass page_extract
# → prompt-injection scan. Use this instead of adding a urllib/requests call site.
python tools/http/fetch_extract.py --url https://example.gov/spec
python tools/http/fetch_extract.py --url https://example.gov/spec --query "key rotation"
python tools/http/fetch_extract.py --url https://example.gov/spec --json

# In Python — never raises; a hostile or dead URL comes back as data:
# from tools.http.fetch_extract import fetch_page
# page = fetch_page(url, query="key rotation")
# page.text      # fit_markdown, already injection-scanned
# page.blocked   # True when a critical finding dropped the content

# Read cap, User-Agent, block_on_critical_injection: `fetch:` in args/http_client.yaml
```

## Security Commands
```bash
python tools/security/sast_runner.py --project-dir "/path"
python tools/security/dependency_auditor.py --project-dir "/path"
python tools/security/secret_detector.py --project-dir "/path"
python tools/security/container_scanner.py --image "sparkpilot:latest"

# ATO boundary tier tagging of scan findings (GREEN/YELLOW/ORANGE/RED)
python tools/security/boundary_tagger.py --report .tmp/security-reports/scan.json --json
python tools/security/boundary_tagger.py --report scan.json --project-id <id> --system-id <sys-id> --create-assessments --json
python tools/security/boundary_tagger.py --report scan.json --gate --json   # exit 1 on any RED finding

# Security Framework (Phase 74 — sec-fnd)
python tools/security/security_context.py --whoami --json
python tools/security/abac_engine.py --review --json
python tools/security/row_security.py --test --table <name> --json
python tools/security/classification_enforcer.py --check --json
python tools/security/column_security.py --mask --table <name> --json
python tools/security/field_security.py --filter --schema <name> --json
python tools/security/encryption_at_rest.py --rotate --classification TS --json
python tools/security/mtls_integration.py --verify --json
python tools/security/security_middleware.py --init-app --json
python tools/security/audit_posture.py --json

# Network-egress fire rate (exa-bench-08) — measure before enforcing
# shared_checks.check_network_egress ships MONITOR-ONLY. Measure, then flip
# agent_egress.enforce in args/agent_egress_policy.yaml. Baseline: 0.093% of
# 78,903 real Bash calls (docs/security/agent-vendor-permission-bypass.md §4a).
python tools/security/egress_fire_rate.py --json                        # what the hook has recorded
python tools/security/egress_fire_rate.py --corpus --json               # replay ~/.claude/projects transcripts
python tools/security/egress_fire_rate.py --corpus <dir> --top 30       # replay a specific corpus

# SIPA Software Integrity PR gate (eqo-sipa) — assess only the *.py files changed on a branch
python tools/integrity/pr_gates.py --base origin/main --json            # preview verdict over branch diff
python tools/integrity/pr_gates.py --cached --json                      # assess the staged index (pre-commit)
python tools/integrity/pr_gates.py --base origin/main --gate            # CI gate: exit 1 on a blocking (QUARANTINE) verdict
```

---

## Analyzer / Responder Contract (anz-con-01)

The contract is DATA — `args/analyzer_contract.yaml`. A new analyzer is declared
entirely there (accepted observable types, output taxonomy, rate limit, sandbox
posture); no base class, no dispatch table, no blueprint edit. An unknown
observable type is rejected when the file is LOADED, naming the offending
analyzer and the legal values — not swallowed at dispatch time the way an
unknown `citation_type` was.

```bash
python tools/analyzers/contract.py --validate            # load + validate; exit 1 on any defect
python tools/analyzers/contract.py --list                # declared analyzers and responders
python tools/analyzers/contract.py --json                # whole contract, machine-readable
python tools/analyzers/contract.py --observable cve      # who accepts this observable type
python tools/analyzers/contract.py --check-sql observable_type   # CHECK clause for a migration
```

### Observable dispatch (anz-disp-01)

One entry point. Submitting an observable fans it out to **every** analyzer that
declared it accepts that type — concurrently, with a per-analyzer timeout — and
returns taxonomy-tagged reports. An analyzer that timed out is reported as
`timeout`, never omitted: a fan-out that dropped it would read identically to
one where it found nothing. Exit code is 2 when the result is partial.

```bash
python tools/analyzers/dispatch.py --observables                          # vocabulary + who accepts what
python tools/analyzers/dispatch.py --type ip --value 198.51.100.7         # fan out to every ip analyzer
python tools/analyzers/dispatch.py --type cve --value CVE-2024-3094 \
    --context '{"project_id":"p1","component":"xz","cvss_score":10.0,"severity":"critical","description":"backdoor"}'
python tools/analyzers/dispatch.py --type vendor --value Acme --json      # machine-readable reports
python tools/analyzers/dispatch.py --type ip --value 1.2.3.4 --analyzer threat_intel_match
python tools/analyzers/dispatch.py --type ip --value 1.2.3.4 --responders # responders ACT — opt-in
```

### Rate limits and sandbox posture (anz-rate-01)

Each declaration's `rate_limit` and `sandbox` posture are **enforced** on every
dispatch, not merely surfaced. Exceeding a limit queues or reports — it never
drops: without `--rate-limit-wait` an out-of-quota analyzer yields a
`rate_limited` report carrying `retry_after_seconds` (and sets `partial`), and
with it the call queues for a slot, bounded both by the flag and by what is
left of that analyzer's own timeout budget.

Sandboxed analyzers run through the platform `SandboxExecutor` behind the
`sandbox_execute` MCP tool — there is no second isolation path. A posture that
requires the sandbox on a host without one is reported `sandbox_unavailable`
and is **not** run in-process. Per-analyzer decisions:
[docs/security/sandbox-coverage.md](../security/sandbox-coverage.md) Gap 48.

```bash
python tools/analyzers/dispatch.py --type cve --value CVE-2024-3094 --rate-limit-wait 5   # queue, don't report
python tools/analyzers/dispatch.py --type file_path --value ./repo --strict-sandbox       # promote on-demand postures
ICDEV_STRICT_SANDBOX=1 python tools/analyzers/dispatch.py --type ip --value 1.2.3.4       # same, host-wide (IL5 / air-gap)
python tools/analyzers/contract.py --json    # declared rate_limit + sandbox posture per analyzer
```

`analyzer_capabilities` additionally reports each analyzer's live rate-limit
window (`rate_limit_state`) and the `execution_mode` its posture resolves to on
this host. Reading it consumes no quota.

> Sandboxed analyzers import the declared module *inside* the container, so
> `sandbox.images.python` in `args/sandbox_config.yaml` must point at an image
> carrying ICDEV. The stock `python:3.12-slim` does not, and the analyzer will
> report `error` naming `ModuleNotFoundError` rather than silently degrading.

MCP (existing gateway, category `analyzers`, no new server):
`analyzer_dispatch` (params: `observable_type`, `value`, `context`,
`analyzers`, `include_responders`, `timeout_seconds`) and
`analyzer_capabilities` (param: `observable_type`).

---

## Browser Automation & Agent Scope Controls
```bash
# Driver resolution (vendored msedgedriver / chromedriver — no runtime downloads)
python tools/browser/driver_manager.py --probe            # Resolved browser + driver path
python tools/browser/driver_manager.py --smoke            # Launch, visit about:blank, quit

# Agent browser scope controls (oss-browse-02) — config: args/browser_scope.yaml
python tools/browser/scope.py --show --json               # Print the active policy
python tools/browser/scope.py --check-url http://localhost:5050/ --json   # exit 0 = allowed
python tools/browser/scope.py --check-url https://example.com/ --json     # exit 1 = denied
# Override the config path with ICDEV_BROWSER_SCOPE_CONFIG.
```

Any **agent-driven** browser session must go through `GuardedDriver`, never a raw
WebDriver. It enforces the domain allowlist (loopback only by default; a routable
host needs to be allowlisted **and** `allow_non_local: true` **and** cleared by
`egress_guard`), the per-run action cap, the per-step timeout, `<secret>name</secret>`
placeholder substitution at the driver, and an `audit_trail` row per action.

```python
from tools.browser import get_driver, GuardedDriver

driver = get_driver(headless=True)
try:
    session = GuardedDriver(driver, run_id="vv-001")
    session.navigate("http://localhost:5050/")        # allowed
    session.type_text(field, "<secret>dashboard_password</secret>")
    session.navigate("https://example.com/")          # raises NavigationDenied
finally:
    driver.quit()
```

---

## SAG Runtime Configuration (hgx-cfg-01)

`args/agent_runtime.yaml` collects the standalone agent runtime's settings in one
documented file. It is a **layer beneath** the existing environment variables,
not a replacement: resolution is

```
explicit argument  >  environment variable  >  args/agent_runtime.yaml  >  built-in default
```

so every env var that worked before still works and still wins. The file is
optional — deleting it changes nothing about how the agent runs.

```bash
# Resolved configuration, plus the env vars currently overriding the file
python -m tools.agent_runtime.config

# Machine-readable (config_path, config_found, env_overrides, resolved)
python -m tools.agent_runtime.config --json

# Resolve against a site-local file instead of args/agent_runtime.yaml
python -m tools.agent_runtime.config --config /etc/icdev/agent_runtime.yaml --json
```

Point the loader at a different file for a whole process with
`ICDEV_AGENT_RUNTIME_CONFIG=/path/to/file.yaml`.

The runtime is registered as the `sag` component, so the toggle is reachable from
the normal component surfaces:

```bash
icdev list                 # sag — Standalone Agent Runtime  (flags: ICDEV_SAG_ENABLED)
icdev status --json        # current on/off state read from .env
icdev disable sag          # `icdev chat` then refuses to start
icdev enable sag
```

`enabled:` in the YAML and `ICDEV_SAG_ENABLED` are the same switch, so the CLI
toggle and the config file cannot disagree. There is deliberately **no `model:`
key** — only `llm_function`, a routing function resolved through `LLMRouter`
against `args/llm_config.yaml`. Per-subsystem toggles (project context, standing
goals, profile memory, skill proposals, approval mode, mutation gate, delegation,
toolset bundles) and the env var that overrides each are documented inline in the
file and tabulated in `tools/manifest/standalone-agent-runtime.md`.

---

## SAG Project Context — Instruction Loading at Session Start (hgx-sess-01)

Loads `CLAUDE.md`, `AGENTS.md`, `memory/MEMORY.md` and the
`session_context_builder` project-state summary into the agent's system prompt,
budgeted against `context_budget.floor_window_for_function` (the minimum window
across the routed chain) rather than a constant.

```bash
# Preview the block the runtime will inject
python tools/agent_runtime/project_context.py

# Budget accounting only — which sections were truncated, and by how much
python tools/agent_runtime/project_context.py --json

# Budget against a different routing function; skip the DB-backed state summary
python tools/agent_runtime/project_context.py --function question_answering \
    --no-project-state --json
```

A large-window model receives the documents intact; a 32k local chain receives a
line-boundary-truncated block carrying an explicit
`[... N of M lines omitted to fit the context budget — read <path> ...]` marker,
so a partial rule set never reads as complete. Toggles:
`ICDEV_SAG_PROJECT_CONTEXT=0` disables the block entirely;
`ICDEV_SAG_PROJECT_STATE=0` keeps the instruction files but skips the project
state summary. The block is built once per session and rebuilt on `/new`.

---

## SAG Standing Goals — `/goal` and Prompt Injection (hgx-goal-02)

`/goal` manages durable objectives from inside a session; the **active** ones are
injected into the system prompt on the next turn, capped and budgeted.

```bash
# Preview the goal block the runtime will inject
python tools/agent_runtime/goal_context.py --user default

# Budget accounting only — shown vs withheld, tokens vs budget
python tools/agent_runtime/goal_context.py --json

# Budget against a different routing function, with an explicit count cap
python tools/agent_runtime/goal_context.py --function question_answering \
    --limit 3 --json
```

In-session commands (`icdev chat`, or any runtime wired to
`tools/agent_runtime/commands.py::dispatch`):

```text
/goal create <title> [| detail] [--priority=N]   Create and start pursuing it
/goal list [status|all]                          Numbered list (default: live)
/goal status [N|id]                              What is injected, or one goal
/goal pause|resume|complete|cancel <N|id>        Lifecycle moves
/goal block <N|id> [reason]                      Mark blocked, recording why
/goal clear [--yes]                              Cancel every live goal
```

Two caps apply and both are announced in the block itself: a count cap
(`ICDEV_SAG_GOAL_LIMIT`, default 5) and a token cap (5% of
`context_budget.available_input_tokens`). Under pressure the block shortens goal
text before it drops goals, so every objective stays at least named. Every
mutation invalidates the runtime's cached block, so a `/goal create` reaches the
model on the very next turn. `ICDEV_SAG_GOALS=0` disables injection entirely.

---

## Refinement Cycles — Snapshot & Rollback of Supplemental State (exa-refine-05)

A *refinement cycle* is a unit of self-modification of the supplemental harness
state — the prompt layers (`prompt_versions`), auto-generated skills
(`sag_skill_registry` + `.agents/skills/icdev-auto-*`) and learned goals
(`genesis_generated_goals` + `data/genesis/suggested_goals`) that ICDEV rewrites
about itself. Snapshot before, roll the whole thing back after.

```bash
# Snapshot the supplemental state and open a cycle
python tools/agent_runtime/refinement_cycle.py open --label "gepa pass"

# Only some stores
python tools/agent_runtime/refinement_cycle.py open --providers prompts,skills

# Cycles newest first, with derived status and refinement count
python tools/agent_runtime/refinement_cycle.py list --limit 10

# What a rollback WOULD do (drifted providers, file changes, added files)
python tools/agent_runtime/refinement_cycle.py show <cycle-id>

# Roll it back. Without --yes this is a preview, same as `show`.
python tools/agent_runtime/refinement_cycle.py rollback <cycle-id> --yes

# Re-verify every chained audit row the cycle wrote
python tools/agent_runtime/refinement_cycle.py verify <cycle-id>
```

The **file half** of a snapshot is `tools/agent_runtime/checkpoints.py` — the
same checkpoints `/snapshot` and `/rollback` drive — so there is one checkpoint
system, not two. The **row half** lives in the append-only
`supplemental_state_snapshots` / `supplemental_refinements` tables (migration
`20260812074403`).

A rollback opens an *undo* cycle first, so it is itself reversible: roll the
`undo_cycle_id` back to reinstate the refinement. That is also what makes
removing a file that appeared mid-cycle safe — it is deleted only once the undo
checkpoint is confirmed to hold recoverable bytes, and anything unrecoverable is
reported under `files_not_removed` rather than dropped.

Every snapshot, applied refinement and rollback writes a **chained** `audit_trail`
row (`event_type='supplemental_state'`), so `verify` recomputes each digest and
its link through `provenance_verifier.verify_audit_integrity`. A row whose audit
write failed reports `unaudited` — it never reads as verified.

Record a change inside a cycle from Python:

```python
from icdev.tools.agent_runtime.refinement_cycle import open_cycle, record_refinement

cycle = open_cycle("nightly gepa pass", actor="gepa_optimizer")
record_refinement(cycle["cycle_id"], "prompts", "activated", target="layer/codegen")
```

---

## Agent Approval Gate — Irreversible Action Confirmation (ars-appr-01)

Classifies an agent tool call by **reversibility** and halts the irreversible
ones for confirmation. Policy: `args/agent_approval_policy.yaml`.

```bash
# Classify a tool call (exit 0; read the JSON for the verdict)
python tools/agent_runtime/approval_gate.py --classify git_push --json
python tools/agent_runtime/approval_gate.py --classify run_command \
    --input '{"command": "git push --force"}' --json
python tools/agent_runtime/approval_gate.py --list-policy --json
```

Tiers: `reversible` and `recoverable` run unattended; `irreversible` and
`unknown` halt. **A tool must be named in the policy to run unattended** —
`default_tier` is `unknown` and `unknown` requires approval, so an allowlist gap
fails closed rather than open. A missing or unreadable policy file makes every
tool `unknown`.

Wire it into a loop with `approval_gate=True`, or set the env var:

```python
from icdev.tools.llm.agent_loop import run_agent_loop

run_agent_loop(router, system_prompt=..., user_prompt=..., tools=..., 
               tool_handlers=..., approval_gate=True)      # or a custom hook
```

```bash
export ICDEV_AGENT_APPROVAL_MODE=enforce   # enforce (default) | dry_run | off
export ICDEV_APPROVAL_ACTOR="jane.doe"     # recorded with every decision
```

Every approval **and** denial is appended to `agent_approval_log` (migration
342, append-only) with the actor and the reason. Argument **values are never
stored** — only argument key names and a SHA-256 of the input, because tool
arguments can carry CUI. `dry_run` and `off` still write the audit row.

### Policy chain — ALLOW / DENY / ASK (exa-policy-01)

A layer **above** the reversibility gate. A policy is a function
`PolicyEvent -> PolicyDecision` returning one of three effects plus a reason.
`classify()` becomes one policy in the chain (`reversibility`) with its verdict
unchanged; a **DENY short-circuits** the chain and is never offered to the
approver.

```bash
python tools/agent_runtime/policy_engine.py --list-policies --json
python tools/agent_runtime/policy_engine.py --evaluate git_push --json
python tools/agent_runtime/policy_engine.py --evaluate run_command \
    --input '{"command": "git push --force"}' --json
```

```python
from tools.agent_runtime.policy_engine import build_policy_hook

run_agent_loop(..., approval_gate=build_policy_hook())   # drop-in for the gate hook
```

Config: `args/agent_policy_chain.yaml` (`on_policy_error`, `chain`, per-event
`floors`, `audit.log_allow`). Decisions land in the same append-only
`agent_approval_log` through `approval_gate.record_decision()`, so the
no-argument-values property is inherited rather than re-implemented.

### Three-level composition + session state (exa-policy-02)

Policies resolve **session** (end user) → **agent** (agent author) → **server**
(admin baseline). A DENY at any level short-circuits the whole composition.
Levels are **additive, never overriding**: the answer is the strictest effect any
level returned, so a session can only ever ADD a deny — which is what makes
evaluating the least-trusted level first safe.

```bash
python tools/agent_runtime/policy_composition.py --levels --json
python tools/agent_runtime/policy_composition.py --evaluate git_push --json
python tools/agent_runtime/policy_composition.py --evaluate git_push \
    --session-policy '{"chain": [{"name": "reversibility"}]}' --json
python tools/agent_runtime/policy_composition.py --state <session-id> --json
python tools/agent_runtime/policy_composition.py --reset-state <session-id> --json
```

```python
from tools.agent_runtime.policy_composition import build_composed_policy_hook

run_agent_loop(..., approval_gate=build_composed_policy_hook(
    session_id=session_id,
    session_policy={"chain": [{"name": "max_tool_calls"}]},   # user tightening
))
```

A stateful policy returns `state_updates` — omnigent's mechanism — and
`SessionState` applies them as each policy returns, so a later policy reads what
an earlier one wrote:

```python
PolicyDecision(
    ALLOW, "under the limit", policy="max_calls",
    state_updates=({"key": "call_count", "action": "increment", "value": 1},),
)
```

Actions: `increment`, `decrement`, `set`, `append`, `delete`. State is keyed by
`session_id` and persisted to `agent_session_policy_state` (migration
`20260812054330`) so a counter is not reset by a process restart mid-session. A
malformed update raises and resolves to DENY — a counter that silently fails to
increment is a limit that silently never fires.

Config: the top-level `chain` in `args/agent_policy_chain.yaml` is the **server**
level; its `agent:` block is the in-repo agent default, overridden by
`<profile_dir>/policy_chain.yaml` or `$ICDEV_AGENT_POLICY_CHAIN_AGENT`. The
session level comes from the runtime or `$ICDEV_AGENT_POLICY_CHAIN_SESSION`.
`audit` is server-only, and `on_policy_error: allow` is refused at every level.

---

## Builtin Agent Policies (exa-policy-03)

The three policies that actually *use* the chain and the session state above.
Each is a **factory**: a chain entry carries `params:` and the factory builds one
configured instance (omnigent's `factory_params` shape), so an instance is
configured rather than copied — and configured per level for free.

```bash
python tools/agent_runtime/policy_builtins.py --list --json
python tools/agent_runtime/policy_builtins.py --describe risk_score --json
python tools/agent_runtime/policy_builtins.py --check max_tool_calls_per_session \
    --params '{"limit": 500}' --json
```

| Policy | What it holds that a regex cannot | Required params |
|--------|-----------------------------------|-----------------|
| `max_tool_calls_per_session` | How many calls this session has already made | `limit` |
| `git_write_allowlist` | Which **branch** in which **repo** a push may write | `allow_branches` and/or `deny_branches` |
| `risk_score` | Risk accrued across a long chain of individually benign calls | `ask_at`, `deny_at` |

```yaml
# args/agent_policy_chain.yaml
chain:
  - name: git_write_allowlist
    enabled: true            # <- how one is switched off, per level
    params:
      repos: ["*"]
      deny_branches: [main, master, "release/*"]   # checked first, case-INSENSITIVE
      allow_branches: ["feat/*", "kanban/*"]       # allowlist, case-SENSITIVE
      on_violation: deny
      on_unknown: ask        # a bare `git push` does not name its branch
```

**No threshold has a Python default.** A missing `limit` / `ask_at` / `deny_at`
is a config error that resolves to a DENY naming it — never a number nobody
chose. An unknown param key, and `params` given to a policy that cannot take
them, are errors for the same reason: accepted-and-ignored is a rule the operator
believes is in force and which is not.

A call a policy **refuses** does not accrue — it never ran. The stateful two
require a `session_id` (`require_session: true`), because without one there is no
session to count against and a per-session limit would silently become no limit.

---

## Normalized Agent Event View (agov-det-01)

A **read-only** projection of the agent activity ICDEV already stores into one
`AgentEvent` shape. Creates no table and issues no write. Sources:
`hook_events`, `agent_executions`, `ai_telemetry`, `audit_trail`,
`ace_audit_log`.

```bash
python tools/agent_detect/events.py --json --limit 20
python tools/agent_detect/events.py --session <session_id> --json
python tools/agent_detect/events.py --source hook_events --event-type command.exec --json
python tools/agent_detect/events.py --summary --json
python -m tools.agent_detect.events --since 2026-08-01 --until 2026-08-09 --json
```

Event types are **mutually exclusive** — one source row yields at most one
event: `command.exec`, `file.read`, `file.write`, `file.delete`,
`network.indicator`, `tool.call`. A recognized shell request is `command.exec`
and never additionally `tool.call`; an unrecognized tool (including every MCP
tool, whose input schema ICDEV does not own) stays `tool.call` with
`mcp_server` and `mcp_tool` preserved.

Two invariants are enforced in code:

- **Classification never reads free text.** `_structured()` raises on any key in
  `FREE_TEXT_KEYS` (`output_summary`, `message`, `details`, `content`,
  `stdout`, …). There is no regex over any payload string anywhere in the
  module, so a command quoted in tool OUTPUT can never be read as evidence that
  the command ran.
- **A promoted event carries the operand that justified it.**
  `AgentEvent.__post_init__` rejects `command.exec` without a `command`,
  `file.*` without a `file_path` and `network.indicator` without a `url`, so an
  ambiguous payload stays `tool.call` rather than being promoted by loose
  pattern matching.

Every mapping carries a `confidence` naming how directly the source supports
it: `direct` (the tool's own documented input field), `derived` (recognized via
the shared `command_tools` list in `args/agent_approval_policy.yaml`) or
`declared` (the row names a tool and nothing more). Order them with
`CONFIDENCE_RANK`.

Library use:

```python
from tools.agent_detect.events import classify, fetch_events, summarize

events = fetch_events(session_id="sess-1", event_types=["command.exec"])
summarize(events)                      # counts by type / source / confidence
classify("Bash", {"tool_input": {"command": "git push"}})
# → ("command.exec", "direct", {"command": "git push"})
```

---

## Agent Detection Operator CLI (agov-det-07)

The operator surface over the declarative rule pack in `args/agent_rules/`.
Four verbs, all with `--json`.

```bash
python tools/agent_detect/cli.py --list --json
python tools/agent_detect/cli.py --check --json
python tools/agent_detect/cli.py --check --rules-dir args/agent_rules_enforce --json
python tools/agent_detect/cli.py --test --json
python tools/agent_detect/cli.py --scan --session <session_id> --json
python tools/agent_detect/cli.py --scan --session <session_id> --record --json
python -m tools.agent_detect.cli --list --json
```

| Verb | What it does |
|------|--------------|
| `--list` | Catalog the loaded rules — id, severity, kind, enforce, source path — plus any files skipped and why |
| `--check` | Validate a rule directory. **Exits non-zero on any invalid rule.** Run it before copying a rule into `args/agent_rules_enforce/` |
| `--test` | Evaluate the rules against the fixture events in `context/agent_detect/fixtures/`. Exits non-zero on a mismatch, and on zero cases |
| `--scan` | Evaluate the rules against the events already stored for one session. Read-only unless `--record` |

**Exit codes:** `0` completed and every check passed · `1` a check failed
(invalid rule, fixture mismatch) · `2` usage error or the verb could not run.

`--check` exists because an invalid rule is **inert, not match-all** — it is
skipped into `RuleSet.errors` rather than degraded into a partial matcher. The
exit code is therefore the only signal an operator ever gets that an enforcement
directory is not doing what its author thinks it is.

`--scan` is read-only by default so an operator can re-run it while tuning rules
without accumulating rows in an append-only table they cannot delete. With
`--record`, matches are appended to `agent_findings` as `decision="observed"`,
`enforced=False` — the CLI runs after the fact and has nothing left to deny.

> **A finding is a RULE MATCH AND NOT PROOF OF EXECUTION.** What the detector
> sees, what it does not, and the measured per-source fidelity are in
> [docs/features/agov-det-coverage-and-limits.md](../features/agov-det-coverage-and-limits.md).
> Read it before reporting a clean `--scan` as evidence.

---

## Agent Wake Store — Agent-Scheduled Resumption (agov-wake-01)

Lets an agent suspend itself and be resumed when a condition it named is met.
Every other scheduler in ICDEV is external to the agent — Genesis reflexes,
`agent_cron_jobs` (operator-declared and recurring), the kanban scheduler — and
none of them can say "stop here, resume me when PR #1342 goes CI-green".

**Library, no CLI.** Table `agent_wakes`, migration `20260809221051`.

```python
from tools.agent_runtime.wake import (
    add_timer, add_timer_in, add_completion, add_event,   # suspend
    due, complete_job, fire_event,                        # signal + collect
    mark_fired, cancel, pending, get,                     # resolve + inspect
)

add_timer_in("sess-1", 900, note="retry the flaky check")     # sleep_for
add_timer("sess-1", "2026-08-10T09:00:00+00:00")              # sleep_until
add_completion("sess-1", "job-42")                            # wake_on(job)
add_event("sess-1", "pr:1342:ci_green")                       # wake_on_event(key)

fire_event("pr:1342:ci_green")        # -> ids promoted pending -> due
for wake in due():                    # promotes elapsed timers, then returns due
    if mark_fired(wake.wake_id):      # True only for the caller that won
        resume(wake.session_id)
```

The state machine is one-directional — `pending -> due -> fired`, or
`-> cancelled` from either live state. Every transition is a conditional
`UPDATE` on the current state, so `mark_fired` is idempotent **and**
exactly-once: two overlapping ticks cannot both resume one suspension. A
`pending` wake cannot be fired directly, because promotion is what evaluates the
condition. Writes raise `WakeStoreUnavailable` rather than drop a wake silently;
reads degrade to empty so a failure cannot wedge the reflex tick.

Agent tools (`sleep_for` / `sleep_until` / `wake_on` / `wake_on_event`) land in
agov-wake-02; the tick and the event emitters in agov-wake-03.
## Approval Inbox — Pending-Approval Store (agov-inbox-01)

`console_approver` denies on EOF, so a headless overnight run refuses every
irreversible action. The inbox is the durable destination for the ask instead —
it changes **where** the question is delivered, never **what** the agent may do.

```bash
python tools/agent_runtime/approval_inbox.py --list --json
python tools/agent_runtime/approval_inbox.py --list --state pending --inbox ops
python tools/agent_runtime/approval_inbox.py --show <item_id> --json
python tools/agent_runtime/approval_inbox.py --resolve <item_id> --approve \
    --reason "authorised" --json
python tools/agent_runtime/approval_inbox.py --resolve <item_id> --deny --json
python tools/agent_runtime/approval_inbox.py --expire-due --json
```

Backed by `approval_items` (migration `20260809203855`), which is **mutable and
deliberately NOT append-only**: an item is created `pending` and then moves
exactly once to `resolved` / `expired` / `cancelled`, and that transition is an
UPDATE. The permanent record stays `agent_approval_log` — every transition to a
terminal state writes one row through the gate's existing `record_decision()`,
so there is no second decision log.

**Expiry and cancellation record `denied`.** A timeout is never an approval; a
store that treated one as approval would silently become an auto-approver.

Argument **values are never stored or delivered**. Rows are mirrored out to
Slack/Teams/Telegram/email, so `render_summary()` — tier, rule, policy prose and
argument key **names** — is the only sanctioned way to build a deliverable body.
`ApprovalRequest.summary()` is **not** safe for this: it previews the
`command` / `path` / `file_path` value.

---

## HITL Trust Deltas — the Delta is the Reviewable Unit (trust-hitl-01)

A `force_*` override records THAT a human bypassed a TRUST gate and never WHAT
CHANGED. `approve_draft` writes "promoted despite 3 citation defect(s)" and the
draft text — the thing actually approved — appears nowhere. A reviewer cannot
review a count.

```bash
python tools/quality/hitl_delta.py --pending --json
python tools/quality/hitl_delta.py --pending --artifact-id draft-42
python tools/quality/hitl_delta.py --show <delta_id> --json
python tools/quality/hitl_delta.py --show <delta_id> --with-text
python tools/quality/hitl_delta.py --settle <delta_id> --approve \
    --reason "verified against the source PDF" --json
python tools/quality/hitl_delta.py --chain <delta_id> --json
```

The diff is **claim-anchored, not textual**: `compute_delta` runs over
`citation_grounding` claim offsets, so each changed span carries a start/end into
its own text plus the `verify_claim` verdict on both sides. A claim that could
not be checked is `unknown` — never `supported`.

**Same storage split as the approval inbox above.** `trust_deltas` (migration
`20260815063941`) is append-only EVIDENCE and is in `APPEND_ONLY_TABLES`; the
human's disposition is mutable STATE and lands in the existing `approval_items`.
`settle_delta` issues no UPDATE against `trust_deltas` — it resolves through
`approval_inbox.resolve()`, which writes the permanent `agent_approval_log` row.
A correction **appends** a successor through `supersedes_delta_id` and never
edits its predecessor, the rule `sbom_revision.apply_correction` already follows;
`revision_chain()` derives supersession at read time.

Artifact **text is never delivered**. It lives in `trust_deltas` behind the RLS
predicate; `render_delta_summary()` builds the inbox body from counts, hashes and
the delta id, because those rows are mirrored to Slack.

**A delta whose enqueue failed still reads pending.** Evidence is written before
the ask, so a dropped ask surfaces as unanswered rather than as an approval.

Consumers: the side-by-side panel is trust-hitl-02, the `force_*` call sites are
trust-hitl-03. Until those land the CLI above is the operable surface.

---

## Approval Inbox — Channel Delivery and Reply Resolution (agov-inbox-03)

Mirrors a pending item to a messaging channel and turns the human's reply back
into a resolution, over the connectors ICDEV already has — every gateway adapter
exposes the same `send_message(channel_user_id, text, thread_id)`, so there is
**no new HTTP client**.

```bash
python tools/agent_runtime/inbox_channel.py --route --json
python tools/agent_runtime/inbox_channel.py --route --persona overnight --json
python tools/agent_runtime/inbox_channel.py --deliver <item_id> --json
python tools/agent_runtime/inbox_channel.py --deliver-pending --inbox ops --json
python tools/agent_runtime/inbox_channel.py --parse "approve [icdev:ai-1234]" --json
```

Wire it into the gate with the deliverer seam agov-inbox-02 already provides:

```python
from tools.agent_runtime.approval_gate import build_approval_hook
from tools.agent_runtime.inbox_approver import make_inbox_approver
from tools.agent_runtime.inbox_channel import make_channel_deliverer

hook = build_approval_hook(
    approver=make_inbox_approver(deliver=make_channel_deliverer(persona="overnight")),
)
```

**The correlation token is the whole design.** A delivered message carries
`[icdev:<item_id>]`; a reply resolves the item that token names and nothing else.
A reply with **no** token — or with two different tokens — is **ignored**. It is
never applied to the most recent or the only pending item: guessing which
approval a bare "yes" meant is how the wrong irreversible action gets approved.

**A delivery failure never loses or resolves the item.** In-app is the store of
record and a channel is a mirror, so a raising adapter, a failed send, a missing
route or an unbuildable channel all leave the item `pending` and answerable.

**Outbound goes through the IL response filter**, then truncation, and *only
then* the token footer — so redacting a CUI marking on an IL4 channel can never
destroy the tag that makes the reply correlatable.

**Inbound still traverses all eight gates.** A reply carrying a token is
normalised to the allowlisted `icdev-approve` command before
`run_security_chain` runs (the same synthetic-command shape agent-mode uses), and
`resolve_from_reply()` refuses to settle anything unless every gate is recorded
as passed. A free-text reply is an *answer*, never an approval: the item stays
pending and still expires to `denied` on its own clock.

Routing lives in `args/approval_inbox_routing.yaml` and resolves **per-session
override → persona default → global default**, merged key by key. Its
`approvers:` list is empty by default — parity with the console approver it
replaces, which trusts whoever holds the terminal — and is enforced fail-closed
once set.

---

## Unattended Sessions — Routing, Not Autonomy (agov-inbox-04)

`unattended` decides **where** an approval ask is delivered. It does **not**
change what the agent may do.

```bash
# Enable for a session (persisted — `--resume` keeps it)
icdev chat --unattended --unattended-reason "overnight backlog run"
icdev chat --resume <ctx-id> --attended     # explicitly route back to this console

# A cron job carries its own flag, because a cron tick has no console at all
icdev cron create nightly --mode agent --payload "..." --interval 1h --unattended
icdev cron unattended <job-id> --on
icdev cron unattended <job-id> --off

# Inspect and set it directly
python tools/agent_runtime/unattended.py --list --json
python tools/agent_runtime/unattended.py --show <session_id> --json
python tools/agent_runtime/unattended.py --set <session_id> --on \
    --reason "overnight backlog run" --json
python tools/agent_runtime/unattended.py --set <session_id> --off --json
python tools/agent_runtime/unattended.py --clear <session_id> --json

# The invariant, printed: what currently requires approval
python tools/agent_runtime/unattended.py --surface --json
```

**What it does not do.** It does not widen the toolset, downgrade any tier,
remove a tier from `require_approval_tiers`, change `default_tier` (still
`unknown`), change the gate's `enforce` / `dry_run` / `off` mode, or approve
anything. An irreversible call still halts — it now **suspends** on a pending
`approval_items` row a human will answer, instead of being denied on EOF by a
console prompt that could not be shown.

`--surface` prints exactly that claim in comparable form (policy tiers,
per-tool classification, resolved gate mode). Its output is asserted
byte-identical with the flag on and off by `tests/test_unattended_flag.py`.

**Never inferred from a missing TTY.** Enabling it is an explicit human act — a
CLI flag, a cron job field, or `ICDEV_UNATTENDED` exported by an operator (a
tri-state: `ICDEV_UNATTENDED=0` is a statement, not an absence). "No TTY" is
true of a CI runner, a cron tick, a Docker `exec` and a pytest run, so an
inference would silently re-route exactly the contexts nobody is watching.

Stored in `agent_unattended_sessions` (migration `20260809213046`) and in
`agent_cron_jobs.unattended`, so a restart resumes with the same routing rather
than reverting mid-run to an approver that denies everything. A read that fails
resolves to *attended* — the stricter path — while `set_unattended` raises
rather than leave an operator with a session that refuses everything for no
visible reason.

---

## AGOV CASE — Portable Case Bundle (agov-case-02)

Exports one agent session as a directory that can be carried to a machine that
never had the source database and verified there. Not a third bundler: SWFT
(`tools/compliance/swft_evidence_bundler.py`) and `prov_recorder` both bundle
software supply-chain evidence per PROJECT, and neither is keyed by
`session_id` — this adds that axis on the same machinery and carries the
`export_prov_json` document verbatim.

```bash
# Whole session
python tools/agent_case/case_bundler.py --session sess-abc123 --out out/case-abc123

# A window of it, as JSON, replacing an existing bundle
python tools/agent_case/case_bundler.py --session sess-abc123 --out out/case-abc123 \
    --since 2026-08-09T10:00:00Z --until 2026-08-09T11:00:00Z --force --json
```

```python
from tools.agent_case.case_bundler import build_case_bundle
result = build_case_bundle("sess-abc123", "out/case-abc123")
result["bundle_digest"]   # time-free identity: same data -> same digest
```

Members: `manifest.json` (SHA-256 of every other file), `context.json`
(endpoint/context header + classification), `timeline.json`, `records/` for
`hook_events`, `audit_trail`, `agent_findings` and `agent_approval_log`,
`artifacts.json`, and `provenance/prov.json`.

Three properties hold by construction. **No member carries export wall-clock** —
it lives only in `manifest.created_at` — so identical input produces a
byte-identical manifest and `bundle_digest` is stable across export times.
**No transcript table is ever read**, so the bundle cannot leak a prompt;
`TRANSCRIPT_SOURCES` names the excluded tables in the header. **The
classification marking is resolved** through
`tools/compliance/classification_manager.py` from the markings on the session's
own records, most restrictive wins — a session with a SECRET audit row produces
a SECRET banner with no code change.

Signed values (`hook_events.payload`, `audit_trail.hash`) are exported verbatim
because the HMAC and the migration-149 chain are computed over them; redaction
applies to operator free text (`agent_approval_log.reason`/`.detail`) via
`tools/llm/output_redactor.py`. Verification that names WHICH records failed is
agov-case-03; the operator CLI is agov-case-04.
## Normalized Agent Event View (agov-det-01)

A **read-only** projection of the agent activity ICDEV already stores into one
`AgentEvent` shape. Creates no table and issues no write. Sources:
`hook_events`, `agent_executions`, `ai_telemetry`, `audit_trail`,
`ace_audit_log`.

```bash
python tools/agent_detect/events.py --json --limit 20
python tools/agent_detect/events.py --session <session_id> --json
python tools/agent_detect/events.py --source hook_events --event-type command.exec --json
python tools/agent_detect/events.py --summary --json
python -m tools.agent_detect.events --since 2026-08-01 --until 2026-08-09 --json
```

Event types are **mutually exclusive** — one source row yields at most one
event: `command.exec`, `file.read`, `file.write`, `file.delete`,
`network.indicator`, `tool.call`. A recognized shell request is `command.exec`
and never additionally `tool.call`; an unrecognized tool (including every MCP
tool, whose input schema ICDEV does not own) stays `tool.call` with
`mcp_server` and `mcp_tool` preserved.

Two invariants are enforced in code:

- **Classification never reads free text.** `_structured()` raises on any key in
  `FREE_TEXT_KEYS` (`output_summary`, `message`, `details`, `content`,
  `stdout`, …). There is no regex over any payload string anywhere in the
  module, so a command quoted in tool OUTPUT can never be read as evidence that
  the command ran.
- **A promoted event carries the operand that justified it.**
  `AgentEvent.__post_init__` rejects `command.exec` without a `command`,
  `file.*` without a `file_path` and `network.indicator` without a `url`, so an
  ambiguous payload stays `tool.call` rather than being promoted by loose
  pattern matching.

Every mapping carries a `confidence` naming how directly the source supports
it: `direct` (the tool's own documented input field), `derived` (recognized via
the shared `command_tools` list in `args/agent_approval_policy.yaml`) or
`declared` (the row names a tool and nothing more). Order them with
`CONFIDENCE_RANK`.

Library use:

```python
from tools.agent_detect.events import classify, fetch_events, summarize

events = fetch_events(session_id="sess-1", event_types=["command.exec"])
summarize(events)                      # counts by type / source / confidence
classify("Bash", {"tool_input": {"command": "git push"}})
# → ("command.exec", "direct", {"command": "git push"})
```
---

## Agent Wake Tick + Event Keys (agov-wake-03)

What ends a suspension. **No daemon** — the tick rides the Genesis cadence that
already drains `agent_cron_jobs`, because ICDEV already runs three long-lived
processes and a fourth is a fourth thing that can die unnoticed.

**Libraries, no CLI.** Ticked by `tools/genesis/reflexes/agent_cron_reflex.py`
(`every 1m` in `args/genesis_config.yaml`).

```python
from tools.agent_runtime.wake_tick import run_due_wakes
from tools.agent_runtime.wake_signals import emit_pr_state, emit_task_status

run_due_wakes()                       # fire every due wake, resume its session
run_due_wakes(resumer=my_delivery)    # swap the delivery channel, not the gate
```

The tick **claims before it delivers** — `mark_fired` first, deliver only if it
returned `True` — so two overlapping ticks cannot resume one suspension twice.
The cost is stated: a delivery that fails after the claim is not retried
(at most once, never twice) and is counted as `failed` in the tick result.

Event keys are `<subject>:<id>:<event>`, emitted by `tools/ci/pr_watcher.py`
(after each PR classification) and by the kanban state machine plus
`pr_watcher._set_task_status` (on every applied task transition):

| Key | Fired when |
|-----|-----------|
| `pr:<n>:ci_green` | CI passed and the PR is mergeable |
| `pr:<n>:ci_failed` / `:merge_conflict` / `:changes_requested` | the matching PR verdict |
| `pr:<n>:merged` / `pr:<n>:closed` | the PR left the open set |
| `task:<id>:<status>` | a kanban task reached that status (`done`, `ci_failed`, …) |

Re-emission is free: `fire_event` only promotes wakes that are still `pending`,
so a poll loop firing `pr:1342:ci_green` every 30s promotes nothing after the
first. Emitting never raises — a PR merge or a task transition is not allowed to
break because a wake could not be promoted.

---

## Security Canvas (SDC) — Demo Runner
```bash
# Run all 3 scenarios (A: Red Team, B: 12-Step Workflow, C: After State)
python tools/sdc/demo_runner.py --audience exec --json
python tools/sdc/demo_runner.py --audience tech --json
python tools/sdc/demo_runner.py --audience engineer --json

# Run a specific scenario
python tools/sdc/demo_runner.py --scenario A --audience exec --json          # Red Team: STRIDE + attack paths
python tools/sdc/demo_runner.py --scenario B --simulate --json               # 12-Step Workflow + auto-approve ISSO gates
python tools/sdc/demo_runner.py --scenario C --audience tech --json          # After State: 0 CAT1, IaC, crosswalk

# Write output to file
python tools/sdc/demo_runner.py --audience exec --json --output demo_result.json

# Seed demo data before first run
python tools/db/seeds/seed_sdc_demo.py --all

# Dashboard route: http://localhost:5050/security/demo
```

---

## GovChain Anchor Transports (trust-anchor-01, D-GC-1)

`args/blockchain_config.yaml` declared `fabric.cli_path: peer` under the comment
"Fabric CLI via subprocess" since GovChain shipped, and there was zero subprocess
usage anywhere in `tools/blockchain/`. Separately, `hfc`/fabric-sdk-py is in
neither `requirements.txt` nor `pyproject.toml`, so `blockchain_config.HAS_FABRIC`
was permanently `False` and every anchor on the platform reached
`NoOpFabricClient`. Anchoring is now routed by a transport registry.

```bash
# Which backend is carrying anchors right now, and why the others are not
python tools/blockchain/transport_registry.py --doctor --json
python tools/blockchain/blockchain_config.py --doctor          # same report via config
python tools/blockchain/blockchain_config.py --test --json     # adds active_transport

# The queue is the fall-through, not a failure: with no healthy transport every
# anchor lands in govchain_pending_operations and is replayed later.
python tools/blockchain/chain_anchor.py --anchor-provenance scr-001 --json
python tools/blockchain/chain_anchor.py --flush-pending --json
```

Transports are tried in ascending `priority` and the first HEALTHY one wins
(`fabric_sdk` 10 -> `peer_cli` 20 -> `noop` 90). Registering one `peer_cli`
entry per endpoint under `fabric.transports.peer_cli.peers` is how peer failover
works. Health is cached for `fabric.transport_health_ttl_seconds` (60s) because
`is_enabled()` is on the dashboard render path.

| Status | Healthy? | Meaning |
|---|---|---|
| `ok` | yes | backend answered and is worth using |
| `degraded` | yes | answered, but something is missing (e.g. no orderer -> invokes will fail) |
| `unreachable` | no | configured but did not answer |
| `unavailable` | no | not installed / not configured (e.g. `hfc` absent, `peer` not on PATH) |

- `hfc` remains an **undeclared dependency**. Nothing in `tools/blockchain/transports/`
  imports it at module scope; absent, `FabricSdkTransport` reports `unavailable`
  and the registry skips it.
- The no-op is **unhealthy by default** — it is the absence of a backend, and
  saying so is what makes the queue fall-through fire. `ICDEV_BLOCKCHAIN_NOOP_HEALTHY=1`
  turns it into a simulation sink whose `noop-` tx ids are **not** chain
  commitments.
- A transport reports failure by RETURNING `status: failed`, not by raising.
  `ChainAnchor` queues on anything that is not `anchored`, and `flush_pending()`
  drains a row only on `anchored`.

## AI Security Commands
```bash
python tools/security/prompt_injection_detector.py --text "input" --json
python tools/security/prompt_injection_detector.py --project-dir /path --gate --json
python tools/security/ai_telemetry_logger.py --summary --json
python tools/security/ai_telemetry_logger.py --anomalies --window-hours 24 --json
python tools/security/ai_bom_generator.py --project-id "sparkpilot" --project-dir . --json
python tools/compliance/atlas_assessor.py --project-id "sparkpilot" --json
python tools/compliance/owasp_llm_assessor.py --project-id "sparkpilot" --json
python tools/compliance/owasp_agentic_assessor.py --project-id "sparkpilot" --json
python tools/security/agent_trust_scorer.py --all --json
```

---

## Aggregation Guard (prop-sec-03 through prop-sec-08)
```bash
# Evaluate SCG aggregation rules against a result set (dry-run, no events written)
python tools/security/aggregation_guard.py --evaluate-rules --json

# Run full guard check for a surface (writes aggregation_events on match)
python tools/security/aggregation_guard.py --guard --surface "proposals/list" --json

# Gate mode — exits non-zero if action=block
python tools/security/aggregation_guard.py --guard --surface "proposals/export" --gate --json

# Show recent aggregation events (audit trail)
python tools/security/aggregation_guard.py --events --limit 50 --json

# Check guard health (rule count, events table row count)
python tools/security/aggregation_guard.py --health --json
```

---

## Reproduce-or-Drop for Dynamic Findings (oss-poc-01)
```bash
# Is this reproduction replayable at all? (predicate hygiene + step/kind checks)
python tools/security/reproduction_validator.py --validate repro.json --json

# Replay one reproduction; exits 2 when the replay was not decisive
python tools/security/reproduction_validator.py --replay repro.json --json

# Replay against a different build (proves a fix landed)
python tools/security/reproduction_validator.py --replay repro.json --target http://127.0.0.1:5051 --json

# Apply the rule to a batch — unconfirmed findings are reported but never block
python tools/security/reproduction_validator.py --enforce findings.json --json

# Gate mode — exits non-zero only for CONFIRMED findings at a blocking severity
python tools/security/reproduction_validator.py --enforce findings.json --gate

# Classify without writing to dynamic_findings / finding_replay_attempts
python tools/security/reproduction_validator.py --enforce findings.json --no-persist --json
```

Replay targets are default-deny allowlisted in `args/reproduction_policy.yaml`
(loopback only out of the box); widen with `ICDEV_REPRO_TARGET_ALLOWLIST` for a
self-hosted staging box — own targets only.

---

## Requirements Intake (RICOAS) Commands
```bash
python tools/requirements/intake_engine.py --project-id "sparkpilot" --customer-name "Name" --customer-org "Org" --impact-level IL4 --json
python tools/requirements/gap_detector.py --session-id "<id>" --check-security --check-compliance --json
python tools/requirements/readiness_scorer.py --session-id "<id>" --json
python tools/requirements/decomposition_engine.py --session-id "<id>" --level story --generate-bdd --json
python tools/requirements/boundary_analyzer.py --project-id "sparkpilot" --list-assessments --json
python tools/supply_chain/dependency_graph.py --project-id "sparkpilot" --build-graph --json
python tools/supply_chain/scrm_assessor.py --project-id "sparkpilot" --aggregate --json
python tools/supply_chain/cve_triager.py --project-id "sparkpilot" --sla-check --json
python tools/simulation/simulation_engine.py --project-id "sparkpilot" --create-scenario --scenario-name "Scenario" --scenario-type what_if --json
python tools/simulation/monte_carlo.py --scenario-id "<id>" --dimension schedule --iterations 10000 --json
python tools/simulation/coa_generator.py --session-id "<id>" --generate-3-coas --simulate --json
```

---

## DevSecOps & ZTA Commands
```bash
python tools/devsecops/profile_manager.py --project-id "sparkpilot" --assess --json
python tools/devsecops/pipeline_security_generator.py --project-id "sparkpilot" --json
python tools/devsecops/policy_generator.py --project-id "sparkpilot" --engine kyverno --json
python tools/devsecops/zta_maturity_scorer.py --project-id "sparkpilot" --all --json
python tools/compliance/nist_800_207_assessor.py --project-id "sparkpilot" --json
python tools/devsecops/service_mesh_generator.py --project-id "sparkpilot" --mesh istio --json
```

---

## Observability & XAI Commands
```bash
python tools/observability/shap/agent_shap.py --project-id "sparkpilot" --last-n 10 --json
python tools/observability/provenance/prov_query.py --entity-id "<id>" --direction backward --json
python tools/observability/provenance/prov_export.py --project-id "sparkpilot" --json
python tools/compliance/xai_assessor.py --project-id "sparkpilot" --json
```

---

## EQO Centralized Logging Commands (eqo-log)
```bash
# Query the append-only centralized_logs sink (RLS-aware, newest first)
python tools/logging/log_query.py --component genesis --level ERROR --json
python tools/logging/log_query.py --contains timeout --since 2026-06-06 --limit 50
# Dashboard: /logs  |  JSON API: GET /api/logs?component=&level=&since=&contains=&limit=
# IQE: POST /logs/api/iqe-query {question}  (collection logs.entries)
```

## Swallowed-Persistence Gate (swp-swallow-01)
```bash
# Report `except Exception: pass` blocks guarding an INSERT (nothing is written)
python tools/refactor/fix_swallowed_persistence.py --dry-run --json
# Rewrite them into logged best-effort handlers (behaviour kept, silence removed)
python tools/refactor/fix_swallowed_persistence.py --write --json
python tools/refactor/fix_swallowed_persistence.py --write --path tools/govcon --path icdev/tools/govcon
# The gate that fails the build if the pattern is reintroduced (fast + full tier)
python tools/workflow/coherence_checker.py --check swallowed_persistence --json
# Standalone CLI over the same detector — exit 0 clean, 1 violations, 2 bad path.
# For a shell / pre-commit hook / air-gapped stage that cannot load the coherence harness.
python tools/dev/check_swallowed_inserts.py
python tools/dev/check_swallowed_inserts.py --path tools/govcon --json
# Standalone check with file:line output — exit 0 clean, 1 violations, 2 detector missing
python tools/dev/check_swallowed_inserts.py
python tools/dev/check_swallowed_inserts.py --json
python tools/dev/check_swallowed_inserts.py --path tools/govcon   # scope one subtree (~1s)
```
CI runs the standalone check as the `Swallowed-INSERT gate` step in the `test` job of
`.github/workflows/icdev-ci.yml` — after `lint`, before pytest — so a reintroduced silent
write fails the build in about a minute instead of at the end of the suite. Run the same
command locally before pushing.

---

## Code Intelligence Commands
```bash
python tools/analysis/code_analyzer.py --project-dir tools/ --json
python tools/analysis/code_analyzer.py --project-dir tools/ --store --json
python tools/analysis/code_analyzer.py --project-dir tools/ --trend --json
python tools/analysis/runtime_feedback.py --health --function analyze_code --json

# Compiler-in-the-Loop Verification (LeanStral-adapted, D-VL-1)
python tools/analysis/verify_loop.py --file src/main.py --language python --json        # Single file verify
python tools/analysis/verify_loop.py --file src/main.py --language python --repair --json  # Verify + LLM repair loop
python tools/analysis/verify_loop.py --project-dir src/ --language python --json          # Project-wide verify
python tools/analysis/verify_loop.py --file src/main.py --language python --gate --json    # Gate evaluation
python tools/analysis/verify_loop.py --dry-run --file src/main.py --language python --json # Preview only

# Formal Verification Gate (LeanStral-adapted, D-VL-6)
python tools/analysis/formal_verifier.py --file src/main.py --json                        # Single file formal checks
python tools/analysis/formal_verifier.py --project-dir src/ --json                        # Project-wide formal checks
python tools/analysis/formal_verifier.py --project-dir src/ --gate --json                 # Gate evaluation
python tools/analysis/formal_verifier.py --generate-properties --file src/main.py --json  # Generate hypothesis test suggestions

# GovEval Benchmark (LeanStral FLTEval-adapted, D-VL-9)
python tools/testing/goveval.py --project-id "sparkpilot" --json                          # Full 7-dimension evaluation
python tools/testing/goveval.py --project-id "sparkpilot" --dimension ssp_completeness --json  # Single dimension
python tools/testing/goveval.py --project-id "sparkpilot" --gate --json                   # Gate evaluation
python tools/testing/goveval.py --project-id "sparkpilot" --trend --json                  # Score trend
python tools/testing/goveval.py --project-id "sparkpilot" --compare --model-a "qwen3" --model-b "claude" --json  # Model A/B

# LSP-over-MCP Server (LeanStral lean-lsp-mcp adapted, D-VL-7)
python tools/mcp/lsp_server.py                                                            # Start MCP server (stdio)
python tools/mcp/lsp_server.py --check --json                                             # Check available LSP servers
```

---

## Knowledge Graph & GraphRAG Commands
```bash
# Knowledge graph analysis
python tools/knowledge_graph/text_network.py --text "input text" --project-id "sparkpilot" --json
python tools/knowledge_graph/ingester.py --file /path/to/doc --project-id "sparkpilot" --json

# GraphRAG retrieval (D-KARL-1 scoring profiles, D-KARL-2 compression)
python tools/knowledge_graph/graph_rag.py --query "zero trust" --project-id sparkpilot --json
python tools/knowledge_graph/graph_rag.py --query "AC-2 compliance" --profile compliance --json
python tools/knowledge_graph/graph_rag.py --query "explore gaps" --profile exploratory --no-compress --json

# AI insight generation (scanner-tier, zero Claude tokens)
python tools/knowledge_graph/insight_generator.py --graph-id <id> --questions --json
python tools/knowledge_graph/insight_generator.py --graph-id <id> --bridge-gaps --json

# Parallel multi-strategy retrieval (D-KARL-3)
python tools/rag/corrective_rag.py --parallel --query "NIST compliance gaps" --json
python tools/rag/corrective_rag.py --parallel --query "zero trust" --profile compliance --json

# KARL pass-rate filtered pair generation (D-KARL-4)
python tools/finetune/pair_generator.py --generate-filtered --dataset-id "ds-xxx" --source-table "research_signals" --json
python tools/finetune/pair_generator.py --generate-filtered --dataset-id "ds-xxx" --source-table "research_signals" --num-attempts 5 --min-pass-rate 0.2 --max-pass-rate 0.8 --json

# KG enrichment — centrality + embeddings (D-KARL-7)
python tools/knowledge_graph/enricher.py --graph-id <id> --centrality --json
python tools/knowledge_graph/enricher.py --graph-id <id> --embeddings --json
python tools/knowledge_graph/enricher.py --graph-id <id> --all --json

# Compliance crosswalk graph
python tools/knowledge_graph/compliance_graph.py --build --json
python tools/knowledge_graph/compliance_graph.py --crosswalk AC-2 --target cmmc --json
python tools/knowledge_graph/compliance_graph.py --coverage fedramp --json

# Automated RAG-to-FT pipeline (D-KARL-5)
python tools/finetune/rag_ft_pipeline.py --run --json
python tools/finetune/rag_ft_pipeline.py --dry-run --json
python tools/finetune/rag_ft_pipeline.py --run --source-type innovation_signals --json
python tools/finetune/rag_ft_pipeline.py --status --json

# KG-to-FT pair generation (D-KARL-6)
python tools/finetune/kg_pair_generator.py --graph-id <id> --dataset-id <id> --json
python tools/finetune/kg_pair_generator.py --graph-id <id> --strategy entity_relationship --no-store --json
python tools/finetune/kg_pair_generator.py --graph-id <id> --strategy compliance_crosswalk --json

# Quality monitoring — RAG eval feedback loop (D-KARL-8)
python tools/finetune/quality_monitor.py --check --json
python tools/finetune/quality_monitor.py --status --json

# Entity disambiguation
python tools/knowledge_graph/disambiguator.py --find-duplicates --json
python tools/knowledge_graph/disambiguator.py --merge --source <id> --target <id> --json
python tools/knowledge_graph/disambiguator.py --add-alias --node-id <id> --alias "name" --json
python tools/knowledge_graph/disambiguator.py --resolve "AC-2" --context "compliance" --json

# Cross-project graph federation
python tools/knowledge_graph/federation.py --search "query" --json
python tools/knowledge_graph/federation.py --shared proj-a proj-b --json
python tools/knowledge_graph/federation.py --create-view "name" --projects proj-a,proj-b --json
python tools/knowledge_graph/federation.py --coverage fedramp --json

# Temporal reasoning
python tools/knowledge_graph/temporal.py --range --start 2026-03-01 --end 2026-03-21 --json
python tools/knowledge_graph/temporal.py --evolution --graph-id <id> --interval day --json
python tools/knowledge_graph/temporal.py --recent --days 7 --json
python tools/knowledge_graph/temporal.py --stale --stale-days 90 --json
python tools/knowledge_graph/temporal.py --diff --graph-id <id> --date-a 2026-03-01 --date-b 2026-03-15 --json
```

---

## Ontology Commands
```bash
# Extract schema (dry-run)
python tools/ontology/schema_extractor.py --dry-run --json

# Validate ontology catalog
python tools/ontology/ontology_catalog.py --validate --json

# Build ontology federation
python tools/ontology/federation.py --build --json

# Query ontology
python tools/ontology/ontology_catalog.py --query "AWS VPC" --json

# Export external mappings
python tools/ontology/external_mappings.py --to stix --json

# Run RAG with ontology-aware code generation
python tools/llm/router.py --ontology-aware --function code_generation --prompt "..." --json
```

---

## MBSE Commands
```bash
python tools/mbse/xmi_parser.py --project-id "sparkpilot" --file /path/model.xmi --json
python tools/mbse/reqif_parser.py --project-id "sparkpilot" --file /path/reqs.reqif --json
python tools/mbse/digital_thread.py --project-id "sparkpilot" auto-link --json
python tools/mbse/digital_thread.py --project-id "sparkpilot" coverage --json
python tools/mbse/model_code_generator.py --project-id "sparkpilot" --language python --output ./src
python tools/mbse/sync_engine.py --project-id "sparkpilot" detect-drift --json
python tools/mbse/des_assessor.py --project-id "sparkpilot" --project-dir /path --json
```

---

## DocHub Commands
```bash
# Document generation
python tools/dochub/doc_generator.py --project-id "sparkpilot" --doc-type ssp --profile 3pao --impact-level IL4 --json
python tools/dochub/doc_generator.py --project-id "sparkpilot" --generate-all --profile compliance --json
python tools/dochub/doc_generator.py --regenerate --doc-id "dh-doc-xxx" --json

# Data collection
python tools/dochub/data_collector.py --project-id "sparkpilot" --collect --json
python tools/dochub/data_collector.py --project-id "sparkpilot" --doc-type ssp --json

# Profiles
python tools/dochub/profile_engine.py --list --json
python tools/dochub/profile_engine.py --profile 3pao --json

# Export (8 formats: markdown, html, pdf_ready, oscal_json, xacta_csv, fedramp_zip, dod_pkg, emass)
python tools/dochub/export_engine.py --doc-id "dh-doc-xxx" --format html --json
python tools/dochub/export_engine.py --project-id "sparkpilot" --format fedramp_zip --output ./exports --json
python tools/dochub/export_engine.py --project-id "sparkpilot" --format dod_pkg --impact-level IL4 --output ./exports --json

# Health scoring
python tools/dochub/health_scorer.py --project-id "sparkpilot" --compute --json
python tools/dochub/health_scorer.py --project-id "sparkpilot" --trend --json
python tools/dochub/health_scorer.py --project-id "sparkpilot" --module-scores --json
python tools/dochub/health_scorer.py --portfolio --json

# Diff/changelog
python tools/dochub/diff_engine.py --doc-id "dh-doc-xxx" --latest --json
python tools/dochub/diff_engine.py --project-id "sparkpilot" --summary --json

# BYOS scanning
python tools/dochub/byos_scanner.py --project-dir /path/to/app --scan --json
python tools/dochub/byos_scanner.py --project-dir /path --scan --store --generate --json
python tools/dochub/byos_scanner.py --import-artifact --file /path/to/sbom.json --artifact-type sbom --project-id X --json

# Research enrichment
python tools/dochub/enrichment_engine.py --project-id "sparkpilot" --enrich --json
python tools/dochub/enrichment_engine.py --project-id "sparkpilot" --check-cves --json
python tools/dochub/enrichment_engine.py --cache-status --json

# Multi-tenant / module management
python tools/dochub/tenant_manager.py --register --project-id "sparkpilot" --name "SparkPilot" --app-type icdev --json
python tools/dochub/tenant_manager.py --list --json
python tools/dochub/tenant_manager.py --register-module --project-id "sparkpilot" --module-name "WriteGuard" --module-slug writeguard --json
python tools/dochub/tenant_manager.py --list-modules --project-id "sparkpilot" --json
python tools/dochub/tenant_manager.py --impact-analysis --target-id "requests" --target-type dependency --json
```

---

## Embedded Development Commands
```bash
# Natural Language to Firmware
python tools/embedded/nl_to_firmware.py --command "Blink LED every 2 seconds" --board simulator --json
python tools/embedded/nl_to_firmware.py --command "Read temperature sensor" --board esp32-s3 --json
python tools/embedded/nl_to_firmware.py --command "Send MQTT message" --board stm32f407 --deploy --json

# CMake and FreeRTOSConfig.h Generation
python tools/embedded/cmake_generator.py --board esp32-s3 --json
python tools/embedded/cmake_generator.py --board simulator --with-tinyml --json
python tools/embedded/cmake_generator.py --board stm32f407 --project-dir ./my-project --json

# Crash Analysis / Self-Healing
python tools/embedded/crash_analyzer.py --crash-type hardfault --device-id dev-001 --json
python tools/embedded/crash_analyzer.py --patterns --json
```

---

## Fleet Management Commands
```bash
# Device Registry
python tools/fleet/device_registry.py --register --name "my-esp32" --board esp32-s3 --json
python tools/fleet/device_registry.py --list --json
python tools/fleet/device_registry.py --heartbeat --device-id dev-001 --json
python tools/fleet/device_registry.py --health --json

# OTA Updates
python tools/fleet/ota_manager.py --deploy --firmware-id fw-001 --device-id dev-001 --json
python tools/fleet/ota_manager.py --canary --firmware-id fw-001 --group-id grp-001 --canary-pct 10 --json
python tools/fleet/ota_manager.py --status --json
```

---

## Edge AI / TinyML Commands
```bash
python tools/edge_ai/model_manager.py --templates --json                           # List model templates
python tools/edge_ai/model_manager.py --register --name "anomaly" --task anomaly_detection --json
python tools/edge_ai/model_manager.py --list --json
python tools/edge_ai/model_manager.py --deploy --model-id mdl-001 --device-id dev-001 --json
python tools/edge_ai/model_manager.py --inference-stats --device-id dev-001 --json
```

---

## Gamified Missions Commands
```bash
python tools/missions/mission_engine.py --seed --json            # Seed 7 default missions
python tools/missions/mission_engine.py --list --json            # List all missions
python tools/missions/mission_engine.py --start --mission 1 --user-id player1 --json
python tools/missions/mission_engine.py --complete --mission 1 --user-id player1 --json
python tools/missions/mission_engine.py --progress --user-id player1 --json
```

---

## Simulator Commands
```bash
python tools/simulator/sim_runner.py --seed --json               # Seed virtual peripherals
python tools/simulator/sim_runner.py --peripherals --json        # List available peripherals
python tools/simulator/sim_runner.py --create --user-id player1 --json  # Create session
python tools/simulator/sim_runner.py --list --json               # List sessions
python tools/simulator/sim_runner.py --status --session-id sim-001 --json
python tools/simulator/sim_runner.py --stop --session-id sim-001 --json
```

---

## Genesis v2.0 — Autonomous Research Lab Commands
```bash
# Daemon
ICDEV_GENESIS_ENABLED=true python tools/genesis/daemon.py    # Run as always-on daemon
python tools/genesis/daemon.py --once --json                  # Single pass (run all due reflexes)
python tools/genesis/daemon.py --status --json                # Show status of all 14 reflexes
python tools/genesis/daemon.py --reflex research --json       # Run one reflex immediately
python tools/genesis/daemon.py --reflex scout --json          # GitHub competitor intel
python tools/genesis/daemon.py --reflex audit --json          # Self-scan (code quality + SAST)
python tools/genesis/daemon.py --reflex comply --json         # cATO evidence + crosswalk + SbD
python tools/genesis/daemon.py --reflex ingest --json         # RSS → innovation_signals
python tools/genesis/daemon.py --reflex market --json         # Marketplace analytics
python tools/genesis/daemon.py --reflex publish --json        # Demand → draft → WriteGuard → staging
python tools/genesis/daemon.py --reflex test --json           # Find untested modules → generate tests
python tools/genesis/daemon.py --reflex learn --json          # Training pair generation
python tools/genesis/daemon.py --reflex heal --json           # Pattern-match errors → remediation
python tools/genesis/daemon.py --reflex evolve --json         # Worst-quality file → LLM analysis → GKP proposal
python tools/genesis/daemon.py --reflex docs --json           # Documentation drift detection → GKP report
python tools/genesis/daemon.py --reflex report --json         # Weekly status report
python tools/genesis/daemon.py --enable research              # Enable a reflex
python tools/genesis/daemon.py --disable evolve               # Disable a reflex
python tools/genesis/daemon.py --reset heal --json            # Reset circuit breaker

# Knowledge Bridge (Promoter)
python tools/genesis/promoter.py --list --json                                      # List all GKPs
python tools/genesis/promoter.py --list --status-filter pending_review --json       # Pending review
python tools/genesis/promoter.py --auto-promote --json                              # Auto-promote eligible
python tools/genesis/promoter.py --promote gkp-xxxx --json                          # Manually promote
python tools/genesis/promoter.py --reject gkp-xxxx --reason "Not relevant" --json   # Reject
python tools/genesis/promoter.py --stats --json                                     # Promotion statistics

# Feedback Collector (v1.x → v2.0 telemetry)
python tools/genesis/feedback_collector.py --collect --json    # Collect all feedback now
python tools/genesis/feedback_collector.py --latest --json     # Show latest feedback
python tools/genesis/feedback_collector.py --summary --json    # 7-day summary
python tools/genesis/feedback_collector.py --priorities --json # Reflex priority recommendations

# Reporter
python tools/genesis/reporter.py --generate --json            # Generate weekly report
python tools/genesis/reporter.py --latest                     # Show latest report
python tools/genesis/reporter.py --list --json                # List all reports

# Self-monitor reflex (includes the kax-stall-01 board throughput stall rule)
python tools/genesis/reflexes/self_monitor.py --json          # Full cycle: probes + board throughput
python tools/genesis/reflexes/self_monitor.py --no-refresh --json   # Skip the live probe refresh
```

### Board throughput stall rule (kax-stall-01)

Fires one `board_throughput:done_flatline` alert when no kanban task reached
`done` inside the configured window WHILE tasks sat in `scheduled`/`in_progress`.
An empty board reads as idle, not stalled. Surfaced as a banner above the Task
Board on Home (`/`) and on `/monitoring`.

```bash
# Read the raw signal (read-only; safe against the live board)
python tools/kanban/metrics.py --stall                        # {stalled, reason, completed_in_window, ...}
python tools/kanban/metrics.py --stall --window-hours 72       # widen the window

# Runtime proof on the AMBIENT backend — refuses to run on a non-empty database
ICDEV_DATABASE_URL=postgresql://icdev:PW@localhost:5432/icdev_stall_verify \
ICDEV_STORAGE_BACKEND=postgresql ICDEV_PG_NO_FALLBACK=1 \
  python tools/db/bootstrap_pg.py                             # build the throwaway PG first
ICDEV_DATABASE_URL=postgresql://icdev:PW@localhost:5432/icdev_stall_verify \
ICDEV_STORAGE_BACKEND=postgresql ICDEV_PG_NO_FALLBACK=1 \
  python tools/testing/verify_board_stall_rule.py --json
```

Config lives in `args/genesis_config.yaml` under `self_monitor.board_throughput`
(`enabled`, `window_hours`, `min_active_tasks`, `cooldown_hours`, `severity`).
Env overrides win over YAML: `ICDEV_BOARD_STALL_ENABLED`,
`ICDEV_BOARD_STALL_WINDOW_HOURS`, `ICDEV_BOARD_STALL_MIN_ACTIVE`,
`ICDEV_BOARD_STALL_COOLDOWN_HOURS`, `ICDEV_BOARD_STALL_SEVERITY`.

### PR watcher liveness probe (kax-obs-02)

"Is the PR watcher actually polling?" answered without the log file. Each
COMPLETED poll appends one row to the existing `heartbeat_checks` table
(`check_type = 'pr_watcher_poll'`, `items_found` = tasks checked,
`details.actions_taken` = actions taken). No new daemon, no new log file — the
launcher already restarts a *dead* watcher, so what this detects is a
**live-but-not-progressing** one, which a process-exists check cannot see.

```bash
python tools/kanban/metrics.py --watcher      # {state, last_poll_at, minutes_since_last_poll, tasks_checked, actions_taken}
python tools/kanban/metrics.py --stall        # same signal joined onto the stall check as `watcher` + `stall_attribution`
python tools/monitor/heartbeat_daemon.py --status   # pr_watcher_poll listed alongside every other check
curl -s localhost:5050/api/live-check | python -m json.tool   # dashboard Live Activity -> `pr_watcher`
```

`stall_attribution` is what makes a flatline actionable — the two situations
that used to look identical:

| value | meaning |
|-------|---------|
| `throughput_present` | tasks are completing; not a stall |
| `watcher_not_polling` | last poll is older than `stale_after_minutes` (default 15) — broken pipe |
| `watcher_polling_nothing_mergeable` | watcher is alive and took zero actions — look at executors / done-gate / CI |

---

## Loop Engineering — GEPA Optimizer & Adversarial Verify
```bash
# GEPA (Genetic Evolution of Prompt Architectures) optimizer
python tools/skills/gepa_optimizer.py --dry-run              # Preview evolution cycle without writing
python tools/skills/gepa_optimizer.py --json                 # Run full GEPA cycle, JSON output
python tools/genesis/reflexes/gepa_optimizer.py --dry-run    # Same via genesis reflex path

# Genesis daemon — GEPA reflex (24 h interval, registered in daemon.py REFLEX_NAMES)
python tools/genesis/daemon.py --reflex gepa --json          # Run GEPA reflex immediately

# Refinement evidence — WHY was this refinement proposed? (exa-refine-04)
python tools/workflow/refinement_evidence.py --task-type build --json     # Collect evidence for a task_type's traces
python tools/workflow/refinement_evidence.py --task-type build --skill icdev-build --window-days 14
python tools/workflow/refinement_evidence.py --artifact-id <artifact-id>  # Show evidence stored on an existing proposal
# Joins each trace's task_id to its `lesson_learned` row in memory_entries and adds the
# per-pattern recurrence score from lesson_learned.get_recurrence, producing a
# `refinement_evidence/v1` bundle that is written whole into
# agent_improvement_artifacts.evidence_traces by reflexion_agent and NOVA SELA.
# THE GATE: a proposal with no supporting lesson rows is persisted with
# status='rejected_no_evidence' — never 'pending' — so GEPA and the review queues
# (which select on 'pending') cannot surface it to a human.
# Config: args/refinement_evidence.yaml (require_evidence, min_lessons,
# min_recurrence_score, window_days). Legacy bare trace-id lists and NOVA provenance
# dicts still read via parse_evidence() and report zero lesson evidence honestly.

# Kanban — clear a stale done-gate block without re-dispatching (kpr-rvfy-02)
python tools/kanban/cli.py --reverify <task-id> --dry-run     # Compute the verdict, write nothing
python tools/kanban/cli.py --reverify <task-id> --json        # Append a fresh verification row
# Exit 0=passed, 1=failed, 2=no such task. pr_watcher's enforced done-gate reads only the
# LATEST kanban_verifications row and nothing writes one except a dispatch, so a task that
# verified badly once cannot auto-merge until it is re-dispatched — which opens a SECOND PR.
# This recomputes the verdict from the branch's real state (remote refs only, so it does not
# depend on the dispatching process still being alive) and appends it. It does not weaken the
# gate: a branch with no work still fails.

# Kanban — LAND a task's PR instead of being refused by the done-gate (kax-merge-01)
python tools/kanban/cli.py --set-status <task-id> done --merge --dry-run   # Preflight only, merges nothing
python tools/kanban/cli.py --set-status <task-id> done --merge --json      # Merge, confirm, then mark done
# `--set-status done` only ever GATED on merge: it refuses while a branch carrying the task id
# has commits not on origin/<default>, and offered --force-done as the audited bypass. Neither
# lands the work. --merge is the way to SATISFY the gate, and it is strictly HARDER than the
# refusal: an OPEN PR based on the default branch, not CONFLICTING, no requested changes, green
# CI (an empty check rollup is unknown, not green), the enforced done-gate
# (pr_watcher._enforced_done_ok — reused, not re-derived), the sibling-file-conflict guard when
# hold_on_sibling_conflict is set, and finally `state == MERGED` read back from GitHub before
# 'done' is written (gh pr merge --auto exits 0 while the merge is still queued). Fail-closed on
# every unknown, and it never reads KANBAN_REQUIRE_MERGE_FOR_DONE — that switch disables the
# local git heuristic, not a landing check. One task id per invocation; not combinable with
# --force-done. Marking done records the same actor='manual' audit transition --force-done does.
# Kanban — re-queue a task for a clean rebuild without faking a failure (kax-recover-02)
python tools/kanban/cli.py --requeue <task-id> --reason "closing stale PR; rebuild on main"
python tools/kanban/cli.py --requeue <id1> <id2> --requeue-status scheduled --json
# Use this INSTEAD of `--set-status <id> backlog`. A hand-written re-queue bumps updated_at
# while leaving last_failure_reason set, and failure_triage.find_recent_failures selects on
# exactly that pair — so a clean re-queue manufactures a phantom triage queue (measured
# 2026-08-08: five healthy sbx tasks entered the autofix queue this way, PR #1379).
# --requeue clears last_failure_reason and branch_name, records the transition, and
# PRESERVES failure_count (the recovery guard's budget). It also works on a task parked in
# a pipeline-owned status like pr_opened, which --set-status cannot write. Exit 1 if any
# task was refused; a manual-mode gate sentinel needs --force.

# Kanban — is restarting the scheduler safe right now? (kax-recover-04)
python -m tools.kanban.startup_recovery --dry-run --json      # Classify only; changes nothing
python -m tools.kanban.startup_recovery --dry-run --force     # Same, even while the daemon owns the runner
python -m tools.kanban.startup_recovery --json                # Perform the sweep (what a restart does)
# Ask BEFORE restarting. Both restart sweeps (the kanban_scheduler.py entrypoint and the
# reflex's cycle-1 sweep) route through recover_interrupted_tasks, which HOLDS any in_progress
# task with provable liveness — an in-process handle, a fresh agent_sessions heartbeat in the
# task worktree, a live kanban:task:<id> lease holder, or an OS process naming the task — and
# resets only genuinely orphaned rows. --dry-run reports, per task, whether its commits survive
# on kanban/<id> or whether a reset discards its work, so a restart is no longer a guess.
# Without --force it no-ops while another live scheduler owns the runner; --once bypasses the
# entrypoint lockfile check, so that guard is what keeps a one-shot run off the live board.

# Kanban — rebase a DIRTY PR branch before it burns its resume budget (kax-conflict-01)
python tools/kanban/rebase_recovery.py --task <task-id> --dry-run --json  # Probe locally, never push
python tools/kanban/rebase_recovery.py --task <task-id> --json            # Rebase + force-with-lease push
# A branch that has merely drifted behind main goes DIRTY, and pr_watcher treats that as a
# resume class — so the PR spends all max_resume_cycles_per_task LLM resumes on a conflict a
# plain rebase would have cleared, then lands in a permanent human queue. This rebases in an
# isolated detached worktree and pushes ONLY when the rebase is clean; a real conflict aborts
# and escalates as before. Only kanban/<task-id> (or its -rN retry sibling) is ever pushed.
# pr_watcher calls this automatically on MERGE_CONFLICT — see args/pr_watcher_config.yaml
# (auto_rebase_on_conflict, max_rebase_attempts_per_task). Rebase attempts are a separate
# ledger from resumes, so recovery never eats the resume budget.

# Kanban task_factory — loop_type and adversarial fields
# Create a looping task (loop_type: "fixed" | "adaptive" | "gepa")
python -c "
from tools.kanban.task_factory import create_tasks
create_tasks([{
  'id': 'loop-example-01',
  'title': 'Example loop task',
  'loop_type': 'adaptive',          # fixed | adaptive | gepa
  'adversarial_enabled': True,      # spawns _run_adversarial_verify after each iteration
  'description': '...',
  'acceptance_criteria': '...',
}])
"

# Adversarial verify (invoked automatically when adversarial_enabled=True on a looping task)
# _run_adversarial_verify(task_id) in tools/kanban/task_factory.py — not a standalone CLI

# OSS Adaptation card (oss- prefix) — 23 tasks / 12 epics adapting RAGFlow, Crawl4AI,
# browser-use and STRIX as patterns rather than dependencies.
# Analysis: docs/spikes/oss-00-ragflow-crawl4ai-browseruse-strix-adaptation.md
python tools/kanban/seed_oss_adaptation.py --dry-run --json   # Validate the graph, write nothing
python tools/kanban/seed_oss_adaptation.py --json             # Seed tasks + ordering edges (idempotent)
python -m tools.kanban.cli --set-status oss-gate-00 done      # RELEASE the card (deliberate human act)
# Seeds TWO dependency layers and needs both: the scalar oss-gate-00 sentinel (blocks
# everything while held in_progress) and 21 junction kanban_task_deps edges for intra-epic
# ordering. Without the edges all 22 tasks become eligible at once with an arbitrary
# created_at tiebreak, so the runner can build oss-browse-02 (scope controls) before
# oss-browse-01 (the primitive they constrain). validate() refuses to seed on an unknown
# edge endpoint, a self-dependency, or a cycle.

# Compass dispatch probe — seeds the one trivial compass task that proves repo-aware
# dispatch end to end (prem-vfy-01). Definition: args/kanban_seed_compass_dispatch.yaml;
# routing: `prem-vfy` prefix in args/kanban_external_repos.yaml.
python -m tools.kanban.seed_compass_dispatch_probe --dry-run --json   # Validate routing, write nothing
python -m tools.kanban.seed_compass_dispatch_probe --json             # Seed onto the board
# Refuses to seed if the id's prefix does not resolve to the repo the YAML claims
# (an unregistered prefix defaults to ICDev — that would build a compass task in ICDev).
# The external repo root must be set where the scheduler runs, else dispatch SKIPs it:
#   $env:ICDEV_KANBAN_REPO_COMPASS = "C:/path/to/compass"
```

---

## Bayesian Autoresearch Commands (Phase 67)
```bash
# Experiment engine (Karpathy Loop)
python tools/autoresearch/experiment_engine.py --create --domain compliance --hypothesis "..." --json
python tools/autoresearch/experiment_engine.py --loop --domain compliance --max-experiments 5 --json
python tools/autoresearch/experiment_engine.py --loop --domain compliance --overnight --json
python tools/autoresearch/experiment_engine.py --status --json
python tools/autoresearch/experiment_engine.py --health --json

# Bayesian experiment selector
python tools/autoresearch/bayesian_selector.py --select --domain compliance --json
python tools/autoresearch/bayesian_selector.py --estimate --domain compliance --json
python tools/autoresearch/bayesian_selector.py --category-order --domain compliance --json

# Fitness evaluator (6 domains)
python tools/autoresearch/fitness_evaluator.py --evaluate compliance --json
python tools/autoresearch/fitness_evaluator.py --evaluate code_quality --project-dir tools/ --json
python tools/autoresearch/fitness_evaluator.py --evaluate-all --json
python tools/autoresearch/fitness_evaluator.py --list-domains --json

# Hypothesis generator
python tools/autoresearch/hypothesis_generator.py --domain compliance --max 5 --json
```

---

## Connector Forge Commands
```bash
# Generate connector from OpenAPI spec (template-only, no LLM)
python -c "from tools.databridge.forge.forge_agent import forge_from_spec; import json; print(json.dumps(forge_from_spec(content='{...}', connector_name='my_api', use_llm=False, run_sandbox_flag=False), indent=2))"

# List forge connectors
python -c "from tools.databridge.forge.forge_agent import list_forge_connectors; import json; print(json.dumps(list_forge_connectors(), indent=2))"

# Promote sandboxed connector
python -c "from tools.databridge.forge.promoter import promote_connector; import json; print(json.dumps(promote_connector('forge-xxx', 'admin'), indent=2))"

# MCP server
echo '{"jsonrpc":"2.0","id":1,"method":"forge_list","params":{}}' | python tools/mcp/connector_forge_server.py
```

---

## CI/CD Commands
```bash
python tools/ci/triggers/webhook_server.py           # Start webhook server
python tools/ci/triggers/poll_trigger.py             # Start issue polling
python tools/ci/workflows/icdev_sdlc.py 123          # Run full SDLC pipeline
```

---

## Forge Studio Blueprint Commands
```bash
# Tier classification (deterministic 12-signal heuristic)
python tools/forge_studio/generator/complexity_detector.py --classify-tier --description "Build me a CRM" --json

# Create blueprint (classify + store)
python tools/forge_studio/blueprint/export_engine.py --description "Build me a CRM" --json
python tools/forge_studio/blueprint/export_engine.py --app-id "app-xxx" --force-tier local --json

# Full automated pipeline: classify → route → build/submit
python tools/forge_studio/blueprint/build_tracker.py --build --description "Build me a CRM" --json
python tools/forge_studio/blueprint/build_tracker.py --build --app-id "app-xxx" --json

# Build status (auto-polls parent for Tier 2)
python tools/forge_studio/blueprint/build_tracker.py --status --blueprint-id "bp-xxx" --json

# Retry queued parent submissions
python tools/forge_studio/blueprint/build_tracker.py --retry-queued --json

# Parent ICDEV™ handoff
python tools/forge_studio/blueprint/parent_client.py --submit --blueprint-id "bp-xxx" --json
python tools/forge_studio/blueprint/parent_client.py --poll --blueprint-id "bp-xxx" --json
python tools/forge_studio/blueprint/parent_client.py --health --json
```

---

## Pulse AI Blog Engine Commands
```bash
# SAM.gov → Pulse Article Pipeline
python tools/pulse/engine/sam_bridge.py --run --json              # Generate articles from SAM.gov opportunities
python tools/pulse/engine/sam_bridge.py --dry-run --json          # Extract topics without generating
python tools/pulse/engine/sam_bridge.py --list-pending --json     # List pending extracted topics
python tools/pulse/engine/sam_bridge.py --stats --json            # Pipeline statistics

# Capability Scanner (D-PULSE-CAP-1, deterministic keyword matching)
python tools/pulse/engine/capability_scanner.py --list --json            # List all capabilities across 18 domains
python tools/pulse/engine/capability_scanner.py --domains --json         # List domain summaries
python tools/pulse/engine/capability_scanner.py --match "zero trust compliance" --json  # Match capabilities by keywords
python tools/pulse/engine/capability_scanner.py --format-context "DevSecOps pipeline" --json  # Format for drafter injection

# Demand Detector (D-PULSE-CAP-2/3, append-only signals)
python tools/pulse/engine/demand_detector.py --detect --json             # Detect signals from recent SAM pain points
python tools/pulse/engine/demand_detector.py --aggregate --json          # Compute frequency/velocity stats
python tools/pulse/engine/demand_detector.py --high-demand --json        # List high-demand unmet signals (threshold 5+)
python tools/pulse/engine/demand_detector.py --suggest-articles --json   # Suggest positioning articles for high-demand gaps
python tools/pulse/engine/demand_detector.py --graph --json              # Query capability graph edges
```

---

## GSD-Adapted Tools (Context Engineering & Quality Guard)
```bash
# 4-Level Verification & Stub Detection (D-GSD-1 through D-GSD-3)
python tools/testing/stub_detector.py --file src/main.py --json                 # Single file: EXISTS→SUBSTANTIVE→WIRED→FUNCTIONAL
python tools/testing/stub_detector.py --project-dir tools/ --json               # Directory scan
python tools/testing/stub_detector.py --project-dir tools/ --gate --json        # Gate evaluation (exit 0=pass)
python tools/testing/stub_detector.py --project-dir tools/ --store --json       # Store results in DB
python tools/testing/stub_detector.py --file src/main.py --max-level substantive --json  # Stop at specific level

# Context Pressure Monitor & Stuck Detection (D-GSD-4 through D-GSD-6)
python tools/agent/context_pressure.py --check health --json                    # Combined health check
python tools/agent/context_pressure.py --check pressure --session-id <id> --json  # Context window pressure
python tools/agent/context_pressure.py --check stuck --session-id <id> --json   # Stuck detection guard
python tools/agent/context_pressure.py --check health --human                   # Human-readable output

# Category-Based Deviation Rules (D-GSD-7 through D-GSD-9)
python tools/knowledge/deviation_rules.py --classify '{"error_message":"SQL injection"}' --json  # Classify failure
python tools/knowledge/deviation_rules.py --apply '{"error_message":"SQL injection"}' --confidence 0.55 --json  # Apply rules
python tools/knowledge/deviation_rules.py --list-categories --json              # List all 5 categories
python tools/knowledge/deviation_rules.py --stats --json                        # Deviation rule statistics
```

---

## Bayesian Teaching Intelligence Commands
```bash
# Information-Gain Scoring (D-BT-1 through D-BT-6)
python tools/intelligence/bayesian_teacher.py --score-pairs --dataset-id "ds-xxx" --json        # Score fine-tuning pairs by info gain
python tools/intelligence/bayesian_teacher.py --optimal-order --project-id "proj-123" --json     # Optimal compliance teaching order
python tools/intelligence/bayesian_teacher.py --teaching-dim --items '["AC-2","AC-3","SC-7"]' --json  # Teaching dimension
python tools/intelligence/bayesian_teacher.py --smart-encode --project-id "proj-123" --json      # SmartEncoding tag compression
python tools/intelligence/bayesian_teacher.py --health --json                                     # Health check
```

---

## Studio Workflow Executor Commands
```bash
# Generic MCP tool executor — dispatch any tool_registry.TOOL_REGISTRY entry as a workflow step
python tools/studio/executors/mcp_executor.py --tool health_check --params '{}'
python tools/studio/executors/mcp_executor.py --tool kg_search --params '{"query":"NIST AC-2"}'
python tools/studio/executors/mcp_executor.py --tool health_check --params '{}' --run-id "run-xxx" --step-id "probe"
# Dispatch as a specific principal (default: the run's `caller` memory key, else
# $ICDEV_MCP_CALLER_IL / $ICDEV_IMPACT_LEVEL, else IL4 with no roles)
python tools/studio/executors/mcp_executor.py --tool health_check --params '{}' \
  --caller-il IL5 --caller-roles isso,compliance_officer --caller-id u1 --tenant-id t1
# A `requires_approval` tool parks a pending human gate on the run and blocks on it
# (dwo-mcp-02-d4). Approve/reject it like any HITL node — workflow Details modal, or
# workflow_runner.approve_step(step_run_id) — the refusal payload names the step_run_id.
python tools/studio/executors/mcp_executor.py --tool terraform_apply   --params '{"terraform_dir":"infra"}' --run-id "run-xxx" --approval-wait 3600
# --approval-wait 0 parks the gate without blocking; the run resumes into the decision.
# Exit 0 = handler returned; exit 1 = unknown tool (suggests closest matches),
# params failing the entry's input_schema, the handler raised, or gate MCP-WF-001
# refused it (not allowlisted / awaiting approval / caller IL too low / missing role).

# Agent executor — run an agent loop as a workflow step (`node_type: agent`, hgx-agent-01)
python tools/studio/executors/agent_executor.py --prompt "Summarise tools/foo.py" --agent-tools worktree_read
python tools/studio/executors/agent_executor.py --prompt "Add a docstring to tools/foo.py" \
  --agent-tools worktree_build --work-dir /path/to/worktree \
  --run-id "run-xxx" --step-id "build" --json
# Bundles compose; `terminal` adds the allowlisted run_command — but a bundle grants the
# CAPABILITY, not the ACCESS: AGENT-WF-001 withholds run_command below IL5 (see below).
python tools/studio/executors/agent_executor.py --prompt "Fix the failing test" \
  --agent-tools worktree_build,terminal --llm-function code_generation --effort high \
  --caller-il IL5 --caller-roles isso --run-id "run-xxx" --approval-wait 3600
# --llm-function is a ROUTING KEY, never a model id. There is no --model flag.
# --approval-mode enforce (default) | dry_run | off  — the ars-appr-01 reversibility gate.
# Exit 0 = the loop ran, OR the step degraded (`degraded: true` — the routed provider
# cannot serve native tool use; the runner records `skipped` and the run continues).
# Exit 1 = unrunnable as authored: no prompt, no declared bundle (default-deny), an
# unknown bundle, the loop raised, or AGENT-WF-001 withheld every tool it declared.

# Agent tool authorization gate — AGENT-WF-001 (hgx-agent-02). Check a tool WITHOUT
# running a loop: default-deny allowlist + per-tool min_il/roles from the
# `agent_workflow_tools` section of args/security_gates.yaml.
python tools/studio/executors/agent_tool_gate.py --list --json                       # the policy
python tools/studio/executors/agent_tool_gate.py --tool read_file    --caller-il IL4 --json
python tools/studio/executors/agent_tool_gate.py --tool run_command  --caller-il IL5 --json
python tools/studio/executors/agent_tool_gate.py --tool write_file   --caller-il IL4 \
  --caller-roles developer --run-id "run-xxx" --json
# Exit 0 = authorized (`disposition`: allowed | requires_approval — the latter still needs
# an approved human gate in the run before the call runs). Exit 1 = refused, `error_type`
# naming the block condition: agent_tool_not_allowlisted / agent_tool_exceeds_caller_il /
# agent_tool_missing_required_role / agent_gate_policy_unavailable.
# Every decision the executor makes is audited to append-only studio_mcp_dispatch_audit —
# the same table the mcp surface uses, so one query covers both node types:
python -c "from tools.studio.executors.mcp_executor import query_dispatch_audit as q; import json; print(json.dumps(q(run_id='run-xxx'), indent=2, default=str))"
```

---

## Studio Headless Run Control
```bash
# Start / inspect / resume a durable graph run WITHOUT the dashboard (hgx-cx-03).
# Same engine the Studio UI drives — workflow_runner's public API, not a second runtime.
python tools/studio/workflow_runner.py --start "wf-xxx" --json            # prints run_id
python tools/studio/workflow_runner.py --start "wf-xxx" --project-id "proj-123" \
  --inputs '{"target":"tools/foo.py"}' --json
python tools/studio/workflow_runner.py --status "run-xxx" --json          # run + steps + step_run_ids
python tools/studio/workflow_runner.py --resume "run-xxx" --json          # re-attach to a run left mid-flight

# WAITING IS THE DEFAULT: the worker is a daemon thread in THIS process, so returning
# immediately would kill it mid-step and strand the run at `running`. --timeout bounds
# the wait (default 600s); --no-wait is fire-and-forget and only correct when something
# else will --resume the run.
python tools/studio/workflow_runner.py --start "wf-xxx" --timeout 1800 --json
python tools/studio/workflow_runner.py --start "wf-xxx" --no-wait --json

# Exit codes — a cron caller has to tell "parked on a human gate" from "broken":
#   0 = run finished successfully (or --status read OK)
#   1 = the run failed, or the command errored (unknown workflow/run, bad --inputs,
#       run not resumable)
#   2 = the run did not finish — parked at awaiting_approval, or still running at
#       --timeout. Clear a gate with workflow_runner.approve_step(step_run_id) — the
#       --status report carries every step_run_id — then --resume.

# Same three operations over MCP (gate MCP-WF-001 authorizes them: studio_run_status is
# allowlisted read-only; studio_run_start/_resume are `requires_approval` because a step
# that spawns another run executes it under this run's authority and can recurse).
python tools/studio/executors/mcp_executor.py --tool studio_run_status --params '{"run_id":"run-xxx"}'
python tools/studio/executors/mcp_executor.py --tool studio_run_start \
  --params '{"workflow_id":"wf-xxx","wait_seconds":60}' --run-id "run-xxx" --approval-wait 3600
# wait_seconds (0 = return as soon as the run row exists) is clamped to 900s so a gate's
# 24h window can never hold an MCP call open.
```

---

## Workflow Discipline Engine Commands
```bash
# PLAN-APPLY-UNIFY Lifecycle (Phase 66, D-WF-1 through D-WF-7)
python tools/workflow/loop_engine.py --create --project-id "proj-123" --phase "auth-module" --json   # Create new loop
python tools/workflow/loop_engine.py --plan --loop-id "wl-xxx" --summary "Implement auth" --json     # Finalize plan
python tools/workflow/loop_engine.py --add-criteria --loop-id "wl-xxx" --given "..." --when "..." --then "..." --json  # Add acceptance criteria
python tools/workflow/loop_engine.py --start-apply --loop-id "wl-xxx" --json                         # Start APPLY phase
python tools/workflow/loop_engine.py --complete-task --loop-id "wl-xxx" --json                        # Complete a task
python tools/workflow/loop_engine.py --verify-criterion --criterion-id "wac-xxx" --status pass --json # Verify criterion
python tools/workflow/loop_engine.py --start-unify --loop-id "wl-xxx" --json                         # Start UNIFY phase
python tools/workflow/loop_engine.py --close --loop-id "wl-xxx" --json                               # Close loop
python tools/workflow/loop_engine.py --status --loop-id "wl-xxx" --json                              # Loop status
python tools/workflow/loop_engine.py --list --project-id "proj-123" --json                           # List loops
python tools/workflow/loop_engine.py --abandon --loop-id "wl-xxx" --json                             # Abandon loop
python tools/workflow/next_action.py --recommend --project-id "proj-123" --json                       # Next action recommendation
python tools/workflow/process_verifier.py --verify --loop-id "wl-xxx" --json                         # Verify required processes
python tools/workflow/handoff_generator.py --generate --loop-id "wl-xxx" --json                      # Generate handoff document
python tools/workflow/reconciler.py --reconcile --loop-id "wl-xxx" --json                            # Run reconciliation

# Implementation Coherence Engine (D-WF-8)
python tools/workflow/coherence_checker.py --all --human                                            # Full coherence check (human output)
python tools/workflow/coherence_checker.py --all --json                                             # Full coherence check (JSON)
python tools/workflow/coherence_checker.py --all --fix --json                                       # Check + auto-fix safe issues (imports, append-only)
python tools/workflow/coherence_checker.py --all --gate                                             # Gate evaluation (exit 0=pass, 1=fail)
python tools/workflow/coherence_checker.py --check schema_code --json                               # Single check
python tools/workflow/coherence_checker.py --changed-files "tools/foo.py,tests/test_foo.py" --json  # Scope to changed files
python tools/workflow/coherence_checker.py --changed-files-from diff.txt --tier fast --gate         # Read the diff from a file (avoids argv limits)
python tools/workflow/coherence_checker.py --tier fast --gate                                       # Per-task gate tier (defers whole-app heavies)
python tools/workflow/coherence_checker.py --tier full --gate                                       # Every check (nightly sweep / post-merge)
python tools/workflow/coherence_checker.py --tier fast --list-tier                                  # Print the check ids a tier would run
python tools/genesis/reflexes/coherence_sweep.py                                                    # Full-tier sweep on main + refresh the gate's baseline
python tools/workflow/coherence_checker.py --check capability_liveness --gate                       # Declared-but-never-consumed capabilities (exa-live-02)

# Capability Liveness gate (exa-live-02) — a capability that is registered, enabled and
# catalogued but has ZERO consumption over the telemetry's LIFETIME fails the gate.
# Measured through tools/awareness/capability_consumption.py using existing telemetry only.
# Per-class backlog counts live in args/liveness_gate.yaml and ratchet DOWN only.
# Deliberately not findings: a unit consumed once and idle inside the recent window
# (low cadence is not death), and a database with no operating history (a fresh worktree
# or ephemeral CI database makes everything look inert — the check warns instead).
# Runs in BOTH tiers (exa-live-03): ~0.75s of GROUP BY counts, so it is a per-task gate
# on every commit, not a nightly-only sweep — a capability declared in a task's own diff
# and wired to nothing is what the per-task gate should catch. A DRAINED class leaves
# args/liveness_gate.yaml entirely rather than sitting at 0; an absent class is already
# budgeted at 0, and a leftover zero is just a number for a future session to edit upward.

# Gate Sentinel Shape (kax-exec-04) — a task whose id is `<card>-gate-<n>` is filtered
# out of promote_backlog_to_scheduled by tools/kanban/gates.py::is_manual_gate, so work
# wearing that id is UNDISPATCHABLE and nothing goes red (tsg-gate-01 sat in backlog
# while the board idled). task_factory.create_tasks now REFUSES to seed that shape unless
# the task also declares itself a gate — 'MANUAL-MODE GATE' in the title, or a 'RISK:'
# line. This check is the other half: rows already on the board, or written around the
# factory. WARN, never fail — the finding is board data, not the diff under review.
python tools/workflow/coherence_checker.py --check gate_sentinel_shape --json                        # Sentinel-shaped ids that are neither held nor depended upon

# Documented Command Paths gate (oss-fix-02) — every `python tools/...` command in
# CLAUDE.md and this file must resolve to a real file. Pre-existing breakage is
# grandfathered in args/doc_command_gate.yaml; NEW broken references fail the gate.
python tools/workflow/coherence_checker.py --check doc_command_paths --json                         # List unresolved documented commands
python tools/workflow/coherence_checker.py --check doc_command_paths --gate                         # Fail on any NEW broken reference

# INSERT / Live Schema Parity gate (swp-gate-01) — every column named in a static
# INSERT under tools/ must exist in the LIVE schema (information_schema on PostgreSQL,
# PRAGMA table_info on SQLite). `CREATE TABLE IF NOT EXISTS` never alters an existing
# table, so source DDL and database drift apart silently; the resulting INSERT raises
# inside `except Exception: pass` and the feature reports success while persisting
# nothing. The 146-entry pre-existing backlog is grandfathered (WARN) in
# args/insert_schema_gate.yaml; NEW mismatches FAIL. No live database = WARN, not fail.
python tools/workflow/coherence_checker.py --check insert_schema_parity --json                      # List INSERT columns absent from the live schema
python tools/workflow/coherence_checker.py --check insert_schema_parity --gate                      # Fail on any NEW mismatch

# Vendored-copy parity (cxo-doc-03) — a stdlib-only module that standalone apps copy verbatim
# into their OWN repos (tools/cortex/client.py -> compass / idea_lab tools/integrations/
# cortex_client.py) must stay a SUBSET of every copy's public API. Targets are declared in
# args/vendor_parity.yaml (no code change to add one). Compares classes/functions/method
# parameter names, NOT bytes — the copies legitimately differ by a provenance header and by
# line endings. A consumer repo that is not checked out on this machine is SKIPPED, never failed.
python tools/workflow/coherence_checker.py --check vendor_parity --json                             # Report copies lagging canonical
python tools/workflow/coherence_checker.py --check vendor_parity --changed-files "tools/cortex/client.py" --gate   # Fail when a changed source outruns a copy

# Vendored-source API manifest (ctx-enf-01) — the half of the gate above that a CI runner can
# actually run. compass and idea_lab are separate PRIVATE repos ICDEV CI never checks out, so
# every consumer SKIPs there (correctly) and the check can never block, however far the copies
# lag. Root cause is repo TOPOLOGY, not the OS: /srv/standalone skips exactly as C:/AI/standalone
# did. args/vendor_api_manifest.json is a COMMITTED snapshot of each declared source's public API,
# generated from the same _public_api() the check compares with, so the two cannot disagree.
# Changing a source's public API without regenerating it FAILS in both coherence tiers and fails
# tests/workflow/test_vendor_api_manifest.py — which makes re-vendoring a deliberate step.
python tools/workflow/vendor_api_manifest.py                                                        # Verify; exit 1 when the manifest is stale
python tools/workflow/vendor_api_manifest.py --write                                                # Regenerate after changing a vendored source
python tools/workflow/vendor_api_manifest.py --json                                                 # Machine-readable drift report

# Bootstrap parity — what `icdev init` scaffolds must be what this repo runs on. Two rules:
#   1. must_match pairs (args/bootstrap_parity.yaml) are byte-identical, both directions.
#   2. payload completeness (exa-bench-10) — every module a PACKAGED HOOK loads by path
#      ships beside it and is not stale. Derived from each hook's own PAYLOAD_MODULES
#      tuple, not from a list in the YAML, so there is nothing to keep in sync.
# The packaged pre_tool_use.py exec_module'd tools/hooks/shared_checks.py, which the
# payload did not ship — so the hook raised on EVERY tool call in EVERY generated
# project, silenced by the `|| true` in the generated settings.json.
python tools/workflow/coherence_checker.py --check bootstrap_parity --json
python tools/workflow/coherence_checker.py --check bootstrap_parity --gate
python tools/installer/prebuild_bootstrap.py                          # Regenerate the whole payload (the sanctioned fix)
# Which checks can actually run where the hook is installed, and why the rest cannot.
# Run it from a scaffolded project: 8 of 11 checks are active there; the 3 that need
# ICDEV's own tools/ modules are NAMED rather than silently failing open.
python .claude/hooks/pre_tool_use.py --self-test

# Completion Auditor — per-canvas 8-component completeness scorecard (TCH)
python tools/quality/completion_auditor.py                                                           # Human table to stdout
python tools/quality/completion_auditor.py --json                                                   # Machine-readable scorecard
python tools/quality/completion_auditor.py --md                                                      # Write docs/quality/completion-scorecard.md (sorted least->most complete)
# Local "review-until-green" loop (greploop-adapted) — gates as a score function over the diff
python tools/quality/review_loop.py --json                            # Working-tree mode: ruff + coherence + SIPA, autofix, iterate
python tools/quality/review_loop.py --base origin/main --max 3 --gate # Branch diff vs base; exit 0=green / 1=not green
python tools/quality/review_loop.py --no-autofix --json               # Report only (no edits); emit fix_brief for the agent

# Outline contracts — does a draft have every required section, in order, with none invented? (trust-struct-02)
python tools/quality/outline_contract.py --list --json                       # 32 artifact types with a declared skeleton
python tools/quality/outline_contract.py --artifact-type ato_ssp             # Show that type's required sections + their source
python tools/quality/outline_contract.py --artifact-type SOP --json
# Validate a list_sections payload (a JSON list, or {"sections": [...]})
python tools/quality/outline_contract.py --artifact-type ato_ssp --sections-file draft.json --json
python tools/quality/outline_contract.py --artifact-type RUNBOOK --sections-file draft.json --gate   # exit 1 on findings
# Findings are missing_section | unknown_section | section_out_of_order, in the shared
# {item_number, issue, detail} shape citation_gate / placeholder_findings / kg_gate use.
# The skeletons are NOT declared in this module — it reads docgen ATO_DOC_TYPES, DIC
# TEMPLATE_SECTIONS and the RFI workbench floor. An artifact type with no declared
# skeleton resolves to None = UNMEASURED; it never fabricates one to fill the gap.
# RFI questionnaire parts are per-solicitation: use contract_from_sections() on the
# session's own sections rather than expecting a static skeleton to fit.
```

---

## Sensitive-Path Inventory — one credential list, three consumers (#exa-bench-09)
```bash
python tools/security/sensitive_paths.py --list --json                          # the whole inventory + which config file it came from
python tools/security/sensitive_paths.py --check ~/.aws/credentials --json      # classify one path (exit 1 when sensitive)
python tools/security/sensitive_paths.py --check-command "cat ~/.netrc" --json  # classify a shell command (exit 1 when it discloses)
```
Patterns live in `args/sensitive_paths.yaml`. It is consumed by three surfaces that
each previously had their own credential list or none at all: the `zero_access` tier
in `args/file_access_tiers.yaml` (`inherits: sensitive_paths`, resolved by
`tools/hooks/shared_checks.py`), the `confidentiality` rule in
`tools/agent_runtime/approval_gate.py`, and `check_path_allowed()` in
`tools/studio/executors/agent_tool_gate.py`. Add a credential glob to the inventory,
never to a consumer — three copies is three lists that drift, and the drift is silent.

Read verbs only: `cat ~/.aws/credentials` discloses, `touch ~/.ssh/authorized_keys`
writes and is deliberately NOT matched (that is exa-bench-07's worktree-containment
gap). Complements `tools/security/secret_detector.py`, which detects credential
CONTENT inside files rather than naming the paths.

---

## NemoClaw-Adapted Agent Sandboxing Commands
```bash
# Credential Broker (D-NC-1)
python tools/security/credential_broker.py --request --agent-id builder --function code_generation --json  # Request scoped token
python tools/security/credential_broker.py --revoke --agent-id builder --json                              # Revoke active tokens
python tools/security/credential_broker.py --audit --agent-id builder --json                               # Audit log
python tools/security/credential_broker.py --status --json                                                 # Broker status
python tools/security/credential_broker.py --gate --project-id "proj-123" --json                           # Gate evaluation

# Blueprint Verifier (D-NC-3)
python tools/security/blueprint_verifier.py --compute --path /path/to/dir --json                           # Compute digest
python tools/security/blueprint_verifier.py --verify --path /path/to/dir --expected <digest> --json        # Verify against expected
python tools/security/blueprint_verifier.py --store --entity-type genome --entity-id v1.2.0 --path /path --json  # Store digest
python tools/security/blueprint_verifier.py --lookup --entity-type genome --entity-id v1.2.0 --json       # Lookup stored digest

# Egress Policy Manager (D-NC-2)
python tools/security/egress_policy_manager.py --resolve --role builder --json                             # Resolve policy for role
python tools/security/egress_policy_manager.py --generate --role builder --json                            # Generate K8s NetworkPolicy
python tools/security/egress_policy_manager.py --validate --role builder --json                            # Validate policy
python tools/security/egress_policy_manager.py --list-roles --json                                         # List available roles

# Egress Monitor (D-NC-6)
python tools/registry/egress_monitor.py --collect --child-id child-1 --endpoint http://localhost:8445/health/egress --json  # Collect egress data
python tools/registry/egress_monitor.py --evaluate --child-id child-1 --json                               # Evaluate against policies
python tools/registry/egress_monitor.py --summary --child-id child-1 --json                                # Summary report

# Propagation Verifier (D-NC-5)
python tools/registry/propagation_verifier.py --verify --propagation-id prop-123 --json                    # Verify post-propagation
python tools/registry/propagation_verifier.py --history --child-id child-1 --json                          # Verification history

# Sandbox Scorer (D-NC-4)
python tools/registry/sandbox_scorer.py --score --capability-id cap-123 --json                             # Score isolation posture
python tools/registry/sandbox_scorer.py --score --source-metadata '{"has_broker":true}' --json             # Score with metadata
```

---

## Harness Engineering Commands
```bash
# Maturity assessment (6 dimensions, Level 0-4)
python tools/harness/maturity_assessor.py --project-dir . --detailed --json

# Exit criteria — list workflows or evaluate a specific one
python tools/harness/exit_criteria_evaluator.py --list --json
python tools/harness/exit_criteria_evaluator.py --workflow build --json
python tools/harness/exit_criteria_evaluator.py --workflow build --check-state .tmp/test_runs/state.json --json

# Trace analysis — analyze recent sessions for patterns
python tools/harness/trace_analyzer.py --last-n 5 --json
python tools/harness/trace_analyzer.py --session-id <id> --json
python tools/harness/trace_analyzer.py --recommendations --limit 20 --json

# Scaffold baseline harness for child apps
python tools/harness/scaffold_harness.py --output-dir /path/to/project --json
python tools/harness/scaffold_harness.py --output-dir /path --impact-level IL4 --json
```

---

## NOVA — Autonomous Self-Learning Digital Coworker Commands
```bash
# DB init — creates NOVA tables in PostgreSQL (called automatically at dashboard startup)
python -c "from tools.nova.db.init_db import init_nova_tables; print(init_nova_tables())"

# ECHO — Execution Tracing
python -c "
from tools.workflow.trace_logger import start_trace, close_trace
tid = start_trace('task-001', 'build', 'icdev-build')
close_trace(tid, 'success', 'success_first_try')
print('trace_id:', tid)
"
python -c "from tools.workflow.trace_logger import get_recent_traces; import json; print(json.dumps(get_recent_traces(limit=10), indent=2))"

# ECHO — Reflexion (requires ICDEV_HARNESS_COLEARN=true)
python -c "from tools.workflow.reflexion_agent import run_batch_reflexion; import json; print(json.dumps(run_batch_reflexion(['build'], dry_run=True), indent=2))"
python -c "from tools.workflow.reflexion_agent import get_latest_improvement; print(get_latest_improvement('build'))"

# SOUL — Coworker Identity
python -c "from icdev.tools.ace.soul_manager import build_identity_preamble; print(build_identity_preamble('ai_developer'))"
python -c "from icdev.tools.ace.soul_manager import record_learning; print(record_learning('ai_developer', 'Always use get_canvas_connection() for canvas tables.'))"

# TRUST — Trust Calibration
python -c "from tools.ace.trust_calibrator import get_trust_score, get_dispatch_config; import json; print(json.dumps(get_dispatch_config('ai_developer'), indent=2))"
python -c "from tools.ace.trust_calibrator import record_trust_event; import json; print(json.dumps(record_trust_event('ai_developer', 'success', 'task-001'), indent=2))"
python -c "from tools.ace.trust_calibrator import get_trust_summary; import json; print(json.dumps(get_trust_summary(), indent=2))"
python -c "from tools.ace.trust_calibrator import run_weekly_recalibration; import json; print(json.dumps(run_weekly_recalibration(), indent=2))"

# SELA — Skill Evolution (dry-run — never auto-merges)
python -c "from tools.evolution.artifact_evolver import evolve_artifact; import json; print(json.dumps(evolve_artifact('icdev-status', 'skill', dry_run=True), indent=2))"
python -c "from tools.evolution.artifact_evolver import evolve_all_skills; import json; print(json.dumps(evolve_all_skills(dry_run=True, limit=3), indent=2))"
python -c "from tools.evolution.eval_builder import build_dataset; ds = build_dataset('icdev-build', '', min_examples=3); print(f'train={len(ds.train)} val={len(ds.val)}')"
```

---

## Innovation Feature Commands
```bash
# VSM Dashboard (F3) — DORA metrics, pipeline flow, bottleneck detection
python tools/analytics/vsm_engine.py --project-id "sparkpilot" --dora --json
python tools/analytics/vsm_engine.py --project-id "sparkpilot" --bottlenecks --json
python tools/analytics/vsm_engine.py --project-id "sparkpilot" --pipelines --json

# Developer Scorecard (F8) — 5-dimension health scoring
python tools/analytics/scorecard.py --project-id "sparkpilot" --compute --json
python tools/analytics/scorecard.py --project-id "sparkpilot" --trend --json
python tools/analytics/scorecard.py --project-id "sparkpilot" --latest --json

# cATO Live Evidence (F1) — continuous OSCAL streaming
python tools/compliance/cato_live_engine.py --project-id "sparkpilot" --stream --json
python tools/compliance/cato_live_engine.py --project-id "sparkpilot" --dashboard --json
python tools/compliance/cato_live_engine.py --project-id "sparkpilot" --timeline --json

# AI Narratives (F4) — compliance narrative workflow
python tools/compliance/narrative_workflow.py --project-id "sparkpilot" --batch --json
python tools/compliance/narrative_workflow.py --project-id "sparkpilot" --pending --json

# Template Exchange (F2) — community compliance templates
python tools/compliance/template_exchange.py --list --json
python tools/compliance/template_exchange.py --create --name "AC-2 SSP" --template-type ssp --framework "NIST 800-53" --content "..." --json

# Firmware SBOM + VEX (F12)
python tools/compliance/firmware_sbom.py --project-id "sparkpilot" --generate --project-dir . --board simulator --json

# Thread Heatmap (F5) — digital thread coverage gaps
python tools/mbse/thread_heatmap.py --project-id "sparkpilot" --generate --json
python tools/mbse/thread_heatmap.py --project-id "sparkpilot" --orphans --json

# PR Intelligence (F6) — compliance/security pre-check
python tools/ci/pr_intelligence.py --project-id "sparkpilot" --analyze --pr-reference HEAD --project-dir . --json

# Threat Modeler (F7) — STRIDE analysis with NIST mapping
python tools/security/threat_modeler.py --project-id "sparkpilot" --list --json
python tools/security/threat_modeler.py --project-id "sparkpilot" --create --name "Model" --components '[{"id":"web","type":"web_application"}]' --json

# Golden Path Scaffolder (F9)
python tools/scaffold/golden_path.py --list --json
python tools/scaffold/golden_path.py --scaffold --template flask_api --project-name "my-project" --json

# Forge Hub (F10) — connector community marketplace
python tools/databridge/forge/community_hub.py --browse --json
python tools/databridge/forge/community_hub.py --featured --json

# ATO Simulator (F11) — Monte Carlo timeline prediction
python tools/simulation/ato_simulator.py --project-id "sparkpilot" --simulate --iterations 1000 --json

# All tools use icdev.db (NOT sparkpilot.db)
```

---

## Cross-Platform Compatibility (D145)
```bash
# Platform check (run on first setup — validates OS compatibility)
python tools/testing/platform_check.py               # Human output
python tools/testing/platform_check.py --json         # JSON output

# Platform utilities (import in Python code)
from tools.compat.platform_utils import IS_WINDOWS, IS_MACOS, IS_LINUX
from tools.compat.platform_utils import get_temp_dir, get_npx_cmd, get_home_dir
from tools.compat.platform_utils import ensure_utf8_console
```

---

## Auto-Scaling (D141-D144)
```bash
# Apply HPA + PDB (requires Metrics Server)
kubectl apply -f k8s/hpa.yaml                        # Horizontal Pod Autoscalers (18 components)
kubectl apply -f k8s/pdb.yaml                        # Pod Disruption Budgets (18 components)
kubectl apply -f k8s/node-autoscaler.yaml             # Cluster Autoscaler reference + prerequisites

# Verify scaling
kubectl get hpa -n icdev                              # Check HPA status
kubectl get pdb -n icdev                              # Check PDB status
kubectl top pods -n icdev                             # Check pod resource usage

# Helm with autoscaling enabled
helm install icdev deploy/helm/ --set autoscaling.enabled=true

# Config: args/scaling_config.yaml — profiles, topology, node autoscaler, rate limiter backend
```

---

## Testing Framework (Adapted from ADW)
```bash
# ICDEV™ platform tests (D155 — 21 test files, ~330+ tests)
pytest tests/ -v --tb=short                          # Run all platform tests
pytest tests/test_circuit_breaker.py -v              # Circuit breaker tests
pytest tests/test_retry.py -v                        # Retry utility tests
pytest tests/test_correlation.py -v                  # Correlation ID tests
pytest tests/test_errors.py -v                       # Error hierarchy tests
pytest tests/test_migration_runner.py -v             # Migration runner tests
pytest tests/test_backup_manager.py -v               # Backup/restore tests
pytest tests/test_openapi_spec.py -v                 # OpenAPI spec tests
pytest tests/test_metrics.py -v                      # Prometheus metrics tests
pytest tests/test_rest_api.py -v                     # REST API endpoint tests
pytest tests/test_swagger_ui.py -v                   # Swagger UI tests
pytest tests/test_audit_logger.py -v                 # Audit logger tests
pytest tests/test_init_icdev_db.py -v                # DB init tests
pytest tests/test_platform_db.py -v                  # Platform DB tests
pytest tests/test_readiness_scorer.py -v             # Readiness scorer tests
pytest tests/test_dev_profile_manager.py -v          # Dev profile manager tests (33 tests)
pytest tests/test_manifest_loader.py -v              # Manifest loader tests (32 tests)
pytest tests/test_session_context_builder.py -v      # Session context builder tests (26 tests)
pytest tests/test_pipeline_config_generator.py -v    # Pipeline config generator tests (14 tests)
pytest tests/test_icdev_client.py -v                 # SDK client tests (12 tests)
pytest tests/test_tool_detector.py -v                # AI tool detector tests (10 tests)
pytest tests/test_instruction_generator.py -v        # Instruction generator tests (14 tests)
pytest tests/test_mcp_config_generator.py -v         # MCP config generator tests (8 tests)
pytest tests/test_skill_translator.py -v             # Skill translator tests (10 tests)
pytest tests/test_companion.py -v                    # Companion orchestrator tests (7 tests)
pytest tests/test_prompt_injection_detector.py -v    # Prompt injection detector tests (47 tests)
pytest tests/test_ai_telemetry.py -v                 # AI telemetry logger tests (12 tests)
pytest tests/test_cloud_providers.py -v              # Cloud provider abstraction tests (20 tests)
pytest tests/test_atlas_assessor.py -v               # ATLAS assessor tests (15 tests)
pytest tests/test_multi_cloud_llm.py -v              # Multi-cloud LLM provider tests (12 tests)
pytest tests/test_child_registry.py -v               # Child registry + telemetry tests (18 tests)
pytest tests/test_evolutionary_intelligence.py -v    # Genome, evaluation, staging, propagation tests (25 tests)
pytest tests/test_genome_evolution.py -v             # Absorption, learning, cross-pollination tests (20 tests)
pytest tests/test_atlas_red_team.py -v               # ATLAS red teaming scanner tests (10 tests)
pytest tests/test_ai_bom_generator.py -v             # AI BOM generator tests (14 tests)
pytest tests/test_phase36_phase37_integration.py -v  # Phase 36↔37 security integration tests (17 tests)
pytest tests/test_cloud_monitoring_iam.py -v         # Cloud monitoring/IAM/registry tests (15 tests)
pytest tests/test_ibm_providers.py -v                # IBM Cloud provider tests (44 tests)
pytest tests/test_region_validator.py -v             # CSP region validator tests (18 tests)
pytest tests/test_translation_manager.py -v          # Translation pipeline tests (35 tests)
pytest tests/test_dependency_mapper.py -v            # Dependency mapper tests (16 tests)
pytest tests/test_source_extractor.py -v             # Source extractor tests (22 tests)
pytest tests/test_behavioral_drift.py -v             # Behavioral drift detection tests (14 tests)
pytest tests/test_tool_chain_validator.py -v          # Tool chain validator tests (22 tests)
pytest tests/test_agent_output_validator.py -v        # Agent output validator tests (22 tests)
pytest tests/test_agent_trust_scorer.py -v            # Agent trust scorer tests (22 tests)
pytest tests/test_mcp_tool_authorizer.py -v           # MCP tool authorizer tests (23 tests)
pytest tests/test_exa_policy_07_registry_authorization.py -v  # Per-tool min_il/required_roles declarations (111 tests)
pytest tests/test_behavioral_red_team.py -v           # Behavioral red teaming tests (13 tests)
pytest tests/test_owasp_agentic_assessor.py -v        # OWASP Agentic assessor tests (16 tests)
pytest tests/test_schemas.py -v                      # Shared schema enforcement tests (29 tests)
pytest tests/test_state_tracker.py -v                # Dirty-tracking state push tests (16 tests)
pytest tests/test_extension_manager.py -v            # Active extension hooks tests (18 tests)
pytest tests/test_chat_manager.py -v                 # Multi-stream chat + intervention tests (22 tests)
pytest tests/test_history_compressor.py -v           # 3-tier history compression tests (25 tests)
pytest tests/test_memory_consolidation.py -v         # AI-driven memory consolidation tests (22 tests)
pytest tests/test_context_server.py -v               # Semantic layer MCP tools tests (20 tests)
pytest tests/test_code_pattern_scanner.py -v         # Dangerous pattern detection tests (30 tests)
pytest tests/test_register_external_patterns.py -v   # Innovation signal registration tests (15 tests)
pytest tests/test_claude_dir_validator.py -v         # .claude directory governance validator tests (50 tests)
pytest tests/test_tracer.py -v                        # Tracer ABC + SQLiteTracer tests (43 tests)
pytest tests/test_trace_context.py -v                 # W3C traceparent + context propagation tests (30 tests)
pytest tests/test_mcp_instrumentation.py -v           # MCP auto-instrumentation tests (8 tests)
pytest tests/test_a2a_trace_propagation.py -v         # A2A distributed tracing tests (10 tests)
pytest tests/test_otel_tracer.py -v                   # OTelTracer + OTelSpan mock tests (17 tests)
pytest tests/test_prov_recorder.py -v                 # Provenance recorder tests (30 tests)
pytest tests/test_agent_shap.py -v                    # AgentSHAP Shapley value tests (20 tests)
pytest tests/test_xai_assessor.py -v                  # XAI compliance assessor tests (34 tests)
pytest tests/test_unified_server.py -v                 # Unified MCP gateway tests (42 tests)
pytest tests/test_oscal_tools.py -v                    # OSCAL ecosystem tools tests (40 tests)
pytest tests/test_omb_m25_21_assessor.py -v              # OMB M-25-21 assessor tests
pytest tests/test_omb_m26_04_assessor.py -v              # OMB M-26-04 assessor tests
pytest tests/test_nist_ai_600_1_assessor.py -v           # NIST AI 600-1 assessor tests
pytest tests/test_gao_ai_assessor.py -v                  # GAO AI assessor tests
pytest tests/test_model_card_generator.py -v             # Model card generator tests
pytest tests/test_ai_transparency.py -v                  # AI transparency integration tests
pytest tests/test_accountability_manager.py -v          # Accountability manager tests (25 tests)
pytest tests/test_ai_impact_assessor.py -v              # AI impact assessor tests (13 tests)
pytest tests/test_ai_incident_response.py -v            # AI incident response tests (19 tests)
pytest tests/test_ai_reassessment_scheduler.py -v       # AI reassessment scheduler tests (18 tests)
pytest tests/test_ai_accountability_audit.py -v         # AI accountability audit tests (20 tests)
pytest tests/test_assessor_accountability_fixes.py -v   # Assessor accountability fixes tests (24 tests)
pytest tests/test_ai_governance_intake.py -v            # AI governance intake detection tests (37 tests)
pytest tests/test_ai_governance_chat_extension.py -v    # AI governance chat extension tests (28 tests)
pytest tests/test_code_analyzer.py -v                   # Code analyzer AST self-analysis tests (29 tests)
pytest tests/test_runtime_feedback.py -v                # Runtime feedback collector tests (22 tests)
pytest tests/test_dispatcher_mode.py -v                 # Dispatcher-only orchestrator mode tests (47 tests)
pytest tests/test_prompt_chain_executor.py -v           # Declarative prompt chain executor tests (63 tests)
pytest tests/test_anvil_critique.py -v                  # ANVIL adversarial critique tests (36 tests)
pytest tests/test_session_purpose.py -v                 # Session purpose + async result injection + tiered file access tests (27 tests)
pytest tests/test_research_engine.py -v                 # Industry Research Engine tests (68 tests)
pytest tests/test_rag_vector_stores.py -v               # RAG vector store backend tests (40 tests)
pytest tests/test_rag_chunker.py -v                     # RAG adaptive chunking tests (20 tests)
pytest tests/test_rag_retriever.py -v                   # RAG retrieval pipeline tests (25 tests)
pytest tests/test_rag_reranker.py -v                    # RAG re-ranking tests (15 tests)
pytest tests/test_rag_ingestion.py -v                   # RAG ingestion manager tests (25 tests)
pytest tests/test_rag_retention.py -v                   # RAG tier migration tests (15 tests)
pytest tests/test_rag_two_tier.py -v                    # RAG two-tier LLM integration tests (10 tests)
pytest tests/test_rag_child_app.py -v                   # RAG child app integration tests (20 tests)
pytest tests/test_finetune_provider.py -v         # Fine-tune provider ABC tests (21 tests)
pytest tests/test_finetune_gpu_detector.py -v      # GPU detection tests (20 tests)
pytest tests/test_finetune_dataset.py -v           # Dataset management tests (32 tests)
pytest tests/test_finetune_training_engine.py -v   # Training engine tests (65 tests)
pytest tests/test_finetune_evaluator.py -v         # Evaluator + promotion tests (67 tests)
pytest tests/test_finetune_router_integration.py -v # Router integration tests (23 tests)
pytest tests/test_finetune_cloud_providers.py -v   # Cloud provider tests (74 tests)
pytest tests/test_api_surface_extractor.py -v            # API surface extractor tests (38 tests)
pytest tests/test_bayesian_teacher.py -v                 # Bayesian teaching intelligence tests (74 tests)
pytest tests/test_workflow_loop.py -v                    # Workflow discipline engine tests (240+ tests)
pytest tests/test_blueprint_verifier.py -v               # Blueprint verifier tests (38 tests)
pytest tests/test_credential_broker.py -v                # Credential broker tests (30 tests)
pytest tests/test_egress_monitor.py -v                   # Egress monitor tests (17 tests)
pytest tests/test_egress_policy_manager.py -v            # Egress policy manager tests (29 tests)
pytest tests/test_propagation_verifier.py -v             # Propagation verifier tests (12 tests)
pytest tests/test_sandbox_scorer.py -v                   # Sandbox scorer tests (15 tests)
pytest tests/test_autoresearch.py -v                     # Bayesian Autoresearch tests (33 tests)

# .claude directory governance
python tools/testing/claude_dir_validator.py --json   # Validate .claude config alignment (exit 0 = pass)
python tools/testing/claude_dir_validator.py --human   # Human-readable terminal output
python tools/testing/claude_dir_validator.py --check append-only --json  # Single check

# Health check
python tools/testing/health_check.py                 # Full system health check
python tools/testing/health_check.py --json           # JSON output

# Production readiness audit (38 checks, 7 categories)
python tools/testing/production_audit.py --human --stream              # Full audit with streaming
python tools/testing/production_audit.py --json                        # JSON output
python tools/testing/production_audit.py --category security --json    # Single category
python tools/testing/production_audit.py --category security,compliance --json  # Multiple categories
python tools/testing/production_audit.py --gate --json                 # Gate evaluation (exit code 0=pass, 1=fail)
pytest tests/test_production_audit.py -v             # Production audit tests (25 tests)

# Production remediation (auto-fix audit blockers)
python tools/testing/production_remediate.py --human --stream              # Auto-fix + stream
python tools/testing/production_remediate.py --auto --json                 # Auto-fix all (JSON)
python tools/testing/production_remediate.py --dry-run --human --stream    # Preview fixes
python tools/testing/production_remediate.py --check-id SEC-002 --auto     # Single check
python tools/testing/production_remediate.py --skip-audit --auto --json    # Reuse latest audit
pytest tests/test_production_remediate.py -v          # Remediation tests (25 tests)

# Test orchestrator (full pipeline: unit + BDD + E2E + gates)
python tools/testing/test_orchestrator.py --project-dir /path/to/project
python tools/testing/test_orchestrator.py --project-dir /path --skip-e2e --project-id "proj-123"
python tools/testing/test_orchestrator.py --project-dir /path --skip-sandbox  # Skip LLM sandbox isolation (D-SEC-11)

# E2E tests (Playwright MCP)
python tools/testing/e2e_runner.py --discover         # List available E2E test specs
python tools/testing/e2e_runner.py --run-all           # Execute all E2E tests
python tools/testing/e2e_runner.py --test-file .claude/commands/e2e/dashboard_health.md
python tools/testing/e2e_runner.py --run-all --validate-screenshots    # E2E + vision validation
python tools/testing/e2e_runner.py --run-all --validate-screenshots --vision-strict  # Vision failures = test failures

# Screenshot validation (vision LLM — Ollama LLaVA / Claude / GPT-4o)
python tools/testing/screenshot_validator.py --check --json                           # Check vision model availability
python tools/testing/screenshot_validator.py --image screenshot.png --assert "CUI banner is visible" --json
python tools/testing/screenshot_validator.py --batch-dir .tmp/test_runs/screenshots/ --json
```

---

## Modular Installation (Phase 33)
```bash
# Interactive wizard — guided setup
python tools/installer/installer.py --interactive

# Profile-based — use a pre-built bundle
python tools/installer/installer.py --profile dod_team --compliance fedramp_high,cmmc --platform k8s
python tools/installer/installer.py --profile isv_startup --platform docker
python tools/installer/installer.py --profile healthcare --compliance hipaa,hitrust

# Add features to existing installation
python tools/installer/installer.py --add-module marketplace
python tools/installer/installer.py --add-compliance hipaa
python tools/installer/installer.py --upgrade                   # Show what can be added

# Status and validation
python tools/installer/installer.py --status --json
python tools/installer/module_registry.py --validate
python tools/installer/compliance_configurator.py --list-postures

# Platform artifact generation
python tools/installer/platform_setup.py --generate docker --modules core,llm,builder,dashboard
python tools/installer/platform_setup.py --generate k8s-rbac --modules core,builder
python tools/installer/platform_setup.py --generate env --modules core,llm
python tools/installer/platform_setup.py --generate helm-values --modules core,llm,builder
```

---

## ICDEV™ Commands
```bash
# Database
python tools/db/init_icdev_db.py                    # Initialize ICDEV™ database (391 tables)

# Database Migrations (D150)
python tools/db/migrate.py --status [--json]                      # Show migration status
python tools/db/migrate.py --up [--target 005] [--dry-run]        # Apply pending migrations
python tools/db/migrate.py --down [--target 003]                  # Roll back migrations
python tools/db/migrate.py --validate [--json]                    # Validate checksums
python tools/db/migrate.py --create "add_feature_table"           # Scaffold new migration (allocates a YYYYMMDDHHMMSS version)
# ALWAYS scaffold with --create. Migration ids are UTC timestamps, not a
# sequence: hand-picking "highest + 1" is a read-modify-write across every
# concurrent session and produced three collisions in one session on
# 2026-08-02, one of which broke main. The legacy 001-341 range is closed.
python tools/db/migrate.py --mark-applied 001                    # Mark existing DB as migrated
python tools/db/migrate.py --up --all-tenants                    # Apply to all tenant DBs

# Shadowed migrations — entries that share a version and therefore never run
python tools/db/migration_versions.py --shadowed --json          # List what is being skipped
# Is a shadowed entry's schema actually MISSING? Replay it against a throwaway
# SQLite db built from the migrations that DO run, and diff the result.
python tools/db/shadowed_migration_replay.py --list
python tools/db/shadowed_migration_replay.py --sample 3          # verdict for the first 3
python tools/db/shadowed_migration_replay.py --migration 010_network_intelligence_schema
python tools/db/shadowed_migration_replay.py --all --json        # all 60, machine-readable
# The baseline takes ~13s to build; --baseline-db caches it across runs.
python tools/db/shadowed_migration_replay.py --all --baseline-db /tmp/base.db
# Verdicts: schema_gap_detected | schema_already_exists | inconclusive.
# SQLite-only oracle — a PG-only entry is inconclusive, never "already exists".
# A gap means the MIGRATION CHAIN lacks the object; canvases that create tables
# at app startup are not modelled, so confirm a declaring source in the tree
# before treating a gap as a defect.
# Duplicate migration versions — a same-version sibling is skipped SILENTLY
python tools/db/migration_versions.py --json                     # Report all duplicates
python tools/db/migration_versions.py --gate                     # Exit 1 on new duplicates OR unexplained allowlist entries
python tools/db/migration_versions.py --shadowed --json          # What is being skipped

# Is a grandfathered collision actually harmless? (mvs-audit-03)
# Classifies every shadowed migration by REBUILDING both backends from empty.
# Without the two oracles below it falls back to source attribution alone.
python tools/db/shadowed_migration_audit.py                      # Human summary
python tools/db/shadowed_migration_audit.py --gaps               # Only entries needing action
python tools/db/shadowed_migration_audit.py --json \
    --fresh-db /path/to/fresh.db \
    --fresh-pg-dsn postgresql://user:pw@host:5432/scratch_db
# Build the oracles first:
#   SQLite : ICDEV_STORAGE_BACKEND=sqlite ICDEV_DB_PATH=<path> python tools/db/init_icdev_db.py
#            python tools/db/migrate.py --up --converge --db-path <path>
#   PG     : ICDEV_DATABASE_URL=<dsn> python tools/db/bootstrap_pg.py
#            ICDEV_DATABASE_URL=<dsn> python tools/db/migrate.py --up --converge
# NOTE: ICDEV_DATABASE_URL outranks ICDEV_PG_DATABASE in storage._get_pg_pool.
# Exporting only ICDEV_PG_DATABASE in an environment that already sets the URL
# silently keeps the OLD target — the connection succeeds and addresses the
# wrong database.

# Database Backup/Restore (D152)
python tools/db/backup.py --backup [--db icdev] [--json]         # Backup single database
python tools/db/backup.py --backup --all [--json]                # Backup all databases
python tools/db/backup.py --backup --tenants [--slug acme]       # Backup tenant databases
python tools/db/backup.py --restore --backup-file path/to/bak    # Restore from backup
python tools/db/backup.py --verify --backup-file path/to/bak     # Verify backup integrity
python tools/db/backup.py --list [--json]                        # List available backups
python tools/db/backup.py --prune [--retention-days 30]          # Remove old backups

# Audit trail (append-only, NIST AU compliant)
python tools/audit/audit_logger.py --event-type "code.commit" --actor "builder-agent" --action "Committed module X" --project-id "proj-123"
python tools/audit/audit_query.py --project "proj-123" --format json
python tools/audit/decision_recorder.py --project-id "proj-123" --decision "Use PostgreSQL" --rationale "RDS requirement" --actor "architect-agent"

# Audit hash-chain integrity sweep (exa-audit-04) — whole table, not one row at a time.
# Buckets every row: verified / pre-cutover (unverifiable, NOT tampered) /
# unchained (post-cutover, writer bypassed) / BROKEN (the tamper signal).
python tools/audit/chain_sweep.py                      # human-readable summary
python tools/audit/chain_sweep.py --json               # full report incl. broken samples + links
python tools/audit/chain_sweep.py --gate               # exit 1 if any link is broken
python tools/audit/chain_sweep.py --verify-signatures  # also verify each signature (slower)
python tools/audit/chain_sweep.py --db-path /path/to/evidence.db   # sweep an evidence copy
# Same data on the dashboard: /provenance -> "Audit Chain Integrity",
# API: GET /api/govchain-provenance/chain-health
# Scheduled: rides the genesis `audit` reflex (args/genesis_config.yaml -> reflexes.audit.checks)

# PreToolUse hook — per-check fire-rate survey (exa-bench-05)
# Replays the tool calls of recent sessions through every check in
# .claude/hooks/pre_tool_use.py and counts what each one would refuse. Run this
# BEFORE changing a check or its enforcement posture — a check enabled without a
# measurement is how nine of them stayed advisory behind `|| true`.
python tools/hooks/fire_rate_survey.py --json
python tools/hooks/fire_rate_survey.py --markdown --since-days 30 --project ICDev
python tools/hooks/fire_rate_survey.py --check env_file_access --samples 25
python tools/hooks/fire_rate_survey.py --live-git          # evaluate branch_deletion for real
python tools/hooks/fire_rate_survey.py --gate --max-fire-rate 0.01   # exit 1 above 1%
# Corpus is the Claude Code transcripts (~/.claude/projects/**/*.jsonl) — the only
# source that carries the OPERANDS. hook_events persists tool-input key names
# only, and is reported unusable rather than contributing a misleading zero.
# Enforcement switches (read by the hook, not by this tool):
#   ICDEV_PRETOOLUSE_ENFORCE=0   all nine checks report but never refuse
#   ICDEV_<CHECK>_GUARD=0        skip one check — see CHECK_KILL_SWITCHES

# MCP servers (stdio transport)
python tools/mcp/unified_server.py                   # Start unified MCP gateway (251 tools, recommended)
python tools/mcp/core_server.py                     # Start core MCP server
python tools/mcp/compliance_server.py               # Start compliance MCP server
python tools/mcp/builder_server.py                  # Start builder MCP server
python tools/mcp/infra_server.py                    # Start infra MCP server
python tools/mcp/knowledge_server.py                # Start knowledge MCP server
python tools/mcp/maintenance_server.py              # Start maintenance MCP server
python tools/mcp/mbse_server.py                    # Start MBSE MCP server
python tools/mcp/requirements_server.py            # Start Requirements MCP server
python tools/mcp/supply_chain_server.py            # Start Supply Chain MCP server
python tools/mcp/simulation_server.py              # Start Simulation MCP server
python tools/mcp/integration_server.py             # Start Integration MCP server
python tools/mcp/research_server.py                # Start Research MCP server

# Requirements Intake (RICOAS)
python tools/requirements/intake_engine.py --project-id "proj-123" --customer-name "Jane Smith" --customer-org "DoD PEO" --impact-level IL5 --json  # New session
python tools/requirements/intake_engine.py --session-id "<id>" --message "We need a mission planning tool" --json                                   # Process turn
python tools/requirements/intake_engine.py --session-id "<id>" --resume --json                                                                       # Resume session
python tools/requirements/intake_engine.py --session-id "<id>" --export --json                                                                       # Export requirements
python tools/requirements/gap_detector.py --session-id "<id>" --check-security --check-compliance --json                                             # Detect gaps
python tools/requirements/readiness_scorer.py --session-id "<id>" --json                                                                             # Score readiness
python tools/requirements/decomposition_engine.py --session-id "<id>" --level story --generate-bdd --json                                            # SAFe decomposition
python tools/requirements/document_extractor.py --session-id "<id>" --upload --file-path /path/to/sow.pdf --document-type sow --json                 # Upload document
python tools/requirements/document_extractor.py --session-id "<id>" --upload --file-path /path/to/whiteboard.png --document-type attachment --json    # Upload image (auto-classified)
python tools/requirements/document_extractor.py --document-id "<id>" --extract --json                                                                 # Extract requirements
python tools/requirements/document_extractor.py --document-id "<id>" --classify --json                                                                # Classify image document

# Spec-Kit Patterns (D156-D161)
python tools/requirements/spec_quality_checker.py --spec-file specs/3-dashboard-kanban/spec.md --json                                                   # Quality check
python tools/requirements/spec_quality_checker.py --spec-file specs/foo.md --annotate --output specs/foo.annotated.md                                    # Annotate with [NEEDS CLARIFICATION]
python tools/requirements/spec_quality_checker.py --spec-dir specs/ --json                                                                               # Batch check all specs
python tools/requirements/consistency_analyzer.py --spec-file specs/3-dashboard-kanban/spec.md --json                                                    # Cross-artifact consistency
python tools/requirements/consistency_analyzer.py --spec-dir specs/ --json                                                                                # Batch consistency check
python tools/requirements/constitution_manager.py --project-id "proj-123" --load-defaults --json                                                         # Load DoD default principles
python tools/requirements/constitution_manager.py --project-id "proj-123" --list --json                                                                  # List project principles
python tools/requirements/constitution_manager.py --project-id "proj-123" --validate --spec-file specs/foo.md --json                                     # Validate spec vs constitution
python tools/requirements/clarification_engine.py --spec-file specs/foo.md --max-questions 5 --json                                                      # Prioritized clarification questions
python tools/requirements/clarification_engine.py --session-id "<id>" --max-questions 5 --json                                                           # Session-based clarification
python tools/requirements/spec_organizer.py --init --issue 3 --slug "dashboard-kanban" --json                                                            # Init spec directory
python tools/requirements/spec_organizer.py --migrate --spec-file specs/issue-3-foo.md --json                                                            # Migrate flat spec to directory
python tools/requirements/spec_organizer.py --migrate-all --json                                                                                          # Migrate all flat specs
python tools/requirements/spec_organizer.py --list --json                                                                                                 # List all spec directories
python tools/requirements/spec_organizer.py --register --spec-dir specs/3-dashboard-kanban/ --project-id "proj-123" --json                               # Register spec in DB
python tools/requirements/decomposition_engine.py --session-id "<id>" --annotate-parallel --json                                                          # Detect parallel task groups

# ATO Boundary Impact (RICOAS Phase 2)
python tools/requirements/boundary_analyzer.py --project-id "proj-123" --register-system --system-name "My System" --ato-status active --classification CUI --impact-level IL5 --json
python tools/requirements/boundary_analyzer.py --project-id "proj-123" --system-id "<id>" --requirement-id "<id>" --json                              # Assess boundary impact
python tools/requirements/boundary_analyzer.py --project-id "proj-123" --generate-alternatives --assessment-id "<id>" --json                          # RED alternative COAs
python tools/requirements/boundary_analyzer.py --project-id "proj-123" --list-assessments --tier RED --json                                           # List RED items

# Supply Chain Intelligence (RICOAS Phase 2)
python tools/supply_chain/dependency_graph.py --project-id "proj-123" --add-vendor --vendor-name "Vendor X" --vendor-type software --country US --json
python tools/supply_chain/dependency_graph.py --project-id "proj-123" --build-graph --json                                                            # Build dependency graph
python tools/supply_chain/dependency_graph.py --project-id "proj-123" --impact "component-name" --impact-type vulnerability --severity critical --json # Impact propagation
python tools/supply_chain/isa_manager.py --project-id "proj-123" --expiring --days 90 --json                                                          # Expiring ISAs
python tools/supply_chain/isa_manager.py --project-id "proj-123" --review-due --json                                                                   # Review overdue ISAs
python tools/supply_chain/scrm_assessor.py --project-id "proj-123" --vendor-id "<id>" --json                                                           # SCRM vendor assessment
python tools/supply_chain/scrm_assessor.py --project-id "proj-123" --aggregate --json                                                                   # Project-wide SCRM
python tools/supply_chain/cve_triager.py --project-id "proj-123" --triage --cve-id CVE-2025-1234 --component openssl --cvss 9.8 --severity critical --json
python tools/supply_chain/cve_triager.py --project-id "proj-123" --sla-check --json                                                                     # CVE SLA compliance
python tools/supply_chain/cve_passive_watcher.py --project-id "proj-123" --scan --json                                                                    # Passive ATO CVE scan (audit log)
python tools/supply_chain/cve_passive_watcher.py --project-id "proj-123" --scan --since-id 0 --json                                                       # Full audit rescan from id=0
python tools/supply_chain/cve_passive_watcher.py --project-id "proj-123" --scan --no-triage --json                                                        # Detection-only (no auto-triage)
python tools/supply_chain/cve_passive_watcher.py --project-id "proj-123" --scan --gate --json                                                             # CI gate — exit 1 on critical/high ATO CVEs
python tools/supply_chain/cve_passive_watcher.py --project-id "proj-123" --status --json                                                                  # Watcher high-watermark & stats
python tools/supply_chain/cve_passive_watcher.py --project-id "proj-123" --watch --interval 60 --json                                                     # Continuous polling (60s)

# Digital Program Twin Simulation (RICOAS Phase 3)
python tools/simulation/simulation_engine.py --project-id "proj-123" --create-scenario --scenario-name "Add auth module" --scenario-type what_if --modifications '{"add_requirements": 3}' --json
python tools/simulation/simulation_engine.py --scenario-id "<id>" --run --dimensions all --json                                                       # Run 6-dimension simulation
python tools/simulation/monte_carlo.py --scenario-id "<id>" --dimension schedule --iterations 10000 --json                                            # Monte Carlo schedule
python tools/simulation/monte_carlo.py --scenario-id "<id>" --dimension cost --iterations 5000 --json                                                 # Monte Carlo cost
python tools/simulation/coa_generator.py --session-id "<id>" --generate-3-coas --simulate --json                                                      # Generate 3 COAs with simulation
python tools/simulation/coa_generator.py --session-id "<id>" --compare --json                                                                          # Compare COAs
python tools/simulation/coa_generator.py --session-id "<id>" --generate-alternative --requirement-id "<id>" --json                                     # RED alternative COAs
python tools/simulation/coa_generator.py --coa-id "<id>" --select --selected-by "Jane Smith" --rationale "Best balance" --json                        # Select COA
python tools/simulation/scenario_manager.py --scenario-id "<id>" --fork --new-name "Variant B" --json                                                  # Fork scenario
python tools/simulation/scenario_manager.py --compare --scenario-ids "<id1>,<id2>" --json                                                              # Compare scenarios

# External Integration (RICOAS Phase 4)
python tools/integration/jira_connector.py --project-id "proj-123" --configure --instance-url "https://org.atlassian.net" --json       # Configure Jira
python tools/integration/jira_connector.py --project-id "proj-123" --push --json                                                       # Push to Jira
python tools/integration/jira_connector.py --project-id "proj-123" --pull --json                                                       # Pull from Jira
python tools/integration/jira_connector.py --project-id "proj-123" --analyze-attachments --attachment-paths "img1.png,img2.jpg" --json    # Analyze Jira image attachments
python tools/integration/servicenow_connector.py --project-id "proj-123" --configure --instance-url "https://org.service-now.com" --json  # Configure ServiceNow
python tools/integration/servicenow_connector.py --project-id "proj-123" --push --json                                                    # Push to ServiceNow
python tools/integration/servicenow_connector.py --project-id "proj-123" --analyze-attachments --attachment-paths "img1.png" --json       # Analyze ServiceNow attachments
python tools/integration/gitlab_connector.py --project-id "proj-123" --configure --instance-url "https://gitlab.org.mil" --json           # Configure GitLab
python tools/integration/gitlab_connector.py --project-id "proj-123" --push --json                                                         # Push to GitLab
python tools/integration/gitlab_connector.py --project-id "proj-123" --pull --json                                                         # Pull from GitLab
python tools/integration/doors_exporter.py --session-id "<id>" --export-reqif --output-path /path/to/output.reqif --json                   # Export ReqIF
python tools/integration/approval_manager.py --session-id "<id>" --submit requirements_package --json                                       # Submit for approval
python tools/integration/approval_manager.py --workflow-id "<id>" --review --decision approved --json                                       # Review approval
python tools/requirements/traceability_builder.py --project-id "proj-123" --build-rtm --gap-analysis --json                                 # Build full RTM

# Observability & Agent Execution (Phase 39)
python tools/agent/agent_executor.py --prompt "echo hello" --model sonnet --json           # Execute agent via CLI
python tools/agent/agent_executor.py --prompt "fix tests" --model opus --max-retries 3     # With retry logic

# NLQ Compliance Queries (Phase 40)
# Start dashboard first: python tools/dashboard/app.py
# Navigate to /query for natural language compliance queries
# Navigate to /events for real-time event timeline (SSE)

# Git Worktree Parallel CI/CD (Phase 41)
python tools/ci/modules/worktree.py --create --task-id test-123 --target-dir src/ --json    # Create worktree
python tools/ci/modules/worktree.py --list --json                                            # List worktrees
python tools/ci/modules/worktree.py --cleanup --worktree-name icdev-test-123                # Cleanup worktree
python tools/ci/modules/worktree.py --status --worktree-name icdev-test-123                 # Worktree status

# Manifest merge rehearsal (kax-conflict-03) — measures which tools/manifest/ layout survives
# two unrelated tasks each registering a new tool under the same topic
python tools/git/manifest_merge_rehearsal.py                              # all layouts, both merge paths
python tools/git/manifest_merge_rehearsal.py --json                       # machine-readable
python tools/git/manifest_merge_rehearsal.py --layout union --branches 5  # one layout, 5 concurrent branches
python tools/git/manifest_merge_rehearsal.py --mode merge-tree            # bare, forge-style server-side merge only
python tools/git/manifest_merge_rehearsal.py --repo .                     # rehearse against a CLONE of this repo + the real shard
python tools/git/manifest_merge_rehearsal.py --repo . --shard tools/manifest/browser.md

# GitLab Task Board Monitor (Phase 41)
python tools/ci/triggers/gitlab_task_monitor.py                    # Start monitor (polls every 20s)
python tools/ci/triggers/gitlab_task_monitor.py --dry-run          # Preview without spawning
python tools/ci/triggers/gitlab_task_monitor.py --once             # Single poll and exit

# Project management
python tools/project/project_create.py --name "my-app" --type microservice
python tools/project/project_list.py
python tools/project/project_status.py --project-id "proj-123"

# Three-Tier DX (D189-D193)
python tools/project/manifest_loader.py --dir /path --json                                          # Parse + validate icdev.yaml
python tools/project/manifest_loader.py --file /path/icdev.yaml --validate                          # Validate manifest
python tools/project/validate_manifest.py --file icdev.yaml --json                                  # Thin validate CLI
python tools/project/session_context_builder.py --format markdown                                   # Build session context (Tier 2)
python tools/project/session_context_builder.py --json                                              # JSON output
python tools/project/session_context_builder.py --init --json                                       # Register project from icdev.yaml
python tools/ci/pipeline_config_generator.py --dir /path --platform auto --dry-run --json           # Preview CI/CD config (Tier 1)
python tools/ci/pipeline_config_generator.py --dir /path --platform github --write                  # Generate GitHub Actions
python tools/ci/pipeline_config_generator.py --dir /path --platform gitlab --write                  # Generate GitLab CI

# Compliance
python tools/compliance/ssp_generator.py --project-id "proj-123"
python tools/compliance/poam_generator.py --project-id "proj-123"
python tools/compliance/stig_checker.py --project-id "proj-123"
python tools/compliance/sbom_generator.py --project-id "proj-123"
python tools/compliance/dependency_resolver.py --project-dir "/path/to/project" --json
python tools/compliance/cui_marker.py --file "/path/to/file" --marking "CUI // SP-CTI"
python tools/compliance/nist_lookup.py --control "AC-2"
python tools/compliance/control_mapper.py --activity "code.commit" --project-id "proj-123"

# CSSP Compliance (DI 8530.01)
python tools/compliance/cssp_assessor.py --project-id "proj-123" --functional-area all
python tools/compliance/cssp_report_generator.py --project-id "proj-123"
python tools/compliance/incident_response_plan.py --project-id "proj-123"
python tools/compliance/siem_config_generator.py --project-dir "/path/to/project" --targets splunk elk
python tools/compliance/cssp_evidence_collector.py --project-id "proj-123" --project-dir "/path"

# Xacta 360 Integration
python tools/compliance/xacta/xacta_sync.py --project-id "proj-123" --mode hybrid
python tools/compliance/xacta/xacta_export.py --project-id "proj-123" --format oscal

# Secure by Design (CISA SbD + DoDI 5000.87)
python tools/compliance/sbd_assessor.py --project-id "proj-123" --domain all
python tools/compliance/sbd_report_generator.py --project-id "proj-123"

# IV&V (IEEE 1012)
python tools/compliance/ivv_assessor.py --project-id "proj-123" --process-area all
python tools/compliance/ivv_report_generator.py --project-id "proj-123"
python tools/compliance/traceability_matrix.py --project-id "proj-123" --project-dir "/path"

# Multi-Framework Compliance (Phase 17)
python tools/compliance/crosswalk_engine.py --control AC-2                             # Crosswalk query
python tools/compliance/crosswalk_engine.py --project-id "proj-123" --coverage          # Coverage across frameworks
python tools/compliance/crosswalk_engine.py --project-id "proj-123" --target fedramp-moderate --gap-analysis
python tools/compliance/classification_manager.py --impact-level IL5                    # Classification markings
python tools/compliance/fedramp_assessor.py --project-id "proj-123" --baseline moderate # FedRAMP assessment
python tools/compliance/fedramp_report_generator.py --project-id "proj-123"             # FedRAMP report
python tools/compliance/cmmc_assessor.py --project-id "proj-123" --level 2              # CMMC assessment
python tools/compliance/cmmc_report_generator.py --project-id "proj-123"                # CMMC report
python tools/compliance/oscal_generator.py --project-id "proj-123" --artifact ssp       # OSCAL generation
python tools/compliance/oscal_generator.py --project-id "proj-123" --deep-validate /path/to/ssp.oscal.json --json  # Deep validation (D302-D305)

# OSCAL Ecosystem Tools (D302-D306)
python tools/compliance/oscal_tools.py --detect --json                                  # Check oscal-cli, oscal-pydantic, NIST catalog availability
python tools/compliance/oscal_tools.py --validate /path/to/ssp.oscal.json --json        # 3-layer deep validation
python tools/compliance/oscal_tools.py --convert /path/to/ssp.json --output-format xml  # Format conversion (requires oscal-cli)
python tools/compliance/oscal_tools.py --resolve-profile /path/to/profile.json --json   # Profile resolution (requires oscal-cli)
python tools/compliance/oscal_tools.py --catalog-lookup AC-2 --json                     # Look up control from NIST catalog
python tools/compliance/oscal_tools.py --catalog-list --family AC --json                # List controls by family
python tools/compliance/oscal_tools.py --catalog-stats --json                           # Catalog statistics
python tools/compliance/oscal_catalog_adapter.py --lookup AC-2 --json                   # Direct catalog adapter CLI
python tools/compliance/oscal_catalog_adapter.py --stats --json                         # Catalog source info

python tools/compliance/emass/emass_sync.py --project-id "proj-123" --mode hybrid       # eMASS sync
python tools/compliance/emass/emass_export.py --project-id "proj-123" --type controls   # eMASS export
python tools/compliance/cato_monitor.py --project-id "proj-123" --check-freshness       # cATO monitoring
python tools/compliance/cato_scheduler.py --project-id "proj-123" --run-due             # cATO scheduling
python tools/compliance/pi_compliance_tracker.py --project-id "proj-123" --velocity     # PI tracking

# FIPS 199/200 Security Categorization (Phase 20)
python tools/compliance/fips199_categorizer.py --list-catalog                                          # Browse SP 800-60 types
python tools/compliance/fips199_categorizer.py --list-catalog --category D.1 --json                    # Filter by category
python tools/compliance/fips199_categorizer.py --project-id "proj-123" --add-type "D.1.1.1"            # Add info type
python tools/compliance/fips199_categorizer.py --project-id "proj-123" --add-type "D.2.3.4" --adjust-c High  # Add with adjustment
python tools/compliance/fips199_categorizer.py --project-id "proj-123" --list-types --json             # List assigned types
python tools/compliance/fips199_categorizer.py --project-id "proj-123" --categorize --json             # Run categorization
python tools/compliance/fips199_categorizer.py --project-id "proj-123" --categorize --method cnssi_1253  # Force CNSSI 1253
python tools/compliance/fips199_categorizer.py --project-id "proj-123" --gate                          # Evaluate gate
python tools/compliance/fips200_validator.py --project-id "proj-123" --json                            # Validate 17 areas
python tools/compliance/fips200_validator.py --project-id "proj-123" --gate --json                     # Gate evaluation

# Universal Compliance Platform (Phase 23)
python tools/compliance/universal_classification_manager.py --list-categories                                   # List all data categories
python tools/compliance/universal_classification_manager.py --banner CUI PHI --json                            # Composite banner (CUI + PHI)
python tools/compliance/universal_classification_manager.py --code-header CUI PCI --language python            # Composite code header
python tools/compliance/universal_classification_manager.py --detect --project-id "proj-123" --json            # Auto-detect data categories
python tools/compliance/universal_classification_manager.py --add-category --project-id "proj-123" --category PHI  # Add data category
python tools/compliance/universal_classification_manager.py --validate --project-id "proj-123" --json          # Validate markings
python tools/compliance/compliance_detector.py --project-id "proj-123" --json                                  # Detect applicable frameworks
python tools/compliance/compliance_detector.py --project-id "proj-123" --apply --json                          # Detect + store in DB
python tools/compliance/compliance_detector.py --project-id "proj-123" --confirm --json                        # Confirm all detected
python tools/compliance/multi_regime_assessor.py --project-id "proj-123" --json                                # Assess all frameworks
python tools/compliance/multi_regime_assessor.py --project-id "proj-123" --gate                                # Multi-regime gate check
python tools/compliance/multi_regime_assessor.py --project-id "proj-123" --minimal-controls --json             # Prioritized control list
python tools/compliance/cjis_assessor.py --project-id "proj-123" --json                                        # CJIS assessment
python tools/compliance/hipaa_assessor.py --project-id "proj-123" --json                                       # HIPAA assessment
python tools/compliance/hitrust_assessor.py --project-id "proj-123" --json                                     # HITRUST assessment
python tools/compliance/soc2_assessor.py --project-id "proj-123" --json                                        # SOC 2 assessment
python tools/compliance/pci_dss_assessor.py --project-id "proj-123" --json                                     # PCI DSS assessment
python tools/compliance/iso27001_assessor.py --project-id "proj-123" --json                                    # ISO 27001 assessment
python tools/compliance/cjis_assessor.py --project-id "proj-123" --gate                                        # CJIS gate check
python tools/compliance/hipaa_assessor.py --project-id "proj-123" --gate                                       # HIPAA gate check

# MBSE Integration (Phase 18)
python tools/mbse/xmi_parser.py --project-id "proj-123" --file /path/model.xmi --json     # Import SysML XMI
python tools/mbse/reqif_parser.py --project-id "proj-123" --file /path/reqs.reqif --json   # Import DOORS ReqIF
python tools/mbse/digital_thread.py --project-id "proj-123" auto-link --json               # Auto-link thread
python tools/mbse/digital_thread.py --project-id "proj-123" coverage --json                # Thread coverage
python tools/mbse/digital_thread.py --project-id "proj-123" report --json                  # Thread report
python tools/mbse/model_code_generator.py --project-id "proj-123" --language python --output ./src  # Generate code
python tools/mbse/model_control_mapper.py --project-id "proj-123" --map-all --json         # Map to NIST controls
python tools/mbse/sync_engine.py --project-id "proj-123" detect-drift --json               # Detect model-code drift
python tools/mbse/sync_engine.py --project-id "proj-123" sync-model-to-code --json         # Sync model→code
python tools/mbse/des_assessor.py --project-id "proj-123" --project-dir /path --json       # DES assessment
python tools/mbse/des_report_generator.py --project-id "proj-123" --output-dir /path       # DES report
python tools/mbse/pi_model_tracker.py --project-id "proj-123" --pi PI-25.1 --snapshot      # PI snapshot
python tools/mbse/diagram_extractor.py --image diagram.png --diagram-type block_definition --project-id "proj-123" --json   # Extract SysML from screenshot
python tools/mbse/diagram_extractor.py --image diagram.png --validate --project-id "proj-123" --json                        # Validate against existing model
python tools/mbse/diagram_extractor.py --image diagram.png --diagram-type block_definition --store --project-id "proj-123" --json  # Extract + store in DB

# Builder (TDD workflow — 6 languages)
python tools/builder/test_writer.py --feature "user auth" --project-dir "/path" --language python
python tools/builder/code_generator.py --test-file "/path/to/test.py" --project-dir "/path" --language java
python tools/builder/scaffolder.py --type java-backend --name "my-service"
python tools/builder/language_support.py --detect "/path/to/project"    # Detect languages
python tools/builder/language_support.py --list                          # List supported languages
python tools/builder/linter.py --project-dir "/path"
python tools/builder/formatter.py --project-dir "/path"

# Dev Profiles & Personalization (Phase 34)
python tools/builder/dev_profile_manager.py --scope tenant --scope-id "tenant-abc" --create --template dod_baseline --json       # Create from template
python tools/builder/dev_profile_manager.py --scope tenant --scope-id "tenant-abc" --create --data '{"language":{"primary":"go"}}' --created-by "admin" --json  # Create explicit
python tools/builder/dev_profile_manager.py --scope tenant --scope-id "tenant-abc" --get --json                                  # Get current profile
python tools/builder/dev_profile_manager.py --scope tenant --scope-id "tenant-abc" --get --version 2 --json                     # Get specific version
python tools/builder/dev_profile_manager.py --scope project --scope-id "proj-123" --resolve --json                               # Resolve 5-layer cascade
python tools/builder/dev_profile_manager.py --scope tenant --scope-id "tenant-abc" --update --changes '{"style":{"line_length":120}}' --change-summary "Update line length" --updated-by "admin" --json  # Update (new version)
python tools/builder/dev_profile_manager.py --scope tenant --scope-id "tenant-abc" --lock --dimension-path "security" --lock-role isso --locked-by "isso@mil" --json   # Lock dimension
python tools/builder/dev_profile_manager.py --scope tenant --scope-id "tenant-abc" --unlock --dimension-path "security" --unlocked-by "isso@mil" --role isso --json    # Unlock dimension
python tools/builder/dev_profile_manager.py --scope tenant --scope-id "tenant-abc" --diff --v1 1 --v2 3 --json                   # Diff versions
python tools/builder/dev_profile_manager.py --scope tenant --scope-id "tenant-abc" --rollback --target-version 1 --rolled-back-by "admin" --json  # Rollback (creates new version)
python tools/builder/dev_profile_manager.py --scope project --scope-id "proj-123" --inject --task-type code_generation --json    # LLM injection context
python tools/builder/dev_profile_manager.py --scope tenant --scope-id "tenant-abc" --history --json                              # Version history
python tools/builder/profile_detector.py --repo-path /path/to/repo --json                    # Auto-detect from repo
python tools/builder/profile_detector.py --text "We use Go, snake_case, 120-char lines" --json  # Detect from text
python tools/builder/profile_md_generator.py --scope project --scope-id "proj-123" --json     # Generate PROFILE.md
python tools/builder/profile_md_generator.py --scope project --scope-id "proj-123" --output /path/PROFILE.md --store  # Generate + store in DB
python tools/builder/cursor_profile_generator.py --scope project --scope-id "proj-123" --format cursorrules                    # Export to .cursorrules
python tools/builder/cursor_profile_generator.py --scope project --scope-id "proj-123" --format mdc --output .cursor/rules/icdev.mdc  # Export to .mdc
python tools/builder/cursor_profile_importer.py --scan .cursor/rules/ --json                                              # Scan Cursor rules
python tools/builder/cursor_profile_importer.py --scan .cursor/rules/ --create --scope platform --scope-id cursor-default --json   # Seed profile from Cursor

# Universal AI Coding Companion (D194-D198)
python tools/dx/companion.py --setup --write                              # Auto-detect tools + generate all configs
python tools/dx/companion.py --setup --all --write                        # All 10 platforms
python tools/dx/companion.py --setup --platforms codex,cursor --write     # Specific platforms
python tools/dx/companion.py --detect --json                              # Detect installed AI tools
python tools/dx/companion.py --sync --write                               # Regenerate after changes
python tools/dx/companion.py --list --json                                # List all supported platforms
python tools/dx/tool_detector.py --json                                   # Detect AI tools (env, config dirs, files)
python tools/dx/instruction_generator.py --all --write --json             # Generate instruction files
python tools/dx/mcp_config_generator.py --all --write --json              # Generate MCP configs from .mcp.json
python tools/dx/skill_translator.py --all --write --json                  # Translate skills to all platforms
python tools/dx/skill_translator.py --list                                # List available Claude Code skills

# Maintenance Audit
python tools/maintenance/dependency_scanner.py --project-id "proj-123"           # Scan all deps
python tools/maintenance/vulnerability_checker.py --project-id "proj-123"        # Check CVEs
python tools/maintenance/maintenance_auditor.py --project-id "proj-123"          # Full audit + score
python tools/maintenance/remediation_engine.py --project-id "proj-123" --dry-run # Preview fixes
python tools/maintenance/remediation_engine.py --project-id "proj-123" --auto    # Auto-fix

# Application Modernization (7Rs Migration)
python tools/modernization/legacy_analyzer.py --project-id "proj-123" --app-id "app-1" --source-path /path/to/legacy   # Analyze legacy app
python tools/modernization/architecture_extractor.py --app-id "app-1" --json                                           # Extract architecture
python tools/modernization/doc_generator.py --app-id "app-1" --output-dir /path/to/docs                               # Generate docs
python tools/modernization/seven_r_assessor.py --project-id "proj-123" --app-id "app-1" --json                         # 7R assessment
python tools/modernization/version_migrator.py --source /path --output /path --language python --from 2.7 --to 3.11    # Version migration
python tools/modernization/framework_migrator.py --source /path --output /path --from struts --to spring-boot          # Framework migration
python tools/modernization/monolith_decomposer.py --app-id "app-1" --target microservices --json                       # Decompose monolith
python tools/modernization/db_migration_planner.py --app-id "app-1" --target postgresql --json                         # DB migration DDL
python tools/modernization/strangler_fig_manager.py --plan-id "plan-1" --status --json                                 # Strangler fig status
python tools/modernization/compliance_bridge.py --plan-id "plan-1" --validate --json                                   # ATO compliance bridge
python tools/modernization/migration_code_generator.py --plan-id "plan-1" --generate-all --output /path                # Generate migration code
python tools/modernization/migration_report_generator.py --app-id "app-1" --type assessment                            # Migration report
python tools/modernization/migration_tracker.py --plan-id "plan-1" --pi PI-25.3 --snapshot --json                      # PI migration tracker
python tools/modernization/ui_analyzer.py --image screenshot.png --json                                                  # Analyze legacy UI screenshot
python tools/modernization/ui_analyzer.py --image-dir /path/to/screenshots/ --app-id "app-1" --project-id "proj-123" --store --json  # Batch analyze + store
python tools/modernization/ui_analyzer.py --image screenshot.png --score-only                                            # Quick complexity score only

# Compliance Diagram Validation (vision-based)
python tools/compliance/diagram_validator.py --image network.png --type network_zone --project-id "proj-123" --json      # Validate network zone diagram
python tools/compliance/diagram_validator.py --image ato_boundary.png --type ato_boundary --expected-components "Web,App,DB" --json  # Validate ATO boundary
python tools/compliance/diagram_validator.py --image dataflow.png --type data_flow --classification CUI --json           # Validate data flow markings
python tools/compliance/diagram_validator.py --image arch.png --type architecture --json                                 # Validate architecture diagram

# Security
python tools/security/sast_runner.py --project-dir "/path"
python tools/security/dependency_auditor.py --project-dir "/path"
python tools/security/secret_detector.py --project-dir "/path"
python tools/security/container_scanner.py --image "my-image:latest"

# AI Security (Phase 37)
python tools/security/prompt_injection_detector.py --text "ignore previous instructions" --json      # Detect prompt injection
python tools/security/prompt_injection_detector.py --file /path/to/file --json                       # Scan file for injections
python tools/security/prompt_injection_detector.py --project-dir /path --gate --json                 # Gate evaluation
python tools/security/ai_telemetry_logger.py --summary --json                                        # AI usage summary
python tools/security/ai_telemetry_logger.py --anomalies --window-hours 24 --json                   # Anomaly detection
python tools/security/atlas_red_team.py --project-id "proj-123" --json                              # Run all ATLAS red team tests (opt-in)
python tools/security/atlas_red_team.py --project-id "proj-123" --technique AML.T0051 --json        # Test specific technique
python tools/compliance/atlas_assessor.py --project-id "proj-123" --json                             # ATLAS compliance assessment
python tools/compliance/atlas_report_generator.py --project-id "proj-123" --json                     # Generate ATLAS compliance report
python tools/compliance/atlas_report_generator.py --project-id "proj-123" --output-path report.md    # Save ATLAS report to file
python tools/security/ai_bom_generator.py --project-id "proj-123" --project-dir . --json             # Generate AI Bill of Materials
python tools/security/ai_bom_generator.py --project-id "proj-123" --gate                             # AI BOM gate check
python tools/compliance/owasp_llm_assessor.py --project-id "proj-123" --json                         # OWASP LLM Top 10 assessment
python tools/compliance/nist_ai_rmf_assessor.py --project-id "proj-123" --json                       # NIST AI RMF assessment
python tools/compliance/iso42001_assessor.py --project-id "proj-123" --json                          # ISO 42001 assessment

# Evolutionary Intelligence (Phase 36)
python tools/registry/child_registry.py --register --name "ChildApp" --type microservice --json      # Register child app
python tools/registry/child_registry.py --list --json                                                 # List children
python tools/registry/genome_manager.py --get --json                                                  # Get current genome version
python tools/registry/genome_manager.py --history --json                                              # Genome version history
python tools/registry/capability_evaluator.py --evaluate --data '{}' --json                           # Evaluate capability
python tools/registry/staging_manager.py --list --json                                                # List staging environments
python tools/registry/propagation_manager.py --list --json                                            # List propagations
python tools/registry/absorption_engine.py --candidates --json                                        # Get absorption candidates
python tools/registry/learning_collector.py --unevaluated --json                                      # Get unevaluated behaviors
python tools/registry/cross_pollinator.py --candidates --json                                         # Find cross-pollination candidates

# Evolution Daemon (D-EVO-1, Phase 36 autonomous lifecycle)
ICDEV_EVOLUTION_ENABLED=true python tools/registry/evolution_daemon.py   # Run as always-on daemon
python tools/registry/evolution_daemon.py --once --json                  # Single pass (run all due reflexes)
python tools/registry/evolution_daemon.py --status --json                # Show status of all 7 reflexes
python tools/registry/evolution_daemon.py --reflex discover --json       # Run one reflex immediately
python tools/registry/evolution_daemon.py --reflex evaluate --json       # Evaluate pending behaviors
python tools/registry/evolution_daemon.py --reflex stage --json          # Create staging environments
python tools/registry/evolution_daemon.py --reflex test --json           # Test active staging envs
python tools/registry/evolution_daemon.py --reflex verify --json         # Check stability windows
python tools/registry/evolution_daemon.py --reflex absorb --json         # Report absorption-ready (HITL)
python tools/registry/evolution_daemon.py --enable discover              # Enable a reflex
python tools/registry/evolution_daemon.py --disable absorb               # Disable a reflex
python tools/registry/evolution_daemon.py --reset test --json            # Reset circuit breaker

# Loop Engineering — GEPA Optimizer & Genesis Daemon Trigger
# GEPA Optimizer — Genome Evolution Pressure Analyzer (gepa-mcp-01)
python tools/skills/gepa_optimizer.py --json                             # Run optimization pass (prune low-fitness genome entries)
python tools/skills/gepa_optimizer.py --dry-run --json                   # Scan without applying writes
# MCP tool: gepa_optimizer
#   Parameters: dry_run (bool, default false) — when true, scan runs but no DB writes are committed.
#   Returns:    {applied: [{capability_id, action, fitness, dry_run}],
#                skipped: [{capability_id, reason, fitness}],
#                errors:  [str]}
#   Handler:    tools/mcp/gap_handlers.py::get_gepa_optimizer_handler
#   Skill:      tools/skills/gepa_optimizer.py::run()
# Genesis daemon 24h trigger — GEPA reflex fires daily via the genesis daemon loop:
#   Config:     args/genesis_config.yaml — add a "gepa_optimizer" entry with interval_seconds: 86400
#   Interval:   86400 s (24 h); controlled by interval_seconds / interval_hours in genesis_config.yaml
#   Enable:     python tools/genesis/daemon.py --enable gepa_optimizer
#   Disable:    python tools/genesis/daemon.py --disable gepa_optimizer
#   Run once:   python tools/genesis/daemon.py --reflex gepa_optimizer --json
#   Thresholds: args/security_gates.yaml → loop_engineering.gepa_min_composite_score (0.60)
#                                           loop_engineering.gepa_min_score_delta (0.05)

# adversarial_verify — Multi-agent adversarial verification for loop-generated outputs
# Spawns N independent skeptic agents, each prompted to REFUTE a finding; result survives
# only when ≥majority agents fail to refute (default threshold: 2 of 3).
# MCP tool: adversarial_verify
#   Parameters: claim (str) — the finding or output to verify
#               agents (int, default 3) — number of skeptic agents to spawn
#               threshold (int, default 2) — minimum non-refuting votes to pass
#               context (str, optional) — supporting context injected into each skeptic prompt
#   Returns:    {survives: bool, votes: int, refuted: int, rationale: str}
#   Handler:    tools/mcp/gap_handlers.py::get_adversarial_verify_handler
# CLI usage (single finding):
python tools/skills/adversarial_verify.py --claim "Finding text here" --agents 3 --json
python tools/skills/adversarial_verify.py --claim "Finding text here" --threshold 2 --json
python tools/skills/adversarial_verify.py --dry-run --json                              # Preview skeptic prompts without spawning agents
# Batch verify (read findings from a JSONL file, one claim per line):
python tools/skills/adversarial_verify.py --batch .tmp/findings.jsonl --json
# Integration: call from workflow scripts via agent() inside pipeline()/parallel() stages
#   const votes = await parallel(Array.from({length: 3}, () => () =>
#     agent(`Try to refute: ${claim}`, {schema: VERDICT})))
#   const survives = votes.filter(Boolean).filter(v => !v.refuted).length >= 2

# --- Loop Engineering Parameters ---
#
# loop_type — Classifies the intent of a workflow loop; gates which required processes
#             the process verifier enforces for that loop (see args/workflow_loop_config.yaml).
#   Column:   workflow_loops.loop_type  (TEXT, DEFAULT 'build')
#   CLI arg:  --loop-type <value>  (tools/workflow/loop_engine.py --create)
#   Valid values:
#     'build'      — Standard feature/implementation loop (default). Enforces security scan,
#                    tests, CUI marking, and compliance check.
#     'compliance' — Compliance-only loop (policy updates, ATO artifacts). Enforces compliance
#                    check and CUI marking; security scan is advisory.
#     'deploy'     — Deployment and release loop. Enforces health check and smoke tests.
#     'fix'        — Bug-fix / hotfix loop. Enforces tests and security scan.
#     'research'   — Research / discovery loop (no deploy gate). No process enforcement;
#                    outputs are informational only.
#     'custom'     — User-defined loop. Required processes are caller-specified via
#                    --boundaries; process verifier skips default enforcement.
#   Example:
python tools/workflow/loop_engine.py --create --project-id "proj-123" --phase "integrate" --loop-type fix --json
python tools/workflow/process_verifier.py --check --project-id "proj-123" --loop-type build --json
#
# adversarial_enabled — Per-task flag that opts a kanban task into the adversarial gate.
#   Column:   kanban_tasks.adversarial_enabled  (INTEGER, DEFAULT 0; 0=off, 1=on)
#   Scope:    Applies only to kanban task dispatch, not to generic workflow loops.
#   Behavior: When adversarial_enabled=1, the kanban scheduler spawns a second Claude CLI
#             subprocess in review-only mode (--max-turns 10) inside a git worktree. The
#             subprocess reads the task title and description (truncated to 1 200 chars) and
#             must emit 'APPROVED:' or 'REJECTED:' on its last non-empty line. Any failure
#             (timeout after 180 s, non-zero exit, unrecognised output) is treated as APPROVED
#             so the gate is never permanently blocking.
#   Pairing:  Conventionally used together with loop_type='non_deterministic' tasks — those
#             whose outputs are non-reproducible and therefore benefit from an independent
#             adversarial review before the result is persisted.
#   Security: The reviewer subprocess is NOT granted --dangerously-allow-filesystem write
#             access or network egress. Re-scope to sandboxed executor if either is added.
#   Set via task_factory (preferred) or kanban CLI:
#     # task_factory: pass adversarial_enabled=True (and loop_type) when seeding tasks
#     from tools.kanban.task_factory import create_tasks
#     create_tasks([{"title": "...", "loop_type": "non_deterministic", "adversarial_enabled": True}])
#     # CLI fallback:
#     python tools/kanban/cli.py --set-field <task-id> adversarial_enabled 1

# Cloud-Agnostic Architecture (Phase 38)
# Cloud Mode Manager (D232)
python tools/cloud/cloud_mode_manager.py --status --json                                               # Current cloud mode and config
python tools/cloud/cloud_mode_manager.py --validate --json                                             # Validate mode against constraints
python tools/cloud/cloud_mode_manager.py --eligible --json                                             # List eligible modes for config
python tools/cloud/cloud_mode_manager.py --check-readiness --json                                      # Check cloud service readiness
# CSP Provider Factory
python -c "from tools.cloud.provider_factory import CSPProviderFactory; f = CSPProviderFactory(); print(f.health_check())"
# CSP Health Check
python tools/cloud/csp_health_checker.py --check --json                                               # Check all CSP services

# CSP Service Monitor (Phase 38 — D239-D241)
python tools/cloud/csp_monitor.py --scan --all --json                                                  # Scan all CSPs for service updates
python tools/cloud/csp_monitor.py --scan --csp aws --json                                              # Scan specific CSP
python tools/cloud/csp_monitor.py --diff --json                                                         # Diff registry vs recent signals (offline)
python tools/cloud/csp_monitor.py --status --json                                                       # Monitor status
python tools/cloud/csp_monitor.py --update-registry --signal-id "sig-xxx" --json                        # Apply signal to registry
python tools/cloud/csp_monitor.py --changelog --days 30 --json                                          # Quick changelog
python tools/cloud/csp_monitor.py --daemon --json                                                       # Continuous monitoring
python tools/cloud/csp_changelog.py --generate --days 30 --json                                         # Full changelog with recommendations
python tools/cloud/csp_changelog.py --generate --format markdown --output .tmp/csp_changelogs/           # Markdown report
python tools/cloud/csp_changelog.py --summary --json                                                    # Summary statistics

# Region Validation (D234)
python tools/cloud/region_validator.py validate --csp aws --region us-gov-west-1 --frameworks fedramp_high,cjis --json
python tools/cloud/region_validator.py eligible --csp azure --frameworks hipaa --json
python tools/cloud/region_validator.py deployment-check --csp aws --region us-gov-west-1 --impact-level IL5 --frameworks hipaa --json
python tools/cloud/region_validator.py list --json
python tools/cloud/region_validator.py list --csp aws --json

# Multi-Cloud Terraform (dispatches to CSP-specific generator)
python tools/infra/terraform_generator.py --project-id "proj-123" --csp azure                         # Generate Azure IaC
python tools/infra/terraform_generator.py --project-id "proj-123" --csp gcp                           # Generate GCP IaC
python tools/infra/terraform_generator.py --project-id "proj-123" --csp oci                           # Generate OCI IaC
# IBM Cloud Terraform (D237)
python tools/infra/terraform_generator_ibm.py --project-id "proj-123" --region us-south --json
# On-Premises Terraform (D236)
python tools/infra/terraform_generator_onprem.py --project-id "proj-123" --target k8s --json
python tools/infra/terraform_generator_onprem.py --project-id "proj-123" --target docker --json

# Cross-Language Translation (Phase 43)
python tools/translation/translation_manager.py \
  --source-path /path/to/source --source-language python --target-language java \
  --output-dir /path/to/output --project-id "proj-123" --validate --json       # Full pipeline
python tools/translation/translation_manager.py \
  --source-path /path --source-language python --target-language java \
  --output-dir /path --project-id "proj-123" --dry-run --json                   # Dry run (no LLM)
python tools/translation/source_extractor.py \
  --source-path /path --language python --output-ir ir.json --project-id "proj-123" --json  # Extract IR only
python tools/translation/code_translator.py \
  --ir-file ir.json --source-language python --target-language go \
  --output-dir /path --candidates 3 --json                                      # Translate with pass@k
python tools/translation/dependency_mapper.py \
  --source-language python --target-language go --imports "flask,requests" --json # Dependency lookup
python tools/translation/test_translator.py \
  --source-test-dir /path/tests --source-language python --target-language java \
  --output-dir /path/output/tests --ir-file ir.json --json                      # Translate tests

# OWASP Agentic AI Security (Phase 45)
python tools/security/ai_telemetry_logger.py --drift --json                                          # Behavioral drift detection
python tools/security/ai_telemetry_logger.py --drift --agent-id "builder-agent" --json               # Drift for specific agent
python tools/security/tool_chain_validator.py --rules --json                                          # List tool chain rules
python tools/security/tool_chain_validator.py --gate --project-id "proj-123" --json                   # Tool chain gate check
python tools/security/agent_output_validator.py --text "some output" --json                           # Validate output text
python tools/security/agent_output_validator.py --gate --project-id "proj-123" --json                 # Output validation gate
python tools/security/agent_trust_scorer.py --score --agent-id "builder-agent" --json                 # Compute trust score
python tools/security/agent_trust_scorer.py --check --agent-id "builder-agent" --json                 # Check agent access
python tools/security/agent_trust_scorer.py --all --json                                              # All agent trust scores
python tools/security/agent_trust_scorer.py --gate --project-id "proj-123" --json                     # Trust scoring gate
python tools/security/mcp_tool_authorizer.py --check --role developer --tool scaffold --json          # Check tool authorization
python tools/security/mcp_tool_authorizer.py --list --role pm --json                                  # List role permissions
python tools/security/mcp_tool_authorizer.py --validate --json                                        # Validate RBAC config (registry-declared since exa-policy-07)
python tools/security/mcp_authz_evidence.py --json                                                    # Is per-tool MCP authz ENFORCED? (behavioural, not file existence)
python tools/security/mcp_authz_evidence.py --gate                                                    # Exit 1 unless a denial actually binds
python tools/security/mcp_authz_evidence.py --gate --allow-monitor                                    # Accept monitor mode as passing
python tools/security/atlas_red_team.py --behavioral --json                                           # Run behavioral red team tests
python tools/security/atlas_red_team.py --behavioral --brt-technique BRT-001 --json                   # Test specific technique
python tools/compliance/owasp_agentic_assessor.py --project-id "proj-123" --json                      # OWASP Agentic assessment
python tools/compliance/owasp_agentic_assessor.py --project-id "proj-123" --gate                      # OWASP Agentic gate

# AI Transparency & Accountability (Phase 48)
python tools/compliance/ai_inventory_manager.py --project-id "proj-123" --register --name "Claude Sonnet" --json
python tools/compliance/ai_inventory_manager.py --project-id "proj-123" --list --json
python tools/compliance/ai_inventory_manager.py --project-id "proj-123" --export --json
python tools/compliance/model_card_generator.py --project-id "proj-123" --model-name "claude-sonnet" --json
python tools/compliance/system_card_generator.py --project-id "proj-123" --json
python tools/compliance/fairness_assessor.py --project-id "proj-123" --json
python tools/compliance/fairness_assessor.py --project-id "proj-123" --gate
python tools/security/confabulation_detector.py --project-id "proj-123" --check-output "text" --json
python tools/security/confabulation_detector.py --project-id "proj-123" --summary --json
python tools/compliance/gao_evidence_builder.py --project-id "proj-123" --json
python tools/compliance/ai_transparency_audit.py --project-id "proj-123" --json
python tools/compliance/ai_transparency_audit.py --project-id "proj-123" --human
python tools/compliance/omb_m25_21_assessor.py --project-id "proj-123" --json
python tools/compliance/omb_m26_04_assessor.py --project-id "proj-123" --json
python tools/compliance/nist_ai_600_1_assessor.py --project-id "proj-123" --json
python tools/compliance/gao_ai_assessor.py --project-id "proj-123" --json

# AI Accountability (Phase 49)
python tools/compliance/accountability_manager.py --project-id "proj-123" --summary --json                    # Accountability summary
python tools/compliance/accountability_manager.py --project-id "proj-123" --register-oversight --plan-name "Human Oversight Plan" --json   # Register plan
python tools/compliance/accountability_manager.py --project-id "proj-123" --designate-caio --name "Jane Smith" --role CAIO --json           # Designate CAIO
python tools/compliance/accountability_manager.py --project-id "proj-123" --file-appeal --appellant "John Doe" --ai-system "System" --json  # File appeal
python tools/compliance/accountability_manager.py --project-id "proj-123" --submit-ethics-review --review-type bias_testing_policy --json   # Ethics review
python tools/compliance/ai_impact_assessor.py --project-id "proj-123" --ai-system "System" --json             # Impact assessment
python tools/compliance/ai_impact_assessor.py --project-id "proj-123" --summary --json                        # Impact summary
python tools/compliance/ai_incident_response.py --project-id "proj-123" --log --type bias_detected --severity high --description "Bias found" --json   # Log incident
python tools/compliance/ai_incident_response.py --project-id "proj-123" --stats --json                        # Incident stats
python tools/compliance/ai_reassessment_scheduler.py --project-id "proj-123" --create --ai-system "System" --frequency annual --json   # Schedule reassessment
python tools/compliance/ai_reassessment_scheduler.py --project-id "proj-123" --overdue --json                 # Check overdue
python tools/compliance/ai_accountability_audit.py --project-id "proj-123" --json                              # Accountability audit

# Code Intelligence (Phase 52 — D331-D337)
python tools/analysis/code_analyzer.py --project-dir tools/ --json                                            # Scan directory for code quality metrics
python tools/analysis/code_analyzer.py --project-dir tools/ --store --json                                    # Scan + store metrics in DB
python tools/analysis/code_analyzer.py --file tools/analysis/code_analyzer.py --json                          # Analyze single file
python tools/analysis/code_analyzer.py --project-dir tools/ --trend --json                                    # Maintainability trend data
python tools/analysis/runtime_feedback.py --xml .tmp/results.xml --project-id proj-123 --json                 # Parse JUnit XML + store feedback
python tools/analysis/runtime_feedback.py --health --function analyze_code --json                              # Per-function health score

# Universal RAG Subsystem (Phase 64)
python tools/rag/ingestion_manager.py --ingest --source innovation_signals --json   # Ingest single source
python tools/rag/ingestion_manager.py --sweep --json                                 # Batch sweep all sources
python tools/rag/ingestion_manager.py --status --json                                # Ingestion status
python tools/rag/ingestion_manager.py --daemon --json                                # Continuous ingestion daemon
python tools/rag/retriever.py --query "FedRAMP AC-2" --json                          # Search across all knowledge
python tools/rag/retention_manager.py --migrate --json                               # Hot/warm/cold tier migration
python tools/rag/retention_manager.py --status --json                                # Retention tier status
python tools/rag/reindex_contextual.py --reindex --source compliance_reference --dry-run --json          # Plan a contextual re-index
python tools/rag/reindex_contextual.py --reindex --source compliance_reference --limit 500 --offset 0 --execute --json  # Resumable window (next_offset/has_more)
python tools/rag/reindex_contextual.py --benchmark --baseline data/rag/rce_baseline.json --json          # Measure retrieval vs baseline

# Chunking Templates (oss-chunk-01) — document-type chunking driven by the source_registry 'chunking' key
python tools/rag/chunking_templates.py --list --json                                 # All templates + the default
python tools/rag/chunking_templates.py --show oscal_catalog --json                   # One template definition
python tools/rag/chunking_templates.py --suggest docs/catalog.md --json              # ADVISORY suggestion — never auto-applied
python tools/rag/chunking_templates.py --preview docs/catalog.md --template oscal_catalog --json  # Chunks a template would produce
# Templates: oscal_catalog (1 chunk/control, never split) · stig_checklist (1 chunk/rule) · rfp_sow (Section L/M)
#            contract (numbered clauses) · sop_runbook (numbered steps) · slide_deck (1 chunk/slide)
#            spreadsheet (row groups + header repeat) · general (default sliding window, unchanged)
# Wire a source: set "chunking": "<template>" on its entry in tools/rag/source_registry.py

# Fine-Tuning (Phase 64 Extension)
python tools/finetune/dataset_manager.py --create --name "my-dataset" --purpose general --json   # Create dataset
python tools/finetune/dataset_manager.py --list --json                                            # List datasets
python tools/finetune/dataset_manager.py --export --dataset-id "ds-xxx" --output data.jsonl --json  # Export JSONL
python tools/finetune/pair_generator.py --dataset-id "ds-xxx" --source-type rag --json            # Generate Q&A pairs from RAG
python tools/finetune/training_engine.py --dataset-id "ds-xxx" --json                             # Train local (Unsloth)
python tools/finetune/training_engine.py --dataset-id "ds-xxx" --provider openai --json           # Train cloud (OpenAI)
python tools/finetune/evaluator.py --model-version-id "mv-xxx" --json                             # Evaluate model
python tools/finetune/ab_evaluator.py --model-a "mv-xxx" --model-b "mv-yyy" --json               # A/B comparison
python tools/finetune/promotion_manager.py --check --model-version-id "mv-xxx" --json             # Check promotion eligibility
python tools/finetune/promotion_manager.py --promote --model-version-id "mv-xxx" --function code_generation --json  # Promote model
python tools/finetune/gpu_detector.py --json                                                       # GPU detection
python tools/finetune/retrain_trigger.py --check --json                                            # Check retrain triggers
python tools/finetune/hp_search.py --create --dataset-id <id> --json                               # Create HP search
python tools/finetune/hp_search.py --run-next --search-id <id> --json                              # Run next trial
python tools/finetune/hp_search.py --record --trial-id <id> --score 0.85 --json                    # Record trial result
python tools/finetune/hp_search.py --status --search-id <id> --json                                # Search status
python tools/finetune/hp_search.py --list --json                                                    # List all searches

# Observability, Traceability & Explainable AI (Phase 46)
python -c "from tools.observability import get_tracer; print(type(get_tracer()).__name__)"           # Check active tracer
python tools/observability/shap/agent_shap.py --trace-id "<trace-id>" --iterations 1000 --json       # SHAP analysis on trace
python tools/observability/shap/agent_shap.py --project-id "proj-123" --last-n 10 --json             # SHAP last N traces
python tools/observability/provenance/prov_query.py --entity-id "<id>" --direction backward --json    # Provenance lineage
python tools/observability/provenance/prov_export.py --project-id "proj-123" --json                   # PROV-JSON export
python tools/compliance/xai_assessor.py --project-id "proj-123" --json                                # XAI assessment (10 checks)
python tools/compliance/xai_assessor.py --project-id "proj-123" --gate                                # XAI gate evaluation

# EU AI Act Risk Classifier (Phase 57, D349)
python tools/compliance/eu_ai_act_classifier.py --project-id "proj-123" --json          # Assess all 12 requirements
python tools/compliance/eu_ai_act_classifier.py --project-id "proj-123" --gate          # Gate evaluation

# Platform One / Iron Bank (Phase 57, D350)
python tools/infra/ironbank_metadata_generator.py --project-id "proj-123" --generate --json                     # Generate hardening manifest
python tools/infra/ironbank_metadata_generator.py --project-id "proj-123" --generate --output-dir .tmp/ironbank # Generate + write to dir
python tools/infra/ironbank_metadata_generator.py --project-id "proj-123" --validate --manifest-path .tmp/ironbank/hardening_manifest.yaml  # Validate
python tools/infra/ironbank_metadata_generator.py --list-base-images --json              # List Iron Bank base images

# Compliance Evidence Auto-Collection (Phase 56, D347)
python tools/compliance/evidence_collector.py --project-id "proj-123" --json             # Collect all frameworks
python tools/compliance/evidence_collector.py --project-id "proj-123" --framework fedramp --json  # Single framework
python tools/compliance/evidence_collector.py --project-id "proj-123" --freshness --max-age-hours 168 --json  # Check freshness
python tools/compliance/evidence_collector.py --list-frameworks --json                   # List supported frameworks

# Infrastructure
python tools/infra/terraform_generator.py --project-id "proj-123"
python tools/infra/ansible_generator.py --project-id "proj-123"
python tools/infra/k8s_generator.py --project-id "proj-123"
python tools/infra/pipeline_generator.py --project-id "proj-123"
python tools/infra/rollback.py --deployment-id "deploy-123"

# Knowledge & Self-Healing
python tools/knowledge/pattern_detector.py --log-data "/path/to/logs"
python tools/knowledge/self_heal_analyzer.py --failure-id "fail-123"
python tools/knowledge/recommendation_engine.py --project-id "proj-123"

# Monitoring
python tools/monitor/log_analyzer.py --source elk --query "error"
python tools/monitor/health_checker.py --target "http://service:8080/health"

# Heartbeat Daemon (Phase 29 — Proactive Monitoring)
python tools/monitor/heartbeat_daemon.py                # Foreground daemon (7 configurable checks)
python tools/monitor/heartbeat_daemon.py --once          # Single pass of all checks
python tools/monitor/heartbeat_daemon.py --check cato_evidence  # Specific check
python tools/monitor/heartbeat_daemon.py --status --json # Show all check statuses

# Webhook-Triggered Auto-Resolution (Phase 29)
python tools/monitor/auto_resolver.py --analyze --alert-file alert.json --json   # Analyze without acting
python tools/monitor/auto_resolver.py --resolve --alert-file alert.json --json   # Full pipeline: analyze + fix + PR
python tools/monitor/auto_resolver.py --history --json                            # Resolution history

# Outcome Verifier (D-EVO-6, self-healing feedback loop)
python tools/monitor/outcome_verifier.py --check-pending --json                  # Check PR merge status
python tools/monitor/outcome_verifier.py --check-recurrence --json               # Check failure recurrence
python tools/monitor/outcome_verifier.py --run-all --json                        # Both checks
python tools/monitor/outcome_verifier.py --status --json                         # Verification summary

# Selective Skill Injection (Phase 29)
python tools/agent/skill_selector.py --query "fix the login tests" --json         # Keyword-based category matching
python tools/agent/skill_selector.py --detect --project-dir /path --json          # File-based detection
python tools/agent/skill_selector.py --query "deploy to staging" --format-context # Injection-ready markdown

# Time-Decay Memory Ranking (Phase 29)
python tools/memory/time_decay.py --score --entry-id 42 --json                    # Score single entry
python tools/memory/time_decay.py --rank --query "keyword" --top-k 10 --json      # Time-decay ranked search
python tools/memory/hybrid_search.py --query "test" --time-decay                   # Integrated time-decay search

# Dashboard (Flask web UI)
python tools/dashboard/app.py                        # Start web dashboard on port 5000
python tools/dashboard/auth.py create-admin --email admin@icdev.local --name "Admin"   # Create first admin + API key
python tools/dashboard/auth.py list-users            # List all dashboard users

# DevSecOps Profile & Pipeline Security (Phase 24)
python tools/devsecops/profile_manager.py --project-id "proj-123" --create --maturity level_3_defined --json   # Create DevSecOps profile
python tools/devsecops/profile_manager.py --project-id "proj-123" --detect --json                              # Auto-detect maturity
python tools/devsecops/profile_manager.py --project-id "proj-123" --assess --json                              # Assess maturity level
python tools/devsecops/profile_manager.py --project-id "proj-123" --json                                       # Get profile
python tools/devsecops/pipeline_security_generator.py --project-id "proj-123" --json                           # Generate pipeline security stages
python tools/devsecops/policy_generator.py --project-id "proj-123" --engine kyverno --json                     # Generate Kyverno policies
python tools/devsecops/policy_generator.py --project-id "proj-123" --engine opa --json                         # Generate OPA policies
python tools/devsecops/attestation_manager.py --project-id "proj-123" --generate --json                        # Generate signing config

# Zero Trust Architecture (Phase 25)
python tools/devsecops/zta_maturity_scorer.py --project-id "proj-123" --all --json                             # Score all 7 ZTA pillars
python tools/devsecops/zta_maturity_scorer.py --project-id "proj-123" --pillar user_identity --json            # Score individual pillar
python tools/devsecops/zta_maturity_scorer.py --project-id "proj-123" --trend --json                           # Maturity trend
python tools/compliance/nist_800_207_assessor.py --project-id "proj-123" --json                                # NIST 800-207 assessment
python tools/compliance/nist_800_207_assessor.py --project-id "proj-123" --gate                                # NIST 800-207 gate
python tools/devsecops/service_mesh_generator.py --project-id "proj-123" --mesh istio --json                   # Generate Istio service mesh
python tools/devsecops/service_mesh_generator.py --project-id "proj-123" --mesh linkerd --json                 # Generate Linkerd service mesh
python tools/devsecops/network_segmentation_generator.py --project-path /path --namespaces "app,data" --json   # Namespace isolation
python tools/devsecops/network_segmentation_generator.py --project-path /path --services "api,db" --json       # Microsegmentation
python tools/devsecops/zta_terraform_generator.py --project-path /path --modules all --json                    # ZTA Terraform modules
python tools/devsecops/pdp_config_generator.py --project-id "proj-123" --pdp-type disa_icam --json             # PDP config
python tools/devsecops/pdp_config_generator.py --project-id "proj-123" --pdp-type zscaler --mesh istio --json  # PEP config

# DoD MOSA (Phase 26 — Modular Open Systems Approach)
python tools/compliance/mosa_assessor.py --project-id "proj-123" --json                                        # MOSA assessment
python tools/compliance/mosa_assessor.py --project-id "proj-123" --gate                                        # MOSA gate check
python tools/mosa/modular_design_analyzer.py --project-dir /path --project-id "proj-123" --store --json        # Modularity analysis
python tools/mosa/mosa_code_enforcer.py --project-dir /path --fix-suggestions --json                           # Code enforcement
python tools/mosa/icd_generator.py --project-id "proj-123" --all --json                                        # Generate ICDs
python tools/mosa/icd_generator.py --project-id "proj-123" --interface-id "iface-1" --json                     # Generate single ICD
python tools/mosa/tsp_generator.py --project-id "proj-123" --json                                              # Generate TSP
python tools/compliance/cato_monitor.py --project-id "proj-123" --mosa-evidence                                # MOSA cATO evidence

# Remote Command Gateway (Phase 28)
python tools/gateway/gateway_agent.py                                          # Start gateway on port 8458
python tools/gateway/user_binder.py --provision --channel mattermost --channel-user-id "user123" --icdev-user-id "admin@enclave.mil" --json  # Pre-provision binding (air-gapped)
python tools/gateway/user_binder.py --list --json                              # List all bindings
python tools/gateway/user_binder.py --revoke <binding-id>                      # Revoke a binding

# SaaS Multi-Tenancy (Phase 21)
python tools/saas/platform_db.py --init                                          # Initialize platform database
python tools/saas/tenant_manager.py --create --name "ACME" --il IL4 --tier professional --admin-email admin@acme.gov
python tools/saas/tenant_manager.py --list --json                                # List all tenants
python tools/saas/tenant_manager.py --provision --tenant-id "tenant-uuid"        # Provision tenant (create DB, K8s NS)
python tools/saas/tenant_manager.py --approve --tenant-id "tenant-uuid" --approver-id "admin-uuid"  # Approve IL5/IL6 tenant
python tools/saas/tenant_manager.py --add-user --tenant-id "tenant-uuid" --email dev@acme.gov --role developer
python tools/saas/api_gateway.py --port 8443 --debug                             # Start API gateway (dev mode)
gunicorn -w 4 -b 0.0.0.0:8443 tools.saas.api_gateway:app                       # Start API gateway (production)
python tools/saas/openapi_spec.py [--output spec.json]                           # Generate OpenAPI spec to file
python tools/saas/licensing/license_generator.py --generate --customer "ACME" --tier enterprise --expires-in-days 365 --private-key /path/key.pem
python tools/saas/licensing/license_validator.py --validate --json               # Validate on-prem license
python tools/saas/infra/namespace_provisioner.py --create --slug acme --il IL4 --tier professional  # Create tenant K8s NS
helm install icdev deploy/helm/ --values deploy/helm/values.yaml                 # Deploy on-prem via Helm

# CI/CD Integration (GitHub + GitLab dual support)
python tools/ci/triggers/webhook_server.py           # Start webhook server (POST /gh-webhook, /gl-webhook)
python tools/ci/triggers/poll_trigger.py             # Start issue polling (every 20s)
python tools/ci/workflows/icdev_plan.py 123          # Run planning phase for issue #123
python tools/ci/workflows/icdev_build.py 123 abc1234 # Run build phase (requires run-id)
python tools/ci/workflows/icdev_test.py 123 abc1234  # Run test phase
python tools/ci/workflows/icdev_review.py 123 abc1234 # Run review phase
python tools/ci/workflows/icdev_sdlc.py 123          # Run full SDLC pipeline
python tools/ci/workflows/icdev_sdlc.py 123 --orchestrated  # DAG-based parallel SDLC
python tools/ci/workflows/icdev_plan_build.py 123    # Run plan + build

# Multi-Agent Orchestration (Opus 4.6)
python tools/agent/bedrock_client.py --probe          # Check Bedrock model availability
python tools/agent/bedrock_client.py --prompt "text" --model opus --effort high  # Invoke Bedrock
python tools/agent/bedrock_client.py --prompt "text" --stream  # Streaming invocation
python tools/agent/token_tracker.py --action summary --project-id "proj-123"  # Token usage summary
python tools/agent/token_tracker.py --action cost --project-id "proj-123"     # Cost breakdown
python tools/agent/team_orchestrator.py --decompose "task description" --project-id "proj-123"  # Decompose task into DAG
python tools/agent/team_orchestrator.py --execute --workflow-id "wf-123"      # Execute workflow
python tools/agent/skill_router.py --route-skill "ssp_generate"              # Route skill to healthy agent
python tools/agent/skill_router.py --health                                  # Show healthy agents
python tools/agent/collaboration.py --pattern reviewer --project-id "proj-123"  # Run reviewer pattern
python tools/agent/authority.py --check security-agent code_generation       # Check domain authority
python tools/agent/mailbox.py --inbox --agent-id "builder-agent"             # Check agent inbox
python tools/agent/agent_memory.py --recall --agent-id "builder-agent" --project-id "proj-123"  # Recall memories
python tools/agent/agent_executor.py --prompt "text" --bedrock               # Execute via Bedrock API

# Phase 61 — Orchestration Improvements
python tools/agent/dispatcher_mode.py --status --project-id "proj-123" --json           # Dispatcher mode status
python tools/agent/dispatcher_mode.py --enable --project-id "proj-123" --json           # Enable dispatcher-only mode
python tools/agent/dispatcher_mode.py --disable --project-id "proj-123" --json          # Disable dispatcher-only mode
python tools/agent/dispatcher_mode.py --check-tool scaffold --project-id "proj-123" --json  # Check if tool is allowed
python tools/agent/prompt_chain_executor.py --list --json                                # List available prompt chains
python tools/agent/prompt_chain_executor.py --chain plan_critique_refine --input "text" --project-id "proj-123" --json  # Execute chain
python tools/agent/prompt_chain_executor.py --chain plan_critique_refine --input "text" --dry-run --json               # Dry run
python tools/agent/prompt_chain_executor.py --history --project-id "proj-123" --json     # Chain execution history
python tools/agent/anvil_critique.py --project-id "proj-123" --phase-output "plan text" --json  # Run ANVIL critique
python tools/agent/anvil_critique.py --session-id "<id>" --status --json                 # Critique session status
python tools/agent/anvil_critique.py --history --project-id "proj-123" --json            # Critique history
python tools/agent/session_purpose.py --declare "Implement auth module" --project-id "proj-123" --json  # Declare session purpose
python tools/agent/session_purpose.py --active --project-id "proj-123" --json            # Get active purpose
python tools/agent/session_purpose.py --complete "<id>" --json                           # Complete purpose
python tools/agent/session_purpose.py --history --project-id "proj-123" --json           # Purpose history

# Agent Topology
python tools/agent/topology.py --build --json                                          # Build topology graph
python tools/agent/topology.py --spof --json                                           # Detect single points of failure
python tools/agent/topology.py --air-gap-check --json                                  # Validate air-gap compliance
python tools/agent/topology.py --providers --json                                      # List provider dependencies
python tools/agent/topology.py --snapshot --json                                       # Take topology snapshot
python tools/agent/topology.py --compare --json                                        # Compare snapshots for drift
python tools/agent/topology.py --stats --json                                          # Topology statistics
python tools/agent/topology.py --gate                                                  # Gate check (CI/CD)

# File Sync
python tools/filesync/sync_engine.py --create --name "Backup" --source /src --dest /dst --json
python tools/filesync/sync_engine.py --list --json
python tools/filesync/sync_engine.py --status --job-id "fsync-xxx" --json
python tools/filesync/sync_engine.py --delete --job-id "fsync-xxx" --json
python tools/filesync/sync_engine.py --run --job-id "fsync-xxx" --json
python tools/filesync/sync_engine.py --run --job-id "fsync-xxx" --dry-run --json
python tools/filesync/sync_engine.py --run-all --json
python tools/filesync/sync_engine.py --conflicts --job-id "fsync-xxx" --json
python tools/filesync/sync_engine.py --resolve --conflict-id "fc-xxx" --resolution source_wins --json
python tools/filesync/sync_engine.py --daemon --json
python tools/filesync/sync_engine.py --watch --job-id "fsync-xxx" --json
python tools/filesync/sync_engine.py --health --json
```

---

## Innovation Engine — Autonomous Self-Improvement (Phase 35)
```bash
# Full pipeline (one-shot)
python tools/innovation/innovation_manager.py --run --json

# Individual stages
python tools/innovation/web_scanner.py --scan --all --json
python tools/innovation/signal_ranker.py --score-all --json
python tools/innovation/triage_engine.py --triage-all --json
python tools/innovation/trend_detector.py --detect --json
python tools/innovation/solution_generator.py --generate-all --json

# Promote benchmark findings to the kanban board as suggested cards (xbm-promote-01)
# Gap-gated, rate-limited, idempotent. Never writes backlog — that is an operator action.
python tools/innovation/kanban_promoter.py --dry-run --json    # preview (the default)
python tools/innovation/kanban_promoter.py --list --json       # candidates + subsystem verdicts
python tools/innovation/kanban_promoter.py --promote --json    # write status='suggested' cards
python tools/innovation/kanban_promoter.py --promote-id <signal_id> --json
# Tighten (or loosen) the rate limit for one run; absent, args/innovation_promoter.yaml wins
python tools/innovation/kanban_promoter.py --promote --max-per-run 3 --max-per-subsystem 1 --json
# The whole contract as one reviewable SELECT — source tables, gap-verdict filter,
# idempotency derivation, columns→task fields. Read-only, PostgreSQL only (xbm-promote-01-d2)
python tools/innovation/kanban_promoter.py --contract-sql --json

# ICDEV's own half of a benchmark comparison, per subsystem tag (xbm-cmp-01-d1)
python tools/innovation/icdev_evidence.py --subsystem observability --json
python tools/innovation/icdev_evidence.py --all --json
python tools/innovation/icdev_evidence.py --audit            # patterns matching nothing

# Which ICDEV subsystem does an external project benchmark? (xbm-cmp-01-d2)
python tools/innovation/subsystem_map.py --project langgraph --json
python tools/innovation/subsystem_map.py --subsystem observability --json
python tools/innovation/subsystem_map.py --all --json        # the comparison map
python tools/innovation/subsystem_map.py --validate          # cross-file integrity

# Verdict engine — ahead / parity / gap / no adaptation needed (xbm-cmp-01-d3)
# `position` (ahead|parity|behind|unknown) is where ICDEV stands; `verdict` is
# what to do about it. A subsystem can be position=ahead AND verdict=no_adaptation_needed.
python tools/innovation/benchmark_compare.py --subsystem observability --json
python tools/innovation/benchmark_compare.py --project langfuse --json   # a finding -> a verdict
python tools/innovation/benchmark_compare.py --all --json
python tools/innovation/benchmark_compare.py --all --verdict gap

# The comparison as a document — docs/research/external-benchmark-map.generated.md (xbm-cmp-01-d4)
# Offline by default so the checked-in file reproduces byte-for-byte and CI can diff it.
# It writes BESIDE the hand-written map, never over it: the map is the cited source of
# every declared reading, and its narrative lives in no config.
# Exact module counts are NOT committed (kax-conflict-02) — the artifact carries the
# classification against the floor, so adding a module changes nothing and two branches
# never conflict on it. Use --json or --live for the integers.
python tools/innovation/benchmark_report.py --write      # regenerate the checked-in report
python tools/innovation/benchmark_report.py --check      # CI gate: fails on drift, prints a diff
python tools/innovation/benchmark_report.py --live       # measure rows; retires findings; prints only
python tools/innovation/benchmark_report.py --json

# Introspective analysis (air-gap safe)
python tools/innovation/introspective_analyzer.py --analyze --all --json

# Competitive intelligence
python tools/innovation/competitive_intel.py --scan --all --json
python tools/innovation/competitive_intel.py --gap-analysis --json

# Standards body monitoring
python tools/innovation/standards_monitor.py --check --all --json

# Status and reporting
python tools/innovation/innovation_manager.py --status --json
python tools/innovation/innovation_manager.py --pipeline-report --json

# Continuous daemon mode
python tools/innovation/innovation_manager.py --daemon --json

# Feedback calibration
python tools/innovation/signal_ranker.py --calibrate --json
```

---

## Creative Engine — Customer-Centric Feature Discovery (Phase 58)
```bash
# Full pipeline
python tools/creative/creative_engine.py --run --json
python tools/creative/creative_engine.py --run --domain "proposal management" --json

# Individual stages
python tools/creative/creative_engine.py --discover --domain "proposal management" --json
python tools/creative/creative_engine.py --scan --all --json
python tools/creative/creative_engine.py --extract --json
python tools/creative/creative_engine.py --score --json
python tools/creative/creative_engine.py --rank --top-k 20 --json
python tools/creative/creative_engine.py --generate --json

# Status
python tools/creative/creative_engine.py --status --json
python tools/creative/creative_engine.py --pipeline-report --json
python tools/creative/creative_engine.py --competitors --json
python tools/creative/creative_engine.py --trends --json
python tools/creative/creative_engine.py --specs --json

# Sub-tools
python tools/creative/source_scanner.py --scan --all --json
python tools/creative/source_scanner.py --list-sources --json
python tools/creative/competitor_discoverer.py --discover --domain "proposal management" --json
python tools/creative/competitor_discoverer.py --list --json
python tools/creative/competitor_discoverer.py --confirm --competitor-id <id> --json
python tools/creative/pain_extractor.py --extract-all --json
python tools/creative/gap_scorer.py --score-all --json
python tools/creative/gap_scorer.py --top --limit 20 --json
python tools/creative/gap_scorer.py --gaps --json
python tools/creative/trend_tracker.py --detect --json
python tools/creative/trend_tracker.py --report --json
python tools/creative/spec_generator.py --generate-all --json
python tools/creative/spec_generator.py --list --json

# Divergent ideation benchmark (dvg-bench-01) — divergence vs single-shot on real
# ICDEV functions; recommend-only (flips no default), air-gap => status "unmeasured"
python tools/creative/divergence_benchmark.py --dry-run                 # list tasks, no model calls
python tools/creative/divergence_benchmark.py --run --json              # measure + persist to data/divergence/
python tools/creative/divergence_benchmark.py --run --tasks <path> --out <dir>

# Daemon mode
python tools/creative/creative_engine.py --daemon --json
```

---

## Industry Research Engine — Deep Vertical Research (Phase 63)
```bash
# Full pipeline
python tools/research/research_engine.py --run --vertical trading --json

# Individual stages
python tools/research/research_engine.py --run-stage SCOPE --session-id "rsess-xxx" --json

# Session management
python tools/research/session_manager.py --create --vertical trading --name "Trading Research" --json
python tools/research/session_manager.py --list --json

# Vertical management
python tools/research/vertical_loader.py --load --json
python tools/research/vertical_loader.py --list --json

# Source scanning (8 streams)
python tools/research/source_scanner.py --scan --session-id "rsess-xxx" --json
python tools/research/source_scanner.py --list-sources --json

# Challenge scoring
python tools/research/challenge_scorer.py --cluster --session-id "rsess-xxx" --json
python tools/research/challenge_scorer.py --score --session-id "rsess-xxx" --json

# Regulatory mapping
python tools/research/regulatory_mapper.py --map --session-id "rsess-xxx" --json

# Capability mapping
python tools/research/capability_mapper.py --map --session-id "rsess-xxx" --json

# Build/buy analysis
python tools/research/build_buy_analyzer.py --analyze --session-id "rsess-xxx" --json

# Trend detection
python tools/research/trend_detector.py --detect --json

# Dossier generation
python tools/research/dossier_generator.py --generate --session-id "rsess-xxx" --json
python tools/research/dossier_generator.py --list --json

# YouTube video scanning (9th source stream)
python tools/research/youtube_scanner.py --scan --queries "topic keyword" --json
python tools/research/youtube_scanner.py --scan --urls "https://youtube.com/watch?v=xxx" --json
python tools/research/youtube_scanner.py --scan --channels "UCxxx" --json

# Forecast generation (cross-engine predictions)
python tools/research/forecast_generator.py --generate --session-id "rsess-xxx" --json
python tools/research/forecast_generator.py --get --session-id "rsess-xxx" --json

# Status
python tools/research/research_engine.py --status --json

# Daemon mode
python tools/research/research_engine.py --daemon --json
```

---

## FathomDeskNews Pipeline — ADN News Intelligence (Phase ADN)
```bash
# Ingest RSS feeds once (all configured feeds in args/news_feeds.yaml)
python tools/trading/news/rss_ingestor.py --run-once --json

# Continuous poller daemon (respects poll_interval_seconds from args/news_feeds.yaml)
python tools/trading/news/rss_ingestor.py --daemon --json

# Classify pending news items (rule-based, no LLM required)
python tools/trading/news/classifier.py --run --json

# Match classified items to macro scenarios (meta_scenarios.yaml)
python tools/trading/news/scenario_matcher.py --run --json

# Aggregate and promote clusters to the dashboard
python tools/trading/news/aggregator.py --run --json

# LLM-backed reasoning over top clusters (requires LLM provider)
python tools/trading/news/news_reasoner.py --run --json

# Database migrations and health
python tools/trading/news/db.py --migrate --json
python tools/trading/news/db.py --health --json

# Perspective scoring (bearish/bullish net_direction wiring)
python tools/trading/news/perspective_scorer.py --score --json
```

---

## FathomDesk — OpenBB Gateway
```bash
python tools/fathomdesk/openbb_gateway.py --ticker AAPL --method get_price --json
```

---

## Marketplace — Federated FORGE Asset Registry (Phase 22)
```bash
# Publish a skill to tenant-local catalog
python tools/marketplace/publish_pipeline.py --asset-path /path --asset-type skill --tenant-id "tenant-abc" --publisher-user "user@mil" --json

# Search the marketplace
python tools/marketplace/search_engine.py --search "STIG checker" --json

# Check IL compatibility
python tools/marketplace/compatibility_checker.py --asset-id "asset-abc" --consumer-il IL5 --json

# Install an asset
python tools/marketplace/install_manager.py --install --asset-id "asset-abc" --tenant-id "tenant-abc" --json

# Review queue (ISSO/security officer)
python tools/marketplace/review_queue.py --pending --json
python tools/marketplace/review_queue.py --review --review-id "rev-abc" --reviewer-id "isso@mil" --decision approved --rationale "Passed review" --json

# Federation sync
python tools/marketplace/federation_sync.py --status --json
python tools/marketplace/federation_sync.py --promote --tenant-id "tenant-abc" --json
python tools/marketplace/federation_sync.py --pull --tenant-id "tenant-abc" --consumer-il IL5 --json

# Security scanning
python tools/marketplace/asset_scanner.py --asset-id "asset-abc" --version-id "ver-abc" --asset-path /path --json

# Catalog management
python tools/marketplace/catalog_manager.py --list --asset-type skill --json
python tools/marketplace/catalog_manager.py --get --slug "tenant-abc/my-skill" --json

# Provenance
python tools/marketplace/provenance_tracker.py --report --asset-id "asset-abc" --json

# OpenClaw Bridge — Zero-trust import/export for ClawHub (clawhub.ai) skills (Phase 69)
# Import a skill from local OpenClaw directory
python tools/marketplace/openclaw_bridge.py --import --source-path /path/to/openclaw-skill --tenant-id "tenant-abc" --imported-by "user@mil" --json

# Import with ClawHub URL for provenance tracking
python tools/marketplace/openclaw_bridge.py --import --source-path /path/to/openclaw-skill --clawhub-url "https://clawhub.ai/author/skill-name" --tenant-id "tenant-abc" --imported-by "user@mil" --json

# List quarantined imports
python tools/marketplace/openclaw_bridge.py --list-quarantine --json
python tools/marketplace/openclaw_bridge.py --list-quarantine --status review_pending --json

# Promote a quarantined import (after review, ISSO/security officer)
python tools/marketplace/openclaw_bridge.py --promote --import-id "oci-abc123" --promoted-by "isso@dod.mil" --json

# Reject a quarantined import
python tools/marketplace/openclaw_bridge.py --reject --import-id "oci-abc123" --rejected-by "isso@dod.mil" --reason "Contains eval() calls" --json

# Export an ICDEV™ skill to OpenClaw format (strips CUI, requires approval)
python tools/marketplace/openclaw_bridge.py --export --asset-id "asset-abc" --version-id "ver-abc" --output-path /path/to/output --exported-by "user@mil" --json

# List pending exports
python tools/marketplace/openclaw_bridge.py --list-exports --json

# Health check
python tools/marketplace/openclaw_bridge.py --health --json

# Revoke a promoted import (rollback)
python tools/marketplace/openclaw_bridge.py --revoke --import-id "oci-abc123" --revoked-by "isso@dod.mil" --reason "Causing errors" --json

# Discover skills on ClawHub (vector search — requires network)
python tools/marketplace/openclaw_bridge.py --discover "code review automation" --limit 10 --json

# Fetch + import a skill from ClawHub by slug (download → quarantine → scan → translate)
python tools/marketplace/openclaw_bridge.py --fetch self-improving-agent --tenant-id "tenant-abc" --imported-by "user@mil" --json

# Gate check (CI/CD)
python tools/marketplace/openclaw_bridge.py --gate --json

# ClawHub DataBridge Connector (standalone)
python tools/databridge/connectors/clawhub_connector.py --search "self-improvement" --json
python tools/databridge/connectors/clawhub_connector.py --get self-improving-agent --json
python tools/databridge/connectors/clawhub_connector.py --download self-improving-agent --output .tmp/ --json
python tools/databridge/connectors/clawhub_connector.py --health --json
```

---

## LLM Tools — Gateway, Prompt Registry, Cost Intelligence, Model Monitor

```bash
# Cost budget — the DOWNGRADE gate on the LLMRouter chain (exa-policy-04)
python tools/llm/cost_budget.py --status --json                                         # Current spend vs limit, and what the router would do
python tools/llm/cost_budget.py --function code_generation --json                       # Evaluate one function's budget
python tools/llm/cost_budget.py --explain code_generation --json                        # Declared chain + per-model price + what it downgrades to
python tools/llm/cost_budget.py --gate                                                  # Exit 1 only when hard_action is 'block' and the limit is reached
# The other four budget layers all BLOCK (token_tracker per agent, module_budget_tracker
# per module, chain_orchestration per run, proxy_budgets per key). This one ASKs at a soft
# threshold — ONCE per threshold per period, deduped via the append-only agent_approval_log —
# and at the hard limit DOWNGRADES: the function's declared routing.<fn>.chain is reordered
# so the affordable tier leads and the expensive model is demoted to the tail (never dropped),
# so a long autonomous run keeps working instead of dying at 02:00.
# Air-gap: local models declare pricing 0.0 and downgrade.prefer_local breaks price ties
# local-first, so the downgrade lands on Ollama. No model id in Python — the order comes from
# the chain and the pricing: block in args/llm_config.yaml (cost_budget:).
# Spend reads ai_telemetry; an absent table reports `unmeasurable`, never a misleading zero.

# AGX reasoning-architecture benchmark + leaderboard (agx-bench-01/02)
python tools/llm/architectures/benchmark.py --dry-run --json                            # List task suite + registered architectures (no model calls)
python tools/llm/architectures/benchmark.py --run --json                                # Run the bench (live models if reachable) -> data/agx/benchmark_latest.json
python tools/llm/architectures/benchmark.py --run --architectures chain_of_thought,baseline --min-samples 3  # Subset / threshold
python tools/llm/architectures/leaderboard.py --markdown                                # Render leaderboard + routing recommendations from latest report
python tools/llm/architectures/leaderboard.py --recommend --json                        # Evidence-based routing recommendations only (RECOMMEND — never writes config)

# LLM Proxy Keys (lpx-keys-01) — virtual keys for /gameday & /academy cohorts
python tools/llm/proxy_keys.py issue --scope-type team --scope-ref 7 --session-id 42 --budget 10 --budget-window exercise --json  # Issue a budgeted team key (shown once)
python tools/llm/proxy_keys.py list --session-id 42 --json                              # List keys (metadata only, never the key/hash)
python tools/llm/proxy_keys.py show <key_id> --json                                     # Show one key by id
python tools/llm/proxy_keys.py revoke <key_id> --actor admin --reason "left cohort" --json  # Revoke (per-key, immediate)
python tools/llm/proxy_keys.py rotate <key_id> --actor admin --json                     # Rotate: revoke old, issue linked successor (new key shown once)
python tools/llm/proxy_keys.py expire --json                                            # Sweep keys past expiry -> status expired
python tools/llm/proxy_keys.py audit --key-id <key_id> --json                           # Append-only lifecycle audit trail (NIST AU)
# Default expiry: ICDEV_LLM_PROXY_KEY_TTL_DAYS (default 30; 0 disables) so a cohort key cannot outlive the cohort
# Master/admin key from ICDEV_LLM_PROXY_MASTER_KEY (never logged/returned); LiteLLM sync is best-effort and OFF unless ICDEV_LLM_PROXY_ENABLED=true

# Local canvas copy (lpx-keys-04) — per-person virtual key, fail-closed, no real key on a laptop
#   Use .env.local-copy.template (gateway URL + virtual-key slot, NO real-provider-key slot). Set ICDEV_LLM_LOCAL_COPY=true.
python -c "import json;from tools.llm.proxy_gateway import local_copy_preflight;print(json.dumps(local_copy_preflight()))"  # onboarding/health preflight
# A local copy with no gateway reachable / no virtual key fails CLOSED with a clear message; it never falls back to a real provider key.

# Per-team spend attribution (lpx-teams-03) — "what did each team spend this exercise?"
python tools/ttx/team_spend.py <session_id> --json                                  # Per-team calls/tokens/cost from ttx_api_log
python tools/ttx/team_spend.py <session_id> --total --json                           # + exercise roll-up

# LLM Proxy Team Budgets (lpx-teams-02) — per-team gameday spend (a team's budget is its key's budget)
python tools/llm/proxy_team_budgets.py provision <session_id> <team_id> --budget 40 --json  # Provision/update exercise budget
python tools/llm/proxy_team_budgets.py check <session_id> <team_id> --projected 0.05 --json  # allow/warn/block + facilitator_message
python tools/llm/proxy_team_budgets.py status <session_id> --json                    # Per-team spend vs budget (attribution is per-team)

# LLM Proxy Team Rate Ceilings (lpx-teams-01) — competition fairness for /gameday
python tools/llm/proxy_team_limits.py configure <session_id> --json                 # Compute+persist per-team RPM/TPM ceilings (sized off actual team count)
python tools/llm/proxy_team_limits.py check <session_id> <team_id> --tokens 1200 --json  # allow/deny for one team (degrades only that team)
python tools/llm/proxy_team_limits.py status <session_id> --json                    # Facilitator per-team usage vs ceiling (at_ceiling flag)
# Org limits: ICDEV_LLM_ORG_RPM (60) / ICDEV_LLM_ORG_TPM (100000); burst ICDEV_LLM_TEAM_BURST_FACTOR (1.5)

# LLM Proxy Budgets (lpx-keys-02) — per-key spend budgets scoped to team/guild/user
python tools/llm/proxy_budgets.py check <key_id> --projected 0.05 --json          # allow|warn|block for a key's budget
python tools/llm/proxy_budgets.py spend <key_id> --json                            # spend summary for current window
python tools/llm/proxy_budgets.py record <key_id> --cost 0.05 --input-tokens 1200 --output-tokens 400 --json  # record spend

# LLM Proxy Observability (lpx-obs-01) — spend + rate metrics into /ops/llm
python tools/llm/proxy_metrics.py --json                                            # Proxy spend/rate metrics (ledger + best-effort Prometheus scrape)
python tools/llm/proxy_metrics.py --window-hours 24 --top 10 --no-scrape --json     # Ledger-only aggregation over a window

# LLM Proxy Reconciliation (lpx-obs-02) — proxy spend vs token_tracker
python tools/llm/proxy_reconcile.py --json                                          # Reconcile proxy spend vs token_tracker/gateway audit
python tools/llm/proxy_reconcile.py --window-hours 24 --threshold-pct 10 --gate --json  # Exit 1 if divergence past threshold (proxy active + both have spend)

# LLM Proxy CUI egress gate (lpx-egress-02) — classified content never traverses the proxy
# ICDEV_LLM_PROXY_MAX_CLASSIFICATION (default UNCLASSIFIED) — highest classification allowed through the proxy;
# CUI and above are refused (fail-closed, invoke-time) unless explicitly raised (an ATO-boundary decision).

# LLM Gateway
python tools/llm/gateway.py --stats --json                                             # Gateway usage statistics
python tools/llm/gateway.py --audit --json --limit 50                                  # Audit log (last N requests)
python tools/llm/gateway.py --check-text "text to check" --json                        # Content safety check
python tools/llm/gateway.py --gate                                                     # Gate check (CI/CD)

# Prompt Registry
python tools/llm/prompt_registry.py --list --json                                      # List all registered prompts
python tools/llm/prompt_registry.py --register --name "layer/house-style" --template-text "text" --function code_generation --json  # Register new version
python tools/llm/prompt_registry.py --register --name "layer/house-style" --template-file hardprompts/house_style.md --function code_generation --json  # ...from a file
python tools/llm/prompt_registry.py --activate --name "layer/house-style" --version 2 --json  # Activate specific version
python tools/llm/prompt_registry.py --rollback --name "layer/house-style" --to-version 1 --json  # Rollback to previous version
python tools/llm/prompt_registry.py --diff --name "layer/house-style" --v1 1 --v2 2 --json  # Diff two prompt versions
python tools/llm/prompt_registry.py --layers --function code_generation --json         # Active supplemental layers the LLM router will apply (exa-refine-01)
python tools/llm/prompt_registry.py --import-hardprompts --json                        # Import from hardprompts/ directory
python tools/llm/prompt_registry.py --seed-call-sites --json                           # Register + activate the call-site prompt bodies at their current module text (exa-refine-02)
python tools/llm/prompt_registry.py --start-ab --name "layer/house-style" --va 1 --vb 2 --split 0.5 --json  # Start A/B test
python tools/llm/prompt_registry.py --gate                                             # Gate check (CI/CD)

# Cost Intelligence
python tools/llm/cost_intelligence.py --dashboard --json                               # Cost dashboard overview
python tools/llm/cost_intelligence.py --anomalies --json                               # Detect cost anomalies
python tools/llm/cost_intelligence.py --project --json                                 # Per-project cost breakdown
python tools/llm/cost_intelligence.py --recommend --json                               # Cost optimization recommendations
python tools/llm/cost_intelligence.py --edge-vs-cloud --function code_generation --json # Edge vs cloud cost comparison
python tools/llm/cost_intelligence.py --alerts --json                                  # Active cost alerts
python tools/llm/cost_intelligence.py --gate                                           # Gate check (CI/CD)

# Model Monitor
python tools/llm/model_monitor.py --record --model qwen3-local --function code_generation --score 0.85 --json  # Record quality score
python tools/llm/model_monitor.py --detect-drift --json                                # Detect model quality drift
python tools/llm/model_monitor.py --health --json                                      # Model health dashboard
python tools/llm/model_monitor.py --gate                                               # Gate check (CI/CD)
```

---

## ICDEV Cortex — Unified AI Facade (ctx-*)

Cortex is a Python **API facade** (`tools/cortex/`, mirrored to `icdev/tools/cortex/`) over
LLMRouter, the four retrieval backends (RAG / GraphRAG / DIC / KB), IQE, and the enforced
TRUST governance chain. There is no standalone argparse CLI — call it in-process or via
`python -c`. Routing is config-driven (`cortex_*` chains in `args/llm_config.yaml`); behavior
tuning lives in `args/cortex_config.yaml` (`$ICDEV_CORTEX_CONFIG` overrides). All chains keep a
local ollama tier so the facade is air-gap safe.

```bash
# --- Generation (api.py: complete / classify / extract) ---
# Free-form completion via the cortex_complete routing chain
python -c "from tools.cortex import complete; r = complete('Summarize NIST 800-53 AC-2'); print(r.text)"

# Single-label classification (LLM chain, deterministic query_classifier fallback offline/air-gap)
python -c "from tools.cortex import classify; r = classify('reset my password', ['billing','account','technical']); print(r.text, r.provider)"

# Structured extraction to a JSON schema (output_schema + fenced-JSON parse)
python -c "from tools.cortex import extract; r = extract('Contact: Jane Doe, jane@x.mil', {'type':'object','properties':{'name':{'type':'string'},'email':{'type':'string'}}}); print(r.text)"

# --- Unified search (search_service.py: strategy routing + CRAG correction) ---
# Agentic auto-routing (classify_route -> backend selection / fan-out), top_k=5
python -c "from tools.cortex import search; [print(h.backend, round(h.score,3), h.content[:60]) for h in search('who owns satellite AX-7', top_k=5)]"

# Force a specific backend or full fan-out (bypass classification): rag|graph|dic|kb|all
python -c "from tools.cortex import search; print(len(search('quarterly revenue trend', strategy='all')))"

# Inspect the routing decision without running backends
python -c "from tools.cortex import classify_route; print(classify_route('list all vendors linked to CVE-2024-1234'))"

# --- Analyst (analyst.py: ask-your-data, IQE primary + NL->SQL fallback) ---
# Natural-language query over platform data (mode: auto | iqe | nlq)
python -c "from tools.cortex import ask; r = ask('show all satellites', mode='auto'); print(r.provider); print(r.text)"

# Pin a canvas scope and request an LLM prose summary (citations grounded/validated)
python -c "from tools.cortex import ask; r = ask('top 5 open incidents', canvas='nocc', summarize=True); print(r.metadata.get('grounding')); print(r.text)"

# --- Governance (governance.py: enforced TRUST chain — gateway, redaction, grounding, provenance) ---
# Wrap any Cortex call so every gate in GATE_ORDER runs (fail-open unless ctx.fail_closed)
python -c "from tools.cortex import GovernancePipeline, complete, CortexContext; ctx=CortexContext(tenant_id='t1', classification='CUI', fail_closed=True); res, rpt = GovernancePipeline().wrap(lambda: complete('draft an RFI intro'), ctx, prompt='draft an RFI intro'); print(rpt.gates_run, rpt.outcomes)"

# --- Config / air-gap invariant (config.py) ---
# Load the merged cortex config (weights, rrf_k, crag_threshold, timeouts)
python -c "from tools.cortex import load_cortex_config; c = load_cortex_config(); print(c['search']['crag_threshold'], c['search']['timeouts'])"

# Verify every cortex_* routing chain retains a local ollama tier (raises CortexAirgapError if not)
python -c "from tools.cortex import assert_airgap_ready; assert_airgap_ready(); print('cortex air-gap ready')"

# --- Domain lenses (domains/: data-driven config profiles over the facade, ctx-canvas-04) ---
# Load the security (XSIAM-style) lens; scope search to threat/vuln/incident sources
python -c "from tools.cortex import load_domain_profile, list_domain_names; print(list_domain_names()); print(load_domain_profile('security').sources)"

# --- MCP server (cortex_server.py: 8 cortex_* tools, ctx-expose-01) ---
# Start the Cortex MCP server over stdio (cortex_search/ask/complete/reason/classify/extract/govern/agent_launch)
# NOTE (ctx-reach-03): .mcp.json launches ONLY icdev-unified, which serves all 8
# of these from TOOL_REGISTRY — that is how they are reached in this repo. This
# standalone command is the BOUNDED alternative for an external / air-gapped MCP
# client that must see only the Cortex family. Both serve the same handlers.
python tools/mcp/cortex_server.py

# --- REST API v1 (rest_v1.py folded onto the /cortex blueprint, ctx-expose-02) ---
# POST JSON to the versioned surface (identity derived server-side; only `domain` is caller-supplied):
#   POST /cortex/api/v1/search   {"query": "...", "top_k": 5, "strategy": "auto", "domain": "security"}
#   POST /cortex/api/v1/ask      {"question": "...", "mode": "auto", "summarize": true}
#   POST /cortex/api/v1/complete {"prompt": "...", "system_prompt": "..."}
#   POST /cortex/api/v1/reason   {"prompt": "...", "mode": "cot"}   # mode: cot | debate | council
#   POST /cortex/api/v1/classify {"text": "...", "labels": ["a", "b"]}
#   POST /cortex/api/v1/extract  {"text": "...", "schema": {"type": "object"}}
#   POST /cortex/api/v1/govern   {"text": "...", "retrieval": false}
#   POST /cortex/api/v1/agent    {"goal": "...", "mode": "auto"}   # mode: auto | team | single | graph
#     team:   {"mode": "team", "roles": ["ai_developer"]}          -> data.instance_id, poll /coworker/<id>
#     graph:  {"mode": "graph", "graph": {"workflow_id": "full_sdlc", "inputs": {...}}} -> data.run_id
#     Scope cortex:agent — NEVER in the default grant (it is the one op that makes the platform ACT).
#     Read `launched` FIRST: a provider that cannot serve native tool-use returns 200 +
#     {"launched": false, "degraded": true, "reason": ...} rather than a 5xx.
#     `tools`/`tool_handlers`/`rubric`/`webhook_url` are NOT accepted from the wire —
#     tool-bearing work belongs in graph mode, where Studio authorizes tools per node.
# Governed ops return 403 + serialized GovernanceReport on a TRUST block; 400 on validation; 422 unanswerable.
#   GET  /cortex/api/v1/health   (unauthenticated liveness — status only)

# --- Service keys (service_keys.py: external-caller auth, ctx-expose-02) ---
# Issue a scoped, tenant-bound icdev_ctx_ key for an external consumer (raw key shown ONCE)
python -m tools.cortex.service_keys create --label compass --tenant compass --scopes cortex:search,cortex:ask,cortex:complete,cortex:govern --ceiling CUI --json
python -m tools.cortex.service_keys list --json
python -m tools.cortex.service_keys revoke --key-id <id> --json
# External callers send the key as `Authorization: Bearer icdev_ctx_...` on
# /cortex/api/v1/* and /api/databridge/v1/* ONLY; tenant/classification bind server-side.

# --- Client SDK (client.py — vendored into compass/idea_lab, ctx-expose-06) ---
python -c "from tools.cortex.client import CortexClient; c = CortexClient('http://localhost:5050', 'icdev_ctx_...'); print(c.is_available())"
# .reason() and .agent() (hgx-cx-02) — reason had an endpoint but no client method:
python -c "from tools.cortex.client import CortexClient; c = CortexClient('http://localhost:5050', 'icdev_ctx_...'); print(c.reason('is this design sound?', mode='debate'))"
python -c "from tools.cortex.client import CortexClient; c = CortexClient('http://localhost:5050', 'icdev_ctx_...'); print(c.agent('run the SDLC', mode='graph', workflow_id='full_sdlc'))"

# --- DataBridge feeds (IRIS stub, ctx-expose-05) ---
#   GET  /api/databridge/v1/iris/staffing_alignment     (scope databridge:iris:read)
#   POST /api/databridge/v1/iris/performance_reviews    (scope databridge:iris:write)
# Stub rows carry metadata.stub=true until the vendor publishes the IRIS API
# (flip with IRIS_STUB_MODE=false + IRIS_BASE_URL + IRIS_API_KEY).
```

Related MCP tools (RAG taxonomy shared by the analyst/search routers): `query_classify`
(4-label taxonomy: fact_single/summary/reasoning/unanswerable), `crag_benchmark_run`
(CRAG evaluation campaign with hallucination-penalizing scoring).

### Retrieval toggle measurement (oss-meas-01-d2)

```bash
# Which toggles can a retrieval benchmark actually score?
python tools/rag/toggle_harness.py --probe
python tools/rag/toggle_harness.py --probe --json
python tools/rag/toggle_harness.py --list

# Prove the isolation reaches the loader the retriever calls (and restores after)
python tools/rag/toggle_harness.py --verify rerank --json

# Benchmark ONE toggle in isolation. Exits 3 if the toggle is NOT-WIRED.
python tools/rag/rag_benchmark.py --toggle rerank --json

# Control arm + one isolated arm per wired toggle, with per-metric deltas
python tools/rag/rag_benchmark.py --sweep
python tools/rag/rag_benchmark.py --sweep --only rerank,binary_prefilter --json

# A/B the served ordering against reflective reranking over the SAME candidates
# (trust-self-02). One retrieval per query, both arms rank that one list, so the
# delta is the reordering and nothing else. Records the number; asserts nothing.
python tools/rag/rag_benchmark.py --reflective-ab --limit 12 --json
python tools/rag/rag_benchmark.py --reflective-ab --max-candidates 3
```

```
# --probe also reports ADOPTION, which is orthogonal to the verdict: WIRED says
# flipping the toggle COULD change retrieval, not that the committed config ever
# flips it. reflective_rerank sat at WIRED while enabled:false made it inert on
# every surface. UNADOPTED / ADOPTED-GLOBAL / ADOPTED [surfaces], read from
# args/rag_config.yaml on disk — never through $ICDEV_RAG_CONFIG, so a sweep arm
# cannot report itself as shipped-on.
#
# --reflective-ab reports `unmeasurable_reflection_degraded` when the reflection
# model was never actually reached. That run's 0.0 delta is not evidence of "no
# benefit", and recording it as one is a DROP decision on evidence that does not
# exist.
```

### Adaptive complexity pre-routing measurement (agx-rag-01 / trust-self-03)

```bash
# Score the skip/single_pass/decompose decision against the committed golden
# query mix (args/rag/golden_query_set.yaml). Heuristic-only by default, so the
# numbers are reproducible offline and identical run to run.
python tools/rag/adaptive_router.py --measure
python tools/rag/adaptive_router.py --measure --json

# Same mix through the live cheap-tier classifier (rag_complexity_classify)
python tools/rag/adaptive_router.py --measure --llm

# Measure as a surface that does NOT require citations, so the skip route is
# available. Cortex is the citation-required case and can never skip.
python tools/rag/adaptive_router.py --measure --no-citations
```

```
# `classifier_sources` in the output reports where each decision ACTUALLY came
# from, not what --llm asked for: classify_complexity falls back to the keyword
# heuristic on any LLM failure and says so only in a debug log, so without that
# tally a fallback run reports heuristic numbers under an LLM label.
#
# The consumer is tools/cortex/search_service.py::search_rag, gated on
# rag.adaptive_routing.enabled (default off = the unchanged single pass). That
# adoption is what moved the toggle from WRAPPER-UNADOPTED to WIRED — the
# wrapper sits ABOVE RAGRetriever, so only a caller could ever reach it.
```

```
# Why the probe exists: an UNWIRED toggle and a wired-but-useless toggle both
# measure as a zero delta. Reporting a number for the first turns "never
# connected" into an evidence-backed "DROP". So --toggle/--sweep refuse to
# benchmark a toggle whose consumer is not in the import closure of
# tools/rag/retriever.py, and say NOT-WIRED instead.
#
# As of oss-meas-01-d2, of the five toggles oss-meas-01 names:
#   WIRED     rerank, binary_prefilter        (measurable)
#   NOT-WIRED reflective_rerank (agx-rag-02), adaptive_routing (agx-rag-01),
#             auto_indexer — 0 non-test import sites; auto_indexer is also
#             ingest-side, so it cannot move a retrieval metric even once wired.
#
# Both have since been given callers, so re-run the probe rather than reading
# the line above as current state:
#   reflective_rerank  WIRED by oss-meas-01 inside retriever.search() step 5b
#   adaptive_routing   WIRED by trust-self-03 at tools/cortex/search_service.py
#                      ::search_rag. It is WRAPPER-shaped, so the probe answers
#                      "has a caller adopted it?", not "is it in the closure?"
#   auto_indexer       still CLI-UNSCHEDULED, and correctly so — it is a CLI.
#
# Isolation never writes args/rag_config.yaml. It writes a temp config and sets
# ICDEV_RAG_CONFIG (tools/rag/config_path.py), because this checkout is shared
# with other agent sessions and the kanban scheduler.
```

---

## Ops Hub Canvas (OHC) — Phase 71

```bash
# Adapter health check (all 11 adapters: 6 OSS + 5 CSP)
python tools/ops_hub/cli.py --health --json

# CI/CD gate — exits 1 if overall ops status is critical
python tools/ops_hub/cli.py --gate

# Per-adapter status table
python tools/ops_hub/cli.py --adapters

# Initialise OHC database (creates 7 tables in data/ohc_canvas.db)
python tools/ops_hub/db/init_db.py

# Apply migration 120
python tools/db/migrations/120_ops_hub/up.py

# Seed Kanban tasks (48 tasks across 8 epics)
python _seed_ohc_kanban.py
```

## SRE Tools — SLO Manager, Runbook Executor, Incident Commander

```bash
# SLO Manager
python tools/sre/slo_manager.py --define --name "api_latency_p99" --target 0.999 --window 30 --json  # Define an SLO
python tools/sre/slo_manager.py --record --slo-name "api_latency_p99" --value 0.9995 --json          # Record SLO measurement
python tools/sre/slo_manager.py --dashboard --json                                     # SLO dashboard overview
python tools/sre/slo_manager.py --gate                                                 # Gate check (CI/CD)

# Runbook Executor
python tools/sre/runbook_executor.py --register --name "restart_service" --alert-pattern "service_down" --json  # Register runbook
python tools/sre/runbook_executor.py --execute --runbook-id rb-xxx --dry-run --json    # Execute runbook (dry run)
python tools/sre/runbook_executor.py --list --json                                     # List registered runbooks
python tools/sre/runbook_executor.py --gate                                            # Gate check (CI/CD)

# Incident Commander
python tools/sre/incident_commander.py --declare --title "API outage" --severity sev1 --json  # Declare incident
python tools/sre/incident_commander.py --update --incident-id inc-xxx --status mitigated --json  # Update incident status
python tools/sre/incident_commander.py --dashboard --json                              # Incident dashboard
python tools/sre/incident_commander.py --gate                                          # Gate check (CI/CD)
```

## Redaction & Data Protection (Phase 70 — D-RDT-1)

```bash
# Detector — PII/sensitive data detection (regex + Ollama NER + deny-lists)
python tools/redaction/detector.py --detect "John Smith SSN 123-45-6789" --json       # Detect PII in text
python tools/redaction/detector.py --detect-file /path/to/file.txt --json             # Detect PII in file
python tools/redaction/detector.py --list-entities --json                              # List all supported entity types
python tools/redaction/detector.py --health --json                                     # Health check
python tools/redaction/detector.py --health --gate                                     # Gate check (CI/CD)

# NER Recognizer — Ollama gemma3 + regex for PERSON/ORGANIZATION (air-gap safe)
python tools/redaction/ner_recognizer.py --extract "John Smith from DISA" --json       # Extract named entities
python tools/redaction/ner_recognizer.py --no-ollama --extract "text" --json           # Regex-only mode
python tools/redaction/ner_recognizer.py --health --json                               # Health check

# Anonymizer — IL-aware anonymization (surrogate, redact, mask, hash)
python tools/redaction/anonymizer.py --anonymize "sensitive text" --json               # Anonymize (metadata only)
python tools/redaction/anonymizer.py --anonymize "text" --show-text --json             # Anonymize (show output)
python tools/redaction/anonymizer.py --anonymize "text" --il IL6 --json               # Anonymize at IL6 (hard redact)
python tools/redaction/anonymizer.py --session abc123 --anonymize "text" --json        # Consistent surrogates per session
python tools/redaction/anonymizer.py --health --json --gate                            # Gate check

# GovCon Sanitizer — pre-LLM hook for proposal/sensitive content
python tools/redaction/govcon_sanitizer.py --sanitize "proposal text" --json           # Sanitize for LLM
python tools/redaction/govcon_sanitizer.py --sanitize "text" --show-text --json        # Show sanitized output
python tools/redaction/govcon_sanitizer.py --sanitize "text" --il IL5 --json           # Specify impact level
python tools/redaction/govcon_sanitizer.py --sanitize "text" --local-only --json       # Simulate local routing
python tools/redaction/govcon_sanitizer.py --health --json --gate                      # Gate check

# Pulse Sanitizer — case study de-identification
python tools/redaction/pulse_sanitizer.py --sanitize-article --title "T" --body "B" --json  # Sanitize article
python tools/redaction/pulse_sanitizer.py --health --json --gate                       # Gate check

# GovCon Recognizers — custom Presidio-compatible recognizer definitions
python tools/redaction/govcon_recognizers.py --list --json                             # List recognizer definitions

# Registry — conversation-scoped surrogate mapping
python tools/redaction/registry.py --session abc123 --list --json                      # List session mappings
python tools/redaction/registry.py --cleanup --json                                    # Remove expired entries
python tools/redaction/registry.py --health --json                                     # Health check

# DB PII Scanner — scan proposal tables for PII density
python tools/redaction/db_scanner.py --scan --json                                     # Scan all GovCon tables
python tools/redaction/db_scanner.py --scan --table proposal_knowledge_base --json     # Scan specific table
python tools/redaction/db_scanner.py --scan --sample-size 50 --json                    # Custom sample size
python tools/redaction/db_scanner.py --health --json --gate                            # Gate check

# ── ICDEV™ Studio (Phase 72) ──────────────────────────────────────────────

# Studio DB Init — create studio_* tables
python tools/studio/init_db.py --json                                                  # Init tables (idempotent)
python tools/studio/init_db.py --verbose                                               # Verbose output

# Workflow Editor — tool catalog, templates, CRUD
python tools/studio/workflow_editor.py --json catalog                                  # List tool catalog
python tools/studio/workflow_editor.py --json templates                                # List built-in workflow templates
python tools/studio/workflow_editor.py --json list                                     # List saved studio workflows
python tools/studio/workflow_editor.py --json get <workflow_id>                        # Get workflow by ID

# Run Memory — run-scoped shared state for workflow steps (dwo-mem-01)
# Inside a step the runner sets ICDEV_RUN_ID, so --run-id may be omitted.
python tools/studio/run_memory.py --run-id <run_id> --set artifact --value '{"path":"x.pdf"}'   # Write a key
python tools/studio/run_memory.py --run-id <run_id> --get artifact                     # Read a key
python tools/studio/run_memory.py --run-id <run_id> --all                              # Dump every key
python tools/studio/run_memory.py --run-id <run_id> --delete artifact                  # Delete a key

# Form Builder — create custom forms with JSON Schema output
python tools/studio/form_builder.py --json field-types                                     # List field types
python tools/studio/form_builder.py --json templates                                       # List form templates
python tools/studio/form_builder.py --json list                                            # List saved forms
python tools/studio/form_builder.py --json get <form_id>                                   # Get form by ID

# Case Manager — FSM lifecycle, Kanban board, SLA tracking
python tools/studio/case_manager.py --json templates                                       # List lifecycle templates
python tools/studio/case_manager.py --json types                                           # List case types
python tools/studio/case_manager.py --json cases                                           # List all cases
python tools/studio/case_manager.py --json board <type_id>                                 # Get Kanban board

# Dashboard Builder — custom widget layouts with role defaults
python tools/studio/dashboard_builder.py --json widgets                                    # List 15 widget types
python tools/studio/dashboard_builder.py --json roles                                      # List role defaults (pm, isso, developer)
python tools/studio/dashboard_builder.py --json list                                       # List saved dashboards
python tools/studio/dashboard_builder.py --json create-default pm                          # Create PM default dashboard

# Automation Builder — event-driven rules (trigger → condition → action)
python tools/studio/automation_builder.py --json triggers                                   # List trigger types
python tools/studio/automation_builder.py --json operators                                  # List condition operators
python tools/studio/automation_builder.py --json actions                                    # List action types
python tools/studio/automation_builder.py --json templates                                  # List automation templates
python tools/studio/automation_builder.py --json list                                       # List saved automations
python tools/studio/automation_builder.py --json runs                                       # List recent runs
python tools/studio/automation_builder.py --json simulate <automation_id>                   # Dry-run simulation
python tools/studio/automation_builder.py --json simulate <automation_id> --event '{...}'   # Dry-run against a specific event
python tools/studio/automation_builder.py --json trigger <automation_id> --event '{...}'    # Fire for real (run_workflow starts a run)

# NL App Builder — describe what you want, get a working app
python tools/studio/nl_app_builder.py --json extract "description of app"                  # Extract capabilities
python tools/studio/nl_app_builder.py --json create "description" --name my-app            # Create builder session
python tools/studio/nl_app_builder.py --json refine <session_id> --classification IL5      # Refine session

# Dashboard Pages
# /studio/app-builder  — NL App Builder (describe → get app)
# /studio/workflows    — Visual Workflow Builder (drag-drop DAG editor)
# /studio/forms        — Form Builder (drag-drop field editor)
# /studio/cases        — Case Management (Kanban board + lifecycle)
# /studio/automations  — Automation Studio (trigger → condition → action)
# /studio/dashboards   — Dashboard Builder (widget grid + role defaults)
# /studio/marketplace  — Marketplace Storefront (browse, search, install)
```

# ── Oracle Anticipatory Agent (Phase Oracle) ──────────────────────────────

```bash
# Oracle Reflex — run all lenses as Genesis daemon reflex (DaemonBase protocol)
# Called automatically by GenesisDaemon; not intended for direct CLI invocation.
# python tools/oracle/oracle_reflex.py  (no standalone CLI — use via run(config, trust))

# Kanban Bridge — sync promoted GKP anticipation reports to suggested kanban tasks
python tools/oracle/kanban_bridge.py --sync --json                                     # Batch-sync all promoted anticipation_report GKPs
python tools/oracle/kanban_bridge.py --sync --min-confidence 0.90 --json               # Only high-confidence reports
python tools/oracle/kanban_bridge.py --gate                                             # Gate check: verify DB reachable
python tools/oracle/kanban_bridge.py --gate --json                                      # Gate check (JSON output)

# Individual Lens — Trajectory (architectural forecasting)
python tools/oracle/lens_trajectory.py --json                                           # Run trajectory analysis
python tools/oracle/lens_trajectory.py --gate                                           # Gate check

# Individual Lens — Ecosystem Gap
python tools/oracle/lens_ecosystem_gap.py --json                                        # Run FORGE-layer gap analysis
python tools/oracle/lens_ecosystem_gap.py --gate                                        # Gate check

# Individual Lens — Workflow Patterns
python tools/oracle/lens_workflow_patterns.py --json                                    # Run workflow pattern mining
python tools/oracle/lens_workflow_patterns.py --gate                                    # Gate check

# Individual Lens — Regulatory Anticipation
python tools/oracle/lens_regulatory.py --json                                           # Run regulatory signal crosswalk
python tools/oracle/lens_regulatory.py --gate                                           # Gate check

# Individual Lens — Child App Demand
python tools/oracle/lens_child_app_demand.py --json                                     # Run child-app demand scoring
python tools/oracle/lens_child_app_demand.py --gate                                     # Gate check

# Dashboard API Endpoints (via Flask dashboard on port 5050)
# GET  /api/oracle/predictions                                                          # Query oracle_predictions (filters: lens_id, severity, outcome)
# GET  /api/oracle/lens-status                                                          # Per-lens health: last_run, prediction_count, avg_confidence
# GET  /api/oracle/convergence                                                          # Convergence events with consensus_score and action_taken
```

## Cross-Canvas Integration Commands

```bash
# ── Canvas Orchestrator — Project Management ─────────────────────────────────

# Create a cross-canvas design project
python tools/canvas/orchestrator.py --create --name "ATO Boundary Project"          # Create project linking designs

# List all canvas projects
python tools/canvas/orchestrator.py --list --json                                   # List projects sorted by updated_at

# Link a canvas design to a project
python tools/canvas/orchestrator.py --link --project-id cp-xxx --canvas idc --design-id xxx

# Unlink a canvas design
python tools/canvas/orchestrator.py --unlink --project-id cp-xxx --canvas mdc

# Get compliance summary across all linked canvases
python tools/canvas/orchestrator.py --compliance --project-id cp-xxx --json

# Compute 4-dimension readiness score (completeness, compliance, coverage, risk)
python tools/canvas/orchestrator.py --readiness --project-id cp-xxx --json

# ── Canvas KG Builder — Knowledge Graph ──────────────────────────────────────

# Rebuild KG for a specific canvas design (called automatically on save)
python tools/canvas/kg_builder.py --canvas idc --design-id xxx --json

# ── Canvas Export Utils — Multi-Format Export ────────────────────────────────
# (Library — called by canvas blueprint export routes, not standalone CLI)
# Supported formats: JSON, Markdown, CSV, DrawIO XML, SVG
# All exports include CUI classification banner

# ── Canvas Auto-Remediation ──────────────────────────────────────────────────
# (Library — called by IDC/ODC blueprints on save)
# Confidence-tiered: >=0.7 auto-fix, 0.3-0.7 suggest, <0.3 escalate
# Rate-limited: max 5 auto-fixes per hour

# ── Dashboard API Endpoints (via Flask dashboard) ────────────────────────────
# GET    /api/canvas-projects                                                     # List all canvas projects
# POST   /api/canvas-projects                                                     # Create a canvas project
# GET    /api/canvas-projects/<id>                                                # Get single project
# PATCH  /api/canvas-projects/<id>                                                # Update project
# DELETE /api/canvas-projects/<id>                                                # Delete project
# POST   /api/canvas-projects/<id>/link                                           # Link canvas design
# POST   /api/canvas-projects/<id>/unlink                                         # Unlink canvas design
# GET    /api/canvas-projects/compliance?project_id=<id>                          # Compliance summary
# GET    /api/canvas-projects/<id>/readiness                                      # Readiness score
```

## DDC External Catalog Sync Commands

```bash
# ── DataHub Sync (DDC → DataHub GMS REST API v2) ─────────────────────────────
# Config: args/datahub_config.yaml  |  Env: ICDEV_DATAHUB_URL, ICDEV_DATAHUB_TOKEN

# Dry-run single design (no writes to DataHub)
python tools/data_canvas/sync/datahub_sync.py --design-id <id> --dry-run --json

# Push single design to DataHub
python tools/data_canvas/sync/datahub_sync.py --design-id <id> --json

# Push all designs to DataHub
python tools/data_canvas/sync/datahub_sync.py --all --json

# Push all designs — exit 1 if any errors (CI gate)
python tools/data_canvas/sync/datahub_sync.py --all --gate --json

# ── OpenMetadata Sync (DDC → OpenMetadata REST API v1) ───────────────────────
# Config: args/openmetadata_config.yaml  |  Env: ICDEV_OM_URL, ICDEV_OM_TOKEN

# Dry-run single design (no writes to OpenMetadata)
python tools/data_canvas/sync/openmetadata_sync.py --design-id <id> --dry-run --json

# Push single design to OpenMetadata
python tools/data_canvas/sync/openmetadata_sync.py --design-id <id> --json

# Push all designs to OpenMetadata
python tools/data_canvas/sync/openmetadata_sync.py --all --json

# Push all designs — exit 1 if any errors (CI gate)
python tools/data_canvas/sync/openmetadata_sync.py --all --gate --json

# ── Dashboard API (via DDC blueprint at /data/) ───────────────────────────────
# POST /data/api/sync/datahub        {"design_id": "<id>", "dry_run": false}    # Trigger DataHub sync
# POST /data/api/sync/openmetadata   {"design_id": "<id>", "dry_run": false}    # Trigger OpenMetadata sync
# Omit design_id to sync all designs. dry_run=true for validation without writes.
```


---

## Showcase Commands
```bash
# AI Canvas Demo Runner — 5-act DoD/IC demo across live canvas DBs
python tools/showcase/ai_canvas_demo_runner.py --scenario 1 --audience exec --json
python tools/showcase/ai_canvas_demo_runner.py --scenario 5 --audience tech
```

`tools/showcase/synthetic_data_engine.py` is a **library, not a CLI** — import
`SyntheticDataEngine` / `DOMAINS` from it:

```python
from icdev.tools.showcase.synthetic_data_engine import DOMAINS, SyntheticDataEngine
```


## System Graph Commands
```bash
# Federated graph API (6 sources — 3500+ nodes, 1600+ edges)
# Dashboard page
open http://localhost:5050/system-graph

# REST API
GET /api/system-graph/graph                          # Full graph (5-min cache)
GET /api/system-graph/graph?type=tool                # Filter by node type
GET /api/system-graph/graph?health=error             # Filter by health
GET /api/system-graph/graph?q=kanban                 # Full-text search
GET /api/system-graph/graph?sources=awareness_kg,goals  # Subset of sources
GET /api/system-graph/node/<node_id>                 # Per-node detail + neighbours
GET /api/system-graph/node-types                     # Node type + edge type metadata

# Python direct import
python -c "from tools.system_graph.graph_builder import build_graph; d = build_graph(); print(d['stats'])"
python -c "from tools.system_graph.graph_builder import get_node_detail; print(get_node_detail('<node_id>'))"

# MCP tools (via unified gateway)
#   system_graph_get        — full graph with optional filters
#   system_graph_node_detail — per-node detail
#   system_graph_stats       — source counts + timing
```

## Conflict Mesh Commands
```bash
# ETL — pull from all providers, normalize, and load into sg_conflict_events
python tools/conflict_mesh/etl_pipeline.py --since 2024-01-01 --dry-run --json
python tools/conflict_mesh/etl_pipeline.py --since 2024-01-01 --limit 50 --json
python tools/conflict_mesh/etl_pipeline.py --providers acled gdelt --since 2024-01-01 --json

# Escalation Predictor — score events and surface high-risk predictions
python tools/conflict_mesh/escalation_predictor.py --batch-since 2024-01-01 --threshold 0.7 --json
python tools/conflict_mesh/escalation_predictor.py --event-id acled-12345 --json
python tools/conflict_mesh/escalation_predictor.py --threshold 0.8 --limit 10 --json

# Python API
python -c "from tools.conflict_mesh.mesh_coordinator import MeshCoordinator; from tools.conflict_mesh.providers.acled_provider import ACLEDProvider; c = MeshCoordinator([ACLEDProvider()]); print(len(c.fetch_all()))"
python -c "from tools.conflict_mesh.ml_pattern_engine import MLPatternEngine; e = MLPatternEngine(); print(e.extract_signals('Artillery bombardment kills soldiers', {}))"
```

## STRATEGOS Commands
```bash
# Adversarial Data Validation Pipeline (bias / deepfake / manipulation detection)
python icdev/tools/strategos/adversarial_validator.py --signal '{"id":"s1","source_type":"social_media","content":"..."}' --json
python icdev/tools/strategos/adversarial_validator.py --signals-file signals.json --json --gate
python icdev/tools/strategos/adversarial_validator.py --health --json

# Batch validation (Python API)
python -c "from icdev.tools.strategos.adversarial_validator import validate_signals; print(validate_signals([...]))"
```


## NOC Operations Canvas (NOCC) Commands
```bash
# Initialize NOCC database (PostgreSQL default, SQLite fallback)
python -c "from tools.noc_canvas.db.init_db import init_db; init_db()"

# Dashboard (web UI)
#   GET /noc                  — NOCC home/overview
#   GET /noc/alarms           — Alarm board
#   GET /noc/incidents        — Incident tracker
#   GET /noc/rfcs             — RFC/Change management
#   GET /noc/mops             — MOP library
#   GET /noc/maintenance      — Maintenance windows
#   GET /noc/sla              — SLA dashboard

# REST API
# POST /api/noc/alarms                          — Ingest alarm
# POST /api/noc/alarms/<id>/ack                 — Acknowledge alarm
# POST /api/noc/alarms/<id>/clear               — Clear alarm
# GET  /api/noc/incidents                        — List open incidents
# POST /api/noc/incidents                        — Create incident
# GET  /api/noc/sla                             — SLA records
# GET  /api/noc/overview                        — Aggregated status
# POST /api/noc/mops/generate                   — LLM-generate MOP steps
# POST /api/noc/iqe-query                       — NL→SQL query

# Python direct import
python -c "from tools.noc_canvas.alarm_correlator import get_active_alarms; from tools.noc_canvas.db.init_db import get_connection; c = get_connection(); print(get_active_alarms(c))"
python -c "from tools.noc_canvas.sla_predictor import get_sla_dashboard; from tools.noc_canvas.db.init_db import get_connection; c = get_connection(); import json; print(json.dumps(get_sla_dashboard(c), indent=2, default=str))"
python -c "from tools.noc_canvas.noc_aggregator import get_noc_overview; from tools.noc_canvas.db.init_db import get_connection; c = get_connection(); import json; print(json.dumps(get_noc_overview(c), indent=2, default=str))"

# Genesis reflexes (run manually or via Genesis daemon)
python tools/genesis/reflexes/nocc_alarm_triage.py     # 2h cadence: auto-incident from alarm storms
python tools/genesis/reflexes/nocc_sla_watcher.py      # 4h cadence: SLA breach detection
python tools/genesis/reflexes/bgp_route_monitor.py     # 1h cadence: BGP session monitoring
python tools/genesis/reflexes/peering_health_monitor.py  # 6h cadence: PeeringDB re-sync + RPKI re-validate

# Skill
# /icdev-noc  — NOC operations brief (alarms, incidents, SLA, maintenance, peering)
```

## Peering Management Canvas (PMC) Commands
```bash
# Initialize PMC database (PostgreSQL default, SQLite fallback)
python -c "from tools.pmc_canvas.db.init_db import init_db; init_db()"

# Dashboard (web UI)
#   GET /pmc                  — PMC home/overview
#   GET /pmc/peers            — BGP peer registry
#   GET /pmc/peers/<id>       — Peer detail + RPKI + config
#   GET /pmc/ix               — Internet Exchange memberships
#   GET /pmc/rpki             — RPKI validation dashboard
#   GET /pmc/policies         — Route policies
#   GET /pmc/requests         — Peering requests pipeline

# REST API
# GET  /api/pmc/overview                         — Aggregated metrics
# POST /api/pmc/peers                            — Add BGP peer
# GET  /api/pmc/peers/<id>/evaluate              — Run decision engine
# POST /api/pmc/peers/<id>/validate-rpki         — Validate RPKI
# GET  /api/pmc/peers/<id>/generate-config       — Generate BGP config
# GET  /api/pmc/peers/<id>/rpsl                  — Generate RPSL aut-num
# POST /api/pmc/peers/<id>/sync                  — Sync from PeeringDB
# GET  /api/pmc/rpki/report                      — Full RPKI validation report
# POST /api/pmc/iqe-query                        — NL→SQL query

# PeeringDB client
python -c "from tools.pmc_canvas.peeringdb_client import get_asn_info; import json; print(json.dumps(get_asn_info(13335), indent=2))"

# RPKI validation
python -c "from tools.pmc_canvas.rpki_validator import validate_prefix; import json; print(json.dumps(validate_prefix('1.1.1.0/24', 13335), indent=2))"
python -c "from tools.pmc_canvas.rpki_validator import generate_roa_report; print(generate_roa_report(1, [{'prefix':'1.1.1.0/24','origin_asn':13335}]))"

# RPSL generator
python -c "from tools.pmc_canvas.rpsl_generator import generate_aut_num; print(generate_aut_num(64512, [], []))"

# BGP config generator (all 6 OS types)
python -c "from tools.pmc_canvas.bgp_config_generator import generate_peer_session; print(generate_peer_session({'asn':13335,'org_name':'Cloudflare'}, 64512, 'ios_xr', '198.51.100.1'))"

# Peering decision engine
python -c "from tools.pmc_canvas.peering_decision_engine import evaluate_peer; import json; print(json.dumps(evaluate_peer({'asn':13335,'org_name':'Cloudflare','traffic_ratio':0.9,'ipv4_prefix_count':1200,'ipv6_prefix_count':300,'irr_as_set':'AS13335'}, 64512, [], []), indent=2))"

# Transit pricing benchmark
python tools/pmc_canvas/transit_pricing_benchmark.py --benchmark --region na --json
python tools/pmc_canvas/transit_pricing_benchmark.py --roi --json
python tools/pmc_canvas/transit_pricing_benchmark.py --ix --json

# Config
#   args/pmc_config.yaml  — Decision engine weights, RPKI thresholds, PeeringDB sync cadence
```

## ISP Carrier Tools Commands
```bash
# ISP Capacity Planner
python -c "from tools.network.isp_capacity_planner import model_traffic_growth; import json; print(json.dumps(model_traffic_growth([100,120,145,180], 12), indent=2))"
python -c "from tools.network.isp_capacity_planner import dwdm_capacity_analysis; import json; print(json.dumps(dwdm_capacity_analysis(fiber_pairs=4, modulation='400G-DP-16QAM', grid='C-band-50GHz', current_channels_used=60), indent=2))"
python -c "from tools.network.isp_capacity_planner import dark_fiber_roi; import json; print(json.dumps(dark_fiber_roi(route_km=120, fiber_pairs=4, iru_cost_usd=800000, annual_lease_usd=120000, lease_term_years=20), indent=2))"
python -c "from tools.network.isp_capacity_planner import capacity_planning_summary; import json; print(json.dumps(capacity_planning_summary('Dallas PoP', [100,120,145,180], 4, '400G-DP-16QAM'), indent=2))"

# FCC Compliance
python tools/network/fcc_compliance.py --calea --json
python tools/network/fcc_compliance.py --part36 --json
python tools/network/fcc_compliance.py --nanp --json
python tools/network/fcc_compliance.py --e911 --json
python tools/network/fcc_compliance.py --all --json

# Telco RFP Adapter (E-Rate / BEAD / RDOF)
python tools/govcon/telco_rfp_adapter.py --form470 --json
python tools/govcon/telco_rfp_adapter.py --bead --json
python tools/govcon/telco_rfp_adapter.py --rdof --json

# DataBridge Connectors
python -c "from tools.databridge.connectors.equinix_ecx_connector import EquinixECXConnector; c = EquinixECXConnector(); print(c.health_check())"
python -c "from tools.databridge.connectors.megaport_connector import MegaportConnector; c = MegaportConnector(); print(c.health_check())"

# NDC Config Generator — IOS-XR and Nokia SR OS (added as OS types)
python -c "from tools.network.config_generator import _DEFAULT_OS, _IFACE_GEN; print([k for k in _DEFAULT_OS if 'ios_xr' in _DEFAULT_OS[k] or 'nokia' in _DEFAULT_OS[k]])"
```

## Circuit & Capacity Canvas (CCC) Commands
```bash
# Initialize CCC database (PostgreSQL default, SQLite fallback)
python tools/ccc_canvas/db/init_db.py

# Circuit aggregator overview
python -c "from tools.ccc_canvas.circuit_aggregator import get_ccc_overview; import json; print(json.dumps(get_ccc_overview(), indent=2))"

# Capacity analysis — run for all active circuits
python -c "from tools.ccc_canvas.db.init_db import get_connection; from tools.ccc_canvas.capacity_engine import run_all_circuits; conn=get_connection(); import json; print(json.dumps(run_all_circuits(conn), indent=2))"

# LOA workflow — generate document
python -c "from tools.ccc_canvas.db.init_db import get_connection; from tools.ccc_canvas.loa_workflow import generate_loa_text; conn=get_connection(); print(generate_loa_text(conn, 1))"

# IQE adapter — query circuits
python -c "from tools.iqe.adapters.ccc import handle_query; from tools.ccc_canvas.db.init_db import get_connection; conn=get_connection(); import json; print(json.dumps(handle_query(conn, 'show active circuits'), indent=2))"

#   GET /ccc                  — CCC overview
#   GET /ccc/circuits         — circuit inventory
#   GET /ccc/cross-connects   — cross-connect list
#   GET /ccc/loa              — LOA request tracker
#   GET /ccc/capacity         — capacity planning
#   GET /ccc/dwdm             — DWDM span inventory
#   POST /api/ccc/circuits    — add circuit
#   POST /api/ccc/loa         — create LOA request
#   GET  /api/ccc/capacity/report — run capacity analysis
#   POST /api/ccc/iqe-query   — IQE natural-language query
```

## DDoS & Security Ops Canvas (DSOC) Commands
```bash
# Initialize DSOC database (PostgreSQL default, SQLite fallback)
python tools/dsoc_canvas/db/init_db.py

# DSOC overview — active mitigations, RTBH, scrubbing utilization, threats
python -c "from tools.dsoc_canvas.db.init_db import get_connection; from tools.dsoc_canvas.dsoc_aggregator import get_dsoc_overview; import json; conn=get_connection(); print(json.dumps(get_dsoc_overview(conn), indent=2))"

# Trigger RTBH blackhole for a prefix (RFC 5635)
python -c "from tools.dsoc_canvas.db.init_db import get_connection; from tools.dsoc_canvas.rtbh_manager import trigger_rtbh; conn=get_connection(); import json; r=trigger_rtbh(conn,'192.0.2.0/24','volumetric_attack'); conn.commit(); print(json.dumps(r, indent=2))"

# Auto-expire RTBH entries whose timer has elapsed
python -c "from tools.dsoc_canvas.db.init_db import get_connection; from tools.dsoc_canvas.rtbh_manager import auto_expire_rtbh; conn=get_connection(); n=auto_expire_rtbh(conn); conn.commit(); print(f'Expired: {n}')"

# Generate IOS-XR flowspec config for a rule dict
python -c "from tools.dsoc_canvas.flowspec_engine import generate_ios_xr_flowspec; r={'rule_name':'block-udp-1900','destination_prefix':'10.0.0.0/8','protocol':'udp','dst_port':'1900','action':'drop','rate_limit_bps':0}; print(generate_ios_xr_flowspec(r))"

# Generate JunOS flowspec config
python -c "from tools.dsoc_canvas.flowspec_engine import generate_junos_flowspec; r={'rule_name':'block-udp-1900','destination_prefix':'10.0.0.0/8','protocol':'udp','dst_port':'1900','action':'drop'}; print(generate_junos_flowspec(r))"

# IQE adapter — query threats
python -c "from tools.iqe.adapters.dsoc import handle_query; from tools.dsoc_canvas.db.init_db import get_connection; import json; conn=get_connection(); print(json.dumps(handle_query(conn, 'show high confidence threats'), indent=2))"

# Genesis reflex — circuit capacity monitor (4h cadence)
python tools/genesis/reflexes/circuit_capacity_monitor.py

# Genesis reflex — NOCC alarm triage (2h cadence)
python tools/genesis/reflexes/nocc_alarm_triage.py

# Genesis reflex — NOCC SLA watcher (4h cadence)
python tools/genesis/reflexes/nocc_sla_watcher.py

# Genesis reflex — BGP route monitor (1h cadence)
python tools/genesis/reflexes/bgp_route_monitor.py

# Genesis reflex — Peering health monitor (6h cadence)
python tools/genesis/reflexes/peering_health_monitor.py

#   GET /dsoc                 — DSOC overview
#   GET /dsoc/flowspec        — BGP flowspec rules
#   GET /dsoc/rtbh            — RTBH blackhole entries
#   GET /dsoc/scrubbing       — scrubbing center inventory
#   GET /dsoc/threats         — threat intelligence feed
#   GET /dsoc/mitigations     — active mitigation tracker
#   POST /api/dsoc/rtbh       — trigger RTBH for a prefix
#   POST /api/dsoc/rtbh/<id>/withdraw — withdraw RTBH entry
#   POST /api/dsoc/flowspec   — create flowspec rule
#   PUT  /api/dsoc/flowspec/<id>/withdraw — withdraw flowspec rule
#   POST /api/dsoc/mitigations/<id>/complete — complete mitigation
#   POST /api/dsoc/iqe-query  — IQE natural-language query
```

## Phase 4 — Commercial & Regulatory Intelligence Commands
```bash
# FCC compliance assessment (CALEA, Part 36, NANP, E-911)
python tools/network/fcc_compliance.py --calea --json
python tools/network/fcc_compliance.py --part36 --json
python tools/network/fcc_compliance.py --nanp --json
python tools/network/fcc_compliance.py --e911 --json
python tools/network/fcc_compliance.py --all --json

# FCC compliance via dashboard API
#   GET /api/network/fcc/calea    — CALEA lawful-intercept checklist
#   GET /api/network/fcc/part36   — Part 36 separations assessment
#   GET /api/network/fcc/nanp     — NANP number inventory
#   GET /api/network/fcc/e911     — E-911 capability check
#   GET /api/network/fcc/all      — All checks combined

# Telco RFP adapter (E-Rate Form 470, BEAD, RDOF)
python tools/govcon/telco_rfp_adapter.py --form470 --json
python tools/govcon/telco_rfp_adapter.py --bead --json
python tools/govcon/telco_rfp_adapter.py --rdof --json

# Telco RFP via dashboard API
#   POST /api/govcon/telco/form470  — Parse Form 470 and score bid
#   GET  /api/govcon/telco/bead     — BEAD compliance matrix
#   GET  /api/govcon/telco/rdof     — RDOF eligibility scoring

# Transit pricing benchmark (PMC)
python tools/pmc_canvas/transit_pricing_benchmark.py --benchmark --region na --json
python tools/pmc_canvas/transit_pricing_benchmark.py --benchmark --region eu --json
python tools/pmc_canvas/transit_pricing_benchmark.py --roi --json

# Transit pricing via dashboard API
#   GET  /api/pmc/transit/benchmark?region=na&speed_gbps=10 — Market benchmark
#   POST /api/pmc/transit/roi — Peering-vs-transit NPV analysis
#   GET  /pmc/transit         — Transit pricing dashboard page
```

## ANVIL Co-Worker Engine (ACE) Commands
```bash
# Launch a new ACE problem-solving instance
python -m icdev.tools.ace.controller --launch 'problem text' [--json]

# Check status of a running ACE instance
python -m icdev.tools.ace.controller --status <instance_id> [--json]

# Abort a running ACE instance
python -m icdev.tools.ace.controller --abort <instance_id>

# List available ACE roles
python -m icdev.tools.ace.controller --list-roles

# Seed ACE kanban tasks (dry-run preview)
python tools/kanban/seed_ace_kanban.py [--dry-run]

# Environment variable to enable /coworker/ canvas
ICDEV_ACE_ENABLED=true
```

### Cross-Repo SME Consult (MCP tools, idea_lab bridge)
Three MCP tools let an external process (primary caller: the standalone
`idea_lab` project's Specialist advisor) consult ICDEV without launching a
full async ACE team session:

```bash
# ace_persona_query — one-shot, persona-informed answer from a single ACE
# role (or an on-the-fly generated persona if role_id is omitted/unknown
# but domain_description is given). tools/ace/persona_query.py + persona_generator.py
# council_query — pressure-test a decision through the 5-perspective LLM
# Council + chairman synthesis. tools/llm/chain_orchestrator.py::invoke_council()
# writeguard_analyze — full deterministic WriteGuard quality check, zero LLM.
# tools/pulse/writeguard.py::handle_writeguard_analyze

# Registered in tools/mcp/tool_registry.py; handlers in tools/mcp/gap_handlers.py
# (ace_persona_query, council_query) and tools/pulse/writeguard.py (writeguard_analyze).
# See tools/manifest/ace.md, tools/manifest/llm-chain-orchestration.md, and
# tools/manifest/writeguard-writing-quality-analysis.md for full details.
```

### Divergent Ideation (Divergence)
Generative counterpart to the Council: widen the option space, then score. OPT-IN
per function (`chain_orchestration.divergence.enabled` default false).

```bash
# Generate a raw idea pool (single isolated generative fan-out)
python tools/llm/chain_orchestrator.py --divergence --function <fn> --prompt "<problem>" --json

# Score + trap-flag the pool (the separate critic; novelty/viability/fit + advisory traps)
python tools/quality/divergence_critic.py --function <fn> --pool-file <pool.md> --json

# Headless skill (both halves, documented steps)
python tools/skills/invoke.py --exec icdev-divergence -- --function <fn> --prompt "<problem>"

# MCP tool: divergence_invoke  params: function, prompt, system_prompt?, score?(bool)
#   Returns: {content, chain_mode, models_used, total_cost_usd, trace_id, stop_reason,
#             rounds, scored?, trap_warnings?}  — advisory traps, never a blocker.
# Registered in tools/mcp/tool_registry.py; handler tools/mcp/gap_handlers.py::handle_divergence_invoke
```

### Cross-Repo Compass Bridge (MCP tools, optional)
Two MCP tools let ICDEV's CPMP/GovCon modules query a separate, standalone
Compass app (`C:\AI\standalone\compass` — LCAT/staffing/rate-card automation)
instead of duplicating its taxonomy. Reverse direction of Compass's own
bridge into ICDEV (dic_search/dic_ingest/ace_persona_query/council_query).

```bash
# compass_lcat_lookup — best-matching BLS SOC labor category for a task
# description or resume, via Compass's live LCAT taxonomy.
# compass_staffing_summary — Compass's current staffing matrix (personnel
# vs. resume-matched LCAT compliance, mismatch/unresolved counts).

# Registered in tools/mcp/tool_registry.py; handlers + HTTP client in
# tools/integrations/compass_mcp_handlers.py + compass_client.py.
# Optional: degrades to an error dict (never raises) if Compass isn't
# configured/reachable. Config: args/compass_integration.yaml.
# See tools/manifest/govcon-intelligence.md for full details.
```

## Autonomous Capability Foundry (ACF) Commands
```bash
# ── Engine — run one harvest→synth→novelty→score→CoD→spec→task-graph→seed cycle ──
python tools/foundry/engine.py --run --json              # full cycle (seeds kanban)
python tools/foundry/engine.py --run --dry-run --json    # full pipeline, seeds NOTHING
python tools/foundry/engine.py --run --max-concepts 3 --json   # override per-cycle cap
python tools/foundry/engine.py --status --json           # recent runs + pipeline + rate_limits

# ── Pipeline stages (standalone, for debugging one stage) ──
python tools/foundry/harvester.py --run-id <id> --json                  # Stage 1 — collect signals
python tools/foundry/novelty_gate.py --concept-json '{...}' --json      # Stage 3 — dedup verdict
python tools/foundry/novelty_gate.py --catalog --json                   #   rebuild dedup catalog
python tools/foundry/spec_generator.py --concept-id <id> --json         # Stage 6 — spec + contract
python tools/foundry/spec_generator.py --concept-json '{...}' --no-persist --json
python tools/foundry/task_graph.py --contract-json '{...}' --json        # Stage 7 — epic skeleton

# ── Schema ──
python -c "from tools.foundry.db.init_db import init_db; init_db()"     # 6 append-only foundry_* tables

# ── Genesis reflex (12h continuous-autonomy loop) ──
python tools/genesis/reflexes/foundry_cycle.py            # run one cycle (no-op if flag off)
python tools/genesis/reflexes/foundry_cycle.py --dry-run  # compute cycle, no persistence
python -c "from tools.genesis.reflexes.foundry_cycle import run; print(run({'dry_run': True}, None))"

# ── Dashboard (web UI) ──
#   GET  /foundry                — cycle roll-up + concept pipeline board
#   GET  /foundry/<concept_id>   — concept detail: scores, spec, emitted tasks, outcomes

# ── REST API ──
#   GET  /api/foundry/runs           — recent foundry_runs (JSON)
#   GET  /api/foundry/concepts       — concept list (filter: status, run_id)
#   GET  /api/foundry/concept/<id>   — single concept + spec + tasks + outcomes
#   POST /api/foundry/run            — trigger one cycle ({"dry_run": true} to skip seeding)
#   POST /foundry/api/iqe-query      — plain-English → IQE → rows

# ── IQE collections (read-only, via /foundry widget or /ask-icdev) ──
#   foundry.concepts | foundry.signals | foundry.runs | foundry.outcomes
#   Seed queries: context/iqe/queries/foundry/01-05

# ── MCP gateway (unified) ──
#   foundry_run    {dry_run, max_concepts}  — trigger one cycle
#   foundry_status {limit}                  — read-only pipeline snapshot

# ── Config + feature flag ──
#   args/foundry_config.yaml     — sources, synthesis, novelty, scoring, rate_limits,
#                                  circuit breaker, self_vet, deliberation (CoD), foundry_cycle
ICDEV_FOUNDRY_ENABLED=true       # .env — master toggle for canvas + reflex

# ── Tests ──
pytest tests/foundry/ -v                                          # engine, novelty, spec, task-graph, blueprint
pytest tests/test_foundry_cycle_reflex.py tests/test_foundry_mcp.py tests/test_foundry_harvester.py -v
```

## ANVIL Co-Worker Engine (ACE) Commands

```bash
# Launch a co-worker instance with a problem statement
python -m icdev.tools.ace.controller --launch 'problem text' [--json]

# Check the status of a running co-worker instance
python -m icdev.tools.ace.controller --status <instance_id> [--json]

# Abort a running co-worker instance
python -m icdev.tools.ace.controller --abort <instance_id>

# List available co-worker roles
python -m icdev.tools.ace.controller --list-roles

# Seed ACE-related kanban tasks (use --dry-run to preview)
python tools/kanban/seed_ace_kanban.py [--dry-run]

# Feature flag — enable /coworker/ canvas
ICDEV_ACE_ENABLED=true        # .env — master toggle for ACE canvas + co-worker reflex
```

## Document Intelligence Canvas (DIC) — Notebook

```bash
# Notebook page (NotebookLM-style view)
# URL: http://localhost:5050/document-intelligence/notebook
# URL: http://localhost:5050/document-intelligence/notebook/<collection_id>

# URL ingest (online mode; air-gap returns empty with warning)
# POST /document-intelligence/api/ingest/url
# Body: {"url": "https://...", "collection_id": "my-collection"}

# YouTube transcript ingest (online mode only)
# POST /document-intelligence/api/ingest/youtube
# Body: {"url": "https://youtube.com/watch?v=...", "collection_id": "my-collection"}

# AI output generators (dual-mode: LLM online, deterministic air-gap fallback)
# POST /document-intelligence/api/generate/study-guide
# POST /document-intelligence/api/generate/faq
# POST /document-intelligence/api/generate/timeline
# POST /document-intelligence/api/generate/audio
# Body: {"collection_id": "my-collection"}

# List outputs for a collection
# GET /document-intelligence/api/outputs?collection_id=my-collection

# Get a single output
# GET /document-intelligence/api/outputs/<output_id>

# Mode info (air-gap vs online, available capabilities)
# GET /document-intelligence/api/mode

# Python — generate outputs directly
python -c "from tools.document_intelligence.output_generators import generate_study_guide; import json; print(json.dumps(generate_study_guide('my-collection', 'default'), indent=2))"
python -c "from tools.document_intelligence.output_generators import generate_faq; import json; print(json.dumps(generate_faq('my-collection', 'default', n=10), indent=2))"
python -c "from tools.document_intelligence.output_generators import generate_timeline; import json; print(json.dumps(generate_timeline('my-collection', 'default'), indent=2))"
python -c "from tools.document_intelligence.output_generators import generate_audio_overview; import json; print(json.dumps(generate_audio_overview('my-collection', 'default'), indent=2))"

# Python — ingest URL or YouTube
python -c "from tools.document_intelligence.extractors import extract_url; e = extract_url('https://example.com/page'); print(e.text[:200])"
python -c "from tools.document_intelligence.extractors import extract_youtube; e = extract_youtube('https://youtube.com/watch?v=dQw4w9WgXcQ'); print(e.text[:200])"

# Push any canvas artifact into DIC
python -c "from tools.document_intelligence.canvas_push import push_artifact; print(push_artifact('compliance', 'NIST Report', 'report text here', 'compliance-docs', 'CUI', 'default'))"

# DIC → Research engine: scan a collection as signals
python -c "from tools.research.source_scanners.dic_scanner import scan_dic_collection; signals = scan_dic_collection({}, {'dic_collection_id': 'my-col'}); print(len(signals), 'signals')"

# DIC → Innovation engine: discover with DIC context
python -c "from tools.innovation.innovation_manager import stage_discover; print(stage_discover(dic_collection_id='my-col'))"

# Weekly DIC digest reflex (manual trigger)
python -c "from tools.genesis.reflexes.dic_digest import run; print(run({}, None))"
```

---

## Network Canvas — PVM (Predictive Vulnerability Management)

```bash
# Risk Predictor
python tools/network/vuln_predictor.py --predict <advisory_id> --json
python tools/network/vuln_predictor.py --predict-all --json
python tools/network/vuln_predictor.py --trajectory <advisory_id> [--limit 10] --json
python tools/network/vuln_predictor.py --top-risks [--limit 20] --json

# Attack Surface Mapper
python tools/network/attack_surface_mapper.py --map [--network-id <id>] --json
python tools/network/attack_surface_mapper.py --surface [--cve CVE-XXXX-XXXX] [--device <name>] [--min-score 0.5] --json
python tools/network/attack_surface_mapper.py --summary --json

# Vulnerability Triage Engine
python tools/network/vuln_triage_engine.py --score [--advisory-ids 1,2,3] --json
python tools/network/vuln_triage_engine.py --queue [--status pending] --json
python tools/network/vuln_triage_engine.py --approve <advisory_id> --by analyst@example.com --json
python tools/network/vuln_triage_engine.py --defer  <advisory_id> --by analyst@example.com --json

# AI Patch Planner
python tools/network/patch_planner.py --create-plan [--approved-by EMAIL] --json
python tools/network/patch_planner.py --plans [--plan-id <uuid>] [--advisory-id <id>] --json
python tools/network/patch_planner.py --plan-summary <plan_id> --json

# PVM Dashboard
# http://localhost:5050/network/vulnerability-intelligence

# PVM IQE Seed Queries
python tools/iqe/cli.py --file context/iqe/queries/network/pvm_01_risk_trajectory.iqe --adapter ndc --json
python tools/iqe/cli.py --file context/iqe/queries/network/pvm_02_attack_surface.iqe   --adapter ndc --json
python tools/iqe/cli.py --file context/iqe/queries/network/pvm_03_triage_queue.iqe     --adapter ndc --json
```

## Document Modernization Engine (docmod)

```bash
# Scan documents for stale content (EOL hardware/software, deprecated tech, superseded standards)
python -c "from tools.doc_modernization import scan_collection; import json; print(json.dumps(scan_collection(), indent=2))"
python -c "from tools.doc_modernization import scan_document; import json; print(json.dumps(scan_document('<doc_id>'), indent=2))"

# Latest-state findings (append-only supersede chains resolved)
python -c "from tools.doc_modernization import get_findings; import json; print(json.dumps(get_findings(state='open'), indent=2, default=str))"

# endoflife.date cache — seed (air-gap), live sync, bundle import
python -m tools.doc_modernization.eol_products_sync --seed --json
python -m tools.doc_modernization.eol_products_sync --sync --json
python -m tools.doc_modernization.eol_products_sync --import bundle.yaml --json

# De facto deployment standards from ni_devices (recency-weighted)
python -c "from tools.doc_modernization.defacto_learner import recompute; print(recompute())"

# Nightly sweep reflex (standalone)
python -m tools.genesis.reflexes.doc_modernization_sweep --dry-run --json

# UI: http://localhost:5050/standards-catalog (curated standards, all domains)
#     http://localhost:5050/document-intelligence/freshness (staleness triage)
# Config: args/docmod/docmod_config.yaml + args/docmod/packs/*.yaml + rulebooks
# MCP tools: docmod_scan, docmod_findings, docmod_redline
```

## Twin Core — Cross-Canvas Digital-Twin Unification (TWX)

```bash
# Cross-canvas twin health report (snapshot freshness, verdict distribution,
# violation counts by severity, refresh-schedule adherence vs genesis reflexes)
python -m tools.twin_core.observer --json
python -m tools.twin_core.observer --window-hours 24 --stale-after-hours 48 --json

# Library API
python -c "from tools.twin_core import observe; import json; print(json.dumps(observe(), default=str)[:400])"
python -c "from tools.twin_core import TwinRegistry; print(TwinRegistry.keys())"  # registered twins
# Config/registry: adapters self-register from tools/twin_core/adapters/*.py
# Canonical schema: tools/twin_core/schema.py (verdict pass|warn|fail|unknown; Sequoia Pattern 4 violations)
```

## FORGE Academy — xAPI Export (aca-trn-05)

```bash
# Export Academy completions as xAPI 1.0.3 statements so they can feed an external LMS/LRS.
# Only records with a verified provenance row (fa_xp_ledger for step/mission,
# fa_certificate_evidence for a certificate) are emitted; the rest are withheld and named
# in the `excluded` block rather than silently dropped.
python -m apps.forge_academy.xapi --json                                   # full envelope: statements + excluded + counts
python -m apps.forge_academy.xapi --statements-only --out academy_feed.json  # bare array an LRS POST /statements expects
python -m apps.forge_academy.xapi --user-id 1 --since 2026-01-01T00:00:00Z   # one learner, incremental
python -m apps.forge_academy.xapi --include-unverified                       # also emit unverifiable records, each stamped verified:false

# Library API
python -c "from apps.forge_academy.xapi import build_statements; import json; print(json.dumps(build_statements()['counts']))"

# HTTP (org-leadership gated, same tier as Oracle / Org Readiness)
#   GET /api/academy/export/xapi[?user_id=&since=&include_unverified=1&statements_only=1]
# Env: ICDEV_XAPI_ACTIVITY_BASE (default https://icdev.ai/xapi/forge-academy) — two deployments
#   feeding the same LRS must not both claim the same activity IRIs.
# SCORM is deliberately NOT implemented: it records one rolled-up completion per launch and would
#   discard the per-step granularity that makes this export worth having.
```

## Agent Browser — Indexed-Element Page Representation (tools/browser/)

```bash
# Probe which WebDriver resolves (vendored msedgedriver → chromedriver → Selenium Manager)
python tools/browser/driver_manager.py --probe
python tools/browser/driver_manager.py --smoke

# Index a page — prints exactly what a model sees:
#   [24] <button> + Add Task
#   [20] <input> role=text Filter tasks (id=kanban-filter-input, type=text)
python tools/browser/agent_browser.py --url http://localhost:5050/kanban --text

# JSON state (index, role, text, allowlisted attributes, bounds, in_viewport, disabled)
python tools/browser/agent_browser.py --url http://localhost:5050 --json

# State + screenshot (always lands under playwright/screenshots/)
python tools/browser/agent_browser.py --url http://localhost:5050 --text --screenshot --name home

# Show the resolved config
python tools/browser/agent_browser.py --config --json

# In a git worktree use the module form — a script path does not put the repo
# root on sys.path, so tools.* resolves to the shared checkout's vendor/drivers.
python -m tools.browser.agent_browser --url http://localhost:5050 --text
```

```python
# Library API — act by index, never by an invented CSS selector
from tools.browser.agent_browser import AgentBrowser

with AgentBrowser() as b:
    state = b.navigate("http://localhost:5050/kanban")
    print(state.to_text())              # model-facing rendering
    b.type_text(20, "oss-browse")       # index from the state above
    b.click(24)
    b.press("Escape")
    b.select(19, "Engineering")         # matches option value, then visible text
    state = b.read_state(screenshot=True)
    print(b.validate("The CUI banner is visible"))   # reuses screenshot_validator

# Agent-loop wiring (same convention as tools/ace/agent_tools.py)
from tools.browser.agent_tools import BrowserToolRegistry
tools, handlers = BrowserToolRegistry(browser).build()
```

```
# Config: args/agent_browser.yaml — page representation only
#   include_attributes  — DOM verbosity allowlist (the main prompt-size knob)
#   max_elements / max_text_length / max_attr_length — hard caps (state.truncated)
#   viewport_only / occlusion_check — geometry filters
#   navigation.settle_ms — post-action pause before re-reading state
#
# Config: args/browser_scope.yaml — the enforced policy (tools/browser/scope.py)
#   allowed_domains / denied_domains / allowed_schemes — default-deny nav gate
#   allow_non_local + require_egress_guard — the two extra switches a routable host needs
#   limits.max_actions_per_run / max_failures / step_timeout_seconds — per-run budget
#   AgentBrowser holds a GuardedDriver, so all of the above applies to every method.
#   There is no navigation policy in agent_browser.yaml — one policy, one file.
# Tests: tests/test_agent_browser.py (56 tests; real-browser test auto-skips with no driver)
#        tests/browser/test_scope.py (52 tests; the policy decision table)
```

## Release & PyPI Packaging

One command from version bump to publish. The middle of the pipeline (sync,
validate, build, wheel inspection, throwaway-venv smoke, air-gap install) is
delegated to `build_release.py` — never reimplemented.

```bash
# Dry run — bump, build, verify everything. Uploads NOTHING. Start here.
python tools/installer/release.py --version 1.2.43

# Let it pick the number
python tools/installer/release.py --bump patch

# Stub out the README + CHANGELOG sections, then stop so you can write them
python tools/installer/release.py --version 1.2.43 --scaffold-notes

# Publish for real (irreversible — a version number can never be reused)
python tools/installer/release.py --version 1.2.43 --publish

# Only reconcile the version declarations
python tools/installer/release.py --version 1.2.43 --bump-only

# Machine-readable report
python tools/installer/release.py --bump patch --json
```

Credentials are read from `.env` (`TWINE_USERNAME`/`TWINE_PASSWORD`, or
`PYPI_API_TOKEN`) and never printed.

**Refusals, each traceable to a release that shipped broken:**

| Refusal | Why |
|---|---|
| `--publish` with `--skip-smoke` | The throwaway-venv smoke test is the only step that catches a wheel which builds, passes `twine check`, and cannot import. 1.2.41 shipped exactly that. |
| `--publish` with `--allow-missing-notes` | `/updates` renders `CHANGELOG.md`; a release with no entry leaves the dashboard advertising an older version. 1.2.38 and 1.2.39 shipped with no entry. |
| Publishing by default | Uploads are irreversible. The default run builds and verifies, then tells you the command to publish. |

The notes gate runs **before** the version bump, so a missing-notes run writes
nothing. Re-running the current version is treated as resuming a half-finished
release, not an error — otherwise a failure after the bump would wedge the retry.

> Do not call `python -m build` directly. That skips `sync_package_tree.py`,
> which is what made the 1.2.40 wheel ship 29 differing and 53 missing `args/`
> files — including the `component_registry.yaml` that 1.2.39 had just fixed.


## Getting ICDEV files WITHOUT a full scaffold

`pip install icdev` installs the package; it does not write into your project.
`icdev init` copies the payload out. If you don't want a full scaffold, take
only what you need:

```bash
icdev init --list                      # dry run: show what WOULD be copied
icdev init --only CLAUDE.md            # just the master instruction file
icdev init --only CLAUDE.md goals      # CLAUDE.md + the FORGE Goals layer
icdev init --only AGENTS.md            # a single AI-platform instruction file
icdev init --minimal                   # CLAUDE.md + .claude/ + platform files + goals/
```

The packaged `CLAUDE.md` is the repo's file **byte-for-byte** — no template
substitution, no stripped-down variant. A test pins that
(`test_packaged_claude_md_is_not_a_stripped_template`).

`goals/` is copied even under `--minimal`. It is the Goals layer of FORGE and
the entry point of ANVIL, and CLAUDE.md instructs the agent to read
`goals/manifest.md` before starting any task — a scaffold without it produces a
project that contradicts its own first instruction.

## Guided setup after `pip install icdev`

`pip install` installs the package; `icdev init` copies the project payload out;
`icdev setup` configures it. The wizard is OS-aware (Windows, WSL, Linux, macOS)
and writes everything to `.env`.

```bash
icdev setup                    # guided: OS → LLM → database → RAG+KG → Docker
icdev setup --components       # skip to the component enable/disable TUI
icdev setup --non-interactive  # accept detected defaults, ask nothing
icdev setup --no-probe         # skip LLM reachability checks (air-gapped)
icdev setup --docker-only      # only (re)generate docker-compose.yml
icdev setup --postgres         # assume PostgreSQL rather than SQLite
icdev setup --dry-run          # show what would change; write nothing
icdev setup --json             # machine-readable environment report
```

**What it configures**

| Step | Detail |
|---|---|
| Environment | OS + release, Python, Docker, local PostgreSQL (:5432), local Ollama (:11434), WSL |
| LLM | primary + fallback provider, API keys, bounded reachability probe |
| Database | SQLite (zero-config) or PostgreSQL; writes DSN or DB path |
| RAG + KG | enable flags and embedding dimension (768 — the air-gap-safe default) |
| Docker | generates a `docker-compose.yml` matched to the answers |
| Components | hands off to the existing registry-driven TUI |

**Why the compose file is generated rather than documented**

Volume paths are where setup actually fails, and the failure is silent — the
container starts and the mount is empty:

| Host | Bind-mount source |
|---|---|
| Windows | `C:/ai/proj/data` — forward slashes, not what `os.path` produces |
| WSL | `./data` — **Linux** rules, even though users think of it as Windows |
| Linux / macOS | `./data` |

The generated file also uses `pgvector/pgvector:pg16` rather than stock
`postgres:16` (ICDEV stores embeddings in a `vector` column, so plain postgres
cannot host the RAG schema), points the app at the `postgres` **service name**
rather than `localhost`, and gates startup on a healthcheck so the first
migration doesn't race the database.

**The LLM probe** is bounded and skippable. A key that is present but rejected
is worse than one that is absent — it fails over silently at runtime, which is
how a stale key can degrade retrieval for weeks before anyone notices.

### Kubernetes (Helm)

```bash
helm install icdev deploy/helm/                       # defaults: RAG on, pgvector
helm install icdev deploy/helm/ -f deploy/helm/values-aws.yaml
helm install icdev deploy/helm/ --set rag.enabled=false   # no vector DB needed
```

`rag.enabled` (default **true**) selects `pgvector/pgvector:pg16` for the
platform database and runs `CREATE EXTENSION IF NOT EXISTS vector` on first
start. With RAG off, the hardened base image is used instead.

This matters: ICDEV stores embeddings in a `vector` column, so a stock postgres
image cannot host the RAG schema — the extension fails to create and every
embedding write raises. Override `rag.vectorImage` with your own mirrored build
in air-gapped or registry-restricted clusters.

`Chart.yaml`'s `appVersion` tracks `icdev/_version.py` and is bumped by
`release.py`. The chart's own `version:` is deliberately independent — it moves
when the templates change, not when the application does.

### Provisioning the database and vector store

`icdev setup` writes a DSN; it does not make a database exist. `--provision-db`
creates whatever is missing, in dependency order:

```bash
icdev setup --provision-db                 # create what's missing
python -m tools.cli.provision_db --check   # report only, change nothing
python -m tools.cli.provision_db --provision --docker --dry-run
python -m tools.cli.provision_db --sqlite --provision
```

Four things must exist before RAG works, and each fails differently:

| Layer | Failure when absent |
|---|---|
| PostgreSQL **server** | connection refused |
| **database + role** | `FATAL: database "icdev" does not exist` |
| **pgvector extension** | `ERROR: type "vector" does not exist` |
| **schema** | `relation "rag_chunks" does not exist` |

The third bites hardest: the extension is only *installable* if the running
image ships pgvector. On stock `postgres:16` the `CREATE EXTENSION` in migration
044 fails and every embedding write raises afterwards — so availability is
checked, not just whether it's enabled.

**Greenfield options**

| Option | Notes |
|---|---|
| **SQLite** | Zero install. File created on first connect; vector store is `rag_chunks` with a BLOB column. Always works, air-gap safe. Brute-force cosine rather than an indexed scan. |
| **Docker** | Starts `pgvector/pgvector:pg16` from the generated compose file and **waits for the server to accept connections** — `compose up -d` returns when the container is created, not when Postgres is ready. |
| **Native** | Per-OS install commands are printed, not executed: that needs elevation and changes the machine outside your project. |
| **Existing server** | Provisions the database, role, extension and schema into a server that's already running (RDS, Cloud SQL, a shared box) without touching its install. |

**Port conflicts** are detected and distinguished, because the three cases need
opposite handling:

- **free** — start a container here
- **an existing PostgreSQL** — do *not* start a second one; provision into it. Two servers is a silent second source of truth.
- **something else** — republish on 5433+, rewriting the DSN and the compose file's host port together. Only the host side moves; Postgres inside the container always listens on 5432.

Provisioning is non-destructive: it creates a database, role and extension, and
never drops, alters or overwrites one that exists. `--dry-run` prints the plan.

### Corporate proxies and LLM gateways

Most enterprises reach the model through a proxy or an internal gateway, and in
that world **there is no API key to configure** — the gateway holds the real
credentials and authenticates upstream on your behalf.

```bash
python -m tools.cli.proxy_detect          # what proxy is this machine using?
python -m tools.cli.proxy_detect --json
```

`icdev setup` runs this automatically, reports what it found, and adopts it.
Detection order — ICDEV's own settings first, because an explicitly configured
rotator is a decision and must not be overwritten by a staler OS value:

| Source | Where |
|---|---|
| `ICDEV_LLM_PROXY_CMD` / `ICDEV_LLM_PROXY` | already configured |
| `HTTPS_PROXY`, `ALL_PROXY`, `HTTP_PROXY` | environment |
| WinINET registry (incl. PAC / `AutoConfigURL`) | Windows |
| `scutil --proxy` (incl. PAC) | macOS |

Pick the `gateway` provider in the wizard and leave the key blank; set
`ICDEV_LLM_GATEWAY_URL` to its OpenAI-compatible base URL. The provider sends
the placeholder `not-needed`, which is what an unauthenticated OpenAI-compatible
endpoint expects.

**Rotation is the part that bites.** A rotating proxy written into `.env` as a
literal URL works until the pool moves, then presents as the LLM being down. So
setup deliberately records the *source*, not the value:

| Config | Behaviour |
|---|---|
| `ICDEV_LLM_PROXY_CMD` | a command printing the **current** proxy; re-run per call, TTL-cached via `ICDEV_LLM_PROXY_CMD_TTL` (default 2 s). Correct under any rotation. |
| nothing written | the SDKs read `HTTPS_PROXY` on every call — correct when the rotator updates the environment |
| `ICDEV_LLM_PROXY` | a fixed URL. Only written when the detected source is **not** rotating. |

`tools/llm/proxy_resolver.py` re-resolves on every invoke and calls
`provider.reset_client()` when the value changed, so an SDK client that captured
the old proxy at construction is rebuilt rather than silently reused.

Note this is a *different* thing from `proxy_gateway.py`: that is ICDEV's own
LiteLLM proxy issuing virtual keys to callers. This is your organisation's
egress path out to the vendor. They compose — ICDEV's proxy can itself sit
behind a corporate one.

Behind a proxy, a direct connection to `api.anthropic.com` is *supposed* to
fail, so the setup probe reports that as expected rather than as a fault.

### Keeping `/updates` correct

`http://localhost:5050/updates` renders `CHANGELOG.md` live, so the page updates
the moment the file does. What `release.py` guarantees is that the file is
*worth rendering* — the notes gate blocks a release unless, checked with the
page's own parser:

| Check | Blocks when |
|---|---|
| `updates_parses` | the entry doesn't parse — `/updates` would silently omit the release |
| `updates_is_newest` | it isn't the top entry — the page would lead with an older version |
| `updates_has_content` | it's still a `--scaffold-notes` TODO stub |

The middle one is the state that had `/updates` advertising 1.2.37 while the
package shipped 1.2.39. The last exists because notes that read as written but
say nothing are worse than none.

---

## IDP Scorecard-as-Code (idp-score-02)

Grades every component in `args/component_registry.yaml` against a ladder of
ranked levels. Rules are **IQE queries**, not a bespoke DSL — see
[tools/manifest/idp-scorecards.md](../../tools/manifest/idp-scorecards.md).

```bash
# List the shipped scorecards, their ladders, and how many rules gate them
python tools/idp/scorecard.py --list
python tools/idp/scorecard.py --list --json

# Evaluate every scorecard in args/scorecards/ (human table)
python tools/idp/scorecard.py

# One scorecard, machine-readable — per-entity level, score, and rule outcomes
python tools/idp/scorecard.py --scorecard component-readiness --json

# Why is one component stuck at its level?
python tools/idp/scorecard.py --scorecard component-readiness --component ndc

# Evaluate scorecards from somewhere else (a tenant overlay, a test fixture)
python tools/idp/scorecard.py --dir /path/to/scorecards --json
```

### Component Scorer (idp-score-01)

The complement to the scorecard above: where `tools/idp/scorecard.py` grades a
component against *declared* YAML/IQE rules, this reads ICDEV's own measurement
subsystems (probes, compliance posture, coherence, the 8-point gate) and turns
each into one dimension score.

```bash
# Score one component across all four dimensions
python tools/quality/component_scorer.py ndc
python tools/quality/component_scorer.py ndc --json

# Score every component in args/component_registry.yaml
python tools/quality/component_scorer.py --all

# Score and upsert into developer_scorecards (one row per component)
python tools/quality/component_scorer.py --all --persist --json
```

**The honesty rule:** any dimension that nothing measured caps `overall_score`
and `letter_grade` at NULL. A component is graded only when all four dimensions
were actually assessed — a weighted average over a subset would overstate the
evidence behind it, and the dimensions most often unassessed are the ones that
would have lowered the score. An unassessed component is still persisted, with
`dimension_details` naming which dimension was missing and why.

> Requires migration `20260802145147_scorecard_component_id`. Without it
> `--persist` raises `ScorecardPersistError` rather than silently writing
> nothing.

### Component Scoring Sweep (idp-score-01-d4)

The operational entry point for the scorer above: walks every component in
`args/component_registry.yaml` and upserts each verdict into
`developer_scorecards`. This is what CI and a scheduled refresh run.

```bash
# Score the whole registry and write the results
python -m tools.quality.run_component_scoring

# Score everything and write NOTHING — the CI shape, no migrated DB needed
python -m tools.quality.run_component_scoring --dry-run

# Machine-readable run report
python -m tools.quality.run_component_scoring --dry-run --json

# One or a few components
python -m tools.quality.run_component_scoring --dry-run --keys ndc,sdc

# Assess the coherence dimension too (a sweep is minutes long, so it is never
# triggered implicitly — hand it the report the checker already produced)
python tools/workflow/coherence_checker.py --all --json > .tmp/coherence.json
python -m tools.quality.run_component_scoring --coherence-report .tmp/coherence.json
```

**Exit codes are load-bearing:** `0` the sweep completed (components *may* be
unassessed), `1` at least one component raised while being scored or persisted
— the rest still completed, `2` the sweep could not start (the registry would
not load, or persistence was asked for with no usable connection).

**An all-NOT_ASSESSED sweep is a clean run.** On a checkout with no probe
history, no compliance assessment and no coherence report, every component
correctly comes back `NOT_ASSESSED` — that is the honest state of ICDEV's own
measurement coverage, not a broken runner. The report's
`unassessed_by_dimension` tally is the actionable part: it names which
measurement subsystem is dark. The CI step (`Component scoring sweep (dry run)`
in `.github/workflows/icdev-ci.yml`) is therefore a smoke gate on the sweep
*running*, and deliberately does not assert that anything was assessed.

### Portal surface (idp-ui-02)

The same catalog and scorecards rendered as a dashboard page, mounted at the
`url_prefix` declared in `args/component_registry.yaml`. It grades itself: the
portal appears in its own catalog and passes the 8-point completeness gate it
surfaces for every other canvas.

| Route | Purpose |
|-------|---------|
| `/idp/` | Ladder, rule coverage, catalog, and the portal's own grade |
| `/idp/catalog` | Same page, catalog section |
| `/idp/scorecards` | Same page, scorecard section |
| `/idp/component/<key>` | One component: facts, per-dimension cards, 8-point breakdown |
| `/idp/evidence?component=<key>&rule=<id>` | Why one rule landed as it did, re-derived live (idp-ui-01) |
| `GET /idp/api/catalog` | JSON catalog (`?scorecard=`, `?refresh=1`) |
| `GET /idp/api/scorecard` | JSON scorecard report |
| `GET /idp/api/component/<key>` | JSON component detail |
| `GET /idp/api/evidence?component=<key>&rule=<id>` | JSON rule evidence |
| `POST /idp/api/iqe-query` | IQE natural-language query over `idp.components` |

The view models are a library, not a CLI — import them:

```python
from tools.idp.portal import (
    portal_overview, component_detail, rule_evidence, self_check,
)

portal_overview()               # everything /idp renders
component_detail("ndc")         # facts, per-dimension scores, evidence links
rule_evidence("ndc", "e2e-spec")  # re-runs that rule live and returns its sources
self_check()["completeness"]    # the portal's own 8-point breakdown
```

### Catalog and scorecards (idp-ui-01)

Every component is listed with its owner, ladder level and A—F letter grade,
and every grade decomposes into per-dimension scores that link to the evidence
behind them.

```bash
# Per-dimension columns and letter grades in the CLI table
python tools/idp/scorecard.py --scorecard component-readiness

# One component's dimensions, grade, and per-rule evidence
python tools/idp/scorecard.py --scorecard component-readiness --component ndc --json
```

A component (or a single dimension) that no rule applies to reports
`score: null`, `letter_grade: null`, `assessed: false` and renders as
**"Not assessed"** — never as `0%` or `F`. A measured zero still reads as a
zero; the two are different claims and the surface keeps them apart.

Any rule expression is a standalone IQE query, so it can be run by hand against
the same collection the scorecard grades:

```bash
python -m tools.iqe.run --query-string \
  'foreach c in idp.components where c.has_e2e_spec == true select c.key'
```

Adding a rule or a level is a YAML edit under `args/scorecards/` — no Python
change. `python tools/idp/scorecard.py --list` reflects it immediately.

### Score history (idp-score-03)

`scorecard.py` computes a standing and throws it away. `score_history.py` keeps
it, so "is this component getting better or worse" becomes answerable. One row
per component per evaluation in the append-only `idp_scorecard_history`, each
carrying the attained ladder level — a promotion or demotion is a comparison of
two adjacent rows, not a re-evaluation of historical rule sets.

```bash
# Record a point for every scorecard (always writes)
python tools/idp/score_history.py --record

# Record only if the scorecard's evaluation.window has rolled over —
# how the scheduled reflex calls it, so a 3h cadence feeds a 24h window
# without writing eight identical rows a day
python tools/idp/score_history.py --record --if-due

# One scorecard only
python tools/idp/score_history.py --record --scorecard component-readiness

# Read one component's series back (oldest-first, with delta and direction)
python tools/idp/score_history.py --trend ndc
python tools/idp/score_history.py --trend ndc --json --limit 30

# Every ladder promotion/demotion across the estate
python tools/idp/score_history.py --level-changes
python tools/idp/score_history.py --level-changes --since 2026-08-01 --json
```

An explicit `--record` always writes, so re-scoring right after a fix shows the
improvement immediately instead of waiting out the window. The scheduled writer
is the Genesis reflex `idp_score_recorder` (3h, GREEN tier — see
`args/genesis_config.yaml`); the window in `args/scorecards/<key>.yaml` decides
whether each cycle actually records, so changing granularity is a YAML edit.

### Rule exemptions — approval and audit (idp-score-04)

An exemption takes ONE component out of ONE rule. It is **not** a pass: the rule
stops applying, so it leaves the score's denominator rather than paying out its
weight, and it does not hold the component back on the ladder. Every exemption
must name **who approved it** and **why** — one that does not is reported as
`INERT` and waives nothing.

`autoApprove` ships **off** (`args/scorecards/<key>.yaml`, `exemptions:`), so a
request waives nothing until somebody approves it. Every state change appends a
row to `idp_rule_exemptions` (append-only); revoking is an event, not a delete.

```bash
# File a request — lands at `pending`, waives nothing yet
python tools/idp/exemptions.py --request e2e-spec:my-canvas \
    --scorecard component-readiness \
    --reason "Headless component; renders no page for Playwright to drive." \
    --requested-by alice --expires 2026-12-31

# Approve it — this is the event that makes it apply
python tools/idp/exemptions.py --approve e2e-spec:my-canvas \
    --by platform-lead --decision-reason "Confirmed headless with the owner."

# Deny, or withdraw a live one (the rule starts applying again)
python tools/idp/exemptions.py --deny e2e-spec:my-canvas --by platform-lead \
    --decision-reason "A smoke spec is feasible here."
python tools/idp/exemptions.py --revoke e2e-spec:my-canvas --by platform-lead \
    --decision-reason "Owner assigned; spec landed."

# Read: current state of each exemption, or the full append-only history
python tools/idp/exemptions.py --list
python tools/idp/exemptions.py --history --json
python tools/idp/exemptions.py --history e2e-spec:my-canvas
```

Grants may also be declared in the scorecard YAML under `exemptions.grants[]`
with a required `approvedBy` and `reason`. When both stores name the same
(rule, component), **the approval log wins** — otherwise revoking a waiver
would require a code change to take effect.

### Gap seeder — a failing rule becomes a kanban task (idp-gap-01)

A catalog product surfaces a red cell and stops. ICDEV owns `kanban_tasks` and
an autonomous build pipeline, so a failing rule can become work. One task per
failing rule per component, with the rule's `failureMessage` as the description
and the IQE query that measured the failure as the acceptance criteria.

```bash
# Dry run — the default. Reads everything, applies every cap, writes nothing.
python tools/idp/gap_seeder.py
python tools/idp/gap_seeder.py --json

# Try different caps before committing to them
python tools/idp/gap_seeder.py --max-per-run 3 --max-per-component 1

# One scorecard only
python tools/idp/gap_seeder.py --scorecard component-readiness

# Actually write (refused until args/idp_gap_seeder.yaml has enabled: true)
python tools/idp/gap_seeder.py --seed --json
python tools/idp/gap_seeder.py --seed --force        # one-off, ignores enabled: false
```

Caps live in `args/idp_gap_seeder.yaml`. `max_tasks_per_component` is applied
**before** `max_tasks_per_run` so one badly scoring component cannot consume the
whole budget; both truncations are logged at WARNING, printed to stderr, and
reported as `truncated` in the JSON — a silent truncation would read as
"nothing left to do". Measured on the live board: 311 failing rules → 10 tasks.

Re-running seeds nothing. The idempotency key is
`idp-gap:<scorecard>:<component>:<rule>` and already-seeded gaps are filtered
out *before* the cap is applied, so the same ten are not re-offered forever.

Nothing dispatches without confirmation: tasks land as `suggested` **and** carry
`depends_on_task_id = idpgap-gate-00`, a `*-gate-00` sentinel held `in_progress`.
The dependency edge is the hold that is enforced in code
(`promote_backlog_to_scheduled::_deps_satisfied`); `suggested` alone is not,
because the kanban deadlock-breaker can promote a card out of it. Release the
whole batch by setting the gate to `done`. Seeding is refused outright if the
gate has already been released.

### Delivery events — give the DORA query something to measure (idp-intel-01)

`/api/sre/dora` (surfaced at `/sre`) bands all four DORA keys correctly and
refuses to launder missing data into a favourable rating — it reports
`Not Assessed`. Measured 2026-08-02 it returned `metrics_assessed: 0`, because
every input table was empty. This emits the inputs; the query is untouched.

The ledger already exists: `kanban_tasks.status = 'done'` is merge-verified, so
a done task with a `completed_at` is a record of a change reaching main, and
`kanban_verifications` records what the verifier said about it on the way.

```bash
# What can the DORA query see right now?
python tools/idp/delivery_events.py --status --json

# What would be emitted — reads everything, writes nothing
python tools/idp/delivery_events.py --sync --dry-run --json

# Emit (incremental and idempotent; re-running adds only new changes)
python tools/idp/delivery_events.py --sync --json
python tools/idp/delivery_events.py --sync --days 90 --json   # cold-install backfill
```

The mapping: one `done` task = one `deployment_initiated` event stamped at the
moment the change landed (not at backfill time); a change whose *most recent*
verification returned `failed`/`phantom` also gets a `deployment_failed`;
work-start → landed becomes a `ci_pipeline_runs` row for lead time. `bypassed`
verifications are **not** counted as failures — an unverified change is not a
failed one — and a task with no dispatch or verification timestamp gets its
deploy event but no pipeline row, reported as `no_start_signal` rather than
having a start invented from `created_at` (that would measure backlog wait).

`mttr` stays `Not Assessed` after a full sync and that is the correct answer:
it reads `sre_incidents`, and this platform has no production incident ledger.
Projecting bug tasks or failed verifications into it would put a rating on the
dashboard that no measurement supports.

The scheduled writer is the Genesis reflex `idp_delivery_events` (6h, GREEN
tier — `args/genesis_config.yaml`). It exists because the endpoint reads a
*rolling* 30-day window: without a writer, a one-off backfill ages out and the
endpoint returns to `metrics_assessed: 0` with nobody having changed a line.

## Executor Parity Benchmark (hgx-exec-04)

A/B replay of a fixed corpus of already-merged kanban tasks through two
AgentAdapters — `claude_cli` (primary) and `local_agent` (the owned,
file-editing rubric loop). Each pair gets a disposable detached worktree at the
task's pre-fix parent commit, the identical `AgentSession`, and one grader:
`tools/workflow/pipeline_grader.make_pipeline_grader`.

Measurement only. It changes no default: `KANBAN_RUBRIC_LOOP` is on the `.env`
import denylist so the benchmark cannot flip it even by accident, and
`args/strategos_config.yaml` is never read or written.

```bash
# What is in the corpus (task ids, base commits, prompt size)
python -m tools.workflow.executor_parity --list

# Resolve corpus + adapters + base commits without building anything
python -m tools.workflow.executor_parity --dry-run

# Full benchmark: 10 tasks x 2 executors, JSON + markdown out
python -m tools.workflow.executor_parity --run \
  --out .tmp/parity.json --report .tmp/parity.md

# One task, one executor (a smoke check before spending the full run)
python -m tools.workflow.executor_parity --run \
  --tasks cxo-doc-01 --executors claude_cli --timeout 300

# Keep the worktrees to inspect what an executor actually produced
python -m tools.workflow.executor_parity --run --limit 1 --keep-worktrees
```

Two rates are reported per executor and they are deliberately not the same
number: `gate_pass_rate` is the harness's own verdict on the tree,
`self_report_rate` is what the executor claimed about itself. The gap is the
result — measured numbers live in
[docs/features/hgx-executor-parity.md](../features/hgx-executor-parity.md).

Corpus: `args/executor_parity_corpus.yaml`. Treat it as a frozen baseline —
adding an entry is fine, rewording one changes what is being measured and
requires re-running both executors.

## Adapter Capability Matrix (exa-bench-03)

A different question from the parity benchmark above, and the two must not be
merged. `executor_parity` measures **outcome parity** — can an executor finish a
job? — by replaying a corpus in worktrees and grading the trees. This probe
measures **capability parity** — can an executor be handed a job that needs
streaming, a sandbox mode or a cancel button at all? It runs offline in
milliseconds: no subprocess, no socket, no model call, no corpus.

```bash
# Full matrix: every registered adapter x seven capabilities
python tools/agents/capability_matrix.py
python tools/agents/capability_matrix.py --json

# Narrow it
python tools/agents/capability_matrix.py --adapter claude_cli --json
python tools/agents/capability_matrix.py --capability sandbox_passthrough

# Exit 1 when a capability is DECLARED but measured absent (opt-in; wired to
# no pipeline — it is a report you can run, not a gate that runs itself)
python tools/agents/capability_matrix.py --gate
```

Each cell reports `declared` (the hand-written claim in
`args/agent_capabilities.yaml`) next to `actual`, which is one of three values
and never two:

| `actual` | meaning |
|---|---|
| `present` | the probe observed the capability at the adapter seam |
| `absent` | the probe observed its absence |
| `unconfirmed` | the probe could not determine it — **not** a synonym for either |

Only `behavioral` probes (adapter code executed, return value inspected) and
`interface` probes (the live object inspected) may assert present or absent. A
`source_evidence` probe — the module source documents a contract only a live run
could exercise — may only ever produce `unconfirmed`.

Routing consults the measurement:

```python
from tools.agents import pick_default, adapters_with

adapter = pick_default("build", require=["sandbox_passthrough"])
adapters_with("interruption")     # names measured present, nothing else
```

`require` is fail-closed: a capability that is merely declared, or that the
probe could not confirm, does not satisfy it. Leaving `require` unset preserves
the previous selection behaviour exactly.

Claims live in `args/agent_capabilities.yaml`. When a row comes back
`overclaimed`, fix the adapter or fix the claim — editing the claim to make the
probe agree rebuilds the hand-written parity table this replaced.

# CI test allowlist (kax-conflict-07) — the list icdev-ci.yml's `test` job runs
python tools/ci/gated_test_list.py --check --list core       # validate: empty/short/missing/dup -> exit 1
python tools/ci/gated_test_list.py --print --list windows    # resolved targets, one per line
python tools/ci/gated_test_list.py --list core --json        # full report (count, floor, missing, duplicates)
python tools/ci/gated_test_list.py --extract-workflow .github/workflows/icdev-ci.yml --job test --min-targets 2
python tools/git/ci_test_list_merge_rehearsal.py             # inline vs external vs external-union, both merge paths
python tools/git/ci_test_list_merge_rehearsal.py --branches 5 --gate
python tools/git/ci_test_list_merge_rehearsal.py --repo .    # rehearse against a CLONE of this repo + the real list

# CI test gating ratchet (tsg-policy-01) — the gap cannot silently REGROW.
# --check above proves the allowlist did not shrink; this proves no test file is
# gated by nothing. Every collectible module under tests/ must be in an allowlist,
# in a documented exclusion (args/test_gating_gate.yaml), or in the grandfathered
# census (args/ci_test_backlog.txt, shrink-only). Anything else fails the `test` job.
# Policy: docs/ci/test-gating-policy.md
python tools/ci/gated_test_list.py --check-coverage          # exit 1 on an ungated new test file
python tools/ci/gated_test_list.py --check-coverage --json   # total/gated/excluded/backlog/unlisted
python tools/ci/gated_test_list.py --prune-backlog           # drop census lines now gated or gone

# AGOV CASE — agent-session forensics CLI (agov-case-04)
# CLI-only by design. There is deliberately NO dashboard page: one would require
# all 8 completeness-gate components from CLAUDE.md (template + icdev/ mirrored
# template + blueprint route + backing module + constants + migration + nav link
# + full IQE wiring), and that is a separate card.
python tools/agent_case/cli.py timeline --session <session_id>                  # ordered timeline, human-readable
python tools/agent_case/cli.py timeline --session <session_id> --json           # machine-readable
python tools/agent_case/cli.py timeline --session <id> --since <iso> --until <iso> --limit 500
python tools/agent_case/cli.py timeline --session <id> --no-redact                 # unmasked; do not disclose as rendered
python tools/agent_case/cli.py build --session <session_id> --out <dir>         # write a portable case bundle
python tools/agent_case/cli.py build --session <id> --out <dir> --force --json  # replace an existing bundle
python tools/agent_case/cli.py verify --bundle <dir>                            # all three layers
python tools/agent_case/cli.py verify --bundle <dir> --layer hmac --json        # one layer (repeatable)
python tools/agent_case/cli.py verify --bundle <dir> --secret <key>             # key instead of $ICDEV_HOOK_HMAC_SECRET

# The three subcommands are also runnable directly as their own modules:
python tools/agent_case/session_timeline.py --session <session_id> --json
python tools/agent_case/case_bundler.py --session <session_id> --out <dir> --json
python tools/agent_case/bundle_verifier.py --bundle <dir> --json

# Exit codes (identical across all three subcommands so callers can branch
# uniformly): 0 ok / 1 a verification layer FAILED or the command errored /
# 2 nothing failed but something could not be verified / 3 bundle unreadable.
# An empty session exits 0 — "no records for this session" is a finding to
# report, not an error to raise.
# tools/agent_case/bundle_format.py is a library (no CLI) — import build_manifest,
# write_manifest, compute_event_hmac, compute_audit_row_hash.
---

## Unified Approval Inbox — ACE + workflow_hitl adapters (agov-inbox-05)

ICDEV has four approval gates asking a human the same question through four
unrelated stores. These adapters give three of them one queue **without
rewriting any of them**.

```bash
python tools/agent_runtime/inbox_adapters.py --list --json
python tools/agent_runtime/inbox_adapters.py --list --origin ace
python tools/agent_runtime/inbox_adapters.py --resolve <item_id> --approve \
    --actor ops-oncall --reason "reviewed" --json
python tools/agent_runtime/inbox_adapters.py --resolve <item_id> --deny \
    --reason "not authorised" --json
```

**Use this `--resolve`, not `approval_inbox.py --resolve`, for a mirrored item.**
The store settles the row; only the adapter knows how to release what was
waiting on it — INSERTing the ACE `hitl_resolved` row that wakes a parked
`CoWorkerThread`, or calling `submit_feedback` to advance a workflow stage.

Each gate keeps its own store as the source of truth for its own waiter, and
`approval_items` is a **mirror** of those:

| Origin | Pending state | Released by |
|--------|---------------|-------------|
| `ace` | `ace_audit_log` row, `action='hitl_pending'` | INSERTing a matching `hitl_resolved` row |
| `workflow_hitl` | `wf_approvals` row, `status='pending'` | `feedback.submit_feedback` |

**Mirroring is best-effort; resolution is bidirectional.** An unmigrated or
unreachable inbox leaves the originating gate holding exactly as it does today —
failing the ACE gate closed on a mirror error would make an optional delivery
channel load-bearing, and failing it open would turn a missing table into an
approval. Answering in the ACE UI (`POST /api/ace/<id>/hitl`) settles the
mirrored item; answering in the inbox releases the ACE thread.

`ace_audit_log` stays **append-only**: a resolution INSERTs a new row, and
nothing in this path UPDATEs an ACE row. The mutable state lives only in
`approval_items` (migration `20260809203855`).

**`tools/integration/approval_manager.py` is deliberately out of scope.**
Document-, COA- and boundary-level approval with multi-reviewer lists has a
different lifetime and audience from a mid-run tool-call gate, and its reviewer
semantics do not survive being flattened into one item with one `resolved_by`.
# tools/agent_case/timeline_redaction.py is a library (no CLI) — import
# TimelineRedactor / impact_level_for. The timeline redacts by default; --no-redact
# is the opt-out and says so in the output and in the result's `limits`.
#
# Redaction masks the DISPLAY projection only. entry["record"] stays byte-exact,
# which is what lets `verify` re-compute the hook_events HMACs and the
# migration-149 audit hash chain over a bundle built from the same timeline.
# Findings are placed at their LAST contributing event, not at their own
# created_at, and list every event id they cite; an id belonging to another
# session is reported under `unresolved_event_ids` and never pulls that event in.
# Two runs over the same rows are byte-identical — import canonical_timeline /
# canonical_json / timeline_digest from session_timeline to check that yourself.
