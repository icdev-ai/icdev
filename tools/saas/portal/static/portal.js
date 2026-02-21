/**
 * ICDEV SaaS Tenant Admin Portal -- JavaScript
 * CUI // SP-CTI
 *
 * Client-side logic for the ICDEV admin portal: API calls, toast notifications,
 * confirm dialogs, API key management, user invitations, auto-refresh, and SSE.
 */

(function () {
    "use strict";

    // -----------------------------------------------------------------------
    // Configuration
    // -----------------------------------------------------------------------
    var API_BASE = "/api/v1";
    var REFRESH_INTERVAL_MS = 30000; // 30 seconds
    var TOAST_DURATION_MS = 4000;

    // -----------------------------------------------------------------------
    // API Helper
    // -----------------------------------------------------------------------

    /**
     * Make an authenticated API call.
     * Adds Authorization header from the session cookie value stored by
     * the portal login flow. Falls back to no auth for public endpoints.
     *
     * @param {string} method - HTTP method (GET, POST, PUT, DELETE)
     * @param {string} url - API endpoint path (e.g., "/api/v1/keys")
     * @param {object|null} body - Request body (will be JSON-serialized)
     * @returns {Promise<object>} - Parsed JSON response
     */
    async function apiCall(method, url, body) {
        var headers = {
            "Content-Type": "application/json",
            "Accept": "application/json"
        };

        // Retrieve API key from page meta or cookie
        var apiKey = getStoredApiKey();
        if (apiKey) {
            headers["Authorization"] = "Bearer " + apiKey;
        }

        var options = {
            method: method,
            headers: headers,
            credentials: "same-origin"
        };

        if (body && (method === "POST" || method === "PUT" || method === "PATCH")) {
            options.body = JSON.stringify(body);
        }

        var response = await fetch(url, options);

        if (response.status === 401) {
            showToast("Session expired. Please sign in again.", "error");
            setTimeout(function () {
                window.location.href = "/portal/login";
            }, 1500);
            throw new Error("Unauthorized");
        }

        if (response.status === 403) {
            showToast("Permission denied for this action.", "error");
            throw new Error("Forbidden");
        }

        var data;
        var contentType = response.headers.get("content-type");
        if (contentType && contentType.indexOf("application/json") !== -1) {
            data = await response.json();
        } else {
            data = { message: await response.text() };
        }

        if (!response.ok) {
            var errorMsg = data.error || data.message || "Request failed";
            showToast(errorMsg, "error");
            throw new Error(errorMsg);
        }

        return data;
    }

    /**
     * Get the stored API key from a cookie or meta tag.
     * The portal login POST stores it in the Flask session; we expose it
     * via a meta tag in the HTML or read from a known cookie.
     *
     * @returns {string|null}
     */
    function getStoredApiKey() {
        // Try meta tag first
        var meta = document.querySelector('meta[name="api-key"]');
        if (meta && meta.content) {
            return meta.content;
        }

        // Try localStorage (set by login page)
        try {
            var key = localStorage.getItem("icdev_portal_key");
            if (key) return key;
        } catch (e) {
            // localStorage not available
        }

        return null;
    }

    // -----------------------------------------------------------------------
    // Toast Notification System
    // -----------------------------------------------------------------------

    /**
     * Show a toast notification.
     *
     * @param {string} message - The message to display
     * @param {string} type - One of: "success", "error", "warning", "info"
     */
    function showToast(message, type) {
        type = type || "info";

        var container = document.getElementById("toast-container");
        if (!container) {
            container = document.createElement("div");
            container.id = "toast-container";
            document.body.appendChild(container);
        }

        var toast = document.createElement("div");
        toast.className = "toast toast-" + type;
        toast.textContent = message;
        container.appendChild(toast);

        // Auto-remove after duration
        setTimeout(function () {
            toast.style.opacity = "0";
            toast.style.transform = "translateX(40px)";
            toast.style.transition = "opacity 0.3s, transform 0.3s";
            setTimeout(function () {
                if (toast.parentNode) {
                    toast.parentNode.removeChild(toast);
                }
            }, 300);
        }, TOAST_DURATION_MS);
    }

    // -----------------------------------------------------------------------
    // Confirm Dialog
    // -----------------------------------------------------------------------

    /**
     * Show a confirmation dialog for destructive actions.
     *
     * @param {string} message - The confirmation message
     * @returns {Promise<boolean>} - True if user confirmed, false otherwise
     */
    function confirmAction(message) {
        return new Promise(function (resolve) {
            var result = window.confirm(message);
            resolve(result);
        });
    }

    // -----------------------------------------------------------------------
    // API Key Management
    // -----------------------------------------------------------------------

    /**
     * Create a new API key.
     * Called from the api_keys.html form onsubmit handler.
     *
     * @param {Event} event - Form submit event
     */
    async function createApiKey(event) {
        event.preventDefault();

        var nameInput = document.getElementById("key-name");
        var keyName = nameInput ? nameInput.value.trim() : "";

        if (!keyName) {
            showToast("Key name is required.", "warning");
            return;
        }

        try {
            var data = await apiCall("POST", API_BASE + "/keys", { name: keyName });

            // REST API returns { key: { id, key, prefix, name, ... } }
            var rawKey = (data.key && data.key.key) ? data.key.key : data.key;
            if (rawKey) {
                // Show the key in the modal
                var modal = document.getElementById("key-modal");
                var keyDisplay = document.getElementById("modal-key-value");
                if (modal && keyDisplay) {
                    keyDisplay.textContent = rawKey;
                    modal.style.display = "flex";
                } else {
                    // Fallback: reload page with key shown
                    window.location.href = "/portal/keys?new_key=" + encodeURIComponent(rawKey);
                }
                showToast("API key created successfully.", "success");
                if (nameInput) nameInput.value = "";
            } else {
                showToast("Key created but could not retrieve value.", "warning");
                window.location.reload();
            }
        } catch (err) {
            // Error already shown by apiCall
        }
    }

    /**
     * Close the key display modal and reload the page.
     */
    function closeKeyModal() {
        var modal = document.getElementById("key-modal");
        if (modal) {
            modal.style.display = "none";
        }
        window.location.reload();
    }

    /**
     * Revoke an API key.
     *
     * @param {string} keyId - The API key ID to revoke
     */
    async function revokeKey(keyId) {
        try {
            await apiCall("DELETE", API_BASE + "/keys/" + keyId);
            showToast("API key revoked.", "success");
            setTimeout(function () {
                window.location.reload();
            }, 800);
        } catch (err) {
            // Error already shown by apiCall
        }
    }

    /**
     * Copy text content of an element to clipboard.
     *
     * @param {string} elementId - ID of the element containing text to copy
     */
    function copyToClipboard(elementId) {
        var el = document.getElementById(elementId);
        if (!el) return;

        var text = el.textContent || el.innerText;
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(text).then(function () {
                showToast("Copied to clipboard.", "success");
            }).catch(function () {
                fallbackCopy(text);
            });
        } else {
            fallbackCopy(text);
        }
    }

    /**
     * Fallback copy using a temporary textarea.
     *
     * @param {string} text - Text to copy
     */
    function fallbackCopy(text) {
        var textarea = document.createElement("textarea");
        textarea.value = text;
        textarea.style.position = "fixed";
        textarea.style.opacity = "0";
        document.body.appendChild(textarea);
        textarea.select();
        try {
            document.execCommand("copy");
            showToast("Copied to clipboard.", "success");
        } catch (e) {
            showToast("Failed to copy. Please copy manually.", "error");
        }
        document.body.removeChild(textarea);
    }

    // -----------------------------------------------------------------------
    // User Management
    // -----------------------------------------------------------------------

    /**
     * Invite a user to the tenant.
     * Called from the team.html invite form.
     *
     * @param {Event} event - Form submit event
     */
    async function inviteUser(event) {
        event.preventDefault();

        var emailInput = document.getElementById("invite-email");
        var roleSelect = document.getElementById("invite-role");

        var email = emailInput ? emailInput.value.trim() : "";
        var role = roleSelect ? roleSelect.value : "viewer";

        if (!email) {
            showToast("Email address is required.", "warning");
            return;
        }

        try {
            await apiCall("POST", API_BASE + "/users", {
                email: email,
                role: role
            });
            showToast("User invited: " + email, "success");
            setTimeout(function () {
                window.location.reload();
            }, 800);
        } catch (err) {
            // Error already shown by apiCall
        }
    }

    /**
     * Remove a user from the tenant.
     *
     * @param {string} userId - The user ID to remove
     */
    async function removeUser(userId) {
        try {
            await apiCall("DELETE", API_BASE + "/users/" + userId);
            showToast("User removed.", "success");
            setTimeout(function () {
                window.location.reload();
            }, 800);
        } catch (err) {
            // Error already shown by apiCall
        }
    }

    // -----------------------------------------------------------------------
    // Dashboard Auto-Refresh
    // -----------------------------------------------------------------------

    var refreshTimer = null;

    /**
     * Start auto-refreshing the dashboard project data.
     * Polls /api/v1/projects every REFRESH_INTERVAL_MS and updates
     * the summary cards if present.
     */
    function startDashboardRefresh() {
        // Only run on the dashboard page
        var summaryCards = document.querySelectorAll(".summary-card");
        if (summaryCards.length === 0) return;

        refreshTimer = setInterval(async function () {
            try {
                var data = await apiCall("GET", API_BASE + "/projects");
                if (data && Array.isArray(data.projects)) {
                    updateDashboardCards(data);
                }
            } catch (err) {
                // Silently fail - the user will see stale data until next refresh
            }
        }, REFRESH_INTERVAL_MS);
    }

    /**
     * Update dashboard summary cards with fresh data.
     *
     * @param {object} data - API response from /api/v1/projects
     */
    function updateDashboardCards(data) {
        var cards = document.querySelectorAll(".summary-card .card-value");
        if (cards.length >= 2 && data.projects) {
            cards[0].textContent = data.projects.length;
            var active = data.projects.filter(function (p) {
                return p.status === "active";
            });
            cards[1].textContent = active.length;
        }
    }

    /**
     * Stop the dashboard auto-refresh timer.
     */
    function stopDashboardRefresh() {
        if (refreshTimer) {
            clearInterval(refreshTimer);
            refreshTimer = null;
        }
    }

    // -----------------------------------------------------------------------
    // SSE (Server-Sent Events) Listener
    // -----------------------------------------------------------------------

    var eventSource = null;

    /**
     * Connect to SSE endpoint for real-time events (if available).
     * Gracefully degrades if the server does not support SSE.
     */
    function connectSSE() {
        if (typeof EventSource === "undefined") return;

        var apiKey = getStoredApiKey();
        var sseUrl = API_BASE + "/events";
        if (apiKey) {
            sseUrl += "?api_key=" + encodeURIComponent(apiKey);
        }

        try {
            eventSource = new EventSource(sseUrl);

            eventSource.onmessage = function (event) {
                try {
                    var data = JSON.parse(event.data);
                    handleSSEEvent(data);
                } catch (e) {
                    // Ignore malformed events
                }
            };

            eventSource.addEventListener("project.updated", function (event) {
                try {
                    var data = JSON.parse(event.data);
                    showToast("Project updated: " + (data.name || data.project_id), "info");
                } catch (e) {
                    // Ignore
                }
            });

            eventSource.addEventListener("compliance.changed", function (event) {
                try {
                    var data = JSON.parse(event.data);
                    showToast("Compliance status changed: " + (data.control || ""), "warning");
                } catch (e) {
                    // Ignore
                }
            });

            eventSource.addEventListener("alert.triggered", function (event) {
                try {
                    var data = JSON.parse(event.data);
                    showToast("Alert: " + (data.message || "New alert"), "error");
                } catch (e) {
                    // Ignore
                }
            });

            eventSource.onerror = function () {
                // Close and do not reconnect - SSE is optional
                if (eventSource) {
                    eventSource.close();
                    eventSource = null;
                }
            };
        } catch (e) {
            // SSE not available, fail silently
        }
    }

    /**
     * Handle a generic SSE event.
     *
     * @param {object} data - Parsed event data
     */
    function handleSSEEvent(data) {
        if (data.type === "refresh") {
            window.location.reload();
        } else if (data.type === "notification") {
            showToast(data.message || "Notification received", data.level || "info");
        }
    }

    /**
     * Disconnect from SSE.
     */
    function disconnectSSE() {
        if (eventSource) {
            eventSource.close();
            eventSource = null;
        }
    }

    // -----------------------------------------------------------------------
    // Glossary Tooltip System
    // -----------------------------------------------------------------------

    /**
     * Dictionary of government/DoD/compliance acronyms with plain-English
     * definitions. Same 42 terms as the main ICDEV dashboard.
     */
    var GLOSSARY = {
        "POA&M":   "Plan of Action & Milestones \u2014 your fix-it list for security gaps with deadlines",
        "POAM":    "Plan of Action & Milestones \u2014 your fix-it list for security gaps with deadlines",
        "STIG":    "Security Technical Implementation Guide \u2014 DoD security checklist for systems",
        "SBOM":    "Software Bill of Materials \u2014 inventory of all software components and dependencies",
        "SSP":     "System Security Plan \u2014 the master document describing your system\u2019s security",
        "CAT-I":   "Category I \u2014 Critical severity finding that must be fixed before deployment",
        "CAT-II":  "Category II \u2014 High severity finding with a 30-day fix deadline",
        "CAT-III": "Category III \u2014 Medium severity finding with a 90-day fix deadline",
        "CUI":     "Controlled Unclassified Information \u2014 sensitive but not classified data",
        "IL2":     "Impact Level 2 \u2014 Public/non-sensitive data, commercial cloud OK",
        "IL4":     "Impact Level 4 \u2014 CUI data, requires AWS GovCloud",
        "IL5":     "Impact Level 5 \u2014 CUI data, requires dedicated GovCloud infrastructure",
        "IL6":     "Impact Level 6 \u2014 SECRET data, requires SIPR network and air-gapped systems",
        "ATO":     "Authorization to Operate \u2014 formal approval to run your system in production",
        "cATO":    "Continuous Authorization to Operate \u2014 automated ongoing security monitoring",
        "FedRAMP": "Federal Risk and Authorization Management Program \u2014 cloud security standard",
        "CMMC":    "Cybersecurity Maturity Model Certification \u2014 DoD contractor security standard",
        "NIST":    "National Institute of Standards and Technology \u2014 sets security frameworks",
        "NIST 800-53": "NIST Special Publication 800-53 \u2014 catalog of security and privacy controls for federal systems",
        "OSCAL":   "Open Security Controls Assessment Language \u2014 machine-readable compliance format",
        "ISSO":    "Information System Security Officer \u2014 person responsible for system security",
        "RMF":     "Risk Management Framework \u2014 the 6-step process for getting your ATO",
        "eMASS":   "Enterprise Mission Assurance Support Service \u2014 DoD\u2019s system for tracking ATOs",
        "A2A":     "Agent-to-Agent \u2014 how ICDEV\u2019s AI agents communicate with each other",
        "SAFe":    "Scaled Agile Framework \u2014 method for organizing large development teams",
        "WSJF":    "Weighted Shortest Job First \u2014 prioritization formula: value divided by effort",
        "BDD":     "Behavior-Driven Development \u2014 writing tests in plain English before coding",
        "TDD":     "Test-Driven Development \u2014 write failing tests first, then write code to pass them",
        "PI":      "Program Increment \u2014 a 10-week development cycle in SAFe",
        "COA":     "Course of Action \u2014 a proposed approach with cost, timeline, and risk tradeoffs",
        "SCRM":    "Supply Chain Risk Management \u2014 assessing vendor and dependency risks",
        "ISA":     "Interconnection Security Agreement \u2014 contract governing data sharing between systems",
        "CVE":     "Common Vulnerabilities and Exposures \u2014 a known security vulnerability with an ID number",
        "RTM":     "Requirements Traceability Matrix \u2014 maps requirements to tests and code",
        "FIPS":    "Federal Information Processing Standards \u2014 government encryption and security standards",
        "SAST":    "Static Application Security Testing \u2014 scanning code for vulnerabilities without running it",
        "IaC":     "Infrastructure as Code \u2014 defining servers and networks in code files (Terraform, Ansible)",
        "ReqIF":   "Requirements Interchange Format \u2014 standard format for sharing requirements between tools",
        "SysML":   "Systems Modeling Language \u2014 visual language for describing complex system architectures",
        "MBSE":    "Model-Based Systems Engineering \u2014 designing systems using models instead of documents",
        "DES":     "Digital Engineering Strategy \u2014 DoD mandate to use digital tools for engineering",
        "CAC/PIV": "Common Access Card / Personal Identity Verification \u2014 DoD smart card for authentication"
    };

    var _glossaryTooltip = null;
    var _glossaryHideTimer = null;

    /**
     * Escape HTML to prevent XSS in tooltip content.
     */
    function escapeHTML(str) {
        var div = document.createElement("div");
        div.appendChild(document.createTextNode(str));
        return div.innerHTML;
    }

    /**
     * Show glossary tooltip above the target element.
     */
    function showGlossaryTooltip(targetEl, text) {
        if (!_glossaryTooltip) {
            _glossaryTooltip = document.createElement("div");
            _glossaryTooltip.className = "glossary-tooltip";
            _glossaryTooltip.setAttribute("role", "tooltip");
            _glossaryTooltip.setAttribute("id", "portal-glossary-tooltip");
            _glossaryTooltip.style.position = "absolute";
            document.body.appendChild(_glossaryTooltip);
        }

        _glossaryTooltip.innerHTML = '<span class="tooltip-icon">i</span> ' + escapeHTML(text);

        // Position above target
        var rect = targetEl.getBoundingClientRect();
        var scrollX = window.pageXOffset || document.documentElement.scrollLeft;
        var scrollY = window.pageYOffset || document.documentElement.scrollTop;

        _glossaryTooltip.style.display = "block";
        var tipRect = _glossaryTooltip.getBoundingClientRect();

        var left = rect.left + scrollX + (rect.width / 2) - (tipRect.width / 2);
        var top = rect.top + scrollY - tipRect.height - 10;

        // Clamp to viewport
        if (left < 8) left = 8;
        if (left + tipRect.width > document.documentElement.clientWidth - 8) {
            left = document.documentElement.clientWidth - tipRect.width - 8;
        }
        if (top < scrollY + 4) {
            top = rect.bottom + scrollY + 10;
        }

        _glossaryTooltip.style.left = left + "px";
        _glossaryTooltip.style.top = top + "px";

        if (_glossaryHideTimer) {
            clearTimeout(_glossaryHideTimer);
            _glossaryHideTimer = null;
        }
    }

    /**
     * Hide the glossary tooltip with a small delay.
     */
    function hideGlossaryTooltip() {
        if (_glossaryHideTimer) clearTimeout(_glossaryHideTimer);
        _glossaryHideTimer = setTimeout(function () {
            if (_glossaryTooltip) {
                _glossaryTooltip.style.display = "none";
            }
            _glossaryHideTimer = null;
        }, 100);
    }

    /**
     * Initialize glossary tooltips on all [data-glossary] elements.
     */
    function initGlossary() {
        var glossaryEls = document.querySelectorAll("[data-glossary]");
        glossaryEls.forEach(function (el) {
            var term = el.getAttribute("data-glossary");
            var definition = GLOSSARY[term];
            if (!definition) return;

            if (!el.getAttribute("tabindex")) {
                el.setAttribute("tabindex", "0");
            }
            el.setAttribute("aria-describedby", "portal-glossary-tooltip");

            el.addEventListener("mouseenter", function () {
                showGlossaryTooltip(el, definition);
            });
            el.addEventListener("mouseleave", function () {
                hideGlossaryTooltip();
            });
            el.addEventListener("focus", function () {
                showGlossaryTooltip(el, definition);
            });
            el.addEventListener("blur", function () {
                hideGlossaryTooltip();
            });
        });
    }

    // -----------------------------------------------------------------------
    // Page Initialization
    // -----------------------------------------------------------------------

    /**
     * Initialize portal JavaScript on page load.
     */
    function init() {
        // Initialize glossary tooltips
        initGlossary();

        // Start dashboard auto-refresh if on dashboard
        startDashboardRefresh();

        // Attempt SSE connection
        connectSSE();

        // Clean up on page unload
        window.addEventListener("beforeunload", function () {
            stopDashboardRefresh();
            disconnectSSE();
        });
    }

    // Run init when DOM is ready
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }

    // -----------------------------------------------------------------------
    // LLM Provider Key Management (Phase 32)
    // -----------------------------------------------------------------------

    /**
     * Add a new LLM provider key via the REST API.
     * @param {Event} event - Form submit event
     */
    async function addLlmKey(event) {
        event.preventDefault();

        var providerSelect = document.getElementById("llm-provider");
        var labelInput = document.getElementById("llm-key-label");
        var keyInput = document.getElementById("llm-api-key");

        var provider = providerSelect ? providerSelect.value : "";
        var keyLabel = labelInput ? labelInput.value.trim() : "";
        var apiKey = keyInput ? keyInput.value.trim() : "";

        if (!provider) {
            showToast("Please select a provider.", "warning");
            return;
        }
        if (!apiKey) {
            showToast("API key is required.", "warning");
            return;
        }

        try {
            await apiCall("POST", API_BASE + "/llm-keys", {
                provider: provider,
                key_label: keyLabel || provider,
                api_key: apiKey
            });
            showToast("LLM key added for " + provider + ".", "success");
            // Clear form
            if (providerSelect) providerSelect.value = "";
            if (labelInput) labelInput.value = "";
            if (keyInput) keyInput.value = "";
            // Reload to show the new key in the table
            setTimeout(function () {
                window.location.reload();
            }, 800);
        } catch (err) {
            // Error already shown by apiCall
        }
    }

    /**
     * Revoke an LLM provider key.
     * @param {string} keyId - The LLM key ID to revoke
     */
    async function revokeLlmKey(keyId) {
        try {
            await apiCall("DELETE", API_BASE + "/llm-keys/" + keyId);
            showToast("LLM key revoked.", "success");
            setTimeout(function () {
                window.location.reload();
            }, 800);
        } catch (err) {
            // Error already shown by apiCall
        }
    }

    // -----------------------------------------------------------------------
    // Export to global scope (for inline onclick handlers in templates)
    // -----------------------------------------------------------------------
    window.apiCall = apiCall;
    window.showToast = showToast;
    window.confirmAction = confirmAction;
    window.createApiKey = createApiKey;
    window.closeKeyModal = closeKeyModal;
    window.revokeKey = revokeKey;
    window.copyToClipboard = copyToClipboard;
    window.inviteUser = inviteUser;
    window.removeUser = removeUser;
    window.addLlmKey = addLlmKey;
    window.revokeLlmKey = revokeLlmKey;

})();
