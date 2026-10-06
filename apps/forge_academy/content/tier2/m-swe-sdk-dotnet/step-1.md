---
ontology_id: icdev:mission:m-swe-sdk-dotnet:step:1
step_class: icdev:Lesson
---

<!-- CUI // SP-CTI -->

# .NET / C# + Anthropic SDK — Dependency Injection Pattern

.NET applications live on dependency injection. This mission wires the official Anthropic C# SDK into `IServiceCollection` so the Claude client is a first-class dependency alongside your EF Core contexts and HTTP clients.

> The code in this mission runs in your own .NET project, not in the Academy sandbox. Type names below match the official `Anthropic` NuGet package. There is also an older community package called `Anthropic.SDK`, with different type names (`MessageParameters`, `IAnthropicClient`), that this mission does **not** use.

## The Anthropic NuGet package

```bash
dotnet add package Anthropic
```

The package gives you `AnthropicClient` (namespace `Anthropic`) and strongly-typed request and response models in `Anthropic.Models.Messages` (`MessageCreateParams`, `Role`, `TextBlock`, `ContentBlock`, ...). All calls are `async`. Typed exceptions live in `Anthropic.Exceptions`: `AnthropicRateLimitException`, `Anthropic5xxException`, `AnthropicApiException`, and others.

`new AnthropicClient()` reads `ANTHROPIC_API_KEY` from the environment. You can also set `ApiKey` explicitly. The client retries connection errors, 429 and 5xx responses on its own (2 retries by default).

## A basic call

```csharp
using Anthropic;
using Anthropic.Models.Messages;

AnthropicClient client = new();

var response = await client.Messages.Create(new MessageCreateParams
{
    Model = "claude-opus-5-5",
    MaxTokens = 16000,
    Messages = [new() { Role = Role.User, Content = "Hello, Claude" }],
});

// ContentBlock is a union: unwrap with .Value and filter to TextBlock.
// With thinking on (the default on current Opus models) the first block may
// be a ThinkingBlock, so never assume Content[0] is text.
foreach (var text in response.Content.Select(b => b.Value).OfType<TextBlock>())
{
    Console.WriteLine(text.Text);
}
```

## Your own interface, not the SDK's

`AnthropicClient` is a concrete class, and the SDK does not ship an interface for you to mock. The testable .NET pattern is to define a narrow interface for **what your application needs**, and implement it once over the SDK:

```csharp
public interface IClaudeService
{
    Task<string> CompleteAsync(string systemPrompt, string userMessage, CancellationToken ct = default);
}
```

The rest of your code depends on `IClaudeService`, and unit tests substitute it with `Moq` or `NSubstitute`. Only the one implementation class touches SDK types.

## Options + registration

```json
// appsettings.json (no secrets here)
{
  "Anthropic": {
    "Model": "claude-opus-5-5",
    "MaxTokens": 16000
  }
}
```

```csharp
public sealed class ClaudeOptions
{
    public string Model     { get; set; } = "claude-opus-5-5";
    public int    MaxTokens { get; set; } = 16000;
}
```

```csharp
// Program.cs
using Anthropic;

var builder = WebApplication.CreateBuilder(args);

builder.Services.Configure<ClaudeOptions>(builder.Configuration.GetSection("Anthropic"));

// One client for the whole app: it is safe to share across requests.
builder.Services.AddSingleton(_ => new AnthropicClient
{
    ApiKey = builder.Configuration["Anthropic:ApiKey"]
             ?? Environment.GetEnvironmentVariable("ANTHROPIC_API_KEY")
             ?? throw new InvalidOperationException("Anthropic:ApiKey is required."),
});

builder.Services.AddSingleton<IClaudeService, ClaudeService>();
```

## Minimal ClaudeService

```csharp
using Anthropic;
using Anthropic.Models.Messages;
using Microsoft.Extensions.Options;

public sealed class ClaudeService : IClaudeService
{
    private readonly AnthropicClient _client;
    private readonly ClaudeOptions _opts;

    public ClaudeService(AnthropicClient client, IOptions<ClaudeOptions> opts)
    {
        _client = client;
        _opts   = opts.Value;
    }

    public async Task<string> CompleteAsync(string systemPrompt, string userMessage, CancellationToken ct = default)
    {
        var response = await _client.Messages.Create(new MessageCreateParams
        {
            Model     = _opts.Model,
            MaxTokens = _opts.MaxTokens,
            System    = systemPrompt,
            Messages  = [new() { Role = Role.User, Content = userMessage }],
        }, cancellationToken: ct);

        return string.Concat(response.Content.Select(b => b.Value).OfType<TextBlock>().Select(t => t.Text));
    }
}
```

If the compiler disagrees on an overload, such as how `System` or the cancellation token is passed, follow its error message. The SDK's XML docs are the reference for exact signatures.

The SDK also integrates with `Microsoft.Extensions.AI`'s `IChatClient` abstraction, if your app already standardises on that.

---

**Your task:** In the next step, configure your .NET integration.
