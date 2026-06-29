STANDARD OPERATING PROCEDURE
SOP-NET-001: Network Peering Agreement Review

**VERSION HISTORY**
| Version | Date | Description of Change | Author/Tool |
| :--- | :--- | :--- | :--- |
| 2.0 | 2026-06-27 | AI-enabled process update via Process-Ify to digitize workflow, embed role accountability, and integrate dynamic checklists. | Process-Ify / Engineering Dept |

1.0 PURPOSE  
This SOP defines the end-to-end automated process for reviewing and approving network peering agreements between carrier entities. The procedure ensures strict adherence to routing policy mandates, security compliance standards, commercial viability requirements, and operational safety protocols through a structured digitized workflow.

2.0 SCOPE  
Applies to all inbound and outbound peering requests originating at Tier-1 and Tier-2 interconnection points. This SOP governs the lifecycle of peer establishment from initial request intake through production cutover and registry documentation for both settlement-free and paid peering arrangements.

3.0 PROCEDURE  

**3.1 Request Intake (Step 1)**  
The Network Engineer initiates the workflow by ingesting the formal Peering Request Form submitted by the partner carrier via the Digital Operations Portal. The assignee validates the requested Autonomous System Number (ASN), projected traffic volumes, and proposed peering location against infrastructure constraints. Simultaneously, the Compliance Officer executes an automated check to verify that the requesting entity does not appear on the restricted entities list before proceeding to subsequent review phases. Refer to *Checklist: Peering Request Intake & Entity Verification*.

**3.2 Capacity Assessment (Step 2)**  
The Network Architect assesses available physical and logical capacity at the proposed peering point to determine feasibility. This step evaluates port utilization, optical circuit availability, and bandwidth scaling capabilities required for the projected traffic load. The architect documents findings regarding potential bottlenecks or necessary upgrades prior to advancing to security reviews. Refer to *Checklist: Port Capacity & Infrastructure Feasibility*.

**3.3 Routing and BGP Security Review (Step 3)**  
The Security Team conducts a comprehensive review of the partner's routing policy, filtering rules, and overall BGP security posture to mitigate hijacking or prefix leak risks. The Network Operations Center (NOC) reviews this data specifically for technical validation regarding prefix limits and maximum-prefix thresholds, ensuring they align with internal redistribution policies and router table space constraints. Refer to *Checklist: Routing Policy & BGP Security Validation*.

**3.4 Commercial Terms Review (Step 4)**  
The Commercial Manager analyzes the settlement terms associated with the peering relationship, determining applicability of either a settlement-free or paid peering model based on traffic symmetry and market standards. This review confirms that commercial expectations meet internal business development goals before Legal engagement occurs. Refer to *Checklist: Settlement Terms & Traffic Symmetry Analysis*.

**3.5 Commercial Review (Step 4 - Continuation)**  
*Correction:* The original workflow separated Commercial and Legal reviews into distinct steps for clarity in execution tracking, though they occurred sequentially. Please refer to **Section 3.6** below for the formalized Legal Agreement Review step as defined in Step 5 of the Process-Ify data.

**3.6 Legal Agreement Review (Step 5)**  
Legal Counsel scrutinizes the draft agreement specifically for liability caps, Service Level Agreements (SLAs), and termination clauses to ensure corporate risk management standards are met. This review occurs concurrently with or immediately following the Commercial Terms evaluation but constitutes a distinct approval gate within the digital workflow. Refer to *Checklist: Legal Liability & Contractual Clause Validation*.

**3.7 Financial Approval (Step 6)**  
The Finance Manager evaluates and approves the projected cost impact of the new peering relationship, including any capital expenditures for port upgrades or operational expenses related to traffic settlement payments. The Finance Director serves as the final approver on this financial gate before executive technical approval is solicited. Refer to *Checklist: Cost Impact & Budget Authorization*.

**3.8 Executive Approval (Step 7)**  
The VP of Network grants final authorization for agreements exceeding a capacity threshold of 10 Gbps or those involving strategic carrier partnerships. This step serves as the definitive go/no-go decision point before any configuration work begins in the production environment. Refer to *Checklist: High-Capacity & Strategic Partner Authorization*.

**3.9 BGP Staging Configuration (Step 8)**  
The Network Engineer configures the initial BGP session within the isolated staging environment using sandboxed router instances and dummy prefixes. This phase allows for validation of peering parameters without impacting live customer traffic or production routing tables. Refer to *Checklist: Staging Environment Session Initialization*.

**3.10 Staging Validation and Monitoring (Step 9)**  
The NOC validates the test session in staging, monitoring connection stability, prefix advertisement consistency, and error logs for a continuous period of 48 hours. This extended observation window ensures long-term reliability before authorizing the transition to production status. Refer to *Checklist: Staging Stability & Long-Duration Monitoring*.

**3.11 Production Cutover (Step 10)**  
Upon successful completion of staging validation, the Network Engineer executes the production cutover by propagating prefixes and activating the BGP session against live routers at the interconnection point. The NOC observes traffic flow during this transition to confirm seamless integration with existing routing domains. Refer to *Checklist: Production Deployment & Traffic Migration*.

**3.12 Peering Registry Logging (Step 11)**  
The Network Operations team logs all approved agreement details, including peer ASNs, peering type, capacity limits, and effective dates into the central Peering Registry database for auditability and reporting purposes. This entry creates a permanent record of the relationship within the organizational asset inventory. Refer to *Checklist: Agreement Documentation & Asset Registration*.

**3.13 Routing Policy Database Update (Step 12)**  
The Network Engineer updates the global routing policy database with new peer prefixes provided during cutover, ensuring internal routers recognize and prefer traffic from this new partner according to defined local preference metrics. The Network Architect reviews these changes to ensure consistency across the broader BGP table structure before final commit. Refer to *Checklist: Global Routing Table Maintenance & Prefix Injection*.