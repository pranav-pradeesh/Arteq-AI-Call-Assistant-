# PII Handling & Security Controls

> A factual map of how patient personal data flows through Arteq and the controls that
> protect it. Each control is tagged **[Implemented]** (enforced in code today),
> **[Configure]** (set at deployment), or **[Roadmap]** (committed hardening). Written
> to be defensible in a hospital security review — it does not overclaim.

---

## 1. Data flow & where PII lives

```
Caller audio ──> LiveKit ──> Sarvam STT ──> transcript (text)
                                   │
                          Gemini LLM (turn context)
                                   │
   ┌───────────────────────────────┴───────────────────────────────┐
   ▼                         ▼                          ▼
appointments (Postgres)  call_logs (Postgres)     Vobiz recording store
 name, phone, slot       caller phone, full        audio file; URL saved
                         transcript, intents,      in call_logs.recording_url
                         outcome, emotion
```

**Personal data at rest:** `appointments` (name, phone, appointment), `call_logs`
(caller phone, full transcript, derived emotional state, recording URL), optional
audio recordings at Vobiz.

**Personal data in transit:** caller audio (LiveKit/SIP), transcript to LLM providers,
WhatsApp message payloads, dashboard API traffic.

---

## 2. Controls by layer

### Identity & access
- **Password hashing:** bcrypt, cost factor 12 (`additions/routes/users_api.py`,
  startup superadmin upsert in `src/main.py`). **[Implemented]**
- **RBAC:** three roles — `super_admin`, `tenant_admin`, `viewer`. **[Implemented]**
- **Tenant isolation:** hospital-scoped routes verify the user is assigned to the
  hospital via `user_tenants` before returning data; cross-tenant access returns 403
  (`admin_api.py::_require_auth`, `_assert_hospital_access`). **[Implemented]**
- **JWT:** HS256, expiry-bounded; validated centrally (`_decode_token`). **[Implemented]**
- **Production secret enforcement:** startup aborts if `DASHBOARD_JWT_SECRET` /
  `DASHBOARD_ADMIN_PASSWORD` are default/weak (`settings.py`). **[Implemented]**

### Application abuse protection
- **Login brute-force guard:** 5 failed attempts / 10-minute window per IP → 429. **[Implemented]**
- **Token-endpoint guard:** per-IP sliding window so the unauthenticated LiveKit token
  endpoint can't be abused to spin up billable rooms (`main.py::_token_rate_ok`). **[Implemented]**
- **Nginx rate limiting** on auth endpoints in the self-host proxy. **[Configure]**

### Network & transport
- **TLS** termination at Nginx (self-host) or platform-managed TLS (Render). **[Configure]**
- **Firewall:** open only documented ports (README "Firewall ports"). **[Configure]**
- **CORS:** restrict `CORS_ORIGINS` to real dashboard origins in production; a wildcard
  in production emits a startup warning. **[Configure]**

### Secret management
- **No secrets in source;** all via `.env` / platform secrets. **[Implemented]**
- **GitGuardian** secret scanning runs in CI (`.gitguardian.yaml`). **[Implemented]**

### Data at rest
- **DB encryption at rest:** provided by managed Postgres (Supabase/cloud) by default,
  or enable VPS disk encryption for self-host. **[Configure]**
- **Recordings:** stored at Vobiz; access restricted via Vobiz console + hospital-scoped
  dashboard API; disabled by default (`VOBIZ_RECORD_CALLS=false`). **[Configure]**

### Logging & observability
- **Structured JSON logging** (structlog) with no transcript bodies written to logs —
  transcripts persist to the DB via `write_call_log`, not to operational logs. **[Implemented]**
- **Prometheus `/metrics`** exposes counters/latency, **not** PII. **[Implemented]**

---

## 3. Known gaps & committed hardening (honest list)

| Gap | Risk | Plan |
|---|---|---|
| No automated PII redaction filter on log events (a phone number could appear in an ad-hoc log line) | Low–medium | **[Roadmap]** Add a structlog processor that masks phone numbers / names before emission |
| `call_logs.transcript` and `caller` stored in plaintext within the DB | Medium (mitigated by DB-at-rest encryption + RBAC) | **[Roadmap]** Optional application-level field encryption |
| Retention purge is a manual procedure | Medium | **[Roadmap]** Scheduled purge job enforcing `DPDP_COMPLIANCE.md` §6 windows |
| Per-call recording-consent flag not yet persisted as a column | Low | **[Roadmap]** Add `consent_recording` to call record |

These are documented deliberately: a credible security posture names its gaps and the
plan to close them.

---

## 4. Recommended production security checklist

- [ ] `ENV=production`, strong `DASHBOARD_JWT_SECRET` (32-byte hex) and admin password ≥ 12 chars
- [ ] `CORS_ORIGINS` restricted to real origins
- [ ] TLS enabled; HSTS on; firewall limited to required ports
- [ ] Managed/encrypted Postgres in an **Indian region**
- [ ] `INTERNAL_API_KEY` set and rotated quarterly
- [ ] Recordings enabled **only** with consent; access audited
- [ ] Dashboard users provisioned with least-privilege roles
- [ ] Backups configured and restore tested (see `../INCIDENT_RESPONSE.md`)
- [ ] Quarterly access review of dashboard users

---

## 5. Penetration testing & review cadence
- Run a security review on the diff before each release (`/security-review`).
- Recommend an **annual third-party penetration test** once a hospital is live.
- Dependency vulnerabilities monitored; patch critical CVEs within 30 days.
