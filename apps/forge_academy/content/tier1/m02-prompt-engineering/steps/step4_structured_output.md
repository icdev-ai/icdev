---
ontology_id: icdev:mission:m02-prompt-engineering:step:4
step_class: icdev:Lab
---

# Structured Output Enforcement

A model that answers in prose is fine for a chat window. A model whose answer feeds **another program** — a database insert, a compliance matrix, a kanban card — must answer in a shape that program can parse, every time. One stray sentence of preamble ("Sure! Here is the JSON:") and `json.loads` raises.

## Three layers of enforcement

Reliable structured output is built in layers. Use as many as your provider and deployment allow.

### 1. Ask for it precisely in the prompt

Name the format, give the schema, and show an example:

```
Extract the contract award as JSON. Respond with ONLY a JSON object, no prose.

Schema:
{"name": string, "naics": string, "value_usd": number,
 "status": "award" | "solicitation" | "modification"}

Example:
{"name": "Example Systems LLC", "naics": "541512", "value_usd": 750000, "status": "award"}
```

Each part does a job:

- **"JSON" + "ONLY"** — tells the model the output is for a machine.
- **The schema** — names every field, its type, and the allowed values for enums. A field you do not name is a field the model is free to omit.
- **The example** — the few-shot technique from step 2, applied to format. Examples are what turn "usually includes `naics`" into "always includes `naics`".

### 2. Use the provider's structured-output mode

As of October 2026, the major hosted APIs (Anthropic, OpenAI, Google) and local runtimes such as Ollama offer a mode that takes a **JSON Schema** and constrains generation to it (sometimes called constrained decoding or "strict" mode). Tool/function calling, covered in step 5, is the same idea: the tool's input schema shapes the model's arguments.

Where it is available, use it — it removes whole classes of formatting failure. Check the current documentation for your provider and model, because support differs by model and changes between releases.

### 3. Validate in code, always

Even with layers 1 and 2, **parse and validate** before you trust the result:

```python
data = parse_llm_output(raw)          # tolerate a prose wrapper
if data is None:
    ...                                # retry, or route to a human
missing = REQUIRED_FIELDS - data.keys()
if missing or data.get("naics") is None:
    ...                                # reject; do not insert a half-record
```

Validation catches what prompts and schemas cannot: a field that is present but `null`, a value that is well-typed but wrong, a model or provider that silently fell back to a mode without constraints. A failed validation is a retry or a human review — never a silent default.

## The lab

The starter gives you two helpers you should **not** change:

- `simulate_llm_extract(prompt, raw_text)` — an offline mock LLM. Like a real model, the quality of its output depends on your prompt. It checks whether your prompt **asks for JSON**, **shows a schema** (contains `{` and `}`), and **gives an example**. Weak prompts get prose, or JSON with missing or `null` fields; a prompt with all three gets a complete record.
- `parse_llm_output(raw_output)` — parses JSON directly, or extracts a JSON object from a prose wrapper. It returns `None` when there is nothing parseable.

## Your task

Complete `extract_contract_data(raw_sam_text)`:

1. Build a prompt that asks for JSON, includes the schema with all four fields (`name`, `naics`, `value_usd`, `status`), and includes an example object. Embed `raw_sam_text` inside delimiters, as you learned in step 3.
2. Call `simulate_llm_extract(prompt, raw_sam_text)` and print the raw output.
3. Parse it with `parse_llm_output()`, print the parsed result, and return the dict.

The grader checks that all four fields are present, that `naics` is **not** `null`, and that the company name was extracted.
