#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""
ICDEV Mermaid Diagram Definitions
==================================
Centralized store of all Mermaid diagram strings used in the dashboard.
Organized by category for the /diagrams catalog page and embedded views.

Usage:
    from tools.dashboard.diagram_definitions import DIAGRAM_CATALOG
"""

DIAGRAM_CATALOG = {

    # ── Workflow Diagrams ───────────────────────────────────────────

    "atlas_workflow": {
        "title": "ATLAS Build Workflow",
        "description": "5-step application build process: Architect, Trace, Link, Assemble, Stress-test",
        "category": "workflows",
        "roles": [],
        "mermaid": """flowchart LR
    A["A: Architect<br/>Define problem, users,<br/>success metrics"]
    T["T: Trace<br/>Data schema,<br/>integrations, stack"]
    L["L: Link<br/>Validate connections,<br/>test APIs"]
    As["A: Assemble<br/>Build layers<br/>DB → Backend → UI"]
    S["S: Stress-test<br/>Functional, integration,<br/>edge case tests"]
    A --> T --> L --> As --> S
    S -.->|Issues found| As
    style A fill:#1a3a5c,stroke:#4a90d9,color:#e0e0e0
    style S fill:#1a3a2d,stroke:#28a745,color:#e0e0e0
""",
    },

    "m_atlas_workflow": {
        "title": "M-ATLAS Workflow (MBSE-Enabled)",
        "description": "6-step build with Model pre-phase for MBSE projects",
        "category": "workflows",
        "roles": [],
        "mermaid": """flowchart LR
    MBSE{MBSE<br/>enabled?}
    M["M: Model<br/>Import XMI/ReqIF,<br/>digital thread"]
    A["A: Architect"]
    T["T: Trace"]
    L["L: Link"]
    As["A: Assemble"]
    S["S: Stress-test"]
    MBSE -->|Yes| M --> A
    MBSE -->|No| A
    A --> T --> L --> As --> S
    style MBSE fill:#3a3a1a,stroke:#ffc107,color:#e0e0e0
    style M fill:#2d1a3a,stroke:#9b59b6,color:#e0e0e0
""",
    },

    "tdd_cycle": {
        "title": "TDD Cycle (RED-GREEN-REFACTOR)",
        "description": "Test-Driven Development state machine: write failing test, implement, refactor",
        "category": "workflows",
        "roles": ["developer"],
        "mermaid": """stateDiagram-v2
    [*] --> RED : Write failing test
    RED --> GREEN : Write minimum code
    GREEN --> REFACTOR : Clean up
    REFACTOR --> RED : Next requirement
    REFACTOR --> [*] : Feature complete

    state RED {
        [*] --> WriteGherkin : Feature file
        WriteGherkin --> WritePytest : Step definitions
        WritePytest --> RunFailing : Must FAIL
    }
    state GREEN {
        [*] --> GenerateCode : Minimum implementation
        GenerateCode --> RunPassing : Must PASS
        RunPassing --> CheckCoverage : Coverage >= 80%
    }
    state REFACTOR {
        [*] --> Lint : flake8 / ruff
        Lint --> Format : black
        Format --> ReRun : Tests still pass?
    }
""",
    },

    "tdd_sequence": {
        "title": "TDD Tool Sequence",
        "description": "Sequence diagram showing tool interactions during a TDD cycle",
        "category": "workflows",
        "roles": ["developer"],
        "mermaid": """sequenceDiagram
    participant O as Orchestrator
    participant TW as test_writer.py
    participant PT as pytest
    participant CG as code_generator.py
    participant LI as linter.py
    participant FM as formatter.py
    participant AL as audit_logger.py

    O->>TW: Write Gherkin + tests
    TW-->>O: Feature file + test files
    O->>PT: Run tests (RED)
    PT-->>O: All FAIL (expected)
    O->>CG: Generate implementation
    CG-->>O: Source code
    O->>PT: Run tests (GREEN)
    PT-->>O: All PASS + coverage
    O->>LI: Lint code
    LI-->>O: Clean
    O->>FM: Format code
    FM-->>O: Formatted
    O->>PT: Re-run tests
    PT-->>O: Still passing
    O->>AL: Log TDD cycle
""",
    },

    # ── Compliance Diagrams ─────────────────────────────────────────

    "compliance_pipeline": {
        "title": "Compliance Pipeline",
        "description": "19-step ATO artifact generation: FIPS 199 through IV&V",
        "category": "compliance",
        "roles": ["isso", "pm", "co"],
        "mermaid": """flowchart TD
    subgraph Foundation
        S0["FIPS 199<br/>Categorization"]
        S0b["FIPS 200<br/>Validation"]
    end
    subgraph Core["Core ATO Artifacts"]
        S1["SSP<br/>System Security Plan"]
        S2["POAM<br/>Plan of Action"]
        S3["STIG<br/>Checklist"]
        S4["SBOM<br/>Software Bill"]
        S5["CUI<br/>Markings"]
        S6["NIST<br/>Control Mapping"]
        S7["Status<br/>Report"]
    end
    subgraph CSSP["CSSP (DI 8530.01)"]
        S9["CSSP Assess"]
        S10["SIEM Config"]
        S11["IR Plan"]
        S12["Evidence"]
        S13["CSSP Report"]
        S14["Xacta Sync"]
    end
    subgraph SbD_IVV["SbD + IV&V"]
        S15["SbD Assess"]
        S16["SbD Report"]
        S17["RTM"]
        S18["IV&V Assess"]
        S19["IV&V Report"]
    end
    S0 --> S0b --> S1
    S1 --> S2 & S3 & S4
    S2 & S3 & S4 --> S5 --> S6 --> S7
    S7 --> S9
    S9 --> S10 & S11
    S10 & S11 --> S12 --> S13 --> S14
    S7 --> S15 --> S16
    S7 --> S17 --> S18 --> S19
    style S0 fill:#1a3a5c,stroke:#4a90d9,color:#e0e0e0
    style S7 fill:#1a3a2d,stroke:#28a745,color:#e0e0e0
    style S19 fill:#1a3a2d,stroke:#28a745,color:#e0e0e0
""",
    },

    "artifact_dependencies": {
        "title": "ATO Artifact Dependencies",
        "description": "Class diagram showing relationships between compliance artifacts",
        "category": "compliance",
        "roles": ["isso", "pm"],
        "mermaid": """classDiagram
    class SSP {
        +17 sections
        +FIPS 199 categorization
        +system boundary
    }
    class POAM {
        +findings list
        +milestones
        +remediation plans
    }
    class STIG {
        +CAT-I findings
        +CAT-II findings
        +CAT-III findings
    }
    class SBOM {
        +components list
        +licenses
        +vulnerabilities
    }
    class CUI_Markings {
        +banner templates
        +portion markings
        +distribution statements
    }
    SSP --> POAM : feeds
    STIG --> POAM : findings feed
    SBOM --> POAM : vuln findings
    SSP --> STIG : controls map
    CUI_Markings --> SSP : applied to
    CUI_Markings --> POAM : applied to
""",
    },

    # ── Security Diagrams ───────────────────────────────────────────

    "security_scan_pipeline": {
        "title": "Security Scan Pipeline",
        "description": "4-scanner chain with quality gate decision points",
        "category": "security",
        "roles": ["developer", "isso"],
        "mermaid": """flowchart TD
    START([Start Security Scan]) --> SAST
    subgraph Pipeline["Security Scanning Pipeline"]
        SAST["1. SAST<br/>bandit / eslint-security"]
        DEP["2. Dependency Audit<br/>pip-audit / npm audit"]
        SEC["3. Secret Detection<br/>detect-secrets"]
        CON["4. Container Scan<br/>trivy"]
    end
    SAST --> G1{0 critical<br/>0 high?}
    G1 -->|PASS| DEP
    G1 -->|FAIL| BLOCK1[BLOCKED<br/>Remediate findings]
    DEP --> G2{0 critical<br/>0 high vuln?}
    G2 -->|PASS| SEC
    G2 -->|FAIL| BLOCK2[BLOCKED<br/>Update deps]
    SEC --> G3{0 secrets<br/>detected?}
    G3 -->|PASS| CON
    G3 -->|FAIL| BLOCK3[BLOCKED<br/>Rotate and remove]
    CON --> G4{0 critical<br/>0 high?}
    G4 -->|PASS| REPORT["Consolidated<br/>Security Report"]
    G4 -->|FAIL| BLOCK4[BLOCKED<br/>Fix container]
    REPORT --> AUDIT["Audit Trail"]
    style BLOCK1 fill:#3a1a1a,stroke:#dc3545,color:#e0e0e0
    style BLOCK2 fill:#3a1a1a,stroke:#dc3545,color:#e0e0e0
    style BLOCK3 fill:#3a1a1a,stroke:#dc3545,color:#e0e0e0
    style BLOCK4 fill:#3a1a1a,stroke:#dc3545,color:#e0e0e0
    style REPORT fill:#1a3a2d,stroke:#28a745,color:#e0e0e0
""",
    },

    "self_healing_flow": {
        "title": "Self-Healing Decision Logic",
        "description": "Confidence-based remediation flow with rate limiting",
        "category": "security",
        "roles": ["developer", "isso"],
        "mermaid": """flowchart TD
    F[Failure Detected] --> A{Confidence<br/>Level?}
    A -->|>= 0.7| AUTO[Auto-Remediate]
    A -->|0.3 - 0.7| SUGGEST[Suggest Fix<br/>Await Approval]
    A -->|< 0.3| ESC[Escalate with<br/>Full Context]
    AUTO --> CHECK{Heals per hour<br/>< 5?}
    CHECK -->|Yes| HEAL[Execute<br/>Remediation]
    CHECK -->|No| COOL[Cooldown<br/>10 min]
    HEAL --> LOG[Log to<br/>Audit Trail]
    SUGGEST --> LOG
    ESC --> LOG
    style AUTO fill:#1a3a2d,stroke:#28a745,color:#e0e0e0
    style ESC fill:#3a1a1a,stroke:#dc3545,color:#e0e0e0
    style SUGGEST fill:#3a3a1a,stroke:#ffc107,color:#e0e0e0
""",
    },

    # ── Architecture Diagrams ───────────────────────────────────────

    "deploy_pipeline": {
        "title": "Deployment Pipeline",
        "description": "IaC generation through 7-stage CI/CD with gates",
        "category": "architecture",
        "roles": [],
        "mermaid": """flowchart TD
    subgraph IaC["IaC Generation"]
        TF["Terraform<br/>AWS GovCloud"]
        AN["Ansible<br/>STIG hardening"]
        K8["Kubernetes<br/>Security contexts"]
        PL["CI/CD Pipeline<br/>.gitlab-ci.yml"]
    end
    TF --> AN --> K8 --> PL
    PL --> GATE{10 Pre-Deploy<br/>Gates}
    GATE -->|ALL PASS| COMMIT["Git Commit<br/>and Push"]
    GATE -->|ANY FAIL| STOP([BLOCKED])
    COMMIT --> PIPE["Pipeline Execution"]
    subgraph Stages["7 CI/CD Stages"]
        B["1. Build"] --> T["2. Test"]
        T --> S["3. SAST"]
        S --> D["4. Deps"]
        D --> C["5. Container"]
        C --> CO["6. Compliance"]
        CO --> DE["7. Deploy"]
    end
    PIPE --> B
    DE --> HEALTH["Health Check"]
    HEALTH --> AUDIT["Audit Log"]
    style STOP fill:#3a1a1a,stroke:#dc3545,color:#e0e0e0
    style HEALTH fill:#1a3a2d,stroke:#28a745,color:#e0e0e0
""",
    },

    "agent_topology": {
        "title": "Multi-Agent Architecture",
        "description": "13 agents across 3 tiers communicating via A2A protocol",
        "category": "architecture",
        "roles": [],
        "mermaid": """flowchart TD
    subgraph Core["Core Tier"]
        ORCH["Orchestrator<br/>:8443"]
        ARCH["Architect<br/>:8444"]
    end
    subgraph Domain["Domain Tier"]
        BUILD["Builder<br/>:8445"]
        COMP["Compliance<br/>:8446"]
        SEC["Security<br/>:8447"]
        INFRA["Infra<br/>:8448"]
        MBSE["MBSE<br/>:8451"]
        MOD["Modernization<br/>:8452"]
        REQ["Requirements<br/>:8453"]
        SC["Supply Chain<br/>:8454"]
        SIM["Simulation<br/>:8455"]
    end
    subgraph Support["Support Tier"]
        KNOW["Knowledge<br/>:8449"]
        MON["Monitor<br/>:8450"]
    end
    ORCH <-->|A2A mTLS| ARCH
    ORCH <-->|A2A mTLS| BUILD
    ORCH <-->|A2A mTLS| COMP
    ORCH <-->|A2A mTLS| SEC
    ORCH <-->|A2A mTLS| INFRA
    ORCH <-->|A2A mTLS| MBSE
    ORCH <-->|A2A mTLS| MOD
    ORCH <-->|A2A mTLS| REQ
    ORCH <-->|A2A mTLS| SC
    ORCH <-->|A2A mTLS| SIM
    ORCH <-->|A2A mTLS| KNOW
    ORCH <-->|A2A mTLS| MON
    style ORCH fill:#1a3a5c,stroke:#4a90d9,color:#e0e0e0
""",
    },

    "gotcha_layers": {
        "title": "GOTCHA Framework Layers",
        "description": "6-layer agentic system: Goals, Orchestration, Tools, Context, Hard Prompts, Args",
        "category": "architecture",
        "roles": [],
        "mermaid": """flowchart TD
    G["Goals<br/>Process definitions<br/>goals/"]
    O["Orchestration<br/>AI decides tool order<br/>(Claude)"]
    T["Tools<br/>Deterministic Python<br/>tools/"]
    A["Args<br/>Behavior settings<br/>args/"]
    C["Context<br/>Static references<br/>context/"]
    H["Hard Prompts<br/>LLM templates<br/>hardprompts/"]
    G --> O
    O --> T
    A -.->|configure| T
    C -.->|inform| O
    H -.->|instruct| O
    style G fill:#1a3a5c,stroke:#4a90d9,color:#e0e0e0
    style O fill:#2d1a3a,stroke:#9b59b6,color:#e0e0e0
    style T fill:#1a3a2d,stroke:#28a745,color:#e0e0e0
""",
    },

    # ── RICOAS Diagrams ─────────────────────────────────────────────

    "intake_flow": {
        "title": "Requirements Intake Flow",
        "description": "6-stage AI-driven conversational intake with readiness gate loop",
        "category": "workflows",
        "roles": [],
        "mermaid": """flowchart TD
    S1["Stage 1:<br/>Session Setup<br/>customer info, IL, ATO"]
    S2["Stage 2:<br/>Conversational Intake<br/>AI-guided Q&A"]
    S3["Stage 3:<br/>Document Upload<br/>SOW/CDD/CONOPS"]
    S4["Stage 4:<br/>Gap Detection<br/>& Readiness Scoring"]
    S5["Stage 5:<br/>SAFe Decomposition<br/>Epic > Feature > Story"]
    S6["Stage 6:<br/>Export & Handoff<br/>to Architect agent"]
    S1 --> S2
    S2 --> S3
    S3 --> S4
    S2 -->|every 5 turns| S4
    S4 -->|score < 0.7| S2
    S4 -->|score >= 0.7| S5
    S5 --> S6
    style S4 fill:#3a3a1a,stroke:#ffc107,color:#e0e0e0
    style S6 fill:#1a3a2d,stroke:#28a745,color:#e0e0e0
""",
    },

    "safe_hierarchy": {
        "title": "SAFe Decomposition Hierarchy",
        "description": "Epic > Capability > Feature > Story > Enabler hierarchy with sizing",
        "category": "workflows",
        "roles": [],
        "mermaid": """classDiagram
    class Epic {
        +name: string
        +wsjf_score: float
        +t_shirt: XL-XXL
    }
    class Capability {
        +name: string
        +t_shirt: L-XL
    }
    class Feature {
        +name: string
        +wsjf_score: float
        +t_shirt: M-L
    }
    class Story {
        +name: string
        +acceptance_criteria: Gherkin
        +t_shirt: XS-M
    }
    class Enabler {
        +type: infrastructure
        +t_shirt: S-L
    }
    Epic "1" --> "*" Capability
    Capability "1" --> "*" Feature
    Feature "1" --> "*" Story
    Feature "1" --> "*" Enabler
""",
    },

    "boundary_tiers": {
        "title": "ATO Boundary Impact Tiers",
        "description": "4-tier decision tree: GREEN, YELLOW, ORANGE, RED with risk scoring",
        "category": "compliance",
        "roles": ["isso", "pm"],
        "mermaid": """flowchart TD
    REQ[New Requirement] --> ASSESS{Assess<br/>Boundary Impact}
    ASSESS --> SC{Score<br/>Range?}
    SC -->|0 - 25| GREEN["GREEN<br/>No ATO impact<br/>Proceed"]
    SC -->|26 - 50| YELLOW["YELLOW<br/>SSP addendum<br/>ISSO notification"]
    SC -->|51 - 75| ORANGE["ORANGE<br/>SSP revision<br/>Security review"]
    SC -->|76 - 100| RED["RED<br/>ATO-invalidating<br/>FULL STOP"]
    RED --> ALT["Generate Alternative<br/>COAs"]
    ALT --> SELECT{Customer<br/>selects COA}
    SELECT -->|Within boundary| GREEN
    SELECT -->|No alternative| REAUTH["Re-authorization<br/>+3-6 months"]
    style GREEN fill:#1a3a2d,stroke:#28a745,color:#e0e0e0
    style YELLOW fill:#3a3a1a,stroke:#ffc107,color:#e0e0e0
    style ORANGE fill:#3a2a1a,stroke:#e8590c,color:#e0e0e0
    style RED fill:#3a1a1a,stroke:#dc3545,color:#e0e0e0
    style REAUTH fill:#3a1a1a,stroke:#dc3545,color:#e0e0e0
""",
    },

    "isa_lifecycle": {
        "title": "ISA Lifecycle State Machine",
        "description": "Interconnection Security Agreement states from draft to expired",
        "category": "compliance",
        "roles": ["isso"],
        "mermaid": """stateDiagram-v2
    [*] --> Draft : Create ISA
    Draft --> Active : Both parties sign
    Active --> ReviewDue : Review period reached
    ReviewDue --> Active : Review approved
    ReviewDue --> Expiring : No review action
    Active --> Expiring : 90 days before expiry
    Expiring --> Renewed : Renewal approved
    Expiring --> Expired : Past expiry date
    Renewed --> Active
    Expired --> [*] : Data flows suspended
    Active --> Terminated : Either party terminates
    Terminated --> [*]
""",
    },

    "simulation_workflow": {
        "title": "Digital Program Twin Simulation",
        "description": "Create scenario, simulate 6 dimensions, Monte Carlo, generate and compare COAs",
        "category": "workflows",
        "roles": [],
        "mermaid": """flowchart TD
    CREATE["Create Scenario"] --> RUN["Run Simulation"]
    RUN --> DIMS
    subgraph DIMS["6 Dimensions"]
        ARCH["Architecture"]
        COMP["Compliance"]
        SUPC["Supply Chain"]
        SCHED["Schedule"]
        COST["Cost"]
        RISK["Risk"]
    end
    SCHED --> MC["Monte Carlo<br/>N iterations"]
    COST --> MC
    MC --> P50["P50: Likely"]
    MC --> P80["P80: Mgmt Reserve"]
    MC --> P90["P90: Conservative"]
    RUN --> COA["Generate 3 COAs"]
    COA --> SPEED["Speed COA<br/>P1 only, 1-2 PIs"]
    COA --> BAL["Balanced COA<br/>P1+P2, 2-3 PIs"]
    COA --> FULL["Comprehensive<br/>Full scope, 3-5 PIs"]
    SPEED & BAL & FULL --> COMPARE["Compare COAs"]
    COMPARE --> SELECT["Customer Selects"]
    style BAL fill:#1a3a5c,stroke:#4a90d9,color:#e0e0e0
    style SELECT fill:#1a3a2d,stroke:#28a745,color:#e0e0e0
""",
    },

    "integration_sync": {
        "title": "External Integration Sync",
        "description": "Bidirectional sync with Jira, ServiceNow, GitLab, and DOORS NG",
        "category": "workflows",
        "roles": [],
        "mermaid": """flowchart TD
    subgraph Config["Configuration"]
        CJ["Configure<br/>Jira"]
        CSN["Configure<br/>ServiceNow"]
        CGL["Configure<br/>GitLab"]
    end
    subgraph Sync["Bidirectional Sync"]
        PUSH["Push<br/>Decomposed Items"]
        PULL["Pull<br/>Status Updates"]
    end
    subgraph Export["DOORS NG"]
        REQIF["Export<br/>ReqIF 1.2"]
        VALID["Validate<br/>Exported File"]
    end
    subgraph Approve["Approval"]
        SUBMIT["Submit for<br/>Approval"]
        REVIEW["Reviewer<br/>Decision"]
    end
    subgraph Trace["Traceability"]
        RTM["Build RTM"]
        GAP["Gap Analysis"]
    end
    CJ & CSN & CGL --> PUSH & PULL
    PUSH --> REQIF --> VALID
    PULL --> RTM --> GAP
    PUSH --> SUBMIT --> REVIEW
    style RTM fill:#1a3a5c,stroke:#4a90d9,color:#e0e0e0
""",
    },

    "rtm_traceability": {
        "title": "RTM Traceability Chain",
        "description": "Requirement to SysML to Code to Test to NIST Control tracing",
        "category": "compliance",
        "roles": [],
        "mermaid": """sequenceDiagram
    participant REQ as Requirement
    participant MODEL as SysML Model
    participant CODE as Code Module
    participant TEST as Test Case
    participant CTRL as NIST Control

    REQ->>MODEL: traced_to
    MODEL->>CODE: generated_from
    CODE->>TEST: tested_by
    REQ->>CTRL: satisfies
    TEST->>REQ: validates
    Note over REQ,CTRL: RTM links all 5 artifact types
    Note over REQ,CTRL: Gap = any missing link in chain
""",
    },

    # ── MOSA Diagrams ───────────────────────────────────────────────

    "mosa_workflow": {
        "title": "MOSA Assessment Workflow",
        "description": "8-step MOSA compliance: detect, assess, analyze, ICD, TSP, enforce, gate, cATO",
        "category": "compliance",
        "roles": ["isso", "pm"],
        "mermaid": """flowchart LR
    D["Detect<br/>DoD/IC signals"] --> A["Assess<br/>25 requirements"]
    A --> AN["Analyze<br/>Modularity metrics"]
    AN --> ICD["Generate<br/>ICDs"]
    ICD --> TSP["Generate<br/>TSP"]
    TSP --> E["Enforce<br/>Code scan"]
    E --> G{Gate<br/>Check}
    G -->|PASS| CATO["cATO<br/>Evidence"]
    G -->|FAIL| FIX["Remediate<br/>Violations"]
    FIX --> AN
    style D fill:#1a3a5c,stroke:#4a90d9,color:#e0e0e0
    style CATO fill:#1a3a2d,stroke:#28a745,color:#e0e0e0
    style FIX fill:#3a3a1a,stroke:#ffc107,color:#e0e0e0
""",
    },

    # ── Zero Trust Diagrams ─────────────────────────────────────────

    "zta_workflow": {
        "title": "Zero Trust Architecture Assessment",
        "description": "ZTA maturity assessment across 7 pillars per NIST 800-207",
        "category": "security",
        "roles": ["isso", "developer"],
        "mermaid": """flowchart TD
    ASSESS["Assess ZTA Maturity"] --> PILLARS
    subgraph PILLARS["7 ZTA Pillars"]
        ID["Identity"]
        DEV["Devices"]
        NET["Networks"]
        APP["Applications"]
        DATA["Data"]
        VIS["Visibility"]
        AUTO["Automation"]
    end
    PILLARS --> SCORE{Maturity<br/>Score}
    SCORE -->|>= 0.34| PASS["Gate PASS"]
    SCORE -->|< 0.34| FAIL["Gate FAIL<br/>Remediate"]
    PASS --> GEN["Generate Artifacts<br/>mTLS, Network Policy,<br/>Service Mesh, PDP"]
    style PASS fill:#1a3a2d,stroke:#28a745,color:#e0e0e0
    style FAIL fill:#3a1a1a,stroke:#dc3545,color:#e0e0e0
""",
    },
}


def get_catalog_for_role(role=None):
    """Return diagram catalog filtered by role visibility.

    Args:
        role: User role string (pm, developer, isso, co) or None for all.

    Returns:
        List of diagram metadata dicts (without full mermaid source).
    """
    catalog = []
    for key, val in DIAGRAM_CATALOG.items():
        roles = val.get("roles", [])
        if roles and role and role not in roles:
            continue
        catalog.append({
            "id": key,
            "title": val["title"],
            "description": val["description"],
            "category": val.get("category", "general"),
        })
    return catalog


def get_diagram(diagram_id):
    """Return full diagram definition by ID, or None if not found."""
    return DIAGRAM_CATALOG.get(diagram_id)
