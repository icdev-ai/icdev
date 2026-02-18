/**
 * ICDEV Dashboard - Minimal API Client
 * Provides fetch helpers and auto-refresh for dashboard sections.
 */

(function () {
    "use strict";

    // ---- Fetch helper ----
    async function fetchJSON(url) {
        try {
            const resp = await fetch(url);
            if (!resp.ok) {
                console.error(`[ICDEV API] ${resp.status} ${resp.statusText} — ${url}`);
                return null;
            }
            return await resp.json();
        } catch (err) {
            console.error(`[ICDEV API] Network error — ${url}`, err);
            return null;
        }
    }

    // ---- DOM update helpers ----

    /**
     * Update the text content of an element by selector.
     */
    function updateText(selector, value) {
        const el = document.querySelector(selector);
        if (el) el.textContent = value;
    }

    /**
     * Update the innerHTML of an element by selector.
     */
    function updateHTML(selector, html) {
        const el = document.querySelector(selector);
        if (el) el.innerHTML = html;
    }

    /**
     * Build a simple table row from an object.
     */
    function buildRow(fields) {
        return "<tr>" + fields.map(f => `<td>${f !== null && f !== undefined ? f : "—"}</td>`).join("") + "</tr>";
    }

    // ---- Auto-refresh ----

    let _refreshInterval = null;
    const DEFAULT_INTERVAL_MS = 30000; // 30 seconds

    /**
     * Start auto-refreshing by calling the provided callback at interval.
     * @param {Function} callback - function to call on each tick
     * @param {number} [intervalMs=30000] - refresh interval in ms
     */
    function startAutoRefresh(callback, intervalMs) {
        stopAutoRefresh();
        const ms = intervalMs || DEFAULT_INTERVAL_MS;
        _refreshInterval = setInterval(callback, ms);
        console.log(`[ICDEV API] Auto-refresh started (${ms}ms)`);
    }

    function stopAutoRefresh() {
        if (_refreshInterval) {
            clearInterval(_refreshInterval);
            _refreshInterval = null;
        }
    }

    // ---- Dashboard-specific refresh functions ----

    /**
     * Refresh the alert count badge in the nav (if present).
     */
    async function refreshAlertBadge() {
        const data = await fetchJSON("/api/alerts");
        if (!data) return;
        const badge = document.querySelector("#alert-badge");
        if (badge) {
            const firing = data.alerts.filter(a => a.status === "firing").length;
            badge.textContent = firing;
            badge.style.display = firing > 0 ? "inline-block" : "none";
        }
    }

    /**
     * Refresh health status on the monitoring page.
     */
    async function refreshHealthStatus() {
        const data = await fetchJSON("/api/metrics/health");
        if (!data) return;
        const el = document.querySelector("#health-status");
        if (el) {
            el.className = "health-banner " + data.status;
            el.textContent = "System Status: " + data.status.toUpperCase();
        }
    }

    // ---- Tab switching ----

    function initTabs() {
        const tabButtons = document.querySelectorAll(".tab-btn");
        tabButtons.forEach(btn => {
            btn.addEventListener("click", function () {
                const target = this.getAttribute("data-tab");

                // Deactivate all
                document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
                document.querySelectorAll(".tab-content").forEach(c => c.classList.remove("active"));

                // Activate selected
                this.classList.add("active");
                const panel = document.getElementById(target);
                if (panel) panel.classList.add("active");
            });
        });
    }

    // ---- Initialization ----

    document.addEventListener("DOMContentLoaded", function () {
        initTabs();

        // Auto-refresh on pages that have a data-auto-refresh attribute
        const body = document.body;
        if (body.dataset.autoRefresh === "true") {
            startAutoRefresh(function () {
                refreshAlertBadge();
                refreshHealthStatus();
            }, DEFAULT_INTERVAL_MS);
        }
    });

    // ---- Shared utilities ----

    /**
     * Escape a string for safe HTML insertion.
     * Used across all dashboard JS modules — single implementation here.
     */
    function escapeHTML(str) {
        var d = document.createElement("div");
        d.appendChild(document.createTextNode(String(str == null ? "" : str)));
        return d.innerHTML;
    }

    // Expose API to global scope for inline usage
    window.ICDEV = {
        fetchJSON: fetchJSON,
        updateText: updateText,
        updateHTML: updateHTML,
        buildRow: buildRow,
        startAutoRefresh: startAutoRefresh,
        stopAutoRefresh: stopAutoRefresh,
        refreshAlertBadge: refreshAlertBadge,
        refreshHealthStatus: refreshHealthStatus,
        escapeHTML: escapeHTML,
    };
})();
