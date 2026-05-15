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

## Starter Code

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
