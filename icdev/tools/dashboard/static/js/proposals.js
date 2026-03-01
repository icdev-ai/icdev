// CUI // SP-CTI
// ICDEV Dashboard — Proposal Lifecycle Tracker (Zero Dependencies)
// Classification: CUI // SP-CTI
(function () {
  'use strict';

  var NS = window.ICDEV || (window.ICDEV = {});

  // ─── Helpers ─────────────────────────────────────────────────────────────

  function escapeHTML(s) {
    return ICDEV.escapeHTML ? ICDEV.escapeHTML(String(s || '')) : String(s || '');
  }

  function cssVar(name, fallback) {
    var v = getComputedStyle(document.documentElement).getPropertyValue(name);
    return v && v.trim() ? v.trim() : fallback;
  }

  function palette(key) {
    var map = {
      bg:        ['--bg-card',          '#16213e'],
      bgSec:     ['--bg-secondary',     '#1a1a2e'],
      border:    ['--border-color',     '#2a2a40'],
      muted:     ['--text-muted',       '#6c6c80'],
      secondary: ['--text-secondary',   '#a0a0b8'],
      primary:   ['--text-primary',     '#e0e0e0'],
      blue:      ['--accent-blue',      '#4a90d9'],
      green:     ['--status-green',     '#28a745'],
      red:       ['--status-red',       '#dc3545'],
      yellow:    ['--status-yellow',    '#ffc107'],
      purple:    ['--accent-purple',    '#9b59b6'],
      orange:    ['--accent-orange',    '#e67e22']
    };
    var entry = map[key];
    return entry ? cssVar(entry[0], entry[1]) : '#ffffff';
  }

  function svgNS(tag, attrs) {
    var el = document.createElementNS('http://www.w3.org/2000/svg', tag);
    if (attrs) Object.keys(attrs).forEach(function (k) { el.setAttribute(k, attrs[k]); });
    return el;
  }

  async function api(url, opts) {
    try {
      var resp = await fetch(url, opts || {});
      if (!resp.ok) {
        var err = await resp.json().catch(function () { return { error: resp.statusText }; });
        console.error('[Proposals]', resp.status, err);
        return null;
      }
      return await resp.json();
    } catch (e) {
      console.error('[Proposals] Network error', url, e);
      return null;
    }
  }

  function post(url, body) {
    return api(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  }

  function put(url, body) {
    return api(url, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  }

  function toast(msg, type) {
    if (ICDEV.showToast) { ICDEV.showToast(msg, type || 'success'); return; }
    console.log('[Proposals]', type || 'info', msg);
  }

  // ─── Section Status Pipeline ─────────────────────────────────────────────

  var STATUS_ORDER = [
    'not_started', 'outlining', 'drafting', 'internal_review',
    'pink_team_ready', 'pink_team_review', 'rework_pink',
    'red_team_ready', 'red_team_review', 'rework_red',
    'gold_team_ready', 'gold_team_review',
    'white_glove', 'final', 'submitted'
  ];

  var STATUS_COLORS = {
    not_started: '#6c6c80', outlining: '#4a90d9', drafting: '#4a90d9',
    internal_review: '#ffc107',
    pink_team_ready: '#e67e22', pink_team_review: '#e67e22', rework_pink: '#dc3545',
    red_team_ready: '#dc3545', red_team_review: '#dc3545', rework_red: '#dc3545',
    gold_team_ready: '#ffc107', gold_team_review: '#ffc107',
    white_glove: '#9b59b6', final: '#28a745', submitted: '#28a745'
  };

  function statusLabel(s) {
    return (s || '').replace(/_/g, ' ').replace(/\b\w/g, function (c) { return c.toUpperCase(); });
  }

  /**
   * Render the 14-step status pipeline bar into containerId.
   */
  function renderStatusPipeline(containerId, currentStatus) {
    var el = document.getElementById(containerId);
    if (!el) return;
    var currentIdx = STATUS_ORDER.indexOf(currentStatus);
    var html = '<div style="display:flex;gap:2px;overflow-x:auto;padding:4px 0;">';
    STATUS_ORDER.forEach(function (s, i) {
      var active = i <= currentIdx;
      var isCurrent = s === currentStatus;
      var bg = active ? (STATUS_COLORS[s] || palette('blue')) : palette('bgSec');
      var textColor = active ? '#fff' : palette('muted');
      var border = isCurrent ? '2px solid #fff' : '1px solid ' + palette('border');
      html += '<div style="flex:1;min-width:60px;padding:6px 4px;text-align:center;' +
        'background:' + bg + ';color:' + textColor + ';border:' + border + ';' +
        'border-radius:4px;font-size:10px;line-height:1.2;font-weight:' + (isCurrent ? '700' : '400') + ';">' +
        statusLabel(s) + '</div>';
    });
    html += '</div>';
    el.innerHTML = html;
  }

  // ─── Review Pipeline (4 Gates) ───────────────────────────────────────────

  var REVIEW_GATES = ['pink_team', 'red_team', 'gold_team', 'white_glove'];
  var REVIEW_COLORS = {
    pink_team: '#ff69b4', red_team: '#dc3545', gold_team: '#ffc107', white_glove: '#e0e0e0'
  };

  function renderReviewPipeline(containerId, reviews) {
    var el = document.getElementById(containerId);
    if (!el) return;
    var reviewMap = {};
    (reviews || []).forEach(function (r) { reviewMap[r.review_type] = r; });

    var html = '<div style="display:flex;gap:12px;align-items:center;padding:8px 0;">';
    REVIEW_GATES.forEach(function (gate, i) {
      var r = reviewMap[gate];
      var color = REVIEW_COLORS[gate];
      var status = r ? r.status : 'not_scheduled';
      var opacity = r ? '1' : '0.3';
      html += '<div style="flex:1;text-align:center;">';
      html += '<div style="width:48px;height:48px;border-radius:50%;background:' + color +
        ';opacity:' + opacity + ';margin:0 auto 6px;display:flex;align-items:center;justify-content:center;">';
      if (status === 'completed') html += '<span style="font-size:20px;color:#000;">&#10003;</span>';
      else if (status === 'in_progress') html += '<span style="font-size:16px;color:#000;">&#9654;</span>';
      html += '</div>';
      html += '<div style="font-size:11px;color:' + palette('secondary') + ';">' + statusLabel(gate) + '</div>';
      html += '<div style="font-size:10px;color:' + palette('muted') + ';">' + statusLabel(status) + '</div>';
      html += '</div>';
      if (i < REVIEW_GATES.length - 1) {
        html += '<div style="font-size:18px;color:' + palette('muted') + ';">&#8594;</div>';
      }
    });
    html += '</div>';
    el.innerHTML = html;
  }

  // ─── Countdown Timer ─────────────────────────────────────────────────────

  function renderCountdown(containerId, dueDateStr) {
    var el = document.getElementById(containerId);
    if (!el || !dueDateStr) return;

    function update() {
      var now = new Date();
      var due = new Date(dueDateStr + 'T23:59:59');
      var diff = due - now;
      var days = Math.floor(diff / 86400000);
      var hours = Math.floor((diff % 86400000) / 3600000);

      if (diff < 0) {
        el.innerHTML = '<span style="color:' + palette('red') + ';font-weight:bold;">OVERDUE by ' +
          Math.abs(days) + ' day' + (Math.abs(days) !== 1 ? 's' : '') + '</span>';
      } else if (days <= 7) {
        el.innerHTML = '<span style="color:' + palette('red') + ';font-weight:bold;">' +
          days + 'd ' + hours + 'h remaining</span>';
      } else if (days <= 14) {
        el.innerHTML = '<span style="color:' + palette('yellow') + ';">' +
          days + ' days remaining</span>';
      } else {
        el.innerHTML = '<span style="color:' + palette('green') + ';">' +
          days + ' days remaining</span>';
      }
    }

    update();
    setInterval(update, 60000);
  }

  // ─── Compliance Coverage Bar ─────────────────────────────────────────────

  function renderComplianceBar(containerId, stats) {
    var el = document.getElementById(containerId);
    if (!el || !stats) return;
    var total = (stats.compliant || 0) + (stats.partial || 0) +
      (stats.non_compliant || 0) + (stats.not_addressed || 0) + (stats.not_applicable || 0);
    if (total === 0) { el.innerHTML = '<div style="color:' + palette('muted') + ';font-size:12px;">No compliance items</div>'; return; }

    var segs = [
      { key: 'compliant', color: palette('green'), count: stats.compliant || 0 },
      { key: 'partial', color: palette('yellow'), count: stats.partial || 0 },
      { key: 'non_compliant', color: palette('red'), count: stats.non_compliant || 0 },
      { key: 'not_addressed', color: palette('orange'), count: stats.not_addressed || 0 },
      { key: 'not_applicable', color: palette('muted'), count: stats.not_applicable || 0 }
    ];

    var html = '<div style="display:flex;height:20px;border-radius:4px;overflow:hidden;background:' + palette('bgSec') + ';">';
    segs.forEach(function (seg) {
      if (seg.count > 0) {
        var pct = (seg.count / total * 100).toFixed(1);
        html += '<div style="width:' + pct + '%;background:' + seg.color +
          ';display:flex;align-items:center;justify-content:center;font-size:10px;color:#fff;font-weight:600;min-width:18px;">' +
          seg.count + '</div>';
      }
    });
    html += '</div>';

    html += '<div style="display:flex;gap:12px;margin-top:6px;flex-wrap:wrap;">';
    segs.forEach(function (seg) {
      if (seg.count > 0) {
        html += '<div style="display:flex;align-items:center;gap:4px;font-size:11px;color:' + palette('secondary') + ';">' +
          '<span style="width:8px;height:8px;border-radius:2px;background:' + seg.color + ';display:inline-block;"></span>' +
          statusLabel(seg.key) + ' (' + seg.count + ')</div>';
      }
    });
    html += '</div>';
    el.innerHTML = html;
  }

  // ─── SVG Timeline / Gantt ─────────────────────────────────────────────────

  function renderTimeline(containerId, sections, oppDueDate) {
    var el = document.getElementById(containerId);
    if (!el) return;
    if (!sections || sections.length === 0) {
      el.innerHTML = '<div style="color:' + palette('muted') + ';text-align:center;padding:24px;">No sections with due dates to display</div>';
      return;
    }

    // Filter sections with due dates
    var items = sections.filter(function (s) { return s.due_date; }).map(function (s) {
      return { id: s.id, title: s.section_number + ': ' + s.title, status: s.status,
        due: new Date(s.due_date), writer: s.writer || 'Unassigned' };
    });
    if (items.length === 0) {
      el.innerHTML = '<div style="color:' + palette('muted') + ';text-align:center;padding:24px;">No sections with due dates</div>';
      return;
    }

    var today = new Date();
    var allDates = items.map(function (i) { return i.due.getTime(); });
    allDates.push(today.getTime());
    if (oppDueDate) allDates.push(new Date(oppDueDate).getTime());
    var minTime = Math.min.apply(null, allDates) - 7 * 86400000;
    var maxTime = Math.max.apply(null, allDates) + 7 * 86400000;
    var rangeMs = maxTime - minTime;

    var rowH = 28, labelW = 200, chartW = 600, padR = 20;
    var svgW = labelW + chartW + padR;
    var svgH = items.length * rowH + 50;

    var svg = svgNS('svg', { width: svgW, height: svgH, viewBox: '0 0 ' + svgW + ' ' + svgH,
      'aria-label': 'Section timeline', role: 'img' });
    svg.style.cssText = 'width:100%;height:auto;max-height:500px;';

    // Header line
    var header = svgNS('line', { x1: labelW, y1: 24, x2: labelW + chartW, y2: 24,
      stroke: palette('border'), 'stroke-width': 1 });
    svg.appendChild(header);

    // Today marker
    var todayX = labelW + ((today.getTime() - minTime) / rangeMs) * chartW;
    var todayLine = svgNS('line', { x1: todayX, y1: 0, x2: todayX, y2: svgH,
      stroke: palette('blue'), 'stroke-width': 1, 'stroke-dasharray': '4,4', opacity: '0.7' });
    svg.appendChild(todayLine);
    var todayLabel = svgNS('text', { x: todayX, y: 12, fill: palette('blue'),
      'font-size': '10', 'text-anchor': 'middle' });
    todayLabel.textContent = 'Today';
    svg.appendChild(todayLabel);

    // Due date marker
    if (oppDueDate) {
      var dueX = labelW + ((new Date(oppDueDate).getTime() - minTime) / rangeMs) * chartW;
      var dueLine = svgNS('line', { x1: dueX, y1: 0, x2: dueX, y2: svgH,
        stroke: palette('red'), 'stroke-width': 2, 'stroke-dasharray': '6,3' });
      svg.appendChild(dueLine);
      var dueLabel = svgNS('text', { x: dueX, y: 12, fill: palette('red'),
        'font-size': '10', 'text-anchor': 'middle', 'font-weight': 'bold' });
      dueLabel.textContent = 'RFP Due';
      svg.appendChild(dueLabel);
    }

    // Rows
    items.forEach(function (item, idx) {
      var y = 30 + idx * rowH;
      // Row background
      if (idx % 2 === 0) {
        var bg = svgNS('rect', { x: 0, y: y, width: svgW, height: rowH,
          fill: palette('bgSec'), opacity: '0.3' });
        svg.appendChild(bg);
      }
      // Label
      var label = svgNS('text', { x: 4, y: y + rowH / 2 + 4, fill: palette('secondary'),
        'font-size': '11' });
      label.textContent = item.title.length > 24 ? item.title.substring(0, 22) + '...' : item.title;
      svg.appendChild(label);

      // Bar (from today to due date, or just a marker if past)
      var startX = todayX;
      var endX = labelW + ((item.due.getTime() - minTime) / rangeMs) * chartW;
      var barColor = STATUS_COLORS[item.status] || palette('blue');
      var barW = Math.max(endX - startX, 4);
      if (endX < startX) barW = 4; // overdue — thin marker

      var bar = svgNS('rect', {
        x: Math.min(startX, endX), y: y + 4, width: Math.abs(barW), height: rowH - 8,
        rx: 3, fill: barColor, opacity: item.due < today ? '0.5' : '0.8'
      });
      svg.appendChild(bar);

      // Due date text
      var dateText = svgNS('text', { x: endX + 4, y: y + rowH / 2 + 4,
        fill: item.due < today ? palette('red') : palette('muted'), 'font-size': '10' });
      dateText.textContent = item.due.toISOString().split('T')[0];
      svg.appendChild(dateText);
    });

    el.innerHTML = '';
    el.appendChild(svg);
  }

  // ─── Assignment Matrix ───────────────────────────────────────────────────

  function renderAssignmentMatrix(containerId, sections) {
    var el = document.getElementById(containerId);
    if (!el) return;
    if (!sections || sections.length === 0) {
      el.innerHTML = '<div style="color:' + palette('muted') + ';text-align:center;padding:24px;">No sections to display</div>';
      return;
    }

    // Group by writer
    var writerMap = {};
    sections.forEach(function (s) {
      var w = s.writer || 'Unassigned';
      if (!writerMap[w]) writerMap[w] = {};
      var phase = getPhase(s.status);
      if (!writerMap[w][phase]) writerMap[w][phase] = [];
      writerMap[w][phase].push(s);
    });

    var phases = ['Not Started', 'Drafting', 'Review', 'Color Team', 'Final'];

    var html = '<div class="table-container" style="margin-top:12px;">';
    html += '<table><thead><tr><th>Writer</th>';
    phases.forEach(function (p) { html += '<th style="text-align:center;">' + p + '</th>'; });
    html += '</tr></thead><tbody>';

    Object.keys(writerMap).sort().forEach(function (writer) {
      html += '<tr><td style="font-weight:600;">' + escapeHTML(writer) + '</td>';
      phases.forEach(function (phase) {
        var secs = writerMap[writer][phase] || [];
        html += '<td style="text-align:center;vertical-align:top;">';
        secs.forEach(function (s) {
          var priColor = s.priority === 'critical_path' ? palette('red') :
            s.priority === 'high' ? palette('yellow') : palette('blue');
          var overdue = s.overdue ? 'border:2px solid ' + palette('red') + ';' : '';
          html += '<div style="display:inline-block;margin:2px;padding:3px 6px;border-radius:3px;' +
            'background:' + palette('bgSec') + ';font-size:10px;color:' + palette('primary') + ';' + overdue + '">' +
            '<span style="color:' + priColor + ';margin-right:3px;">&#9679;</span>' +
            escapeHTML(s.section_number) + '</div>';
        });
        html += '</td>';
      });
      html += '</tr>';
    });

    html += '</tbody></table></div>';
    el.innerHTML = html;
  }

  function getPhase(status) {
    if (status === 'not_started') return 'Not Started';
    if (['outlining', 'drafting'].indexOf(status) >= 0) return 'Drafting';
    if (status === 'internal_review') return 'Review';
    if (['pink_team_ready', 'pink_team_review', 'rework_pink',
         'red_team_ready', 'red_team_review', 'rework_red',
         'gold_team_ready', 'gold_team_review', 'white_glove'].indexOf(status) >= 0) return 'Color Team';
    return 'Final';
  }

  // ─── Modal Controls ──────────────────────────────────────────────────────

  function openModal(id) {
    var el = document.getElementById(id);
    if (el) el.style.display = 'flex';
  }

  function closeModal(id) {
    var el = document.getElementById(id);
    if (el) el.style.display = 'none';
  }

  // ─── Form Submissions ────────────────────────────────────────────────────

  async function submitOppForm(e) {
    e.preventDefault();
    var form = document.getElementById('create-opp-form');
    var data = {};
    new FormData(form).forEach(function (v, k) { if (v) data[k] = v; });
    var result = await post('/api/proposals/opportunities', data);
    if (result && result.id) {
      toast('Opportunity created');
      closeModal('create-opp-modal');
      window.location.reload();
    } else {
      toast('Failed to create opportunity', 'error');
    }
    return false;
  }

  async function submitSectionForm(e, oppId) {
    e.preventDefault();
    var form = document.getElementById('create-section-form');
    var data = {};
    new FormData(form).forEach(function (v, k) { if (v) data[k] = v; });
    var result = await post('/api/proposals/opportunities/' + oppId + '/sections', data);
    if (result && result.id) {
      toast('Section created');
      closeModal('create-section-modal');
      window.location.reload();
    } else {
      toast('Failed to create section', 'error');
    }
    return false;
  }

  async function submitVolumeForm(e, oppId) {
    e.preventDefault();
    var form = document.getElementById('create-volume-form');
    var data = {};
    new FormData(form).forEach(function (v, k) { if (v) data[k] = v; });
    var result = await post('/api/proposals/opportunities/' + oppId + '/volumes', data);
    if (result && result.id) {
      toast('Volume created');
      closeModal('create-volume-modal');
      window.location.reload();
    } else {
      toast('Failed to create volume', 'error');
    }
    return false;
  }

  async function submitComplianceForm(e, oppId) {
    e.preventDefault();
    var form = document.getElementById('create-compliance-form');
    var data = {};
    new FormData(form).forEach(function (v, k) { if (v) data[k] = v; });
    var result = await post('/api/proposals/opportunities/' + oppId + '/compliance', data);
    if (result && result.id) {
      toast('Compliance item created');
      closeModal('create-compliance-modal');
      window.location.reload();
    } else {
      toast('Failed to create compliance item', 'error');
    }
    return false;
  }

  async function submitReviewForm(e, oppId) {
    e.preventDefault();
    var form = document.getElementById('schedule-review-form');
    var data = {};
    new FormData(form).forEach(function (v, k) { if (v) data[k] = v; });
    var result = await post('/api/proposals/opportunities/' + oppId + '/reviews', data);
    if (result && result.id) {
      toast('Review scheduled');
      closeModal('schedule-review-modal');
      window.location.reload();
    } else {
      toast('Failed to schedule review', 'error');
    }
    return false;
  }

  // ─── Actions ─────────────────────────────────────────────────────────────

  async function advanceStatus(sectionId, newStatus) {
    var result = await put('/api/proposals/sections/' + sectionId + '/status', { status: newStatus });
    if (result && result.new_status) {
      toast('Status advanced to ' + statusLabel(result.new_status));
      window.location.reload();
    } else {
      toast((result && result.error) || 'Failed to advance status', 'error');
    }
  }

  async function saveNotes(sectionId) {
    var textarea = document.getElementById('section-notes');
    if (!textarea) return;
    var result = await put('/api/proposals/sections/' + sectionId, { notes: textarea.value });
    if (result && result.status === 'ok') {
      toast('Notes saved');
    } else {
      toast('Failed to save notes', 'error');
    }
  }

  // ─── Data Loaders (for tabs that fetch on demand) ────────────────────────

  async function loadAssignmentMatrix(containerId, oppId) {
    var data = await api('/api/proposals/opportunities/' + oppId + '/sections');
    if (data) renderAssignmentMatrix(containerId, data.sections || data);
  }

  async function loadTimeline(containerId, oppId, dueDate) {
    var data = await api('/api/proposals/opportunities/' + oppId + '/sections');
    if (data) renderTimeline(containerId, data.sections || data, dueDate);
  }

  async function loadStats(oppId) {
    var data = await api('/api/proposals/opportunities/' + oppId + '/stats');
    if (!data) return;
    // Update stat cards
    var statMap = {
      'stat-sections-complete': data.sections_complete + ' / ' + data.sections_total,
      'stat-compliance-pct': (data.compliance_coverage_pct || 0).toFixed(0) + '%',
      'stat-open-findings': data.open_findings || 0,
      'stat-critical-findings': data.critical_findings || 0
    };
    Object.keys(statMap).forEach(function (id) {
      var el = document.getElementById(id);
      if (el) el.textContent = statMap[id];
    });
  }

  // ─── Auto-Refresh ────────────────────────────────────────────────────────

  var _refreshTimer = null;

  function startAutoRefresh(oppId, intervalMs) {
    stopAutoRefresh();
    _refreshTimer = setInterval(function () {
      loadStats(oppId);
    }, intervalMs || 30000);
  }

  function stopAutoRefresh() {
    if (_refreshTimer) { clearInterval(_refreshTimer); _refreshTimer = null; }
  }

  // ─── Public API ──────────────────────────────────────────────────────────

  NS.proposals = {
    // Renderers
    renderStatusPipeline: renderStatusPipeline,
    renderReviewPipeline: renderReviewPipeline,
    renderCountdown: renderCountdown,
    renderComplianceBar: renderComplianceBar,
    renderTimeline: renderTimeline,
    renderAssignmentMatrix: renderAssignmentMatrix,

    // Modals
    openModal: openModal,
    closeModal: closeModal,

    // Form submissions
    submitOppForm: submitOppForm,
    submitSectionForm: submitSectionForm,
    submitVolumeForm: submitVolumeForm,
    submitComplianceForm: submitComplianceForm,
    submitReviewForm: submitReviewForm,

    // Actions
    advanceStatus: advanceStatus,
    saveNotes: saveNotes,

    // Data loaders
    loadAssignmentMatrix: loadAssignmentMatrix,
    loadTimeline: loadTimeline,
    loadStats: loadStats,

    // Auto-refresh
    startAutoRefresh: startAutoRefresh,
    stopAutoRefresh: stopAutoRefresh,

    // Constants (for external use)
    STATUS_ORDER: STATUS_ORDER,
    STATUS_COLORS: STATUS_COLORS
  };
})();
