"use client";
import * as React from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { Appointment, AppointmentStatus, AppointmentEvent } from "@/lib/types";
import { fmtDateTime } from "@/lib/utils";
import {
  PageHeader, Badge, Button, Select, Label, Spinner,
} from "@/components/ui";
import { DataTable, ColumnDef } from "@/components/data-table";
import { RequireHospital } from "@/components/require-hospital";
import { useToast } from "@/components/providers";

const ALL_STATUSES: AppointmentStatus[] = [
  "pending", "booked", "confirmed", "cancelled", "rescheduled", "requested",
];

function statusTone(s: AppointmentStatus): "green" | "red" | "yellow" | "gray" {
  if (s === "confirmed") return "green";
  if (s === "cancelled") return "red";
  if (s === "pending" || s === "requested") return "yellow";
  return "gray";
}

function workflowTone(s: string): "green" | "red" | "yellow" | "blue" | "gray" {
  if (s === "confirmed" || s === "doctor_available") return "green";
  if (s === "cancelled" || s === "missed" || s === "doctor_unavailable") return "red";
  if (s === "doctor_delayed") return "yellow";
  if (s === "reminder_sent") return "blue";
  return "gray";
}

function EventsModal({ hospitalId, appt, onClose }: {
  hospitalId: string; appt: Appointment; onClose: () => void;
}) {
  const { data: events = [], isLoading } = useQuery<AppointmentEvent[]>({
    queryKey: ["appt-events", hospitalId, appt.id],
    queryFn: () => api.getAppointmentEvents(hospitalId, appt.id),
  });
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-md max-h-[80vh] flex flex-col">
        <div className="flex items-center justify-between px-5 py-4 border-b">
          <h2 className="font-semibold text-sm">Appointment events — {appt.patient_name}</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600">✕</button>
        </div>
        <div className="overflow-y-auto flex-1 px-5 py-4">
          {isLoading ? (
            <div className="flex justify-center py-8"><Spinner className="h-5 w-5" /></div>
          ) : events.length === 0 ? (
            <p className="text-sm text-gray-400 text-center py-8">No events recorded yet.</p>
          ) : (
            <ol className="relative border-l border-gray-200 space-y-4 ml-2">
              {events.map((ev) => (
                <li key={ev.id} className="ml-4">
                  <div className="absolute -left-1.5 h-3 w-3 rounded-full bg-gray-300 border border-white" />
                  <p className="text-xs font-medium text-gray-700">{ev.event_type.replace(/_/g, " ")}</p>
                  {ev.detail && <p className="text-xs text-gray-500 mt-0.5">{ev.detail}</p>}
                  <p className="text-xs text-gray-400 mt-0.5">{fmtDateTime(ev.created_at)}</p>
                </li>
              ))}
            </ol>
          )}
        </div>
      </div>
    </div>
  );
}

function AppointmentsInner({ hospitalId }: { hospitalId: string }) {
  const toast = useToast();
  const qc = useQueryClient();
  const [statusFilter, setStatusFilter] = React.useState<AppointmentStatus | "">("");
  const [eventsAppt, setEventsAppt] = React.useState<Appointment | null>(null);

  const { data = [], isLoading } = useQuery({
    queryKey: ["appointments", hospitalId, statusFilter],
    queryFn: () => api.listAppointments(hospitalId, statusFilter || undefined, 200),
  });

  const updateMutation = useMutation({
    mutationFn: ({ apptId, status }: { apptId: string; status: AppointmentStatus }) =>
      api.updateAppointmentStatus(hospitalId, apptId, status),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["appointments", hospitalId] });
      toast("Appointment status updated");
    },
    onError: (e: Error) => toast(e.message, "err"),
  });

  const columns: ColumnDef<Appointment, unknown>[] = [
    {
      header: "Patient",
      accessorKey: "patient_name",
      cell: ({ row }) => row.original.patient_name ?? <span className="text-gray-400">—</span>,
    },
    {
      header: "Phone",
      accessorKey: "patient_phone",
      cell: ({ row }) => row.original.patient_phone ?? <span className="text-gray-400">—</span>,
    },
    {
      header: "Slot",
      accessorKey: "slot_time",
      cell: ({ row }) =>
        row.original.slot_time ? fmtDateTime(row.original.slot_time) : <span className="text-gray-400">—</span>,
    },
    {
      header: "Status",
      accessorKey: "status",
      cell: ({ row }) => {
        const appt = row.original;
        return (
          <div className="flex flex-wrap items-center gap-1">
            <Badge tone={statusTone(appt.status)}>{appt.status}</Badge>
            {appt.workflow_status && (
              <Badge tone={workflowTone(appt.workflow_status)} className="text-xs">
                {appt.workflow_status.replace(/_/g, " ")}
              </Badge>
            )}
            {appt.reminder_sent && (
              <Badge tone="blue" className="text-xs">reminder</Badge>
            )}
            {appt.confirmation_sent && (
              <Badge tone="green" className="text-xs">confirmed</Badge>
            )}
          </div>
        );
      },
    },
    {
      header: "Actions",
      id: "actions",
      cell: ({ row }) => {
        const appt = row.original;
        const busy = updateMutation.isPending;
        return (
          <div className="flex gap-1.5 flex-wrap">
            {appt.status !== "confirmed" && (
              <Button
                variant="outline"
                className="px-2 py-1 text-xs"
                disabled={busy}
                onClick={() => updateMutation.mutate({ apptId: appt.id, status: "confirmed" })}
              >
                Confirm
              </Button>
            )}
            {appt.status !== "cancelled" && (
              <Button
                variant="danger"
                className="px-2 py-1 text-xs"
                disabled={busy}
                onClick={() => updateMutation.mutate({ apptId: appt.id, status: "cancelled" })}
              >
                Cancel
              </Button>
            )}
            <Button
              variant="ghost"
              className="px-2 py-1 text-xs"
              onClick={() => setEventsAppt(appt)}
            >
              Events
            </Button>
          </div>
        );
      },
    },
  ];

  return (
    <div>
      <PageHeader title="Appointments" />
      <div className="mb-4 flex items-center gap-2">
        <Label className="text-sm">Status</Label>
        <Select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value as AppointmentStatus | "")}
          className="w-44"
        >
          <option value="">All</option>
          {ALL_STATUSES.map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </Select>
      </div>
      {isLoading ? (
        <div className="flex justify-center py-12"><Spinner className="h-6 w-6" /></div>
      ) : (
        <DataTable
          columns={columns}
          data={data}
          searchPlaceholder="Search patient…"
          emptyTitle="No appointments found"
        />
      )}
      {eventsAppt && (
        <EventsModal
          hospitalId={hospitalId}
          appt={eventsAppt}
          onClose={() => setEventsAppt(null)}
        />
      )}
    </div>
  );
}

export default function AppointmentsPage() {
  return (
    <RequireHospital>
      {(hospitalId) => <AppointmentsInner hospitalId={hospitalId} />}
    </RequireHospital>
  );
}
