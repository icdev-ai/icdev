# Phase 8 — Self-Healing System

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Phase | 8 |
| Title | Self-Healing System |
| Status | Implemented |
| Priority | P1 |
| Dependencies | Phase 7 (Security Scanning), Phase 9 (Monitoring & Observability) |
| Author | ICDEV Architect Agent |
| Date | 2026-02-23 |

---

## 1. Problem Statement

Production systems built by ICDEV inevitably encounter failures: services crash, dependencies degrade, configurations drift, and deployments introduce regressions. Without automated remediation, every incident requires human intervention, creating bottlenecks that violate SLA targets and leave Gov/DoD systems vulnerable during the gap between detection and resolution.

Manual incident response is too slow for mission-critical systems operating at IL4/IL5/IL6 impact levels. An operator may take 15-30 minutes to diagnose and fix a known issue that has been solved before. Meanwhile, the system is degraded or offline, impacting warfighter readiness and operational availability.

The Self-Healing System closes this gap by maintaining a growing knowledge base of failure patterns and their proven remediations. When monitoring detects an anomaly, the system matches it against known patterns, evaluates confidence, and either auto-remediates (high confidence), suggests a fix (medium confidence), or escalates with full diagnostic context (low confidence). Every successful fix strengthens the knowledge base, creating a positive feedback loop where the system becomes more resilient over time.

---

## 2. Goals

1. Detect production issues automatically by matching error patterns, log anomalies, and metric threshold breaches against a knowledge base of known failure signatures
2. Apply a 3-tier confidence-based decision engine that auto-remediates at >= 0.7 confidence, suggests fixes at 0.3-0.7, and escalates with full context below 0.3
3. Enforce rate limiting (max 5 auto-heal actions per hour, 10-minute cooldown between identical actions) to prevent remediation storms and cascading failures
4. Implement a feedback loop where successful remediations increase pattern confidence by 0.05 and failures decrease it by 0.1, enabling continuous learning
5. Use Bedrock LLM for root cause analysis when no pattern match exists, generating new pattern candidates for human review
6. Record all self-healing events in the append-only audit trail for NIST IR-4, IR-5, and SI-5 compliance
7. Support cross-project pattern sharing so a fix learned in one project benefits all future projects
8. Prevent remediation loops with a maximum of 3 retries per pattern per incident and circuit breaker protection

---

## 3. Architecture

```
+-----------------------------------------------------------+
|                    Monitoring Layer                         |
|  +------------+  +---------------+  +------------------+   |
|  | Log        |  | Metric        |  | Alert            |   |
|  | Analyzer   |  | Collector     |  | Correlator       |   |
|  +-----+------+  +-------+-------+  +--------+---------+   |
|        |                  |                   |             |
+--------+------------------+-------------------+-------------+
         |                  |                   |
         v                  v                   v
+-----------------------------------------------------------+
|                 Pattern Detection Engine                    |
|  +--------------------------------------------------------+|
|  | Knowledge Base (knowledge_patterns table)              ||
|  | BM25 keyword + frequency analysis + time correlation   ||
|  +--------------------------------------------------------+|
+----------------------------+------------------------------+
                             |
                             v
+-----------------------------------------------------------+
|              Root Cause Analyzer                           |
|  +------------------+  +-----------------------------+    |
|  | Pattern Match    |  | Bedrock LLM Analysis        |    |
|  | (known issues)   |  | (unknown issues)            |    |
|  +--------+---------+  +-------------+---------------+    |
+-----------|----------------------------|-----------------+
            |                            |
            v                            v
+-----------------------------------------------------------+
|              Decision Engine (3-Tier)                      |
|                                                           |
|  >= 0.7 + auto_healable  -->  AUTO-REMEDIATE              |
|  >= 0.7 + !auto_healable -->  SUGGEST (require approval)  |
|  0.3 - 0.7               -->  SUGGEST (require approval)  |
|  < 0.3                   -->  ESCALATE (full context)     |
|                                                           |
|  Rate Limiter: 5/hour, 10-min cooldown per target         |
+----------------------------+------------------------------+
                             |
                             v
+-----------------------------------------------------------+
|              Remediation Engine                            |
|  Actions: restart, scale, clear cache, rollback,          |
|           config fix, dependency update                   |
+----------------------------+------------------------------+
                             |
                             v
+-----------------------------------------------------------+
|              Feedback Loop                                |
|  Success: confidence += 0.05 (max 1.0), use_count++      |
|  Failure: confidence -= 0.1, log failure reason           |
|  Record in self_healing_events (append-only)              |
+-----------------------------------------------------------+
```

---

## 4. Requirements

### 4.1 Pattern Detection

#### REQ-08-001: Knowledge Base Pattern Matching
The system SHALL maintain a knowledge base of failure patterns in the `knowledge_patterns` table, each with a signature, root cause description, remediation steps, confidence score, and use count.

#### REQ-08-002: Statistical Matching
The system SHALL use statistical methods (BM25 keyword matching, frequency analysis, time correlation) for pattern matching against incoming error data. No GPU is required.

#### REQ-08-003: Bedrock LLM Root Cause Analysis
When no pattern match is found, the system SHALL invoke Bedrock LLM for root cause analysis (when available) and generate a candidate pattern for human review.

### 4.2 Decision Engine

#### REQ-08-004: Three-Tier Confidence Thresholds
The system SHALL apply the following decision logic:
- Confidence >= 0.7 AND auto_healable = true: auto-remediate immediately
- Confidence >= 0.7 AND auto_healable = false: suggest fix, require approval
- Confidence 0.3-0.7: suggest fix, require approval
- Confidence < 0.3: escalate with full diagnostic context

#### REQ-08-005: Rate Limiting
The system SHALL enforce a maximum of 5 self-heal actions per hour (configurable) and a 10-minute cooldown between identical actions on the same target.

#### REQ-08-006: Cascading Failure Prevention
The system SHALL limit remediation retries to a maximum of 3 per pattern per incident and implement circuit breaker protection (fail fast after 5 failures in 1 minute).

### 4.3 Remediation

#### REQ-08-007: Remediation Actions
The system SHALL support the following remediation actions: restart service, scale up replicas, clear cache, rollback deployment, apply configuration fix, and update dependency.

#### REQ-08-008: Approval Requirement for Infrastructure Actions
Deployment rollbacks, infrastructure scaling, and database failover actions SHALL always require explicit human approval regardless of confidence score.

### 4.4 Feedback Loop

#### REQ-08-009: Confidence Adjustment
The system SHALL adjust pattern confidence after each remediation attempt: +0.05 for success (maximum 1.0), -0.1 for failure.

#### REQ-08-010: Cross-Project Learning
Patterns learned in one project SHALL be available to all projects in the knowledge base, enabling cross-project remediation benefit.

### 4.5 Audit and Compliance

#### REQ-08-011: Append-Only Audit Trail
All self-healing events (auto-remediation, suggestions, escalations) SHALL be recorded in the append-only audit trail with pattern_id, confidence, action_taken, and result, satisfying NIST IR-4, IR-5, and SI-5.

#### REQ-08-012: Rate Limit State Tracking
The system SHALL track rate limit state in the `self_healing_events` table to enforce per-hour and per-target cooldown limits across process restarts.

---

## 5. Database Schema

### Tables

| Table | Purpose |
|-------|---------|
| `knowledge_patterns` | Failure pattern signatures with root cause, remediation, confidence, and use count |
| `self_healing_events` | Append-only record of all self-healing actions (auto, suggested, escalated) |
| `metric_snapshots` | Time-series metric data feeding anomaly detection |
| `alerts` | Correlated alerts with severity roll-up and root cause grouping |

---

## 6. Tools

| Tool | Purpose |
|------|---------|
| `tools/knowledge/pattern_detector.py` | Match detected anomalies against knowledge base using BM25 + statistical methods |
| `tools/knowledge/self_heal_analyzer.py` | Root cause analysis (pattern-based or Bedrock LLM), remediation execution |
| `tools/knowledge/recommendation_engine.py` | Generate recommendations for unmatched patterns |
| `tools/monitor/log_analyzer.py` | Parse logs for error patterns, anomalies, and security events |
| `tools/monitor/metric_collector.py` | Collect metrics from Prometheus/Grafana endpoints |
| `tools/monitor/alert_correlator.py` | Correlate related alerts into single root cause groups |
| `tools/audit/audit_logger.py` | Record self-healing events in append-only audit trail |

---

## 7. Architecture Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| D4 | Statistical methods for pattern detection; Bedrock LLM for root cause analysis | GPU not available in environment; statistical methods are deterministic and air-gap safe; LLM supplements for unknown patterns only |
| D146 | Application-level circuit breaker using ABC + in-memory state (stdlib only) | Prevents cascading remediation failures; 3-state machine (CLOSED, OPEN, HALF_OPEN) matches the self-healing retry pattern |
| D147 | Reusable retry utility with exponential backoff + full jitter | Self-healing actions need configurable retry with backoff; extracted from bedrock_client.py for reuse |

---

## 8. Security Gate

**Self-Healing Gate:**
- Maximum 5 auto-heal actions per hour enforced
- 10-minute cooldown between identical actions on same target
- Maximum 3 retries per pattern per incident
- Infrastructure actions (rollback, scale, failover) require explicit human approval regardless of confidence
- All events recorded in append-only audit trail (NIST IR-4, IR-5, SI-5)
- Unknown patterns (confidence < 0.3) always escalated, never auto-remediated

---

## 9. Commands

```bash
# Knowledge base pattern detection
python tools/knowledge/pattern_detector.py --log-data "/path/to/logs"

# Self-healing analysis and remediation
python tools/knowledge/self_heal_analyzer.py --failure-id "fail-123"

# Recommendation engine
python tools/knowledge/recommendation_engine.py --project-id "proj-123"

# Monitoring triggers for self-healing
python tools/monitor/log_analyzer.py --source elk --query "error"
python tools/monitor/health_checker.py --target "http://service:8080/health"

# Audit trail for self-healing events
python tools/audit/audit_logger.py --event-type "self_heal.auto" --actor "knowledge-agent" --action "Restarted service X" --project-id "proj-123"
```

**CUI // SP-CTI**
