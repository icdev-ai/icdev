# Chain of Thought Prompt Engineering

> Guidelines for writing reasoner-friendly prompts.

## Reasoner Role

The reasoner must:
1. Break the problem into explicit steps.
2. Show intermediate reasoning before the final answer.
3. Number each step clearly.
4. Not skip logic — every deduction must be visible.

## Critic Role

The critic must:
1. Review every step of the reasoning.
2. Identify logical errors, unstated assumptions, and gaps.
3. Suggest specific corrections, not vague complaints.
4. Be respectful but rigorous.

## Synthesizer Role

The synthesizer must:
1. Combine reasoning and critique into a single polished answer.
2. Incorporate all valid corrections from the critique.
3. Discard invalid or irrelevant critique points.
4. Be direct — no meta-commentary about what it changed.

## Example: Before / After

### Before (single-call prompt)

```
Write a Python function to merge two sorted lists.
```

### After (CoT-structured prompt)

```
[SYSTEM]
You are a careful reasoning assistant. Think step by step. Show your full
reasoning before giving the final answer. Number each step clearly.

[TASK]
Write a Python function to merge two sorted lists.

[INSTRUCTION]
Break this down step by step. Show your reasoning, then provide the final
answer clearly labeled as [FINAL ANSWER].
```

## Template Variables

| Variable | Source |
|----------|--------|
| `{{user_prompt}}` | Original user request |
| `{{system_prompt}}` | Canvas/system context |
| `{{output_schema}}` | `request.output_schema` (if set) |
| `{{tools}}` | `request.tools` (if set) |

All templates include CUI // SP-CTI classification banner automatically.

## When to Use CoT

Use CoT when:
- The task requires multi-step reasoning.
- Errors in intermediate steps would compound.
- Transparency is required (compliance, audit, safety).
- The function has high output variance in quality scores.

Do not use CoT when:
- The task is a simple lookup or classification.
- Latency is critical and correctness is easily verified.
- The function is in the `excluded_functions` list.
