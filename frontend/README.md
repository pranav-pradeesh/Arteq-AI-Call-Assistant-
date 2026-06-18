# Arteq Admin Dashboard (Next.js rebuild)

Production rebuild of the Arteq Hospital Voice Agent admin dashboard, replacing the single-file Alpine.js SPA. See `../DASHBOARD_PLAN.md` for the full plan.

## Stack
Next.js 14 (App Router) · TypeScript · Tailwind · TanStack Query/Table · Recharts · React Hook Form + Zod · lucide-react.

## Run
```bash
cp .env.local.example .env.local      # set NEXT_PUBLIC_API_BASE to your FastAPI backend
npm install
npm run dev                            # http://localhost:3000
```
The Next dev server proxies `/admin/api/*` → backend `/admin/*` and `/api/v1/*` → backend `/api/v1/*` (see `next.config.mjs`), so the browser uses one origin.

## Architecture
- `src/lib/api.ts` — typed client for all backend endpoints (existing + planned).
- `src/lib/types.ts` — domain types mirroring the DB schema.
- `src/components/` — UI primitives (`ui.tsx`), `data-table.tsx`, `modal.tsx`, `app-shell.tsx`, `providers.tsx` (React Query + toast + current-hospital context), `require-hospital.tsx`.
- `src/app/(app)/<route>/page.tsx` — one folder per dashboard page.

## Auth (NextAuth + RBAC)
Login uses **NextAuth** (Credentials provider) against the backend RBAC endpoint `POST /admin/auth/login` (email + password); the role comes from `GET /admin/auth/me`. The backend JWT is carried in the NextAuth session and sent as a Bearer token by `src/lib/api.ts`. Route protection is enforced by `src/middleware.ts` (unauthenticated → `/login`), and `/users` + `/tenants` are gated to `super_admin`. The Admin nav section is hidden for other roles.

Requires the backend additions (`../backend-additions/`): run migrations `006_users_rbac.sql` + `006b_seed_superadmin.sql`, then log in with the seeded super-admin. Set `NEXTAUTH_SECRET` (`openssl rand -hex 32`) and `NEXTAUTH_URL` in `.env.local`.

## Status
Foundation + page set scaffolded. Pages depending on planned endpoints (analytics, call QA, live, users) are wired to `src/lib/api.ts` and will light up when the backend additions (`backend-additions/`) ship.
