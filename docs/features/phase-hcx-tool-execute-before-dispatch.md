# CUI // SP-CTI

# TOOL_EXECUTE_BEFORE — the gating extension point, now dispatched (hcx-live-01)

## The defect

`tools/extensions/extension_manager.py` declares ten `ExtensionPoint` members.
Before this task exactly **one** production site in `tools/` called `dispatch()`
— `tools/dashboard/chat_manager.py:79`, for the chat points — plus
`.claude/hooks/post_tool_use.py` for `TOOL_EXECUTE_AFTER`.

`TOOL_EXECUTE_BEFORE` — the **gating** point, and the whole reason the
behavioral tier (`allow_modification=True`) exists — had no production
dispatcher at all. Its only references were in tests. Measured on the checkout
that opened this task:

```
extension_manager.handler_count(ExtensionPoint.TOOL_EXECUTE_BEFORE)  ->  0
extension_manager.handler_count()                                    ->  9
```

This is ICDEV's signature bug: a descriptive registry beside an imperative
hardcoded list, where the descriptive one silently does nothing. An operator
could write a perfectly correct extension file, see it register, and watch it
never fire.

## Where it is dispatched

`tools/agent_runtime/dispatch.py` — `make_handler(...)._run`, where every SAG
tool call already passes through the safety gate. Every built-in, decorated,
MCP-registry and external tool the agent runtime can call goes through this one
function, so there is exactly one place to wire and exactly one place to audit.

## The composition rule (this is the security half)

```
extension  ->  (refusal? block)  ->  safety gate  ->  (block? block)  ->  execute
```

An extension **may deny**, and **may never permit** what the safety gate
refused. Two properties fall out of that order, and both are load-bearing:

- **The gate judges exactly the input the tool receives.** A behavioral
  extension may rewrite `tool_input`, and the gate is evaluated *after* the
  rewrite. Dispatching after the gate instead would let a drop-in extension file
  swap in a payload the gate never saw — a one-line permission bypass wearing
  the clothes of the behavioral tier.
- **Nothing runs between the gate's verdict and execution**, so no extension can
  un-block what the gate blocked. An extension refusal short-circuits *ahead* of
  the gate: strictly more blocking, and it spares a human approver being prompted
  to authorise a call that has already been refused.

This mirrors `_compose_pre_tool_hooks` in `icdev/tools/llm/agent_loop.py`, where
the approval gate is chained ahead of the caller's hook and the first non-empty
block message wins.

## The contract for an extension author

Register at `ExtensionPoint.TOOL_EXECUTE_BEFORE` with
`allow_modification=True`. The context passed in:

| Key | Meaning |
|-----|---------|
| `tool_name` | The tool about to run (`ToolSpec.name`) |
| `tool_input` | The arguments the model supplied |
| `read_only` | Whether the tool is declared non-mutating |
| `source` | `mcp` / `builtin` / `decorated` / `external` |
| `task_id` | The run's task or session id, or `""` |

Return the context to allow, optionally with a rewritten `tool_input`. To
refuse, set `deny` truthy and `deny_reason` to the message the model should
see:

```python
def handle(context: dict) -> dict:
    if context["tool_name"] == "run_command" and "curl" in str(context["tool_input"]):
        return {**context, "deny": True, "deny_reason": "network egress is not permitted"}
    return context


EXTENSION_HOOKS = {
    "tool_execute_before": {
        "handler": handle,
        "name": "no_egress",
        "priority": 10,
        "allow_modification": True,
    },
}
```

Three things the dispatcher deliberately does **not** do:

- **It reads only `tool_input`, `deny` and `deny_reason` back out.**
  `tool_name` and `read_only` are taken from the `ToolSpec`, never re-read from
  the returned context. The default gate waves every read-only tool through, so
  a context key an extension controls deciding that flag would skip the mutation
  gate outright.
- **Only the behavioral tier may refuse.** `ExtensionManager.dispatch` ignores an
  observational handler's return value, so it ignores its `deny` too. This is
  the tier distinction doing its job, not an oversight.
- **It does not fail closed when the extension layer is missing.** With no
  extension manager there are no extensions, and an extension can only ever
  *add* a refusal — the safety gate is unaffected either way. A handler that
  raises is caught by `ExtensionManager.dispatch` and the call proceeds to the
  gate.

## Which singleton

`tools/extensions/extension_manager.py` and
`icdev/tools/extensions/extension_manager.py` are physically distinct copies
holding **distinct singletons** — verified, not assumed:

```
a.extension_manager is b.extension_manager  ->  False
```

`dispatch.py` resolves `tools.extensions.extension_manager`, the same import
`chat_manager`, `awareness.hooks` and `.claude/hooks/post_tool_use.py` use.
**Both** copies of `dispatch.py` name `tools.` for that reason, so the two
mirrored modules talk to one registry rather than two. Registering against the
`icdev.` copy would put a handler somewhere the runtime never looks.

`ExtensionPoint` is a `str` Enum, so a member from either copy hashes and
compares equal and a cross-copy dict lookup still resolves — the *enum* is
interchangeable, the *singleton* is not.

## Verification

`tests/agent_runtime/test_dispatch_extension_point.py` — 13 tests, gate and
tool injected, no DB and no network. Gated in `args/ci_test_files/core.txt` in
the same PR that adds it.

Seven of them assert the ordering, and they are the threat model rather than
coverage: an extension cannot allow what the gate refused, cannot relabel a
mutating tool read-only, cannot rename the tool the gate judges, and cannot
hand the tool an input the gate did not see. RED was recorded before the
implementation — 7 of the 13 failed against the merge-base tree. The other 6
are the bypass guards, which pass trivially there only because nothing
dispatched at all.

## Related

- `hcx-live-02` — count extension dispatches and stop swallowing handler
  failures silently.
- `hcx-live-03` — wire `AGENT_START`/`AGENT_END` or delete the four remaining
  dead extension points.
