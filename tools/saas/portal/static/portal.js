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

            if (data.key) {
                // Show the key in the modal
                var modal = document.getElementById("key-modal");
                var keyDisplay = document.getElementById("modal-key-value");
                if (modal && keyDisplay) {
                    keyDisplay.textContent = data.key;
                    modal.style.display = "flex";
                } else {
                    // Fallback: reload page with key shown
                    window.location.href = "/portal/keys?new_key=" + encodeURIComponent(data.key);
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
    // Page Initialization
    // -----------------------------------------------------------------------

    /**
     * Initialize portal JavaScript on page load.
     */
    function init() {
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

})();
