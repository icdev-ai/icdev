# CUI // SP-CTI
# FORGE Academy — Phase 5: Credential + Multimodal

**Date:** 2026-05-09  
**Roadmap phase:** Phase 5 of ICDEV™ AI Upskilling & Innovation Platform  
**Status:** COMPLETE

---

## What Was Built

### 1. FORGE AI Certification System

Three-tier credential framework tied to real learning gates.

**Database** (`apps/forge_academy/db.py`):
- `check_cert_eligibility(user_id, cert_key)` — evaluates all gates, returns `{eligible, gates[]}`
- `issue_certificate(user_id, cert_key)` — idempotent; awards XP bonus on first issue; token = `secrets.token_urlsafe(32)`
- `get_user_certificates(user_id)` — all certs for a user
- `verify_certificate_token(token)` — JOIN with fa_users; public verification endpoint

**Routes** (`apps/forge_academy/blueprint.py`):

| Route | Purpose |
|-------|---------|
| `GET /academy/certificate/<level>` | Cert page: gates checklist + claim button or display earned cert |
| `POST /api/academy/certificate/<level>/issue` | API: issue cert if eligible |
| `GET /academy/verify/<token>` | Public verification — no login required |
| `GET /academy/my-certificates` | Dashboard showing all 3 tiers + eligibility status |

**Cert Tiers** (from `CERT_TIERS` in `constants.py`):

| Tier | Key | Gates | XP Bonus |
|------|-----|-------|----------|
| Foundation | `foundation` | Tier 1 complete + full role Tier 2 + assessment ≥70 | +1,000 |
| Practitioner | `practitioner` | Foundation + AADC score ≥80 + 1 GameDay scenario | +2,500 |
| Expert | `expert` | Practitioner + Tier 3 complete + GameDay top-50% | +5,000 |

**Templates** (3 new, mirrored to `icdev/`):
- `forge_academy/certificate.html` — CUI-marked cert display with copy-token, issue button, gates status
- `forge_academy/cert_verify.html` — Public verification page (valid / invalid)
- `forge_academy/my_certificates.html` — All-tiers dashboard grid

---

### 2. Adaptive Learning Path

**API:** `GET /api/academy/learning-path?limit=5`

Returns up to N mission recommendations for the logged-in user, prioritized:
1. In-progress missions first
2. Then by tier (Tier 1 → Tier 2) ascending
3. Then alphabetically by title

Filters by user's current role so irrelevant missions don't clutter the list.

---

### 3. M11 Multimodal AI Mission (Tier 1)

**Slug:** `m-t1-11-multimodal`  
**Tier:** 1 | **Topic:** ai_foundations | **Role:** all | **XP:** 350  
**Prereq:** m10-tier1-capstone

**3 steps:**
1. **Watch** — Multimodal AI concepts: vision models, document understanding, supported formats, Gov/DoD use cases
2. **Coding** — Build `DocumentClassifier` with base64 image input, JSON confidence response, threshold gating, graceful error handling
3. **Reflect** — Wire classifier as a RAG pipeline pre-filter; reason about latency trade-offs and caching strategies

**Content files:** `apps/forge_academy/content/tier1/m11-multimodal/step-{1,2,3}.md`

---

## Verification

```bash
# Import smoke test
python -c "
from apps.forge_academy.blueprint import certificate_page, api_issue_certificate, verify_cert, my_certificates, api_learning_path
from apps.forge_academy.db import check_cert_eligibility, issue_certificate, get_user_certificates, verify_certificate_token
from apps.forge_academy.content_loader import BUILTIN_MISSIONS, BUILTIN_STEPS
m11 = next(m for m in BUILTIN_MISSIONS if m['slug'] == 'm-t1-11-multimodal')
assert m11 and 'm-t1-11-multimodal' in BUILTIN_STEPS
print('Phase 5 smoke test PASSED')
"
```

## Total Missions After Phase 5

65 missions across Tier 1–2 (up from 64 after adding M11).

---

## Files Changed

| File | Change |
|------|--------|
| `apps/forge_academy/db.py` | +4 cert functions appended |
| `apps/forge_academy/blueprint.py` | +5 cert/learning-path routes; updated imports |
| `apps/forge_academy/content_loader.py` | +M11 mission in BUILTIN_MISSIONS + BUILTIN_STEPS |
| `apps/forge_academy/content/tier1/m11-multimodal/step-{1,2,3}.md` | New content files |
| `tools/dashboard/templates/forge_academy/certificate.html` | New |
| `tools/dashboard/templates/forge_academy/cert_verify.html` | New |
| `tools/dashboard/templates/forge_academy/my_certificates.html` | New |
| `icdev/tools/dashboard/templates/forge_academy/{certificate,cert_verify,my_certificates}.html` | Mirrored |
| `.claude/commands/start.md` | Added 4 new routes to Pages list |
