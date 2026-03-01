# [TEMPLATE: CUI // SP-CTI]
# ICDEV Pre-Tool-Use Hook — Safety validation before tool execution
# Adapted from ADW pre_tool_use.py

"""
Pre-tool-use hook that validates tool calls before execution.

Blocks:
    - Dangerous rm -rf commands
    - Access to .env files containing secrets
    - UPDATE/DELETE/DROP/TRUNCATE on all 32 append-only tables (D6, NIST AU)
      See APPEND_ONLY_TABLES list in is_append_only_table_modification()
    - Deletion of CUI-marked artifacts without explicit approval

Exit codes:
    0 = allow tool call
    2 = block tool call (shows error to Claude)
"""

import json
import os
import re
import sys
from fnmatch import fnmatch
from pathlib import Path


def is_dangerous_rm_command(command: str) -> bool:
    """Detect dangerous rm commands."""
    normalized = " ".join(command.lower().split())

    patterns = [
        r"\brm\s+.*-[a-z]*r[a-z]*f",
        r"\brm\s+.*-[a-z]*f[a-z]*r",
        r"\brm\s+--recursive\s+--force",
        r"\brm\s+--force\s+--recursive",
        r"\brm\s+-r\s+.*-f",
        r"\brm\s+-f\s+.*-r",
    ]

    for pattern in patterns:
        if re.search(pattern, normalized):
            return True

    # Check for rm with recursive flag targeting dangerous paths
    dangerous_paths = [r"/", r"/\*", r"~", r"~/", r"\$HOME", r"\.\.", r"\*", r"\."]
    if re.search(r"\brm\s+.*-[a-z]*r", normalized):
        for path in dangerous_paths:
            if re.search(path, normalized):
                return True

    return False


def is_env_file_access(tool_name: str, tool_input: dict) -> bool:
    """Check if a tool is trying to access .env files."""
    if tool_name in ("Read", "Edit", "MultiEdit", "Write"):
        file_path = tool_input.get("file_path", "")
        if ".env" in file_path and not file_path.endswith(".env.sample"):
            return True

    elif tool_name == "Bash":
        command = tool_input.get("command", "")
        env_patterns = [
            r"\b\.env\b(?!\.sample)",
            r"cat\s+.*\.env\b(?!\.sample)",
            r"echo\s+.*>\s*\.env\b(?!\.sample)",
        ]
        for pattern in env_patterns:
            if re.search(pattern, command):
                return True

    return False


def is_append_only_table_modification(tool_name: str, tool_input: dict) -> bool:
    """Block UPDATE/DELETE/DROP/TRUNCATE on all append-only tables (NIST 800-53 AU, D6).

    This list must stay in sync with init_icdev_db.py. Run the governance
    validator to detect drift: python tools/testing/claude_dir_validator.py --json
    """
    APPEND_ONLY_TABLES = [
        # Core audit
        "audit_trail",
        "hook_events",
        # Phase 44 — Innovation Adaptation
        "extension_execution_log",
        "memory_consolidation_log",
        # Phase 29 — Proactive Monitoring
        "auto_resolution_log",
        # Phase 36 — Evolutionary Intelligence
        "propagation_log",
        # Phase 37 — AI Security
        "prompt_injection_log",
        "ai_telemetry",
        # Phase 22 — Marketplace
        "marketplace_reviews",
        "marketplace_scan_results",
        # Multi-Agent Orchestration
        "agent_vetoes",
        # Dashboard Auth (D169-D172)
        "dashboard_auth_log",
        # Phase 24 — DevSecOps
        "devsecops_pipeline_audit",
        # Phase 28 — Remote Gateway
        "remote_command_log",
        # Phase 35 — Innovation Engine (D206)
        "innovation_signals",
        "innovation_triage_log",
        # Phase 39 — Observability
        "agent_executions",
        # Phase 40 — NLQ
        "nlq_queries",
        # Phase 22 — Marketplace (immutable published versions)
        "marketplace_versions",
        # Phase 34 — Dev Profiles (immutable rows, D183)
        "dev_profiles",
        # Phase 45 — OWASP Agentic AI Security (D258, D259, D260)
        "tool_chain_events",
        "agent_trust_scores",
        "agent_output_violations",
        # Phase 46 — Observability, Traceability & XAI (D280-D290)
        "otel_spans",
        "prov_entities",
        "prov_activities",
        "prov_relations",
        "shap_attributions",
        "xai_assessments",
        # Phase 47 — Production Readiness Audit (D292)
        "production_audits",
        # Phase 47 — Production Remediation (D296-D300)
        "remediation_audit_log",
        # OSCAL Ecosystem (D306 — validation audit trail)
        "oscal_validation_log",
        # Phase 48 — AI Transparency & Accountability (D307-D315)
        "confabulation_checks",
        "fairness_assessments",
        "model_cards",
        "system_cards",
        "ai_use_case_inventory",
        # Phase 49 — AI Accountability (D316-D321)
        "ai_oversight_plans",
        "ai_accountability_appeals",
        "ai_incident_log",
        "ai_ethics_reviews",
        # Phase 52 — Code Intelligence (D332)
        "code_quality_metrics",
        "runtime_feedback",
        # Phase 53 — OWASP ASI + FedRAMP 20x (D339)
        "owasp_asi_assessments",
        # Phase 57 — EU AI Act (D349)
        "eu_ai_act_assessments",
        # Proposal Lifecycle (D-PROP-3 — reviews, findings, status history are immutable)
        "proposal_reviews",
        "proposal_review_findings",
        "proposal_status_history",
        # Creative Engine (D357 — creative_competitors excluded: allows UPDATE for status transitions)
        "creative_signals",
        "creative_pain_points",
        "creative_feature_gaps",
        "creative_specs",
        "creative_trends",
        # GovCon Intelligence (Phase 59, D361-D373)
        "rfp_shall_statements",
        "rfp_requirement_patterns",
        "icdev_capability_map",
        "proposal_section_drafts",
        "govcon_awards",
        # Customer Delivery Tracking (D374)
        "customer_deliveries",
        # Phase 59 — Questions to Government (D-QTG-2)
        "proposal_question_responses",
        # Phase 60 — CPMP (D-CPMP-7)
        "cpmp_status_history",
        "cpmp_negative_events",
        "cpmp_evm_periods",
        "cpmp_cdrl_generations",
        "cpmp_cor_access_log",
        # Phase 61 — ATLAS Critique (Feature 3)
        "atlas_critique_findings",
        # Phase 61 — Prompt Chain Execution (Feature 2)
        "prompt_chain_executions",
    ]

    if tool_name == "Bash":
        command = tool_input.get("command", "").lower()
        for table in APPEND_ONLY_TABLES:
            # Block SQL UPDATE/DELETE on protected table
            if re.search(rf"(update|delete)\s+(from\s+)?{table}", command):
                return True
            # Block DROP TABLE on protected table
            if re.search(rf"drop\s+table\s+.*{table}", command):
                return True
            # Block TRUNCATE on protected table
            if re.search(rf"truncate\s+.*{table}", command):
                return True

    return False


def _load_file_access_tiers():
    """Load file access tier config from args/file_access_tiers.yaml."""
    try:
        import yaml
    except ImportError:
        return None
    config_path = Path(__file__).resolve().parent.parent.parent / "args" / "file_access_tiers.yaml"
    if not config_path.exists():
        return None
    try:
        with open(config_path, "r") as f:
            config = yaml.safe_load(f)
        tiers = config.get("file_access_tiers", {})
        if not tiers.get("enabled", False):
            return None
        return tiers
    except Exception:
        return None


def _matches_tier(file_path: str, patterns: list) -> bool:
    """Check if file_path matches any pattern in the tier (glob-style)."""
    if not file_path:
        return False
    # Normalize to forward slashes and strip leading ./
    fp = file_path.replace("\\", "/")
    if fp.startswith("./"):
        fp = fp[2:]
    for pattern in patterns:
        if pattern.startswith("!"):
            continue  # exclusion patterns handled separately
        # Check exclusions first
        excluded = False
        for exc in patterns:
            if exc.startswith("!") and fnmatch(fp, exc[1:]):
                excluded = True
                break
        if excluded:
            continue
        if fnmatch(fp, pattern) or fnmatch(os.path.basename(fp), pattern):
            return True
    return False


def check_file_access_tiers(tool_name: str, tool_input: dict) -> str:
    """Check file access tiers. Returns error message if blocked, None if allowed.

    Decision D-ORCH-8: Tiered file access control.
    """
    tiers = _load_file_access_tiers()
    if not tiers:
        return None

    file_path = ""
    is_write = False
    is_delete = False

    if tool_name in ("Read",):
        file_path = tool_input.get("file_path", "")
        # Read — only blocked by zero_access
    elif tool_name in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        file_path = tool_input.get("file_path", tool_input.get("notebook_path", ""))
        is_write = True
    elif tool_name == "Bash":
        command = tool_input.get("command", "")
        # Check for rm/delete commands targeting protected files
        rm_match = re.search(r'\brm\s+(?:-[a-z]*\s+)*([^\s|;&]+)', command)
        if rm_match:
            file_path = rm_match.group(1)
            is_delete = True
        # Check for write redirections
        redir_match = re.search(r'>\s*([^\s|;&]+)', command)
        if redir_match and not is_delete:
            file_path = redir_match.group(1)
            is_write = True

    if not file_path:
        return None

    # Zero access — block everything
    zero_patterns = [p for t in [tiers.get("zero_access", {})] for p in t.get("patterns", [])]
    if _matches_tier(file_path, zero_patterns):
        return f"BLOCKED: File '{file_path}' is in zero_access tier (D-ORCH-8). No access allowed."

    # Read only — block writes and deletes
    ro_patterns = [p for t in [tiers.get("read_only", {})] for p in t.get("patterns", [])]
    if (is_write or is_delete) and _matches_tier(file_path, ro_patterns):
        return f"BLOCKED: File '{file_path}' is in read_only tier (D-ORCH-8). Write/delete prohibited."

    # No delete — block deletes only
    nd_patterns = [p for t in [tiers.get("no_delete", {})] for p in t.get("patterns", [])]
    if is_delete and _matches_tier(file_path, nd_patterns):
        return f"BLOCKED: File '{file_path}' is in no_delete tier (D-ORCH-8). Deletion prohibited."

    return None


def main():
    try:
        input_data = json.load(sys.stdin)
        tool_name = input_data.get("tool_name", "")
        tool_input = input_data.get("tool_input", {})

        # Block .env file access
        if is_env_file_access(tool_name, tool_input):
            print("BLOCKED: Access to .env files is prohibited. Use AWS Secrets Manager.", file=sys.stderr)
            sys.exit(2)

        # Block dangerous rm commands
        if tool_name == "Bash":
            command = tool_input.get("command", "")
            if is_dangerous_rm_command(command):
                print("BLOCKED: Dangerous rm command detected and prevented", file=sys.stderr)
                sys.exit(2)

        # Block modification of all append-only tables (NIST 800-53 AU, D6)
        if is_append_only_table_modification(tool_name, tool_input):
            print("BLOCKED: Append-only table (D6, NIST 800-53 AU). No UPDATE/DELETE/DROP/TRUNCATE allowed.", file=sys.stderr)
            sys.exit(2)

        # Check tiered file access control (D-ORCH-8)
        tier_error = check_file_access_tiers(tool_name, tool_input)
        if tier_error:
            print(tier_error, file=sys.stderr)
            sys.exit(2)

        sys.exit(0)

    except json.JSONDecodeError:
        sys.exit(0)
    except Exception:
        sys.exit(0)


if __name__ == "__main__":
    main()
