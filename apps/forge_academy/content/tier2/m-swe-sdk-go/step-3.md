---
ontology_id: icdev:mission:m-swe-sdk-go:step:3
step_class: icdev:Assessment
---

<!-- CUI // SP-CTI -->

# Go Service Production Notes

A correct Go AI service is one thing; a maintainable, observable, and safely-shutdown service is another. This step covers the operational patterns specific to Go: context propagation through the full call stack, streaming cancellation, structured logging discipline, interface-based mocking, and graceful shutdown.

## Context propagation from HTTP handler to API call

The `context.Context` from your HTTP handler is the authoritative source of truth for the lifetime of every operation in that request. Propagate it everywhere — never create a new background context inside a handler:

```go
func (h *Handler) Analyze(w http.ResponseWriter, r *http.Request) {
    ctx := r.Context() // inherits deadline from server's ReadTimeout

    report, err := h.svc.Analyze(ctx, r.Body)
    if err != nil {
        if errors.Is(err, context.Canceled) {
            // Client disconnected — not a server error
            return
        }
        http.Error(w, "analysis failed", http.StatusInternalServerError)
        return
    }
    json.NewEncoder(w).Encode(report)
}
```

Set `ReadTimeout`, `WriteTimeout`, and `IdleTimeout` on `http.Server`. These propagate into request contexts automatically.

## ctx.Done() in streaming

When streaming from the Claude API, pass the request context to `NewStreaming`. A cancelled context ends the stream, and `stream.Err()` reports it. Check `ctx.Done()` between chunks too, so you stop writing to a client that has gone:

```go
stream := client.Messages.NewStreaming(ctx, params)
for stream.Next() {
    event := stream.Current()
    switch ev := event.AsAny().(type) {
    case anthropic.ContentBlockDeltaEvent:
        if delta, ok := ev.Delta.AsAny().(anthropic.TextDelta); ok {
            select {
            case <-ctx.Done():
                return ctx.Err()
            default:
            }
            w.Write([]byte(delta.Text))
            if f, ok := w.(http.Flusher); ok {
                f.Flush()
            }
        }
    }
}
if err := stream.Err(); err != nil && !errors.Is(err, context.Canceled) {
    return err
}
```

The non-blocking `select` with `default` checks for cancellation without blocking on every iteration. If you also need the full message at the end, call `message.Accumulate(event)` on an `anthropic.Message{}` inside the loop; the Go SDK has no `GetFinalMessage()` helper.

## slog structured logging without leaking prompt content

Go's `log/slog` package is the standard structured logger. Log metadata, never content:

```go
slog.Info("claude call complete",
    slog.String("model", string(params.Model)),
    slog.Int64("input_tokens",  msg.Usage.InputTokens),
    slog.Int64("output_tokens", msg.Usage.OutputTokens),
    slog.Duration("latency", time.Since(start)),
    slog.String("stop_reason", string(msg.StopReason)),
)
// NEVER: slog.String("prompt", systemPrompt)
// NEVER: slog.String("response", textOf(msg))
```

In a CUI environment, prompt and response content is classified data. It must not appear in application logs, which may flow to unclassified log aggregators.

## Interface-based mocking for tests

Don't mock the SDK's types. Define a small interface at **your** service's boundary, so tests never need to construct SDK response structs:

```go
// Completer is the only thing Service needs from Claude.
type Completer interface {
    Complete(ctx context.Context, system, user string) (string, error)
}

// claudeCompleter is the production implementation.
type claudeCompleter struct {
    client anthropic.Client
    model  string
}

func (c claudeCompleter) Complete(ctx context.Context, system, user string) (string, error) {
    msg, err := c.client.Messages.New(ctx, anthropic.MessageNewParams{
        Model:     anthropic.Model(c.model),
        MaxTokens: 16000,
        System:    []anthropic.TextBlockParam{{Text: system}},
        Messages:  []anthropic.MessageParam{anthropic.NewUserMessage(anthropic.NewTextBlock(user))},
    })
    if err != nil {
        return "", err
    }
    return textOf(msg), nil
}

type Service struct {
    claude Completer
}
```

In tests, implement the interface with a fake:

```go
type fakeCompleter struct {
    response string
    err      error
}

func (f fakeCompleter) Complete(context.Context, string, string) (string, error) {
    return f.response, f.err
}
```

This tests your parsing, validation and error handling without the network, and it survives SDK upgrades.

## Graceful shutdown with context cancellation

```go
ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGTERM, syscall.SIGINT)
defer stop()

srv := &http.Server{Addr: ":8080", Handler: mux}
go func() {
    if err := srv.ListenAndServe(); err != http.ErrServerClosed {
        log.Fatal(err)
    }
}()

<-ctx.Done()
shutdownCtx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
defer cancel()
srv.Shutdown(shutdownCtx) // drains in-flight requests, including streaming Claude calls
```

The 30-second shutdown window must be longer than your longest expected Claude streaming response.

## Reflection questions

Answer the two fields on this step:

1. **How will you propagate context cancellation from the HTTP handler to the Claude API call?**
2. **How will you validate the structured JSON output against your Go struct schema?**

To go further: what happens to a goroutine blocked on `client.Messages.New(...)` when its context is cancelled? And after a rolling restart, why might some requests fail with `connection reset by peer`, and what shutdown change fixes it?

---

**Your task:** Answer the reflection questions to complete this mission.
