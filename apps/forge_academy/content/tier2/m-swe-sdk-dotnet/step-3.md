---
ontology_id: icdev:mission:m-swe-sdk-dotnet:step:3
step_class: icdev:Assessment
---

<!-- CUI // SP-CTI -->

# .NET Integration Review

The final step covers the four pillars of a production-grade .NET AI service: unit testing with mocks, resilience with Polly v8, observability with OpenTelemetry, and health checks.

## Mocking your own interface with NSubstitute / Moq

Your controllers and domain services depend on `IClaudeService` (step 1), not on `AnthropicClient`. Mocking is therefore trivial, and no SDK response objects need to be built by hand:

```csharp
// Using NSubstitute
[Fact]
public async Task Triage_Flags_Critical_When_Claude_Says_So()
{
    // Arrange
    var claude = Substitute.For<IClaudeService>();
    claude.CompleteAsync(Arg.Any<string>(), Arg.Any<string>(), Arg.Any<CancellationToken>())
          .Returns("{\"severity\":\"critical\"}");

    var sut = new TriageService(claude);

    // Act
    var result = await sut.TriageAsync("Analyse this CVE.", CancellationToken.None);

    // Assert
    Assert.Equal(Severity.Critical, result.Severity);
}
```

With Moq, use `Mock<IClaudeService>` and `.Setup(...)`. Cover the one thin `ClaudeService` adapter with an integration test against a stub HTTP endpoint, or a small number of live calls in a gated test run.

## Polly v8 ResiliencePipeline for retry + circuit breaker

The SDK already retries 429, 5xx and connection errors (2 retries by default), so add Polly for **policy you want on top**, mainly the circuit breaker. Failures surface as SDK exceptions, not `HttpResponseMessage`s, so handle exception types:

```csharp
// Program.cs  (Polly.Extensions)
builder.Services.AddResiliencePipeline("claude", pipeline =>
{
    pipeline
        .AddRetry(new RetryStrategyOptions
        {
            MaxRetryAttempts = 2,
            BackoffType      = DelayBackoffType.Exponential,
            Delay            = TimeSpan.FromSeconds(1),
            ShouldHandle     = new PredicateBuilder()
                .Handle<AnthropicRateLimitException>()
                .Handle<Anthropic5xxException>()
                .Handle<AnthropicIOException>(),
        })
        .AddCircuitBreaker(new CircuitBreakerStrategyOptions
        {
            FailureRatio      = 0.5,
            SamplingDuration  = TimeSpan.FromSeconds(10),
            MinimumThroughput = 5,
            BreakDuration     = TimeSpan.FromSeconds(30),
            ShouldHandle      = new PredicateBuilder()
                .Handle<Anthropic5xxException>()
                .Handle<AnthropicIOException>(),
        });
});
```

Never retry `AnthropicBadRequestException` (400) or other 4xx errors: they are caller bugs. Stacking Polly retries on the SDK's own retries multiplies attempts, so count both layers when you size `MaxRetryAttempts`. Inject `ResiliencePipelineProvider<string>`, get `"claude"`, and call `pipeline.ExecuteAsync(async ct => await _client.Messages.Create(...), ct)`.

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
    var response = await _client.Messages.Create(/* MessageCreateParams */);
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

Keep the readiness check cheap. An option that costs nothing: track the outcome of recent real calls (or the circuit-breaker state) and report from that. A check that sends a live request on every probe spends tokens and rate limit on every scrape.

```csharp
builder.Services.AddHealthChecks()
    .AddCheck<ClaudeHealthCheck>("anthropic-api", HealthStatus.Degraded, tags: ["ai", "external", "ready"]);

public sealed class ClaudeHealthCheck(ClaudeCallTracker tracker) : IHealthCheck
{
    public Task<HealthCheckResult> CheckHealthAsync(HealthCheckContext ctx, CancellationToken ct = default) =>
        Task.FromResult(tracker.RecentFailureRatio > 0.5
            ? HealthCheckResult.Degraded("Recent Claude calls failing")
            : HealthCheckResult.Healthy());
}
```

`ClaudeCallTracker` is your own small singleton that `ClaudeService` updates after each call. Map health checks to `/health/live` and `/health/ready` separately. The AI check belongs on `/health/ready` only: a degraded Claude API should pull the pod from the load balancer, not restart it.

## Reflection questions

Answer the two fields on this step:

1. **How will you mock the Claude client in unit tests? Describe the interface design.**
2. **Would you use Polly for retry/circuit-breaker on the Claude API calls? Explain your policy design.** Account for the retries the SDK already does.

To go further: why does the circuit breaker use `FailureRatio = 0.5` instead of opening on a single failure? And during an Anthropic outage with the breaker open, what does `/health/ready` return, and is the pod recycled?

---

**Your task:** Answer the reflection questions to complete this mission.
