# Onboarding a new hospital / clinic (same VPS)

The stack is multi-tenant: one set of containers serves every hospital. A call's
room is named `{slug}-call-{uuid}`, and the agent resolves the slug → hospital row,
so each tenant gets its own doctors, departments, FAQs, hours, greeting, language,
plan and recordings — fully isolated by `slug`.

## 1. What you need per hospital
- A **name** and a unique **slug** (lowercase-kebab, e.g. `city-clinic`).
- A dashboard **admin username + password**.
- (For inbound calls) a **Vobiz DID** in +E.164 (e.g. `+9179XXXXXXXX`), with Vobiz
  configured to route that DID to this VPS SIP (UDP 5060). The inbound trunk already
  allows Vobiz's IP ranges, and accepts the DID with or without the country code.

## 2. Provision (one command, on the VPS, from /root/arteq)
```bash
./scripts/add_tenant.sh \
  --name "City Clinic" --slug city-clinic \
  --admin-user cityclinic --admin-pass 'Strong@Pass1' \
  --did +917900000000 \
  --plan full \
  --language ml-IN --tier hospital \
  --phone +914870000000 --address "MG Road, Thrissur"
```
Idempotent on `--slug` (re-run to update). Options: `--name-ml`, `--agent-name`,
`--trial-days N` (0 = active now), `--kb-file /path/to/handbook.txt`.

**`--plan full`** = inbound AI answering + outbound + dashboard (use this for a live
hospital). **`--plan trial`** = outbound reminders + dashboard only (no inbound AI).

It creates: the hospital row, the dashboard admin login (scoped to this tenant),
and — if `--did` is given — the LiveKit inbound SIP trunk + dispatch rule so calls
to that number reach the agent.

## 3. After provisioning — admin configures via the dashboard
Log in at `http://<vps>/login` with the admin user, then set up:
- **Departments** (name, floor, ext, timings)
- **Doctors** → **Schedules** (consulting days/times — this drives slot availability)
- **FAQs** (answered by the agent), **Knowledge** (free-text handbook)
- **Holidays** (closed dates / special hours)
- **Settings** (greeting, agent language, hours, staff-alert phone)
- **Emergency** contacts

No code or DB work needed — everything the agent uses is dashboard-managed.

## 4. Deploying code updates
```bash
./scripts/update.sh        # git pull + rebuild app/agent/frontend + restart
```
Always rebuild (not just restart) the **agent** after agent-side changes — the
running agent uses the built image, not the source on disk.

## 5. Notes
- Recordings are per-tenant and visible only to that hospital's admin (not super admin).
- The super admin (Hospitals page) can toggle each tenant's plan (trial/full) and tier.
- One DID per hospital; the slug in the room name is the routing key.
