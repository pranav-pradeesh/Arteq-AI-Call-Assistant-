"use client";
import * as React from "react";
import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { SessionProvider, useSession } from "next-auth/react";
import { setAuthToken, api } from "@/lib/api";
import type { TrialStatus } from "@/lib/types";

// Keeps the in-module API token in sync with the NextAuth session.
function AuthTokenSync() {
  const { data: session } = useSession();
  React.useEffect(() => {
    setAuthToken(session?.accessToken ?? null);
  }, [session?.accessToken]);
  return null;
}

// ── Toast / notice ──────────────────────────────────────────────
type Notice = { id: number; msg: string; tone: "ok" | "err" };
const ToastCtx = React.createContext<(msg: string, tone?: "ok" | "err") => void>(() => {});
export const useToast = () => React.useContext(ToastCtx);

// ── Current hospital (selected tenant context) ──────────────────
type HospitalState = {
  hospitalId: string | null;
  setHospitalId: (id: string | null) => void;
};
const HospitalCtx = React.createContext<HospitalState>({ hospitalId: null, setHospitalId: () => {} });
export const useCurrentHospital = () => React.useContext(HospitalCtx);

// ── Trial / read-only ───────────────────────────────────────────
// readOnly is true when the selected hospital's trial has expired AND the user
// is not a super_admin (Arteq staff keep full access to activate/manage).
type ReadOnlyState = { readOnly: boolean; trial: TrialStatus | null };
const ReadOnlyCtx = React.createContext<ReadOnlyState>({ readOnly: false, trial: null });
export const useReadOnly = () => React.useContext(ReadOnlyCtx).readOnly;
export const useTrialStatus = () => React.useContext(ReadOnlyCtx).trial;

function ReadOnlyProvider({ children }: { children: React.ReactNode }) {
  const { hospitalId } = useCurrentHospital();
  const { data: session } = useSession();
  const role = (session?.user as { role?: string } | undefined)?.role;
  const { data: trial = null } = useQuery({
    queryKey: ["trial-status", hospitalId],
    queryFn: () => api.trialStatus(hospitalId as string),
    enabled: !!hospitalId,
    retry: false,
  });
  const readOnly =
    !!trial && trial.subscription_status === "expired" && role !== "super_admin";
  return <ReadOnlyCtx.Provider value={{ readOnly, trial }}>{children}</ReadOnlyCtx.Provider>;
}

export function Providers({ children }: { children: React.ReactNode }) {
  const [client] = React.useState(
    () => new QueryClient({ defaultOptions: { queries: { staleTime: 30_000, retry: 1 } } })
  );

  const [notices, setNotices] = React.useState<Notice[]>([]);
  const notify = React.useCallback((msg: string, tone: "ok" | "err" = "ok") => {
    const id = Date.now() + Math.random();
    setNotices((n) => [...n, { id, msg, tone }]);
    setTimeout(() => setNotices((n) => n.filter((x) => x.id !== id)), 3500);
  }, []);

  const [hospitalId, setHospitalIdState] = React.useState<string | null>(null);
  React.useEffect(() => {
    setHospitalIdState(window.localStorage.getItem("arteq_hospital"));
  }, []);
  const setHospitalId = React.useCallback((id: string | null) => {
    setHospitalIdState(id);
    if (id) window.localStorage.setItem("arteq_hospital", id);
    else window.localStorage.removeItem("arteq_hospital");
  }, []);

  return (
    <SessionProvider>
    <QueryClientProvider client={client}>
      <AuthTokenSync />
      <ToastCtx.Provider value={notify}>
        <HospitalCtx.Provider value={{ hospitalId, setHospitalId }}>
          <ReadOnlyProvider>{children}</ReadOnlyProvider>
          <div className="fixed bottom-4 right-4 z-[100] space-y-2">
            {notices.map((n) => (
              <div
                key={n.id}
                className={`rounded-lg px-4 py-2.5 text-sm text-white shadow-lg ${
                  n.tone === "err" ? "bg-red-600" : "bg-gray-900"
                }`}
              >
                {n.msg}
              </div>
            ))}
          </div>
        </HospitalCtx.Provider>
      </ToastCtx.Provider>
    </QueryClientProvider>
    </SessionProvider>
  );
}
