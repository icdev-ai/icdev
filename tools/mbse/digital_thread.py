# CUI // SP-CTI
#!/usr/bin/env python3
"""ICDEV Digital Thread Engine — end-to-end traceability across the MBSE lifecycle.

Manages N:M links between: DOORS requirements -> SysML elements -> code modules
-> test files -> NIST controls -> STIG rules -> compliance artifacts.

Supports forward/backward trace, coverage analysis, orphan/gap detection,
heuristic auto-linking, and CUI-marked traceability reports.
"""

import argparse
import json
import re
import sqlite3
import sys
from collections import deque
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

# Audit trail integration (graceful fallback for standalone use)
try:
    sys.path.insert(0, str(BASE_DIR))
    from tools.audit.audit_logger import log_event
except ImportError:
    def log_event(**kwargs):
        pass

# Valid artifact types in the digital thread
VALID_TYPES = (
    "doors_requirement", "sysml_element", "code_module",
    "test_file", "nist_control", "stig_rule", "compliance_artifact",
)

# Valid link relationship types
VALID_LINK_TYPES = (
    "satisfies", "derives_from", "implements", "verifies",
    "traces_to", "allocates", "refines", "maps_to",
)

# Expected chain order for completeness analysis
THREAD_CHAIN = [
    "doors_requirement", "sysml_element", "code_module",
    "test_file", "nist_control",
]

# Keyword-to-NIST-control-family mapping for auto-linking
CONTROL_KEYWORD_MAP = {
    "AC": ["auth", "access", "login", "permission", "role", "rbac", "authorization"],
    "AU": ["audit", "log", "logging", "trail", "monitor", "event"],
    "SC": ["encrypt", "crypto", "tls", "ssl", "certificate", "cipher", "hash"],
    "IA": ["identity", "authenticate", "credential", "password", "mfa", "token"],
    "CM": ["config", "configuration", "baseline", "change_management"],
    "SI": ["integrity", "validation", "sanitize", "input_check", "patch"],
    "AC": ["access", "auth", "login", "permission", "role", "rbac", "authorization"],
}


# ---------------------------------------------------------------------------
# Helper: resolve human-readable name for an element
# ---------------------------------------------------------------------------
def _resolve_element_name(element_type: str, element_id: str, conn) -> str:
    """Resolve human-readable name for an element by type.

    doors_requirement  -> doors_requirements.title
    sysml_element      -> sysml_elements.name
    code_module        -> just the path string
    test_file          -> just the path string
    nist_control       -> compliance_controls.title
    stig_rule          -> stig_findings.title
    compliance_artifact -> 'artifact: ' + element_id
    """
    c = conn.cursor()
    try:
        if element_type == "doors_requirement":
            c.execute("SELECT title FROM doors_requirements WHERE id = ?", (element_id,))
            row = c.fetchone()
            return row[0] if row else element_id
        elif element_type == "sysml_element":
            c.execute("SELECT name FROM sysml_elements WHERE id = ?", (element_id,))
            row = c.fetchone()
            return row[0] if row else element_id
        elif element_type == "code_module":
            return element_id
        elif element_type == "test_file":
            return element_id
        elif element_type == "nist_control":
            c.execute("SELECT title FROM compliance_controls WHERE id = ?", (element_id,))
            row = c.fetchone()
            return row[0] if row else element_id
        elif element_type == "stig_rule":
            c.execute("SELECT title FROM stig_findings WHERE rule_id = ?", (element_id,))
            row = c.fetchone()
            return row[0] if row else element_id
        elif element_type == "compliance_artifact":
            return f"artifact: {element_id}"
        else:
            return element_id
    except sqlite3.OperationalError:
        return element_id


# ---------------------------------------------------------------------------
# Core: create_link
# ---------------------------------------------------------------------------
def create_link(project_id: str, source_type: str, source_id: str,
                target_type: str, target_id: str, link_type: str,
                evidence: str = None, confidence: float = 1.0,
                created_by: str = "icdev-mbse-engine", db_path=None) -> dict:
    """Create a digital thread link. Returns {"id": int, "created": bool} or error.

    Uses INSERT OR REPLACE for idempotency.
    """
    if source_type not in VALID_TYPES:
        return {"error": f"Invalid source_type '{source_type}'. Valid: {VALID_TYPES}"}
    if target_type not in VALID_TYPES:
        return {"error": f"Invalid target_type '{target_type}'. Valid: {VALID_TYPES}"}
    if link_type not in VALID_LINK_TYPES:
        return {"error": f"Invalid link_type '{link_type}'. Valid: {VALID_LINK_TYPES}"}
    if not (0.0 <= confidence <= 1.0):
        return {"error": "Confidence must be between 0.0 and 1.0"}

    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    c = conn.cursor()
    try:
        c.execute(
            """INSERT OR REPLACE INTO digital_thread_links
               (project_id, source_type, source_id, target_type, target_id,
                link_type, confidence, evidence, created_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (project_id, source_type, source_id, target_type, target_id,
             link_type, confidence, evidence, created_by,
             datetime.now().isoformat()),
        )
        conn.commit()
        link_id = c.lastrowid
        created = True

        # Audit trail
        try:
            log_event(
                event_type="digital_thread_linked",
                actor=created_by,
                action=f"Linked {source_type}:{source_id} -> {target_type}:{target_id} ({link_type})",
                project_id=project_id,
                details={
                    "link_id": link_id,
                    "source_type": source_type,
                    "source_id": source_id,
                    "target_type": target_type,
                    "target_id": target_id,
                    "link_type": link_type,
                    "confidence": confidence,
                },
                db_path=path,
            )
        except Exception:
            pass  # Audit failure should not block link creation

        return {"id": link_id, "created": created}
    except sqlite3.Error as e:
        return {"error": str(e)}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Core: delete_link
# ---------------------------------------------------------------------------
def delete_link(project_id: str, link_id: int, db_path=None) -> bool:
    """Delete a specific link by ID. Returns True if deleted, False otherwise."""
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    c = conn.cursor()
    try:
        c.execute(
            "DELETE FROM digital_thread_links WHERE id = ? AND project_id = ?",
            (link_id, project_id),
        )
        conn.commit()
        deleted = c.rowcount > 0
        return deleted
    except sqlite3.Error:
        return False
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Trace: forward (BFS)
# ---------------------------------------------------------------------------
def get_forward_trace(project_id: str, source_type: str, source_id: str,
                      max_depth: int = 10, db_path=None) -> dict:
    """Trace forward from a source element through the digital thread.

    Uses BFS traversal. Returns tree structure:
    {"source": {"type": str, "id": str, "name": str}, "links": [
        {"link_type": str, "target": {"type": str, "id": str, "name": str},
         "confidence": float, "children": [...]}
    ]}
    """
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row

    source_name = _resolve_element_name(source_type, source_id, conn)
    result = {
        "source": {"type": source_type, "id": source_id, "name": source_name},
        "links": [],
    }

    # BFS traversal
    visited = set()
    visited.add((source_type, source_id))
    queue = deque()
    queue.append((source_type, source_id, result["links"], 0))

    c = conn.cursor()
    while queue:
        curr_type, curr_id, parent_links, depth = queue.popleft()
        if depth >= max_depth:
            continue

        c.execute(
            """SELECT target_type, target_id, link_type, confidence, evidence
               FROM digital_thread_links
               WHERE project_id = ? AND source_type = ? AND source_id = ?""",
            (project_id, curr_type, curr_id),
        )
        rows = c.fetchall()
        for row in rows:
            t_type = row["target_type"]
            t_id = row["target_id"]
            key = (t_type, t_id)

            t_name = _resolve_element_name(t_type, t_id, conn)
            child_links = []
            node = {
                "link_type": row["link_type"],
                "confidence": row["confidence"],
                "target": {"type": t_type, "id": t_id, "name": t_name},
                "children": child_links,
            }
            parent_links.append(node)

            if key not in visited:
                visited.add(key)
                queue.append((t_type, t_id, child_links, depth + 1))

    conn.close()
    return result


# ---------------------------------------------------------------------------
# Trace: backward (BFS)
# ---------------------------------------------------------------------------
def get_backward_trace(project_id: str, target_type: str, target_id: str,
                       max_depth: int = 10, db_path=None) -> dict:
    """Trace backward from a target element. Same tree structure but reversed."""
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row

    target_name = _resolve_element_name(target_type, target_id, conn)
    result = {
        "target": {"type": target_type, "id": target_id, "name": target_name},
        "links": [],
    }

    # BFS traversal backward
    visited = set()
    visited.add((target_type, target_id))
    queue = deque()
    queue.append((target_type, target_id, result["links"], 0))

    c = conn.cursor()
    while queue:
        curr_type, curr_id, parent_links, depth = queue.popleft()
        if depth >= max_depth:
            continue

        c.execute(
            """SELECT source_type, source_id, link_type, confidence, evidence
               FROM digital_thread_links
               WHERE project_id = ? AND target_type = ? AND target_id = ?""",
            (project_id, curr_type, curr_id),
        )
        rows = c.fetchall()
        for row in rows:
            s_type = row["source_type"]
            s_id = row["source_id"]
            key = (s_type, s_id)

            s_name = _resolve_element_name(s_type, s_id, conn)
            child_links = []
            node = {
                "link_type": row["link_type"],
                "confidence": row["confidence"],
                "source": {"type": s_type, "id": s_id, "name": s_name},
                "children": child_links,
            }
            parent_links.append(node)

            if key not in visited:
                visited.add(key)
                queue.append((s_type, s_id, child_links, depth + 1))

    conn.close()
    return result


# ---------------------------------------------------------------------------
# Trace: full bidirectional
# ---------------------------------------------------------------------------
def get_full_thread(project_id: str, element_type: str, element_id: str,
                    db_path=None) -> dict:
    """Complete bidirectional trace from any point. Returns both forward and backward."""
    path = db_path or DB_PATH
    forward = get_forward_trace(project_id, element_type, element_id, db_path=path)
    backward = get_backward_trace(project_id, element_type, element_id, db_path=path)
    conn = sqlite3.connect(str(path))
    name = _resolve_element_name(element_type, element_id, conn)
    conn.close()
    return {
        "element": {"type": element_type, "id": element_id, "name": name},
        "forward": forward.get("links", []),
        "backward": backward.get("links", []),
    }


# ---------------------------------------------------------------------------
# Coverage analysis
# ---------------------------------------------------------------------------
def compute_coverage(project_id: str, db_path=None) -> dict:
    """Coverage metrics across the digital thread.

    - requirement_coverage: % of doors_requirements linked to sysml_elements
    - model_coverage: % of sysml_elements (blocks only) linked to code_modules
    - test_coverage: % of code_modules linked to test_files
    - control_coverage: % of project_controls linked to any thread element
    - overall_thread_completeness: % of requirements with full chain
      (req -> model -> code -> test -> control)
    """
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    c = conn.cursor()

    # Total DOORS requirements for this project
    c.execute("SELECT COUNT(*) FROM doors_requirements WHERE project_id = ?", (project_id,))
    total_reqs = c.fetchone()[0]

    # Requirements linked to sysml_elements
    c.execute(
        """SELECT COUNT(DISTINCT source_id) FROM digital_thread_links
           WHERE project_id = ? AND source_type = 'doors_requirement'
             AND target_type = 'sysml_element'""",
        (project_id,),
    )
    linked_reqs = c.fetchone()[0]

    # Total SysML blocks for this project
    c.execute(
        "SELECT COUNT(*) FROM sysml_elements WHERE project_id = ? AND element_type = 'block'",
        (project_id,),
    )
    total_blocks = c.fetchone()[0]

    # Blocks linked to code_modules
    c.execute(
        """SELECT COUNT(DISTINCT source_id) FROM digital_thread_links
           WHERE project_id = ? AND source_type = 'sysml_element'
             AND target_type = 'code_module'""",
        (project_id,),
    )
    linked_blocks = c.fetchone()[0]

    # Unique code_modules that are link sources or targets
    c.execute(
        """SELECT COUNT(DISTINCT cm) FROM (
               SELECT source_id AS cm FROM digital_thread_links
               WHERE project_id = ? AND source_type = 'code_module'
               UNION
               SELECT target_id AS cm FROM digital_thread_links
               WHERE project_id = ? AND target_type = 'code_module'
           )""",
        (project_id, project_id),
    )
    total_code = c.fetchone()[0]

    # Code modules linked to test_files
    c.execute(
        """SELECT COUNT(DISTINCT source_id) FROM digital_thread_links
           WHERE project_id = ? AND source_type = 'code_module'
             AND target_type = 'test_file'""",
        (project_id,),
    )
    code_with_tests = c.fetchone()[0]

    # Total project_controls
    c.execute("SELECT COUNT(*) FROM project_controls WHERE project_id = ?", (project_id,))
    total_controls = c.fetchone()[0]

    # Controls linked to any thread element
    c.execute(
        """SELECT COUNT(DISTINCT pc.control_id) FROM project_controls pc
           WHERE pc.project_id = ?
             AND (EXISTS (
                 SELECT 1 FROM digital_thread_links dtl
                 WHERE dtl.project_id = pc.project_id
                   AND ((dtl.source_type = 'nist_control' AND dtl.source_id = pc.control_id)
                     OR (dtl.target_type = 'nist_control' AND dtl.target_id = pc.control_id))
             ))""",
        (project_id,),
    )
    linked_controls = c.fetchone()[0]

    # Full chain completeness: requirement -> model -> code -> test -> control
    # For each requirement, check if a full chain exists
    c.execute("SELECT id FROM doors_requirements WHERE project_id = ?", (project_id,))
    req_ids = [row[0] for row in c.fetchall()]
    full_chain_count = 0

    for req_id in req_ids:
        # req -> sysml_element
        c.execute(
            """SELECT target_id FROM digital_thread_links
               WHERE project_id = ? AND source_type = 'doors_requirement'
                 AND source_id = ? AND target_type = 'sysml_element'""",
            (project_id, req_id),
        )
        model_ids = [row[0] for row in c.fetchall()]
        if not model_ids:
            continue

        has_full_chain = False
        for model_id in model_ids:
            # sysml_element -> code_module
            c.execute(
                """SELECT target_id FROM digital_thread_links
                   WHERE project_id = ? AND source_type = 'sysml_element'
                     AND source_id = ? AND target_type = 'code_module'""",
                (project_id, model_id),
            )
            code_ids = [row[0] for row in c.fetchall()]
            if not code_ids:
                continue

            for code_id in code_ids:
                # code_module -> test_file
                c.execute(
                    """SELECT target_id FROM digital_thread_links
                       WHERE project_id = ? AND source_type = 'code_module'
                         AND source_id = ? AND target_type = 'test_file'""",
                    (project_id, code_id),
                )
                test_ids = [row[0] for row in c.fetchall()]
                if not test_ids:
                    continue

                # Any element in chain -> nist_control
                c.execute(
                    """SELECT 1 FROM digital_thread_links
                       WHERE project_id = ?
                         AND target_type = 'nist_control'
                         AND (
                             (source_type = 'doors_requirement' AND source_id = ?)
                             OR (source_type = 'sysml_element' AND source_id = ?)
                             OR (source_type = 'code_module' AND source_id = ?)
                             OR (source_type = 'test_file' AND source_id IN ({}))
                         )
                       LIMIT 1""".format(",".join("?" * len(test_ids))),
                    (project_id, req_id, model_id, code_id, *test_ids),
                )
                if c.fetchone():
                    has_full_chain = True
                    break
                break  # Only need one path
            if has_full_chain:
                break

        if has_full_chain:
            full_chain_count += 1

    def pct(num, denom):
        return round((num / denom) * 100, 2) if denom > 0 else 0.0

    conn.close()
    return {
        "requirement_coverage": pct(linked_reqs, total_reqs),
        "model_coverage": pct(linked_blocks, total_blocks),
        "test_coverage": pct(code_with_tests, total_code),
        "control_coverage": pct(linked_controls, total_controls),
        "overall_thread_completeness": pct(full_chain_count, total_reqs),
        "details": {
            "total_requirements": total_reqs,
            "requirements_linked": linked_reqs,
            "total_blocks": total_blocks,
            "blocks_linked": linked_blocks,
            "total_code_modules": total_code,
            "code_with_tests": code_with_tests,
            "total_controls": total_controls,
            "controls_linked": linked_controls,
            "full_chain_requirements": full_chain_count,
        },
    }


# ---------------------------------------------------------------------------
# Orphan detection
# ---------------------------------------------------------------------------
def find_orphans(project_id: str, db_path=None) -> dict:
    """Find elements with no links in either direction.

    - requirements_without_model: DOORS requirements not linked to any sysml_element
    - blocks_without_code: SysML blocks not linked to any code_module
    - code_without_tests: code_modules not linked to any test_file
    - controls_without_evidence: NIST controls not linked to any thread element
    """
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    c = conn.cursor()

    # Requirements without model links
    c.execute(
        """SELECT dr.id, dr.doors_id, dr.title FROM doors_requirements dr
           WHERE dr.project_id = ?
             AND dr.id NOT IN (
                 SELECT source_id FROM digital_thread_links
                 WHERE project_id = ? AND source_type = 'doors_requirement'
                   AND target_type = 'sysml_element'
             )
             AND dr.id NOT IN (
                 SELECT target_id FROM digital_thread_links
                 WHERE project_id = ? AND target_type = 'doors_requirement'
                   AND source_type = 'sysml_element'
             )""",
        (project_id, project_id, project_id),
    )
    reqs_orphans = [{"id": r[0], "doors_id": r[1], "title": r[2]} for r in c.fetchall()]

    # SysML blocks without code links
    c.execute(
        """SELECT se.id, se.name FROM sysml_elements se
           WHERE se.project_id = ? AND se.element_type = 'block'
             AND se.id NOT IN (
                 SELECT source_id FROM digital_thread_links
                 WHERE project_id = ? AND source_type = 'sysml_element'
                   AND target_type = 'code_module'
             )
             AND se.id NOT IN (
                 SELECT target_id FROM digital_thread_links
                 WHERE project_id = ? AND target_type = 'sysml_element'
                   AND source_type = 'code_module'
             )""",
        (project_id, project_id, project_id),
    )
    blocks_orphans = [{"id": r[0], "name": r[1]} for r in c.fetchall()]

    # Code modules without test links
    # Gather all code_modules that appear in links for this project
    c.execute(
        """SELECT DISTINCT cm FROM (
               SELECT source_id AS cm FROM digital_thread_links
               WHERE project_id = ? AND source_type = 'code_module'
               UNION
               SELECT target_id AS cm FROM digital_thread_links
               WHERE project_id = ? AND target_type = 'code_module'
           )""",
        (project_id, project_id),
    )
    all_code = [row[0] for row in c.fetchall()]

    code_orphans = []
    for code_id in all_code:
        c.execute(
            """SELECT 1 FROM digital_thread_links
               WHERE project_id = ?
                 AND ((source_type = 'code_module' AND source_id = ? AND target_type = 'test_file')
                   OR (target_type = 'code_module' AND target_id = ? AND source_type = 'test_file'))
               LIMIT 1""",
            (project_id, code_id, code_id),
        )
        if not c.fetchone():
            code_orphans.append({"id": code_id})

    # NIST controls without evidence (no links at all)
    c.execute(
        """SELECT pc.control_id FROM project_controls pc
           WHERE pc.project_id = ?
             AND NOT EXISTS (
                 SELECT 1 FROM digital_thread_links dtl
                 WHERE dtl.project_id = pc.project_id
                   AND ((dtl.source_type = 'nist_control' AND dtl.source_id = pc.control_id)
                     OR (dtl.target_type = 'nist_control' AND dtl.target_id = pc.control_id))
             )""",
        (project_id,),
    )
    control_orphans = [{"control_id": r[0]} for r in c.fetchall()]

    conn.close()
    return {
        "requirements_without_model": {
            "count": len(reqs_orphans),
            "items": reqs_orphans,
        },
        "blocks_without_code": {
            "count": len(blocks_orphans),
            "items": blocks_orphans,
        },
        "code_without_tests": {
            "count": len(code_orphans),
            "items": code_orphans,
        },
        "controls_without_evidence": {
            "count": len(control_orphans),
            "items": control_orphans,
        },
    }


# ---------------------------------------------------------------------------
# Gap detection
# ---------------------------------------------------------------------------
def find_gaps(project_id: str, db_path=None) -> dict:
    """Find missing links in expected chains.

    - requirement has model link but model has no code link
    - model has code link but code has no test link
    - code has test link but no control link
    """
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    c = conn.cursor()
    gaps = []

    # Gap 1: requirement -> model exists, but model -> code missing
    c.execute(
        """SELECT dtl.source_id, dtl.target_id
           FROM digital_thread_links dtl
           WHERE dtl.project_id = ?
             AND dtl.source_type = 'doors_requirement'
             AND dtl.target_type = 'sysml_element'""",
        (project_id,),
    )
    req_model_links = c.fetchall()
    for req_id, model_id in req_model_links:
        c.execute(
            """SELECT 1 FROM digital_thread_links
               WHERE project_id = ? AND source_type = 'sysml_element'
                 AND source_id = ? AND target_type = 'code_module'
               LIMIT 1""",
            (project_id, model_id),
        )
        if not c.fetchone():
            req_name = _resolve_element_name("doors_requirement", req_id, conn)
            model_name = _resolve_element_name("sysml_element", model_id, conn)
            gaps.append({
                "gap_type": "model_without_code",
                "description": (
                    f"Requirement '{req_name}' ({req_id}) traces to model "
                    f"'{model_name}' ({model_id}), but model has no code link"
                ),
                "requirement_id": req_id,
                "model_id": model_id,
                "missing_link": "sysml_element -> code_module",
            })

    # Gap 2: model -> code exists, but code -> test missing
    c.execute(
        """SELECT dtl.source_id, dtl.target_id
           FROM digital_thread_links dtl
           WHERE dtl.project_id = ?
             AND dtl.source_type = 'sysml_element'
             AND dtl.target_type = 'code_module'""",
        (project_id,),
    )
    model_code_links = c.fetchall()
    for model_id, code_id in model_code_links:
        c.execute(
            """SELECT 1 FROM digital_thread_links
               WHERE project_id = ? AND source_type = 'code_module'
                 AND source_id = ? AND target_type = 'test_file'
               LIMIT 1""",
            (project_id, code_id),
        )
        if not c.fetchone():
            model_name = _resolve_element_name("sysml_element", model_id, conn)
            gaps.append({
                "gap_type": "code_without_test",
                "description": (
                    f"Model '{model_name}' ({model_id}) traces to code "
                    f"'{code_id}', but code has no test link"
                ),
                "model_id": model_id,
                "code_id": code_id,
                "missing_link": "code_module -> test_file",
            })

    # Gap 3: code -> test exists, but no control link from any chain element
    c.execute(
        """SELECT dtl.source_id, dtl.target_id
           FROM digital_thread_links dtl
           WHERE dtl.project_id = ?
             AND dtl.source_type = 'code_module'
             AND dtl.target_type = 'test_file'""",
        (project_id,),
    )
    code_test_links = c.fetchall()
    for code_id, test_id in code_test_links:
        c.execute(
            """SELECT 1 FROM digital_thread_links
               WHERE project_id = ?
                 AND target_type = 'nist_control'
                 AND (
                     (source_type = 'code_module' AND source_id = ?)
                     OR (source_type = 'test_file' AND source_id = ?)
                 )
               LIMIT 1""",
            (project_id, code_id, test_id),
        )
        if not c.fetchone():
            gaps.append({
                "gap_type": "test_without_control",
                "description": (
                    f"Code '{code_id}' has test '{test_id}', "
                    f"but neither is linked to a NIST control"
                ),
                "code_id": code_id,
                "test_id": test_id,
                "missing_link": "code_module/test_file -> nist_control",
            })

    conn.close()
    return {
        "total_gaps": len(gaps),
        "gaps": gaps,
    }


# ---------------------------------------------------------------------------
# Auto-link by name matching
# ---------------------------------------------------------------------------
def _camel_to_snake(name: str) -> str:
    """Convert CamelCase to snake_case."""
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()


def _snake_to_camel(name: str) -> str:
    """Convert snake_case to CamelCase."""
    return "".join(word.capitalize() for word in name.split("_"))


def auto_link_by_name(project_id: str, db_path=None) -> dict:
    """Heuristic auto-linking by name matching.

    1. Match SysML block names to Python class names (case-insensitive,
       underscore/camel conversion)
    2. Match SysML block names to file names (snake_case conversion)
    3. Match requirement IDs found in code comments to DOORS requirements
    4. Match requirement IDs found in test names to DOORS requirements

    Creates links with confidence 0.7 and evidence="auto_linked_by_name_match"
    """
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    c = conn.cursor()
    matches = []
    links_created = 0

    # 1. Get all SysML blocks for this project
    c.execute(
        """SELECT id, name FROM sysml_elements
           WHERE project_id = ? AND element_type = 'block'""",
        (project_id,),
    )
    blocks = c.fetchall()

    # 2. Get all code mappings for this project (model_code_mappings)
    c.execute(
        """SELECT code_path, code_type FROM model_code_mappings
           WHERE project_id = ?""",
        (project_id,),
    )
    code_paths = c.fetchall()

    # 3. Get all DOORS requirements
    c.execute(
        """SELECT id, doors_id, title FROM doors_requirements
           WHERE project_id = ?""",
        (project_id,),
    )
    requirements = c.fetchall()

    # Strategy 1 & 2: Match SysML block names to code paths
    for block_id, block_name in blocks:
        snake_name = _camel_to_snake(block_name)
        lower_name = block_name.lower()

        for code_path_row in code_paths:
            code_path = code_path_row[0]
            file_stem = Path(code_path).stem.lower()
            file_name = Path(code_path).name.lower()

            # Match block name (snake_case) to file stem
            if snake_name == file_stem or lower_name == file_stem:
                result = create_link(
                    project_id=project_id,
                    source_type="sysml_element",
                    source_id=block_id,
                    target_type="code_module",
                    target_id=code_path,
                    link_type="implements",
                    confidence=0.7,
                    evidence="auto_linked_by_name_match",
                    created_by="icdev-auto-linker",
                    db_path=path,
                )
                if result.get("created") or result.get("id"):
                    links_created += 1
                    matches.append({
                        "type": "block_to_code",
                        "block_id": block_id,
                        "block_name": block_name,
                        "code_path": code_path,
                        "match_method": "name_match",
                    })

            # Match CamelCase block name to file name containing it
            camel_lower = block_name.lower().replace("_", "")
            stem_lower = file_stem.replace("_", "")
            if camel_lower == stem_lower and camel_lower:
                result = create_link(
                    project_id=project_id,
                    source_type="sysml_element",
                    source_id=block_id,
                    target_type="code_module",
                    target_id=code_path,
                    link_type="implements",
                    confidence=0.7,
                    evidence="auto_linked_by_name_match",
                    created_by="icdev-auto-linker",
                    db_path=path,
                )
                if result.get("created") or result.get("id"):
                    links_created += 1
                    matches.append({
                        "type": "block_to_code_camel",
                        "block_id": block_id,
                        "block_name": block_name,
                        "code_path": code_path,
                        "match_method": "camel_case_match",
                    })

    # Strategy 3 & 4: Match requirement IDs in code/test paths
    # Build a lookup of doors_id -> internal id
    req_lookup = {}
    for req_id, doors_id, title in requirements:
        req_lookup[doors_id.lower()] = req_id
        # Also try matching the internal id
        req_lookup[req_id.lower()] = req_id

    for code_path_row in code_paths:
        code_path = code_path_row[0]
        code_type = code_path_row[1]
        file_str = Path(code_path).name.lower()

        for doors_id_lower, req_internal_id in req_lookup.items():
            # Normalize doors_id for filename matching (e.g., REQ-001 -> req_001 or req001)
            normalized = doors_id_lower.replace("-", "_").replace(" ", "_")
            normalized_no_sep = doors_id_lower.replace("-", "").replace("_", "").replace(" ", "")

            if normalized in file_str or normalized_no_sep in file_str:
                if code_type == "test":
                    # Test file references requirement
                    result = create_link(
                        project_id=project_id,
                        source_type="test_file",
                        source_id=code_path,
                        target_type="doors_requirement",
                        target_id=req_internal_id,
                        link_type="verifies",
                        confidence=0.7,
                        evidence="auto_linked_by_name_match",
                        created_by="icdev-auto-linker",
                        db_path=path,
                    )
                else:
                    # Code module references requirement
                    result = create_link(
                        project_id=project_id,
                        source_type="code_module",
                        source_id=code_path,
                        target_type="doors_requirement",
                        target_id=req_internal_id,
                        link_type="implements",
                        confidence=0.7,
                        evidence="auto_linked_by_name_match",
                        created_by="icdev-auto-linker",
                        db_path=path,
                    )
                if result.get("created") or result.get("id"):
                    links_created += 1
                    matches.append({
                        "type": "req_in_filename",
                        "code_path": code_path,
                        "requirement_id": req_internal_id,
                        "doors_id": doors_id_lower,
                        "match_method": "requirement_id_in_filename",
                    })

    conn.close()
    return {
        "links_created": links_created,
        "matches": matches,
    }


# ---------------------------------------------------------------------------
# Auto-link to NIST controls by keyword
# ---------------------------------------------------------------------------
def auto_link_to_controls(project_id: str, db_path=None) -> dict:
    """Auto-map model elements to NIST controls by element type/stereotype.

    - Elements with 'auth', 'access', 'login' -> AC family
    - Elements with 'audit', 'log' -> AU family
    - Elements with 'encrypt', 'crypto', 'tls' -> SC family
    - Elements with 'identity', 'authenticate' -> IA family

    Creates links with confidence 0.6 and evidence="auto_linked_by_keyword_match"
    """
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    c = conn.cursor()
    mappings = []
    links_created = 0

    # Get all SysML elements for this project
    c.execute(
        """SELECT id, name, element_type, stereotype, description
           FROM sysml_elements WHERE project_id = ?""",
        (project_id,),
    )
    elements = c.fetchall()

    # Get available NIST controls for this project
    c.execute(
        """SELECT DISTINCT pc.control_id, cc.family, cc.title
           FROM project_controls pc
           JOIN compliance_controls cc ON pc.control_id = cc.id
           WHERE pc.project_id = ?""",
        (project_id,),
    )
    controls = c.fetchall()

    # Build family -> control_ids lookup
    family_controls = {}
    for ctrl_id, family, title in controls:
        family_controls.setdefault(family, []).append((ctrl_id, title))

    for elem_id, elem_name, elem_type, stereotype, description in elements:
        # Combine searchable text
        search_text = " ".join(
            s.lower() for s in [elem_name or "", stereotype or "", description or ""]
        )

        for family, keywords in CONTROL_KEYWORD_MAP.items():
            if family not in family_controls:
                continue

            matched_keywords = [kw for kw in keywords if kw in search_text]
            if not matched_keywords:
                continue

            # Link to all controls in the matched family
            for ctrl_id, ctrl_title in family_controls[family]:
                result = create_link(
                    project_id=project_id,
                    source_type="sysml_element",
                    source_id=elem_id,
                    target_type="nist_control",
                    target_id=ctrl_id,
                    link_type="maps_to",
                    confidence=0.6,
                    evidence="auto_linked_by_keyword_match",
                    created_by="icdev-auto-linker",
                    db_path=path,
                )
                if result.get("created") or result.get("id"):
                    links_created += 1
                    mappings.append({
                        "element_id": elem_id,
                        "element_name": elem_name,
                        "control_id": ctrl_id,
                        "control_title": ctrl_title,
                        "control_family": family,
                        "matched_keywords": matched_keywords,
                    })

    conn.close()
    return {
        "links_created": links_created,
        "mappings": mappings,
    }


# ---------------------------------------------------------------------------
# Traceability report (CUI-marked markdown)
# ---------------------------------------------------------------------------
def generate_traceability_report(project_id: str, db_path=None) -> str:
    """Generate full digital thread report as CUI-marked markdown.

    Includes coverage summary, orphan analysis, gap analysis,
    complete trace chains, and recommendations.
    """
    path = db_path or DB_PATH
    timestamp = datetime.now().isoformat()

    coverage = compute_coverage(project_id, db_path=path)
    orphans = find_orphans(project_id, db_path=path)
    gaps = find_gaps(project_id, db_path=path)
    integrity = validate_thread_integrity(project_id, db_path=path)

    # Collect all trace chains from requirements
    conn = sqlite3.connect(str(path))
    c = conn.cursor()
    c.execute("SELECT id, doors_id, title FROM doors_requirements WHERE project_id = ?",
              (project_id,))
    all_reqs = c.fetchall()

    c.execute("SELECT COUNT(*) FROM digital_thread_links WHERE project_id = ?", (project_id,))
    total_links = c.fetchone()[0]
    conn.close()

    # Build report
    lines = [
        "CUI // SP-CTI",
        "",
        "# Digital Thread Traceability Report",
        "",
        f"**Project:** {project_id}",
        f"**Generated:** {timestamp}",
        f"**Classification:** CUI // SP-CTI",
        "",
        "---",
        "",
        "## 1. Coverage Summary",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Requirement Coverage | {coverage['requirement_coverage']}% |",
        f"| Model Coverage | {coverage['model_coverage']}% |",
        f"| Test Coverage | {coverage['test_coverage']}% |",
        f"| Control Coverage | {coverage['control_coverage']}% |",
        f"| Overall Thread Completeness | {coverage['overall_thread_completeness']}% |",
        f"| Total Digital Thread Links | {total_links} |",
        "",
        "### Coverage Details",
        "",
    ]

    details = coverage.get("details", {})
    lines.append(f"- Requirements: {details.get('requirements_linked', 0)}/{details.get('total_requirements', 0)} linked to models")
    lines.append(f"- Model Blocks: {details.get('blocks_linked', 0)}/{details.get('total_blocks', 0)} linked to code")
    lines.append(f"- Code Modules: {details.get('code_with_tests', 0)}/{details.get('total_code_modules', 0)} linked to tests")
    lines.append(f"- Controls: {details.get('controls_linked', 0)}/{details.get('total_controls', 0)} linked to thread")
    lines.append(f"- Full Chain (req->model->code->test->control): {details.get('full_chain_requirements', 0)}/{details.get('total_requirements', 0)}")
    lines.append("")

    # Orphan analysis
    lines.append("---")
    lines.append("")
    lines.append("## 2. Orphan Analysis")
    lines.append("")

    total_orphans = (
        orphans["requirements_without_model"]["count"]
        + orphans["blocks_without_code"]["count"]
        + orphans["code_without_tests"]["count"]
        + orphans["controls_without_evidence"]["count"]
    )
    lines.append(f"**Total orphaned elements:** {total_orphans}")
    lines.append("")

    lines.append(f"### Requirements Without Model ({orphans['requirements_without_model']['count']})")
    if orphans["requirements_without_model"]["items"]:
        for item in orphans["requirements_without_model"]["items"]:
            lines.append(f"- `{item.get('doors_id', item['id'])}`: {item.get('title', 'N/A')}")
    else:
        lines.append("- None (all requirements traced)")
    lines.append("")

    lines.append(f"### Blocks Without Code ({orphans['blocks_without_code']['count']})")
    if orphans["blocks_without_code"]["items"]:
        for item in orphans["blocks_without_code"]["items"]:
            lines.append(f"- `{item['id']}`: {item.get('name', 'N/A')}")
    else:
        lines.append("- None (all blocks traced)")
    lines.append("")

    lines.append(f"### Code Without Tests ({orphans['code_without_tests']['count']})")
    if orphans["code_without_tests"]["items"]:
        for item in orphans["code_without_tests"]["items"]:
            lines.append(f"- `{item['id']}`")
    else:
        lines.append("- None (all code modules have tests)")
    lines.append("")

    lines.append(f"### Controls Without Evidence ({orphans['controls_without_evidence']['count']})")
    if orphans["controls_without_evidence"]["items"]:
        for item in orphans["controls_without_evidence"]["items"]:
            lines.append(f"- `{item['control_id']}`")
    else:
        lines.append("- None (all controls have thread links)")
    lines.append("")

    # Gap analysis
    lines.append("---")
    lines.append("")
    lines.append("## 3. Gap Analysis")
    lines.append("")
    lines.append(f"**Total gaps found:** {gaps['total_gaps']}")
    lines.append("")

    if gaps["gaps"]:
        for i, gap in enumerate(gaps["gaps"], 1):
            lines.append(f"**Gap {i}** ({gap['gap_type']}): {gap['description']}")
            lines.append(f"  - Missing link: `{gap['missing_link']}`")
            lines.append("")
    else:
        lines.append("No gaps detected. All chains are complete.")
        lines.append("")

    # Trace chains
    lines.append("---")
    lines.append("")
    lines.append("## 4. Requirement Trace Chains")
    lines.append("")

    for req_id, doors_id, title in all_reqs[:50]:  # Limit to 50 for report size
        trace = get_forward_trace(project_id, "doors_requirement", req_id, max_depth=5, db_path=path)
        chain_depth = _count_chain_depth(trace.get("links", []))
        status = "COMPLETE" if chain_depth >= 4 else f"PARTIAL (depth {chain_depth})"
        lines.append(f"- **{doors_id}** ({title}): {status}")

    if len(all_reqs) > 50:
        lines.append(f"  - ... and {len(all_reqs) - 50} more requirements")
    lines.append("")

    # Integrity
    lines.append("---")
    lines.append("")
    lines.append("## 5. Thread Integrity")
    lines.append("")
    lines.append(f"**Valid:** {'YES' if integrity['valid'] else 'NO'}")
    lines.append(f"**Issues found:** {len(integrity['issues'])}")
    lines.append("")

    if integrity["issues"]:
        for issue in integrity["issues"][:20]:
            lines.append(f"- [{issue['severity']}] {issue['description']}")
        if len(integrity["issues"]) > 20:
            lines.append(f"- ... and {len(integrity['issues']) - 20} more issues")
    lines.append("")

    # Recommendations
    lines.append("---")
    lines.append("")
    lines.append("## 6. Recommendations")
    lines.append("")

    recommendations = []
    if coverage["requirement_coverage"] < 100:
        recommendations.append(
            f"Link remaining {details.get('total_requirements', 0) - details.get('requirements_linked', 0)} "
            f"requirements to SysML model elements"
        )
    if coverage["model_coverage"] < 100:
        recommendations.append(
            f"Map remaining {details.get('total_blocks', 0) - details.get('blocks_linked', 0)} "
            f"SysML blocks to code modules"
        )
    if coverage["test_coverage"] < 100:
        recommendations.append(
            f"Write tests for {details.get('total_code_modules', 0) - details.get('code_with_tests', 0)} "
            f"untested code modules"
        )
    if coverage["control_coverage"] < 100:
        recommendations.append(
            f"Map remaining {details.get('total_controls', 0) - details.get('controls_linked', 0)} "
            f"NIST controls to thread elements"
        )
    if not integrity["valid"]:
        recommendations.append("Resolve thread integrity issues before ATO submission")
    if gaps["total_gaps"] > 0:
        recommendations.append(f"Address {gaps['total_gaps']} chain gaps to improve completeness")

    if recommendations:
        for i, rec in enumerate(recommendations, 1):
            lines.append(f"{i}. {rec}")
    else:
        lines.append("No recommendations. Digital thread is complete.")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("CUI // SP-CTI")

    return "\n".join(lines)


def _count_chain_depth(links: list, depth: int = 0) -> int:
    """Count the maximum depth of a trace chain."""
    if not links:
        return depth
    max_d = depth
    for link in links:
        child_depth = _count_chain_depth(link.get("children", []), depth + 1)
        if child_depth > max_d:
            max_d = child_depth
    return max_d


# ---------------------------------------------------------------------------
# Thread integrity validation
# ---------------------------------------------------------------------------
def validate_thread_integrity(project_id: str, db_path=None) -> dict:
    """Check for data integrity issues.

    - Broken links (source/target IDs that don't exist in their tables)
    - Circular references
    - Duplicate links
    - Invalid types
    """
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    c = conn.cursor()
    issues = []

    # Get all links for this project
    c.execute(
        """SELECT id, source_type, source_id, target_type, target_id, link_type, confidence
           FROM digital_thread_links WHERE project_id = ?""",
        (project_id,),
    )
    all_links = c.fetchall()

    # Check 1: Invalid types
    for link_id, src_type, src_id, tgt_type, tgt_id, ltype, conf in all_links:
        if src_type not in VALID_TYPES:
            issues.append({
                "severity": "error",
                "type": "invalid_source_type",
                "link_id": link_id,
                "description": f"Link {link_id}: invalid source_type '{src_type}'",
            })
        if tgt_type not in VALID_TYPES:
            issues.append({
                "severity": "error",
                "type": "invalid_target_type",
                "link_id": link_id,
                "description": f"Link {link_id}: invalid target_type '{tgt_type}'",
            })
        if ltype not in VALID_LINK_TYPES:
            issues.append({
                "severity": "error",
                "type": "invalid_link_type",
                "link_id": link_id,
                "description": f"Link {link_id}: invalid link_type '{ltype}'",
            })

    # Check 2: Broken links (source/target IDs not in their tables)
    type_table_map = {
        "doors_requirement": ("doors_requirements", "id"),
        "sysml_element": ("sysml_elements", "id"),
        "nist_control": ("compliance_controls", "id"),
        "stig_rule": ("stig_findings", "rule_id"),
    }

    for link_id, src_type, src_id, tgt_type, tgt_id, ltype, conf in all_links:
        # Check source exists
        if src_type in type_table_map:
            table, col = type_table_map[src_type]
            try:
                c.execute(f"SELECT 1 FROM {table} WHERE {col} = ? LIMIT 1", (src_id,))
                if not c.fetchone():
                    issues.append({
                        "severity": "warning",
                        "type": "broken_source_link",
                        "link_id": link_id,
                        "description": (
                            f"Link {link_id}: source {src_type} '{src_id}' "
                            f"not found in {table}"
                        ),
                    })
            except sqlite3.OperationalError:
                pass  # Table might not exist

        # Check target exists
        if tgt_type in type_table_map:
            table, col = type_table_map[tgt_type]
            try:
                c.execute(f"SELECT 1 FROM {table} WHERE {col} = ? LIMIT 1", (tgt_id,))
                if not c.fetchone():
                    issues.append({
                        "severity": "warning",
                        "type": "broken_target_link",
                        "link_id": link_id,
                        "description": (
                            f"Link {link_id}: target {tgt_type} '{tgt_id}' "
                            f"not found in {table}"
                        ),
                    })
            except sqlite3.OperationalError:
                pass  # Table might not exist

    # Check 3: Circular references (detect cycles using DFS)
    # Build adjacency list
    adj = {}
    for link_id, src_type, src_id, tgt_type, tgt_id, ltype, conf in all_links:
        src_key = (src_type, src_id)
        tgt_key = (tgt_type, tgt_id)
        adj.setdefault(src_key, []).append((tgt_key, link_id))

    visited = set()
    in_stack = set()
    cycle_links = set()

    def dfs(node):
        visited.add(node)
        in_stack.add(node)
        for neighbor, lid in adj.get(node, []):
            if neighbor in in_stack:
                cycle_links.add(lid)
            elif neighbor not in visited:
                dfs(neighbor)
        in_stack.discard(node)

    for node in adj:
        if node not in visited:
            dfs(node)

    for lid in cycle_links:
        issues.append({
            "severity": "warning",
            "type": "circular_reference",
            "link_id": lid,
            "description": f"Link {lid}: participates in a circular reference chain",
        })

    # Check 4: Duplicate links (same source+target+link_type, different IDs)
    # The UNIQUE constraint should prevent this, but check anyway
    seen_combos = {}
    for link_id, src_type, src_id, tgt_type, tgt_id, ltype, conf in all_links:
        combo = (src_type, src_id, tgt_type, tgt_id, ltype)
        if combo in seen_combos:
            issues.append({
                "severity": "info",
                "type": "duplicate_link",
                "link_id": link_id,
                "description": (
                    f"Link {link_id}: duplicate of link {seen_combos[combo]} "
                    f"({src_type}:{src_id} -> {tgt_type}:{tgt_id} [{ltype}])"
                ),
            })
        else:
            seen_combos[combo] = link_id

    conn.close()
    valid = all(issue["severity"] != "error" for issue in issues)
    return {
        "valid": valid,
        "total_links": len(all_links),
        "issues_count": len(issues),
        "issues": issues,
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="ICDEV Digital Thread Engine -- end-to-end traceability"
    )
    parser.add_argument("--project-id", required=True, help="Project identifier")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--db-path", type=Path, help="Override database path")

    sub = parser.add_subparsers(dest="command")

    # create-link
    link_p = sub.add_parser("create-link", help="Create a digital thread link")
    link_p.add_argument("--source-type", required=True, choices=VALID_TYPES,
                        help="Source element type")
    link_p.add_argument("--source-id", required=True, help="Source element ID")
    link_p.add_argument("--target-type", required=True, choices=VALID_TYPES,
                        help="Target element type")
    link_p.add_argument("--target-id", required=True, help="Target element ID")
    link_p.add_argument("--link-type", required=True, choices=VALID_LINK_TYPES,
                        help="Relationship type")
    link_p.add_argument("--evidence", help="Evidence for the link")
    link_p.add_argument("--confidence", type=float, default=1.0,
                        help="Confidence score 0.0-1.0 (default: 1.0)")

    # delete-link
    del_p = sub.add_parser("delete-link", help="Delete a digital thread link by ID")
    del_p.add_argument("--link-id", required=True, type=int, help="Link ID to delete")

    # trace-forward
    fwd_p = sub.add_parser("trace-forward", help="Forward trace from an element")
    fwd_p.add_argument("--source-type", required=True, choices=VALID_TYPES,
                        help="Source element type")
    fwd_p.add_argument("--source-id", required=True, help="Source element ID")
    fwd_p.add_argument("--max-depth", type=int, default=10,
                        help="Maximum traversal depth (default: 10)")

    # trace-backward
    bwd_p = sub.add_parser("trace-backward", help="Backward trace to an element")
    bwd_p.add_argument("--target-type", required=True, choices=VALID_TYPES,
                        help="Target element type")
    bwd_p.add_argument("--target-id", required=True, help="Target element ID")
    bwd_p.add_argument("--max-depth", type=int, default=10,
                        help="Maximum traversal depth (default: 10)")

    # full-thread
    full_p = sub.add_parser("full-thread", help="Bidirectional trace from an element")
    full_p.add_argument("--element-type", required=True, choices=VALID_TYPES,
                        help="Element type")
    full_p.add_argument("--element-id", required=True, help="Element ID")

    # coverage
    sub.add_parser("coverage", help="Compute digital thread coverage metrics")

    # orphans
    sub.add_parser("orphans", help="Find elements with no links")

    # gaps
    sub.add_parser("gaps", help="Find missing links in expected chains")

    # auto-link
    sub.add_parser("auto-link", help="Auto-link elements by name matching")

    # auto-link-controls
    sub.add_parser("auto-link-controls",
                    help="Auto-map elements to NIST controls by keyword")

    # report
    sub.add_parser("report", help="Generate full traceability report")

    # validate
    sub.add_parser("validate", help="Validate thread integrity")

    args = parser.parse_args()
    db = args.db_path or DB_PATH

    if not args.command:
        parser.print_help()
        sys.exit(1)

    print("CUI // SP-CTI")
    print("")

    if args.command == "create-link":
        result = create_link(
            project_id=args.project_id,
            source_type=args.source_type,
            source_id=args.source_id,
            target_type=args.target_type,
            target_id=args.target_id,
            link_type=args.link_type,
            evidence=args.evidence,
            confidence=args.confidence,
            db_path=db,
        )
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            if "error" in result:
                print(f"ERROR: {result['error']}")
            else:
                print(f"Link created: ID={result['id']}")
                print(f"  {args.source_type}:{args.source_id} -> {args.target_type}:{args.target_id}")
                print(f"  Type: {args.link_type} | Confidence: {args.confidence}")

    elif args.command == "delete-link":
        deleted = delete_link(args.project_id, args.link_id, db_path=db)
        if args.json:
            print(json.dumps({"deleted": deleted}, indent=2))
        else:
            if deleted:
                print(f"Link {args.link_id} deleted.")
            else:
                print(f"Link {args.link_id} not found or could not be deleted.")

    elif args.command == "trace-forward":
        result = get_forward_trace(
            project_id=args.project_id,
            source_type=args.source_type,
            source_id=args.source_id,
            max_depth=args.max_depth,
            db_path=db,
        )
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            _print_forward_tree(result)

    elif args.command == "trace-backward":
        result = get_backward_trace(
            project_id=args.project_id,
            target_type=args.target_type,
            target_id=args.target_id,
            max_depth=args.max_depth,
            db_path=db,
        )
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            _print_backward_tree(result)

    elif args.command == "full-thread":
        result = get_full_thread(
            project_id=args.project_id,
            element_type=args.element_type,
            element_id=args.element_id,
            db_path=db,
        )
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            elem = result["element"]
            print(f"Full Thread: {elem['type']}:{elem['id']} ({elem['name']})")
            print("")
            print("--- Forward Trace ---")
            _print_links(result.get("forward", []), indent=2)
            print("")
            print("--- Backward Trace ---")
            _print_links(result.get("backward", []), indent=2, direction="backward")

    elif args.command == "coverage":
        result = compute_coverage(args.project_id, db_path=db)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print("Digital Thread Coverage")
            print("=" * 40)
            print(f"  Requirement Coverage:     {result['requirement_coverage']}%")
            print(f"  Model Coverage:           {result['model_coverage']}%")
            print(f"  Test Coverage:            {result['test_coverage']}%")
            print(f"  Control Coverage:         {result['control_coverage']}%")
            print(f"  Overall Completeness:     {result['overall_thread_completeness']}%")

    elif args.command == "orphans":
        result = find_orphans(args.project_id, db_path=db)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print("Orphan Analysis")
            print("=" * 40)
            for category, data in result.items():
                print(f"\n  {category}: {data['count']}")
                for item in data["items"][:10]:
                    label = item.get("title") or item.get("name") or item.get("control_id") or item.get("id")
                    print(f"    - {label}")
                if data["count"] > 10:
                    print(f"    ... and {data['count'] - 10} more")

    elif args.command == "gaps":
        result = find_gaps(args.project_id, db_path=db)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Gap Analysis: {result['total_gaps']} gaps found")
            print("=" * 40)
            for gap in result["gaps"]:
                print(f"\n  [{gap['gap_type']}] {gap['description']}")
                print(f"    Missing: {gap['missing_link']}")

    elif args.command == "auto-link":
        result = auto_link_by_name(args.project_id, db_path=db)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Auto-Link by Name: {result['links_created']} links created")
            for m in result["matches"]:
                print(f"  - [{m.get('match_method', 'unknown')}] {m}")

    elif args.command == "auto-link-controls":
        result = auto_link_to_controls(args.project_id, db_path=db)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Auto-Link to Controls: {result['links_created']} links created")
            for m in result["mappings"]:
                print(f"  - {m['element_name']} -> {m['control_id']} ({m['control_family']})")
                print(f"    Keywords: {', '.join(m['matched_keywords'])}")

    elif args.command == "report":
        report = generate_traceability_report(args.project_id, db_path=db)
        print(report)

    elif args.command == "validate":
        result = validate_thread_integrity(args.project_id, db_path=db)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            status = "VALID" if result["valid"] else "INVALID"
            print(f"Thread Integrity: {status}")
            print(f"  Total links: {result['total_links']}")
            print(f"  Issues: {result['issues_count']}")
            for issue in result["issues"]:
                print(f"  - [{issue['severity']}] {issue['description']}")

    print("")
    print("CUI // SP-CTI")


# ---------------------------------------------------------------------------
# Pretty-print helpers for CLI
# ---------------------------------------------------------------------------
def _print_forward_tree(result: dict):
    """Print a forward trace result as an indented tree."""
    src = result.get("source", {})
    print(f"Forward Trace: {src['type']}:{src['id']} ({src.get('name', '')})")
    print("")
    _print_links(result.get("links", []), indent=2)


def _print_backward_tree(result: dict):
    """Print a backward trace result as an indented tree."""
    tgt = result.get("target", {})
    print(f"Backward Trace: {tgt['type']}:{tgt['id']} ({tgt.get('name', '')})")
    print("")
    _print_links(result.get("links", []), indent=2, direction="backward")


def _print_links(links: list, indent: int = 0, direction: str = "forward"):
    """Recursively print trace links."""
    prefix = " " * indent
    for link in links:
        lt = link.get("link_type", "?")
        conf = link.get("confidence", 1.0)

        if direction == "forward":
            target = link.get("target", {})
            label = f"{target.get('type', '?')}:{target.get('id', '?')} ({target.get('name', '')})"
            print(f"{prefix}--[{lt} ({conf})]-> {label}")
            _print_links(link.get("children", []), indent + 4, direction)
        else:
            source = link.get("source", {})
            label = f"{source.get('type', '?')}:{source.get('id', '?')} ({source.get('name', '')})"
            print(f"{prefix}<-[{lt} ({conf})]-- {label}")
            _print_links(link.get("children", []), indent + 4, direction)


if __name__ == "__main__":
    main()
# CUI // SP-CTI
