# Chat Router

## Intent Classifier
**File:** `tools/chat_router/intent_classifier.py`
**Function:** `classify(message: str) -> dict`

Maps a user message to a canvas mode using keyword rules (fast path) + LLM fallback.

Returns: `{mode, canvas_type, confidence, reason}`

Modes: `intake | cam | ndc | sdc | eda | ddc | pdc | bdc | odc | idc`

**API endpoint:** `POST /api/chat/route-intent`  
Body: `{message: str, context_id?: str}`  
Response: same shape as `classify()`
