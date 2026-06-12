# Chain of Debate Prompt Engineering

> Guidelines for writing debater-friendly prompts.

## Debater Role

Each debater must:
1. Argue their assigned position strongly but honestly.
2. Address prior arguments from other debaters directly.
3. Cite evidence and reasoning, not rhetoric.
4. Be respectful but firm — no ad hominem attacks.

## Judge Role

The judge must:
1. Evaluate all positions neutrally.
2. State which argument is strongest and why.
3. Provide a confidence score (0.0–1.0).
4. Not simply average — choose the best reasoning.

## Template Variables

| Variable | Source |
|----------|--------|
| `{{user_prompt}}` | Original user request |
| `{{position}}` | Assigned position for this debater |
| `{{prior_arguments}}` | Arguments from previous debate rounds |
| `{{system_prompt}}` | Canvas/system context |
| `{{output_schema}}` | `request.output_schema` (if set) |

All templates include CUI // SP-CTI classification banner automatically.

## Example: Architecture Review Debate

### Prompt

```
[TASK]
Debate: Should we adopt a microservices architecture for this system?

[YOUR POSITION]
Strongly in favor of microservices.

[PRIOR ARGUMENT 1]
Opponent argued that operational complexity outweighs benefits.

[INSTRUCTION]
Argue your position. Address the prior argument. Explain why your position
is strongest. End with [ARGUMENT].
```

## Example: Risk Assessment Debate

### Prompt

```
[TASK]
Debate: Is the proposed third-party API integration an acceptable risk?

[YOUR POSITION]
Moderately against due to insufficient vendor security documentation.

[INSTRUCTION]
Argue your position. Provide reasoning and evidence. End with [ARGUMENT].
```

## When to Use CoD

Use CoD when:
- The question is subjective or evaluative (reviews, assessments, strategy).
- Multiple valid perspectives exist.
- Groupthink is a risk — you need forced disagreement.
- The stakes are high and a single point of failure is unacceptable.

Do not use CoD when:
- There is an objectively correct answer (use CoT instead).
- The task requires creativity, not evaluation.
- The function is in the `excluded_functions` list.
