/**
 * IL5 Display Client — polls /api/il5/events and renders table + stats.
 */
(function () {
    'use strict';

    var POLL_INTERVAL = 3000;
    var tableBody = document.getElementById('il5-body');
    var bannerText = document.getElementById('banner-text');

    function setText(id, val) {
        var el = document.getElementById(id);
        if (el) el.textContent = val;
    }

    function formatNumber(n) {
        if (n === undefined || n === null) return '-';
        return n.toLocaleString ? n.toLocaleString() : String(n);
    }

    function formatDate(iso) {
        if (!iso) return '-';
        try {
            var d = new Date(iso);
            return d.toLocaleString ? d.toLocaleString() : iso;
        } catch (e) {
            return iso;
        }
    }

    function slaBadge(status) {
        if (status === 1 || status === 'MET') {
            return '<span style="color:#28a745;font-weight:600;">MET</span>';
        }
        if (status === 0 || status === 'VIOLATED') {
            return '<span style="color:#dc3545;font-weight:600;">VIOLATED</span>';
        }
        return '<span style="color:#6c757d;">UNKNOWN</span>';
    }

    function renderEvents(events) {
        if (!tableBody) return;
        if (!events || events.length === 0) {
            tableBody.innerHTML = '<tr><td colspan="7" style="text-align:center; color:var(--text-secondary);">No IL5 events found.</td></tr>';
            return;
        }
        var html = '';
        events.forEach(function (e) {
            var statusLabel = e.sla_status || (e.sla_met === 1 ? 'MET' : (e.sla_met === 0 ? 'VIOLATED' : 'UNKNOWN'));
            html += '<tr>' +
                '<td>' + (e.id || '').substring(0, 8) + '…</td>' +
                '<td>' + (e.source_id || '-') + '</td>' +
                '<td>' + (e.classification || 'CUI') + ' // ' + (e.impact_level || 'IL5') + '</td>' +
                '<td>' + formatDate(e.ingested_at) + '</td>' +
                '<td>' + formatDate(e.source_published_at) + '</td>' +
                '<td>' + (e.display_latency_s !== null && e.display_latency_s !== undefined ? e.display_latency_s : '-') + '</td>' +
                '<td>' + slaBadge(statusLabel) + '</td>' +
                '</tr>';
        });
        tableBody.innerHTML = html;
    }

    function loadEvents() {
        fetch('/api/il5/events?limit=50')
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (bannerText && data.classification) {
                    bannerText.textContent = data.classification;
                }
                renderEvents(data.events || []);
            })
            .catch(function (err) {
                console.error('IL5 events poll failed', err);
                if (tableBody) {
                    tableBody.innerHTML = '<tr><td colspan="7" style="text-align:center; color:#dc3545;">Failed to load IL5 events.</td></tr>';
                }
            });
    }

    function loadSummary() {
        fetch('/api/il5/sla-summary')
            .then(function (r) { return r.json(); })
            .then(function (data) {
                setText('stat-total', formatNumber(data.total));
                setText('stat-met', formatNumber(data.sla_met));
                setText('stat-violated', formatNumber(data.sla_violated));
                setText('stat-compliance', (data.compliance_pct !== undefined ? data.compliance_pct : '-') + '%');
            })
            .catch(function () { /* summary is non-critical */ });
    }

    function tick() {
        loadEvents();
        loadSummary();
    }

    tick();
    setInterval(tick, POLL_INTERVAL);
})();
