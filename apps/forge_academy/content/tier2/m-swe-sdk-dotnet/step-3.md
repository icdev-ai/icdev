<!-- CUI // SP-CTI -->

# .NET Integration Review

The final step covers the four pillars of a production-grade .NET AI service: unit testing with mocks, resilience with Polly v8, observability with OpenTelemetry, and health checks.

## IAnthropicClient mock with Moq / NSubstitute

Because your service depends on `IAnthropicClient` (an interface), mocking is trivial:

```csharp
// Using NSubstitute
[Fact]
public async Task AnalyzeAsync_Returns_Text_Content()
{
    // Arrange
    var mockClient = Substitute.For<IAnthropicClient>();
    mockClient.Messages
              .CreateAsync(Arg.Any<MessageParameters>(), Arg.Any<CancellationToken>())
              .Returns(new MessageResponse
              {
                  Content = [new TextContent { Text = "Critical vulnerability detected." }],
                  StopReason = "end_turn",
              });

    var sut = new ClaudeService(mockClient, Options.Create(new AnthropicOptions()));

    // Act
    var result = await sut.AnalyzeAsync("Analyse this CVE.", CancellationToken.None);

    // Assert
    Assert.Equal("Critical vulnerability detected.", result);
}
```

With Moq, use `Mock<IAnthropicClient>` and `.Setup(...)`. Both libraries produce the same effect — pick whichever your team already uses.

## Polly v8 ResiliencePipeline for retry + circuit breaker

Polly v8 replaced the v7 API with a `ResiliencePipeline` builder:

```csharp
// Program.cs
builder.Services.AddResiliencePipeline("claude", pipelineBuilder =>
{
    pipelineBuilder
        .AddRetry(new RetryStrategyOptions
        {
            MaxRetryAttempts = 3,
            BackoffType      = DelayBackoffType.Exponential,
            Delay            = TimeSpan.FromSeconds(1),
            ShouldHandle     = args => args.Outcome switch
            {
                { Exception: HttpRequestException }        => PredicateResult.True(),
                { Result.StatusCode: >= HttpStatusCode.InternalServerError } => PredicateResult.True(),
                _ => PredicateResult.False(),
            },
        })
        .AddCircuitBreaker(new CircuitBreakerStrategyOptions
        {
            FailureRatio        = 0.5,
            SamplingDuration    = TimeSpan.FromSeconds(10),
            MinimumThroughput   = 5,
            BreakDuration       = TimeSpan.FromSeconds(30),
        });
});
```

Inject `ResiliencePipelineProvider<string>` into your service and call `pipeline.ExecuteAsync(async ct => await _client.Messages.CreateAsync(..., ct), ct)`.

## OpenTelemetry instrumentation for LLM latency

```csharp
builder.Services.AddOpenTelemetry()
    .WithTracing(tracing =>
    {
        tracing
            .AddAspNetCoreInstrumentation()
            .AddHttpClientInstrumentation()
            .AddSource("ClaudeService")
            .AddOtlpExporter();
    })
    .WithMetrics(metrics =>
    {
        metrics.AddAspNetCoreInstrumentation()
               .AddOtlpExporter();
    });
```

In `ClaudeService`, instrument the call:

```csharp
private static readonly ActivitySource _tracer = new("ClaudeService");

public async Task<string> AnalyzeAsync(string input, CancellationToken ct)
{
    using var activity = _tracer.StartActivity("claude.messages.create");
    activity?.SetTag("llm.model", _opts.Model);

    var sw = Stopwatch.StartNew();
    var response = await _client.Messages.CreateAsync(..., ct);
    sw.Stop();

    activity?.SetTag("llm.input_tokens",  response.Usage?.InputTokens);
    activity?.SetTag("llm.output_tokens", response.Usage?.OutputTokens);
    activity?.SetTag("llm.latency_ms",    sw.ElapsedMilliseconds);

    return /* text content */;
}
```

## Cancellation token propagation

Pass `CancellationToken` through every async boundary without exception. Use `[EnumeratorCancellation]` in async iterators. Never use `CancellationToken.None` in production paths — always propagate from the HTTP context.

## Health checks for AI dependencies

```csharp
builder.Services.AddHealthChecks()
    .AddCheck<AnthropicHealthCheck>("anthropic-api", HealthStatus.Degraded,
        tags: ["ai", "external"]);

public class AnthropicHealthCheck : IHealthCheck
{
    private readonly IAnthropicClient _client;
    public AnthropicHealthCheck(IAnthropicClient client) => _client = client;

    public async Task<HealthCheckResult> CheckHealthAsync(
        HealthCheckContext ctx, CancellationToken ct)
    {
        try
        {
            await _client.Messages.CreateAsync(new MessageParameters
            {
                Model     = "claude-haiku-4-5",
                MaxTokens = 1,
                Messages  = [new Message { Role = RoleType.User, Content = "ping" }],
            }, ct);
            return HealthCheckResult.Healthy();
        }
        catch (Exception ex)
        {
            return HealthCheckResult.Degraded(ex.Message);
        }
    }
}
```

Map health checks to `/health/live` and `/health/ready` separately — the AI health check belongs on `/health/ready` (not `/health/live`) since a degraded Claude API should pull the pod from the load balancer but not restart it.

## Reflection questions

1. Why does the circuit breaker use `FailureRatio = 0.5` rather than triggering on a single failure?
2. Your health check calls Claude with `MaxTokens = 1` — why not `MaxTokens = 0`?
3. `CancellationToken.None` in a controller action means the call continues even if the browser closes. When would this be intentional?
4. OpenTelemetry tags `llm.input_tokens` — why is this useful for a FinOps/cost monitoring dashboard?
5. Your circuit breaker is in OPEN state during an Anthropic outage. A health check fires. What status does your `/health/ready` endpoint return, and is the pod recycled?

---

**Your task:** Answer the reflection questions to complete this mission.
