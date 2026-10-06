---
ontology_id: icdev:mission:m-sre-ai-04-incident-response:step:3
step_class: icdev:Assessment
---

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
| T14:33 | On-call confirms the drift and logs an AI incident (`log_incident`, type `model_drift`) |
| T14:35 | `summarize` routed back to its previous model. Quality monitoring resumed. |
| T16:10 | Quality scores stabilizing. No new drift events. |
| T16:10 | Root cause identified: Ollama updated `qwen3-local` to a new quantization level automatically |

### Field 2: Affected Model and Version

Always record the exact model ID and version, not just the model family name. "qwen3-local" is ambiguous. "qwen3-local-v1.3-q4_K_M (Ollama auto-updated 2026-05-09 T11:00)" is actionable.

### Field 3: User Impact

Quantify:
- N requests processed during the incident window with degraded quality
- User-visible error rate (if any)
- Estimated quality score of affected outputs (`get_drift_history()` gives the drift events; the per-call scores are in `model_quality_scores`)
- SLA breach? (If quality SLA requires ≥0.75 and the incident lasted 2h at 0.61, that is a breach)

### Field 4: Root Cause

State the drift type, attack vector, or configuration change that caused it. Map it to one of the ICDEV drift types (`quality_degradation`, `latency_increase`, `token_inflation`, `availability_drop`) or AI incident types (`confabulation`, `model_drift`, `data_breach`, ...). Include the proximate cause (what triggered it) and the root cause (what allowed it to happen).

**Example:** Proximate — Ollama auto-updated model weights. Root — no version pin on `qwen3-local` in `llm_config.yaml`, and no automated smoke test fires on Ollama model updates.

### Field 5: Prevention

State what monitoring, guardrail, or configuration change would have caught this sooner or prevented it entirely. This field drives action items.

**Example:** (1) Pin Ollama model versions in `llm_config.yaml`. (2) Add a startup health check that compares current model checksum against pinned baseline. (3) Add a Kanban task to the `icdev-maintain` flow to verify Ollama model versions weekly.

## Lessons from Real AI Incidents

**Monitoring gaps are the most common cause of delayed detection.** Incidents are often found days late. They are not rare; the team simply had no quality-score monitoring. Infrastructure monitoring (uptime, latency, error rate) is not enough for AI systems.

**Gradual drift is invisible without baselines.** Teams that hadn't established baselines had no reference point. A model scoring 0.64 looks fine if you don't know it used to score 0.86.

## NIST IR-8: AI Incident Response Requirements

NIST IR-8 (Incident Response Planning) requires that the incident response plan explicitly address AI system failures. Your runbook must include:

- Procedures for AI-specific incident types (not just generic IT incidents)
- Contact information for model vendor support (for cloud models)
- Rollback procedures for each model in production
- Data breach notification procedures if CUI was exposed via prompt injection

## Post-Incident: Strengthen the Academy and the Knowledge Base

Close the loop in two places:

1. **Knowledge base.** Record the failure pattern so `auto_resolver.py` can match it next time, at `suggest` level unless the fix is provably safe to automate.
2. **Training.** Turn the lesson into an assessment question. FORGE Academy item banks live in `apps/forge_academy/content/item_banks/<mission-slug>.yaml`, so a question about this incident type reaches the next SRE's training path.

The Academy builds institutional memory from incidents. Every failure becomes a question in the next SRE's training path.

**Your task:** Answer the reflection questions.
