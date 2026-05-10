<!-- CUI // SP-CTI -->

# Incident Retrospective

The incident is resolved. Monitoring is stable. Now comes the most important — and most skipped — step: the retrospective. Without structured retrospectives, AI incidents repeat. With them, each incident strengthens your monitoring stack permanently.

## The 5 Required Postmortem Fields

Every AI incident postmortem must capture these five fields. Incomplete postmortems are not closed — they are assigned back to the on-call engineer.

### Field 1: Timeline

Document with minute-level granularity:

| Timestamp | Event |
|---|---|
| 2026-05-09 T14:32 | `detect_drift()` fires `critical` quality_degradation on `qwen3-local/summarize` |
| T14:33 | `auto_resolver.py` normalizes alert, selects `model_rollback` (confidence 0.82) |
| T14:35 | Model swapped to `qwen3-local-v1.2`. Quality monitoring resumed. |
| T16:10 | Quality scores stabilizing. No new drift events. |
| T16:10 | Root cause identified: Ollama updated `qwen3-local` to a new quantization level automatically |

### Field 2: Affected Model and Version

Always record the exact model ID and version, not just the model family name. "qwen3-local" is ambiguous. "qwen3-local-v1.3-q4_K_M (Ollama auto-updated 2026-05-09 T11:00)" is actionable.

### Field 3: User Impact

Quantify:
- N requests processed during the incident window with degraded quality
- User-visible error rate (if any)
- Estimated quality score of affected outputs (use `get_drift_history()` to reconstruct)
- SLA breach? (If quality SLA requires ≥0.75 and the incident lasted 2h at 0.61, that is a breach)

### Field 4: Root Cause

State the drift type, attack vector, or configuration change that caused it. Map to one of the ICDEV drift types (`quality_degradation`, `latency_increase`, `token_inflation`, `availability_drop`). Include the proximate cause (what triggered it) and the root cause (what allowed it to happen).

**Example:** Proximate — Ollama auto-updated model weights. Root — no version pin on `qwen3-local` in `llm_config.yaml`, and no automated smoke test fires on Ollama model updates.

### Field 5: Prevention

State what monitoring, guardrail, or configuration change would have caught this sooner or prevented it entirely. This field drives action items.

**Example:** (1) Pin Ollama model versions in `llm_config.yaml`. (2) Add a startup health check that compares current model checksum against pinned baseline. (3) Add a Kanban task to the `icdev-maintain` flow to verify Ollama model versions weekly.

## Lessons from Real AI Incidents

**Monitoring gaps are the #1 cause of delayed detection.** In a survey of 47 production AI incidents, the average time-to-detection was 3.4 days — not because incidents were rare, but because teams had no quality-score monitoring. Infrastructure monitoring (uptime, latency, error rate) is insufficient for AI systems.

**Gradual drift is invisible without baselines.** Teams that hadn't established baselines had no reference point. A model scoring 0.64 looks fine if you don't know it used to score 0.86.

## NIST IR-8: AI Incident Response Requirements

NIST IR-8 (Incident Response Planning) requires that the incident response plan explicitly address AI system failures. Your runbook must include:

- Procedures for AI-specific incident types (not just generic IT incidents)
- Contact information for model vendor support (for cloud models)
- Rollback procedures for each model in production
- Data breach notification procedures if CUI was exposed via prompt injection

## Post-Incident: Strengthen the Academy

After closing an AI incident, add a new `BUILTIN_STEPS` test case to the FORGE Academy mission that encodes the failure pattern you discovered. This turns production failures into training material for the next engineer.

```python
# Example: Add to apps/forge_academy/engine/builtin_steps.py
BUILTIN_STEPS["m-sre-ai-04-incident-response"]["ollama_auto_update_drift"] = {
    "description": "Ollama auto-updated model; quality dropped 26% before detection.",
    "lesson": "Pin model versions. Add startup checksum validation.",
    "prevention": "version_pin + startup_smoke_test",
}
```

The Academy builds institutional memory from incidents. Every failure becomes a question in the next SRE's training path.

**Your task:** Answer the reflection questions.
