# CUI // SP-CTI
"""Seed Kanban tasks for SIPA — Software Integrity & Provenance Assessor.

Project: sipa  (task_prefix 'sipa-')  — code-integrity / supply-chain assessment.
Plan: C:/Users/schuo/.claude/plans/i-want-to-work-quiet-hejlsberg.md
Spec: docs/features/phase-sipa-software-integrity.md

Detects malicious code / unauthorized workloads (embedded by a human OR an AI agent)
that are NOT part of the original requirement/PRD, across ICDEV's own code, contributed
code, and external software (GitHub/GitLab/UNC/URI). Two modes:
  Mode A (PRD known)  -> capability vs RTM-authorized capability ("semantic backdoor")
  Mode B (PRD unknown)-> capability vs claimed purpose ("undisclosed capability")
Static-only (never executes target code) + quarantine-first + HITL blocking gate +
deterministic core with optional LLM-assist (fallback).

Task IDs follow the <prefix><epic>-<N> convention (sipa-<epic>-NN) so
tools/project/kanban_project_sync.py auto-populates args/projects.yaml.

depends_on_task_id is single-parent (engine limitation, honored by the dispatcher:
a task dispatches only when its parent is 'done'/'decomposed'). Secondary cross-epic
prerequisites are named in the description text.

Run:
    python tools/kanban/seed_sipa_integrity.py
    python tools/kanban/seed_sipa_integrity.py --dry-run
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

_BASE = Path(__file__).resolve().parents[2]
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from tools.kanban.task_factory import create_tasks  # noqa: E402

PROJECT_ID = "sipa"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


TASKS = [
    # =========================================================================
    # EPIC db — constants + dual-schema DB (PG-first, RLS) + registration
    # =========================================================================
    {
        "id": "sipa-db-01",
        "title": "constants.py — capability/verdict/source/mode/severity enums + CHECK strings",
        "description": (
            "Create tools/integrity/__init__.py and tools/integrity/constants.py. Define the enums that "
            "drive every SQL CHECK constraint (never hardcode constraints): "
            "CAPABILITY_TYPES = (network_egress, filesystem, process_exec, dynamic_code, crypto, "
            "env_secret, serialization, obfuscation); SOURCE_TYPES = (local, git, unc, uri); "
            "ASSESS_MODES = (provenance_aware, provenance_blind, auto); "
            "VERDICTS = (allow, review, quarantine); ASSESS_STATUS = (quarantine, assessed, approved, "
            "rejected); FINDING_SCANNERS = (sast, secrets, deps, formal, semgrep, capability, "
            "reconciliation, tamper); FINDING_TYPES (e.g. dangerous_api, secret, vuln_dependency, "
            "unauthorized_capability, undisclosed_capability, tamper_mismatch, known_bad_signature); "
            "SEVERITY = (critical, high, medium, low, info). Add RISK_WEIGHTS (per capability_type + "
            "per severity) and FEATURE_FLAG = 'ICDEV_INTEGRITY_ENABLED'. Derive CHECK_* helper strings "
            "(e.g. CHECK_CAPABILITY_TYPES = \"','\".join(...)) for db/init_db.py to consume."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": None,
    },
    {
        "id": "sipa-db-02",
        "title": "db/init_db.py — PG-first dual schema, 5 RLS tables (tenant_id/classification, append-only)",
        "description": (
            "Create tools/integrity/db/init_db.py modeled on the PG-first dual-schema pattern "
            "(SCHEMA_PG + SCHEMA_SQLITE via .replace() transforms; backend defaults to 'postgresql', "
            "SQLite fallback). These are SENSITIVE findings tables, so use the RLS-aware "
            "tools.db.storage.get_connection() (NOT get_canvas_connection) and include tenant_id + "
            "classification columns on EVERY table. Tables: "
            "integrity_assessments(id, source_type CHECK, source_ref, mode CHECK, project_id, "
            "session_id, dir_digest, status CHECK, verdict CHECK, risk_score, tenant_id, classification, "
            "created_by, created_at, updated_at); "
            "integrity_capabilities(APPEND-ONLY: id, assessment_id, file_path, function_name, "
            "capability_type CHECK, evidence TEXT/JSON, line_start, line_end, risk_weight, tenant_id, "
            "classification, created_at); "
            "integrity_findings(APPEND-ONLY: id, assessment_id, source_scanner CHECK, finding_type, "
            "severity CHECK, file_path, line, detail JSON, tenant_id, classification, created_at); "
            "integrity_verdicts(APPEND-ONLY: id, assessment_id, verdict CHECK, risk_score, rationale "
            "JSON, decided_by, tenant_id, classification, created_at); "
            "integrity_authorizations(APPEND-ONLY HITL: id, assessment_id, capability_id, requirement_id, "
            "claim_ref, authorized BOOL, reason, reviewed_by, tenant_id, classification, created_at). "
            "ALL CHECK constraints derived from constants.py. init_db() idempotent + graceful if exists. "
            "Requires sipa-db-01."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "sipa-db-01",
    },
    {
        "id": "sipa-db-03",
        "title": "Register integrity_* tables in init_icdev_db.py + migration + conftest schema",
        "description": (
            "Wire the schema into the platform. (1) Add the 5 integrity_* CREATE TABLE statements to "
            "tools/db/init_icdev_db.py (both PG and SQLite paths, mirroring constants-derived CHECKs). "
            "(2) Add a numbered migration under tools/db/migrations/ (follow the existing NNN_*.py "
            "pattern) that creates the tables idempotently. (3) Add the 5 tables to "
            "tests/conftest.py MINIMAL_ICDEV_SCHEMA so SQLite-forced tests see them. Verify "
            "`python tools/db/init_icdev_db.py` runs clean and a fresh pytest collection imports. "
            "Requires sipa-db-02."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "sipa-db-02",
    },
    {
        "id": "sipa-db-04",
        "title": "APPEND_ONLY tables + .env toggle + args/integrity_config.yaml skeleton",
        "description": (
            "(1) Add the 4 append-only tables (integrity_capabilities, integrity_findings, "
            "integrity_verdicts, integrity_authorizations) to APPEND_ONLY_TABLES in "
            ".claude/hooks/pre_tool_use.py. (2) Add ICDEV_INTEGRITY_ENABLED=true to .env(.example). "
            "(3) Create args/integrity_config.yaml skeleton: scheme_allowlist (https github/gitlab, "
            "file://, unc), capability risk_weights (ref constants), verdict thresholds "
            "(review_at, quarantine_at), scanner toggles (sast/secrets/deps/formal/container/semgrep), "
            "llm_assist.enabled:false, quarantine_dir '.tmp/integrity_quarantine'. Requires sipa-db-03."
        ),
        "task_type": "chore",
        "priority": "high",
        "depends_on_task_id": "sipa-db-03",
    },

    # =========================================================================
    # EPIC ingest — quarantine-first staging (never executes target code)
    # =========================================================================
    {
        "id": "sipa-ingest-01",
        "title": "ingest.py — stage local/UNC/URI into quarantine + scheme allowlist + assessment row",
        "description": (
            "Create tools/integrity/ingest.py. stage(source, source_type=auto, project_id=None, "
            "session_id=None) -> {assessment_id, staged_path, source_type}. Copy local path / UNC / URI "
            "content into .tmp/integrity_quarantine/<assessment_id>/ (mirror the QUARANTINE_DIR pattern "
            "in tools/marketplace/openclaw_bridge.py). Enforce the scheme allowlist from "
            "args/integrity_config.yaml; reject anything else with a clear error. Insert an "
            "integrity_assessments row (status='quarantine', mode auto-detected: provenance_aware if "
            "project_id/session_id supplied else provenance_blind) via get_connection() stamping "
            "tenant_id/classification from the caller context. NEVER execute target code. Tests: local "
            "dir staged + row created; disallowed scheme rejected. Requires sipa-db-04."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "sipa-db-04",
    },
    {
        "id": "sipa-ingest-02",
        "title": "ingest.py — git clone (GitHub/GitLab) shallow, fixed-arg subprocess, no shell",
        "description": (
            "Extend ingest.py for source_type='git': shallow `git clone --depth 1 <url> <quarantine>` "
            "using subprocess with a FIXED ARG LIST and shell=False (NEVER shell=True, NEVER string "
            "interpolation into a shell). Validate the URL against the allowlist (https GitHub/GitLab) "
            "before cloning; strip credentials from logs. The cloned tree is data only and is NEVER "
            "executed. Add a regression test asserting no shell=True anywhere in tools/integrity/ and "
            "that a malformed/disallowed URL is rejected before any subprocess call. Tests use a tiny "
            "local bare repo fixture (file:// path) so no network is required. Requires sipa-ingest-01."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "sipa-ingest-01",
    },
    {
        "id": "sipa-ingest-03",
        "title": "ingest.py — SHA-256 directory tamper digest baseline via blueprint_verifier",
        "description": (
            "After staging, compute a SHA-256 recursive directory digest of the quarantined tree by "
            "reusing tools/security/blueprint_verifier.py (compute path digest) and store it in "
            "integrity_assessments.dir_digest. Expose a re-verify helper that recomputes the digest and "
            "flags a 'tamper_mismatch' finding if the staged tree changed between stage and assess. "
            "Tests: digest is stable for identical input, changes when a file is modified. "
            "Requires sipa-ingest-02."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "sipa-ingest-02",
    },

    # =========================================================================
    # EPIC scan — orchestrate existing scanners into normalized findings
    # =========================================================================
    {
        "id": "sipa-scan-01",
        "title": "scanners.py — SAST + secrets + dependency adapters -> normalized integrity_findings",
        "description": (
            "Create tools/integrity/scanners.py with thin adapters that SHELL OUT to the existing "
            "scanners (do not reimplement): tools/security/sast_runner.py (multi-language bandit/gosec/"
            "etc.), tools/security/secret_detector.py, tools/security/dependency_auditor.py — each run "
            "against the quarantined path. Normalize every result into the integrity_findings shape "
            "(source_scanner, finding_type, severity, file_path, line, detail) and persist (append-only) "
            "for the assessment. Map each scanner's severity into constants.SEVERITY. Tests against a "
            "fixture with a hardcoded secret + a known-bad dependency manifest. Requires sipa-ingest-03."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "sipa-ingest-03",
    },
    {
        "id": "sipa-scan-02",
        "title": "scanners.py — formal_verifier + conditional container_scanner adapters",
        "description": (
            "Add adapters for tools/analysis/formal_verifier.py (SQLi/dangerous-pattern/input-validation "
            "property checks) and tools/security/container_scanner.py (trivy) — the container scan runs "
            "ONLY when a Dockerfile/image is present in the quarantined tree. Normalize into "
            "integrity_findings. Respect the scanner toggles in args/integrity_config.yaml (a disabled "
            "scanner is skipped, not failed). Tests: formal findings recorded; container adapter no-ops "
            "cleanly when no Dockerfile. Requires sipa-scan-01."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "sipa-scan-01",
    },
    {
        "id": "sipa-scan-03",
        "title": "Malicious-signature Semgrep rules + pattern_classifier integration",
        "description": (
            "Author malicious-signature rules in context/integrity/semgrep_rules/*.yaml (e.g. "
            "reverse-shell/C2 callback, base64-decode-then-exec, credential exfil to external host, "
            "dynamic import of attacker-controlled string, suspicious cron/persistence). Wire them into "
            "scanners.py by reusing tools/aiify/pattern_classifier.py::detect_patterns (Semgrep CLI with "
            "AST fallback) pointed at the new rules dir. Emit 'known_bad_signature' integrity_findings "
            "with rule id + line. Tests: a planted reverse-shell fixture trips a signature; a benign file "
            "does not. Requires sipa-scan-02."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "sipa-scan-02",
    },

    # =========================================================================
    # EPIC cap — behavioral capability manifest (NEW core)
    # =========================================================================
    {
        "id": "sipa-cap-01",
        "title": "capability_extractor.py — Python AST: network / filesystem / process_exec",
        "description": (
            "Create tools/integrity/capability_extractor.py. extract(path) -> list of capability records "
            "{file_path, function_name, capability_type, evidence(json: host/path/literal), line_start, "
            "line_end, risk_weight}. Phase 1 detectors via Python `ast` (extend the AST-scan idea from "
            "openclaw_bridge.analyze_script_safety into a NORMALIZED MANIFEST, not a boolean): "
            "network_egress (socket, http.client, urllib, requests, httpx — capture target host/url "
            "literals), filesystem (open/Path read+write, shutil — capture path literals + mode), "
            "process_exec (subprocess.*, os.system/popen/exec*, multiprocessing). Persist append-only to "
            "integrity_capabilities. Tests with a benign fixture (no flags) and a fixture that opens a "
            "socket + writes a file + calls subprocess. Requires sipa-scan-03."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "sipa-scan-03",
    },
    {
        "id": "sipa-cap-02",
        "title": "capability_extractor.py — dynamic_code / crypto / env_secret / serialization / obfuscation",
        "description": (
            "Add the remaining Python detectors: dynamic_code (eval, exec, compile, __import__, "
            "types.FunctionType), crypto (hashlib weak algos, Crypto/cryptography, ssl context tweaks), "
            "env_secret (os.environ access, reads of .env/credentials/keys, keyring), serialization "
            "(pickle, marshal, shelve, yaml.load unsafe), obfuscation (base64/codecs/zlib/binascii decode "
            "feeding eval/exec, long hex/base64 string literals, char-code assembly). Weight each by "
            "constants.RISK_WEIGHTS. Tests: a planted backdoor fixture (base64-decode -> exec + socket) "
            "yields network_egress + dynamic_code + obfuscation capabilities. Requires sipa-cap-01."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "sipa-cap-01",
    },
    {
        "id": "sipa-cap-03",
        "title": "capability_extractor.py — multi-language breadth via Semgrep taxonomy mapping",
        "description": (
            "Add breadth coverage for non-Python (Java/Go/JS-TS/Rust/.NET): a Semgrep ruleset in "
            "context/integrity/semgrep_rules/capability_*.yaml whose findings map onto the SAME "
            "constants.CAPABILITY_TYPES taxonomy, so a Go net.Dial or a JS child_process.exec produces "
            "network_egress / process_exec capability records identical in shape to the Python path. "
            "Python stays the deepest (AST); other languages are signature-based. Tests: a Go fixture "
            "with net/http and a JS fixture with child_process each yield the expected capability types. "
            "Requires sipa-cap-02."
        ),
        "task_type": "build",
        "priority": "medium",
        "depends_on_task_id": "sipa-cap-02",
    },

    # =========================================================================
    # EPIC intent — reconciliation (NEW core): Mode B, then Mode A, then LLM-assist
    # =========================================================================
    {
        "id": "sipa-intent-01",
        "title": "claim_parser.py — parse README/docstrings/declared purpose -> claimed capability set",
        "description": (
            "Create tools/integrity/claim_parser.py. parse_claim(path, declared_purpose=None) -> "
            "{claimed_capabilities: set, sources: [...]}. Read README.*, module/package docstrings, and a "
            "caller-supplied declared_purpose string; map natural-language claims to constants."
            "CAPABILITY_TYPES with a deterministic keyword/rule lexicon (e.g. 'download/fetch/API/HTTP' "
            "-> network_egress; 'save/write/cache/log to file' -> filesystem; 'run/spawn/shell command' "
            "-> process_exec; 'encrypt/sign/hash' -> crypto). Absence of a claim for a capability is the "
            "signal Mode B uses. Tests: a README that says 'formats JSON' yields an EMPTY network/process "
            "claim set; one that says 'uploads results to the API' yields network_egress. "
            "Requires sipa-cap-02."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "sipa-cap-02",
    },
    {
        "id": "sipa-intent-02",
        "title": "intent_reconciler.py — Mode B: manifest vs claim -> undisclosed_capability + intrinsic risk",
        "description": (
            "Create tools/integrity/intent_reconciler.py. reconcile_blind(manifest, claim) -> findings: "
            "for each capability present in the code manifest but NOT implied by the parsed claim, emit an "
            "'undisclosed_capability' integrity_finding (severity from RISK_WEIGHTS; e.g. an undisclosed "
            "network_egress or process_exec is high/critical). Add an intrinsic-risk pass that flags "
            "dangerous capabilities regardless of claim (dynamic_code+obfuscation together => critical). "
            "This is the primary external-code path (no PRD). Tests: a 'JSON formatter' whose manifest "
            "has network_egress => undisclosed_capability(network_egress, high). Requires sipa-intent-01; "
            "secondary sipa-cap-02 (manifest)."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "sipa-intent-01",
    },

    # =========================================================================
    # EPIC score — unified risk score + verdict
    # =========================================================================
    {
        "id": "sipa-score-01",
        "title": "scoring.py — combine findings + capability + reconciliation + tamper -> verdict",
        "description": (
            "Create tools/integrity/scoring.py. score(assessment_id) -> {risk_score 0..100, verdict, "
            "rationale}. Aggregate: scanner finding severities + capability risk_weights + reconciliation "
            "findings (undisclosed/unauthorized capability) + tamper_mismatch, using weights/thresholds "
            "from args/integrity_config.yaml. Map score -> ALLOW (< review_at) / REVIEW (< quarantine_at) "
            "/ QUARANTINE (>= quarantine_at); any critical known_bad_signature or undisclosed process_exec "
            "forces QUARANTINE. Write an append-only integrity_verdicts row (decided_by='engine') with a "
            "JSON rationale enumerating the top contributors. Tests: benign => ALLOW; planted backdoor => "
            "QUARANTINE with rationale citing the capabilities. Requires sipa-intent-02."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "sipa-intent-02",
    },

    # =========================================================================
    # EPIC engine — orchestration + CLI + gate + HITL + security gate
    # =========================================================================
    {
        "id": "sipa-engine-01",
        "title": "engine.py — assess() orchestration wiring ingest->scan->cap->intent->score->persist",
        "description": (
            "Create tools/integrity/engine.py. assess(source, mode='auto', project_id=None, "
            "session_id=None) -> {assessment_id, verdict, risk_score, findings_count, capabilities_count, "
            "status}. Orchestrate: ingest.stage -> scanners.run_all -> capability_extractor.extract -> "
            "(Mode B) claim_parser + intent_reconciler.reconcile_blind -> scoring.score, persisting at "
            "each step. mode='auto' resolves to provenance_blind when no project_id/session_id is given. "
            "All DB access via get_connection() with tenant_id/classification. Tests: end-to-end on a "
            "benign fixture (ALLOW) and a planted-backdoor fixture (QUARANTINE), assertions on persisted "
            "rows. Requires sipa-score-01."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "sipa-score-01",
    },
    {
        "id": "sipa-engine-02",
        "title": "engine.py CLI — --source/--mode/--gate/--json + gate exit codes",
        "description": (
            "Add an argparse CLI / __main__ to engine.py: "
            "`python tools/integrity/engine.py --source <path|git-url|unc|uri> [--mode auto|blind|aware] "
            "[--project-id ...] [--session-id ...] [--gate] [--json]`. --json prints the assessment "
            "summary; --gate exits non-zero (e.g. 1) when verdict==QUARANTINE (and optionally on REVIEW "
            "per config), 0 otherwise — so it can wire into CI/pre-merge. Tests invoke main() and assert "
            "exit codes for benign vs backdoor fixtures. Requires sipa-engine-01."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "sipa-engine-01",
    },
    {
        "id": "sipa-engine-03",
        "title": "engine.py — HITL promote/reject + audit_logger + integrity_authorizations",
        "description": (
            "Add promote(assessment_id, reviewed_by, reason) and reject(assessment_id, reviewed_by, "
            "reason) mirroring tools/marketplace/openclaw_bridge.promote_import: transition "
            "integrity_assessments.status quarantine->approved|rejected, write an append-only "
            "integrity_authorizations row, and log to the append-only audit_trail via "
            "tools/audit/audit_logger (event_type integrity_promoted / integrity_rejected). Promotion is "
            "the ONLY way quarantined external code becomes 'approved'. Tests: promote flips status + "
            "writes audit + authorization rows; reject likewise. Requires sipa-engine-02."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "sipa-engine-02",
    },
    {
        "id": "sipa-engine-04",
        "title": "Integrity security gate + sandbox-coverage decision + no-shell/no-exec regression test",
        "description": (
            "(1) Add an 'Integrity Gate' to args/security_gates.yaml that blocks on verdict==QUARANTINE "
            "(warn on REVIEW), wired to engine --gate. (2) Add a docs/security/sandbox-coverage.md entry "
            "(OPT-58) for SIPA's git/UNC/URI fetch classified 'bypass-documented': fixed-arg git, no "
            "shell=True, target code never executed, scheme allowlist enforced. (3) Land the regression "
            "test enforcing that decision: assert no `shell=True` and no execution of fetched code "
            "anywhere under tools/integrity/. Requires sipa-engine-03."
        ),
        "task_type": "chore",
        "priority": "high",
        "depends_on_task_id": "sipa-engine-03",
    },

    # =========================================================================
    # EPIC intent (Phase 2) — Mode A (RTM authorization) + LLM-assist
    # =========================================================================
    {
        "id": "sipa-intent-03",
        "title": "intent_reconciler.py — Mode A: RTM-derived allowed capabilities -> unauthorized_capability",
        "description": (
            "Add reconcile_aware(manifest, project_id, session_id) for provenance-aware mode. Build the "
            "RTM via tools/requirements/traceability_builder.build_rtm and read intake_requirements text; "
            "derive an ALLOWED-capability set per the same keyword lexicon as claim_parser (a requirement "
            "that says 'notify by email' authorizes network_egress, etc.). Emit 'unauthorized_capability' "
            "findings for capabilities present in code with NO authorizing requirement (the semantic "
            "backdoor case), and surface requirements with no implementing code as coverage gaps (reuse "
            "review_traceability). Wire engine.assess to call this when mode=provenance_aware. Tests: a "
            "capability with no requirement => unauthorized_capability. Requires sipa-engine-04; secondary "
            "sipa-intent-02 (shared finding shape)."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "sipa-engine-04",
    },
    {
        "id": "sipa-intent-04",
        "title": "intent_reconciler.py — optional LLM-assist second opinion with deterministic fallback",
        "description": (
            "Add an OPTIONAL LLM-assisted reconciliation: when args/integrity_config.yaml llm_assist."
            "enabled AND a provider is available, call tools/llm/router.LLMRouter.invoke('intent_"
            "reconciliation', {claim_or_requirement_text, capability_manifest_summary}) to get a "
            "match/mismatch judgement + rationale, recorded as an advisory finding. The deterministic "
            "rule-based result ALWAYS computes first and is the fallback (air-gap safe; no LLM required). "
            "Add the function route to args/llm_config.yaml. Tests: with LLM disabled the deterministic "
            "path still produces a verdict (fallback). Requires sipa-intent-03."
        ),
        "task_type": "build",
        "priority": "medium",
        "depends_on_task_id": "sipa-intent-03",
    },

    # =========================================================================
    # EPIC prov — provenance linkage + authorizing-requirement edges
    # =========================================================================
    {
        "id": "sipa-prov-01",
        "title": "Provenance linkage — record assessment+verdict as PROV + citation registry edges",
        "description": (
            "Record each assessment in the provenance graph: use tools/observability/provenance/"
            "prov_recorder.ProvRecorder to write a prov_entity (entity_type='report', content_hash="
            "dir_digest) + a prov_activity ('integrity_assessment') + relations (verdict wasGeneratedBy "
            "assessment); register in tools/provenance/registry (source_citation_registry) with a trust "
            "score. In Mode A, attach edges linking authorized capabilities to their requirement_id so "
            "the chain code->capability->requirement is queryable. Tests: assessment produces a prov "
            "entity + citation row. Requires sipa-intent-04."
        ),
        "task_type": "build",
        "priority": "medium",
        "depends_on_task_id": "sipa-intent-04",
    },

    # =========================================================================
    # EPIC dash — dashboard canvas (8-component completeness gate)
    # =========================================================================
    {
        "id": "sipa-dash-01",
        "title": "blueprint.py — create_integrity_blueprint() factory: page routes + JSON API",
        "description": (
            "Create tools/integrity/blueprint.py with create_integrity_blueprint() (returns None if "
            "ICDEV_INTEGRITY_ENABLED is false), url_prefix='/integrity', before_request init_db. Page "
            "routes: / (assessment list) and /<assessment_id> (detail: capabilities + findings + verdict "
            "+ promote/reject controls). JSON API: POST /api/integrity/assess (body: source, mode, "
            "project_id?, session_id?), GET /api/integrity/assessments, GET /api/integrity/assessment/"
            "<id>, POST /api/integrity/assessment/<id>/promote, POST .../reject. Reads run under "
            "g.security_context (RLS); writes audit via engine. Import-safe with try/except like other "
            "canvases. Tests hit the routes with the Flask test client. Requires sipa-prov-01; secondary "
            "sipa-engine-03 (promote/reject)."
        ),
        "task_type": "build",
        "priority": "critical",
        "depends_on_task_id": "sipa-prov-01",
    },
    {
        "id": "sipa-dash-02",
        "title": "Templates — integrity/index.html + assessment.html (+ icdev/ mirror, IQE widget)",
        "description": (
            "Create tools/dashboard/templates/integrity/index.html (assessment list: source, mode, "
            "verdict badge ALLOW/REVIEW/QUARANTINE, risk score, date; an 'Assess' form taking a "
            "source ref + mode) and assessment.html (detail: capability manifest table with evidence, "
            "findings grouped by scanner/severity, verdict + rationale, promote/reject buttons for "
            "quarantined items). Extend base.html, CUI//SP-CTI banner, dark mode consistent with other "
            "canvases. Include {% include 'includes/iqe_query_widget.html' %} and pass iqe_canvas="
            "'integrity'. Mirror both templates to icdev/tools/dashboard/templates/integrity/ (or rely "
            "on companion sync). Requires sipa-dash-01."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "sipa-dash-01",
    },
    {
        "id": "sipa-dash-03",
        "title": "Register blueprint in app.py + nav link (base.html) + start.md Pages line",
        "description": (
            "(1) Register the blueprint in tools/dashboard/app.py (_CANVAS_DEFS entry "
            "'integrity'/'ICDEV_INTEGRITY_ENABLED'/'tools.integrity.blueprint'/'create_integrity_"
            "blueprint' and add 'integrity' to _CANVAS_DEFAULTS_TRUE). (2) Add a nav link under the "
            "Compliance/Security menu in base.html pointing to /integrity. (3) Add /integrity and "
            "/integrity/<id> to the Pages: line in .claude/commands/start.md. Verify the dashboard starts "
            "with no ImportError and /integrity renders. Requires sipa-dash-02."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "sipa-dash-02",
    },
    {
        "id": "sipa-dash-04",
        "title": "IQE — adapters/integrity.py + _CANVAS_MAP + PATH_CANVAS + /api/iqe-query + seed queries",
        "description": (
            "Full IQE integration (8th completeness component): create tools/iqe/adapters/integrity.py "
            "registering read-only collections returning REAL rows (integrity.assessments, "
            "integrity.capabilities, integrity.findings, integrity.verdicts) — mirror "
            "tools/iqe/adapters/ai_augmentation.py. Add the 'integrity' entry to _CANVAS_MAP in "
            "tools/dashboard/app.py and a PATH_CANVAS entry [/^\\/integrity/, 'integrity'] in base.html. "
            "Add POST /integrity/api/iqe-query to the blueprint. Add >=3 seed queries in "
            "context/iqe/queries/integrity/*.iqe (e.g. quarantined assessments, undisclosed-capability "
            "findings, highest-risk assessments). Requires sipa-dash-03."
        ),
        "task_type": "build",
        "priority": "high",
        "depends_on_task_id": "sipa-dash-03",
    },

    # =========================================================================
    # EPIC mcp — MCP gateway registration
    # =========================================================================
    {
        "id": "sipa-mcp-01",
        "title": "MCP — register integrity_assess + integrity_list tools in the gateway",
        "description": (
            "Register SIPA in the MCP gateway: add integrity_assess(source, mode) and "
            "integrity_list_assessments() to tools/mcp/tool_registry.py and the corresponding handlers in "
            "tools/mcp/gap_handlers.py, reusing the security/knowledge MCP server pattern (deterministic, "
            "JSON in/out, no shell). Tests: registry lists the tools; handler returns a JSON assessment "
            "for a fixture path. Requires sipa-dash-04."
        ),
        "task_type": "build",
        "priority": "medium",
        "depends_on_task_id": "sipa-dash-04",
    },

    # =========================================================================
    # EPIC reflex — Genesis self-assessment drift reflex
    # =========================================================================
    {
        "id": "sipa-reflex-01",
        "title": "integrity_monitor.py — Genesis reflex: self-assess ICDEV tools/ for new unauthorized capabilities",
        "description": (
            "Create tools/genesis/reflexes/integrity_monitor.py following an existing reflex "
            "(e.g. tools/genesis/reflexes/ndc_topology_drift.py: CADENCE_HOURS, run(ctx, conn) -> "
            "{scanned, flagged, status}). Periodically run engine.assess(mode='aware') over ICDEV's own "
            "tools/ against the platform RTM, and when a NEW high-risk or unauthorized_capability appears "
            "vs the last baseline, open a kanban remediation card (or oracle_prediction) — reuse the "
            "drift_detector dedupe idea so it doesn't re-alert. Register the reflex in REFLEX_NAMES in "
            "tools/genesis/daemon.py AND the reflex registry AND genesis config (all three, per the "
            "daemon-registration gotcha). Use 127.0.0.1 not localhost for any probe. Tests: a seeded "
            "unauthorized capability produces exactly one card. Requires sipa-mcp-01."
        ),
        "task_type": "build",
        "priority": "medium",
        "depends_on_task_id": "sipa-mcp-01",
    },

    # =========================================================================
    # EPIC doc — goal workflow + manifest shard + commands reference
    # =========================================================================
    {
        "id": "sipa-doc-01",
        "title": "goals/software_integrity.md + manifest shard + commands.md registration",
        "description": (
            "(1) Create goals/software_integrity.md describing the two-mode orchestration (when to use "
            "Mode A vs B, the quarantine+HITL flow) and add a row to goals/manifest.md. (2) Create the "
            "tools/manifest/software-integrity.md shard documenting every tools/integrity/ module + the "
            "Genesis reflex + IQE adapter, and add its index line to tools/manifest.md. (3) Add the SIPA "
            "CLI commands to docs/reference/commands.md. Requires sipa-reflex-01."
        ),
        "task_type": "chore",
        "priority": "medium",
        "depends_on_task_id": "sipa-reflex-01",
    },

    # =========================================================================
    # EPIC vv — CodeLens + Coherence + sync/health + Playwright E2E
    # =========================================================================
    {
        "id": "sipa-vv-01",
        "title": "Comprehensive CodeLens — every tools/integrity/ module PASSes the quality gate",
        "description": (
            "Run tools/analysis/code_lens.py --file <m> --json on EVERY module under tools/integrity/ "
            "(engine, ingest, scanners, capability_extractor, claim_parser, intent_reconciler, scoring, "
            "blueprint, constants, db/init_db) and on tools/iqe/adapters/integrity.py and "
            "tools/genesis/reflexes/integrity_monitor.py. Refactor any module that does not PASS "
            "(maintainability >= 0.6, zero critical smells, no high cyclomatic complexity) — split long/"
            "complex functions, reduce nesting — without changing behavior (tests stay green). Record the "
            "before/after gate results in the task completion notes. Requires sipa-doc-01."
        ),
        "task_type": "test",
        "priority": "high",
        "depends_on_task_id": "sipa-doc-01",
    },
    {
        "id": "sipa-vv-02",
        "title": "Comprehensive Coherence — coherence_checker --all --fix --gate green + ruff + pytest",
        "description": (
            "Run the full coherence gate and make it green for SIPA: `python tools/workflow/"
            "coherence_checker.py --all --fix --gate`. Resolve every check touching SIPA — schema<->code "
            "parity (integrity_* INSERTs match CREATE TABLE), manifest (new modules documented), "
            "append-only (4 integrity_* tables registered), route uniqueness + nav/route parity "
            "(/integrity), sandbox coverage (the OPT-58 entry exists), security_context (RLS auto-wiring "
            "intact, no undocumented bypass), IQE wiring. Then `ruff check tools/integrity/` clean and "
            "`pytest tests/test_integrity_*.py -v` green (run api_surface_extractor before writing/fixing "
            "tests). Requires sipa-vv-01."
        ),
        "task_type": "test",
        "priority": "critical",
        "depends_on_task_id": "sipa-vv-01",
    },
    {
        "id": "sipa-vv-03",
        "title": "Companion sync (foreground) + health check + full suite green",
        "description": (
            "Run `python tools/dx/companion.py --sync --write --json` IN THE FOREGROUND (never background "
            "— background sync has deleted uncommitted work) so the integrity templates/skills propagate "
            "to all AI platforms. Run `python tools/testing/health_check.py --json` (env + DB + deps "
            "green). Run the full `pytest tests/ -q` to confirm no regressions from the integrity_* schema "
            "additions. On Windows set $env:PYTHONPATH='C:\\AI\\ICDev'. Requires sipa-vv-02."
        ),
        "task_type": "test",
        "priority": "high",
        "depends_on_task_id": "sipa-vv-02",
    },
    {
        "id": "sipa-vv-04",
        "title": "Playwright E2E — assess backdoor->QUARANTINE, benign->ALLOW, IQE widget + screenshots",
        "description": (
            "End-to-end via Playwright MCP against the running dashboard: (1) open /integrity; (2) submit "
            "an assess for a benign local fixture -> verdict ALLOW; (3) submit a planted-backdoor fixture "
            "(socket + base64-decode-then-exec) -> verdict QUARANTINE, and the detail page lists the "
            "network_egress + dynamic_code + obfuscation capabilities and the undisclosed/known-bad "
            "findings; (4) exercise the IQE widget with a seed query ('show quarantined assessments') and "
            "confirm real rows; (5) screenshots to playwright/screenshots/integrity-*.png. Write the "
            "feature doc docs/features/phase-sipa-software-integrity-results.md summarizing verified "
            "behavior + any LLM fallbacks. All gates green. Requires sipa-vv-03."
        ),
        "task_type": "test",
        "priority": "critical",
        "depends_on_task_id": "sipa-vv-03",
    },
]


def seed(dry_run: bool = False) -> int:
    now = _now()
    # All SIPA tasks are time-deferred: status='scheduled' WITH a real
    # scheduled_at timestamp, project_id='sipa', classification='CUI'.
    specs = [
        {
            **t,
            "status": "scheduled",
            "scheduled_at": now,
            "project_id": PROJECT_ID,
            "classification": "CUI",
        }
        for t in TASKS
    ]
    report = create_tasks(
        PROJECT_ID,
        specs,
        dry_run=dry_run,
        strict=False,
        register_project=False,
    )
    inserted = len(report.seeded) if not dry_run else (len(TASKS) - len(report.skipped))
    skipped = len(report.skipped)
    verb = "Would seed" if dry_run else "Seeded"
    print(f"{verb} {inserted} tasks, skipped {skipped} existing (total defined: {len(TASKS)}).")
    return inserted


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Seed SIPA (software integrity) kanban tasks")
    ap.add_argument("--dry-run", action="store_true", help="Print only, no DB write")
    args = ap.parse_args()
    seed(dry_run=args.dry_run)
