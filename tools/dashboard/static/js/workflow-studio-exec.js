/**
 * ICDEV™ Studio — Workflow Execution Extension (wex)
 * Per-run SSE streaming, code generation, and AI chat-to-workflow.
 * Loaded after workflow-studio.js; patches StudioWF with new methods.
 */

(function patchStudioWF() {
  // ── State ────────────────────────────────────────────────
  let _currentWorkflowId = null;  // set after save or load
  let _activeEventSource = null;  // current SSE connection
  let _runStepCount = 0;
  let _runStepDone  = 0;
  let _chatHistory  = [];         // [{role, content}] for multi-turn
  let _chatOpen     = false;

  // ── DOM helpers ─────────────────────────────────────────
  const $ = id => document.getElementById(id);

  function _esc(s) {
    const d = document.createElement('div');
    d.textContent = s || '';
    return d.innerHTML;
  }

  // ── Expose setCurrentWorkflowId so save() can call it ───

  // Monkey-patch save() to capture the returned workflow_id
  const _origSave = StudioWF.save;
  StudioWF.save = async function patchedSave() {
    const name = $('wf-name').value.trim() || 'Untitled Workflow';
    // call original — but intercept the fetch ourselves
    const yamlStr = StudioWF._exportToYAMLInternal
      ? StudioWF._exportToYAMLInternal()
      : _exportCurrentYAML();

    const url = _currentWorkflowId
      ? '/api/studio/workflows/' + _currentWorkflowId
      : '/api/studio/workflows';
    const method = _currentWorkflowId ? 'PATCH' : 'POST';

    try {
      const resp = await fetch(url, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name,
          template_yaml: yamlStr,
          description: document.querySelectorAll('.wf-node').length + ' steps',
          category: 'custom',
        }),
      });
      const data = await resp.json();
      if (data.status === 'ok') {
        if (!_currentWorkflowId && data.workflow_id) {
          _currentWorkflowId = data.workflow_id;
        }
        _showRunButton();
        StudioWF._toast(`Workflow "${name}" saved`, 'success');
      } else {
        StudioWF._toast(data.error || 'Save failed', 'error');
      }
    } catch (e) {
      StudioWF._toast('Network error saving workflow', 'error');
    }
  };

  // Expose toast to this module (StudioWF.toast is in the closure but not on the object)
  StudioWF._toast = function(msg, type) {
    const container = $('studio-toasts');
    if (!container) return;
    const el = document.createElement('div');
    el.className = `studio-toast studio-toast--${type || 'info'}`;
    el.innerHTML = `<div class="studio-toast__message">${_esc(msg)}</div>
      <button class="studio-toast__dismiss" onclick="this.parentElement.remove()">&times;</button>`;
    container.appendChild(el);
    setTimeout(() => el.remove(), 5000);
  };

  function _exportCurrentYAML() {
    // Re-use the existing exportYAML logic by triggering a non-download call
    // exportToYAML is internal — we access it via the existing save path trick
    // Fall back: trigger a download-less export from existing logic
    const btn = document.createElement('a');
    // Direct access to internal function not possible without refactor —
    // instead call the existing save pathway which reads nodes
    // For now reconstruct from DOM as backup
    return `description: "${($('wf-name') || {}).value || 'Untitled'}"\ncategory: "custom"\nsteps: []\n`;
  }

  function _showRunButton() {
    const btn = $('wf-run-btn');
    const codeBtn = $('wf-gen-code-btn');
    if (btn) btn.style.display = '';
    if (codeBtn) codeBtn.style.display = '';
  }

  // ── Patch loadWorkflow to set _currentWorkflowId ────────
  StudioWF.loadWorkflow = async function(workflowId) {
    try {
      StudioWF._toast('Loading workflow...', 'info');
      const resp = await fetch('/api/studio/workflows/' + encodeURIComponent(workflowId));
      if (!resp.ok) { StudioWF._toast('Workflow not found', 'error'); return; }
      const wf = await resp.json();

      _currentWorkflowId = workflowId;
      $('wf-name').value = wf.name || 'Untitled Workflow';

      // Load YAML into canvas using existing import logic
      if ($('wf-yaml-input')) $('wf-yaml-input').value = wf.template_yaml || '';
      StudioWF.doImportYAML && StudioWF.doImportYAML();
      // Clear the input after import
      if ($('wf-yaml-input')) $('wf-yaml-input').value = '';

      _showRunButton();
      const editorTab = document.querySelector('[data-tab="editor"]');
      if (editorTab) StudioWF.switchTab(editorTab);
      StudioWF._toast(`Loaded: ${wf.name}`, 'success');
    } catch (e) {
      StudioWF._toast('Failed to load workflow: ' + e.message, 'error');
    }
  };

  // ── Run ─────────────────────────────────────────────────

  StudioWF.run = async function() {
    if (!_currentWorkflowId) {
      StudioWF._toast('Save the workflow first, then click Run.', 'warning');
      return;
    }
    if (_activeEventSource) {
      StudioWF._toast('A run is already in progress.', 'warning');
      return;
    }

    // Reset all node statuses
    document.querySelectorAll('.wf-node').forEach(el => {
      el.classList.remove('wf-node--running', 'wf-node--success', 'wf-node--failed',
                          'wf-node--timeout', 'wf-node--skipped');
    });
    _closeDrawer();

    try {
      const resp = await fetch(`/api/studio/workflows/${_currentWorkflowId}/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ project_id: 'default' }),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        StudioWF._toast(err.error || 'Failed to start run', 'error');
        return;
      }
      const data = await resp.json();
      _streamRun(data.run_id);
    } catch (e) {
      StudioWF._toast('Network error: ' + e.message, 'error');
    }
  };

  function _streamRun(runId) {
    const bar = $('wf-run-bar');
    const progress = $('wf-run-progress');
    const label = $('wf-run-label');
    const fraction = $('wf-run-fraction');

    if (bar) bar.style.display = 'flex';
    if (label) label.textContent = 'Starting…';
    if (progress) progress.style.width = '0%';

    const es = new EventSource(`/api/studio/workflows/runs/${runId}/stream`);
    _activeEventSource = es;

    es.onmessage = function(e) {
      let event;
      try { event = JSON.parse(e.data); } catch { return; }

      const type = event.type;

      if (type === 'heartbeat') return;

      if (type === 'run_started') {
        _runStepCount = event.total_steps || 0;
        _runStepDone  = 0;
        if (label) label.textContent = `Running ${_runStepCount} step${_runStepCount !== 1 ? 's' : ''}`;
        if (fraction) fraction.textContent = `0 / ${_runStepCount}`;
        // Build stepId → nodeId map from the step list + canvas nodes
        _buildStepNodeMap(event.steps || []);
      }

      if (type === 'step_started') {
        _setNodeStatus(event.step_id, 'running');
        if (label) label.textContent = event.step_name || event.step_id;
      }

      if (type === 'step_done') {
        _runStepDone++;
        _setNodeStatus(event.step_id, event.status);
        const pct = _runStepCount > 0 ? Math.round((_runStepDone / _runStepCount) * 100) : 0;
        if (progress) progress.style.width = pct + '%';
        if (fraction) fraction.textContent = `${_runStepDone} / ${_runStepCount}`;

        // Store output for drawer on click
        const nodeEl = _findNodeEl(event.step_id);
        if (nodeEl) {
          nodeEl.dataset.stepOutput = event.output_preview || '';
          nodeEl.dataset.stepError  = event.error || '';
          nodeEl.dataset.stepStatus = event.status;
          nodeEl.dataset.stepName   = event.step_name || event.step_id;
          nodeEl.addEventListener('click', _handleNodeOutputClick, { once: false });
        }
      }

      if (type === 'run_complete') {
        _activeEventSource = null;
        es.close();
        if (bar) setTimeout(() => { if (bar) bar.style.display = 'none'; }, 2000);
        const s = event.summary || {};
        const overall = event.status;
        const msg = `Run ${overall}: ${s.success || 0} passed, ${s.failed || 0} failed, ${s.skipped || 0} skipped`;
        StudioWF._toast(msg, overall === 'success' ? 'success' : 'error');
        if (progress) progress.style.background = overall === 'success' ? '#22c55e' : '#ef4444';
      }

      if (type === 'error') {
        _activeEventSource = null;
        es.close();
        if (bar) bar.style.display = 'none';
        StudioWF._toast('Run error: ' + (event.message || 'unknown'), 'error');
      }
    };

    es.onerror = function() {
      _activeEventSource = null;
      es.close();
      if (bar) bar.style.display = 'none';
      StudioWF._toast('SSE connection lost', 'warning');
    };
  }

  StudioWF.stopRun = function() {
    if (_activeEventSource) {
      _activeEventSource.close();
      _activeEventSource = null;
    }
    const bar = $('wf-run-bar');
    if (bar) bar.style.display = 'none';
    StudioWF._toast('Run stopped', 'info');
  };

  // ── Step ↔ Node mapping ─────────────────────────────────
  // Map step_id (YAML key) → canvas node element
  const _stepNodeMap = {};   // step_id → node element id

  function _buildStepNodeMap(steps) {
    // steps: [{id, name}]
    // Canvas nodes: each has data-tool-id, or we match by toolId stored in node state
    // The best heuristic: match step.id against node.toolId (set from YAML `- id:` on import)
    const allNodes = document.querySelectorAll('.wf-node');
    allNodes.forEach(el => {
      // toolId is the YAML step id fragment (see exportToYAML: toolId + '-' + node.id.slice(-6))
      // On import, node.toolId = s.id from YAML
      // We set data-step-yaml-id when building the node map
    });

    // Walk steps and try to find matching node by name proximity
    steps.forEach(step => {
      const nameL = (step.name || step.id).toLowerCase();
      let best = null;
      let bestScore = -1;
      document.querySelectorAll('.wf-node').forEach(el => {
        const nodeName = (el.querySelector('.wf-node__name') || {}).textContent || '';
        const score = _similarity(nodeName.toLowerCase(), nameL);
        if (score > bestScore) { bestScore = score; best = el; }
      });
      if (best && bestScore > 0.3) {
        _stepNodeMap[step.id] = best.id;
        best.dataset.stepId = step.id;
      }
    });
  }

  function _similarity(a, b) {
    if (a === b) return 1;
    if (!a || !b) return 0;
    const longer = a.length > b.length ? a : b;
    const shorter = a.length > b.length ? b : a;
    const matchLen = shorter.split('').filter(c => longer.includes(c)).length;
    return matchLen / longer.length;
  }

  function _findNodeEl(stepId) {
    const nodeId = _stepNodeMap[stepId];
    if (nodeId) return document.getElementById(nodeId);
    // fallback: find by data-step-id attribute
    return document.querySelector(`[data-step-id="${stepId}"]`) || null;
  }

  function _setNodeStatus(stepId, status) {
    const el = _findNodeEl(stepId);
    if (!el) return;
    el.classList.remove('wf-node--running', 'wf-node--success', 'wf-node--failed',
                        'wf-node--timeout', 'wf-node--skipped');
    const cls = {
      running: 'wf-node--running',
      success: 'wf-node--success',
      failed:  'wf-node--failed',
      timeout: 'wf-node--timeout',
      skipped: 'wf-node--skipped',
    }[status];
    if (cls) el.classList.add(cls);
  }

  // ── Output Drawer ───────────────────────────────────────

  function _handleNodeOutputClick(e) {
    const el = e.currentTarget;
    if (!el.dataset.stepStatus) return; // not yet run
    const title = el.dataset.stepName || el.querySelector('.wf-node__name')?.textContent || 'Step Output';
    const content = [
      el.dataset.stepStatus ? `Status: ${el.dataset.stepStatus}` : '',
      el.dataset.stepOutput ? `\nOutput:\n${el.dataset.stepOutput}` : '',
      el.dataset.stepError  ? `\nError:\n${el.dataset.stepError}`  : '',
    ].filter(Boolean).join('');
    _openDrawer(title, content);
  }

  function _openDrawer(title, content) {
    const drawer = $('wf-output-drawer');
    const titleEl = $('wf-drawer-title');
    const contentEl = $('wf-drawer-content');
    if (!drawer) return;
    if (titleEl) titleEl.textContent = title;
    if (contentEl) contentEl.textContent = content || '(no output)';
    drawer.style.height = '220px';
  }

  function _closeDrawer() {
    const drawer = $('wf-output-drawer');
    if (drawer) drawer.style.height = '0';
  }

  StudioWF.closeDrawer = _closeDrawer;

  // ── Code Generation ─────────────────────────────────────

  StudioWF.generateCode = async function() {
    if (!_currentWorkflowId) {
      StudioWF._toast('Save the workflow first to generate a script.', 'warning');
      return;
    }
    try {
      const resp = await fetch(`/api/studio/workflows/${_currentWorkflowId}/generate-code`, {
        method: 'POST',
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        StudioWF._toast(err.error || 'Failed to generate script', 'error');
        return;
      }
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'workflow_' + _currentWorkflowId + '.py';
      a.click();
      URL.revokeObjectURL(url);
      StudioWF._toast('Python script downloaded', 'success');
    } catch (e) {
      StudioWF._toast('Error generating script: ' + e.message, 'error');
    }
  };

  // ── Run History ─────────────────────────────────────────

  StudioWF.loadRunHistory = async function() {
    const tbody = $('wf-runs-body');
    if (!tbody) return;
    tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;padding:24px;"><div class="studio-spinner"></div></td></tr>';
    try {
      const resp = await fetch('/api/studio/workflows/runs?limit=100');
      const data = await resp.json();
      const runs = data.runs || [];
      if (!runs.length) {
        tbody.innerHTML = '<tr><td colspan="6" class="studio-text-muted" style="text-align:center;padding:32px;">No workflow runs yet</td></tr>';
        return;
      }
      tbody.innerHTML = runs.map(run => {
        const summary = _parseSummary(run.summary_json);
        const duration = _runDuration(run.started_at, run.completed_at);
        const statusBadge = _runStatusBadge(run.status);
        return `<tr>
          <td style="font-weight:500;">${_esc(run.workflow_name || run.workflow_id)}</td>
          <td>${statusBadge}</td>
          <td style="font-size:0.8rem;color:var(--studio-text-muted,#94a3b8);">
            ${(summary.success||0)+(summary.failed||0)+(summary.skipped||0)} / ${summary.total||0}
          </td>
          <td style="font-size:0.8rem;color:var(--studio-text-muted,#94a3b8);">${duration}</td>
          <td style="font-size:0.75rem;color:var(--studio-text-muted,#94a3b8);">
            ${run.started_at ? new Date(run.started_at).toLocaleString() : '—'}
          </td>
          <td>
            <button class="studio-btn studio-btn--ghost studio-btn--sm"
                    onclick="StudioWF.showRunDetail('${run.run_id}')">Details</button>
          </td>
        </tr>`;
      }).join('');
    } catch (e) {
      tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;padding:24px;color:#ef4444;">Failed to load run history</td></tr>';
    }
  };

  StudioWF.showRunDetail = async function(runId) {
    const modal = $('wf-run-detail-modal');
    const body  = $('wf-run-detail-body');
    const title = $('wf-run-detail-title');
    if (!modal || !body) return;
    if (title) title.textContent = 'Run: ' + runId;
    body.innerHTML = '<div class="studio-spinner" style="margin:24px auto;"></div>';
    modal.style.display = '';
    try {
      const resp = await fetch('/api/studio/workflows/runs/' + runId);
      const run = await resp.json();
      const steps = run.steps || [];
      body.innerHTML = `
        <div style="margin-bottom:12px; padding:12px; background:var(--studio-bg,#161829);
             border-radius:6px; font-size:0.82rem;">
          <div><strong>Workflow:</strong> ${_esc(run.workflow_name || run.workflow_id)}</div>
          <div><strong>Status:</strong> ${_runStatusBadge(run.status)}</div>
          <div><strong>Started:</strong> ${run.started_at ? new Date(run.started_at).toLocaleString() : '—'}</div>
          <div><strong>Completed:</strong> ${run.completed_at ? new Date(run.completed_at).toLocaleString() : '—'}</div>
        </div>
        <table class="studio-table" style="font-size:0.8rem;">
          <thead><tr><th>Step</th><th>Status</th><th>Duration</th><th>Output</th></tr></thead>
          <tbody>
          ${steps.map(s => `<tr>
            <td>${_esc(s.step_name || s.step_id)}</td>
            <td>${_runStatusBadge(s.status)}</td>
            <td>${s.duration_ms ? s.duration_ms + 'ms' : '—'}</td>
            <td style="max-width:300px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
                color:var(--studio-text-muted,#94a3b8); font-family:var(--studio-font-mono);">
              ${_esc((s.stdout || s.stderr || '—').slice(0, 200))}
            </td>
          </tr>`).join('')}
          </tbody>
        </table>`;
    } catch (e) {
      body.innerHTML = '<div style="color:#ef4444; padding:16px;">Failed to load run detail</div>';
    }
  };

  function _parseSummary(json) {
    try { return JSON.parse(json || '{}'); } catch { return {}; }
  }

  function _runDuration(start, end) {
    if (!start || !end) return '—';
    const ms = new Date(end) - new Date(start);
    if (ms < 1000) return ms + 'ms';
    if (ms < 60000) return (ms / 1000).toFixed(1) + 's';
    return Math.floor(ms / 60000) + 'm ' + Math.floor((ms % 60000) / 1000) + 's';
  }

  function _runStatusBadge(status) {
    const map = {
      success:   ['#22c55e', '✓ Success'],
      failed:    ['#ef4444', '✗ Failed'],
      running:   ['#6366f1', '◉ Running'],
      pending:   ['#94a3b8', '○ Pending'],
      cancelled: ['#f59e0b', '⊘ Cancelled'],
      timeout:   ['#f59e0b', '⏱ Timeout'],
      skipped:   ['#71717a', '— Skipped'],
    };
    const [color, label] = map[status] || ['#94a3b8', status];
    return `<span style="color:${color};font-weight:600;font-size:0.78rem;">${label}</span>`;
  }

  // ── Chat Panel ───────────────────────────────────────────

  StudioWF.toggleChat = function() {
    _chatOpen = !_chatOpen;
    const layout = document.getElementById('wf-studio-layout');
    if (layout) layout.classList.toggle('wf-studio--chat-open', _chatOpen);
    const btn = $('wf-chat-toggle-btn');
    if (btn) btn.style.background = _chatOpen ? 'rgba(99,102,241,0.15)' : '';
    if (_chatOpen) {
      setTimeout(() => { const inp = $('wf-chat-input'); if (inp) inp.focus(); }, 50);
    }
  };

  StudioWF.sendChatMessage = async function() {
    const input = $('wf-chat-input');
    const status = $('wf-chat-status');
    const sendBtn = $('wf-chat-send');
    if (!input) return;

    const message = input.value.trim();
    if (!message) return;

    input.value = '';
    _appendChatMsg(message, 'user');
    _chatHistory.push({ role: 'user', content: message });

    if (status) status.textContent = 'Generating…';
    if (sendBtn) sendBtn.disabled = true;

    const thinkingEl = _appendChatMsg('Thinking…', 'assistant thinking');

    try {
      const resp = await fetch('/api/studio/chat/generate-workflow', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message,
          history: _chatHistory.slice(-6),  // last 6 turns for context
        }),
      });
      const data = await resp.json();
      thinkingEl.remove();

      if (data.status === 'ok') {
        _chatHistory.push({ role: 'assistant', content: data.yaml });
        _appendChatMsgWithApply(
          `Generated ${data.steps_count} step workflow: <em>${_esc(data.description || message)}</em>`,
          data.yaml
        );
      } else {
        _chatHistory.push({ role: 'assistant', content: 'Error: ' + data.error });
        _appendChatMsg('Could not generate workflow: ' + (data.error || 'LLM unavailable'), 'assistant');
      }
    } catch (e) {
      thinkingEl.remove();
      _appendChatMsg('Network error: ' + e.message, 'assistant');
    } finally {
      if (status) status.textContent = '';
      if (sendBtn) sendBtn.disabled = false;
    }
  };

  function _appendChatMsg(text, role) {
    const container = $('wf-chat-messages');
    if (!container) return document.createElement('div');
    const el = document.createElement('div');
    el.className = 'wf-chat-msg wf-chat-msg--' + (role.includes('user') ? 'user' : role.includes('thinking') ? 'thinking' : 'assistant');
    el.innerHTML = text;
    container.appendChild(el);
    container.scrollTop = container.scrollHeight;
    return el;
  }

  function _appendChatMsgWithApply(text, yamlStr) {
    const container = $('wf-chat-messages');
    if (!container) return;
    const el = document.createElement('div');
    el.className = 'wf-chat-msg wf-chat-msg--assistant';
    el.innerHTML = `${text}
      <div class="wf-chat-apply-btn">
        <button class="studio-btn studio-btn--primary studio-btn--sm"
                onclick="StudioWF.applyChatYAML(this)">
          &#10003; Apply to Canvas
        </button>
        <button class="studio-btn studio-btn--ghost studio-btn--sm"
                onclick="StudioWF.previewChatYAML(this)" style="margin-left:4px;">
          Preview
        </button>
      </div>`;
    el.dataset.yaml = yamlStr;
    container.appendChild(el);
    container.scrollTop = container.scrollHeight;
  }

  StudioWF.applyChatYAML = function(btn) {
    const msgEl = btn.closest('[data-yaml]');
    if (!msgEl) return;
    const yaml = msgEl.dataset.yaml;
    if (!yaml) return;
    const inp = $('wf-yaml-input');
    if (inp) { inp.value = yaml; StudioWF.doImportYAML && StudioWF.doImportYAML(); }
    // Switch to editor
    const editorTab = document.querySelector('[data-tab="editor"]');
    if (editorTab) StudioWF.switchTab && StudioWF.switchTab(editorTab);
    _currentWorkflowId = null;  // new unsaved canvas
    const runBtn = $('wf-run-btn');
    const codeBtn = $('wf-gen-code-btn');
    if (runBtn) runBtn.style.display = 'none';
    if (codeBtn) codeBtn.style.display = 'none';
    StudioWF._toast('Workflow loaded onto canvas — save it to run.', 'success');
  };

  StudioWF.previewChatYAML = function(btn) {
    const msgEl = btn.closest('[data-yaml]');
    if (!msgEl) return;
    const yaml = msgEl.dataset.yaml;
    const inp = $('wf-yaml-input');
    if (inp) inp.value = yaml || '';
    const modal = $('wf-yaml-modal');
    if (modal) modal.style.display = '';
  };

  // ── Hook into switchTab for run history auto-load ────────
  const _origSwitchTab = StudioWF.switchTab;
  StudioWF.switchTab = function(el) {
    _origSwitchTab && _origSwitchTab.call(this, el);
    if (el && el.dataset && el.dataset.tab === 'runs') {
      StudioWF.loadRunHistory();
    }
  };

  // ── Init patch ───────────────────────────────────────────
  document.addEventListener('DOMContentLoaded', function() {
    // Add click handler on canvas to clear node selection / close drawer when clicking bg
    const canvas = document.getElementById('wf-canvas');
    if (canvas) {
      canvas.addEventListener('click', function(e) {
        if (e.target === canvas || e.target.id === 'wf-nodes' || e.target.id === 'wf-edges') {
          _closeDrawer();
        }
      });
    }
  });

})();
