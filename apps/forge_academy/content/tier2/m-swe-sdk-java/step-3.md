---
ontology_id: icdev:mission:m-swe-sdk-java:step:3
step_class: icdev:Assessment
---

<!-- CUI // SP-CTI -->

# Production Considerations for Claude in Spring Boot

A working integration is not the same as a production-ready one. This step covers the operational patterns that separate a prototype from a service you can actually run at scale.

## Retry with Resilience4j

Transient API errors (rate limits, network blips, 529 overloaded) should trigger automatic retry with exponential backoff — not propagate as 500s to your callers.

```java
@Bean
public Retry claudeRetry(RetryRegistry registry) {
    return registry.retry("claude", RetryConfig.custom()
        .maxAttempts(3)
        .waitDuration(Duration.ofSeconds(1))
        .retryExceptions(AnthropicServiceException.class, SocketTimeoutException.class)
        .ignoreExceptions(AnthropicInvalidRequestException.class) // 400s: don't retry
        .build());
}

// In ClaudeService:
public String chat(String system, String user) {
    return Retry.decorateSupplier(claudeRetry, () -> doApiCall(system, user)).get();
}
```

The key rule: retry on `5xx` and network errors; never retry on `4xx` (those are caller bugs).

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

Avoid real API calls in unit and integration tests. **WireMock** is the standard choice for HTTP-level contract tests:

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

```properties
# application.properties
anthropic.connect-timeout-ms=5000
anthropic.read-timeout-ms=30000
```

Pass these to the SDK's `HttpClient` builder. Always set both — an absent read timeout means a stalled response holds a thread indefinitely.

## Reflection questions

1. Why would you set `ignoreExceptions(AnthropicInvalidRequestException.class)` in the retry config?
2. What is the risk of a shared `Anthropic` client instance across concurrent requests? (Hint: check if the SDK client is thread-safe.)
3. A circuit breaker is in HALF_OPEN state. What does that mean, and how does the next call affect the state?
4. Your integration test passes with WireMock but fails in staging. What are three things you would check first?
5. Why must prompt content be excluded from standard application logs in a CUI environment?

---

**Your task:** Answer the reflection questions to complete this mission.
