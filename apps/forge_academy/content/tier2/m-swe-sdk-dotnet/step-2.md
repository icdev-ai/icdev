---
ontology_id: icdev:mission:m-swe-sdk-dotnet:step:2
step_class: icdev:Lesson
---

<!-- CUI // SP-CTI -->

# Configure Your .NET Integration

With the DI wiring in place, this step covers the API usage you will actually ship: handling the response union, streaming with `IAsyncEnumerable`, running parallel calls, and the configuration hierarchy for the API key.

## Handling the response

`response.Content` is a list of `ContentBlock` unions. Narrow each one with `TryPick*` or with `.Value` + `OfType<T>()`:

```csharp
foreach (var block in response.Content)
{
    if (block.TryPickText(out TextBlock? text))
    {
        sb.Append(text.Text);
    }
    else if (block.TryPickToolUse(out ToolUseBlock? toolUse))
    {
        // Claude wants a tool: run it, send a tool_result, call again.
    }
}
```

If you filter with `OfType<TextBlock>()` alone, a tool-use turn comes back as an empty string. Check `response.StopReason` and handle tool use explicitly in an agentic loop.

## IAsyncEnumerable for streaming

Streaming is the recommended pattern for user-facing features. `client.Messages.CreateStreaming(...)` returns an async stream of `RawMessageStreamEvent` unions:

```csharp
public async IAsyncEnumerable<string> StreamAsync(
    string systemPrompt,
    string userMessage,
    [EnumeratorCancellation] CancellationToken ct = default)
{
    var parameters = new MessageCreateParams
    {
        Model     = _opts.Model,
        MaxTokens = 64000,
        System    = systemPrompt,
        Messages  = [new() { Role = Role.User, Content = userMessage }],
    };

    await foreach (var streamEvent in _client.Messages.CreateStreaming(parameters).WithCancellation(ct))
    {
        if (streamEvent.TryPickContentBlockDelta(out var delta) &&
            delta.Delta.TryPickText(out var text))
        {
            yield return text.Text;
        }
    }
}
```

In a minimal API or controller, write each chunk to `HttpContext.Response` and flush. `[EnumeratorCancellation]` makes the token passed through `WithCancellation(...)` reach the method, so the stream stops when the client disconnects.

## Parallel calls with Task.WhenAll and a concurrency cap

```csharp
public async Task<string[]> BatchAnalyzeAsync(IEnumerable<string> inputs, CancellationToken ct)
{
    using var gate = new SemaphoreSlim(10); // at most 10 calls in flight

    var tasks = inputs.Select(async input =>
    {
        await gate.WaitAsync(ct);
        try
        {
            return await _claude.CompleteAsync("You are a security analyst.", input, ct);
        }
        finally
        {
            gate.Release();
        }
    });

    return await Task.WhenAll(tasks);
}
```

Rate limits apply at the organisation level, so an uncapped `Task.WhenAll` over a large batch just converts work into 429s. The SDK retries those, but slowly.

## Config hierarchy: appsettings → User Secrets → Key Vault

.NET configuration is layered. Each layer overrides the previous one:

| Layer | Used in |
|---|---|
| `appsettings.json` | Committed defaults (no secrets) |
| `appsettings.{env}.json` | Environment-specific overrides |
| User Secrets (`dotnet user-secrets`) | Local development only; stored in your user profile, outside the repo |
| Environment variables | CI/CD and containers (`Anthropic__ApiKey` or `ANTHROPIC_API_KEY`) |
| Azure Key Vault / AWS Secrets Manager | Production |

```csharp
// Add Key Vault in production (Azure.Extensions.AspNetCore.Configuration.Secrets + Azure.Identity)
if (builder.Environment.IsProduction())
{
    var kvUri = builder.Configuration["KeyVaultUri"]!;
    builder.Configuration.AddAzureKeyVault(new Uri(kvUri), new DefaultAzureCredential());
}
```

A Key Vault secret named `Anthropic--ApiKey` surfaces as the configuration key `Anthropic:ApiKey`, because the double dash maps to the colon separator.

## Configuration questions

Pick an answer for each field on this step:

1. **DI service lifetime for the Claude client.** Singleton, scoped, or transient? The client holds an HTTP connection pool and is safe to share.
2. **Async pattern.** Plain `async`/`await`, `IAsyncEnumerable` streaming, or `Task.WhenAll` fan-out.
3. **Where will the API key live?** `appsettings.json` + `IOptions<T>`, an environment variable only, a vault (Azure Key Vault or AWS Secrets Manager), or User Secrets for local dev.

Think about this as you choose: User Secrets live outside the project directory. What happens if a Docker image build relies on them?

---

**Your task:** Answer the configuration questions above.
