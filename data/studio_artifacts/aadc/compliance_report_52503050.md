# AI Compliance Report — AADC
**Generated:** 2026-05-19 23:58 UTC  
**Project:** default  
**Compliance Gate:** FAIL

## NIST AI RMF (4 Functions)
| Function | Status |
|----------|--------|
| NIST AI RMF — GOVERN | FAIL |
| NIST AI RMF — MAP | PASS |
| NIST AI RMF — MEASURE | FAIL |
| NIST AI RMF — MANAGE | FAIL |

## DoD AI Ethics Principles (5)
| Principle | Status |
|-----------|--------|
| DoD AI Ethics — Responsible | PASS |
| DoD AI Ethics — Equitable | FAIL |
| DoD AI Ethics — Traceable | FAIL |
| DoD AI Ethics — Reliable | PASS |
| DoD AI Ethics — Governable | FAIL |

## Operational AI Controls
| Control | Status |
|---------|--------|
| Human-in-the-Loop (high-risk) | FAIL |
| Model Versioning | PASS |
| AI Decision Logging | FAIL |
| Input Sanitization | PASS |
| Output Filtering | FAIL |

## Failures (Blocking)
- FAIL [nist_rmf_govern]: NIST AI RMF — GOVERN: AI governance structures and policies in place — NOT IMPLEMENTED
- FAIL [human_in_loop_high_risk]: Human-in-the-Loop (high-risk): Human oversight for high-risk decisions — NOT IMPLEMENTED
- FAIL [ai_decision_logging]: AI Decision Logging: Audit trail for all AI decisions — NOT IMPLEMENTED

## Warnings
- WARN [nist_rmf_measure]: NIST AI RMF — MEASURE: AI risk metrics defined and tracked — NOT IMPLEMENTED
- WARN [nist_rmf_manage]: NIST AI RMF — MANAGE: AI risk response strategies implemented — NOT IMPLEMENTED
- WARN [dod_equitable]: DoD AI Ethics — Equitable: Bias testing and fairness measures — NOT IMPLEMENTED
- WARN [dod_traceable]: DoD AI Ethics — Traceable: Explainable and auditable AI decisions — NOT IMPLEMENTED
- WARN [dod_governable]: DoD AI Ethics — Governable: Human control and intervention capability — NOT IMPLEMENTED
- WARN [output_filtering]: Output Filtering: PII/harmful content filtered from outputs — NOT IMPLEMENTED
