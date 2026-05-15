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

When streaming from the Claude API, check `ctx.Done()` between chunks to honour client cancellation:

```go
stream := client.Messages.NewStreaming(ctx, params)
for stream.Next() {
    event := stream.Current()
    if delta, ok := event.Delta.(anthropic.ContentBlockDeltaEventDelta); ok {
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
if err := stream.Err(); err != nil && !errors.Is(err, context.Canceled) {
    return err
}
```

The non-blocking `select` with `default` checks for cancellation without blocking on every iteration.

## slog structured logging without leaking prompt content

Go 1.21's `log/slog` package is the standard structured logger. Log metadata, never content:

```go
slog.Info("claude call complete",
    slog.String("model", string(params.Model.Value)),
    slog.Int("input_tokens",  int(msg.Usage.InputTokens)),
    slog.Int("output_tokens", int(msg.Usage.OutputTokens)),
    slog.Duration("latency", time.Since(start)),
    slog.String("stop_reason", string(msg.StopReason)),
)
// NEVER: slog.String("prompt", systemPrompt)
// NEVER: slog.String("response", msg.Content[0].Text)
```

In a CUI environment, prompt and response content is classified data. It must not appear in application logs which may flow to unclassified log aggregators.

## Interface-based mocking for tests

Define a `ClaudeClient` interface that matches the methods you call:

```go
type ClaudeClient interface {
    NewMessage(ctx context.Context, params anthropic.MessageNewParams) (*anthropic.Message, error)
}

type Service struct {
    claude ClaudeClient
}
```

In tests, implement the interface with a mock:

```go
type mockClaude struct {
    response string
    err      error
}

func (m *mockClaude) NewMessage(_ context.Context, _ anthropic.MessageNewParams) (*anthropic.Message, error) {
    if m.err != nil {
        return nil, m.err
    }
    return &anthropic.Message{
        Content: []anthropic.ContentBlock{{Text: m.response}},
    }, nil
}
```

This approach tests your service logic without hitting the network, with full type safety.

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

1. What happens to a goroutine blocked on `client.Messages.New(...)` when the parent context is cancelled?
2. Why is a non-blocking `select { case <-ctx.Done(): default: }` preferred over a blocking `select` in the streaming loop?
3. Your service processes 1000 documents per minute. Each call averages 2 seconds. How many concurrent goroutines does your `maxConcurrent = 5` bounded fan-out actually keep busy at steady state?
4. Why should the interface-based mock live in `_test.go` files rather than production code?
5. You deploy your service and notice that after a rolling restart, some requests fail with `connection reset by peer`. What shutdown pattern change would address this?

---

**Your task:** Answer the reflection questions to complete this mission.
