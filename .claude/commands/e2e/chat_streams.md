# E2E Test: Chat Streams Multi-Pane Interface

Verify the Phase 44 Chat Streams page loads correctly with multi-context UI, message send, and intervention controls.

## Prerequisites
- Flask dashboard running on http://localhost:5000
- Database initialized (chat_contexts, chat_messages, chat_tasks tables present)

## Steps

1. Navigate to http://localhost:5000/chat-streams
2. Wait for the page to fully load
3. Verify the page title or heading contains "Chat Streams"
4. Screenshot the full chat streams page

5. Assert the CUI banner "CUI // SP-CTI" is visible at the top of the page
6. Assert the CUI banner "CUI // SP-CTI" is visible at the bottom of the page

7. Verify the context sidebar is visible (left panel with context list)
8. Verify the "New Chat" or "Create Context" button is present

9. Click the "New Chat" or "Create Context" button
10. Wait for context creation dialog or inline form to appear
11. Screenshot the context creation state

12. Verify a new context appears in the sidebar list
13. Verify the main chat area shows an empty message stream for the new context
14. Verify the message input area is visible and enabled

15. Type "Hello from E2E test" into the message input
16. Submit the message (click send button or press Enter)
17. Wait for the message to appear in the chat stream
18. Screenshot the chat with the sent message visible

19. Verify the sent message displays with role "user"
20. Verify the context status indicator shows processing or idle state

21. Navigate back to http://localhost:5000
22. Verify the main dashboard loads without errors
23. Navigate to http://localhost:5000/chat-streams again
24. Verify the previously created context is still listed in the sidebar

## Expected Results
- Chat Streams page loads without errors (no 500, no tracebacks)
- CUI // SP-CTI banners are visible on every page view
- Context sidebar renders with create button
- New context can be created and appears in list
- Message input works and displays sent messages
- Navigation to/from chat streams preserves context state

## CUI Verification
- Check that both header and footer CUI banners are present
- Verify banner text matches exactly: "CUI // SP-CTI"
