---
collection: ace.sessions
question: Show me sessions that failed with errors
example_iqe: "FROM ace.sessions WHERE result_subtype != 'success' ORDER BY created_at DESC LIMIT 25"
---
Find agent loop sessions that did not complete successfully, ordered by most recent.
