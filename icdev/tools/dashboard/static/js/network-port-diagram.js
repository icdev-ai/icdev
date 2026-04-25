// CUI // SP-CTI — Network Migration Physical Port Diagram
// tools/dashboard/static/js/network-port-diagram.js
'use strict';

const PD = (() => {

  const PW = 22, PH = 16, GAP = 3;

  const COLOR = {
    mapped:       ['#1a5e2d', '#27ae60'],
    unmapped:     ['#1e3a6e', '#2d4a7e'],
    incompatible: ['#3d1515', '#a93226'],
    mgmt:         ['#1e2e50', '#3a5080'],
  };

  const CABLE_PALETTE = [
    '#27ae60','#3498db','#e67e22','#9b59b6',
    '#e74c3c','#1abc9c','#f1c40f','#2ecc71',
    '#e91e63','#00bcd4','#ff9800','#8bc34a',
  ];

  function _esc(s) {
    return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
                          .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
  }

  function _isMgmt(name) {
    return !name || /^(fxp|me\d|em\d|mgmt|loopback|lo\d+)$/i.test(name.split('/').pop()) ||
      /fxp|me0|em0|mgmt|loopback/i.test(name);
  }

  function _portStatus(name, side, portMap, compat) {
    if (_isMgmt(name)) return 'mgmt';
    const isIncompat = compat && compat.some(c =>
      c.severity === 'error' && (c.src_interface === name || c.tgt_interface === name));
    if (isIncompat) return 'incompatible';
    if (side === 'src') {
      return portMap.some(r => r.src_interface === name || r.source_if === name) ? 'mapped' : 'unmapped';
    }
    return portMap.some(r => r.tgt_interface === name || r.target_if === name) ? 'mapped' : 'unmapped';
  }

  function _portRect(x, y, w, h, status, name, showLabel, labelText) {
    const [fill, stroke] = COLOR[status] || COLOR.unmapped;
    let s = `<rect class="pd-port" x="${x}" y="${y}" width="${w}" height="${h}" rx="2" ` +
      `fill="${fill}" stroke="${stroke}" stroke-width="1" cursor="pointer" data-name="${_esc(name)}"></rect>`;
    if (showLabel && labelText) {
      s += `<text x="${x + w / 2}" y="${y + h - 3}" text-anchor="middle" ` +
        `font-family="monospace" font-size="7" fill="#8ab" pointer-events="none">${_esc(labelText)}</text>`;
    }
    return s;
  }

  // ── MX304: 36×QSFP28 (100GE) in 2 rows of 18 + 2×QSFP-DD (400GE) ──────
  function renderMX304(ports, portMap, compat, showLabels, portData) {
    const COLS = 18;
    const data100 = ports.filter(p => !_isMgmt(p.name) && !p.is400g);
    const data400 = ports.filter(p => p.is400g);
    const mgmtP   = ports.filter(p => _isMgmt(p.name) && p.name);

    const Q400W = PW * 2 + 2;
    const W = 8 + COLS * (PW + GAP) - GAP + 14 + Q400W + 10;
    const H = 10 + 18 + 2 * (PH + GAP) + 4 + PH + 10;

    let s = `<rect x="0" y="0" width="${W}" height="${H}" fill="#0d1520" stroke="#2d4a7e" stroke-width="1.5" rx="4"></rect>`;
    s += `<text x="8" y="14" font-family="monospace" font-size="10" fill="#4a6080" font-weight="bold">MX304</text>`;
    s += `<text x="${W - 8}" y="14" font-family="monospace" font-size="8" fill="#2a3a50" text-anchor="end">2U · 36×100GE + 2×400GE</text>`;

    // 100GE ports — 2 rows of 18
    const pX = 8, pY = 18;
    data100.slice(0, COLS * 2).forEach((p, i) => {
      const row = Math.floor(i / COLS), col = i % COLS;
      const x = pX + col * (PW + GAP), y = pY + row * (PH + GAP);
      const st = _portStatus(p.name, p._side, portMap, compat);
      const lbl = p.name.split('/').pop();
      portData.set(p.name, p);
      s += _portRect(x, y, PW, PH, st, p.name, showLabels, lbl);
    });

    // 400GE QSFP-DD ports (taller, right side)
    const q400X = pX + COLS * (PW + GAP) - GAP + 14;
    s += `<text x="${q400X}" y="${pY - 2}" font-family="monospace" font-size="7" fill="#3a5070">QSFP-DD</text>`;
    data400.slice(0, 2).forEach((p, i) => {
      const y = pY + i * (PH * 2 + GAP + 1);
      const st = _portStatus(p.name, p._side, portMap, compat);
      const [fill, stroke] = COLOR[st] || COLOR.unmapped;
      portData.set(p.name, p);
      s += `<rect class="pd-port" x="${q400X}" y="${y}" width="${Q400W}" height="${PH * 2}" rx="2" ` +
        `fill="${fill}" stroke="${stroke}" stroke-width="1.5" cursor="pointer" data-name="${_esc(p.name)}"></rect>`;
      s += `<text x="${q400X + Q400W / 2}" y="${y + PH + 2}" text-anchor="middle" ` +
        `font-family="monospace" font-size="7" fill="#9ab" pointer-events="none">400G</text>`;
    });

    // Mgmt strip
    const mgmtY = pY + 2 * (PH + GAP) + 6;
    s += `<text x="8" y="${mgmtY + 12}" font-family="monospace" font-size="8" fill="#3a5070">MGMT</text>`;
    mgmtP.slice(0, 4).forEach((p, i) => {
      portData.set(p.name, p);
      s += _portRect(44 + i * (PW + GAP + 2), mgmtY, PW, PH, 'mgmt', p.name, false, '');
    });

    return { svg: s, width: W, height: H };
  }

  // ── MX10003: 3 MPC slot bays, ports grouped by FPC index ─────────────────
  function renderMX10003(ports, portMap, compat, showLabels, portData) {
    const SLOT_COUNT = 3;
    const SLOT_LBL_W = 44;
    const PORT_COLS = 12;
    const SLOT_PORT_ROWS = 2;
    const SLOT_INNER_H = 14 + SLOT_PORT_ROWS * (PH + GAP);
    const SLOT_H = SLOT_INNER_H + 8;
    const W = SLOT_LBL_W + PORT_COLS * (PW + GAP) + 20;
    const H = 22 + SLOT_COUNT * (SLOT_H + GAP) + PH + 18;

    // Group by FPC index
    const fpcPorts = [[], [], []];
    const mgmtP = [];
    ports.forEach(p => {
      if (_isMgmt(p.name)) { if (p.name) mgmtP.push(p); return; }
      const m = p.name.match(/^(?:et|xe|ge|fe|ae|irb)-(\d)/);
      const fpc = m ? Math.min(parseInt(m[1]), SLOT_COUNT - 1) : 0;
      fpcPorts[fpc].push(p);
    });

    let s = `<rect x="0" y="0" width="${W}" height="${H}" fill="#0d1520" stroke="#2d4a7e" stroke-width="1.5" rx="4"></rect>`;
    s += `<text x="8" y="15" font-family="monospace" font-size="10" fill="#4a6080" font-weight="bold">MX10003</text>`;
    s += `<text x="${W - 8}" y="15" font-family="monospace" font-size="8" fill="#2a3a50" text-anchor="end">3×MPC Modular</text>`;

    const startY = 20;
    for (let slot = 0; slot < SLOT_COUNT; slot++) {
      const slotY = startY + slot * (SLOT_H + GAP);
      const slotPorts = fpcPorts[slot];

      s += `<rect x="4" y="${slotY}" width="${W - 8}" height="${SLOT_H}" fill="#0a1018" stroke="#1a2a40" stroke-width="1" rx="2"></rect>`;
      s += `<text x="8" y="${slotY + 12}" font-family="monospace" font-size="9" fill="#3a5070">MPC${slot}</text>`;
      s += `<text x="${SLOT_LBL_W - 4}" y="${slotY + 12}" font-family="monospace" font-size="7" fill="#1e2e40" text-anchor="end">fpc${slot}</text>`;

      const portAreaX = SLOT_LBL_W;
      const portAreaY = slotY + 14;
      slotPorts.slice(0, PORT_COLS * SLOT_PORT_ROWS).forEach((p, i) => {
        const col = i % PORT_COLS, row = Math.floor(i / PORT_COLS);
        const x = portAreaX + col * (PW + GAP), y = portAreaY + row * (PH + GAP);
        const st = _portStatus(p.name, p._side, portMap, compat);
        const lbl = p.name.replace(/^[a-z]+-\d+\/\d+\//, '');
        portData.set(p.name, p);
        s += _portRect(x, y, PW, PH, st, p.name, showLabels, lbl);
      });

      if (!slotPorts.length) {
        s += `<text x="${portAreaX + 4}" y="${portAreaY + 12}" font-family="monospace" font-size="8" fill="#1e2e40">empty slot</text>`;
      }
    }

    // Mgmt strip
    const mgmtY = startY + SLOT_COUNT * (SLOT_H + GAP) + 4;
    s += `<text x="8" y="${mgmtY + 12}" font-family="monospace" font-size="8" fill="#3a5070">MGMT</text>`;
    mgmtP.slice(0, 4).forEach((p, i) => {
      portData.set(p.name, p);
      s += _portRect(SLOT_LBL_W + i * (PW + GAP + 2), mgmtY, PW, PH, 'mgmt', p.name, false, '');
    });

    return { svg: s, width: W, height: H };
  }

  // ── Generic grid fallback for any other model ─────────────────────────────
  function renderGeneric(ports, portMap, compat, showLabels, portData, modelName) {
    const COLS = 12;
    const dataPorts = ports.filter(p => !_isMgmt(p.name));
    const mgmtP    = ports.filter(p => _isMgmt(p.name) && p.name);
    const rows = Math.max(1, Math.ceil(dataPorts.length / COLS));
    const W = 8 + COLS * (PW + GAP) + 12;
    const H = 22 + rows * (PH + GAP) + PH + 18;

    let s = `<rect x="0" y="0" width="${W}" height="${H}" fill="#0d1520" stroke="#2d4a7e" stroke-width="1.5" rx="4"></rect>`;
    s += `<text x="8" y="15" font-family="monospace" font-size="10" fill="#4a6080" font-weight="bold">${_esc(modelName || 'Device')}</text>`;

    dataPorts.forEach((p, i) => {
      const col = i % COLS, row = Math.floor(i / COLS);
      const x = 8 + col * (PW + GAP), y = 22 + row * (PH + GAP);
      const st = _portStatus(p.name, p._side, portMap, compat);
      const lbl = p.name.split('/').pop();
      portData.set(p.name, p);
      s += _portRect(x, y, PW, PH, st, p.name, showLabels, lbl);
    });

    const mgmtY = 22 + rows * (PH + GAP) + 6;
    mgmtP.slice(0, 6).forEach((p, i) => {
      portData.set(p.name, p);
      s += _portRect(8 + i * (PW + GAP + 2), mgmtY, PW, PH, 'mgmt', p.name, false, '');
    });

    return { svg: s, width: W, height: H };
  }

  // ── Main PortDiagram class ────────────────────────────────────────────────
  class PortDiagram {
    constructor(sessionId) {
      this.sessionId = sessionId;
      this.portMap = [];
      this.compat  = [];
      this.session = {};
      this.hw      = {};
      this.srcPorts = [];
      this.tgtPorts = [];
      this.showLabels = true;
      this.showCables = false;
      // portData maps: name -> port object, per side
      this._pd = { src: new Map(), tgt: new Map() };
    }

    async load() {
      const [sr, pr] = await Promise.all([
        fetch(`/migration-canvas/api/network-migration/${this.sessionId}`),
        fetch(`/migration-canvas/api/network-migration/${this.sessionId}/port-map`),
      ]);
      if (!sr.ok) throw new Error('Session load failed: ' + sr.statusText);
      const sd = await sr.json();
      const pd = pr.ok ? await pr.json() : {};
      this.session = sd.session || {};
      this.hw      = sd.hardware_profiles || {};
      this.portMap = pd.port_map || [];
      this.compat  = sd.compat_checks || [];
      this._buildPorts(sd.parsed_interfaces || []);
    }

    _buildPorts(parsedIfs) {
      this.srcPorts = this._expand(this.hw.source || {}, parsedIfs, 'src');
      this.tgtPorts = this._expand(this.hw.target || {}, [], 'tgt');
      // Merge parsed interfaces that hardware profile did not enumerate
      const inProfile = new Set(this.srcPorts.map(p => p.name));
      parsedIfs.forEach(ifc => {
        if (!inProfile.has(ifc.name)) {
          this.srcPorts.push({
            name: ifc.name, speed: ifc.speed || '', optic: ifc.optic_type || '',
            ip: ifc.ip_address || '', desc: ifc.description || '', _side: 'src',
          });
        }
      });
    }

    _expand(hw, parsedIfs, side) {
      const ports = [];
      const profs = hw.ports_json
        ? (typeof hw.ports_json === 'string' ? JSON.parse(hw.ports_json) : hw.ports_json)
        : [];
      profs.forEach(prof => {
        if (prof.note) return; // MX10003 modular placeholder — skip
        const prefix = prof.if_prefix || 'et-0/0/';
        const start  = prof.if_start || 0;
        const end    = typeof prof.if_end === 'number' ? prof.if_end : start + (prof.count || 1) - 1;
        const is400g = /400/i.test(prof.speed || '');
        for (let i = start; i <= end; i++) {
          const name = prefix + i;
          const ifc = parsedIfs.find(f => f.name === name) || {};
          ports.push({
            name, speed: prof.speed || '', optic: prof.type || '',
            ip: ifc.ip_address || '', desc: ifc.description || '',
            is400g, _side: side,
          });
        }
      });
      return ports;
    }

    _model(side) {
      const raw = side === 'src' ? this.session.src_model : this.session.tgt_model;
      return (raw || '').toUpperCase().trim();
    }

    _renderSide(svgId, ports, side) {
      const svgEl  = document.getElementById(svgId);
      const model  = this._model(side);
      const pd     = this._pd[side];
      pd.clear();

      let result;
      if (model === 'MX304') {
        result = renderMX304(ports, this.portMap, this.compat, this.showLabels, pd);
      } else if (model === 'MX10003') {
        result = renderMX10003(ports, this.portMap, this.compat, this.showLabels, pd);
      } else {
        const hwObj = side === 'src' ? (this.hw.source || {}) : (this.hw.target || {});
        result = renderGeneric(ports, this.portMap, this.compat, this.showLabels, pd,
          model || hwObj.model || 'Device');
      }

      svgEl.setAttribute('width',  result.width);
      svgEl.setAttribute('height', result.height);
      svgEl.innerHTML = result.svg;

      svgEl.querySelectorAll('.pd-port').forEach(el => {
        el.addEventListener('mouseenter', evt => this._showPop(evt, el, side));
        el.addEventListener('mouseleave', ()  => this._hidePop());
        el.addEventListener('click', () => this._clickPort(el, side));
      });
    }

    render() {
      document.getElementById('src-chassis-label').textContent =
        (this.session.src_device_name || 'Source') + ' — ' + (this.session.src_model || '');
      document.getElementById('tgt-chassis-label').textContent =
        (this.session.tgt_device_name || 'Target') + ' — ' + (this.session.tgt_model || '');
      this._renderSide('src-svg', this.srcPorts, 'src');
      this._renderSide('tgt-svg', this.tgtPorts, 'tgt');
      this._renderTable();
      this._renderCables();
      this._updateStats();
    }

    _getPort(name, side) {
      return this._pd[side].get(name) || { name, speed: '—', optic: '—', ip: '—', desc: '—' };
    }

    _showPop(evt, el, side) {
      const pop  = document.getElementById('popover');
      const name = el.dataset.name;
      const port = this._getPort(name, side);
      document.getElementById('pop-title').textContent = name;

      const keys = side === 'src'
        ? ['src_interface', 'source_if', 'tgt_interface', 'target_if']
        : ['tgt_interface', 'target_if', 'src_interface', 'source_if'];
      const map = this.portMap.find(r => r[keys[0]] === name || r[keys[1]] === name);
      const pairedIf = map ? (map[keys[2]] || map[keys[3]]) : null;

      const rows = [
        ['Speed',       port.speed || '—'],
        ['Optic',       port.optic || '—'],
        ['IP / Circuit',port.ip   || '—'],
        ['Description', port.desc || '—'],
        pairedIf ? [side === 'src' ? '→ Target' : '← Source', pairedIf] : null,
        map && map.optic_change ? ['Optic Change', `${map.optic_old||'?'} → ${map.optic_new||'?'}`] : null,
        map ? ['Status',   map.status   || '—'] : null,
        map && map.cable_id       ? ['Cable ID',  map.cable_id] : null,
        map && map.far_end_device ? ['Far End',   `${map.far_end_device}${map.far_end_port ? '/' + map.far_end_port : ''}`] : null,
      ].filter(Boolean);

      document.getElementById('pop-rows').innerHTML = rows.map(([l, v]) =>
        `<div class="popover-row"><span class="popover-lbl">${l}</span>` +
        `<span class="popover-val">${_esc(String(v))}</span></div>`
      ).join('');

      pop.style.display = 'block';
      const px = Math.min(evt.clientX + 14, window.innerWidth  - 250);
      const py = Math.min(evt.clientY + 14, window.innerHeight - 200);
      pop.style.left = px + 'px';
      pop.style.top  = py + 'px';
    }

    _hidePop() { document.getElementById('popover').style.display = 'none'; }

    _clickPort(el, side) {
      const name = el.dataset.name;
      const keys = side === 'src'
        ? ['src_interface', 'source_if', 'tgt_interface', 'target_if']
        : ['tgt_interface', 'target_if', 'src_interface', 'source_if'];
      const map = this.portMap.find(r => r[keys[0]] === name || r[keys[1]] === name);
      if (!map) return;
      const pairedName = map[keys[2]] || map[keys[3]];
      const otherSvg = document.getElementById(side === 'src' ? 'tgt-svg' : 'src-svg');
      // Clear prior highlights
      otherSvg.querySelectorAll('.pd-port').forEach(e => {
        e.style.filter = '';
        e.setAttribute('opacity', '1');
      });
      if (!pairedName) return;
      const selector = `[data-name="${CSS.escape(pairedName)}"]`;
      otherSvg.querySelectorAll(selector).forEach(e => {
        e.style.filter = 'brightness(2) saturate(1.5)';
        setTimeout(() => { e.style.filter = ''; }, 800);
      });
    }

    toggleLabels() {
      this.showLabels = !this.showLabels;
      document.getElementById('toggle-labels').classList.toggle('active', this.showLabels);
      this.render();
    }

    toggleCables() {
      this.showCables = !this.showCables;
      document.getElementById('toggle-cables').classList.toggle('active', this.showCables);
      this._renderCables();
    }

    _renderCables() {
      const overlay = document.getElementById('cable-overlay');
      if (!overlay) return;
      if (!this.showCables) { overlay.innerHTML = ''; return; }

      const grid = document.getElementById('chassis-grid');
      const srcSvg = document.getElementById('src-svg');
      const tgtSvg = document.getElementById('tgt-svg');
      const gridRect = grid.getBoundingClientRect();

      overlay.setAttribute('width',  gridRect.width);
      overlay.setAttribute('height', gridRect.height);

      let lines = '';
      let ci = 0;
      this.portMap.forEach(row => {
        const sName = row.src_interface || row.source_if;
        const tName = row.tgt_interface || row.target_if;
        if (!sName || !tName) return;
        const sEl = srcSvg.querySelector(`[data-name="${CSS.escape(sName)}"]`);
        const tEl = tgtSvg.querySelector(`[data-name="${CSS.escape(tName)}"]`);
        if (!sEl || !tEl) return;

        const sb = sEl.getBoundingClientRect();
        const tb = tEl.getBoundingClientRect();
        const x1 = sb.right  - gridRect.left;
        const y1 = (sb.top + sb.bottom) / 2 - gridRect.top;
        const x2 = tb.left   - gridRect.left;
        const y2 = (tb.top + tb.bottom) / 2 - gridRect.top;
        const mx = (x1 + x2) / 2;
        const color = CABLE_PALETTE[ci++ % CABLE_PALETTE.length];
        lines += `<path d="M${x1.toFixed(1)},${y1.toFixed(1)} ` +
          `C${mx.toFixed(1)},${y1.toFixed(1)} ${mx.toFixed(1)},${y2.toFixed(1)} ${x2.toFixed(1)},${y2.toFixed(1)}" ` +
          `fill="none" stroke="${color}" stroke-width="1.5" stroke-dasharray="4,3" opacity="0.75"></path>`;
      });
      overlay.innerHTML = lines;
    }

    _renderTable() {
      document.getElementById('detail-tbody').innerHTML = this.portMap.map(r => {
        const srcIf  = r.src_interface || r.source_if || '';
        const tgtIf  = r.tgt_interface || r.target_if || '';
        const srcOptic = r.src_optic_type || r.optic_old || r.optic_type || '—';
        const tgtOptic = r.tgt_optic_required || r.optic_new || r.optic_old || '—';
        const ip = r.src_ip_address || r.ip_address || '—';
        return `<tr>
          <td><code>${_esc(srcIf)}</code></td>
          <td>${r.speed_gbps ? r.speed_gbps + 'G' : '—'}</td>
          <td>${_esc(srcOptic)}</td>
          <td><code>${_esc(tgtIf)}</code></td>
          <td>${r.optic_change
            ? `<span class="badge badge-optic">${_esc(r.optic_new || '?')}</span>`
            : _esc(tgtOptic)}</td>
          <td>${_esc(ip)}</td>
          <td><span class="badge badge-${r.status === 'mapped' ? 'mapped' : 'unmapped'}">${r.status || '—'}</span></td>
        </tr>`;
      }).join('');
    }

    _updateStats() {
      const total  = this.portMap.length;
      const mapped = this.portMap.filter(r => r.status === 'mapped').length;
      const optic  = this.portMap.filter(r => r.optic_change).length;
      const el = document.getElementById('pd-stats');
      if (el) el.textContent = `${mapped}/${total} ports mapped · ${optic} optic change${optic !== 1 ? 's' : ''}`;
    }

    exportSvg() {
      const src  = document.getElementById('src-svg');
      const tgt  = document.getElementById('tgt-svg');
      const srcW = +src.getAttribute('width'),  srcH = +src.getAttribute('height');
      const tgtW = +tgt.getAttribute('width'),  tgtH = +tgt.getAttribute('height');
      const GAP = 40, PAD = 20, LBL = 20;
      const W = PAD + srcW + GAP + tgtW + PAD;
      const H = PAD + LBL + Math.max(srcH, tgtH) + PAD;
      const srcLabel = _esc(document.getElementById('src-chassis-label').textContent);
      const tgtLabel = _esc(document.getElementById('tgt-chassis-label').textContent);
      const xml = `<?xml version="1.0" encoding="utf-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" style="background:#0d1b2e;font-family:monospace">
  <text x="${PAD}" y="${PAD + 12}" fill="#7a8cb0" font-size="11">${srcLabel}</text>
  <g transform="translate(${PAD},${PAD + LBL})">${src.innerHTML}</g>
  <text x="${PAD + srcW + GAP}" y="${PAD + 12}" fill="#7a8cb0" font-size="11">${tgtLabel}</text>
  <g transform="translate(${PAD + srcW + GAP},${PAD + LBL})">${tgt.innerHTML}</g>
</svg>`;
      const blob = new Blob([xml], { type: 'image/svg+xml' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = `port-diagram-${this.sessionId}.svg`;
      a.click();
    }

    async exportDrawio() {
      try {
        const r = await fetch(
          `/migration-canvas/api/network-migration/${this.sessionId}/export-diagram?format=drawio`
        );
        if (!r.ok) { alert('DrawIO export failed: ' + r.statusText); return; }
        const blob = await r.blob();
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = `port-diagram-${this.sessionId}.drawio`;
        a.click();
      } catch (e) { alert('Export failed: ' + e.message); }
    }
  }

  return { PortDiagram };
})();
