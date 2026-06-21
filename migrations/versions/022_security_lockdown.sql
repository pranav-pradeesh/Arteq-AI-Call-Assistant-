-- Migration 022: DB security lockdown (from Supabase security advisors).
-- Idempotent — safe to re-run.
--
-- WHY THIS IS SAFE FOR THE APP:
--   The FastAPI backend (and n8n) connect with the SERVICE-ROLE key, which
--   BYPASSES RLS. Enabling RLS with no permissive policy closes the public
--   PostgREST/REST API (anon + authenticated) for these tables but does NOT
--   affect the backend, the dashboard, or n8n. This shuts the public PII
--   exposure that the Supabase advisors flagged (patients, bookings.patient_id,
--   whatsapp_messages, appointment_events, etc.).
--
-- After running, the advisors drop from ERROR/WARN to INFO ("RLS enabled, no
-- policy") on these tables — which is the intended end state.

-- 1) Enable RLS on PII / exposed tables that currently have none.
ALTER TABLE public.patients                   ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.bookings                   ENABLE ROW LEVEL SECURITY;  -- exposes patient_id (PII)
ALTER TABLE public.whatsapp_messages          ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.appointment_events         ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.doctor_availability_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.outbound_call_queue        ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.missed_questions           ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.tenants                    ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.campaigns                  ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.campaign_recipients        ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.announcements              ENABLE ROW LEVEL SECURITY;

-- 2) Drop the "allow everything" (USING(true)) policies so RLS actually protects
--    these tables (the policy made RLS a no-op for the authenticated role).
DROP POLICY IF EXISTS p_authed_all     ON public.appointments;
DROP POLICY IF EXISTS p_authed_all     ON public.billing_info;
DROP POLICY IF EXISTS p_authed_all     ON public.call_logs;
DROP POLICY IF EXISTS p_authed_all     ON public.departments;
DROP POLICY IF EXISTS p_authed_all     ON public.doctors;
DROP POLICY IF EXISTS p_authed_all_emb ON public.embeddings;
DROP POLICY IF EXISTS p_authed_all     ON public.emergency_contacts;
DROP POLICY IF EXISTS p_authed_all     ON public.events;
DROP POLICY IF EXISTS p_authed_all     ON public.hospitals;
DROP POLICY IF EXISTS p_authed_all     ON public.insurance_providers;
DROP POLICY IF EXISTS p_authed_all     ON public.lab_services;
DROP POLICY IF EXISTS p_authed_all     ON public.schedules;
DROP POLICY IF EXISTS p_authed_all_tts ON public.tts_cache;

-- 3) Convert SECURITY DEFINER views to run with the querying user's privileges.
ALTER VIEW public.v_doctor_schedule    SET (security_invoker = on);
ALTER VIEW public.v_doctor_today       SET (security_invoker = on);
ALTER VIEW public.opd_queue_today      SET (security_invoker = on);
ALTER VIEW public.pending_followups    SET (security_invoker = on);
ALTER VIEW public.call_analytics_daily SET (security_invoker = on);
ALTER VIEW public.call_heatmap         SET (security_invoker = on);

-- 4) Pin mutable function search_path (low severity, safe).
ALTER FUNCTION public.match_documents  SET search_path = public, pg_temp;
ALTER FUNCTION public.touch_updated_at SET search_path = public, pg_temp;

-- NOTE: the `vector` extension-in-public advisory is intentionally left as-is;
-- moving it can break existing references. Address separately if desired.
