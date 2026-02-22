#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""Solution Spec Generator — auto-generate solution specs from triaged innovation signals.

Takes triaged+approved signals and generates structured solution specifications that
feed into ICDEV's existing ATLAS build pipeline. Specs are deterministic (template-based,
not LLM) with GOTCHA layer mapping, BDD acceptance criteria, compliance impact, test
plans, and marketplace asset type classification.

Architecture:
    - Template-based generation (no LLM, air-gap safe)
    - Maps signal categories to GOTCHA layers (goal/tool/arg/context/hardprompt)
    - Optional spec quality checking via spec_quality_checker.py (D156)
    - Results stored in innovation_solutions table (append-only, D6)

Usage:
    python tools/innovation/solution_generator.py --generate --signal-id "sig-xxx" --json
    python tools/innovation/solution_generator.py --generate-all --json
    python tools/innovation/solution_generator.py --status --solution-id "sol-xxx" --json
    python tools/innovation/solution_generator.py --list --status generated --limit 20 --json
"""

import argparse
import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# =========================================================================
# PATH SETUP
# =========================================================================
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))
CONFIG_PATH = BASE_DIR / "args" / "innovation_config.yaml"

# =========================================================================
# GRACEFUL IMPORTS
# =========================================================================
try:
    import yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

try:
    from tools.audit.audit_logger import log_event as audit_log_event
    _HAS_AUDIT = True
except ImportError:
    _HAS_AUDIT = False
    def audit_log_event(**kwargs):
        return -1

try:
    import jinja2  # noqa: F401
    _HAS_JINJA2 = True
except ImportError:
    _HAS_JINJA2 = False

try:
    from tools.requirements.spec_quality_checker import check_spec_quality
    _HAS_SPEC_CHECKER = True
except (ImportError, AttributeError):
    _HAS_SPEC_CHECKER = False

# =========================================================================
# CONSTANTS & LAYER DEFINITIONS
# =========================================================================
VALID_STATUSES = ("generated", "building", "built", "published", "failed")
EFFORT_MAP = {
    "S": {"label": "Small", "story_points": "1-3", "days": "1-2"},
    "M": {"label": "Medium", "story_points": "5-8", "days": "3-5"},
    "L": {"label": "Large", "story_points": "8-13", "days": "5-10"},
    "XL": {"label": "Extra-Large", "story_points": "13-21", "days": "10-20"},
}

# GOTCHA layer info: description, proposed solution template, asset type, base effort, test templates
GOTCHA_LAYER_INFO = {
    "goal": {
        "description": "Process definitions -- what to achieve, which tools to use, expected outputs",
        "proposed_template": ("Create a new goal workflow in `goals/` that defines:\n"
            "1. Objective and success criteria\n2. Required tools and invocation order\n"
            "3. Expected outputs and validation rules\n4. Error handling and edge cases\n"
            "5. Integration with existing ATLAS/M-ATLAS phases"),
        "asset_type": "goal", "effort": "M",
        "test_unit": "Validate goal YAML/markdown structure, required sections present",
        "test_bdd": ("Feature: {title} workflow\n  Scenario: Execute {title} end-to-end\n"
            "    Given the goal file exists in goals/\n    And all referenced tools are available\n"
            "    When the orchestrator executes the goal\n    Then all expected outputs are generated\n"
            "    And no security gates are violated"),
    },
    "tool": {
        "description": "Python scripts, one job each -- deterministic, don't think, just execute",
        "proposed_template": ("Create a new deterministic Python script in `tools/` that:\n"
            "1. Accepts CLI args via argparse with `--json` flag\n"
            "2. Follows CUI header and PATH SETUP patterns\n"
            "3. Uses `_get_db()`, `_now()`, `_audit()` helper pattern\n"
            "4. Returns structured JSON output\n5. Graceful imports for optional deps\n"
            "6. Add entry to `tools/manifest.md`"),
        "asset_type": "tool", "effort": "L",
        "test_unit": ("pytest unit tests: happy path, error handling, DB ops, JSON validation, "
            "graceful degradation when optional deps missing"),
        "test_bdd": ("Feature: {title} tool\n  Scenario: Generate output with valid input\n"
            "    Given a valid input is provided\n    When the tool is executed with --json\n"
            "    Then the output is valid JSON\n    And the audit trail is updated\n"
            "  Scenario: Handle missing database gracefully\n"
            "    Given the database does not exist\n    When the tool is executed\n"
            "    Then a clear error message is returned"),
    },
    "arg": {
        "description": "YAML/JSON behavior settings -- change behavior without editing goals/tools",
        "proposed_template": ("Create a new YAML configuration file in `args/` that:\n"
            "1. Defines configurable parameters with sensible defaults\n"
            "2. Inline comments explaining each setting\n3. CUI header marking\n"
            "4. Follows existing args/ naming convention\n"
            "5. Documents env var overrides (ICDEV_ prefix, D193)"),
        "asset_type": "arg", "effort": "S",
        "test_unit": "pytest: YAML parsing, required keys present, defaults sensible, env var override (D193)",
        "test_bdd": ("Feature: {title} configuration\n"
            "  Scenario: Load configuration with defaults\n"
            "    Given no environment overrides are set\n"
            "    When the configuration is loaded\n    Then all default values are populated\n"
            "  Scenario: Environment variable override\n"
            "    Given ICDEV_<KEY> is set in environment\n"
            "    When the configuration is loaded\n    Then the env value takes precedence over YAML"),
    },
    "context": {
        "description": "Static reference material -- tone rules, samples, standards, guidelines",
        "proposed_template": ("Create a new context reference file in `context/` that:\n"
            "1. Structured reference data (JSON or markdown)\n"
            "2. Readable by goals and tools at runtime\n3. Provenance metadata (source, version)\n"
            "4. CUI markings appropriate to content\n5. Self-contained (no external deps)"),
        "asset_type": "context", "effort": "S",
        "test_unit": "pytest: file loads, required fields present, no broken refs, UTF-8 encoding",
        "test_bdd": ("Feature: {title} reference data\n"
            "  Scenario: Load context at runtime\n"
            "    Given the context file exists in context/\n    When a tool reads the context\n"
            "    Then all entries are valid and accessible"),
    },
    "hardprompt": {
        "description": "Reusable LLM instruction templates -- outline, rewrite, summarize",
        "proposed_template": ("Create a new hardprompt template in `hardprompts/` that:\n"
            "1. Reusable LLM instruction with clear input/output contract\n"
            "2. Placeholder tokens for variable substitution\n"
            "3. Expected response format (JSON, markdown, etc.)\n"
            "4. Works with multiple LLM providers (Bedrock, Anthropic, OpenAI-compat)\n"
            "5. CUI handling instructions when applicable"),
        "asset_type": "hardprompt", "effort": "S",
        "test_unit": "pytest: template renders, placeholders substituted, format instructions clear",
        "test_bdd": ("Feature: {title} prompt template\n"
            "  Scenario: Render template with valid inputs\n"
            "    Given valid substitution values\n    When the template is rendered\n"
            "    Then the output contains all expected sections"),
    },
}

CATEGORY_LAYER_MAP = {
    "security_vulnerability": "tool", "compliance_gap": "goal",
    "developer_experience": "tool", "performance": "tool",
    "modernization": "goal", "supply_chain": "tool",
    "infrastructure": "arg", "testing": "tool", "ai_tooling": "hardprompt",
}

CATEGORY_FRAMEWORKS = {
    "security_vulnerability": ["NIST 800-53 (SI, RA)", "FedRAMP", "CMMC"],
    "compliance_gap": ["NIST 800-53", "FedRAMP", "CMMC", "CJIS", "HIPAA"],
    "supply_chain": ["NIST 800-161", "CMMC (SC)", "FedRAMP (SA)"],
    "modernization": ["FedRAMP (CM)", "CMMC (CM)", "NIST 800-53 (CM)"],
    "infrastructure": ["NIST 800-53 (SC, AC)", "FedRAMP", "STIG"],
    "testing": ["NIST 800-53 (SA)", "IEEE 1012 IV&V", "DoDI 5000.87 DES"],
    "performance": ["NIST 800-53 (SC-5, SI-2)"],
    "ai_tooling": ["NIST 800-53 (SA-15)", "FedRAMP"],
    "developer_experience": [],
}

BOUNDARY_NOTES = {
    "RED": "ATO-invalidating change. Full SSP revision required. Alternative COAs must be generated.",
    "ORANGE": "Significant boundary change. SSP addendum and ISSO review required.",
    "YELLOW": "Minor boundary adjustment. Possible POAM entry.",
    "GREEN": "No boundary change anticipated. Standard compliance review.",
}

# =========================================================================
# DATABASE HELPERS
# =========================================================================
def _get_db(db_path=None):
    """Get database connection with dict-like row access."""
    path = db_path or DB_PATH
    if not path.exists():
        raise FileNotFoundError(f"Database not found: {path}\nRun: python tools/db/init_icdev_db.py")
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _now():
    """ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _audit(event_type, actor, action, details=None, project_id=None):
    """Write audit trail entry (append-only, D6)."""
    if _HAS_AUDIT:
        try:
            audit_log_event(event_type=event_type, actor=actor, action=action,
                            details=json.dumps(details) if details else None,
                            project_id=project_id or "innovation-engine")
        except Exception:
            pass


def _load_config():
    """Load innovation config from YAML."""
    if not _HAS_YAML or not CONFIG_PATH.exists():
        return {}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _ensure_solutions_table(conn):
    """Create innovation_solutions table if it does not exist (D6 append-only)."""
    conn.execute("""CREATE TABLE IF NOT EXISTS innovation_solutions (
        id TEXT PRIMARY KEY, signal_id TEXT NOT NULL, spec_content TEXT NOT NULL,
        gotcha_layer TEXT NOT NULL, asset_type TEXT NOT NULL, estimated_effort TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'generated', spec_quality_score REAL,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        FOREIGN KEY (signal_id) REFERENCES innovation_signals(id))""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_solutions_signal ON innovation_solutions(signal_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_solutions_status ON innovation_solutions(status)")
    conn.commit()


# =========================================================================
# RESOLUTION HELPERS
# =========================================================================
def _resolve_gotcha_layer(signal):
    """Determine GOTCHA layer: explicit column > category map > keyword match > default 'tool'."""
    explicit = signal.get("gotcha_layer")
    if explicit and explicit in GOTCHA_LAYER_INFO:
        return explicit
    category = signal.get("category") or ""
    if category in CATEGORY_LAYER_MAP:
        return CATEGORY_LAYER_MAP[category]
    config = _load_config()
    layer_mapping = config.get("triage", {}).get("gotcha_fit", {}).get("layer_mapping", {})
    text = (signal.get("title", "") + " " + signal.get("description", "")).lower()
    for layer, keywords in layer_mapping.items():
        if layer in GOTCHA_LAYER_INFO and any(kw.lower() in text for kw in keywords):
            return layer
    return "tool"


def _estimate_effort(gotcha_layer, signal):
    """Estimate effort (S/M/L/XL) from layer base + community score + boundary tier."""
    base = GOTCHA_LAYER_INFO.get(gotcha_layer, {}).get("effort", "M")
    order = ["S", "M", "L", "XL"]
    idx = order.index(base)
    if (signal.get("community_score") or 0.0) > 0.8:
        idx = min(idx + 1, 3)
    if (signal.get("boundary_tier") or "GREEN") in ("ORANGE", "RED"):
        idx = min(idx + 1, 3)
    return order[idx]


def _build_acceptance_criteria(gotcha_layer, signal):
    """Generate BDD-style Given/When/Then acceptance criteria."""
    title = signal.get("title", "the feature")
    category = signal.get("category") or ""
    layer_criteria = {
        "tool": [
            f"- [ ] Given valid input, When `{title}` tool is invoked with `--json`, Then structured JSON is returned",
            "- [ ] Given the tool is in `tools/manifest.md`, When checked, Then the entry exists",
            "- [ ] Given DB unavailable, When invoked, Then a clear error without crash",
            "- [ ] Given valid inputs, When completed, Then audit trail entry is written",
        ],
        "goal": [
            "- [ ] Given goal file in `goals/`, When orchestrator reads it, Then required sections present",
            "- [ ] Given all referenced tools exist, When executed end-to-end, Then outputs produced",
            "- [ ] Given a tool fails mid-workflow, When error handler runs, Then intermediates preserved",
            "- [ ] Given goal in `goals/manifest.md`, When checked, Then entry exists",
        ],
        "arg": [
            "- [ ] Given YAML in `args/`, When loaded, Then all values parse correctly",
            "- [ ] Given no env overrides, When defaults used, Then behavior correct",
            "- [ ] Given `ICDEV_<KEY>` env var set, When loaded, Then env overrides YAML (D193)",
        ],
        "context": [
            "- [ ] Given context file in `context/`, When a tool reads it, Then entries valid",
            "- [ ] Given context referenced by goal, When goal executes, Then data accessible",
            "- [ ] Given UTF-8 content, When loaded, Then no encoding errors",
        ],
        "hardprompt": [
            "- [ ] Given valid substitution values, When rendered, Then all placeholders replaced",
            "- [ ] Given template sent to LLM, When response received, Then matches expected format",
            "- [ ] Given CUI content, When prompt includes CUI instructions, Then markings preserved",
        ],
    }
    criteria = list(layer_criteria.get(gotcha_layer, layer_criteria["tool"]))
    if category == "security_vulnerability":
        criteria.append("- [ ] Given SAST scanning, When solution code scanned, Then 0 critical/high findings")
    elif category == "compliance_gap":
        criteria.append("- [ ] Given crosswalk engine queried, When controls implemented, Then FedRAMP/CMMC auto-populated")
    elif category == "supply_chain":
        criteria.append("- [ ] Given SBOM regenerated, When solution built, Then new deps tracked")
    criteria.append("- [ ] Given CUI markings required, When files generated, Then appropriate markings present")
    return "\n".join(criteria)


# =========================================================================
# SPEC TEMPLATE & GENERATION
# =========================================================================
_SPEC_TEMPLATE = """\
# Solution: {title}

CUI // SP-CTI

## Problem Statement
{description}

## Source
- URL: {url}
- Community Score: {community_score:.2f}
- Innovation Score: {innovation_score:.2f}
- Category: {category}

## GOTCHA Layer
**{gotcha_layer}** -- {layer_description}

## Proposed Solution
{proposed_solution}

## Acceptance Criteria
{acceptance_criteria}

## Compliance Impact
- Boundary Tier: {boundary_tier}
- Frameworks Affected: {frameworks}
- Notes: {compliance_notes}

## Test Plan
### Unit Tests
{test_unit}

### BDD Scenarios
```gherkin
{test_bdd}
```

## Marketplace
- Asset Type: {asset_type}
- Estimated Effort: {effort} ({effort_label}, ~{effort_days} days, {effort_points} story points)
"""


def generate_solution_spec(signal_id, db_path=None):
    """Generate a full solution specification from an approved signal.

    Reads signal from DB, resolves GOTCHA layer, generates structured spec with
    acceptance criteria, compliance impact, test plan, marketplace classification.
    Stores result in innovation_solutions table.

    Args:
        signal_id: The signal identifier (sig-xxx).
        db_path: Optional database path override.

    Returns:
        dict with solution_id, spec_content, status, and metadata.
    """
    conn = _get_db(db_path)
    _ensure_solutions_table(conn)
    try:
        signal = conn.execute("SELECT * FROM innovation_signals WHERE id = ?",
                              (signal_id,)).fetchone()
        if not signal:
            return {"error": f"Signal not found: {signal_id}"}
        signal = dict(signal)

        if signal.get("status") != "approved":
            return {"error": f"Signal {signal_id} not approved (status: {signal.get('status')})",
                    "signal_status": signal.get("status")}

        existing = conn.execute("SELECT id, status FROM innovation_solutions WHERE signal_id = ?",
                                (signal_id,)).fetchone()
        if existing:
            return {"error": f"Solution already exists for signal {signal_id}",
                    "solution_id": existing["id"], "solution_status": existing["status"]}

        gotcha_layer = _resolve_gotcha_layer(signal)
        layer_info = GOTCHA_LAYER_INFO.get(gotcha_layer, GOTCHA_LAYER_INFO["tool"])
        effort = _estimate_effort(gotcha_layer, signal)
        effort_details = EFFORT_MAP.get(effort, EFFORT_MAP["M"])
        boundary_tier = signal.get("boundary_tier") or "GREEN"
        category = signal.get("category") or "uncategorized"
        frameworks = CATEGORY_FRAMEWORKS.get(category, [])
        metadata = {}
        try:
            metadata = json.loads(signal.get("metadata", "{}") or "{}")
        except (json.JSONDecodeError, TypeError):
            pass
        compliance_notes = metadata.get("compliance_notes") or BOUNDARY_NOTES.get(boundary_tier, BOUNDARY_NOTES["GREEN"])
        innovation_score = signal.get("innovation_score") if signal.get("innovation_score") is not None else (signal.get("community_score") or 0.0)
        asset_type = layer_info.get("asset_type", "tool")

        spec_content = _SPEC_TEMPLATE.format(
            title=signal.get("title", "Untitled"),
            description=signal.get("description", "No description provided."),
            url=signal.get("url", "N/A"),
            community_score=float(signal.get("community_score") or 0.0),
            innovation_score=float(innovation_score),
            category=category, gotcha_layer=gotcha_layer,
            layer_description=layer_info.get("description", ""),
            proposed_solution=layer_info.get("proposed_template", ""),
            acceptance_criteria=_build_acceptance_criteria(gotcha_layer, signal),
            boundary_tier=boundary_tier,
            frameworks=", ".join(frameworks) if frameworks else "None identified",
            compliance_notes=compliance_notes,
            test_unit=layer_info.get("test_unit", "Standard unit tests required."),
            test_bdd=layer_info.get("test_bdd", "").format(title=signal.get("title", "Feature")),
            asset_type=asset_type, effort=effort,
            effort_label=effort_details["label"],
            effort_days=effort_details["days"], effort_points=effort_details["story_points"])

        spec_quality_score = None
        if _HAS_SPEC_CHECKER:
            try:
                spec_quality_score = check_spec_quality(spec_content).get("score")
            except Exception:
                pass

        sol_id = f"sol-{uuid.uuid4().hex[:12]}"
        now = _now()
        conn.execute("""INSERT INTO innovation_solutions
            (id, signal_id, spec_content, gotcha_layer, asset_type, estimated_effort,
             status, spec_quality_score, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'generated', ?, ?, ?)""",
            (sol_id, signal_id, spec_content, gotcha_layer, asset_type, effort,
             spec_quality_score, now, now))
        conn.commit()

        _audit("innovation.solution_generated", "innovation-agent",
               f"Generated solution {sol_id} from signal {signal_id}",
               {"solution_id": sol_id, "signal_id": signal_id, "gotcha_layer": gotcha_layer,
                "asset_type": asset_type, "effort": effort, "boundary_tier": boundary_tier})

        # Phase 36 integration: propagation metadata for capability evaluator
        propagation_metadata = {
            "target_child_ids": [],  # Populated during propagation planning
            "security_assessment": {
                "injection_scanned": False,
                "trust_level": "system",
                "boundary_tier": boundary_tier,
            },
            "compliance_frameworks": frameworks,
            "source_signal_id": signal_id,
            "gotcha_layer": gotcha_layer,
        }

        return {"solution_id": sol_id, "signal_id": signal_id, "gotcha_layer": gotcha_layer,
                "asset_type": asset_type, "estimated_effort": effort, "effort_details": effort_details,
                "boundary_tier": boundary_tier, "frameworks_affected": frameworks,
                "spec_quality_score": spec_quality_score, "status": "generated",
                "spec_content": spec_content, "created_at": now,
                "propagation_metadata": propagation_metadata}
    finally:
        conn.close()


def generate_all_approved(db_path=None):
    """Generate specs for all approved signals without existing solutions.

    Respects max_auto_solutions_per_pi budget from innovation_config.yaml.

    Args:
        db_path: Optional database path override.

    Returns:
        dict with generated count, errors, and per-solution results.
    """
    config = _load_config()
    max_solutions = config.get("scoring", {}).get("max_auto_solutions_per_pi", 10)
    conn = _get_db(db_path)
    _ensure_solutions_table(conn)
    try:
        signals = [dict(r) for r in conn.execute("""
            SELECT s.* FROM innovation_signals s
            LEFT JOIN innovation_solutions sol ON s.id = sol.signal_id
            WHERE s.status = 'approved' AND sol.id IS NULL
            ORDER BY s.community_score DESC""").fetchall()]
    finally:
        conn.close()

    if not signals:
        return {"generated": 0, "errors": 0, "skipped": 0,
                "message": "No approved signals pending solution generation.", "results": []}

    to_process = signals[:max_solutions]
    skipped = max(0, len(signals) - max_solutions)
    results, generated, errors = [], 0, 0
    for sig in to_process:
        result = generate_solution_spec(sig["id"], db_path)
        if "error" in result:
            errors += 1
        else:
            generated += 1
        results.append({"signal_id": sig["id"], "signal_title": sig.get("title", ""),
                         "result": result.get("solution_id") or result.get("error"),
                         "status": "generated" if "solution_id" in result else "error"})

    _audit("innovation.batch_generation", "innovation-agent",
           f"Batch: {generated} generated, {errors} errors, {skipped} skipped",
           {"generated": generated, "errors": errors, "skipped": skipped, "budget": max_solutions})
    return {"generated": generated, "errors": errors, "skipped": skipped,
            "budget": max_solutions, "total_approved": len(signals), "results": results}


def get_solution_status(solution_id=None, db_path=None):
    """Get detailed status of a specific solution.

    Args:
        solution_id: The solution identifier (sol-xxx).
        db_path: Optional database path override.

    Returns:
        dict with solution details or error.
    """
    if not solution_id:
        return {"error": "solution_id is required"}
    conn = _get_db(db_path)
    _ensure_solutions_table(conn)
    try:
        row = conn.execute("SELECT * FROM innovation_solutions WHERE id = ?",
                           (solution_id,)).fetchone()
        if not row:
            return {"error": f"Solution not found: {solution_id}"}
        sol = dict(row)
        sig_row = conn.execute("SELECT title, source, category, community_score, url "
                               "FROM innovation_signals WHERE id = ?",
                               (sol["signal_id"],)).fetchone()
        sig_summary = dict(sig_row) if sig_row else {}
        return {"solution_id": sol["id"], "signal_id": sol["signal_id"],
                "signal_summary": sig_summary, "gotcha_layer": sol["gotcha_layer"],
                "asset_type": sol["asset_type"], "estimated_effort": sol["estimated_effort"],
                "effort_details": EFFORT_MAP.get(sol["estimated_effort"], {}),
                "status": sol["status"], "spec_quality_score": sol["spec_quality_score"],
                "spec_content": sol["spec_content"],
                "created_at": sol["created_at"], "updated_at": sol["updated_at"]}
    finally:
        conn.close()


def list_solutions(status=None, limit=20, db_path=None):
    """List generated solutions with optional status filter.

    Args:
        status: Filter by status (generated/building/built/published/failed).
        limit: Maximum number of results (default 20).
        db_path: Optional database path override.

    Returns:
        dict with solutions list and counts.
    """
    conn = _get_db(db_path)
    _ensure_solutions_table(conn)
    try:
        query = ("SELECT sol.id, sol.signal_id, sol.gotcha_layer, sol.asset_type, "
                 "sol.estimated_effort, sol.status, sol.spec_quality_score, "
                 "sol.created_at, sol.updated_at, s.title as signal_title, "
                 "s.source as signal_source, s.category as signal_category "
                 "FROM innovation_solutions sol "
                 "LEFT JOIN innovation_signals s ON sol.signal_id = s.id")
        params = []
        if status:
            if status not in VALID_STATUSES:
                return {"error": f"Invalid status: {status}. Valid: {', '.join(VALID_STATUSES)}"}
            query += " WHERE sol.status = ?"
            params.append(status)
        query += " ORDER BY sol.created_at DESC LIMIT ?"
        params.append(limit)

        solutions = [{"solution_id": r["id"], "signal_id": r["signal_id"],
                       "signal_title": r["signal_title"] or "", "signal_source": r["signal_source"] or "",
                       "signal_category": r["signal_category"] or "", "gotcha_layer": r["gotcha_layer"],
                       "asset_type": r["asset_type"], "estimated_effort": r["estimated_effort"],
                       "status": r["status"], "spec_quality_score": r["spec_quality_score"],
                       "created_at": r["created_at"], "updated_at": r["updated_at"]}
                      for r in conn.execute(query, params).fetchall()]

        counts = {r["status"]: r["cnt"] for r in conn.execute(
            "SELECT status, COUNT(*) as cnt FROM innovation_solutions GROUP BY status").fetchall()}
        return {"solutions": solutions, "returned": len(solutions), "total": sum(counts.values()),
                "filter_status": status, "counts_by_status": counts}
    finally:
        conn.close()


# =========================================================================
# CLI
# =========================================================================
def _print_human(args, result):
    """Print human-readable output."""
    if "error" in result:
        print(f"ERROR: {result['error']}")
        return
    if args.generate:
        q = result.get("spec_quality_score")
        print(f"Solution generated: {result.get('solution_id')}\n"
              f"  Signal: {result.get('signal_id')}  GOTCHA: {result.get('gotcha_layer')}\n"
              f"  Asset: {result.get('asset_type')}  Effort: {result.get('estimated_effort')}\n"
              f"  Boundary: {result.get('boundary_tier')}  Quality: {f'{q:.2f}' if q else 'N/A'}\n"
              f"  Frameworks: {', '.join(result.get('frameworks_affected', [])) or 'None'}\n\n"
              f"--- Spec Content ---\n{result.get('spec_content', '')}")
    elif args.generate_all:
        print(f"Batch: {result.get('generated', 0)} generated, {result.get('errors', 0)} errors, "
              f"{result.get('skipped', 0)} skipped (budget: {result.get('budget')})")
        for r in result.get("results", []):
            icon = "OK" if r["status"] == "generated" else "FAIL"
            print(f"  [{icon}] {r['signal_id']}: {r['signal_title'][:60]} -> {r['result']}")
    elif args.status:
        sig = result.get("signal_summary", {})
        q = result.get("spec_quality_score")
        print(f"Solution: {result.get('solution_id')}\n"
              f"  Signal: {result.get('signal_id')} ({sig.get('title', 'N/A')})\n"
              f"  GOTCHA: {result.get('gotcha_layer')}  Asset: {result.get('asset_type')}\n"
              f"  Effort: {result.get('estimated_effort')}  Status: {result.get('status')}\n"
              f"  Quality: {f'{q:.2f}' if q else 'N/A'}")
    elif args.list:
        counts = result.get("counts_by_status", {})
        print(f"Solutions ({result.get('returned', 0)} of {result.get('total', 0)})"
              f"  Status: {', '.join(f'{k}={v}' for k, v in counts.items())}")
        for s in result.get("solutions", []):
            q = s.get("spec_quality_score")
            print(f"  {s['solution_id']} [{s['status']:10s}] {s['gotcha_layer']:10s} "
                  f"{s['asset_type']:12s} effort={s['estimated_effort']} "
                  f"quality={f'{q:.2f}' if q else 'N/A'}\n"
                  f"    Signal: {s['signal_title'][:70]}")


def main():
    parser = argparse.ArgumentParser(
        description="ICDEV Solution Spec Generator -- generate specs from triaged innovation signals")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--db-path", type=Path, default=None, help="Database path override")

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--generate", action="store_true", help="Generate spec for a single signal")
    group.add_argument("--generate-all", action="store_true", help="Generate specs for all approved signals")
    group.add_argument("--status", action="store_true", help="Check status of a specific solution")
    group.add_argument("--list", action="store_true", help="List generated solutions")

    parser.add_argument("--signal-id", type=str, help="Signal ID (with --generate)")
    parser.add_argument("--solution-id", type=str, help="Solution ID (with --status)")
    parser.add_argument("--status-filter", type=str, dest="filter_status",
                        choices=VALID_STATUSES, help="Filter by status (with --list)")
    parser.add_argument("--limit", type=int, default=20, help="Max results (with --list)")

    args = parser.parse_args()
    try:
        if args.generate:
            if not args.signal_id:
                parser.error("--generate requires --signal-id")
            result = generate_solution_spec(args.signal_id, args.db_path)
        elif args.generate_all:
            result = generate_all_approved(args.db_path)
        elif args.status:
            if not args.solution_id:
                parser.error("--status requires --solution-id")
            result = get_solution_status(args.solution_id, args.db_path)
        elif args.list:
            result = list_solutions(args.filter_status, args.limit, args.db_path)
        else:
            result = {"error": "No action specified"}

        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            _print_human(args, result)
    except Exception as e:
        err = {"error": str(e)}
        if args.json:
            print(json.dumps(err, indent=2))
        else:
            print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
