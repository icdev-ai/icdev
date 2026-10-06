---
ontology_id: icdev:mission:m02-prompt-engineering:step:5
step_class: icdev:Lesson
---

# Reasoning, Tool Prompts, and Prompt Versioning

The last step of this mission covers three things that separate a prompt that works in a demo from one you can operate: getting the model to reason when it should (and not when it should not), prompting it to use tools, and treating prompts as versioned artifacts.

## Chain-of-thought

**Chain-of-thought (CoT)** prompting asks the model to work through intermediate steps before giving its answer — "think step by step", or a few-shot example that shows its working. For multi-step problems (arithmetic, multi-hop questions, applying several rules in order) it tends to improve accuracy, because each generated step becomes context for the next one.

```
Determine whether this finding is CAT1, CAT2 or CAT3.
First list which STIG rule applies and whether the system is internet-facing.
Then give the severity on its own line as: SEVERITY: <CAT1|CAT2|CAT3>
```

Note the last line: ask for the reasoning *and* a clearly separated final answer, so your code can parse the answer without parsing the reasoning.

## Extended thinking

As of October 2026, several providers offer models with a built-in reasoning phase — Anthropic calls it **extended thinking**; others describe reasoning models with a reasoning-effort setting. Instead of you prompting for step-by-step text, the model produces a separate reasoning block (or hidden reasoning tokens) before its final answer, and you control it with an API parameter such as a thinking token budget or an effort level.

Practical differences from prompted CoT:

- The reasoning is kept apart from the answer, so the answer stays clean for parsing.
- You pay for the reasoning tokens. A bigger thinking budget means more output tokens and more latency.
- Provider guidance generally favours stating the goal clearly over scripting each reasoning step.

Parameter names and limits differ by provider and model, and change between releases — read the current documentation for the model you deploy.

## When NOT to use reasoning

Reasoning is not free, and it is not always better:

- **Simple lookups, classification against clear labels, reformatting, extraction** — the answer does not need intermediate steps; reasoning adds cost and latency for no accuracy gain.
- **Latency-critical paths** — an autocomplete, or a chat reply that must start streaming immediately.
- **High-volume batch jobs** — reasoning tokens multiply cost across every call.
- **Reasoning is not evidence.** A fluent chain of reasoning can still reach a wrong answer, and the stated steps may not be the process that actually produced it. Verify the answer (step 4's validation, a test, a citation), not the prose.

Rule of thumb: start without reasoning; turn it on for the task types where you **measure** an accuracy gain worth the cost.

## Prompting for tool (function) calling

With tool calling, you describe functions to the model and it replies with a structured request to call one, which **your code** executes. The tool's description and input schema *are* a prompt — the model reads them to decide when and how to call.

```json
{
  "name": "lookup_naics",
  "description": "Look up the title of a NAICS code. Use when the user gives a 6-digit NAICS code and needs its industry name. Do not use for SIC codes.",
  "input_schema": {
    "type": "object",
    "properties": {"code": {"type": "string", "description": "6-digit NAICS code, e.g. 541519"}},
    "required": ["code"]
  }
}
```

Good tool prompts:

- **Say when to use it, and when not to.** The description is how the model chooses between tools.
- **Describe every parameter** with a type, a format and an example value.
- **Keep the tool set small and distinct.** Overlapping tools ("search_docs" vs "find_documents") produce wrong choices.
- **Return clear errors** from the tool so the model can correct itself.
- **Treat tool results as untrusted data** (step 3) — a web page returned by a search tool can carry an indirect prompt injection.

## Prompts are code: version them

A prompt edit changes production behaviour exactly as a code edit does, so it needs the same controls: a version history, an audit of who changed what, the ability to compare versions, and a fast rollback.

ICDEV keeps that in its prompt registry, `tools/llm/prompt_registry.py`:

| Function | What it does |
|----------|--------------|
| `register_prompt(name, template_text, function_name)` | Stores a new version; identical text is deduplicated by hash |
| `activate_prompt(name, version)` | Makes a version live and deprecates the previously active one |
| `render_prompt(name, default_template, **vars)` | Call-site read path: renders the active version, or falls back to the code's own template when nothing is registered |
| `diff_versions(name, v1, v2)` | Shows what changed between two versions |
| `rollback_prompt(name, to_version)` | Reactivates an earlier version |
| `start_ab_test(...)` / `record_ab_result(...)` | Compares two versions on measured quality |

The `render_prompt` fallback is the important design choice: a call site keeps working with its in-code template on a fresh install, and becomes versionable the moment a template is registered — no code change is needed to roll a prompt forward or back.

## Key takeaways

- **CoT** helps multi-step problems; ask for a separately parseable final answer.
- **Extended thinking** moves reasoning into a separate, budgeted phase controlled by an API parameter — and you pay for it.
- Skip reasoning for simple, latency-critical or high-volume tasks, and never treat reasoning text as proof.
- A tool's description and schema are a prompt: say when to use it, describe every parameter, keep tools distinct.
- Version prompts like code — register, activate, diff, roll back — with `tools/llm/prompt_registry.py`.

Answer the questions below to complete this step.
