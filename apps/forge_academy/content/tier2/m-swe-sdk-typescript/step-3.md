---
ontology_id: icdev:mission:m-swe-sdk-typescript:step:3
step_class: icdev:Assessment
---

<!-- CUI // SP-CTI -->

# TypeScript AI Feature Review

With a working streaming integration, this step covers the engineering decisions that determine long-term maintainability: state management, Edge Runtime constraints, performance optimisation, and a solid testing strategy.

## State management options

Three patterns dominate React AI applications:

**React Context** — suitable for simple single-session chat state. Cheap to set up, no extra dependencies. Pitfall: context updates re-render every consumer, which causes jank at 20+ tokens/second if your context holds the full accumulated string.

**Zustand** — lightweight global store. Minimises re-renders because components subscribe only to the slice they need. Recommended pattern: store `{ messages: MessageParam[], streaming: boolean, error: string | null }` in a Zustand slice. Update only the last message in `streaming` state.

```typescript
const useChatStore = create<ChatState>((set) => ({
  messages: [],
  streaming: false,
  appendChunk: (chunk: string) => set((s) => {
    const last = s.messages.at(-1);
    if (last?.role !== 'assistant') return s;
    return {
      messages: [...s.messages.slice(0, -1),
                 { ...last, content: (last.content as string) + chunk }],
    };
  }),
}));
```

**Server state (React Query / SWR)** — good if your chat history is persisted server-side and you need cache invalidation, pagination of history, and optimistic updates for network resilience.

## Edge Runtime vs Node.js runtime

The Anthropic TypeScript SDK runs on both: it is built on `fetch` and supports web-standard runtimes, including Vercel Edge. The choice is about **your** route's constraints, not the SDK's:

- **Edge** (`export const runtime = 'edge'`): fast cold starts, close to users. No Node.js built-ins (`fs`, `net`, native DB drivers) in your own code, and platform limits on execution time and on how long a response may take to start.
- **Node.js** (`export const runtime = 'nodejs'`, the default): full Node APIs, your database drivers, longer execution limits. It's usually the right home for a route that loads context from a database, runs server-side tools, and streams long generations.

For a government deployment the deciding question is often where the code physically runs. An edge network may place execution outside your authorised boundary.

## Performance: output format and prompt caching

**Controlling format.** Assistant-message prefill (ending `messages` with a partial `assistant` turn) is **not supported** on current models and returns a 400. To get a reliable format, use structured outputs (`output_config: { format: ... }` with a JSON schema, or `client.messages.parse()`), or clear system-prompt instructions.

**Prompt caching.** For static system prompts or large document context, mark the end of the stable prefix with `cache_control: { type: 'ephemeral' }`. Cache reads are billed at about 10% of the input price, and cache writes cost a little more than normal input. The default TTL is 5 minutes, refreshed on each hit; `{ type: 'ephemeral', ttl: '1h' }` keeps it for an hour. Caching is a prefix match, so anything that changes per request must come after the breakpoint. Prefixes below a model-dependent minimum length are not cached at all.

```typescript
{
  role: 'user',
  content: [{
    type: 'text',
    text: largeContextDocument,
    cache_control: { type: 'ephemeral' },
  }],
}
```

Check `usage.cache_read_input_tokens` on repeat calls to confirm the cache is being hit.

## Testing with jest + msw (Mock Service Worker)

MSW intercepts `fetch` at the network layer — no real HTTP requests in tests.

```typescript
// tests/handlers.ts
import { http, HttpResponse } from 'msw';

export const handlers = [
  http.post('https://api.anthropic.com/v1/messages', () => {
    return HttpResponse.json({
      id: 'msg_test',
      type: 'message',
      role: 'assistant',
      content: [{ type: 'text', text: 'Hello from mock.' }],
      model: 'claude-opus-5-5',
      stop_reason: 'end_turn',
      usage: { input_tokens: 10, output_tokens: 5 },
    });
  }),
];
```

Set up `setupServer` in `jest.setup.ts` and import handlers. The SDK calls `fetch` under the hood, so MSW intercepts its requests without any module-level mocking.

For streaming tests, return a `ReadableStream` body from the MSW handler.

## Reflection questions

Answer the two fields on this step:

1. **How will you manage conversation state across server and client in Next.js?**
2. **Will you run this on the Edge Runtime or the Node.js runtime? Explain your choice.**

To go further: why does `appendChunk` update only the last message instead of replacing the whole array? And what does an MSW test exercise that a jest mock of the `@anthropic-ai/sdk` module doesn't?

---

**Your task:** Answer the reflection questions to complete this mission.
