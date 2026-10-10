# Running Keycloak Without an External Identity Provider

## Executive Summary

**Yes.** Keycloak is a **standalone identity manager**. It does not require an external OIDC provider, LDAP server, or any other identity source. Keycloak ships with its own built-in user database, password policies, session management, MFA, and self-registration — out of the box.

You only federate to external systems (like Active Directory, Azure AD, Okta) if you **want** to. Keycloak works perfectly as the sole identity authority for ICDEV.

---

## What Keycloak Is (Standalone Mode)

Keycloak is not a "bridge" or "wrapper" that requires another identity system behind it. It is a **full identity and access management (IAM) server** with:

| Feature | Built-in? |
|---------|-----------|
| User database (usernames, emails, passwords) | ✅ Yes — internal H2/PostgreSQL database |
| Password policies (length, complexity, history, rotation) | ✅ Yes — configurable per realm |
| Self-service user registration | ✅ Yes — toggle on/off per realm |
| Email verification | ✅ Yes — SMTP integration required |
| Password reset / "forgot password" | ✅ Yes — via email token |
| Multi-factor authentication (TOTP, WebAuthn, SMS) | ✅ Yes — opt-in or enforced |
| Session management (concurrent sessions, idle timeout) | ✅ Yes |
| Role and group management | ✅ Yes — create roles/groups in admin UI |
| Audit logging (logins, failures, admin actions) | ✅ Yes — events stored internally |
| Admin REST API | ✅ Yes — full CRUD on users, roles, groups |
| OIDC token issuance | ✅ Yes — Keycloak IS the OIDC provider |
| SAML assertion issuance | ✅ Yes — Keycloak IS the SAML IdP |

**Keycloak does NOT require:**
- Active Directory
- Azure AD / Entra ID
- Okta / Auth0 / OneLogin
- Any LDAP server
- Any other OIDC provider

These are all **optional federation sources** you can add later if you want.

---

## Standalone Keycloak Architecture for ICDEV

```
Student Browser
      │
      ├───(1) Navigates to ICDEV Dashboard
      │
      ├───(2) Not logged in → redirect to Keycloak login page
      │
      ├───(3) Keycloak checks its internal user database
      │        (PostgreSQL or H2 — NOT external AD/LDAP)
      │
      ├───(4) Valid credentials → Keycloak issues OIDC token
      │
      └───(5) ICDEV validates token via JWKS endpoint
              → g.current_user populated from token claims
              → LLM Router resolves virtual key
```

**No external identity system anywhere in this flow.**

---

## Deployment: Keycloak Standalone for ICDEV

### Docker Compose (No External IdP Required)

```yaml
# docker-compose.yml — Keycloak standalone
services:
  keycloak:
    image: quay.io/keycloak/keycloak:26.0
    command: ["start", "--optimized", "--hostname-port=8080"]
    environment:
      KC_DB: postgres
      KC_DB_URL: jdbc:postgresql://icdev-postgres:5432/keycloak
      KC_DB_USERNAME: keycloak
      KC_DB_PASSWORD: ${KEYCLOAK_DB_PASSWORD}
      KC_HOSTNAME: keycloak.icdev.local
      KC_PROXY: edge
      KEYCLOAK_ADMIN: admin
      KEYCLOAK_ADMIN_PASSWORD: ${KEYCLOAK_ADMIN_PASSWORD}
      # Optional: enable user self-registration
      KC_FEATURES: registration
    volumes:
      - keycloak-data:/opt/keycloak/data
    ports:
      - "8080:8080"
    networks:
      - icdev-net
    depends_on:
      - icdev-postgres

volumes:
  keycloak-data:
```

### First-Time Setup (Admin UI)

1. **Create the realm:**
   - Navigate to `http://keycloak.icdev.local:8080/admin`
   - Log in with `admin` / `${KEYCLOAK_ADMIN_PASSWORD}`
   - Create realm: `icdev`

2. **Create the OIDC client for ICDEV:**
   - Realm Settings → Clients → Create Client
   - **Client ID**: `icdev-dashboard`
   - **Client Authentication**: `On`
   - **Authentication Flow**: `Standard Flow` (Authorization Code)
   - **Valid Redirect URIs**: `http://icdev-dashboard:5000/oidc/callback`
   - **Web Origins**: `http://icdev-dashboard:5000`
   - Copy the **Client Secret** to ICDEV's `.env`

3. **Enable user registration (optional):**
   - Realm Settings → Login → `User registration: On`
   - Students can create their own accounts via a "Register" link on the login page
   - Or keep it off and admin creates accounts manually

4. **Create roles:**
   - Realm Settings → Roles → Create Role
   - `icdev-student`, `icdev-facilitator`, `icdev-admin`
   - These map to ICDEV dashboard roles in the OIDC callback

5. **Create users (if not using self-registration):**
   - Users → Add User
   - Enter username, email, first/last name
   - Credentials → Set Password → `Temporary: Off`
   - Role Mappings → Assign `icdev-student`

---

## ICDEV Configuration (No External IdP)

```yaml
# .env — only Keycloak, no external IdP
KEYCLOAK_BASE_URL=http://keycloak.icdev.local:8080
KEYCLOAK_REALM=icdev
KEYCLOAK_CLIENT_ID=icdev-dashboard
KEYCLOAK_CLIENT_SECRET=***
KEYCLOAK_AUTH_URL=${KEYCLOAK_BASE_URL}/realms/${KEYCLOAK_REALM}/protocol/openid-connect/auth
KEYCLOAK_TOKEN_URL=${KEYCLOAK_BASE_URL}/realms/${KEYCLOAK_REALM}/protocol/openid-connect/token
KEYCLOAK_JWKS_URL=${KEYCLOAK_BASE_URL}/realms/${KEYCLOAK_REALM}/protocol/openid-connect/certs
```

```python
# tools/dashboard/app.py — OIDC callback (unchanged from before)
# This works identically whether Keycloak is standalone or federated
@app.route("/oidc/callback")
def oidc_callback():
    code = flask_request.args.get("code")
    
    token = oauth_auth.exchange_code(
        code=code,
        token_url=os.environ["KEYCLOAK_TOKEN_URL"],
        client_id=os.environ["KEYCLOAK_CLIENT_ID"],
        client_secret=os.environ["KEYCLOAK_CLIENT_SECRET"],
        redirect_uri=url_for("oidc_callback", _external=True),
    )
    
    claims = oauth_auth.validate_jwt(
        token["access_token"],
        jwks_uri=os.environ["KEYCLOAK_JWKS_URL"],
    )
    
    email = claims.get("email")
    username = claims.get("preferred_username")
    keycloak_roles = claims.get("realm_access", {}).get("roles", [])
    
    # Map Keycloak roles → ICDEV roles
    role_map = {
        "icdev-admin": "admin",
        "icdev-facilitator": "pm",
        "icdev-student": "developer",
    }
    icdev_role = next((role_map[r] for r in keycloak_roles if r in role_map), "developer")
    
    user = get_user_by_email(email)
    if not user:
        user = create_user(email, username, role=icdev_role)
    
    flask_session["user_id"] = user["id"]
    return redirect(url_for("index"))
```

---

## User Provisioning Options (All Internal to Keycloak)

### Option A: Admin Creates Users (Manual)

- Admin logs into Keycloak admin console
- Users → Add User → enter details → set password → assign role
- Communicate credentials to student via secure channel
- Good for: small cohorts (20-80 students), controlled onboarding

### Option B: Self-Registration (Students Sign Up)

- Realm Settings → Login → `User registration: On`
- Students see "Register" link on login page
- They create their own account with username, email, password
- Keycloak sends email verification (requires SMTP config)
- Default role assigned automatically (e.g., `icdev-student`)
- Good for: open enrollment, less admin overhead

### Option C: Admin REST API (Scripted)

```python
import requests

def create_student_in_keycloak(username, email, first_name, last_name, password):
    admin_token = requests.post(
        "http://keycloak.icdev.local:8080/realms/master/protocol/openid-connect/token",
        data={
            "grant_type": "client_credentials",
            "client_id": "admin-cli",
            "client_secret": ADMIN_CLI_SECRET,
        }
    ).json()["access_token"]
    
    resp = requests.post(
        "http://keycloak.icdev.local:8080/admin/realms/icdev/users",
        headers={
            "Authorization": f"Bearer {admin_token}",
            "Content-Type": "application/json",
        },
        json={
            "username": username,
            "email": email,
            "firstName": first_name,
            "lastName": last_name,
            "enabled": True,
            "emailVerified": True,
            "credentials": [{"type": "password", "value": password, "temporary": False}],
            "realmRoles": ["icdev-student"],
        }
    )
    return resp.status_code == 201
```

Good for: bulk importing a student roster from CSV.

---

## What You Give Up by Not Federating

| Feature | Standalone Keycloak | With AD/LDAP Federation |
|---------|---------------------|--------------------------|
| Students use existing Windows passwords | ❌ No | ✅ Yes |
| Seamless SSO on domain machines | ❌ No | ✅ Yes (with Kerberos) |
| IT manages users in one place | ❌ No — separate Keycloak user DB | ✅ Yes — AD is source of truth |
| Automatic deprovisioning on AD disable | ❌ No — manual in Keycloak | ✅ Yes — sync handles it |
| Group/OU sync from AD | ❌ No | ✅ Yes |
| Works offline / air-gapped | ✅ Yes | ✅ Yes (if AD is on-prem) |

**Bottom line:** If your students don't have existing AD accounts and you're not in a managed Windows domain, standalone Keycloak is actually the **simpler and correct choice**.

---

## When to Add Federation Later

You can start with standalone Keycloak today and add LDAP/AD federation later without losing existing users:

1. **Phase 1 (Now):** Deploy standalone Keycloak. Create 80 student accounts via admin UI or self-registration. ICDEV authenticates via Keycloak OIDC.
2. **Phase 2 (Later):** If your org moves to AD or gets an existing IdP, add a User Federation provider in Keycloak. Existing Keycloak users remain; new users can be sourced from AD. You can even migrate Keycloak passwords to AD if needed.

Keycloak is designed for this incremental adoption.

---

## Summary

| Question | Answer |
|----------|--------|
| **Does Keycloak need an external identity provider?** | **No.** Keycloak IS the identity provider. |
| **Does Keycloak need Active Directory?** | **No.** Optional add-on, not a prerequisite. |
| **Where do user accounts live?** | In Keycloak's internal database (PostgreSQL or H2). |
| **Who manages passwords?** | Keycloak — with its own password policies, resets, MFA. |
| **Can students self-register?** | Yes — toggle `User registration: On` in realm settings. |
| **Can I bulk-create users?** | Yes — via admin REST API or CSV import extensions. |
| **Can I add AD later?** | Yes — federation is additive. Existing users are preserved. |
| **Is this simpler than AD federation?** | **Yes.** No service accounts, no LDAP certificates, no DNS records, no keytabs. |

**Recommendation for ICDEV:** Deploy standalone Keycloak. It gives you centralized auth, password management, MFA, and role mapping — with zero dependency on external identity systems. If you ever get AD, you can federate later in an afternoon.
