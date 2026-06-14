// Typed API client for the existing FastAPI backend (admin_api.py).
// Calls are proxied through Next rewrites: /admin/api/* -> backend /admin/*.
//
// Auth: the backend JWT is held in the NextAuth session (see lib/auth-options.ts).
// request() attaches it as a Bearer token; the token is cached in-module and kept
// in sync by <AuthTokenSync/> in components/providers.tsx.

import type {
  Hospital, Department, Doctor, Schedule, BillingItem, EmergencyContact,
  Faq, Appointment, AppointmentStatus, Callback, CallLog, Stats, Tenant,
  TelephonyStatus, SetupStatus, HisConfig, AnalyticsPoint, AnalyticsSummary,
  CallFeedback, MissedQuestion, User,
} from "./types";

import { getSession } from "next-auth/react";

const BASE = "/admin/api";

// The backend JWT lives in the NextAuth session. We cache it in-module (kept in
// sync by <AuthTokenSync/> in providers) and fall back to getSession() if a
// request fires before the sync runs.
let _cachedToken: string | null = null;
export function setAuthToken(t: string | null) {
  _cachedToken = t;
}
async function resolveToken(): Promise<string | null> {
  if (_cachedToken) return _cachedToken;
  try {
    const s = await getSession();
    _cachedToken = (s?.accessToken as string | undefined) ?? null;
  } catch {
    _cachedToken = null;
  }
  return _cachedToken;
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(path: string, opts: RequestInit = {}): Promise<T> {
  const token = await resolveToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(opts.headers as Record<string, string> | undefined),
  };
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch(`${BASE}${path}`, { ...opts, headers });
  if (res.status === 401) {
    _cachedToken = null;
    if (typeof window !== "undefined" && !path.includes("/login")) {
      window.location.href = "/login";
    }
    throw new ApiError(401, "Unauthorized");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body?.detail || body?.message || detail;
    } catch { /* ignore */ }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

const get = <T>(p: string) => request<T>(p);
const post = <T>(p: string, body?: unknown) =>
  request<T>(p, { method: "POST", body: body ? JSON.stringify(body) : undefined });
const put = <T>(p: string, body?: unknown) =>
  request<T>(p, { method: "PUT", body: body ? JSON.stringify(body) : undefined });
const del = <T>(p: string) => request<T>(p, { method: "DELETE" });

const qs = (params: Record<string, string | number | undefined>) => {
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v != null && v !== "") u.set(k, String(v));
  const s = u.toString();
  return s ? `?${s}` : "";
};

export const api = {
  // ── Auth ──────────────────────────────────────────────
  login: (password: string) => post<{ access_token: string; token_type: string }>("/login", { password }),

  // ── Hospitals ─────────────────────────────────────────
  listHospitals: () => get<Hospital[]>("/hospitals"),
  getHospital: (id: string) => get<Hospital>(`/hospitals/${id}`),
  createHospital: (b: Partial<Hospital>) => post<Hospital>("/hospitals", b),
  updateHospital: (id: string, b: Partial<Hospital>) => put<Hospital>(`/hospitals/${id}`, b),

  // ── Departments ───────────────────────────────────────
  listDepartments: (hid: string) => get<Department[]>(`/hospitals/${hid}/departments`),
  createDepartment: (hid: string, b: Partial<Department>) => post<Department>(`/hospitals/${hid}/departments`, b),
  updateDepartment: (hid: string, id: string, b: Partial<Department>) => put<Department>(`/hospitals/${hid}/departments/${id}`, b),
  deleteDepartment: (hid: string, id: string) => del<void>(`/hospitals/${hid}/departments/${id}`),

  // ── Doctors + schedules ───────────────────────────────
  listDoctors: (hid: string) => get<Doctor[]>(`/hospitals/${hid}/doctors`),
  createDoctor: (hid: string, b: Partial<Doctor>) => post<Doctor>(`/hospitals/${hid}/doctors`, b),
  updateDoctor: (hid: string, id: string, b: Partial<Doctor>) => put<Doctor>(`/hospitals/${hid}/doctors/${id}`, b),
  deleteDoctor: (hid: string, id: string) => del<void>(`/hospitals/${hid}/doctors/${id}`),
  addSchedule: (doctorId: string, b: Partial<Schedule>) => post<Schedule>(`/doctors/${doctorId}/schedules`, b),
  deleteSchedule: (scheduleId: string) => del<void>(`/schedules/${scheduleId}`),

  // ── Billing ───────────────────────────────────────────
  listBilling: (hid: string) => get<BillingItem[]>(`/hospitals/${hid}/billing`),
  createBilling: (hid: string, b: Partial<BillingItem>) => post<BillingItem>(`/hospitals/${hid}/billing`, b),
  updateBilling: (hid: string, id: string, b: Partial<BillingItem>) => put<BillingItem>(`/hospitals/${hid}/billing/${id}`, b),
  deleteBilling: (hid: string, id: string) => del<void>(`/hospitals/${hid}/billing/${id}`),

  // ── Emergency ─────────────────────────────────────────
  listEmergency: (hid: string) => get<EmergencyContact[]>(`/hospitals/${hid}/emergency`),
  createEmergency: (hid: string, b: Partial<EmergencyContact>) => post<EmergencyContact>(`/hospitals/${hid}/emergency`, b),
  updateEmergency: (hid: string, id: string, b: Partial<EmergencyContact>) => put<EmergencyContact>(`/hospitals/${hid}/emergency/${id}`, b),
  deleteEmergency: (hid: string, id: string) => del<void>(`/hospitals/${hid}/emergency/${id}`),

  // ── FAQs ──────────────────────────────────────────────
  listFaqs: (hid: string) => get<Faq[]>(`/hospitals/${hid}/faqs`),
  createFaq: (hid: string, b: Partial<Faq>) => post<Faq>(`/hospitals/${hid}/faqs`, b),
  updateFaq: (hid: string, id: string, b: Partial<Faq>) => put<Faq>(`/hospitals/${hid}/faqs/${id}`, b),
  deleteFaq: (hid: string, id: string) => del<void>(`/hospitals/${hid}/faqs/${id}`),

  // ── Calls / stats ─────────────────────────────────────
  listCalls: (hid: string, limit = 50) => get<CallLog[]>(`/hospitals/${hid}/calls${qs({ limit })}`),
  getStats: (hid: string) => get<Stats>(`/hospitals/${hid}/stats`),

  // ── Appointments / callbacks ──────────────────────────
  listAppointments: (hid: string, status?: AppointmentStatus, limit = 50) =>
    get<Appointment[]>(`/hospitals/${hid}/appointments${qs({ status, limit })}`),
  updateAppointmentStatus: (hid: string, apptId: string, status: AppointmentStatus) =>
    put<Appointment>(`/hospitals/${hid}/appointments/${apptId}/status`, { status }),
  listCallbacks: (hid: string, status?: string) => get<Callback[]>(`/hospitals/${hid}/callbacks${qs({ status })}`),

  // ── Telephony / setup ─────────────────────────────────
  telephonyStatus: (hid: string) => get<TelephonyStatus>(`/telephony/status${qs({ hospital_id: hid })}`),
  setupStatus: (hid: string) => get<SetupStatus>(`/hospitals/${hid}/setup-status`),
  provisionNumber: (hid: string) => post<{ plivo_number: string; bsnl_forward_code?: string }>(`/hospitals/${hid}/provision-number`),
  runSipSetup: () => post<Record<string, unknown>>("/sip/setup"),
  clearCache: (hid: string) => post<void>(`/hospitals/${hid}/cache/clear`),

  // ── Knowledge base (stored on hospital) ───────────────
  saveKnowledgeBase: (hid: string, knowledge_base: string) => put<Hospital>(`/hospitals/${hid}`, { knowledge_base }),

  // ── HIS integration ───────────────────────────────────
  getHisConfig: (hid: string) => get<HisConfig>(`/hospitals/${hid}/his-config`),
  saveHisConfig: (hid: string, b: HisConfig) => put<HisConfig>(`/hospitals/${hid}/his-config`, b),
  testHisConfig: (hid: string) => post<{ ok: boolean; detail?: string }>(`/hospitals/${hid}/his-config/test`),

  // ── Onboarding wizard ─────────────────────────────────
  wizard: (b: unknown) => post<{ hospital_id: string; slug: string; plivo_number?: string; bsnl_forward_code?: string }>("/hospitals/wizard", b),

  // ── Features / tenants ────────────────────────────────
  featuresCatalog: () => get<Record<string, unknown>>("/features/catalog"),
  listTenants: () => get<Tenant[]>("/tenants"),
  getTenant: (slug: string) => get<Tenant>(`/tenants/${slug}`),
  createTenant: (b: Partial<Tenant>) => post<Tenant>("/tenants", b),
  updateTenant: (slug: string, b: Partial<Tenant>) => put<Tenant>(`/tenants/${slug}`, b),
  updateTenantFeatures: (slug: string, features: Record<string, boolean>) => put<Tenant>(`/tenants/${slug}/features`, features),
  deleteTenant: (slug: string) => del<void>(`/tenants/${slug}`),

  // ── Planned additions (Subagent D backend) ────────────
  analytics: (hid: string, from?: string, to?: string, bucket: "day" | "hour" = "day") =>
    get<AnalyticsPoint[]>(`/hospitals/${hid}/analytics${qs({ from, to, bucket })}`),
  analyticsSummary: (hid: string, from?: string, to?: string) =>
    get<AnalyticsSummary>(`/hospitals/${hid}/analytics/summary${qs({ from, to })}`),
  getCall: (hid: string, callId: string) => get<CallLog>(`/hospitals/${hid}/calls/${callId}`),
  listFeedback: (hid: string, minRating?: number, maxRating?: number) =>
    get<CallFeedback[]>(`/hospitals/${hid}/feedback${qs({ min_rating: minRating, max_rating: maxRating })}`),
  listMissedQuestions: (hid: string, language?: string) =>
    get<MissedQuestion[]>(`/hospitals/${hid}/missed-questions${qs({ language })}`),
  activeCalls: (hid: string) => get<CallLog[]>(`/hospitals/${hid}/active-calls`),

  // ── Users / RBAC (planned) ────────────────────────────
  me: () => get<User>("/auth/me"),
  listUsers: () => get<User[]>("/users"),
  createUser: (b: Partial<User> & { password?: string }) => post<User>("/users", b),
  updateUser: (id: string, b: Partial<User>) => put<User>(`/users/${id}`, b),
  deleteUser: (id: string) => del<void>(`/users/${id}`),
};
