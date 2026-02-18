// CUI // SP-CTI
/**
 * ICDEV Dashboard - Batch Operations Module
 * Provides catalog display, execution, and progress tracking for
 * multi-step batch operations (Full ATO, Security Scan, etc.).
 *
 * Extends window.ICDEV (created by api.js, enhanced by ux.js).
 * Auto-initializes on DOMContentLoaded when pathname is /batch.
 * Zero external dependencies.
 */

(function () {
    "use strict";

    var ICDEV = window.ICDEV || {};
    window.ICDEV = ICDEV;

    // ========================================================================
    // CONFIGURATION
    // ========================================================================

    var POLL_INTERVAL_MS = 2000;
    var ICON_MAP = {
        shield:    "\uD83D\uDEE1\uFE0F",  // shield
        lock:      "\uD83D\uDD12",          // lock
        clipboard: "\uD83D\uDCCB",          // clipboard
        hammer:    "\uD83D\uDD28"           // hammer
    };
    var STATUS_ICONS = {
        pending:   "\u2022",   // bullet
        running:   "\u25B6",   // play triangle (animated via CSS)
        completed: "\u2713",   // checkmark
        failed:    "\u2715",   // X
        skipped:   "\u2212",   // minus
        cancelled: "\u2718"    // heavy X
    };

    // Active state
    var _currentProjectId = "";
    var _pollTimer = null;
    var _containerId = "batch-container";
    var _currentRunId = null;
    var _stopOnFailure = false;

    // ========================================================================
    // STYLES (injected once)
    // ========================================================================

    var _stylesInjected = false;

    function injectStyles() {
        if (_stylesInjected) {
            return;
        }
        _stylesInjected = true;

        var css = [
            ".batch-catalog {",
            "  display: grid;",
            "  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));",
            "  gap: 20px;",
            "  padding: 20px 0;",
            "}",
            ".batch-card {",
            "  background: #16213e;",
            "  border: 1px solid #2a2a40;",
            "  border-radius: 8px;",
            "  padding: 24px;",
            "  transition: border-color 0.2s ease, box-shadow 0.2s ease;",
            "}",
            ".batch-card:hover {",
            "  border-color: #4a90d9;",
            "  box-shadow: 0 4px 16px rgba(74, 144, 217, 0.15);",
            "}",
            ".batch-card-icon {",
            "  font-size: 2rem;",
            "  margin-bottom: 12px;",
            "}",
            ".batch-card-title {",
            "  font-size: 1.1rem;",
            "  font-weight: 700;",
            "  color: #e0e0e0;",
            "  margin-bottom: 8px;",
            "}",
            ".batch-card-desc {",
            "  font-size: 0.85rem;",
            "  color: #a0a0b8;",
            "  line-height: 1.5;",
            "  margin-bottom: 16px;",
            "}",
            ".batch-card-steps {",
            "  font-size: 0.78rem;",
            "  color: #6c6c80;",
            "  margin-bottom: 16px;",
            "}",
            ".batch-card-steps span {",
            "  display: inline-block;",
            "  background: #1a1a2e;",
            "  padding: 2px 8px;",
            "  border-radius: 4px;",
            "  margin: 2px 4px 2px 0;",
            "}",
            ".batch-run-btn {",
            "  display: inline-block;",
            "  padding: 8px 20px;",
            "  background: #4a90d9;",
            "  color: #fff;",
            "  border: none;",
            "  border-radius: 6px;",
            "  font-size: 0.85rem;",
            "  font-weight: 600;",
            "  cursor: pointer;",
            "  transition: background 0.2s ease;",
            "}",
            ".batch-run-btn:hover {",
            "  background: #3a7bc8;",
            "}",
            ".batch-run-btn:disabled {",
            "  background: #2a2a40;",
            "  color: #6c6c80;",
            "  cursor: not-allowed;",
            "}",
            "/* Progress view */",
            ".batch-progress {",
            "  background: #16213e;",
            "  border: 1px solid #2a2a40;",
            "  border-radius: 8px;",
            "  padding: 24px;",
            "  max-width: 800px;",
            "  margin: 20px auto;",
            "}",
            ".batch-progress-header {",
            "  display: flex;",
            "  align-items: center;",
            "  justify-content: space-between;",
            "  margin-bottom: 20px;",
            "}",
            ".batch-progress-title {",
            "  font-size: 1.15rem;",
            "  font-weight: 700;",
            "  color: #e0e0e0;",
            "}",
            ".batch-progress-status {",
            "  font-size: 0.82rem;",
            "  font-weight: 600;",
            "  padding: 4px 12px;",
            "  border-radius: 12px;",
            "}",
            ".batch-progress-status.running {",
            "  background: rgba(74, 144, 217, 0.15);",
            "  color: #4a90d9;",
            "}",
            ".batch-progress-status.completed {",
            "  background: rgba(40, 167, 69, 0.15);",
            "  color: #28a745;",
            "}",
            ".batch-progress-status.completed_with_failures {",
            "  background: rgba(255, 193, 7, 0.15);",
            "  color: #ffc107;",
            "}",
            ".batch-step-list {",
            "  list-style: none;",
            "  padding: 0;",
            "  margin: 0;",
            "}",
            ".batch-step-item {",
            "  display: flex;",
            "  align-items: center;",
            "  gap: 14px;",
            "  padding: 12px 0;",
            "  border-bottom: 1px solid #1a1a2e;",
            "}",
            ".batch-step-item:last-child {",
            "  border-bottom: none;",
            "}",
            ".batch-step-icon {",
            "  width: 28px;",
            "  height: 28px;",
            "  border-radius: 50%;",
            "  display: flex;",
            "  align-items: center;",
            "  justify-content: center;",
            "  font-size: 0.8rem;",
            "  font-weight: 700;",
            "  flex-shrink: 0;",
            "}",
            ".batch-step-icon.pending {",
            "  background: #1a1a2e;",
            "  color: #6c6c80;",
            "  border: 2px solid #2a2a40;",
            "}",
            ".batch-step-icon.running {",
            "  background: #16213e;",
            "  color: #4a90d9;",
            "  border: 2px solid #4a90d9;",
            "  animation: icdev-batch-spin 1.2s linear infinite;",
            "}",
            ".batch-step-icon.completed {",
            "  background: #28a745;",
            "  color: #fff;",
            "  border: 2px solid #28a745;",
            "}",
            ".batch-step-icon.failed {",
            "  background: rgba(220, 53, 69, 0.15);",
            "  color: #dc3545;",
            "  border: 2px solid #dc3545;",
            "}",
            ".batch-step-name {",
            "  flex: 1;",
            "  font-size: 0.9rem;",
            "  color: #e0e0e0;",
            "  font-weight: 600;",
            "}",
            ".batch-step-name.pending {",
            "  color: #6c6c80;",
            "}",
            ".batch-step-time {",
            "  font-size: 0.75rem;",
            "  color: #6c6c80;",
            "  white-space: nowrap;",
            "}",
            ".batch-step-output {",
            "  font-size: 0.75rem;",
            "  color: #a0a0b8;",
            "  background: #0f0f23;",
            "  padding: 8px 12px;",
            "  border-radius: 4px;",
            "  margin: 4px 0 0 42px;",
            "  white-space: pre-wrap;",
            "  word-break: break-all;",
            "  max-height: 100px;",
            "  overflow-y: auto;",
            "}",
            ".batch-back-btn {",
            "  display: inline-block;",
            "  margin-top: 20px;",
            "  padding: 8px 20px;",
            "  background: transparent;",
            "  color: #4a90d9;",
            "  border: 1px solid #4a90d9;",
            "  border-radius: 6px;",
            "  font-size: 0.85rem;",
            "  cursor: pointer;",
            "  transition: background 0.2s ease;",
            "}",
            ".batch-back-btn:hover {",
            "  background: rgba(74, 144, 217, 0.1);",
            "}",
            ".batch-project-input {",
            "  background: #1a1a2e;",
            "  border: 1px solid #2a2a40;",
            "  color: #e0e0e0;",
            "  padding: 8px 12px;",
            "  border-radius: 6px;",
            "  font-size: 0.85rem;",
            "  width: 100%;",
            "  max-width: 280px;",
            "  margin-bottom: 16px;",
            "}",
            ".batch-project-input:focus {",
            "  outline: none;",
            "  border-color: #4a90d9;",
            "}",
            "@keyframes icdev-batch-spin {",
            "  0%   { transform: rotate(0deg); }",
            "  100% { transform: rotate(360deg); }",
            "}",
            "/* History view */",
            ".batch-history-btn {",
            "  display: inline-block;",
            "  padding: 8px 16px;",
            "  background: transparent;",
            "  color: #4a90d9;",
            "  border: 1px solid #2a2a40;",
            "  border-radius: 6px;",
            "  font-size: 0.82rem;",
            "  cursor: pointer;",
            "  margin-bottom: 16px;",
            "  transition: border-color 0.2s ease;",
            "}",
            ".batch-history-btn:hover {",
            "  border-color: #4a90d9;",
            "}",
            ".batch-history-table {",
            "  width: 100%;",
            "  border-collapse: collapse;",
            "  font-size: 0.82rem;",
            "}",
            ".batch-history-table th {",
            "  text-align: left;",
            "  padding: 10px 12px;",
            "  color: #6c6c80;",
            "  font-weight: 600;",
            "  border-bottom: 1px solid #2a2a40;",
            "}",
            ".batch-history-table td {",
            "  padding: 10px 12px;",
            "  color: #e0e0e0;",
            "  border-bottom: 1px solid #1a1a2e;",
            "}",
            ".batch-history-table tr:hover td {",
            "  background: rgba(74, 144, 217, 0.05);",
            "}",
            ".batch-status-pill {",
            "  display: inline-block;",
            "  padding: 2px 10px;",
            "  border-radius: 10px;",
            "  font-size: 0.75rem;",
            "  font-weight: 600;",
            "}",
            ".batch-status-pill.completed { background: rgba(40,167,69,0.15); color: #28a745; }",
            ".batch-status-pill.failed, .batch-status-pill.stopped_on_failure { background: rgba(220,53,69,0.15); color: #dc3545; }",
            ".batch-status-pill.completed_with_failures { background: rgba(255,193,7,0.15); color: #ffc107; }",
            ".batch-status-pill.cancelled { background: rgba(108,108,128,0.15); color: #6c6c80; }",
            ".sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}"
        ].join("\n");

        var style = document.createElement("style");
        style.textContent = css;
        document.head.appendChild(style);
    }

    // ========================================================================
    // CATALOG RENDERING
    // ========================================================================

    function renderCatalog(catalog) {
        var container = document.getElementById(_containerId);
        if (!container) {
            return;
        }

        var html = '<button class="batch-history-btn" id="batch-history-btn" aria-label="View batch run history">' +
                   '\uD83D\uDCCB Run History</button>';
        html += '<div class="batch-catalog" role="list" aria-label="Available batch operations">';

        for (var i = 0; i < catalog.length; i++) {
            var item = catalog[i];
            var icon = ICON_MAP[item.icon] || ICON_MAP.shield;

            html += '<div class="batch-card" role="listitem" data-batch-id="' + escapeAttr(item.batch_id) + '">';
            html += '  <div class="batch-card-icon">' + icon + '</div>';
            html += '  <div class="batch-card-title">' + escapeHTML(item.name) + '</div>';
            html += '  <div class="batch-card-desc">' + escapeHTML(item.description) + '</div>';
            html += '  <div class="batch-card-steps">';
            for (var j = 0; j < item.steps.length; j++) {
                html += '<span>' + escapeHTML(item.steps[j]) + '</span>';
            }
            html += '  </div>';
            html += '  <button class="batch-run-btn" data-batch-id="' + escapeAttr(item.batch_id) + '">';
            html += '    Run ' + escapeHTML(item.name);
            html += '  </button>';
            html += '</div>';
        }

        html += '</div>';
        container.innerHTML = html;

        // Attach click handlers
        var buttons = container.querySelectorAll(".batch-run-btn");
        for (var b = 0; b < buttons.length; b++) {
            buttons[b].addEventListener("click", onRunClick);
        }
        // History button
        var histBtn = document.getElementById("batch-history-btn");
        if (histBtn) {
            histBtn.addEventListener("click", function () { loadHistory(); });
        }
    }

    // ========================================================================
    // EXECUTION
    // ========================================================================

    function onRunClick(evt) {
        var batchId = evt.target.getAttribute("data-batch-id");
        if (!batchId) {
            return;
        }

        // Prompt for project ID if not set
        if (!_currentProjectId) {
            var input = prompt("Enter project ID (e.g., proj-123):");
            if (!input || !input.trim()) {
                return;
            }
            _currentProjectId = input.trim();
        }

        // Disable all run buttons
        var buttons = document.querySelectorAll(".batch-run-btn");
        for (var i = 0; i < buttons.length; i++) {
            buttons[i].disabled = true;
        }

        startBatch(batchId, _currentProjectId);
    }

    function startBatch(batchId, projectId, stopOnFailure) {
        _stopOnFailure = !!stopOnFailure;
        var body = JSON.stringify({
            batch_id: batchId,
            project_id: projectId,
            stop_on_failure: _stopOnFailure
        });

        fetch("/api/batch/execute", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: body
        })
        .then(function (resp) {
            if (!resp.ok) {
                return resp.json().then(function (err) {
                    throw new Error(err.error || "Failed to start batch");
                });
            }
            return resp.json();
        })
        .then(function (data) {
            _currentRunId = data.run_id;
            if (ICDEV.showNotification) {
                ICDEV.showNotification("Batch started: " + batchId, "info");
            }
            pollStatus(data.run_id);
        })
        .catch(function (err) {
            if (ICDEV.showNotification) {
                ICDEV.showNotification("Error: " + err.message, "error");
            }
            // Re-enable buttons
            var buttons = document.querySelectorAll(".batch-run-btn");
            for (var i = 0; i < buttons.length; i++) {
                buttons[i].disabled = false;
            }
        });
    }

    // ========================================================================
    // POLLING & PROGRESS
    // ========================================================================

    function pollStatus(runId) {
        if (_pollTimer) {
            clearInterval(_pollTimer);
        }

        // First fetch immediately
        fetchAndRenderStatus(runId);

        _pollTimer = setInterval(function () {
            fetchAndRenderStatus(runId);
        }, POLL_INTERVAL_MS);
    }

    function fetchAndRenderStatus(runId) {
        fetch("/api/batch/status/" + encodeURIComponent(runId))
        .then(function (resp) {
            if (!resp.ok) {
                throw new Error("Status fetch failed");
            }
            return resp.json();
        })
        .then(function (run) {
            renderProgress(run);

            // Stop polling when batch is done
            if (run.status !== "running") {
                if (_pollTimer) {
                    clearInterval(_pollTimer);
                    _pollTimer = null;
                }
                onBatchComplete(run);
            }
        })
        .catch(function () {
            // Silently continue polling on transient errors
        });
    }

    var _prevStepStatuses = {};

    function renderProgress(run) {
        var container = document.getElementById(_containerId);
        if (!container) {
            return;
        }

        var statusLabel = run.status.replace(/_/g, " ");
        var statusClass = run.status;

        var html = '<div class="batch-progress">';
        html += '  <div class="batch-progress-header">';
        html += '    <div class="batch-progress-title">' + escapeHTML(run.batch_name || run.batch_id) + '</div>';
        html += '    <div class="batch-progress-status ' + escapeAttr(statusClass) + '">' + escapeHTML(statusLabel) + '</div>';
        html += '  </div>';

        // Pipeline visualization (uses ux.js if available)
        html += '  <div id="batch-pipeline"></div>';

        // Step list
        html += '  <ul class="batch-step-list" role="list" aria-label="Batch operation steps">';
        for (var i = 0; i < run.steps.length; i++) {
            var step = run.steps[i];
            var iconClass = step.status;
            var iconSymbol = STATUS_ICONS[step.status] || STATUS_ICONS.pending;

            html += '<li role="listitem" aria-label="' + escapeAttr(step.name) + ': ' + escapeAttr(step.status) + '">';
            html += '  <div class="batch-step-item">';
            html += '    <div class="batch-step-icon ' + escapeAttr(iconClass) + '" aria-hidden="true">' + iconSymbol + '</div>';
            html += '    <div class="batch-step-name ' + (step.status === "pending" ? "pending" : "") + '">' + escapeHTML(step.name) + '</div>';
            if (step.start_time && step.end_time) {
                var duration = calcDuration(step.start_time, step.end_time);
                html += '  <div class="batch-step-time">' + duration + '</div>';
            } else if (step.status === "running") {
                html += '  <div class="batch-step-time">running...</div>';
            }
            html += '  </div>';

            // Show output for completed/failed/skipped/cancelled steps
            if (step.output_summary && step.status !== "pending" && step.status !== "running") {
                html += '<div class="batch-step-output">' + escapeHTML(step.output_summary) + '</div>';
            }
            html += '</li>';

            // Fire toast on status transitions
            var stepKey = run.run_id + "-" + i;
            var prev = _prevStepStatuses[stepKey];
            if (prev && prev !== step.status && ICDEV.showNotification) {
                if (step.status === "completed") {
                    ICDEV.showNotification(step.name + " completed", "success", 3000);
                } else if (step.status === "failed") {
                    ICDEV.showNotification(step.name + " failed", "error", 5000);
                }
            }
            _prevStepStatuses[stepKey] = step.status;
        }
        html += '  </ul>';

        // Screen reader live region for status updates
        html += '<div aria-live="polite" class="sr-only" id="batch-sr-status">' +
                escapeHTML(run.batch_name) + ' is ' + escapeHTML(statusLabel) + '</div>';

        // Cancel button (visible when running)
        if (run.status === "running") {
            html += '<button class="batch-run-btn" id="batch-cancel-btn" style="background:#dc3545;margin-top:12px" aria-label="Cancel running batch operation">Cancel Batch</button>';
        }

        // Back button (visible when done)
        if (run.status !== "running") {
            html += '<button class="batch-back-btn" id="batch-back-btn" aria-label="Return to batch catalog">Back to Catalog</button>';
        }

        html += '</div>';
        container.innerHTML = html;

        // Render pipeline using ux.js helper if available
        if (ICDEV.createProgressPipeline) {
            var pipelineSteps = [];
            for (var p = 0; p < run.steps.length; p++) {
                var s = run.steps[p];
                var pipelineStatus = "pending";
                if (s.status === "completed") { pipelineStatus = "completed"; }
                else if (s.status === "running") { pipelineStatus = "active"; }
                else if (s.status === "failed") { pipelineStatus = "blocked"; }
                else if (s.status === "skipped" || s.status === "cancelled") { pipelineStatus = "pending"; }
                pipelineSteps.push({name: s.name, status: pipelineStatus});
            }
            ICDEV.createProgressPipeline("batch-pipeline", pipelineSteps);
        }

        // Attach cancel button handler
        var cancelBtn = document.getElementById("batch-cancel-btn");
        if (cancelBtn) {
            cancelBtn.addEventListener("click", function () {
                if (!_currentRunId) return;
                cancelBtn.disabled = true;
                cancelBtn.textContent = "Cancelling...";
                fetch("/api/batch/cancel/" + encodeURIComponent(_currentRunId), { method: "POST" })
                    .then(function (resp) { return resp.json(); })
                    .then(function () {
                        if (ICDEV.showNotification) {
                            ICDEV.showNotification("Cancel requested — finishing current step", "warning");
                        }
                    })
                    .catch(function () {
                        cancelBtn.disabled = false;
                        cancelBtn.textContent = "Cancel Batch";
                    });
            });
        }

        // Attach back button handler
        var backBtn = document.getElementById("batch-back-btn");
        if (backBtn) {
            backBtn.addEventListener("click", function () {
                _prevStepStatuses = {};
                _currentRunId = null;
                loadCatalog();
            });
        }
    }

    function onBatchComplete(run) {
        _currentRunId = null;
        if (!ICDEV.showNotification) {
            return;
        }
        if (run.status === "cancelled") {
            ICDEV.showNotification(run.batch_name + " was cancelled", "warning", 5000);
            return;
        }
        var failed = 0, skipped = 0;
        var total = run.steps.length;
        for (var i = 0; i < total; i++) {
            if (run.steps[i].status === "failed") failed++;
            if (run.steps[i].status === "skipped") skipped++;
        }
        var passed = total - failed - skipped;
        if (failed === 0 && skipped === 0) {
            ICDEV.showNotification(
                run.batch_name + " completed successfully (" + total + "/" + total + " steps)",
                "success",
                6000
            );
        } else if (run.status === "stopped_on_failure") {
            ICDEV.showNotification(
                run.batch_name + " stopped on failure (" + passed + " passed, " + failed + " failed, " + skipped + " skipped)",
                "error",
                8000
            );
        } else {
            ICDEV.showNotification(
                run.batch_name + " finished with " + failed + " failed step" + (failed > 1 ? "s" : "") +
                " (" + passed + "/" + total + " passed)",
                "warning",
                8000
            );
        }
    }

    // ========================================================================
    // HELPERS
    // ========================================================================

    /** Delegate to shared ICDEV.escapeHTML (api.js). */
    function escapeHTML(str) {
        return ICDEV.escapeHTML ? ICDEV.escapeHTML(str) : String(str || "");
    }

    function escapeAttr(str) {
        return (str || "").replace(/&/g, "&amp;").replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    }

    function calcDuration(startISO, endISO) {
        var start = new Date(startISO);
        var end = new Date(endISO);
        var diffMs = end.getTime() - start.getTime();
        if (diffMs < 0) { return "0s"; }
        var secs = Math.floor(diffMs / 1000);
        if (secs < 60) { return secs + "s"; }
        var mins = Math.floor(secs / 60);
        var remSecs = secs % 60;
        return mins + "m " + remSecs + "s";
    }

    // ========================================================================
    // HISTORY VIEW
    // ========================================================================

    function loadHistory() {
        var container = document.getElementById(_containerId);
        if (!container) return;
        container.innerHTML = '<div style="color:#6c6c80; padding:20px;">Loading run history...</div>';

        var url = "/api/batch/history?limit=25";
        if (_currentProjectId) {
            url += "&project_id=" + encodeURIComponent(_currentProjectId);
        }

        fetch(url)
        .then(function (resp) {
            if (!resp.ok) throw new Error("Failed to load history");
            return resp.json();
        })
        .then(function (data) {
            renderHistory(data.runs || [], data.total || 0);
        })
        .catch(function (err) {
            container.innerHTML = '<div style="color:#dc3545; padding:20px;">Error loading history: ' +
                escapeHTML(err.message) + '</div>';
        });
    }

    function renderHistory(runs, total) {
        var container = document.getElementById(_containerId);
        if (!container) return;

        var html = '<button class="batch-back-btn" id="history-back-btn" style="margin-bottom:16px">' +
                   '\u2190 Back to Catalog</button>';
        html += '<h3 style="color:#e0e0e0;margin:0 0 16px">Run History (' + total + ' total)</h3>';

        if (runs.length === 0) {
            html += '<div style="color:#6c6c80;padding:20px 0">No batch runs recorded yet.</div>';
            container.innerHTML = html;
            attachHistoryBackBtn();
            return;
        }

        html += '<table class="batch-history-table" role="table" aria-label="Batch run history">';
        html += '<thead><tr>';
        html += '<th>Batch</th><th>Project</th><th>Status</th><th>Steps</th><th>Started</th><th>Duration</th>';
        html += '</tr></thead><tbody>';

        for (var i = 0; i < runs.length; i++) {
            var r = runs[i];
            var statusLabel = (r.status || "").replace(/_/g, " ");
            var steps = r.steps || [];
            var passed = 0, failed = 0;
            for (var s = 0; s < steps.length; s++) {
                if (steps[s].status === "completed") passed++;
                if (steps[s].status === "failed") failed++;
            }
            var stepSummary = passed + "/" + steps.length;
            if (failed > 0) stepSummary += " (" + failed + " failed)";

            var started = r.start_time ? formatTimestamp(r.start_time) : "\u2014";
            var duration = (r.start_time && r.end_time) ? calcDuration(r.start_time, r.end_time) : "\u2014";

            html += '<tr>';
            html += '<td>' + escapeHTML(r.batch_name || r.batch_id) + '</td>';
            html += '<td style="color:#6c6c80">' + escapeHTML(r.project_id) + '</td>';
            html += '<td><span class="batch-status-pill ' + escapeAttr(r.status) + '">' + escapeHTML(statusLabel) + '</span></td>';
            html += '<td>' + stepSummary + '</td>';
            html += '<td style="color:#6c6c80">' + started + '</td>';
            html += '<td style="color:#6c6c80">' + duration + '</td>';
            html += '</tr>';
        }

        html += '</tbody></table>';
        container.innerHTML = html;
        attachHistoryBackBtn();
    }

    function attachHistoryBackBtn() {
        var btn = document.getElementById("history-back-btn");
        if (btn) {
            btn.addEventListener("click", function () { loadCatalog(); });
        }
    }

    function formatTimestamp(iso) {
        try {
            var d = new Date(iso);
            return d.toLocaleDateString() + " " + d.toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"});
        } catch (e) {
            return iso;
        }
    }

    // ========================================================================
    // CATALOG LOADER
    // ========================================================================

    function loadCatalog() {
        var container = document.getElementById(_containerId);
        if (!container) {
            return;
        }
        container.innerHTML = '<div style="color:#6c6c80; padding:20px;">Loading batch operations...</div>';

        fetch("/api/batch/catalog")
        .then(function (resp) {
            if (!resp.ok) {
                throw new Error("Failed to load catalog");
            }
            return resp.json();
        })
        .then(function (data) {
            renderCatalog(data.catalog || []);
        })
        .catch(function (err) {
            container.innerHTML = '<div style="color:#dc3545; padding:20px;">Error loading catalog: ' +
                escapeHTML(err.message) + '</div>';
        });
    }

    // ========================================================================
    // INITIALIZATION
    // ========================================================================

    function init() {
        // Only activate on /batch page
        if (window.location.pathname !== "/batch") {
            return;
        }

        injectStyles();
        loadCatalog();
    }

    // Expose for external use
    ICDEV.batchLoadCatalog = loadCatalog;
    ICDEV.batchStartBatch = startBatch;
    ICDEV.batchLoadHistory = loadHistory;

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }

})();
