# Auto-Registered (Coherence Fix)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Macro Data | tools\trading\data\macro_data.py | Auto-registered: data/macro_data.py | --json | JSON |



## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Alert Engine | tools\trading\market_intel\alert_engine.py | Auto-registered: market_intel/alert_engine.py | --json | JSON |
| Auto Trader | tools\trading\market_intel\auto_trader.py | Auto-registered: market_intel/auto_trader.py | --json | JSON |
| Alpaca Adapter | tools\trading\brokers\alpaca_adapter.py | Stdlib REST client for Alpaca paper/live (account, orders, positions, bars, quotes). Falls back to sample data when creds missing. | --account, --quote SYM, --bars SYM, --limit N, --json | JSON |
| Kill Switch | tools\trading\risk\kill_switch.py | Global auto-trading halt (env / file / DB sources). Operator-toggleable from dashboard. | --status, --trip REASON, --clear, --by NAME, --json | JSON |
| PDT Tracker | tools\trading\risk\pdt_tracker.py | Pattern Day Trader rule pre-flight check (3 daytrades / 5 days under $25k). Reads broker account when available. | --daytrade, --json | JSON |
| Drawdown Monitor | tools\trading\risk\drawdown_monitor.py | Daily P&L watcher; warns at -1%, halts at -2% (auto-trips kill-switch). | --warn PCT, --halt PCT, --json | JSON |
| VIX Sizing | tools\trading\risk\vix_sizing.py | VIX-conditional position sizing scale factor (0.4x–1.5x). Applied in auto_trader qty calc. | --vix N, --base-qty N, --json | JSON |
| VIX Term Structure | tools\trading\risk\vix_term_structure.py | ^VIX9D/^VIX/^VIX3M/^VIX6M curve shape (CONTANGO/NORMAL/FLAT/BACKWARDATED); gates new longs on deep backwardation (≥5%). 5-min cache. | --gate, --json | JSON |
| Adaptive Stops | tools\trading\risk\adaptive_stops.py | VIX-adaptive stop/target width (widens at higher VIX to prevent noise stop-outs). | --base-stop N, --base-target N, --vix N, --json | JSON |
| VIX Weight Rotation | tools\trading\analysis\vix_weight_rotation.py | VIX-conditional signal weight rotation (momentum in low-vol, mean-reversion in high-vol). | --vix N, --json | JSON |
| Vol Divergence | tools\trading\analysis\vol_divergence.py | Realized vs implied vol comparison (IV-RV); flags vol_rich / hedges_cheap / aligned. | --ticker SYM, --window N, --json | JSON |
| Hedge Recommender | tools\trading\risk\hedge_recommender.py | Tail-risk hedge advisory (VXX / SPX puts / put-write overlay); text only — never executes options. | --equity N, --json | JSON |
| Confluence: VIX Structure | tools\trading\analysis\confluence_pillars\vix_structure.py | VIX term-structure pillar (contango=bull, backwardation=bear). | library module | PillarVote |
| Trade Audit | tools\trading\audit\trade_audit.py | Append-only NIST-AU audit trail for signal/order lifecycle (ad_trade_audit). | --query, --ticker, --event, --limit, --json | JSON |
| Rollout Preset Loader | tools\trading\rollout\preset_loader.py | Loads phased rollout configs (micro_live → scale_10k → scale_25k → scale_100k). Refuses load unless ICDEV_TRADING_TIER env matches tier. | --list, --tier NAME, --check-short, --locate-ok, --json | JSON |
| Order Poller | tools\trading\execution\order_poller.py | Polls broker for terminal status of non-terminal local orders; updates ad_orders + audit row on transition. Idempotent. | --limit N, --json | JSON |
| Position Reconciler | tools\trading\execution\position_reconciler.py | Diffs local ad_positions vs broker.list_positions(); reports qty/price/orphan drift. Read-only by default. | --portfolio-id ID, --auto-fix, --json | JSON |
| Exit Manager | tools\trading\execution\exit_manager.py | Position exit rules: stop_loss / take_profit / trailing_stop / time_stop. Auto-registered at entry by auto_trader. | --register, --evaluate, --list, --ticker SYM, --kind K, --pct P, --max-hold-hours H, --json | JSON |
| Exit Executor | tools\trading\execution\exit_executor.py | Closes round-trip: reads triggered exits, submits market SELL via order_manager, marks executed. Idempotent; broker-rejected exits are NOT retried. | --run, --dry-run, --json | JSON |
| Market Calendar | tools\trading\calendar\market_calendar.py | NYSE session classifier (regular/pre/post/closed/half-day) + holiday awareness through 2028. | --at ISO, --json | JSON |
| Earnings Calendar | tools\trading\calendar\earnings_calendar.py | Earnings blackout windows (default ±24h around report). Persisted in ad_earnings_calendar. | --add, --check, --upcoming, --ticker SYM, --report-at ISO, --days N, --json | JSON |
| Slippage Tracker | tools\trading\analytics\slippage_tracker.py | Per-order + rolling slippage in bps; alerts when avg drifts past threshold. Tagged at order time by auto_trader. | --summary, --per-order, --ticker SYM, --days N, --alert-bps N, --json | JSON |
| Strategy Attribution | tools\trading\analytics\strategy_attribution.py | FIFO P&L attribution per strategy_id; surfaces retirement candidates. | --days N, --retire, --min-fills N, --max-realized N, --json | JSON |
| Confluence Scorer | tools\trading\analysis\confluence_scorer.py | Explicit N-of-M pillar agreement score + tier (A/B/C/D) + sizing/exit tuning; 3-source enable toggle (env / file / DB). | --ticker SYM, --signal-id ID, --status, --enable, --disable, --persist, --json | JSON |
| Confluence: Multi-Timeframe | tools\trading\analysis\confluence_pillars\multi_timeframe.py | Daily/weekly/monthly trend alignment pillar for confluence scorer. | library module (imported by confluence_scorer) | PillarVote |
| Confluence: Price Levels | tools\trading\analysis\confluence_pillars\price_levels.py | Support/MA/fib/round/pivot clustering within ±1.5% of current price. | library module | PillarVote |
| Confluence: TA Stack | tools\trading\analysis\confluence_pillars\ta_stack.py | RSI(14) / MACD(12,26) / MA cross / Bollinger ±2σ alignment pillar. | library module | PillarVote |
| Confluence: Event Stack | tools\trading\analysis\confluence_pillars\event_stack.py | Analyst upgrades / earnings beat / insider buys / news cluster pillar. | library module | PillarVote |
| Decision Snapshot | tools\trading\audit\decision_snapshot.py | Append-only immutable per-signal decision context (sha256-hashed for tamper detection). Append-only via APPEND_ONLY_TABLES. | --get SIGID, --list, --ticker SYM, --verify SIGID, --limit N, --json | JSON |
| Decision Replay | tools\trading\audit\decision_replay.py | Reconstructs full signal→order→fill→exit chain for one signal_id; supports diff between two snapshots. | --signal-id SIGID, --diff ID_A ID_B, --json | JSON |
| Signal Explainer | tools\trading\llm\signal_explainer.py | LLM-backed narrative per signal_id; templates when no LLM available. Cached in ad_signal_narratives. | --signal-id SIGID, --refresh, --json | JSON |
| Pillar Weight Learner | tools\trading\ml\pillar_weight_learner.py | Elastic-net learns confluence pillar weights from realized P&L; learned weights override confluence_scorer defaults automatically. | --train, --active, --days N, --alpha N, --l1-ratio N, --json | JSON |
| Fill Quality Model | tools\trading\ml\fill_quality_model.py | Gradient-boost predicts per-order slippage (bps); auto_trader skips when predicted > alpha budget. Persisted at data/ml_models/fill_quality.pkl. | --train, --predict, --ticker SYM, --side buy/sell, --qty N, --vix N, --json | JSON |
| Earnings Extractor | tools\trading\llm\earnings_extractor.py | LLM extracts guidance/tone/risk flags/supply-chain/AI capex from transcripts/10-K; cached by (ticker, filing_hash) in ad_earnings_analysis. Heuristic fallback. | --ticker SYM, --text/--file, --filing-type, --period, --refresh, --latest, --json | JSON |
| Confluence: Earnings LLM | tools\trading\analysis\confluence_pillars\earnings_llm.py | Earnings-analysis pillar: guidance raised+bullish→bull, lowered/bearish→bear. | library module | PillarVote |
| Regime HMM | tools\trading\ml\regime_hmm.py | Learned 5-state Gaussian HMM over (VIX, vix_slope, RV, yield_spread, DXY); auto-labeled DEEP_RISK_ON→DEEP_RISK_OFF; compatibility shim to GREEN/YELLOW/RED. | --train, --train-from CSV, --predict-current, --json | JSON |
| Exit Outcome Recorder | tools\trading\feedback\exit_outcome_recorder.py | Records realized P&L per triggered exit tagged with tier/regime/vix; feeds exit-tuning analytics. | --run, --stats, --days N, --json | JSON |
| Counterfactual Tracker | tools\trading\feedback\counterfactual_tracker.py | Scores fill-quality skips against 24h price moves (was the skip correct?); resolved=True records, feeds future model training. | --resolve, --stats, --min-age-hours N, --json | JSON |
| Strategy Retirement | tools\trading\feedback\strategy_retirement.py | Auto-disables strategies with ≥5 fills and negative realized P&L over window; default/unattributed protected. | --run, --list, --reactivate ID, --dry-run, --json | JSON |
| ML Model Registry | tools\trading\ml\model_registry.py | Consolidated freshness/metric view for all ML models; feeds /api/fathomdesk/ml-health + dashboard tiles. | --health, --json | JSON |
| Earnings Batch LLM | tools\trading\llm\earnings_batch.py | One LLM call for N tickers (vs N calls). Results persisted in ad_earnings_analysis. Heuristic fallback. | --jsonl-file PATH, --filing-type, --json | JSON |
| Per-Cycle Cache | tools\trading\analysis\_per_cycle_cache.py | Process-local TTL cache used by auto_trader to dedupe quote/regime/confluence lookups per cycle. | library module | n/a |
| Batch Scanner | tools\trading\market_intel\batch_scanner.py | Auto-registered: market_intel/batch_scanner.py | --json | JSON |
| Cascade Engine | tools\trading\market_intel\cascade_engine.py | Auto-registered: market_intel/cascade_engine.py | --json | JSON |
| Expert Agents | tools\trading\market_intel\expert_agents.py | Auto-registered: market_intel/expert_agents.py | --json | JSON |
| Forecaster | tools\trading\market_intel\forecaster.py | Auto-registered: market_intel/forecaster.py | --json | JSON |
| Kg Seeder | tools\trading\market_intel\kg_seeder.py | Auto-registered: market_intel/kg_seeder.py | --json | JSON |
| Scenario Engine | tools\trading\market_intel\scenario_engine.py | Auto-registered: market_intel/scenario_engine.py | --json | JSON |
| Universe | tools\trading\market_intel\universe.py | Auto-registered: market_intel/universe.py | --json | JSON |
| Market Intel Daemon | tools/trading/market_intel/daemon.py | FathomDesk autonomous trading daemon — 11 schedule-driven reflexes (market_scanner, approved_monitor, macro_watcher, alert_detector, kg_enricher, gap_detector, scenario_analyzer, expert_advisor, exit_executor, daily_brief, portfolio_strategist). Circuit breakers, trust kernel, audit trail (trading_daemon_reflex_state, trading_daemon_audit). Config: args/trading_daemon_config.yaml | --start, --once REFLEX, --status, --reset REFLEX, --json | Daemon run results / reflex status JSON |
| Market Intel Gap Detector | tools/trading/market_intel/gap_detector.py | Structural gap detector for FathomDesk knowledge graph using modularity-based community detection to identify blind spots in market pricing | --detect, --json | Gap findings |
| Market Intel Judge | tools/trading/market_intel/judge.py | LLM-as-a-Judge for FathomDesk with Socratic feedback loop; uses qwen3.5 for expert challenges and Claude for synthesis | --judge, --json | Judgment results |



## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Cascade Bridge | tools\simulation\cascade_bridge.py | BFS cascade through simulation KG; traces multi-level impacts across architecture, compliance, supply_chain, schedule, cost, risk dimensions. | --project-id (--project alias), --trigger, --node, --depth, --width, --gate, --json | JSON cascade result |
| Query Parser | tools\simulation\query_parser.py | Auto-registered: simulation/query_parser.py | --json | JSON |
| Risk Monitor | tools\simulation\risk_monitor.py | Live composite + CPARS risk scorer using weighted formulas. On-demand or periodic daemon mode. | --project-id (--project alias), --contract, --gate, --persist, --json | JSON risk scores |



## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Injection Scanner | tools\security\injection_scanner.py | Auto-registered: security/injection_scanner.py | --json | JSON |
| Telegram Listener | tools\notifications\adapters\telegram_listener.py | Auto-registered: adapters/telegram_listener.py | --json | JSON |
| Telegram Connector | tools\databridge\connectors\telegram_connector.py | Auto-registered: connectors/telegram_connector.py | --json | JSON |



## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Alpha Calculator | tools\trading\factors\alpha_calculator.py | Auto-registered: factors/alpha_calculator.py | --json | JSON |
| Cost Model | tools\trading\factors\cost_model.py | Auto-registered: factors/cost_model.py | --json | JSON |
| Factor Data | tools\trading\factors\factor_data.py | Auto-registered: factors/factor_data.py | --json | JSON |
| Factor Regression | tools\trading\factors\factor_regression.py | Auto-registered: factors/factor_regression.py | --json | JSON |
| Election Phase | tools\trading\factors\election_phase.py | US presidential 4-year cycle classifier (POST/MIDTERM/PRE/ELECTION) + sweet-spot detector + premium multipliers | --date YYYY-MM-DD, --history, --json | JSON |
| Regime Premiums | tools\trading\factors\regime_premiums.py | Auto-registered: factors/regime_premiums.py | --json | JSON |
| Signal Validator | tools\trading\factors\signal_validator.py | Auto-registered: factors/signal_validator.py | --json | JSON |
| Skill Tracker | tools\trading\factors\skill_tracker.py | Auto-registered: factors/skill_tracker.py | --json | JSON |



## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Govcon Scan | tools\genesis\reflexes\govcon_scan.py | Auto-registered: reflexes/govcon_scan.py | --json | JSON |



## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Export Import | tools\network\export_import.py | Auto-registered: network/export_import.py | --json | JSON |
| Inventory Export | tools\network\inventory_export.py | Ansible inventory INI (hosts grouped by zone/role) and Terraform HCL skeleton (VPC, subnet, security group) derived from topology graph. Routes: POST /api/export/<topo_id>/ansible, POST /api/export/<topo_id>/terraform | graph dict | .ini / .tf text |
| Montecarlo | tools\network\montecarlo.py | Auto-registered: network/montecarlo.py | --json | JSON |



## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Kanban Scheduler | tools\genesis\kanban_scheduler.py | Long-running Kanban reflex scheduler. Startup-recovery resets interrupted tasks. Single-instance lockfile guard. | --interval N, --once, --json | JSON status (with --once --json) |
| Add Hpc To Aiml | tools\network\add_hpc_to_aiml.py | Auto-registered: network/add_hpc_to_aiml.py | --json | JSON |
| Ato Generator | tools\network\ato_generator.py | Auto-registered: network/ato_generator.py | --json | JSON |
| Fix Template Zones | tools\network\fix_template_zones.py | Auto-registered: network/fix_template_zones.py | --json | JSON |
| Update Template Zones | tools\network\update_template_zones.py | Auto-registered: network/update_template_zones.py | --json | JSON |
| OCR Fallback | tools/network/ocr_fallback.py | OCR-based diagram extraction fallback (pytesseract + rapidocr-onnxruntime ensemble) for air-gap environments without vision LLM. Spatial proximity inference for connection detection. | --image, --check, --json, --gate | JSON topology / status |
| GNS3 Adapter | tools/network/adapters/gns3_adapter.py | GNS3 v2 REST API client for lab orchestration — project CRUD, node start/stop/snapshot/capture, link creation. Stdlib urllib only. Graceful unreachable-backend handling. | (library) — `GNS3Adapter(url, username, password)`; `health()`, `create_project()`, `add_node()`, `link_nodes()`, `start_topology()`, `snapshot()`, `capture()` | Project/node dicts or {status:unreachable} |
| NDC SOPs | tools/network/sops.py | Network SOP (Standard Operating Procedure) CRUD + draft→review→approved workflow. Example SOPs: change window, circuit provisioning, firewall rule change, DNS update. | (library) — `create_sop`, `list_sops`, `submit_for_review`, `approve_sop`, `reject_sop`, `deprecate_sop`, `get_approval_history` | SOP dicts |
| NDC Snapshots | tools/network/snapshots.py | Frozen restorable design snapshots (topology + device configs + lineage) with SHA256 canonical-JSON integrity check. Supports blob_uri for .gns3project tarballs. | (library) — `create_snapshot`, `list_snapshots`, `get_snapshot`, `restore_snapshot`, `verify_snapshot` | Snapshot manifest + sha256 |
| NDC Lab Clone | tools/network/lab_clone.py | "Sanitize → Lab Mode" clone of production designs with deterministic secret/IP/PII redaction, external-peering disablement, UNCLASSIFIED re-marking. Redaction log records audit without plaintext secrets. | (library) — `sanitize_to_lab(design_id)`, `redact_text(text)`, `rewrite_ip_to_rfc1918(ip)`; CLI `--design-id` | clone_id + redaction_log |
| NDC Container Node | tools/network/container_node.py | Docker container as first-class topology node. Whitelist of 13 approved images (FRR, Suricata, NGINX, etc.) gated by Supply Chain Boundary. | (library) — `validate_container_node`, `create_container_node`, `list_container_nodes` | Validation + persistence results |
| NDC Lab Health | tools/network/lab_health.py | Lab backend health probe (adapter-first + TCP/HTTP fallback). Classifies green/yellow/red based on reachability + capacity. 30s poll cadence via /network/labs dashboard. | (library) — `load_backends`, `probe_backend`, `probe_all`; CLI `--json`, `--name` | Backend health list |
| NDC Airgap Bundle | tools/network/airgap_bundle.py | Offline air-gap lab bundle builder — tars GNS3/Containerlab binaries + curated images into a signed tar.gz. Generates CycloneDX 1.4 SBOM per image, SHA-256 manifest for all files, and registers the bundle in Boundary Canvas as an authorized component (bd_authorized_components). Supports IL4/IL5/IL6 SCIF/SIPR enclaves where pulling from gns3.com is forbidden. | CLI `--build --output bundle.tar.gz --images-dir DIR`, `--verify bundle.tar.gz`, `--list-registered`, `--json`, `--gate` | Bundle tar.gz + manifest.json + sbom.json + BDC registration |
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Stig Import | tools\network\stig_import.py | Auto-registered: network/stig_import.py | --json | JSON |
| Intent Validator | tools\network\intent_validator.py | Network Canvas intent-based topology validation engine — bandwidth, redundancy, isolation, latency, encryption constraints | --json | JSON |
| Change Request | tools\network\change_request.py | Network Canvas Change Request Markup engine — add/remove/modify markup (green/red/yellow), CAB review document with before/after diffs | --json | JSON |
| NetBox Client | tools\network\netbox_client.py | NetBox REST API client — pull devices, IP allocations, VLANs, prefixes, racks, circuits; push canvas nodes back as NetBox devices. Stdlib only (no third-party deps). Blueprint routes: GET /api/netbox/status, POST /api/netbox/configure, GET/POST /api/netbox/pull/*, POST /api/netbox/import/<topo_id>, POST /api/netbox/push/<topo_id>, GET /api/netbox/sync-log | --url, --token, --pull, --site, --json, --gate | JSON / text |
| Auto-Discovery | tools\network\discovery.py | Live network auto-discovery ENGINE — SNMP/SSH/CDP/LLDP neighbour crawl, ping sweep, JointJS graph builder, as-designed vs as-built diff. Optional deps: pysnmp, netmiko. A LIBRARY + CLI: it persists nothing and serves no route. This row used to claim eight blueprint routes and NONE of them existed (rmf-disc-02) — the page at /network/discovery returned the string "Discovery page coming soon" with HTTP 200. The routes now live in tools/network/routes/discovery.py and persistence in tools/network/discovery_store.py. | --target, --method, --community, --username, --diff, --json | JSON / text |
| Discovery Store | tools\network\discovery_store.py | rmf-disc-02. Persistence and inventory seam for the discovery engine, and the ONLY writer of ni_devices on the discovery path — the routes and the asset_discovery reflex both call it rather than writing their own INSERT. create_scan/record_scan_result/record_scan_failure, list_scans/get_scan/delete_scan (cascades to nc_discovery_diffs), import_to_topology (merge|replace), run_diff (persists nc_discovery_diffs), import_scan_devices (source='discovery'), seed_synthetic_devices (source='synthetic'), device_inventory_stats (counts BY PROVENANCE; reports measurable:false rather than a zero when the table is unreadable). _safe_config strips SNMP community strings and SSH passwords before a scan config is persisted, recording only THAT authentication happened. | `--stats`, `--list-scans`, `--scan ID`, `--delete-scan ID`, `--seed-synthetic [--topology-id ID] [--count N] [--seed N]`, `--json` | JSON |
| Discovery Routes | tools\network\routes\discovery.py | rmf-disc-02. The /network/discovery page (with context: scans, topologies, protocol availability, inventory) plus the five endpoints the page has always called and that were DEFINED NOWHERE: POST /api/discovery/scan, GET|DELETE /api/discovery/scans/<id>, POST /api/discovery/scans/<id>/import/<topo>, POST /api/discovery/diff — and GET /api/discovery/scans, GET /api/discovery/inventory. A scan is SYNCHRONOUS and bounded by MAX_TARGETS (1024 addresses); an oversized sweep is refused WHOLE and names the count rather than being truncated. | (Flask routes) | JSON |



## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Intent Validator | tools\network\intent_validator.py | Auto-registered: network/intent_validator.py | --json | JSON |



## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Topology Styler | tools\network\topology_styler.py | Auto-registered: network/topology_styler.py | --json | JSON |



## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Visio Export | tools\network\visio_export.py | Auto-registered: network/visio_export.py | --json | JSON |



## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Vuln Overlay | tools\network\vuln_overlay.py | Auto-registered: network/vuln_overlay.py | --json | JSON |



## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Bandwidth Sim | tools\network\bandwidth_sim.py | Auto-registered: network/bandwidth_sim.py | --json | JSON |
| Nl Query | tools\network\nl_query.py | Auto-registered: network/nl_query.py | --json | JSON |



## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Cloud Architecture | tools\network\cloud_architecture.py | Auto-registered: network/cloud_architecture.py | --json | JSON |



## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Deploy Catalog | tools\pipeline\deploy_catalog.py | Auto-registered: pipeline/deploy_catalog.py | --json | JSON |
| Deploy Generator | tools\pipeline\deploy_generator.py | Auto-registered: pipeline/deploy_generator.py | --json | JSON |
| E2E Devops Canvas | tools\testing\e2e_devops_canvas.py | Auto-registered: testing/e2e_devops_canvas.py | --json | JSON |



## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Antipattern Detector | tools\pipeline\antipattern_detector.py | Auto-registered: pipeline/antipattern_detector.py | --json | JSON |
| Iac Validator | tools\pipeline\iac_validator.py | Auto-registered: pipeline/iac_validator.py | --json | JSON |
| Seed Runbooks | tools\sre\seed_runbooks.py | Auto-registered: sre/seed_runbooks.py | --json | JSON |



## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Security Canvas Agent | tools\security_canvas\agent.py | ICDEV™ Security Design Canvas intelligent agent — auto-triggers STRIDE assessments on NDC topology save, IaC scan on IaC generation, CI/CD security on pipeline save, and SDC posture scoring. Pure deterministic Python, no LLM. | (library — imported by security_canvas/blueprint.py) | dict / JSON |
| Security Engine | tools\security_canvas\security_engine.py | Auto-registered: security_canvas/security_engine.py | --json | JSON |
| SDC Compliance KG | tools\security_canvas\compliance_kg.py | Builds sdc-compliance-kg: STRIDE→NIST→framework traversable graph | --build / --node-info / --path-from / --stride-coverage / --sdc-ctrl-coverage --json | JSON |
| SDC NL Query | tools\security_canvas\nl_query.py | Natural language query engine for SDC compliance graph; auto-builds KG if missing | --query "..." --build --json | JSON |
| SDC ATO Artifacts | tools/security_canvas/artifacts.py | Generates SSP, SAR, and POA&M ATO artifacts as Markdown from security design assessment data; no LLM dependency | generate_artifact_bundle(design_id, design_name, graph_data) | dict {ssp, sar, poam, metadata} |
| SDC NDC Bridge | tools/security_canvas/bridge.py | Bidirectional sync between Network Design Canvas and Security Design Canvas — imports NDC topology as SDC security design, syncs NDC compliance findings as SDC threats, pushes SDC remediation back to NDC as compliance fixes | import_ndc_topology(topology_id) / sync_ndc_compliance(topology_id) / push_sdc_remediation(design_id) | dict / JSON |
| Remediation Engine | tools/security_canvas/remediation.py | Deterministic remediation planning: generates phased remediation plans, POA&M entries, and effort estimates from security assessment results. No LLM dependency. | (library) generate_remediation_plan(), generate_poam_entries(), estimate_effort() | Plan dict / POA&M list / effort string |
| SDC IR Runbooks | tools/security_canvas/runbooks.py | Pre-built incident response playbooks for 12 incident types (credential compromise, ransomware, DDoS, data breach, insider threat, supply chain, zero-day, phishing, cloud misconfiguration, API abuse, privilege escalation, data integrity violation), aligned to NIST 800-53 IR/CP/SC control families. Five-phase IR lifecycle (Detect→Contain→Eradicate→Recover→Lessons Learned). No LLM dependency. | (library) get_all_runbooks() / get_runbook_by_id(id) / get_applicable_runbooks(findings) | list[dict] / dict |
| SDC Collaboration | tools/security_canvas/collaboration.py | Polling-based real-time collaboration engine for Security Design Canvas with participant tracking and operation queuing (no WebSocket) | (library) | Collaboration state dict |
| SDC DB Init | tools/security_canvas/db/init_db.py | Database initializer for Security Design Canvas — creates schema and seeds canonical templates; supports SQLite or PostgreSQL backend | --init, --json | DB init status |



## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Importers | tools\security_canvas\importers.py | Auto-registered: security_canvas/importers.py | --json | JSON |



## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| E2E Security Canvas | tools\testing\e2e_security_canvas.py | Auto-registered: testing/e2e_security_canvas.py | --json | JSON |



## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Boundary Engine | tools\boundary_canvas\boundary_engine.py | Pure deterministic assessment engine for the BDC Boundary Design Canvas. Functions: boundary compliance checking (NIST 800-53 controls), ISA lifecycle validation (warning 60d / critical 30d expiry), PPS matrix generation, boundary gap detection, SCIF/ATO/FedRAMP posture scoring. No Flask or LLM dependency — callable as a library from the BDC blueprint. | (library — imported by boundary_canvas/blueprint.py) | dict / JSON |
| Code Gen Agentic | tools\builder\code_gen_agentic.py | Auto-registered: builder/code_gen_agentic.py | --json | JSON |
| Code Gen Core | tools\builder\code_gen_core.py | Auto-registered: builder/code_gen_core.py | --json | JSON |
| Code Gen Multilang | tools\builder\code_gen_multilang.py | Auto-registered: builder/code_gen_multilang.py | --json | JSON |
| Code Gen Python | tools\builder\code_gen_python.py | Auto-registered: builder/code_gen_python.py | --json | JSON |
| Data Engine | tools\data_canvas\data_engine.py | Auto-registered: data_canvas/data_engine.py | --json | JSON |
| Infra Engine | tools\infra_canvas\infra_engine.py | Auto-registered: infra_canvas/infra_engine.py | --json | JSON |
| Blueprint Helpers | tools\network\blueprint_helpers.py | Auto-registered: network/blueprint_helpers.py | --json | JSON |
| Observability Engine | tools\observability_canvas\observability_engine.py | Auto-registered: observability_canvas/observability_engine.py | --json | JSON |
| E2E New Canvases | tools\testing\e2e_new_canvases.py | Auto-registered: testing/e2e_new_canvases.py | --json | JSON |
| Canvas Orchestrator | tools/canvas/orchestrator.py | Cross-Canvas Integration Engine — links all 9 design canvases (IDC/NDC/SDC/BDC/PDC/ODC/DDC/QDC/MDC) via canvas_projects entity in icdev.db; CRUD for design projects, link/unlink designs, aggregate compliance summary, compute 4-dimension readiness score (completeness/compliance/coverage/risk) | create --name / list / summary --json | JSON |
| Canvas KG Builder | tools/canvas/kg_builder.py | Incremental Knowledge Graph builder for all 9 design canvases. rebuild_canvas_kg(canvas, design_id) for targeted on-save upsert; stores nodes/edges to canvas_kg_nodes and canvas_kg_edges in icdev.db; logs every build to canvas_kg_build_log (append-only — NIST AU). | --build-all / --build-canvas idc --design-id \<id\> / --stats --json | JSON |
| Canvas Projects API | tools\dashboard\api\canvas_projects.py | REST API Blueprint for canvas_projects: GET/POST/PUT/DELETE /api/canvas-projects, link/unlink canvas designs, GET /api/canvas-projects/compliance for 7-canvas posture summary | (blueprint) | JSON |
| Canvas Export Utils | tools\canvas\export_utils.py | Unified multi-format export for all 9 design canvases. 5 functions: export_json, export_markdown, export_csv, export_drawio (mxGraphModel XML), export_svg. CUI banner included in all formats. Stdlib only — no external dependencies. | (library — called by canvas blueprints) | JSON / Markdown / CSV / DrawIO XML / SVG |



## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Plan Decomposer | tools\kanban\plan_decomposer.py | Auto-registered: kanban/plan_decomposer.py | --json | JSON |
| Source Stats | tools/kanban/source_stats.py | Analyzes kanban task dispatch sources and verification patterns to identify root causes of failures | --stats, --json | Source/failure breakdown |



## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Base Lens | tools\oracle\base_lens.py | Abstract 3-phase pipeline (analyze → score → propose) for all Oracle lenses; exception isolation per lens | N/A (library) | OraclePrediction list |
| Oracle Prediction | tools/oracle/prediction.py | Canonical OraclePrediction dataclass — id, lens, title, description, confidence, severity, recommendations, data; imported by base_lens and all lenses | N/A (library) | OraclePrediction dataclass |
| Oracle Reflex | tools\oracle\oracle_reflex.py | Orchestrates all 10 Oracle lenses, persists oracle_predictions, emits GKP artifacts; DaemonBase-compatible run() | run(config, trust) | {success, metric_value, details} |
| Trajectory Lens | tools\oracle\lens_trajectory.py | Lens 3: architectural trajectory forecasting — CC/maintainability regression, days-to-threshold, hotspot detection | --json / --gate | JSON |



## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Lens Ecosystem Gap | tools\oracle\lens_ecosystem_gap.py | Lens 1: FORGE-layer manifest gap detection, dead-table scan, coherence recidivism tracking | --json / --gate | JSON |
| Lens Workflow Patterns | tools\oracle\lens_workflow_patterns.py | Lens 2: audit_trail sequential pattern mining, tool-pair co-occurrence, self-heal detection, kanban recurrence | --json / --gate | JSON |
| Lens Regulatory Anticipation | tools\oracle\lens_regulatory.py | Lens 5: regulatory/standards signal crosswalk to ICDEV™ frameworks, effective-date extraction, compliance gap scoring | --json / --gate | JSON |
| Lens Child App Demand | tools\oracle\lens_child_app_demand.py | Lens 6: dossier + SAM.gov + marketplace demand scoring to predict top child-app verticals | --json / --gate | JSON |
| Oracle Kanban Bridge | tools\oracle\kanban_bridge.py | Convert promoted anticipation_report GKPs to suggested kanban tasks; batch-sync backfill | --sync / --gate / --json | JSON |



## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Fast Transforms | tools\builder\fast_transforms.py | Auto-registered: builder/fast_transforms.py | --json | JSON |



## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Lens Convergence | tools\oracle\lens_convergence.py | Auto-registered: oracle/lens_convergence.py | --json | JSON |



## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Iac Generator | tools\infra_canvas\iac_generator.py | Auto-registered: infra_canvas/iac_generator.py | --json | JSON |
| E2E Bdc Canvas | tools\testing\e2e_bdc_canvas.py | Auto-registered: testing/e2e_bdc_canvas.py | --json | JSON |
| E2E Ddc Canvas | tools\testing\e2e_ddc_canvas.py | Auto-registered: testing/e2e_ddc_canvas.py | --json | JSON |
| E2E Idc Canvas | tools\testing\e2e_idc_canvas.py | Auto-registered: testing/e2e_idc_canvas.py | --json | JSON |
| E2E Odc Canvas | tools\testing\e2e_odc_canvas.py | Auto-registered: testing/e2e_odc_canvas.py | --json | JSON |



## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| DDC Introspector | tools/data_canvas/introspector.py | Reverse-engineer live PostgreSQL/SQLite/MySQL/SQL Server into DDC graph nodes (entities + columns + FK edges) — closes idealized-vs-prod gap | --dsn DSN [--db-type auto\|sqlite\|postgresql\|mysql\|sqlserver] [--schema SCHEMA] [--json] [--meta-only] | DDC graph JSON or metadata summary |
| DDC Column Lineage | tools/data_canvas/lineage.py | Pure-function column-level lineage engine: build DAG, compute downstream impact, trace upstream provenance, generate data contract assertions, validate edges | (library — imported by data_engine.py and blueprint.py) | DAG dict, impact/provenance dicts, assertion list |
| DDC dbt Exporter | tools/data_canvas/exporters/dbt.py | Generate dbt sources.yml (raw entity declarations) + model.yml (column descriptions, generic tests, sensitivity tags) from a DDC graph — lets dbt users adopt DDC as their data design entry point | (library — called via export_dbt(name, graph, ...); CLI self-test: python tools/data_canvas/exporters/dbt.py) | (sources_bytes, model_bytes) tuple |
| DDC DataHub Sync | tools/data_canvas/sync/datahub_sync.py | One-way push of DDC entities (datasets/dataJobs), lineage edges, and classification tags into DataHub GMS REST API (v2). Supports dry-run, single-design or all-designs sync. | --design-id ID \| --all [--dry-run] [--json] [--gate] | JSON sync report |
| DDC OpenMetadata Sync | tools/data_canvas/sync/openmetadata_sync.py | One-way push of DDC entities (tables/topics/containers/pipelines), lineage, and DDC classification tags into OpenMetadata REST API v1. Supports dry-run, single-design or all-designs sync. | --design-id ID \| --all [--dry-run] [--json] [--gate] | JSON sync report |
| Pii Detector | tools\data_canvas\pii_detector.py | Auto-registered: data_canvas/pii_detector.py | --json | JSON |
| Cloud Import | tools\infra_canvas\cloud_import.py | Auto-registered: infra_canvas/cloud_import.py | --json | JSON |
| Sigma Generator | tools\observability_canvas\sigma_generator.py | Auto-registered: observability_canvas/sigma_generator.py | --json | JSON |
| Splunk SPL Exporter | tools\observability_canvas\exporters\splunk.py | Convert Sigma rules to Splunk SPL search stanzas; field modifiers (contains/gt/lt/cidr) translated deterministically | library — sigma_to_spl(rule_yaml) / batch_to_spl(rules) | SPL string |
| Elastic Query DSL Exporter | tools\observability_canvas\exporters\elastic.py | Convert Sigma rules to Elasticsearch Query DSL JSON; ECS field mapping, bool/must/should clauses, range/wildcard queries | library — sigma_to_eql(rule_yaml) / batch_to_eql(rules) | JSON string |
| Sentinel KQL Exporter | tools\observability_canvas\exporters\sentinel.py | Convert Sigma rules to Microsoft Sentinel KQL queries; Sentinel table routing by logsource category, contains/range/cidr operators | library — sigma_to_kql(rule_yaml) / batch_to_kql(rules) | KQL string |
| Canvas Indexer | tools\rag\canvas_indexer.py | Auto-registered: rag/canvas_indexer.py | --json | JSON |
| Claude Cli | tools\kanban\executors\claude_cli.py | Auto-registered: executors/claude_cli.py | --json | JSON |
| Gitlab Pipeline | tools\kanban\executors\gitlab_pipeline.py | Auto-registered: executors/gitlab_pipeline.py | --json | JSON |



## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Canvas Auto Remediate | tools\canvas\auto_remediate.py | Confidence-tiered auto-remediation for canvas assessment findings. >=0.7 auto-fix (add missing nodes), 0.3-0.7 suggest, <0.3 escalate. IDC (8 rules) and ODC (8 rules) wrappers. Max 5 auto-fixes/hour rate limiter. | (library — called by IDC/ODC blueprints on save) | JSON remediation report |
| POA&M Auto-Remediator | tools\canvas\auto_remediator.py | Cross-canvas POA&M auto-remediator (CLI). Takes finding hashes (or --all-pending / --all-approved), looks up source design, backs up canvas DB, applies vendor-neutral handler (21 rules across security/observability/boundary canvases), re-runs assessment to verify, marks finding_approvals.decision='remediated', writes audit_trail. 5 IDC rules require vendor selection (file as GitHub issues instead). | --finding-hash, --all-pending, --all-approved, --canvas, --list-handlers, --dry-run, --json, --gate | Per-finding result + summary; updates finding_approvals + audit_trail |
| Agent Toolkit | tools\agent_toolkit\ | OPT-67: unified builtin tool catalog for ICDEV agents (deepagents pattern, MIT-inspired, no upstream runtime dep). 10 primitives: read_file, write_file, edit_file, ls, glob, grep, execute_shell, write_todos, update_todo, spawn_subagent. One-line `create_agent(name, system_prompt)` factory composes the catalog with LLMRouter. Works LLM-free (primitives only) OR as a tool-calling agent loop. See tools/agent_toolkit/__init__.py for exports. | (library) — import tools.agent_toolkit; also tools.agent_toolkit.create_agent() | Agent object with .invoke(messages) -> AgentResult |



## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Canvas Health Scanner | tools\canvas\canvas_health_scanner.py | Auto-registered: canvas/canvas_health_scanner.py | --json | JSON |
| Proposal Generator | tools\oracle\proposal_generator.py | Auto-registered: oracle/proposal_generator.py | --json | JSON |
| Remediation Lens | tools\oracle\lenses\remediation_lens.py | Auto-registered: lenses/remediation_lens.py | --json | JSON |
| Workflow Pattern Lens | tools/oracle/lenses/lens_workflow_patterns.py | Oracle sub-lens: mine frequent 3–5 step sequential event patterns from audit_trail + kanban_tasks; tool-pair co-occurrence (>80% rate = composition candidate); self-heal detection (backlog→in_progress→backlog→done cycles). Called by lens_workflow_patterns.py at root oracle/ level. | --json / --gate | JSON pattern report |



## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| BDC SOPs | tools/boundary_canvas/sops.py | CRUD and approval workflow for Boundary Design Canvas SOPs (ISA renewal, boundary change approval, cross-domain transfer, interconnection decommission). Lifecycle: draft → pending_review → approved/rejected. Functions: get_all_sops, get_sop_by_id, create_sop, update_sop, delete_sop, submit_for_review, approve_sop, reject_sop, seed_sops. NIST control tagging per SOP (CA-3, CM-3, SC-7, etc.). | (library — called by BDC blueprint) | SOP dict / list |
| Sops | tools\observability_canvas\sops.py | Auto-registered: observability_canvas/sops.py | --json | JSON |
| Sops | tools\pipeline\sops.py | Auto-registered: pipeline/sops.py | --json | JSON |
| Gate Executor | tools\qdc_canvas\gate_executor.py | Auto-registered: qdc_canvas/gate_executor.py | --json | JSON |
| Qdc Engine | tools\qdc_canvas\qdc_engine.py | Auto-registered: qdc_canvas/qdc_engine.py | --json | JSON |
| QDC DB Init | tools/qdc_canvas/db/init_db.py | Database initializer for Quality Design Canvas — creates schema and seeds templates, snippets, runbooks, and SOPs; supports SQLite or PostgreSQL backend | --init, --json | DB init status |
| Sops | tools\security_canvas\sops.py | Auto-registered: security_canvas/sops.py | --json | JSON |
| E2E Qdc Canvas | tools\testing\e2e_qdc_canvas.py | Auto-registered: testing/e2e_qdc_canvas.py | --json | JSON |
| Lens Quality | tools\oracle\lenses\lens_quality.py | Auto-registered: lenses/lens_quality.py | --json | JSON |



## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Migration Engine | tools\migration_canvas\migration_engine.py | Auto-registered: migration_canvas/migration_engine.py | --json | JSON |



## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| E2E Diagram Validator | tools\testing\e2e_diagram_validator.py | Auto-registered: testing/e2e_diagram_validator.py | --json | JSON |
| Lens Migration | tools\oracle\lenses\lens_migration.py | Auto-registered: lenses/lens_migration.py | --json | JSON |



## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Config Parser | tools\network\config_parser.py | Auto-registered: network/config_parser.py | --json | JSON |
| Device Manager | tools\network\device_manager.py | Auto-registered: network/device_manager.py | --json | JSON |
| Folder Watcher | tools\network\folder_watcher.py | Auto-registered: network/folder_watcher.py | --json | JSON |
| Ingestion Pipeline | tools\network\ingestion_pipeline.py | Auto-registered: network/ingestion_pipeline.py | --json | JSON |
| Network Ingester | tools\network\network_ingester.py | Auto-registered: network/network_ingester.py | --json | JSON |
| Network Intelligence | tools\network\network_intelligence.py | Auto-registered: network/network_intelligence.py | --json | JSON |
| Network Query Router | tools\network\network_query_router.py | Auto-registered: network/network_query_router.py | --json | JSON |
| Nms Adapter | tools\network\nms_adapter.py | Auto-registered: network/nms_adapter.py | --json | JSON |
| Librenms Adapter | tools\network\adapters\librenms_adapter.py | Auto-registered: adapters/librenms_adapter.py | --json | JSON |
| Netbox Adapter | tools\network\adapters\netbox_adapter.py | Auto-registered: adapters/netbox_adapter.py | --json | JSON |
| Solarwinds Adapter | tools\network\adapters\solarwinds_adapter.py | Auto-registered: adapters/solarwinds_adapter.py | --json | JSON |



## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Naming Engine | tools\network\naming_engine.py | Auto-registered: network/naming_engine.py | --json | JSON |
| Topology Enricher | tools\network\topology_enricher.py | Auto-registered: network/topology_enricher.py | --json | JSON |
| Topology Validator | tools\network\topology_validator.py | Auto-registered: network/topology_validator.py | --json | JSON |



## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Promote Next Phase | tools\awareness\promote_next_phase.py | Auto-registered: awareness/promote_next_phase.py | --json | JSON |



## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Component Indexer | tools\awareness\component_indexer.py | Auto-registered: awareness/component_indexer.py | --json | JSON |
| Enablement | tools\awareness\enablement.py | Auto-registered: awareness/enablement.py | --json | JSON |





## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Glossary Engine | tools\writing\glossary_engine.py | Auto-registered: writing/glossary_engine.py | --json | JSON |
| Rewriter | tools\writing\rewriter.py | Auto-registered: writing/rewriter.py | --json | JSON |
| Style Guide | tools\writing\style_guide.py | Auto-registered: writing/style_guide.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| NDC Analysis Routes | tools/network/routes/analysis.py | Network Design Canvas — Analysis routes (compliance assessment, simulation, NL query dispatch) | (blueprint) | Flask routes |
| NDC Governance Routes | tools/network/routes/governance.py | Network Design Canvas — Governance routes (change requests, CAB approvals) | (blueprint) | Flask routes |
| NDC Ingestion Routes | tools/network/routes/ingestion.py | Network Canvas — Ingestion API Routes (file upload, config ingestion, NMS adapter management, audit log) | (blueprint) | Flask routes |
| NDC Intelligence Routes | tools/network/routes/intelligence.py | Network Infrastructure Intelligence — 16 endpoints for diagram ingestion, device management, 13 analysis dimensions, NL query | (blueprint) | Flask routes |
| NDC Projects Routes | tools/network/routes/projects.py | Network Design Canvas — Project management routes (CRUD for topology projects) | (blueprint) | Flask routes |
| MCP Standalone Builder | tools/mcp/standalone/builder.py | Standalone MCP server wrapper for Builder — resolves install dir, sets sys.path, starts Builder MCP server | (entrypoint) | MCP server process |
| MCP Standalone Compliance | tools/mcp/standalone/compliance.py | Standalone MCP server wrapper for Compliance — resolves install dir, sets sys.path, starts Compliance MCP server | (entrypoint) | MCP server process |
| MCP Standalone Core | tools/mcp/standalone/core.py | Standalone MCP server wrapper for Core — resolves install dir, sets sys.path, starts Core MCP server | (entrypoint) | MCP server process |
| MCP Standalone Knowledge | tools/mcp/standalone/knowledge.py | Standalone MCP server wrapper for Knowledge — resolves install dir, sets sys.path, starts Knowledge MCP server | (entrypoint) | MCP server process |
| MCP Standalone Maintenance | tools/mcp/standalone/maintenance.py | Standalone MCP server wrapper for Maintenance — resolves install dir, sets sys.path, starts Maintenance MCP server | (entrypoint) | MCP server process |
| Audit Reflex | tools/genesis/reflexes/audit.py | Genesis Audit Reflex — daily self-scan: code quality + SAST via existing ICDEV™ analysis tools; aggregates findings into an audit report (GREEN tier) | config dict, trust kernel | Reflex results + GKP export |
| Awareness Reflex | tools/genesis/reflexes/awareness.py | Genesis Awareness Reflex — 3-hour internal self-observation cycle: component graph refresh, health probe, drift detection, gap detection (GREEN tier) | config dict, trust kernel | Reflex results + GKP export |
| Comply Reflex | tools/genesis/reflexes/comply.py | Genesis Comply Reflex — daily cATO evidence freshness check, stale SSP regeneration, crosswalk sync (GREEN tier) | config dict, trust kernel | Reflex results + GKP export |
| Evolve Reflex | tools/genesis/reflexes/evolve.py | Genesis Evolve Reflex — nightly code quality mutation: picks worst-quality file, proposes improvement via phi4-reasoning, exports GKP code_patch for human review (ORANGE tier) | config dict, trust kernel | Reflex results + GKP export |
| Heal Reflex | tools/genesis/reflexes/heal.py | Genesis Heal Reflex — continuous pattern-based auto-remediation: monitors audit trail errors, matches known healing patterns, applies fixes with confidence gating (YELLOW tier, max 5/hour) | config dict, trust kernel | Reflex results + GKP export |
| Ingest Reflex | tools/genesis/reflexes/ingest.py | Genesis Ingest Reflex — every 4h feed ingestion: NIST NVD, CISA KEV, FedRAMP updates → knowledge graph via knowledge_graph ingester (GREEN tier) | config dict, trust kernel | Reflex results + GKP export |
| FathomDesk News Patterns Reflex | tools/genesis/reflexes/fathomdesk_news_patterns.py | Genesis FathomDesk News Patterns Reflex — runs NewsPatternAnalyzer each cycle, emits regime_shift and crackdown patterns, promotes to GKP (GREEN tier) | config dict, trust kernel | Reflex results + GKP export |
| Kanban Reflex | tools/genesis/reflexes/kanban.py | Genesis Kanban Executor Reflex — polls kanban_tasks for due scheduled tasks and dispatches them (GREEN tier) | config dict, trust kernel | Reflex results + GKP export |
| Learn Reflex | tools/genesis/reflexes/learn.py | Genesis Learn Reflex — generate training pairs and fine-tune local Ollama from approved outputs (YELLOW tier) | config dict, trust kernel | Reflex results + GKP export |
| Market Reflex | tools/genesis/reflexes/market.py | Genesis Market Reflex — track marketplace module usage and suggest improvements (GREEN tier) | config dict, trust kernel | Reflex results + GKP export |
| Quality Reflex | tools/genesis/reflexes/quality.py | Genesis Quality Reflex — self-learning QA/QC improvement cycle (YELLOW tier) | config dict, trust kernel | Reflex results + GKP export |
| Research Reflex | tools/genesis/reflexes/research.py | Genesis Research Reflex — scrape NIST/CISA/DoD feeds and GitHub trending; export GKP research signals (GREEN tier) | config dict, trust kernel | Reflex results + GKP export |
| Test Reflex | tools/genesis/reflexes/test.py | Genesis Test Reflex — identify under-tested tools, generate real test stubs, run and keep passing (YELLOW tier) | config dict, trust kernel | Reflex results + GKP export |
| Docs Reflex | tools/genesis/reflexes/docs.py | Genesis Docs Reflex — documentation drift detection and automated repair (GREEN tier) | config dict, trust kernel | Reflex results + GKP export |
| Report Reflex | tools/genesis/reflexes/report.py | Genesis Report Reflex — weekly autonomous status report with promotions, circuit breakers, and reflex activity (GREEN tier) | config dict, trust kernel | Reflex results + GKP export |
| Scout Reflex | tools/genesis/reflexes/scout.py | Genesis Scout Reflex — monitor competitor and adjacent GitHub repos for intelligence briefs (GREEN tier) | config dict, trust kernel | Reflex results + GKP export |
| Publish Reflex | tools/genesis/reflexes/publish.py | Genesis Publish Reflex — end-to-end Pulse article pipeline from demand topic to WriteGuard staging (YELLOW tier) | config dict, trust kernel | Reflex results + GKP export |
| GovCon Scan Reflex | tools/genesis/reflexes/govcon_scan.py | Genesis GovCon Scan Reflex — daily SAM.gov incremental scan and demand signal extraction (GREEN tier) | config dict, trust kernel | Reflex results + GKP export |
| Internal Chat Adapter | tools/gateway/adapters/internal.py | Internal Chat adapter — bridges ICDEV™ /chat page to the Remote Command Gateway | (library) | Adapter API |
| Mattermost Adapter | tools/gateway/adapters/mattermost.py | Mattermost adapter for the Remote Command Gateway (air-gapped environments) | (library) | Adapter API |
| DDC ODPS Exporter | tools/data_canvas/exporters/odps.py | Open Data Product Standard (ODPS) v3 exporter for Data Design Canvas graphs | --export, --json | ODPS JSON artifact |
| DataBridge Code Generator | tools/databridge/forge/code_generator.py | Connector Forge Stage 3 — code generation from connector spec (D-CF-1) | (library) | Generated connector code |
| DataBridge Promoter | tools/databridge/forge/promoter.py | Connector Forge — Sandbox-to-Production promotion workflow (D-CF-6) | (library) | Promotion result |
| DataBridge Connection Pool | tools/databridge/scale/connection_pool.py | Scale Connection Pool — per-connector-type reusable connection pools (D-SC-2) | (library) | Pool handles |
| DataBridge Scale Engine | tools/databridge/scale/engine.py | DataBridge Scale Engine — horizontal scaling orchestrator for connector execution (D-SC-1) | (library) | Scaling metrics |
| Dashboard Agents API | tools/dashboard/api/agents.py | Dashboard API Blueprint — agent status and control endpoints | (blueprint) | Flask routes |
| AI Accountability API | tools/dashboard/api/ai_accountability.py | AI Accountability API Blueprint — REST endpoints for Phase 49 dashboard | (blueprint) | Flask routes |
| AI Transparency API | tools/dashboard/api/ai_transparency.py | AI Transparency API Blueprint — REST endpoints for Phase 48 dashboard | (blueprint) | Flask routes |
| Analytics API | tools/dashboard/api/analytics.py | Dashboard Analytics API — funnel analytics and conversion metrics | (blueprint) | Flask routes |
| Dashboard Audit API | tools/dashboard/api/audit.py | Dashboard API Blueprint — audit trail query endpoints | (blueprint) | Flask routes |
| Dashboard cATO API | tools/dashboard/api/cato.py | Dashboard API Blueprint — Continuous ATO (cATO) endpoints | (blueprint) | Flask routes |
| Dashboard Compliance API | tools/dashboard/api/compliance.py | Dashboard API Blueprint — compliance status and control mapping endpoints | (blueprint) | Flask routes |
| Activity API | tools/dashboard/api/activity.py | Merged activity feed with filters and pagination | (blueprint) | Flask routes |
| Admin API | tools/dashboard/api/admin.py | Admin user management page | (blueprint) | Flask routes |
| ATO Package API | tools/dashboard/api/ato_package.py | Dashboard API: ATO Package Builder | (blueprint) | Flask routes |
| Batch API | tools/dashboard/api/batch.py | Batch task history and management endpoints | (blueprint) | Flask routes |
| Canvas Projects API | tools/dashboard/api/canvas_projects.py | Canvas Projects API — cross-canvas project management | (blueprint) | Flask routes |
| Chat Multi-Stream API | tools/dashboard/api/chat.py | Flask Blueprint for multi-stream parallel chat API (Phase 44) | (blueprint) | Flask routes |
| CI/CD API | tools/dashboard/api/cicd.py | Dashboard API — CI/CD pipeline status endpoints | (blueprint) | Flask routes |
| Code Quality API | tools/dashboard/api/code_quality.py | Code Quality API Blueprint — REST endpoints for code intelligence dashboard (Phase 52) | (blueprint) | Flask routes |
| Compliance Debt API | tools/dashboard/api/compliance_debt.py | Dashboard API: Compliance Debt Burndown | (blueprint) | Flask routes |
| Control Inheritance API | tools/dashboard/api/control_inheritance.py | Dashboard API: Control Inheritance Visualizer | (blueprint) | Flask routes |
| CPMP API | tools/dashboard/api/cpmp.py | Dashboard API: Contract Performance Management Portal (Phase 60) | (blueprint) | Flask routes |
| Diagrams API | tools/dashboard/api/diagrams.py | Diagrams API Blueprint — serves Mermaid diagram catalog and definitions | (blueprint) | Flask routes |
| Events API | tools/dashboard/api/events.py | Events API blueprint — HTTP poll transport, recent events, event ingest | (blueprint) | Flask routes |
| Evidence API | tools/dashboard/api/evidence.py | Dashboard API: Evidence Collection (Phase 56) | (blueprint) | Flask routes |
| FedRAMP 20x API | tools/dashboard/api/fedramp_20x.py | FedRAMP 20x KSI Dashboard API (Phase 53) | (blueprint) | Flask routes |
| FileSync API | tools/dashboard/api/filesync.py | Dashboard API: File Sync Module (D-SYNC-1 through D-SYNC-12) | (blueprint) | Flask routes |
| Fine-Tune API | tools/dashboard/api/finetune.py | Fine-Tuning API Blueprint — REST endpoints for fine-tuning dashboard (Phase 64) | (blueprint) | Flask routes |
| GovCon API | tools/dashboard/api/govcon.py | Dashboard API: GovCon Intelligence — SAM.gov, requirement extraction, opportunity management | (blueprint) | Flask routes |
| IaC Gallery API | tools/dashboard/api/iac.py | Dashboard API: Infrastructure as Code Gallery | (blueprint) | Flask routes |
| Intake API | tools/dashboard/api/intake.py | AI-driven requirements intake session endpoints | (blueprint) | Flask routes |
| Kanban API | tools/dashboard/api/kanban.py | Kanban Task Board API — CRUD for task cards on the dashboard Kanban | (blueprint) | Flask routes |
| Kanban Plan API | tools/dashboard/api/kanban_plan.py | Kanban Plan API — task decomposition and scheduling endpoints | (blueprint) | Flask routes |
| Lineage API | tools/dashboard/api/lineage.py | Dashboard API: Artifact Lineage (Phase 56) | (blueprint) | Flask routes |
| Metrics API | tools/dashboard/api/metrics.py | Return recent metric snapshots, optionally filtered by project_id | (blueprint) | Flask routes |
| Migration API | tools/dashboard/api/migration.py | Dashboard API: Migration Tracker (7R Assessment, Plans, Tasks, Artifacts, Progress) | (blueprint) | Flask routes |
| Migration Cost API | tools/dashboard/api/migration_cost.py | Dashboard API: Migration Cost Estimator | (blueprint) | Flask routes |
| NDC Labs API | tools/dashboard/api/ndc_labs.py | NDC Lab Backend Health API | (blueprint) | Flask routes |
| NDC SOPs Dashboard API | tools/dashboard/api/ndc_sops.py | NDC SOPs API — CRUD + approval workflow endpoints | (blueprint) | Flask routes |
| NLQ API | tools/dashboard/api/nlq.py | NLQ (Natural Language Query) API blueprint — compliance database queries | (blueprint) | Flask routes |
| Oracle Dashboard API | tools/dashboard/api/oracle.py | Oracle API — anticipatory intelligence predictions and remediation history | (blueprint) | Flask routes |
| Orchestration API | tools/dashboard/api/orchestration.py | Dashboard API: Real-Time Orchestration Dashboard (Phase 61) | (blueprint) | Flask routes |
| OSCAL API | tools/dashboard/api/oscal.py | OSCAL API Blueprint — REST endpoints for OSCAL ecosystem (D302-D306) | (blueprint) | Flask routes |
| POAM API | tools/dashboard/api/poam.py | ICDEV Canvas Findings (POA&M) approval API | (blueprint) | Flask routes |
| PR Intel API | tools/dashboard/api/pr_intel.py | Dashboard API: PR Intelligence / Compliance Drift | (blueprint) | Flask routes |
| Prod Audit API | tools/dashboard/api/prod_audit.py | Production Audit API Blueprint — REST endpoints for audit and remediation (D291-D300) | (blueprint) | Flask routes |
| Projects API | tools/dashboard/api/projects.py | Dashboard projects listing and management API | (blueprint) | Flask routes |
| Proposal Genesis Dashboard API | tools/dashboard/api/proposal_genesis.py | Dashboard API: Proposal Genesis — autonomous capture-to-delivery daemon | (blueprint) | Flask routes |
| Proposals API | tools/dashboard/api/proposals.py | Dashboard API: Proposal Writing Lifecycle Tracker | (blueprint) | Flask routes |
| RAG Eval API | tools/dashboard/api/rag_eval.py | RAG Evaluation Dashboard API — campaign listing, quality status, dataset balance | (blueprint) | Flask routes |
| Sandbox API | tools/dashboard/api/sandbox.py | OPT-57 Sandbox liveness API | (blueprint) | Flask routes |
| SbD API | tools/dashboard/api/sbd.py | Dashboard API: Secure by Design Assessment (CISA SbD) | (blueprint) | Flask routes |
| Security Scan API | tools/dashboard/api/security_scan.py | Dashboard API: Security Scan Results | (blueprint) | Flask routes |
| SRE API | tools/dashboard/api/sre.py | SRE API Blueprint — SLO, incident, runbook, DORA, and chaos endpoints | (blueprint) | Flask routes |
| STIG Manager API | tools/dashboard/api/stig_manager.py | Dashboard API: STIG Benchmark Manager | (blueprint) | Flask routes |
| Studio API | tools/dashboard/api/studio.py | ICDEV Studio API Blueprint | (blueprint) | Flask routes |
| Traces API | tools/dashboard/api/traces.py | Traces API Blueprint — REST endpoints for observability (Phase 46) | (blueprint) | Flask routes |
| Usage API | tools/dashboard/api/usage.py | Dashboard usage tracking and user activity API | (blueprint) | Flask routes |
| WriteGuard API | tools/dashboard/api/writeguard.py | Dashboard API: WriteGuard — Content Quality Analysis, Export and History | (blueprint) | Flask routes |
| CI Event Envelope | tools/ci/core/event_envelope.py | Unified event envelope for all CI/CD trigger sources (D132) — normalizes GitHub, GitLab, and webhook events | (library) | EventEnvelope dataclass |
| AppForge Architect Reflex | tools/appforge/reflexes/architect.py | AppForge Architect Reflex — generate app blueprint from selected challenge | config dict | Blueprint artifact |
| AppForge Build Reflex | tools/appforge/reflexes/build.py | AppForge Build Reflex — create standalone child app from blueprint | config dict | Build result |
| AppForge Evaluate Reflex | tools/appforge/reflexes/evaluate.py | AppForge Evaluate Reflex — score and select the best challenge to build next | config dict | Evaluation scores |
| Claude CLI Adapter | tools/agents/adapters/claude_cli.py | OPT-71: Claude Code CLI adapter — bridges Claude Code CLI to the ICDEV™ agent adapter API | (library) | Adapter API |
| Codex CLI Adapter | tools/agents/adapters/codex_cli.py | OPT-71: OpenAI Codex CLI adapter stub — placeholder for Codex CLI integration | (library) | Adapter API |
| Copilot CLI Adapter | tools/agents/adapters/copilot_cli.py | OPT-71: GitHub Copilot CLI adapter stub — placeholder for Copilot CLI integration | (library) | Adapter API |
| Local LLM Router Adapter | tools/agents/adapters/local_llm_router.py | OPT-71: Local LLMRouter adapter — routes agent requests to local Ollama models | (library) | Adapter API |





## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Prebuild Bootstrap | tools\installer\prebuild_bootstrap.py | Auto-registered: installer/prebuild_bootstrap.py | --json | JSON |
| Sync Package Tree | tools\installer\sync_package_tree.py | Auto-registered: installer/sync_package_tree.py | --json | JSON |



## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Build Release | tools\installer\build_release.py | Auto-registered: installer/build_release.py | --json | JSON |
| Validate Package Config | tools\installer\validate_package_config.py | Auto-registered: installer/validate_package_config.py | --json | JSON |
