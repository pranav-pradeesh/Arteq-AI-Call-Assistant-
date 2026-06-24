"use client";
import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { Mic, Volume2, Brain, PhoneCall, AlertTriangle } from "lucide-react";
import { api } from "@/lib/api";
import type { UsageResponse, ServiceLine } from "@/lib/types";
import { RequireHospital } from "@/components/require-hospital";
import { PageHeader, Card, CardHeader, CardBody, Badge, Spinner, EmptyState } from "@/components/ui";
import { StatCard } from "@/components/stat-card";
import { paiseToRupees } from "@/lib/utils";

function fmtDay(iso: string) {
  return new Date(iso).toLocaleDateString("en-IN", { day: "numeric", month: "short" });
}

function SourceBadge({ source }: { source: ServiceLine["source"] }) {
  return source === "billed" ? (
    <Badge tone="green">billed</Badge>
  ) : (
    <Badge tone="gray">list-price</Badge>
  );
}

function UsageBar({ pct, over }: { pct: number | null | undefined; over: boolean }) {
  if (pct == null) {
    return <p className="text-xs text-gray-500">No plan limit set — usage is uncapped.</p>;
  }
  const width = Math.min(pct, 100);
  const tone = over ? "bg-red-600" : pct >= 80 ? "bg-amber-500" : "bg-brand-600";
  return (
    <div>
      <div className="h-2.5 w-full overflow-hidden rounded-full bg-gray-100">
        <div className={`h-full ${tone}`} style={{ width: `${width}%` }} />
      </div>
      <p className={`mt-1 text-xs ${over ? "text-red-600 font-medium" : "text-gray-500"}`}>
        {pct.toFixed(1)}% of plan limit{over ? " · over limit" : ""}
      </p>
    </div>
  );
}

const SERVICES: { key: keyof UsageResponse["by_service"]; label: string; icon: React.ElementType; vendor: string }[] = [
  { key: "telephony", label: "Telephony", icon: PhoneCall, vendor: "Vobiz" },
  { key: "stt", label: "Speech-to-text", icon: Mic, vendor: "Sarvam Saarika" },
  { key: "tts", label: "Text-to-speech", icon: Volume2, vendor: "Sarvam Bulbul" },
  { key: "llm", label: "LLM", icon: Brain, vendor: "Gemini / OpenRouter" },
];

function UsageView({ u }: { u: UsageResponse }) {
  return (
    <div>
      <PageHeader
        title="Usage & Cost"
        action={
          <Badge tone="blue">{u.plan_name ? `Plan: ${u.plan_name}` : "No plan set"}</Badge>
        }
      />
      <p className="-mt-3 mb-5 text-sm text-gray-500">
        Billing period {fmtDay(u.period_start)} – {fmtDay(u.period_end)}
        {u.hospital_name ? ` · ${u.hospital_name}` : ""}
      </p>

      {u.over_limit && (
        <div className="mb-4 flex items-center gap-2 rounded-lg bg-red-50 p-3 text-sm text-red-800">
          <AlertTriangle className="h-4 w-4 shrink-0" />
          This hospital has exceeded its plan limit for the current period.
        </div>
      )}

      <div className="mb-5 flex flex-wrap gap-3">
        <StatCard label="Total cost (this period)" value={paiseToRupees(u.cost_paise)} />
        <StatCard label="Calls" value={String(u.calls)} hint={`${u.inbound_calls} in · ${u.outbound_calls} out`} />
        <StatCard
          label="Minutes"
          value={u.minutes.toLocaleString("en-IN")}
          hint={u.monthly_minutes_limit != null
            ? `of ${u.monthly_minutes_limit.toLocaleString("en-IN")} · ${Math.max(0, u.monthly_minutes_limit - u.minutes).toLocaleString("en-IN")} left`
            : undefined}
        />
        {u.price_per_minute_paise != null && (
          <StatCard label="Rate" value={`${paiseToRupees(u.price_per_minute_paise)}/min`} />
        )}
        {u.amount_due_paise != null && (
          <StatCard label="Amount due (minutes)" value={paiseToRupees(u.amount_due_paise)} />
        )}
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <Card>
          <CardHeader className="font-medium text-gray-900">Plan usage</CardHeader>
          <CardBody className="space-y-4">
            <UsageBar pct={u.percent_used} over={u.over_limit} />
            <dl className="space-y-1.5 text-sm">
              <LimitRow label="Calls" used={u.calls} limit={u.monthly_call_limit} />
              <LimitRow label="Minutes" used={u.minutes} limit={u.monthly_minutes_limit} />
              <LimitRow
                label="Cost"
                used={u.cost_paise}
                limit={u.monthly_cost_limit_paise}
                fmt={paiseToRupees}
              />
            </dl>
          </CardBody>
        </Card>

        <Card>
          <CardHeader className="flex items-center justify-between">
            <span className="font-medium text-gray-900">Cost per service</span>
          </CardHeader>
          <CardBody className="p-0">
            <ul className="divide-y divide-gray-100">
              {SERVICES.map(({ key, label, icon: Icon, vendor }) => {
                const line = u.by_service[key];
                return (
                  <li key={key} className="flex items-center justify-between px-4 py-3 text-sm">
                    <span className="flex items-center gap-2.5">
                      <Icon className="h-4 w-4 text-brand-600" />
                      <span>
                        <span className="font-medium text-gray-900">{label}</span>
                        <span className="block text-xs text-gray-400">{vendor}</span>
                      </span>
                    </span>
                    <span className="flex items-center gap-2">
                      <SourceBadge source={line.source} />
                      <span className="font-medium text-gray-900">{paiseToRupees(line.paise)}</span>
                    </span>
                  </li>
                );
              })}
            </ul>
          </CardBody>
        </Card>
      </div>

      <p className="mt-4 text-xs text-gray-400">
        <strong>billed</strong> = real cost charged by the platform (Vobiz CDR / OpenRouter).
        {" "}<strong>list-price</strong> = real measured usage × the provider&apos;s published rate
        (Gemini &amp; Sarvam expose no per-call cost API).
      </p>
    </div>
  );
}

function LimitRow({
  label, used, limit, fmt,
}: {
  label: string;
  used: number;
  limit?: number | null;
  fmt?: (n: number) => string;
}) {
  const f = fmt ?? ((n: number) => n.toLocaleString("en-IN"));
  return (
    <div className="flex justify-between">
      <dt className="text-gray-500">{label}</dt>
      <dd className="font-medium text-gray-900">
        {f(used)} <span className="text-gray-400">/ {limit == null ? "∞" : f(limit)}</span>
      </dd>
    </div>
  );
}

function Inner({ hospitalId }: { hospitalId: string }) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["usage", hospitalId],
    queryFn: () => api.hospitalUsage(hospitalId),
  });
  if (isLoading) {
    return <div className="grid place-items-center py-20 text-gray-400"><Spinner /> Loading usage…</div>;
  }
  if (isError || !data) {
    return <EmptyState title="Couldn’t load usage" hint="Try again in a moment." />;
  }
  return <UsageView u={data} />;
}

export default function UsagePage() {
  return <RequireHospital>{(hid) => <Inner hospitalId={hid} />}</RequireHospital>;
}
