<!-- CUI // SP-CTI -->
# Research: Network Canvas Role-Based Canvas Permissions

**Feature ID:** task-35befb8f8b
**Tier:** 4
**Priority:** Low
**Date:** 2026-03-28
**Status:** Research Complete

---

## Summary

Design for a three-tier RBAC model on the Network Design Canvas, where Viewers see read-only diagrams, Editors modify non-security elements, and Security Architects exclusively control enclave boundaries, firewall rules, and encryption device placement.

---

## Current State

### Authentication
- Session-based login via `nc_login_required` decorator in `tools/network/blueprint.py`
- Three roles defined in `nc_users` table: `viewer`, `editor`, `admin`
- **Gap:** No enforcement — all authenticated users have full canvas write access regardless of role

### Existing Infrastructure (usable)
| Component | Location | Relevance |
|-----------|----------|-----------|
| User roles table | `nc_users.role` in `tools/network/db/init_db.py:317` | Already has viewer/editor/admin |
| Audit trail | `nc_audit` table + `_audit()` fn at `blueprint.py:115` | Immutable, CUI-tagged |
| SaaS RBAC matrix | `tools/saas/auth/rbac.py` | Full permission framework to extend |
| Dashboard RBAC | `tools/dashboard/auth.py:295` | `require_role()` decorator pattern |
| Status gates | `_check_status_gate()` at `blueprint.py:156` | Compliance/severity blocking pattern |

---

## Proposed Three-Tier Role Model

### Roles

| Role | Existing Mapping | Canvas Capabilities |
|------|-----------------|---------------------|
| **Viewer** | `nc_users.role = 'viewer'` | Read-only: pan/zoom, export PNG/PDF, run compliance audit, view annotations |
| **Editor** | `nc_users.role = 'editor'` | All Viewer permissions + add/move/delete non-security nodes, edit labels, configure link bandwidth, run Monte Carlo/What-If |
| **Security Architect** | `nc_users.role = 'admin'` (rename or add new) | All Editor permissions + enclave boundary create/edit/delete, firewall rule placement, encryption device placement, classification label changes |

> **Note:** Rename `admin` → `security_architect` for semantic clarity, or add `security_architect` as a new role between `editor` and `admin`. Keep `admin` for system administration (user management, project deletion).

### Security-Gated Elements

These node/edge types require `security_architect` role to create, modify, or delete:

```
Node types (security-gated):
  - enclave_boundary / security_zone
  - firewall / ngfw / waf / ips / ids
  - crypto_device / type1_encryptor / taclane / haipe
  - dmz_boundary
  - cross_domain_solution

Edge/link types (security-gated):
  - classified_link (SECRET/TS)
  - encrypted_tunnel (IPSec, MACsec, TLS with classification)
  - firewall_rule (ACL entries)

Topology-level attributes (security-gated):
  - classification label (CUI → SECRET → TS)
  - enclave membership assignments
  - boundary policy changes
```

---

## Implementation Plan

### Phase 1: Database Changes

**File:** `tools/network/db/init_db.py`

#### 1a. Add `security_architect` role to `nc_users`

```sql
-- Update CHECK constraint to include new role
role TEXT DEFAULT 'editor' CHECK(role IN ('viewer', 'editor', 'security_architect', 'admin'))
```

#### 1b. Add `nc_canvas_permissions` table for project-level sharing

```sql
CREATE TABLE IF NOT EXISTS nc_canvas_permissions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES nc_projects(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES nc_users(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK(role IN ('viewer', 'editor', 'security_architect')),
    granted_by TEXT REFERENCES nc_users(id),
    granted_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(project_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_nc_canvas_permissions_project ON nc_canvas_permissions(project_id);
CREATE INDEX IF NOT EXISTS idx_nc_canvas_permissions_user ON nc_canvas_permissions(user_id);
```

#### 1c. Add `nc_security_elements` registry (for runtime gating)

```sql
CREATE TABLE IF NOT EXISTS nc_security_elements (
    element_type TEXT PRIMARY KEY,   -- 'firewall', 'enclave_boundary', etc.
    category TEXT NOT NULL,          -- 'node', 'edge', 'attribute'
    min_role TEXT NOT NULL DEFAULT 'security_architect',
    description TEXT
);
```

Seed data:
```sql
INSERT OR IGNORE INTO nc_security_elements VALUES
  ('enclave_boundary',    'node',      'security_architect', 'Enclave/security zone boundary'),
  ('firewall',            'node',      'security_architect', 'Firewall / NGFW'),
  ('ngfw',                'node',      'security_architect', 'Next-gen firewall'),
  ('waf',                 'node',      'security_architect', 'Web application firewall'),
  ('ips',                 'node',      'security_architect', 'Intrusion prevention system'),
  ('ids',                 'node',      'security_architect', 'Intrusion detection system'),
  ('crypto_device',       'node',      'security_architect', 'Cryptographic device'),
  ('type1_encryptor',     'node',      'security_architect', 'NSA Type 1 encryptor'),
  ('taclane',             'node',      'security_architect', 'TACLANE encryption device'),
  ('haipe',               'node',      'security_architect', 'HAIPE encryptor'),
  ('dmz_boundary',        'node',      'security_architect', 'DMZ boundary'),
  ('cross_domain_solution','node',     'security_architect', 'Cross-domain solution'),
  ('classified_link',     'edge',      'security_architect', 'Classified network link'),
  ('encrypted_tunnel',    'edge',      'security_architect', 'Encrypted tunnel (IPSec/MACsec)'),
  ('firewall_rule',       'edge',      'security_architect', 'Firewall ACL rule'),
  ('classification_label','attribute', 'security_architect', 'Topology classification label'),
  ('enclave_membership',  'attribute', 'security_architect', 'Node enclave assignment');
```

---

### Phase 2: Backend RBAC Enforcement

**File:** `tools/network/blueprint.py`

#### 2a. Role hierarchy helper

```python
ROLE_HIERARCHY = {
    'viewer':              0,
    'editor':              1,
    'security_architect':  2,
    'admin':               3,
}

def _has_role(required_role: str, user_role: str) -> bool:
    """Return True if user_role meets or exceeds required_role."""
    return ROLE_HIERARCHY.get(user_role, -1) >= ROLE_HIERARCHY.get(required_role, 999)
```

#### 2b. Enhanced `nc_login_required` decorator

```python
def nc_require_role(min_role: str):
    """Decorator factory: enforces minimum role on a canvas route."""
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            user_id = session.get("user_id")
            if not user_id:
                return jsonify({"error": "Unauthorized"}), 401
            user = _get_user(user_id)  # fetch from nc_users
            if not user or not _has_role(min_role, user["role"]):
                _audit("permission_denied", "route", request.path, {
                    "required": min_role, "actual": user.get("role") if user else None
                }, user_id)
                return jsonify({"error": "Forbidden", "required_role": min_role}), 403
            return f(*args, **kwargs)
        return wrapped
    return decorator
```

#### 2c. Security element gate on canvas mutation endpoints

Apply to: `POST /api/topology`, `PUT /api/node`, `DELETE /api/node`, `POST /api/link`, etc.

```python
SECURITY_ELEMENT_TYPES = {
    'enclave_boundary', 'firewall', 'ngfw', 'waf', 'ips', 'ids',
    'crypto_device', 'type1_encryptor', 'taclane', 'haipe',
    'dmz_boundary', 'cross_domain_solution', 'classified_link',
    'encrypted_tunnel', 'firewall_rule',
}

def _check_security_element_permission(element_type: str, user_role: str) -> bool:
    """Returns True if user may modify this element type."""
    if element_type in SECURITY_ELEMENT_TYPES:
        return _has_role('security_architect', user_role)
    return _has_role('editor', user_role)
```

#### 2d. Route-level role decoration

```python
# Viewer routes — read-only
@bp.route('/canvas/<topo_id>')
@nc_login_required       # any authenticated user
def canvas_view(topo_id): ...

# Editor routes — non-security mutations
@bp.route('/api/node', methods=['POST'])
@nc_require_role('editor')
def add_node():
    data = request.json
    element_type = data.get('type', '')
    user = _get_current_user()
    if not _check_security_element_permission(element_type, user['role']):
        return jsonify({"error": "Security elements require Security Architect role"}), 403
    ...

# Security Architect routes — security mutations
@bp.route('/api/enclave', methods=['POST', 'PUT', 'DELETE'])
@nc_require_role('security_architect')
def manage_enclave(): ...

@bp.route('/api/firewall-rule', methods=['POST', 'PUT', 'DELETE'])
@nc_require_role('security_architect')
def manage_firewall_rule(): ...
```

---

### Phase 3: Frontend Enforcement

**File:** `tools/dashboard/templates/network/canvas.html`

The server must inject the current user's role into the page for client-side UI gating (not as a security measure — server is authoritative — but for UX):

```python
# In the canvas route handler:
return render_template('network/canvas.html',
    topo_id=topo_id,
    user_role=session.get('user_role', 'viewer'),
    security_element_types=list(SECURITY_ELEMENT_TYPES),
    ...
)
```

#### Client-side gating (JavaScript)

```javascript
const USER_ROLE = "{{ user_role | tojson }}";
const SECURITY_ELEMENTS = {{ security_element_types | tojson }};
const ROLE_HIERARCHY = { viewer: 0, editor: 1, security_architect: 2, admin: 3 };

function hasRole(required) {
    return (ROLE_HIERARCHY[USER_ROLE] || 0) >= (ROLE_HIERARCHY[required] || 999);
}

// Disable toolbar buttons for insufficient role
function applyRoleGating() {
    if (!hasRole('editor')) {
        document.querySelectorAll('.canvas-toolbar .tool-add, .tool-delete').forEach(el => {
            el.disabled = true;
            el.title = 'Viewer: read-only access';
        });
    }
    if (!hasRole('security_architect')) {
        document.querySelectorAll('[data-security-gated]').forEach(el => {
            el.disabled = true;
            el.classList.add('role-locked');
            el.title = 'Requires Security Architect role';
        });
    }
}

// On drag-drop: validate before sending to server
graph.on('add', function(cell) {
    const elType = cell.get('type') || '';
    if (SECURITY_ELEMENTS.includes(elType) && !hasRole('security_architect')) {
        cell.remove();
        showToast('Security elements require Security Architect role', 'error');
    }
});
```

#### Visual indicators

```css
/* Canvas toolbar — role-locked elements */
.role-locked {
    opacity: 0.4;
    cursor: not-allowed;
    position: relative;
}
.role-locked::after {
    content: '🔒';
    position: absolute;
    top: 2px;
    right: 2px;
    font-size: 10px;
}

/* Viewer mode — entire canvas read-only overlay hint */
.viewer-mode .joint-paper {
    cursor: default !important;
}
```

---

### Phase 4: Project-Level Permission Sharing

For multi-user workflows, project owners should be able to grant specific users access at a given role level:

**API endpoints:**

```
POST   /network/api/projects/<project_id>/permissions
       Body: { "user_id": "...", "role": "editor" }
       Auth: project owner or admin

GET    /network/api/projects/<project_id>/permissions
       Returns list of user-role grants

DELETE /network/api/projects/<project_id>/permissions/<user_id>
       Revokes access
```

**Effective role resolution:**

```python
def get_effective_role(user_id: str, project_id: str) -> str:
    """
    Returns the highest role applicable to this user for this project.
    System role (nc_users.role) sets the ceiling; project grant can lower it.
    """
    system_role = _get_user_role(user_id)           # from nc_users
    project_grant = _get_project_grant(user_id, project_id)  # from nc_canvas_permissions
    if project_grant is None:
        return system_role  # default: system role applies globally
    # Take the lower of the two (least privilege)
    if ROLE_HIERARCHY.get(project_grant, 0) < ROLE_HIERARCHY.get(system_role, 0):
        return project_grant
    return system_role
```

---

## Audit Trail Integration

All RBAC decisions — including denials — must be logged to `nc_audit`:

```python
# Permission denied
_audit("rbac_denied", "route", request.path, {
    "required_role": min_role,
    "user_role": user_role,
    "element_type": element_type,
    "user_agent": request.user_agent.string,
}, user_id)

# Security element modification (approved)
_audit("security_element_modified", element_type, element_id, {
    "action": "add" | "edit" | "delete",
    "user_role": "security_architect",
    "topo_id": topo_id,
}, user_id)
```

This satisfies **NIST 800-53 AC-3** (Access Enforcement), **AC-6** (Least Privilege), and **AU-12** (Audit Generation).

---

## NIST 800-53 Control Coverage

| Control | Description | How This Addresses It |
|---------|-------------|----------------------|
| AC-2 | Account Management | Role assigned at user creation; reviewable |
| AC-3 | Access Enforcement | Server-side role checks on every mutation endpoint |
| AC-5 | Separation of Duties | Security Architects isolated from general Editors |
| AC-6 | Least Privilege | Viewers cannot write; Editors cannot touch security elements |
| AC-17 | Remote Access | Session-gated; roles enforced regardless of access path |
| AU-2 | Audit Events | RBAC denials logged to immutable nc_audit |
| AU-12 | Audit Generation | Every security element change timestamped with user + role |
| CM-5 | Access Restrictions for Change | Security topology changes limited to Security Architect role |

---

## Files to Modify

| File | Change |
|------|--------|
| `tools/network/db/init_db.py` | Add `security_architect` role, `nc_canvas_permissions` table, `nc_security_elements` table + seed data |
| `tools/network/blueprint.py` | Add `ROLE_HIERARCHY`, `nc_require_role()`, `_check_security_element_permission()`, apply decorators to all canvas mutation routes |
| `tools/dashboard/templates/network/canvas.html` | Inject `user_role` + `security_element_types`, add JS gating + CSS visual indicators |
| `tools/network/db/migrations/` | Create migration script for role addition + new tables |

---

## Out of Scope (Tier 4 — Low Priority)

- Multi-tenant role federation with `tools/saas/auth/rbac.py` (requires SaaS integration work)
- Real-time collaborative editing with per-cursor role display
- Role-based export restrictions (classified exports)
- Workflow approval gates for security element changes (separate CAB feature)

---

## Dependencies

- Existing: `nc_users`, `nc_audit`, `_audit()` function, `nc_login_required`
- No new Python packages required
- JointJS (already in canvas.html) for element type detection on client side
