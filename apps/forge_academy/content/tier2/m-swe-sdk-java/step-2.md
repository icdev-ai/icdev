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
anthropic.model=claude-sonnet-4-5
anthropic.max-tokens=2048
```

For production, override `ANTHROPIC_API_KEY` via an environment variable injected by your secret store (AWS Secrets Manager, Azure Key Vault, Vault by HashiCorp). The `${...}` syntax delegates resolution to Spring's `Environment` abstraction automatically.

## Handling the ContentBlock response

The API returns a `Message` with a `content` list of `ContentBlock` objects. Each block has a `type` — either `text` or `tool_use`. Always check the type before calling `.text()`:

```java
// Safely extract all text content
String text = response.content().stream()
    .filter(b -> b.type() == ContentBlock.Type.TEXT)
    .map(b -> b.text().orElse(""))
    .collect(Collectors.joining("\n"));
```

## Full ClaudeService skeleton

```java
@Service
@RequiredArgsConstructor
public class ClaudeService {

    @Value("${anthropic.api-key}")
    private String apiKey;

    @Value("${anthropic.model:claude-sonnet-4-5}")
    private String model;

    @Value("${anthropic.max-tokens:2048}")
    private int maxTokens;

    private Anthropic client;

    @PostConstruct
    public void init() {
        this.client = Anthropic.builder()
                .apiKey(apiKey)
                .build();
    }

    public String chat(String systemPrompt, String userMessage) {
        var params = MessageCreateParams.builder()
                .model(model)
                .maxTokens(maxTokens)
                .system(systemPrompt)
                .addUserMessage(userMessage)
                .build();

        var response = client.messages().create(params);

        return response.content().stream()
                .filter(b -> b.type() == ContentBlock.Type.TEXT)
                .map(b -> b.text().orElse(""))
                .collect(Collectors.joining("\n"));
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

        var response = client.messages().create(params);
        return response.content().get(0).text().orElseThrow();
    }
}
```

## Tool use integration concept

The SDK does not use Java annotations to declare tools — you define them as `ToolDefinition` objects carrying a JSON Schema for the input. Here is the pattern:

1. Build a `ToolDefinition` with `name`, `description`, and an `inputSchema` (JSON object schema).
2. Add it to `MessageCreateParams.builder().tools(List.of(myTool))`.
3. Inspect the response: if any `ContentBlock` has `type == TOOL_USE`, extract the `toolUse()` block, run your Java method, and append a `tool_result` message.
4. Call the API again with the full updated message history.
5. Repeat until the response contains only `text` blocks.

This agentic loop is typically extracted into a helper like `ToolLoopExecutor` to keep `ClaudeService` clean.

## Configuration questions

1. Which Spring profile will hold your `ANTHROPIC_API_KEY` override in your deployment environment?
2. Should `ClaudeService` be `@Singleton` (default) or `@RequestScope`? What are the memory implications of each?
3. How would you expose `chat()` as a REST endpoint via a `@RestController`?
4. What HTTP status code should your controller return when the Claude API returns a 529 (overloaded) error?

---

**Your task:** Answer the configuration questions above, then move to the next step.
