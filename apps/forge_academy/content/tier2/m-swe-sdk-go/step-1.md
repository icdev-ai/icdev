---
ontology_id: icdev:mission:m-swe-sdk-go:step:1
step_class: icdev:Lesson
---

<!-- CUI // SP-CTI -->

# Go + Claude API — Structured Output & Concurrency

Go's strengths — static typing, goroutines, and explicit error handling — align naturally with production AI service patterns. This mission covers structured output extraction and the concurrent request patterns that let a Go service handle multiple LLM calls efficiently.

## The anthropic-sdk-go package

```bash
go get github.com/anthropics/anthropic-sdk-go
```

The module path is `github.com/anthropics/anthropic-sdk-go`. It requires Go 1.21+. The SDK is built around the standard `net/http` transport — no third-party HTTP library dependency. The client is safe for concurrent use across goroutines.

## How Go's http.Client maps to the SDK

Internally, the SDK accepts an `option.WithHTTPClient(*http.Client)` option. This means you can inject your own `http.Client` with custom timeouts, a transport with connection pooling tuned for your environment, or a test transport for mocks:

```go
httpClient := &http.Client{
    Timeout: 60 * time.Second,
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

## MessageParam type

Conversations are built from `[]anthropic.MessageParam`. Each param carries a role (`user` or `assistant`) and content. Use the helpers:

```go
params := []anthropic.MessageParam{
    anthropic.NewUserMessage(anthropic.NewTextBlock("Summarise this document.")),
}
```

## Basic messages.New() call

```go
package main

import (
    "context"
    "fmt"
    "os"

    anthropic "github.com/anthropics/anthropic-sdk-go"
    "github.com/anthropics/anthropic-sdk-go/option"
)

func main() {
    client := anthropic.NewClient(
        option.WithAPIKey(os.Getenv("ANTHROPIC_API_KEY")),
    )

    msg, err := client.Messages.New(context.Background(), anthropic.MessageNewParams{
        Model:     anthropic.F(anthropic.ModelClaudioSonnet4_5),
        MaxTokens: anthropic.F(int64(1024)),
        Messages: anthropic.F([]anthropic.MessageParam{
            anthropic.NewUserMessage(anthropic.NewTextBlock("Hello, Claude.")),
        }),
    })
    if err != nil {
        fmt.Fprintf(os.Stderr, "API error: %v\n", err)
        os.Exit(1)
    }

    fmt.Println(msg.Content[0].Text)
}
```

## Structured output via JSON schema in system prompt

Go has no native type-safe structured output mode in the SDK (unlike function calling in other SDKs). The standard approach is to include a JSON schema in the system prompt and unmarshal the response:

```
System: Respond ONLY with valid JSON matching this schema:
{"type":"object","properties":{"summary":{"type":"string"},"severity":{"type":"string","enum":["low","medium","high","critical"]},"cves":{"type":"array","items":{"type":"string"}}},"required":["summary","severity","cves"]}
```

Claude reliably follows this instruction when the schema is precise and the system prompt has no competing instructions.

## Goroutines + WaitGroup/errgroup for concurrent calls

```go
import "golang.org/x/sync/errgroup"

g, ctx := errgroup.WithContext(context.Background())

results := make([]string, len(documents))
for i, doc := range documents {
    i, doc := i, doc // capture loop vars
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

`errgroup` cancels all in-flight goroutines via `ctx` as soon as any returns an error — critical for not wasting API credits on a partial fan-out.

## context.Context propagation

Every SDK call accepts a `context.Context`. Propagate it from your HTTP handler all the way to the API call. This gives you:
- Request-scoped cancellation (client disconnects → in-flight Claude call is aborted)
- Deadline propagation (set a per-request timeout with `context.WithTimeout`)
- Trace ID propagation via context values for distributed tracing

---

**Your task:** In the next step, design your Go integration.
