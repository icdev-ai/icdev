---
ontology_id: icdev:mission:m-swe-sdk-typescript:step:1
step_class: icdev:Lesson
---

<!-- CUI // SP-CTI -->

# Next.js + Claude SDK — Streaming Architecture

AI features in web applications live or die by perceived latency. A 4-second wait for a complete response feels broken. The same 4 seconds streaming token-by-token feels fast. This mission covers the full streaming architecture for the Anthropic SDK in a Next.js App Router application.

## The @anthropic-ai/sdk package

```bash
npm install @anthropic-ai/sdk
```

The package ships CommonJS and ESM builds and runs on Node.js 18+ and on web-standard runtimes (it uses `fetch`), so it works in Next.js route handlers on either the Node.js or the Edge runtime. Never import it in a client component (`'use client'`): your API key would be exposed to the browser.

> The code in this mission runs in your own Next.js project, not in the Academy sandbox.

## How Next.js App Router API routes work for AI

App Router API routes live in `app/api/<path>/route.ts`. Each file exports named functions (`GET`, `POST`, etc.) that receive a `Request` object and return a `Response`. This maps cleanly to the streaming pattern:

```
Browser  →  POST /api/chat  →  route.ts  →  Anthropic SDK  →  Claude API
                                  ↓
Browser  ←  ReadableStream (SSE)  ←  route.ts streams chunks
```

The key insight: `route.ts` runs on the **server**. Your API key never reaches the browser. The browser reads a `ReadableStream` of text chunks and appends them to the UI as they arrive.

## ReadableStream + TextDecoder for streaming

```typescript
// app/api/chat/route.ts
import Anthropic from '@anthropic-ai/sdk';

const client = new Anthropic(); // reads ANTHROPIC_API_KEY from process.env

export async function POST(req: Request) {
  const { messages } = await req.json();

  const stream = client.messages.stream({
    model: 'claude-opus-5-5',
    max_tokens: 16000,
    messages,
  });

  const readable = new ReadableStream({
    async start(controller) {
      for await (const chunk of stream) {
        if (
          chunk.type === 'content_block_delta' &&
          chunk.delta.type === 'text_delta'
        ) {
          controller.enqueue(new TextEncoder().encode(chunk.delta.text));
        }
      }
      controller.close();
    },
  });

  return new Response(readable, {
    headers: { 'Content-Type': 'text/plain; charset=utf-8' },
  });
}
```

The client uses `TextDecoder` to read the chunks back as strings and appends them to state.

## The Message object structure

A complete (non-streaming) response is a `Message` with:

- `id` — unique identifier
- `role` — always `"assistant"`
- `content` — array of `ContentBlock` (`"text"`, `"tool_use"`, and on current Opus models `"thinking"`, so filter by type rather than reading `content[0]`)
- `model` — the model that generated it
- `stop_reason` — `"end_turn"`, `"tool_use"`, `"max_tokens"`, etc.
- `usage` — `{ input_tokens, output_tokens }`

During streaming, you receive `RawMessageStreamEvent` objects. The useful types are `content_block_delta` (carries the text chunk) and `message_delta` (carries `stop_reason` when done). If you also need the complete message, `await stream.finalMessage()` returns it once the stream ends.

## Why streaming matters for UX

Time-to-first-token is typically 200–800 ms. Without streaming, the user sees nothing for the full generation time (1–10+ seconds). With streaming, they see text appear almost immediately. Research on AI UX consistently shows users rate streamed responses as faster and more trustworthy even when total latency is identical.

## Server-side vs client-side tool calling

Tool calling can happen server-side (in `route.ts`) or trigger a round-trip to the client:

| Pattern | When to use |
|---|---|
| Server-side tool execution | Database lookups, secret-keyed APIs, file I/O |
| Client-side tool execution | Browser APIs (geolocation, clipboard), UI mutations |

For government applications, prefer server-side tool execution — the tool logic stays inside your IL boundary.

---

**Your task:** In the next step, configure your AI feature.
