---
ontology_id: icdev:mission:m-swe-sdk-java:step:3
step_class: icdev:Assessment
---

<!-- CUI // SP-CTI -->

# Production Considerations for Claude in Spring Boot

A working integration is not the same as a production-ready one. This step covers the operational patterns that separate a prototype from a service you can actually run at scale.

## Retry with Resilience4j

The SDK already retries 429, 5xx (including 529 overloaded) and connection errors, 2 times by default (`.maxRetries(n)` on the client builder). Add Resilience4j for policy **on top**, chiefly the circuit breaker, and count both layers when you size retries.

```java
@Bean
public Retry claudeRetry(RetryRegistry registry) {
    return registry.retry("claude", RetryConfig.custom()
        .maxAttempts(2)
        .waitDuration(Duration.ofSeconds(1))
        .retryExceptions(RateLimitException.class, InternalServerException.class, AnthropicIoException.class)
        .ignoreExceptions(BadRequestException.class) // 400s: don't retry
        .build());
}

// In ClaudeService:
public String chat(String system, String user) {
    return Retry.decorateSupplier(claudeRetry, () -> doApiCall(system, user)).get();
}
```

The exception classes live in `com.anthropic.errors`. `AnthropicServiceException` is the base class for **every** HTTP error, including 4xx, so don't list it under `retryExceptions`. The rule: retry on 429, 5xx and network errors; never retry other 4xx errors, which are caller bugs.

## Circuit breaker pattern

A circuit breaker prevents retry storms when the API is down for an extended period. Resilience4j's `CircuitBreaker` wraps the same call:

```java
CircuitBreaker cb = CircuitBreaker.ofDefaults("claude");
Supplier<String> decorated = CircuitBreaker.decorateSupplier(cb, () -> doApiCall(...));
String result = Try.ofSupplier(decorated)
    .recover(CallNotPermittedException.class, e -> fallbackResponse())
    .get();
```

When the circuit is **open**, calls fail fast and return your `fallbackResponse()` immediately — protecting downstream threads.

## Async with @Async and CompletableFuture

For endpoints that don't need to block the HTTP thread:

```java
@Async("aiExecutor")
public CompletableFuture<String> chatAsync(String system, String user) {
    return CompletableFuture.completedFuture(doApiCall(system, user));
}
```

Configure a dedicated `ThreadPoolTaskExecutor` named `aiExecutor` with bounded queue depth so AI calls never starve your main request threads.

## Test strategy: WireMock

Avoid real API calls in unit and integration tests. **WireMock** is the standard choice for HTTP-level contract tests. Point the client at it with `AnthropicOkHttpClient.builder().baseUrl("http://localhost:8089")` in your test configuration:

```java
@WireMockTest(httpPort = 8089)
class ClaudeServiceTest {

    @Test
    void returns_text_content() {
        stubFor(post(urlEqualTo("/v1/messages"))
            .willReturn(aResponse()
                .withHeader("Content-Type", "application/json")
                .withBodyFile("claude_response.json")));

        var result = service.chat("You are helpful.", "Hello");
        assertThat(result).isNotBlank();
    }
}
```

Store fixture JSON in `src/test/resources/__files/`. **MockServer** is an alternative with a Java DSL, but WireMock has better Spring Boot integration via `@WireMockTest`.

## Logging without leaking PII

Log metadata, not content:

```java
log.info("Claude request model={} maxTokens={} inputTokens={}",
         model, maxTokens, response.usage().inputTokens());
// NEVER: log.debug("User prompt: {}", userMessage);
```

If you need prompt debugging, use a dedicated audit log behind a feature flag gated to non-production environments. In IL4+ environments, prompt content is CUI and must not appear in application logs.

## Timeout configuration

```java
AnthropicOkHttpClient.builder()
    .apiKey(apiKey)
    .timeout(Duration.ofSeconds(120))   // per request; the SDK default is 10 minutes
    .maxRetries(2)
    .build();
```

Set an explicit timeout that fits your endpoint's SLA, and stream long generations instead of raising the timeout. The SDK default (10 minutes) is far longer than most HTTP callers will wait.

## Reflection questions

Answer the two fields on this step:

1. **How will you handle LLM API failures without breaking your Spring Boot service?** Cover retries (SDK + yours), the circuit breaker, the fallback response, and the status code you return.
2. **How will you test the AI feature: unit test, integration test, or contract test?**

To go further: the SDK client is thread-safe, so what would go wrong if you built a new client per request instead of sharing one? And why must prompt content stay out of standard application logs in a CUI environment?

---

**Your task:** Answer the reflection questions to complete this mission.
