#!/usr/bin/env python3
# CUI // SP-CTI
"""TFW Chat Agent — Traffic Flow Walkthrough conversational agent.

Handles all slash commands for the Simulation Chat UI and routes each to the
appropriate canvas-aware generator. Also implements AUDIT mode, which scans
walkthrough findings and surfaces them as POA&M candidates.

Slash commands dispatched here:
  /ppsm         → PPSM / API Surface / Event Catalog matrix (canvas-aware)
  /api-surface  → alias for /ppsm on SDC
  /event-catalog → alias for /ppsm on EDA
  /dfd          → Data Flow Diagram (Mermaid)
  /cis          → CIS Controls v8 mapping
  /isa          → Inter-System Agreement document
  /poam         → Plan of Action and Milestones
  /oscal        → OSCAL 1.1.2 Component Definition (JSON)
  /bundle       → Download artifact bundle (delegates to blueprint bundle route)
  /diff         → Diff current topology vs baseline / previous REFINE state
  /spec         → Structured spec from REFINE session (delegates to spec_generator)
  /audit        → AUDIT mode: scan topology → surface findings → add to POAM
  /refactor     → Generate Python snippets to convert Java/legacy modules (CAM)
  /components   → Map AWS components to application features (CAM)
  /deprecated   → Identify EOL/legacy libraries to replace (CAM)
  /status       → Simulate migration readiness DRYRUN check (CAM)
  /coa          → Cloud Optimization Advice for a topic or component (CAM)

session.canvas_type is always auto-injected from the session DB record; callers
only need to pass session_id.

Public surface:
  TFWChatAgent.process(session_id, content) -> dict
    Returns: {reply, mode, diagram_mermaid?, artifacts?, canvas_type}
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

logger = get_logger("icdev.tfw_chat_agent")

# ---------------------------------------------------------------------------
# Canvas-specific command visibility
# Determines which slash commands appear in the UI autocomplete per canvas.
# ---------------------------------------------------------------------------

CANVAS_SLASH_COMMANDS: dict[str, list[str]] = {
    "cam": [
        "/coa", "/deprecated", "/refactor", "/status", "/analyze", "/components",
    ],
    "ndc": [
        "/explain", "/troubleshoot", "/refine", "/audit", "/analyze",
        "/ppsm", "/dfd", "/cis", "/isa", "/poam", "/oscal", "/bundle", "/diff", "/spec",
    ],
    "sdc": [
        "/explain", "/troubleshoot", "/audit", "/analyze",
        "/api-surface", "/dfd", "/cis", "/isa", "/poam", "/oscal", "/bundle", "/diff", "/spec",
    ],
    "eda": [
        "/explain", "/troubleshoot", "/refine", "/audit", "/analyze",
        "/event-catalog", "/dfd", "/cis", "/isa", "/poam", "/oscal", "/bundle", "/diff", "/spec",
    ],
    "ddc": [
        "/explain", "/troubleshoot", "/refine", "/audit", "/analyze",
        "/dfd", "/cis", "/isa", "/poam", "/oscal", "/bundle", "/diff", "/spec",
    ],
    "pdc": [
        "/explain", "/troubleshoot", "/refine", "/audit", "/analyze",
        "/dfd", "/cis", "/isa", "/poam", "/oscal", "/bundle", "/diff", "/spec",
    ],
    "bdc": [
        "/explain", "/troubleshoot", "/audit", "/analyze",
        "/ppsm", "/dfd", "/cis", "/isa", "/poam", "/oscal", "/bundle", "/diff", "/spec",
    ],
    "odc": [
        "/explain", "/troubleshoot", "/refine", "/audit", "/analyze",
        "/dfd", "/cis", "/isa", "/poam", "/oscal", "/bundle", "/diff", "/spec",
    ],
    "idc": [
        "/explain", "/troubleshoot", "/refine", "/audit", "/analyze",
        "/ppsm", "/dfd", "/cis", "/isa", "/poam", "/oscal", "/bundle", "/diff", "/spec",
    ],
}

# Fallback for unknown canvas types
_DEFAULT_COMMANDS = [
    "/explain", "/troubleshoot", "/audit", "/analyze",
    "/dfd", "/cis", "/isa", "/poam", "/oscal", "/bundle", "/diff", "/spec",
]


def get_canvas_commands(canvas_type: str) -> list[str]:
    """Return the slash commands available for a given canvas type."""
    return CANVAS_SLASH_COMMANDS.get(canvas_type.lower(), _DEFAULT_COMMANDS)


# ---------------------------------------------------------------------------
# AUDIT mode — topology findings → POA&M candidates
# ---------------------------------------------------------------------------

_AUDIT_PATTERNS: list[tuple[re.Pattern, str, str, str]] = [
    # (pattern, finding_template, risk_level, remediation_hint)
    (
        re.compile(r"\btelnet\b|\bftp\b|\bhttp\b(?!s)", re.I),
        "Insecure protocol detected: '{match}'",
        "High",
        "Replace with encrypted equivalent (SSH/SFTP/HTTPS).",
    ),
    (
        re.compile(r"\bno.?auth\b|\bunauthenticated\b|\bopen\b.*\bapi\b", re.I),
        "Unauthenticated access path detected: '{match}'",
        "Critical",
        "Add authentication (OAuth2/mTLS/API key) to this path.",
    ),
    (
        re.compile(r"\bself.?signed\b|\bexpired.?cert\b", re.I),
        "Certificate weakness detected: '{match}'",
        "High",
        "Replace self-signed/expired certificate with CA-issued cert.",
    ),
    (
        re.compile(r"\bdirect.?db\b|\bdb.?exposed\b|\bport.?5432\b|\bport.?3306\b|\bport.?27017\b", re.I),
        "Database directly exposed in data flow: '{match}'",
        "High",
        "Place database behind an application/service layer; restrict direct access.",
    ),
    (
        re.compile(r"\bplaintext\b|\bcleartext\b|\bunencrypted\b", re.I),
        "Plaintext data flow detected: '{match}'",
        "High",
        "Enforce encryption at rest and in transit for this path.",
    ),
    (
        re.compile(r"\bpublic\b.*\b(rce|exec|shell|cmd)\b", re.I),
        "Public code execution path detected: '{match}'",
        "Critical",
        "Isolate execution environment; add input validation and sandboxing.",
    ),
    (
        re.compile(r"\bno.?mfa\b|\bsingle.?factor\b|\bpassword.?only\b", re.I),
        "Weak authentication (no MFA) detected: '{match}'",
        "Moderate",
        "Enforce MFA for all privileged access paths.",
    ),
    (
        re.compile(r"\bno.?log\b|\bnot.?logged\b|\bmissing.?audit\b", re.I),
        "Missing audit logging detected: '{match}'",
        "Moderate",
        "Enable comprehensive audit logging and SIEM forwarding.",
    ),
]


def _scan_labels_for_findings(
    nodes: list[dict], edges: list[dict]
) -> list[dict]:
    """Scan topology node/edge labels for AUDIT findings."""
    from datetime import timedelta
    findings: list[dict] = []
    all_items = [(n.get("label", ""), "node") for n in nodes] + [
        (e.get("label", ""), "edge") for e in edges
    ]
    seen_findings: set[str] = set()
    for label, item_type in all_items:
        if not label:
            continue
        for pattern, template, risk_level, remediation in _AUDIT_PATTERNS:
            m = pattern.search(label)
            if m:
                finding_text = template.format(match=m.group(0))
                dedup_key = f"{risk_level}:{finding_text}"
                if dedup_key in seen_findings:
                    continue
                seen_findings.add(dedup_key)
                days = 30 if risk_level == "Critical" else (45 if risk_level == "High" else 60)
                due = (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%d")
                findings.append(
                    {
                        "finding_id": f"F-{uuid.uuid4().hex[:8].upper()}",
                        "weakness": finding_text,
                        "risk_level": risk_level,
                        "remediation": remediation,
                        "scheduled_completion": due,
                        "responsible_party": "ISSM",
                        "status": "Open",
                        "source": "audit-walkthrough",
                        "topology_item": label,
                        "item_type": item_type,
                    }
                )
    return findings


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------


def _get_db():
    from tools.dashboard.config import DB_PATH
    from tools.db.storage import get_connection
    return get_connection(db_path=str(DB_PATH))


def _load_canvas_type(session_id: str) -> str:
    """Load canvas_type from DB for a session. Returns 'ndc' on any error."""
    if not session_id:
        return "ndc"
    try:
        conn = _get_db()
        row = conn.execute(
            "SELECT canvas_type FROM nc_simulation_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        conn.close()
        if row:
            return (row[0] or "ndc").lower()
    except Exception:
        pass
    return "ndc"


def _load_graph_json(session_id: str) -> dict[str, Any]:
    """Load the topology graph for a session."""
    try:
        conn = _get_db()
        row = conn.execute(
            "SELECT topology_id, metadata FROM nc_simulation_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        if not row:
            conn.close()
            return {"nodes": [], "edges": []}
        meta_raw = row[1] or "{}"
        try:
            meta = json.loads(meta_raw) if isinstance(meta_raw, str) else meta_raw
        except json.JSONDecodeError:
            meta = {}
        if "refined_graph_json" in meta:
            conn.close()
            return meta["refined_graph_json"]
        topology_id = row[0]
        if topology_id:
            trow = conn.execute(
                "SELECT graph_json FROM topologies WHERE id = ?", (topology_id,)
            ).fetchone()
            if trow and trow[0]:
                conn.close()
                try:
                    return json.loads(trow[0]) if isinstance(trow[0], str) else trow[0]
                except json.JSONDecodeError:
                    return {"nodes": [], "edges": []}
        conn.close()
    except Exception:
        pass
    return {"nodes": [], "edges": []}


# ---------------------------------------------------------------------------
# Slash command handlers
# ---------------------------------------------------------------------------


def _handle_ppsm(session_id: str, canvas_type: str, args_text: str) -> dict:
    from tools.simulation.artifacts.ppsm_extractor import generate_ppsm
    from tools.canvas.canvas_registry import CANVAS_AVAILABLE_ARTIFACTS
    try:
        rows = generate_ppsm(session_id, canvas_type)
        resolved_map = {"ndc": "ndc", "sdc": "sdc", "eda": "eda",
                        "bdc": "sdc", "idc": "ndc", "odc": "ndc",
                        "ddc": "eda", "pdc": "eda", "qdc": "ndc", "mdc": "ndc"}
        resolved = resolved_map.get(canvas_type.lower(), "ndc")
        spec = CANVAS_AVAILABLE_ARTIFACTS.get(resolved, {})
        artifact_name = spec.get("artifact_name", "PPSM")
        cols = spec.get("columns", [])
        # Build markdown table
        if rows and cols:
            header = "| " + " | ".join(cols) + " |"
            sep = "| " + " | ".join("---" for _ in cols) + " |"
            body = "\n".join(
                "| " + " | ".join(str(r.get(c, "")) for c in cols) + " |"
                for r in rows[:20]
            )
            table = f"{header}\n{sep}\n{body}"
            reply = (
                f"**[{artifact_name} — {canvas_type.upper()}]** "
                f"{len(rows)} row(s)\n\n{table}\n\n"
                "Use `/oscal` to export as OSCAL or `/bundle` to download all artifacts."
            )
        else:
            reply = f"**[{artifact_name}]** No topology data available yet. Upload a diagram or use `/refine` first."
        return {"reply": reply, "mode": "ppsm", "artifacts": [{"type": "ppsm", "rows": rows}]}
    except Exception as exc:
        return {"reply": f"[PPSM] Error: {exc}", "mode": "ppsm", "error": str(exc)}


def _handle_dfd(session_id: str, canvas_type: str, args_text: str) -> dict:
    from tools.simulation.artifacts.dfd_generator import generate_dfd
    try:
        result = generate_dfd(session_id, canvas_type)
        mermaid_src = result["mermaid"]
        mermaid_fence = f"```mermaid\n{mermaid_src}\n```"
        reply = (
            f"**[DFD — {canvas_type.upper()}]** "
            f"{result['total_elements']} element(s), {result['total_flows']} flow(s)\n\n"
            f"{mermaid_fence}\n\n"
            "Processes shown as circles, data-stores as cylinders, external entities as rectangles."
        )
        return {
            "reply": reply,
            "mode": "dfd",
            "diagram_mermaid": mermaid_fence,
            "artifacts": [{"type": "dfd", "data": result}],
        }
    except Exception as exc:
        return {"reply": f"[DFD] Error: {exc}", "mode": "dfd", "error": str(exc)}


def _handle_cis(session_id: str, canvas_type: str, args_text: str) -> dict:
    from tools.simulation.artifacts.cis_generator import generate_cis_report
    try:
        result = generate_cis_report(session_id, canvas_type)
        reply = f"**[CIS Controls v8 — {canvas_type.upper()}]** {result['total']} control(s) mapped\n\n{result['markdown']}"
        return {"reply": reply, "mode": "cis", "artifacts": [{"type": "cis", "data": result}]}
    except Exception as exc:
        return {"reply": f"[CIS] Error: {exc}", "mode": "cis", "error": str(exc)}


def _handle_isa(session_id: str, canvas_type: str, args_text: str) -> dict:
    from tools.simulation.artifacts.isa_generator import generate_isa
    try:
        result = generate_isa(session_id, canvas_type)
        reply = f"**[ISA — {result['isa_id']}]**\n\n{result['markdown']}"
        return {"reply": reply, "mode": "isa", "artifacts": [{"type": "isa", "data": result}]}
    except Exception as exc:
        return {"reply": f"[ISA] Error: {exc}", "mode": "isa", "error": str(exc)}


def _handle_poam(session_id: str, canvas_type: str, args_text: str) -> dict:
    from tools.simulation.artifacts.poam_generator import generate_poam
    try:
        result = generate_poam(session_id, canvas_type)
        reply = (
            f"**[POA&M — {result['poam_id']}]** "
            f"{result['total']} finding(s): "
            f"🔴 {result['critical']} Critical | "
            f"🟠 {result['high']} High | "
            f"🟡 {result['moderate']} Moderate | "
            f"🟢 {result['low']} Low\n\n{result['markdown']}"
        )
        return {"reply": reply, "mode": "poam", "artifacts": [{"type": "poam", "data": result}]}
    except Exception as exc:
        return {"reply": f"[POA&M] Error: {exc}", "mode": "poam", "error": str(exc)}


def _handle_oscal(session_id: str, canvas_type: str, args_text: str) -> dict:
    from tools.simulation.artifacts.oscal_exporter import generate_oscal
    try:
        doc = generate_oscal(session_id, canvas_type)
        cd = doc.get("component-definition", {})
        n_comp = len(cd.get("components", []))
        oscal_json = json.dumps(doc, indent=2)
        reply = (
            f"**[OSCAL 1.1.2 — {canvas_type.upper()}]** "
            f"{n_comp} component(s) exported\n\n"
            f"```json\n{oscal_json[:2000]}{'...[truncated]' if len(oscal_json) > 2000 else ''}\n```\n\n"
            "Use `/bundle` to download the full OSCAL file."
        )
        return {
            "reply": reply,
            "mode": "oscal",
            "artifacts": [{"type": "oscal", "document": doc}],
        }
    except Exception as exc:
        return {"reply": f"[OSCAL] Error: {exc}", "mode": "oscal", "error": str(exc)}


def _handle_diff(session_id: str, canvas_type: str, args_text: str) -> dict:
    """Diff current refined topology vs baseline topology."""
    try:
        conn = _get_db()
        row = conn.execute(
            "SELECT topology_id, metadata FROM nc_simulation_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        conn.close()
        if not row:
            return {"reply": "[DIFF] Session not found.", "mode": "diff"}

        meta_raw = row[1] or "{}"
        try:
            meta = json.loads(meta_raw) if isinstance(meta_raw, str) else meta_raw
        except json.JSONDecodeError:
            meta = {}

        refined = meta.get("refined_graph_json", {})
        baseline_nodes: list[dict] = []
        topology_id = row[0]
        if topology_id:
            conn2 = _get_db()
            trow = conn2.execute(
                "SELECT graph_json FROM topologies WHERE id = ?", (topology_id,)
            ).fetchone()
            conn2.close()
            if trow and trow[0]:
                try:
                    baseline = json.loads(trow[0]) if isinstance(trow[0], str) else trow[0]
                    baseline_nodes = baseline.get("nodes", [])
                except json.JSONDecodeError:
                    pass

        refined_nodes = refined.get("nodes", [])
        baseline_ids = {n.get("id") for n in baseline_nodes}
        refined_ids = {n.get("id") for n in refined_nodes}

        added = [n for n in refined_nodes if n.get("id") not in baseline_ids]
        removed = [n for n in baseline_nodes if n.get("id") not in refined_ids]

        if not added and not removed:
            reply = "**[DIFF]** No topology changes detected between baseline and current REFINE state."
        else:
            lines = ["**[DIFF — Topology Changes]**\n"]
            if added:
                lines.append(f"**Added ({len(added)}):**")
                lines += [f"  + {n.get('label', n.get('id', '?'))}" for n in added]
            if removed:
                lines.append(f"\n**Removed ({len(removed)}):**")
                lines += [f"  - {n.get('label', n.get('id', '?'))}" for n in removed]
            reply = "\n".join(lines)

        return {
            "reply": reply,
            "mode": "diff",
            "added": added,
            "removed": removed,
        }
    except Exception as exc:
        return {"reply": f"[DIFF] Error: {exc}", "mode": "diff", "error": str(exc)}


def _handle_spec(session_id: str, canvas_type: str, args_text: str) -> dict:
    from tools.simulation.artifacts.spec_generator import generate_spec, spec_to_yaml
    try:
        spec = generate_spec(session_id, canvas_type)
        yaml_str = spec_to_yaml(spec)
        canvas_display = spec.get("canvas_display", canvas_type.upper())
        reply = (
            f"**[SPEC — {canvas_display}]**\n\n"
            f"```yaml\n{yaml_str}\n```\n\n"
            "Use `/refine` to update the diagram or `/troubleshoot` to analyze fault paths."
        )
        return {"reply": reply, "mode": "spec", "spec": spec}
    except Exception as exc:
        return {"reply": f"[SPEC] Error: {exc}", "mode": "spec", "error": str(exc)}


def _handle_audit(session_id: str, canvas_type: str, args_text: str) -> dict:
    """AUDIT mode: scan walkthrough topology → surface POA&M candidates."""
    from tools.simulation.artifacts.poam_generator import add_poam_finding, get_poam_findings

    graph = _load_graph_json(session_id)
    nodes: list[dict] = graph.get("nodes", [])
    edges: list[dict] = graph.get("edges", [])

    # Scan for findings
    new_findings = _scan_labels_for_findings(nodes, edges)

    # Persist each finding
    added_ids: list[str] = []
    for finding in new_findings:
        try:
            add_poam_finding(session_id, finding)
            added_ids.append(finding["finding_id"])
        except Exception as exc:
            logger.warning("AUDIT: failed to persist finding %s: %s", finding.get("finding_id"), exc)

    # Build reply
    if not new_findings:
        total_existing = len(get_poam_findings(session_id))
        reply = (
            f"**[AUDIT — {canvas_type.upper()}]** No new findings in current topology.\n\n"
            f"Existing POA&M findings: {total_existing}. "
            "Use `/poam` to review all findings."
        )
    else:
        def _risk_badge(r: str) -> str:
            return {"Critical": "🔴", "High": "🟠", "Moderate": "🟡", "Low": "🟢"}.get(r, "⚪")

        lines = [f"**[AUDIT — {canvas_type.upper()}]** {len(new_findings)} new finding(s) added to POA&M:\n"]
        for f in new_findings:
            lines.append(
                f"{_risk_badge(f['risk_level'])} **[{f['risk_level']}]** "
                f"`{f['finding_id']}` — {f['weakness']}"
            )
        lines.append("\nUse `/poam` to view the full Plan of Action and Milestones.")
        reply = "\n".join(lines)

    return {
        "reply": reply,
        "mode": "audit",
        "findings_added": len(new_findings),
        "finding_ids": added_ids,
        "canvas_type": canvas_type,
    }


# ---------------------------------------------------------------------------
# Slash command registry
# ---------------------------------------------------------------------------

# Maps command prefix → handler function
def _handle_analyze(session_id: str, canvas_type: str, args_text: str) -> dict:
    """Fetch a URL and return a canvas-aware LLM analysis."""
    url = args_text.strip()
    if not url:
        return {
            "reply": (
                "**Usage:** `/analyze <url>`\n\n"
                "Examples:\n"
                "- `/analyze https://github.com/org/repo/tree/main/src`\n"
                "- `/analyze https://docs.example.com/architecture`\n\n"
                "Paste a GitHub repo, directory, file, or any web page — "
                "I'll fetch it and produce a structured analysis tuned to the current canvas."
            ),
            "mode": "analyze",
        }
    if not url.startswith("http"):
        return {
            "reply": f"**Invalid URL:** `{url}` — must start with `http://` or `https://`",
            "mode": "analyze",
        }
    try:
        from tools.chat_router.url_analyzer import analyze
        result = analyze(url, canvas_type)
        return {"reply": result["reply"], "mode": "analyze"}
    except Exception as exc:
        return {"reply": f"[Analyze error: {exc}]", "mode": "error"}


def _llm_call(system_prompt: str, user_content: str) -> str | None:
    """Invoke LLM via router (provider-agnostic). Returns text or None on failure."""
    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest
        router = LLMRouter()
        req = LLMRequest(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
        )
        resp = router.invoke("chat_response", req)
        return (resp.content or "").strip() if resp else None
    except Exception as exc:
        logger.warning("LLM call failed: %s", exc)
        return None


def _resolve_arg_with_url(arg: str) -> tuple[str, str]:
    """If *arg* is a URL, fetch its content and return (label, context_block).

    Returns (arg, "") when *arg* is not a URL — callers treat blank context as
    name-only mode.
    """
    if not arg.startswith("http://") and not arg.startswith("https://"):
        return arg, ""
    try:
        from tools.chat_router.url_analyzer import fetch_content
        content, _ = fetch_content(arg)
        block = f"\n\n--- URL CONTENT: {arg} ---\n{content[:4000]}\n--- END ---"
        return arg, block
    except Exception as exc:
        logger.warning("URL fetch failed for %s: %s", arg, exc)
        return arg, f"\n\n[Could not fetch URL: {exc}]"


def _handle_refactor(session_id: str, canvas_type: str, args_text: str) -> dict:
    """Generate Python conversion snippets for a Java/legacy module or URL."""
    module = args_text.strip()
    if not module:
        return {
            "reply": (
                "**Usage:** `/refactor <module | url>`\n\n"
                "Examples:\n"
                "- `/refactor AuthService` — Java auth service → Python equivalent\n"
                "- `/refactor DataAccessLayer` — JDBC/Hibernate → SQLAlchemy\n"
                "- `/refactor https://github.com/org/repo` — fetch repo and generate conversion plan\n\n"
                "Provide a module name or a URL and I'll generate idiomatic Python "
                "conversion snippets with migration notes."
            ),
            "mode": "refactor",
        }

    label, url_ctx = _resolve_arg_with_url(module)
    system = (
        "You are a senior migration architect specializing in Java-to-Python modernization for AWS cloud deployments. "
        "Produce concise, idiomatic Python 3.12 code snippets for the requested module conversion. "
        "Include: (1) equivalent Python class/function, (2) key library mappings (e.g. JDBC→SQLAlchemy, "
        "Spring→FastAPI, EhCache→redis-py), (3) gotchas or behavioral differences to watch. "
        "Format with markdown code fences."
    )
    user = f"Generate Python conversion snippets for: **{label}**{url_ctx}"
    reply = _llm_call(system, user)
    if not reply:
        reply = (
            "LLM unavailable. Common Java→Python mappings:\n"
            "- `Spring @Service` → `class MyService:` with dependency injection via constructor\n"
            "- `JDBC/Hibernate` → `SQLAlchemy` ORM or `asyncpg` for async\n"
            "- `EhCache` → `redis-py` with `@lru_cache` for in-process caching\n"
            "- `Log4j` → `logging` stdlib + `structlog` for structured output\n"
            "- `JUnit` → `pytest` with fixtures"
        )
    return {"reply": f"**[REFACTOR — {label}]**\n\n{reply}", "mode": "refactor"}


def _handle_components(session_id: str, canvas_type: str, args_text: str) -> dict:
    """List AWS components mapped to application features or a URL."""
    feature = args_text.strip()
    if not feature:
        return {
            "reply": (
                "**Usage:** `/components <feature | url>`\n\n"
                "Examples:\n"
                "- `/components authentication` — IAM, Cognito, WAF mappings\n"
                "- `/components caching` — ElastiCache, DAX, CloudFront\n"
                "- `/components https://github.com/org/repo` — infer component map from repo\n\n"
                "Describe a feature, function, or paste a URL and I'll map it to the optimal AWS component stack."
            ),
            "mode": "components",
        }

    label, url_ctx = _resolve_arg_with_url(feature)
    system = (
        "You are an AWS solutions architect. For the requested feature or codebase, produce a structured table "
        "mapping application functions to AWS components. Include: Component, Purpose, Tier "
        "(Compute/Storage/Network/Security/AI-ML), and Migration Notes. "
        "Focus on GovCloud-compatible services. Use markdown tables."
    )
    user = f"Map AWS components for: **{label}**{url_ctx}"
    reply = _llm_call(system, user)
    if not reply:
        reply = (
            "LLM unavailable. Core AWS component patterns:\n\n"
            "| Component | Purpose | Tier |\n|-----------|---------|------|\n"
            "| EKS | Container orchestration | Compute |\n"
            "| ElastiCache | Distributed caching | Storage |\n"
            "| Bedrock | Foundation model inference | AI/ML |\n"
            "| RDS Aurora | Relational DB | Storage |\n"
            "| API Gateway | REST/WebSocket routing | Network |\n"
            "| WAF + Shield | DDoS / OWASP protection | Security |"
        )
    return {"reply": f"**[COMPONENTS — {label}]**\n\n{reply}", "mode": "components"}


def _handle_deprecated(session_id: str, canvas_type: str, args_text: str) -> dict:
    """Identify legacy/deprecated libraries in a codebase name or URL."""
    arg = args_text.strip()
    label, url_ctx = _resolve_arg_with_url(arg) if arg else ("the current codebase", "")
    system = (
        "You are a modernization engineer performing a dependency audit. "
        "For the provided codebase or tech stack, identify: (1) EOL/deprecated libraries, "
        "(2) security-risky packages (known CVEs or unmaintained), "
        "(3) recommended replacements with migration effort estimate (Low/Medium/High). "
        "Format as a markdown table: Library | Status | Replacement | Effort | Notes."
    )
    user = f"Identify deprecated or legacy libraries in: **{label}**{url_ctx}"
    reply = _llm_call(system, user)
    if not reply:
        reply = (
            "LLM unavailable. Common deprecated patterns:\n\n"
            "| Library | Status | Replacement | Effort |\n|---------|--------|-------------|--------|\n"
            "| Log4j 1.x | EOL + CVEs | Log4j 2.x / Logback | Low |\n"
            "| Spring 4.x | EOL | Spring Boot 3.x | Medium |\n"
            "| Java 8 / 11 | Nearing EOL | Java 21 LTS | Medium |\n"
            "| EhCache 2.x | EOL | Caffeine / Redis | Low |\n"
            "| Struts 2 | CVE history | Spring MVC / Quarkus | High |"
        )
    return {"reply": f"**[DEPRECATED — {label}]**\n\n{reply}", "mode": "deprecated"}


def _handle_status(session_id: str, canvas_type: str, args_text: str) -> dict:
    """Simulate migration readiness checks (DRYRUN) for a component or URL."""
    component = args_text.strip()
    if not component:
        return {
            "reply": (
                "**Usage:** `/status <component | url>`\n\n"
                "Examples:\n"
                "- `/status AuthService` — readiness check for auth module\n"
                "- `/status database` — DB migration readiness\n"
                "- `/status https://github.com/org/repo` — readiness check from live repo\n"
                "- `/status all` — full system migration readiness scan\n\n"
                "Runs a simulated DRYRUN migration readiness check."
            ),
            "mode": "status",
        }

    label, url_ctx = _resolve_arg_with_url(component)
    system = (
        "You are a migration readiness assessor running a DRYRUN check. "
        "Produce a structured readiness report for the named component. Include: "
        "(1) Readiness Score 0–100, (2) Go/No-Go verdict, (3) Blocker items (must fix before migration), "
        "(4) Warning items (fix after migration), (5) Passing checks. "
        "Format with clear headers and color-coded status (✅ Pass / ⚠️ Warn / ❌ Block). "
        "Label the report clearly as DRYRUN — SIMULATED."
    )
    user = f"Run a DRYRUN migration readiness check for: **{label}**{url_ctx}"
    reply = _llm_call(system, user)
    if not reply:
        import random
        score = random.randint(55, 85)
        reply = (
            f"> ⚠️ **DRYRUN — SIMULATED** (LLM unavailable)\n\n"
            f"**Readiness Score:** {score}/100 — {'🟡 Conditional Go' if score < 75 else '🟢 Go'}\n\n"
            "✅ Source code compiles without errors\n"
            "✅ Unit test coverage ≥ 60%\n"
            "⚠️ Integration tests not yet migrated\n"
            "⚠️ Secrets still in environment variables (move to Secrets Manager)\n"
            "❌ Database schema migration not validated in target environment\n\n"
            "*Tip: pass a GitHub URL to enrich this check with actual source data.*"
        )
    return {"reply": f"**[STATUS — DRYRUN: {label}]**\n\n{reply}", "mode": "status"}


def _handle_coa(session_id: str, canvas_type: str, args_text: str) -> dict:
    """Get Cloud Optimization Advice for a topic, component, or URL."""
    topic = args_text.strip()
    if not topic:
        return {
            "reply": (
                "**Usage:** `/coa <topic | url>`\n\n"
                "Examples:\n"
                "- `/coa latency` — reduce API response time\n"
                "- `/coa cost` — optimize AWS spend\n"
                "- `/coa https://github.com/org/repo` — optimization advice from live codebase\n"
                "- `/coa cold-start` — Lambda/EKS startup optimization\n\n"
                "Describe a concern or paste a URL for specific, actionable Cloud Optimization Advice."
            ),
            "mode": "coa",
        }

    label, url_ctx = _resolve_arg_with_url(topic)
    system = (
        "You are a cloud performance and cost optimization expert specializing in AWS GovCloud. "
        "For the given topic or codebase, provide specific Cloud Optimization Advice (COA). Structure as: "
        "(1) Problem Analysis — root causes to investigate, "
        "(2) Quick Wins — changes achievable in < 1 sprint, "
        "(3) Strategic Changes — architectural improvements for long-term gain, "
        "(4) Expected Impact — latency reduction %, cost savings estimate, or throughput gain. "
        "Be specific: name AWS services, configuration flags, and numeric thresholds."
    )
    user = f"Provide Cloud Optimization Advice for: **{label}**{url_ctx}"
    reply = _llm_call(system, user)
    if not reply:
        reply = (
            "LLM unavailable. General optimization patterns:\n\n"
            "**Quick Wins:**\n"
            "- Enable ElastiCache in front of RDS for read-heavy queries (↓ latency ~60–80%)\n"
            "- Right-size EC2/EKS nodes with Compute Optimizer recommendations\n"
            "- Add CloudFront CDN for static assets and API responses\n\n"
            "**Strategic Changes:**\n"
            "- Migrate hot-path endpoints to Lambda@Edge for sub-10ms global response\n"
            "- Adopt DynamoDB for session/state storage instead of RDS\n"
            "- Enable Graviton3 instances for 20–40% cost reduction on compute"
        )
    return {"reply": f"**[COA — {label}]**\n\n{reply}", "mode": "coa"}


_COMMAND_HANDLERS: dict[str, Any] = {
    "/ppsm": _handle_ppsm,
    "/api-surface": _handle_ppsm,
    "/event-catalog": _handle_ppsm,
    "/dfd": _handle_dfd,
    "/cis": _handle_cis,
    "/isa": _handle_isa,
    "/poam": _handle_poam,
    "/oscal": _handle_oscal,
    "/bundle": None,  # Handled as redirect to bundle route
    "/diff": _handle_diff,
    "/spec": _handle_spec,
    "/audit": _handle_audit,
    "/analyze": _handle_analyze,
    "/refactor": _handle_refactor,
    "/components": _handle_components,
    "/deprecated": _handle_deprecated,
    "/status": _handle_status,
    "/coa": _handle_coa,
}


def _parse_command(content: str) -> tuple[str | None, str]:
    """Return (command, args_text) if content starts with a slash command."""
    if not content.startswith("/"):
        return None, content
    for cmd in sorted(_COMMAND_HANDLERS, key=len, reverse=True):
        if content.lower().startswith(cmd):
            args = content[len(cmd):].strip()
            return cmd, args
    return None, content


# ---------------------------------------------------------------------------
# Main agent entry point
# ---------------------------------------------------------------------------


class TFWChatAgent:
    """Canvas-aware TFW chat agent.

    Handles slash command dispatch and AUDIT mode. session.canvas_type is
    auto-injected from DB; callers only pass session_id.
    """

    def process(self, session_id: str, content: str) -> dict:
        """Process a chat message for a TFW session.

        Args:
            session_id: nc_simulation_sessions.id (may be empty string)
            content:    Raw message text (may start with a slash command)

        Returns:
            dict with at minimum: reply (str), mode (str), canvas_type (str)
        """
        canvas_type = _load_canvas_type(session_id)
        cmd, args_text = _parse_command(content.strip())

        if cmd is None:
            # Not a slash command — return a brief stub reply.
            # The blueprint's existing explain/troubleshoot/refine logic handles these.
            return {"reply": None, "mode": "explain", "canvas_type": canvas_type}

        if cmd == "/bundle":
            # Bundle is served by the blueprint route; return a redirect hint.
            download_url = f"/api/simulate/bundle/{session_id}/download" if session_id else None
            reply = (
                f"**[BUNDLE — {canvas_type.upper()}]** Artifact bundle ready.\n\n"
                + (f"[Download bundle]({download_url})" if download_url else "Start a session first.")
            )
            return {"reply": reply, "mode": "bundle", "canvas_type": canvas_type,
                    "download_url": download_url}

        handler = _COMMAND_HANDLERS.get(cmd)
        if handler is None:
            return {
                "reply": f"[Unknown command: `{cmd}`] Available: {', '.join(sorted(_COMMAND_HANDLERS))}",
                "mode": "error",
                "canvas_type": canvas_type,
            }

        result = handler(session_id, canvas_type, args_text)
        result["canvas_type"] = canvas_type
        return result


# ---------------------------------------------------------------------------
# Module-level convenience — single shared instance
# ---------------------------------------------------------------------------

_agent = TFWChatAgent()


def process_message(session_id: str, content: str) -> dict:
    """Module-level entry point used by blueprint.py."""
    return _agent.process(session_id, content)
