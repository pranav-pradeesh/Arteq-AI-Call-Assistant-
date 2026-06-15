"use client";
import * as React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { signOut, useSession } from "next-auth/react";
import {
  LayoutDashboard, Phone, MessageSquareText, BarChart3, CalendarCheck, PhoneCall,
  Radio, Settings, Building2, Stethoscope, HelpCircle, Receipt, Siren, BookOpen,
  Network, Users, Boxes, LogOut, PlusCircle, Menu, X,
} from "lucide-react";
import { api } from "@/lib/api";
import { useCurrentHospital } from "./providers";
import { Reveal } from "./reveal";
import { cn } from "@/lib/utils";

type Item = { href: string; label: string; icon: React.ElementType };
const SECTIONS: { title: string; items: Item[]; superAdminOnly?: boolean }[] = [
  {
    title: "Operations",
    items: [
      { href: "/overview", label: "Overview", icon: LayoutDashboard },
      { href: "/calls", label: "Calls", icon: Phone },
      { href: "/qa", label: "Call QA", icon: MessageSquareText },
      { href: "/analytics", label: "Analytics", icon: BarChart3 },
      { href: "/live", label: "Live", icon: Radio },
      { href: "/appointments", label: "Appointments", icon: CalendarCheck },
      { href: "/callbacks", label: "Callbacks", icon: PhoneCall },
    ],
  },
  {
    title: "Configuration",
    items: [
      { href: "/settings", label: "Settings", icon: Settings },
      { href: "/departments", label: "Departments", icon: Building2 },
      { href: "/doctors", label: "Doctors", icon: Stethoscope },
      { href: "/faqs", label: "FAQs", icon: HelpCircle },
      { href: "/billing", label: "Billing", icon: Receipt },
      { href: "/emergency", label: "Emergency", icon: Siren },
      { href: "/knowledge", label: "Knowledge", icon: BookOpen },
    ],
  },
  {
    title: "Telephony",
    items: [
      { href: "/telephony", label: "Telephony", icon: Phone },
      { href: "/setup", label: "Setup", icon: Network },
      { href: "/his", label: "HIS Integration", icon: Network },
    ],
  },
  {
    title: "Admin",
    superAdminOnly: true,
    items: [
      { href: "/hospitals", label: "Hospitals", icon: Building2 },
      { href: "/onboarding", label: "Onboard hospital", icon: PlusCircle },
      { href: "/tenants", label: "Tenants", icon: Boxes },
      { href: "/users", label: "Users & Roles", icon: Users },
    ],
  },
];

function HospitalSwitcher() {
  const { hospitalId, setHospitalId } = useCurrentHospital();
  const { data: hospitals = [] } = useQuery({ queryKey: ["hospitals"], queryFn: api.listHospitals });
  React.useEffect(() => {
    if (!hospitalId && hospitals.length) setHospitalId(hospitals[0].id);
  }, [hospitalId, hospitals, setHospitalId]);
  return (
    <select
      className="input max-w-[16rem]"
      value={hospitalId ?? ""}
      onChange={(e) => setHospitalId(e.target.value || null)}
    >
      <option value="" disabled>Select hospital…</option>
      {hospitals.map((h) => (
        <option key={h.id} value={h.id}>{h.name}{h.tier ? ` · ${h.tier}` : ""}</option>
      ))}
    </select>
  );
}

type Section = (typeof SECTIONS)[number];

function NavContent({
  sections, pathname, onNavigate,
}: { sections: Section[]; pathname: string; onNavigate?: () => void }) {
  return (
    <>
      <div className="flex h-14 items-center gap-2 px-5 font-semibold">
        <img src="/logo.svg" alt="Arteq" className="h-8 w-8 object-contain" />
        Arteq Admin
      </div>
      <nav className="space-y-5 px-3 py-4">
        {sections.map((s) => (
          <div key={s.title}>
            <p className="px-2 pb-1 text-[10px] font-semibold uppercase tracking-wider text-gray-400">{s.title}</p>
            {s.items.map((it) => {
              const active = pathname === it.href;
              const Icon = it.icon;
              return (
                <Link
                  key={it.href}
                  href={it.href}
                  onClick={onNavigate}
                  className={cn(
                    "flex items-center gap-2.5 rounded-md px-2 py-1.5 text-sm",
                    active ? "bg-brand-50 font-medium text-brand-700" : "text-gray-600 hover:bg-gray-100"
                  )}
                >
                  <Icon className="h-4 w-4" /> {it.label}
                </Link>
              );
            })}
          </div>
        ))}
      </nav>
    </>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { data: session } = useSession();
  const role = (session?.user as { role?: string } | undefined)?.role;
  // Show admin-only sections to super_admins. During the bootstrap phase the
  // role may be undefined briefly; treat that as allowed so the management
  // screens are never accidentally hidden. Defined non-admin roles
  // (tenant_admin / viewer) are still excluded.
  const sections = SECTIONS.filter(
    (s) => !s.superAdminOnly || role === "super_admin" || !role
  );
  const [mobileOpen, setMobileOpen] = React.useState(false);

  return (
    <div className="flex min-h-screen">
      {/* Desktop sidebar */}
      <aside className="hidden w-60 shrink-0 border-r border-gray-200 bg-white lg:block">
        <NavContent sections={sections} pathname={pathname} />
      </aside>

      {/* Mobile drawer */}
      {mobileOpen && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div className="absolute inset-0 bg-black/40" onClick={() => setMobileOpen(false)} />
          <aside className="absolute left-0 top-0 h-full w-64 border-r border-gray-200 bg-white">
            <button
              className="absolute right-2 top-2 rounded p-1 text-gray-400 hover:bg-gray-100"
              onClick={() => setMobileOpen(false)}
            >
              <X className="h-4 w-4" />
            </button>
            <NavContent sections={sections} pathname={pathname} onNavigate={() => setMobileOpen(false)} />
          </aside>
        </div>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 items-center justify-between gap-2 border-b border-gray-200 bg-white px-4">
          <div className="flex min-w-0 items-center gap-2">
            <button className="rounded p-1.5 text-gray-600 hover:bg-gray-100 lg:hidden" onClick={() => setMobileOpen(true)} aria-label="Open menu">
              <Menu className="h-5 w-5" />
            </button>
            <HospitalSwitcher />
          </div>
          <div className="flex items-center gap-3">
            {session?.user?.email && (
              <span className="hidden text-xs text-gray-500 sm:block">
                {session.user.email}{role ? ` · ${role.replace("_", " ")}` : ""}
              </span>
            )}
            <button className="btn-ghost" onClick={() => signOut({ callbackUrl: "/login" })}>
              <LogOut className="h-4 w-4" /> <span className="hidden sm:inline">Logout</span>
            </button>
          </div>
        </header>
        <main className="min-w-0 flex-1 p-4 sm:p-5">
          <Reveal>{children}</Reveal>
        </main>
      </div>
    </div>
  );
}
