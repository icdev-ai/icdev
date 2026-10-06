---
ontology_id: icdev:mission:m-swe-sdk-java:step:1
step_class: icdev:Lesson
---

<!-- CUI // SP-CTI -->

# Spring Boot + Claude API — Architecture

Modern Java enterprise applications increasingly need to integrate large language models as first-class service dependencies. The official Anthropic Java SDK makes this straightforward while preserving the dependency-injection patterns your Spring Boot team already relies on.

> The code in this mission runs in your own Spring Boot project, not in the Academy sandbox. Class names below match the official `com.anthropic:anthropic-java` SDK (2.x). Check the SDK README when you upgrade.

## How the SDK fits into Spring Boot

The Anthropic Java SDK (`com.anthropic:anthropic-java`) is a plain Java library. You build an `AnthropicClient` (package `com.anthropic.client`) with the OkHttp-based builder `AnthropicOkHttpClient` (package `com.anthropic.client.okhttp`). Request and response models live in `com.anthropic.models.messages`, and typed exceptions live in `com.anthropic.errors`. The client is thread-safe and holds a connection pool, so expose **one** client as a Spring `@Bean` and inject it, the same pattern as an S3 client.

## Adding the dependency

**Gradle (`build.gradle`):**

```groovy
dependencies {
    implementation 'com.anthropic:anthropic-java:2.34.0'
}
```

**Maven (`pom.xml`):**

```xml
<dependency>
    <groupId>com.anthropic</groupId>
    <artifactId>anthropic-java</artifactId>
    <version>2.34.0</version>
</dependency>
```

Use the latest 2.x release available to you.

## SDK vs raw HTTP alternatives

| Approach | Pros | Cons |
|---|---|---|
| `RestTemplate` / `RestClient` | Familiar, synchronous | Manual JSON marshalling, no streaming built-in |
| `WebClient` | Reactive, streaming-capable | Requires SSE parsing by hand |
| **Anthropic SDK** | Typed models, streaming, retries built-in | Extra dependency |

The SDK is the right choice for production: it retries 429, 5xx and connection errors (2 retries by default), deserialises content blocks into typed objects, and exposes streaming as a Java `Stream` of events.

## Message format

Every call sends a list of messages with a `role` (`user` or `assistant`) and content. System instructions go in a separate `system` parameter, not in the messages list. The response `content()` is a list of `ContentBlock` unions. A block can be text, thinking (on by default on current Opus models), or a tool-use request, so never assume the first block is text.

## Streaming via Server-Sent Events

For long responses you stream. `client.messages().createStreaming(params)` returns a `StreamResponse<RawMessageStreamEvent>`; call `.stream()` on it inside try-with-resources. Text arrives in `content_block_delta` events. Forward each chunk to the browser with Spring MVC's `SseEmitter`. If you use WebFlux, adapt the SDK's async streaming (`client.async()`) to a `Flux` yourself. The SDK does not return a Reactor `Flux`.

```java
try (StreamResponse<RawMessageStreamEvent> response = client.messages().createStreaming(params)) {
    response.stream()
        .flatMap(event -> event.contentBlockDelta().stream())
        .flatMap(delta -> delta.delta().text().stream())
        .forEach(text -> emitter.send(text.text()));   // handle IOException in real code
}
```

## Tool use (function calling)

Claude can call tools you define. You add a `Tool` (name, description, and a JSON Schema `Tool.InputSchema`) to the request. When the model decides to call it, the response contains a tool-use block (`block.toolUse()` is present) and `stopReason` is `tool_use`. Your service runs the function, sends back a `tool_result`, and calls the API again until the model returns its final text. The SDK also has a beta tool runner that drives this loop for annotated tool classes.

## Minimal configuration + @Service

```java
@Configuration
public class AnthropicConfig {

    @Bean
    public AnthropicClient anthropicClient(@Value("${anthropic.api-key}") String apiKey) {
        return AnthropicOkHttpClient.builder()
                .apiKey(apiKey)
                .build();
    }
}

@Service
public class ClaudeService {

    private final AnthropicClient client;

    public ClaudeService(AnthropicClient client) {
        this.client = client;
    }

    public String complete(String userMessage) {
        Message response = client.messages().create(
            MessageCreateParams.builder()
                .model("claude-opus-5-5")
                .maxTokens(16000L)
                .addUserMessage(userMessage)
                .build());

        return response.content().stream()
                .flatMap(block -> block.text().stream())
                .map(TextBlock::text)
                .collect(Collectors.joining());
    }
}
```

`.model(String)` works for every model ID. The SDK's typed `Model.*` constants can lag new model launches. The API key comes from configuration via `@Value` and is never hardcoded. `AnthropicOkHttpClient.fromEnv()` reads `ANTHROPIC_API_KEY` directly if you prefer that.

## Key takeaways

- The SDK wraps the REST API with typed Java models, so there's no manual JSON parsing.
- One thread-safe `AnthropicClient` bean, injected wherever it's needed.
- Streaming is a `StreamResponse` you forward through `SseEmitter`. WebFlux needs an adapter.
- Tool use follows a request → execute → `tool_result` → re-request loop.

---

**Your task:** In the next step, you'll configure your own Claude integration endpoint.
