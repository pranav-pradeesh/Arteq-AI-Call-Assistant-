"use client";
import * as React from "react";
import { useRouter } from "next/navigation";
import { useSession } from "next-auth/react";
import { AppShell } from "@/components/app-shell";
import { Spinner } from "@/components/ui";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const { status } = useSession();

  React.useEffect(() => {
    if (status === "unauthenticated") router.replace("/login");
  }, [status, router]);

  if (status !== "authenticated") {
    return (
      <div className="grid min-h-screen place-items-center text-gray-400">
        <Spinner className="h-6 w-6" />
      </div>
    );
  }
  return <AppShell>{children}</AppShell>;
}
