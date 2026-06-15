"use client";
import * as React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Stethoscope, Building2, Home, RotateCcw } from "lucide-react";
import { cn } from "@/lib/utils";
import { MockStoreProvider, useMockStore } from "./_store";

const TABS = [
  { href: "/mock", label: "Home", icon: Home },
  { href: "/mock/doctor", label: "Doctor Dashboard", icon: Stethoscope },
  { href: "/mock/admin", label: "Hospital Admin", icon: Building2 },
];

function MockHeader() {
  const pathname = usePathname();
  const { reset } = useMockStore();
  return (
    <header className="sticky top-0 z-40 border-b border-gray-200 bg-white">
      <div className="mx-auto flex h-14 max-w-6xl items-center gap-1 px-4">
        <span className="mr-3 inline-flex items-center gap-2 font-semibold text-gray-900">
          <span className="rounded-md bg-brand-600 px-1.5 py-0.5 text-xs text-white">MOCK</span>
          Arteq Care
        </span>
        <nav className="flex items-center gap-1">
          {TABS.map((t) => {
            const active = pathname === t.href;
            const Icon = t.icon;
            return (
              <Link
                key={t.href}
                href={t.href}
                className={cn(
                  "inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm",
                  active ? "bg-brand-50 font-medium text-brand-700" : "text-gray-600 hover:bg-gray-100"
                )}
              >
                <Icon className="h-4 w-4" />
                <span className="hidden sm:inline">{t.label}</span>
              </Link>
            );
          })}
        </nav>
        <button
          onClick={() => {
            if (confirm("Reset all mock data?")) reset();
          }}
          className="ml-auto inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm text-gray-500 hover:bg-gray-100"
          title="Reset demo data"
        >
          <RotateCcw className="h-4 w-4" />
          <span className="hidden sm:inline">Reset</span>
        </button>
      </div>
    </header>
  );
}

export default function MockLayout({ children }: { children: React.ReactNode }) {
  return (
    <MockStoreProvider>
      <div className="min-h-screen bg-gray-50">
        <MockHeader />
        <main className="mx-auto max-w-6xl px-4 py-6">{children}</main>
      </div>
    </MockStoreProvider>
  );
}
