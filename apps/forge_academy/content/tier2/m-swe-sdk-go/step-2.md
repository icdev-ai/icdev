<!-- CUI // SP-CTI -->

# Design Your Go Integration

This step moves from concepts to design: defining a Go struct for structured output, a complete JSON schema injection, retry with exponential backoff, and a bounded parallel fan-out pattern.

## Defining a Go struct for structured output

```go
// ThreatReport is the structured output Claude returns for a threat analysis.
type ThreatReport struct {
    Summary  string   `json:"summary"`
    Severity string   `json:"severity"` // low | medium | high | critical
    CVEs     []string `json:"cves"`
    MITRE    []string `json:"mitre"`    // ATT&CK technique IDs
}
```

Derive the JSON schema from the struct manually (or use a schema generator like `github.com/invopop/jsonschema`):

```go
const threatSchema = `{
  "type": "object",
  "properties": {
    "summary":  { "type": "string" },
    "severity": { "type": "string", "enum": ["low","medium","high","critical"] },
    "cves":     { "type": "array",  "items": { "type": "string" } },
    "mitre":    { "type": "array",  "items": { "type": "string" } }
  },
  "required": ["summary","severity","cves","mitre"]
}`
```

## Unmarshalling the response

```go
func parseThreatReport(raw string) (*ThreatReport, error) {
    // Strip markdown fences if the model wraps JSON in ```json ... ```
    raw = strings.TrimSpace(raw)
    if strings.HasPrefix(raw, "```") {
        raw = strings.Trim(raw, "`")
        raw = strings.TrimPrefix(raw, "json")
        raw = strings.TrimSpace(raw)
    }

    var report ThreatReport
    if err := json.Unmarshal([]byte(raw), &report); err != nil {
        return nil, fmt.Errorf("unmarshal failed: %w\nraw: %s", err, raw)
    }
    return &report, nil
}
```

The markdown fence stripping is a practical necessity — even with schema instructions, Claude occasionally wraps JSON in a code fence.

## Retry with exponential backoff

Using the standard library (no extra dependency):

```go
func callWithRetry(ctx context.Context, client *anthropic.Client, params anthropic.MessageNewParams) (*anthropic.Message, error) {
    const maxAttempts = 4
    base := 500 * time.Millisecond

    for attempt := range maxAttempts {
        msg, err := client.Messages.New(ctx, params)
        if err == nil {
            return msg, nil
        }

        var apiErr *anthropic.Error
        if errors.As(err, &apiErr) && apiErr.StatusCode < 500 {
            return nil, err // 4xx: don't retry
        }

        if attempt == maxAttempts-1 {
            return nil, fmt.Errorf("max retries exceeded: %w", err)
        }

        jitter := time.Duration(rand.Int63n(int64(base)))
        wait := (base << attempt) + jitter
        select {
        case <-ctx.Done():
            return nil, ctx.Err()
        case <-time.After(wait):
        }
    }
    panic("unreachable")
}
```

`sethvargo/go-retry` provides a cleaner API with configurable back-off policies if you prefer a library.

## Bounded parallel fan-out

Running all goroutines unbounded saturates the API rate limit instantly. Use a semaphore channel to bound concurrency:

```go
const maxConcurrent = 5

sem := make(chan struct{}, maxConcurrent)
g, ctx := errgroup.WithContext(context.Background())
reports := make([]*ThreatReport, len(documents))

for i, doc := range documents {
    i, doc := i, doc
    sem <- struct{}{} // acquire slot
    g.Go(func() error {
        defer func() { <-sem }() // release slot
        resp, err := callWithRetry(ctx, client, buildParams(doc))
        if err != nil {
            return err
        }
        reports[i], err = parseThreatReport(resp.Content[0].Text)
        return err
    })
}

if err := g.Wait(); err != nil {
    log.Fatalf("fan-out failed: %v", err)
}
```

## Configuration questions

1. Why do we capture `i` and `doc` as new variables inside the loop before passing them to the goroutine?
2. The retry skips 4xx errors. What specific status code would a rate-limit response return, and should it be retried?
3. If `sem <- struct{}{}` is placed inside the goroutine instead of outside, what race condition can occur?
4. How would you modify `parseThreatReport` to validate that `severity` is one of the allowed enum values before returning?
5. `errgroup.WithContext` returns a new context. What happens to that context when one goroutine returns an error?

---

**Your task:** Answer the configuration questions above.
