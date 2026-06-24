"use client";
import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CalendarDays, Clock, MapPin, Phone, CheckCircle2, AlarmClock, Ban,
} from "lucide-react";
import {
  PageHeader, Button, Badge, Card, CardBody, CardHeader, EmptyState, Spinner,
} from "@/components/ui";
import { StatCard } from "@/components/stat-card";
import { api } from "@/lib/api";
import { useToast } from "@/components/providers";
import { fmtDateTime, dowLabel } from "@/lib/utils";
import type { DoctorAppointment, DoctorAvailabilityStatus } from "@/lib/types";

// Doctors are stored with or without a "Dr." prefix; normalise so we never show
// "Dr. Dr. Meera".
function displayName(name?: string | null): string {
  const n = (name ?? "").trim().replace(/^dr\.?\s*/i, "");
  return n ? `Dr. ${n}` : "Doctor";
}

function todayLocal(): string {
  const d = new Date();
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
}

function apptTone(status: string): "green" | "red" | "yellow" | "gray" {
  if (status === "confirmed") return "green";
  if (status === "cancelled") return "red";
  if (status === "booked") return "yellow";
  return "gray";
}

const AVAIL: { value: DoctorAvailabilityStatus; label: string; icon: React.ElementType; tone: string }[] = [
  { value: "available", label: "Available", icon: CheckCircle2, tone: "bg-green-600" },
  { value: "delayed", label: "Delayed", icon: AlarmClock, tone: "bg-amber-500" },
  { value: "unavailable", label: "Unavailable", icon: Ban, tone: "bg-red-600" },
];

function AvailabilityControl({ current }: { current?: string | null }) {
  const qc = useQueryClient();
  const toast = useToast();
  const m = useMutation({
    mutationFn: (s: DoctorAvailabilityStatus) => api.setMyAvailability(s),
    onSuccess: (_d, s) => {
      toast(`Availability set to “${s}”.`);
      qc.invalidateQueries({ queryKey: ["doctor-me"] });
    },
    onError: () => toast("Could not update availability.", "err"),
  });
  return (
    <Card>
      <CardHeader className="flex items-center justify-between">
        <span className="font-medium text-gray-900">Today&apos;s availability</span>
        {m.isPending && <Spinner />}
      </CardHeader>
      <CardBody>
        <div className="flex flex-wrap gap-2">
          {AVAIL.map((a) => {
            const active = current === a.value;
            const Icon = a.icon;
            return (
              <button
                key={a.value}
                disabled={m.isPending}
                onClick={() => m.mutate(a.value)}
                className={`flex items-center gap-2 rounded-lg border px-3 py-2 text-sm font-medium transition disabled:opacity-50 ${
                  active
                    ? `${a.tone} border-transparent text-white shadow-sm`
                    : "border-gray-200 text-gray-700 hover:bg-gray-50"
                }`}
              >
                <Icon className="h-4 w-4" /> {a.label}
              </button>
            );
          })}
        </div>
        <p className="mt-2 text-xs text-gray-500">
          Patients calling the AI agent are told your current status, and your
          confirmed appointments are notified when you mark yourself delayed or
          unavailable.
        </p>
      </CardBody>
    </Card>
  );
}

function AppointmentRow({ a }: { a: DoctorAppointment }) {
  return (
    <li className="flex items-start justify-between gap-2 px-4 py-3 text-sm">
      <div className="min-w-0">
        <p className="font-medium text-gray-900">{a.patient_name || "Unknown patient"}</p>
        <p className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs text-gray-500">
          <span className="inline-flex items-center gap-1">
            <Clock className="h-3.5 w-3.5" /> {fmtDateTime(a.slot_time)}
          </span>
          {a.patient_phone && (
            <span className="inline-flex items-center gap-1">
              <Phone className="h-3.5 w-3.5" /> {a.patient_phone}
            </span>
          )}
          {a.confirmation_code && (
            <span className="font-mono text-[11px] text-gray-400">{a.confirmation_code}</span>
          )}
        </p>
      </div>
      <Badge tone={apptTone(a.status)}>{a.status}</Badge>
    </li>
  );
}

export default function DoctorDashboardPage() {
  const [day, setDay] = React.useState<string>(todayLocal());

  const me = useQuery({ queryKey: ["doctor-me"], queryFn: api.doctorMe });
  const appts = useQuery({
    queryKey: ["doctor-appts", day],
    queryFn: () => api.doctorAppointments(day),
  });
  const schedule = useQuery({ queryKey: ["doctor-schedule"], queryFn: api.doctorSchedule });

  const confirmed = (appts.data ?? []).filter((a) => a.status === "confirmed").length;

  if (me.isLoading) {
    return (
      <div className="grid place-items-center py-20 text-gray-400">
        <Spinner /> Loading your dashboard…
      </div>
    );
  }
  if (me.isError) {
    return (
      <EmptyState
        title="Couldn’t load your profile"
        hint="Your login isn’t linked to a doctor record yet. Please contact your hospital admin."
      />
    );
  }

  return (
    <div>
      <PageHeader title={displayName(me.data?.name)} />
      <p className="-mt-3 mb-5 text-sm text-gray-500">
        {[me.data?.specialty, me.data?.department, me.data?.hospital].filter(Boolean).join(" · ")}
      </p>

      <div className="mb-5 flex flex-wrap gap-3">
        <StatCard label="Appointments (this day)" value={String(appts.data?.length ?? 0)} />
        <StatCard label="Confirmed" value={String(confirmed)} />
        <StatCard
          label="Current status"
          value={(me.data?.availability_status ?? "available").replace(/_/g, " ")}
        />
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <div className="space-y-5">
          <AvailabilityControl current={me.data?.availability_status} />

          {/* Weekly schedule */}
          <Card>
            <CardHeader className="font-medium text-gray-900">Weekly schedule</CardHeader>
            <CardBody className="p-0">
              {schedule.isLoading ? (
                <div className="p-4 text-sm text-gray-400"><Spinner /> Loading…</div>
              ) : (schedule.data?.length ?? 0) === 0 ? (
                <EmptyState title="No schedule set" hint="Your hospital admin can add consulting hours." />
              ) : (
                <ul className="divide-y divide-gray-100">
                  {schedule.data!.map((s, i) => (
                    <li key={i} className="flex items-center justify-between px-4 py-2.5 text-sm">
                      <span className="font-medium text-gray-900">{dowLabel(s.day_of_week)}</span>
                      <span className="flex items-center gap-3 text-gray-600">
                        <span className="inline-flex items-center gap-1">
                          <Clock className="h-3.5 w-3.5" />
                          {s.start_time?.slice(0, 5)}–{s.end_time?.slice(0, 5)}
                        </span>
                        {s.room && (
                          <span className="inline-flex items-center gap-1 text-gray-500">
                            <MapPin className="h-3.5 w-3.5" /> {s.room}
                          </span>
                        )}
                        {!s.active && <Badge tone="gray">off</Badge>}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </CardBody>
          </Card>
        </div>

        {/* Appointments for the chosen day */}
        <Card>
          <CardHeader className="flex flex-wrap items-center justify-between gap-2">
            <span className="flex items-center gap-2 font-medium text-gray-900">
              <CalendarDays className="h-4 w-4" /> Appointments
            </span>
            <input
              type="date"
              className="input max-w-[12rem]"
              value={day}
              onChange={(e) => setDay(e.target.value || todayLocal())}
            />
          </CardHeader>
          <CardBody className="p-0">
            {appts.isLoading ? (
              <div className="p-4 text-sm text-gray-400"><Spinner /> Loading…</div>
            ) : (appts.data?.length ?? 0) === 0 ? (
              <EmptyState title="No appointments" hint="Nothing booked for this day." />
            ) : (
              <ul className="divide-y divide-gray-100">
                {appts.data!.map((a) => (
                  <AppointmentRow key={a.id} a={a} />
                ))}
              </ul>
            )}
          </CardBody>
        </Card>
      </div>
    </div>
  );
}
