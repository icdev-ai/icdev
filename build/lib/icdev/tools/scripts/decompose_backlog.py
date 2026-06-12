#!/usr/bin/env python3
"""Decompose large kanban backlog tasks into atomic subtasks."""
from __future__ import annotations

import sys
from pathlib import Path
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from datetime import datetime, timezone
from tools.db.storage import get_connection


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def create_subtask(conn, parent_id, title, description, task_type="build", priority="low", order=0, depends_on=None):
    task_id = f"{parent_id}-d{order}"
    conn.execute(
        """
        INSERT INTO kanban_tasks (id, title, description, task_type, priority, status, created_at, updated_at, depends_on_task_id, source_prediction_id, dispatch_source)
        VALUES (?, ?, ?, ?, ?, 'backlog', ?, ?, ?, ?, 'manual_decompose')
        """,
        (task_id, title, description, task_type, priority, now_iso(), now_iso(), depends_on, parent_id),
    )
    return task_id


def mark_decomposed(conn, task_id):
    conn.execute(
        "UPDATE kanban_tasks SET status='decomposed', updated_at=? WHERE id=?",
        (now_iso(), task_id),
    )


def main():
    conn = get_connection()

    # 1. Core features (5 subsystems)
    parent = "task-577d7ea80b"
    mark_decomposed(conn, parent)
    create_subtask(conn, parent, "Build Workload Discovery Scanner", "Implement server/database/app inventory from CMDB or manual import. Assign MAP readiness score 1-5. Single module.", "build", "low", 1)
    create_subtask(conn, parent, "Build Migration Wave Planner", "Drag-and-drop UI for sequencing workloads with dependency mapping. Single frontend + backend route.", "build", "low", 2)
    create_subtask(conn, parent, "Build Compliance Checker", "Validate each workload against DISA STIG checklists before and after migration. Single tool module.", "build", "low", 3)
    create_subtask(conn, parent, "Build Migration Executor", "Terraform/Ansible integration for actual lift-and-shift migrations. Single orchestrator module.", "build", "low", 4)
    create_subtask(conn, parent, "Build Rollback Engine", "Revert a workload within 4 hours of a failed migration. Single rollback module.", "build", "low", 5)
    print(f"Decomposed {parent} -> 5 subtasks")

    # 2. RBAC (feature + story)
    for parent in ["task-a5b62160cd", "task-c36ed72566"]:
        mark_decomposed(conn, parent)
        create_subtask(conn, parent, "RBAC: Migration Engineer role", "Read-write on wave plans and executor, read-only on compliance. Single role definition + DB row.", "build", "low", 1)
        create_subtask(conn, parent, "RBAC: ISSO role", "Read-write on compliance posture, read-only on all else. Single role definition + DB row.", "build", "low", 2)
        create_subtask(conn, parent, "RBAC: Component Admin role", "Read-write on workload inventory. Single role definition + DB row.", "build", "low", 3)
        create_subtask(conn, parent, "RBAC: Read-Only Auditor role", "Read-only all data. Single role definition + DB row.", "build", "low", 4)
        create_subtask(conn, parent, "RBAC: CISO role", "Read-only executive dashboard. Single role definition + DB row.", "build", "low", 5)
        create_subtask(conn, parent, "RBAC: Permission matrix enforcement", "Implement AC-2 account management via IdAM. Principle of least privilege. Single auth module.", "build", "low", 6)
        print(f"Decomposed {parent} -> 6 subtasks")

    # 3. UAT/DoD
    parent = "task-d9632e43e7"
    mark_decomposed(conn, parent)
    create_subtask(conn, parent, "DoD: Workload Discovery sign-off", "95% of CMDB assets imported with MAP scores. Single validation script.", "build", "low", 1)
    create_subtask(conn, parent, "DoD: Wave Planner sign-off", "Waves sequenced and ServiceNow CR auto-created. Single validation script.", "build", "low", 2)
    create_subtask(conn, parent, "DoD: Compliance Checker sign-off", "STIG delta report generated. Single validation script.", "build", "low", 3)
    create_subtask(conn, parent, "DoD: Executor sign-off", "Health checks pass. Single validation script.", "build", "low", 4)
    print(f"Decomposed {parent} -> 4 subtasks")

    # 4. Incident response (feature + story)
    for parent in ["task-341c9fa6a3", "task-ed5d7551c4"]:
        mark_decomposed(conn, parent)
        create_subtask(conn, parent, "IR: Splunk SIEM detection integration", "Automated detection with DISA threat signatures. Single connector module.", "build", "low", 1)
        create_subtask(conn, parent, "IR: P1 incident notification", "ISSO notified within 1 hour, US-CERT within 1 business day per FISMA IR-6. Single notification module.", "build", "low", 2)
        create_subtask(conn, parent, "IR: Incident response plan maintenance", "Plan maintained and tracked. Single document + DB table.", "build", "low", 3)
        create_subtask(conn, parent, "IR: Annual tabletop exercise", "Tabletop exercise conducted annually. Single scheduler + template.", "build", "low", 4)
        create_subtask(conn, parent, "IR: eMASS incident tracking", "All incidents tracked in eMASS. Single integration module.", "build", "low", 5)
        print(f"Decomposed {parent} -> 5 subtasks")

    # 5. Data classification (feature + story)
    for parent in ["task-afea4889fd", "task-7f1c9e3010"]:
        mark_decomposed(conn, parent)
        create_subtask(conn, parent, "Data classification: workload metadata", "Tagging and classification for workload metadata. Single module.", "build", "low", 1)
        create_subtask(conn, parent, "Data classification: migration data", "Tagging and classification for migration data. Single module.", "build", "low", 2)
        create_subtask(conn, parent, "Data classification: generated artifacts", "Tagging and classification for generated artifacts. Single module.", "build", "low", 3)
        print(f"Decomposed {parent} -> 3 subtasks")

    # 6. Disaster recovery (feature + story)
    for parent in ["task-c7f02c5c2d", "task-438a8a687b"]:
        mark_decomposed(conn, parent)
        create_subtask(conn, parent, "DR: Multi-AZ failover to us-east-2", "Configure Multi-AZ with 4h RTO / 15m RPO. Single Terraform module.", "build", "low", 1)
        create_subtask(conn, parent, "DR: Daily RDS snapshots to separate account", "Automated daily RDS snapshots in separate AWS account. Single Lambda + IAM.", "build", "low", 2)
        create_subtask(conn, parent, "DR: Quarterly DR failover test", "Test DR failover quarterly. Single test script + scheduler.", "build", "low", 3)
        create_subtask(conn, parent, "DR: S3 cross-region replication", "Enable S3 cross-region replication. Single Terraform module.", "build", "low", 4)
        create_subtask(conn, parent, "DR: Backup verification Lambda", "Automated backup verification via Lambda. Single function.", "build", "low", 5)
        create_subtask(conn, parent, "DR: RTO/RPO monitoring dashboard", "Monitor and alert on RTO/RPO compliance. Single dashboard widget.", "build", "low", 6)
        print(f"Decomposed {parent} -> 6 subtasks")

    # 7. Audit logging (story + feature)
    for parent in ["task-4274962bf7", "task-598e8b399e"]:
        mark_decomposed(conn, parent)
        create_subtask(conn, parent, "Audit: User action logging", "Capture all user actions with tamper-evident storage in Splunk. Single module.", "build", "low", 1)
        create_subtask(conn, parent, "Audit: API call logging", "Capture all API calls with tamper-evident storage in Splunk. Single module.", "build", "low", 2)
        create_subtask(conn, parent, "Audit: Migration event logging", "Capture all migration events with tamper-evident storage in Splunk. Single module.", "build", "low", 3)
        print(f"Decomposed {parent} -> 3 subtasks")

    # 8. Project timeline (feature + story)
    for parent in ["task-81bebda6c0", "task-4b9ddbdf3b"]:
        mark_decomposed(conn, parent)
        create_subtask(conn, parent, "Timeline: PI 1-3 discovery and planning", "18-month project: discovery and planning phase artifacts. Single doc/module.", "build", "low", 1)
        create_subtask(conn, parent, "Timeline: PI 4-6 execution", "18-month project: execution phase artifacts. Single doc/module.", "build", "low", 2)
        create_subtask(conn, parent, "Timeline: PI 7-9 dashboard and integrations", "18-month project: dashboard and integration phase artifacts. Single doc/module.", "build", "low", 3)
        print(f"Decomposed {parent} -> 3 subtasks")

    # 9. ATO extension (3 duplicates)
    for parent in ["task-c790ef5d71", "task-7f0ecf9c42", "task-b16cb05be9"]:
        mark_decomposed(conn, parent)
        create_subtask(conn, parent, "ATO: Extend DISA RMF boundary diagram", "Update boundary diagram to include cloud-hosted migration tooling. Single diagram file.", "build", "low", 1)
        create_subtask(conn, parent, "ATO: Update SSP for cloud components", "Update System Security Plan with cloud component details. Single doc.", "build", "low", 2)
        create_subtask(conn, parent, "ATO: eMASS package update", "Submit updated ATO package to eMASS for cloud extension. Single workflow.", "build", "low", 3)
        print(f"Decomposed {parent} -> 3 subtasks")

    conn.commit()
    conn.close()
    print("Done. All parents marked decomposed, subtasks created.")


if __name__ == "__main__":
    main()
