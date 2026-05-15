---
ontology_id: icdev:mission:m11-multimodal:step:1
step_class: icdev:Lesson
---

# CUI // SP-CTI
# Multimodal AI — Vision, Documents, Images

## What Is Multimodal AI?

A multimodal model can process more than text. It accepts **images, PDFs, scanned documents, diagrams, and screenshots** as inputs alongside text prompts. This extends what an LLM can do from "answer questions about text" to "reason about the content in a document you scanned."

The Claude API supports multimodal inputs via the `image` content block. You can send a base64-encoded image or a file URL alongside your text, and the model analyzes both together.

```python
import anthropic, base64, pathlib

client = anthropic.Anthropic()
image_bytes = pathlib.Path("document.png").read_bytes()
b64 = base64.standard_b64encode(image_bytes).decode("utf-8")

response = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=1024,
    messages=[{
        "role": "user",
        "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
            {"type": "text",  "text": "What type of government form is this? What is the form number?"},
        ],
    }],
)
print(response.content[0].text)
```

## Three Multimodal Use Cases

| Use Case | What You Send | What You Get Back |
|----------|--------------|------------------|
| **Document Classification** | Scanned form / PDF page | Document type, form number, agency |
| **Diagram Understanding** | Architecture diagram / flowchart | Component list, relationships, risks |
| **Table Extraction** | Image of a table | Structured data as JSON |

## Why It Matters for Gov/DoD

- **STIG and compliance forms** often arrive as scanned PDFs — a vision model can read and classify them automatically
- **Intelligence reports** mix text and imagery — a multimodal RAG pipeline can ingest both
- **Legacy system screenshots** are often the only "API" available — vision models bridge that gap

## Supported Formats

| Format | MIME Type | Notes |
|--------|-----------|-------|
| PNG | `image/png` | Preferred for screenshots |
| JPEG | `image/jpeg` | For photos |
| GIF | `image/gif` | First frame only |
| WebP | `image/webp` | Modern format |
| PDF | Process page-by-page | Convert each page to image first |

## What's Next

In Step 2 you'll build a **document classifier** that:
1. Accepts a document image
2. Asks Claude to classify it into one of your categories
3. Returns the category + confidence reasoning
4. Applies a confidence threshold before accepting the result
