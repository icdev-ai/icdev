---
ontology_id: icdev:mission:m-swe-sdk-typescript:step:2
step_class: icdev:Lesson
---

<!-- CUI // SP-CTI -->

# Build the AI Feature

With the streaming architecture clear, this step builds the complete implementation: a streaming `/api/chat` route, the React client that reads it, TypeScript types for all Claude data structures, server-side tool calling, and error boundaries.

## Full streaming route.ts

```typescript
// app/api/chat/route.ts
import Anthropic from '@anthropic-ai/sdk';

const client = new Anthropic(); // reads ANTHROPIC_API_KEY from the server environment

// Tools are defined on the SERVER. Never accept tool definitions from the request body:
// a browser could then hand your model arbitrary tools.
const TOOLS: Anthropic.Tool[] = [{
  name: 'get_document',
  description: 'Retrieve a document by ID from the internal store',
  input_schema: {
    type: 'object',
    properties: { doc_id: { type: 'string' } },
    required: ['doc_id'],
  },
}];

export async function POST(req: Request): Promise<Response> {
  const body = await req.json();
  const messages: Anthropic.MessageParam[] = body.messages;

  if (!messages?.length) {
    return new Response('messages required', { status: 400 });
  }

  const stream = client.messages.stream({
    model: 'claude-opus-5-5',
    max_tokens: 16000,
    system: 'You are a helpful assistant.',
    messages,
    tools: TOOLS,
  }, { signal: req.signal }); // abort the upstream call if the browser disconnects

  const encoder = new TextEncoder();
  const readable = new ReadableStream({
    async start(controller) {
      try {
        for await (const event of stream) {
          if (event.type === 'content_block_delta' && event.delta.type === 'text_delta') {
            controller.enqueue(encoder.encode(event.delta.text));
          }
        }
        controller.close();
      } catch (err) {
        controller.error(err); // error() and close() are mutually exclusive
      }
    },
    cancel() {
      stream.abort();
    },
  });

  return new Response(readable, {
    headers: {
      'Content-Type': 'text/plain; charset=utf-8',
      'X-Content-Type-Options': 'nosniff',
      'Cache-Control': 'no-store',
    },
  });
}
```

This route only streams text. If the model stops with `stop_reason: 'tool_use'`, run the tool on the server and call the API again (see below) before you finish the response.

## Client: manual fetch + ReadableStream

Without a pre-built hook library, reading the stream is straightforward:

```typescript
async function sendMessage(userText: string) {
  setLoading(true);
  setOutput('');

  const res = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ messages: [{ role: 'user', content: userText }] }),
  });

  if (!res.ok || !res.body) throw new Error('Stream failed');

  const reader = res.body.getReader();
  const decoder = new TextDecoder();

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    setOutput(prev => prev + decoder.decode(value, { stream: true }));
  }

  setLoading(false);
}
```

Pass an `AbortController`'s `signal` to `fetch` and abort it in your component's cleanup, so navigating away stops the stream (and, through `req.signal`, the upstream Claude call). If you use the Vercel AI SDK, its `useChat` hook wraps this pattern with connection management and abort-on-unmount.

## TypeScript types for Message and ContentBlock

```typescript
import type {
  Message,
  ContentBlock,
  TextBlock,
  ToolUseBlock,
  MessageParam,
} from '@anthropic-ai/sdk/resources/messages';

function isText(block: ContentBlock): block is TextBlock {
  return block.type === 'text';
}

function isToolUse(block: ContentBlock): block is ToolUseBlock {
  return block.type === 'tool_use';
}
```

Always use the SDK's exported types rather than redefining them — they stay in sync when you upgrade the package.

## Server-side tool calling

```typescript
const response = await client.messages.create({
  model: 'claude-opus-5-5',
  max_tokens: 16000,
  tools: TOOLS,
  tool_choice: { type: 'auto' },
  messages,
});

if (response.stop_reason === 'tool_use') {
  const toolResults = await Promise.all(
    response.content
      .filter((b): b is Anthropic.ToolUseBlock => b.type === 'tool_use')
      .map(async (b) => ({
        type: 'tool_result' as const,
        tool_use_id: b.id,
        content: JSON.stringify(await runTool(b.name, b.input)),
      })),
  );
  messages.push({ role: 'assistant', content: response.content });
  messages.push({ role: 'user', content: toolResults }); // all results in ONE user message
  // ...call the API again until stop_reason is 'end_turn'
}
```

Execute tools server-side and send the `tool_result` back. Never relay tool calls for server-side resources to the browser. Keep `tool_choice` on `auto`: forcing a specific tool (`{ type: 'tool' }` or `{ type: 'any' }`) is rejected by the newest models. The SDK's beta `toolRunner` helper can drive this loop for you.

## Error boundary for AI failures

Wrap your AI UI component in a React `ErrorBoundary`. Network errors, timeouts, and model errors all surface as rejected promises in the fetch. A boundary prevents a streaming failure from crashing your entire page.

## Configuration questions

Pick an answer for each field on this step:

1. **UI interaction pattern.** Chat interface (multi-turn), single-shot form + result, inline completion, or background job + result page.
2. **Server-side tool calling needed?** Should the LLM call your API routes, or only generate?
3. **How will you handle streaming in the UI?** `ReadableStream` + `TextDecoder`, Server-Sent Events, WebSocket, or poll for completion.

Think about these as you choose: what happens if you import `@anthropic-ai/sdk` in a `'use client'` component? And your route must fetch database context before calling Claude: where in the route does that belong, and why?

---

**Your task:** Answer the configuration questions above.
