"use client";
import * as React from "react";
import { Phone, MessageCircle, CalendarCheck, CheckCircle2 } from "lucide-react";
import { PageHeader, Card, CardBody, CardHeader, Badge, EmptyState } from "@/components/ui";
import { StatCard } from "@/components/stat-card";
import { fmtDateTime, paiseToRupees } from "@/lib/utils";
import { useMockStore, type BookingStatus } from "../_store";

function statusBadge(s: BookingStatus) {
  const map: Record<BookingStatus, { tone: "green" | "red" | "yellow" | "gray"; label: string }> = {
    confirmed: { tone: "green", label: "confirmed" },
    pending_payment: { tone: "yellow", label: "awaiting payment" },
    awaiting_confirmation: { tone: "yellow", label: "awaiting AI confirm" },
    cancelled: { tone: "red", label: "cancelled" },
  };
  const v = map[s];
  return <Badge tone={v.tone}>{v.label}</Badge>;
}

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const m = Math.floor(diff / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return fmtDateTime(iso);
}

export default function AdminDashboard() {
  const { patients, bookings, whatsapp, calls } = useMockStore();

  const activeTokens = bookings.filter((b) => b.token?.active).length;
  const pending = bookings.filter(
    (b) => b.status === "pending_payment" || b.status === "awaiting_confirmation"
  ).length;
  const revenue = bookings
    .filter((b) => b.status === "confirmed" && b.paymentMode === "pay_now")
    .reduce((s, b) => s + b.amountPaise, 0);

  return (
    <div>
      <PageHeader title="Hospital Admin Dashboard" />

      <div className="mb-5 flex flex-wrap gap-3">
        <StatCard label="Patients" value={String(patients.length)} hint="Registered" />
        <StatCard label="Bookings" value={String(bookings.length)} />
        <StatCard label="Pending" value={String(pending)} hint="Awaiting payment / confirm" />
        <StatCard label="Active tokens" value={String(activeTokens)} />
        <StatCard label="Collected" value={paiseToRupees(revenue)} hint="Pay-now" />
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        {/* Bookings overview */}
        <Card className="lg:col-span-2">
          <CardHeader className="flex items-center justify-between">
            <span className="flex items-center gap-2 font-medium text-gray-900">
              <CalendarCheck className="h-4 w-4 text-gray-500" /> Bookings &amp; tokens
            </span>
            <Badge>{bookings.length}</Badge>
          </CardHeader>
          <CardBody className="p-0">
            {bookings.length === 0 ? (
              <EmptyState title="No bookings yet" hint="Create one from the Doctor dashboard." />
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="bg-gray-50 text-left text-xs uppercase tracking-wide text-gray-500">
                    <tr>
                      <th className="px-4 py-2 font-medium">Patient</th>
                      <th className="px-4 py-2 font-medium">Appointment</th>
                      <th className="px-4 py-2 font-medium">Payment</th>
                      <th className="px-4 py-2 font-medium">Amount</th>
                      <th className="px-4 py-2 font-medium">Token</th>
                      <th className="px-4 py-2 font-medium">Status</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-100">
                    {bookings.map((b) => (
                      <tr key={b.id}>
                        <td className="px-4 py-2.5">
                          <p className="font-medium text-gray-900">{b.patientName}</p>
                          <p className="font-mono text-[11px] text-gray-400">{b.patientId}</p>
                        </td>
                        <td className="px-4 py-2.5 text-gray-600">{fmtDateTime(b.slot)}</td>
                        <td className="px-4 py-2.5 text-gray-600">
                          {b.paymentMode === "pay_now" ? "Pay now" : "Pay later"}
                        </td>
                        <td className="px-4 py-2.5 text-gray-600">{paiseToRupees(b.amountPaise)}</td>
                        <td className="px-4 py-2.5">
                          {b.token ? (
                            <span className="inline-flex items-center gap-1 font-mono text-xs">
                              {b.token.code}
                              <Badge tone={b.token.active ? "green" : "gray"} className="text-[10px]">
                                {b.token.active ? "active" : "inactive"}
                              </Badge>
                            </span>
                          ) : (
                            <span className="text-gray-300">—</span>
                          )}
                        </td>
                        <td className="px-4 py-2.5">{statusBadge(b.status)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </CardBody>
        </Card>

        {/* AI call log */}
        <Card>
          <CardHeader className="flex items-center justify-between">
            <span className="flex items-center gap-2 font-medium text-gray-900">
              <Phone className="h-4 w-4 text-gray-500" /> AI agent call log
            </span>
            <Badge>{calls.length}</Badge>
          </CardHeader>
          <CardBody className="p-0">
            {calls.length === 0 ? (
              <EmptyState title="No calls yet" hint="Calls are placed on patient add and confirmation." />
            ) : (
              <ul className="divide-y divide-gray-100">
                {calls.map((c) => (
                  <li key={c.id} className="px-4 py-3 text-sm">
                    <div className="flex items-center justify-between">
                      <p className="font-medium text-gray-900">{c.patientName}</p>
                      <span className="text-xs text-gray-400">{timeAgo(c.at)}</span>
                    </div>
                    <p className="text-xs text-gray-500">{c.phone} · {c.purpose}</p>
                    <p className="mt-1 flex items-start gap-1.5 text-xs text-gray-600">
                      <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-green-500" />
                      {c.outcome}
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </CardBody>
        </Card>

        {/* WhatsApp feed */}
        <Card>
          <CardHeader className="flex items-center justify-between">
            <span className="flex items-center gap-2 font-medium text-gray-900">
              <MessageCircle className="h-4 w-4 text-gray-500" /> WhatsApp messages
            </span>
            <Badge>{whatsapp.length}</Badge>
          </CardHeader>
          <CardBody className="p-0">
            {whatsapp.length === 0 ? (
              <EmptyState title="No messages yet" hint="Tokens & confirmations are sent here." />
            ) : (
              <ul className="space-y-2 p-3">
                {whatsapp.map((m) => (
                  <li key={m.id} className="rounded-lg bg-green-50 p-2.5 text-sm">
                    <div className="flex items-center justify-between">
                      <span className="text-xs font-medium text-gray-700">{m.patientName}</span>
                      <span className="text-[11px] text-gray-400">{timeAgo(m.at)}</span>
                    </div>
                    <p className="mt-0.5 text-xs text-gray-700">{m.body}</p>
                    <p className="mt-1 text-[10px] text-gray-400">{m.phone}</p>
                  </li>
                ))}
              </ul>
            )}
          </CardBody>
        </Card>
      </div>
    </div>
  );
}
