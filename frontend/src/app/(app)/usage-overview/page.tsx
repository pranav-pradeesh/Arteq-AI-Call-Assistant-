"use client";
import * as React from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { UsageResponse, PlanUpdate } from "@/lib/types";
import { FormModal } from "@/components/modal";
import { PageHeader, Button, Field, Input, Badge, Card, CardBody, Spinner, EmptyState } from "@/components/ui";
import { useToast } from "@/components/providers";
import { paiseToRupees } from "@/lib/utils";

function MiniBar({ pct, over }: { pct: number | null | undefined; over: boolean }) {
  if (pct == null) return <span className="text-xs text-gray-400">no limit</span>;
  const tone = over ? "bg-red-600" : pct >= 80 ? "bg-amber-500" : "bg-brand-600";
  return (
    <div className="w-28">
      <div className="h-2 w-full overflow-hidden rounded-full bg-gray-100">
        <div className={`h-full ${tone}`} style={{ width: `${Math.min(pct, 100)}%` }} />
      </div>
      <span className={`text-[11px] ${over ? "text-red-600" : "text-gray-500"}`}>{pct.toFixed(0)}%</span>
    </div>
  );
}

type PlanForm = {
  plan_name: string;
  monthly_call_limit: string;
  monthly_minutes_limit: string;
  monthly_cost_limit_rupees: string;
  price_per_minute_rupees: string;
  billing_cycle_day: string;
};

function num(s: string): number | null {
  const t = s.trim();
  if (t === "") return null;
  const n = Number(t);
  return Number.isFinite(n) ? n : null;
}

function PlanModal({ row, onClose }: { row: UsageResponse; onClose: () => void }) {
  const toast = useToast();
  const qc = useQueryClient();
  const [form, setForm] = React.useState<PlanForm>({
    plan_name: row.plan_name ?? "",
    monthly_call_limit: row.monthly_call_limit?.toString() ?? "",
    monthly_minutes_limit: row.monthly_minutes_limit?.toString() ?? "",
    monthly_cost_limit_rupees:
      row.monthly_cost_limit_paise != null ? String(row.monthly_cost_limit_paise / 100) : "",
    price_per_minute_rupees:
      row.price_per_minute_paise != null ? String(row.price_per_minute_paise / 100) : "",
    billing_cycle_day: "", // blank keeps the existing cycle day (backend COALESCE)
  });
  const set = (k: keyof PlanForm) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm((f) => ({ ...f, [k]: e.target.value }));

  const mut = useMutation({
    mutationFn: () => {
      const rupees = num(form.monthly_cost_limit_rupees);
      const body: PlanUpdate = {
        plan_name: form.plan_name.trim() || null,
        monthly_call_limit: num(form.monthly_call_limit),
        monthly_minutes_limit: num(form.monthly_minutes_limit),
        monthly_cost_limit_paise: rupees == null ? null : Math.round(rupees * 100),
        price_per_minute_paise: (() => {
          const r = num(form.price_per_minute_rupees);
          return r == null ? null : Math.round(r * 100);
        })(),
        billing_cycle_day: num(form.billing_cycle_day),
      };
      return api.setHospitalPlan(row.hospital_id, body);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["usage-overview"] });
      toast("Plan updated", "ok");
      onClose();
    },
    onError: (e: Error) => toast(e.message, "err"),
  });

  return (
    <FormModal
      open
      onClose={onClose}
      title={`Plan — ${row.hospital_name ?? "Hospital"}`}
      onSubmit={(e) => { e.preventDefault(); mut.mutate(); }}
      saving={mut.isPending}
      submitLabel="Save plan"
    >
      <Field label="Plan name">
        <Input value={form.plan_name} onChange={set("plan_name")} placeholder="e.g. starter" />
      </Field>
      <p className="text-xs text-gray-500">Leave a limit blank for unlimited.</p>
      <Field label="Monthly call limit">
        <Input type="number" min={0} value={form.monthly_call_limit} onChange={set("monthly_call_limit")} />
      </Field>
      <Field label="Monthly minutes limit">
        <Input type="number" min={0} value={form.monthly_minutes_limit} onChange={set("monthly_minutes_limit")} />
      </Field>
      <Field label="Monthly cost limit (₹)">
        <Input type="number" min={0} value={form.monthly_cost_limit_rupees} onChange={set("monthly_cost_limit_rupees")} />
      </Field>
      <Field label="Price per minute (₹) — what the hospital is billed">
        <Input type="number" min={0} step="0.01" value={form.price_per_minute_rupees} onChange={set("price_per_minute_rupees")} placeholder="e.g. 3" />
      </Field>
      <Field label="Billing cycle day (1–28)">
        <Input type="number" min={1} max={28} value={form.billing_cycle_day} onChange={set("billing_cycle_day")} placeholder="1" />
      </Field>
    </FormModal>
  );
}

export default function UsageOverviewPage() {
  const { data = [], isLoading, isError } = useQuery({
    queryKey: ["usage-overview"],
    queryFn: api.usageOverview,
  });
  const [editing, setEditing] = React.useState<UsageResponse | null>(null);

  if (isLoading) {
    return <div className="grid place-items-center py-20 text-gray-400"><Spinner /> Loading…</div>;
  }
  if (isError) {
    return <EmptyState title="Couldn’t load usage" hint="Super-admin access required." />;
  }

  const totalPaise = data.reduce((s, r) => s + r.cost_paise, 0);

  return (
    <div>
      <PageHeader title="Usage — all hospitals" />
      <p className="-mt-3 mb-5 text-sm text-gray-500">
        Current billing period · total across all hospitals: <strong>{paiseToRupees(totalPaise)}</strong>
      </p>

      <Card>
        <CardBody className="p-0 overflow-x-auto">
          {data.length === 0 ? (
            <EmptyState title="No hospitals yet" />
          ) : (
            <table className="w-full text-sm">
              <thead className="border-b border-gray-100 text-left text-xs uppercase tracking-wide text-gray-400">
                <tr>
                  <th className="px-4 py-2.5">Hospital</th>
                  <th className="px-4 py-2.5">Plan</th>
                  <th className="px-4 py-2.5">Calls</th>
                  <th className="px-4 py-2.5">Minutes</th>
                  <th className="px-4 py-2.5">Cost</th>
                  <th className="px-4 py-2.5">Usage</th>
                  <th className="px-4 py-2.5"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-50">
                {data.map((r) => (
                  <tr key={r.hospital_id} className={r.over_limit ? "bg-red-50/40" : ""}>
                    <td className="px-4 py-2.5 font-medium text-gray-900">{r.hospital_name ?? r.hospital_id}</td>
                    <td className="px-4 py-2.5">
                      {r.plan_name ? <Badge tone="blue">{r.plan_name}</Badge> : <span className="text-gray-400">—</span>}
                    </td>
                    <td className="px-4 py-2.5 text-gray-700">{r.calls}</td>
                    <td className="px-4 py-2.5 text-gray-700">{r.minutes}</td>
                    <td className="px-4 py-2.5 font-medium text-gray-900">{paiseToRupees(r.cost_paise)}</td>
                    <td className="px-4 py-2.5"><MiniBar pct={r.percent_used} over={r.over_limit} /></td>
                    <td className="px-4 py-2.5 text-right">
                      <Button variant="outline" className="text-xs" onClick={() => setEditing(r)}>
                        Edit plan
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </CardBody>
      </Card>

      {editing && <PlanModal row={editing} onClose={() => setEditing(null)} />}
    </div>
  );
}
