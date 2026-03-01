/**
 * CUI // SP-CTI
 *
 * ICDEV Dashboard - Table Enhancement Module
 * Adds search, sort, column filter, CSV export, and row count to all dashboard tables.
 * Extends window.ICDEV (created by api.js, extended by ux.js).
 *
 * No external dependencies. Works with the rendered DOM only.
 */

(function () {
    "use strict";

    var NS = window.ICDEV || (window.ICDEV = {});

    // ========================================================================
    // Constants
    // ========================================================================

    var MAX_FILTER_CARDINALITY = 10;
    var SORT_ASC = "asc";
    var SORT_DESC = "desc";

    // Inline style fragments matching the dark government theme
    var STYLES = {
        input: [
            "background: #0d1b2a",
            "color: #e0e0e0",
            "border: 1px solid #2a2a40",
            "border-radius: 4px",
            "padding: 5px 10px",
            "font-size: 0.8rem",
            "outline: none",
            "font-family: inherit",
            "min-width: 160px"
        ].join(";"),
        button: [
            "background: #4a90d9",
            "color: #fff",
            "border: none",
            "border-radius: 4px",
            "padding: 5px 12px",
            "font-size: 0.78rem",
            "font-weight: 600",
            "cursor: pointer",
            "font-family: inherit",
            "white-space: nowrap"
        ].join(";"),
        rowCount: [
            "font-size: 0.75rem",
            "color: #6c6c80",
            "padding: 6px 16px 8px",
            "text-align: right"
        ].join(";"),
        emptyMsg: [
            "text-align: center",
            "color: #6c6c80",
            "padding: 32px 16px",
            "font-style: italic"
        ].join(";"),
        sortIndicator: [
            "margin-left: 4px",
            "font-size: 0.65rem",
            "opacity: 0.7"
        ].join(";"),
        thClickable: [
            "cursor: pointer",
            "user-select: none"
        ].join(";"),
        filterIcon: [
            "margin-left: 4px",
            "cursor: pointer",
            "font-size: 0.65rem",
            "opacity: 0.6",
            "position: relative"
        ].join(";"),
        filterDropdown: [
            "position: absolute",
            "top: 100%",
            "left: 0",
            "z-index: 100",
            "background: #16213e",
            "border: 1px solid #2a2a40",
            "border-radius: 4px",
            "padding: 8px",
            "min-width: 160px",
            "max-height: 220px",
            "overflow-y: auto",
            "box-shadow: 0 4px 16px rgba(0,0,0,0.45)",
            "font-weight: normal",
            "text-transform: none",
            "letter-spacing: normal"
        ].join(";"),
        filterLabel: [
            "display: flex",
            "align-items: center",
            "gap: 6px",
            "padding: 3px 0",
            "font-size: 0.8rem",
            "color: #e0e0e0",
            "cursor: pointer",
            "white-space: nowrap"
        ].join(";"),
        headerControls: [
            "display: flex",
            "align-items: center",
            "gap: 8px"
        ].join(";")
    };

    // ========================================================================
    // Utility helpers
    // ========================================================================

    /**
     * Sanitize a string for use as a filename.
     */
    function sanitizeFilename(str) {
        return str.replace(/[^a-zA-Z0-9_\- ]/g, "").replace(/\s+/g, "_").substring(0, 80) || "export";
    }

    /**
     * Extract the text content from a cell, ignoring hidden elements.
     */
    function cellText(td) {
        return (td.textContent || "").trim();
    }

    /**
     * Determine whether a string looks like a number.
     */
    function isNumeric(str) {
        if (!str) return false;
        var cleaned = str.replace(/[,$%]/g, "");
        return cleaned !== "" && !isNaN(Number(cleaned));
    }

    /**
     * Attempt to parse a string as a date. Returns the timestamp or NaN.
     */
    function parseDate(str) {
        if (!str) return NaN;
        var d = new Date(str);
        return d.getTime();
    }

    /**
     * Compare function for sorting: handles numbers, dates, then falls back to
     * case-insensitive string comparison.
     */
    function compareValues(a, b) {
        // Try numeric comparison first
        if (isNumeric(a) && isNumeric(b)) {
            return parseFloat(a.replace(/[,$%]/g, "")) - parseFloat(b.replace(/[,$%]/g, ""));
        }
        // Try date comparison
        var da = parseDate(a);
        var db = parseDate(b);
        if (!isNaN(da) && !isNaN(db)) {
            return da - db;
        }
        // Fallback to string comparison
        return a.localeCompare(b, undefined, { sensitivity: "base" });
    }

    /**
     * Get all data rows from a tbody, excluding empty-row placeholders.
     */
    function getDataRows(tbody) {
        var rows = [];
        var trs = tbody.querySelectorAll("tr");
        for (var i = 0; i < trs.length; i++) {
            if (!trs[i].classList.contains("empty-row") && !trs[i].hasAttribute("data-icdev-empty-msg")) {
                rows.push(trs[i]);
            }
        }
        return rows;
    }

    // ========================================================================
    // Table enhancer
    // ========================================================================

    /**
     * Enhance a single table-container with search, sort, filter, export, and
     * row count capabilities.
     *
     * @param {HTMLElement} container - A div.table-container element
     */
    function enhanceTable(container) {
        var table = container.querySelector("table");
        if (!table) return;

        var thead = table.querySelector("thead");
        var tbody = table.querySelector("tbody");
        if (!thead || !tbody) return;

        var allDataRows = getDataRows(tbody);
        if (allDataRows.length === 0) return;

        var ths = thead.querySelectorAll("th");
        var colCount = ths.length;
        if (colCount === 0) return;

        // ---- State ----
        var sortCol = -1;
        var sortDir = null;             // null | "asc" | "desc"
        var searchTerm = "";
        var columnFilters = {};         // colIndex -> Set of allowed values (null = all)
        var openDropdown = null;        // currently-open filter dropdown element

        // Hide existing empty-row elements (the template ones)
        var emptyRows = tbody.querySelectorAll("tr.empty-row");
        for (var er = 0; er < emptyRows.length; er++) {
            emptyRows[er].style.display = "none";
        }

        // ---- Locate or create the header area for controls ----
        var tableHeader = container.querySelector(".table-header");
        var controlsWrapper = document.createElement("div");
        controlsWrapper.setAttribute("style", STYLES.headerControls);

        // Search input
        var searchInput = document.createElement("input");
        searchInput.type = "text";
        searchInput.placeholder = "Search\u2026";
        searchInput.setAttribute("aria-label", "Search table rows");
        searchInput.setAttribute("style", STYLES.input);
        controlsWrapper.appendChild(searchInput);

        // CSV export button
        var exportBtn = document.createElement("button");
        exportBtn.type = "button";
        exportBtn.textContent = "Export CSV";
        exportBtn.setAttribute("aria-label", "Export visible rows as CSV");
        exportBtn.setAttribute("style", STYLES.button);
        controlsWrapper.appendChild(exportBtn);

        if (tableHeader) {
            tableHeader.appendChild(controlsWrapper);
        } else {
            // If no .table-header exists, insert above the table
            var syntheticHeader = document.createElement("div");
            syntheticHeader.setAttribute("style", "padding: 10px 16px; display: flex; justify-content: flex-end; align-items: center;");
            syntheticHeader.appendChild(controlsWrapper);
            container.insertBefore(syntheticHeader, table);
        }

        // ---- Row count element ----
        var rowCountEl = document.createElement("div");
        rowCountEl.setAttribute("style", STYLES.rowCount);
        rowCountEl.setAttribute("aria-live", "polite");
        // Insert after the table
        if (table.nextSibling) {
            container.insertBefore(rowCountEl, table.nextSibling);
        } else {
            container.appendChild(rowCountEl);
        }

        // ---- Empty message row (injected, separate from template empty-row) ----
        var emptyMsgRow = document.createElement("tr");
        emptyMsgRow.setAttribute("data-icdev-empty-msg", "1");
        var emptyMsgTd = document.createElement("td");
        emptyMsgTd.setAttribute("colspan", String(colCount));
        emptyMsgTd.setAttribute("style", STYLES.emptyMsg);
        emptyMsgTd.textContent = "No matching rows";
        emptyMsgRow.appendChild(emptyMsgTd);
        emptyMsgRow.style.display = "none";
        tbody.appendChild(emptyMsgRow);

        // ================================================================
        // Column sort setup
        // ================================================================

        var sortIndicators = [];

        for (var ci = 0; ci < colCount; ci++) {
            (function (colIndex) {
                var th = ths[colIndex];
                th.setAttribute("style", (th.getAttribute("style") || "") + ";" + STYLES.thClickable);
                th.setAttribute("aria-sort", "none");
                th.setAttribute("role", "columnheader");

                // Sort indicator span
                var indicator = document.createElement("span");
                indicator.setAttribute("style", STYLES.sortIndicator);
                indicator.setAttribute("aria-hidden", "true");
                indicator.textContent = "";
                th.appendChild(indicator);
                sortIndicators.push(indicator);

                th.addEventListener("click", function (e) {
                    // Do not sort if the click target is inside a filter dropdown
                    if (e.target.closest && e.target.closest("[data-icdev-filter-dropdown]")) return;
                    if (e.target.hasAttribute && e.target.hasAttribute("data-icdev-filter-icon")) return;

                    if (sortCol === colIndex) {
                        sortDir = sortDir === SORT_ASC ? SORT_DESC : SORT_ASC;
                    } else {
                        sortCol = colIndex;
                        sortDir = SORT_ASC;
                    }
                    applyAll();
                });
            })(ci);
        }

        // ================================================================
        // Column filter setup (low-cardinality columns)
        // ================================================================

        for (var fi = 0; fi < colCount; fi++) {
            (function (colIndex) {
                // Collect unique values for this column
                var uniqueValues = {};
                for (var ri = 0; ri < allDataRows.length; ri++) {
                    var cells = allDataRows[ri].querySelectorAll("td");
                    if (cells[colIndex]) {
                        var val = cellText(cells[colIndex]);
                        uniqueValues[val] = true;
                    }
                }
                var keys = Object.keys(uniqueValues);
                if (keys.length < 2 || keys.length > MAX_FILTER_CARDINALITY) return;

                keys.sort(function (a, b) {
                    return a.localeCompare(b, undefined, { sensitivity: "base" });
                });

                // Create filter icon
                var th = ths[colIndex];
                th.style.position = "relative";

                var filterIcon = document.createElement("span");
                filterIcon.setAttribute("style", STYLES.filterIcon);
                filterIcon.setAttribute("data-icdev-filter-icon", "1");
                filterIcon.setAttribute("aria-label", "Filter this column");
                filterIcon.setAttribute("role", "button");
                filterIcon.setAttribute("tabindex", "0");
                filterIcon.textContent = "\u25BC";
                th.appendChild(filterIcon);

                // Dropdown panel
                var dropdown = document.createElement("div");
                dropdown.setAttribute("style", STYLES.filterDropdown);
                dropdown.setAttribute("data-icdev-filter-dropdown", "1");
                dropdown.style.display = "none";

                var checkboxes = [];

                for (var ki = 0; ki < keys.length; ki++) {
                    (function (value) {
                        var label = document.createElement("label");
                        label.setAttribute("style", STYLES.filterLabel);

                        var cb = document.createElement("input");
                        cb.type = "checkbox";
                        cb.checked = true;
                        cb.value = value;
                        cb.addEventListener("change", function () {
                            updateColumnFilter(colIndex, checkboxes);
                        });
                        checkboxes.push(cb);

                        var text = document.createElement("span");
                        text.textContent = value || "(empty)";

                        label.appendChild(cb);
                        label.appendChild(text);
                        dropdown.appendChild(label);
                    })(keys[ki]);
                }

                th.appendChild(dropdown);

                // Toggle dropdown on icon click
                filterIcon.addEventListener("click", function (e) {
                    e.stopPropagation();
                    toggleDropdown(dropdown);
                });
                filterIcon.addEventListener("keydown", function (e) {
                    if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        e.stopPropagation();
                        toggleDropdown(dropdown);
                    }
                });

                // Prevent clicks inside dropdown from triggering sort
                dropdown.addEventListener("click", function (e) {
                    e.stopPropagation();
                });
            })(fi);
        }

        function toggleDropdown(dropdown) {
            if (openDropdown && openDropdown !== dropdown) {
                openDropdown.style.display = "none";
            }
            if (dropdown.style.display === "none") {
                dropdown.style.display = "block";
                openDropdown = dropdown;
            } else {
                dropdown.style.display = "none";
                openDropdown = null;
            }
        }

        function updateColumnFilter(colIndex, checkboxes) {
            var allChecked = true;
            var allowed = {};
            for (var i = 0; i < checkboxes.length; i++) {
                if (checkboxes[i].checked) {
                    allowed[checkboxes[i].value] = true;
                } else {
                    allChecked = false;
                }
            }
            if (allChecked) {
                delete columnFilters[colIndex];
            } else {
                columnFilters[colIndex] = allowed;
            }
            applyAll();
        }

        // ================================================================
        // Close dropdowns when clicking outside
        // ================================================================

        document.addEventListener("click", function (e) {
            if (openDropdown && !openDropdown.contains(e.target)) {
                openDropdown.style.display = "none";
                openDropdown = null;
            }
        });

        // ================================================================
        // Search handler
        // ================================================================

        searchInput.addEventListener("input", function () {
            searchTerm = searchInput.value.toLowerCase();
            applyAll();
        });

        // ================================================================
        // Export CSV handler
        // ================================================================

        exportBtn.addEventListener("click", function () {
            var visibleRows = getVisibleRows();
            var headers = [];
            for (var hi = 0; hi < colCount; hi++) {
                // Get header text without sort indicator or filter icon
                var headerText = ths[hi].childNodes[0];
                headers.push(headerText ? headerText.textContent.trim() : "");
            }

            var csvLines = [];
            csvLines.push(headers.map(csvEscape).join(","));

            for (var ri = 0; ri < visibleRows.length; ri++) {
                var cells = visibleRows[ri].querySelectorAll("td");
                var row = [];
                for (var ci2 = 0; ci2 < colCount; ci2++) {
                    row.push(csvEscape(cells[ci2] ? cellText(cells[ci2]) : ""));
                }
                csvLines.push(row.join(","));
            }

            var csvContent = csvLines.join("\n");
            var filename = "export";
            if (tableHeader) {
                var h2 = tableHeader.querySelector("h2");
                if (h2) {
                    filename = sanitizeFilename(h2.textContent);
                }
            }

            downloadCSV(csvContent, filename + ".csv");
        });

        // ================================================================
        // Core apply function: filter, sort, update visibility
        // ================================================================

        function applyAll() {
            // 1. Determine visible rows (search + column filters)
            var filtered = [];
            for (var i = 0; i < allDataRows.length; i++) {
                var row = allDataRows[i];
                if (!matchesSearch(row)) continue;
                if (!matchesFilters(row)) continue;
                filtered.push(row);
            }

            // 2. Sort filtered rows
            if (sortCol >= 0 && sortDir) {
                filtered.sort(function (a, b) {
                    var cellsA = a.querySelectorAll("td");
                    var cellsB = b.querySelectorAll("td");
                    var valA = cellsA[sortCol] ? cellText(cellsA[sortCol]) : "";
                    var valB = cellsB[sortCol] ? cellText(cellsB[sortCol]) : "";
                    var cmp = compareValues(valA, valB);
                    return sortDir === SORT_DESC ? -cmp : cmp;
                });
            }

            // 3. Hide all data rows first
            for (var h = 0; h < allDataRows.length; h++) {
                allDataRows[h].style.display = "none";
            }

            // 4. Show filtered rows in sort order by re-appending
            for (var s = 0; s < filtered.length; s++) {
                filtered[s].style.display = "";
                tbody.appendChild(filtered[s]);
            }

            // 5. Update sort indicators
            for (var si = 0; si < sortIndicators.length; si++) {
                if (si === sortCol && sortDir) {
                    sortIndicators[si].textContent = sortDir === SORT_ASC ? " \u25B2" : " \u25BC";
                    ths[si].setAttribute("aria-sort", sortDir === SORT_ASC ? "ascending" : "descending");
                } else {
                    sortIndicators[si].textContent = "";
                    ths[si].setAttribute("aria-sort", "none");
                }
            }

            // 6. Show/hide empty message
            if (filtered.length === 0) {
                emptyMsgRow.style.display = "";
            } else {
                emptyMsgRow.style.display = "none";
            }

            // 7. Update row count
            updateRowCount(filtered.length, allDataRows.length);
        }

        function matchesSearch(row) {
            if (!searchTerm) return true;
            var cells = row.querySelectorAll("td");
            for (var c = 0; c < cells.length; c++) {
                if (cellText(cells[c]).toLowerCase().indexOf(searchTerm) !== -1) {
                    return true;
                }
            }
            return false;
        }

        function matchesFilters(row) {
            var cells = row.querySelectorAll("td");
            for (var colIdx in columnFilters) {
                if (!columnFilters.hasOwnProperty(colIdx)) continue;
                var allowed = columnFilters[colIdx];
                var idx = parseInt(colIdx, 10);
                var val = cells[idx] ? cellText(cells[idx]) : "";
                if (!allowed[val]) return false;
            }
            return true;
        }

        function getVisibleRows() {
            var visible = [];
            for (var i = 0; i < allDataRows.length; i++) {
                if (allDataRows[i].style.display !== "none") {
                    visible.push(allDataRows[i]);
                }
            }
            return visible;
        }

        function updateRowCount(shown, total) {
            if (shown === total) {
                rowCountEl.textContent = "Showing " + total + " row" + (total !== 1 ? "s" : "");
            } else {
                rowCountEl.textContent = "Showing " + shown + " of " + total + " rows";
            }
        }

        // ---- Initial row count ----
        updateRowCount(allDataRows.length, allDataRows.length);
    }

    // ========================================================================
    // CSV helpers
    // ========================================================================

    /**
     * Escape a value for CSV: wrap in quotes if it contains commas, quotes,
     * or newlines.
     */
    function csvEscape(value) {
        if (value == null) return '""';
        var str = String(value);
        if (str.indexOf(",") !== -1 || str.indexOf('"') !== -1 || str.indexOf("\n") !== -1) {
            return '"' + str.replace(/"/g, '""') + '"';
        }
        return str;
    }

    /**
     * Trigger a CSV download in the browser.
     */
    function downloadCSV(csvContent, filename) {
        var blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
        var link = document.createElement("a");
        link.href = URL.createObjectURL(blob);
        link.download = filename;
        link.style.display = "none";
        document.body.appendChild(link);
        link.click();
        // Cleanup
        setTimeout(function () {
            document.body.removeChild(link);
            URL.revokeObjectURL(link.href);
        }, 100);
    }

    // ========================================================================
    // Public API
    // ========================================================================

    /**
     * Manually enhance a specific table container. Useful for dynamically
     * added tables after initial page load.
     *
     * @param {HTMLElement} container - A div.table-container element
     */
    NS.enhanceTable = enhanceTable;

    // ========================================================================
    // Auto-initialization
    // ========================================================================

    function initTables() {
        var containers = document.querySelectorAll("div.table-container");
        for (var i = 0; i < containers.length; i++) {
            enhanceTable(containers[i]);
        }
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initTables);
    } else {
        initTables();
    }

})();
