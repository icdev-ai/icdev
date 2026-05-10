/**
 * ICDEV™ Studio — NL App Builder
 * "Describe what you want → get a working app"
 */

const AppBuilder = (() => {
  let sessionId = null;
  let blueprint = null;

  const $ = id => document.getElementById(id);

  const EXAMPLES = [
    "I need an app that tracks CDRL deliverables for my Navy contract, sends alerts when due dates approach, and generates CPARS-ready reports. It should be IL4 CUI compliant with FedRAMP Moderate controls.",
    "Build a compliance automation hub that runs continuous STIG checks, generates POA&Ms automatically, produces SSP documents, and tracks remediation status. Needs CMMC Level 2 and FedRAMP High assessment.",
    "Create a security scanning platform that performs SAST analysis, dependency vulnerability checks, secret detection, and container scanning. Should integrate with CI/CD pipelines and produce SBOM reports.",
  ];

  function toast(msg, type = 'info') {
    const container = $('studio-toasts');
    if (!container) return;
    const el = document.createElement('div');
    el.className = `studio-toast studio-toast--${type}`;
    el.innerHTML = `
      <div class="studio-toast__message">${msg}</div>
      <button class="studio-toast__dismiss" onclick="this.parentElement.remove()">&times;</button>`;
    container.appendChild(el);
    setTimeout(() => el.remove(), 5000);
  }

  function esc(s) {
    const d = document.createElement('div');
    d.textContent = s || '';
    return d.innerHTML;
  }

  // ── Examples ──
  function useExample(idx) {
    $('ab-input').value = EXAMPLES[idx] || '';
    $('ab-input').focus();
  }

  // ── Add message to chat ──
  function addMessage(text, type = 'system') {
    const msgs = $('ab-messages');
    const avatar = type === 'user' ? '&#128100;' : '&#129302;';
    const div = document.createElement('div');
    div.className = `ab-msg ab-msg--${type}`;
    div.innerHTML = `
      <div class="ab-msg__avatar">${avatar}</div>
      <div class="ab-msg__body">
        <div class="ab-msg__text">${text}</div>
      </div>`;
    msgs.appendChild(div);
    msgs.scrollTop = msgs.scrollHeight;
  }

  // ── Submit description ──
  async function submit() {
    const input = $('ab-input');
    const desc = input.value.trim();
    if (!desc) return;

    // Show user message
    addMessage(esc(desc), 'user');
    input.value = '';

    // Update status
    $('ab-status').textContent = 'Analyzing...';
    $('ab-status').className = 'studio-badge studio-badge--warning';
    $('ab-submit-btn').disabled = true;

    try {
      const resp = await fetch('/api/studio/app-builder/sessions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ description: desc }),
      });
      const data = await resp.json();

      if (data.status === 'error') {
        addMessage('Error: ' + esc(data.error), 'system');
        toast(data.error, 'error');
        return;
      }

      sessionId = data.session_id;
      blueprint = data.blueprint_preview;

      // Show AI response
      const msgHtml = (data.message || '')
        .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
        .replace(/\n/g, '<br>');
      addMessage(msgHtml, 'system');

      // Show preview panel
      renderPreview(data);

      $('ab-status').textContent = 'Preview Ready';
      $('ab-status').className = 'studio-badge studio-badge--success';
      $('ab-build-btn').disabled = false;
      toast('Blueprint generated — review and adjust', 'success');

    } catch (e) {
      addMessage('Network error: ' + esc(e.message), 'system');
      toast('Request failed', 'error');
    } finally {
      $('ab-submit-btn').disabled = false;
    }
  }

  // ── Render preview panel ──
  function renderPreview(data) {
    const preview = $('ab-preview');
    preview.style.display = 'block';
    // Force grid recalculation
    preview.closest('.ab-layout').style.gridTemplateColumns = '1fr 1fr';

    const bp = data.blueprint_preview;
    const extraction = data.extraction;

    // App name
    $('ab-app-name').value = bp.app_name || '';

    // Impact level
    document.querySelectorAll('.ab-il-btn').forEach(btn => {
      btn.classList.toggle('ab-il-btn--active', btn.dataset.il === bp.impact_level);
    });

    // Purpose
    $('ab-purpose').textContent = (extraction.purpose || 'general').replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());

    // Capabilities
    renderCapabilities(bp.capabilities);

    // Agents
    renderAgents(bp.agents || []);
  }

  function renderCapabilities(caps) {
    const grid = $('ab-cap-grid');
    const capLabels = {
      core: 'Core Framework', compliance: 'Compliance & ATO', security: 'Security Scanning',
      testing: 'Testing (TDD/BDD)', cicd: 'CI/CD Pipeline', knowledge: 'Knowledge Base',
      memory: 'Memory System', infrastructure: 'Infrastructure', maintenance: 'Maintenance',
      orchestration: 'Orchestration', mbse: 'MBSE / SysML', ricoas: 'Requirements Intake',
      supply_chain: 'Supply Chain Risk', simulation: 'Simulation', devsecops_zta: 'DevSecOps / ZTA',
      ai_security: 'AI Security', ai_governance: 'AI Governance', observability: 'Observability',
      rag: 'RAG / Search', fine_tuning: 'Fine-Tuning', knowledge_graph: 'Knowledge Graph',
      databridge: 'DataBridge', dashboard: 'Dashboard UI',
    };

    let html = '';
    let enabledCount = 0;
    for (const [key, enabled] of Object.entries(caps)) {
      const label = capLabels[key] || key;
      const cls = enabled ? 'ab-cap-item--on' : '';
      if (enabled) enabledCount++;
      html += `
        <div class="ab-cap-item ${cls}" data-cap="${key}"
             onclick="AppBuilder.toggleCap('${key}')">
          <div class="ab-cap-toggle">${enabled ? '&#10003;' : ''}</div>
          <span>${esc(label)}</span>
        </div>`;
    }
    grid.innerHTML = html;
    $('ab-cap-count').textContent = `(${enabledCount} enabled)`;
  }

  function renderAgents(agents) {
    const container = $('ab-agents');
    if (!agents.length) {
      container.innerHTML = '<div class="studio-text-muted" style="font-size:0.8rem;">No agents configured</div>';
      return;
    }
    container.innerHTML = agents.map(a => `
      <div class="ab-agent">
        <div class="ab-agent__name">${esc(a.name)}</div>
        <div class="ab-agent__role">${esc(a.role || '')}</div>
        <div class="ab-agent__port">:${a.actual_port || a.base_port}</div>
      </div>
    `).join('');
  }

  // ── Toggle capability ──
  async function toggleCap(capName) {
    if (!sessionId || !blueprint) return;

    const current = blueprint.capabilities[capName];
    blueprint.capabilities[capName] = !current;

    // Update UI immediately
    renderCapabilities(blueprint.capabilities);

    // Persist to server
    try {
      await fetch(`/api/studio/app-builder/sessions/${sessionId}/refine`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ toggle_capabilities: { [capName]: !current } }),
      });
    } catch (e) {
      // Revert on failure
      blueprint.capabilities[capName] = current;
      renderCapabilities(blueprint.capabilities);
      toast('Failed to update', 'error');
    }
  }

  // ── Update app name ──
  async function updateName(name) {
    if (!sessionId) return;
    try {
      await fetch(`/api/studio/app-builder/sessions/${sessionId}/refine`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ app_name: name }),
      });
      if (blueprint) blueprint.app_name = name;
    } catch (e) { /* silent */ }
  }

  // ── Set impact level ──
  async function setIL(il) {
    if (!sessionId) return;

    document.querySelectorAll('.ab-il-btn').forEach(btn => {
      btn.classList.toggle('ab-il-btn--active', btn.dataset.il === il);
    });

    try {
      const resp = await fetch(`/api/studio/app-builder/sessions/${sessionId}/refine`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ classification: il }),
      });
      const data = await resp.json();
      if (data.blueprint_preview) {
        blueprint = data.blueprint_preview;
        renderCapabilities(blueprint.capabilities);
        renderAgents(blueprint.agents || []);
      }
    } catch (e) {
      toast('Failed to update classification', 'error');
    }
  }

  // ── Build ──
  async function build() {
    if (!sessionId) return;

    const btn = $('ab-build-btn');
    btn.innerHTML = '<span class="studio-spinner studio-spinner--sm"></span> Building...';
    btn.disabled = true;
    $('ab-status').textContent = 'Building...';
    $('ab-status').className = 'studio-badge studio-badge--warning';

    addMessage('Starting build process... This will create a full ICDEV™ child application with all selected capabilities.', 'system');

    try {
      const resp = await fetch(`/api/studio/app-builder/sessions/${sessionId}/build`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });
      const data = await resp.json();

      if (data.status === 'error') {
        addMessage('Build failed: ' + esc(data.error), 'system');
        toast('Build failed: ' + data.error, 'error');
        btn.innerHTML = '&#128296; Retry Build';
        btn.disabled = false;
        $('ab-status').textContent = 'Build Failed';
        $('ab-status').className = 'studio-badge studio-badge--error';
        return;
      }

      const result = data.build_result || {};
      const stepSummary = Object.entries(result.steps || {})
        .map(([k, v]) => `${v.status === 'success' ? '&#10003;' : '&#10007;'} ${k}`)
        .join('<br>');

      addMessage(
        `<strong>Build Complete!</strong><br><br>` +
        `App: <strong>${esc(result.app_name || blueprint.app_name)}</strong><br>` +
        `Location: <code>${esc(result.child_root || 'N/A')}</code><br>` +
        `Status: <strong>${esc(result.status || 'unknown')}</strong><br>` +
        `Duration: ${(result.elapsed_seconds || 0).toFixed(1)}s<br><br>` +
        `<details><summary>Step Details</summary>${stepSummary}</details>`,
        'system'
      );

      toast('App built successfully!', 'success');
      btn.innerHTML = '&#10003; Built';
      $('ab-status').textContent = 'Built';
      $('ab-status').className = 'studio-badge studio-badge--success';

    } catch (e) {
      addMessage('Network error during build: ' + esc(e.message), 'system');
      toast('Build request failed', 'error');
      btn.innerHTML = '&#128296; Retry Build';
      btn.disabled = false;
      $('ab-status').textContent = 'Error';
      $('ab-status').className = 'studio-badge studio-badge--error';
    }
  }

  // ── Init ──
  document.addEventListener('DOMContentLoaded', () => {
    $('ab-input').focus();
  });

  return { submit, useExample, toggleCap, updateName, setIL, build };
})();
