// CUI // SP-CTI
// ICDEV™ Unified Chat — multi-stream backbone + RICOAS intake features.
// Merges Phase 44 multi-stream (D257-D260) with RICOAS requirements intake.
// Single page: context sidebar | message stream | RICOAS + Governance sidebar.

(function () {
    'use strict';

    // ===================================================================
    // Config & State
    // ===================================================================

    var POLL_INTERVAL = 2000;
    var CHAT_API = '/api/chat';
    var INTAKE_API = '/api/intake';

    var _activeContextId = null;
    var _contextVersions = {};
    var _pollTimer = null;
    var _userId = 'dashboard-user';

    // Intake session mappings: context_id -> intake_session_id
    var _intakeMap = {};
    try { _intakeMap = JSON.parse(localStorage.getItem('icdev_intake_map') || '{}'); } catch (e) {}

    // Active intake session for current context
    var _activeIntakeSessionId = null;

    // Canvas mode mappings: context_id -> canvas_type (persisted across page loads)
    var _canvasMap = {};
    try { _canvasMap = JSON.parse(localStorage.getItem('icdev_canvas_map') || '{}'); } catch (e) {}

    // Simulate session IDs: context_id -> simulate session_id (in-memory, resets on page load)
    var _simSessionMap = {};

    // Active canvas type for current context
    var _activeCanvasType = 'intake';

    // RICOAS timers and state
    var _readinessTimer = null;
    var _lastReadinessScore = 0;
    var _coaTimer = null;
    var _coasLoaded = false;
    var _buildTimer = null;
    var _testTimer = null;
    var _turnCount = 0;
    var _activeTechniqueId = null;
    var _isPanelMode = false;
    var _panelPersonas = ['developer', 'analyst'];

    // Framework display name mapping
    var FRAMEWORK_NAMES = {
        fedramp_moderate: 'FedRAMP Moderate',
        fedramp_high: 'FedRAMP High',
        cmmc_l2: 'CMMC L2',
        cmmc_l3: 'CMMC L3',
        nist_800_171: 'NIST 800-171',
        nist_800_207: 'NIST 800-207 (ZTA)',
        cnssi_1253: 'CNSSI 1253',
        hipaa: 'HIPAA',
        pci_dss: 'PCI DSS',
        cjis: 'CJIS',
        soc2: 'SOC 2',
        iso_27001: 'ISO 27001',
        hitrust: 'HITRUST'
    };

    var COMPLEXITY_LABELS = {
        quick_flow: 'Quick Flow',
        standard: 'Standard',
        full_pipeline: 'Full Pipeline'
    };

    var PHASE_ICONS = {
        pending: '&#x25CB;',
        running: '&#x25CF;',
        done: '&#x2713;',
        error: '&#x2717;',
        warning: '&#x26A0;'
    };

    // Detect user from page if available
    try {
        var badge = document.querySelector('.user-badge-name');
        if (badge) _userId = badge.textContent.trim() || _userId;
    } catch (e) {}

    var ns = window.ICDEV || {};

    // ===================================================================
    // Utility helpers
    // ===================================================================

    function escHtml(s) {
        var d = document.createElement('div');
        d.textContent = s;
        return d.innerHTML;
    }

    function escAttr(s) {
        return escHtml(s).replace(/'/g, '&#39;');
    }

    function setText(id, val) {
        var el = document.getElementById(id);
        if (el) el.textContent = val;
    }

    function saveIntakeMappings() {
        try { localStorage.setItem('icdev_intake_map', JSON.stringify(_intakeMap)); } catch (e) {}
    }

    function saveCanvasMappings() {
        try { localStorage.setItem('icdev_canvas_map', JSON.stringify(_canvasMap)); } catch (e) {}
    }

    function setContextCanvasType(ctxId, canvasType) {
        _canvasMap[ctxId] = canvasType || 'intake';
        saveCanvasMappings();
        if (ctxId === _activeContextId) {
            _activeCanvasType = _canvasMap[ctxId];
            updateModeChip(_activeCanvasType);
            updateSlashBar(_activeCanvasType);
        }
        // Persist to server
        fetch(CHAT_API + '/' + ctxId + '/mode', {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ canvas_type: canvasType || 'intake' })
        }).catch(function () {});
    }

    function isIntakeContext(ctxId) {
        return !!_intakeMap[ctxId];
    }

    function chatApi(method, path, body) {
        var opts = { method: method, headers: { 'Content-Type': 'application/json' } };
        if (body) opts.body = JSON.stringify(body);
        return fetch(CHAT_API + path, opts).then(function (r) { return r.json(); });
    }

    // ===================================================================
    // SECTION 1: Multi-Stream Backbone (context management, polling)
    // ===================================================================

    function createContext(options) {
        options = options || {};
        return chatApi('POST', '/contexts', {
            user_id: _userId,
            tenant_id: options.tenant_id || '',
            title: options.title || '',
            project_id: options.project_id || '',
            agent_model: options.agent_model || (window._CHAT_CONFIG && window._CHAT_CONFIG.defaultModel) || '',
            system_prompt: options.system_prompt || ''
        }).then(function (ctx) {
            if (ctx.error) {
                if (ns.notify) ns.notify(ctx.error, 'error');
                return ctx;
            }
            refreshContextList();
            switchContext(ctx.context_id);
            return ctx;
        });
    }

    function refreshContextList() {
        return chatApi('GET', '/contexts?user_id=' + encodeURIComponent(_userId) + '&include_closed=false')
            .then(function (data) {
                var contexts = data.contexts || [];
                renderContextList(contexts);
                updateTopStats(contexts);
                return contexts;
            });
    }

    function deleteContext(ctxId) {
        chatApi('POST', '/' + ctxId + '/close').then(function () {
            if (_activeContextId === ctxId) {
                _activeContextId = null;
                _activeIntakeSessionId = null;
                stopPolling();
                stopRicoasTimers();
                hideRicoasSidebar();
                setText('chat-title', 'Start a new conversation');
                var inp = document.getElementById('message-input');
                var btn = document.getElementById('btn-send');
                var closeBtn = document.getElementById('btn-close-context');
                if (inp) inp.disabled = true;
                if (btn) btn.disabled = true;
                if (closeBtn) closeBtn.style.display = 'none';
                var stream = document.getElementById('message-stream');
                if (stream) stream.innerHTML = '';
                var wb = document.getElementById('chat-welcome-banner');
                if (wb) { stream.appendChild(wb); wb.style.display = ''; }
            }
            refreshContextList();
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

        // Restore canvas type and intake session for this context
        _activeIntakeSessionId = _intakeMap[ctxId] || null;
        _activeCanvasType = _canvasMap[ctxId] || 'intake';
        updateModeChip(_activeCanvasType);
        updateSlashBar(_activeCanvasType);

        // Canvas mode takes priority; intake is secondary; regular is fallback
        if (_activeCanvasType && _activeCanvasType !== 'intake') {
            switchToCanvasContext(ctxId, _activeCanvasType);
        } else if (_activeIntakeSessionId) {
            switchToIntakeContext(ctxId, _activeIntakeSessionId);
        } else {
            switchToRegularContext(ctxId);
        }
    }

    function switchToCanvasContext(ctxId, canvasType) {
        hideRicoasSidebar();
        stopRicoasTimers();

        var canvasLabels = {
            cam: 'Migration (CAM)', ndc: 'Network Design (NDC)', sdc: 'Security Design (SDC)',
            eda: 'Data Architecture (EDA)', ddc: 'Database Design (DDC)', pdc: 'Process Design (PDC)',
            bdc: 'Business Design (BDC)', odc: 'Observability (ODC)', idc: 'Infrastructure (IDC)'
        };

        chatApi('GET', '/contexts/' + ctxId).then(function (ctx) {
            if (ctx.error) return;
            var label = canvasLabels[canvasType] || canvasType.toUpperCase();
            setText('chat-title', ctx.title || label);
            var statusEl = document.getElementById('chat-status');
            if (statusEl) { statusEl.textContent = label; statusEl.className = 'badge badge-success'; }
            var inp = document.getElementById('message-input');
            var btn = document.getElementById('btn-send');
            var closeBtn = document.getElementById('btn-close-context');
            var uploadBtn = document.getElementById('chat-upload-btn');
            if (inp) { inp.disabled = false; inp.placeholder = canvasType === 'cam' ? '/coa oracle  |  /deprecated elasticsearch  |  ask about migration' : '/explain  |  /audit  |  describe your design'; }
            if (btn) btn.disabled = false;
            if (closeBtn) closeBtn.style.display = 'inline-block';
            if (uploadBtn) uploadBtn.style.display = 'inline-block';
            renderMessages(ctx.messages || []);
            updateInterventionBar(false);
            startPolling(ctxId);
            loadContextTasks(ctxId);
        });

        // Ensure a simulate session exists for this context
        if (!_simSessionMap[ctxId]) {
            fetch('/api/simulate/session', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ canvas_type: canvasType })
            }).then(function (r) { return r.json(); }).then(function (d) {
                _simSessionMap[ctxId] = d.session_id || d.id || '';
            }).catch(function () {});
        }
    }

    function switchToRegularContext(ctxId) {
        // Hide RICOAS sidebar, stop RICOAS timers
        hideRicoasSidebar();
        stopRicoasTimers();

        chatApi('GET', '/contexts/' + ctxId).then(function (ctx) {
            if (ctx.error) return;
            // Sync canvas_type from server in case it was set externally
            if (ctx.canvas_type && ctx.canvas_type !== 'intake' && (_canvasMap[ctxId] || 'intake') === 'intake') {
                _canvasMap[ctxId] = ctx.canvas_type;
                saveCanvasMappings();
                _activeCanvasType = ctx.canvas_type;
                updateModeChip(_activeCanvasType);
                updateSlashBar(_activeCanvasType);
                switchToCanvasContext(ctxId, ctx.canvas_type);
                return;
            }
            // Restore intake session from DB field or legacy system_prompt pattern
            var recoveredSession = ctx.intake_session_id || '';
            if (!recoveredSession && ctx.system_prompt && ctx.system_prompt.indexOf('RICOAS intake session: ') === 0) {
                recoveredSession = ctx.system_prompt.replace('RICOAS intake session: ', '').trim();
            }
            if (recoveredSession && !_intakeMap[ctxId]) {
                _intakeMap[ctxId] = recoveredSession;
                saveIntakeMappings();
                _activeIntakeSessionId = recoveredSession;
                switchToIntakeContext(ctxId, recoveredSession);
                return;
            }
            setText('chat-title', ctx.title || ctxId);
            var statusEl = document.getElementById('chat-status');
            statusEl.textContent = ctx.status;
            statusEl.className = 'badge badge-' + (ctx.status === 'active' ? 'success' : 'warning');

            var inp = document.getElementById('message-input');
            var btn = document.getElementById('btn-send');
            var closeBtn = document.getElementById('btn-close-context');
            var uploadBtn = document.getElementById('chat-upload-btn');
            if (ctx.status === 'active') {
                inp.disabled = false;
                btn.disabled = false;
                closeBtn.style.display = 'inline-block';
            } else {
                inp.disabled = true;
                btn.disabled = true;
                closeBtn.style.display = 'none';
            }
            if (uploadBtn) uploadBtn.style.display = ctx.status === 'active' ? 'inline-block' : 'none';

            renderMessages(ctx.messages || []);
            updateInterventionBar(ctx.is_processing);
            startPolling(ctxId);
            loadContextTasks(ctxId);
        });
    }

    function switchToIntakeContext(ctxId, intakeSessionId) {
        // Show RICOAS sidebar
        showRicoasSidebar();

        // Load context header from chat API
        chatApi('GET', '/contexts/' + ctxId).then(function (ctx) {
            if (ctx.error) return;
            setText('chat-title', ctx.title || 'Requirements Intake');
            var statusEl = document.getElementById('chat-status');
            statusEl.textContent = 'intake';
            statusEl.className = 'badge badge-success';

            var inp = document.getElementById('message-input');
            var btn = document.getElementById('btn-send');
            var closeBtn = document.getElementById('btn-close-context');
            var uploadBtn = document.getElementById('chat-upload-btn');
            inp.disabled = false;
            btn.disabled = false;
            closeBtn.style.display = 'inline-block';
            if (uploadBtn) uploadBtn.style.display = 'inline-block';

            updateInterventionBar(false);
        });

        // Load messages from intake API
        fetch(INTAKE_API + '/session/' + intakeSessionId)
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (data.error) {
                    // Fallback: show welcome message
                    var stream = document.getElementById('message-stream');
                    if (stream) stream.innerHTML = renderMessageHtml({ role: 'assistant', content: 'Welcome! I\'m the ICDEV™ Requirements Analyst. Tell me about the application you want to build.' });
                    return;
                }
                var messages = data.messages || data.conversation || [];
                var mapped = [];
                for (var i = 0; i < messages.length; i++) {
                    var m = messages[i];
                    mapped.push({
                        role: m.role === 'customer' ? 'user' : m.role === 'analyst' ? 'assistant' : m.role,
                        content: m.content,
                        turn_number: m.turn_number
                    });
                }
                renderMessages(mapped);
            })
            .catch(function () {
                var stream = document.getElementById('message-stream');
                if (stream) stream.innerHTML = renderMessageHtml({ role: 'assistant', content: 'Welcome! Describe what you want to build.' });
            });

        // Start RICOAS features
        startReadinessPolling();
        startCoaPolling();
        refreshReadiness();
        refreshComplexity();
        refreshCoas();
        loadTechniques();
        refreshBuild();

        // Display framework tags from config
        var cfg = window._CHAT_CONFIG || {};
        displayFrameworkTags(cfg.wizardFrameworks || '');

        // Start chat polling too (for intervention)
        startPolling(ctxId);
    }

    function closeContext(ctxId) {
        chatApi('POST', '/' + ctxId + '/close').then(function () {
            refreshContextList();
            setText('chat-title', 'Start a new conversation');
            document.getElementById('message-input').disabled = true;
            document.getElementById('btn-send').disabled = true;
            document.getElementById('btn-close-context').style.display = 'none';
            _activeContextId = null;
            _activeIntakeSessionId = null;
            stopPolling();
            stopRicoasTimers();
            hideRicoasSidebar();
            var stream = document.getElementById('message-stream');
            var wb = document.getElementById('chat-welcome-banner');
            if (wb && stream) { stream.appendChild(wb); wb.style.display = ''; }
        });
    }

    // ===================================================================
    // SECTION 2: Messaging (routes to chat or intake API)
    // ===================================================================

    // ===================================================================
    // /analyze <url> — works in all modes (canvas, intake, regular)
    // ===================================================================

    function sendAnalyzeCommand(url) {
        appendMessage({ role: 'user', content: '/analyze ' + url });

        var typingId = 'typing-' + Date.now();
        var stream = document.getElementById('message-stream');
        if (stream) {
            stream.innerHTML += '<div id="' + typingId + '" class="msg-bubble msg-bubble--system">'
                + '<div class="agent-name">Analyzer</div>'
                + '<div style="opacity:0.6;font-size:0.85rem;">Fetching and analyzing <code>'
                + escHtml(url) + '</code>…</div></div>';
            stream.scrollTop = stream.scrollHeight;
        }

        fetch('/api/chat/analyze', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url: url, canvas_type: _activeCanvasType || 'intake' })
        })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            var typing = document.getElementById(typingId);
            if (typing) typing.remove();
            if (data.error && !data.reply) {
                appendMessage({ role: 'system', content: 'Error: ' + data.error });
                return;
            }
            appendMessage({ role: 'assistant', content: data.reply || '(no response)' });
        })
        .catch(function (err) {
            var typing = document.getElementById(typingId);
            if (typing) typing.remove();
            appendMessage({ role: 'system', content: 'Analyze error: ' + err.message });
        });
    }

    function sendMessage() {
        var inp = document.getElementById('message-input');
        var content = inp ? inp.value.trim() : '';
        if (!content || !_activeContextId) return;

        // /analyze <url> — intercept before any canvas/intake routing
        if (content.toLowerCase().startsWith('/analyze')) {
            var analyzeArg = content.slice(8).trim();
            if (analyzeArg) {
                inp.value = '';
                sendAnalyzeCommand(analyzeArg);
                return;
            }
            // No URL given — show usage hint via the regular canvas path if available
        }

        if (_activeCanvasType && _activeCanvasType !== 'intake') {
            sendCanvasMessage(_activeContextId, content, _activeCanvasType);
        } else if (_activeIntakeSessionId) {
            // Auto-detect intent on first few messages
            var ctx = _contextVersions[_activeContextId];
            var msgCount = ctx ? (ctx.message_count || 0) : 0;
            if (msgCount <= 2) {
                detectAndMaybeSwitch(_activeContextId, content, function () {
                    sendIntakeMessage(content);
                });
            } else {
                sendIntakeMessage(content);
            }
        } else {
            sendChatMessage(_activeContextId, content);
        }
        inp.value = '';
    }

    function detectAndMaybeSwitch(ctxId, content, fallback) {
        fetch(CHAT_API + '/route-intent', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: content, context_id: ctxId })
        }).then(function (r) { return r.json(); }).then(function (d) {
            if (d.mode && d.mode !== 'intake' && d.confidence >= 0.70) {
                // Switch context to canvas mode
                setContextCanvasType(ctxId, d.mode);
                _activeCanvasType = d.mode;
                appendMessage({ role: 'system', content: 'Switched to ' + d.mode.toUpperCase() + ' canvas mode. ' + (d.reason || '') });
                // Re-send as canvas message now that mode is set
                sendCanvasMessage(ctxId, content, d.mode);
            } else {
                fallback();
            }
        }).catch(function () { fallback(); });
    }

    function sendChatMessage(ctxId, content) {
        chatApi('POST', '/' + ctxId + '/send', { content: content, role: 'user' })
            .then(function (res) {
                if (res.error) {
                    if (ns.notify) ns.notify(res.error, 'error');
                    return;
                }
                appendMessage({ role: 'user', content: content, turn_number: res.turn_number });
            });
    }

    function sendIntakeMessage(content) {
        if (_isPanelMode && _activeIntakeSessionId) {
            sendPanelMessage(content);
            return;
        }
        // Append user message immediately
        appendMessage({ role: 'user', content: content });

        // Show typing indicator
        var typingId = 'typing-' + Date.now();
        var stream = document.getElementById('message-stream');
        if (stream) {
            stream.innerHTML += '<div id="' + typingId + '" style="padding: 8px 12px; margin-bottom: 4px; background: var(--bg-secondary); border-radius: 4px;">'
                + '<div style="font-size: 0.75rem; font-weight: 600; color: var(--accent-blue); margin-bottom: 4px;">Agent</div>'
                + '<div style="font-size: 0.85rem; opacity: 0.6;">Thinking...</div></div>';
            stream.scrollTop = stream.scrollHeight;
        }

        fetch(INTAKE_API + '/turn', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_id: _activeIntakeSessionId, message: content })
        })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            // Remove typing indicator
            var typing = document.getElementById(typingId);
            if (typing) typing.remove();

            if (data.error) {
                appendMessage({ role: 'system', content: 'Error: ' + data.error });
                return;
            }
            appendMessage({ role: 'assistant', content: data.analyst_response || 'Thank you. Tell me more.' });

            // Update stats
            _turnCount = data.turn_number || _turnCount + 2;
            setText('stat-turns', _turnCount);
            if (data.total_requirements !== undefined) setText('stat-requirements', data.total_requirements);

            // Update readiness if provided
            if (data.readiness_update) updateReadinessDisplay(data.readiness_update);

            // Render BDD previews if provided
            if (data.bdd_previews && data.bdd_previews.length > 0) renderBddPreviews(data.bdd_previews);

            // Refresh readiness and complexity
            refreshReadiness();
            refreshComplexity();
        })
        .catch(function (err) {
            var typing = document.getElementById(typingId);
            if (typing) typing.remove();
            appendMessage({ role: 'system', content: 'Connection error: ' + err.message });
        });
    }

    // ===================================================================
    // Canvas message dispatch + rich response rendering
    // ===================================================================

    function sendCanvasMessage(ctxId, content, canvasType) {
        appendMessage({ role: 'user', content: content });

        var typingId = 'typing-' + Date.now();
        var stream = document.getElementById('message-stream');
        if (stream) {
            stream.innerHTML += '<div id="' + typingId + '" class="msg-bubble msg-bubble--system"><div class="agent-name">' + canvasType.toUpperCase() + ' Agent</div><div style="opacity:0.6;font-size:0.85rem;">Thinking...</div></div>';
            stream.scrollTop = stream.scrollHeight;
        }

        var simSessionId = _simSessionMap[ctxId] || '';

        fetch('/api/simulate/message', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_id: simSessionId, content: content, canvas_type: canvasType })
        }).then(function (r) { return r.json(); }).then(function (d) {
            var typing = document.getElementById(typingId);
            if (typing) typing.remove();

            var reply = d.reply || d.content || d.message || '';
            appendMessage({ role: 'assistant', content: reply });

            // Render rich canvas extras
            var extras = '';
            if (d.deprecation && d.deprecation.status && d.deprecation.status !== 'unknown' && d.deprecation.status !== 'active') {
                extras += _renderDepWarning(d.deprecation);
            }
            if (d.coa_cards && d.coa_cards.length) {
                extras += _renderCOACards(d.coa_cards, d.tech || '');
            }
            if (extras) {
                if (stream) { stream.innerHTML += extras; stream.scrollTop = stream.scrollHeight; }
            }

            // Render Mermaid diagram if in reply
            var mermaidMatch = reply.match(/```mermaid\n([\s\S]*?)```/);
            if (mermaidMatch && stream) {
                var mDiv = document.createElement('div');
                mDiv.className = 'msg-bubble msg-bubble--system';
                mDiv.innerHTML = '<div class="mermaid">' + mermaidMatch[1] + '</div>';
                stream.appendChild(mDiv);
                if (window.mermaid) { try { mermaid.init(undefined, mDiv.querySelectorAll('.mermaid')); } catch (e) {} }
                stream.scrollTop = stream.scrollHeight;
            }
        }).catch(function (e) {
            var typing = document.getElementById(typingId);
            if (typing) typing.remove();
            appendMessage({ role: 'system', content: 'Canvas error: ' + e.message });
        });
    }

    function _esc(s) { return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }

    // ===================================================================
    // Inline Q&A widget — detect questions in assistant messages and
    // render interactive answer fields so users don't copy/paste.
    // ===================================================================

    function _extractQuestions(text) {
        // Strip markdown syntax before scanning for questions
        var plain = text
            .replace(/```[\s\S]*?```/g, '')   // code blocks
            .replace(/`[^`]+`/g, '')           // inline code
            .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1') // links
            .replace(/[*_>#]/g, '');           // emphasis/headers

        // Split on question marks — each segment ending with ? is a candidate
        var candidates = plain.match(/[^?!.\n]{10,}[^?!.]*\?/g) || [];

        var questions = [];
        var seen = {};
        for (var i = 0; i < candidates.length; i++) {
            var q = candidates[i]
                .replace(/^\s*[-*\d.]+\s*/, '') // strip list markers
                .replace(/\s+/g, ' ')
                .trim();
            if (q.length < 15) continue;        // ignore trivial fragments
            // Deduplicate loosely
            var key = q.slice(0, 40).toLowerCase();
            if (seen[key]) continue;
            seen[key] = true;
            questions.push(q);
        }
        return questions;
    }

    function _injectQAWidget(stream, rawContent) {
        var questions = _extractQuestions(rawContent);
        if (questions.length < 1) return;

        var lastBubble = stream.lastElementChild;
        if (!lastBubble || lastBubble.classList.contains('qa-widget')) return;

        var widgetId = 'qa-' + Date.now();
        var html = '<div class="qa-widget" id="' + widgetId + '">';
        html += '<div class="qa-widget__header">';
        html += '<span class="qa-widget__header-icon">💬</span>';
        html += 'Answer inline <span class="qa-widget__count">— ' + questions.length + ' question' + (questions.length > 1 ? 's' : '') + '</span>';
        html += '</div>';
        html += '<div class="qa-widget__fields">';
        for (var i = 0; i < questions.length; i++) {
            html += '<div class="qa-widget__item">';
            html += '<label class="qa-widget__label"><span class="qa-widget__label-num">' + (i + 1) + '.</span>' + escHtml(questions[i]) + '</label>';
            html += '<textarea class="qa-widget__input" rows="2" placeholder="Your answer…" data-qi="' + i + '"></textarea>';
            html += '</div>';
        }
        html += '</div>';
        html += '<div class="qa-widget__footer">';
        html += '<button class="qa-widget__submit" id="' + widgetId + '-send">Send All Answers</button>';
        html += '<button class="qa-widget__skip" id="' + widgetId + '-skip">Dismiss</button>';
        html += '</div>';
        html += '</div>';

        lastBubble.insertAdjacentHTML('afterend', html);
        stream.scrollTop = stream.scrollHeight;

        var widget = document.getElementById(widgetId);
        if (!widget) return;

        document.getElementById(widgetId + '-skip').addEventListener('click', function () {
            widget.remove();
        });

        document.getElementById(widgetId + '-send').addEventListener('click', function () {
            var inputs = widget.querySelectorAll('.qa-widget__input');
            var parts = [];
            for (var j = 0; j < questions.length; j++) {
                var answer = inputs[j] ? inputs[j].value.trim() : '';
                if (answer) parts.push((j + 1) + '. ' + questions[j] + '\n   ' + answer);
            }
            if (!parts.length) return;

            // Collapse widget before sending
            widget.classList.add('qa-widget--sent');
            widget.innerHTML = '<div class="qa-widget__sent-label">Answers submitted</div>';

            // Put composed text in input and fire send directly
            var msgInput = document.getElementById('message-input');
            if (msgInput) {
                msgInput.disabled = false;
                msgInput.value = parts.join('\n\n');
                var sendBtn = document.getElementById('btn-send');
                if (sendBtn) sendBtn.disabled = false;
                sendMessage();
            }
        });
    }

    function _renderDepWarning(dep) {
        var isCrit = dep.severity === 'critical' || dep.severity === 'high';
        return '<div style="background:rgba(' + (isCrit ? '198,40,40' : '230,81,0') + ',0.15);border-left:4px solid ' + (isCrit ? '#f87171' : '#fb923c') + ';border-radius:6px;padding:0.65rem 1rem;margin:0.5rem 0;font-size:0.88rem;">'
            + (isCrit ? '🔴' : '🟡') + ' <strong>' + _esc(dep.tech) + (dep.eol_date ? ' (EOL: ' + _esc(dep.eol_date) + ')' : ' AT RISK') + '</strong>'
            + (dep.successor ? ' — Successor: <strong>' + _esc(dep.successor) + '</strong>' : '')
            + '</div>';
    }

    function _renderCOACards(coas, techName) {
        var html = '<div style="margin-top:0.5rem;">';
        for (var i = 0; i < coas.length; i++) {
            var coa = coas[i];
            var isRec = coa.recommended;
            var effortColors = { low: '#4ade80', medium: '#fb923c', high: '#f87171' };
            html += '<div style="border:1px solid var(--border-color,#2a2a40);border-radius:10px;padding:1rem;margin-bottom:0.85rem;background:var(--bg-card,#16213e);' + (isRec ? 'border-color:#4a90d9;' : '') + '">';
            if (isRec) html += '<span style="background:#1976d2;color:#fff;border-radius:12px;padding:0.15rem 0.55rem;font-size:0.72rem;font-weight:700;text-transform:uppercase;margin-right:0.35rem;">Recommended</span>';
            html += '<div style="font-weight:700;font-size:1rem;margin:0.4rem 0;color:var(--text-primary,#e0e0e0);">' + _esc(coa.title || '') + '</div>';
            html += '<div style="font-size:0.82rem;margin-bottom:0.5rem;">';
            html += '<span style="background:rgba(40,167,69,0.2);color:#4ade80;border-radius:12px;padding:0.15rem 0.55rem;font-size:0.72rem;font-weight:700;margin-right:0.3rem;">' + _esc(coa.strategy_label || coa.strategy || '') + '</span>';
            if (coa.effort_days) html += '<span style="color:' + (effortColors[coa.effort] || '#aaa') + ';font-size:0.78rem;margin-right:0.3rem;">' + _esc(coa.effort_days) + 'd</span>';
            if (coa.timeline_weeks) html += '<span style="font-size:0.78rem;color:var(--text-muted);">~' + _esc(coa.timeline_weeks) + ' weeks</span>';
            html += '</div>';
            if (isRec && coa.recommended_reason) html += '<div style="font-size:0.83rem;color:#60a5fa;margin-bottom:0.5rem;font-style:italic;">' + _esc(coa.recommended_reason) + '</div>';
            var pros = coa.pros || [], cons = coa.cons || [];
            if (pros.length || cons.length) {
                html += '<div style="display:grid;grid-template-columns:1fr 1fr;gap:0.75rem;margin:0.5rem 0;">';
                html += '<div><div style="font-weight:700;font-size:0.78rem;text-transform:uppercase;color:#4ade80;margin-bottom:0.25rem;">Pros</div><ul style="margin:0;padding-left:1.1rem;font-size:0.85rem;">';
                for (var p = 0; p < Math.min(pros.length, 5); p++) html += '<li>' + _esc(pros[p]) + '</li>';
                html += '</ul></div>';
                html += '<div><div style="font-weight:700;font-size:0.78rem;text-transform:uppercase;color:#f87171;margin-bottom:0.25rem;">Cons</div><ul style="margin:0;padding-left:1.1rem;font-size:0.85rem;">';
                for (var c = 0; c < Math.min(cons.length, 5); c++) html += '<li>' + _esc(cons[c]) + '</li>';
                html += '</ul></div>';
                html += '</div>';
            }
            html += '</div>';
        }
        html += '</div>';
        return html;
    }

    function intervene(ctxId, message) {
        if (!ctxId || !message) return;
        chatApi('POST', '/' + ctxId + '/intervene', { message: message })
            .then(function (res) {
                if (res.error) {
                    if (ns.notify) ns.notify(res.error, 'error');
                    return;
                }
                appendMessage({ role: 'intervention', content: message, turn_number: res.turn_number });
                document.getElementById('intervention-input').value = '';
            });
    }

    // ===================================================================
    // SECTION 3: File Upload (RICOAS + RAG/KG for all contexts)
    // ===================================================================

    function uploadFiles(files) {
        for (var i = 0; i < files.length; i++) {
            if (_activeIntakeSessionId) {
                uploadSingleFileIntake(files[i]);
            }
            // Always also index into RAG+KG for the active context
            uploadSingleFileRagKg(files[i]);
        }
    }

    function uploadSingleFile(file) {
        // Legacy alias — route to intake upload (backward compat)
        if (_activeIntakeSessionId) {
            uploadSingleFileIntake(file);
        }
        uploadSingleFileRagKg(file);
    }

    function uploadSingleFileIntake(file) {
        var formData = new FormData();
        formData.append('session_id', _activeIntakeSessionId);
        formData.append('file', file);

        fetch(INTAKE_API + '/upload', { method: 'POST', body: formData })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (data.error) return;
            var docEl = document.getElementById('stat-documents');
            if (docEl) docEl.textContent = (parseInt(docEl.textContent, 10) || 0) + 1;
            refreshReadiness();
        })
        .catch(function () {});
    }

    function uploadSingleFileRagKg(file) {
        // Show uploading indicator card
        var uploadingContent = '📎 Indexing **' + file.name + '** into RAG + Knowledge Graph…';
        appendMessage({ role: 'system', content: uploadingContent });

        var formData = new FormData();
        formData.append('file', file);
        formData.append('context_id', _activeContextId || '');

        fetch('/api/chat/upload', { method: 'POST', body: formData })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (data.error) {
                appendMessage({ role: 'system', content: '❌ Upload failed for **' + file.name + '**: ' + data.error });
                return;
            }
            // Build rich confirmation message
            var chunks = data.rag_chunks || 0;
            var kgEntities = (data.kg && data.kg.total_entities) ? data.kg.total_entities : 0;
            var kgEdges = (data.kg && data.kg.total_edges) ? data.kg.total_edges : 0;
            var sizeKb = data.file_size_kb ? ' (' + data.file_size_kb + ' KB)' : '';
            var ftype = data.file_type ? data.file_type.toUpperCase() : '';

            var msg = '✅ **' + file.name + '** indexed' + sizeKb + '\n'
                    + '• RAG: ' + chunks + ' searchable chunk' + (chunks !== 1 ? 's' : '') + '\n';
            if (kgEntities > 0) {
                msg += '• KG: ' + kgEntities + ' entit' + (kgEntities !== 1 ? 'ies' : 'y')
                     + (kgEdges > 0 ? ', ' + kgEdges + ' relationship' + (kgEdges !== 1 ? 's' : '') : '') + '\n';
            }
            if (ftype === 'IMAGE' || ftype === 'PNG' || ftype === 'JPG' || ftype === 'JPEG' || ftype === 'GIF' || ftype === 'WEBP' || ftype === 'SVG') {
                msg += '• 📌 Image stored as reference — describe its contents in your next message to add semantic context';
            } else {
                msg += '• Now ask questions about this document and I\'ll retrieve the relevant sections automatically';
            }
            appendMessage({ role: 'system', content: msg });
            refreshDocumentList();
        })
        .catch(function (err) {
            appendMessage({ role: 'system', content: '❌ Upload error: ' + err.message });
        });
    }

    // ===================================================================
    // SECTION 3b: Intel Tab — Document List + KG Attribution
    // ===================================================================

    function refreshDocumentList() {
        var params = 'context_id=' + encodeURIComponent(_activeContextId || '');
        fetch('/api/chat/sources?' + params)
        .then(function (r) { return r.json(); })
        .then(function (data) {
            var el = document.getElementById('intel-docs-content');
            if (!el) return;
            var sources = data.sources || [];
            if (!sources.length) {
                el.innerHTML = 'No documents indexed. Upload a file to chat with your documents.';
                return;
            }
            var html = '<ul style="margin:0;padding:0 0 0 14px;">';
            for (var i = 0; i < sources.length; i++) {
                var s = sources[i];
                var name = escHtml(s.filename || s.source_id);
                var chunks = s.chunk_count || 0;
                var ts = s.indexed_at ? s.indexed_at.slice(0, 16).replace('T', ' ') : '';
                html += '<li style="margin-bottom:4px;"><span style="color:var(--accent-blue);">' + name + '</span>'
                      + ' <span style="color:var(--text-muted);font-size:0.75rem;">' + chunks + ' chunk' + (chunks !== 1 ? 's' : '') + (ts ? ' · ' + ts : '') + '</span></li>';
            }
            html += '</ul>';
            el.innerHTML = html;
        })
        .catch(function () {});
    }

    function updateKgAttribution(nodes) {
        var el = document.getElementById('intel-kg-content');
        if (!el) return;
        if (!nodes || !nodes.length) {
            el.innerHTML = 'No KG nodes retrieved yet.';
            return;
        }
        var html = '<ul style="margin:0;padding:0 0 0 14px;">';
        for (var i = 0; i < nodes.length; i++) {
            var n = nodes[i];
            var label = escHtml(n.label || '');
            var etype = escHtml(n.entity_type || '');
            var score = n.score !== undefined ? n.score.toFixed(2) : '';
            var summary = escHtml((n.summary || '').slice(0, 120));
            html += '<li style="margin-bottom:6px;">'
                  + '<span style="color:#a78bfa;font-weight:600;">' + label + '</span>'
                  + (etype ? ' <span style="color:var(--text-muted);font-size:0.7rem;">[' + etype + ']</span>' : '')
                  + (score ? ' <span style="color:var(--text-muted);font-size:0.7rem;">(' + score + ')</span>' : '')
                  + (summary ? '<br><span style="color:var(--text-muted);font-size:0.75rem;">' + summary + '</span>' : '')
                  + '</li>';
        }
        html += '</ul>';
        el.innerHTML = html;
    }

    // ===================================================================
    // SECTION 4: Polling (dirty-tracking for regular contexts)
    // ===================================================================

    function startPolling(ctxId) {
        stopPolling();
        _pollTimer = setInterval(function () {
            pollContextState(ctxId);
        }, POLL_INTERVAL);
    }

    function stopPolling() {
        if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null; }
    }

    function pollContextState(ctxId) {
        var sinceVersion = _contextVersions[ctxId] || 0;
        chatApi('GET', '/' + ctxId + '/state?since_version=' + sinceVersion + '&client_id=' + encodeURIComponent(_userId))
            .then(function (state) {
                if (!state || state.error) return;
                if (state.dirty_version > sinceVersion) _contextVersions[ctxId] = state.dirty_version;

                var updates = state.state_updates || {};
                if (!updates.up_to_date && updates.changes) {
                    for (var i = 0; i < updates.changes.length; i++) {
                        var change = updates.changes[i];
                        if (change.type === 'new_message' && change.data && change.data.role === 'assistant') {
                            if (!isIntakeContext(ctxId)) refreshChatMessages(ctxId);
                        }
                        if (change.type === 'rag_attribution' && change.data) {
                            var ragEl = document.getElementById('intel-rag-content');
                            if (ragEl && change.data.source_count) {
                                ragEl.innerHTML = '<span style="color:#2196f3;font-weight:600;">' + change.data.source_count +
                                    ' source(s)</span> used in last response.';
                            }
                        }
                        if (change.type === 'kg_attribution' && change.data && change.data.nodes) {
                            updateKgAttribution(change.data.nodes);
                        }
                    }
                }
                updateInterventionBar(state.is_processing);

                // Notify state change hooks
                if (window._chatOnStateChange) {
                    for (var j = 0; j < window._chatOnStateChange.length; j++) {
                        try { window._chatOnStateChange[j](state); } catch (e) {}
                    }
                }
            });
    }

    function refreshChatMessages(ctxId) {
        chatApi('GET', '/' + ctxId + '/messages?since=0&limit=100')
            .then(function (data) {
                if (data.messages) renderMessages(data.messages);
            });
    }

    // ===================================================================
    // SECTION 5: RICOAS Features — Readiness
    // ===================================================================

    function refreshReadiness() {
        if (!_activeIntakeSessionId) return;
        fetch(INTAKE_API + '/readiness/' + _activeIntakeSessionId)
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (data.error) return;
            updateReadinessDisplay(data);
        })
        .catch(function () {});
    }

    function updateReadinessDisplay(data) {
        var overall = data.overall_score || data.overall || 0;
        var pct = Math.round(overall * 100);
        var arc = document.getElementById('readiness-arc');
        if (arc) {
            var offset = 314 - (314 * overall);
            arc.setAttribute('stroke-dashoffset', offset);
            if (overall >= 0.7) arc.setAttribute('stroke', 'var(--status-green)');
            else if (overall >= 0.4) arc.setAttribute('stroke', 'var(--accent-blue)');
            else arc.setAttribute('stroke', 'var(--status-red, #dc3545)');
        }
        var pctEl = document.getElementById('readiness-pct');
        if (pctEl) pctEl.textContent = pct + '%';

        var dims = ['completeness', 'clarity', 'feasibility', 'compliance', 'testability'];
        var dimData = data.dimensions || data;
        for (var i = 0; i < dims.length; i++) {
            var dim = dims[i];
            var raw = dimData[dim];
            var val = typeof raw === 'object' ? (raw.score || 0) : (raw || 0);
            var barFill = document.getElementById('bar-' + dim);
            var valEl = document.getElementById('val-' + dim);
            if (barFill) barFill.style.width = Math.round(val * 100) + '%';
            if (valEl) valEl.textContent = Math.round(val * 100) + '%';
        }

        if (data.total_requirements !== undefined) setText('stat-requirements', data.total_requirements);
        else if (data.requirement_count !== undefined) setText('stat-requirements', data.requirement_count);

        var score = data.readiness_score || overall;
        var gauge = document.getElementById('readiness-gauge');
        var dimsEl = document.getElementById('readiness-dimensions');
        var placeholder = document.getElementById('readiness-placeholder');
        if (score > 0) {
            if (gauge) gauge.style.display = '';
            if (dimsEl) dimsEl.style.display = '';
            if (placeholder) placeholder.style.display = 'none';
        }

        _lastReadinessScore = overall;
        var planBtn = document.getElementById('generate-plan-btn');
        var exportBtn = document.getElementById('export-btn');
        var forceBtn = document.getElementById('force-build-btn');
        if (planBtn) planBtn.style.display = overall >= 0.7 ? 'block' : 'none';
        if (exportBtn) exportBtn.style.display = overall > 0 ? 'block' : 'none';
        if (forceBtn) {
            var hasReqs = (data.total_requirements || data.requirement_count || 0) > 0;
            forceBtn.style.display = (overall > 0 && overall < 0.7 && hasReqs) ? 'block' : 'none';
            forceBtn.title = 'Readiness: ' + Math.round(overall * 100) + '% — recommended threshold is 70%';
        }
    }

    function startReadinessPolling() {
        if (_readinessTimer) clearInterval(_readinessTimer);
        _readinessTimer = setInterval(function () {
            if (!document.hidden) refreshReadiness();
        }, 10000);
    }

    // ===================================================================
    // SECTION 6: RICOAS Features — Complexity
    // ===================================================================

    function refreshComplexity() {
        if (!_activeIntakeSessionId) return;
        fetch(INTAKE_API + '/complexity/' + _activeIntakeSessionId)
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (data.error || data.status !== 'ok') return;
            updateComplexityDisplay(data);
        })
        .catch(function () {});
    }

    function updateComplexityDisplay(data) {
        var section = document.getElementById('complexity-section');
        var levelEl = document.getElementById('complexity-level');
        var barEl = document.getElementById('complexity-bar');
        var scoreText = document.getElementById('complexity-score-text');
        var recEl = document.getElementById('complexity-recommendation');
        if (!section || !levelEl) return;

        section.style.display = 'block';
        var level = data.complexity_level || 'standard';
        var score = data.overall_score || 0;
        var label = COMPLEXITY_LABELS[level] || level;
        var cssClass = 'level-' + level.replace(/_/g, '-');

        levelEl.innerHTML = '<span class="level-badge ' + cssClass + '">' + label + '</span>';
        if (barEl) {
            barEl.style.width = score + '%';
            barEl.className = 'complexity-bar-fill';
            if (level === 'standard') barEl.classList.add('bar-standard');
            else if (level === 'full_pipeline') barEl.classList.add('bar-full-pipeline');
        }
        if (scoreText) scoreText.textContent = Math.round(score) + '/100';

        var rec = data.recommendation;
        if (rec && recEl) {
            var phases = rec.estimated_phases || 0;
            var skip = rec.skip_tiers || [];
            var html = '<strong>' + phases + ' pipeline phases</strong>';
            if (skip.length > 0) html += ' &mdash; skip ' + skip.join(', ').replace(/_/g, ' ');
            recEl.innerHTML = html;
            recEl.style.display = 'block';
        }
    }

    // ===================================================================
    // SECTION 7: RICOAS Features — Framework Tags
    // ===================================================================

    function displayFrameworkTags(frameworksStr) {
        if (!frameworksStr) return;
        var section = document.getElementById('frameworks-section');
        var container = document.getElementById('framework-tags');
        if (!section || !container) return;

        var frameworks = frameworksStr.split(',').filter(function (f) { return f.trim(); });
        if (frameworks.length === 0) return;

        section.style.display = 'block';
        container.innerHTML = '';
        for (var i = 0; i < frameworks.length; i++) {
            var fwId = frameworks[i].trim();
            var tag = document.createElement('span');
            tag.className = 'framework-tag';
            tag.textContent = FRAMEWORK_NAMES[fwId] || fwId;
            container.appendChild(tag);
        }
    }

    // ===================================================================
    // SECTION 8: RICOAS Features — Elicitation Techniques
    // ===================================================================

    function loadTechniques() {
        fetch(INTAKE_API + '/techniques')
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (data.error || !data.techniques) return;
            renderTechniqueChips(data.techniques);
        })
        .catch(function () {});
    }

    function renderTechniqueChips(techniques) {
        var container = document.getElementById('technique-chips');
        if (!container) return;
        container.innerHTML = '';
        for (var i = 0; i < techniques.length; i++) {
            var t = techniques[i];
            var chip = document.createElement('button');
            chip.className = 'technique-chip';
            chip.setAttribute('data-technique-id', t.id);
            chip.title = t.short;
            if (t.id === _activeTechniqueId) chip.classList.add('active');
            chip.textContent = t.name;
            chip.onclick = (function (techId) { return function () { activateTechnique(techId); }; })(t.id);
            container.appendChild(chip);
        }
    }

    function activateTechnique(techId) {
        if (!_activeIntakeSessionId) return;
        fetch(INTAKE_API + '/techniques/' + _activeIntakeSessionId + '/activate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ technique_id: techId })
        })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (data.error) { appendMessage({ role: 'system', content: 'Error: ' + data.error }); return; }
            _activeTechniqueId = techId;
            var banner = document.getElementById('technique-active');
            var nameEl = document.getElementById('technique-active-name');
            if (banner && nameEl && data.technique) {
                nameEl.textContent = data.technique.name;
                banner.style.display = 'flex';
            }
            var chips = document.querySelectorAll('.technique-chip');
            for (var i = 0; i < chips.length; i++) {
                chips[i].classList.toggle('active', chips[i].getAttribute('data-technique-id') === techId);
            }
            // Show technique explanation + suggested questions
            if (data.technique || data.suggested_questions) {
                appendTechniqueActivation(data);
            }
        })
        .catch(function (err) { appendMessage({ role: 'system', content: 'Error: ' + err.message }); });
    }

    function deactivateTechnique() {
        if (!_activeIntakeSessionId) return;
        fetch(INTAKE_API + '/techniques/' + _activeIntakeSessionId + '/deactivate', { method: 'POST' })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (data.error) { appendMessage({ role: 'system', content: 'Error: ' + data.error }); return; }
            _activeTechniqueId = null;
            var banner = document.getElementById('technique-active');
            if (banner) banner.style.display = 'none';
            var chips = document.querySelectorAll('.technique-chip');
            for (var i = 0; i < chips.length; i++) chips[i].classList.remove('active');
            appendMessage({ role: 'system', content: 'Technique deactivated. Standard intake mode resumed.' });
        })
        .catch(function (err) { appendMessage({ role: 'system', content: 'Error: ' + err.message }); });
    }

    // ===================================================================
    // SECTION 9: RICOAS Features — BDD Preview
    // ===================================================================

    function renderBddPreviews(previews) {
        var section = document.getElementById('bdd-preview-section');
        var list = document.getElementById('bdd-preview-list');
        if (!section || !list) return;
        section.style.display = 'block';
        for (var i = 0; i < previews.length; i++) {
            var item = document.createElement('div');
            item.className = 'bdd-preview-item';
            var label = document.createElement('div');
            label.className = 'bdd-preview-label';
            label.textContent = previews[i].requirement;
            var pre = document.createElement('pre');
            pre.className = 'bdd-preview-block';
            pre.textContent = previews[i].gherkin;
            item.appendChild(label);
            item.appendChild(pre);
            list.appendChild(item);
        }
    }

    // ===================================================================
    // SECTION 10: RICOAS Features — Export, Plan, Post-Export Actions
    // ===================================================================

    function chatGeneratePlan() {
        if (!_activeIntakeSessionId) return;
        appendMessage({ role: 'system', content: 'Readiness threshold reached! Exporting requirements for plan generation...' });
        chatExport();
    }

    function chatForceStartBuild() {
        if (!_activeIntakeSessionId) return;
        var pct = Math.round(_lastReadinessScore * 100);
        if (!confirm('Your readiness is ' + pct + '% (recommended: 70%).\n\nGaps in the requirements may lead to rework. Proceed anyway?')) return;
        fetch(INTAKE_API + '/force-build/' + _activeIntakeSessionId, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ confirmed: true })
        })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (data.error) { appendMessage({ role: 'system', content: 'Error: ' + data.error }); return; }
            appendMessage({ role: 'system', content: '⚠ ' + data.message });
            var panel = document.getElementById('post-export-actions');
            if (panel) panel.style.display = 'block';
            var forceBtn = document.getElementById('force-build-btn');
            if (forceBtn) forceBtn.style.display = 'none';
        })
        .catch(function (err) { appendMessage({ role: 'system', content: 'Error: ' + err.message }); });
    }

    // ===================================================================
    // SECTION 10b: Multi-Persona Panel
    // ===================================================================

    function chatTogglePanel(enabled) {
        _isPanelMode = enabled;
        var picker = document.getElementById('panel-persona-picker');
        var stream = document.getElementById('message-stream');
        if (picker) picker.style.display = enabled ? 'block' : 'none';
        if (stream) stream.classList.toggle('panel-mode-active', enabled);

        // Sync _panelPersonas from checked chips
        if (enabled) _syncPanelPersonas();

        // Wire chip clicks to update _panelPersonas
        var chips = document.querySelectorAll('#panel-persona-chips input[type="checkbox"]');
        chips.forEach(function (cb) {
            cb.onchange = function () {
                var chip = cb.closest('.panel-chip');
                if (chip) chip.classList.toggle('panel-chip--active', cb.checked);
                _syncPanelPersonas();
            };
        });
    }

    function _syncPanelPersonas() {
        var checked = document.querySelectorAll('#panel-persona-chips input[type="checkbox"]:checked');
        _panelPersonas = [];
        checked.forEach(function (cb) { _panelPersonas.push(cb.value); });
        if (_panelPersonas.length === 0) _panelPersonas = ['developer', 'analyst'];
    }

    function sendPanelMessage(content) {
        appendMessage({ role: 'user', content: content });

        var typingId = 'typing-' + Date.now();
        var stream = document.getElementById('message-stream');
        var names = _panelPersonas.map(function (p) {
            return p.replace('_', ' ').replace(/\b\w/g, function (c) { return c.toUpperCase(); });
        }).join(', ');
        if (stream) {
            stream.innerHTML += '<div id="' + typingId + '" style="padding:8px 12px;margin-bottom:4px;background:var(--bg-secondary);border-radius:4px;">'
                + '<div style="font-size:0.72rem;font-weight:700;color:#4a90d9;margin-bottom:3px;">Panel [' + escHtml(names) + ']</div>'
                + '<div style="font-size:0.85rem;opacity:0.6;">Experts thinking in parallel…</div></div>';
            stream.scrollTop = stream.scrollHeight;
        }

        fetch(INTAKE_API + '/panel-turn', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                session_id: _activeIntakeSessionId,
                message: content,
                personas: _panelPersonas,
            })
        })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            var typing = document.getElementById(typingId);
            if (typing) typing.remove();

            if (data.error) {
                appendMessage({ role: 'system', content: 'Panel error: ' + data.error });
                return;
            }

            _renderPanelResponse(data);

            // Update stats
            if (data.total_requirements !== undefined) setText('stat-requirements', data.total_requirements);
            refreshReadiness();
            refreshComplexity();
        })
        .catch(function (err) {
            var typing = document.getElementById(typingId);
            if (typing) typing.remove();
            appendMessage({ role: 'system', content: 'Panel error: ' + err.message });
        });
    }

    function _renderPanelResponse(data) {
        var stream = document.getElementById('message-stream');
        if (!stream) return;

        var responses = data.panel_responses || [];
        var merged = data.merged_requirements || [];

        var html = '<div class="panel-response">';

        responses.forEach(function (r) {
            var color = r.color || '#4a90d9';
            html += '<div class="panel-persona-bubble" style="--panel-color:' + escHtml(color) + '">';
            html += '<div class="panel-persona-bubble__label">' + escHtml(r.display_name || r.persona) + '</div>';
            if (r.error) {
                html += '<div class="panel-persona-bubble__body" style="color:#f87171;font-size:0.8rem;">Error: ' + escHtml(r.error) + '</div>';
            } else {
                // Strip REQ: lines from body — show them separately as pills
                var bodyText = (r.response || '').replace(/^REQ:.*$/gm, '').replace(/\n{3,}/g, '\n\n').trim();
                html += '<div class="panel-persona-bubble__body">' + escHtml(bodyText) + '</div>';
                if (r.requirements && r.requirements.length > 0) {
                    html += '<div class="panel-persona-bubble__reqs">';
                    r.requirements.forEach(function (req) {
                        html += '<span class="panel-req-pill" title="' + escHtml(req.type || '') + '">' + escHtml(req.text) + '</span>';
                    });
                    html += '</div>';
                }
            }
            html += '</div>';
        });

        // Consensus footer
        html += '<div class="panel-consensus">';
        if (merged.length > 0) {
            html += '<strong>' + merged.length + ' requirement' + (merged.length !== 1 ? 's' : '') + ' captured</strong>';
        } else {
            html += 'No requirements extracted this turn.';
        }
        if (data.panel_question) {
            html += ' &mdash; <em>' + escHtml(data.panel_question) + '</em>';
        }
        html += '</div>';
        html += '</div>';

        stream.innerHTML += html;
        stream.scrollTop = stream.scrollHeight;

        // Inject inline QA widget for the panel question
        if (data.panel_question) {
            _injectQAWidget(stream, data.panel_question);
        }
    }

    function chatExport() {
        if (!_activeIntakeSessionId) return;
        fetch(INTAKE_API + '/export/' + _activeIntakeSessionId, { method: 'POST' })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (data.error) { appendMessage({ role: 'system', content: 'Export error: ' + data.error }); return; }
            var count = data.requirements ? data.requirements.length : (data.count || 0);
            appendMessage({ role: 'system', content: 'Exported ' + count + ' requirements successfully. Choose an action below.' });
            var panel = document.getElementById('post-export-actions');
            if (panel) panel.style.display = 'block';
            var exportBtn = document.getElementById('export-btn');
            if (exportBtn) exportBtn.style.display = 'none';
        })
        .catch(function (err) { appendMessage({ role: 'system', content: 'Export error: ' + err.message }); });
    }

    function chatTriggerBuild() {
        if (!_activeIntakeSessionId) return;
        appendMessage({ role: 'system', content: 'Starting build pipeline...' });
        fetch(INTAKE_API + '/build/' + _activeIntakeSessionId + '/start', { method: 'POST' })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (data.error) { appendMessage({ role: 'system', content: 'Error: ' + data.error }); return; }
            appendMessage({ role: 'system', content: 'Build pipeline started. Track progress in the sidebar.' });
            showBuildPipeline(data.phases || []);
            startBuildPolling();
        })
        .catch(function (err) { appendMessage({ role: 'system', content: 'Error: ' + err.message }); });
    }

    function chatRunSimulation() {
        if (!_activeIntakeSessionId) return;
        appendMessage({ role: 'system', content: 'Generating COAs with simulation...' });
        fetch(INTAKE_API + '/coas/' + _activeIntakeSessionId + '/generate', { method: 'POST' })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (data.error) { appendMessage({ role: 'system', content: 'Error: ' + data.error }); return; }
            var count = data.coas ? data.coas.length : 0;
            appendMessage({ role: 'system', content: count + ' COAs generated. Select one in the sidebar.' });
            if (data.coas) renderCoaCards(data.coas);
        })
        .catch(function (err) { appendMessage({ role: 'system', content: 'Simulation error: ' + err.message }); });
    }

    function chatViewRequirements() {
        if (!_activeIntakeSessionId) return;
        window.open(INTAKE_API + '/session/' + _activeIntakeSessionId, '_blank');
    }

    // ===================================================================
    // SECTION 11: RICOAS Features — PRD
    // ===================================================================

    function chatGeneratePRD() {
        if (!_activeIntakeSessionId) return;
        appendMessage({ role: 'system', content: 'Generating PRD...' });
        fetch(INTAKE_API + '/prd/' + _activeIntakeSessionId)
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (data.error) { appendMessage({ role: 'system', content: 'Error generating PRD: ' + data.error }); return; }
            var md = data.prd_markdown || '';
            if (!md) { appendMessage({ role: 'system', content: 'PRD generated but empty — add more requirements first.' }); return; }
            var blob = new Blob([md], { type: 'text/markdown;charset=utf-8' });
            var url = URL.createObjectURL(blob);
            var a = document.createElement('a');
            a.href = url; a.download = 'PRD-' + _activeIntakeSessionId + '.md';
            document.body.appendChild(a); a.click(); document.body.removeChild(a);
            URL.revokeObjectURL(url);
            var summary = 'PRD generated: ' + (data.total_requirements || 0) + ' requirements';
            if (data.has_coa) summary += ', COA included';
            if (data.has_decomposition) summary += ', SAFe decomposition included';
            summary += '. Downloaded.';
            appendMessage({ role: 'system', content: summary });
        })
        .catch(function (err) { appendMessage({ role: 'system', content: 'Error: ' + err.message }); });
    }

    function chatValidatePRD() {
        if (!_activeIntakeSessionId) return;
        appendMessage({ role: 'system', content: 'Running PRD quality validation (6 checks)...' });
        fetch(INTAKE_API + '/prd/' + _activeIntakeSessionId + '/validate')
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (data.error) { appendMessage({ role: 'system', content: 'Error: ' + data.error }); return; }
            var overall = (data.overall || 'unknown').toUpperCase();
            var score = data.overall_score || 0;
            var icon = overall === 'PASS' ? '\u2705' : overall === 'WARNING' ? '\u26A0\uFE0F' : '\u274C';
            var lines = [icon + ' PRD Quality: ' + overall + ' (' + score + '%)'];
            var checks = data.checks || [];
            for (var i = 0; i < checks.length; i++) {
                var c = checks[i];
                var sev = (c.severity || '').toUpperCase();
                var ci = sev === 'PASS' ? '\u2705' : sev === 'WARNING' ? '\u26A0\uFE0F' : '\u274C';
                lines.push(ci + ' ' + c.check.replace(/_/g, ' ') + ': ' + sev);
            }
            appendMessage({ role: 'system', content: lines.join('\n') });
        })
        .catch(function (err) { appendMessage({ role: 'system', content: 'Validation error: ' + err.message }); });
    }

    // ===================================================================
    // SECTION 11b: Send to Kanban — decompose plan into backlog tasks
    // ===================================================================

    function chatSendToKanban() {
        // Get the last assistant message content from the active context
        var contextId = _activeContextId;
        if (!contextId) {
            alert("No active chat context");
            return;
        }

        // Collect all assistant messages from the chat to build the plan
        var messages = document.querySelectorAll("#chat-messages .chat-msg-assistant .chat-msg-content");
        if (!messages.length) {
            alert("No assistant messages found");
            return;
        }

        // Use the last assistant message as the plan (most recent plan output)
        var lastMsg = messages[messages.length - 1];
        var planMarkdown = lastMsg.innerText || lastMsg.textContent || "";

        if (!planMarkdown.trim()) {
            alert("No plan content found in chat");
            return;
        }

        // Preview first
        ICDEV.fetchJSON("/api/kanban/preview-plan", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({markdown: planMarkdown})
        }).then(function(data) {
            if (!data || !data.count) {
                alert("Could not extract tasks from the plan. Try a more structured format (## Phase 1, ## Step 2, etc.)");
                return;
            }
            // Confirm with user
            var taskList = data.tasks.map(function(t, i) {
                return (i + 1) + ". [" + t.priority + "] " + t.title;
            }).join("\n");

            if (confirm("Send " + data.count + " tasks to Kanban backlog?\n\n" + taskList)) {
                ICDEV.fetchJSON("/api/kanban/from-plan", {
                    method: "POST",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({markdown: planMarkdown})
                }).then(function(result) {
                    if (result && result.tasks_created) {
                        alert(result.tasks_created + " tasks added to Kanban backlog!");
                        // Refresh kanban if on that page
                        if (typeof ICDEV.refreshKanban === "function") {
                            ICDEV.refreshKanban();
                        }
                    }
                });
            }
        });
    }

    // ===================================================================
    // SECTION 12: RICOAS Features — COA rendering & selection
    // ===================================================================

    function renderCoaCards(coas) {
        var section = document.getElementById('coa-section');
        var list = document.getElementById('coa-list');
        if (!section || !list) return;
        section.style.display = 'block';
        list.innerHTML = '';
        _coasLoaded = true;
        if (_coaTimer) { clearInterval(_coaTimer); _coaTimer = null; }

        var hasSelected = false;
        for (var i = 0; i < coas.length; i++) {
            if (coas[i].status === 'selected') hasSelected = true;
        }

        for (var j = 0; j < coas.length; j++) {
            var c = coas[j];
            var card = document.createElement('div');
            card.className = 'coa-card';
            if (c.status === 'selected') card.className += ' coa-card-selected';
            else if (c.status === 'rejected') card.className += ' coa-card-rejected';

            var header = document.createElement('div');
            header.className = 'coa-card-header';
            var name = document.createElement('span');
            name.className = 'coa-card-name';
            name.textContent = c.coa_name || c.coa_type || 'COA';
            header.appendChild(name);

            var tier = (c.boundary_tier || 'green').toLowerCase();
            var bdg = document.createElement('span');
            bdg.className = 'coa-tier-badge coa-tier-' + tier;
            bdg.textContent = tier.toUpperCase();
            header.appendChild(bdg);
            card.appendChild(header);

            var desc = document.createElement('div');
            desc.className = 'coa-card-desc';
            desc.textContent = c.description || '';
            card.appendChild(desc);

            var stats = document.createElement('div');
            stats.className = 'coa-card-stats';
            var timeline = c.timeline;
            if (typeof timeline === 'string') { try { timeline = JSON.parse(timeline); } catch (e) { timeline = null; } }
            var pis = c.timeline_pis || (timeline && timeline.timeline_pis) || '?';
            var piSpan = document.createElement('span');
            piSpan.textContent = pis + ' PIs';
            stats.appendChild(piSpan);

            var riskProfile = c.risk_profile;
            if (typeof riskProfile === 'string') { try { riskProfile = JSON.parse(riskProfile); } catch (e) { riskProfile = null; } }
            var risk = c.risk_level || (riskProfile && (riskProfile.overall_risk || riskProfile.risk_level)) || '?';
            var riskSpan = document.createElement('span');
            riskSpan.textContent = 'Risk: ' + risk;
            stats.appendChild(riskSpan);
            card.appendChild(stats);

            var actions = document.createElement('div');
            actions.className = 'coa-card-actions';
            if (c.status === 'selected') {
                var unsBtn = document.createElement('button');
                unsBtn.className = 'coa-select-btn';
                unsBtn.style.cssText = 'border-color:var(--status-red,#dc3545);color:var(--status-red,#dc3545);';
                unsBtn.textContent = 'Unselect';
                unsBtn.onclick = function () { chatUnselectCoa(); };
                actions.appendChild(unsBtn);
                var banner = document.getElementById('coa-selected-banner');
                var bannerName = document.getElementById('coa-selected-name');
                if (banner && bannerName) { bannerName.textContent = c.coa_name || c.coa_type; banner.style.display = 'block'; }
            } else if (c.status !== 'rejected') {
                var btn = document.createElement('button');
                btn.className = 'coa-select-btn';
                btn.textContent = 'Select';
                btn.setAttribute('data-coa-id', c.id);
                btn.onclick = (function (coaId) { return function () { chatSelectCoa(coaId); }; })(c.id);
                if (hasSelected) btn.disabled = true;
                actions.appendChild(btn);
            }
            card.appendChild(actions);
            list.appendChild(card);
        }
    }

    function chatSelectCoa(coaId) {
        if (!_activeIntakeSessionId) return;
        fetch(INTAKE_API + '/coas/' + _activeIntakeSessionId + '/select', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ coa_id: coaId, selected_by: 'Dashboard User', rationale: 'Selected via chat UI' })
        })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (data.error) { appendMessage({ role: 'system', content: 'Selection error: ' + data.error }); return; }
            appendMessage({ role: 'system', content: 'COA selected! Architecture and scope locked in for build.' });
            refreshCoas();
        })
        .catch(function (err) { appendMessage({ role: 'system', content: 'Selection error: ' + err.message }); });
    }

    function chatUnselectCoa() {
        if (!_activeIntakeSessionId) return;
        fetch(INTAKE_API + '/coas/' + _activeIntakeSessionId + '/unselect', { method: 'POST' })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (data.error) { appendMessage({ role: 'system', content: 'Unselect error: ' + data.error }); return; }
            appendMessage({ role: 'system', content: 'COA unselected.' });
            var banner = document.getElementById('coa-selected-banner');
            if (banner) banner.style.display = 'none';
            refreshCoas();
        })
        .catch(function (err) { appendMessage({ role: 'system', content: 'Unselect error: ' + err.message }); });
    }

    function refreshCoas() {
        if (!_activeIntakeSessionId) return;
        fetch(INTAKE_API + '/coas/' + _activeIntakeSessionId)
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (data.error) return;
            if (data.coas && data.coas.length > 0) renderCoaCards(data.coas);
        })
        .catch(function () {});
    }

    function startCoaPolling() {
        if (_coaTimer) return;
        _coaTimer = setInterval(function () {
            if (_coasLoaded || document.hidden) return;
            refreshCoas();
        }, 15000);
    }

    // ===================================================================
    // SECTION 13: RICOAS Features — Build Pipeline
    // ===================================================================

    function showBuildPipeline(phases, jobStatus, jobError) {
        var section = document.getElementById('build-pipeline-section');
        if (section) section.style.display = 'block';
        renderPipelinePhases(phases, jobStatus || 'running', jobError || '');
    }

    function renderPipelinePhases(phases, jobStatus, jobError) {
        var container = document.getElementById('build-pipeline-phases');
        var statusEl = document.getElementById('build-pipeline-status');
        if (!container) return;
        container.innerHTML = '';
        var allDone = true, hasError = false;

        for (var i = 0; i < phases.length; i++) {
            var p = phases[i];
            var row = document.createElement('div');
            row.className = 'build-phase build-phase-' + p.status;
            if (i > 0) {
                var connector = document.createElement('div');
                connector.className = 'build-phase-connector';
                if (p.status === 'done' || p.status === 'warning') connector.classList.add('build-phase-connector-done');
                else if (p.status === 'running') connector.classList.add('build-phase-connector-active');
                container.appendChild(connector);
            }
            var icon = document.createElement('span');
            icon.className = 'build-phase-icon';
            if (p.status === 'running') icon.className += ' build-phase-icon-pulse';
            icon.innerHTML = PHASE_ICONS[p.status] || PHASE_ICONS.pending;
            row.appendChild(icon);
            var text = document.createElement('div');
            text.className = 'build-phase-text';
            var nameSpan = document.createElement('span');
            nameSpan.className = 'build-phase-name';
            nameSpan.textContent = p.name;
            text.appendChild(nameSpan);
            if (p.detail) {
                var detail = document.createElement('span');
                detail.className = 'build-phase-detail';
                detail.textContent = p.detail;
                text.appendChild(detail);
            }
            row.appendChild(text);
            container.appendChild(row);
            if (p.status !== 'done' && p.status !== 'warning') allDone = false;
            if (p.status === 'error') hasError = true;
        }
        if (jobStatus === 'error') hasError = true;

        if (statusEl) {
            if (hasError) {
                var errMsg = 'Build encountered an error';
                if (jobError) errMsg += ': ' + jobError;
                statusEl.innerHTML = '<span class="build-status-error">' + errMsg.replace(/</g, '&lt;') + '</span>';
                if (_buildTimer) { clearInterval(_buildTimer); _buildTimer = null; }
            } else if (allDone) {
                statusEl.innerHTML = '<span class="build-status-done">Build pipeline complete</span>';
                if (_buildTimer) { clearInterval(_buildTimer); _buildTimer = null; }
                var doneActions = document.getElementById('build-done-actions');
                if (doneActions) doneActions.style.display = 'block';
            } else {
                statusEl.innerHTML = '<span class="build-status-running">Building...</span>';
            }
        }
    }

    function refreshBuild() {
        if (!_activeIntakeSessionId) return;
        fetch(INTAKE_API + '/build/' + _activeIntakeSessionId + '/status')
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (!data.phases || data.phases.length === 0) return;
            showBuildPipeline(data.phases, data.status, data.error);
            if (data.status === 'running') startBuildPolling();
        })
        .catch(function () {});
    }

    function startBuildPolling() {
        if (_buildTimer) clearInterval(_buildTimer);
        var emptyPolls = 0;
        _buildTimer = setInterval(function () {
            if (!_activeIntakeSessionId || document.hidden) return;
            fetch(INTAKE_API + '/build/' + _activeIntakeSessionId + '/status')
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (!data.phases || data.phases.length === 0) {
                    emptyPolls++;
                    if (emptyPolls >= 3) { if (_buildTimer) { clearInterval(_buildTimer); _buildTimer = null; } }
                    return;
                }
                emptyPolls = 0;
                renderPipelinePhases(data.phases, data.status, data.error);
                if (data.status === 'done' || data.status === 'error') {
                    if (_buildTimer) { clearInterval(_buildTimer); _buildTimer = null; }
                    if (data.status === 'done') appendMessage({ role: 'system', content: 'Build pipeline complete! Project is ready.' });
                }
            })
            .catch(function () {});
        }, 2000);
    }

    // Post-build actions
    function chatViewProject() {
        if (!_activeIntakeSessionId) return;
        fetch(INTAKE_API + '/build/' + _activeIntakeSessionId + '/project')
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (data.project_id) window.open('/projects/' + data.project_id, '_blank');
            else appendMessage({ role: 'system', content: 'No project found for this session.' });
        })
        .catch(function (err) { appendMessage({ role: 'system', content: 'Error: ' + err.message }); });
    }

    function chatRunTests() {
        if (!_activeIntakeSessionId) return;
        appendMessage({ role: 'system', content: 'Starting test suite...' });
        fetch(INTAKE_API + '/test/' + _activeIntakeSessionId + '/start', { method: 'POST' })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (data.error) { appendMessage({ role: 'system', content: 'Error: ' + data.error }); return; }
            appendMessage({ role: 'system', content: 'Test pipeline started.' });
            var section = document.getElementById('build-pipeline-section');
            if (section) section.style.display = 'block';
            var header = section ? section.querySelector('h4') : null;
            if (header) header.textContent = 'Test Pipeline';
            renderPipelinePhases(data.phases || [], 'running', '');
            startTestPolling();
        })
        .catch(function (err) { appendMessage({ role: 'system', content: 'Error: ' + err.message }); });
    }

    function startTestPolling() {
        if (_testTimer) clearInterval(_testTimer);
        var emptyPolls = 0;
        _testTimer = setInterval(function () {
            if (!_activeIntakeSessionId || document.hidden) return;
            fetch(INTAKE_API + '/test/' + _activeIntakeSessionId + '/status')
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (!data.phases || data.phases.length === 0) {
                    emptyPolls++;
                    if (emptyPolls >= 3) { if (_testTimer) { clearInterval(_testTimer); _testTimer = null; } }
                    return;
                }
                emptyPolls = 0;
                renderPipelinePhases(data.phases, data.status, data.error);
                if (data.status === 'done' || data.status === 'error') {
                    if (_testTimer) { clearInterval(_testTimer); _testTimer = null; }
                    var section = document.getElementById('build-pipeline-section');
                    var header = section ? section.querySelector('h4') : null;
                    if (header) header.textContent = 'Test Results';
                    var doneActions = document.getElementById('build-done-actions');
                    if (doneActions) doneActions.style.display = 'block';
                    if (data.status === 'done') appendMessage({ role: 'system', content: 'Test pipeline complete!' });
                }
            })
            .catch(function () {});
        }, 2000);
    }

    // ===================================================================
    // SECTION 14: Sidebar management
    // ===================================================================

    function showRicoasSidebar() {
        var rightSidebar = document.getElementById('right-sidebar');
        var layout = document.getElementById('chat-layout');
        var ricoasBtn = document.getElementById('btn-ricoas-toggle');
        if (rightSidebar) rightSidebar.classList.add('chat-right-panel--visible');
        if (layout) layout.classList.add('chat-layout--right-open');
        if (ricoasBtn) ricoasBtn.style.display = 'inline-block';
        // Switch to RICOAS tab
        var tab = document.getElementById('tab-ricoas');
        if (tab) tab.click();
    }

    function hideRicoasSidebar() {
        var ricoasBtn = document.getElementById('btn-ricoas-toggle');
        if (ricoasBtn) ricoasBtn.style.display = 'none';
        var rightSidebar = document.getElementById('right-sidebar');
        var layout = document.getElementById('chat-layout');
        if (rightSidebar) rightSidebar.classList.remove('chat-right-panel--visible');
        if (layout) layout.classList.remove('chat-layout--right-open');
    }

    function stopRicoasTimers() {
        if (_readinessTimer) { clearInterval(_readinessTimer); _readinessTimer = null; }
        if (_coaTimer) { clearInterval(_coaTimer); _coaTimer = null; }
        if (_buildTimer) { clearInterval(_buildTimer); _buildTimer = null; }
        if (_testTimer) { clearInterval(_testTimer); _testTimer = null; }
        _coasLoaded = false;
        _activeIntakeSessionId = null;
    }

    // ===================================================================
    // SECTION 15: Rendering
    // ===================================================================

    function renderContextList(contexts) {
        var container = document.getElementById('context-list');
        if (!container) return;
        if (!contexts.length) {
            container.innerHTML = '<div class="ctx-empty">No active chats.<br>Click <strong>+ New</strong> to start.</div>';
            return;
        }
        var html = '';
        for (var i = 0; i < contexts.length; i++) {
            var c = contexts[i];
            var isActive = c.context_id === _activeContextId;
            var dotClass = c.status === 'active' ? 'ctx-dot--active' : 'ctx-dot--closed';
            var isIntake = isIntakeContext(c.context_id);
            var titleText = escHtml(c.title || c.context_id) + (isIntake ? ' <span style="font-size:0.68rem;opacity:0.7;">[RICOAS]</span>' : '');
            var meta = c.message_count === 0 ? 'No messages yet' :
                (c.message_count + ' message' + (c.message_count !== 1 ? 's' : ''))
                + (c.is_processing ? ' · processing' : '')
                + (c.queue_depth > 0 ? ' · ' + c.queue_depth + ' queued' : '');
            html += '<div class="ctx-item' + (isActive ? ' active' : '') + '" data-ctx-id="' + escAttr(c.context_id) + '">'
                + '<span class="ctx-dot ' + dotClass + '"></span>'
                + '<div class="ctx-body">'
                + '<div class="ctx-title">' + titleText + '</div>'
                + '<div class="ctx-meta">' + meta + '</div>'
                + '</div>'
                + '<button class="ctx-delete-btn" data-del-id="' + escAttr(c.context_id) + '" title="Close context" aria-label="Close">×</button>'
                + '</div>';
        }
        container.innerHTML = html;

        container.querySelectorAll('.ctx-item').forEach(function (el) {
            el.addEventListener('click', function (e) {
                if (e.target.closest('.ctx-delete-btn')) return;
                switchContext(el.dataset.ctxId);
            });
        });
        container.querySelectorAll('.ctx-delete-btn').forEach(function (btn) {
            btn.addEventListener('click', function (e) {
                e.stopPropagation();
                deleteContext(btn.dataset.delId);
            });
        });
    }

    // Advisory content_type → display metadata mapping (D-CU-2)
    var ADVISORY_MAP = {
        'governance_advisory': { label: 'Governance', advisory: 'governance', icon: '\u26A0' },
        'bayesian_advisory':   { label: 'Bayesian Learning', advisory: 'bayesian', icon: '\uD83E\uDDE0' },
        'rag_attribution':     { label: 'Knowledge Sources', advisory: 'rag', icon: '\uD83D\uDCDA' },
        'kg_attribution':      { label: 'Knowledge Graph', advisory: 'kg',  icon: '\uD83D\uDD78' },
        'code_quality_advisory': { label: 'Code Quality', advisory: 'code_quality', icon: '\uD83D\uDD27' },
        'genesis_advisory':    { label: 'Genesis Insight', advisory: 'genesis', icon: '\uD83D\uDD2C' },
        'intake_advisory':     { label: 'Requirements', advisory: 'intake', icon: '\uD83D\uDCCB' },
        'context_health':      { label: 'Context Health', advisory: 'health', icon: '\u26A1' },
        'workflow_status':     { label: 'Workflow', advisory: 'workflow', icon: '\uD83D\uDD04' }
    };

    function renderMessages(messages) {
        var stream = document.getElementById('message-stream');
        if (!stream) return;
        if (!messages.length) {
            stream.innerHTML = '<div class="msg-bubble msg-bubble--system">Start a conversation by sending a message.</div>';
            return;
        }
        var html = '';
        for (var i = 0; i < messages.length; i++) html += renderMessageHtml(messages[i]);
        stream.innerHTML = html;
        stream.scrollTop = stream.scrollHeight;
        var wb = document.getElementById('chat-welcome-banner');
        if (wb) wb.style.display = (messages && messages.length > 0) ? 'none' : '';
        // Show Q&A widget on the last assistant message (if it has multiple questions)
        var last = messages[messages.length - 1];
        if (last && last.role === 'assistant') _injectQAWidget(stream, last.content || '');
    }

    function appendMessage(msg) {
        var stream = document.getElementById('message-stream');
        if (!stream) return;
        var placeholder = stream.querySelector('.msg-bubble--system');
        if (placeholder && stream.children.length === 1) stream.innerHTML = '';
        stream.innerHTML += renderMessageHtml(msg);
        stream.scrollTop = stream.scrollHeight;
        if (msg.role === 'assistant') _injectQAWidget(stream, msg.content || '');
    }

    function renderMessageHtml(msg) {
        var role = msg.role || 'user';
        var ct = msg.content_type || 'text';

        // Check if this is an advisory message type
        var advInfo = ADVISORY_MAP[ct] || ADVISORY_MAP[role];
        if (advInfo) {
            return '<div class="msg-bubble msg-bubble--advisory" data-advisory="' + advInfo.advisory + '">'
                + '<span class="msg-advisory-icon">' + advInfo.icon + '</span>'
                + '<span class="agent-name">' + advInfo.label + (msg.turn_number ? ' (#' + msg.turn_number + ')' : '') + '</span>'
                + '<div class="msg-markdown">' + renderContent(msg.content || '') + '</div>'
                + '</div>';
        }

        // Role-based bubble class
        var bubbleClass = 'msg-bubble';
        var label = 'You';
        if (role === 'assistant') { bubbleClass += ' msg-bubble--agent'; label = 'Agent'; }
        else if (role === 'system') { bubbleClass += ' msg-bubble--system'; label = 'System'; }
        else if (role === 'intervention') { bubbleClass += ' msg-bubble--intervention'; label = 'Intervention'; }
        else { bubbleClass += ' msg-bubble--user'; }

        var turnSuffix = msg.turn_number ? ' (#' + msg.turn_number + ')' : '';

        return '<div class="' + bubbleClass + '">'
            + '<div class="agent-name">' + label + turnSuffix + '</div>'
            + '<div class="msg-markdown">' + renderContent(msg.content || '') + '</div>'
            + '</div>';
    }

    function renderContent(text) {
        // Use marked.js if available, otherwise escape HTML and preserve whitespace
        if (typeof marked !== 'undefined') {
            try { return marked.parse(text); } catch (e) { /* fall through */ }
        }
        return '<span style="white-space:pre-wrap;word-break:break-word;">' + escHtml(text) + '</span>';
    }

    function appendTechniqueActivation(data) {
        var stream = document.getElementById('message-stream');
        if (!stream) return;
        var tech = data.technique || {};
        var qs = data.suggested_questions || [];
        var html = '<div class="msg-bubble msg-bubble--advisory" data-advisory="intake">';
        html += '<div class="agent-name">Technique Activated</div>';
        html += '<div class="msg-markdown"><strong>' + escHtml(tech.name || 'Technique') + '</strong>';
        if (tech.description) html += '<br><span class="intel-section-content">' + escHtml(tech.description) + '</span>';
        html += '</div>';
        if (qs.length > 0) {
            html += '<div class="intel-section-content" style="margin-top:6px;">Try asking:</div>';
            for (var i = 0; i < qs.length; i++) {
                html += '<button class="technique-question-btn action-card" data-q="' + escAttr(qs[i]) + '">' + escHtml(qs[i]) + '</button>';
            }
        }
        html += '</div>';
        stream.innerHTML += html;
        stream.scrollTop = stream.scrollHeight;

        // Wire up question buttons
        var btns = stream.querySelectorAll('.technique-question-btn');
        for (var j = 0; j < btns.length; j++) {
            btns[j].addEventListener('click', function () {
                var input = document.getElementById('message-input');
                if (input) { input.value = this.getAttribute('data-q'); input.focus(); }
            });
        }
    }

    function showTypingIndicator(visible) {
        var el = document.getElementById('typing-indicator');
        if (el) {
            if (visible) el.classList.add('typing-indicator--visible');
            else el.classList.remove('typing-indicator--visible');
        }
    }

    function updateInterventionBar(isProcessing) {
        var bar = document.getElementById('intervention-bar');
        if (bar) {
            if (isProcessing) bar.classList.add('chat-intervention--visible');
            else bar.classList.remove('chat-intervention--visible');
        }
        showTypingIndicator(isProcessing);
    }

    function updateTopStats(contexts) {
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
            chatApi('GET', '/diagnostics').then(function (d) {
                if (!d) return;
                setText('stat-active', d.active_contexts || 0);
                setText('stat-processing', d.processing || 0);
                setText('stat-queued', d.total_queued || 0);
                setText('stat-total', d.total_contexts || 0);
            });
        }
    }

    // ===================================================================
    // SECTION 16: Intake context creation (RICOAS bridge)
    // ===================================================================

    function createIntakeContext(options) {
        options = options || {};
        var cfg = window._CHAT_CONFIG || {};
        var goal = options.goal || cfg.wizardGoal || 'build';
        var role = options.role || cfg.wizardRole || 'developer';
        var classification = options.classification !== undefined ? options.classification : (cfg.wizardClassification || '');
        var frameworks = (options.frameworks || cfg.wizardFrameworks || '').split(',').filter(function (f) { return f.trim(); });

        // Step 1: Create intake session
        fetch(INTAKE_API + '/session', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                goal: goal,
                role: role,
                classification: classification,
                customer_name: 'Dashboard User',
                frameworks: frameworks,
                custom_role_name: cfg.customRoleName || '',
                custom_role_description: cfg.customRoleDesc || ''
            })
        })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (data.error) {
                console.error('[ICDEV] Intake session error:', data.error);
                appendMessage({ role: 'system', content: 'Could not start your session. Please refresh the page or create a new conversation. If this keeps happening, contact your administrator.' });
                return;
            }
            var intakeSessionId = data.session_id;
            var title = options.title || 'Intake: ' + goal;

            // Step 2: Create chat context
            chatApi('POST', '/contexts', {
                user_id: _userId,
                tenant_id: '',
                title: title,
                project_id: '',
                agent_model: options.agent_model || (window._CHAT_CONFIG && window._CHAT_CONFIG.defaultModel) || '',
                system_prompt: 'RICOAS intake session: ' + intakeSessionId
            }).then(function (ctx) {
                if (ctx.error) {
                    console.error('[ICDEV] Chat context creation error:', ctx.error);
                    appendMessage({ role: 'system', content: 'Could not start your session. Please refresh the page or create a new conversation. If this keeps happening, contact your administrator.' });
                    return;
                }
                // Step 3: Store mapping locally
                _intakeMap[ctx.context_id] = intakeSessionId;
                saveIntakeMappings();

                // Step 4: Persist to DB so /chat/<session_id> can restore this context
                chatApi('PATCH', '/' + ctx.context_id + '/link-intake', { intake_session_id: intakeSessionId });

                refreshContextList();
                switchContext(ctx.context_id);

                // Apply panel mode selected in the new context modal
                // Use setTimeout to let switchContext (async) complete the RICOAS sidebar first
                if (options.panelEnabled && options.panelPersonas && options.panelPersonas.length) {
                    var _pendingPersonas = options.panelPersonas;
                    setTimeout(function () {
                        // Sync sidebar chips BEFORE chatTogglePanel reads them via _syncPanelPersonas
                        var sidebarChips = document.querySelectorAll('#panel-persona-chips input[type="checkbox"]');
                        for (var sci = 0; sci < sidebarChips.length; sci++) {
                            sidebarChips[sci].checked = _pendingPersonas.indexOf(sidebarChips[sci].value) !== -1;
                            var chipLabel = sidebarChips[sci].parentElement;
                            if (chipLabel) chipLabel.classList.toggle('panel-chip--active', sidebarChips[sci].checked);
                        }
                        var toggleEl = document.getElementById('panel-mode-toggle');
                        if (toggleEl) toggleEl.checked = true;
                        chatTogglePanel(true);
                    }, 400);
                }

                // Show welcome message from intake
                if (data.message) {
                    var stream = document.getElementById('message-stream');
                    if (stream) stream.innerHTML = renderMessageHtml({ role: 'assistant', content: data.message });
                }

                // Update URL for backward compat
                history.replaceState(null, '', '/chat/' + intakeSessionId);
            });
        })
        .catch(function (err) {
            console.error('[ICDEV] Intake session connection error:', err);
            appendMessage({ role: 'system', content: 'Could not start your session. Please refresh the page or create a new conversation. If this keeps happening, contact your administrator.' });
        });
    }

    // Load an existing intake session into a context — checks localStorage, then DB, then creates
    function loadIntakeSession(sessionId, preferredContextId) {
        // 1. Preferred context from server-side lookup (autoContextId)
        if (preferredContextId) {
            _intakeMap[preferredContextId] = sessionId;
            saveIntakeMappings();
            refreshContextList().then(function () { switchContext(preferredContextId); });
            return;
        }
        // 2. Check localStorage mapping
        for (var ctxId in _intakeMap) {
            if (_intakeMap[ctxId] === sessionId) {
                refreshContextList().then(function () { switchContext(ctxId); });
                return;
            }
        }
        // 3. Search DB-loaded contexts for one whose intake_session_id matches
        refreshContextList().then(function (contexts) {
            for (var i = 0; i < (contexts || []).length; i++) {
                var c = contexts[i];
                if (c.intake_session_id === sessionId) {
                    _intakeMap[c.context_id] = sessionId;
                    saveIntakeMappings();
                    switchContext(c.context_id);
                    return;
                }
            }
            // 4. Nothing found — create a new context linked to the existing session
            chatApi('POST', '/contexts', {
                user_id: _userId,
                tenant_id: '',
                title: 'Intake: ' + sessionId.substring(0, 8),
                project_id: '',
                agent_model: (window._CHAT_CONFIG && window._CHAT_CONFIG.defaultModel) || '',
                system_prompt: 'RICOAS intake session: ' + sessionId
            }).then(function (ctx) {
                if (ctx.error) return;
                _intakeMap[ctx.context_id] = sessionId;
                saveIntakeMappings();
                chatApi('PATCH', '/' + ctx.context_id + '/link-intake', { intake_session_id: sessionId });
                refreshContextList();
                switchContext(ctx.context_id);
            });
        });
    }

    // ===================================================================
    // SECTION 17: Event bindings & init
    // ===================================================================

    function init() {
        // New context modal
        var modal = document.getElementById('new-context-modal');
        var btnNew = document.getElementById('btn-new-context');
        var btnCancel = document.getElementById('btn-cancel-modal');
        var btnCreate = document.getElementById('btn-create-context');

        if (btnNew) btnNew.addEventListener('click', function () {
            if (modal) modal.classList.add('chat-modal-overlay--visible');
        });
        if (btnCancel) btnCancel.addEventListener('click', function () {
            if (modal) modal.classList.remove('chat-modal-overlay--visible');
        });
        // ---------------------------------------------------------------
        // Canvas preset definitions (persona chips + system prompts)
        // ---------------------------------------------------------------
        var _CANVAS_PRESETS = {
            intake: [
                { label: 'Requirements Analyst', prompt: 'You are an experienced requirements analyst helping capture detailed software requirements using proven elicitation techniques. Ask focused questions one topic at a time.' },
                { label: 'Product Owner', prompt: 'You are a product owner focused on user value, business outcomes, and backlog prioritization. Help translate business needs into actionable requirements.' },
                { label: 'Business Analyst', prompt: 'You are a business analyst bridging stakeholder needs and technical teams, specializing in process analysis and functional decomposition.' },
                { label: 'Enterprise Architect', prompt: 'You are an enterprise architect capturing requirements with an eye toward system-of-systems integration, scalability, and long-term architectural fit.' },
            ],
            cam: [
                { label: 'Migration Architect', prompt: 'You are a cloud migration architect specializing in assessing legacy systems and designing phased migration strategies using the 7Rs framework (Rehost, Replatform, Repurchase, Refactor, Re-architect, Retire, Retain).' },
                { label: 'Modernization Lead', prompt: 'You are an application modernization lead focused on refactoring monoliths, strangler fig patterns, and incremental cloud-native transformation.' },
                { label: 'Legacy Assessment', prompt: 'You are a legacy system assessor identifying technical debt, EOL dependencies, and migration risk across enterprise portfolios. Prioritize by business impact and migration complexity.' },
                { label: 'Cloud Journey Advisor', prompt: 'You are a cloud journey advisor helping organizations understand migration paths, cost models, and organizational change management for cloud adoption.' },
            ],
            ndc: [
                { label: 'Network Architect', prompt: 'You are a network architect designing secure, resilient enterprise network topologies including segmentation, redundancy, and traffic flow.' },
                { label: 'Zero Trust Designer', prompt: 'You are a zero trust network architect implementing NIST SP 800-207 principles: verify explicitly, use least privilege access, assume breach.' },
                { label: 'SD-WAN Engineer', prompt: 'You are an SD-WAN and WAN optimization engineer designing hybrid connectivity solutions for distributed enterprise environments.' },
                { label: 'Campus Network', prompt: 'You are a campus network architect designing LAN/WLAN topologies with 802.1X, NAC, VLANs, and QoS for enterprise campuses.' },
            ],
            sdc: [
                { label: 'Security Architect', prompt: 'You are a security architect designing defense-in-depth architectures, threat models, and layered security control frameworks.' },
                { label: 'Threat Modeler', prompt: 'You are a threat modeling specialist using STRIDE, PASTA, and MITRE ATT&CK to systematically identify, prioritize, and mitigate security risks.' },
                { label: 'IAM Designer', prompt: 'You are an identity and access management architect specializing in OAuth 2.0, OIDC, SAML, RBAC/ABAC, and zero-trust identity architectures.' },
                { label: 'ISSO Advisor', prompt: 'You are an Information System Security Officer guiding teams through system authorization, continuous monitoring, POA&M management, and compliance activities.' },
            ],
            eda: [
                { label: 'Data Architect', prompt: 'You are an enterprise data architect designing scalable, governed data ecosystems including data lakes, warehouses, pipelines, and integration patterns.' },
                { label: 'Event Streaming Eng.', prompt: 'You are an event streaming architect specializing in Kafka-based architectures, event sourcing, CQRS, and real-time stream processing.' },
                { label: 'ETL/ELT Designer', prompt: 'You are a data integration specialist designing ETL/ELT pipelines, data quality frameworks, and transformation logic for enterprise data platforms.' },
                { label: 'Lakehouse Architect', prompt: 'You are a data lakehouse architect designing Delta Lake/Iceberg/Hudi table formats, medallion architectures, and unified batch/streaming platforms.' },
            ],
            ddc: [
                { label: 'Database Architect', prompt: 'You are a database architect designing schemas, indexing strategies, and data models for relational (PostgreSQL, SQL Server) and NoSQL (MongoDB, DynamoDB) systems.' },
                { label: 'Schema Designer', prompt: 'You are a data modeler specializing in entity-relationship design, normalization (1NF–5NF), and schema evolution strategies.' },
                { label: 'Performance Tuner', prompt: 'You are a database performance engineer focused on query optimization, indexing, partitioning, sharding, and read replica patterns.' },
                { label: 'Data Governance', prompt: 'You are a data governance architect defining master data management, data lineage, classification, and stewardship frameworks.' },
            ],
            pdc: [
                { label: 'Process Architect', prompt: 'You are a business process architect designing workflows, orchestration patterns, and BPMN 2.0 models for complex enterprise operations.' },
                { label: 'Workflow Designer', prompt: 'You are a workflow designer mapping process flows, swim-lane diagrams, and state machines to support automation and hand-off analysis.' },
                { label: 'Automation Engineer', prompt: 'You are a process automation engineer specializing in workflow orchestration, RPA, saga patterns, and integration choreography.' },
                { label: 'BPM Analyst', prompt: 'You are a BPM analyst identifying process inefficiencies, re-engineering opportunities, and KPI frameworks for business process improvement.' },
            ],
            bdc: [
                { label: 'Business Architect', prompt: 'You are a business architect designing capability maps, value streams, and organizational structures aligned to strategic business goals.' },
                { label: 'Value Stream Mapper', prompt: 'You are a lean practitioner mapping value streams to identify waste, bottlenecks, and optimization opportunities across end-to-end business flows.' },
                { label: 'Capability Planner', prompt: 'You are a business capability planner building capability heat maps, investment prioritization models, and strategic roadmaps.' },
                { label: 'Org Designer', prompt: 'You are an organizational design consultant structuring teams, reporting lines, and operating models to maximize delivery effectiveness.' },
            ],
            odc: [
                { label: 'Observability Eng.', prompt: 'You are an observability engineer designing three-pillar telemetry architectures (metrics, logs, traces) with OpenTelemetry, Prometheus, and distributed tracing.' },
                { label: 'SRE', prompt: 'You are a site reliability engineer defining SLIs, SLOs, error budgets, runbooks, and incident response frameworks to balance reliability and velocity.' },
                { label: 'SIEM Architect', prompt: 'You are a SIEM architect designing security monitoring pipelines, detection rules, correlation logic, and threat hunting workflows.' },
                { label: 'AIOps Designer', prompt: 'You are an AIOps architect designing ML-driven anomaly detection, intelligent alerting, and automated remediation pipelines for large-scale operations.' },
            ],
            idc: [
                { label: 'Infrastructure Architect', prompt: 'You are an infrastructure architect designing cloud-native, containerized environments using IaC, Kubernetes, and GitOps delivery patterns.' },
                { label: 'Platform Engineer', prompt: 'You are a platform engineer building internal developer platforms with automated provisioning, golden paths, CI/CD, and self-service infrastructure.' },
                { label: 'DevOps Engineer', prompt: 'You are a DevOps engineer designing CI/CD pipelines, deployment automation, blue/green strategies, and infrastructure-as-code with Terraform and Ansible.' },
                { label: 'FinOps Architect', prompt: 'You are a FinOps architect designing cloud cost management frameworks, tagging strategies, rightsizing policies, and showback/chargeback models.' },
            ],
        };

        var _REGIME_DESCRIPTIONS = {
            'il2':        'IL2 — Public / FedRAMP Low equivalent, suitable for non-CUI federal workloads.',
            'il4':        'IL4 — CUI (Controlled Unclassified Information) on DoD GovCloud.',
            'il5':        'IL5 — CUI requiring additional protection on dedicated DoD infrastructure.',
            'il6':        'IL6 — SECRET/SIPR, NSA Type 1 encryption, air-gapped CI/CD.',
            'fedramp-low':  'FedRAMP Low baseline (NIST SP 800-53 Low impact controls).',
            'fedramp-mod':  'FedRAMP Moderate baseline (NIST SP 800-53 Moderate, 325 controls).',
            'fedramp-high': 'FedRAMP High baseline (NIST SP 800-53 High impact, 421 controls).',
            'cmmc1':      'CMMC Level 1 — basic cyber hygiene (17 practices, FAR 52.204-21).',
            'cmmc2':      'CMMC Level 2 — advanced cyber hygiene (110 practices, NIST SP 800-171).',
            'cmmc3':      'CMMC Level 3 — expert (110+ practices, NIST SP 800-172 subset).',
            'nist-800-53': 'NIST SP 800-53 Rev 5 security and privacy controls.',
            'stig':       'DoD STIG/SRG technical security requirements and benchmarks.',
            'rmf':        'DoD Risk Management Framework (A&A process, DIACAP successor).',
            'stateramp':  'StateRAMP — FedRAMP-aligned framework for state and local government.',
            'itar':       'ITAR — International Traffic in Arms Regulations for defense articles.',
        };

        function _buildComplianceBlurb(il, regimes) {
            var parts = [];
            if (il && _REGIME_DESCRIPTIONS[il]) parts.push(_REGIME_DESCRIPTIONS[il]);
            for (var ri = 0; ri < regimes.length; ri++) {
                if (_REGIME_DESCRIPTIONS[regimes[ri]]) parts.push(_REGIME_DESCRIPTIONS[regimes[ri]]);
            }
            if (!parts.length) return '';
            return '\n\n--- Compliance Requirements ---\n' + parts.join('\n');
        }

        var canvasSelect = document.getElementById('new-ctx-canvas');
        var intakeRow = document.getElementById('intake-checkbox-row');

        function _updateModalForCanvas(canvas) {
            var container = document.getElementById('ctx-preset-chips');
            if (!container) return;
            var presets = _CANVAS_PRESETS[canvas] || [];
            container.innerHTML = '';
            for (var pi = 0; pi < presets.length; pi++) {
                (function (preset) {
                    var chip = document.createElement('button');
                    chip.type = 'button';
                    chip.className = 'ctx-preset-chip';
                    chip.textContent = preset.label;
                    chip.addEventListener('click', function () {
                        var chips = container.querySelectorAll('.ctx-preset-chip');
                        for (var ci = 0; ci < chips.length; ci++) chips[ci].classList.remove('ctx-preset-chip--active');
                        this.classList.add('ctx-preset-chip--active');
                        var ta = document.getElementById('new-ctx-prompt');
                        if (ta) ta.value = preset.prompt;
                    });
                    container.appendChild(chip);
                }(presets[pi]));
            }
            // Show/hide intake checkbox
            if (intakeRow) intakeRow.style.display = canvas === 'intake' ? '' : 'none';
            // Show/hide expert panel section
            var panelModeRow = document.getElementById('panel-mode-row');
            if (panelModeRow) panelModeRow.style.display = canvas === 'intake' ? '' : 'none';
        }

        // Wire compliance env radio toggle
        var envRadios = document.querySelectorAll('input[name="ctx-env"]');
        var govRows = document.getElementById('ctx-gov-rows');
        for (var ei = 0; ei < envRadios.length; ei++) {
            envRadios[ei].addEventListener('change', function () {
                if (govRows) govRows.style.display = this.value === 'gov' ? '' : 'none';
            });
        }

        // Wire IL chip active class via :has() fallback for older browsers
        var ilRadios = document.querySelectorAll('input[name="ctx-il"]');
        for (var ii = 0; ii < ilRadios.length; ii++) {
            ilRadios[ii].addEventListener('change', function () {
                var chips = document.querySelectorAll('.ctx-il-chip');
                for (var ci = 0; ci < chips.length; ci++) chips[ci].classList.remove('ctx-il-chip--selected');
                if (this.parentElement) this.parentElement.classList.add('ctx-il-chip--selected');
            });
        }

        // Wire canvas change → update presets
        if (canvasSelect) {
            canvasSelect.addEventListener('change', function () {
                _updateModalForCanvas(this.value);
            });
        }

        // Init presets for default canvas (intake)
        _updateModalForCanvas('intake');

        // Wire expert panel toggle in modal
        var modalPanelEnabled = document.getElementById('modal-panel-enabled');
        var modalPanelPersonas = document.getElementById('modal-panel-personas');
        if (modalPanelEnabled) {
            modalPanelEnabled.addEventListener('change', function () {
                if (modalPanelPersonas) modalPanelPersonas.style.display = this.checked ? 'flex' : 'none';
            });
        }
        // Wire persona chip active class in modal (since chip inputs are visually hidden)
        if (modalPanelPersonas) {
            var mpChipInputs = modalPanelPersonas.querySelectorAll('input[type="checkbox"]');
            mpChipInputs.forEach(function (cb) {
                cb.addEventListener('change', function () {
                    var chip = cb.closest('.panel-chip');
                    if (chip) chip.classList.toggle('panel-chip--active', cb.checked);
                });
            });
        }

        function _readCompliance() {
            var envEl = document.querySelector('input[name="ctx-env"]:checked');
            var isGov = envEl && envEl.value === 'gov';
            var ilEl = document.querySelector('input[name="ctx-il"]:checked');
            var il = isGov ? (ilEl ? ilEl.value : 'il4') : '';
            var regimes = [];
            if (isGov) {
                var checked = document.querySelectorAll('.ctx-regime-chip input:checked');
                for (var ri = 0; ri < checked.length; ri++) regimes.push(checked[ri].value);
            }
            return { il: il, regimes: regimes, isGov: isGov };
        }

        function _resetModal() {
            var titleEl = document.getElementById('new-ctx-title');
            var promptEl = document.getElementById('new-ctx-prompt');
            var intakeEl = document.getElementById('new-ctx-intake');
            var commercialEl = document.getElementById('ctx-env-commercial');
            var il4El = document.querySelector('input[name="ctx-il"][value="il4"]');
            if (titleEl) titleEl.value = '';
            if (promptEl) promptEl.value = '';
            if (intakeEl) intakeEl.checked = true;
            if (canvasSelect) canvasSelect.value = 'intake';
            if (commercialEl) { commercialEl.checked = true; }
            if (govRows) govRows.style.display = 'none';
            if (il4El) {
                il4El.checked = true;
                var ilChips = document.querySelectorAll('.ctx-il-chip');
                for (var ci = 0; ci < ilChips.length; ci++) ilChips[ci].classList.remove('ctx-il-chip--selected');
                if (il4El.parentElement) il4El.parentElement.classList.add('ctx-il-chip--selected');
            }
            var regimeChecks = document.querySelectorAll('.ctx-regime-chip input');
            for (var ri = 0; ri < regimeChecks.length; ri++) regimeChecks[ri].checked = false;
            // Reset expert panel
            var mpEnabled = document.getElementById('modal-panel-enabled');
            var mpPersonas = document.getElementById('modal-panel-personas');
            if (mpEnabled) mpEnabled.checked = false;
            if (mpPersonas) mpPersonas.style.display = 'none';
            var mpChips = document.querySelectorAll('#modal-panel-personas input[type="checkbox"]');
            for (var pi = 0; pi < mpChips.length; pi++) mpChips[pi].checked = mpChips[pi].value === 'developer' || mpChips[pi].value === 'analyst';
            _updateModalForCanvas('intake');
        }

        if (btnCreate) btnCreate.addEventListener('click', function () {
            var title = document.getElementById('new-ctx-title').value.trim();
            var model = document.getElementById('new-ctx-model').value;
            var prompt = document.getElementById('new-ctx-prompt').value.trim();
            var canvasMode = canvasSelect ? canvasSelect.value : 'intake';
            var comp = _readCompliance();
            var isIntake = canvasMode === 'intake' && document.getElementById('new-ctx-intake').checked;

            if (canvasMode !== 'intake') {
                // Design canvas — append compliance blurb to system prompt
                var compBlurb = _buildComplianceBlurb(comp.il, comp.regimes);
                var sysPrompt = (prompt + compBlurb).trim();
                createContext({ title: title || (canvasMode.toUpperCase() + ' Chat'), agent_model: model, system_prompt: sysPrompt }).then(function (ctx) {
                    if (ctx && ctx.context_id) setContextCanvasType(ctx.context_id, canvasMode);
                });
            } else if (isIntake) {
                var mpEl = document.getElementById('modal-panel-enabled');
                var panelEnabled = mpEl ? mpEl.checked : false;
                var panelPersonas = [];
                if (panelEnabled) {
                    var panelChecks = document.querySelectorAll('#modal-panel-personas input[type="checkbox"]:checked');
                    for (var pci = 0; pci < panelChecks.length; pci++) panelPersonas.push(panelChecks[pci].value);
                    if (!panelPersonas.length) panelPersonas = ['developer', 'analyst'];
                }
                createIntakeContext({ title: title, agent_model: model, classification: comp.il, panelEnabled: panelEnabled, panelPersonas: panelPersonas });
            } else {
                var compBlurbR = _buildComplianceBlurb(comp.il, comp.regimes);
                createContext({ title: title, agent_model: model, system_prompt: (prompt + compBlurbR).trim() });
            }
            if (modal) modal.classList.remove('chat-modal-overlay--visible');
            _resetModal();
        });

        // Send message
        var btnSend = document.getElementById('btn-send');
        var msgInput = document.getElementById('message-input');
        if (btnSend) btnSend.addEventListener('click', function () { sendMessage(); });
        if (msgInput) msgInput.addEventListener('keydown', function (e) {
            if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
        });

        // Intervene
        var btnIntervene = document.getElementById('btn-intervene');
        var intInput = document.getElementById('intervention-input');
        if (btnIntervene) btnIntervene.addEventListener('click', function () {
            intervene(_activeContextId, intInput.value.trim());
        });
        if (intInput) intInput.addEventListener('keydown', function (e) {
            if (e.key === 'Enter') { e.preventDefault(); intervene(_activeContextId, intInput.value.trim()); }
        });

        // Close context
        var btnClose = document.getElementById('btn-close-context');
        if (btnClose) btnClose.addEventListener('click', function () {
            if (_activeContextId) closeContext(_activeContextId);
        });

        // RICOAS sidebar toggle
        var btnRicoas = document.getElementById('btn-ricoas-toggle');
        if (btnRicoas) btnRicoas.addEventListener('click', function () {
            var ricoas = document.getElementById('ricoas-sidebar');
            if (ricoas) {
                var visible = ricoas.style.display !== 'none';
                ricoas.style.display = visible ? 'none' : 'block';
            }
        });

        // File upload — label[for=chat-file-input] opens dialog natively; only wire change event
        var fileInput = document.getElementById('chat-file-input');
        if (fileInput) {
            fileInput.addEventListener('change', function () {
                if (fileInput.files.length > 0) uploadFiles(fileInput.files);
                fileInput.value = '';
            });
        }

        // Drag-and-drop on message stream + input area
        var dropZones = [
            document.getElementById('message-stream'),
            document.getElementById('input-area')
        ];
        dropZones.forEach(function (zone) {
            if (!zone) return;
            zone.addEventListener('dragenter', function (e) {
                e.preventDefault();
                zone.classList.add('chat-drop-active');
            });
            zone.addEventListener('dragover', function (e) {
                e.preventDefault();
                zone.classList.add('chat-drop-active');
            });
            zone.addEventListener('dragleave', function (e) {
                if (!zone.contains(e.relatedTarget)) zone.classList.remove('chat-drop-active');
            });
            zone.addEventListener('drop', function (e) {
                e.preventDefault();
                zone.classList.remove('chat-drop-active');
                if (e.dataTransfer.files.length > 0) uploadFiles(e.dataTransfer.files);
            });
        });

        // Initial load — only treat wizardGoal as a wizard flow when URL query params
        // are present (e.g. /chat?goal=build). Without params, the server renders a
        // default goal; in that case fall through to auto-select existing contexts.
        var cfg = window._CHAT_CONFIG || {};
        var hasUrlParams = window.location.search && window.location.search.length > 1;
        if (cfg.sessionId) {
            loadIntakeSession(cfg.sessionId, cfg.autoContextId || null);
        } else if (cfg.wizardCanvas && hasUrlParams) {
            // Deep link: /chat?canvas=cam — auto-create a canvas-mode context
            var canvasMode = cfg.wizardCanvas;
            var canvasLabels2 = { cam:'Migration Analysis', ndc:'Network Design', sdc:'Security Design', eda:'Data Architecture', ddc:'Database Design', pdc:'Process Design', odc:'Observability', idc:'Infrastructure' };
            refreshContextList();
            createContext({ title: canvasLabels2[canvasMode] || canvasMode.toUpperCase() + ' Chat' }).then(function (ctx) {
                if (ctx && ctx.context_id) {
                    setContextCanvasType(ctx.context_id, canvasMode);
                }
            });
        } else if (cfg.wizardGoal && hasUrlParams) {
            refreshContextList();
            createIntakeContext({});
        } else {
            // Auto-select: prefer an existing intake (RICOAS) context; if none, create one
            refreshContextList().then(function (contexts) {
                if (contexts && contexts.length > 0) {
                    // Find the first intake context in the list
                    var intakeCtx = null;
                    for (var i = 0; i < contexts.length; i++) {
                        if (isIntakeContext(contexts[i].context_id)) {
                            intakeCtx = contexts[i];
                            break;
                        }
                    }
                    if (intakeCtx) {
                        switchContext(intakeCtx.context_id);
                    } else {
                        // Existing contexts are all regular — auto-create intake so RICOAS is available
                        createIntakeContext({});
                    }
                } else {
                    // No contexts at all — create a fresh intake context
                    createIntakeContext({});
                }
            });
        }
    }

    // ===================================================================
    // SECTION 18: Namespace exports
    // ===================================================================

    ns.chatGeneratePlan = chatGeneratePlan;
    ns.chatForceStartBuild = chatForceStartBuild;
    ns.chatTogglePanel = chatTogglePanel;
    ns.chatExport = chatExport;
    ns.chatTriggerBuild = chatTriggerBuild;
    ns.chatRunSimulation = chatRunSimulation;
    ns.chatViewRequirements = chatViewRequirements;
    ns.chatGeneratePRD = chatGeneratePRD;
    ns.chatValidatePRD = chatValidatePRD;
    ns.chatSendToKanban = chatSendToKanban;
    ns.chatSelectCoa = chatSelectCoa;
    ns.chatUnselectCoa = chatUnselectCoa;
    ns.chatViewProject = chatViewProject;
    ns.chatRunTests = chatRunTests;
    ns.chatActivateTechnique = activateTechnique;
    ns.chatDeactivateTechnique = deactivateTechnique;

    // RAG + KG document management
    ns.uploadFiles = uploadFiles;
    ns.refreshDocumentList = refreshDocumentList;
    ns.updateKgAttribution = updateKgAttribution;

    // ── Tasks Panel (Kanban-Chat integration) ────────────────────────────
    var _tasksCtxId = null;
    var _tasksTimer = null;

    var STATUS_COLORS = {
        done: '#4caf50', in_progress: '#4a90e2', scheduled: '#f39c12',
        suggested: '#9b59b6', failed: '#e74c3c'
    };
    var STATUS_LABELS = {
        done: 'Done', in_progress: 'Running', scheduled: 'Queued',
        suggested: 'Suggested', failed: 'Failed'
    };

    function loadContextTasks(ctxId) {
        _tasksCtxId = ctxId;
        if (_tasksTimer) clearInterval(_tasksTimer);
        fetchAndRenderTasks(ctxId);
        _tasksTimer = setInterval(function() {
            if (_tasksCtxId === ctxId) fetchAndRenderTasks(ctxId);
            else clearInterval(_tasksTimer);
        }, 8000);
    }

    function fetchAndRenderTasks(ctxId) {
        fetch('/api/chat/' + ctxId + '/tasks')
            .then(function(r) { return r.ok ? r.json() : null; })
            .then(function(data) {
                if (!data) return;
                renderTasksList(data.tasks || []);
            })
            .catch(function() {});
    }

    function renderTasksList(tasks) {
        var listEl = document.getElementById('tasks-list');
        var emptyEl = document.getElementById('tasks-empty');
        var badge = document.getElementById('tasks-badge');
        if (!listEl) return;

        if (!tasks.length) {
            listEl.innerHTML = '';
            if (emptyEl) emptyEl.style.display = 'block';
            if (badge) badge.style.display = 'none';
            return;
        }
        if (emptyEl) emptyEl.style.display = 'none';

        // Badge count of non-done tasks
        var pending = tasks.filter(function(t) { return t.status !== 'done'; }).length;
        if (badge) {
            badge.textContent = pending;
            badge.style.display = pending > 0 ? 'inline-flex' : 'none';
        }

        listEl.innerHTML = tasks.map(function(t) {
            var color = STATUS_COLORS[t.status] || '#888';
            var label = STATUS_LABELS[t.status] || t.status;
            var typeIcon = t.task_type === 'test' ? '&#x2713;' :
                           t.task_type === 'chore' ? '&#x2699;' : '&#x25B6;';
            return '<div class="chat-task-item chat-task-item--' + t.status + '">' +
                   '<div class="chat-task-item__header">' +
                   '<span class="chat-task-badge" style="background:' + color + '22;color:' + color + ';border-color:' + color + '44;">' + label + '</span>' +
                   '<span class="chat-task-type-icon">' + typeIcon + '</span>' +
                   '</div>' +
                   '<div class="chat-task-item__title">' + escapeHtml(t.title) + '</div>' +
                   (t.depends_on_task_id ? '<div class="chat-task-dep">depends on ' + escapeHtml(t.depends_on_task_id) + '</div>' : '') +
                   '</div>';
        }).join('');
    }

    function escapeHtml(s) {
        return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
    }

    // V&V chain button
    document.addEventListener('DOMContentLoaded', function() {
        var vvBtn = document.getElementById('btn-vv-chain');
        if (vvBtn) {
            vvBtn.addEventListener('click', function() {
                if (!_tasksCtxId) { alert('Select a chat context first.'); return; }
                var canvas = prompt('Canvas name (e.g. govlift, network, fathomdesk):', '') || '';
                vvBtn.disabled = true;
                vvBtn.textContent = 'Queuing...';
                fetch('/api/chat/' + _tasksCtxId + '/vv-chain', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({canvas: canvas})
                }).then(function(r) { return r.json(); }).then(function(d) {
                    vvBtn.disabled = false;
                    vvBtn.innerHTML = '+ V&amp;V Chain';
                    fetchAndRenderTasks(_tasksCtxId);
                    // Switch to tasks tab
                    var tabEl = document.getElementById('tab-tasks');
                    if (tabEl) tabEl.click();
                }).catch(function() {
                    vvBtn.disabled = false;
                    vvBtn.innerHTML = '+ V&amp;V Chain';
                });
            });
        }
    });

    // ===================================================================
    // Mode chip + slash bar
    // ===================================================================

    var _CANVAS_LABELS = {
        intake: null,  // no chip for default intake
        cam: { label: 'Migration', color: '#3949ab' },
        ndc: { label: 'Network', color: '#1976d2' },
        sdc: { label: 'Security', color: '#c62828' },
        eda: { label: 'Data Arch', color: '#388e3c' },
        ddc: { label: 'Database', color: '#f57c00' },
        pdc: { label: 'Process', color: '#7b1fa2' },
        bdc: { label: 'Business', color: '#0097a7' },
        odc: { label: 'Observability', color: '#5c6bc0' },
        idc: { label: 'Infrastructure', color: '#455a64' }
    };
    var _canvasCmdsCache = {};

    function updateModeChip(canvasType) {
        var chip = document.getElementById('chat-mode-chip');
        if (!chip) return;
        var info = _CANVAS_LABELS[canvasType];
        if (!info) {
            chip.style.display = 'none';
            return;
        }
        chip.textContent = info.label;
        chip.style.display = 'inline-block';
        chip.style.background = info.color;
        chip.style.color = '#fff';
        chip.title = 'Canvas mode: ' + canvasType.toUpperCase() + ' — click to switch';
    }

    function updateSlashBar(canvasType) {
        var bar = document.getElementById('chat-slash-bar');
        var hint = document.getElementById('chat-slash-hint');
        if (!bar || !hint) return;
        if (!canvasType || canvasType === 'intake') {
            bar.style.display = 'none';
            return;
        }
        bar.style.display = 'block';
        if (_canvasCmdsCache[canvasType]) {
            _renderSlashHint(canvasType, _canvasCmdsCache[canvasType]);
        } else {
            fetch('/api/simulate/slash-commands?canvas_type=' + canvasType)
                .then(function (r) { return r.json(); })
                .then(function (d) {
                    _canvasCmdsCache[canvasType] = d.commands || [];
                    _renderSlashHint(canvasType, _canvasCmdsCache[canvasType]);
                })
                .catch(function () {});
        }
    }

    function _renderSlashHint(ct, cmds) {
        var hint = document.getElementById('chat-slash-hint');
        if (!hint) return;
        var parts = ['<strong>' + ct.toUpperCase() + ' commands:</strong>'];
        for (var i = 0; i < cmds.length; i++) {
            parts.push('<span style="color:var(--accent-blue-light,#6db3f8);text-decoration:underline dotted;font-family:monospace;cursor:pointer;" onclick="(function(c){var inp=document.getElementById(\'message-input\');if(inp){inp.value=c+\' \';inp.focus();}})(\''+cmds[i]+'\')">' + cmds[i] + '</span>');
        }
        hint.innerHTML = parts.join(' &bull; ');
    }

    // Wire slash autocomplete to textarea
    (function() {
        document.addEventListener('DOMContentLoaded', function() {
            var inp = document.getElementById('message-input');
            var dropdown = document.getElementById('chat-slash-dropdown');
            if (!inp || !dropdown) return;
            inp.addEventListener('input', function() {
                var val = inp.value.trim();
                if (!val.startsWith('/') || !_activeCanvasType || _activeCanvasType === 'intake') {
                    dropdown.style.display = 'none'; return;
                }
                var cmds = _canvasCmdsCache[_activeCanvasType] || [];
                var matches = cmds.filter(function(c) { return c.startsWith(val.toLowerCase()); });
                if (!matches.length) { dropdown.style.display = 'none'; return; }
                dropdown.innerHTML = matches.map(function(c) {
                    return '<div style="padding:0.4rem 1rem;cursor:pointer;font-size:0.85rem;color:var(--text-primary);" onmouseover="this.style.background=\'rgba(74,144,217,0.2)\'" onmouseout="this.style.background=\'\'" onclick="(function(){var i=document.getElementById(\'message-input\');if(i){i.value=\''+c+' \';i.focus();}document.getElementById(\'chat-slash-dropdown\').style.display=\'none\';})()">' + '<code>' + c + '</code></div>';
                }).join('');
                dropdown.style.display = 'block';
            });
            inp.addEventListener('blur', function() { setTimeout(function() { dropdown.style.display = 'none'; }, 150); });
        });
    })();

    // Mode chip click — open canvas switcher dropdown
    (function() {
        document.addEventListener('DOMContentLoaded', function() {
            var chip = document.getElementById('chat-mode-chip');
            if (!chip) return;
            chip.addEventListener('click', function() {
                if (!_activeContextId) return;
                var modes = ['intake','cam','ndc','sdc','eda','ddc','pdc','odc','idc'];
                var labels = { intake:'Requirements Intake', cam:'Migration (CAM)', ndc:'Network (NDC)', sdc:'Security (SDC)', eda:'Data Arch (EDA)', ddc:'Database (DDC)', pdc:'Process (PDC)', odc:'Observability (ODC)', idc:'Infrastructure (IDC)' };
                var choice = prompt('Switch canvas mode:\n' + modes.map(function(m,i){ return (i+1)+'. '+labels[m]; }).join('\n') + '\n\nEnter number or canvas code:');
                if (!choice) return;
                var idx = parseInt(choice) - 1;
                var mode = (idx >= 0 && idx < modes.length) ? modes[idx] : choice.toLowerCase().trim();
                if (modes.indexOf(mode) >= 0) {
                    setContextCanvasType(_activeContextId, mode);
                    if (mode !== 'intake') switchToCanvasContext(_activeContextId, mode);
                    else switchToRegularContext(_activeContextId);
                }
            });
        });
    })();

    // Multi-stream API
    ns.chatStreams = {
        createContext: createContext,
        switchContext: switchContext,
        sendMessage: sendMessage,
        intervene: intervene,
        pollContextState: pollContextState,
        refreshContextList: refreshContextList,
        closeContext: closeContext,
        loadContextTasks: loadContextTasks
    };

    window.ICDEV = ns;

    // Prompt chip click handler
    document.addEventListener('click', function(e) {
        if (!e.target.classList.contains('chat-welcome__prompt-chip')) return;
        var prompt = e.target.getAttribute('data-prompt');
        if (!prompt || !_activeContextId) return;
        var inp = document.getElementById('message-input');
        if (inp && !inp.disabled) {
            inp.value = prompt;
            inp.dispatchEvent(new Event('input'));
            sendMessage();
        }
    });

    // Init on DOM ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
