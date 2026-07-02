# Chat Manager

## ChatManager
**File:** `tools/chat/chat_manager.py`  
**Class:** `ChatManager(user_id, tenant_id, classification)`

Service layer for `chat_contexts` / `chat_messages` — chat context lifecycle management.

Key methods:
- `create_context(title, classification, agent_model, config, ...)` → `ctx_id`
- `get_context(context_id)` → `dict | None`
- `list_contexts(status, limit)` → `list[dict]`
- `update_status(context_id, status)` — valid statuses: active/paused/completed/error/archived
- `add_message(context_id, role, content, content_type, metadata)` → `msg_id`
- `get_messages(context_id, limit, offset)` → `list[dict]`
- `get_last_message(context_id)` → `dict | None`
- `set_coworker_instance(context_id, instance_id)` — stores ACE instance ID in `context_config` JSON
- `get_coworker_instance(context_id)` → `str | None`
- `update_config(context_id, updates)` — merges keys into `context_config` JSON

Module-level shortcuts: `create_context()`, `get_context()`, `set_coworker_instance()`, `get_coworker_instance()`.

Uses `get_connection()` (global RLS) because `chat_contexts` carries `tenant_id` and `classification`.

---

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
