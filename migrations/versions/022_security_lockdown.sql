-- Migration 022: DB security lockdown (from Supabase security advisors).
-- Idempotent -- safe to re-run.
-- Views/functions may not exist in self-hosted deployments; wrapped in DO blocks.

-- 1) Enable RLS on PII / exposed tables that currently have none.
ALTER TABLE public.patients                   ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.bookings                   ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.whatsapp_messages          ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.appointment_events         ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.doctor_availability_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.outbound_call_queue        ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.missed_questions           ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.tenants                    ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.campaigns                  ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.campaign_recipients        ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.announcements              ENABLE ROW LEVEL SECURITY;

-- 2) Drop "allow everything" policies (idempotent -- IF EXISTS).
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

-- 3) Convert SECURITY DEFINER views to security_invoker (only if view exists).
DO $$
DECLARE v TEXT;
BEGIN
  FOREACH v IN ARRAY ARRAY['v_doctor_schedule','v_doctor_today','opd_queue_today',
                            'pending_followups','call_analytics_daily','call_heatmap'] LOOP
    IF EXISTS (SELECT 1 FROM pg_views WHERE schemaname='public' AND viewname=v) THEN
      EXECUTE format('ALTER VIEW public.%I SET (security_invoker = on)', v);
    END IF;
  END LOOP;
END $$;

-- 4) Pin mutable function search_path (only if function exists).
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
             WHERE n.nspname='public' AND p.proname='match_documents') THEN
    EXECUTE 'ALTER FUNCTION public.match_documents SET search_path = public, pg_temp';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
             WHERE n.nspname='public' AND p.proname='touch_updated_at') THEN
    EXECUTE 'ALTER FUNCTION public.touch_updated_at SET search_path = public, pg_temp';
  END IF;
END $$;
