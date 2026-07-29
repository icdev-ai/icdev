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
  StudioWF.loadWorkflow = async function(workflowId, runAfter = false) {
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
      if (runAfter) setTimeout(() => StudioWF.run(), 400);
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
        if (label && event.status === 'skipped') {
          label.textContent = `${event.step_name || event.step_id}: skipped — ${event.error || 'no tool path'}`;
        }

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
        const arts = event.artifacts || s.artifacts || [];
        let toastType, barColor, msg;
        if (overall === 'success') {
          toastType = 'success'; barColor = '#22c55e';
          msg = `Run complete: ${s.success || 0} passed, ${s.failed || 0} failed, ${s.skipped || 0} skipped`;
          if (arts.length) msg += ` — ${arts.length} artifact(s) generated`;
        } else if (overall === 'warning') {
          toastType = 'warning'; barColor = '#f59e0b';
          msg = `All ${s.skipped || 0} steps skipped — tool scripts not found. Click a node to see details, or configure tool paths.`;
        } else {
          toastType = 'error'; barColor = '#ef4444';
          msg = `Run failed: ${s.success || 0} passed, ${s.failed || 0} failed, ${s.skipped || 0} skipped`;
        }
        StudioWF._toast(msg, toastType);
        if (progress) progress.style.background = barColor;
        // Show artifact panel immediately on success
        if (arts.length) _showArtifactPanel(arts);
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
    const countEl = $('wf-runs-count');
    const masterCb = document.getElementById('wf-runs-select-all');
    if (!tbody) return;
    tbody.innerHTML = '<tr><td colspan="8" style="text-align:center;padding:24px;"><div class="studio-spinner"></div></td></tr>';
    if (masterCb) masterCb.checked = false;
    StudioWF._updateRunSelectionUI();
    try {
      const resp = await fetch('/api/studio/workflows/runs?limit=100');
      const data = await resp.json();
      const runs = data.runs || [];
      if (countEl) countEl.textContent = runs.length ? `${runs.length} run${runs.length !== 1 ? 's' : ''}` : '';
      if (!runs.length) {
        tbody.innerHTML = '<tr><td colspan="8" class="studio-text-muted" style="text-align:center;padding:32px;">No workflow runs yet</td></tr>';
        return;
      }
      tbody.innerHTML = runs.map(run => {
        const summary = _parseSummary(run.summary_json);
        const duration = _runDuration(run.started_at, run.completed_at);
        const effectiveStatus = (run.status === 'success' && summary.all_skipped) ? 'warning' : run.status;
        const statusBadge = _runStatusBadge(effectiveStatus);
        const rid = _esc(run.run_id);
        return `<tr id="run-row-${rid}">
          <td style="text-align:center;width:32px;">
            <input type="checkbox" class="wf-run-cb" data-run-id="${rid}"
                   style="cursor:pointer;accent-color:var(--studio-accent,#6366f1);"
                   onchange="StudioWF._updateRunSelectionUI()">
          </td>
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
                    onclick="StudioWF.showRunDetail('${rid}')">Details</button>
          </td>
          <td>
            <button class="studio-btn studio-btn--ghost studio-btn--sm"
                    title="Delete this run"
                    style="color:#f87171;border-color:#7f1d1d;"
                    onclick="StudioWF.deleteRun('${rid}')">&#128465;</button>
          </td>
        </tr>`;
      }).join('');
    } catch (e) {
      tbody.innerHTML = '<tr><td colspan="8" style="text-align:center;padding:24px;color:#ef4444;">Failed to load run history</td></tr>';
    }
  };

  StudioWF._updateRunSelectionUI = function() {
    const checkboxes = document.querySelectorAll('.wf-run-cb');
    const checked = document.querySelectorAll('.wf-run-cb:checked');
    const masterCb = document.getElementById('wf-runs-select-all');
    const btn = document.getElementById('wf-delete-selected-btn');
    const countSpan = document.getElementById('wf-selected-count');
    const n = checked.length;
    if (masterCb) {
      masterCb.indeterminate = n > 0 && n < checkboxes.length;
      masterCb.checked = checkboxes.length > 0 && n === checkboxes.length;
    }
    if (btn) btn.style.display = n > 0 ? '' : 'none';
    if (countSpan) countSpan.textContent = n;
  };

  StudioWF.toggleSelectAllRuns = function(checked) {
    document.querySelectorAll('.wf-run-cb').forEach(cb => { cb.checked = checked; });
    StudioWF._updateRunSelectionUI();
  };

  StudioWF.deleteRun = async function(runId) {
    if (!confirm('Delete this run record?')) return;
    try {
      const resp = await fetch('/api/studio/workflows/runs/' + encodeURIComponent(runId), { method: 'DELETE' });
      if (resp.ok) {
        const row = document.getElementById('run-row-' + runId);
        if (row) row.remove();
        const tbody = $('wf-runs-body');
        const countEl = $('wf-runs-count');
        const remaining = tbody ? tbody.querySelectorAll('tr[id^="run-row-"]').length : 0;
        if (countEl) countEl.textContent = remaining ? `${remaining} run${remaining !== 1 ? 's' : ''}` : '';
        if (!remaining && tbody) {
          tbody.innerHTML = '<tr><td colspan="8" class="studio-text-muted" style="text-align:center;padding:32px;">No workflow runs yet</td></tr>';
        }
        StudioWF._updateRunSelectionUI();
      } else {
        StudioWF._toast('Failed to delete run', 'error');
      }
    } catch (e) {
      StudioWF._toast('Network error deleting run', 'error');
    }
  };

  StudioWF.deleteSelectedRuns = async function() {
    const checked = document.querySelectorAll('.wf-run-cb:checked');
    const ids = Array.from(checked).map(cb => cb.dataset.runId);
    if (!ids.length) return;
    if (!confirm(`Delete ${ids.length} selected run${ids.length !== 1 ? 's' : ''}? This cannot be undone.`)) return;
    let deleted = 0, failed = 0;
    for (const id of ids) {
      try {
        const resp = await fetch('/api/studio/workflows/runs/' + encodeURIComponent(id), { method: 'DELETE' });
        if (resp.ok) {
          const row = document.getElementById('run-row-' + id);
          if (row) row.remove();
          deleted++;
        } else {
          failed++;
        }
      } catch (e) {
        failed++;
      }
    }
    const tbody = $('wf-runs-body');
    const countEl = $('wf-runs-count');
    const remaining = tbody ? tbody.querySelectorAll('tr[id^="run-row-"]').length : 0;
    if (countEl) countEl.textContent = remaining ? `${remaining} run${remaining !== 1 ? 's' : ''}` : '';
    if (!remaining && tbody) {
      tbody.innerHTML = '<tr><td colspan="8" class="studio-text-muted" style="text-align:center;padding:32px;">No workflow runs yet</td></tr>';
    }
    StudioWF._updateRunSelectionUI();
    if (deleted) StudioWF._toast(`Deleted ${deleted} run${deleted !== 1 ? 's' : ''}${failed ? `, ${failed} failed` : ''}`, failed ? 'error' : 'success');
  };

  StudioWF.deleteAllRuns = async function() {
    const tbody = $('wf-runs-body');
    const count = tbody ? tbody.querySelectorAll('tr[id^="run-row-"]').length : 0;
    if (!count) { StudioWF._toast('No runs to delete', 'info'); return; }
    if (!confirm(`Delete all ${count} run record${count !== 1 ? 's' : ''}? This cannot be undone.`)) return;
    try {
      const resp = await fetch('/api/studio/workflows/runs', { method: 'DELETE' });
      const data = await resp.json();
      if (resp.ok) {
        StudioWF._toast(`Deleted ${data.count} run${data.count !== 1 ? 's' : ''}`, 'success');
        StudioWF.loadRunHistory();
      } else {
        StudioWF._toast('Failed to delete runs', 'error');
      }
    } catch (e) {
      StudioWF._toast('Network error deleting runs', 'error');
    }
  };

  // Track active approval-poll interval per modal
  let _approvalPollTimer = null;

  StudioWF.showRunDetail = async function(runId) {
    const modal = $('wf-run-detail-modal');
    const body  = $('wf-run-detail-body');
    const title = $('wf-run-detail-title');
    if (!modal || !body) return;
    if (title) title.textContent = 'Run: ' + runId;
    body.innerHTML = '<div class="studio-spinner" style="margin:24px auto;"></div>';
    modal.style.display = '';
    if (_approvalPollTimer) { clearInterval(_approvalPollTimer); _approvalPollTimer = null; }
    await _renderRunDetail(runId, body);
  };

  async function _renderRunDetail(runId, body) {
    try {
      const resp = await fetch('/api/studio/workflows/runs/' + runId);
      const run = await resp.json();
      const steps = run.steps || [];
      const summary = _parseSummary(run.summary_json);
      const runArts = summary.artifacts || [];
      const detailStatus = (run.status === 'success' && summary.all_skipped) ? 'warning' : run.status;

      body.innerHTML = `
        <div style="margin-bottom:12px; padding:12px; background:var(--studio-bg,#161829);
             border-radius:6px; font-size:0.82rem;">
          <div><strong>Workflow:</strong> ${_esc(run.workflow_name || run.workflow_id)}</div>
          <div><strong>Status:</strong> ${_runStatusBadge(detailStatus)}</div>
          <div><strong>Started:</strong> ${run.started_at ? new Date(run.started_at).toLocaleString() : '—'}</div>
          <div><strong>Completed:</strong> ${run.completed_at ? new Date(run.completed_at).toLocaleString() : '—'}</div>
          ${_triggerBadge(run.trigger_event)}
          ${_resumeControl(runId, run.status)}
        </div>
        ${_artifactHtml(runArts)}
        <table class="studio-table" style="font-size:0.8rem;margin-top:12px;">
          <thead><tr><th>Step</th><th>Status</th><th>Duration</th><th>Output / Artifacts</th></tr></thead>
          <tbody>
          ${steps.map(s => {
            let stepArts = [];
            try { stepArts = JSON.parse(s.stdout || '{}').artifacts || []; } catch {}
            const artLinks = stepArts.map(a => {
              const viewUrl = `/api/artifacts/${(a.path || '').split('/').map(encodeURIComponent).join('/')}`;
              return `<a href="${viewUrl}" target="_blank" style="color:#60a5fa;font-size:10px;margin-right:4px;">${_esc(a.name)}</a>`;
            }).join('');
            const approvalBtns = s.status === 'awaiting_approval' ? `
              <div style="margin-top:6px;display:flex;gap:6px;align-items:center;">
                <span style="font-size:10px;color:#f59e0b;margin-right:4px;">⏳ Awaiting approval</span>
                <button onclick="StudioWF.approveStep('${runId}','${s.step_run_id}')"
                  style="font-size:10px;padding:3px 10px;background:#22c55e;color:#000;border:none;border-radius:4px;cursor:pointer;font-weight:700;">
                  ✓ Approve
                </button>
                <button onclick="StudioWF.rejectStep('${runId}','${s.step_run_id}')"
                  style="font-size:10px;padding:3px 10px;background:#ef4444;color:#fff;border:none;border-radius:4px;cursor:pointer;font-weight:700;">
                  ✗ Reject
                </button>
              </div>` : '';
            return `<tr>
              <td>${_esc(s.step_name || s.step_id)}</td>
              <td>${_runStatusBadge(s.status)}</td>
              <td>${s.duration_ms ? s.duration_ms + 'ms' : '—'}</td>
              <td style="max-width:320px;color:var(--studio-text-muted,#94a3b8);font-family:var(--studio-font-mono);font-size:10px;">
                ${artLinks || _esc((s.stdout || s.stderr || '—').slice(0, 150))}
                ${approvalBtns}
              </td>
            </tr>`;
          }).join('')}
          </tbody>
        </table>`;

      // Auto-poll every 4s while run is awaiting approval
      if (run.status === 'awaiting_approval') {
        if (!_approvalPollTimer) {
          _approvalPollTimer = setInterval(async () => {
            const modal = $('wf-run-detail-modal');
            if (!modal || modal.style.display === 'none') {
              clearInterval(_approvalPollTimer); _approvalPollTimer = null; return;
            }
            await _renderRunDetail(runId, body);
          }, 4000);
        }
      } else {
        if (_approvalPollTimer) { clearInterval(_approvalPollTimer); _approvalPollTimer = null; }
      }
    } catch (e) {
      body.innerHTML = '<div style="color:#ef4444; padding:16px;">Failed to load run detail</div>';
    }
  }

  // Mirrors RESUMABLE_RUN_STATUSES in tools/studio/workflow_runner.py.
  const _RESUMABLE_RUN_STATUSES = ['pending', 'running', 'awaiting_approval', 'failed'];

  /**
   * "Started by" line — the event that started this run (dwo-vv-03-d3).
   *
   * `run.trigger_event` is the `studio_trigger_events` row that
   * GET /api/studio/workflows/runs/<id> resolves for the run. A manually
   * started run has no such row, and this renders nothing at all — an empty
   * "Started by: —" would claim a linkage that does not exist.
   *
   * The ids are rendered as text, not as links: there is no trigger-event
   * detail route to link to, and a href that 404s reads as a feature.
   */
  function _triggerBadge(ev) {
    if (!ev || !ev.event_id) return '';
    const parts = [];
    if (ev.source_name || ev.source_id) parts.push(_esc(ev.source_name || ev.source_id));
    if (ev.event_type) parts.push(_esc(ev.event_type));
    const label = parts.length ? ` ${parts.join(' · ')}` : '';
    return `<div id="wf-run-trigger-badge" style="margin-top:6px;"
                 data-event-id="${_esc(ev.event_id)}"
                 data-trigger-id="${_esc(ev.trigger_id || '')}">
      <strong>Started by:</strong>
      <span class="studio-badge" title="This run was started by a workflow trigger, not by hand"
            style="background:#1e3a5f;color:#93c5fd;padding:1px 6px;border-radius:4px;
                   font-size:10px;font-weight:700;">&#9889; trigger${label}</span>
      <code style="font-family:var(--studio-font-mono);font-size:10px;
                   color:var(--studio-text-muted,#94a3b8);margin-left:4px;"
            title="studio_trigger_events.event_id">${_esc(ev.event_id)}</code>
    </div>`;
  }

  function _resumeControl(runId, status) {
    if (!_RESUMABLE_RUN_STATUSES.includes(status)) return '';
    return `<div style="margin-top:10px;">
      <button class="studio-btn studio-btn--sm" id="wf-resume-btn"
              title="Continue this run — steps that already succeeded are replayed, not re-executed"
              onclick="StudioWF.resumeRun('${_esc(runId)}')">&#9654; Resume Run</button>
    </div>`;
  }

  StudioWF.resumeRun = async function(runId) {
    if (!confirm('Resume this run?\n\nSteps that already succeeded are replayed, not re-executed. '
                 + 'Failed steps run again.')) return;
    const btn = document.getElementById('wf-resume-btn');
    if (btn) { btn.disabled = true; btn.textContent = '⏳ Resuming…'; }
    let data = {};
    let resp;
    try {
      resp = await fetch(`/api/studio/runs/${encodeURIComponent(runId)}/resume`, {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'
      });
      data = await resp.json().catch(() => ({}));
    } catch (e) {
      alert('Resume failed: ' + e);
      if (btn) { btn.disabled = false; btn.textContent = '▶ Resume Run'; }
      return;
    }
    if (!resp.ok) {
      alert('Resume failed: ' + (data.error || resp.status));
      if (btn) { btn.disabled = false; btn.textContent = '▶ Resume Run'; }
      return;
    }
    const body = $('wf-run-detail-body');
    if (body) {
      body.innerHTML = '<div class="studio-spinner" style="margin:24px auto;"></div>';
      setTimeout(() => _renderRunDetail(runId, body), 1200);
    }
  };

  StudioWF.approveStep = async function(runId, stepRunId) {
    if (!confirm('Approve this step and continue the workflow?')) return;
    const resp = await fetch(`/api/studio/workflows/runs/${runId}/steps/${stepRunId}/approve`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({actor: 'DBA'})
    });
    const data = await resp.json();
    if (!resp.ok) { alert('Approval failed: ' + (data.error || resp.status)); return; }
    const body = $('wf-run-detail-body');
    if (body) { body.innerHTML = '<div class="studio-spinner" style="margin:24px auto;"></div>'; }
    setTimeout(() => _renderRunDetail(runId, body), 800);
  };

  StudioWF.rejectStep = async function(runId, stepRunId) {
    const reason = prompt('Reason for rejection (optional):') ?? '';
    if (reason === null) return; // cancelled
    const resp = await fetch(`/api/studio/workflows/runs/${runId}/steps/${stepRunId}/reject`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({actor: 'DBA', reason})
    });
    const data = await resp.json();
    if (!resp.ok) { alert('Rejection failed: ' + (data.error || resp.status)); return; }
    const body = $('wf-run-detail-body');
    if (body) { body.innerHTML = '<div class="studio-spinner" style="margin:24px auto;"></div>'; }
    setTimeout(() => _renderRunDetail(runId, body), 800);
  };

  function _parseSummary(json) {
    try { return JSON.parse(json || '{}'); } catch { return {}; }
  }

  const _EXT_ICONS = { md: '📄', yaml: '📋', yml: '📋', py: '🐍', sh: '🖥', tfvars: '🏗', ini: '⚙', json: '{}', txt: '📝' };

  function _artifactHtml(arts) {
    if (!arts || !arts.length) return '';
    const items = arts.map(a => {
      const ext = (a.path || a.name || '').split('.').pop().toLowerCase();
      const icon = _EXT_ICONS[ext] || '📎';
      const name = a.name || a.path || 'Artifact';
      const viewUrl  = `/api/artifacts/${(a.path || '').split('/').map(encodeURIComponent).join('/')}`;
      const dlUrl    = `${viewUrl}?download=1`;
      return `<div style="display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid #1e3a6e;">
        <span style="font-size:16px;">${icon}</span>
        <span style="flex:1;font-size:11px;color:#eaeaea;">${_esc(name)}</span>
        <a href="${viewUrl}" target="_blank" style="font-size:10px;color:#60a5fa;text-decoration:none;padding:2px 6px;border:1px solid #60a5fa44;border-radius:3px;">View</a>
        <a href="${dlUrl}" style="font-size:10px;color:#34d399;text-decoration:none;padding:2px 6px;border:1px solid #34d39944;border-radius:3px;">↓ Download</a>
      </div>`;
    }).join('');
    return `<div style="margin-top:12px;padding:10px;background:#0d1b2a;border-radius:6px;border:1px solid #22c55e44;">
      <div style="font-size:11px;font-weight:700;color:#22c55e;margin-bottom:6px;">Generated Artifacts (${arts.length})</div>
      ${items}
    </div>`;
  }

  function _showArtifactPanel(arts) {
    const panel = document.getElementById('dc-panel-body') || document.getElementById('wf-artifact-panel');
    if (!panel) return;
    const existing = panel.querySelector('.wf-artifact-section');
    if (existing) existing.remove();
    const div = document.createElement('div');
    div.className = 'wf-artifact-section';
    div.innerHTML = _artifactHtml(arts);
    panel.prepend(div);
    const rightPanel = document.getElementById('dc-right-panel') || panel.closest('.dc-right-panel');
    if (rightPanel) rightPanel.style.display = '';
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
      warning:   ['#f59e0b', '⚠ All Skipped'],
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

  // ── Saved Workflows tab ─────────────────────────────────

  function _countSteps(yaml) {
    if (!yaml) return 0;
    const stepsMatch = yaml.match(/^steps:\s*\n([\s\S]*)/m);
    if (!stepsMatch) return 0;
    return (stepsMatch[1].match(/^\s{2}-\s/gm) || []).length;
  }

  function _categoryBadgeClass(category) {
    const map = {
      compliance: 'studio-badge--success',
      security:   'studio-badge--error',
      devops:     'studio-badge--info',
      research:   'studio-badge--accent',
    };
    return map[category] || 'studio-badge--neutral';
  }

  StudioWF.loadSavedWorkflows = async function() {
    const grid = $('wf-saved-grid');
    const countEl = $('wf-saved-count');
    if (!grid) return;
    grid.innerHTML = '<div class="studio-spinner studio-spinner--lg" style="margin:48px auto;grid-column:1/-1;"></div>';
    try {
      const resp = await fetch('/api/studio/workflows');
      const data = await resp.json();
      const wfs = data.workflows || [];
      if (countEl) countEl.textContent = wfs.length;
      if (!wfs.length) {
        grid.innerHTML = `<div class="studio-empty" style="grid-column:1/-1;">
          <div class="studio-empty__icon">&#128451;</div>
          <div class="studio-empty__title">No saved workflows</div>
          <div class="studio-empty__description">Create your first workflow in the editor tab.</div>
        </div>`;
        return;
      }
      grid.innerHTML = wfs.map(wf => {
        const steps = _countSteps(wf.template_yaml);
        const badgeCls = _categoryBadgeClass(wf.category);
        return `<div class="studio-card studio-card--interactive">
          <div class="studio-card__header">
            <span class="studio-badge ${badgeCls}">${_esc(wf.category || 'custom')}</span>
            <span style="font-size:0.75rem;color:var(--studio-text-muted,#94a3b8);">${steps} step${steps !== 1 ? 's' : ''}</span>
          </div>
          <div class="studio-card__body" style="padding:12px 16px;">
            <div style="font-weight:600;font-size:0.95rem;margin-bottom:4px;">${_esc(wf.name)}</div>
            <div style="font-size:0.8rem;color:var(--studio-text-muted,#94a3b8);min-height:32px;">${_esc(wf.description || '')}</div>
          </div>
          <div class="studio-card__footer" style="padding:8px 16px;gap:6px;">
            <button class="studio-btn studio-btn--secondary studio-btn--sm"
                    data-wf-id="${wf.workflow_id}"
                    onclick="StudioWF.loadWorkflow(this.dataset.wfId)">&#9998; Edit</button>
            <button class="studio-btn studio-btn--sm"
                    data-wf-id="${wf.workflow_id}"
                    onclick="StudioWF.loadWorkflow(this.dataset.wfId, true)"
                    style="background:#22c55e;border-color:#22c55e;color:#fff;font-weight:700;">&#9654; Run</button>
            <button class="studio-btn studio-btn--ghost studio-btn--sm"
                    data-wf-id="${wf.workflow_id}"
                    data-wf-name="${_esc(wf.name)}"
                    onclick="StudioWF.deleteWorkflow(this.dataset.wfId, this.dataset.wfName)"
                    style="color:#ef4444;margin-left:auto;">&#128465;</button>
          </div>
        </div>`;
      }).join('');
    } catch (e) {
      grid.innerHTML = '<div class="studio-empty" style="grid-column:1/-1;color:#ef4444;">Failed to load workflows</div>';
    }
  };

  StudioWF.deleteWorkflow = async function(workflowId, name) {
    if (!confirm(`Delete workflow "${name}"? This cannot be undone.`)) return;
    try {
      const resp = await fetch('/api/studio/workflows/' + encodeURIComponent(workflowId), {
        method: 'DELETE',
      });
      const data = await resp.json();
      if (data.status === 'ok') {
        StudioWF._toast(`Deleted "${name}"`, 'success');
        if (_currentWorkflowId === workflowId) {
          _currentWorkflowId = null;
          const runBtn = $('wf-run-btn');
          const codeBtn = $('wf-gen-code-btn');
          if (runBtn) runBtn.style.display = 'none';
          if (codeBtn) codeBtn.style.display = 'none';
        }
        StudioWF.loadSavedWorkflows();
      } else {
        StudioWF._toast(data.error || 'Delete failed', 'error');
      }
    } catch (e) {
      StudioWF._toast('Network error: ' + e.message, 'error');
    }
  };

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

  // ── Template Library ─────────────────────────────────────
  let _templatesLoaded = false;

  function _categoryColor(cat) {
    const map = {
      compliance: 'studio-badge--success',
      security:   'studio-badge--error',
      devops:     'studio-badge--info',
      research:   'studio-badge--accent',
      data:       'studio-badge--warning',
      ai:         'studio-badge--accent',
      monitoring: 'studio-badge--info',
    };
    return map[(cat || '').toLowerCase()] || 'studio-badge--neutral';
  }

  StudioWF.loadTemplates = async function() {
    const grid = $('wf-template-grid');
    if (!grid) return;
    grid.innerHTML = '<div class="studio-spinner studio-spinner--lg" style="margin:48px auto;grid-column:1/-1;"></div>';
    try {
      const resp = await fetch('/api/studio/workflows/templates');
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      const data = await resp.json();
      const templates = data.templates || [];

      const builtinEl  = $('tpl-builtin-count');
      const communityEl = $('tpl-community-count');
      const categoryEl  = $('tpl-category-count');
      if (builtinEl)   builtinEl.textContent  = data.builtin   ?? templates.filter(t => t.author === 'builtin').length;
      if (communityEl) communityEl.textContent = data.community ?? 0;
      if (categoryEl)  categoryEl.textContent  = Object.keys(data.categories || {}).length;

      if (!templates.length) {
        grid.innerHTML = `<div class="studio-empty" style="grid-column:1/-1;">
          <div class="studio-empty__icon">&#128196;</div>
          <div class="studio-empty__title">No templates found</div>
          <div class="studio-empty__description">Add YAML files to context/workflow_templates/</div>
        </div>`;
        return;
      }

      StudioWF._templateCache = {};
      templates.forEach(t => { StudioWF._templateCache[t.id] = t; });

      grid.innerHTML = templates.map(t => {
        const badgeCls = _categoryColor(t.category);
        const tags = (t.tags || []).slice(0, 3).map(tag =>
          `<span style="display:inline-block;padding:1px 6px;border-radius:3px;
                  font-size:0.68rem;background:rgba(99,102,241,0.15);
                  color:var(--studio-accent,#6366f1);margin-right:3px;">${_esc(tag)}</span>`
        ).join('');
        return `<div class="studio-card studio-card--interactive">
          <div class="studio-card__header">
            <span class="studio-badge ${badgeCls}">${_esc(t.category || 'general')}</span>
            <span style="font-size:0.75rem;color:var(--studio-text-muted,#94a3b8);">${t.steps_count || 0} step${(t.steps_count || 0) !== 1 ? 's' : ''}</span>
          </div>
          <div class="studio-card__body" style="padding:12px 16px;">
            <div style="font-weight:600;font-size:0.95rem;margin-bottom:4px;">${_esc(t.name)}</div>
            <div style="font-size:0.8rem;color:var(--studio-text-muted,#94a3b8);min-height:32px;margin-bottom:8px;">${_esc(t.description || '')}</div>
            <div>${tags}</div>
          </div>
          <div class="studio-card__footer" style="padding:8px 16px;">
            <button class="studio-btn studio-btn--primary studio-btn--sm"
                    data-tpl-id="${_esc(t.id)}"
                    onclick="StudioWF._installTemplateById(this)">&#128229; Load</button>
          </div>
        </div>`;
      }).join('');
    } catch (e) {
      grid.innerHTML = `<div class="studio-empty" style="grid-column:1/-1;color:#ef4444;">Failed to load templates: ${_esc(e.message)}</div>`;
    }
  };

  StudioWF._installTemplateById = function(btn) {
    const id = btn.dataset.tplId;
    const t = (StudioWF._templateCache || {})[id];
    if (!t) { StudioWF._toast('Template data not found', 'error'); return; }
    StudioWF.installTemplate(t);
  };

  StudioWF.installTemplate = function(t) {
    const inp = $('wf-yaml-input');
    if (inp) inp.value = t.yaml || '';
    if (StudioWF.doImportYAML) StudioWF.doImportYAML(t.yaml);

    const nameEl = $('wf-name');
    if (nameEl) nameEl.value = t.name || 'Untitled Workflow';

    const editorTab = document.querySelector('[data-tab="editor"]');
    if (editorTab) StudioWF.switchTab && StudioWF.switchTab(editorTab);

    _currentWorkflowId = null;
    const runBtn = $('wf-run-btn');
    const codeBtn = $('wf-gen-code-btn');
    if (runBtn) runBtn.style.display = 'none';
    if (codeBtn) codeBtn.style.display = 'none';

    StudioWF._toast(`Template "${t.name}" loaded — edit and save to run.`, 'success');
  };

  // ── Triggers panel (dwo-evt-04-d5) ───────────────────────

  // Parse a textarea that is allowed to be blank. Returns the sentinel on bad
  // JSON so the caller can tell "user left it empty" (valid, means none) from
  // "user typed something broken" (must not be sent as null and silently
  // become a trigger that matches everything).
  const _BAD_JSON = Symbol('bad-json');
  function _optionalJson(text) {
    const raw = (text || '').trim();
    if (!raw) return null;
    try { return JSON.parse(raw); } catch { return _BAD_JSON; }
  }

  function _triggerSourceOptions(sources) {
    return ['<option value="">Select a source…</option>'].concat(
      sources.map(s => {
        const label = s.name || s.source_id;
        const kind = s.channel || s.kind ? ` (${_esc(s.channel || s.kind)})` : '';
        return `<option value="${_esc(s.source_id)}">${_esc(label)}${kind}</option>`;
      })
    ).join('');
  }

  StudioWF.loadTriggers = async function() {
    const unsaved = $('wf-triggers-unsaved');
    const main    = $('wf-triggers-main');
    const countEl = $('wf-triggers-count');

    // A trigger references a stored workflow id, so there is nothing to bind
    // to until the workflow has been saved at least once.
    if (!_currentWorkflowId) {
      if (unsaved) unsaved.style.display = '';
      if (main) main.style.display = 'none';
      if (countEl) countEl.textContent = '0';
      return;
    }
    if (unsaved) unsaved.style.display = 'none';
    if (main) main.style.display = '';

    let data;
    try {
      const resp = await fetch('/api/studio/workflows/' + _currentWorkflowId + '/triggers');
      data = await resp.json();
    } catch (e) {
      StudioWF._toast('Could not load triggers: ' + e, 'error');
      return;
    }

    const sources  = data.sources  || [];
    const triggers = data.triggers || [];

    ['wf-trigger-source', 'wf-sim-source'].forEach(id => {
      const sel = $(id);
      if (!sel) return;
      const keep = sel.value;
      sel.innerHTML = _triggerSourceOptions(sources);
      if (keep) sel.value = keep;
    });

    if (countEl) countEl.textContent = String(triggers.length);

    const body = $('wf-triggers-body');
    if (!body) return;
    if (!triggers.length) {
      const msg = data.available === false
        ? 'Event sources are unavailable — run the database migrations to enable triggers.'
        : 'No triggers bound to this workflow';
      body.innerHTML = `<tr><td colspan="5" class="studio-text-muted"
        style="text-align:center; padding:32px;">${msg}</td></tr>`;
      return;
    }
    body.innerHTML = triggers.map(t => {
      const filter  = (t.event_filter && t.event_filter.length) ? JSON.stringify(t.event_filter) : '—';
      const mapping = (t.input_mapping && Object.keys(t.input_mapping).length)
        ? JSON.stringify(t.input_mapping) : '—';
      return `<tr data-trigger-id="${_esc(t.trigger_id)}">
        <td>${_esc(t.source_name || t.source_id)}</td>
        <td>${_esc(t.event_type || 'any')}</td>
        <td><code style="font-size:10px;">${_esc(filter)}</code></td>
        <td><code style="font-size:10px;">${_esc(mapping)}</code></td>
        <td>${t.enabled ? '&#10003;' : '&#10005;'}</td>
      </tr>`;
    }).join('');
  };

  StudioWF.createTrigger = async function() {
    if (!_currentWorkflowId) {
      StudioWF._toast('Save the workflow first.', 'error');
      return;
    }
    const sourceId = ($('wf-trigger-source') || {}).value || '';
    if (!sourceId) {
      StudioWF._toast('Choose an event source.', 'error');
      return;
    }
    const filter  = _optionalJson(($('wf-trigger-filter')  || {}).value);
    const mapping = _optionalJson(($('wf-trigger-mapping') || {}).value);
    if (filter === _BAD_JSON)  { StudioWF._toast('Event filter is not valid JSON.', 'error');  return; }
    if (mapping === _BAD_JSON) { StudioWF._toast('Input mapping is not valid JSON.', 'error'); return; }

    const btn = $('wf-trigger-create-btn');
    if (btn) btn.disabled = true;
    try {
      const resp = await fetch('/api/studio/workflows/' + _currentWorkflowId + '/triggers', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          source_id: sourceId,
          event_type: ($('wf-trigger-event-type') || {}).value || '',
          event_filter: filter,
          input_mapping: mapping,
        }),
      });
      const data = await resp.json();
      if (!resp.ok) {
        StudioWF._toast(data.error || 'Could not create trigger.', 'error');
        return;
      }
      StudioWF._toast('Trigger created.', 'success');
      await StudioWF.loadTriggers();
    } catch (e) {
      StudioWF._toast('Could not create trigger: ' + e, 'error');
    } finally {
      if (btn) btn.disabled = false;
    }
  };

  StudioWF.simulateTrigger = async function() {
    if (!_currentWorkflowId) {
      StudioWF._toast('Save the workflow first.', 'error');
      return;
    }
    const sourceId = ($('wf-sim-source') || {}).value || '';
    if (!sourceId) {
      StudioWF._toast('Choose an event source to simulate.', 'error');
      return;
    }
    const payload = _optionalJson(($('wf-sim-payload') || {}).value) || {};
    if (payload === _BAD_JSON) { StudioWF._toast('Payload is not valid JSON.', 'error'); return; }

    const out = $('wf-sim-result');
    const btn = $('wf-trigger-simulate-btn');
    if (btn) btn.disabled = true;
    if (out) out.innerHTML = '<span class="studio-text-muted">Dispatching…</span>';
    try {
      const resp = await fetch(
        '/api/studio/workflows/' + _currentWorkflowId + '/triggers/simulate', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            source_id: sourceId,
            event_type: ($('wf-sim-event-type') || {}).value || '',
            payload: payload,
          }),
        });
      const data = await resp.json();
      if (!resp.ok) {
        if (out) out.innerHTML = `<span style="color:#fca5a5;">${_esc(data.error || 'Simulate failed.')}</span>`;
        return;
      }
      const runs = data.runs || [];
      // A simulate that matches nothing is a result, not a failure — it is the
      // most common thing a user is trying to debug here, so it says why.
      if (!runs.length) {
        const why = (data.results || []).map(r => r.reason).filter(Boolean)[0]
          || 'no trigger on this source matched the event';
        if (out) out.innerHTML = `<div id="wf-sim-no-match" style="color:#fbbf24;">
          &#9888; No run started — ${_esc(why)}</div>`;
        return;
      }
      if (out) {
        out.innerHTML = `<div id="wf-sim-started" style="color:#86efac;">
          &#9889; Started ${runs.length} run${runs.length === 1 ? '' : 's'}:
          ${runs.map(r => `<a href="#" onclick="StudioWF.showRunDetail('${_esc(r)}');return false;"
             style="color:#60a5fa;">${_esc(r)}</a>`).join(', ')}</div>`;
      }
      StudioWF._toast(`Simulate started ${runs.length} run(s).`, 'success');
    } catch (e) {
      if (out) out.innerHTML = `<span style="color:#fca5a5;">${_esc(String(e))}</span>`;
    } finally {
      if (btn) btn.disabled = false;
    }
  };

  // ── Hook into switchTab for auto-load per tab ────────────
  const _origSwitchTab = StudioWF.switchTab;
  StudioWF.switchTab = function(el) {
    _origSwitchTab && _origSwitchTab.call(this, el);
    if (el && el.dataset) {
      if (el.dataset.tab === 'runs')  StudioWF.loadRunHistory();
      if (el.dataset.tab === 'saved') StudioWF.loadSavedWorkflows();
      if (el.dataset.tab === 'triggers') StudioWF.loadTriggers();
      if (el.dataset.tab === 'templates' && !_templatesLoaded) {
        _templatesLoaded = true;
        StudioWF.loadTemplates();
      }
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

    // Auto-load workflow when ?load=<workflow_id> is present in URL
    const loadId = new URLSearchParams(window.location.search).get('load');
    if (loadId) {
      StudioWF.loadWorkflow(loadId);
    }
  });

})();
