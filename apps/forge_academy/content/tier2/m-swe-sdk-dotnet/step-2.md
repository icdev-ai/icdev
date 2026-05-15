---
ontology_id: icdev:mission:m-swe-sdk-dotnet:step:2
step_class: icdev:Lesson
---

<!-- CUI // SP-CTI -->

# Configure Your .NET Integration

With the DI wiring in place, this step covers the full API usage: calling `CreateAsync`, streaming with `IAsyncEnumerable`, running parallel calls, and the configuration hierarchy for API key management.

## Calling Messages.CreateAsync

```csharp
public async Task<string> AnalyzeAsync(string input, CancellationToken ct)
{
    var response = await _client.Messages.CreateAsync(new MessageParameters
    {
        Model     = "claude-sonnet-4-5",
        MaxTokens = 2048,
        System    = [new SystemMessage("You are a security analyst.")],
        Messages  = [new Message { Role = RoleType.User, Content = input }],
    }, ct);

    return response.Content
                   .OfType<TextContent>()
                   .Select(t => t.Text)
                   .FirstOrDefault() ?? string.Empty;
}
```

`response.Content` is `IReadOnlyList<ContentBase>`. Use `OfType<TextContent>()` to filter — tool use responses will contain `ToolUseContent` blocks which you must handle separately in an agentic loop.

## IAsyncEnumerable for streaming

Streaming is the recommended pattern for user-facing features. The SDK exposes it as `IAsyncEnumerable<MessageStreamEvent>`:

```csharp
public async IAsyncEnumerable<string> StreamAsync(
    string systemPrompt,
    string userMessage,
    [EnumeratorCancellation] CancellationToken ct = default)
{
    var parameters = new MessageParameters
    {
        Model     = "claude-sonnet-4-5",
        MaxTokens = 2048,
        System    = [new SystemMessage(systemPrompt)],
        Messages  = [new Message { Role = RoleType.User, Content = userMessage }],
        Stream    = true,
    };

    await foreach (var streamEvent in
        _client.Messages.StreamClaudeMessageAsync(parameters, ct)
                        .WithCancellation(ct))
    {
        if (streamEvent is ContentBlockDeltaEvent { Delta: TextDelta td })
        {
            yield return td.Text;
        }
    }
}
```

In a minimal API or controller, pipe this directly to a `StreamWriter` on the `HttpContext.Response` with `Content-Type: text/plain`. The `[EnumeratorCancellation]` attribute correctly propagates the `CancellationToken` when the client disconnects.

## Parallel calls with Task.WhenAll

```csharp
public async Task<IEnumerable<string>> BatchAnalyzeAsync(
    IEnumerable<string> inputs,
    CancellationToken ct)
{
    var tasks = inputs.Select(input =>
        _client.Messages.CreateAsync(new MessageParameters
        {
            Model     = "claude-sonnet-4-5",
            MaxTokens = 512,
            Messages  = [new Message { Role = RoleType.User, Content = input }],
        }, ct)
        .ContinueWith(t => t.Result.Content.OfType<TextContent>()
                                           .FirstOrDefault()?.Text ?? "",
                      ct, TaskContinuationOptions.OnlyOnRanToCompletion,
                      TaskScheduler.Default)
    );

    return await Task.WhenAll(tasks);
}
```

Bound concurrency with `SemaphoreSlim` before the `Select` if your batch size is large — API rate limits apply at the account level.

## Config hierarchy: appsettings → User Secrets → Azure Key Vault

.NET configuration is layered. Each layer overrides the previous:

| Layer | Used in |
|---|---|
| `appsettings.json` | Committed defaults (no secrets) |
| `appsettings.{env}.json` | Environment-specific, not committed |
| User Secrets (`dotnet user-secrets`) | Local dev only — `~/.microsoft/usersecrets/` |
| Environment variables | CI/CD pipelines |
| Azure Key Vault | Production — `AddAzureKeyVault(...)` |

```csharp
// Add Key Vault in production
if (builder.Environment.IsProduction())
{
    var kvUri = builder.Configuration["KeyVaultUri"]!;
    builder.Configuration.AddAzureKeyVault(new Uri(kvUri), new DefaultAzureCredential());
}
```

The API key in Key Vault is read as `Anthropic--ApiKey` (double-dash maps to the colon separator in the options class).

## Configuration questions

1. Why is `[EnumeratorCancellation]` required on the `CancellationToken` parameter in an `async IAsyncEnumerable` method?
2. `Task.WhenAll` with 100 simultaneous calls might hit rate limits. How would you add a `SemaphoreSlim` to cap concurrency at 10?
3. User Secrets are stored outside the project directory. What is the risk if you rely on them in a Docker container build?
4. The streaming method returns `IAsyncEnumerable<string>`. How would you convert this to an ASP.NET Core SSE response?
5. `OfType<TextContent>()` returns empty if the model returns a `ToolUseContent` block. How would you detect and handle this case?

---

**Your task:** Answer the configuration questions above.
