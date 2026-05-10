# CUI // SP-CTI
# Wiring the Classifier into a RAG Pipeline

## The Problem

A RAG pipeline retrieves documents and feeds them to an LLM. But if your corpus contains mixed document types — forms, diagrams, reports — the retriever doesn't know which documents are relevant to a *visual* query. The classifier bridges this gap.

## The Architecture

```
[User Query]
     │
     ▼
[Classifier] ──── "This query needs diagram documents"
     │
     ▼
[Filtered Retriever] ──── searches only diagram-type documents
     │
     ▼
[LLM + Retrieved Context] ──── answers using relevant docs
     │
     ▼
[Response]
```

## Implementation Pattern

```python
class MultimodalRAGPipeline:
    def __init__(self, classifier: DocumentClassifier, retriever, llm_client):
        self.classifier = classifier
        self.retriever = retriever  # your existing RAG retriever
        self.client = llm_client

    def pre_filter(self, query: str, documents: list[dict]) -> list[dict]:
        """Filter documents by type inferred from query keywords."""
        query_lower = query.lower()
        type_hints = {
            "diagram": "Technical diagram",
            "chart": "Technical diagram",
            "form": "Government form",
            "report": "Scanned report",
        }
        expected_category = None
        for keyword, category in type_hints.items():
            if keyword in query_lower:
                expected_category = category
                break

        if not expected_category:
            return documents  # no filter — return all

        filtered = []
        for doc in documents:
            if doc.get("pre_classified_category") == expected_category:
                filtered.append(doc)
            elif "image_path" in doc:
                result = self.classifier.classify(doc["image_path"])
                doc["pre_classified_category"] = result.category
                if result.accepted and result.category == expected_category:
                    filtered.append(doc)

        return filtered if filtered else documents  # fall back if nothing matched

    def query(self, user_query: str) -> str:
        all_docs = self.retriever.search(user_query, top_k=20)
        filtered_docs = self.pre_filter(user_query, all_docs)
        context = "\n\n".join(doc["content"] for doc in filtered_docs[:5])
        response = self.client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            messages=[{
                "role": "user",
                "content": f"Context:\n{context}\n\nQuestion: {user_query}",
            }],
        )
        return response.content[0].text
```

## Latency Mitigation

Adding a vision classification step adds ~1–2 seconds per image. Mitigations:

| Strategy | Implementation | Trade-off |
|----------|----------------|-----------|
| **Pre-classify at ingest** | Run classifier when documents are added to corpus | Zero latency at query time; stale if docs change |
| **Cache classifications** | Store result in DB by file hash; skip if hash matches | Eliminates repeat calls for same document |
| **Classify only uncached** | Check DB first, call API only for new documents | Best balance; ~0ms for known docs |
| **Async batch classify** | Background worker pre-classifies new uploads | No query-time cost; small delay for brand-new docs |

## Reflection Questions

Answer these in the text fields on the right →

1. **Pre-filter strategy** — How will you use the classifier output to filter documents before RAG retrieval? Will you pre-classify at ingest time or classify on the fly?

2. **Fallback handling** — What happens when the classifier returns `accepted=False` (confidence below threshold)? Do you include the document anyway, route it for human review, or drop it?

3. **Latency trade-off** — Quantify the cost: if you have 100 documents and classify each on the fly at 1.5s each, that's 150 seconds. What caching strategy reduces this to <200ms?

## Mission Complete

When you've answered all three reflection questions, you've completed Mission M11. You now know how to:
- Accept image inputs in a Claude API call
- Build a confident document classifier with threshold logic
- Wire classification output as a pre-filter step in a RAG pipeline
- Reason about latency trade-offs in multimodal architectures

**+350 XP awarded. Tier 1 Multimodal track unlocked.**
