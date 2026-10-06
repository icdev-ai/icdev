# Mission M-T1-11 — Multimodal AI
# Step 2: Image-in-Prompt — build a document classifier
#
# The sandbox has no network and no LLM, so `simulate_vision_call` below stands in
# for `client.messages.create(...)`. It takes the SAME `messages` list a real vision
# call takes and returns the SAME shape a real response has:
#
#     {"model": ..., "content": [{"type": "text", "text": "<model reply>"}], "usage": {...}}
#
# It only "sees" a document if you actually send the image as a base64 image block.
# Keep this function as it is — the grader calls your classifier, not this stub.

import base64
import json
import pathlib  # noqa: F401 -- for your _encode_image (file-path input)
from dataclasses import dataclass


def simulate_vision_call(messages: list) -> dict:
    """Stand-in for a vision model. Reads the image block you send it."""
    image_bytes = b""
    for message in messages:
        for block in message.get("content", []):
            if isinstance(block, dict) and block.get("type") == "image":
                image_bytes = base64.b64decode(block["source"]["data"])
    if not image_bytes:
        text = "I don't see an image in this request."
    elif b"STANDARD FORM" in image_bytes:
        text = json.dumps({"category": "Government form", "confidence": 0.92,
                           "reason": "An SF-series header is printed at the top."})
    elif b"SCHEMATIC" in image_bytes:
        text = "```json\n" + json.dumps({"category": "Technical diagram", "confidence": 0.81,
                                          "reason": "Labelled boxes joined by arrows."}) + "\n```"
    else:
        text = json.dumps({"category": "Other", "confidence": 0.40,
                           "reason": "No recognisable layout."})
    return {"model": "simulated-vision", "content": [{"type": "text", "text": text}],
            "usage": {"input_tokens": 1200, "output_tokens": 40}}


@dataclass
class ClassificationResult:
    category: str
    confidence: float
    reason: str
    accepted: bool


class DocumentClassifier:
    """Classify document images with a vision model."""

    def __init__(self, categories: list, confidence_threshold: float = 0.75):
        self.categories = categories
        self.threshold = confidence_threshold

    def _encode_image(self, image_input) -> tuple:
        """Return (base64_text, media_type).

        TODO:
          * str input  -> it is a file path: read its bytes; media type comes from the
                          extension — ".png" -> "image/png", ".jpg"/".jpeg" -> "image/jpeg"
          * bytes input -> use the bytes as-is; media type "image/png"
          * encode with base64.standard_b64encode(data).decode("utf-8")
        """
        raise NotImplementedError("TODO: implement _encode_image")

    def _build_messages(self, b64: str, media_type: str) -> list:
        """Return the messages list for the vision call.

        TODO: ONE user message whose "content" is a list of two blocks, in this order:
          1. {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}}
          2. {"type": "text", "text": <your prompt listing self.categories and asking for
                                       JSON with "category", "confidence" and "reason">}
        """
        raise NotImplementedError("TODO: implement _build_messages")

    def classify(self, image_input) -> ClassificationResult:
        """TODO:
          1. b64, media_type = self._encode_image(image_input)
          2. response = simulate_vision_call(self._build_messages(b64, media_type))
          3. raw = response["content"][0]["text"].strip()
          4. If raw is wrapped in ``` fences (optionally ```json), strip them.
          5. json.loads(raw) -> read "category", "confidence" (float) and "reason".
          6. If the reply is not valid JSON, do NOT raise: return
             ClassificationResult("unknown", 0.0, <a short reason>, False).
          7. accepted = confidence >= self.threshold
        """
        raise NotImplementedError("TODO: implement classify")


# Try it once your methods are filled in:
# classifier = DocumentClassifier(
#     categories=["Government form", "Technical diagram", "Scanned report", "Other"],
#     confidence_threshold=0.75,
# )
# result = classifier.classify(b"\x89PNG...STANDARD FORM 86...")
# print(result)
