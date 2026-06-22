"use client";
import * as React from "react";
import { useRouter } from "next/navigation";
import { useSession } from "next-auth/react";
import { AppShell } from "@/components/app-shell";
import { AppLoader } from "@/components/brand";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const { data: session, status } = useSession();
  const role = (session?.user as { role?: string } | undefined)?.role;

  React.useEffect(() => {
    if (status === "unauthenticated") router.replace("/login");
    // Doctors have a dedicated self-service dashboard and no admin access.
    else if (status === "authenticated" && role === "doctor") router.replace("/doctor");
  }, [status, role, router]);

  if (status !== "authenticated" || role === "doctor") {
    return <AppLoader label="Signing in" />;
  }
  return <AppShell>{children}</AppShell>;
}
