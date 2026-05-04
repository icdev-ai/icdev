/**
 * ICDEV™ Studio — Form Builder
 * Drag-and-drop form designer with JSON Schema output.
 */
const FormBuilder = (() => {
  let fields = [];
  let editingFieldIdx = null;
  let fieldTypes = [];

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

  // ── Load field types ──
  async function loadFieldTypes() {
    try {
      const resp = await fetch('/api/studio/forms/field-types');
      const data = await resp.json();
      fieldTypes = data.field_types || [];
      renderPalette();
    } catch (e) {
      fieldTypes = [
        {type:'text',label:'Text',icon:'T'},{type:'textarea',label:'Long Text',icon:'P'},
        {type:'number',label:'Number',icon:'#'},{type:'date',label:'Date',icon:'D'},
        {type:'select',label:'Dropdown',icon:'V'},{type:'checkbox',label:'Checkbox',icon:'X'},
        {type:'email',label:'Email',icon:'@'},{type:'file',label:'File Upload',icon:'F'},
      ];
      renderPalette();
    }
  }

  function renderPalette() {
    const container = $('fb-field-types');
    container.innerHTML = fieldTypes.map(ft => `
      <button class="fb-field-type-btn" onclick="FormBuilder.addField('${ft.type}')">
        <div class="fb-field-type-icon">${esc(ft.icon)}</div>
        <span>${esc(ft.label)}</span>
      </button>
    `).join('');
  }

  // ── Add field ──
  function addField(type) {
    const ft = fieldTypes.find(t => t.type === type) || { type, label: type, icon: '?' };
    const field = {
      id: `f${Date.now().toString(36)}`,
      type: type,
      label: `${ft.label} Field`,
      required: false,
      placeholder: '',
      options: type === 'select' || type === 'multiselect' ? ['Option 1', 'Option 2'] : undefined,
    };
    fields.push(field);
    renderFields();
    if ($('fb-empty')) $('fb-empty').style.display = 'none';
    toast(`${ft.label} field added`, 'success');
  }

  // ── Render fields ──
  function renderFields() {
    const container = $('fb-fields-container');
    $('fb-field-count').textContent = `${fields.length} field${fields.length !== 1 ? 's' : ''}`;

    if (!fields.length) {
      container.innerHTML = `<div class="studio-empty" id="fb-empty">
        <div class="studio-empty__icon">&#128221;</div>
        <div class="studio-empty__title">Start Building</div>
        <div class="studio-empty__description">Click field types on the left to add them.</div>
      </div>`;
      return;
    }

    const ft_map = {};
    fieldTypes.forEach(ft => ft_map[ft.type] = ft);

    container.innerHTML = fields.map((f, i) => {
      const ft = ft_map[f.type] || { icon: '?', label: f.type };
      return `
        <div class="fb-field studio-animate-in" onclick="FormBuilder.editField(${i})" style="animation-delay:${i * 30}ms;">
          <div class="fb-field__icon">${esc(ft.icon)}</div>
          <div class="fb-field__label">${esc(f.label)}</div>
          <div class="fb-field__type">${esc(ft.label)}</div>
          ${f.required ? '<div class="fb-field__required">Required</div>' : ''}
          <div class="fb-field__actions">
            <button class="studio-btn studio-btn--ghost studio-btn--sm"
                    onclick="event.stopPropagation();FormBuilder.moveField(${i},-1)" ${i === 0 ? 'disabled' : ''}>&#9650;</button>
            <button class="studio-btn studio-btn--ghost studio-btn--sm"
                    onclick="event.stopPropagation();FormBuilder.moveField(${i},1)" ${i === fields.length - 1 ? 'disabled' : ''}>&#9660;</button>
          </div>
        </div>`;
    }).join('');
  }

  // ── Move field ──
  function moveField(idx, dir) {
    const newIdx = idx + dir;
    if (newIdx < 0 || newIdx >= fields.length) return;
    [fields[idx], fields[newIdx]] = [fields[newIdx], fields[idx]];
    renderFields();
  }

  // ── Edit field ──
  function editField(idx) {
    editingFieldIdx = idx;
    const f = fields[idx];
    const hasOptions = f.type === 'select' || f.type === 'multiselect';

    $('fb-field-modal-body').innerHTML = `
      <div class="studio-form-group">
        <label class="studio-label">Label</label>
        <input type="text" class="studio-input" id="fld-label" value="${esc(f.label)}">
      </div>
      <div class="studio-form-group">
        <label class="studio-label">Placeholder</label>
        <input type="text" class="studio-input" id="fld-placeholder" value="${esc(f.placeholder || '')}">
      </div>
      <div class="studio-form-group">
        <label class="studio-label">
          <input type="checkbox" id="fld-required" ${f.required ? 'checked' : ''}> Required
        </label>
      </div>
      ${hasOptions ? `
        <div class="studio-form-group">
          <label class="studio-label">Options (one per line)</label>
          <textarea class="studio-input studio-textarea" id="fld-options" rows="4">${(f.options || []).join('\n')}</textarea>
        </div>` : ''}
    `;

    $('fb-delete-field-btn').onclick = () => { deleteField(idx); closeFieldModal(); };
    $('fb-field-modal').style.display = '';
  }

  function saveFieldConfig() {
    if (editingFieldIdx === null) return;
    const f = fields[editingFieldIdx];
    f.label = $('fld-label').value.trim() || f.label;
    f.placeholder = $('fld-placeholder').value.trim();
    f.required = $('fld-required').checked;
    const optEl = $('fld-options');
    if (optEl) f.options = optEl.value.split('\n').map(o => o.trim()).filter(Boolean);
    closeFieldModal();
    renderFields();
    toast('Field updated', 'success');
  }

  function deleteField(idx) {
    fields.splice(idx, 1);
    renderFields();
    if (!fields.length) { const emptyEl = $('fb-empty'); if (emptyEl) emptyEl.style.display = ''; }
    toast('Field removed', 'info');
  }

  function closeFieldModal() { $('fb-field-modal').style.display = 'none'; editingFieldIdx = null; }

  // ── Save form ──
  async function save() {
    const name = $('fb-form-name').value.trim() || 'Untitled Form';
    if (!fields.length) { toast('Add at least one field', 'warning'); return; }

    try {
      const resp = await fetch('/api/studio/forms', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, fields, description: `${fields.length} fields` }),
      });
      const data = await resp.json();
      if (data.status === 'ok') toast(`Form "${name}" saved`, 'success');
      else toast(data.error || 'Save failed', 'error');
    } catch (e) { toast('Network error', 'error'); }
  }

  // ── Templates ──
  async function showTemplates() {
    try {
      const resp = await fetch('/api/studio/forms/templates');
      const data = await resp.json();
      const templates = data.templates || [];
      $('fb-templates-grid').innerHTML = templates.map((tpl, i) => `
        <div class="studio-card studio-card--interactive studio-animate-in" style="animation-delay:${i * 40}ms;cursor:pointer;"
             onclick="FormBuilder.useTemplate('${esc(tpl.id)}')">
          <div class="studio-card__header">
            <div class="studio-card__header-title">${esc(tpl.name)}</div>
            <span class="studio-badge studio-badge--accent">${tpl.fields.length} fields</span>
          </div>
          <div class="studio-card__body">
            <div class="studio-text-muted" style="font-size:0.8rem;">${esc(tpl.description)}</div>
            <div class="studio-mt-2"><span class="studio-tag">${esc(tpl.category)}</span></div>
          </div>
        </div>
      `).join('');
      $('fb-templates-modal').style.display = '';
    } catch (e) { toast('Failed to load templates', 'error'); }
  }

  async function useTemplate(tplId) {
    try {
      const resp = await fetch('/api/studio/forms/templates');
      const data = await resp.json();
      const tpl = (data.templates || []).find(t => t.id === tplId);
      if (!tpl) return;
      fields = JSON.parse(JSON.stringify(tpl.fields));
      $('fb-form-name').value = tpl.name;
      renderFields();
      $('fb-templates-modal').style.display = 'none';
      $('fb-empty') && ($('fb-empty').style.display = 'none');
      toast(`Template "${tpl.name}" loaded`, 'success');
    } catch (e) { toast('Failed to load template', 'error'); }
  }

  // ── Saved forms ──
  async function loadSaved() {
    try {
      const resp = await fetch('/api/studio/forms');
      const data = await resp.json();
      const forms = data.forms || [];
      $('fb-saved-count').textContent = forms.length;
      const grid = $('fb-saved-grid');
      if (!forms.length) {
        grid.innerHTML = `<div class="studio-empty" style="grid-column:1/-1;"><div class="studio-empty__icon">&#128221;</div><div class="studio-empty__title">No saved forms</div></div>`;
        return;
      }
      grid.innerHTML = forms.map((f, i) => `
        <div class="studio-card studio-card--interactive studio-animate-in" style="animation-delay:${i * 40}ms;">
          <div class="studio-card__header">
            <div class="studio-card__header-title">${esc(f.name)}</div>
            <span class="studio-badge studio-badge--${f.status === 'published' ? 'success' : 'neutral'}">${esc(f.status)}</span>
          </div>
          <div class="studio-card__body">
            <div class="studio-text-muted" style="font-size:0.8rem;">${esc(f.description || 'No description')}</div>
          </div>
          <div class="studio-card__footer">
            <span class="studio-text-muted" style="font-size:0.7rem;">v${f.version}</span>
          </div>
        </div>
      `).join('');
    } catch (e) { console.error(e); }
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
    fields = [];
    $('fb-form-name').value = 'Untitled Form';
    renderFields();
    const tab = document.querySelector('[data-tab="designer"]');
    if (tab) switchTab(tab);
  }

  function preview() {
    if (!fields.length) { toast('Add fields before previewing', 'warning'); return; }
    const formName = $('fb-form-name')?.value || 'Form Preview';

    const inputStyle = 'width:100%;padding:8px 10px;border:1px solid #374151;border-radius:6px;background:#1f2937;color:#f3f4f6;font-size:0.875rem;box-sizing:border-box;';

    const fieldHtml = fields.map(f => {
      const reqMark = f.required ? ' <span style="color:#ef4444;">*</span>' : '';
      const label = `<label style="display:block;margin-bottom:6px;font-size:0.8125rem;font-weight:500;color:#d1d5db;">${esc(f.label)}${reqMark}</label>`;
      let input;
      switch (f.type) {
        case 'textarea':
          input = `<textarea style="${inputStyle}" placeholder="${esc(f.placeholder||'')}" rows="3" ${f.required?'required':''}></textarea>`;
          break;
        case 'select':
        case 'multiselect': {
          const opts = (f.options||[]).map(o=>`<option>${esc(o)}</option>`).join('');
          input = `<select style="${inputStyle}" ${f.type==='multiselect'?'multiple':''}>${opts}</select>`;
          break;
        }
        case 'checkbox':
          return `<div style="display:flex;align-items:center;gap:8px;margin-bottom:14px;"><input type="checkbox" ${f.required?'required':''}><label style="font-size:0.875rem;color:#d1d5db;">${esc(f.label)}</label></div>`;
        case 'date':
          input = `<input type="date" style="${inputStyle}" ${f.required?'required':''}>`;
          break;
        case 'number':
          input = `<input type="number" style="${inputStyle}" placeholder="${esc(f.placeholder||'')}" ${f.required?'required':''}>`;
          break;
        case 'email':
          input = `<input type="email" style="${inputStyle}" placeholder="${esc(f.placeholder||f.label)}" ${f.required?'required':''}>`;
          break;
        case 'file':
          input = `<input type="file" style="${inputStyle}" ${f.required?'required':''}>`;
          break;
        default:
          input = `<input type="text" style="${inputStyle}" placeholder="${esc(f.placeholder||'')}" ${f.required?'required':''}>`;
      }
      return `<div style="margin-bottom:14px;">${label}${input}</div>`;
    }).join('');

    const overlay = document.createElement('div');
    overlay.className = 'fb-preview-overlay';
    overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.72);z-index:9999;display:flex;align-items:center;justify-content:center;';
    overlay.innerHTML = `
      <div style="background:#111827;border:1px solid #374151;border-radius:12px;padding:32px;max-width:540px;width:92%;max-height:82vh;overflow-y:auto;box-shadow:0 24px 60px rgba(0,0,0,0.5);">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:24px;">
          <h2 style="color:#f9fafb;font-size:1.125rem;font-weight:700;margin:0;">${esc(formName)}</h2>
          <button onclick="this.closest('.fb-preview-overlay').remove()" style="background:transparent;border:none;color:#9ca3af;font-size:1.5rem;cursor:pointer;line-height:1;">&times;</button>
        </div>
        <form onsubmit="event.preventDefault();this.closest('.fb-preview-overlay').remove();return false;">
          ${fieldHtml}
          <div style="margin-top:20px;padding-top:16px;border-top:1px solid #374151;">
            <button type="submit" style="background:#6366f1;color:#fff;border:none;padding:10px 28px;border-radius:6px;font-size:0.875rem;font-weight:600;cursor:pointer;">Submit</button>
            <button type="button" onclick="this.closest('.fb-preview-overlay').remove()" style="margin-left:8px;background:transparent;color:#9ca3af;border:1px solid #374151;padding:10px 20px;border-radius:6px;font-size:0.875rem;cursor:pointer;">Close</button>
          </div>
        </form>
      </div>`;
    overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });
    document.body.appendChild(overlay);
  }

  // ── Init ──
  document.addEventListener('DOMContentLoaded', loadFieldTypes);

  return { addField, editField, saveFieldConfig, deleteField, closeFieldModal,
           moveField, save, showTemplates, useTemplate, switchTab, createNew, preview };
})();
