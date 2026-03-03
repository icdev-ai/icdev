// CUI // SP-CTI
// ICDEV Unified Chat — multi-stream backbone + RICOAS intake features.
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

        chatApi('GET', '/' + ctxId).then(function (ctx) {
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
        chatApi('GET', '/' + ctxId).then(function (ctx) {
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
        fetch(INTAKE_API + '/conversation/' + intakeSessionId)
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (data.error) {
                    // Fallback: show welcome message
                    var stream = document.getElementById('message-stream');
                    if (stream) stream.innerHTML = renderMessageHtml({ role: 'assistant', content: 'Welcome! I\'m the ICDEV Requirements Analyst. Tell me about the application you want to build.' });
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

    function sendIntakeMessage(content) {
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
        var ricoas = document.getElementById('ricoas-sidebar');
        var ricoasBtn = document.getElementById('btn-ricoas-toggle');
        if (rightSidebar) rightSidebar.style.display = 'block';
        if (ricoas) ricoas.style.display = 'block';
        if (ricoasBtn) ricoasBtn.style.display = 'inline-block';
    }

    function hideRicoasSidebar() {
        var ricoas = document.getElementById('ricoas-sidebar');
        var ricoasBtn = document.getElementById('btn-ricoas-toggle');
        if (ricoas) ricoas.style.display = 'none';
        if (ricoasBtn) ricoasBtn.style.display = 'none';
        // Hide right sidebar if gov is also hidden
        var gov = document.getElementById('gov-sidebar');
        if (!gov || gov.style.display === 'none') {
            var rightSidebar = document.getElementById('right-sidebar');
            if (rightSidebar) rightSidebar.style.display = 'none';
        }
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
                + '<div style="display: flex; justify-content: space-between; align-items: center;">'
                + '<span style="font-size: 0.85rem; font-weight: 500;">' + escHtml(c.title || c.context_id) + titleSuffix + '</span>'
                + '<span style="width: 8px; height: 8px; border-radius: 50%; background: ' + statusColor + '; display: inline-block;"></span>'
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
    }

    function renderMessages(messages) {
        var stream = document.getElementById('message-stream');
        if (!stream) return;
        if (!messages.length) {
            stream.innerHTML = '<div style="text-align: center; padding: 40px; color: var(--text-muted); font-size: 0.9rem;">Start a conversation by sending a message.</div>';
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
        var extraClass = '';
        if (msg.role === 'governance_advisory') {
            extraClass = ' msg-governance_advisory';
            label = 'Governance Advisory';
            labelColor = 'var(--accent-purple, #7c4dff)';
        }

        return '<div class="' + extraClass + '" style="padding: 8px 12px; margin-bottom: 4px; background: ' + bgColor + '; border-left: ' + borderLeft + '; border-radius: 4px;">'
            + '<div style="font-size: 0.75rem; font-weight: 600; color: ' + labelColor + '; margin-bottom: 4px;">'
            + label + (msg.turn_number ? ' (#' + msg.turn_number + ')' : '') + '</div>'
            + '<div style="font-size: 0.85rem; white-space: pre-wrap; word-break: break-word;">' + escHtml(msg.content || '') + '</div>'
            + '</div>';
    }

    function appendTechniqueActivation(data) {
        var stream = document.getElementById('message-stream');
        if (!stream) return;
        var tech = data.technique || {};
        var qs = data.suggested_questions || [];
        var html = '<div style="padding: 8px 12px; margin-bottom: 4px; background: var(--bg-tertiary, #112); border-left: 3px solid var(--accent-blue); border-radius: 4px;">';
        html += '<div style="font-size: 0.75rem; font-weight: 600; color: var(--accent-blue); margin-bottom: 4px;">Technique Activated</div>';
        html += '<div style="font-size: 0.85rem;"><strong>' + escHtml(tech.name || 'Technique') + '</strong>';
        if (tech.description) html += '<br><span style="color: var(--text-secondary);">' + escHtml(tech.description) + '</span>';
        html += '</div>';
        if (qs.length > 0) {
            html += '<div style="margin-top: 6px; font-size: 0.8rem;">Try asking:</div>';
            for (var i = 0; i < qs.length; i++) {
                html += '<button class="technique-question-btn" data-q="' + escAttr(qs[i]) + '" style="display: block; margin: 4px 0; padding: 4px 8px; background: var(--bg-secondary); border: 1px solid var(--border-color); border-radius: 3px; color: var(--text-primary); cursor: pointer; font-size: 0.8rem; text-align: left; width: 100%;">' + escHtml(qs[i]) + '</button>';
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

    function updateInterventionBar(isProcessing) {
        var bar = document.getElementById('intervention-bar');
        if (bar) bar.style.display = isProcessing ? 'block' : 'none';
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
        var classification = options.classification || cfg.wizardClassification || 'il4';
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
                appendMessage({ role: 'system', content: 'Error creating intake session: ' + data.error });
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
                agent_model: options.agent_model || 'sonnet',
                system_prompt: 'RICOAS intake session: ' + intakeSessionId
            }).then(function (ctx) {
                if (ctx.error) {
                    appendMessage({ role: 'system', content: 'Error creating chat context: ' + ctx.error });
                    return;
                }
                // Step 3: Store mapping
                _intakeMap[ctx.context_id] = intakeSessionId;
                saveIntakeMappings();

                refreshContextList();
                switchContext(ctx.context_id);

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
            appendMessage({ role: 'system', content: 'Connection error: ' + err.message });
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
            if (modal) modal.style.display = 'flex';
        });
        if (btnCancel) btnCancel.addEventListener('click', function () {
            if (modal) modal.style.display = 'none';
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
            if (modal) modal.style.display = 'none';
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
    // SECTION 18: Namespace exports
    // ===================================================================

    ns.chatGeneratePlan = chatGeneratePlan;
    ns.chatExport = chatExport;
    ns.chatTriggerBuild = chatTriggerBuild;
    ns.chatRunSimulation = chatRunSimulation;
    ns.chatViewRequirements = chatViewRequirements;
    ns.chatGeneratePRD = chatGeneratePRD;
    ns.chatValidatePRD = chatValidatePRD;
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

    window.ICDEV = ns;

    // Init on DOM ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
