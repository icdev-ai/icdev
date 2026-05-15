---
ontology_id: icdev:mission:m-swe-sdk-dotnet:step:1
step_class: icdev:Lesson
---

<!-- CUI // SP-CTI -->

# .NET / C# + Anthropic SDK — Dependency Injection Pattern

.NET applications live on dependency injection. The Anthropic C# SDK is designed to plug into `IServiceCollection` cleanly, making it a first-class citizen alongside your EF Core contexts and HTTP clients.

## The Anthropic.SDK NuGet package

```bash
dotnet add package Anthropic.SDK
```

The package targets `net8.0` and higher. It ships `IAnthropicClient`, a concrete `AnthropicClient`, and strongly-typed models for messages, content blocks, and streaming events. All operations are `async` — the SDK has no synchronous API surface.

## IAnthropicClient interface

The SDK exposes `IAnthropicClient` as the primary abstraction:

```csharp
public interface IAnthropicClient
{
    Task<MessageResponse> Messages.CreateAsync(
        MessageParameters parameters,
        CancellationToken cancellationToken = default);

    IAsyncEnumerable<MessageStreamEvent> Messages.StreamClaudeMessageAsync(
        MessageParameters parameters,
        CancellationToken cancellationToken = default);
}
```

Always depend on `IAnthropicClient`, never on the concrete class — this keeps your service testable with `Moq` or `NSubstitute` without any HTTP traffic.

## Registering via IServiceCollection

```csharp
// Program.cs
using Anthropic.SDK;
using Anthropic.SDK.Extensions;

var builder = WebApplication.CreateBuilder(args);

builder.Services.AddAnthropic(options =>
{
    options.ApiKey = builder.Configuration["Anthropic:ApiKey"]
                    ?? throw new InvalidOperationException("Anthropic:ApiKey is required.");
});

builder.Services.AddSingleton<ClaudeService>();
```

`AddAnthropic()` registers `IAnthropicClient` as a singleton and wires it to `IHttpClientFactory` internally. This means the SDK participates in .NET's `HttpClient` lifecycle management — no socket exhaustion, no stale DNS.

## IConfiguration + IOptions<AnthropicOptions>

For richer configuration:

```csharp
// appsettings.json
{
  "Anthropic": {
    "ApiKey": "",
    "Model": "claude-sonnet-4-5",
    "MaxTokens": 2048
  }
}
```

```csharp
public class AnthropicOptions
{
    public string ApiKey   { get; set; } = string.Empty;
    public string Model    { get; set; } = "claude-sonnet-4-5";
    public int    MaxTokens { get; set; } = 2048;
}

builder.Services.Configure<AnthropicOptions>(
    builder.Configuration.GetSection("Anthropic"));
```

Your service then injects `IOptions<AnthropicOptions>` to read these values without touching `IConfiguration` directly.

## Minimal ClaudeService + Program.cs

```csharp
// ClaudeService.cs
public sealed class ClaudeService
{
    private readonly IAnthropicClient _client;
    private readonly AnthropicOptions _opts;

    public ClaudeService(IAnthropicClient client, IOptions<AnthropicOptions> opts)
    {
        _client = client;
        _opts   = opts.Value;
    }

    public async Task<string> CompleteAsync(
        string systemPrompt,
        string userMessage,
        CancellationToken ct = default)
    {
        var response = await _client.Messages.CreateAsync(new MessageParameters
        {
            Model      = _opts.Model,
            MaxTokens  = _opts.MaxTokens,
            System     = [new SystemMessage(systemPrompt)],
            Messages   = [new Message { Role = RoleType.User, Content = userMessage }],
        }, ct);

        return response.Content
                       .OfType<TextContent>()
                       .FirstOrDefault()?.Text ?? string.Empty;
    }
}
```

## IHttpClientFactory integration

`AddAnthropic()` registers a named `HttpClient` via `IHttpClientFactory`. You can configure it:

```csharp
builder.Services.AddHttpClient("Anthropic", c =>
{
    c.Timeout = TimeSpan.FromSeconds(90);
});
```

This is the correct .NET idiom — never create `new HttpClient()` manually in a service.

---

**Your task:** In the next step, configure your .NET integration.
