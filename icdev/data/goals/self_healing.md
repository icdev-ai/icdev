# Goal: Self-Healing System

## Purpose
Detect production issues automatically, match against known patterns, and remediate with appropriate confidence thresholds. Implements a feedback loop where successful fixes increase pattern confidence over time, creating an ever-improving knowledge base.

## Trigger
- Monitoring detects anomaly or error pattern
- `/icdev-monitor --self-heal` skill invoked
- CI/CD pipeline failure
- Alert threshold exceeded

## Inputs
- Error logs, metrics, or alert data
- Knowledge base patterns (`data/icdev.db` → `knowledge_patterns` table)
- Self-healing configuration (`args/monitoring_config.yaml`)
- Rate limit state (`self_healing_events` table)

## Process

### Step 1: Detect Issue
**Tools:** `tools/monitor/log_analyzer.py`, `tools/monitor/metric_collector.py`, `tools/monitor/alert_correlator.py`
- Parse logs for error patterns
- Check metrics against thresholds
- Correlate related alerts into single root cause

### Step 2: Match Against Knowledge Base
**Tool:** `tools/knowledge/pattern_detector.py`
- Search knowledge_patterns table for matching patterns
- Use statistical matching: BM25 keyword + frequency analysis + time correlation
- Return matched patterns with confidence scores

### Step 3: Analyze Root Cause
**Tool:** `tools/knowledge/self_heal_analyzer.py`
- If pattern match found: use pattern's known root cause
- If no match: use Bedrock LLM for root cause analysis (when available)
- Determine severity and impact scope

### Step 4: Decision Engine
Apply confidence thresholds:

| Confidence | Auto-Healable | Action |
|------------|---------------|--------|
| >= 0.7 | Yes | Auto-remediate immediately |
| >= 0.7 | No | Suggest fix, require approval |
| 0.3 - 0.7 | Any | Suggest fix, require approval |
| < 0.3 | Any | Escalate with full context |

### Step 5: Rate Limiting
Before executing any self-heal action:
- Check `self_healing_events` table for recent actions
- **Max 5 self-heal actions per hour** (configurable)
- **10-minute cooldown** between identical actions on same target
- If rate limited: queue action for later execution

### Step 6: Execute Remediation (if approved)
**Tool:** `tools/knowledge/self_heal_analyzer.py` → `trigger_self_heal()`
- Apply the pattern's documented solution
- Common actions:
  - Restart service
  - Scale up replicas
  - Clear cache
  - Rollback deployment
  - Apply configuration fix
  - Update dependency

### Step 7: Verify Fix
- Re-run health checks after remediation
- Confirm error pattern is no longer active
- Measure resolution time

### Step 8: Feedback Loop
- **Success:** Increment pattern `use_count`, increase `confidence` by 0.05 (max 1.0)
- **Failure:** Decrease `confidence` by 0.1, log failure reason
- Record event in `self_healing_events` with status

### Step 9: Audit Trail
**Tool:** `tools/audit/audit_logger.py`
- Record: event_type=self_heal.{auto|suggested|escalated}
- Include: pattern_id, confidence, action_taken, result
- **NIST Controls:** IR-4 (Incident Handling), IR-5 (Incident Monitoring), SI-5 (Security Alerts)

## Outputs
- Detection report (what was found)
- Pattern match results (known/unknown)
- Action taken or suggested
- Verification results
- Audit trail entry

## Configuration (monitoring_config.yaml)
```yaml
self_healing:
  enabled: true
  auto_heal_confidence_threshold: 0.7
  suggest_fix_threshold: 0.3
  max_actions_per_hour: 5
  cooldown_minutes: 10
  require_approval_for:
    - deployment.rollback
    - infrastructure.scale
    - database.failover
```

## Pattern Learning
The knowledge base grows through:
1. **Manual addition:** Developer adds pattern via `/icdev-knowledge add`
2. **Failure analysis:** When failures are analyzed, new patterns are suggested
3. **Successful fixes:** Confirmed fixes become high-confidence patterns
4. **Cross-project learning:** Patterns from one project benefit all projects

## Edge Cases
- Cascading failures: detect and prevent remediation loops (max 3 retries per pattern per incident)
- Multiple simultaneous issues: prioritize by severity, handle sequentially
- Unknown patterns: always escalate, never auto-fix
- Infrastructure-level issues: require explicit approval regardless of confidence
- Rate limit exceeded: queue with priority, notify operations team

## Related Goals
- `monitoring.md` — Log analysis and metric collection
- `deploy_workflow.md` — Deployment and rollback
- `security_scan.md` — Security pattern detection
