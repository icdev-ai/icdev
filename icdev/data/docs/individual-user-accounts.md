# Individual User Accounts for ICDEV™ /gameday & /academy

## Executive Summary

ICDEV **already has** individual account support. The `dashboard_users` table, RBAC roles, and per-user API key generation are all live. The problem is that **auto-login via `.env` bypasses everything** — when `ICDEV_DASHBOARD_API_KEY` is set in `.env`, every visitor is automatically logged in as the same admin user.

To give each student their own account, you need to **disable auto-login** and **generate per-student credentials**. This is a configuration + operational change, not a rebuild.

---

## Current Auth Flow (Why Everyone Shares One Account)

```
Browser → /login
            ↓
    ┌─────────────────────────────────────────────┐
    │ login_page() in tools/dashboard/app.py:4775 │
    │                                             │
    │ 1. Check ICDEV_DASHBOARD_API_KEY in .env    │
    │ 2. If set → auto-login as admin@icdev.local │
    │ 3. Redirect to /index                         │
    │                                             │
    │ Student NEVER sees the login form.          │
    └─────────────────────────────────────────────┘
```

**The smoking gun** (`tools/dashboard/app.py:4778-4793`):
```python
env_key = os.environ.get("ICDEV_DASHBOARD_API_KEY", "")
if env_key:
    user = validate_api_key(env_key)
    if not user:
        user = bootstrap_env_user(env_key)  # Creates admin@icdev.local
    if user:
        flask_session["user_id"] = user["id"]
        return redirect(url_for("index"))
```

**Also:** `ICDEV_DASHBOARD_DEV_AUTOLOGIN` in `tools/dashboard/auth.py:626` is a full auth bypass for local dev. Must be `false` in production.

---

## What ICDEV Already Has (You Don't Need to Build)

| Feature | Status | Location |
|---------|--------|----------|
| User table with roles | ✅ Live | `dashboard_users` table — roles: `admin`, `developer`, `pm`, `isso`, `co`, `cor`, `ao` |
| Per-user API keys | ✅ Live | `dashboard_api_keys` table — SHA-256 hashed, with expiry, status, last_used |
| Create user UI | ✅ Live | `/admin/users` — create user, assign role, generate key, suspend/reactivate |
| Create user CLI | ✅ Live | `python tools/dashboard/auth.py create-admin --email ... --name ...` |
| Session management | ✅ Live | Flask signed cookies with 15-min JWT access / 7-day refresh |
| MFA enrollment | ✅ Live | `templates/mfa/enroll.html`, `templates/mfa/challenge.html` |
| OAuth/OIDC client | ✅ Ready | `tools/saas/auth/oauth_auth.py` — JWKS fetch, JWT validation, tenant-aware IdP |
| SAML 2.0 / CAC-PIV | ✅ Live | `tools/saas/auth/saml_auth.py` — full DoD IdP integration |
| Security context (MAC) | ✅ Live | `tools/security/security_context.py` — Bell-LaPadula clearance + compartments |
| Audit logging | ✅ Live | `dashboard_auth_log` — append-only, NIST AU-6 compliant |

---

## The Fix — Three Paths from Simplest to Most Complete

### Path 1: API Keys Per Student (30 minutes — do this today)

**What:** Keep the existing API-key login, but disable auto-login and generate individual keys.

**Steps:**

1. **Disable auto-login** in `.env`:
   ```bash
   # REMOVE or comment out:
   # ICDEV_DASHBOARD_API_KEY=icdev_dash_...
   
   # Ensure this is NOT set:
   # ICDEV_DASHBOARD_DEV_AUTOLOGIN=true
   ```

2. **Restart the dashboard** so the env change takes effect.

3. **Generate keys for each student** via the admin CLI:
   ```bash
   # As admin, run for each student:
   python tools/dashboard/auth.py create-admin \
     --email "student1@academy.local" \
     --name "Student One" \
     --json
   
   # Output: API key (save it — shown only once)
   ```

   Or via the `/admin/users` web UI:
   - Navigate to Admin → User Management
   - Click "Create User" → enter email, name, role=`developer`
   - Click "Gen Key" → copy the key, send to student

4. **Students log in** at `/login` with their individual API key.

**Pros:** Zero code changes. Works immediately.
**Cons:** Students must paste a long API key. No password reset flow. Key distribution is manual.

---

### Path 2: Username + Password Login (2-3 hours — recommended)

**What:** Add a username/password form to the existing login page, so students don't need to manage API keys.

**What ICDEV is missing:**
- Password hashing (bcrypt/argon2) in `tools/dashboard/auth.py`
- Password column in `dashboard_users` table (or separate `dashboard_passwords` table)
- Login form with email + password fields
- Password reset flow (optional for now — admin can reset via CLI)

**Minimal changes needed:**

```python
# 1. In tools/dashboard/auth.py — add password verification
import bcrypt

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())

def authenticate_user(email: str, password: str) -> dict | None:
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT * FROM dashboard_users WHERE email = %s AND status = 'active'",
            (email,)
        ).fetchone()
        if row and verify_password(password, row["password_hash"]):
            return dict(row)
        return None
    finally:
        conn.close()
```

```python
# 2. In tools/dashboard/app.py — update login_page()
@app.route("/login", methods=["GET", "POST"])
def login_page():
    # REMOVE the auto-login block (lines 4778-4793)
    
    if flask_request.method == "POST":
        # Try API key first (backward compat)
        raw_key = flask_request.form.get("api_key", "").strip()
        if raw_key:
            user = validate_api_key(raw_key)
        else:
            # New: try email + password
            email = flask_request.form.get("email", "").strip()
            password = flask_request.form.get("password", "")
            user = authenticate_user(email, password)
        
        if user:
            flask_session["user_id"] = user["id"]
            # ... rest unchanged
```

```html
<!-- 3. In tools/dashboard/templates/login.html — add email/password form -->
<form method="POST" action="{{ url_for('login_page') }}">
    <div class="form-group">
        <label for="email">Email</label>
        <input type="email" id="email" name="email" class="form-control" required>
    </div>
    <div class="form-group">
        <label for="password">Password</label>
        <input type="password" id="password" name="password" class="form-control" required>
    </div>
    <button type="submit" class="btn btn-primary btn-block">Sign In</button>
</form>

<hr>
<p class="text-muted">Or sign in with your API key:</p>
<!-- existing API key form -->
```

```sql
-- 4. Migration — add password_hash column
ALTER TABLE dashboard_users ADD COLUMN password_hash TEXT;
```

**Pros:** Familiar login UX. No key management. Easy password reset.
**Cons:** Small schema + code changes. Need to hash existing users' passwords (or require them to set one on next login).

---

### Path 3: Keycloak SSO Integration (1-2 days — future-proof)

**What:** Replace ICDEV's custom auth with Keycloak as the identity provider. ICDEV becomes an OIDC Relying Party.

**Why this is easier than it sounds:**
- `tools/saas/auth/oauth_auth.py` already has OIDC client logic (JWKS fetch, JWT validation, tenant-aware IdP resolution)
- Keycloak supports user registration, password resets, MFA, group/role mapping out of the box
- ICDEV just needs to point its OIDC config at Keycloak's `.well-known/openid-configuration`

**What needs to change:**

```python
# In tools/dashboard/app.py — replace login_page() with OIDC redirect
@app.route("/login")
def login_page():
    """Redirect to Keycloak for authentication."""
    keycloak_url = os.environ.get("KEYCLOAK_AUTH_URL")
    client_id = os.environ.get("KEYCLOAK_CLIENT_ID")
    redirect_uri = url_for("oidc_callback", _external=True)
    state = secrets.token_urlsafe(32)
    flask_session["oidc_state"] = state
    
    auth_url = (
        f"{keycloak_url}?"
        f"client_id={client_id}&"
        f"response_type=code&"
        f"scope=openid+profile+email&"
        f"redirect_uri={redirect_uri}&"
        f"state={state}"
    )
    return redirect(auth_url)

@app.route("/oidc/callback")
def oidc_callback():
    """Handle Keycloak callback, create/update user, set session."""
    code = flask_request.args.get("code")
    state = flask_request.args.get("state")
    
    # Verify state
    if state != flask_session.get("oidc_state"):
        abort(403)
    
    # Exchange code for token
    token = exchange_code_for_token(code)  # uses tools/saas/auth/oauth_auth.py
    
    # Extract claims
    email = token["email"]
    name = token.get("name", email)
    keycloak_role = token.get("realm_access", {}).get("roles", ["developer"])
    
    # Find or create user in dashboard_users
    user = get_user_by_email(email)
    if not user:
        user = create_user(email, name, role=map_keycloak_role(keycloak_role))
    
    flask_session["user_id"] = user["id"]
    return redirect(url_for("index"))
```

```yaml
# .env additions
KEYCLOAK_BASE_URL=https://keycloak.icdev.local
KEYCLOAK_REALM=icdev
KEYCLOAK_CLIENT_ID=icdev-dashboard
KEYCLOAK_CLIENT_SECRET=***
```

**Pros:** Centralized identity. Self-service registration. Password resets. MFA. Role sync from Keycloak groups.
**Cons:** Requires Keycloak deployment. More moving parts. OIDC callback handler needed.

---

## Linking Individual Accounts to LLM Proxy Virtual Keys

Once students have individual ICDEV accounts, you need to decide how their LLM usage is attributed.

### Option A: Cohort-Level Virtual Keys (Simplest)

```
Student logs in → ICDEV knows their identity → ICDEV's LLM Router uses
                 a shared cohort virtual key (e.g., sk-academy-2026-q3)
                 → LiteLLM Proxy → Anthropic
```

**ICDEV code change:** In the LLM router or academy module, attach the `user_id` as metadata to every LLM request:

```python
# In apps/forge_academy/blueprint.py or tools/llm/router.py
user = getattr(g, "current_user", {})
if user:
    request_metadata = {
        "user_id": user.get("id"),
        "email": user.get("email"),
        "cohort": "academy-2026-q3",
    }
    # Pass metadata to LiteLLM as extra_headers
    extra_headers = {
        "x-litellm-metadata": json.dumps(request_metadata)
    }
```

LiteLLM logs will then show which user made each request, even though the virtual key is shared.

### Option B: Per-User Virtual Keys (Most Granular)

```
Student logs in → ICDEV looks up their per-user virtual key
                 → LiteLLM Proxy enforces per-user budget/rate limit
                 → Anthropic
```

**Implementation:** Store per-user virtual keys in ICDEV's database:

```sql
ALTER TABLE dashboard_users ADD COLUMN llm_virtual_key TEXT;
```

On user creation (or first LLM call), generate a LiteLLM virtual key via API:

```python
import requests

def get_or_create_user_virtual_key(user_id: str, email: str) -> str:
    # Check cache/DB first
    user = get_user_by_id(user_id)
    if user and user.get("llm_virtual_key"):
        return user["llm_virtual_key"]
    
    # Generate new virtual key in LiteLLM
    resp = requests.post(
        "http://litellm-proxy:4000/key/generate",
        headers={"Authorization": f"Bearer {LITELLM_MASTER_KEY}"},
        json={
            "budget": 10.0,  # $10 per student
            "rpm_limit": 60,
            "tpm_limit": 100000,
            "models": ["anthropic/claude-sonnet-4"],
            "metadata": {"user_id": user_id, "email": email},
        }
    )
    virtual_key = resp.json()["key"]
    
    # Store in ICDEV DB
    update_user_llm_key(user_id, virtual_key)
    return virtual_key
```

Then in `tools/llm/router.py`, instead of using a single `LITELLM_STUDENT_KEY` env var, resolve the virtual key per-request:

```python
# In LLMRouter.invoke() or anthropic_provider.py
user = getattr(g, "current_user", None)
if user:
    virtual_key = get_or_create_user_virtual_key(user["id"], user.get("email"))
else:
    virtual_key = os.environ.get("LITELLM_DEFAULT_KEY")

# Use virtual_key as the api_key for the Anthropic provider
```

**Pros:** True per-user budget enforcement. Individual rate limiting. Fine-grained spend tracking.
**Cons:** Requires DB schema change. More LiteLLM API calls. Need cleanup for inactive users.

---

## Recommended Implementation Order

### Phase 1: Disable Auto-Login (Today — 5 minutes)
```bash
# In .env
# REMOVE: ICDEV_DASHBOARD_API_KEY=...
# REMOVE: ICDEV_DASHBOARD_DEV_AUTOLOGIN=true
# Restart dashboard
```

### Phase 2: Generate Student API Keys (Today — 30 minutes)
- Admin navigates to `/admin/users`
- Creates 20-80 student accounts with role=`developer`
- Generates keys, distributes via secure channel (email, LMS, etc.)
- Students log in at `/login` with their individual key

### Phase 3: Add Password Login (This week — 2-3 hours)
- Add `password_hash` column to `dashboard_users`
- Update `login.html` with email + password form
- Add `authenticate_user()` to `tools/dashboard/auth.py`
- Students can now log in with familiar username/password
- API keys still work for service accounts / automation

### Phase 4: LiteLLM Proxy with Per-User Keys (This week — 3-4 hours)
- Deploy LiteLLM Proxy (see `anthropic-proxy-strategy.md`)
- Add `llm_virtual_key` column to `dashboard_users`
- Implement `get_or_create_user_virtual_key()` in academy module
- Each student's LLM usage is budgeted individually

### Phase 5: Keycloak SSO (Future sprint — 1-2 days)
- Deploy Keycloak in Docker Compose or K8s
- Configure ICDEV as OIDC RP
- Students register/login via Keycloak
- Role mapping from Keycloak groups → ICDEV roles
- Optional: auto-provision virtual keys on first login

---

## Files to Change

| File | Change |
|------|--------|
| `.env` | Remove `ICDEV_DASHBOARD_API_KEY` and `ICDEV_DASHBOARD_DEV_AUTOLOGIN` |
| `tools/dashboard/app.py` | Remove auto-login block (lines 4778-4793). Add password auth branch in `login_page()`. |
| `tools/dashboard/auth.py` | Add `hash_password()`, `verify_password()`, `authenticate_user()`. |
| `tools/dashboard/templates/login.html` | Add email + password form above existing API key form. |
| `tools/db/init_icdev_db.py` | Add `password_hash TEXT` to `dashboard_users` CREATE TABLE. |
| `tools/db/migrations/` | Migration script for existing deployments. |
| `tools/llm/router.py` | Resolve per-user virtual key from `g.current_user` before calling provider. |
| `apps/forge_academy/blueprint.py` | Attach `user_id` metadata to LLM requests. |

---

## Summary

| Question | Answer |
|----------|--------|
| **Does ICDEV already support individual accounts?** | **Yes.** The `dashboard_users` table, per-user API keys, and admin UI are all live. |
| **Why does everyone share one account today?** | `ICDEV_DASHBOARD_API_KEY` in `.env` triggers auto-login as the same admin user for every visitor. |
| **Fastest fix?** | Remove the `.env` key. Generate individual API keys via `/admin/users`. Done in 30 minutes. |
| **Better fix?** | Add username/password login to the existing login page. 2-3 hours of work. |
| **Best long-term fix?** | Keycloak SSO + per-user LiteLLM virtual keys. Full identity + budget governance. |
| **Does this block the Anthropic proxy?** | No. The proxy is independent. You can deploy it in parallel. |

**Bottom line:** You don't need to build user accounts. You need to **stop using the shared auto-login** and start **using the account system that already exists**.
