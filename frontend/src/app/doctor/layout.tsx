"use client";
import * as React from "react";
import { useRouter } from "next/navigation";
import { signOut, useSession } from "next-auth/react";
import { LogOut, Stethoscope } from "lucide-react";
import { AppLoader } from "@/components/brand";

/**
 * Doctor portal shell. A logged-in doctor (role="doctor") gets a deliberately
 * minimal layout — no hospital switcher, no admin nav — because every view is
 * already scoped server-side to their own doctor_id. Anyone who isn't a doctor
 * is bounced to the admin overview; unauthenticated users go to /login.
 */
export default function DoctorLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const { data: session, status } = useSession();
  const role = (session?.user as { role?: string } | undefined)?.role;

  React.useEffect(() => {
    if (status === "unauthenticated") router.replace("/login");
    else if (status === "authenticated" && role && role !== "doctor") router.replace("/overview");
  }, [status, role, router]);

  if (status !== "authenticated" || role !== "doctor") {
    return <AppLoader label="Loading your portal" />;
  }

  return (
    <div className="min-h-dvh bg-gray-50">
      <header className="flex h-14 items-center justify-between border-b border-gray-200 bg-white px-4 sm:px-6">
        <div className="flex items-center gap-2 font-semibold text-gray-900">
          <span className="grid h-8 w-8 place-items-center rounded-lg bg-brand-50 text-brand-700">
            <Stethoscope className="h-4.5 w-4.5" />
          </span>
          Doctor Portal
        </div>
        <div className="flex items-center gap-3">
          {session?.user?.email && (
            <span className="hidden text-xs text-gray-500 sm:block">{session.user.email}</span>
          )}
          <button className="btn-ghost" onClick={() => signOut({ callbackUrl: "/login" })}>
            <LogOut className="h-4 w-4" /> <span className="hidden sm:inline">Logout</span>
          </button>
        </div>
      </header>
      <main className="mx-auto max-w-5xl p-4 sm:p-6">{children}</main>
    </div>
  );
}
