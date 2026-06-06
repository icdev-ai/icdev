/**
 * ICDEV™ Studio — Dashboard Builder
 * Custom widget layouts with role-based defaults.
 */
const DashBuilder = (() => {
  let widgetTypes = [];
  let widgets = []; // [{id, widget_type, x, y, w, h, config}]
  let dashId = null;

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

  // ── Load widget types ──
  async function init() {
    try {
      const resp = await fetch('/api/studio/dashboards/widgets');
      const data = await resp.json();
      widgetTypes = data.widgets || [];
      renderPalette();
    } catch (e) { console.error(e); }
  }

  function renderPalette() {
    const container = $('db-widget-types');
    const categories = {};
    widgetTypes.forEach(wt => {
      const cat = wt.category || 'other';
      if (!categories[cat]) categories[cat] = [];
      categories[cat].push(wt);
    });

    let html = '';
    for (const [cat, wts] of Object.entries(categories)) {
      html += `<div style="font-size:0.6rem;font-weight:600;color:var(--studio-text-muted);text-transform:uppercase;letter-spacing:0.05em;padding:8px 12px 4px;margin-top:4px;">${esc(cat)}</div>`;
      for (const wt of wts) {
        html += `
          <button class="db-widget-btn" onclick="DashBuilder.addWidget('${esc(wt.id)}')">
            <div class="db-widget-icon">${esc(wt.icon)}</div>
            <span>${esc(wt.label)}</span>
          </button>`;
      }
    }
    container.innerHTML = html;
  }

  // ── Add widget ──
  function addWidget(typeId) {
    const wt = widgetTypes.find(w => w.id === typeId);
    if (!wt) return;

    const id = 'w' + Date.now().toString(36);
    const size = wt.default_size || { w: 4, h: 3 };

    // Find next available position (simple left-to-right, top-to-bottom)
    let y = 0;
    if (widgets.length) {
      const maxY = Math.max(...widgets.map(w => (w.y || 0) + (w.h || 3)));
      y = maxY;
    }

    widgets.push({
      id,
      widget_type: typeId,
      label: wt.label,
      icon: wt.icon,
      x: 0,
      y: y,
      w: size.w,
      h: size.h,
      config: { title: wt.label },
    });

    renderGrid();
    toast(`${wt.label} added`, 'success');
  }

  // ── Remove widget ──
  function removeWidget(id) {
    widgets = widgets.filter(w => w.id !== id);
    renderGrid();
    toast('Widget removed', 'info');
  }

  // ── Render grid ──
  function renderGrid() {
    const grid = $('db-grid');
    $('db-widget-count').textContent = `${widgets.length} widget${widgets.length !== 1 ? 's' : ''}`;

    if (!widgets.length) {
      grid.innerHTML = `<div class="studio-empty" id="db-empty" style="grid-column:1/-1;">
        <div class="studio-empty__icon">&#128202;</div>
        <div class="studio-empty__title">Start Building</div>
        <div class="studio-empty__description">Click widgets on the left, or start from a role-based default.</div>
      </div>`;
      return;
    }

    grid.innerHTML = widgets.map(w => {
      const spanCols = Math.min(w.w || 4, 12);
      const spanRows = w.h || 3;

      return `
        <div class="db-widget" style="grid-column: span ${spanCols}; min-height: ${spanRows * 50}px;"
             data-widget-id="${w.id}">
          <div class="db-widget__header">
            <div class="db-widget__title">${esc(w.config?.title || w.label)}</div>
            <button class="db-widget__remove" onclick="DashBuilder.removeWidget('${w.id}')">&times;</button>
          </div>
          <div class="db-widget__body">
            ${renderWidgetPreview(w)}
          </div>
        </div>`;
    }).join('');
  }

  function renderWidgetPreview(w) {
    switch (w.widget_type) {
      case 'metric_card':
        return `<div style="text-align:center;">
          <div class="db-widget__value">--</div>
          <div class="db-widget__placeholder">${esc(w.config?.title || 'Metric')}</div>
        </div>`;
      case 'gauge':
      case 'compliance_gauge':
        return `<div style="text-align:center;">
          <div style="width:60px;height:60px;border:4px solid var(--studio-accent);border-radius:50%;display:flex;align-items:center;justify-content:center;margin:0 auto;">
            <span style="font-size:1.1rem;font-weight:700;color:var(--studio-text-primary);">--%</span>
          </div>
          <div class="db-widget__placeholder studio-mt-2">${esc(w.config?.title || 'Score')}</div>
        </div>`;
      case 'bar_chart':
        return `<div style="display:flex;align-items:flex-end;gap:6px;height:80px;">
          ${[60,85,45,70,90,55,75].map(h => `<div style="width:12px;height:${h}%;background:var(--studio-accent);border-radius:2px;opacity:0.6;"></div>`).join('')}
        </div>`;
      case 'line_chart':
        return `<svg viewBox="0 0 120 40" style="width:100%;max-height:80px;"><polyline points="0,35 20,20 40,28 60,10 80,18 100,5 120,12" fill="none" stroke="var(--studio-accent)" stroke-width="2" opacity="0.6"/></svg>`;
      case 'pie_chart':
        return `<div style="width:60px;height:60px;border:6px solid var(--studio-accent);border-radius:50%;border-top-color:var(--studio-success);border-right-color:var(--studio-warning);margin:0 auto;"></div>`;
      case 'data_table':
        return `<div class="db-widget__placeholder">&#9776; Data Table<br><small>Rows from query</small></div>`;
      case 'status_list':
        return `<div style="text-align:left;width:100%;">
          ${['Active','Healthy','Warning'].map((s,i) => `<div style="display:flex;align-items:center;gap:8px;padding:4px 0;font-size:0.75rem;color:var(--studio-text-secondary);">
            <div style="width:6px;height:6px;border-radius:50%;background:${['var(--studio-success)','var(--studio-info)','var(--studio-warning)'][i]};"></div>${s}</div>`).join('')}
        </div>`;
      case 'alert_feed':
        return `<div class="db-widget__placeholder">&#128276; Alert Feed</div>`;
      case 'agent_grid':
        return `<div class="db-widget__placeholder">&#129302; Agent Status Grid</div>`;
      case 'case_board_mini':
        return `<div class="db-widget__placeholder">&#128203; Case Board</div>`;
      case 'workflow_status':
        return `<div class="db-widget__placeholder">&#9881; Workflow Status</div>`;
      case 'automation_feed':
        return `<div class="db-widget__placeholder">&#9889; Automation Feed</div>`;
      case 'markdown':
        return `<div class="db-widget__placeholder">&#128196; Markdown Content</div>`;
      case 'embed':
        return `<div class="db-widget__placeholder">&#127760; Embedded Content</div>`;
      default:
        return `<div class="db-widget__placeholder">${esc(w.label)}</div>`;
    }
  }

  // ── Role defaults ──
  async function createFromRole(role) {
    toggleRoleMenu();
    try {
      const resp = await fetch('/api/studio/dashboards/role-defaults');
      const defaults = await resp.json();
      const def = defaults[role];
      if (!def) { toast('No default for this role', 'warning'); return; }

      $('db-dash-name').value = def.name;
      widgets = def.widgets.map((w, i) => ({
        id: 'w' + i + Date.now().toString(36),
        widget_type: w.widget_type,
        label: (widgetTypes.find(wt => wt.id === w.widget_type) || {}).label || w.widget_type,
        icon: (widgetTypes.find(wt => wt.id === w.widget_type) || {}).icon || '?',
        x: w.x, y: w.y, w: w.w, h: w.h,
        config: w.config || {},
      }));
      renderGrid();
      toast(`${def.name} loaded`, 'success');
    } catch (e) { toast('Failed to load defaults', 'error'); }
  }

  function toggleRoleMenu() {
    const menu = $('db-role-menu');
    menu.style.display = menu.style.display === 'none' ? '' : 'none';
  }

  // ── Save ──
  async function save() {
    const name = $('db-dash-name').value.trim() || 'My Dashboard';
    if (!widgets.length) { toast('Add at least one widget', 'warning'); return; }

    const layout = widgets.map(w => ({
      widget_type: w.widget_type, x: w.x, y: w.y, w: w.w, h: w.h, config: w.config,
    }));

    const method = dashId ? 'PATCH' : 'POST';
    const url = dashId ? `/api/studio/dashboards/${dashId}` : '/api/studio/dashboards';

    try {
      const resp = await fetch(url, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, layout, shared: $('db-shared')?.checked || false }),
      });
      const data = await resp.json();
      if (data.status === 'ok') {
        dashId = data.dashboard_id || dashId;
        toast(`Dashboard "${name}" saved`, 'success');
      } else toast(data.error || 'Save failed', 'error');
    } catch (e) { toast('Network error', 'error'); }
  }

  // ── Saved dashboards ──
  async function loadSaved() {
    try {
      const resp = await fetch('/api/studio/dashboards');
      const data = await resp.json();
      const dashes = data.dashboards || [];
      $('db-saved-count').textContent = dashes.length;
      const grid = $('db-saved-grid');
      if (!dashes.length) {
        grid.innerHTML = `<div class="studio-empty" style="grid-column:1/-1;">
          <div class="studio-empty__icon">&#128202;</div>
          <div class="studio-empty__title">No saved dashboards</div></div>`;
        return;
      }
      grid.innerHTML = dashes.map((d, i) => `
        <div class="studio-card studio-card--interactive studio-animate-in" style="animation-delay:${i * 40}ms;">
          <div class="studio-card__header">
            <div class="studio-card__header-title">${esc(d.name)}</div>
            ${d.role_default ? `<span class="studio-badge studio-badge--accent">${esc(d.role_default)}</span>` : ''}
          </div>
          <div class="studio-card__body">
            <div class="studio-flex studio-gap-2">
              <span class="studio-badge studio-badge--neutral">${d.widget_count} widgets</span>
              ${d.shared ? '<span class="studio-badge studio-badge--info">Shared</span>' : ''}
            </div>
          </div>
          <div class="studio-card__footer">
            <span class="studio-text-muted" style="font-size:0.7rem;">
              ${d.updated_at ? new Date(d.updated_at).toLocaleDateString() : ''}
            </span>
            <button class="studio-btn studio-btn--secondary studio-btn--sm"
                    onclick="DashBuilder.loadDashboard('${esc(d.dashboard_id)}')">Open</button>
          </div>
        </div>
      `).join('');
    } catch (e) { console.error(e); }
  }

  async function loadDashboard(id) {
    try {
      const resp = await fetch(`/api/studio/dashboards/${id}`);
      const data = await resp.json();
      if (data.error) { toast(data.error, 'error'); return; }

      dashId = id;
      $('db-dash-name').value = data.name;
      const layout = data.layout || {};
      widgets = (layout.grid || []).map((w, i) => ({
        id: 'w' + i + Date.now().toString(36),
        widget_type: w.widget_type,
        label: (widgetTypes.find(wt => wt.id === w.widget_type) || {}).label || w.widget_type,
        icon: (widgetTypes.find(wt => wt.id === w.widget_type) || {}).icon || '?',
        x: w.x, y: w.y, w: w.w, h: w.h, config: w.config || {},
      }));
      renderGrid();
      const tab = document.querySelector('[data-tab="editor"]');
      if (tab) switchTab(tab);
      toast(`Dashboard loaded`, 'success');
    } catch (e) { toast('Failed to load', 'error'); }
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
    widgets = []; dashId = null;
    $('db-dash-name').value = 'My Dashboard';
    renderGrid();
    const tab = document.querySelector('[data-tab="editor"]');
    if (tab) switchTab(tab);
  }

  // Close role menu on click outside
  document.addEventListener('click', (e) => {
    if (!e.target.closest('.studio-dropdown')) {
      const m = $('db-role-menu');
      if (m) m.style.display = 'none';
    }
  });

  document.addEventListener('DOMContentLoaded', init);

  return { addWidget, removeWidget, createFromRole, toggleRoleMenu,
           save, loadSaved, loadDashboard, switchTab, createNew };
})();
