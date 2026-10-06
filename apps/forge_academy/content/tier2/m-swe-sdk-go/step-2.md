---
ontology_id: icdev:mission:m-swe-sdk-go:step:2
step_class: icdev:Lesson
---

<!-- CUI // SP-CTI -->

# Design Your Go Integration

This step moves from concepts to design: a Go struct for structured output, the JSON schema that describes it, retry with exponential backoff, and a bounded parallel fan-out.

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

Derive the JSON schema from the struct by hand, or with a schema generator such as `github.com/invopop/jsonschema`:

```go
const threatSchema = `{
  "type": "object",
  "properties": {
    "summary":  { "type": "string" },
    "severity": { "type": "string", "enum": ["low","medium","high","critical"] },
    "cves":     { "type": "array",  "items": { "type": "string" } },
    "mitre":    { "type": "array",  "items": { "type": "string" } }
  },
  "required": ["summary","severity","cves","mitre"],
  "additionalProperties": false
}`
```

Pass this schema as the structured-output format (`output_config.format`) when you can. Use it in the system prompt as the fallback.

## Unmarshalling and validating the response

```go
var allowedSeverity = map[string]bool{"low": true, "medium": true, "high": true, "critical": true}

func parseThreatReport(raw string) (*ThreatReport, error) {
    // The system-prompt fallback sometimes wraps JSON in a ```json fence.
    raw = strings.TrimSpace(raw)
    raw = strings.TrimPrefix(raw, "```json")
    raw = strings.TrimPrefix(raw, "```")
    raw = strings.TrimSuffix(raw, "```")
    raw = strings.TrimSpace(raw)

    var report ThreatReport
    dec := json.NewDecoder(strings.NewReader(raw))
    dec.DisallowUnknownFields()
    if err := dec.Decode(&report); err != nil {
        return nil, fmt.Errorf("unmarshal failed: %w", err)
    }
    if !allowedSeverity[report.Severity] {
        return nil, fmt.Errorf("invalid severity %q", report.Severity)
    }
    return &report, nil
}
```

`json.Unmarshal` alone accepts any `severity` string and silently ignores unknown fields. Validation is your job unless the API enforced the schema.

## Text of a response

With thinking on (the default on current Opus models), `Content[0]` may be a thinking block. Collect the text blocks instead:

```go
func textOf(msg *anthropic.Message) string {
    var sb strings.Builder
    for _, block := range msg.Content {
        if t, ok := block.AsAny().(anthropic.TextBlock); ok {
            sb.WriteString(t.Text)
        }
    }
    return sb.String()
}
```

## Retry with exponential backoff

The SDK already retries 429 and 5xx responses twice. Raise that with `option.WithMaxRetries(n)` before you write your own loop. If you need custom policy, the SDK returns a single `*anthropic.Error` type for every non-2xx response; unwrap it with `errors.As` and branch on `StatusCode`:

```go
func callWithRetry(ctx context.Context, client anthropic.Client, params anthropic.MessageNewParams) (*anthropic.Message, error) {
    const maxAttempts = 4
    base := 500 * time.Millisecond

    for attempt := 0; attempt < maxAttempts; attempt++ {
        msg, err := client.Messages.New(ctx, params)
        if err == nil {
            return msg, nil
        }

        var apiErr *anthropic.Error
        if errors.As(err, &apiErr) && apiErr.StatusCode < 500 && apiErr.StatusCode != 429 {
            return nil, err // other 4xx: a caller bug, don't retry
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
    return nil, errors.New("unreachable")
}
```

If you retry yourself, set `option.WithMaxRetries(0)` on the client so the two layers don't multiply.

## Bounded parallel fan-out

Unbounded goroutines saturate your rate limit instantly. `errgroup.SetLimit` bounds concurrency without a hand-rolled semaphore:

```go
g, ctx := errgroup.WithContext(context.Background())
g.SetLimit(5)
reports := make([]*ThreatReport, len(documents))

for i, doc := range documents {
    g.Go(func() error { // blocks here while 5 calls are already in flight
        resp, err := callWithRetry(ctx, client, buildParams(doc))
        if err != nil {
            return err
        }
        reports[i], err = parseThreatReport(textOf(resp))
        return err
    })
}

if err := g.Wait(); err != nil {
    log.Fatalf("fan-out failed: %v", err)
}
```

## Configuration questions

Pick an answer for each field on this step:

1. **Response format your Go service needs.** Is it structured JSON (schema-validated), plain text or Markdown, streaming text, or tool-call results?
2. **Retry strategy for API failures.** Choose exponential backoff, fixed interval, fail fast, or a circuit breaker. Remember the SDK's built-in retries when you choose.
3. **Will you fan out concurrent LLM calls?** Choose a goroutine pool, errgroup (with `SetLimit`), or sequential calls only.

Questions to think through as you choose:

- What status code does a rate-limit response return, and why does the retry loop above treat it differently from other 4xx codes?
- `errgroup.WithContext` returns a derived context. What happens to calls using it when one goroutine returns an error?

---

**Your task:** Answer the configuration questions above.
