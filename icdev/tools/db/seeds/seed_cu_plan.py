#!/usr/bin/env python3
from __future__ import annotations
# CUI // SP-CTI
"""Seed: Chat UX — Non-Developer Accessibility & Bug Fixes (18 tasks, 5 epics).

  cu-fix-*     — P0 bug fixes (intake.py + chat.js error handling)
  cu-css-*     — CSS additions (chat.css)
  cu-html-*    — HTML label/UX changes (chat.html, base.html)
  cu-js-*      — JS string + welcome handler (chat.js)
  cu-vv-*      — V&V gate (Playwright E2E + companion sync)

Dependency chain (single dep per task, fully serialized to avoid file conflicts):
  cu-fix-01 (no dep) ─────────────────────────────────────────────────────────┐
  cu-fix-02 (no dep) ──────────────────────────────────────────────────────┐  │
                                                                           │  │
  cu-fix-01 → cu-es-03 → cu-labels-01 → cu-labels-02 → cu-modal-01 →     │  │
    cu-es-01 → cu-sidebar-01 → cu-sidebar-02 → cu-input-01 →             │  │
    cu-input-02 → cu-stats-01 → cu-sidebar-03 →                          │  │
    cu-labels-03 → cu-labels-04 → cu-es-02 → cu-vv-01 → cu-vv-02        │  │
                                                                          └──┘
  cu-fix-02 has no dependents — runs in parallel; chat.js section
  it touches (createIntakeContext error handler) is not touched by
  any later task until cu-labels-03 (which starts after cu-sidebar-03,
  well after cu-fix-02 will have completed).

Run: python tools/db/seeds/seed_cu_plan.py
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from tools.db.storage import get_connection  # noqa: E402

NOW = datetime.now(timezone.utc).isoformat()

DB_PATH = ROOT / "data" / "icdev.db"


def _task(
    task_id: str,
    title: str,
    description: str,
    task_type: str = "build",
    priority: str = "high",
    depends_on: str | None = None,
) -> tuple:
    return (
        task_id, title, description, task_type, priority,
        "scheduled", NOW, "claude_cli", "cu_plan",
        depends_on, NOW, NOW,
    )


INSERT_SQL = """
INSERT INTO kanban_tasks
    (id, title, description, task_type, priority, status,
     scheduled_at, executor_type, dispatch_source,
     depends_on_task_id, created_at, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (id) DO NOTHING
"""

TASKS: list[tuple] = [

    # ──────────────────────────────────────────────────────────────────────────
    # EPIC FIX — P0 Bug Fixes
    # Both run immediately (no dependencies). cu-fix-01 starts the HTML chain;
    # cu-fix-02 is independent and completes before the JS chain starts.
    # ──────────────────────────────────────────────────────────────────────────
    _task(
        "cu-fix-01",
        "Fix impact_level empty-string DB constraint crash in intake API",
        (
            "File: tools/dashboard/api/intake.py\n"
            "\n"
            "The bug: when /chat loads without URL params, chat.js auto-calls "
            "createIntakeContext({}) with classification='' . Line 157 maps this via "
            "il_map.get(classification, '') which returns '' . PostgreSQL rejects '' "
            "because the intake_sessions.impact_level column has "
            "CHECK(impact_level IN ('IL2','IL4','IL5','IL6')) — empty string is "
            "neither NULL nor a valid value. The raw DB error is shown in the chat "
            "window and the textarea stays disabled, blocking all chat.\n"
            "\n"
            "Fix: change line 157 from:\n"
            "  impact_level = il_map.get(classification, '')\n"
            "to:\n"
            "  impact_level = il_map.get(classification, 'IL4') or 'IL4'\n"
            "\n"
            "The `or 'IL4'` guard also handles any future case where the dict "
            "lookup returns a falsy value.\n"
            "\n"
            "Only touch tools/dashboard/api/intake.py line 157. No other changes."
        ),
        priority="critical",
    ),

    _task(
        "cu-fix-02",
        "Humanize intake session error messages in chat.js",
        (
            "File: tools/dashboard/static/js/chat.js\n"
            "\n"
            "Currently at line ~1922 (inside createIntakeContext), when data.error "
            "is present the raw DB/server error string is appended directly to the "
            "message stream:\n"
            "  appendMessage({ role: 'system', content: 'Error creating intake "
            "session: ' + data.error });\n"
            "\n"
            "This exposes PostgreSQL constraint violation text, table names, and "
            "row values to the user.\n"
            "\n"
            "Replace that single appendMessage call with two actions:\n"
            "1. Log the technical error to the console: "
            "console.error('[ICDEV] Intake session error:', data.error);\n"
            "2. Show a friendly message to the user: "
            "appendMessage({ role: 'system', content: "
            "'Could not start your session. Please refresh the page or create a new "
            "conversation. If this keeps happening, contact your administrator.' });\n"
            "\n"
            "Apply the same pattern to the two .catch() handlers in createIntakeContext "
            "(connection error and context creation error) — log technical detail to "
            "console.error, show a plain English message to the user.\n"
            "\n"
            "Only touch tools/dashboard/static/js/chat.js. No other changes."
        ),
        priority="critical",
    ),

    # ──────────────────────────────────────────────────────────────────────────
    # EPIC CSS — Welcome banner + stats bar role filter
    # Runs after cu-fix-01 so the page is functional when testing.
    # ──────────────────────────────────────────────────────────────────────────
    _task(
        "cu-es-03",
        "Add CSS for welcome banner and suggested prompt chips to chat.css",
        (
            "File: tools/dashboard/static/css/chat.css\n"
            "\n"
            "Append the following new CSS rules at the end of the file (do not "
            "modify any existing rules):\n"
            "\n"
            "/* Welcome banner */\n"
            ".chat-welcome { padding: 2rem 1.5rem; text-align: center; "
            "max-width: 600px; margin: 2rem auto 0; }\n"
            ".chat-welcome__title { font-size: 1.25rem; font-weight: 600; "
            "color: var(--text-primary); margin-bottom: 0.5rem; }\n"
            ".chat-welcome__subtitle { font-size: 0.9rem; color: var(--text-muted); "
            "margin-bottom: 1.5rem; line-height: 1.5; }\n"
            ".chat-welcome__prompts { display: flex; flex-wrap: wrap; gap: 0.5rem; "
            "justify-content: center; }\n"
            ".chat-welcome__prompt-chip { background: var(--bg-card); "
            "border: 1px solid var(--border-color); border-radius: 20px; "
            "padding: 0.4rem 0.85rem; font-size: 0.82rem; color: var(--text-secondary); "
            "cursor: pointer; transition: background 0.15s, border-color 0.15s; "
            "user-select: none; }\n"
            ".chat-welcome__prompt-chip:hover { background: var(--accent-blue-dim, "
            "rgba(79,142,247,0.12)); border-color: var(--accent-blue); "
            "color: var(--accent-blue); }\n"
            "\n"
            "/* Stats bar role filter — hide for non-developer roles */\n"
            ".view-role--pm #chat-top-stats,\n"
            ".view-role--contracting #chat-top-stats,\n"
            ".view-role--analyst #chat-top-stats,\n"
            ".view-role--bizdev #chat-top-stats,\n"
            ".view-role--cor #chat-top-stats { display: none !important; }\n"
            "\n"
            "Only append to the end of the file. No existing rules should be touched."
        ),
        depends_on="cu-fix-01",
    ),

    # ──────────────────────────────────────────────────────────────────────────
    # EPIC HTML — All label/UX changes to chat.html and base.html (sequential)
    # ──────────────────────────────────────────────────────────────────────────
    _task(
        "cu-labels-01",
        "Rename page header and context sidebar labels in chat.html",
        (
            "File: tools/dashboard/templates/chat.html\n"
            "\n"
            "Make the following plain-text substitutions:\n"
            "\n"
            "1. H1 title: change 'Multi-Stream Chat' → 'AI Assistant'\n"
            "\n"
            "2. Page subtitle paragraph (class text-muted): replace the entire "
            "paragraph text with: 'Ask anything about your project — requirements, "
            "architecture, security design, or compliance. I route your question to "
            "the right specialist automatically.'\n"
            "\n"
            "3. Remove the second subtitle div (the one starting 'AI detects your "
            "intent automatically — ask about migration…'). It is now covered by "
            "the rewritten main subtitle above.\n"
            "\n"
            "4. Sidebar header label: change '<strong>Contexts</strong>' → "
            "'<strong>Conversations</strong>'\n"
            "\n"
            "5. Stat label 'Total Contexts' → 'Total Conversations'\n"
            "\n"
            "Only touch tools/dashboard/templates/chat.html. No other changes."
        ),
        depends_on="cu-es-03",
    ),

    _task(
        "cu-labels-02",
        "Rename RICOAS/Gov/Intel header buttons to plain English in chat.html",
        (
            "File: tools/dashboard/templates/chat.html\n"
            "\n"
            "The chat header contains four toggle buttons with id attrs "
            "btn-ricoas-toggle, btn-gov-toggle, btn-intel-toggle, btn-focus-mode.\n"
            "Their current labels are: 'RICOAS', 'Gov', 'Intel', 'Focus'.\n"
            "\n"
            "Make the following changes:\n"
            "\n"
            "1. btn-ricoas-toggle: change text content 'RICOAS' → 'Requirements'\n"
            "   Update title attr: 'Toggle Requirements sidebar'\n"
            "\n"
            "2. btn-gov-toggle: change text content 'Gov' → 'Governance'\n"
            "   Update title attr: 'Toggle AI Governance sidebar'\n"
            "\n"
            "3. btn-intel-toggle: change text content 'Intel' → 'Knowledge'\n"
            "   Update title attr: 'Toggle Knowledge & Intelligence sidebar'\n"
            "\n"
            "4. btn-focus-mode: keep text 'Focus' but update title attr to: "
            "'Focus mode — hide side panels for a cleaner chat view'\n"
            "\n"
            "Also update the matching tab buttons in the right sidebar "
            "(id=tab-ricoas, tab-gov, tab-intel):\n"
            "  tab-ricoas text: 'RICOAS' → 'Requirements'\n"
            "  tab-gov text: 'Gov' → 'Governance'\n"
            "  tab-intel text: 'Intel' → 'Knowledge'\n"
            "\n"
            "Only touch tools/dashboard/templates/chat.html. No other changes."
        ),
        depends_on="cu-labels-01",
    ),

    _task(
        "cu-modal-01",
        "Wrap advanced New Chat modal fields in collapsible section in chat.html",
        (
            "File: tools/dashboard/templates/chat.html\n"
            "\n"
            "The 'New Chat Context' modal currently shows all fields upfront. "
            "Non-developers are confused by Agent Model, Canvas Mode, System Prompt, "
            "and the RICOAS checkbox.\n"
            "\n"
            "Changes:\n"
            "\n"
            "1. After the Compliance Context field (id=ctx-gov-rows area), add a "
            "collapsed section toggle:\n"
            "<div class='chat-modal__advanced-toggle' id='ctx-advanced-toggle' "
            "style='margin-top:0.75rem;cursor:pointer;font-size:0.82rem;"
            "color:var(--text-muted);user-select:none;'>Advanced settings ▸</div>\n"
            "<div id='ctx-advanced-body' style='display:none;'></div>\n"
            "\n"
            "2. Move these existing field divs INSIDE the ctx-advanced-body div:\n"
            "   - chat-modal__field containing label 'Agent Model' (id=new-ctx-model)\n"
            "   - chat-modal__field containing label 'Canvas Mode' (id=new-ctx-canvas)\n"
            "   - chat-modal__field id=ctx-preset-row (Persona)\n"
            "   - chat-modal__field id=intake-checkbox-row (RICOAS checkbox)\n"
            "   - chat-modal__field containing label 'System Prompt' (id=new-ctx-prompt)\n"
            "\n"
            "3. Add an inline <script> block at the bottom of the modal (before "
            "chat-modal__actions) to toggle the advanced section:\n"
            "document.getElementById('ctx-advanced-toggle').addEventListener('click', "
            "function() { "
            "  var body = document.getElementById('ctx-advanced-body'); "
            "  var open = body.style.display !== 'none'; "
            "  body.style.display = open ? 'none' : ''; "
            "  this.textContent = open ? 'Advanced settings \\u25b8' : "
            "'Advanced settings \\u25be'; "
            "});\n"
            "\n"
            "The modal now shows only Title and Compliance Context by default. "
            "Advanced options expand on click.\n"
            "\n"
            "Only touch tools/dashboard/templates/chat.html. No other changes."
        ),
        depends_on="cu-labels-02",
    ),

    _task(
        "cu-es-01",
        "Add welcome banner and suggested prompt chips HTML to chat.html",
        (
            "File: tools/dashboard/templates/chat.html\n"
            "\n"
            "When a context is first selected, the message-stream div is empty and "
            "shows nothing. Add a static welcome state HTML block inside "
            "#message-stream so new users see a helpful starting point.\n"
            "\n"
            "Add directly inside the <div id='message-stream' class='chat-messages'> "
            "element (as its initial child content):\n"
            "\n"
            "<div class='chat-welcome' id='chat-welcome-banner'>\n"
            "  <div class='chat-welcome__title'>Hello! How can I help you today?</div>\n"
            "  <div class='chat-welcome__subtitle'>"
            "I can help you capture requirements, design systems, review security, "
            "and build government or commercial applications. Just describe what "
            "you want to accomplish.</div>\n"
            "  <div class='chat-welcome__prompts'>\n"
            "    <span class='chat-welcome__prompt-chip' "
            "data-prompt='I want to build a new application'>I want to build an app</span>\n"
            "    <span class='chat-welcome__prompt-chip' "
            "data-prompt='Help me capture requirements for my project'>"
            "Capture requirements</span>\n"
            "    <span class='chat-welcome__prompt-chip' "
            "data-prompt='Review my security architecture'>Review security</span>\n"
            "    <span class='chat-welcome__prompt-chip' "
            "data-prompt='Explain the compliance requirements for a DoD system'>"
            "DoD compliance guidance</span>\n"
            "  </div>\n"
            "</div>\n"
            "\n"
            "The CSS for .chat-welcome and .chat-welcome__prompt-chip was added in "
            "cu-es-03. The JS click handler that hides the banner and sends the "
            "prompt text is added in cu-es-02.\n"
            "\n"
            "Only touch tools/dashboard/templates/chat.html. No other changes."
        ),
        depends_on="cu-modal-01",
    ),

    _task(
        "cu-sidebar-01",
        "Hide readiness gauge until score > 0 in chat.html RICOAS sidebar",
        (
            "File: tools/dashboard/templates/chat.html\n"
            "\n"
            "The readiness gauge (id=readiness-gauge) and dimensions "
            "(id=readiness-dimensions) always show even when no requirements have "
            "been captured (score is 0%). This looks like a failing metric to "
            "non-developers.\n"
            "\n"
            "Changes:\n"
            "\n"
            "1. Add style='display:none;' to the readiness-gauge div:\n"
            "   <div class='readiness-gauge-container' id='readiness-gauge' "
            "style='display:none;text-align:center;'>\n"
            "\n"
            "2. Add style='display:none;' to the readiness-dimensions div:\n"
            "   <div class='readiness-dimensions' id='readiness-dimensions' "
            "style='display:none;'>\n"
            "\n"
            "3. Replace the existing h4 'Readiness Score' heading with:\n"
            "   <h4 class='chat-sidebar-heading'>Readiness Score</h4>\n"
            "   <div id='readiness-placeholder' style='font-size:0.82rem;"
            "color:var(--text-muted);padding:0.5rem 0 0.75rem;'>"
            "Start chatting to track your requirements readiness.</div>\n"
            "\n"
            "The JS (in existing chat.js refreshReadiness) already updates the "
            "readiness-arc and readiness-pct elements. In cu-es-02 we will also "
            "show/hide the gauge and hide the placeholder when score > 0.\n"
            "\n"
            "Only touch tools/dashboard/templates/chat.html. No other changes."
        ),
        depends_on="cu-es-01",
    ),

    _task(
        "cu-sidebar-02",
        "Humanize RICOAS right-sidebar section labels in chat.html",
        (
            "File: tools/dashboard/templates/chat.html\n"
            "\n"
            "The RICOAS sidebar (id=ricoas-sidebar) uses specialist jargon that "
            "confuses non-developers. Make these label changes:\n"
            "\n"
            "1. Section heading 'Elicitation Techniques' → 'Ways to Explore Requirements'\n"
            "   (the h4 inside id=techniques-section)\n"
            "\n"
            "2. Section heading 'BDD Scenarios (Draft)' → 'Test Scenarios (Draft)'\n"
            "   (the h4 inside id=bdd-preview-section)\n"
            "\n"
            "3. Section heading 'Courses of Action' → 'Options to Consider'\n"
            "   (the h4 inside id=coa-section)\n"
            "\n"
            "4. Section heading in the right panel tabs:\n"
            "   Tab button tab-ricoas title attr: update to 'Requirements Intake — "
            "readiness score, test scenarios, and recommended next steps'\n"
            "   Tab button tab-gov title attr: update to 'AI Governance — "
            "transparency and accountability status'\n"
            "   Tab button tab-intel title attr: update to 'Knowledge — "
            "documents, knowledge graph, and AI learning status'\n"
            "\n"
            "5. In the gov-sidebar: change the placeholder text "
            "'Loading governance status...' → 'Loading AI Governance status...'\n"
            "\n"
            "6. In the intel-sidebar heading span: change subtitle "
            "'RAG + Bayesian + Quality + Genesis' → "
            "'Documents · Learning · Research'\n"
            "\n"
            "Only touch tools/dashboard/templates/chat.html. No other changes."
        ),
        depends_on="cu-sidebar-01",
    ),

    _task(
        "cu-input-01",
        "Replace Choose File text button with icon attach button in chat.html",
        (
            "File: tools/dashboard/templates/chat.html\n"
            "\n"
            "The current upload button uses text label 'Choose File' which is "
            "browser-default file dialog language. Modern chat UIs use an icon.\n"
            "\n"
            "Find the button with id='chat-upload-btn' in the input area. "
            "Currently it contains an SVG icon and the text 'Upload'.\n"
            "\n"
            "Changes:\n"
            "1. Remove the word 'Upload' (the text node after the SVG) from the "
            "button so it becomes icon-only.\n"
            "\n"
            "2. Update the title attr to: "
            "'Attach a file (PDF, Word, image, CSV, JSON, YAML, Markdown)'\n"
            "\n"
            "3. Update aria-label to: 'Attach file'\n"
            "\n"
            "4. Add a CSS style to the button to make it square and not add "
            "extra padding for text: add style='padding:0 8px;' to the button element.\n"
            "\n"
            "The existing SVG upload arrow icon is sufficient visual indicator. "
            "The tooltip (title attr) provides the detailed description on hover.\n"
            "\n"
            "Only touch tools/dashboard/templates/chat.html. No other changes."
        ),
        depends_on="cu-sidebar-02",
    ),

    _task(
        "cu-input-02",
        "Rename intervention bar label in chat.html",
        (
            "File: tools/dashboard/templates/chat.html\n"
            "\n"
            "The intervention bar (id=intervention-bar) shows during AI processing "
            "with the label 'Agent is processing...' and a text input labeled "
            "'Type to intervene...' with a button 'Intervene'.\n"
            "\n"
            "Non-developers do not know what 'intervene' means in this context "
            "and may try to type in it instead of the main input.\n"
            "\n"
            "Changes:\n"
            "1. Change label text from 'Agent is processing...' → "
            "'AI is thinking…'\n"
            "\n"
            "2. Change the input placeholder from 'Type to intervene...' → "
            "'Send a new direction or clarification…'\n"
            "\n"
            "3. Change the button text from 'Intervene' → 'Redirect'\n"
            "   Update btn-intervene title attr to: 'Send a new direction to the AI'\n"
            "\n"
            "Only touch tools/dashboard/templates/chat.html. No other changes."
        ),
        depends_on="cu-input-01",
    ),

    _task(
        "cu-stats-01",
        "Add role-filter CSS class to stats bar in chat.html and chat.css",
        (
            "Files: tools/dashboard/templates/chat.html\n"
            "\n"
            "The stats bar (id=chat-top-stats, class=stat-grid) shows operational "
            "metrics ('Processing', 'Queued Messages', 'Total Conversations') that "
            "are meaningless to non-developer roles.\n"
            "\n"
            "The CSS rules to hide it for non-dev roles were added in cu-es-03:\n"
            "  .view-role--pm #chat-top-stats { display: none !important; }\n"
            "  (and similar for contracting, analyst, bizdev, cor)\n"
            "\n"
            "Now wire the view-role class to the page body. In base.html the "
            "'View as' dropdown (id=role-selector or similar) controls role. "
            "Find the existing JS that handles the 'View as' dropdown role change "
            "in base.html — it sets data attributes or class on the body.\n"
            "\n"
            "In chat.html, add a small inline script in the {% block scripts %} "
            "section that:\n"
            "1. Reads the current role from the 'View as' dropdown on page load.\n"
            "2. Maps it to a CSS class and adds it to document.body:\n"
            "   'Program Manager' → view-role--pm\n"
            "   'Contracting Officer' → view-role--contracting\n"
            "   'Analyst' → view-role--analyst\n"
            "   'Business Development' → view-role--bizdev\n"
            "   'Contracting Officer Representative' → view-role--cor\n"
            "3. Listens for the dropdown change event and updates the class.\n"
            "\n"
            "The dropdown is: <select id (grep base.html for 'Select dashboard view "
            "role' or similar)>. Check base.html for the actual element id.\n"
            "\n"
            "Only touch tools/dashboard/templates/chat.html. No other changes."
        ),
        depends_on="cu-input-02",
    ),

    _task(
        "cu-sidebar-03",
        "Suppress IQE global mini-bar on /chat route in base.html",
        (
            "File: tools/dashboard/templates/base.html\n"
            "\n"
            "The IQE global mini-bar (id=iqe-minibar) is a second chat-like "
            "interface that appears on every page. On /chat it duplicates the main "
            "chat input, confusing non-developers about where to type.\n"
            "\n"
            "The mini-bar JS in base.html sets its initial transform and visibility. "
            "Find the inline <script> block that initialises the IQE mini-bar "
            "(around line 736 where 'var bar = document.getElementById' appears).\n"
            "\n"
            "At the very start of that script block, add:\n"
            "  if (window.location.pathname === '/chat' || "
            "window.location.pathname.startsWith('/chat/')) {\n"
            "    var _mb = document.getElementById('iqe-minibar');\n"
            "    if (_mb) _mb.style.display = 'none';\n"
            "  }\n"
            "\n"
            "This hides the mini-bar entirely on /chat and /chat/<session> routes "
            "without affecting any other pages. The Ctrl+Shift+Q keyboard shortcut "
            "will also be inert on /chat since the bar is display:none.\n"
            "\n"
            "Only touch tools/dashboard/templates/base.html. No other changes."
        ),
        depends_on="cu-stats-01",
    ),

    # ──────────────────────────────────────────────────────────────────────────
    # EPIC JS — String updates + welcome state handler (sequential after HTML)
    # Starts after cu-sidebar-03 (all HTML/base work done) to avoid
    # concurrent edits to chat.js while cu-fix-02 runs independently.
    # By the time cu-sidebar-03 completes, cu-fix-02 is guaranteed done.
    # ──────────────────────────────────────────────────────────────────────────
    _task(
        "cu-labels-03",
        "Replace 'Select or create a context' empty-state strings in chat.js",
        (
            "File: tools/dashboard/static/js/chat.js\n"
            "\n"
            "The string 'Select or create a context' appears in multiple places "
            "in chat.js as the empty/default chat title. It is developer vocabulary.\n"
            "\n"
            "Search for all occurrences of the string 'Select or create a context' "
            "in chat.js (there should be 2-4 occurrences in setText calls, "
            "deleteContext, closeContext, etc.).\n"
            "\n"
            "Replace every occurrence with: 'Start a new conversation'\n"
            "\n"
            "Use replace_all=true for this Edit since the string is identical "
            "each time.\n"
            "\n"
            "Also update the matching static text in the chat header: "
            "in chat.html (line ~61) the span id='chat-title' contains the "
            "text 'Select or create a context' as its initial content. "
            "Change it to 'Start a new conversation'.\n"
            "\n"
            "Wait — chat.html is done in earlier tasks. Focus only on chat.js "
            "in this task. The chat.html initial text was not changed in earlier "
            "tasks (they focused on other elements), so also update "
            "tools/dashboard/templates/chat.html line ~61:\n"
            "  <span id='chat-title' class='chat-header__title'>"
            "Select or create a context</span>\n"
            "→\n"
            "  <span id='chat-title' class='chat-header__title'>"
            "Start a new conversation</span>\n"
            "\n"
            "Touch: tools/dashboard/static/js/chat.js (replace_all) "
            "AND tools/dashboard/templates/chat.html (single occurrence)."
        ),
        depends_on="cu-sidebar-03",
    ),

    _task(
        "cu-labels-04",
        "Rename context message count display from '0 msgs' to plain English in chat.js",
        (
            "File: tools/dashboard/static/js/chat.js\n"
            "\n"
            "In the renderContextList function, around line 1718, each context item "
            "shows a message count as:\n"
            "  var meta = c.message_count + ' msg' + (c.message_count !== 1 ? 's' : '')\n"
            "\n"
            "This produces '0 msgs', '1 msg', '5 msgs' which is terse developer "
            "shorthand. A non-developer might not understand '0 msgs'.\n"
            "\n"
            "Replace that line with:\n"
            "  var meta = c.message_count === 0 ? 'No messages yet' :\n"
            "    (c.message_count + ' message' + (c.message_count !== 1 ? 's' : ''))\n"
            "\n"
            "This produces 'No messages yet', '1 message', '5 messages' — "
            "plain English that any user understands.\n"
            "\n"
            "Only touch tools/dashboard/static/js/chat.js. No other changes."
        ),
        depends_on="cu-labels-03",
    ),

    _task(
        "cu-es-02",
        "Add welcome banner show/hide logic and prompt chip click handler to chat.js",
        (
            "File: tools/dashboard/static/js/chat.js\n"
            "\n"
            "The welcome banner HTML was added to #message-stream in cu-es-01. "
            "This task wires the JS behaviour:\n"
            "\n"
            "1. Hide the welcome banner when messages exist:\n"
            "   In renderMessages() function, after the stream is populated with "
            "messages, hide the banner:\n"
            "     var wb = document.getElementById('chat-welcome-banner');\n"
            "     if (wb) wb.style.display = (messages && messages.length > 0) "
            "? 'none' : '';\n"
            "\n"
            "2. Show the welcome banner when a context is cleared (closeContext, "
            "deleteContext, setText to empty state):\n"
            "   In closeContext() and deleteContext() after clearing the stream, add:\n"
            "     var stream = document.getElementById('message-stream');\n"
            "     var wb = document.getElementById('chat-welcome-banner');\n"
            "     if (wb) { stream.appendChild(wb); wb.style.display = ''; }\n"
            "   (This re-attaches the banner to the stream if it was detached, "
            "or just shows it if it's still a child.)\n"
            "\n"
            "3. Prompt chip click handler:\n"
            "   In the DOMContentLoaded listener (or at the bottom of the IIFE), add:\n"
            "     document.addEventListener('click', function(e) {\n"
            "       if (!e.target.classList.contains('chat-welcome__prompt-chip')) "
            "return;\n"
            "       var prompt = e.target.getAttribute('data-prompt');\n"
            "       if (!prompt || !_activeContextId) return;\n"
            "       var inp = document.getElementById('message-input');\n"
            "       if (inp && !inp.disabled) {\n"
            "         inp.value = prompt;\n"
            "         inp.dispatchEvent(new Event('input'));\n"
            "         sendMessage();\n"
            "       }\n"
            "     });\n"
            "\n"
            "4. Hide readiness placeholder, show gauge when score > 0:\n"
            "   In the refreshReadiness() success handler, after updating the arc "
            "and percentage text, add:\n"
            "     var score = data.readiness_score || 0;\n"
            "     var gauge = document.getElementById('readiness-gauge');\n"
            "     var dims = document.getElementById('readiness-dimensions');\n"
            "     var placeholder = document.getElementById('readiness-placeholder');\n"
            "     if (score > 0) {\n"
            "       if (gauge) gauge.style.display = '';\n"
            "       if (dims) dims.style.display = '';\n"
            "       if (placeholder) placeholder.style.display = 'none';\n"
            "     }\n"
            "\n"
            "Only touch tools/dashboard/static/js/chat.js."
        ),
        depends_on="cu-labels-04",
    ),

    # ──────────────────────────────────────────────────────────────────────────
    # EPIC V&V — Playwright E2E + companion sync
    # ──────────────────────────────────────────────────────────────────────────
    _task(
        "cu-vv-01",
        "Playwright E2E — verify /chat loads clean, input enabled, welcome state shows",
        (
            "Run Playwright browser automation to verify all cu-* changes are "
            "working correctly end-to-end.\n"
            "\n"
            "Test sequence:\n"
            "\n"
            "1. Navigate to http://localhost:5050/chat\n"
            "2. Take screenshot: playwright/screenshots/chat-ux-after.png\n"
            "3. Assert: #message-stream does NOT contain the text "
            "'Error creating intake session' or 'violates check constraint'\n"
            "4. Assert: #chat-welcome-banner is visible (display != none)\n"
            "5. Assert: #message-input is NOT disabled\n"
            "6. Assert: #btn-send is NOT disabled\n"
            "7. Assert: page H1 text equals 'AI Assistant'\n"
            "8. Assert: sidebar strong label contains 'Conversations'\n"
            "9. Click the first .chat-welcome__prompt-chip\n"
            "10. Assert: #chat-welcome-banner is now hidden\n"
            "11. Assert: #message-input.value is not empty (prompt was filled)\n"
            "12. Take screenshot: playwright/screenshots/chat-ux-prompt-filled.png\n"
            "13. Navigate to http://localhost:5050/chat, click '+ New' button\n"
            "14. Assert: the New Conversation modal is visible\n"
            "15. Assert: 'ctx-advanced-body' has display:none (advanced is collapsed)\n"
            "16. Click 'Advanced settings' toggle\n"
            "17. Assert: 'ctx-advanced-body' is now visible\n"
            "18. Take screenshot: playwright/screenshots/chat-ux-modal.png\n"
            "\n"
            "If any assertion fails, output a clear failure message with the "
            "expected vs actual value and mark the task failed.\n"
            "\n"
            "Use mcp__playwright__browser_* tools (not selenium) for this E2E test. "
            "Screenshots must be saved to playwright/screenshots/ as specified."
        ),
        task_type="build",
        priority="high",
        depends_on="cu-es-02",
    ),

    _task(
        "cu-vv-02",
        "Companion sync + coherence check after chat UX changes",
        (
            "Run the two mandatory post-implementation checks:\n"
            "\n"
            "1. Companion sync — pushes all changed files to the AI platform configs:\n"
            "   python tools/dx/companion.py --sync --write --json\n"
            "   Assert: exit code 0. If it fails, read the error and fix before "
            "proceeding.\n"
            "\n"
            "2. Coherence check — validates cross-file consistency:\n"
            "   python tools/workflow/coherence_checker.py --all --fix --gate\n"
            "   Assert: exit code 0. The --fix flag auto-corrects minor issues. "
            "If the gate fails after --fix, report which check failed and why.\n"
            "\n"
            "These are non-optional. Do not mark this task complete unless both "
            "commands exit 0."
        ),
        task_type="build",
        priority="medium",
        depends_on="cu-vv-01",
    ),
]


def main() -> None:
    import os
    db_path = os.environ.get("ICDEV_DB_PATH", str(DB_PATH))
    conn = get_connection(db_path=db_path)
    inserted = 0
    for task in TASKS:
        try:
            conn.execute(INSERT_SQL, task)
            inserted += 1
        except Exception as exc:
            print(f"  SKIP {task[0]}: {exc}")
    conn.commit()
    conn.close()
    print(f"Seeded {inserted}/{len(TASKS)} tasks (cu-* prefix, chat-ux project)")
    print("Dependency chain:")
    for t in TASKS:
        dep = t[9] or "(none)"
        print(f"  {t[0]:20s}  dep={dep}")


if __name__ == "__main__":
    main()
