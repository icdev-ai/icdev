/**
 * CUI // SP-CTI
 * ICDEV Dashboard - Kanban Board Auto-Refresh
 * Fetches projects from /api/projects and re-renders Kanban columns.
 */

(function () {
    "use strict";

    var REFRESH_INTERVAL_MS = 30000;
    var _kanbanTimer = null;

    /**
     * Render project cards into Kanban columns.
     * @param {Array} projects - Array of project objects with id, name, type, status, classification
     */
    function renderKanban(projects) {
        var board = document.getElementById("kanban-board");
        if (!board) return;

        // Group projects by status
        var columns = { planning: [], active: [], completed: [], inactive: [] };
        projects.forEach(function (p) {
            var status = p.status || "inactive";
            if (columns[status]) {
                columns[status].push(p);
            } else {
                columns.inactive.push(p);
            }
        });

        // Update each column
        var columnEls = board.querySelectorAll(".kanban-column");
        columnEls.forEach(function (colEl) {
            var status = colEl.getAttribute("data-status");
            if (!status || !columns[status]) return;

            var items = columns[status];

            // Update count badge
            var countEl = colEl.querySelector(".kanban-column-count");
            if (countEl) countEl.textContent = items.length;

            // Re-render body
            var bodyEl = colEl.querySelector(".kanban-column-body");
            if (!bodyEl) return;

            if (items.length === 0) {
                bodyEl.innerHTML = '<div class="kanban-empty">No projects</div>';
                return;
            }

            var esc = window.ICDEV && ICDEV.escapeHTML ? ICDEV.escapeHTML : function (s) { return String(s || ""); };
            var html = items.map(function (p) {
                var typeBadge = p.type
                    ? '<span class="badge badge-info">' + esc(p.type) + "</span>"
                    : "";
                var classBadge = p.classification
                    ? '<span class="badge badge-warning">' + esc(p.classification) + "</span>"
                    : "";
                return (
                    '<a href="/projects/' + esc(p.id) + '" class="kanban-card" data-project-id="' + esc(p.id) + '">' +
                    '<div class="kanban-card-title" title="' + esc(p.name) + '">' + esc(p.name) + "</div>" +
                    '<div class="kanban-card-meta">' + typeBadge + classBadge + "</div>" +
                    "</a>"
                );
            }).join("");

            bodyEl.innerHTML = html;
        });
    }

    /**
     * Fetch projects from API and re-render the Kanban board.
     */
    function refreshKanban() {
        if (!window.ICDEV || !ICDEV.fetchJSON) return;
        ICDEV.fetchJSON("/api/projects").then(function (data) {
            if (data && data.projects) {
                renderKanban(data.projects);
            }
        });
    }

    /**
     * Start periodic Kanban refresh.
     */
    function startKanbanRefresh() {
        stopKanbanRefresh();
        _kanbanTimer = setInterval(refreshKanban, REFRESH_INTERVAL_MS);
    }

    function stopKanbanRefresh() {
        if (_kanbanTimer) {
            clearInterval(_kanbanTimer);
            _kanbanTimer = null;
        }
    }

    // Initialize on page load
    document.addEventListener("DOMContentLoaded", function () {
        if (document.getElementById("kanban-board")) {
            startKanbanRefresh();
        }
    });

    // Expose to global ICDEV namespace
    if (window.ICDEV) {
        ICDEV.refreshKanban = refreshKanban;
        ICDEV.startKanbanRefresh = startKanbanRefresh;
        ICDEV.stopKanbanRefresh = stopKanbanRefresh;
    }
})();
