#!/usr/bin/env python3
"""
add_tenant.py — provision a new hospital/clinic tenant on the Arteq stack.

Idempotent: re-running with the same --slug updates the existing tenant and
admin instead of creating duplicates.

Runs inside the app container (it has asyncpg + DATABASE_URL). Use the wrapper
  scripts/add_tenant.sh --name "City Clinic" --slug city-clinic \
      --admin-user cityclinic --admin-pass 'Secret@123' [options]

Options:
  --name         Display name (required)
  --slug         URL/room slug, lowercase-kebab (required)
  --admin-user   Dashboard login username for this tenant (required)
  --admin-pass   Dashboard login password (required, >= 8 chars)
  --name-ml      Malayalam display name           (default: same as --name)
  --phone        Contact phone                     (default: "")
  --address      Address                           (default: "")
  --agent-name   Voice agent name                  (default: Arya)
  --language     Agent language code               (default: ml-IN)
  --tier         hospital | clinic                 (default: hospital)
  --trial-days   Trial length in days; 0 = activate immediately (default: 14)
  --kb-file      Path (inside container) to a knowledge-base text file (optional)
"""
import argparse, asyncio, asyncpg, os, sys, uuid, json
from datetime import datetime, timedelta, timezone

import bcrypt

DEFAULT_HOURS = json.dumps({
    "mon": ["09:00", "17:00"], "tue": ["09:00", "17:00"], "wed": ["09:00", "17:00"],
    "thu": ["09:00", "17:00"], "fri": ["09:00", "17:00"], "sat": ["09:00", "13:00"],
    "sun": [],
})
VALID_ROLES = ("super_admin", "tenant_admin", "viewer", "doctor")


def parse_args():
    p = argparse.ArgumentParser(description="Provision a new Arteq hospital/clinic tenant.")
    p.add_argument("--name", required=True)
    p.add_argument("--slug", required=True)
    p.add_argument("--admin-user", required=True)
    p.add_argument("--admin-pass", required=True)
    p.add_argument("--name-ml", default=None)
    p.add_argument("--phone", default="")
    p.add_argument("--address", default="")
    p.add_argument("--agent-name", default="Arya")
    p.add_argument("--language", default="ml-IN")
    p.add_argument("--tier", default="hospital", choices=["hospital", "clinic"])
    p.add_argument("--trial-days", type=int, default=14)
    p.add_argument("--kb-file", default=None)
    return p.parse_args()


async def main():
    a = parse_args()
    slug = a.slug.strip().lower()
    username = a.admin_user.strip().lower()
    if len(a.admin_pass) < 8:
        sys.exit("ERROR: --admin-pass must be at least 8 characters")

    kb = ""
    if a.kb_file:
        with open(a.kb_file, "r", encoding="utf-8") as f:
            kb = f.read()

    if a.trial_days > 0:
        sub_status = "trial"
        expires = datetime.now(timezone.utc) + timedelta(days=a.trial_days)
    else:
        sub_status = "active"
        expires = None

    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        # ── 1. Hospital (upsert on slug) ─────────────────────────────────────
        existing = await conn.fetchrow("SELECT id FROM hospitals WHERE slug=$1", slug)
        if existing:
            hid = existing["id"]
            await conn.execute(
                "UPDATE hospitals SET name=$1, name_ml=$2, address=$3, phone=$4, "
                "agent_name=$5, agent_language=$6, tier=$7, active=true, "
                "subscription_status=$8, trial_expires_at=$9, knowledge_base=$10 "
                "WHERE id=$11",
                a.name, a.name_ml or a.name, a.address, a.phone,
                a.agent_name, a.language, a.tier, sub_status, expires, kb, hid,
            )
            print(f"updated hospital {a.name!r} (slug={slug}, id={hid})")
        else:
            hid = str(uuid.uuid4())
            await conn.execute(
                "INSERT INTO hospitals (id, name, name_ml, address, phone, hours, slug, "
                "active, tier, agent_name, agent_language, subscription_status, "
                "trial_expires_at, knowledge_base) "
                "VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7,true,$8,$9,$10,$11,$12,$13)",
                hid, a.name, a.name_ml or a.name, a.address, a.phone, DEFAULT_HOURS,
                slug, a.tier, a.agent_name, a.language, sub_status, expires, kb,
            )
            print(f"created hospital {a.name!r} (slug={slug}, id={hid})")

        # ── 2. Admin user (upsert on username stored in email column) ────────
        pw_hash = bcrypt.hashpw(a.admin_pass.encode()[:72], bcrypt.gensalt(rounds=12)).decode()
        urow = await conn.fetchrow("SELECT id FROM users WHERE email=$1", username)
        if urow:
            uid = urow["id"]
            await conn.execute(
                "UPDATE users SET password_hash=$1, role='tenant_admin', active=true WHERE id=$2",
                pw_hash, uid,
            )
            print(f"updated admin user {username!r}")
        else:
            uid = str(uuid.uuid4())
            await conn.execute(
                "INSERT INTO users (id, email, password_hash, role, active) "
                "VALUES ($1,$2,$3,'tenant_admin',true)",
                uid, username, pw_hash,
            )
            print(f"created admin user {username!r}")

        # ── 3. Link user to tenant ───────────────────────────────────────────
        await conn.execute(
            "INSERT INTO user_tenants (user_id, tenant_slug) VALUES ($1,$2) "
            "ON CONFLICT DO NOTHING",
            uid, slug,
        )
        print(f"linked {username!r} -> {slug}")

        print("\n=== Tenant ready ===")
        print(f"  Hospital : {a.name} ({slug}) — {sub_status}"
              + (f", trial until {expires:%Y-%m-%d}" if expires else ""))
        print(f"  Login    : username={username}  (dashboard at http://187.127.153.87/login)")
        print(f"  Agent    : {a.agent_name} [{a.language}], tier={a.tier}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
