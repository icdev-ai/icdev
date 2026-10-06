---
ontology_id: icdev:mission:m-swe-sdk-go:step:1
step_class: icdev:Lesson
---

<!-- CUI // SP-CTI -->

# Go + Claude API — Structured Output & Concurrency

Go's strengths — static typing, goroutines, and explicit error handling — align naturally with production AI service patterns. This mission covers structured output extraction and the concurrent request patterns that let a Go service handle multiple LLM calls efficiently.

> The code in this mission runs in your own Go project, not in the Academy sandbox. Model IDs and SDK shapes below match the official `anthropic-sdk-go` v1 API. Check the SDK's README and release notes when you upgrade.

## The anthropic-sdk-go package

```bash
go get github.com/anthropics/anthropic-sdk-go
```

The module path is `github.com/anthropics/anthropic-sdk-go`; request options live in the `option` subpackage. The SDK is built on the standard `net/http` transport, so there's no third-party HTTP dependency. Create one client and share it: it is safe for concurrent use across goroutines.

`anthropic.NewClient()` with no arguments reads `ANTHROPIC_API_KEY` from the environment. The SDK already retries connection errors, 408, 409, 429 and 5xx responses with exponential backoff (2 retries by default). Keep that in mind before you add your own retry loop on top.

## How Go's http.Client maps to the SDK

The SDK accepts an `option.WithHTTPClient(*http.Client)` option. You can inject your own `http.Client` with custom timeouts, a transport tuned for connection pooling, or a test transport for mocks:

```go
httpClient := &http.Client{
    Timeout: 120 * time.Second,
    Transport: &http.Transport{
        MaxIdleConnsPerHost: 20,
        IdleConnTimeout:     90 * time.Second,
    },
}
client := anthropic.NewClient(
    option.WithAPIKey(os.Getenv("ANTHROPIC_API_KEY")),
    option.WithHTTPClient(httpClient),
)
```

A hard `http.Client.Timeout` also cuts off streaming responses. For long generations, prefer per-request deadlines through `context.WithTimeout`.

## MessageParam type

Conversations are built from `[]anthropic.MessageParam`. Each param carries a role (`user` or `assistant`) and content. Use the helpers:

```go
messages := []anthropic.MessageParam{
    anthropic.NewUserMessage(anthropic.NewTextBlock("Summarise this document.")),
}
```

## Basic Messages.New() call

In the v1 SDK, request fields are plain Go values (the old `anthropic.F(...)` wrappers are gone), and `Model` is a string alias, so you pass the model ID directly:

```go
package main

import (
    "context"
    "fmt"
    "os"

    "github.com/anthropics/anthropic-sdk-go"
    "github.com/anthropics/anthropic-sdk-go/option"
)

func main() {
    client := anthropic.NewClient(
        option.WithAPIKey(os.Getenv("ANTHROPIC_API_KEY")),
    )

    msg, err := client.Messages.New(context.Background(), anthropic.MessageNewParams{
        Model:     "claude-opus-5-5",
        MaxTokens: 16000,
        Messages: []anthropic.MessageParam{
            anthropic.NewUserMessage(anthropic.NewTextBlock("Hello, Claude.")),
        },
    })
    if err != nil {
        fmt.Fprintf(os.Stderr, "API error: %v\n", err)
        os.Exit(1)
    }

    // Content is a list of blocks (thinking, text, tool_use, ...). Don't assume
    // Content[0] is text; switch on the variant.
    for _, block := range msg.Content {
        switch b := block.AsAny().(type) {
        case anthropic.TextBlock:
            fmt.Println(b.Text)
        }
    }
}
```

## Structured output

There are two ways to get JSON you can unmarshal into a Go struct:

1. **Structured outputs (preferred).** The Messages API accepts a JSON schema under `output_config.format` and constrains the response to it. In Go you set it through the `OutputConfig` field of `MessageNewParams`; see the SDK reference for the exact format type.
2. **Schema in the system prompt (fallback).** Put the schema in the system prompt and unmarshal the reply. It works well when the schema is precise, but nothing enforces it, so you must validate.

```
System: Respond ONLY with valid JSON matching this schema:
{"type":"object","properties":{"summary":{"type":"string"},"severity":{"type":"string","enum":["low","medium","high","critical"]},"cves":{"type":"array","items":{"type":"string"}}},"required":["summary","severity","cves"]}
```

Assistant-message prefill (ending the conversation with a partial assistant turn such as `{`) is **not** supported on current models and returns a 400. Use one of the two approaches above.

## Goroutines + errgroup for concurrent calls

```go
import "golang.org/x/sync/errgroup"

g, ctx := errgroup.WithContext(context.Background())
g.SetLimit(5) // at most 5 calls in flight

results := make([]string, len(documents))
for i, doc := range documents {
    g.Go(func() error {
        resp, err := callClaude(ctx, client, doc)
        if err != nil {
            return err
        }
        results[i] = resp
        return nil
    })
}

if err := g.Wait(); err != nil {
    log.Fatalf("one or more calls failed: %v", err)
}
```

As soon as any goroutine returns an error, `errgroup` cancels the shared `ctx`. Every in-flight SDK call that was passed that `ctx` is aborted, so a failed fan-out stops spending API credits. Since Go 1.22, each loop iteration gets its own `i` and `doc`, so the old `i, doc := i, doc` copy is no longer needed.

## context.Context propagation

Every SDK call accepts a `context.Context`. Propagate it from your HTTP handler all the way to the API call. This gives you:

- Request-scoped cancellation (client disconnects → in-flight Claude call is aborted)
- Deadline propagation (set a per-request timeout with `context.WithTimeout`)
- Trace ID propagation via context values for distributed tracing

---

**Your task:** In the next step, design your Go integration.
