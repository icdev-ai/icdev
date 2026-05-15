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

## Edge Runtime limitations

Next.js Edge Runtime (`runtime = 'edge'`) does not support:
- Node.js built-ins (`fs`, `crypto`, `net`, `http`)
- `@anthropic-ai/sdk` (uses Node.js `http` internally)
- Long-running connections may be cut at CDN edge timeout limits (~30s on Vercel)

**Use `runtime = 'nodejs'` for any route that calls the Anthropic SDK.** Edge Runtime is suitable for lightweight middleware (auth checks, geo-routing) but not LLM routes.

## Performance: prefilling and prompt caching

**Prefilling** — pre-populate the assistant turn with a few tokens to guide the format. Pass an `assistant` role message at the end of your messages array. Reduces time-to-useful-content.

**Prompt caching** — for static system prompts or large document context, add `cache_control: { type: 'ephemeral' }` to the relevant message block. Cached tokens are billed at ~10% of input token cost. Cache TTL is 5 minutes — worth it for repeated calls within a session.

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
      model: 'claude-sonnet-4-5',
      stop_reason: 'end_turn',
      usage: { input_tokens: 10, output_tokens: 5 },
    });
  }),
];
```

Set up `setupServer` in `jest.setup.ts` and import handlers. Your `ClaudeService` or route handler calls `fetch` as normal — MSW intercepts without any mocking at the module level.

For streaming tests, return a `ReadableStream` body from the MSW handler.

## Reflection questions

1. Why does Zustand's `appendChunk` only update the last message instead of replacing the full array?
2. Your team wants to deploy the chat route to Vercel Edge Network for lower latency. What is the blocker, and what is the best alternative?
3. A user sends a 50-page PDF as context on every message in a session. How would prompt caching change the billing picture, and what is the cache expiry constraint you must work around?
4. MSW intercepts at the `fetch` layer. What does this test that a jest mock of the `@anthropic-ai/sdk` module does not test?
5. Your streaming route works in development but returns empty chunks in production. Name two things to check immediately.

---

**Your task:** Answer the reflection questions to complete this mission.
