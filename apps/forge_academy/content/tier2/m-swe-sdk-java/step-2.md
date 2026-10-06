---
ontology_id: icdev:mission:m-swe-sdk-java:step:2
step_class: icdev:Lesson
---

<!-- CUI // SP-CTI -->

# Add Claude to Your Spring Boot Service

With the architecture understood, it's time to wire the SDK into a working Spring Boot service. This step covers the full implementation: reading credentials from configuration, making the API call, handling the response, and sketching a tool-use pattern.

## Reading the API key securely

Never hardcode the API key. Spring Boot's `@Value` annotation reads from `application.properties`, environment variables, or a secrets manager in a uniform way:

```properties
# application.properties
anthropic.api-key=${ANTHROPIC_API_KEY}
anthropic.model=claude-opus-5-5
anthropic.max-tokens=16000
```

For production, override `ANTHROPIC_API_KEY` via an environment variable injected by your secret store (AWS Secrets Manager, Azure Key Vault, Vault by HashiCorp). The `${...}` syntax delegates resolution to Spring's `Environment` abstraction automatically.

## Handling the ContentBlock response

The API returns a `Message` whose `content()` is a list of `ContentBlock` unions. Each accessor returns an `Optional`: `block.text()` is present only for text blocks, `block.toolUse()` only for tool-use blocks, and `block.thinking()` only for thinking blocks. Filter rather than assume:

```java
// Safely extract all text content
String text = response.content().stream()
    .flatMap(block -> block.text().stream())
    .map(TextBlock::text)
    .collect(Collectors.joining("
"));
```

## Full ClaudeService skeleton

```java
@Service
public class ClaudeService {

    private final AnthropicClient client;
    private final String model;
    private final long maxTokens;

    public ClaudeService(AnthropicClient client,
                         @Value("${anthropic.model:claude-opus-5-5}") String model,
                         @Value("${anthropic.max-tokens:16000}") long maxTokens) {
        this.client = client;
        this.model = model;
        this.maxTokens = maxTokens;
    }

    public String chat(String systemPrompt, String userMessage) {
        var params = MessageCreateParams.builder()
                .model(model)
                .maxTokens(maxTokens)
                .system(systemPrompt)
                .addUserMessage(userMessage)
                .build();

        return textOf(client.messages().create(params));
    }

    public String chatWithHistory(List<MessageParam> history, String newUserMessage) {
        var allMessages = new ArrayList<>(history);
        allMessages.add(MessageParam.builder()
                .role(MessageParam.Role.USER)
                .content(newUserMessage)
                .build());

        var params = MessageCreateParams.builder()
                .model(model)
                .maxTokens(maxTokens)
                .messages(allMessages)
                .build();

        return textOf(client.messages().create(params));
    }

    private static String textOf(Message response) {
        return response.content().stream()
                .flatMap(block -> block.text().stream())
                .map(TextBlock::text)
                .collect(Collectors.joining("
"));
    }
}
```

## Tool use integration concept

Tools are defined as data: a `Tool` with a name, a description and a JSON Schema input (`Tool.InputSchema`). Here is the manual loop:

1. Build a `Tool` and add it with `MessageCreateParams.builder().addTool(myTool)`.
2. Inspect the response. If `stopReason` is `tool_use`, take each block whose `toolUse()` is present, run your Java method with its input, and build a `tool_result` for its id.
3. Append the assistant turn and **all** tool results (in one user message), then call the API again.
4. Repeat until `stopReason` is `end_turn`.

Extract this loop into a helper such as `ToolLoopExecutor` to keep `ClaudeService` clean. Alternatively, use the SDK's beta tool runner, which drives the loop for annotated tool classes.

## Configuration questions

Fill in the fields on this step:

1. **What endpoint will call the LLM?** For example `/api/summarize`, exposed by a `@RestController` that delegates to `ClaudeService`.
2. **Input type.** Free-form user text, structured JSON, document or file content, or a database record.
3. **Streaming response required?** Stream tokens through `SseEmitter`, or return a single JSON response.
4. **Does the LLM need to call tools (functions)?** If yes, plan the loop above.

Think about this as you answer: what HTTP status should your controller return when the Claude API is overloaded (HTTP 529) even after the SDK's retries?

---

**Your task:** Answer the configuration questions above, then move to the next step.
