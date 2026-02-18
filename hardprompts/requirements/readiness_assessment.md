# Readiness Assessment Prompt

> CUI // SP-CTI

Assess the readiness of the current requirements set for proceeding to the next phase.

## Input
- Session: {{session_summary}}
- Requirements: {{requirements_json}}
- Gap analysis: {{gap_results}}
- Ambiguity analysis: {{ambiguity_results}}
- Impact level: {{impact_level}}
- Current readiness score: {{current_score}}

## Assessment Dimensions

Score each dimension 0.0-1.0 with evidence:

1. **Completeness** (25%): All requirement categories covered?
2. **Clarity** (25%): Ambiguity ratio below 10%?
3. **Feasibility** (20%): No infeasible requirements? Constraints aligned?
4. **Compliance** (15%): NIST control families addressed for impact level?
5. **Testability** (15%): 80%+ have BDD acceptance criteria?

## Output Format
```json
{
  "overall_score": 0.0,
  "dimensions": {
    "completeness": {"score": 0.0, "evidence": "...", "gaps": [...]},
    "clarity": {"score": 0.0, "evidence": "...", "ambiguities": [...]},
    "feasibility": {"score": 0.0, "evidence": "...", "concerns": [...]},
    "compliance": {"score": 0.0, "evidence": "...", "missing_families": [...]},
    "testability": {"score": 0.0, "evidence": "...", "untestable_count": 0}
  },
  "recommendation": "proceed|gather_more|critical_gaps",
  "next_questions": ["Top 3 questions to improve readiness"]
}
```
