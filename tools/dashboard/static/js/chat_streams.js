/**
 * CUI // SP-CTI
 * Multi-stream parallel chat UI (Phase 44 — D257-D260, D265-D267).
 *
 * Zero-dependency IIFE extending ICDEV.chatStreams.
 * Manages context list, message rendering, version-tracked polling,
 * and intervention UI.
 */
(function () {
    'use strict';

    var POLL_INTERVAL = 2000; // ms
    var API_BASE = '/api/chat';

    // Per-context version tracking for dirty-tracking state push (Feature 4)
    var _contextVersions = {};
    var _activeContextId = null;
    var _pollTimer = null;
    var _userId = 'dashboard-user'; // Default; set from auth

    // Detect user from page if available
    try {
        var badge = document.querySelector('.user-badge-name');
        if (badge) _userId = badge.textContent.trim() || _userId;
    } catch (e) {}

    // -------------------------------------------------------------------
    // API helpers
    // -------------------------------------------------------------------

    function api(method, path, body) {
        var opts = { method: method, headers: { 'Content-Type': 'application/json' } };
        if (body) opts.body = JSON.stringify(body);
        return fetch(API_BASE + path, opts).then(function (r) { return r.json(); });
    }

    // -------------------------------------------------------------------
    // Context management
    // -------------------------------------------------------------------

    function createContext(options) {
        options = options || {};
        return api('POST', '/contexts', {
            user_id: _userId,
            tenant_id: options.tenant_id || '',
            title: options.title || '',
            project_id: options.project_id || '',
            agent_model: options.agent_model || 'sonnet',
            system_prompt: options.system_prompt || ''
        }).then(function (ctx) {
            if (ctx.error) {
                if (window.ICDEV && ICDEV.notify) ICDEV.notify(ctx.error, 'error');
                return ctx;
            }
            refreshContextList();
            switchContext(ctx.context_id);
            return ctx;
        });
    }

    function refreshContextList() {
        api('GET', '/contexts?user_id=' + encodeURIComponent(_userId) + '&include_closed=true')
            .then(function (data) {
                renderContextList(data.contexts || []);
                updateStats(data.contexts || []);
            });
    }

    function switchContext(ctxId) {
        _activeContextId = ctxId;
        if (!_contextVersions[ctxId]) _contextVersions[ctxId] = 0;

        // Highlight active in sidebar
        var items = document.querySelectorAll('.ctx-item');
        for (var i = 0; i < items.length; i++) {
            items[i].classList.toggle('active', items[i].dataset.ctxId === ctxId);
        }

        // Load context
        api('GET', '/contexts/' + ctxId).then(function (ctx) {
            if (ctx.error) return;
            document.getElementById('chat-title').textContent = ctx.title || ctxId;
            var statusEl = document.getElementById('chat-status');
            statusEl.textContent = ctx.status;
            statusEl.className = 'badge badge-' + (ctx.status === 'active' ? 'success' : 'warning');

            // Enable input
            var inp = document.getElementById('message-input');
            var btn = document.getElementById('btn-send');
            var closeBtn = document.getElementById('btn-close-context');
            if (ctx.status === 'active') {
                inp.disabled = false;
                btn.disabled = false;
                closeBtn.style.display = 'inline-block';
            } else {
                inp.disabled = true;
                btn.disabled = true;
                closeBtn.style.display = 'none';
            }

            renderMessages(ctx.messages || []);
            updateInterventionBar(ctx.is_processing);

            // Start polling
            startPolling(ctxId);
        });
    }

    function closeContext(ctxId) {
        api('POST', '/' + ctxId + '/close').then(function () {
            refreshContextList();
            document.getElementById('chat-title').textContent = 'Select or create a context';
            document.getElementById('message-input').disabled = true;
            document.getElementById('btn-send').disabled = true;
            document.getElementById('btn-close-context').style.display = 'none';
            _activeContextId = null;
            stopPolling();
        });
    }

    // -------------------------------------------------------------------
    // Messaging
    // -------------------------------------------------------------------

    function sendMessage(ctxId, content) {
        if (!ctxId || !content) return;
        api('POST', '/' + ctxId + '/send', { content: content, role: 'user' })
            .then(function (res) {
                if (res.error) {
                    if (window.ICDEV && ICDEV.notify) ICDEV.notify(res.error, 'error');
                    return;
                }
                // Append optimistic message
                appendMessage({ role: 'user', content: content, turn_number: res.turn_number });
                document.getElementById('message-input').value = '';
            });
    }

    function intervene(ctxId, message) {
        if (!ctxId || !message) return;
        api('POST', '/' + ctxId + '/intervene', { message: message })
            .then(function (res) {
                if (res.error) {
                    if (window.ICDEV && ICDEV.notify) ICDEV.notify(res.error, 'error');
                    return;
                }
                appendMessage({ role: 'intervention', content: message, turn_number: res.turn_number });
                document.getElementById('intervention-input').value = '';
            });
    }

    // -------------------------------------------------------------------
    // Polling (Feature 4 — dirty-tracking)
    // -------------------------------------------------------------------

    function startPolling(ctxId) {
        stopPolling();
        _pollTimer = setInterval(function () {
            pollContextState(ctxId);
        }, POLL_INTERVAL);
    }

    function stopPolling() {
        if (_pollTimer) {
            clearInterval(_pollTimer);
            _pollTimer = null;
        }
    }

    function pollContextState(ctxId) {
        var sinceVersion = _contextVersions[ctxId] || 0;
        api('GET', '/' + ctxId + '/state?since_version=' + sinceVersion + '&client_id=' + encodeURIComponent(_userId))
            .then(function (state) {
                if (!state || state.error) return;

                // Update version
                if (state.dirty_version > sinceVersion) {
                    _contextVersions[ctxId] = state.dirty_version;
                }

                // Process state updates
                var updates = state.state_updates || {};
                if (!updates.up_to_date && updates.changes) {
                    for (var i = 0; i < updates.changes.length; i++) {
                        var change = updates.changes[i];
                        if (change.type === 'new_message' && change.data && change.data.role === 'assistant') {
                            // Fetch new messages
                            refreshMessages(ctxId);
                        }
                    }
                }

                // Update processing status
                updateInterventionBar(state.is_processing);
                updateStats(null); // Refresh diagnostics
            });
    }

    function refreshMessages(ctxId) {
        api('GET', '/' + ctxId + '/messages?since=0&limit=100')
            .then(function (data) {
                if (data.messages) renderMessages(data.messages);
            });
    }

    // -------------------------------------------------------------------
    // Rendering
    // -------------------------------------------------------------------

    function renderContextList(contexts) {
        var container = document.getElementById('context-list');
        if (!container) return;
        if (!contexts.length) {
            container.innerHTML = '<div style="padding: 16px; text-align: center; color: var(--text-muted); font-size: 0.85rem;">No chat contexts yet. Click + New to start.</div>';
            return;
        }
        var html = '';
        for (var i = 0; i < contexts.length; i++) {
            var c = contexts[i];
            var isActive = c.context_id === _activeContextId;
            var statusColor = c.status === 'active' ? 'var(--accent-green, #0a0)' : 'var(--text-muted)';
            html += '<div class="ctx-item' + (isActive ? ' active' : '') + '" data-ctx-id="' + c.context_id + '" '
                + 'style="padding: 8px 12px; border-bottom: 1px solid var(--border-color); cursor: pointer;'
                + (isActive ? ' background: var(--bg-tertiary, #223);' : '') + '">'
                + '<div style="display: flex; justify-content: space-between; align-items: center;">'
                + '<span style="font-size: 0.85rem; font-weight: 500;">' + escHtml(c.title || c.context_id) + '</span>'
                + '<span style="width: 8px; height: 8px; border-radius: 50%; background: ' + statusColor + '; display: inline-block;"></span>'
                + '</div>'
                + '<div style="font-size: 0.75rem; color: var(--text-muted); margin-top: 2px;">'
                + c.message_count + ' msgs'
                + (c.is_processing ? ' · processing' : '')
                + (c.queue_depth > 0 ? ' · ' + c.queue_depth + ' queued' : '')
                + '</div></div>';
        }
        container.innerHTML = html;

        // Click handlers
        var items = container.querySelectorAll('.ctx-item');
        for (var j = 0; j < items.length; j++) {
            items[j].addEventListener('click', (function (id) {
                return function () { switchContext(id); };
            })(items[j].dataset.ctxId));
        }
    }

    function renderMessages(messages) {
        var stream = document.getElementById('message-stream');
        if (!stream) return;
        if (!messages.length) {
            stream.innerHTML = '<div style="text-align: center; padding: 40px; color: var(--text-muted); font-size: 0.9rem;">Start a conversation by sending a message.</div>';
            return;
        }
        var html = '';
        for (var i = 0; i < messages.length; i++) {
            html += renderMessageHtml(messages[i]);
        }
        stream.innerHTML = html;
        stream.scrollTop = stream.scrollHeight;
    }

    function appendMessage(msg) {
        var stream = document.getElementById('message-stream');
        if (!stream) return;
        // Clear placeholder
        var placeholder = stream.querySelector('[style*="text-align: center"]');
        if (placeholder && stream.children.length === 1) stream.innerHTML = '';
        stream.innerHTML += renderMessageHtml(msg);
        stream.scrollTop = stream.scrollHeight;
    }

    function renderMessageHtml(msg) {
        var role = msg.role || 'user';
        var bgColor = role === 'assistant' ? 'var(--bg-secondary)' : role === 'intervention' ? 'var(--bg-warning, #332)' : role === 'system' ? 'var(--bg-tertiary, #112)' : 'transparent';
        var borderLeft = role === 'intervention' ? '3px solid var(--accent-yellow, #fa0)' : role === 'system' ? '3px solid var(--accent-red, #d44)' : 'none';
        var label = role === 'assistant' ? 'Agent' : role === 'intervention' ? 'Intervention' : role === 'system' ? 'System' : 'You';
        var labelColor = role === 'assistant' ? 'var(--accent-blue)' : role === 'intervention' ? 'var(--accent-yellow, #fa0)' : role === 'system' ? 'var(--accent-red, #d44)' : 'var(--accent-green, #0a0)';

        return '<div style="padding: 8px 12px; margin-bottom: 4px; background: ' + bgColor + '; border-left: ' + borderLeft + '; border-radius: 4px;">'
            + '<div style="font-size: 0.75rem; font-weight: 600; color: ' + labelColor + '; margin-bottom: 4px;">'
            + label + (msg.turn_number ? ' (#' + msg.turn_number + ')' : '') + '</div>'
            + '<div style="font-size: 0.85rem; white-space: pre-wrap; word-break: break-word;">' + escHtml(msg.content || '') + '</div>'
            + '</div>';
    }

    function updateInterventionBar(isProcessing) {
        var bar = document.getElementById('intervention-bar');
        if (bar) bar.style.display = isProcessing ? 'block' : 'none';
    }

    function updateStats(contexts) {
        if (contexts) {
            var active = 0, processing = 0, queued = 0;
            for (var i = 0; i < contexts.length; i++) {
                if (contexts[i].status === 'active') active++;
                if (contexts[i].is_processing) processing++;
                queued += contexts[i].queue_depth || 0;
            }
            setText('stat-active', active);
            setText('stat-processing', processing);
            setText('stat-queued', queued);
            setText('stat-total', contexts.length);
        } else {
            // Fetch diagnostics
            api('GET', '/diagnostics').then(function (d) {
                if (!d) return;
                setText('stat-active', d.active_contexts || 0);
                setText('stat-processing', d.processing || 0);
                setText('stat-queued', d.total_queued || 0);
                setText('stat-total', d.total_contexts || 0);
            });
        }
    }

    function setText(id, val) {
        var el = document.getElementById(id);
        if (el) el.textContent = val;
    }

    function escHtml(s) {
        var d = document.createElement('div');
        d.textContent = s;
        return d.innerHTML;
    }

    // -------------------------------------------------------------------
    // Event bindings
    // -------------------------------------------------------------------

    function init() {
        // New context modal
        var modal = document.getElementById('new-context-modal');
        var btnNew = document.getElementById('btn-new-context');
        var btnCancel = document.getElementById('btn-cancel-modal');
        var btnCreate = document.getElementById('btn-create-context');

        if (btnNew) btnNew.addEventListener('click', function () {
            if (modal) modal.style.display = 'flex';
        });
        if (btnCancel) btnCancel.addEventListener('click', function () {
            if (modal) modal.style.display = 'none';
        });
        if (btnCreate) btnCreate.addEventListener('click', function () {
            var title = document.getElementById('new-ctx-title').value.trim();
            var model = document.getElementById('new-ctx-model').value;
            var prompt = document.getElementById('new-ctx-prompt').value.trim();
            createContext({ title: title, agent_model: model, system_prompt: prompt });
            if (modal) modal.style.display = 'none';
            // Clear fields
            document.getElementById('new-ctx-title').value = '';
            document.getElementById('new-ctx-prompt').value = '';
        });

        // Send message
        var btnSend = document.getElementById('btn-send');
        var msgInput = document.getElementById('message-input');
        if (btnSend) btnSend.addEventListener('click', function () {
            sendMessage(_activeContextId, msgInput.value.trim());
        });
        if (msgInput) msgInput.addEventListener('keydown', function (e) {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendMessage(_activeContextId, msgInput.value.trim());
            }
        });

        // Intervene
        var btnIntervene = document.getElementById('btn-intervene');
        var intInput = document.getElementById('intervention-input');
        if (btnIntervene) btnIntervene.addEventListener('click', function () {
            intervene(_activeContextId, intInput.value.trim());
        });
        if (intInput) intInput.addEventListener('keydown', function (e) {
            if (e.key === 'Enter') {
                e.preventDefault();
                intervene(_activeContextId, intInput.value.trim());
            }
        });

        // Close context
        var btnClose = document.getElementById('btn-close-context');
        if (btnClose) btnClose.addEventListener('click', function () {
            if (_activeContextId) closeContext(_activeContextId);
        });

        // Initial load
        refreshContextList();
    }

    // -------------------------------------------------------------------
    // Expose on ICDEV namespace
    // -------------------------------------------------------------------

    window.ICDEV = window.ICDEV || {};
    window.ICDEV.chatStreams = {
        createContext: createContext,
        switchContext: switchContext,
        sendMessage: sendMessage,
        intervene: intervene,
        pollContextState: pollContextState,
        refreshContextList: refreshContextList,
        closeContext: closeContext
    };

    // Init on DOM ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
