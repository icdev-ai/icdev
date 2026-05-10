/**
 * ICDEV™ Studio — Case Manager
 * Kanban board + lifecycle state machine for tracking cases.
 */
const CaseManager = (() => {
  let currentTypeId = null;
  let caseTypes = [];
  let boardData = null;

  const $ = id => document.getElementById(id);

  function toast(msg, type = 'info') {
    const c = $('studio-toasts');
    if (!c) return;
    const el = document.createElement('div');
    el.className = `studio-toast studio-toast--${type}`;
    el.innerHTML = `<div class="studio-toast__message">${msg}</div>
      <button class="studio-toast__dismiss" onclick="this.parentElement.remove()">&times;</button>`;
    c.appendChild(el);
    setTimeout(() => el.remove(), 4000);
  }

  function esc(s) { const d = document.createElement('div'); d.textContent = s || ''; return d.innerHTML; }

  // ── Load case types ──
  async function loadTypes() {
    try {
      const resp = await fetch('/api/studio/cases/types');
      const data = await resp.json();
      caseTypes = data.types || [];
      $('cm-types-count').textContent = caseTypes.length;

      const sel = $('cm-type-select');
      sel.innerHTML = '<option value="">Select a case type...</option>';
      caseTypes.forEach(ct => {
        sel.innerHTML += `<option value="${esc(ct.type_id)}">${esc(ct.name)} (${ct.states_count} states)</option>`;
      });

      if (caseTypes.length && !currentTypeId) {
        sel.value = caseTypes[0].type_id;
        selectType(caseTypes[0].type_id);
      }
    } catch (e) { console.error(e); }
  }

  // ── Select type & load board ──
  async function selectType(typeId) {
    if (!typeId) return;
    currentTypeId = typeId;
    await loadBoard();
  }

  async function loadBoard() {
    if (!currentTypeId) return;
    try {
      const resp = await fetch(`/api/studio/cases/types/${currentTypeId}/board`);
      boardData = await resp.json();
      if (boardData.status === 'error') {
        toast(boardData.error, 'error');
        return;
      }
      renderBoard();
      renderList();
    } catch (e) { toast('Failed to load board', 'error'); }
  }

  function renderBoard() {
    if (!boardData) return;
    const board = $('cm-board');
    const states = boardData.states || [];
    const columns = boardData.columns || {};

    if (!states.length) {
      board.innerHTML = `<div class="studio-empty"><div class="studio-empty__icon">&#128203;</div>
        <div class="studio-empty__title">No states defined</div></div>`;
      return;
    }

    board.innerHTML = states.map(s => {
      const cases = columns[s.id] || [];
      return `
        <div class="cm-column studio-animate-in">
          <div class="cm-column__header">
            <div class="cm-column__dot" style="background:${s.color || '#6b7280'};"></div>
            <span>${esc(s.label)}</span>
            <span class="cm-column__count">${cases.length}</span>
          </div>
          <div class="cm-column__body">
            ${cases.length ? cases.map(c => renderCaseCard(c)).join('') :
              `<div class="studio-text-muted" style="font-size:0.75rem;text-align:center;padding:16px;">No cases</div>`}
          </div>
        </div>`;
    }).join('');
  }

  function renderCaseCard(c) {
    return `
      <div class="cm-card" onclick="CaseManager.showDetail('${esc(c.case_id)}')">
        <div class="cm-card__title">${esc(c.title)}</div>
        <div class="cm-card__meta">
          <span class="cm-priority cm-priority--${c.priority}">${esc(c.priority)}</span>
          <span>${esc(c.assigned_to || 'Unassigned')}</span>
        </div>
      </div>`;
  }

  function renderList() {
    if (!boardData) return;
    const allCases = Object.values(boardData.columns || {}).flat();
    const body = $('cm-list-body');

    if (!allCases.length) {
      body.innerHTML = `<tr><td colspan="6" class="studio-text-muted" style="text-align:center;padding:32px;">No cases</td></tr>`;
      return;
    }

    const stateMap = {};
    (boardData.states || []).forEach(s => stateMap[s.id] = s);

    body.innerHTML = allCases.map(c => {
      const s = stateMap[c.current_state] || {};
      return `<tr onclick="CaseManager.showDetail('${esc(c.case_id)}')" style="cursor:pointer;">
        <td style="color:var(--studio-text-primary);font-weight:500;">${esc(c.title)}</td>
        <td><span class="studio-badge" style="background:${s.color || '#6b7280'}20;color:${s.color || '#6b7280'};">${esc(s.label || c.current_state)}</span></td>
        <td><span class="cm-priority cm-priority--${c.priority}">${esc(c.priority)}</span></td>
        <td>${esc(c.assigned_to || '-')}</td>
        <td>${c.updated_at ? new Date(c.updated_at).toLocaleDateString() : '-'}</td>
        <td></td>
      </tr>`;
    }).join('');
  }

  // ── Create case ──
  function createCase() {
    if (!currentTypeId) { toast('Select a case type first', 'warning'); return; }
    $('cm-case-title').value = '';
    $('cm-case-desc').value = '';
    $('cm-case-priority').value = 'medium';
    $('cm-case-assigned').value = '';
    $('cm-case-modal').style.display = '';
  }

  async function submitCase() {
    const title = $('cm-case-title').value.trim();
    if (!title) { toast('Title is required', 'warning'); return; }

    try {
      const resp = await fetch('/api/studio/cases', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          type_id: currentTypeId,
          title,
          description: $('cm-case-desc').value.trim(),
          priority: $('cm-case-priority').value,
          assigned_to: $('cm-case-assigned').value.trim() || null,
        }),
      });
      const data = await resp.json();
      if (data.status === 'ok') {
        toast('Case created', 'success');
        closeModal('cm-case-modal');
        await loadBoard();
      } else {
        toast(data.error || 'Failed', 'error');
      }
    } catch (e) { toast('Network error', 'error'); }
  }

  // ── Case detail ──
  async function showDetail(caseId) {
    try {
      const resp = await fetch(`/api/studio/cases/${caseId}`);
      const c = await resp.json();
      if (c.error) { toast(c.error, 'error'); return; }

      // Get available transitions
      const ct = caseTypes.find(t => t.type_id === c.type_id);
      let transitions = [];
      if (ct) {
        const lc = JSON.parse(ct.lifecycle_json || '{}');
        transitions = (lc.transitions || []).filter(t => t.from === c.current_state);
      }

      const stateMap = {};
      if (ct) {
        const lc = JSON.parse(ct.lifecycle_json || '{}');
        (lc.states || []).forEach(s => stateMap[s.id] = s);
      }

      const currentState = stateMap[c.current_state] || {};

      $('cm-detail-title').textContent = c.title;
      $('cm-detail-body').innerHTML = `
        <div class="studio-flex studio-items-center studio-gap-3 studio-mb-6">
          <span class="studio-badge" style="background:${currentState.color || '#6b7280'}20;color:${currentState.color || '#6b7280'};font-size:0.85rem;">
            ${esc(currentState.label || c.current_state)}
          </span>
          <span class="cm-priority cm-priority--${c.priority}" style="font-size:0.8rem;">${esc(c.priority)} priority</span>
          ${c.assigned_to ? `<span class="studio-text-muted">Assigned to: ${esc(c.assigned_to)}</span>` : ''}
        </div>

        ${c.description ? `<div class="studio-form-group">
          <label class="studio-label">Description</label>
          <p style="color:var(--studio-text-secondary);font-size:0.85rem;line-height:1.7;">${esc(c.description)}</p>
        </div>` : ''}

        ${transitions.length ? `
          <div class="studio-form-group">
            <label class="studio-label">Actions</label>
            <div class="studio-flex studio-gap-2" style="flex-wrap:wrap;">
              ${transitions.map(t => {
                const toState = stateMap[t.to] || {};
                return `<button class="studio-btn studio-btn--secondary studio-btn--sm"
                  onclick="CaseManager.doTransition('${esc(caseId)}','${esc(t.to)}')"
                  style="border-color:${toState.color || 'var(--studio-border)'};">
                  ${esc(t.label)} &rarr; ${esc(toState.label || t.to)}
                </button>`;
              }).join('')}
            </div>
          </div>` : ''}

        <div class="studio-form-group studio-mt-4">
          <label class="studio-label">History</label>
          ${(c.history || []).map(h => {
            const fromS = stateMap[h.from_state] || {};
            const toS = stateMap[h.to_state] || {};
            return `<div class="cm-history-item">
              <div class="cm-history-dot" style="background:${toS.color || 'var(--studio-accent)'};"></div>
              <div style="flex:1;">
                <div style="color:var(--studio-text-primary);font-weight:500;">
                  ${h.from_state ? `${esc(fromS.label || h.from_state)} &rarr; ` : ''}${esc(toS.label || h.to_state)}
                </div>
                ${h.comment ? `<div style="color:var(--studio-text-muted);">${esc(h.comment)}</div>` : ''}
                <div style="color:var(--studio-text-muted);font-size:0.7rem;">
                  ${h.changed_by || 'system'} &middot; ${h.changed_at ? new Date(h.changed_at).toLocaleString() : ''}
                </div>
              </div>
            </div>`;
          }).join('')}
        </div>
      `;
      $('cm-detail-modal').style.display = '';
    } catch (e) { toast('Failed to load case', 'error'); }
  }

  async function doTransition(caseId, toState) {
    try {
      const resp = await fetch(`/api/studio/cases/${caseId}/transition`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ to_state: toState }),
      });
      const data = await resp.json();
      if (data.status === 'ok') {
        toast(`Transitioned to ${toState}`, 'success');
        closeModal('cm-detail-modal');
        await loadBoard();
      } else {
        toast(data.error || 'Transition failed', 'error');
      }
    } catch (e) { toast('Network error', 'error'); }
  }

  // ── Templates ──
  async function showTemplates() {
    try {
      const resp = await fetch('/api/studio/cases/templates');
      const data = await resp.json();
      const templates = data.templates || [];
      $('cm-templates-grid').innerHTML = templates.map((tpl, i) => `
        <div class="mkt-asset studio-animate-in" style="animation-delay:${i * 40}ms;cursor:pointer;"
             onclick="CaseManager.useTemplate('${esc(tpl.id)}')">
          <div class="mkt-asset__banner" style="background:linear-gradient(135deg,${tpl.states[0]?.color || '#6366f1'},${tpl.states[tpl.states.length-1]?.color || '#22c55e'});"></div>
          <div class="mkt-asset__body">
            <div class="mkt-asset__name">${esc(tpl.name)}</div>
            <div class="mkt-asset__description">${esc(tpl.description)}</div>
            <div class="mkt-asset__tags">
              <span class="studio-tag">${tpl.states.length} states</span>
              <span class="studio-tag">${tpl.transitions.length} transitions</span>
              <span class="studio-tag">${esc(tpl.category)}</span>
            </div>
          </div>
        </div>
      `).join('');
      $('cm-templates-modal').style.display = '';
    } catch (e) { toast('Failed to load templates', 'error'); }
  }

  async function useTemplate(tplId) {
    try {
      const resp = await fetch('/api/studio/cases/templates');
      const data = await resp.json();
      const tpl = (data.templates || []).find(t => t.id === tplId);
      if (!tpl) return;

      const createResp = await fetch('/api/studio/cases/types', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: tpl.name,
          lifecycle: { states: tpl.states, transitions: tpl.transitions, sla_rules: tpl.sla_rules || [] },
          description: tpl.description,
        }),
      });
      const result = await createResp.json();
      if (result.status === 'ok') {
        toast(`Case type "${tpl.name}" created`, 'success');
        closeModal('cm-templates-modal');
        await loadTypes();
        selectType(result.type_id);
        $('cm-type-select').value = result.type_id;
      } else {
        toast(result.error || 'Failed', 'error');
      }
    } catch (e) { toast('Failed', 'error'); }
  }

  // ── Case types tab ──
  async function renderTypesGrid() {
    const grid = $('cm-types-grid');
    if (!caseTypes.length) {
      grid.innerHTML = `<div class="studio-empty" style="grid-column:1/-1;">
        <div class="studio-empty__icon">&#9881;</div>
        <div class="studio-empty__title">No case types</div>
        <div class="studio-empty__description">Create one from a template.</div>
        <button class="studio-btn studio-btn--primary" onclick="CaseManager.showTemplates()">Browse Templates</button>
      </div>`;
      return;
    }
    grid.innerHTML = caseTypes.map((ct, i) => `
      <div class="studio-card studio-card--interactive studio-animate-in" style="animation-delay:${i * 40}ms;cursor:pointer;"
           onclick="CaseManager.selectType('${esc(ct.type_id)}');document.querySelector('[data-tab=board]').click();">
        <div class="studio-card__header">
          <div class="studio-card__header-title">${esc(ct.name)}</div>
        </div>
        <div class="studio-card__body">
          <div class="studio-flex studio-gap-2">
            <span class="studio-badge studio-badge--accent">${ct.states_count} states</span>
            <span class="studio-badge studio-badge--neutral">${ct.transitions_count} transitions</span>
          </div>
        </div>
      </div>
    `).join('');
  }

  // ── Tabs ──
  function switchTab(el) {
    document.querySelectorAll('.studio-tab').forEach(t => t.classList.remove('studio-tab--active'));
    el.classList.add('studio-tab--active');
    document.querySelectorAll('.studio-tab-panel').forEach(p => p.style.display = 'none');
    $('panel-' + el.dataset.tab).style.display = '';
    if (el.dataset.tab === 'types') renderTypesGrid();
  }

  function closeModal(id) { $(id).style.display = 'none'; }
  function applyFilters() { loadBoard(); }

  // ── Init ──
  document.addEventListener('DOMContentLoaded', loadTypes);

  return { selectType, createCase, submitCase, showDetail, doTransition,
           showTemplates, useTemplate, switchTab, closeModal, applyFilters };
})();
