# CUI // SP-CTI
# Endpoint Security — Secure API Generation Prompt (D-EPSEC-8)

You are generating API endpoint code. Every endpoint MUST include the security
safeguards listed below. Omitting any of these produces code that WILL fail the
`endpoint_security_scanner` gate and block deployment.

---

## 1. Authentication (NIST AC-3 — Access Enforcement)

Every non-exempt route MUST have an auth decorator or middleware check.

| Language   | Pattern                                      |
|------------|----------------------------------------------|
| Python     | `@require_role("admin", "pm", "developer")`  |
| Java       | `@PreAuthorize("hasRole('ADMIN')")`           |
| Go         | `authMiddleware(handler)`                     |
| TypeScript | `router.get("/path", authMiddleware, handler)`|
| Rust       | `#[authorize]`                                |
| C#         | `[Authorize(Roles = "Admin")]`                |

Exempt routes (no auth required): `/health`, `/ready`, `/metrics`, `/ping`,
`/favicon`, `/static`, `/login`.

## 2. Input Validation (NIST SI-10 — Information Input Validation)

All POST/PUT/PATCH endpoints MUST validate input before processing.

| Language   | Pattern                                              |
|------------|------------------------------------------------------|
| Python     | `_validate_fields(data, required=["name", "value"])` |
| Java       | `@Valid @RequestBody CreateDto dto`                   |
| Go         | `validate.Struct(req)`                                |
| TypeScript | `const schema = zod.object({...}); schema.parse(body)` |
| Rust       | `#[validate] struct CreateReq { ... }`                |
| C#         | `if (!ModelState.IsValid) return BadRequest()`        |

Return `400 Bad Request` with a descriptive error for invalid input.

## 3. IDOR Protection (NIST AC-4 — Information Flow Enforcement)

Resource-specific endpoints (e.g., `/users/<id>`, `/contracts/<id>`) MUST
verify the authenticated user is authorized to access the specific resource.

**Anti-pattern (NEVER generate this):**
```python
# BAD: user_id from query string — attacker controls it
user_id = request.args.get("user_id")
```

**Correct pattern:**
```python
# GOOD: user identity from authenticated session
user_email = g.current_user.get("email", "")
if resource.owner != user_email:
    return jsonify({"error": "Access denied"}), 403
```

## 4. Error Responses

| Status | When                                     |
|--------|------------------------------------------|
| 400    | Invalid input, missing required fields   |
| 401    | No credentials or expired token          |
| 403    | Authenticated but not authorized         |
| 404    | Resource not found                       |
| 500    | Internal error (never expose stack trace)|

## 5. Anti-Patterns — NEVER Generate

- `request.args.get("user_id")` for identity — always use `g.current_user`
- Routes without auth decorators
- POST/PUT without input validation
- `eval()`, `exec()`, `os.system()` with user input
- SQL string concatenation — use parameterized queries
- `innerHTML` / `document.write()` with user data — use `textContent`
- Returning full stack traces to the client
