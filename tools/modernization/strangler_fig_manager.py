# [TEMPLATE: CUI // SP-CTI]
#!/usr/bin/env python3
"""Strangler Fig Pattern Migration Manager for ICDEV DoD Modernization.

Manages incremental legacy-to-modern cutover using the strangler fig pattern.
Tracks per-component migration status, generates API gateway routing configs,
produces anti-corruption layer (ACL) adapter code, and validates coexistence
health between legacy and modern systems during the transition period.

The strangler fig pattern allows legacy systems to be incrementally replaced
by routing requests through a facade that dispatches to either the legacy or
modern implementation on a per-component basis. As each component is migrated
and validated, traffic is shifted from legacy to modern until the old system
can be fully decommissioned.

Usage:
    # Initialize strangler fig tracking for a migration plan
    python tools/modernization/strangler_fig_manager.py \\
        --plan-id mplan-abc123 --create

    # Check cutover status dashboard
    python tools/modernization/strangler_fig_manager.py \\
        --plan-id mplan-abc123 --status

    # Track a component cutover to parallel mode
    python tools/modernization/strangler_fig_manager.py \\
        --plan-id mplan-abc123 --cutover --component-id lcomp-xyz789 --to parallel

    # Generate routing configuration
    python tools/modernization/strangler_fig_manager.py \\
        --plan-id mplan-abc123 --routing --json

    # Check coexistence health
    python tools/modernization/strangler_fig_manager.py \\
        --plan-id mplan-abc123 --health

    # Generate cutover checklist for a component
    python tools/modernization/strangler_fig_manager.py \\
        --plan-id mplan-abc123 --checklist --component-id lcomp-xyz789

    # Execute cutover (mark as modern)
    python tools/modernization/strangler_fig_manager.py \\
        --plan-id mplan-abc123 --execute-cutover --component-id lcomp-xyz789

Classification: CUI // SP-CTI
"""

import argparse
import collections
import json
import sqlite3
import textwrap
import uuid
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

# Valid cutover statuses for the strangler fig lifecycle
CUTOVER_STATUSES = ("legacy", "parallel", "modern", "decommissioned")

# Mapping from strangler fig cutover status to migration_task status
CUTOVER_TO_TASK_STATUS = {
    "legacy": "pending",
    "parallel": "in_progress",
    "modern": "completed",
    "decommissioned": "completed",
}

# Classification banner for generated artifacts
CUI_BANNER = "CUI // SP-CTI"


# ---------------------------------------------------------------------------
# Database helper
# ---------------------------------------------------------------------------

def _get_db():
    """Return a sqlite3 connection to the ICDEV operational database.

    The database file must already exist (created by tools/db/init_icdev_db.py).
    Uses row_factory = sqlite3.Row for dict-like access.
    """
    if not DB_PATH.exists():
        raise FileNotFoundError(
            f"ICDEV database not found at {DB_PATH}. "
            "Run 'python tools/db/init_icdev_db.py' first."
        )
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _log_audit(conn, project_id, event_type, actor, action, details=None,
               classification="CUI"):
    """Write an immutable audit trail entry within an existing connection.

    Audit trail is append-only per NIST 800-53 AU controls.
    """
    conn.execute(
        """INSERT INTO audit_trail
           (project_id, event_type, actor, action, details, classification)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            project_id,
            event_type,
            actor,
            action,
            json.dumps(details) if details else None,
            classification,
        ),
    )


# ---------------------------------------------------------------------------
# Helper: resolve project_id from plan
# ---------------------------------------------------------------------------

def _get_plan_project_id(conn, plan_id):
    """Look up the project_id for a migration plan via legacy_applications."""
    row = conn.execute(
        """SELECT la.project_id
           FROM migration_plans mp
           JOIN legacy_applications la ON mp.legacy_app_id = la.id
           WHERE mp.id = ?""",
        (plan_id,),
    ).fetchone()
    return row["project_id"] if row else None


# ---------------------------------------------------------------------------
# 1. Create strangler fig plan
# ---------------------------------------------------------------------------

def create_strangler_plan(plan_id):
    """Initialize strangler fig tracking for a migration plan.

    Verifies the plan exists and uses the strangler_fig migration approach,
    then creates cutover tracking tasks for every legacy component and
    catalogues all legacy API endpoints.

    Args:
        plan_id: Migration plan ID (must exist in migration_plans).

    Returns:
        dict with plan summary including component and API counts.
    """
    conn = _get_db()
    try:
        plan = conn.execute(
            "SELECT * FROM migration_plans WHERE id = ?", (plan_id,)
        ).fetchone()

        if not plan:
            raise ValueError(f"Migration plan '{plan_id}' not found.")

        if plan["migration_approach"] != "strangler_fig":
            raise ValueError(
                f"Plan '{plan_id}' uses migration_approach='{plan['migration_approach']}'. "
                "Strangler fig manager requires migration_approach='strangler_fig'."
            )

        legacy_app_id = plan["legacy_app_id"]

        # Fetch all legacy components for this application
        components = conn.execute(
            "SELECT * FROM legacy_components WHERE legacy_app_id = ?",
            (legacy_app_id,),
        ).fetchall()

        # Fetch all legacy APIs for this application
        apis = conn.execute(
            "SELECT * FROM legacy_apis WHERE legacy_app_id = ?",
            (legacy_app_id,),
        ).fetchall()

        # Create a cutover migration_task for each component
        now = datetime.now(timezone.utc).isoformat()
        tasks_created = 0
        for comp in components:
            task_id = f"mtask-{uuid.uuid4().hex[:12]}"
            description = json.dumps({
                "strangler_fig_status": "legacy",
                "component_type": comp["component_type"],
                "qualified_name": comp["qualified_name"],
            })
            try:
                conn.execute(
                    """INSERT INTO migration_tasks
                       (id, plan_id, legacy_component_id, task_type, title,
                        description, status, created_at)
                       VALUES (?, ?, ?, 'cutover', ?, ?, 'pending', ?)""",
                    (
                        task_id,
                        plan_id,
                        comp["id"],
                        f"Cutover: {comp['name']}",
                        description,
                        now,
                    ),
                )
                tasks_created += 1
            except sqlite3.IntegrityError:
                pass  # Task may already exist

        # Update total_tasks on the plan
        conn.execute(
            """UPDATE migration_plans
               SET total_tasks = total_tasks + ?, status = 'in_progress',
                   updated_at = ?
               WHERE id = ?""",
            (tasks_created, now, plan_id),
        )

        # Log audit event
        project_id = _get_plan_project_id(conn, plan_id)
        _log_audit(
            conn,
            project_id=project_id,
            event_type="migration_planned",
            actor="strangler-fig-manager",
            action=f"Initialized strangler fig tracking for plan {plan_id}",
            details={
                "plan_id": plan_id,
                "legacy_app_id": legacy_app_id,
                "components": len(components),
                "apis": len(apis),
                "tasks_created": tasks_created,
            },
        )

        conn.commit()

        summary = {
            "plan_id": plan_id,
            "plan_name": plan["plan_name"],
            "legacy_app_id": legacy_app_id,
            "strategy": plan["strategy"],
            "migration_approach": plan["migration_approach"],
            "component_count": len(components),
            "api_count": len(apis),
            "tasks_created": tasks_created,
            "status": "in_progress",
        }

        print(f"[INFO] Strangler fig plan initialized for '{plan['plan_name']}'")
        print(f"       Components: {len(components)} | APIs: {len(apis)} | Tasks: {tasks_created}")
        return summary

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 2. Register facade
# ---------------------------------------------------------------------------

def register_facade(plan_id, legacy_endpoint, modern_endpoint, component_id=None):
    """Register an endpoint facade mapping for strangler fig routing.

    Stores the facade as a migration_task with task_type='generate_facade'
    and routing metadata in the description field as JSON.

    Args:
        plan_id: Migration plan ID.
        legacy_endpoint: Legacy endpoint path (e.g. '/api/v1/users').
        modern_endpoint: Modern endpoint path (e.g. '/api/v2/users').
        component_id: Optional legacy component ID this facade covers.

    Returns:
        dict with the facade registration details.
    """
    conn = _get_db()
    try:
        plan = conn.execute(
            "SELECT * FROM migration_plans WHERE id = ?", (plan_id,)
        ).fetchone()
        if not plan:
            raise ValueError(f"Migration plan '{plan_id}' not found.")

        task_id = f"mtask-{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()

        facade_props = {
            "legacy_path": legacy_endpoint,
            "modern_path": modern_endpoint,
            "routing": "legacy",
            "component_id": component_id,
        }

        conn.execute(
            """INSERT INTO migration_tasks
               (id, plan_id, legacy_component_id, task_type, title,
                description, status, created_at)
               VALUES (?, ?, ?, 'generate_facade', ?, ?, 'in_progress', ?)""",
            (
                task_id,
                plan_id,
                component_id,
                f"Facade: {legacy_endpoint} -> {modern_endpoint}",
                json.dumps(facade_props),
                now,
            ),
        )

        conn.commit()

        result = {
            "task_id": task_id,
            "plan_id": plan_id,
            "legacy_endpoint": legacy_endpoint,
            "modern_endpoint": modern_endpoint,
            "component_id": component_id,
            "routing": "legacy",
            "registered_at": now,
        }

        print(f"[INFO] Facade registered: {legacy_endpoint} -> {modern_endpoint}")
        return result

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 3. Track cutover
# ---------------------------------------------------------------------------

def track_cutover(plan_id, component_id, status):
    """Update the cutover status for a specific component.

    Valid status transitions follow the strangler fig lifecycle:
        legacy -> parallel -> modern -> decommissioned

    Args:
        plan_id: Migration plan ID.
        component_id: Legacy component ID.
        status: Target cutover status (legacy|parallel|modern|decommissioned).

    Returns:
        dict with updated component status information.
    """
    if status not in CUTOVER_STATUSES:
        raise ValueError(
            f"Invalid cutover status '{status}'. "
            f"Valid: {', '.join(CUTOVER_STATUSES)}"
        )

    conn = _get_db()
    try:
        # Find the cutover task for this component
        task = conn.execute(
            """SELECT * FROM migration_tasks
               WHERE plan_id = ? AND legacy_component_id = ?
                 AND task_type = 'cutover'""",
            (plan_id, component_id),
        ).fetchone()

        if not task:
            raise ValueError(
                f"No cutover task found for component '{component_id}' "
                f"in plan '{plan_id}'."
            )

        now = datetime.now(timezone.utc).isoformat()
        task_status = CUTOVER_TO_TASK_STATUS[status]
        completed_at = now if status in ("modern", "decommissioned") else None

        # Update the task description with new strangler_fig_status
        desc = {}
        if task["description"]:
            try:
                desc = json.loads(task["description"])
            except (json.JSONDecodeError, TypeError):
                desc = {}
        desc["strangler_fig_status"] = status
        desc["cutover_updated_at"] = now

        conn.execute(
            """UPDATE migration_tasks
               SET status = ?, description = ?, completed_at = ?
               WHERE id = ?""",
            (task_status, json.dumps(desc), completed_at, task["id"]),
        )

        project_id = _get_plan_project_id(conn, plan_id)

        # If switching to modern: create digital thread link
        if status == "modern":
            try:
                conn.execute(
                    """INSERT INTO digital_thread_links
                       (project_id, source_type, source_id, target_type,
                        target_id, link_type, confidence, evidence, created_by)
                       VALUES (?, 'legacy_component', ?, 'code_module', ?,
                               'replaces', 0.95,
                               ?, 'strangler-fig-manager')""",
                    (
                        project_id,
                        component_id,
                        f"modern-{component_id}",
                        "Strangler fig cutover: component migrated to modern implementation",
                    ),
                )
            except sqlite3.IntegrityError:
                pass  # Link already exists

        # If decommissioned: log audit trail event
        if status == "decommissioned":
            # Fetch component name for audit message
            comp = conn.execute(
                "SELECT name, qualified_name FROM legacy_components WHERE id = ?",
                (component_id,),
            ).fetchone()
            comp_name = comp["name"] if comp else component_id

            _log_audit(
                conn,
                project_id=project_id,
                event_type="strangler_fig_cutover",
                actor="strangler-fig-manager",
                action=f"Decommissioned legacy component: {comp_name}",
                details={
                    "plan_id": plan_id,
                    "component_id": component_id,
                    "component_name": comp_name,
                    "qualified_name": comp["qualified_name"] if comp else None,
                    "final_status": "decommissioned",
                },
            )

        conn.commit()

        result = {
            "plan_id": plan_id,
            "component_id": component_id,
            "cutover_status": status,
            "task_status": task_status,
            "task_id": task["id"],
            "updated_at": now,
        }

        print(f"[INFO] Component {component_id} cutover status: {status}")
        return result

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 4. Get cutover status dashboard
# ---------------------------------------------------------------------------

def get_cutover_status(plan_id):
    """Get per-component cutover status dashboard for a migration plan.

    Queries all cutover tasks, groups by status, and computes overall
    migration progress percentage.

    Args:
        plan_id: Migration plan ID.

    Returns:
        dict with status counts, progress percentage, and component list.
    """
    conn = _get_db()
    try:
        plan = conn.execute(
            "SELECT * FROM migration_plans WHERE id = ?", (plan_id,)
        ).fetchone()
        if not plan:
            raise ValueError(f"Migration plan '{plan_id}' not found.")

        tasks = conn.execute(
            """SELECT mt.*, lc.name as comp_name, lc.component_type,
                      lc.qualified_name
               FROM migration_tasks mt
               LEFT JOIN legacy_components lc ON mt.legacy_component_id = lc.id
               WHERE mt.plan_id = ? AND mt.task_type = 'cutover'""",
            (plan_id,),
        ).fetchall()

        # Count by strangler fig status
        status_counts = collections.Counter()
        components = []

        for task in tasks:
            desc = {}
            if task["description"]:
                try:
                    desc = json.loads(task["description"])
                except (json.JSONDecodeError, TypeError):
                    desc = {}

            sf_status = desc.get("strangler_fig_status", "legacy")
            status_counts[sf_status] += 1

            components.append({
                "component_id": task["legacy_component_id"],
                "name": task["comp_name"] or "unknown",
                "type": task["component_type"] or "unknown",
                "qualified_name": task["qualified_name"],
                "cutover_status": sf_status,
                "task_id": task["id"],
                "task_status": task["status"],
            })

        total = len(tasks)
        modern_count = status_counts.get("modern", 0)
        decommissioned_count = status_counts.get("decommissioned", 0)
        migrated = modern_count + decommissioned_count
        progress_pct = round((migrated / total * 100), 2) if total > 0 else 0.0

        result = {
            "plan_id": plan_id,
            "plan_name": plan["plan_name"],
            "total": total,
            "legacy_count": status_counts.get("legacy", 0),
            "parallel_count": status_counts.get("parallel", 0),
            "modern_count": modern_count,
            "decommissioned_count": decommissioned_count,
            "progress_pct": progress_pct,
            "components": components,
        }

        print(f"[INFO] Cutover Status for '{plan['plan_name']}'")
        print(f"       Total: {total} | Legacy: {status_counts.get('legacy', 0)} | "
              f"Parallel: {status_counts.get('parallel', 0)} | "
              f"Modern: {modern_count} | Decommissioned: {decommissioned_count}")
        print(f"       Progress: {progress_pct}%")
        return result

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 5. Generate routing config
# ---------------------------------------------------------------------------

def generate_routing_config(plan_id):
    """Generate API gateway routing rules for the strangler fig facade.

    For each registered facade endpoint, determines whether traffic should
    route to the legacy or modern backend based on the component's current
    cutover status. Also generates Nginx and K8s Ingress config snippets
    as comments in the output.

    Args:
        plan_id: Migration plan ID.

    Returns:
        dict with routing rules, Nginx snippet, and K8s Ingress snippet.
    """
    conn = _get_db()
    try:
        # Fetch all facade registrations
        facades = conn.execute(
            """SELECT mt.*, lc.name as comp_name
               FROM migration_tasks mt
               LEFT JOIN legacy_components lc ON mt.legacy_component_id = lc.id
               WHERE mt.plan_id = ? AND mt.task_type = 'generate_facade'""",
            (plan_id,),
        ).fetchall()

        # Build a component_id -> cutover_status map from cutover tasks
        cutover_tasks = conn.execute(
            """SELECT legacy_component_id, description
               FROM migration_tasks
               WHERE plan_id = ? AND task_type = 'cutover'""",
            (plan_id,),
        ).fetchall()

        comp_status_map = {}
        for ct in cutover_tasks:
            if ct["legacy_component_id"]:
                desc = {}
                if ct["description"]:
                    try:
                        desc = json.loads(ct["description"])
                    except (json.JSONDecodeError, TypeError):
                        desc = {}
                comp_status_map[ct["legacy_component_id"]] = desc.get(
                    "strangler_fig_status", "legacy"
                )

        routes = []
        nginx_lines = [
            f"# {CUI_BANNER}",
            "# Strangler Fig Routing Configuration — Nginx",
            f"# Generated: {datetime.now(timezone.utc).isoformat()}",
            f"# Plan: {plan_id}",
            "",
        ]
        k8s_annotations = []

        for facade in facades:
            props = {}
            if facade["description"]:
                try:
                    props = json.loads(facade["description"])
                except (json.JSONDecodeError, TypeError):
                    props = {}

            legacy_path = props.get("legacy_path", "/")
            modern_path = props.get("modern_path", "/")
            comp_id = props.get("component_id") or facade["legacy_component_id"]

            # Determine routing target based on component cutover status
            comp_cutover = comp_status_map.get(comp_id, "legacy")
            if comp_cutover in ("modern", "decommissioned"):
                target = "modern"
                backend_url = modern_path
            else:
                target = "legacy"
                backend_url = legacy_path

            route_entry = {
                "path": legacy_path,
                "method": "ALL",
                "target": target,
                "backend_url": backend_url,
                "legacy_url": legacy_path,
                "modern_url": modern_path,
                "component_id": comp_id,
                "component_status": comp_cutover,
            }
            routes.append(route_entry)

            # Nginx snippet
            upstream = "modern_upstream" if target == "modern" else "legacy_upstream"
            nginx_lines.append(f"location {legacy_path} {{")
            nginx_lines.append(f"    # Component: {comp_id} (status: {comp_cutover})")
            nginx_lines.append(f"    proxy_pass http://{upstream}{backend_url};")
            nginx_lines.append(f"    proxy_set_header X-Strangler-Target {target};")
            nginx_lines.append("}")
            nginx_lines.append("")

            # K8s Ingress annotation
            k8s_annotations.append({
                "path": legacy_path,
                "service": f"{'modern' if target == 'modern' else 'legacy'}-service",
                "port": 8443 if target == "modern" else 8080,
            })

        nginx_lines.append(f"# {CUI_BANNER}")
        nginx_snippet = "\n".join(nginx_lines)

        # Generate K8s Ingress YAML snippet
        k8s_lines = [
            f"# {CUI_BANNER}",
            "# Strangler Fig Routing — K8s Ingress",
            f"# Generated: {datetime.now(timezone.utc).isoformat()}",
            "apiVersion: networking.k8s.io/v1",
            "kind: Ingress",
            "metadata:",
            f"  name: strangler-fig-{plan_id}",
            "  annotations:",
            "    nginx.ingress.kubernetes.io/rewrite-target: /",
            "spec:",
            "  rules:",
            "  - http:",
            "      paths:",
        ]
        for ann in k8s_annotations:
            k8s_lines.append(f"      - path: {ann['path']}")
            k8s_lines.append("        pathType: Prefix")
            k8s_lines.append("        backend:")
            k8s_lines.append("          service:")
            k8s_lines.append(f"            name: {ann['service']}")
            k8s_lines.append("            port:")
            k8s_lines.append(f"              number: {ann['port']}")
        k8s_lines.append(f"# {CUI_BANNER}")
        k8s_snippet = "\n".join(k8s_lines)

        result = {
            "plan_id": plan_id,
            "route_count": len(routes),
            "routes": routes,
            "nginx_config": nginx_snippet,
            "k8s_ingress": k8s_snippet,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

        print(f"[INFO] Generated routing config: {len(routes)} routes")
        return result

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 6. Generate ACL code
# ---------------------------------------------------------------------------

def generate_acl_code(plan_id, boundary_name, language="python"):
    """Generate anti-corruption layer (ACL) adapter code.

    Produces adapter classes that translate between legacy and modern
    interfaces, preventing legacy domain concepts from leaking into
    the modern codebase.

    Args:
        plan_id: Migration plan ID.
        boundary_name: Name for the ACL boundary (used in class names).
        language: Target language ('python', 'java', or 'csharp').

    Returns:
        str containing the generated adapter source code.
    """
    conn = _get_db()
    try:
        plan = conn.execute(
            "SELECT * FROM migration_plans WHERE id = ?", (plan_id,)
        ).fetchone()
        if not plan:
            raise ValueError(f"Migration plan '{plan_id}' not found.")

        legacy_app_id = plan["legacy_app_id"]

        # Fetch components for this plan to build adapter methods
        components = conn.execute(
            """SELECT lc.* FROM legacy_components lc
               JOIN migration_tasks mt ON mt.legacy_component_id = lc.id
               WHERE mt.plan_id = ? AND mt.task_type = 'cutover'
               ORDER BY lc.name""",
            (plan_id,),
        ).fetchall()

        # Fetch APIs for method signatures
        apis = conn.execute(
            "SELECT * FROM legacy_apis WHERE legacy_app_id = ?",
            (legacy_app_id,),
        ).fetchall()

        # Build API lookup by component_id
        api_by_comp = collections.defaultdict(list)
        for api in apis:
            if api["component_id"]:
                api_by_comp[api["component_id"]].append(api)

        class_name = f"{_to_pascal_case(boundary_name)}AclAdapter"
        interface_name = f"I{class_name}"
        now = datetime.now(timezone.utc).isoformat()

        if language == "python":
            code = _generate_python_acl(
                class_name, boundary_name, components, api_by_comp, now
            )
        elif language == "java":
            code = _generate_java_acl(
                class_name, interface_name, boundary_name, components,
                api_by_comp, now
            )
        elif language == "csharp":
            code = _generate_csharp_acl(
                class_name, interface_name, boundary_name, components,
                api_by_comp, now
            )
        else:
            raise ValueError(
                f"Unsupported language '{language}'. "
                "Supported: python, java, csharp"
            )

        print(f"[INFO] Generated {language} ACL code: {class_name}")
        return code

    finally:
        conn.close()


def _to_pascal_case(name):
    """Convert a snake_case or kebab-case name to PascalCase."""
    parts = name.replace("-", "_").split("_")
    return "".join(p.capitalize() for p in parts if p)


def _to_method_name(name, language="python"):
    """Convert a component name to a safe method name."""
    safe = name.replace("-", "_").replace(".", "_").replace(" ", "_").lower()
    if language == "python":
        return f"translate_{safe}"
    return f"translate{_to_pascal_case(safe)}"


def _generate_python_acl(class_name, boundary, components, api_by_comp, now):
    """Generate Python ACL adapter class."""
    lines = [
        f"# {CUI_BANNER}",
        f'"""Anti-Corruption Layer adapter for boundary: {boundary}.',
        "",
        f"Auto-generated by ICDEV strangler-fig-manager at {now}.",
        "Maps legacy interfaces to modern domain model.",
        "",
        f"Classification: {CUI_BANNER}",
        '"""',
        "",
        "",
        f"class {class_name}:",
        f'    """ACL adapter translating legacy {boundary} interfaces to modern API."""',
        "",
        "    def __init__(self, legacy_client, modern_client):",
        '        """Initialize with both legacy and modern service clients.',
        "",
        "        Args:",
        "            legacy_client: Client for the legacy system.",
        "            modern_client: Client for the modern system.",
        '        """',
        "        self._legacy = legacy_client",
        "        self._modern = modern_client",
        "",
    ]

    for comp in components:
        method_name = _to_method_name(comp["name"], "python")
        comp_apis = api_by_comp.get(comp["id"], [])

        lines.append(f"    def {method_name}(self, request_data):")
        lines.append(f'        """Translate legacy {comp["name"]} request to modern format.')
        lines.append("")
        lines.append(f'        Legacy component: {comp["qualified_name"] or comp["name"]}')
        lines.append(f'        Component type: {comp["component_type"]}')
        if comp_apis:
            lines.append("        Legacy endpoints:")
            for api in comp_apis:
                lines.append(f"            {api['method']} {api['path']}")
        lines.append("")
        lines.append("        Args:")
        lines.append("            request_data: dict with legacy request payload.")
        lines.append("")
        lines.append("        Returns:")
        lines.append("            dict with translated modern response.")
        lines.append('        """')
        lines.append("        # TODO: Implement legacy-to-modern data mapping")
        lines.append("        modern_request = self._map_request(request_data)")
        lines.append("        response = self._modern.call(modern_request)")
        lines.append("        return self._map_response(response)")
        lines.append("")

    lines.append("    def _map_request(self, legacy_data):")
    lines.append('        """Map legacy request format to modern request format."""')
    lines.append("        # TODO: Implement field-by-field mapping")
    lines.append("        return legacy_data")
    lines.append("")
    lines.append("    def _map_response(self, modern_response):")
    lines.append('        """Map modern response format back to legacy response format."""')
    lines.append("        # TODO: Implement response translation")
    lines.append("        return modern_response")
    lines.append("")
    lines.append(f"# {CUI_BANNER}")

    return "\n".join(lines)


def _generate_java_acl(class_name, interface_name, boundary, components,
                       api_by_comp, now):
    """Generate Java ACL adapter interface and implementation class."""
    lines = [
        f"// {CUI_BANNER}",
        "/**",
        f" * Anti-Corruption Layer adapter for boundary: {boundary}.",
        " *",
        f" * Auto-generated by ICDEV strangler-fig-manager at {now}.",
        " * Maps legacy interfaces to modern domain model.",
        " *",
        f" * Classification: {CUI_BANNER}",
        " */",
        "",
        f"public interface {interface_name} {{",
    ]

    for comp in components:
        method_name = _to_method_name(comp["name"], "java")
        lines.append("    /**")
        lines.append(f'     * Translate legacy {comp["name"]} request to modern format.')
        lines.append("     */")
        lines.append(f"    Map<String, Object> {method_name}(Map<String, Object> requestData);")
        lines.append("")

    lines.append("}")
    lines.append("")
    lines.append("")
    lines.append(f"public class {class_name} implements {interface_name} {{")
    lines.append("")
    lines.append("    private final Object legacyClient;")
    lines.append("    private final Object modernClient;")
    lines.append("")
    lines.append(f"    public {class_name}(Object legacyClient, Object modernClient) {{")
    lines.append("        this.legacyClient = legacyClient;")
    lines.append("        this.modernClient = modernClient;")
    lines.append("    }")
    lines.append("")

    for comp in components:
        method_name = _to_method_name(comp["name"], "java")
        lines.append("    @Override")
        lines.append(f"    public Map<String, Object> {method_name}(Map<String, Object> requestData) {{")
        lines.append(f"        // TODO: Implement legacy-to-modern mapping for {comp['name']}")
        lines.append("        Map<String, Object> modernRequest = mapRequest(requestData);")
        lines.append("        // Call modern service and translate response")
        lines.append("        return mapResponse(modernRequest);")
        lines.append("    }")
        lines.append("")

    lines.append("    private Map<String, Object> mapRequest(Map<String, Object> legacyData) {")
    lines.append("        // TODO: Implement field-by-field mapping")
    lines.append("        return legacyData;")
    lines.append("    }")
    lines.append("")
    lines.append("    private Map<String, Object> mapResponse(Map<String, Object> modernResponse) {")
    lines.append("        // TODO: Implement response translation")
    lines.append("        return modernResponse;")
    lines.append("    }")
    lines.append("}")
    lines.append(f"// {CUI_BANNER}")

    return "\n".join(lines)


def _generate_csharp_acl(class_name, interface_name, boundary, components,
                         api_by_comp, now):
    """Generate C# ACL adapter interface and implementation class."""
    lines = [
        f"// {CUI_BANNER}",
        "/// <summary>",
        f"/// Anti-Corruption Layer adapter for boundary: {boundary}.",
        "///",
        f"/// Auto-generated by ICDEV strangler-fig-manager at {now}.",
        "/// Maps legacy interfaces to modern domain model.",
        "///",
        f"/// Classification: {CUI_BANNER}",
        "/// </summary>",
        "",
        "using System.Collections.Generic;",
        "",
        f"public interface {interface_name}",
        "{",
    ]

    for comp in components:
        method_name = _to_method_name(comp["name"], "csharp")
        lines.append(f"    /// <summary>Translate legacy {comp['name']} request to modern format.</summary>")
        lines.append(f"    Dictionary<string, object> {method_name}(Dictionary<string, object> requestData);")
        lines.append("")

    lines.append("}")
    lines.append("")
    lines.append(f"public class {class_name} : {interface_name}")
    lines.append("{")
    lines.append("    private readonly object _legacyClient;")
    lines.append("    private readonly object _modernClient;")
    lines.append("")
    lines.append(f"    public {class_name}(object legacyClient, object modernClient)")
    lines.append("    {")
    lines.append("        _legacyClient = legacyClient;")
    lines.append("        _modernClient = modernClient;")
    lines.append("    }")
    lines.append("")

    for comp in components:
        method_name = _to_method_name(comp["name"], "csharp")
        lines.append(f"    public Dictionary<string, object> {method_name}(Dictionary<string, object> requestData)")
        lines.append("    {")
        lines.append(f"        // TODO: Implement legacy-to-modern mapping for {comp['name']}")
        lines.append("        var modernRequest = MapRequest(requestData);")
        lines.append("        return MapResponse(modernRequest);")
        lines.append("    }")
        lines.append("")

    lines.append("    private Dictionary<string, object> MapRequest(Dictionary<string, object> legacyData)")
    lines.append("    {")
    lines.append("        // TODO: Implement field-by-field mapping")
    lines.append("        return legacyData;")
    lines.append("    }")
    lines.append("")
    lines.append("    private Dictionary<string, object> MapResponse(Dictionary<string, object> modernResponse)")
    lines.append("    {")
    lines.append("        // TODO: Implement response translation")
    lines.append("        return modernResponse;")
    lines.append("    }")
    lines.append("}")
    lines.append(f"// {CUI_BANNER}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 7. Check coexistence health
# ---------------------------------------------------------------------------

def check_coexistence_health(plan_id):
    """Verify that legacy and modern systems can safely coexist.

    Performs three categories of checks:
      1. All parallel components have both legacy and modern endpoints
      2. No circular dependencies between legacy and modern components
      3. All facade routes are properly defined

    Args:
        plan_id: Migration plan ID.

    Returns:
        dict with health status, issues list, and warnings list.
    """
    conn = _get_db()
    try:
        plan = conn.execute(
            "SELECT * FROM migration_plans WHERE id = ?", (plan_id,)
        ).fetchone()
        if not plan:
            raise ValueError(f"Migration plan '{plan_id}' not found.")

        issues = []
        warnings = []

        legacy_app_id = plan["legacy_app_id"]

        # Fetch all cutover tasks with their status
        cutover_tasks = conn.execute(
            """SELECT mt.*, lc.name as comp_name, lc.qualified_name
               FROM migration_tasks mt
               LEFT JOIN legacy_components lc ON mt.legacy_component_id = lc.id
               WHERE mt.plan_id = ? AND mt.task_type = 'cutover'""",
            (plan_id,),
        ).fetchall()

        # Fetch all facade registrations
        facades = conn.execute(
            """SELECT * FROM migration_tasks
               WHERE plan_id = ? AND task_type = 'generate_facade'""",
            (plan_id,),
        ).fetchall()

        # Build facade component set
        facade_component_ids = set()
        for f in facades:
            if f["legacy_component_id"]:
                facade_component_ids.add(f["legacy_component_id"])
            if f["description"]:
                try:
                    props = json.loads(f["description"])
                    cid = props.get("component_id")
                    if cid:
                        facade_component_ids.add(cid)
                except (json.JSONDecodeError, TypeError):
                    pass

        # Check 1: Parallel components should have facade routes
        parallel_components = []
        for task in cutover_tasks:
            desc = {}
            if task["description"]:
                try:
                    desc = json.loads(task["description"])
                except (json.JSONDecodeError, TypeError):
                    desc = {}
            sf_status = desc.get("strangler_fig_status", "legacy")

            if sf_status == "parallel":
                parallel_components.append(task)
                comp_id = task["legacy_component_id"]
                if comp_id and comp_id not in facade_component_ids:
                    issues.append({
                        "check": "parallel_without_facade",
                        "severity": "error",
                        "component_id": comp_id,
                        "component_name": task["comp_name"],
                        "message": (
                            f"Component '{task['comp_name']}' is in parallel mode "
                            "but has no facade route registered."
                        ),
                    })

        # Check 2: Look for potential circular dependencies
        # between components that are in different cutover states
        comp_status_map = {}
        for task in cutover_tasks:
            if task["legacy_component_id"]:
                desc = {}
                if task["description"]:
                    try:
                        desc = json.loads(task["description"])
                    except (json.JSONDecodeError, TypeError):
                        desc = {}
                comp_status_map[task["legacy_component_id"]] = desc.get(
                    "strangler_fig_status", "legacy"
                )

        # Query dependencies for cross-status circular references
        deps = conn.execute(
            """SELECT source_component_id, target_component_id
               FROM legacy_dependencies
               WHERE legacy_app_id = ?
                 AND source_component_id IS NOT NULL
                 AND target_component_id IS NOT NULL""",
            (legacy_app_id,),
        ).fetchall()

        # Check for bidirectional dependencies crossing cutover boundaries
        dep_pairs = set()
        for dep in deps:
            src = dep["source_component_id"]
            tgt = dep["target_component_id"]
            src_status = comp_status_map.get(src, "legacy")
            tgt_status = comp_status_map.get(tgt, "legacy")

            # Flag if modern component depends on legacy component
            if src_status in ("modern", "decommissioned") and tgt_status == "legacy":
                pair_key = (src, tgt)
                if pair_key not in dep_pairs:
                    dep_pairs.add(pair_key)
                    warnings.append({
                        "check": "cross_boundary_dependency",
                        "severity": "warning",
                        "source_id": src,
                        "target_id": tgt,
                        "message": (
                            f"Modern component {src} depends on legacy component {tgt}. "
                            "Consider adding an ACL adapter."
                        ),
                    })

            # Flag circular: A depends on B and B depends on A across boundaries
            reverse_key = (tgt, src)
            if reverse_key in dep_pairs:
                issues.append({
                    "check": "circular_cross_boundary",
                    "severity": "error",
                    "components": [src, tgt],
                    "message": (
                        f"Circular dependency detected between {src} and {tgt} "
                        "across cutover boundaries."
                    ),
                })

        # Check 3: All APIs for parallel/modern components should have facades
        apis = conn.execute(
            "SELECT * FROM legacy_apis WHERE legacy_app_id = ?",
            (legacy_app_id,),
        ).fetchall()

        for api in apis:
            comp_id = api["component_id"]
            if not comp_id:
                continue
            comp_status = comp_status_map.get(comp_id, "legacy")
            if comp_status in ("parallel", "modern") and comp_id not in facade_component_ids:
                warnings.append({
                    "check": "api_without_facade",
                    "severity": "warning",
                    "component_id": comp_id,
                    "api_path": api["path"],
                    "api_method": api["method"],
                    "message": (
                        f"API {api['method']} {api['path']} on "
                        f"{comp_status} component has no facade route."
                    ),
                })

        healthy = len(issues) == 0

        result = {
            "plan_id": plan_id,
            "healthy": healthy,
            "issues": issues,
            "warnings": warnings,
            "checks_performed": [
                "parallel_components_have_facades",
                "no_circular_cross_boundary_deps",
                "all_facade_routes_defined",
            ],
            "parallel_count": len(parallel_components),
            "facade_count": len(facades),
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

        status_label = "HEALTHY" if healthy else "UNHEALTHY"
        print(f"[INFO] Coexistence Health: {status_label}")
        print(f"       Issues: {len(issues)} | Warnings: {len(warnings)}")
        return result

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 8. Generate cutover checklist
# ---------------------------------------------------------------------------

def generate_cutover_checklist(plan_id, component_id):
    """Generate a pre-cutover validation checklist for a component.

    Produces a comprehensive checklist that must be satisfied before
    a component can be safely switched from legacy to modern.

    Args:
        plan_id: Migration plan ID.
        component_id: Legacy component ID to generate checklist for.

    Returns:
        dict with checklist items and their verification status.
    """
    conn = _get_db()
    try:
        # Verify plan and component exist
        plan = conn.execute(
            "SELECT * FROM migration_plans WHERE id = ?", (plan_id,)
        ).fetchone()
        if not plan:
            raise ValueError(f"Migration plan '{plan_id}' not found.")

        comp = conn.execute(
            "SELECT * FROM legacy_components WHERE id = ?", (component_id,)
        ).fetchone()
        if not comp:
            raise ValueError(f"Component '{component_id}' not found.")

        project_id = _get_plan_project_id(conn, plan_id)

        # Check for existing modern implementation (digital thread link)
        modern_link = conn.execute(
            """SELECT * FROM digital_thread_links
               WHERE project_id = ? AND source_type = 'legacy_component'
                 AND source_id = ? AND link_type = 'replaces'""",
            (project_id, component_id),
        ).fetchone()

        # Check for test coverage tasks
        test_tasks = conn.execute(
            """SELECT * FROM migration_tasks
               WHERE plan_id = ? AND legacy_component_id = ?
                 AND task_type = 'generate_test'""",
            (plan_id, component_id),
        ).fetchall()
        tests_exist = len(test_tasks) > 0
        tests_passing = all(t["status"] == "completed" for t in test_tasks) if tests_exist else False

        # Check for facade registration
        facade = conn.execute(
            """SELECT * FROM migration_tasks
               WHERE plan_id = ? AND legacy_component_id = ?
                 AND task_type = 'generate_facade'""",
            (plan_id, component_id),
        ).fetchone()

        # Check for schema migration tasks
        schema_task = conn.execute(
            """SELECT * FROM migration_tasks
               WHERE plan_id = ? AND legacy_component_id = ?
                 AND task_type IN ('migrate_schema', 'migrate_data')""",
            (plan_id, component_id),
        ).fetchall()
        data_migrated = all(t["status"] == "completed" for t in schema_task) if schema_task else False

        # Check compliance controls mapped
        compliance_links = conn.execute(
            """SELECT * FROM digital_thread_links
               WHERE project_id = ? AND source_type = 'legacy_component'
                 AND source_id = ?
                 AND target_type IN ('nist_control', 'stig_rule')""",
            (project_id, component_id),
        ).fetchall()

        checklist = [
            {
                "item": "Modern implementation exists",
                "verified": modern_link is not None,
                "details": (
                    f"Digital thread link found: {modern_link['target_id']}"
                    if modern_link else "No modern implementation linked yet"
                ),
            },
            {
                "item": "Tests passing",
                "verified": tests_passing,
                "details": (
                    f"{len(test_tasks)} test task(s) completed"
                    if tests_passing else
                    f"{len(test_tasks)} test task(s) found, not all passing"
                    if tests_exist else "No test tasks found"
                ),
            },
            {
                "item": "API compatibility verified",
                "verified": facade is not None,
                "details": (
                    "Facade route registered"
                    if facade else "No facade route registered"
                ),
            },
            {
                "item": "Data migration complete",
                "verified": data_migrated,
                "details": (
                    f"{len(schema_task)} schema/data task(s) completed"
                    if data_migrated else
                    f"{len(schema_task)} schema/data task(s) pending"
                    if schema_task else "No data migration tasks defined"
                ),
            },
            {
                "item": "Rollback plan exists",
                "verified": facade is not None,
                "details": (
                    "Facade allows instant rollback to legacy routing"
                    if facade else "No facade — manual rollback required"
                ),
            },
            {
                "item": "Monitoring in place",
                "verified": False,
                "details": "Verify health checks and alerting configured for modern endpoint",
            },
            {
                "item": "Compliance controls mapped",
                "verified": len(compliance_links) > 0,
                "details": (
                    f"{len(compliance_links)} compliance link(s) found"
                    if compliance_links else "No compliance controls linked"
                ),
            },
        ]

        all_verified = all(item["verified"] for item in checklist)

        result = {
            "plan_id": plan_id,
            "component_id": component_id,
            "component_name": comp["name"],
            "qualified_name": comp["qualified_name"],
            "ready_for_cutover": all_verified,
            "verified_count": sum(1 for i in checklist if i["verified"]),
            "total_checks": len(checklist),
            "checklist": checklist,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

        ready_label = "READY" if all_verified else "NOT READY"
        verified_count = sum(1 for i in checklist if i["verified"])
        print(f"[INFO] Cutover Checklist for '{comp['name']}': {ready_label}")
        print(f"       Verified: {verified_count}/{len(checklist)}")
        for item in checklist:
            mark = "[x]" if item["verified"] else "[ ]"
            print(f"       {mark} {item['item']}")
        return result

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 9. Execute cutover
# ---------------------------------------------------------------------------

def execute_cutover(plan_id, component_id):
    """Execute the cutover for a component, marking it as migrated to modern.

    This is the operational step that:
      1. Updates the component status to 'modern'
      2. Creates a digital thread traceability link
      3. Updates facade routing to point to modern backend
      4. Logs an audit trail event
      5. Updates the migration plan's completed_tasks count

    Args:
        plan_id: Migration plan ID.
        component_id: Legacy component ID to cut over.

    Returns:
        dict with cutover execution result.
    """
    conn = _get_db()
    try:
        # Verify plan exists
        plan = conn.execute(
            "SELECT * FROM migration_plans WHERE id = ?", (plan_id,)
        ).fetchone()
        if not plan:
            raise ValueError(f"Migration plan '{plan_id}' not found.")

        # Verify component exists
        comp = conn.execute(
            "SELECT * FROM legacy_components WHERE id = ?", (component_id,)
        ).fetchone()
        if not comp:
            raise ValueError(f"Component '{component_id}' not found.")

        # Find the cutover task
        task = conn.execute(
            """SELECT * FROM migration_tasks
               WHERE plan_id = ? AND legacy_component_id = ?
                 AND task_type = 'cutover'""",
            (plan_id, component_id),
        ).fetchone()
        if not task:
            raise ValueError(
                f"No cutover task found for component '{component_id}' "
                f"in plan '{plan_id}'."
            )

        now = datetime.now(timezone.utc).isoformat()
        project_id = _get_plan_project_id(conn, plan_id)

        # 1. Update cutover task to completed with modern status
        desc = {}
        if task["description"]:
            try:
                desc = json.loads(task["description"])
            except (json.JSONDecodeError, TypeError):
                desc = {}
        desc["strangler_fig_status"] = "modern"
        desc["cutover_executed_at"] = now

        conn.execute(
            """UPDATE migration_tasks
               SET status = 'completed', description = ?, completed_at = ?
               WHERE id = ?""",
            (json.dumps(desc), now, task["id"]),
        )

        # 2. Create digital thread link: legacy_component replaces -> code_module
        modern_target_id = f"modern-{component_id}"
        try:
            conn.execute(
                """INSERT INTO digital_thread_links
                   (project_id, source_type, source_id, target_type,
                    target_id, link_type, confidence, evidence, created_by)
                   VALUES (?, 'legacy_component', ?, 'code_module', ?,
                           'replaces', 0.95, ?, 'strangler-fig-manager')""",
                (
                    project_id,
                    component_id,
                    modern_target_id,
                    f"Cutover executed: {comp['name']} migrated to modern implementation",
                ),
            )
        except sqlite3.IntegrityError:
            pass  # Link already exists

        # 3. Update facade routing for this component
        facade_tasks = conn.execute(
            """SELECT * FROM migration_tasks
               WHERE plan_id = ? AND legacy_component_id = ?
                 AND task_type = 'generate_facade'""",
            (plan_id, component_id),
        ).fetchall()

        for ft in facade_tasks:
            f_desc = {}
            if ft["description"]:
                try:
                    f_desc = json.loads(ft["description"])
                except (json.JSONDecodeError, TypeError):
                    f_desc = {}
            f_desc["routing"] = "modern"
            f_desc["routing_updated_at"] = now
            conn.execute(
                """UPDATE migration_tasks
                   SET description = ?, status = 'completed'
                   WHERE id = ?""",
                (json.dumps(f_desc), ft["id"]),
            )

        # 4. Log audit trail event
        _log_audit(
            conn,
            project_id=project_id,
            event_type="strangler_fig_cutover",
            actor="strangler-fig-manager",
            action=f"Cutover executed for component: {comp['name']}",
            details={
                "plan_id": plan_id,
                "component_id": component_id,
                "component_name": comp["name"],
                "qualified_name": comp["qualified_name"],
                "cutover_status": "modern",
                "facades_updated": len(facade_tasks),
            },
        )

        # 5. Update migration plan completed_tasks count
        completed = conn.execute(
            """SELECT COUNT(*) as cnt FROM migration_tasks
               WHERE plan_id = ? AND status = 'completed'""",
            (plan_id,),
        ).fetchone()

        completed_count = completed["cnt"] if completed else 0
        total = plan["total_tasks"] or 0

        new_plan_status = plan["status"]
        completion_date = None
        if completed_count >= total and total > 0:
            new_plan_status = "completed"
            completion_date = now

        conn.execute(
            """UPDATE migration_plans
               SET completed_tasks = ?, status = ?, completion_date = ?,
                   updated_at = ?
               WHERE id = ?""",
            (completed_count, new_plan_status, completion_date, now, plan_id),
        )

        conn.commit()

        result = {
            "plan_id": plan_id,
            "component_id": component_id,
            "component_name": comp["name"],
            "cutover_status": "modern",
            "task_id": task["id"],
            "digital_thread_link": {
                "source_type": "legacy_component",
                "source_id": component_id,
                "target_type": "code_module",
                "target_id": modern_target_id,
                "link_type": "replaces",
            },
            "facades_updated": len(facade_tasks),
            "plan_completed_tasks": completed_count,
            "plan_total_tasks": total,
            "plan_status": new_plan_status,
            "executed_at": now,
        }

        print(f"[INFO] Cutover executed for '{comp['name']}'")
        print(f"       Status: modern | Facades updated: {len(facade_tasks)}")
        print(f"       Plan progress: {completed_count}/{total} tasks completed")
        return result

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI interface
# ---------------------------------------------------------------------------

def main():
    """Command-line entry point for the strangler fig migration manager."""
    parser = argparse.ArgumentParser(
        description="CUI // SP-CTI -- Strangler Fig Pattern Migration Manager",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
        Examples:
          # Initialize strangler fig tracking
          python tools/modernization/strangler_fig_manager.py \\
              --plan-id mplan-abc123 --create

          # Check cutover status
          python tools/modernization/strangler_fig_manager.py \\
              --plan-id mplan-abc123 --status

          # Move component to parallel mode
          python tools/modernization/strangler_fig_manager.py \\
              --plan-id mplan-abc123 --cutover \\
              --component-id lcomp-xyz789 --to parallel

          # Generate routing config as JSON
          python tools/modernization/strangler_fig_manager.py \\
              --plan-id mplan-abc123 --routing --json

          # Check coexistence health
          python tools/modernization/strangler_fig_manager.py \\
              --plan-id mplan-abc123 --health

          # Generate cutover checklist
          python tools/modernization/strangler_fig_manager.py \\
              --plan-id mplan-abc123 --checklist \\
              --component-id lcomp-xyz789

          # Execute cutover
          python tools/modernization/strangler_fig_manager.py \\
              --plan-id mplan-abc123 --execute-cutover \\
              --component-id lcomp-xyz789

        Classification: CUI // SP-CTI
        """),
    )

    parser.add_argument(
        "--plan-id", required=True,
        help="Migration plan ID (required for all operations)",
    )
    parser.add_argument(
        "--component-id",
        help="Legacy component ID (required for --cutover, --checklist, --execute-cutover)",
    )

    # Action flags
    action_group = parser.add_mutually_exclusive_group(required=True)
    action_group.add_argument(
        "--create", action="store_true",
        help="Initialize strangler fig tracking for the plan",
    )
    action_group.add_argument(
        "--status", action="store_true",
        help="Show cutover status dashboard",
    )
    action_group.add_argument(
        "--cutover", action="store_true",
        help="Track component cutover status change (requires --component-id and --to)",
    )
    action_group.add_argument(
        "--routing", action="store_true",
        help="Generate API gateway routing configuration",
    )
    action_group.add_argument(
        "--health", action="store_true",
        help="Check coexistence health between legacy and modern",
    )
    action_group.add_argument(
        "--checklist", action="store_true",
        help="Generate pre-cutover validation checklist (requires --component-id)",
    )
    action_group.add_argument(
        "--execute-cutover", action="store_true",
        help="Execute cutover, marking component as modern (requires --component-id)",
    )

    # Cutover options
    parser.add_argument(
        "--to", dest="cutover_target",
        choices=CUTOVER_STATUSES,
        help="Target cutover status (used with --cutover)",
    )

    # Output format
    parser.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output results as JSON",
    )

    args = parser.parse_args()

    try:
        if args.create:
            result = create_strangler_plan(args.plan_id)
            if args.json_output:
                print(json.dumps(result, indent=2))

        elif args.status:
            result = get_cutover_status(args.plan_id)
            if args.json_output:
                print(json.dumps(result, indent=2))

        elif args.cutover:
            if not args.component_id:
                parser.error("--cutover requires --component-id")
            if not args.cutover_target:
                parser.error("--cutover requires --to <status>")
            result = track_cutover(
                args.plan_id, args.component_id, args.cutover_target
            )
            if args.json_output:
                print(json.dumps(result, indent=2))

        elif args.routing:
            result = generate_routing_config(args.plan_id)
            if args.json_output:
                print(json.dumps(result, indent=2))

        elif args.health:
            result = check_coexistence_health(args.plan_id)
            if args.json_output:
                print(json.dumps(result, indent=2))

        elif args.checklist:
            if not args.component_id:
                parser.error("--checklist requires --component-id")
            result = generate_cutover_checklist(args.plan_id, args.component_id)
            if args.json_output:
                print(json.dumps(result, indent=2))

        elif args.execute_cutover:
            if not args.component_id:
                parser.error("--execute-cutover requires --component-id")
            result = execute_cutover(args.plan_id, args.component_id)
            if args.json_output:
                print(json.dumps(result, indent=2))

    except FileNotFoundError as exc:
        print(f"[ERROR] {exc}")
        raise SystemExit(1)
    except ValueError as exc:
        print(f"[ERROR] {exc}")
        raise SystemExit(1)
    except sqlite3.Error as exc:
        print(f"[ERROR] Database error: {exc}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
# [TEMPLATE: CUI // SP-CTI]
