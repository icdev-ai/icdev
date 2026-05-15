---
ontology_id: icdev:mission:m09-langchain:step:1
step_class: icdev:Lesson
---

# LangChain Essentials — LCEL Chains

LangChain Expression Language (LCEL) is LangChain's composition interface — a declarative way to build LLM pipelines using the `|` operator. An LCEL chain is a sequence of transforms, each taking the output of the last as its input.

## The pipe operator

```python
chain = prompt | llm | output_parser
result = chain.invoke({"topic": "STIG remediation"})
```

This creates a pipeline:
1. `prompt.invoke({"topic": ...})` → `ChatPromptValue`
2. `llm.invoke(ChatPromptValue)` → `AIMessage`
3. `output_parser.invoke(AIMessage)` → `str`

Each component is a **Runnable** — any object with `.invoke()`, `.batch()`, and `.stream()` methods.

## Core components

| Component | What it does | Example |
|-----------|--------------|---------|
| `ChatPromptTemplate` | Formats messages from variables | System + user template |
| `LLM / ChatModel` | Generates completions | Ollama, Bedrock, OpenAI |
| `StrOutputParser` | Extracts string from AIMessage | Most common |
| `JsonOutputParser` | Parses JSON from response | Structured extraction |
| `RunnablePassthrough` | Passes input unchanged | Joining branches |
| `RunnableParallel` | Runs multiple chains in parallel | Multi-retriever RAG |

## LCEL for RAG

```python
rag_chain = (
    {"context": retriever, "question": RunnablePassthrough()}
    | prompt
    | llm
    | StrOutputParser()
)
answer = rag_chain.invoke("What are the CAT I STIG requirements?")
```

## Why LCEL over plain Python?

1. **Streaming** — any LCEL chain automatically supports `.stream()` with zero changes
2. **Async** — `.ainvoke()` / `.astream()` without boilerplate
3. **Tracing** — LangSmith hooks into every component automatically
4. **Batching** — `.batch([input1, input2])` for parallel execution

## In ICDEV

ICDEV's RAG pipeline uses LCEL under the hood. The `tools/rag/retriever.py` is a LangChain Retriever, and the chain wires it to the LLM router using LCEL's `RunnableParallel` for multi-source retrieval.

## Your task

Implement a minimal LCEL-compatible `Runnable` base class and build a 3-component chain using the `|` operator (Python's `__or__` / `__ror__`).
