# Goal: External Integration Layer (RICOAS Phase 4)

## Purpose
Bidirectional sync with Jira, ServiceNow, and GitLab. Export to DOORS NG via ReqIF. Approval workflows. Full RTM traceability.

## When to Use
- Push decomposed requirements to Jira/ServiceNow/GitLab
- Pull status updates from external systems
- Export requirements to DOORS NG via ReqIF 1.2
- Submit requirements package for approval
- Build full Requirements Traceability Matrix

## Workflow

### External System Sync
1. Configure integration: `configure_jira`/`configure_servicenow`/`configure_gitlab`
2. Push decomposed items: `sync_jira`/`sync_servicenow`/`sync_gitlab` with direction=push
3. Pull status updates: direction=pull
4. Check sync status: view last sync info and mapping count

### DOORS NG Export
1. Export requirements as ReqIF: `export_reqif` with output_path
2. Validate exported file: `doors_exporter.py --validate`
3. Import into DOORS NG via standard ReqIF import

### Approval Workflow
1. Submit for approval: `submit_approval` with type and reviewers
2. Reviewers decide: `review_approval` with approved/rejected/conditional
3. Track pending: list pending approvals

### Traceability
1. Build RTM: `build_traceability` — links requirement→SysML→code→test→control
2. Gap analysis: identify requirements with missing trace links
3. Coverage metric: percentage of fully traced requirements

## Visual Workflow

### Integration Pipeline Flowchart

```mermaid
flowchart TD
    A["Configure Integration"] --> B{System?}
    B -->|"Jira"| C1["Configure Jira"]
    B -->|"ServiceNow"| C2["Configure ServiceNow"]
    B -->|"GitLab"| C3["Configure GitLab"]
    C1 --> D["Push/Pull Sync"]
    C2 --> D
    C3 --> D
    D --> E["DOORS NG ReqIF Export"]
    E --> F["Approval Workflow"]
    F --> G{Decision?}
    G -->|"Approved"| H["Build RTM"]
    G -->|"Rejected"| I["Return to Intake"]
    G -->|"Conditional"| J["Address Conditions"] --> F
    H --> K["Gap Analysis"]
    K --> L["Full Traceability Achieved"]

    style A fill:#1a3a5c,stroke:#4a90d9,color:#e0e0e0
    style B fill:#1a3a5c,stroke:#4a90d9,color:#e0e0e0
    style C1 fill:#1a3a5c,stroke:#4a90d9,color:#e0e0e0
    style C2 fill:#1a3a5c,stroke:#4a90d9,color:#e0e0e0
    style C3 fill:#1a3a5c,stroke:#4a90d9,color:#e0e0e0
    style D fill:#1a3a5c,stroke:#4a90d9,color:#e0e0e0
    style E fill:#1a3a5c,stroke:#4a90d9,color:#e0e0e0
    style F fill:#3a3a1a,stroke:#ffc107,color:#e0e0e0
    style G fill:#3a2a1a,stroke:#e8590c,color:#e0e0e0
    style H fill:#1a3a2d,stroke:#28a745,color:#e0e0e0
    style I fill:#3a1a1a,stroke:#dc3545,color:#e0e0e0
    style J fill:#3a3a1a,stroke:#ffc107,color:#e0e0e0
    style K fill:#3a3a1a,stroke:#ffc107,color:#e0e0e0
    style L fill:#1a3a2d,stroke:#28a745,color:#e0e0e0
```

### RTM Traceability Sequence

```mermaid
sequenceDiagram
    participant REQ as Requirement
    participant MDL as SysML Model
    participant CODE as Code Module
    participant TEST as Test Case
    participant CTRL as NIST Control

    REQ->>MDL: trace_forward (requirement → model element)
    MDL->>CODE: generate_code (model → implementation)
    CODE->>TEST: write_tests (code → test coverage)
    TEST->>CTRL: control_map (test evidence → NIST 800-53)
    CTRL-->>REQ: trace_backward (control → originating requirement)

    Note over REQ,CTRL: Full bidirectional traceability chain
    Note over REQ,MDL: Digital thread link
    Note over CODE,TEST: TDD RED → GREEN → REFACTOR
    Note over TEST,CTRL: Compliance evidence
```

---

## Tools Used
| Tool | Purpose |
|------|---------|
| tools/integration/jira_connector.py | Bidirectional Jira sync |
| tools/integration/servicenow_connector.py | Bidirectional ServiceNow sync |
| tools/integration/gitlab_connector.py | Bidirectional GitLab sync |
| tools/integration/doors_exporter.py | ReqIF 1.2 export for DOORS NG |
| tools/integration/approval_manager.py | Approval workflow management |
| tools/requirements/traceability_builder.py | Full RTM builder |
| tools/mcp/integration_server.py | MCP server (10 tools) |

## Edge Cases
- Jira/ServiceNow/GitLab unreachable → log error, preserve local state
- Conflicting changes (modified in both systems) → flag conflict, don't overwrite
- ReqIF export with no requirements → return empty but valid XML
- Approval timeout → escalate after configurable period
