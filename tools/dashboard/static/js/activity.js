/**
 * Activity Feed Client (Phase 30 — D174)
 *
 * Loads merged audit_trail + hook_events, supports:
 * - HTTP polling (always available)
 * - WebSocket via Socket.IO (when available, D170)
 * - Filters: source, event type, actor, free-text search
 * - CSV export
 */
(function () {
    'use strict';

    var POLL_INTERVAL = 3000;
    var PAGE_SIZE = 100;
    var currentOffset = 0;
    var cursor = '';
    var pollTimer = null;
    var socketConnected = false;

    // DOM refs
    var body = document.getElementById('activity-body');
    var loadMoreBtn = document.getElementById('btn-load-more');
    var wsDot = document.getElementById('ws-dot');
    var wsLabel = document.getElementById('ws-label');

    // ---- Stats ----
    function loadStats() {
        fetch('/api/activity/stats')
            .then(function (r) { return r.json(); })
            .then(function (data) {
                setText('stat-total', formatNumber(data.total));
                setText('stat-today', formatNumber(data.today));
                setText('stat-hour', formatNumber(data.last_hour));
                setText('stat-audit', formatNumber(data.audit_total));
                setText('stat-hook', formatNumber(data.hook_total));
            })
            .catch(function () { /* stats are non-critical */ });
    }

    function setText(id, val) {
        var el = document.getElementById(id);
        if (el) el.textContent = val;
    }

    function formatNumber(n) {
        if (n === undefined || n === null) return '-';
        return n.toLocaleString ? n.toLocaleString() : String(n);
    }

    // ---- Filters ----
    function getFilters() {
        return {
            source: val('filter-source'),
            event_type: val('filter-event-type'),
            actor: val('filter-actor'),
            search: val('filter-search')
        };
    }

    function val(id) {
        var el = document.getElementById(id);
        return el ? el.value : '';
    }

    function buildQueryString(filters, offset) {
        var params = [];
        if (filters.source) params.push('source=' + encodeURIComponent(filters.source));
        if (filters.event_type) params.push('event_type=' + encodeURIComponent(filters.event_type));
        if (filters.actor) params.push('actor=' + encodeURIComponent(filters.actor));
        params.push('limit=' + PAGE_SIZE);
        if (offset) params.push('offset=' + offset);
        return params.join('&');
    }

    // ---- Load feed ----
    function loadFeed(append) {
        if (!append) currentOffset = 0;
        var filters = getFilters();
        var qs = buildQueryString(filters, currentOffset);

        fetch('/api/activity/feed?' + qs)
            .then(function (r) { return r.json(); })
            .then(function (data) {
                var events = data.events || [];
                var search = filters.search.toLowerCase();

                // Client-side free-text filter
                if (search) {
                    events = events.filter(function (e) {
                        return (
                            (e.event_type || '').toLowerCase().indexOf(search) !== -1 ||
                            (e.actor_or_agent || '').toLowerCase().indexOf(search) !== -1 ||
                            (e.summary || '').toLowerCase().indexOf(search) !== -1 ||
                            (e.project_id || '').toLowerCase().indexOf(search) !== -1
                        );
                    });
                }

                if (!append) body.innerHTML = '';
                events.forEach(function (e) {
                    body.appendChild(createRow(e));
                });

                if (!append && events.length === 0) {
                    body.innerHTML = '<tr><td colspan="6" style="text-align:center;color:var(--text-secondary);">No events found.</td></tr>';
                }

                // Update cursor for polling
                if (events.length > 0) {
                    cursor = events[0].created_at;
                }

                // Show/hide Load More
                if (loadMoreBtn) {
                    loadMoreBtn.style.display = (data.count >= PAGE_SIZE) ? '' : 'none';
                }
                currentOffset += data.count;
            })
            .catch(function (err) {
                console.error('Activity feed error:', err);
            });
    }

    function createRow(e) {
        var tr = document.createElement('tr');
        var sourceBadge = '<span class="source-badge ' + (e.source || '') + '">' + (e.source || '') + '</span>';
        tr.innerHTML =
            '<td style="white-space:nowrap;font-size:0.8rem;">' + (e.created_at || '') + '</td>' +
            '<td>' + sourceBadge + '</td>' +
            '<td>' + escapeHtml(e.event_type || '') + '</td>' +
            '<td>' + escapeHtml(e.actor_or_agent || '') + '</td>' +
            '<td>' + escapeHtml(e.summary || '') + '</td>' +
            '<td>' + escapeHtml(e.project_id || '') + '</td>';
        return tr;
    }

    function escapeHtml(s) {
        var div = document.createElement('div');
        div.appendChild(document.createTextNode(s));
        return div.innerHTML;
    }

    // ---- Filter options ----
    function loadFilterOptions() {
        fetch('/api/activity/filter-options')
            .then(function (r) { return r.json(); })
            .then(function (data) {
                populateSelect('filter-event-type', data.event_types || [], 'All Event Types');
                populateSelect('filter-actor', data.actors || [], 'All Actors');
            })
            .catch(function () { /* non-critical */ });
    }

    function populateSelect(id, items, defaultLabel) {
        var el = document.getElementById(id);
        if (!el) return;
        var current = el.value;
        el.innerHTML = '<option value="">' + defaultLabel + '</option>';
        items.forEach(function (item) {
            var opt = document.createElement('option');
            opt.value = item;
            opt.textContent = item;
            if (item === current) opt.selected = true;
            el.appendChild(opt);
        });
    }

    // ---- Polling ----
    function startPolling() {
        if (socketConnected) return; // WebSocket handles updates
        setConnectionStatus('polling');
        pollTimer = setInterval(function () {
            if (!cursor) return;
            fetch('/api/activity/poll?cursor=' + encodeURIComponent(cursor))
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    var events = data.events || [];
                    if (events.length > 0) {
                        cursor = data.cursor;
                        // Prepend new events
                        events.reverse().forEach(function (e) {
                            var row = createRow(e);
                            row.style.animation = 'fadeIn 0.3s ease-in';
                            if (body.firstChild) {
                                body.insertBefore(row, body.firstChild);
                            } else {
                                body.appendChild(row);
                            }
                        });
                        loadStats();
                    }
                })
                .catch(function () { /* polling failure is non-critical */ });
        }, POLL_INTERVAL);
    }

    function stopPolling() {
        if (pollTimer) {
            clearInterval(pollTimer);
            pollTimer = null;
        }
    }

    // ---- WebSocket (Socket.IO) ----
    function tryWebSocket() {
        if (typeof io === 'undefined') {
            // Socket.IO not loaded — fall back to polling
            startPolling();
            return;
        }

        try {
            var socket = io({transports: ['websocket', 'polling']});

            socket.on('connect', function () {
                socketConnected = true;
                stopPolling();
                setConnectionStatus('connected');
                socket.emit('join', {room: 'activity'});
            });

            socket.on('activity_event', function (data) {
                var row = createRow(data);
                row.style.animation = 'fadeIn 0.3s ease-in';
                if (body.firstChild) {
                    body.insertBefore(row, body.firstChild);
                } else {
                    body.appendChild(row);
                }
                if (data.created_at) cursor = data.created_at;
                loadStats();
            });

            socket.on('disconnect', function () {
                socketConnected = false;
                setConnectionStatus('disconnected');
                startPolling();
            });

            socket.on('connect_error', function () {
                socketConnected = false;
                startPolling();
            });
        } catch (e) {
            startPolling();
        }
    }

    function setConnectionStatus(status) {
        if (wsDot) {
            wsDot.className = 'ws-dot ' + status;
        }
        if (wsLabel) {
            var labels = {connected: 'WebSocket', polling: 'Polling', disconnected: 'Disconnected'};
            wsLabel.textContent = labels[status] || status;
        }
    }

    // ---- CSV export ----
    function exportCSV() {
        var rows = body.querySelectorAll('tr');
        var csv = 'Time,Source,Event Type,Actor,Summary,Project\n';
        rows.forEach(function (tr) {
            var cells = tr.querySelectorAll('td');
            if (cells.length >= 6) {
                var line = [];
                for (var i = 0; i < cells.length; i++) {
                    line.push('"' + (cells[i].textContent || '').replace(/"/g, '""') + '"');
                }
                csv += line.join(',') + '\n';
            }
        });
        var blob = new Blob([csv], {type: 'text/csv'});
        var url = URL.createObjectURL(blob);
        var a = document.createElement('a');
        a.href = url;
        a.download = 'activity_feed_' + new Date().toISOString().slice(0, 10) + '.csv';
        a.click();
        URL.revokeObjectURL(url);
    }

    // ---- Init ----
    function init() {
        loadStats();
        loadFilterOptions();
        loadFeed(false);

        // Filter change handlers
        ['filter-source', 'filter-event-type', 'filter-actor'].forEach(function (id) {
            var el = document.getElementById(id);
            if (el) el.addEventListener('change', function () { loadFeed(false); });
        });

        var searchEl = document.getElementById('filter-search');
        if (searchEl) {
            var searchTimeout;
            searchEl.addEventListener('input', function () {
                clearTimeout(searchTimeout);
                searchTimeout = setTimeout(function () { loadFeed(false); }, 300);
            });
        }

        if (loadMoreBtn) {
            loadMoreBtn.addEventListener('click', function () { loadFeed(true); });
        }

        var exportBtn = document.getElementById('btn-export-csv');
        if (exportBtn) {
            exportBtn.addEventListener('click', exportCSV);
        }

        // Try WebSocket, fall back to polling
        tryWebSocket();
    }

    // Wait for DOM
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
