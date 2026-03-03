// CUI // SP-CTI
/**
 * ICDEV Dashboard - Real-Time Live Updates Module (HTTP Polling)
 * Extends window.ICDEV (created by api.js, extended by ux.js/charts.js/tables.js).
 *
 * Features:
 *   1. HTTP polling transport (replaces SSE — D103)
 *   2. Connection status indicator (green/yellow/red dot in nav bar)
 *   3. Cursor-based incremental event fetching
 *   4. Dashboard auto-update on new events
 *   5. Smart refresh logic (batched, page-aware)
 *   6. Event classification for toast notifications
 *   7. Reconnect countdown tooltip on status dot
 *
 * Decision D103: HTTP polling replaces SSE as primary transport. More
 * proxy/firewall friendly for DoD networks, works with Flask synchronous
 * WSGI, no long-lived connections. Poll interval: 3s default.
 *
 * Zero external dependencies.
 */
(function () {
    "use strict";

    var ICDEV = window.ICDEV || {};
    window.ICDEV = ICDEV;

    // ---- Constants ----
    var POLL_ENDPOINT = "/api/events/poll";
    var POLL_INTERVAL_MS = 3000;
    var POLL_BACKOFF_MAX_MS = 30000;
    var MAX_TABLE_ROWS = 10;
    var STATE_DISCONNECTED = "disconnected";
    var STATE_CONNECTED = "connected";
    var STATE_DEGRADED = "degraded";

    // ---- Internal state ----
    var _pollTimer = null;
    var _state = STATE_DISCONNECTED;
    var _cursor = "";             // ISO timestamp cursor for incremental poll
    var _consecutiveErrors = 0;
    var _currentInterval = POLL_INTERVAL_MS;
    var _dot = null;              // status indicator element
    var _styled = false;
    var _countdownTimer = null;   // for reconnect countdown display
    var _nextPollAt = 0;          // timestamp of next poll attempt

    // ---- Filter state ----
    var _filters = { severity: "", hook_type: "", tool_name: "" };
    var _filterBarRendered = false;

    // ========================================================================
    // 1. CSS Injection
    // ========================================================================
    function injectStyles() {
        if (_styled) return;
        _styled = true;
        var s = document.createElement("style");
        s.id = "icdev-live-styles";
        s.textContent = [
            ".icdev-poll-dot{display:inline-block;width:12px;height:12px;border-radius:50%;",
            "  margin-left:10px;vertical-align:middle;transition:background .3s;flex-shrink:0;cursor:help}",
            ".icdev-poll-dot.poll-connected{background:#28a745;animation:icdev-poll-pulse 2s infinite}",
            ".icdev-poll-dot.poll-degraded{background:#ffc107;animation:none}",
            ".icdev-poll-dot.poll-disconnected{background:#dc3545;animation:none}",
            "@keyframes icdev-poll-pulse{0%,100%{box-shadow:0 0 0 0 rgba(40,167,69,.4)}",
            "  50%{box-shadow:0 0 0 4px rgba(40,167,69,0)}}",
            ".icdev-filter-bar{display:flex;gap:10px;flex-wrap:wrap;padding:12px 0;align-items:center}",
            ".icdev-filter-bar label{font-size:.78rem;color:#6c6c80;font-weight:600}",
            ".icdev-filter-bar select{background:#1a1a2e;color:#e0e0e0;border:1px solid #2a2a40;",
            "  border-radius:4px;padding:4px 8px;font-size:.8rem;cursor:pointer}",
            ".icdev-filter-bar select:focus{outline:none;border-color:#4a90d9}",
            ".icdev-filter-clear{background:transparent;border:1px solid #2a2a40;color:#6c6c80;",
            "  border-radius:4px;padding:4px 10px;font-size:.78rem;cursor:pointer}",
            ".icdev-filter-clear:hover{border-color:#4a90d9;color:#e0e0e0}"
        ].join("\n");
        document.head.appendChild(s);
    }

    // ========================================================================
    // 2. Connection Status Indicator + Countdown Tooltip
    // ========================================================================
    function ensureStatusDot() {
        if (_dot) return _dot;
        injectStyles();
        var brand = document.querySelector(".navbar-brand");
        if (!brand) return null;
        _dot = document.createElement("span");
        _dot.className = "icdev-poll-dot poll-disconnected";
        _dot.setAttribute("role", "status");
        _dot.setAttribute("aria-label", "Real-time connection: disconnected");
        _dot.title = "Poll: Disconnected";
        brand.parentNode.insertBefore(_dot, brand.nextSibling);
        return _dot;
    }

    function updateStatusDot(state, detail) {
        var dot = ensureStatusDot();
        if (!dot) return;
        dot.classList.remove("poll-connected", "poll-degraded", "poll-disconnected");
        var aria = "Real-time connection: ";
        var tip = "Poll: ";
        if (state === STATE_CONNECTED) {
            dot.classList.add("poll-connected");
            aria += "connected";
            tip += "Connected" + (detail ? " (" + detail + ")" : "");
        } else if (state === STATE_DEGRADED) {
            dot.classList.add("poll-degraded");
            aria += "degraded — retrying";
            tip += "Retrying" + (detail ? " (" + detail + ")" : "");
        } else {
            dot.classList.add("poll-disconnected");
            aria += "disconnected";
            tip += "Disconnected" + (detail ? " — " + detail : "");
        }
        dot.setAttribute("aria-label", aria);
        dot.title = tip;
    }

    /** Update tooltip with countdown to next poll attempt (for degraded state). */
    function startCountdown() {
        stopCountdown();
        _countdownTimer = setInterval(function () {
            if (_state !== STATE_DEGRADED || !_dot) { stopCountdown(); return; }
            var remaining = Math.max(0, Math.ceil((_nextPollAt - Date.now()) / 1000));
            _dot.title = "Poll: Retrying in " + remaining + "s (errors: " + _consecutiveErrors + ")";
        }, 1000);
    }

    function stopCountdown() {
        if (_countdownTimer) { clearInterval(_countdownTimer); _countdownTimer = null; }
    }

    // ========================================================================
    // 3. HTTP Polling (replaces EventSource SSE — D103)
    // ========================================================================
    function startPolling() {
        if (_pollTimer) return;
        _state = STATE_CONNECTED;
        _consecutiveErrors = 0;
        _currentInterval = POLL_INTERVAL_MS;
        updateStatusDot(STATE_CONNECTED);
        console.log("[ICDEV Live] HTTP polling started (" + POLL_INTERVAL_MS + "ms)");
        // Do an initial poll immediately
        doPoll();
    }

    function stopPolling() {
        if (_pollTimer) { clearTimeout(_pollTimer); _pollTimer = null; }
        stopCountdown();
        _state = STATE_DISCONNECTED;
        updateStatusDot(STATE_DISCONNECTED);
        console.log("[ICDEV Live] HTTP polling stopped.");
    }

    function schedulePoll() {
        if (_pollTimer) clearTimeout(_pollTimer);
        _nextPollAt = Date.now() + _currentInterval;
        _pollTimer = setTimeout(function () { _pollTimer = null; doPoll(); }, _currentInterval);
    }

    function doPoll() {
        var url = POLL_ENDPOINT + "?limit=25";
        if (_cursor) url += "&since=" + encodeURIComponent(_cursor);
        if (_filters.severity) url += "&severity=" + encodeURIComponent(_filters.severity);
        if (_filters.hook_type) url += "&hook_type=" + encodeURIComponent(_filters.hook_type);
        if (_filters.tool_name) url += "&tool_name=" + encodeURIComponent(_filters.tool_name);

        var fetchFn = (typeof ICDEV.fetchJSON === "function") ? ICDEV.fetchJSON : null;
        if (!fetchFn) {
            schedulePoll();
            return;
        }

        fetchFn(url).then(function (data) {
            if (!data) {
                onPollError();
                return;
            }

            // Success — reset backoff
            _consecutiveErrors = 0;
            _currentInterval = data.poll_interval_ms || POLL_INTERVAL_MS;
            if (_state !== STATE_CONNECTED) {
                _state = STATE_CONNECTED;
                stopCountdown();
                updateStatusDot(STATE_CONNECTED);
                console.log("[ICDEV Live] Poll recovered — connected.");
            }

            // Update cursor
            if (data.cursor) _cursor = data.cursor;

            // Process new events
            if (data.events && data.events.length > 0) {
                processEvents(data.events);
            }

            schedulePoll();
        });
    }

    function onPollError() {
        _consecutiveErrors++;
        // Exponential backoff: 3s, 6s, 12s, 24s... capped at 30s
        _currentInterval = Math.min(
            POLL_INTERVAL_MS * Math.pow(2, _consecutiveErrors - 1),
            POLL_BACKOFF_MAX_MS
        );

        if (_consecutiveErrors >= 3) {
            _state = STATE_DISCONNECTED;
            updateStatusDot(STATE_DISCONNECTED, _consecutiveErrors + " consecutive errors");
        } else {
            _state = STATE_DEGRADED;
            updateStatusDot(STATE_DEGRADED, "retry in " + (_currentInterval / 1000) + "s");
            startCountdown();
        }

        console.warn("[ICDEV Live] Poll error #" + _consecutiveErrors +
            ". Next poll in " + (_currentInterval / 1000) + "s");
        schedulePoll();
    }

    // ========================================================================
    // 4. Event Processing & Classification
    // ========================================================================
    function processEvents(events) {
        // Events come newest-first from the API; process oldest-first
        var ordered = events.slice().reverse();
        for (var i = 0; i < ordered.length; i++) {
            classifyAndNotify(ordered[i]);
        }
        // Batch refresh the page
        refreshForPage(ordered);
    }

    /**
     * Classify event and show toast if warranted:
     *   - severity critical/high -> error toast
     *   - PostToolUse + sast/security -> warning toast
     *   - PostToolUse + deploy -> info toast
     *   - otherwise -> silent
     */
    function classifyAndNotify(data) {
        var sev = (data.severity || "").toLowerCase();
        var hook = data.hook_type || "";
        var tool = (data.tool_name || data.tool || "").toLowerCase();
        var notify = ICDEV.showNotification;
        if (typeof notify !== "function") return;

        if (sev === "critical" || sev === "high") {
            notify(fmtMsg(data, "Alert"), "error");
        } else if (hook === "PostToolUse" && (tool.indexOf("sast") !== -1 || tool.indexOf("security") !== -1)) {
            notify(fmtMsg(data, "Security scan completed"), "warning");
        } else if (hook === "PostToolUse" && tool.indexOf("deploy") !== -1) {
            notify(fmtMsg(data, "Deployment"), "info");
        }
    }

    function fmtMsg(data, prefix) {
        var m = prefix;
        if (data.tool_name || data.tool) m += ": " + (data.tool_name || data.tool);
        if (data.project_id) m += " [" + data.project_id + "]";
        if (data.message) m += " \u2014 " + data.message;
        return m;
    }

    // ========================================================================
    // 5. Smart Refresh Logic (Page-Aware)
    // ========================================================================
    function refreshForPage(events) {
        if (!events.length) return;
        var path = window.location.pathname;
        var needsAlerts = false, needsCharts = false;
        for (var i = 0; i < events.length; i++) {
            var t = (events[i].tool_name || events[i].tool || "").toLowerCase();
            if (events[i].severity || t.indexOf("alert") !== -1 || t.indexOf("monitor") !== -1) needsAlerts = true;
            if (/deploy|sast|stig|poam|sbom|compliance/.test(t)) needsCharts = true;
        }
        if (path === "/" || path === "") {
            refreshHomeNotifications();
            if (needsCharts) refreshHomeCharts();
            refreshHomeActivity(events);
        }
        if (path === "/monitoring") {
            if (needsAlerts && typeof ICDEV.refreshAlertBadge === "function") ICDEV.refreshAlertBadge();
            if (typeof ICDEV.refreshHealthStatus === "function") ICDEV.refreshHealthStatus();
        }
        if (path === "/events") refreshEventsPage();
    }

    // ========================================================================
    // 6. Dashboard Section Refreshers
    // ========================================================================
    function refreshHomeNotifications() {
        if (typeof ICDEV.fetchJSON !== "function") return;
        ICDEV.fetchJSON("/api/notifications").then(function (data) {
            if (!data || !data.notifications) return;
            var count = 0;
            for (var i = 0; i < data.notifications.length; i++) {
                var n = data.notifications[i];
                if (n.type === "error" && n.message) {
                    var m = n.message.match(/^(\d+)\s+alert/);
                    if (m) count = parseInt(m[1], 10);
                }
            }
            updateCardValue("Firing Alerts", count, count > 0 ? "red" : "green");
        });
    }

    function refreshHomeCharts() {
        if (typeof ICDEV.fetchJSON !== "function") return;
        ICDEV.fetchJSON("/api/charts/overview").then(function (d) {
            if (!d) return;
            if (d.agent_health && typeof ICDEV.gaugeChart === "function" && document.getElementById("chart-agent-health")) {
                ICDEV.gaugeChart("chart-agent-health", {
                    value: d.agent_health.ratio,
                    label: d.agent_health.active + "/" + d.agent_health.total + " Active",
                    thresholds: { good: 0.7, warning: 0.4 }
                });
            }
            if (d.compliance && typeof ICDEV.barChart === "function" && document.getElementById("chart-compliance")) {
                ICDEV.barChart("chart-compliance", {
                    labels: ["POA&M", "STIG"],
                    series: [
                        { name: "Open",   color: "#dc3545", data: [d.compliance.poam.open,   d.compliance.stig.open] },
                        { name: "Closed", color: "#28a745", data: [d.compliance.poam.closed, d.compliance.stig.closed] }
                    ],
                    showLegend: true
                });
            }
            if (d.agent_health) updateCardValue("Active Agents", d.agent_health.active);
        });
    }

    function refreshHomeActivity(events) {
        var alertsTbody = findTbody("Recent Alerts");
        var actTbody = findTbody("Recent Activity");
        if (!alertsTbody && !actTbody) return;
        for (var i = events.length - 1; i >= 0; i--) {
            var ev = events[i];
            var sev = (ev.severity || "info").toLowerCase();
            var hook = ev.hook_type || "event";
            var tool = ev.tool_name || ev.tool || "unknown";
            var ts = ev.timestamp || ev.created_at || new Date().toISOString();
            if (alertsTbody && (sev === "critical" || sev === "high" || sev === "warning")) {
                prependRow(alertsTbody, [badge(sev), esc(tool), esc(ev.message || hook), timeAgo(ts)]);
            }
            if (actTbody) {
                prependRow(actTbody, [esc(hook), esc(tool), esc(ev.project_id || "\u2014"), timeAgo(ts)]);
            }
        }
    }

    function refreshEventsPage() {
        if (typeof ICDEV.fetchJSON !== "function") return;
        var url = "/api/events/recent?limit=50";
        if (_filters.severity) url += "&severity=" + encodeURIComponent(_filters.severity);
        if (_filters.hook_type) url += "&hook_type=" + encodeURIComponent(_filters.hook_type);
        if (_filters.tool_name) url += "&tool_name=" + encodeURIComponent(_filters.tool_name);
        ICDEV.fetchJSON(url).then(function (data) {
            if (!data || !data.events) return;
            var tbody = findTbody("Recent Events") || document.querySelector("table tbody");
            if (!tbody) return;
            while (tbody.firstChild) tbody.removeChild(tbody.firstChild);
            for (var i = 0; i < Math.min(data.events.length, 50); i++) {
                var e = data.events[i];
                var tr = document.createElement("tr");
                tr.innerHTML = "<td>" + esc(e.hook_type || "") + "</td><td>" + esc(e.tool_name || "") +
                    "</td><td>" + esc(e.session_id || "") + "</td><td>" + esc(e.created_at || "") + "</td>";
                tbody.appendChild(tr);
            }
        });
    }

    // ========================================================================
    // 7. DOM Helpers
    // ========================================================================
    function updateCardValue(label, value, colorClass) {
        var cards = document.querySelectorAll(".card");
        for (var i = 0; i < cards.length; i++) {
            var lbl = cards[i].querySelector(".card-label");
            if (lbl && lbl.textContent.indexOf(label) !== -1) {
                var el = cards[i].querySelector(".card-value");
                if (el) {
                    el.textContent = value;
                    if (colorClass) el.className = "card-value " + colorClass;
                }
                break;
            }
        }
    }

    function findTbody(headerText) {
        var headers = document.querySelectorAll(".table-header h2, .table-container h2");
        for (var i = 0; i < headers.length; i++) {
            if (headers[i].textContent.indexOf(headerText) !== -1) {
                var c = headers[i].closest(".table-container");
                if (c) return c.querySelector("tbody");
            }
        }
        return null;
    }

    function prependRow(tbody, cells) {
        // Ensure container is marked as live region for screen readers
        if (tbody.parentNode && !tbody.parentNode.getAttribute("aria-live")) {
            tbody.parentNode.setAttribute("aria-live", "polite");
            tbody.parentNode.setAttribute("aria-relevant", "additions");
        }
        var tr = document.createElement("tr");
        tr.style.cssText = "opacity:0;transition:opacity .3s";
        for (var i = 0; i < cells.length; i++) {
            var td = document.createElement("td");
            td.innerHTML = cells[i];
            tr.appendChild(td);
        }
        tbody.insertBefore(tr, tbody.firstChild);
        void tr.offsetHeight; // force reflow
        tr.style.opacity = "1";
        // Trim to MAX_TABLE_ROWS
        var rows = tbody.querySelectorAll("tr:not([data-icdev-empty-msg]):not(.empty-row)");
        for (var r = rows.length - 1; r >= MAX_TABLE_ROWS; r--) {
            if (rows[r].parentNode) rows[r].parentNode.removeChild(rows[r]);
        }
    }

    /** Delegate to shared ICDEV.escapeHTML (api.js). */
    function esc(str) {
        return ICDEV.escapeHTML ? ICDEV.escapeHTML(str) : String(str || "");
    }

    function badge(severity) {
        var cm = { critical: "critical", high: "high", warning: "warning", info: "info", low: "low" };
        return '<span class="badge badge-' + (cm[severity] || "info") + '" role="status" aria-label="Severity: ' + esc(severity) + '">' + esc(severity) + '</span>';
    }

    function timeAgo(iso) {
        if (typeof ICDEV.formatTimeAgo === "function") return esc(ICDEV.formatTimeAgo(iso));
        if (!iso) return "just now";
        var d = Date.now() - new Date(iso).getTime();
        if (d < 60000) return "just now";
        var m = Math.floor(d / 60000);
        return m < 60 ? m + "m ago" : Math.floor(m / 60) + "h ago";
    }

    // ========================================================================
    // 8. Event Filter Bar (events page only)
    // ========================================================================

    /** Build and insert a filter bar above the events table on /events page. */
    function renderFilterBar() {
        if (_filterBarRendered) return;
        if (window.location.pathname !== "/events") return;
        var target = document.querySelector(".table-container") || document.querySelector("table");
        if (!target) return;
        _filterBarRendered = true;

        var bar = document.createElement("div");
        bar.className = "icdev-filter-bar";
        bar.id = "icdev-event-filters";
        bar.setAttribute("role", "toolbar");
        bar.setAttribute("aria-label", "Event filters");

        // Load filter options from API
        var fetchFn = (typeof ICDEV.fetchJSON === "function") ? ICDEV.fetchJSON : null;
        if (!fetchFn) return;

        fetchFn("/api/events/filter-options").then(function (data) {
            if (!data) return;

            // Severity dropdown
            bar.innerHTML = '<label>Filters:</label>';
            bar.innerHTML += buildSelect("icdev-filter-sev", "Severity", ["critical", "high", "warning", "info", "low"]);
            bar.innerHTML += buildSelect("icdev-filter-hook", "Hook Type", data.hook_types || []);
            bar.innerHTML += buildSelect("icdev-filter-tool", "Tool", data.tool_names || []);
            bar.innerHTML += '<button class="icdev-filter-clear" id="icdev-filter-clear">Clear</button>';

            target.parentNode.insertBefore(bar, target);

            // Attach change handlers
            var sevSel = document.getElementById("icdev-filter-sev");
            var hookSel = document.getElementById("icdev-filter-hook");
            var toolSel = document.getElementById("icdev-filter-tool");
            var clearBtn = document.getElementById("icdev-filter-clear");

            if (sevSel) sevSel.addEventListener("change", function () {
                _filters.severity = this.value; _cursor = ""; refreshEventsPage();
            });
            if (hookSel) hookSel.addEventListener("change", function () {
                _filters.hook_type = this.value; _cursor = ""; refreshEventsPage();
            });
            if (toolSel) toolSel.addEventListener("change", function () {
                _filters.tool_name = this.value; _cursor = ""; refreshEventsPage();
            });
            if (clearBtn) clearBtn.addEventListener("click", function () {
                _filters = { severity: "", hook_type: "", tool_name: "" };
                if (sevSel) sevSel.value = "";
                if (hookSel) hookSel.value = "";
                if (toolSel) toolSel.value = "";
                _cursor = "";
                refreshEventsPage();
            });
        });
    }

    function buildSelect(id, label, options) {
        var html = '<select id="' + id + '" aria-label="Filter by ' + label + '">';
        html += '<option value="">All ' + label + 's</option>';
        for (var i = 0; i < options.length; i++) {
            html += '<option value="' + esc(options[i]) + '">' + esc(options[i]) + '</option>';
        }
        html += '</select>';
        return html;
    }

    // ========================================================================
    // 9. Public API
    // ========================================================================

    /** Manually initiate HTTP polling. Called automatically on DOMContentLoaded. */
    ICDEV.connectLive = function () { startPolling(); };

    /** Manually stop HTTP polling. */
    ICDEV.disconnectLive = function () { stopPolling(); };

    /** Check whether polling is currently active and healthy. */
    ICDEV.isLiveConnected = function () { return _state === STATE_CONNECTED; };

    /** Set a filter and reset the cursor so events are re-fetched with new criteria. */
    ICDEV.setEventFilter = function (key, value) {
        if (key in _filters) { _filters[key] = value || ""; _cursor = ""; }
    };

    /** Get current filter state. */
    ICDEV.getEventFilters = function () {
        return { severity: _filters.severity, hook_type: _filters.hook_type, tool_name: _filters.tool_name };
    };

    // Legacy SSE aliases (backward compat)
    ICDEV.connectSSE = ICDEV.connectLive;
    ICDEV.disconnectSSE = ICDEV.disconnectLive;
    ICDEV.isSSEConnected = ICDEV.isLiveConnected;

    // ========================================================================
    // 9. Initialization
    // ========================================================================
    function initLive() {
        ensureStatusDot();
        renderFilterBar();
        startPolling();
        window.addEventListener("beforeunload", stopPolling);
        // Pause polling when tab is hidden, resume when visible
        document.addEventListener("visibilitychange", function () {
            if (document.hidden) {
                if (_pollTimer) { clearTimeout(_pollTimer); _pollTimer = null; }
            } else {
                if (!_pollTimer && _state !== STATE_DISCONNECTED) doPoll();
            }
        });
        console.log("[ICDEV Live] Module initialized. HTTP poll transport (D103).");
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initLive);
    } else {
        initLive();
    }
})();
