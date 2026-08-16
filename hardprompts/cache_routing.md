# Cache-Friendly Prompt Routing

## Guidelines for Maximizing Cache Hit Rate

### 1. Use Deterministic Language

Avoid time-dependent references in prompts that are meant to be cached:

- **Bad**: "Generate code as of today, May 14, 2026"
- **Good**: "Generate code using the current stable API version"

- **Bad**: "Review the latest PR #12345"
- **Good**: "Review PR #12345 (sha: abcdef)"

### 2. Stable Message Ordering

The cache key canonicalizes messages by sorting them. However, for optimal cache
hits, keep message order stable in your calling code:

```python
# Bad: messages appended in variable order
messages = []
if context:
    messages.append({"role": "system", "content": context})
if history:
    messages.extend(history)
messages.append({"role": "user", "content": query})

# Good: fixed order
messages = [
    {"role": "system", "content": context},
    {"role": "user", "content": query},
]
```

### 3. Separate Time-Varying Context

Move time-varying data out of the cache key:

```python
# Bad: timestamp in system_prompt
system_prompt = f"You are a helpful assistant. Today is {datetime.now()}."

# Good: static system_prompt + time in first user message
system_prompt = "You are a helpful assistant."
messages = [
    {"role": "user", "content": f"Today is {datetime.now()}. Please generate a report."}
]
```

The system prompt is cached with `context_cache: true`. The user message with
the timestamp is not cached, but the expensive system prompt KV state is reused.

### 4. System Prompt vs User Message

For context caching, the system prompt is the best place to put large, stable
context blocks. Say so with the provider-neutral `cache_prefix` flag — the
provider translates it (cch-cap-01); never set `cache_control` from a caller,
that is Anthropic's wire vocabulary:

```python
# Best for caching: large context in system_prompt
request = LLMRequest(
    system_prompt="""
    You are ICDEV™, an AI assistant for certified software development.
    
    [5000 lines of guidelines, examples, and reference material]
    """,
    messages=[{"role": "user", "content": "Generate a compliance report."}],
    cache_prefix=True,
)
```

What that buys depends on the provider's declared support: `explicit`
(Anthropic, Bedrock) marks breakpoints on the wire, `automatic` (OpenAI, Azure)
caches the prefix without being asked, `local` (Ollama, vLLM) reuses the KV
state for a latency win with nothing to bill, and `none` is a first-class
answer. Subsequent calls with different user messages but the same system
prompt reuse the cached prefix wherever the provider supports it.

### 5. Per-Canvas Best Practices

| Canvas | Context Cache Strategy |
|--------|----------------------|
| NDC | Cache network topology guidelines + device catalog |
| SDC | Cache STIG controls + attack patterns |
| MDC | Cache migration playbook + vendor mappings |
| DDC | Cache schema definitions + lineage rules |
| AADC | Cache agent behavior guidelines + tool schemas |

### 6. Temperature and max_tokens

- Temperature != 1.0 is included in the cache key. Keep it stable for cacheable calls.
- max_tokens != 4096 is included in the cache key. Use consistent values.

### 7. Tools and Output Schema

Tool schemas and output schemas are canonicalized in the key. Keep them stable:

```python
# Bad: dynamically generated schema
tools = [{"name": f"fn_{uuid4()}", ...}]

# Good: stable schema
tools = [{"name": "generate_report", ...}]
```

### 8. Before/After Example

**Before** (cache hit rate ~5%):
```python
for issue in issues:
    prompt = f"Review issue #{issue.id} created at {issue.created_at}"
    router.invoke("code_review", LLMRequest(messages=[{"role": "user", "content": prompt}]))
```

**After** (cache hit rate ~40%):
```python
system = "You are a code reviewer. Review the provided issue and suggest fixes."
for issue in issues:
    prompt = f"Issue ID: {issue.id}\nCreated: {issue.created_at}\nDescription: {issue.description}"
    router.invoke(
        "code_review",
        LLMRequest(
            system_prompt=system,
            messages=[{"role": "user", "content": prompt}],
            cache_prefix=True,
        )
    )
```

The system prompt is cached via context caching. The response cache hits when
the same issue is reviewed again (e.g., by a different canvas or after a retry).

## Classification
CUI // SP-CTI
