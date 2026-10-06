---
ontology_id: icdev:mission:m11-multimodal:step:2
step_class: icdev:Lesson
---

# CUI // SP-CTI
# Build a Document Classifier with Multimodal AI

## Your Mission

Build a `DocumentClassifier` class that takes an image (path or bytes) and returns:
- `category` — one of your predefined labels
- `confidence` — the model's stated confidence (0.0–1.0)
- `reason` — a short explanation
- `accepted` — `True` if confidence meets your threshold

## What You Write in the Editor

The editor's starter runs in a sandbox with **no network and no LLM**, so a stub,
`simulate_vision_call(messages)`, stands in for `client.messages.create(...)`. It takes
the same `messages` list a real vision call takes and returns the same shape:
`{"model": ..., "content": [{"type": "text", "text": "<reply>"}], "usage": {...}}`.
Keep the stub, the `ClassificationResult` dataclass and the class name
`DocumentClassifier(categories, confidence_threshold=0.75)` exactly as given, and fill in
three methods:

| Method | Must return |
|---|---|
| `_encode_image(image_input)` | `(base64_text, media_type)`. A `str` is a file path: read its bytes; `.png` → `"image/png"`, `.jpg`/`.jpeg` → `"image/jpeg"`. `bytes` are used as-is with `"image/png"`. Encode with `base64.standard_b64encode(data).decode("utf-8")`. |
| `_build_messages(b64, media_type)` | ONE user message, `[{"role": "user", "content": [image_block, text_block]}]` — the image block first (`{"type": "image", "source": {"type": "base64", "media_type": ..., "data": b64}}`), then a text block whose prompt lists **every** category and asks for JSON with `category`, `confidence`, `reason`. |
| `classify(image_input)` | A `ClassificationResult`. Call `simulate_vision_call(...)` exactly once, read `response["content"][0]["text"]`, strip ```` ``` ```` / ```` ```json ```` fences, `json.loads` it, take `category`, `confidence` (float) and `reason` from the reply, and set `accepted = confidence >= threshold`. If the reply is not JSON, do not raise — return `ClassificationResult("unknown", 0.0, <reason>, False)`. |

The grader swaps the stub for one that replies with values it picks at random, so the
category, confidence and reason must come from the reply — not from your code.

## The Same Classifier Against the Real API (reference)

This is the production shape — the sandbox version above has the same methods, with `simulate_vision_call` in place of `self.client.messages.create`.

```python
import anthropic
import base64
import json
import pathlib
from dataclasses import dataclass


@dataclass
class ClassificationResult:
    category: str
    confidence: float
    reason: str
    accepted: bool


class DocumentClassifier:
    """Classify document images using Claude's vision capability."""

    def __init__(
        self,
        categories: list[str],
        confidence_threshold: float = 0.75,
        model: str = "claude-sonnet-4-6",
    ):
        self.categories = categories
        self.threshold = confidence_threshold
        self.client = anthropic.Anthropic()
        self.model = model

    def _encode_image(self, image_input: str | bytes) -> tuple[str, str]:
        """Return (base64_data, media_type)."""
        if isinstance(image_input, str):
            data = pathlib.Path(image_input).read_bytes()
            ext = pathlib.Path(image_input).suffix.lower().lstrip(".")
            media_type = f"image/{ext if ext != 'jpg' else 'jpeg'}"
        else:
            data = image_input
            media_type = "image/png"  # default for bytes input
        return base64.standard_b64encode(data).decode("utf-8"), media_type

    def classify(self, image_input: str | bytes) -> ClassificationResult:
        b64, media_type = self._encode_image(image_input)
        categories_str = "\n".join(f"- {c}" for c in self.categories)
        prompt = f"""You are a document classification expert.
Classify the document image into ONE of these categories:
{categories_str}

Respond ONLY with valid JSON in this exact format:
{{
  "category": "<one of the categories above>",
  "confidence": <0.0 to 1.0>,
  "reason": "<one sentence explaining your classification>"
}}"""
        response = self.client.messages.create(
            model=self.model,
            max_tokens=256,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}},
                    {"type": "text", "text": prompt},
                ],
            }],
        )
        raw = response.content[0].text.strip()
        # Parse JSON — strip markdown fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        result = json.loads(raw)
        confidence = float(result.get("confidence", 0.0))
        return ClassificationResult(
            category=result.get("category", "unknown"),
            confidence=confidence,
            reason=result.get("reason", ""),
            accepted=confidence >= self.threshold,
        )
```

## Test It

```python
classifier = DocumentClassifier(
    categories=["Government form", "Technical diagram", "Scanned report", "Other"],
    confidence_threshold=0.75,
)

result = classifier.classify("path/to/your/document.png")
print(f"Category : {result.category}")
print(f"Confidence: {result.confidence:.0%}")
print(f"Reason   : {result.reason}")
print(f"Accepted : {result.accepted}")
```

## Acceptance Criteria

Your classifier passes when:
- Returns a valid `ClassificationResult` for any image input
- `accepted=False` when confidence is below threshold (don't just always return True)
- Handles JSON parse errors gracefully (try/except → return `category="unknown"`, `confidence=0.0`)
- Works with both a file path and raw bytes as `image_input`

## Challenge (Bonus)

Batch-classify a folder of documents and write the results to a CSV:
```python
import csv, pathlib

results = []
for img_path in pathlib.Path("documents/").glob("*.png"):
    r = classifier.classify(str(img_path))
    results.append({"file": img_path.name, "category": r.category,
                     "confidence": r.confidence, "accepted": r.accepted})

with open("classification_results.csv", "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=["file","category","confidence","accepted"])
    writer.writeheader()
    writer.writerows(results)
```
