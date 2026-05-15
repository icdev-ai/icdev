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
import type { MessageParam } from '@anthropic-ai/sdk/resources/messages';

const client = new Anthropic({
  apiKey: process.env.ANTHROPIC_API_KEY,
});

export const runtime = 'nodejs'; // required: SDK uses Node.js built-ins

export async function POST(req: Request): Promise<Response> {
  const body = await req.json();
  const messages: MessageParam[] = body.messages;

  if (!messages?.length) {
    return new Response('messages required', { status: 400 });
  }

  const stream = client.messages.stream({
    model: 'claude-sonnet-4-5',
    max_tokens: 2048,
    system: 'You are a helpful assistant.',
    messages,
    tools: body.tools ?? [],
    tool_choice: body.tool_choice ?? { type: 'auto' },
  });

  const encoder = new TextEncoder();
  const readable = new ReadableStream({
    async start(controller) {
      try {
        for await (const event of stream) {
          if (
            event.type === 'content_block_delta' &&
            event.delta.type === 'text_delta'
          ) {
            controller.enqueue(encoder.encode(event.delta.text));
          }
          if (event.type === 'message_delta' && event.delta.stop_reason) {
            // Optionally signal stop reason via a sentinel chunk
            controller.enqueue(encoder.encode('\x00'));
          }
        }
      } catch (err) {
        controller.error(err);
      } finally {
        controller.close();
      }
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

If you use the `ai` SDK from Vercel, the `useChat` hook wraps this pattern with connection management, optimistic updates, and abort-on-unmount.

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

## Server-side tool calling with tool_choice

```typescript
tools: [{
  name: 'get_document',
  description: 'Retrieve a classified document by ID',
  input_schema: {
    type: 'object' as const,
    properties: { doc_id: { type: 'string' } },
    required: ['doc_id'],
  },
}],
tool_choice: { type: 'auto' },
```

When the model returns a `tool_use` block, execute the function server-side and re-invoke the API with the `tool_result`. Never relay tool calls to the browser for server-side resources.

## Error boundary for AI failures

Wrap your AI UI component in a React `ErrorBoundary`. Network errors, timeouts, and model errors all surface as rejected promises in the fetch. A boundary prevents a streaming failure from crashing your entire page.

## Configuration questions

1. Why is `export const runtime = 'nodejs'` required in the route?
2. What happens if you import `@anthropic-ai/sdk` in a client component (`'use client'`)?
3. How would you add an `AbortController` to the `fetch` call so the stream stops when the user navigates away?
4. Your route needs to call a database to retrieve context before sending to Claude. Where in the route function should this happen, and why?

---

**Your task:** Answer the configuration questions above.
