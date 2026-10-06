---
ontology_id: icdev:mission:m04-first-agent:step:2
step_class: icdev:Lesson
---

# Tool Selection

*Current as of October 2026.*

In step 1, `decide_tool()` was a set of `if` statements. In a real agent that
function is the LLM, and the only thing it knows about your tools is what you tell
it. Tool selection is the step where most agent failures begin.

## What the model actually sees

The model never sees your Python. For each tool it sees a **name**, a
**description**, and a **parameter schema** (names, types, which are required). That
is the entire basis for its choice. A tool named `run` described as "runs things"
will be called for everything, or for nothing.

Write descriptions for the model, not for a colleague:

- Say **when to use it** and, just as important, **when not to**.
- State what it returns, so the model can tell whether the result answers the task.
- Keep parameter names unambiguous: `control_id` beats `id`.

## Fewer, more distinct tools

Every tool added is another option to confuse. Two tools whose descriptions overlap
("look up a control" and "search controls") force the model to guess, and it will
guess differently on different runs. Prefer a small set of tools with clearly
separated purposes; merge or remove overlapping ones.

## Never trust the selection blindly

The model's tool call is untrusted output. Before executing it:

1. **Check the name exists.** In step 1, `TOOLS[name]` raises `KeyError` on an
   invented tool name. Return an error message to the model as the observation
   instead, so it can correct itself on the next turn.
2. **Validate the arguments** against the schema. A missing or mistyped argument
   should become an observation the model can fix, not a crash.
3. **Apply least privilege.** Give the agent only the tools the task needs. A
   read-only question should not have a delete tool in reach — the safest call is
   the one the agent cannot make.

## Errors are observations

When a tool fails, the failure goes back into the conversation as the tool result.
That is what lets a loop beat a pipeline: the model reads "Control V-999 not found"
and tries a different lookup. Swallowing the error, or returning an empty string,
takes that ability away and leaves the model reasoning from nothing.

In ICDEV, `icdev/tools/llm/agent_loop.py` runs this loop, and a tool can be declared
`is_read_only` so that calls which only read are distinguishable from calls that
change state.
