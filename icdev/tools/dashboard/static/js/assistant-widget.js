// CUI // SP-CTI
// Codebase Assistant Widget (Phase 69 — D-CA-3 to D-CA-8)
// Floating chat widget for codebase Q&A on every dashboard page.
// Persists across page navigation via sessionStorage.
// Auto-scopes to current page's module via ROUTE_MODULE_MAP.

(function () {
    'use strict';

    var API_BASE = '/api/assistant';
    var STORAGE_KEY = 'icdev_widget';
    var _contextId = null;
    var _messages = [];
    var _scope = null;
    var _expanded = false;
    var _loading = false;

    // ===================================================================
    // State Persistence (sessionStorage — D-CA-3)
    // ===================================================================

    function saveState() {
        try {
            sessionStorage.setItem(STORAGE_KEY, JSON.stringify({
                context_id: _contextId,
                messages: _messages.slice(-50), // Keep last 50 messages
                scope: _scope,
                expanded: _expanded
            }));
        } catch (e) { /* quota exceeded */ }
    }

    function loadState() {
        try {
            var raw = sessionStorage.getItem(STORAGE_KEY);
            if (!raw) return;
            var state = JSON.parse(raw);
            _contextId = state.context_id || null;
            _messages = state.messages || [];
            _scope = state.scope || null;
            _expanded = state.expanded || false;
        } catch (e) { /* corrupted */ }
    }

    // ===================================================================
    // Auto-Scoping (D-CA-4)
    // ===================================================================

    function detectScope() {
        var map = window.ROUTE_MODULE_MAP || {};
        var path = window.location.pathname;
        // Try longest prefix match
        var bestMatch = null;
        var bestLen = 0;
        for (var prefix in map) {
            if (path === prefix || path.indexOf(prefix + '/') === 0 || path.indexOf(prefix) === 0) {
                if (prefix.length > bestLen) {
                    bestMatch = map[prefix];
                    bestLen = prefix.length;
                }
            }
        }
        return bestMatch;
    }

    function populateScopeDropdown() {
        var select = document.getElementById('assistant-scope');
        if (!select) return;
        var map = window.ROUTE_MODULE_MAP || {};
        var modules = {};
        for (var prefix in map) {
            var mod = map[prefix];
            if (!modules[mod]) {
                modules[mod] = prefix;
            }
        }
        // Clear existing options (keep "All")
        while (select.options.length > 1) select.remove(1);
        var sorted = Object.keys(modules).sort();
        for (var i = 0; i < sorted.length; i++) {
            var opt = document.createElement('option');
            opt.value = sorted[i];
            // Display friendly name: "tools/pulse/" → "Pulse"
            var label = sorted[i].replace('tools/', '').replace(/\/$/, '').replace(/_/g, ' ');
            label = label.charAt(0).toUpperCase() + label.slice(1);
            opt.textContent = label;
            select.appendChild(opt);
        }
        // Set current scope
        if (_scope) select.value = _scope;
    }

    // ===================================================================
    // UI Rendering
    // ===================================================================

    function renderMessages() {
        var container = document.getElementById('assistant-messages');
        if (!container) return;
        var empty = document.getElementById('assistant-empty');

        if (_messages.length === 0) {
            if (empty) empty.style.display = 'block';
            return;
        }
        if (empty) empty.style.display = 'none';

        var html = '';
        for (var i = 0; i < _messages.length; i++) {
            var msg = _messages[i];
            var isUser = msg.role === 'user';
            html += '<div class="assistant-msg ' + (isUser ? 'assistant-msg--user' : '') + '">';
            html += '<div class="assistant-msg__bubble">';
            if (isUser) {
                html += escHtml(msg.content);
            } else {
                // nav-sec-08: render LLM markdown through the shared fail-closed
                // sanitizer (marked + DOMPurify) so answer content can't inject
                // active markup. Falls back to escaped text if unavailable.
                var rendered;
                if (typeof window.safeMarkdown === 'function') {
                    rendered = window.safeMarkdown(msg.content);
                } else if (typeof ICDEV !== 'undefined' && ICDEV.renderMarkdown) {
                    rendered = ICDEV.renderMarkdown(msg.content);
                } else {
                    rendered = escHtml(msg.content);
                }
                html += '<div class="msg-markdown">' + rendered + '</div>';
                // Citations
                if (msg.citations && msg.citations.length) {
                    html += '<div class="assistant-msg__citations">';
                    for (var j = 0; j < msg.citations.length; j++) {
                        var c = msg.citations[j];
                        var display = c.file + (c.line ? ':' + c.line : '');
                        html += '<a class="citation-badge" title="' + escHtml(c.snippet || '') + '">'
                            + '&#128196; ' + escHtml(display) + '</a>';
                    }
                    html += '</div>';
                }
                if (msg.source) {
                    html += '<div class="assistant-msg__source">' + (msg.source === 'cache' ? 'Cached answer' : 'LLM response') + '</div>';
                }
            }
            html += '</div></div>';
        }
        container.innerHTML = html;
        container.scrollTop = container.scrollHeight;
    }

    function escHtml(s) {
        var d = document.createElement('div');
        d.textContent = s || '';
        return d.innerHTML;
    }

    function setLoading(loading) {
        _loading = loading;
        var typing = document.getElementById('assistant-typing');
        var input = document.getElementById('assistant-input');
        var send = document.getElementById('assistant-send');
        if (typing) typing.style.display = loading ? 'flex' : 'none';
        if (input) input.disabled = loading;
        if (send) send.disabled = loading;
    }

    function setStatus(text) {
        var el = document.getElementById('assistant-status-text');
        if (el) el.textContent = text;
    }

    // ===================================================================
    // Widget Toggle
    // ===================================================================

    function expand() {
        var fab = document.getElementById('assistant-fab');
        var widget = document.getElementById('assistant-widget');
        if (fab) fab.style.display = 'none';
        if (widget) widget.style.display = 'flex';
        _expanded = true;
        saveState();
        renderMessages();
        loadSuggestions();
        var input = document.getElementById('assistant-input');
        if (input) setTimeout(function() { input.focus(); }, 100);
    }

    function collapse() {
        var fab = document.getElementById('assistant-fab');
        var widget = document.getElementById('assistant-widget');
        if (fab) fab.style.display = 'flex';
        if (widget) widget.style.display = 'none';
        _expanded = false;
        saveState();
    }

    function goFullScreen() {
        if (_contextId) {
            saveState();
            window.location.href = '/chat?widget_context=' + encodeURIComponent(_contextId);
        } else {
            window.location.href = '/chat';
        }
    }

    function clearChat() {
        _messages = [];
        _contextId = null;
        saveState();
        var container = document.getElementById('assistant-messages');
        if (container) container.innerHTML = '';
        var empty = document.getElementById('assistant-empty');
        if (empty) empty.style.display = 'block';
        loadSuggestions();
        setStatus('Ready');
    }

    // ===================================================================
    // Orange panel (Strategos) coordination — shift blue widget left
    // ===================================================================

    var _ORANGE_SHIFT = 444; // 420px orange panel + 24px gap

    function applyOrangePanelOffset() {
        var panel = document.getElementById('sg-chat-panel');
        var isOpen = panel && panel.classList.contains('sg-panel-open');
        var fab = document.getElementById('assistant-fab');
        var widget = document.getElementById('assistant-widget');
        var right = isOpen ? _ORANGE_SHIFT + 'px' : '24px';
        if (fab) fab.style.right = right;
        if (widget) widget.style.right = right;
    }

    function watchOrangePanel() {
        var panel = document.getElementById('sg-chat-panel');
        if (!panel) return;
        var observer = new MutationObserver(applyOrangePanelOffset);
        observer.observe(panel, { attributes: true, attributeFilter: ['class'] });
        applyOrangePanelOffset(); // apply on init
    }

    // ===================================================================
    // API Communication
    // ===================================================================

    function sendQuery(question) {
        if (!question.trim() || _loading) return;

        // Add user message
        _messages.push({ role: 'user', content: question });
        renderMessages();
        setLoading(true);
        setStatus('Searching codebase...');

        var body = {
            question: question,
            context_id: _contextId,
            page_path: window.location.pathname
        };
        if (_scope) body.scope = _scope;

        fetch(API_BASE + '/query', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        })
        .then(function (r) {
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function (data) {
            _contextId = data.context_id || _contextId;
            _messages.push({
                role: 'assistant',
                content: data.answer || 'No answer available.',
                citations: data.citations || [],
                source: data.source || 'llm'
            });
            renderMessages();
            setStatus('Ready');
            saveState();
        })
        .catch(function (err) {
            _messages.push({
                role: 'assistant',
                content: 'Error: ' + err.message + '. Make sure the dashboard is running and the codebase is indexed.'
            });
            renderMessages();
            setStatus('Error — retry or check dashboard');
        })
        .finally(function () {
            setLoading(false);
        });
    }

    function loadSuggestions() {
        var container = document.getElementById('assistant-suggestions');
        if (!container) return;
        if (_messages.length > 0) return; // Only show in empty state

        fetch(API_BASE + '/suggestions?page_path=' + encodeURIComponent(window.location.pathname))
        .then(function (r) { return r.ok ? r.json() : { suggestions: [] }; })
        .then(function (data) {
            var suggestions = data.suggestions || [];
            if (suggestions.length === 0) {
                // Default suggestions
                suggestions = [
                    'How is the ICDEV™ codebase structured?',
                    'What does the LLM router do?',
                    'How does the RAG retriever work?'
                ];
            }
            var html = '';
            for (var i = 0; i < suggestions.length; i++) {
                html += '<button class="assistant-suggestion-btn" data-q="' + escHtml(suggestions[i]) + '">'
                    + escHtml(suggestions[i]) + '</button>';
            }
            container.innerHTML = html;
            // Wire click handlers
            var btns = container.querySelectorAll('.assistant-suggestion-btn');
            for (var j = 0; j < btns.length; j++) {
                btns[j].addEventListener('click', function () {
                    var q = this.getAttribute('data-q');
                    var input = document.getElementById('assistant-input');
                    if (input) input.value = q;
                    sendQuery(q);
                    if (input) input.value = '';
                });
            }
        })
        .catch(function () { /* Suggestions are optional */ });
    }

    function loadStatus() {
        fetch(API_BASE + '/status')
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (data) {
            if (data) {
                setStatus(data.index_status === 'indexing'
                    ? 'Indexing... (' + (data.indexed_files || 0) + ' files)'
                    : (data.indexed_files || 0) + ' files indexed');
            }
        })
        .catch(function () { setStatus('Ready'); });
    }

    // ===================================================================
    // Initialization
    // ===================================================================

    function init() {
        var fab = document.getElementById('assistant-fab');
        var closeBtn = document.getElementById('assistant-close');
        var clearBtn = document.getElementById('assistant-clear');
        var fullscreenBtn = document.getElementById('assistant-fullscreen');
        var sendBtn = document.getElementById('assistant-send');
        var input = document.getElementById('assistant-input');
        var scopeSelect = document.getElementById('assistant-scope');

        if (!fab) return; // Widget not present on this page

        // Load persisted state
        loadState();

        // Auto-detect scope from URL
        var detectedScope = detectScope();
        if (detectedScope && !_scope) {
            _scope = detectedScope;
        }

        // Populate scope dropdown
        populateScopeDropdown();

        // Event handlers
        fab.addEventListener('click', expand);
        if (closeBtn) closeBtn.addEventListener('click', collapse);
        if (clearBtn) clearBtn.addEventListener('click', clearChat);
        if (fullscreenBtn) fullscreenBtn.addEventListener('click', goFullScreen);

        if (sendBtn) sendBtn.addEventListener('click', function () {
            var q = input ? input.value.trim() : '';
            if (q) { sendQuery(q); input.value = ''; }
        });

        if (input) input.addEventListener('keydown', function (e) {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                var q = input.value.trim();
                if (q) { sendQuery(q); input.value = ''; }
            }
        });

        if (scopeSelect) scopeSelect.addEventListener('change', function () {
            _scope = this.value || null;
            saveState();
            setStatus(_scope ? 'Scoped to: ' + _scope : 'Global scope');
        });

        // Restore expanded state
        if (_expanded) {
            expand();
        }

        // Load index status
        loadStatus();

        // Watch for orange Strategos panel open/close and adjust position
        watchOrangePanel();
    }

    // Run on DOM ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
