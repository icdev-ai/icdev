/**
 * ICDEV™ Studio — Automation Builder
 * Visual trigger → condition → action rule engine.
 */
const AutoBuilder = (() => {
  let triggers = [], actionTypes = [], operators = [];
  let selectedTrigger = null;
  let triggerConfig = {};
  let conditions = [];
  let actions = [];
  let savedId = null;

  const $ = id => document.getElementById(id);
  function toast(msg, type = 'info') {
    const c = $('studio-toasts'); if (!c) return;
    const el = document.createElement('div');
    el.className = `studio-toast studio-toast--${type}`;
    el.innerHTML = `<div class="studio-toast__message">${msg}</div>
      <button class="studio-toast__dismiss" onclick="this.parentElement.remove()">&times;</button>`;
    c.appendChild(el); setTimeout(() => el.remove(), 4000);
  }
  function esc(s) { const d = document.createElement('div'); d.textContent = s || ''; return d.innerHTML; }

  // ── Load definitions ──
  async function init() {
    try {
      const [tResp, aResp, oResp] = await Promise.all([
        fetch('/api/studio/automations/triggers'),
        fetch('/api/studio/automations/actions'),
        fetch('/api/studio/automations/operators'),
      ]);
      triggers = (await tResp.json()).triggers || [];
      actionTypes = (await aResp.json()).actions || [];
      operators = (await oResp.json()).operators || [];
      renderTriggerPicker();
    } catch (e) { console.error('Failed to load definitions:', e); }
  }

  function renderTriggerPicker() {
    const grid = $('at-trigger-grid');
    grid.innerHTML = triggers.map(t => `
      <button class="at-trigger-btn ${selectedTrigger?.id === t.id ? 'at-trigger-btn--selected' : ''}"
              onclick="AutoBuilder.selectTrigger('${esc(t.id)}')">
        <div class="at-trigger-icon" style="background:${t.color}20;color:${t.color};">${esc(t.icon)}</div>
        <div>
          <div style="font-weight:600;">${esc(t.label)}</div>
          <div style="font-size:0.65rem;color:var(--studio-text-muted);">${esc(t.description)}</div>
        </div>
      </button>
    `).join('');
  }

  function selectTrigger(id, config) {
    selectedTrigger = triggers.find(t => t.id === id) || null;
    triggerConfig = config ? JSON.parse(JSON.stringify(config)) : {};
    renderTriggerPicker();
    if (selectedTrigger) {
      $('at-trigger-selected').style.display = 'block';
      $('at-trigger-selected').innerHTML = `
        <div class="studio-flex studio-items-center studio-gap-3">
          <div class="at-trigger-icon" style="background:${selectedTrigger.color}20;color:${selectedTrigger.color};width:36px;height:36px;">
            ${esc(selectedTrigger.icon)}
          </div>
          <div>
            <div style="font-weight:600;color:var(--studio-text-primary);">${esc(selectedTrigger.label)}</div>
            <div class="studio-text-muted" style="font-size:0.8rem;">${esc(selectedTrigger.description)}</div>
          </div>
          <button class="studio-btn studio-btn--ghost studio-btn--sm" style="margin-left:auto;"
                  onclick="AutoBuilder.clearTrigger()">&times;</button>
        </div>
        ${renderTriggerConfig()}`;
      $('at-trigger-picker').style.display = 'none';
    }
  }

  // Triggers that carry configuration (e.g. external_event names an event
  // source) declare config_fields; everything else renders nothing.
  function renderTriggerConfig() {
    const fields = (selectedTrigger && selectedTrigger.config_fields) || [];
    if (!fields.length) return '';
    return `<div class="studio-mt-4" style="display:grid;gap:8px;">
      ${fields.map(f => `
        <label style="display:block;">
          <span class="studio-label">${esc(f.label)}${f.required ? ' *' : ''}</span>
          <input type="text" class="studio-input" style="width:100%;"
                 placeholder="${esc(f.placeholder || '')}" value="${esc(triggerConfig[f.name] || '')}"
                 onchange="AutoBuilder.updateTriggerConfig('${esc(f.name)}', this.value)">
        </label>`).join('')}
    </div>`;
  }

  function updateTriggerConfig(name, val) { triggerConfig[name] = val; }

  function clearTrigger() {
    selectedTrigger = null;
    triggerConfig = {};
    $('at-trigger-selected').style.display = 'none';
    $('at-trigger-picker').style.display = 'block';
    renderTriggerPicker();
  }

  // ── Conditions ──
  function addCondition() {
    conditions.push({ field: '', operator: 'equals', value: '' });
    renderConditions();
  }

  function renderConditions() {
    const container = $('at-conditions-list');
    if (!conditions.length) {
      container.innerHTML = '<div class="studio-text-muted" style="font-size:0.85rem;">No conditions — all events will trigger actions.</div>';
      return;
    }
    container.innerHTML = conditions.map((c, i) => `
      <div class="at-condition-row">
        <input type="text" class="studio-input" placeholder="field name" value="${esc(c.field)}"
               style="width:140px;" onchange="AutoBuilder.updateCondition(${i},'field',this.value)">
        <select class="studio-input studio-select" style="width:130px;"
                onchange="AutoBuilder.updateCondition(${i},'operator',this.value)">
          ${operators.map(o => `<option value="${o.id}" ${c.operator === o.id ? 'selected' : ''}>${esc(o.label)}</option>`).join('')}
        </select>
        <input type="text" class="studio-input" placeholder="value" value="${esc(c.value)}"
               style="flex:1;" onchange="AutoBuilder.updateCondition(${i},'value',this.value)">
        <button class="studio-btn studio-btn--ghost studio-btn--sm" onclick="AutoBuilder.removeCondition(${i})">&times;</button>
      </div>
    `).join('');
  }

  function updateCondition(idx, key, val) { conditions[idx][key] = val; }
  function removeCondition(idx) { conditions.splice(idx, 1); renderConditions(); }

  // ── Actions ──
  function showActionPicker() {
    const grid = $('at-action-picker-grid');
    grid.innerHTML = actionTypes.map(a => `
      <div class="studio-card studio-card--interactive" style="cursor:pointer;"
           onclick="AutoBuilder.addAction('${esc(a.id)}')">
        <div class="studio-card__body" style="display:flex;align-items:center;gap:12px;">
          <div class="at-action-item__icon" style="background:${a.color}20;color:${a.color};">${esc(a.icon)}</div>
          <div>
            <div class="at-action-item__label">${esc(a.label)}</div>
            <div class="at-action-item__desc">${esc(a.description)}</div>
          </div>
        </div>
      </div>
    `).join('');
    $('at-action-modal').style.display = '';
  }

  function addAction(typeId) {
    const at = actionTypes.find(a => a.id === typeId);
    if (!at) return;
    actions.push({ type: typeId, label: at.label, icon: at.icon, color: at.color, params: at.params || [], config: {} });
    renderActions();
    closeModal('at-action-modal');
    toast(`Action "${at.label}" added`, 'success');
  }

  function renderActions() {
    const container = $('at-actions-list');
    if (!actions.length) {
      container.innerHTML = '<div class="studio-text-muted" style="font-size:0.85rem;">No actions configured.</div>';
      return;
    }
    container.innerHTML = actions.map((a, i) => `
      <div class="at-action-item" style="flex-wrap:wrap;">
        <div class="at-action-item__icon" style="background:${a.color}20;color:${a.color};">${esc(a.icon)}</div>
        <div style="flex:1;">
          <div class="at-action-item__label">${esc(a.label)}</div>
        </div>
        <button class="studio-btn studio-btn--ghost studio-btn--sm" onclick="AutoBuilder.removeAction(${i})">&times;</button>
        ${(a.params || []).length ? `<div style="flex-basis:100%;display:grid;gap:6px;margin-top:8px;">
          ${(a.params || []).map(p => `
            <label style="display:flex;align-items:center;gap:8px;">
              <span class="studio-text-muted" style="font-size:0.75rem;width:110px;">${esc(p)}</span>
              <input type="text" class="studio-input" style="flex:1;" value="${esc(a.config[p] || '')}"
                     onchange="AutoBuilder.updateActionConfig(${i}, '${esc(p)}', this.value)">
            </label>`).join('')}
        </div>` : ''}
      </div>
    `).join('');
  }

  function updateActionConfig(idx, key, val) { actions[idx].config[key] = val; }

  function removeAction(idx) { actions.splice(idx, 1); renderActions(); }

  // ── Save ──
  async function save() {
    const name = $('at-name').value.trim() || 'Untitled Automation';
    if (!selectedTrigger) { toast('Select a trigger', 'warning'); return; }
    if (!actions.length) { toast('Add at least one action', 'warning'); return; }

    try {
      const resp = await fetch('/api/studio/automations', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name,
          trigger: { type: selectedTrigger.id, config: triggerConfig },
          conditions,
          actions: actions.map(a => ({ type: a.type, config: a.config })),
          description: `${selectedTrigger.label} → ${actions.length} action(s)`,
        }),
      });
      const data = await resp.json();
      if (data.status === 'ok') {
        savedId = data.automation_id;
        toast(`Automation "${name}" saved`, 'success');
      } else toast(data.error || 'Save failed', 'error');
    } catch (e) { toast('Network error', 'error'); }
  }

  // ── Simulate ──
  async function simulate() {
    if (!savedId) { toast('Save the automation first to simulate', 'warning'); return; }
    try {
      const resp = await fetch(`/api/studio/automations/${savedId}/simulate`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });
      const data = await resp.json();
      $('at-sim-body').innerHTML = `
        <div class="studio-flex studio-gap-3 studio-mb-4">
          <span class="studio-badge studio-badge--${data.trigger_matched ? 'success' : 'warning'}">
            Trigger: ${data.trigger_matched ? 'MATCHED' : 'NOT MATCHED'}
          </span>
          <span class="studio-badge studio-badge--${data.conditions_met ? 'success' : 'warning'}">
            Conditions: ${data.conditions_met ? 'MET' : 'NOT MET'}
          </span>
          <span class="studio-badge studio-badge--info">${(data.actions_preview || []).length} action(s)</span>
        </div>
        ${data.trigger_reason ? `<div class="studio-text-muted" style="font-size:0.75rem;margin-bottom:12px;">${esc(data.trigger_reason)}</div>` : ''}
        ${data.sample_event ? `<details style="margin-bottom:12px;"><summary class="studio-text-muted" style="font-size:0.75rem;cursor:pointer;">Sample event</summary>
          <pre style="font-size:0.7rem;white-space:pre-wrap;">${esc(JSON.stringify(data.sample_event, null, 2))}</pre></details>` : ''}
        ${(data.condition_results || []).map(cr => `
          <div style="font-size:0.8rem;margin-bottom:8px;color:${cr.met ? 'var(--studio-success)' : 'var(--studio-error)'};">
            ${cr.met ? '&#10003;' : '&#10007;'} ${esc(cr.field)} ${esc(cr.operator)} "${esc(cr.expected)}" (actual: "${esc(cr.actual)}")
          </div>
        `).join('')}
        <div class="studio-mt-4">
          <div class="studio-label">Actions that would execute:</div>
          ${(data.actions_preview || []).map(ap => `
            <div class="at-action-item" style="opacity:${ap.would_execute ? 1 : 0.4};">
              <div style="font-size:0.85rem;color:var(--studio-text-primary);">${esc(ap.type)}${
                ap.workflow_id ? ` &rarr; ${esc(ap.workflow_id)}` : ''}</div>
              <span class="studio-badge studio-badge--${ap.would_execute ? 'success' : 'neutral'}">
                ${ap.would_execute ? 'Would Execute' : 'Skipped'}
              </span>
            </div>
          `).join('')}
        </div>`;
      $('at-sim-modal').style.display = '';
    } catch (e) { toast('Simulation failed', 'error'); }
  }

  // ── Templates ──
  async function showTemplates() {
    try {
      const resp = await fetch('/api/studio/automations/templates');
      const data = await resp.json();
      const tpls = data.templates || [];
      $('at-templates-grid').innerHTML = tpls.map((tpl, i) => `
        <div class="mkt-asset studio-animate-in" style="animation-delay:${i * 40}ms;cursor:pointer;"
             onclick="AutoBuilder.useTemplate('${esc(tpl.id)}')">
          <div class="mkt-asset__banner" style="background:linear-gradient(135deg,#6366f1,#f59e0b);"></div>
          <div class="mkt-asset__body">
            <div class="mkt-asset__name">${esc(tpl.name)}</div>
            <div class="mkt-asset__description">${esc(tpl.description)}</div>
            <div class="mkt-asset__tags">
              <span class="studio-tag">${esc(tpl.trigger.type)}</span>
              <span class="studio-tag">${tpl.actions.length} action(s)</span>
            </div>
          </div>
        </div>
      `).join('');
      $('at-templates-modal').style.display = '';
    } catch (e) { toast('Failed to load templates', 'error'); }
  }

  async function useTemplate(tplId) {
    try {
      const resp = await fetch('/api/studio/automations/templates');
      const data = await resp.json();
      const tpl = (data.templates || []).find(t => t.id === tplId);
      if (!tpl) return;

      $('at-name').value = tpl.name;
      selectTrigger(tpl.trigger.type, tpl.trigger.config);
      conditions = JSON.parse(JSON.stringify(tpl.conditions || []));
      renderConditions();
      actions = tpl.actions.map(a => {
        const at = actionTypes.find(t => t.id === a.type) || {};
        return { type: a.type, label: at.label || a.type, icon: at.icon || '?', color: at.color || '#6b7280', params: at.params || [], config: a.config || {} };
      });
      renderActions();
      closeModal('at-templates-modal');
      toast(`Template "${tpl.name}" loaded`, 'success');
    } catch (e) { toast('Failed', 'error'); }
  }

  // ── Saved list ──
  async function loadSaved() {
    try {
      const resp = await fetch('/api/studio/automations');
      const data = await resp.json();
      const autos = data.automations || [];
      $('at-saved-count').textContent = autos.length;
      const grid = $('at-saved-grid');
      if (!autos.length) {
        grid.innerHTML = `<div class="studio-empty" style="grid-column:1/-1;"><div class="studio-empty__icon">&#9889;</div><div class="studio-empty__title">No automations</div></div>`;
        return;
      }
      grid.innerHTML = autos.map((a, i) => `
        <div class="studio-card studio-card--interactive studio-animate-in" style="animation-delay:${i * 40}ms;">
          <div class="studio-card__header">
            <div class="studio-card__header-title">${esc(a.name)}</div>
            <span class="studio-badge studio-badge--${a.enabled ? 'success' : 'neutral'}">${a.enabled ? 'Active' : 'Disabled'}</span>
          </div>
          <div class="studio-card__body">
            <div class="studio-text-muted" style="font-size:0.8rem;margin-bottom:8px;">${esc(a.description || '')}</div>
            <div class="studio-flex studio-gap-2">
              <span class="studio-tag">${esc(a.trigger?.type || '?')}</span>
              <span class="studio-tag">${(a.actions || []).length} action(s)</span>
            </div>
          </div>
          <div class="studio-card__footer">
            <button class="studio-btn studio-btn--ghost studio-btn--sm"
                    onclick="AutoBuilder.toggleAuto('${esc(a.automation_id)}', ${!a.enabled})">
              ${a.enabled ? 'Disable' : 'Enable'}
            </button>
            <button class="studio-btn studio-btn--danger studio-btn--sm"
                    onclick="AutoBuilder.deleteAuto('${esc(a.automation_id)}')">Delete</button>
          </div>
        </div>
      `).join('');
    } catch (e) { console.error(e); }
  }

  async function toggleAuto(id, enabled) {
    await fetch(`/api/studio/automations/${id}/toggle`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled }),
    });
    toast(enabled ? 'Enabled' : 'Disabled', 'success');
    loadSaved();
  }

  async function deleteAuto(id) {
    await fetch(`/api/studio/automations/${id}`, { method: 'DELETE' });
    toast('Deleted', 'info');
    loadSaved();
  }

  // ── Tabs ──
  function switchTab(el) {
    document.querySelectorAll('.studio-tab').forEach(t => t.classList.remove('studio-tab--active'));
    el.classList.add('studio-tab--active');
    document.querySelectorAll('.studio-tab-panel').forEach(p => p.style.display = 'none');
    $('panel-' + el.dataset.tab).style.display = '';
    if (el.dataset.tab === 'saved') loadSaved();
  }

  function createNew() {
    selectedTrigger = null; triggerConfig = {}; conditions = []; actions = []; savedId = null;
    $('at-name').value = 'Untitled Automation';
    clearTrigger(); renderConditions(); renderActions();
    const tab = document.querySelector('[data-tab="builder"]');
    if (tab) switchTab(tab);
  }

  function closeModal(id) { $(id).style.display = 'none'; }

  document.addEventListener('DOMContentLoaded', init);

  return {
    selectTrigger, clearTrigger, updateTriggerConfig, addCondition, updateCondition, removeCondition,
    showActionPicker, addAction, updateActionConfig, removeAction, save, simulate,
    showTemplates, useTemplate, loadSaved, toggleAuto, deleteAuto,
    switchTab, createNew, closeModal,
  };
})();
