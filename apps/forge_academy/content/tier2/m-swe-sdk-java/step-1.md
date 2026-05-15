---
ontology_id: icdev:mission:m-swe-sdk-java:step:1
step_class: icdev:Lesson
---

<!-- CUI // SP-CTI -->

# Spring Boot + Claude API — Architecture

Modern Java enterprise applications increasingly need to integrate large language models as first-class service dependencies. The Anthropic Java SDK makes this straightforward while preserving the dependency-injection patterns your Spring Boot team already relies on.

## How the SDK fits into Spring Boot

The Anthropic Java SDK (`com.anthropic:anthropic-java`) is a standard Java library. It ships a blocking client (`Anthropic`) and a non-blocking async variant (`AnthropicAsync`). In a Spring Boot service you typically wrap one of these in a `@Service` bean and let the DI container own the lifecycle — exactly the same pattern as a JPA repository or an S3 client.

## Adding the dependency

**Gradle (`build.gradle`):**

```groovy
dependencies {
    implementation 'com.anthropic:anthropic-java:0.9.0'
    // For reactive streaming support
    implementation 'org.springframework.boot:spring-boot-starter-webflux'
}
```

**Maven (`pom.xml`):** replace with `<groupId>com.anthropic</groupId>` / `<artifactId>anthropic-java</artifactId>`.

## SDK vs raw HTTP alternatives

| Approach | Pros | Cons |
|---|---|---|
| `RestTemplate` | Familiar, synchronous | Manual JSON marshalling, no streaming built-in |
| `WebClient` | Reactive, streaming-capable | Requires SSE parsing by hand |
| **Anthropic SDK** | Typed models, streaming, retries built-in | Extra dependency |

The SDK is the right choice for any production feature: it handles retries, deserialises `ContentBlock` types, and exposes a clean `Stream<RawMessageStreamEvent>` for SSE.

## Message format

Every API call sends a list of `MessageParam` objects with a `role` (`user` or `assistant`) and a `content` string. The model alternates roles in a conversation. System instructions live in a separate `system` parameter, not in the messages list.

## Streaming via Server-Sent Events

For long responses you stream. The SDK returns a `Stream<RawMessageStreamEvent>` (blocking) or a `Flux<RawMessageStreamEvent>` (async). Each event is a discriminated union: `content_block_delta` carries the text chunk; `message_stop` signals completion. You pipe these chunks directly to the HTTP response using Spring's `SseEmitter` or WebFlux `Flux<ServerSentEvent<String>>`.

## Tool use (function calling)

Claude can invoke tools you define. You register a `ToolDefinition` (name + JSON Schema for parameters) when building the request. When the model decides to call a tool, it returns a `tool_use` `ContentBlock` instead of text. Your service executes the function, appends a `tool_result` message, and calls the API again. This loop continues until the model returns a final text response.

## Minimal @Service bean

```java
@Service
public class ClaudeService {

    private final Anthropic client;

    public ClaudeService(@Value("${anthropic.api-key}") String apiKey) {
        this.client = Anthropic.builder()
                .apiKey(apiKey)
                .build();
    }

    public String complete(String userMessage) {
        var response = client.messages().create(
            MessageCreateParams.builder()
                .model(Model.CLAUDE_SONNET_4_5)
                .maxTokens(1024)
                .addUserMessage(userMessage)
                .build()
        );
        return response.content().get(0).text().orElseThrow();
    }
}
```

The API key is read from `application.properties` via `@Value` — never hardcoded.

## Key takeaways

- The SDK wraps the REST API with typed Java models — no manual JSON parsing.
- Spring Boot treats the client as any other singleton bean.
- SSE streaming is natively supported; use `WebFlux` for reactive pipelines.
- Tool use follows a request/respond/execute/re-request loop.

---

**Your task:** In the next step, you'll configure your own Claude integration endpoint.
