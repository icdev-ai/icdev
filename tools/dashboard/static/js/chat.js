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

    // RICOAS timers and state
    var _readinessTimer = null;
    var _coaTimer = null;
    var _coasLoaded = false;
    var _buildTimer = null;
    var _testTimer = null;
    var _turnCount = 0;
    var _activeTechniqueId = null;

    // Fast-track use case state
    var _fastTrackConfig = null;   // {skip_requirement_types, user_config, uc_label} when active
    var _fastTrackDone = false;    // prevents re-triggering on subsequent messages

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
            agent_model: options.agent_model || 'sonnet',
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
        chatApi('GET', '/contexts?user_id=' + encodeURIComponent(_userId) + '&include_closed=true')
            .then(function (data) {
                renderContextList(data.contexts || []);
                updateTopStats(data.contexts || []);
            });
    }

    function switchContext(ctxId) {
        _activeContextId = ctxId;
        if (!_contextVersions[ctxId]) _contextVersions[ctxId] = 0;

        // Reset fast-track state on context switch
        _fastTrackConfig = null;
        _fastTrackDone = false;

        // Highlight active in sidebar
        var items = document.querySelectorAll('.ctx-item');
        for (var i = 0; i < items.length; i++) {
            items[i].classList.toggle('active', items[i].dataset.ctxId === ctxId);
        }

        // Check if this is an intake context
        _activeIntakeSessionId = _intakeMap[ctxId] || null;

        if (_activeIntakeSessionId) {
            switchToIntakeContext(ctxId, _activeIntakeSessionId);
        } else {
            switchToRegularContext(ctxId);
        }
    }

    function switchToRegularContext(ctxId) {
        // Hide RICOAS sidebar, stop RICOAS timers
        hideRicoasSidebar();
        stopRicoasTimers();

        chatApi('GET', '/contexts/' + ctxId).then(function (ctx) {
            if (ctx.error) return;
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
            if (uploadBtn) uploadBtn.style.display = 'none';

            renderMessages(ctx.messages || []);
            updateInterventionBar(ctx.is_processing);
            startPolling(ctxId);
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
        // Reset fast-track state on context close
        _fastTrackConfig = null;
        _fastTrackDone = false;
        chatApi('POST', '/' + ctxId + '/close').then(function () {
            refreshContextList();
            setText('chat-title', 'Select or create a context');
            document.getElementById('message-input').disabled = true;
            document.getElementById('btn-send').disabled = true;
            document.getElementById('btn-close-context').style.display = 'none';
            _activeContextId = null;
            _activeIntakeSessionId = null;
            stopPolling();
            stopRicoasTimers();
            hideRicoasSidebar();
        });
    }

    // ===================================================================
    // SECTION 2: Messaging (routes to chat or intake API)
    // ===================================================================

    function sendMessage() {
        var inp = document.getElementById('message-input');
        var content = inp ? inp.value.trim() : '';
        if (!content || !_activeContextId) return;

        if (_activeIntakeSessionId) {
            sendIntakeMessage(content);
        } else {
            sendChatMessage(_activeContextId, content);
        }
        inp.value = '';
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

    function _setSendProcessing(isProcessing) {
        var btn = document.getElementById('btn-send');
        var inp = document.getElementById('message-input');
        if (btn) {
            btn.disabled = isProcessing;
            if (isProcessing) {
                btn.dataset.originalText = btn.innerText || btn.textContent || 'Send';
                btn.innerHTML = '<span class="send-spinner"></span>';
            } else {
                btn.innerText = btn.dataset.originalText || 'Send';
            }
        }
        if (inp) inp.disabled = isProcessing;
    }

    function sendIntakeMessage(content) {
        // Append user message immediately
        appendMessage({ role: 'user', content: content });

        // Use the styled typing indicator instead of an inline static div
        showTypingIndicator(true);
        _setSendProcessing(true);

        fetch(INTAKE_API + '/turn', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_id: _activeIntakeSessionId, message: content })
        })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            showTypingIndicator(false);
            _setSendProcessing(false);

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
            showTypingIndicator(false);
            _setSendProcessing(false);
            appendMessage({ role: 'system', content: 'Connection error: ' + err.message });
        });
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
    // SECTION 3: File Upload (RICOAS contexts only)
    // ===================================================================

    function uploadFiles(files) {
        if (!_activeIntakeSessionId) {
            appendMessage({ role: 'system', content: 'File upload requires a RICOAS intake context.' });
            return;
        }
        for (var i = 0; i < files.length; i++) {
            uploadSingleFile(files[i]);
        }
    }

    function uploadSingleFile(file) {
        appendMessage({ role: 'system', content: 'Uploading ' + file.name + '...' });
        var formData = new FormData();
        formData.append('session_id', _activeIntakeSessionId);
        formData.append('file', file);

        fetch(INTAKE_API + '/upload', { method: 'POST', body: formData })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (data.error) {
                appendMessage({ role: 'system', content: 'Upload failed: ' + data.error });
                return;
            }
            var msg = 'Uploaded ' + file.name;
            if (data.requirements_extracted > 0) msg += ' — extracted ' + data.requirements_extracted + ' requirement(s)';
            appendMessage({ role: 'system', content: msg });

            var docEl = document.getElementById('stat-documents');
            if (docEl) docEl.textContent = (parseInt(docEl.textContent, 10) || 0) + 1;
            refreshReadiness();
        })
        .catch(function (err) {
            appendMessage({ role: 'system', content: 'Upload error: ' + err.message });
        });
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

        var planBtn = document.getElementById('generate-plan-btn');
        var exportBtn = document.getElementById('export-btn');
        if (planBtn) planBtn.style.display = overall >= 0.7 ? 'block' : 'none';
        if (exportBtn) exportBtn.style.display = overall > 0 ? 'block' : 'none';
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

    // Shared selector constant so DOM drift is easier to detect
    var _AGENT_MSG_SELECTOR = "#message-stream .msg-bubble--agent .msg-markdown";

    function _extractPlanMarkdown() {
        // Try the live DOM first
        var messages = document.querySelectorAll(_AGENT_MSG_SELECTOR);
        if (messages.length) {
            var lastMsg = messages[messages.length - 1];
            var text = lastMsg.innerText || lastMsg.textContent || "";
            if (text.trim()) return text.trim();
        }
        // Fallback: concatenate all assistant messages in the DOM
        var all = document.querySelectorAll("#message-stream .msg-bubble--agent");
        var parts = [];
        for (var i = 0; i < all.length; i++) {
            var md = all[i].querySelector(".msg-markdown");
            if (md) {
                var t = (md.innerText || md.textContent || "").trim();
                if (t) parts.push(t);
            }
        }
        if (parts.length) return parts.join("\n\n");
        return null;
    }

    function chatSendToKanban() {
        var contextId = _activeContextId;
        if (!contextId) {
            alert("No active chat context");
            return;
        }

        var planMarkdown = _extractPlanMarkdown();

        // If nothing in DOM and we have an intake session, fetch conversation from API
        if (!planMarkdown && _activeIntakeSessionId) {
            fetch(INTAKE_API + '/session/' + _activeIntakeSessionId)
                .then(function(r) { return r.json(); })
                .then(function(data) {
                    if (data && data.messages && data.messages.length) {
                        var parts = [];
                        for (var i = 0; i < data.messages.length; i++) {
                            var m = data.messages[i];
                            if ((m.role === 'assistant' || m.role === 'analyst') && m.content) parts.push(m.content.trim());
                        }
                        if (parts.length) {
                            _sendPlanToKanban(parts.join("\n\n"), true);
                            return;
                        }
                    }
                    _sendRequirementsToKanban();
                })
                .catch(function(err) {
                    _sendRequirementsToKanban();
                });
            return;
        }

        if (!planMarkdown) {
            alert("No plan content found. Export requirements or generate a PRD first.");
            return;
        }

        _sendPlanToKanban(planMarkdown, true);
    }

    function _sendRequirementsToKanban() {
        if (!_activeIntakeSessionId) {
            alert("No plan content found. Export requirements or generate a PRD first.");
            return;
        }
        fetch(INTAKE_API + '/export/' + _activeIntakeSessionId, { method: 'POST' })
            .then(function(r) { return r.json(); })
            .then(function(data) {
                if (!data || !data.requirements || !data.requirements.length) {
                    alert("No requirements found. Have a conversation first to capture requirements.");
                    return;
                }
                var md = data.requirements.map(function(req, i) {
                    return (i + 1) + ". " + (req.raw_text || req.text || "Requirement");
                }).join("\n");
                _sendPlanToKanban(md, false);
            })
            .catch(function(err) {
                alert("Failed to load requirements: " + err.message);
            });
    }

    function _sendPlanToKanban(planMarkdown, allowRequirementsFallback) {
        // Preview first
        fetch("/api/kanban/preview-plan", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({markdown: planMarkdown})
        }).then(function(r) { return r.json(); }).then(function(data) {
            if (!data || !data.count) {
                if (allowRequirementsFallback && _activeIntakeSessionId) {
                    _sendRequirementsToKanban();
                    return;
                }
                alert("Could not extract tasks from the plan. Try a more structured format (## Phase 1, ## Step 2, etc.)");
                return;
            }
            // Confirm with user
            var taskList = data.tasks.map(function(t, i) {
                return (i + 1) + ". [" + t.priority + "] " + t.title;
            }).join("\n");

            if (confirm("Send " + data.count + " tasks to Kanban backlog?\n\n" + taskList)) {
                var payload = {markdown: planMarkdown};
                if (_activeIntakeSessionId) payload.session_id = _activeIntakeSessionId;
                fetch("/api/kanban/from-plan", {
                    method: "POST",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify(payload)
                }).then(function(r) { return r.json(); }).then(function(result) {
                    if (result && result.tasks_created) {
                        alert(result.tasks_created + " tasks added to Kanban backlog! Automated decomposition will begin shortly.");
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
        if (rightSidebar) {
            rightSidebar.classList.add('chat-right-panel--visible');
            rightSidebar.classList.add('chat-right-panel--open');
        }
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
        if (rightSidebar) {
            rightSidebar.classList.remove('chat-right-panel--visible');
            rightSidebar.classList.remove('chat-right-panel--open');
        }
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
            container.innerHTML = '<div style="padding: 16px; text-align: center; color: var(--text-muted); font-size: 0.85rem;">No chat contexts yet. Click + New to start.</div>';
            return;
        }
        var html = '';
        for (var i = 0; i < contexts.length; i++) {
            var c = contexts[i];
            var isActive = c.context_id === _activeContextId;
            var statusColor = c.status === 'active' ? 'var(--accent-green, #0a0)' : 'var(--text-muted)';
            var isIntake = isIntakeContext(c.context_id);
            var titleSuffix = isIntake ? ' [RICOAS]' : '';
            html += '<div class="ctx-item' + (isActive ? ' active' : '') + '" data-ctx-id="' + c.context_id + '" '
                + 'style="padding: 8px 12px; border-bottom: 1px solid var(--border-color); cursor: pointer;'
                + (isActive ? ' background: var(--bg-tertiary, #223);' : '') + '">'
                + '<div style="display: flex; justify-content: space-between; align-items: center; gap: 4px;">'
                + '<span style="font-size: 0.85rem; font-weight: 500; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">' + escHtml(c.title || c.context_id) + titleSuffix + '</span>'
                + '<span style="width: 8px; height: 8px; border-radius: 50%; background: ' + statusColor + '; display: inline-block; flex-shrink: 0;"></span>'
                + '<button class="ctx-item__delete" data-ctx-id="' + c.context_id + '" title="Delete context" tabindex="-1">&#x2715;</button>'
                + '</div>'
                + '<div style="font-size: 0.75rem; color: var(--text-muted); margin-top: 2px;">'
                + c.message_count + ' msgs'
                + (c.is_processing ? ' · processing' : '')
                + (c.queue_depth > 0 ? ' · ' + c.queue_depth + ' queued' : '')
                + '</div></div>';
        }
        container.innerHTML = html;

        var items = container.querySelectorAll('.ctx-item');
        for (var j = 0; j < items.length; j++) {
            items[j].addEventListener('click', (function (id) {
                return function () { switchContext(id); };
            })(items[j].dataset.ctxId));
        }

        var delBtns = container.querySelectorAll('.ctx-item__delete');
        for (var k = 0; k < delBtns.length; k++) {
            delBtns[k].addEventListener('click', (function (id) {
                return function (e) {
                    e.stopPropagation();
                    // Remove from DOM immediately
                    var item = container.querySelector('.ctx-item[data-ctx-id="' + id + '"]');
                    if (item) item.remove();
                    // Only clear active-context UI if this was the active context
                    if (id === _activeContextId) {
                        closeContext(id);
                    } else {
                        chatApi('POST', '/' + id + '/close');
                    }
                };
            })(delBtns[k].dataset.ctxId));
        }
    }

    // Advisory content_type → display metadata mapping (D-CU-2)
    var ADVISORY_MAP = {
        'governance_advisory': { label: 'Governance', advisory: 'governance', icon: '\u26A0' },
        'bayesian_advisory':   { label: 'Bayesian Learning', advisory: 'bayesian', icon: '\uD83E\uDDE0' },
        'rag_attribution':     { label: 'Knowledge Sources', advisory: 'rag', icon: '\uD83D\uDCDA' },
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
    }

    function appendMessage(msg) {
        var stream = document.getElementById('message-stream');
        if (!stream) return;
        var placeholder = stream.querySelector('.msg-bubble--system');
        if (placeholder && stream.children.length === 1) stream.innerHTML = '';
        stream.innerHTML += renderMessageHtml(msg);
        stream.scrollTop = stream.scrollHeight;
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
        var goal = options.uc_category || options.goal || cfg.wizardGoal || 'build';
        var role = options.role || cfg.wizardRole || 'developer';
        var classification = options.classification || cfg.wizardClassification || 'il4';
        var frameworks = (options.frameworks || cfg.wizardFrameworks || '').split(',').filter(function (f) { return f.trim(); });

        // Step 1: Create intake session — pass use case context so RICOAS starts informed
        var sessionBody = {
            goal: goal,
            role: role,
            classification: classification,
            customer_name: 'Dashboard User',
            frameworks: frameworks,
            custom_role_name: options.uc_label || cfg.customRoleName || '',
            custom_role_description: options.system_prompt || cfg.customRoleDesc || ''
        };
        // Pass fast-track context and template requirements if provided
        if (options.fast_track) {
            sessionBody.extra_context = {
                skip_requirement_types: options.skip_requirement_types || [],
                user_config: options.user_config || {},
                fast_track: true
            };
            sessionBody.template_requirements = options.template_requirements || [];
        }
        return fetch(INTAKE_API + '/session', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(sessionBody)
        })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (data.error) {
                appendMessage({ role: 'system', content: 'Error creating intake session: ' + data.error });
                return null;
            }
            var intakeSessionId = data.session_id;
            var title = options.title || 'Intake: ' + goal;

            // Step 2: Create chat context
            return chatApi('POST', '/contexts', {
                user_id: _userId,
                tenant_id: '',
                title: title,
                project_id: '',
                agent_model: options.agent_model || 'kimi-cloud',
                system_prompt: 'RICOAS intake session: ' + intakeSessionId
            }).then(function (ctx) {
                if (ctx.error) {
                    appendMessage({ role: 'system', content: 'Error creating chat context: ' + ctx.error });
                    return null;
                }
                // Step 3: Store mapping
                _intakeMap[ctx.context_id] = intakeSessionId;
                saveIntakeMappings();

                refreshContextList();
                switchContext(ctx.context_id);

                // Show intake welcome only when no use case seed_message will replace it
                if (data.message && !options.suppress_intake_welcome) {
                    var stream = document.getElementById('message-stream');
                    if (stream) stream.innerHTML = renderMessageHtml({ role: 'assistant', content: data.message });
                }

                // Update URL for backward compat
                history.replaceState(null, '', '/chat/' + intakeSessionId);

                // Expose intake_session_id on the returned ctx for fast-track trigger
                ctx.intake_session_id = intakeSessionId;
                return ctx;
            });
        })
        .catch(function (err) {
            appendMessage({ role: 'system', content: 'Connection error: ' + err.message });
            return null;
        });
    }

    // Load an existing intake session into a context
    function loadIntakeSession(sessionId) {
        // Check if we already have a context for this intake session
        for (var ctxId in _intakeMap) {
            if (_intakeMap[ctxId] === sessionId) {
                refreshContextList();
                switchContext(ctxId);
                return;
            }
        }
        // Create a new context for this existing intake session
        chatApi('POST', '/contexts', {
            user_id: _userId,
            tenant_id: '',
            title: 'Intake: ' + sessionId.substring(0, 8),
            project_id: '',
            agent_model: 'sonnet',
            system_prompt: 'RICOAS intake session: ' + sessionId
        }).then(function (ctx) {
            if (ctx.error) return;
            _intakeMap[ctx.context_id] = sessionId;
            saveIntakeMappings();
            refreshContextList();
            switchContext(ctx.context_id);
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
        if (btnCreate) btnCreate.addEventListener('click', function () {
            var title = document.getElementById('new-ctx-title').value.trim();
            var model = document.getElementById('new-ctx-model').value;
            var prompt = document.getElementById('new-ctx-prompt').value.trim();
            var isIntake = document.getElementById('new-ctx-intake').checked;

            if (isIntake) {
                createIntakeContext({ title: title, agent_model: model });
            } else {
                createContext({ title: title, agent_model: model, system_prompt: prompt });
            }
            if (modal) modal.classList.remove('chat-modal-overlay--visible');
            document.getElementById('new-ctx-title').value = '';
            document.getElementById('new-ctx-prompt').value = '';
            document.getElementById('new-ctx-intake').checked = false;
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

        // File upload
        var uploadBtn = document.getElementById('chat-upload-btn');
        var fileInput = document.getElementById('chat-file-input');
        if (uploadBtn && fileInput) {
            uploadBtn.addEventListener('click', function () { fileInput.click(); });
            fileInput.addEventListener('change', function () {
                if (fileInput.files.length > 0) uploadFiles(fileInput.files);
                fileInput.value = '';
            });
        }

        // Drag-and-drop on message stream
        var streamEl = document.getElementById('message-stream');
        if (streamEl) {
            streamEl.addEventListener('dragover', function (e) { e.preventDefault(); });
            streamEl.addEventListener('drop', function (e) {
                e.preventDefault();
                if (e.dataTransfer.files.length > 0) uploadFiles(e.dataTransfer.files);
            });
        }

        // Initial load
        refreshContextList();

        // Check for wizard params (auto-create intake context)
        var cfg = window._CHAT_CONFIG || {};
        if (cfg.sessionId) {
            // Resume existing intake session
            loadIntakeSession(cfg.sessionId);
        } else if (cfg.wizardGoal) {
            // Create new intake context from wizard
            createIntakeContext({});
        }
    }

    // ===================================================================
    // SECTION 18: Common Use Cases — catalog, deep links, edit modal
    // ===================================================================

    var _allUseCases = [];
    var _activeUseCase = null;
    var _ucContextMap = {};
    try { _ucContextMap = JSON.parse(localStorage.getItem('icdev_uc_ctx_map') || '{}'); } catch (e) {}
    var _ucEditId = null;

    // New state: category filter, compact mode, chain mode
    var _ucActiveCategory = '';
    var _ucCompact = true;
    var _chainMode = false;
    var _chainSelected = {};

    // Government persona archetype templates (F2a)
    var ARCHETYPE_TEMPLATES = {
        ko: 'You are a senior Contracting Officer (KO) with expertise in FAR/DFARS, acquisition planning, source selection, price/cost analysis, and contract administration. You assist government program offices and contractors in navigating federal procurement regulations, preparing solicitation documents, evaluating proposals, and managing contract performance. You are familiar with LPTA, best value, and other source selection methodologies, as well as streamlined acquisition authorities (SAP, OTs, BPAs).',
        isso: 'You are an Information System Security Officer (ISSO) with deep expertise in the Risk Management Framework (RMF), NIST SP 800-53 Rev 5, DISA STIGs, DIACAP-to-RMF transition, and continuous Authorization to Operate (cATO). You help system owners, ISSMs, and program managers navigate A&A documentation, control implementation, POAM management, and STIG compliance. You are familiar with eMASS, Xacta, and automated compliance tools.',
        zt: 'You are a Zero Trust Architecture (ZTA) specialist with expertise in NIST SP 800-207, DoD Zero Trust Strategy, CISA Zero Trust Maturity Model (ZTMM), and the 7 ZTA pillars: User, Device, Network, Application, Data, Automation & Orchestration, and Visibility & Analytics. You help organizations design identity-centric security architectures, deploy micro-segmentation, and achieve ZTA maturity levels aligned with federal mandates (EO 14028, M-22-09).',
        ito: 'You are a senior IT Operations Manager with expertise in ITSM (ITIL v4), service desk operations, infrastructure lifecycle management, patch management, capacity planning, and IT service continuity. You help agencies optimize their IT service delivery, reduce mean time to resolution (MTTR), and align IT operations with business objectives. You are familiar with ServiceNow, Jira, and government-specific IT frameworks.',
        sl: 'You are a program manager with deep experience in state and local government IT modernization, grant-funded programs (BEAD, SLIGP, E-Rate, ARPA, BRIC), and interoperability with federal systems. You help state and local agencies navigate federal grant requirements, procurement constraints, technology modernization, and data sharing agreements. You understand state-level legislative constraints, tribal government coordination, and regional broadband planning.',
        pm: 'You are a senior government program manager with expertise in SAFe Agile for government, Earned Value Management (EVM/ANSI-EIA-748), Integrated Baseline Reviews (IBR), the Federal Acquisition Regulation, and DoD 5000.87 software acquisition pathway. You help program offices plan and execute technology programs from inception to delivery, managing schedule, cost, scope, and stakeholder risk. You are familiar with PPBE, color reviews, and Congressional reporting requirements.'
    };

    // Category display names
    var CATEGORY_LABELS = {
        '': 'All',
        modernization: 'Modernization',
        budget: 'Budget',
        knowledge: 'Knowledge',
        acquisition: 'Acquisition',
        compliance_ato: 'Compliance/ATO',
        zero_trust: 'Zero Trust',
        it_operations: 'IT Ops',
        state_local: 'State/Local',
        general: 'General'
    };

    // ---- Category chips (F2b) ----

    function renderCategoryChips(useCases) {
        var container = document.getElementById('uc-category-chips');
        if (!container) return;
        var cats = {};
        for (var i = 0; i < useCases.length; i++) {
            var c = useCases[i].category || 'general';
            cats[c] = (cats[c] || 0) + 1;
        }
        var html = '<button class="uc-cat-chip' + (_ucActiveCategory === '' ? ' uc-cat-chip--active' : '') + '" data-cat="">All (' + useCases.length + ')</button>';
        Object.keys(cats).sort().forEach(function (cat) {
            var label = CATEGORY_LABELS[cat] || cat;
            html += '<button class="uc-cat-chip' + (_ucActiveCategory === cat ? ' uc-cat-chip--active' : '') + '" data-cat="' + escHtml(cat) + '">' + escHtml(label) + ' (' + cats[cat] + ')</button>';
        });
        container.innerHTML = html;
        container.querySelectorAll('.uc-cat-chip').forEach(function (btn) {
            btn.addEventListener('click', function () {
                setActiveCategory(btn.dataset.cat);
            });
        });
    }

    function setActiveCategory(cat) {
        _ucActiveCategory = cat;
        var filtered = cat ? _allUseCases.filter(function (uc) { return (uc.category || 'general') === cat; }) : _allUseCases;
        var q = (document.getElementById('usecase-search') || {}).value;
        if (q) filtered = filtered.filter(function (uc) { return uc.label.toLowerCase().includes(q.toLowerCase()) || (uc.description || '').toLowerCase().includes(q.toLowerCase()); });
        renderCategoryChips(_allUseCases);
        renderUseCases(filtered);
    }

    // ---- Compact/expanded toggle (F2d) ----

    function toggleUcCompact() {
        _ucCompact = !_ucCompact;
        var btn = document.getElementById('btn-uc-compact');
        if (btn) btn.classList.toggle('chat-uc-toolbar-btn--active', _ucCompact);
        var filtered = _ucActiveCategory
            ? _allUseCases.filter(function (uc) { return (uc.category || 'general') === _ucActiveCategory; })
            : _allUseCases;
        renderUseCases(filtered);
    }

    // ---- Chain mode (F2d) ----

    function enterChainMode() {
        _chainMode = true;
        _chainSelected = {};
        var bar = document.getElementById('uc-chain-bar');
        if (bar) bar.style.display = 'flex';
        updateChainCount();
        renderUseCases(_ucActiveCategory ? _allUseCases.filter(function (uc) { return (uc.category || 'general') === _ucActiveCategory; }) : _allUseCases);
    }

    function exitChainMode() {
        _chainMode = false;
        _chainSelected = {};
        var bar = document.getElementById('uc-chain-bar');
        if (bar) bar.style.display = 'none';
        renderUseCases(_ucActiveCategory ? _allUseCases.filter(function (uc) { return (uc.category || 'general') === _ucActiveCategory; }) : _allUseCases);
    }

    function updateChainCount() {
        var count = Object.keys(_chainSelected).length;
        var el = document.getElementById('uc-chain-count');
        if (el) el.textContent = count + ' selected';
    }

    function activateChain() {
        var ids = Object.keys(_chainSelected);
        if (!ids.length) { alert('Select at least one use case to chain.'); return; }
        var name = (document.getElementById('uc-chain-name') || {}).value || 'Chained Session';
        fetch(CHAT_API + '/chains', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({name: name, use_case_ids: ids})
        })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (!data.ok) { alert('Chain creation failed: ' + (data.error || 'unknown error')); return; }
            return fetch(CHAT_API + '/chains/' + data.chain_id + '/activate', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({user_id: _currentUserId || 'dashboard-user'})
            }).then(function (r) { return r.json(); }).then(function (act) {
                if (act.context_id) {
                    addMessage('system', '⛓ Chain activated — ' + data.requirement_count + ' merged requirements seeded into intake session.', null);
                    loadContexts();
                } else if (act.error) {
                    addMessage('system', '⚠ Chain activate: ' + act.error, null);
                }
            });
        })
        .catch(function (e) { alert('Chain error: ' + e.message); })
        .finally(function () { exitChainMode(); });
    }

    // ---- Export / Import (F2f) ----

    function exportUseCase(ucId) {
        window.location = CHAT_API + '/use-cases/' + encodeURIComponent(ucId) + '/export';
    }

    function openImportModal() {
        var m = document.getElementById('uc-import-modal');
        if (m) { m.style.display = 'flex'; document.getElementById('uc-import-result').textContent = ''; }
    }

    function closeImportModal() {
        var m = document.getElementById('uc-import-modal');
        if (m) m.style.display = 'none';
    }

    function submitImport() {
        var file = (document.getElementById('uc-import-file') || {}).files;
        if (!file || !file.length) { alert('Select a YAML bundle file first.'); return; }
        var overwrite = (document.getElementById('uc-import-overwrite') || {}).checked;
        var fd = new FormData();
        fd.append('file', file[0]);
        fd.append('overwrite', overwrite ? 'true' : 'false');
        fetch(CHAT_API + '/use-cases/import', {method: 'POST', body: fd})
            .then(function (r) { return r.json(); })
            .then(function (data) {
                var res = document.getElementById('uc-import-result');
                if (res) {
                    var msg = '';
                    if (data.imported && data.imported.length) msg += '✓ Imported: ' + data.imported.join(', ') + '. ';
                    if (data.skipped && data.skipped.length) msg += '⚠ Skipped: ' + data.skipped.map(function(s){return s.id;}).join(', ') + '. ';
                    if (data.errors && data.errors.length) msg += '✗ Errors: ' + data.errors.length + '.';
                    res.textContent = msg || 'No changes.';
                }
                loadUseCases();
            })
            .catch(function (e) {
                var res = document.getElementById('uc-import-result');
                if (res) res.textContent = 'Import failed: ' + e.message;
            });
    }

    // ---- Wizard (F2e) ----

    var _wizStep = 1;
    var _wizReqs = [];
    var _wizSteps = [];

    function openWizard(cloneFrom) {
        _wizStep = 1;
        _wizReqs = [];
        _wizSteps = [{step: 1, label: 'Scope & Discovery', description: 'Define scope and identify key assets', action: 'requirement_intake'},
                     {step: 2, label: 'Requirements Review', description: 'Review and validate pre-seeded requirements', action: 'review_requirements'},
                     {step: 3, label: 'Artifact Generation', description: 'Generate artifacts and documentation', action: 'generate_artifacts'},
                     {step: 4, label: 'Stakeholder Review', description: 'Submit for government stakeholder review', action: 'submit_review'},
                     {step: 5, label: 'Export & Close', description: 'Export final package and close use case', action: 'export_bundle'}];
        // Populate clone select
        var cloneSel = document.getElementById('uc-wiz-clone-from');
        if (cloneSel) {
            cloneSel.innerHTML = _allUseCases.map(function (uc) {
                return '<option value="' + escHtml(uc.id) + '">' + escHtml(uc.label) + '</option>';
            }).join('');
        }
        if (cloneFrom) {
            document.querySelector('input[name="wiz-start"][value="clone"]').checked = true;
            document.getElementById('uc-wiz-clone-wrap').style.display = '';
            if (cloneSel) cloneSel.value = cloneFrom;
            var src = _allUseCases.find(function(uc){ return uc.id === cloneFrom; });
            if (src) {
                setTimeout(function () {
                    var el = document.getElementById('uc-wiz-label'); if (el) el.value = src.label + ' (Copy)';
                    var ei = document.getElementById('uc-wiz-icon'); if (ei) ei.value = src.icon || '';
                    var ec = document.getElementById('uc-wiz-category'); if (ec) ec.value = src.category || 'general';
                    var eb = document.getElementById('uc-wiz-badge'); if (eb) eb.value = src.badge || '';
                    var ed = document.getElementById('uc-wiz-desc'); if (ed) ed.value = src.description || '';
                    var es = document.getElementById('uc-wiz-system'); if (es) es.value = src.system_prompt || '';
                    var ese = document.getElementById('uc-wiz-seed'); if (ese) ese.value = src.seed_message || '';
                    _wizReqs = JSON.parse(JSON.stringify(src.template_requirements || []));
                    _wizSteps = JSON.parse(JSON.stringify(src.workflow_steps || _wizSteps));
                }, 50);
            }
        }
        wizShowStep(1);
        var m = document.getElementById('uc-create-wizard');
        if (m) m.style.display = 'flex';
    }

    function closeWizard() {
        var m = document.getElementById('uc-create-wizard');
        if (m) m.style.display = 'none';
    }

    function wizShowStep(n) {
        _wizStep = n;
        for (var i = 1; i <= 5; i++) {
            var s = document.getElementById('uc-wiz-s' + i);
            if (s) s.style.display = (i === n) ? '' : 'none';
        }
        var stepNum = document.getElementById('uc-wiz-step-num');
        if (stepNum) stepNum.textContent = n;
        var prev = document.getElementById('uc-wiz-prev');
        var next = document.getElementById('uc-wiz-next');
        var finish = document.getElementById('uc-wiz-finish');
        if (prev) prev.style.display = (n > 1) ? '' : 'none';
        if (next) next.style.display = (n < 5) ? '' : 'none';
        if (finish) finish.style.display = (n === 5) ? '' : 'none';
        if (n === 4) renderWizReqList();
        if (n === 5) renderWizStepsList();
    }

    function renderWizReqList() {
        var container = document.getElementById('uc-wiz-req-list');
        if (!container) return;
        if (!_wizReqs.length) { container.innerHTML = '<div style="color:#8b949e;font-size:0.8rem">No requirements yet. Add some below.</div>'; return; }
        container.innerHTML = _wizReqs.map(function (r, idx) {
            return '<div style="display:flex;gap:6px;align-items:center">'
                + '<select style="width:90px;font-size:0.75rem" onchange="_wizReqs[' + idx + '].priority=this.value"><option' + (r.priority==='high'?' selected':'') + '>high</option><option' + (r.priority==='medium'?' selected':'') + '>medium</option><option' + (r.priority==='critical'?' selected':'') + '>critical</option><option' + (r.priority==='low'?' selected':'') + '>low</option></select>'
                + '<input style="flex:1;font-size:0.75rem" value="' + escHtml(r.text || '') + '" oninput="_wizReqs[' + idx + '].text=this.value" placeholder="Requirement text...">'
                + '<button style="color:#f85149;background:none;border:none;cursor:pointer" onclick="_wizReqs.splice(' + idx + ',1);renderWizReqList()">&#x2715;</button>'
                + '</div>';
        }).join('');
    }

    function renderWizStepsList() {
        var container = document.getElementById('uc-wiz-steps-list');
        if (!container) return;
        container.innerHTML = _wizSteps.map(function (s, idx) {
            return '<div style="display:flex;gap:6px;align-items:center">'
                + '<span style="font-size:0.72rem;color:#8b949e;width:20px">' + (idx+1) + '</span>'
                + '<input style="flex:1;font-size:0.75rem" value="' + escHtml(s.label || '') + '" oninput="_wizSteps[' + idx + '].label=this.value" placeholder="Step label...">'
                + '<input style="flex:2;font-size:0.75rem" value="' + escHtml(s.description || '') + '" oninput="_wizSteps[' + idx + '].description=this.value" placeholder="Description...">'
                + '<button style="color:#f85149;background:none;border:none;cursor:pointer" onclick="_wizSteps.splice(' + idx + ',1);renderWizStepsList()">&#x2715;</button>'
                + '</div>';
        }).join('');
    }

    function applyArchetype(key) {
        var tpl = ARCHETYPE_TEMPLATES[key];
        if (!tpl) return;
        var el = document.getElementById('uc-wiz-system');
        if (el) el.value = tpl;
    }

    function wizNext() {
        if (_wizStep === 1) {
            var startMode = document.querySelector('input[name="wiz-start"]:checked');
            if (startMode && startMode.value === 'clone') {
                var sel = document.getElementById('uc-wiz-clone-from');
                var src = sel && _allUseCases.find(function (uc) { return uc.id === sel.value; });
                if (src) {
                    setTimeout(function () {
                        var el;
                        el = document.getElementById('uc-wiz-label'); if (el && !el.value) el.value = src.label + ' (Copy)';
                        el = document.getElementById('uc-wiz-icon'); if (el && !el.value) el.value = src.icon || '';
                        el = document.getElementById('uc-wiz-category'); if (el) el.value = src.category || 'general';
                        el = document.getElementById('uc-wiz-badge'); if (el && !el.value) el.value = src.badge || '';
                        el = document.getElementById('uc-wiz-desc'); if (el && !el.value) el.value = src.description || '';
                        el = document.getElementById('uc-wiz-system'); if (el && !el.value) el.value = src.system_prompt || '';
                        el = document.getElementById('uc-wiz-seed'); if (el && !el.value) el.value = src.seed_message || '';
                        if (!_wizReqs.length) _wizReqs = JSON.parse(JSON.stringify(src.template_requirements || []));
                        if (_wizSteps.length <= 5) _wizSteps = JSON.parse(JSON.stringify(src.workflow_steps || _wizSteps));
                    }, 0);
                }
            }
        }
        if (_wizStep < 5) wizShowStep(_wizStep + 1);
    }

    function wizPrev() {
        if (_wizStep > 1) wizShowStep(_wizStep - 1);
    }

    function collectWizardPayload() {
        return {
            label: (document.getElementById('uc-wiz-label') || {}).value || '',
            icon: (document.getElementById('uc-wiz-icon') || {}).value || '⚙',
            category: (document.getElementById('uc-wiz-category') || {}).value || 'general',
            badge: (document.getElementById('uc-wiz-badge') || {}).value || '',
            description: (document.getElementById('uc-wiz-desc') || {}).value || '',
            system_prompt: (document.getElementById('uc-wiz-system') || {}).value || '',
            seed_message: (document.getElementById('uc-wiz-seed') || {}).value || '',
            ricoas: (document.getElementById('uc-wiz-ricoas') || {}).checked !== false,
            fast_track: (document.getElementById('uc-wiz-fast-track') || {}).checked !== false,
            boost_threshold: parseInt((document.getElementById('uc-wiz-boost') || {}).value || '70', 10),
            agent_model: (document.getElementById('uc-wiz-model') || {}).value || 'kimi-cloud',
            template_requirements: _wizReqs.filter(function (r) { return r.text; }),
            workflow_steps: _wizSteps.filter(function (s) { return s.label; }).map(function (s, i) { return Object.assign({}, s, {step: i+1}); }),
        };
    }

    function submitWizard() {
        var payload = collectWizardPayload();
        if (!payload.label) { alert('Name is required.'); wizShowStep(2); return; }
        fetch(CHAT_API + '/use-cases', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload)
        })
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (data.ok) {
                closeWizard();
                loadUseCases();
            } else {
                alert('Create failed: ' + (data.error || 'unknown error'));
            }
        })
        .catch(function (e) { alert('Error: ' + e.message); });
    }

    // ---- Catalog load / render ----

    function loadUseCases() {
        fetch(CHAT_API + '/use-cases')
            .then(function (r) { return r.ok ? r.json() : { use_cases: [] }; })
            .then(function (data) {
                _allUseCases = data.use_cases || [];
                renderCategoryChips(_allUseCases);
                renderUseCases(_allUseCases);
            })
            .catch(function () { renderUseCases([]); });
    }

    function renderUseCases(cases) {
        var list = document.getElementById('usecase-list');
        if (!list) return;

        list.classList.toggle('chat-uc-list--compact', _ucCompact);
        list.classList.toggle('chat-uc-list--expanded', !_ucCompact);

        if (!cases.length) {
            list.innerHTML = '<div class="chat-uc-loading">No use cases found.</div>';
            return;
        }

        var html = '';
        for (var i = 0; i < cases.length; i++) {
            var uc = cases[i];
            var ucIdEsc = escAttr(uc.id);
            var isSelected = _chainMode && !!_chainSelected[uc.id];

            var chainCb = _chainMode
                ? '<input type="checkbox" class="uc-chain-cb" data-uc-id="' + ucIdEsc + '"'
                    + (isSelected ? ' checked' : '') + ' onclick="event.stopPropagation()">'
                : '';

            if (_ucCompact) {
                var ftBadge = uc.fast_track
                    ? '<span class="chat-uc-card__ft-badge" title="Pre-loaded requirements">&#x26A1;</span>' : '';
                var stepCount = (uc.workflow_steps && uc.workflow_steps.length)
                    ? '<span class="uc-step-count" title="' + uc.workflow_steps.length + ' workflow steps">&#x276F;' + uc.workflow_steps.length + '</span>' : '';
                html += '<div class="chat-uc-compact-row' + (isSelected ? ' chain-selected' : '') + '" data-uc-id="' + ucIdEsc + '" tabindex="0" role="button" aria-label="Start ' + escAttr(uc.label) + '">'
                    + chainCb
                    + '<span class="chat-uc-compact-row__icon">' + escHtml(uc.icon || '⚙') + '</span>'
                    + '<span class="chat-uc-compact-row__label">' + escHtml(uc.label) + '</span>'
                    + ftBadge + stepCount
                    + '<span class="chat-uc-compact-row__actions">'
                    + '<button class="chat-uc-edit-btn" data-uc-id="' + ucIdEsc + '" title="Edit" onclick="event.stopPropagation()">&#x270F;</button>'
                    + '<button class="chat-uc-export-btn" data-uc-id="' + ucIdEsc + '" title="Export bundle" onclick="event.stopPropagation()">&#x2913;</button>'
                    + '</span>'
                    + '</div>';
            } else {
                var badge = uc.badge ? '<span class="chat-uc-card__badge">' + escHtml(uc.badge) + '</span>' : '';
                var chips = '';
                var qa = uc.quick_actions || [];
                for (var qi = 0; qi < qa.length; qi++) {
                    chips += '<a href="' + escHtml(qa[qi].url) + '" target="' + escHtml(qa[qi].target || '_blank') + '"'
                        + ' class="chat-uc-qa-chip" title="' + escHtml(qa[qi].label) + '"'
                        + ' onclick="event.stopPropagation()">'
                        + escHtml(qa[qi].icon || '') + ' ' + escHtml(qa[qi].label) + '</a>';
                }
                var ftExpBadge = uc.fast_track
                    ? ' <span class="chat-uc-card__ft-badge" title="Pre-loaded requirements — PRD ready immediately">&#x26A1; Ready</span>' : '';
                var wfSteps = uc.workflow_steps || [];
                var wfHtml = '';
                if (wfSteps.length) {
                    wfHtml = '<div class="chat-uc-card__workflow">'
                        + wfSteps.map(function (s, idx) {
                            return '<span class="uc-wf-step" title="Step ' + (idx + 1) + ': ' + escAttr(s.description || s.label || '') + '">'
                                + escHtml(s.label || ('Step ' + (idx + 1))) + '</span>';
                        }).join('<span class="uc-wf-arrow">&#x203A;</span>')
                        + '</div>';
                }
                html += '<div class="chat-uc-card' + (isSelected ? ' chain-selected' : '') + '" data-uc-id="' + ucIdEsc + '" tabindex="0" role="button" aria-label="Start ' + escAttr(uc.label) + '">'
                    + '<div class="chat-uc-card__top">'
                    + chainCb
                    + '<span class="chat-uc-card__label"><span class="chat-uc-card__icon">' + escHtml(uc.icon || '') + '</span>' + escHtml(uc.label) + ftExpBadge + '</span>'
                    + '<button class="chat-uc-edit-btn" data-uc-id="' + ucIdEsc + '" title="View / edit use case" tabindex="0">&#x270F;</button>'
                    + badge
                    + '</div>'
                    + '<div class="chat-uc-card__desc">' + escHtml(uc.description || '') + '</div>'
                    + wfHtml
                    + (chips ? '<div class="chat-uc-card__chips">' + chips + '</div>' : '')
                    + '<div class="chat-uc-card__actions">'
                    + '<a href="' + CHAT_API + '/use-cases/' + escHtml(uc.id) + '/standalone" class="chat-uc-standalone-btn" title="Download self-contained HTML app" onclick="event.stopPropagation()" download>&#x2913; Standalone</a>'
                    + '<button class="chat-uc-export-btn" data-uc-id="' + ucIdEsc + '" title="Export YAML bundle" onclick="event.stopPropagation()">&#x2913; Export</button>'
                    + '</div>'
                    + '</div>';
            }
        }
        list.innerHTML = html;

        list.querySelectorAll('.chat-uc-compact-row').forEach(function (row) {
            row.addEventListener('click', function (e) {
                if (e.target.closest('.chat-uc-edit-btn') || e.target.closest('.chat-uc-export-btn') || e.target.classList.contains('uc-chain-cb')) return;
                if (_chainMode) {
                    var id = row.dataset.ucId;
                    if (_chainSelected[id]) { delete _chainSelected[id]; } else { _chainSelected[id] = true; }
                    var cb = row.querySelector('.uc-chain-cb');
                    if (cb) cb.checked = !!_chainSelected[id];
                    row.classList.toggle('chain-selected', !!_chainSelected[id]);
                    updateChainCount();
                    return;
                }
                startUseCase(row.dataset.ucId);
            });
            row.addEventListener('keydown', function (e) {
                if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); if (!_chainMode) startUseCase(row.dataset.ucId); }
            });
        });

        list.querySelectorAll('.chat-uc-card').forEach(function (card) {
            card.addEventListener('click', function (e) {
                if (e.target.closest('.chat-uc-edit-btn') || e.target.closest('.chat-uc-qa-chip')
                    || e.target.closest('.chat-uc-standalone-btn') || e.target.closest('.chat-uc-export-btn')
                    || e.target.classList.contains('uc-chain-cb')) return;
                if (_chainMode) {
                    var id = card.dataset.ucId;
                    if (_chainSelected[id]) { delete _chainSelected[id]; } else { _chainSelected[id] = true; }
                    var cb = card.querySelector('.uc-chain-cb');
                    if (cb) cb.checked = !!_chainSelected[id];
                    card.classList.toggle('chain-selected', !!_chainSelected[id]);
                    updateChainCount();
                    return;
                }
                startUseCase(card.dataset.ucId);
            });
            card.addEventListener('keydown', function (e) {
                if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); if (!_chainMode) startUseCase(card.dataset.ucId); }
            });
        });

        list.querySelectorAll('.chat-uc-edit-btn').forEach(function (btn) {
            btn.addEventListener('click', function (e) {
                e.stopPropagation();
                openUcEditModal(btn.dataset.ucId);
            });
        });

        list.querySelectorAll('.chat-uc-export-btn').forEach(function (btn) {
            btn.addEventListener('click', function (e) {
                e.stopPropagation();
                exportUseCase(btn.dataset.ucId);
            });
        });

        list.querySelectorAll('.uc-chain-cb').forEach(function (cb) {
            cb.addEventListener('change', function (e) {
                e.stopPropagation();
                var id = cb.dataset.ucId;
                if (cb.checked) { _chainSelected[id] = true; } else { delete _chainSelected[id]; }
                var parent = cb.closest('.chat-uc-card') || cb.closest('.chat-uc-compact-row');
                if (parent) parent.classList.toggle('chain-selected', cb.checked);
                updateChainCount();
            });
        });
    }

    // ---- Start use case (create context + seed message) ----

    function startUseCase(ucId) {
        var card = document.querySelector('.chat-uc-card[data-uc-id="' + ucId + '"]');
        if (card) card.classList.add('chat-uc-card--loading');

        function clearLoading() {
            if (card) card.classList.remove('chat-uc-card--loading');
        }

        fetch(CHAT_API + '/use-cases/' + encodeURIComponent(ucId))
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (uc) {
                if (!uc || uc.error) { clearLoading(); return; }
                // Store fast-track config before context creation
                _fastTrackConfig = null;
                _fastTrackDone = false;
                if (uc.fast_track) {
                    _fastTrackConfig = {
                        skip_requirement_types: uc.skip_requirement_types || [],
                        user_config: uc.user_config || {},
                        uc_label: uc.label || ucId
                    };
                }
                var opts = {
                    title: uc.label || ucId,
                    agent_model: uc.agent_model || 'kimi-cloud',
                    system_prompt: uc.system_prompt || '',
                    uc_category: uc.category || '',
                    uc_label: uc.label || '',
                    suppress_intake_welcome: !!uc.seed_message,
                    fast_track: !!uc.fast_track,
                    skip_requirement_types: uc.skip_requirement_types || [],
                    user_config: uc.user_config || {},
                    template_requirements: uc.template_requirements || []
                };
                function afterCreate(ctx) {
                    clearLoading();
                    if (!ctx || ctx.error) return;
                    _ucContextMap[ctx.context_id] = ucId;
                    try { localStorage.setItem('icdev_uc_ctx_map', JSON.stringify(_ucContextMap)); } catch (e) {}
                    _activeUseCase = uc;
                    // Display seed_message as the AI's opening message, not as user input
                    if (uc.seed_message) {
                        var stream = document.getElementById('message-stream');
                        if (stream) stream.innerHTML = renderMessageHtml({ role: 'assistant', content: uc.seed_message.trim() });
                    }
                    // Canvas seed confirmation (F2g) — best-effort informational message
                    var seeds = uc.canvas_seeds || [];
                    if (seeds.length) {
                        var totalTemplates = seeds.reduce(function (n, s) { return n + ((s.templates || []).length + (s.snippets || []).length); }, 0);
                        if (totalTemplates > 0) {
                            var canvasNames = seeds.map(function (s) {
                                return (s.canvas || '').replace(/_canvas$/, '').replace(/_/g, ' ');
                            }).filter(Boolean).join(', ');
                            addMessage('system', '&#x1F4C1; ' + totalTemplates + ' canvas template' + (totalTemplates !== 1 ? 's' : '') + ' configured for this use case (' + canvasNames + '). Activate a chain to pre-load them into the canvas.', null);
                        }
                    }
                    // Trigger fast-track sequence if applicable
                    if (_fastTrackConfig && !_fastTrackDone && ctx.intake_session_id) {
                        _fastTrackDone = true;
                        runFastTrackSequence(ctx.intake_session_id, uc.label || ucId);
                    }
                }
                if (uc.ricoas) {
                    createIntakeContext(opts).then(afterCreate);
                } else {
                    createContext(opts).then(afterCreate);
                }
            })
            .catch(function () { clearLoading(); });
    }

    // ---- Active use case action bar ----

    function renderUcActionBar(uc) {
        var container = document.getElementById('uc-specific-actions');
        if (!container) return;
        var qa = (uc && uc.quick_actions) ? uc.quick_actions : [];
        if (!qa.length) {
            container.innerHTML = '';
            container.style.display = 'none';
            return;
        }
        container.style.display = 'block';
        var html = '<div class="uc-action-bar__label">'
            + escHtml((uc.icon || '') + ' ' + (uc.label || '')) + ' — Canvas &amp; Tools</div>';
        for (var i = 0; i < qa.length; i++) {
            html += '<a href="' + escHtml(qa[i].url) + '" target="' + escHtml(qa[i].target || '_blank') + '"'
                + ' class="btn btn-sm btn-outline uc-action-btn">'
                + escHtml(qa[i].icon || '') + ' ' + escHtml(qa[i].label) + '</a>';
        }
        container.innerHTML = html;
    }

    function syncUcStateForContext(ctxId) {
        var ucId = _ucContextMap[ctxId];
        if (ucId) {
            for (var i = 0; i < _allUseCases.length; i++) {
                if (_allUseCases[i].id === ucId) {
                    _activeUseCase = _allUseCases[i];
                    renderUcActionBar(_allUseCases[i]);
                    return;
                }
            }
        }
        _activeUseCase = null;
        renderUcActionBar(null);
    }

    // ===================================================================
    // Fast-track sequence: AI Boost → PRD → Send to Kanban
    // ===================================================================

    function runFastTrackSequence(sessionId, ucLabel) {
        var label = ucLabel || 'Use Case';

        appendMessage({ role: 'system', content: 'Running AI Boost — augmenting pre-loaded requirements framework...' });

        fetch(INTAKE_API + '/ai-boost/' + encodeURIComponent(sessionId), { method: 'POST' })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (boost) {
            var reqCount = (boost && boost.requirements_added != null) ? boost.requirements_added : '?';
            appendMessage({ role: 'system', content: 'AI Boost complete — ' + reqCount + ' additional requirements generated. Producing PRD...' });

            return fetch(INTAKE_API + '/prd/' + encodeURIComponent(sessionId))
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (prd) {
                if (!prd || prd.error) {
                    appendMessage({ role: 'system', content: 'PRD generation failed — you can still download it manually from the Requirements panel.' });
                    return;
                }
                // Trigger PRD download
                var markdown = prd.prd_markdown || prd.content || '';
                var totalReqs = prd.requirement_count || '';
                var blob = new Blob([markdown], { type: 'text/markdown' });
                var url = URL.createObjectURL(blob);
                var a = document.createElement('a');
                a.href = url;
                a.download = label.replace(/\s+/g, '_') + '_PRD.md';
                a.style.display = 'none';
                document.body.appendChild(a);
                a.click();
                setTimeout(function () { document.body.removeChild(a); URL.revokeObjectURL(url); }, 1000);

                var reqSummary = totalReqs ? ' (' + totalReqs + ' requirements)' : '';
                appendMessage({
                    role: 'system',
                    content: 'PRD ready' + reqSummary + '. <a href="' + url + '" download="' + escHtml(label.replace(/\s+/g, '_') + '_PRD.md') + '">[Download PRD]</a>'
                        + '<br><button class="uc-send-kanban-btn" onclick="window._sendToKanban(' + JSON.stringify(sessionId) + ',' + JSON.stringify(label) + ',' + JSON.stringify(markdown.slice(0, 2000)) + ')">Send to Kanban</button>'
                });

                refreshReadiness();
            });
        })
        .catch(function (err) {
            appendMessage({ role: 'system', content: 'Fast-track error: ' + (err.message || 'unknown') + '. You can run AI Boost manually.' });
        });
    }

    // Exposed globally so inline onclick can call it
    window._sendToKanban = function sendToKanban(sessionId, ucLabel, prdExcerpt) {
        var today = new Date().toLocaleDateString('en-US', { year: 'numeric', month: '2-digit', day: '2-digit' });
        fetch('/api/kanban/tasks', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                title: (ucLabel || 'Use Case') + ' — ' + today,
                description: prdExcerpt || '',
                task_type: 'build',
                priority: 'medium',
                status: 'backlog'
            })
        })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (data) {
            if (data && !data.error) {
                appendMessage({ role: 'system', content: 'Task added to Kanban backlog. <a href="/kanban" target="_blank">[View Kanban →]</a>' });
            } else {
                appendMessage({ role: 'system', content: 'Kanban task creation failed — open <a href="/kanban" target="_blank">Kanban</a> and create manually.' });
            }
        })
        .catch(function () {
            appendMessage({ role: 'system', content: 'Could not reach Kanban API.' });
        });
    };

    // Wrap switchContext to keep action bar in sync on context switches
    var _origSwitchContext = switchContext;
    switchContext = function (ctxId) {
        _origSwitchContext(ctxId);
        syncUcStateForContext(ctxId);
    };

    // ---- Edit modal ----

    function openUcEditModal(ucId) {
        fetch(CHAT_API + '/use-cases/' + encodeURIComponent(ucId))
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (uc) {
                if (!uc || uc.error) return;
                _ucEditId = ucId;
                document.getElementById('uc-f-id').value = ucId;
                document.getElementById('uc-f-label').value = uc.label || '';
                document.getElementById('uc-f-icon').value = uc.icon || '';
                document.getElementById('uc-f-badge').value = uc.badge || '';
                var catEl = document.getElementById('uc-f-category');
                if (catEl) catEl.value = uc.category || 'general';
                var modelEl = document.getElementById('uc-f-model');
                if (modelEl) modelEl.value = uc.agent_model || 'kimi-cloud';
                var ricoasEl = document.getElementById('uc-f-ricoas');
                if (ricoasEl) ricoasEl.checked = !!uc.ricoas;
                document.getElementById('uc-f-desc').value = uc.description || '';
                document.getElementById('uc-f-system').value = uc.system_prompt || '';
                document.getElementById('uc-f-seed').value = uc.seed_message || '';
                renderUcQaRows(uc.quick_actions || []);
                // Fast-track user_config section
                var ucConfigSection = document.getElementById('uc-user-config-section');
                if (ucConfigSection) {
                    ucConfigSection.style.display = uc.fast_track ? 'block' : 'none';
                    if (uc.fast_track) {
                        var userConfig = uc.user_config || {};
                        renderUcTagList('uc-industries-list', (userConfig.industries || {}).defaults || []);
                        renderUcTagList('uc-equipment-list', (userConfig.equipment_types || {}).defaults || []);
                        renderUcTagList('uc-vendors-list', (userConfig.vendors || {}).defaults || []);
                        var skipTypes = uc.skip_requirement_types || [];
                        document.querySelectorAll('.uc-skip-type').forEach(function (cb) {
                            cb.checked = skipTypes.indexOf(cb.value) !== -1;
                        });
                    }
                }
                document.getElementById('uc-modal-title').textContent = 'Edit — ' + (uc.label || ucId);
                document.getElementById('uc-edit-modal').style.display = 'flex';
                document.getElementById('uc-f-label').focus();
            });
    }

    function renderUcQaRows(qaList) {
        var container = document.getElementById('uc-f-qa-list');
        if (!container) return;
        container.innerHTML = '';
        for (var i = 0; i < qaList.length; i++) appendUcQaRow(qaList[i]);
    }

    function appendUcQaRow(qa) {
        qa = qa || {};
        var container = document.getElementById('uc-f-qa-list');
        if (!container) return;
        var row = document.createElement('div');
        row.className = 'uc-qa-row';
        row.innerHTML = '<input type="text" class="form-control uc-qa-icon" placeholder="Icon" value="' + escHtml(qa.icon || '') + '" maxlength="8">'
            + '<input type="text" class="form-control uc-qa-label" placeholder="Label" value="' + escHtml(qa.label || '') + '">'
            + '<input type="text" class="form-control uc-qa-url" placeholder="/path or https://..." value="' + escHtml(qa.url || '') + '">'
            + '<select class="form-control uc-qa-target"><option value="_blank">New tab</option><option value="_self">Same tab</option></select>'
            + '<button type="button" class="btn btn-sm btn-danger uc-qa-remove" title="Remove">&#x2715;</button>';
        var targetEl = row.querySelector('.uc-qa-target');
        if (targetEl) targetEl.value = qa.target || '_blank';
        row.querySelector('.uc-qa-remove').addEventListener('click', function () { row.remove(); });
        container.appendChild(row);
    }

    function collectUcQaRows() {
        var rows = document.querySelectorAll('#uc-f-qa-list .uc-qa-row');
        var result = [];
        for (var i = 0; i < rows.length; i++) {
            var icon = rows[i].querySelector('.uc-qa-icon').value.trim();
            var label = rows[i].querySelector('.uc-qa-label').value.trim();
            var url = rows[i].querySelector('.uc-qa-url').value.trim();
            var target = rows[i].querySelector('.uc-qa-target').value;
            if (label && url) result.push({ icon: icon, label: label, url: url, target: target });
        }
        return result;
    }

    // --- Tag list helpers for user_config (industries, equipment, vendors) ---

    function renderUcTagList(containerId, items) {
        var container = document.getElementById(containerId);
        if (!container) return;
        container.innerHTML = '';
        for (var i = 0; i < items.length; i++) appendUcTag(containerId, items[i]);
    }

    function appendUcTag(containerId, text) {
        var container = document.getElementById(containerId);
        if (!container || !text.trim()) return;
        var tag = document.createElement('span');
        tag.className = 'uc-config-tag';
        tag.innerHTML = escHtml(text.trim())
            + '<button class="uc-tag-remove" type="button" title="Remove">&#x2715;</button>';
        tag.querySelector('.uc-tag-remove').addEventListener('click', function () { tag.remove(); });
        container.appendChild(tag);
    }

    function collectUcTags(containerId) {
        var tags = document.querySelectorAll('#' + containerId + ' .uc-config-tag');
        var result = [];
        for (var i = 0; i < tags.length; i++) {
            var text = tags[i].textContent.replace('✕', '').trim();
            if (text) result.push(text);
        }
        return result;
    }

    function collectCheckedSkipTypes() {
        var checked = document.querySelectorAll('.uc-skip-type:checked');
        var result = [];
        for (var i = 0; i < checked.length; i++) result.push(checked[i].value);
        return result;
    }

    function saveUcEdit() {
        var ucId = document.getElementById('uc-f-id').value;
        if (!ucId) return;
        var saveBtn = document.getElementById('uc-modal-save');
        if (saveBtn) { saveBtn.disabled = true; saveBtn.textContent = 'Saving…'; }

        var payload = {
            label: document.getElementById('uc-f-label').value.trim(),
            icon: document.getElementById('uc-f-icon').value.trim(),
            badge: document.getElementById('uc-f-badge').value.trim(),
            category: document.getElementById('uc-f-category').value,
            agent_model: document.getElementById('uc-f-model').value,
            ricoas: document.getElementById('uc-f-ricoas').checked,
            description: document.getElementById('uc-f-desc').value.trim(),
            system_prompt: document.getElementById('uc-f-system').value,
            seed_message: document.getElementById('uc-f-seed').value,
            quick_actions: collectUcQaRows(),
        };
        // Collect fast-track user_config if section is visible
        var ucConfigSection = document.getElementById('uc-user-config-section');
        if (ucConfigSection && ucConfigSection.style.display !== 'none') {
            payload.user_config = {
                industries: { defaults: collectUcTags('uc-industries-list') },
                equipment_types: { defaults: collectUcTags('uc-equipment-list') },
                vendors: { defaults: collectUcTags('uc-vendors-list') }
            };
            payload.skip_requirement_types = collectCheckedSkipTypes();
            payload.fast_track = true;
        }

        fetch(CHAT_API + '/use-cases/' + encodeURIComponent(ucId), {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        })
            .then(function (r) { return r.json(); })
            .then(function (result) {
                if (result.error) { alert('Save failed: ' + result.error); return; }
                closeUcEditModal();
                loadUseCases();
            })
            .catch(function () { alert('Network error saving use case.'); })
            .then(function () {
                if (saveBtn) { saveBtn.disabled = false; saveBtn.textContent = 'Save Changes'; }
            });
    }

    function resetUcDefault(ucId) {
        if (!ucId) return;
        if (!confirm('Reset "' + ucId + '" to factory defaults? Your customizations will be deleted.')) return;
        fetch(CHAT_API + '/use-cases/' + encodeURIComponent(ucId) + '/override', { method: 'DELETE' })
            .then(function (r) { return r.json(); })
            .then(function () { closeUcEditModal(); loadUseCases(); })
            .catch(function () { alert('Reset failed.'); });
    }

    function closeUcEditModal() {
        var modal = document.getElementById('uc-edit-modal');
        if (modal) modal.style.display = 'none';
        _ucEditId = null;
    }

    function initUcEditModal() {
        var closeBtn = document.getElementById('uc-modal-close');
        var cancelBtn = document.getElementById('uc-modal-cancel');
        var saveBtn = document.getElementById('uc-modal-save');
        var resetBtn = document.getElementById('uc-reset-btn');
        var addQaBtn = document.getElementById('uc-qa-add-btn');
        var overlay = document.getElementById('uc-edit-modal');

        if (closeBtn) closeBtn.addEventListener('click', closeUcEditModal);
        if (cancelBtn) cancelBtn.addEventListener('click', closeUcEditModal);
        if (saveBtn) saveBtn.addEventListener('click', saveUcEdit);
        if (resetBtn) resetBtn.addEventListener('click', function () { resetUcDefault(_ucEditId); });
        if (addQaBtn) addQaBtn.addEventListener('click', function () { appendUcQaRow({}); });
        // Wire tag-add buttons for user_config lists
        function wireTagBtn(btnId, listId, inputId) {
            var btn = document.getElementById(btnId);
            var input = document.getElementById(inputId);
            if (btn && input) {
                btn.addEventListener('click', function () {
                    var val = input.value.trim();
                    if (val) { appendUcTag(listId, val); input.value = ''; }
                });
                input.addEventListener('keydown', function (e) {
                    if (e.key === 'Enter') { e.preventDefault(); btn.click(); }
                });
            }
        }
        wireTagBtn('uc-industries-add-btn', 'uc-industries-list', 'uc-industries-input');
        wireTagBtn('uc-equipment-add-btn', 'uc-equipment-list', 'uc-equipment-input');
        wireTagBtn('uc-vendors-add-btn', 'uc-vendors-list', 'uc-vendors-input');
        if (overlay) {
            overlay.addEventListener('click', function (e) { if (e.target === overlay) closeUcEditModal(); });
            overlay.addEventListener('keydown', function (e) { if (e.key === 'Escape') closeUcEditModal(); });
        }
    }

    // ---- Panel init ----

    function initUseCasesPanel() {
        var collapseBtn = document.getElementById('btn-uc-collapse');
        var panel = document.getElementById('uc-panel');
        var searchInput = document.getElementById('usecase-search');

        if (collapseBtn && panel) {
            collapseBtn.addEventListener('click', function () {
                var collapsed = panel.classList.toggle('collapsed');
                collapseBtn.classList.toggle('collapsed', collapsed);
                try { localStorage.setItem('icdev_uc_collapsed', collapsed ? '1' : '0'); } catch (e) {}
            });
            try {
                if (localStorage.getItem('icdev_uc_collapsed') === '1') {
                    panel.classList.add('collapsed');
                    collapseBtn.classList.add('collapsed');
                }
            } catch (e) {}
        }

        if (searchInput) {
            searchInput.addEventListener('input', function () {
                var q = this.value.trim().toLowerCase();
                var base = _ucActiveCategory
                    ? _allUseCases.filter(function (uc) { return (uc.category || 'general') === _ucActiveCategory; })
                    : _allUseCases;
                if (!q) { renderUseCases(base); return; }
                renderUseCases(base.filter(function (uc) {
                    return uc.label.toLowerCase().indexOf(q) !== -1
                        || (uc.description || '').toLowerCase().indexOf(q) !== -1
                        || (uc.category || '').toLowerCase().indexOf(q) !== -1;
                }));
            });
        }

        // Toolbar buttons
        var btnChainMode = document.getElementById('btn-uc-chain-mode');
        if (btnChainMode) btnChainMode.addEventListener('click', function () { _chainMode ? exitChainMode() : enterChainMode(); });

        var btnCreate = document.getElementById('btn-uc-create');
        if (btnCreate) btnCreate.addEventListener('click', function () { openWizard(null); });

        var btnImport = document.getElementById('btn-uc-import');
        if (btnImport) btnImport.addEventListener('click', openImportModal);

        var btnCompact = document.getElementById('btn-uc-compact');
        if (btnCompact) btnCompact.addEventListener('click', toggleUcCompact);

        // Chain bar
        var btnChainActivate = document.getElementById('btn-chain-activate');
        if (btnChainActivate) btnChainActivate.addEventListener('click', activateChain);

        var btnChainCancel = document.getElementById('btn-chain-cancel');
        if (btnChainCancel) btnChainCancel.addEventListener('click', exitChainMode);

        // Import modal
        var importClose = document.getElementById('uc-import-close');
        if (importClose) importClose.addEventListener('click', closeImportModal);

        var importCancel = document.getElementById('uc-import-cancel');
        if (importCancel) importCancel.addEventListener('click', closeImportModal);

        var importSubmit = document.getElementById('uc-import-submit');
        if (importSubmit) importSubmit.addEventListener('click', submitImport);

        var importOverlay = document.getElementById('uc-import-modal');
        if (importOverlay) importOverlay.addEventListener('click', function (e) { if (e.target === importOverlay) closeImportModal(); });

        // Wizard navigation
        var wizCloseBtn = document.getElementById('uc-wiz-close');
        if (wizCloseBtn) wizCloseBtn.addEventListener('click', closeWizard);

        var wizNextBtn = document.getElementById('uc-wiz-next');
        if (wizNextBtn) wizNextBtn.addEventListener('click', wizNext);

        var wizPrevBtn = document.getElementById('uc-wiz-prev');
        if (wizPrevBtn) wizPrevBtn.addEventListener('click', wizPrev);

        var wizFinishBtn = document.getElementById('uc-wiz-finish');
        if (wizFinishBtn) wizFinishBtn.addEventListener('click', submitWizard);

        var wizOverlay = document.getElementById('uc-create-wizard');
        if (wizOverlay) wizOverlay.addEventListener('click', function (e) { if (e.target === wizOverlay) closeWizard(); });

        // Wizard step 1: clone radio toggle
        document.querySelectorAll('input[name="wiz-start"]').forEach(function (radio) {
            radio.addEventListener('change', function () {
                var wrap = document.getElementById('uc-wiz-clone-wrap');
                if (wrap) wrap.style.display = (this.value === 'clone') ? '' : 'none';
            });
        });

        // Wizard step 4: add requirement
        var addReq = document.getElementById('uc-wiz-add-req');
        if (addReq) addReq.addEventListener('click', function () {
            _wizReqs.push({ type: 'functional', priority: 'high', text: '' });
            renderWizReqList();
        });

        // Wizard step 5: add workflow step
        var addStep = document.getElementById('uc-wiz-add-step');
        if (addStep) addStep.addEventListener('click', function () {
            _wizSteps.push({ step: _wizSteps.length + 1, label: '', description: '', action: '' });
            renderWizStepsList();
        });

        // Wizard step 3: archetype buttons
        document.querySelectorAll('.uc-archetype-btn[data-archetype]').forEach(function (btn) {
            btn.addEventListener('click', function () { applyArchetype(btn.dataset.archetype); });
        });

        initUcEditModal();
        loadUseCases();
    }


    // ===================================================================
    // SECTION 19: Namespace exports
    // ===================================================================

    ns.chatGeneratePlan = chatGeneratePlan;
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

    // Multi-stream API
    ns.chatStreams = {
        createContext: createContext,
        switchContext: switchContext,
        sendMessage: sendMessage,
        intervene: intervene,
        pollContextState: pollContextState,
        refreshContextList: refreshContextList,
        closeContext: closeContext
    };

    ns.chatStartUseCase = startUseCase;
    ns.chatLoadUseCases = loadUseCases;

    window.ICDEV = ns;

    // Init on DOM ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function () { init(); initUseCasesPanel(); });
    } else {
        init();
        initUseCasesPanel();
    }
})();
